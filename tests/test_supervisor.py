"""Tests for the supervisor and the health endpoint.

entrypoint.py did three things and looked at none of them:

  - It ran main.py with subprocess.run() and threw the result away. The exit code became
    honest in the previous step of this phase, and still nothing read it.
  - It started the long lived servers with Popen and never touched the handles
    again. Either could die at any point in the four hours between runs, and the container
    would sit there apparently fine, serving nothing.
  - It required a Discord webhook to start at all, and used it for exactly one thing:
    announcing the daily login gift.

    ./venv/bin/python tests/test_supervisor.py
"""

import json
import sys
import tempfile

from datetime import datetime, timedelta, timezone
from os import makedirs, path

### Make the repository root importable regardless of where this is run from
sys.path.insert(0, path.dirname(path.dirname(path.abspath(__file__))))

from backend import exceptions, health, miscellaneous, supervisor

### ===============================================================================

PASSED = []

NOW = datetime(2026, 8, 13, 12, 0, 0, tzinfo=timezone.utc)


def check(name, fn):
    """Run a single test and record the result."""
    try:
        fn()
    except AssertionError as e:
        print(f"  FAIL  {name}\n        {e}")
        PASSED.append(False)
    except Exception as e:
        print(f"  ERROR {name}\n        {type(e).__name__}: {e}")
        PASSED.append(False)
    else:
        print(f"  ok    {name}")
        PASSED.append(True)


class FakeProcess:
    """Stands in for a Popen handle."""

    def __init__(self, exit_code=None):
        ### None while running, an exit code once it has stopped
        self.exit_code = exit_code

    def poll(self):
        return self.exit_code


class FakeLauncher:
    """Records every start and hands back a process that is alive."""

    def __init__(self):
        self.started = []

    def __call__(self, command, cwd=None):
        self.started.append({"command": command, "cwd": cwd})
        return FakeProcess()


def collect_notifications():
    """Swap the Discord notifier for one that records. Returns (messages, restore)."""
    messages = []
    original = miscellaneous.discord_notification

    def fake(title, message, colour, webhook):
        messages.append({"title": title, "message": message,
                         "colour": colour, "webhook": webhook})

    miscellaneous.discord_notification = fake

    def restore():
        miscellaneous.discord_notification = original

    return messages, restore


def manifest(stages, run_id="RUN-2", all_ok=None, finished=None):
    """A run manifest as main.py writes it."""
    return {
        "runId": run_id,
        "startedAt": (finished or NOW).isoformat(),
        "finishedAt": (finished or NOW).isoformat(),
        "allOk": all(s["status"] == "ok" for s in stages) if all_ok is None else all_ok,
        "abortedBy": None,
        "stages": stages,
    }


def ok(name):
    return {"name": name, "status": "ok", "durationSeconds": 1.0, "error": None}


def failed(name, error="KeyError: 'trp'"):
    return {"name": name, "status": "failed", "durationSeconds": 1.0, "error": error}


### ===============================================================================
### Watching the children
### ===============================================================================


def test_a_child_is_started_with_its_command_and_directory():
    launcher = FakeLauncher()
    child = supervisor.Child("flask api", ["python3", "-m", "flask", "run"], cwd="/code",
                             launcher=launcher)
    child.start()

    assert launcher.started == [{"command": ["python3", "-m", "flask", "run"], "cwd": "/code"}], \
        f"got {launcher.started}"


def test_a_running_child_is_left_alone():
    launcher = FakeLauncher()
    child = supervisor.Child("flask api", ["python3", "-m", "flask", "run"], launcher=launcher)
    child.start()

    assert supervisor.check_children([child], now=1000) == [], "a live child was restarted"
    assert len(launcher.started) == 1, f"started {len(launcher.started)} times"


def test_a_dead_child_is_restarted():
    """This is the whole point: the handles used to be dropped on the floor."""
    launcher = FakeLauncher()
    child = supervisor.Child("flask api", ["flask", "run"], launcher=launcher)
    child.start()
    child.process.exit_code = 1

    restarted = supervisor.check_children([child], now=1000)

    assert [c.name for c in restarted] == ["flask api"], f"got {restarted}"
    assert len(launcher.started) == 2, f"started {len(launcher.started)} times"
    assert child.last_exit_code == 1, f"got {child.last_exit_code}"


def test_a_child_that_was_never_started_counts_as_dead():
    launcher = FakeLauncher()
    child = supervisor.Child("flask api", ["python3", "-m", "flask", "run"], launcher=launcher)

    assert child.is_alive() is False
    supervisor.check_children([child], now=1000)
    assert len(launcher.started) == 1, "it should have been started"


def test_a_crash_loop_backs_off_instead_of_spinning():
    """A Flask that cannot start would otherwise be restarted every poll, forever."""
    launcher = FakeLauncher()
    child = supervisor.Child("flask api", ["python3", "-m", "flask", "run"], launcher=launcher)
    child.start()

    now = 1000
    for _ in range(4):
        child.process.exit_code = 1
        supervisor.check_children([child], now=now)
        now += 1  # a second later, well inside every backoff window

    ### One initial start plus a single restart: the rest were too early
    assert len(launcher.started) == 2, \
        f"expected the backoff to hold, got {len(launcher.started)} starts"


def test_the_backoff_lets_go_once_it_has_waited():
    launcher = FakeLauncher()
    child = supervisor.Child("flask api", ["python3", "-m", "flask", "run"], launcher=launcher)
    child.start()

    now = 1000
    for _ in range(3):
        child.process.exit_code = 1
        supervisor.check_children([child], now=now)
        now += supervisor.MAX_RESTART_DELAY_SECONDS + 1

    assert len(launcher.started) == 4, \
        f"expected every attempt to go through, got {len(launcher.started)}"


def test_the_backoff_is_capped():
    child = supervisor.Child("flask api", ["python3", "-m", "flask", "run"], launcher=FakeLauncher())
    child.restarts = 50

    assert child.backoff_seconds() == supervisor.MAX_RESTART_DELAY_SECONDS, \
        f"got {child.backoff_seconds()}"


def test_a_restart_that_itself_fails_does_not_end_the_supervisor():
    """Popen can raise - an OSError on fork under memory pressure is not exotic in a
    container running Node and Python side by side. Letting it out would end the one
    process whose entire job is to survive its children dying."""
    def refuses_to_start(command, cwd=None):
        raise OSError("Cannot allocate memory")

    child = supervisor.Child("flask api", ["python3", "-m", "flask", "run"], launcher=refuses_to_start)
    child.process = FakeProcess(exit_code=1)

    restarted = supervisor.check_children([child], now=1000)

    assert restarted == [], "a child that could not be started was reported as restarted"
    assert child.restarts == 1, "the attempt still counts, so the backoff applies"


def test_a_failed_restart_still_backs_off():
    def refuses_to_start(command, cwd=None):
        raise OSError("Cannot allocate memory")

    child = supervisor.Child("flask api", ["python3", "-m", "flask", "run"], launcher=refuses_to_start)
    child.process = FakeProcess(exit_code=1)

    supervisor.check_children([child], now=1000)
    supervisor.check_children([child], now=1001)

    assert child.restarts == 1, f"the second attempt was too early, got {child.restarts}"


def test_only_the_first_restarts_are_worth_a_message():
    """A child that dies every five minutes has already been reported."""
    child = supervisor.Child("flask api", ["python3", "-m", "flask", "run"], launcher=FakeLauncher())

    alerted = []
    for attempt in range(1, 26):
        child.restarts = attempt
        if child.should_alert():
            alerted.append(attempt)

    assert alerted == [1, 2, 3, 10, 20], f"got {alerted}"


### ===============================================================================
### Alerting on a run
### ===============================================================================


def test_a_failed_run_is_announced():
    """The webhook was a hard startup requirement that only ever announced a gift."""
    messages, restore = collect_notifications()
    try:
        reporter = supervisor.RunReporter("https://discord.test/hook")
        result = reporter.report(1, manifest([ok("market"), failed("turnovers")]))
    finally:
        restore()

    assert result == "failure", f"got {result}"
    assert len(messages) == 1, f"got {messages}"
    assert "turnovers" in messages[0]["message"], f"got {messages[0]['message']}"
    assert "KeyError" in messages[0]["message"], "the error itself belongs in the message"


def test_a_successful_run_says_nothing():
    messages, restore = collect_notifications()
    try:
        reporter = supervisor.RunReporter("https://discord.test/hook")
        result = reporter.report(0, manifest([ok("market")]))
    finally:
        restore()

    assert result is None, f"got {result}"
    assert messages == [], f"got {messages}"


def test_a_lasting_outage_is_announced_once():
    """Six identical messages a day is how a channel gets muted."""
    messages, restore = collect_notifications()
    try:
        reporter = supervisor.RunReporter("https://discord.test/hook")
        for _ in range(6):
            reporter.report(1, manifest([failed("market")]))
    finally:
        restore()

    assert len(messages) == 1, f"expected one alert for six failed runs, got {len(messages)}"
    assert reporter.consecutive_failures == 6, f"got {reporter.consecutive_failures}"


def test_recovery_is_announced_once():
    messages, restore = collect_notifications()
    try:
        reporter = supervisor.RunReporter("https://discord.test/hook")
        reporter.report(1, manifest([failed("market")]))
        result = reporter.report(0, manifest([ok("market")]))
        reporter.report(0, manifest([ok("market")]))
    finally:
        restore()

    assert result == "recovery", f"got {result}"
    assert len(messages) == 2, f"expected a failure and a recovery, got {len(messages)}"
    assert "back to normal" in messages[1]["title"], f"got {messages[1]['title']}"


def test_a_failure_after_a_recovery_is_announced_again():
    messages, restore = collect_notifications()
    try:
        reporter = supervisor.RunReporter("https://discord.test/hook")
        reporter.report(1, manifest([failed("market")]))
        reporter.report(0, manifest([ok("market")]))
        reporter.report(1, manifest([failed("market")]))
    finally:
        restore()

    assert len(messages) == 3, f"got {[m['title'] for m in messages]}"


def test_a_broken_webhook_does_not_take_the_supervisor_down():
    """The alert is the last thing between a broken deployment and nobody noticing. It is
    not worth ending the supervisor over - whatever shape the problem arrives in.

    NotificatonException is the only type discord_notification() lets out today. The
    promise made here should not quietly depend on that staying true, so a plain
    RuntimeError has to be survivable as well.
    """
    original = miscellaneous.discord_notification

    for failure in (exceptions.NotificatonException("Notification failed!"),
                    RuntimeError("something nobody predicted")):
        def explode(*args, **kwargs):
            raise failure

        miscellaneous.discord_notification = explode
        try:
            supervisor.notify("t", "m", 0, "https://discord.test/hook")
        finally:
            miscellaneous.discord_notification = original


def test_nothing_is_sent_without_a_webhook():
    messages, restore = collect_notifications()
    try:
        supervisor.notify("t", "m", 0, None)
    finally:
        restore()

    assert messages == [], f"got {messages}"


def test_a_non_zero_exit_with_no_failed_stage_is_reported_as_a_contradiction():
    """The two disagreeing is worth saying out loud rather than papering over."""
    body = supervisor.describe_failed_run(manifest([ok("market")], all_ok=False))

    assert "every stage is marked ok" in body, f"got {body}"


def test_a_missing_manifest_is_described_rather_than_crashed_on():
    body = supervisor.describe_failed_run(None)

    assert "without leaving a manifest" in body, f"got {body}"


def test_a_long_list_of_failures_is_cut_short():
    """The embed has a length limit, and eight identical errors say one thing."""
    stages = [failed(f"stage{i}") for i in range(8)]
    body = supervisor.describe_failed_run(manifest(stages))

    assert "and 3 more" in body, f"got {body}"


### ===============================================================================
### The health report
### ===============================================================================


class TempTimestamps:
    """Point the health module at a temporary timestamp directory."""

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ts_dir = path.join(self.tmp.name, "timestamps")
        makedirs(self.ts_dir, exist_ok=True)

        self.original = health.TIMESTAMP_DIR
        health.TIMESTAMP_DIR = self.ts_dir

        return self

    def __exit__(self, *exc):
        health.TIMESTAMP_DIR = self.original
        self.tmp.cleanup()
        return False

    def write(self, payload):
        with open(path.join(self.ts_dir, "ts_run_manifest.json"), "w") as f:
            json.dump(payload, f)


def test_a_clean_recent_run_is_ok():
    with TempTimestamps() as dirs:
        dirs.write(manifest([ok("market"), ok("balances")]))
        report = health.health_report(now=NOW + timedelta(minutes=5))

    assert report["status"] == health.OK, f"got {report}"
    assert health.is_healthy(report) is True


def test_a_failed_stage_is_degraded_but_still_serving():
    """A restart would not have made Kickbase answer, so this must not fail the check."""
    with TempTimestamps() as dirs:
        dirs.write(manifest([ok("market"), failed("turnovers")]))
        report = health.health_report(now=NOW + timedelta(minutes=5))

    assert report["status"] == health.DEGRADED, f"got {report}"
    assert health.is_healthy(report) is True, "a bad scrape is not a broken container"
    assert report["failedStages"] == ["turnovers"], f"got {report}"


def test_a_scheduler_that_stopped_is_unhealthy():
    """This is the case a restart does fix."""
    with TempTimestamps() as dirs:
        dirs.write(manifest([ok("market")]))
        report = health.health_report(now=NOW + timedelta(days=2))

    assert report["status"] == health.STALE, f"got {report}"
    assert health.is_healthy(report) is False


def test_a_missing_manifest_is_unhealthy():
    """The scraper has never completed once, which is a broken deployment."""
    with TempTimestamps():
        report = health.health_report(now=NOW)

    assert report["status"] == health.UNKNOWN, f"got {report}"
    assert health.is_healthy(report) is False


def test_an_unreadable_manifest_is_unhealthy():
    with TempTimestamps() as dirs:
        with open(path.join(dirs.ts_dir, "ts_run_manifest.json"), "w") as f:
            f.write('{"runId": "trunca')
        report = health.health_report(now=NOW)

    assert report["status"] == health.UNKNOWN, f"got {report}"


def test_staleness_follows_the_configured_schedule():
    """Changing RUN_SCHEDULE has to move the threshold with it, not invalidate it."""
    hourly = health.max_run_age("0 * * * *")
    four_hourly = health.max_run_age("10 2,6,10,14,18,22 * * *")

    assert hourly < four_hourly, f"hourly {hourly} vs four hourly {four_hourly}"
    assert four_hourly == timedelta(hours=9), f"got {four_hourly}"


def longest_real_gap(expression, samples=400):
    """The longest gap the expression actually produces, measured the slow honest way."""
    from croniter import croniter

    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    cron = croniter(expression, base)
    fires = [cron.get_next(datetime) for _ in range(samples)]

    return max(later - earlier for earlier, later in zip(fires, fires[1:]))


def test_an_uneven_schedule_is_measured_by_its_longest_gap():
    """`0 8,12,20 * * *` - morning, midday, evening - has a first gap of 4 hours and a
    real gap of 12 overnight. Measuring the first one reported the container as stale
    every night from about 05:00 until the 08:00 run finished, with nothing wrong.
    """
    threshold = health.max_run_age("0 8,12,20 * * *")

    assert threshold >= timedelta(hours=12), \
        f"the overnight gap is 12h, the threshold is {threshold}"


def test_no_ordinary_schedule_reports_a_healthy_container_as_stale():
    """The evenly spaced ones passed either way; the uneven ones are the point.

    `30 6,12,18 * * *` used to come out at 13h against a real 12h and pass by luck, which
    is worse than failing: trying one expression told you nothing about the next.
    """
    schedules = [
        "10 2,6,10,14,18,22 * * *",  # the default
        "0 8,12,20 * * *",           # morning, midday, evening
        "0 6,7 * * *",               # twice, an hour apart
        "0 7,8,9 * * *",             # three times, an hour apart
        "30 6,12,18 * * *",          # the one that used to pass by accident
        "0 * * * *",                 # hourly
        "*/5 * * * *",               # every five minutes
        "0 3 * * 1",                 # weekly
    ]

    too_tight = []
    for expression in schedules:
        threshold = health.max_run_age(expression)
        real = longest_real_gap(expression)

        if threshold < real:
            too_tight.append(f"{expression}: threshold {threshold} < real gap {real}")

    assert too_tight == [], f"these would report a healthy container as stale: {too_tight}"


def test_a_schedule_that_fires_constantly_does_not_hang_the_check():
    """A week of "every minute" is ten thousand steps, and the answer is obvious after
    twenty. The health endpoint has a ten second timeout in the Dockerfile."""
    import time

    started = time.perf_counter()
    threshold = health.max_run_age("* * * * *")
    elapsed = time.perf_counter() - started

    assert elapsed < 1.0, f"took {elapsed:.2f}s"
    assert threshold < timedelta(hours=2), f"got {threshold}"


def test_a_monthly_schedule_is_not_walked_for_a_full_week_of_fires():
    """The cycle is a week, but a monthly expression steps past it in two fires."""
    threshold = health.max_run_age("0 4 1 * *")

    assert threshold >= timedelta(days=28), f"got {threshold}"


def test_an_unreadable_schedule_falls_back_generously():
    """A wrong "stale" restarts a container that was fine."""
    assert health.max_run_age("not a cron expression") >= health.FALLBACK_MAX_AGE


def test_a_stale_run_is_reported_as_stale_even_when_every_stage_was_ok():
    """The stages are from whenever it last ran, which is the point."""
    with TempTimestamps() as dirs:
        dirs.write(manifest([ok("market")]))
        report = health.health_report(now=NOW + timedelta(days=3))

    assert report["status"] == health.STALE, f"got {report}"


def test_the_report_says_how_old_the_run_is():
    with TempTimestamps() as dirs:
        dirs.write(manifest([ok("market")]))
        report = health.health_report(now=NOW + timedelta(hours=2))

    assert report["lastRunAgeSeconds"] == 7200, f"got {report}"


### ===============================================================================
### The endpoint
### ===============================================================================


def call_health():
    """Call /api/health through Flask's test client."""
    import app

    client = app.app.test_client()
    response = client.get("/api/health")

    return response.status_code, response.get_json()


def test_the_endpoint_answers_200_when_all_is_well():
    with TempTimestamps() as dirs:
        dirs.write(manifest([ok("market")], finished=datetime.now(timezone.utc)))
        status, payload = call_health()

    assert status == 200, f"got {status}: {payload}"
    assert payload["status"] == health.OK, f"got {payload}"


def test_the_endpoint_answers_200_when_a_stage_failed():
    with TempTimestamps() as dirs:
        dirs.write(manifest([ok("market"), failed("turnovers")],
                            finished=datetime.now(timezone.utc)))
        status, payload = call_health()

    assert status == 200, f"a bad scrape must not read as a broken container: {payload}"
    assert payload["status"] == health.DEGRADED, f"got {payload}"


def test_the_endpoint_answers_503_when_the_scheduler_stopped():
    with TempTimestamps() as dirs:
        dirs.write(manifest([ok("market")],
                            finished=datetime.now(timezone.utc) - timedelta(days=2)))
        status, payload = call_health()

    assert status == 503, f"got {status}: {payload}"
    assert payload["status"] == health.STALE, f"got {payload}"


def test_the_endpoint_answers_503_without_a_manifest():
    with TempTimestamps():
        status, payload = call_health()

    assert status == 503, f"got {status}: {payload}"


def test_the_endpoint_names_the_run_it_judged():
    with TempTimestamps() as dirs:
        dirs.write(manifest([ok("market")], run_id="20260813T120000Z-abcd",
                            finished=datetime.now(timezone.utc)))
        _, payload = call_health()

    assert payload["runId"] == "20260813T120000Z-abcd", f"got {payload}"


### ===============================================================================
### entrypoint.py
###
### Importable at all only since this step: the environment checks and the whole script
### body used to run at import time, so the part that decides whether a run counted as a
### success could not be tested.
### ===============================================================================


class FakeResult:
    """Stands in for a CompletedProcess."""

    def __init__(self, returncode):
        self.returncode = returncode


def run_entrypoint_scraper(returncode, manifest_payload):
    """Call entrypoint.run_scraper() with a stubbed main.py and manifest.

    Returns:
        tuple: the exit code it reported, and the Discord messages it caused.
    """
    import entrypoint

    messages, restore = collect_notifications()
    tmp = tempfile.TemporaryDirectory()
    ts_dir = path.join(tmp.name, "timestamps")
    makedirs(ts_dir, exist_ok=True)

    if manifest_payload is not None:
        with open(path.join(ts_dir, "ts_run_manifest.json"), "w") as f:
            json.dump(manifest_payload, f)

    original = (entrypoint.subprocess.run, entrypoint.TIMESTAMP_DIR)
    entrypoint.TIMESTAMP_DIR = ts_dir

    try:
        entrypoint.subprocess.run = lambda *a, **kw: FakeResult(returncode)
        reporter = supervisor.RunReporter("https://discord.test/hook")
        code = entrypoint.run_scraper(reporter)
    finally:
        entrypoint.subprocess.run, entrypoint.TIMESTAMP_DIR = original
        restore()
        tmp.cleanup()

    return code, messages


def test_the_entrypoint_reads_the_exit_code():
    """subprocess.run() returned a result that was thrown away."""
    code, _ = run_entrypoint_scraper(1, manifest([failed("turnovers")]))

    assert code == 1, f"got {code}"


def test_a_failed_run_reaches_discord_from_the_entrypoint():
    _, messages = run_entrypoint_scraper(1, manifest([ok("market"), failed("turnovers")]))

    assert len(messages) == 1, f"got {messages}"
    assert "turnovers" in messages[0]["message"], f"got {messages[0]['message']}"


def test_a_successful_run_reaches_nobody():
    _, messages = run_entrypoint_scraper(0, manifest([ok("market")]))

    assert messages == [], f"got {messages}"


def test_a_failed_run_without_a_manifest_is_still_reported():
    """Should not be possible - main.py writes one whatever happens - but a supervisor
    that crashes on a missing file is worse than one that says so."""
    _, messages = run_entrypoint_scraper(1, None)

    assert len(messages) == 1, f"got {messages}"


def test_the_entrypoint_and_the_health_check_agree_on_the_schedule():
    """Two different defaults would mean the staleness threshold silently stops matching."""
    import entrypoint

    assert entrypoint.DEFAULT_RUN_SCHEDULE == health.DEFAULT_RUN_SCHEDULE


def test_importing_the_entrypoint_starts_nothing():
    """It used to run the environment checks, npm install and the supervisor loop at
    import time, which is why none of it could be tested."""
    import entrypoint

    assert callable(entrypoint.check_environment)
    assert callable(entrypoint.supervise)
    assert callable(entrypoint.build_children)


def test_the_child_is_the_one_process_the_container_serves_from():
    """It used to be two: a create-react-app dev server on 3000 with the data compiled into
    the bundle, and Flask on 5000. Flask serves the prebuilt frontend as well now."""
    import entrypoint

    children = entrypoint.build_children()

    assert [c.name for c in children] == ["flask api"], f"got {[c.name for c in children]}"
    assert all(c.process is None for c in children), "building must not start them"


def test_the_flask_port_is_configurable():
    """The healthcheck in the Dockerfile reads the same variable, so the two cannot disagree
    about which port to ask."""
    import entrypoint

    from os import environ

    original = environ.get("FLASK_PORT")
    environ["FLASK_PORT"] = "8080"

    try:
        assert "--port=8080" in entrypoint.build_children()[0].command
    finally:
        if original is None:
            del environ["FLASK_PORT"]
        else:
            environ["FLASK_PORT"] = original


def test_nothing_in_the_entrypoint_runs_npm():
    """npm install on every start and a dev server in production were both consequences of the
    data being compiled into the bundle. It is fetched now."""
    source = (path.join(path.dirname(path.dirname(path.abspath(__file__))), "entrypoint.py"))

    with open(source, "r") as f:
        text = f.read()

    ### The quoted form is how it would be invoked; the prose around it is allowed to keep
    ### saying what used to happen here.
    assert '"npm"' not in text, "the entrypoint must not reach for npm any more"
    assert "sleep(120)" not in text, "the two startup sleeps waited for servers nobody polled"


### ===============================================================================

if __name__ == "__main__":
    print("watching the children")
    check("a child is started with its command and directory", test_a_child_is_started_with_its_command_and_directory)
    check("a running child is left alone", test_a_running_child_is_left_alone)
    check("a dead child is restarted", test_a_dead_child_is_restarted)
    check("a child that was never started counts as dead", test_a_child_that_was_never_started_counts_as_dead)
    check("a crash loop backs off", test_a_crash_loop_backs_off_instead_of_spinning)
    check("the backoff lets go once it has waited", test_the_backoff_lets_go_once_it_has_waited)
    check("the backoff is capped", test_the_backoff_is_capped)
    check("a restart that itself fails does not end the supervisor", test_a_restart_that_itself_fails_does_not_end_the_supervisor)
    check("a failed restart still backs off", test_a_failed_restart_still_backs_off)
    check("only the first restarts are worth a message", test_only_the_first_restarts_are_worth_a_message)

    print("\nalerting on a run")
    check("a failed run is announced", test_a_failed_run_is_announced)
    check("a successful run says nothing", test_a_successful_run_says_nothing)
    check("a lasting outage is announced once", test_a_lasting_outage_is_announced_once)
    check("recovery is announced once", test_recovery_is_announced_once)
    check("a failure after a recovery is announced again", test_a_failure_after_a_recovery_is_announced_again)
    check("a broken webhook does not take the supervisor down", test_a_broken_webhook_does_not_take_the_supervisor_down)
    check("nothing is sent without a webhook", test_nothing_is_sent_without_a_webhook)
    check("a contradiction is reported as one", test_a_non_zero_exit_with_no_failed_stage_is_reported_as_a_contradiction)
    check("a missing manifest is described", test_a_missing_manifest_is_described_rather_than_crashed_on)
    check("a long list of failures is cut short", test_a_long_list_of_failures_is_cut_short)

    print("\nthe health report")
    check("a clean recent run is ok", test_a_clean_recent_run_is_ok)
    check("a failed stage is degraded but still serving", test_a_failed_stage_is_degraded_but_still_serving)
    check("a scheduler that stopped is unhealthy", test_a_scheduler_that_stopped_is_unhealthy)
    check("a missing manifest is unhealthy", test_a_missing_manifest_is_unhealthy)
    check("an unreadable manifest is unhealthy", test_an_unreadable_manifest_is_unhealthy)
    check("staleness follows the configured schedule", test_staleness_follows_the_configured_schedule)
    check("an uneven schedule is measured by its longest gap", test_an_uneven_schedule_is_measured_by_its_longest_gap)
    check("no ordinary schedule reports a healthy container as stale", test_no_ordinary_schedule_reports_a_healthy_container_as_stale)
    check("a constant schedule does not hang the check", test_a_schedule_that_fires_constantly_does_not_hang_the_check)
    check("a monthly schedule is not walked for a full week", test_a_monthly_schedule_is_not_walked_for_a_full_week_of_fires)
    check("an unreadable schedule falls back generously", test_an_unreadable_schedule_falls_back_generously)
    check("a stale run is stale even with every stage ok", test_a_stale_run_is_reported_as_stale_even_when_every_stage_was_ok)
    check("the report says how old the run is", test_the_report_says_how_old_the_run_is)

    print("\nthe endpoint")
    check("answers 200 when all is well", test_the_endpoint_answers_200_when_all_is_well)
    check("answers 200 when a stage failed", test_the_endpoint_answers_200_when_a_stage_failed)
    check("answers 503 when the scheduler stopped", test_the_endpoint_answers_503_when_the_scheduler_stopped)
    check("answers 503 without a manifest", test_the_endpoint_answers_503_without_a_manifest)
    check("names the run it judged", test_the_endpoint_names_the_run_it_judged)

    print("\nentrypoint.py")
    check("reads the exit code", test_the_entrypoint_reads_the_exit_code)
    check("a failed run reaches Discord", test_a_failed_run_reaches_discord_from_the_entrypoint)
    check("a successful run reaches nobody", test_a_successful_run_reaches_nobody)
    check("a failed run without a manifest is still reported", test_a_failed_run_without_a_manifest_is_still_reported)
    check("entrypoint and health agree on the schedule", test_the_entrypoint_and_the_health_check_agree_on_the_schedule)
    check("importing the entrypoint starts nothing", test_importing_the_entrypoint_starts_nothing)
    check("the child is the one served process", test_the_child_is_the_one_process_the_container_serves_from)
    check("the flask port is configurable", test_the_flask_port_is_configurable)
    check("nothing in the entrypoint runs npm", test_nothing_in_the_entrypoint_runs_npm)

    total, passed = len(PASSED), sum(PASSED)
    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)
