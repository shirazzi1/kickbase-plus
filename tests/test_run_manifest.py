"""Tests for the run manifest and for main() no longer lying about failure.

Before this, main() was one try around eight calls. The first exception aborted every
stage behind it, and then `ts_main.json` was stamped fresh unconditionally - so a run that
died at the first stage looked, from the frontend, exactly like a perfect one. Somebody
reading the market values had no way to tell whether they were minutes or days old.

Per-stage isolation on its own would make that worse, not better: the files a failed stage
did not write simply keep their previous content, so a partly failed run looks complete.
The manifest and the per-dataset run id are what stop that.

    ./venv/bin/python tests/test_run_manifest.py
"""

import json
import sys
import tempfile

from os import makedirs, path

### Make the repository root importable regardless of where this is run from
sys.path.insert(0, path.dirname(path.dirname(path.abspath(__file__))))

from backend import exceptions, miscellaneous, runs

### ===============================================================================

PASSED = []


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


def manifest():
    """A manifest with a fixed run id."""
    return runs.RunManifest("20260813T120000Z-test")


def boom(exception):
    """A stage that fails with the given exception."""
    def stage():
        raise exception
    return stage


### ===============================================================================
### Run ids
### ===============================================================================


def test_a_run_id_sorts_by_time():
    """A directory listing or a log grep should read in order."""
    first = runs.new_run_id()
    second = runs.new_run_id()

    ### Same second, so only the prefix is comparable - but it must be the leading part
    assert first[:16] <= second[:16], f"{first} then {second}"
    assert first.startswith("20"), f"expected a timestamp prefix, got {first}"


def test_two_runs_in_the_same_second_stay_apart():
    """A run that fails early and is restarted lands in the same second."""
    ids = {runs.new_run_id() for _ in range(50)}
    assert len(ids) == 50, f"only {len(ids)} distinct ids out of 50"


def test_the_current_run_id_is_the_one_that_was_started():
    runs.start_run("abc")
    try:
        assert runs.current_run_id() == "abc"
    finally:
        runs.end_run()


def test_there_is_no_current_run_after_it_ended():
    """app.py is long lived and writes data files too; it must not borrow a run id."""
    runs.start_run("abc")
    runs.end_run()

    assert runs.current_run_id() is None


### ===============================================================================
### One stage at a time
### ===============================================================================


def test_a_successful_stage_is_recorded():
    m = manifest()
    assert m.run("market", lambda: None) is True

    assert [s["name"] for s in m.stages] == ["market"], f"got {m.stages}"
    assert m.stages[0]["status"] == runs.OK, f"got {m.stages[0]}"
    assert m.stages[0]["error"] is None, f"got {m.stages[0]}"


def test_a_failing_stage_does_not_take_the_next_one_with_it():
    """The whole point: a broken turnovers calculation must not cost the market data."""
    m = manifest()
    ran = []

    m.run("turnovers", boom(ValueError("nope")))
    m.run("market", lambda: ran.append("market"))

    assert ran == ["market"], "the stage behind the failure did not run"
    assert m.stages[0]["status"] == runs.FAILED, f"got {m.stages[0]}"
    assert m.stages[1]["status"] == runs.OK, f"got {m.stages[1]}"


def test_a_failure_is_recorded_with_its_type_and_message():
    m = manifest()
    m.run("turnovers", boom(KeyError("trp")))

    assert "KeyError" in m.stages[0]["error"], f"got {m.stages[0]['error']}"
    assert "trp" in m.stages[0]["error"], f"got {m.stages[0]['error']}"


def test_a_stage_records_how_long_it_took():
    m = manifest()
    m.run("market", lambda: None)

    assert m.stages[0]["durationSeconds"] >= 0, f"got {m.stages[0]}"


def test_an_expired_token_stops_the_run():
    """Nothing behind it can succeed, and carrying on means seven more rounds of
    retries and backoff against an API that is already unhappy with us."""
    m = manifest()
    ran = []

    m.run("market", boom(exceptions.AuthExpiredException("401")))
    m.run("balances", lambda: ran.append("balances"))
    m.run("turnovers", lambda: ran.append("turnovers"))

    assert ran == [], f"stages kept running after the token was gone: {ran}"
    assert m.aborted_by == "market", f"got {m.aborted_by}"
    assert [s["status"] for s in m.stages] == [runs.FAILED, runs.SKIPPED, runs.SKIPPED], \
        f"got {[s['status'] for s in m.stages]}"


def test_a_skipped_stage_says_why():
    m = manifest()
    m.run("market", boom(exceptions.AuthExpiredException("401")))
    m.run("balances", lambda: None)

    assert "market" in m.stages[1]["error"], f"got {m.stages[1]['error']}"


def test_an_ordinary_failure_does_not_stop_the_run():
    """Only the fatal ones do. A rate limit or a bad payload is one stage's problem."""
    m = manifest()
    ran = []

    m.run("market", boom(exceptions.RateLimitedException("429")))
    m.run("balances", lambda: ran.append("balances"))

    assert ran == ["balances"], f"got {ran}"
    assert m.aborted_by is None, f"got {m.aborted_by}"


### ===============================================================================
### The verdict
### ===============================================================================


def test_all_ok_when_every_stage_succeeded():
    m = manifest()
    m.run("market", lambda: None)
    m.run("balances", lambda: None)

    assert m.all_ok is True, f"got {m.to_dict()}"
    assert m.failed_stages == [], f"got {m.failed_stages}"


def test_not_all_ok_when_one_stage_failed():
    m = manifest()
    m.run("market", lambda: None)
    m.run("balances", boom(ValueError("nope")))

    assert m.all_ok is False, f"got {m.to_dict()}"
    assert m.failed_stages == ["balances"], f"got {m.failed_stages}"


def test_a_run_with_no_stages_is_not_ok():
    """A run that got no further than the login has nothing to show, and calling that
    a success is the exact lie this replaces."""
    assert manifest().all_ok is False


def test_a_skipped_stage_counts_as_not_ok():
    m = manifest()
    m.run("market", boom(exceptions.AuthExpiredException("401")))
    m.run("balances", lambda: None)

    assert m.all_ok is False, f"got {m.to_dict()}"
    assert m.failed_stages == ["market", "balances"], f"got {m.failed_stages}"


def test_the_summary_names_what_went_wrong():
    m = manifest()
    m.run("market", lambda: None)
    m.run("balances", boom(ValueError("nope")))

    summary = m.summary()
    assert "balances" in summary, f"got {summary}"
    assert "1/2" in summary, f"got {summary}"


def test_the_manifest_carries_the_run_id_and_every_stage():
    m = manifest()
    m.run("market", lambda: None)
    m.finish()

    payload = m.to_dict()
    assert payload["runId"] == "20260813T120000Z-test", f"got {payload}"
    assert payload["allOk"] is True, f"got {payload}"
    assert payload["finishedAt"] is not None, f"got {payload}"
    assert [s["name"] for s in payload["stages"]] == ["market"], f"got {payload}"


def test_the_manifest_is_json_serialisable():
    """It is written to disk, so a datetime left in it would fail the run at the end."""
    m = manifest()
    m.run("market", boom(ValueError("nope")))
    m.finish()

    json.dumps(m.to_dict())


### ===============================================================================
### main() end to end
### ===============================================================================


def run_main(stage_results):
    """Run main() with a stubbed login and stubbed stages.

    Args:
        stage_results (dict): stage name -> None to succeed, an exception to raise, or a
            callable to run as the stage body.

    Returns:
        tuple: the manifest and the timestamp directory it wrote into.
    """
    import main
    from backend.kickbase.v4 import leagues

    tmp = tempfile.TemporaryDirectory()
    data_dir = path.join(tmp.name, "data")
    ts_dir = path.join(data_dir, "timestamps")
    makedirs(ts_dir, exist_ok=True)

    def fake_stage(name):
        def stage():
            outcome = stage_results.get(name)

            if isinstance(outcome, BaseException):
                raise outcome
            if callable(outcome):
                outcome()
        return stage

    original = (main.DATA_DIR, main.LOG_DIR, main.TIMESTAMP_DIR,
                miscellaneous.DATA_DIR, miscellaneous.TIMESTAMP_DIR,
                miscellaneous.LAST_GOOD_DIR,
                main.login, main.build_stages, leagues.clear_caches)

    main.DATA_DIR = data_dir
    main.LOG_DIR = path.join(tmp.name, "logs")
    main.TIMESTAMP_DIR = ts_dir
    miscellaneous.DATA_DIR = data_dir
    miscellaneous.TIMESTAMP_DIR = ts_dir
    miscellaneous.LAST_GOOD_DIR = path.join(tmp.name, "last-good")

    try:
        main.login = lambda: (object(), "token", "1")
        main.build_stages = lambda *a: [(name, fake_stage(name)) for name in stage_results]
        leagues.clear_caches = lambda: None

        return main.main(), ts_dir, tmp
    finally:
        (main.DATA_DIR, main.LOG_DIR, main.TIMESTAMP_DIR,
         miscellaneous.DATA_DIR, miscellaneous.TIMESTAMP_DIR,
         miscellaneous.LAST_GOOD_DIR,
         main.login, main.build_stages, leagues.clear_caches) = original


def test_main_runs_every_stage_and_reports_all_ok():
    from os import environ
    environ["START_DATE"] = "2026-08-01T18:00:00Z"

    result, _, tmp = run_main({"market": None, "balances": None})
    try:
        assert result.all_ok is True, f"got {result.to_dict()}"
        assert [s["name"] for s in result.stages] == ["market", "balances"], f"got {result.stages}"
    finally:
        tmp.cleanup()
        runs.end_run()


def test_main_keeps_going_after_one_stage_fails():
    from os import environ
    environ["START_DATE"] = "2026-08-01T18:00:00Z"

    result, _, tmp = run_main({"market": ValueError("nope"), "balances": None})
    try:
        assert result.all_ok is False, f"got {result.to_dict()}"
        assert result.failed_stages == ["market"], f"got {result.failed_stages}"
        assert result.stages[1]["status"] == runs.OK, "the stage behind the failure must still run"
    finally:
        tmp.cleanup()
        runs.end_run()


def test_every_stage_writes_under_the_same_run_id():
    """This is what lets the frontend tell "written by this run" from "left over"."""
    from os import environ
    environ["START_DATE"] = "2026-08-01T18:00:00Z"

    result, ts_dir, tmp = run_main({
        "market": lambda: miscellaneous.write_timestamp("ts_market.json", rows=3),
    })

    try:
        with open(path.join(ts_dir, "ts_market.json")) as f:
            stamp = json.load(f)
    finally:
        tmp.cleanup()
        runs.end_run()

    assert stamp["runId"] == result.run_id, \
        f"dataset says {stamp['runId']}, run says {result.run_id}"
    assert stamp["rows"] == 3, f"got {stamp}"


def test_a_dataset_a_failed_stage_did_not_rewrite_keeps_the_older_run_id():
    """The load bearing risk of per-stage isolation: the file is still there, still
    plausible, and only the run id says it is a run behind."""
    from os import environ
    environ["START_DATE"] = "2026-08-01T18:00:00Z"

    ### First run writes the dataset
    first, ts_dir, tmp = run_main({
        "market": lambda: miscellaneous.write_timestamp("ts_market.json", rows=3),
    })
    runs.end_run()

    try:
        ### Second run fails that stage, so nothing rewrites the timestamp
        original = (miscellaneous.TIMESTAMP_DIR,)
        miscellaneous.TIMESTAMP_DIR = ts_dir
        try:
            second = runs.RunManifest(runs.start_run())
            second.run("market", boom(ValueError("nope")))
        finally:
            (miscellaneous.TIMESTAMP_DIR,) = original
            runs.end_run()

        with open(path.join(ts_dir, "ts_market.json")) as f:
            stamp = json.load(f)
    finally:
        tmp.cleanup()

    assert stamp["runId"] == first.run_id, \
        "the dataset must still name the run that actually wrote it"
    assert stamp["runId"] != second.run_id, \
        "a stale dataset must not carry the current run id"


def test_a_failed_login_produces_a_manifest_that_is_not_ok():
    """It used to print the exception and return, and the run still stamped itself fresh."""
    from os import environ
    import main
    from backend.kickbase.v4 import leagues

    environ["START_DATE"] = "2026-08-01T18:00:00Z"

    tmp = tempfile.TemporaryDirectory()
    ts_dir = path.join(tmp.name, "data", "timestamps")
    makedirs(ts_dir, exist_ok=True)

    original = (main.LOG_DIR, main.TIMESTAMP_DIR, miscellaneous.TIMESTAMP_DIR,
                main.login, leagues.clear_caches)
    main.LOG_DIR = path.join(tmp.name, "logs")
    main.TIMESTAMP_DIR = ts_dir
    miscellaneous.TIMESTAMP_DIR = ts_dir

    try:
        def failing_login():
            raise exceptions.LoginException("[CRITICAL] Login failed!")

        main.login = failing_login
        leagues.clear_caches = lambda: None

        result = main.main()
    finally:
        (main.LOG_DIR, main.TIMESTAMP_DIR, miscellaneous.TIMESTAMP_DIR,
         main.login, leagues.clear_caches) = original
        tmp.cleanup()
        runs.end_run()

    assert result.all_ok is False, f"got {result.to_dict()}"
    ### The login is not a stage, but it is recorded as one when it fails: an empty
    ### manifest would say nothing about why the run produced nothing
    assert [s["name"] for s in result.stages] == ["login"], f"got {result.stages}"
    assert result.stages[0]["status"] == runs.FAILED, f"got {result.stages[0]}"


### ===============================================================================
### run_once(): whatever kills the run, it still leaves a record
###
### The manifest is only worth anything if it is always written. A path that ends the
### process without one is worse than having none at all: the previous run's manifest
### stays on disk, every dataset still carries that run's id, and the frontend therefore
### renders every single table as current - over a run that never happened.
### ===============================================================================


def run_once_with(login_failure):
    """Run run_once() with a login that fails in the given way.

    Returns:
        tuple: the manifest it wrote, and the manifest and ts_main it left on disk.
    """
    import main
    from backend.kickbase.v4 import leagues

    tmp = tempfile.TemporaryDirectory()
    data_dir = path.join(tmp.name, "data")
    ts_dir = path.join(data_dir, "timestamps")
    makedirs(ts_dir, exist_ok=True)

    ### What a previous, successful run left behind
    with open(path.join(ts_dir, "ts_run_manifest.json"), "w") as f:
        json.dump({"runId": "RUN-1", "allOk": True,
                   "stages": [{"name": "market", "status": "ok"}]}, f)
    with open(path.join(ts_dir, "ts_main.json"), "w") as f:
        json.dump({"time": "2026-08-13T05:00:00", "runId": "RUN-1", "allOk": True}, f)

    original = (main.LOG_DIR, main.TIMESTAMP_DIR, main.DATA_DIR,
                miscellaneous.DATA_DIR, miscellaneous.TIMESTAMP_DIR,
                miscellaneous.LAST_GOOD_DIR,
                main.login, leagues.clear_caches)

    main.LOG_DIR = path.join(tmp.name, "logs")
    main.TIMESTAMP_DIR = ts_dir
    main.DATA_DIR = data_dir
    miscellaneous.DATA_DIR = data_dir
    miscellaneous.TIMESTAMP_DIR = ts_dir
    miscellaneous.LAST_GOOD_DIR = path.join(tmp.name, "last-good")

    try:
        from os import environ
        environ["START_DATE"] = "2026-08-01T18:00:00Z"

        def failing_login():
            raise login_failure

        main.login = failing_login
        leagues.clear_caches = lambda: None

        manifest = main.run_once()

        with open(path.join(ts_dir, "ts_run_manifest.json")) as f:
            written_manifest = json.load(f)
        with open(path.join(ts_dir, "ts_main.json")) as f:
            written_main = json.load(f)

        return manifest, written_manifest, written_main
    finally:
        (main.LOG_DIR, main.TIMESTAMP_DIR, main.DATA_DIR,
         miscellaneous.DATA_DIR, miscellaneous.TIMESTAMP_DIR,
         miscellaneous.LAST_GOOD_DIR,
         main.login, leagues.clear_caches) = original
        tmp.cleanup()
        runs.end_run()


def assert_the_run_left_an_honest_record(written_manifest, written_main):
    """Every failure shape has to come out the same way on disk."""
    assert written_manifest["allOk"] is False, f"got {written_manifest}"
    assert written_manifest["runId"] != "RUN-1", \
        "the previous run's manifest is still on disk"
    assert written_main["allOk"] is False, f"got {written_main}"
    assert written_main["runId"] == written_manifest["runId"], \
        f"the timestamp names a different run than the manifest: {written_main}"


def test_a_login_that_calls_exit_still_leaves_a_manifest():
    """exit() raises SystemExit, which is a BaseException and no handler caught it.

    This was the real hole: main.py's login() called exit() when the account had no
    leagues. The process ended with code 0 - SystemExit(None) reads as success - and left
    the previous run's manifest in place for the frontend to render green.
    """
    _, written_manifest, written_main = run_once_with(SystemExit())

    assert_the_run_left_an_honest_record(written_manifest, written_main)
    assert any("SystemExit" in (s["error"] or "") for s in written_manifest["stages"]), \
        f"got {written_manifest['stages']}"


def test_a_login_that_raises_an_unexpected_error_still_leaves_a_manifest():
    """A changed API response is a KeyError, a full disk an OSError. Neither is one of
    this project's own exception types, and the narrow handler let both through."""
    for failure in (KeyError("u"), OSError("no space left on device")):
        _, written_manifest, written_main = run_once_with(failure)
        assert_the_run_left_an_honest_record(written_manifest, written_main)


def test_a_login_that_raises_a_known_error_still_leaves_a_manifest():
    _, written_manifest, written_main = run_once_with(
        exceptions.LoginException("[CRITICAL] Login failed!"))

    assert_the_run_left_an_honest_record(written_manifest, written_main)


def test_the_exit_code_follows_the_manifest():
    """The signal entrypoint.py is meant to read in the next step."""
    manifest, _, _ = run_once_with(SystemExit())
    assert manifest.all_ok is False, "a failed run must not exit 0"


def test_an_account_without_leagues_is_a_login_failure_not_a_process_exit():
    """A function three levels down is not the place that ends the process."""
    import main
    from backend.kickbase.v4 import leagues, user

    class FakeUser:
        name = "Max"
        id = "1"

    ### login() reads these as module globals, and main.py only assigns them in its
    ### __main__ block - so importing it as a module leaves them undefined
    credentials = {"kb_mail": "a@b.c", "kb_password": "x",
                   "discord_webhook": "https://d.test/h"}
    missing = [name for name in credentials if not hasattr(main, name)]

    original = (user.login, leagues.get_league_list)
    for name, value in credentials.items():
        setattr(main, name, value)

    try:
        user.login = lambda *a: (FakeUser(), "token")
        leagues.get_league_list = lambda token: []

        try:
            main.login()
        except exceptions.LoginException as e:
            assert "No leagues" in str(e), f"got {e}"
        except SystemExit:
            raise AssertionError("exit() slips past every handler on the way up")
        else:
            raise AssertionError("expected a LoginException")
    finally:
        user.login, leagues.get_league_list = original
        for name in missing:
            delattr(main, name)


### ===============================================================================

if __name__ == "__main__":
    print("run ids")
    check("a run id sorts by time", test_a_run_id_sorts_by_time)
    check("two runs in the same second stay apart", test_two_runs_in_the_same_second_stay_apart)
    check("the current run id is the one that was started", test_the_current_run_id_is_the_one_that_was_started)
    check("there is no current run after it ended", test_there_is_no_current_run_after_it_ended)

    print("\none stage at a time")
    check("a successful stage is recorded", test_a_successful_stage_is_recorded)
    check("a failing stage does not take the next one with it", test_a_failing_stage_does_not_take_the_next_one_with_it)
    check("a failure is recorded with its type and message", test_a_failure_is_recorded_with_its_type_and_message)
    check("a stage records how long it took", test_a_stage_records_how_long_it_took)
    check("an expired token stops the run", test_an_expired_token_stops_the_run)
    check("a skipped stage says why", test_a_skipped_stage_says_why)
    check("an ordinary failure does not stop the run", test_an_ordinary_failure_does_not_stop_the_run)

    print("\nthe verdict")
    check("all ok when every stage succeeded", test_all_ok_when_every_stage_succeeded)
    check("not all ok when one stage failed", test_not_all_ok_when_one_stage_failed)
    check("a run with no stages is not ok", test_a_run_with_no_stages_is_not_ok)
    check("a skipped stage counts as not ok", test_a_skipped_stage_counts_as_not_ok)
    check("the summary names what went wrong", test_the_summary_names_what_went_wrong)
    check("the manifest carries the run id and every stage", test_the_manifest_carries_the_run_id_and_every_stage)
    check("the manifest is JSON serialisable", test_the_manifest_is_json_serialisable)

    print("\nmain() end to end")
    check("runs every stage and reports all ok", test_main_runs_every_stage_and_reports_all_ok)
    check("keeps going after one stage fails", test_main_keeps_going_after_one_stage_fails)
    check("every stage writes under the same run id", test_every_stage_writes_under_the_same_run_id)
    check("a stale dataset keeps the older run id", test_a_dataset_a_failed_stage_did_not_rewrite_keeps_the_older_run_id)
    check("a failed login produces a manifest that is not ok", test_a_failed_login_produces_a_manifest_that_is_not_ok)

    print("\nrun_once(): whatever kills the run, it still leaves a record")
    check("a login that calls exit() still leaves a manifest", test_a_login_that_calls_exit_still_leaves_a_manifest)
    check("an unexpected error still leaves a manifest", test_a_login_that_raises_an_unexpected_error_still_leaves_a_manifest)
    check("a known error still leaves a manifest", test_a_login_that_raises_a_known_error_still_leaves_a_manifest)
    check("the exit code follows the manifest", test_the_exit_code_follows_the_manifest)
    check("no leagues is a login failure, not a process exit", test_an_account_without_leagues_is_a_login_failure_not_a_process_exit)

    total, passed = len(PASSED), sum(PASSED)
    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)
