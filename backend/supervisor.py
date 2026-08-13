"""
### Watching the things the container depends on.

`entrypoint.py` did three things and looked at none of them:

  - It ran `main.py` with `subprocess.run()` and ignored the result. The exit code became
    honest in the previous step of this phase, and nothing read it.
  - It started the frontend and the Flask API with `Popen` and never touched the handles
    again. Either could die at any point in the following four hours and the container
    would sit there, apparently fine, serving nothing.
  - It required a Discord webhook to start at all, and then used it for exactly one
    thing: announcing the daily login gift.

This module holds the parts of that worth testing on their own. `entrypoint.py` keeps the
cron arithmetic and the wiring.
"""

import logging
import subprocess

from backend import exceptions, miscellaneous

### ===============================================================================

### Discord embed colours
COLOUR_FAILURE = 16711680  # red
COLOUR_RECOVERY = 3066993  # green
COLOUR_WARNING = 16753920  # orange

### How long to wait before restarting a child that died, and how far to back off if it
### keeps dying. A frontend that crashes on startup would otherwise be restarted in a
### tight loop for as long as the container lives.
RESTART_DELAY_SECONDS = 5
MAX_RESTART_DELAY_SECONDS = 300

### How many errors from one stage to put in a Discord message. The embed has a length
### limit and a manifest where everything failed says the same thing eight times.
MAX_REPORTED_STAGES = 5


class Child:
    """### A long running process the container needs, and the state to restart it.

    Wraps Popen so that "is it still alive" is a question with an answer. Before this the
    handles were simply dropped on the floor.
    """

    def __init__(self, name: str, command: list, cwd: str = None, launcher=None):
        """### Describe a child process without starting it yet.

        Args:
            name (str): What to call it in the log and in an alert.
            command (list): The command to run.
            cwd (str): The working directory to run it in.
            launcher (callable): How to start it. Defaults to subprocess.Popen, and is
                replaced in tests.
        """
        self.name = name
        self.command = command
        self.cwd = cwd
        self.launcher = launcher or subprocess.Popen
        self.process = None
        self.restarts = 0
        self.last_exit_code = None
        self.last_restart_at = None

    def start(self) -> None:
        """### Start the process."""
        logging.info(f"Starting {self.name}: {' '.join(self.command)}")
        self.process = self.launcher(self.command, cwd=self.cwd)

    def is_alive(self) -> bool:
        """### Whether the process is still running.

        Returns:
            bool: False if it was never started or has exited.
        """
        if self.process is None:
            return False

        ### poll() returns None while the process is running, and the exit code once it
        ### is not. It also reaps the child, which is what stops a dead frontend from
        ### lingering as a zombie for the life of the container.
        code = self.process.poll()

        if code is None:
            return True

        self.last_exit_code = code

        return False

    def restart(self, now: float) -> None:
        """### Start the process again and count the attempt.

        Args:
            now (float): The current time as a unix timestamp.
        """
        self.restarts += 1
        self.last_restart_at = now
        logging.warning(f"{self.name} exited with code {self.last_exit_code}. "
                        f"Restarting it (attempt {self.restarts}).")
        self.start()

    def backoff_seconds(self) -> int:
        """### How long to leave this child down before trying again.

        Doubles per consecutive restart. A frontend that crashes on startup would
        otherwise be restarted in a tight loop for the life of the container, and the
        container log would be nothing but restart notices.

        Returns:
            int: Seconds to wait.
        """
        delay = RESTART_DELAY_SECONDS * (2 ** max(0, self.restarts - 1))

        return min(delay, MAX_RESTART_DELAY_SECONDS)

    def may_restart(self, now: float) -> bool:
        """### Whether enough time has passed since the last restart attempt.

        The wait happens here rather than by sleeping, so a child stuck in a crash loop
        does not hold up the scraper schedule as well.

        Args:
            now (float): The current time as a unix timestamp.

        Returns:
            bool: True if it may be started again now.
        """
        if self.last_restart_at is None:
            return True

        return now - self.last_restart_at >= self.backoff_seconds()

    def should_alert(self) -> bool:
        """### Whether this restart is worth a Discord message.

        The first three, then every tenth. A child that dies once is worth knowing about;
        one that dies every five minutes has already been reported, and the twentieth
        message about it is the one that gets the channel muted.

        Returns:
            bool: True if this restart should be announced.
        """
        return self.restarts <= 3 or self.restarts % 10 == 0


def check_children(children: list, now: float) -> list:
    """### Restart every child that has died and is due another attempt.

    Args:
        children (list): The Child objects to check.
        now (float): The current time as a unix timestamp.

    Returns:
        list: The children that were restarted, for alerting.
    """
    restarted = []

    for child in children:
        if child.is_alive():
            continue

        if not child.may_restart(now):
            continue

        ### Popen itself can fail - an OSError on fork under memory pressure is not exotic
        ### in a container running Node and Python side by side. Letting that out would
        ### end the one process whose entire job is to survive its children dying. The
        ### counters were already advanced by restart(), so the backoff applies to the
        ### next attempt just as it would after a normal restart.
        try:
            child.restart(now)
        except Exception as e:
            logging.error(f"Could not restart {child.name}: {type(e).__name__}: {e}. "
                          f"Leaving it down and trying again in {child.backoff_seconds()}s.")
            continue

        restarted.append(child)

    return restarted


def notify(title: str, message: str, colour: int, webhook: str) -> None:
    """### Send a Discord message without letting a webhook problem end the run.

    The alert is the last thing standing between a broken deployment and nobody noticing.
    It is not worth taking the supervisor down over.

    Caught broadly rather than as NotificatonException. Today that is the only type
    discord_notification() lets out, but the promise made here - the supervisor survives
    a notification problem - should not quietly depend on a property of another module.

    Args:
        title (str): Embed title.
        message (str): Embed body.
        colour (int): Embed colour.
        webhook (str): The Discord webhook URL.
    """
    if not webhook:
        logging.warning(f"No Discord webhook configured, not sending: {title}")
        return

    try:
        miscellaneous.discord_notification(title, message, colour, webhook)
    except Exception as e:
        logging.error(f"Could not send the Discord alert '{title}': {type(e).__name__}: {e}")


def describe_failed_run(manifest: dict) -> str:
    """### Turn a run manifest into something worth reading on a phone.

    Args:
        manifest (dict): The contents of ts_run_manifest.json.

    Returns:
        str: The message body.
    """
    if not manifest:
        return ("The run ended without leaving a manifest at all. That should not be "
                "possible - main.py writes one whatever happens - so something killed "
                "the process outright.")

    stages = manifest.get("stages") or []
    failed = [s for s in stages if s.get("status") != "ok"]

    lines = [f"Run `{manifest.get('runId', 'unknown')}`"]

    if not failed:
        ### A non-zero exit with nothing marked failed means the two disagree, and that is
        ### worth saying out loud rather than papering over
        lines.append("The run reported a failure but every stage is marked ok. "
                     "Check the container log.")
        return "\n".join(lines)

    lines.append(f"**{len(failed)} of {len(stages)} stages did not succeed:**")

    for stage in failed[:MAX_REPORTED_STAGES]:
        error = stage.get("error") or "no error recorded"
        lines.append(f"• `{stage['name']}` ({stage.get('status')}): {error}")

    if len(failed) > MAX_REPORTED_STAGES:
        lines.append(f"• ... and {len(failed) - MAX_REPORTED_STAGES} more")

    lines.append("")
    lines.append("The datasets behind those stages keep whatever the last successful run "
                 "wrote. The Dev tab marks them as out of date.")

    return "\n".join(lines)


class RunReporter:
    """### Decides which run outcomes are worth a Discord message.

    Alerting on every failed run turns a Kickbase outage into six messages a day, and the
    seventh is the one nobody reads. So: one message when the runs start failing, one when
    they start working again, and nothing in between.
    """

    def __init__(self, webhook: str):
        """### Start reporting to a webhook.

        Args:
            webhook (str): The Discord webhook URL.
        """
        self.webhook = webhook
        self.last_run_failed = False
        self.consecutive_failures = 0

    def report(self, exit_code: int, manifest: dict) -> str:
        """### Look at a finished run and alert if the situation changed.

        Args:
            exit_code (int): What main.py exited with. Non-zero means not every stage
                succeeded.
            manifest (dict): The contents of ts_run_manifest.json, if it could be read.

        Returns:
            str: "failure", "recovery" or None, for the log and for tests.
        """
        if exit_code != 0:
            self.consecutive_failures += 1

            if self.last_run_failed:
                logging.warning(f"Run failed again ({self.consecutive_failures} in a row). "
                                "Not alerting a second time.")
                self.last_run_failed = True
                return None

            self.last_run_failed = True
            notify("Kickbase Insights: run failed",
                   describe_failed_run(manifest), COLOUR_FAILURE, self.webhook)
            return "failure"

        if self.last_run_failed:
            failures = self.consecutive_failures
            self.last_run_failed = False
            self.consecutive_failures = 0
            notify("Kickbase Insights: back to normal",
                   f"A run completed with every stage ok, after {failures} failed one(s). "
                   f"Run `{(manifest or {}).get('runId', 'unknown')}`.",
                   COLOUR_RECOVERY, self.webhook)
            return "recovery"

        self.consecutive_failures = 0

        return None
