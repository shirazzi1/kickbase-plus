"""
### Whether this deployment is actually doing its job.

The container had no health signal at all. Flask could die, the scheduler could stop, and
the only symptom was a website whose numbers quietly stopped moving - which is how the
whole "the pipeline lies about failure" problem showed itself to begin with.

The report below distinguishes two questions that are easy to conflate:

  - **Is the service up?** Answering this request at all already says yes.
  - **Is the data being kept up to date?** That is what the run manifest knows.

They get different HTTP statuses on purpose, because a Docker healthcheck is a restart
signal and not every problem is one a restart can fix:

  - A stage that failed against a Kickbase outage is `degraded` and stays **200**.
    Restarting the container would not have made Kickbase answer.
  - No run for far longer than the schedule allows is `stale` and gives **503**. That
    means the scheduler itself has stopped, and a restart is exactly the right response.
  - No readable manifest is `unknown` and gives **503**: the scraper has never completed
    once, which is a broken deployment rather than a bad day.
"""

import json
import logging

from datetime import datetime, timedelta, timezone
from os import getenv, path

from backend.paths import TIMESTAMP_DIR

### ===============================================================================

OK = "ok"
DEGRADED = "degraded"
STALE = "stale"
UNKNOWN = "unknown"

### Statuses a Docker healthcheck should act on. Everything else answers 200, because a
### restart would not improve it.
UNHEALTHY = (STALE, UNKNOWN)

### The same default entrypoint.py uses, so the two cannot disagree about how often a run
### is expected.
DEFAULT_RUN_SCHEDULE = "10 2,6,10,14,18,22 * * *"

### How many missed runs to tolerate before calling the data stale. Two, because a single
### failed run is a bad day and not a stopped scheduler.
MISSED_RUNS_ALLOWED = 2

### Slack on top, for a run that takes a while and for clock drift.
STALENESS_SLACK = timedelta(hours=1)

### Used when RUN_SCHEDULE cannot be parsed at all. Generous on purpose: a wrong "stale"
### restarts a container that was fine.
FALLBACK_MAX_AGE = timedelta(hours=12)


def expected_run_interval(schedule: str = None) -> timedelta:
    """### How long the configured schedule leaves between two runs.

    Read off the cron expression rather than configured separately, so changing
    RUN_SCHEDULE moves the staleness threshold with it instead of quietly invalidating it.

    Args:
        schedule (str): A cron expression. Defaults to RUN_SCHEDULE.

    Returns:
        timedelta: The gap between the next two runs, or the fallback if the expression
            cannot be read.
    """
    expression = schedule or getenv("RUN_SCHEDULE", DEFAULT_RUN_SCHEDULE)

    try:
        from croniter import croniter

        ### A fixed base, so the answer does not depend on when this is asked. The gap
        ### between two fires is what matters, not which two.
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        cron = croniter(expression, base)
        first = cron.get_next(datetime)
        second = cron.get_next(datetime)

        return second - first
    except Exception as e:
        logging.warning(f"Could not read RUN_SCHEDULE '{expression}': {e}. "
                        f"Falling back to {FALLBACK_MAX_AGE} for the staleness check.")
        return FALLBACK_MAX_AGE


def max_run_age(schedule: str = None) -> timedelta:
    """### How old the last run may be before the data counts as stale.

    Args:
        schedule (str): A cron expression. Defaults to RUN_SCHEDULE.

    Returns:
        timedelta: The threshold.
    """
    interval = expected_run_interval(schedule)

    return interval * MISSED_RUNS_ALLOWED + STALENESS_SLACK


def read_manifest() -> dict:
    """### Read the last run's manifest.

    Returns:
        dict: The manifest, or None if there is none that can be read.
    """
    manifest_path = path.join(TIMESTAMP_DIR, "ts_run_manifest.json")

    if not path.exists(manifest_path):
        return None

    try:
        with open(manifest_path, "r") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logging.warning(f"Could not read the run manifest: {e}")
        return None


def health_report(now: datetime = None, schedule: str = None) -> dict:
    """### What to answer a health check with.

    Args:
        now (datetime): The instant to judge against, normally now.
        schedule (str): A cron expression. Defaults to RUN_SCHEDULE.

    Returns:
        dict: status, a human readable reason, and the run it is based on.
    """
    now = now or datetime.now(timezone.utc)
    manifest = read_manifest()

    if not manifest or not manifest.get("runId"):
        return {
            "status": UNKNOWN,
            "reason": "No run manifest yet. The scraper has never completed a run.",
            "runId": None,
            "allOk": None,
            "failedStages": [],
            "lastRunAgeSeconds": None,
        }

    failed = [s["name"] for s in manifest.get("stages", []) if s.get("status") != "ok"]
    age = _age_of(manifest, now)
    threshold = max_run_age(schedule)

    report = {
        "status": OK,
        "reason": "The last run completed and every stage succeeded.",
        "runId": manifest["runId"],
        "allOk": manifest.get("allOk"),
        "failedStages": failed,
        "lastRunAgeSeconds": int(age.total_seconds()) if age is not None else None,
    }

    ### Staleness first: a scheduler that has stopped is the worse problem, and the stages
    ### it reports are from whenever it last ran.
    if age is None:
        report["status"] = UNKNOWN
        report["reason"] = "The run manifest carries no usable finish time."
    elif age > threshold:
        report["status"] = STALE
        report["reason"] = (f"The last run finished {_describe(age)} ago, which is longer "
                            f"than the {_describe(threshold)} the schedule allows. "
                            "The scheduler has probably stopped.")
    elif not manifest.get("allOk"):
        report["status"] = DEGRADED
        report["reason"] = (f"The last run finished {_describe(age)} ago but "
                            f"{len(failed)} stage(s) did not succeed: {', '.join(failed)}.")

    return report


def is_healthy(report: dict) -> bool:
    """### Whether a health report should answer 200.

    Args:
        report (dict): A health_report().

    Returns:
        bool: False only for the problems a restart could actually fix.
    """
    return report["status"] not in UNHEALTHY


def _age_of(manifest: dict, now: datetime):
    """### How long ago the run finished.

    Args:
        manifest (dict): The run manifest.
        now (datetime): The instant to measure against.

    Returns:
        timedelta: The age, or None if the manifest carries no usable finish time.
    """
    finished = manifest.get("finishedAt") or manifest.get("startedAt")

    if not finished:
        return None

    try:
        parsed = datetime.fromisoformat(str(finished).replace("Z", "+00:00"))
    except ValueError:
        return None

    ### A manifest written by an older version may carry a naive timestamp
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return now - parsed


def _describe(span: timedelta) -> str:
    """### A short, readable duration for a health message.

    Args:
        span (timedelta): The duration.

    Returns:
        str: e.g. "2h 15m".
    """
    total = int(max(0, span.total_seconds()))
    hours, remainder = divmod(total, 3600)
    minutes = remainder // 60

    if hours:
        return f"{hours}h {minutes}m"

    return f"{minutes}m"
