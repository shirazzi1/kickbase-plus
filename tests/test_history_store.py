"""Tests for the append-only history store that hangs off the JSON writer.

Phase 0 shortened the market value fetches from 365 days to 31, so every signal that reads
a longer curve now depends on this store accumulating locally. There is no backfill: a run
whose line never got appended is a hole in the record for good. Hence the three things
tested here.

  - **Appending, not replacing.** Two writes of a dataset have to leave two lines. The
    obvious way to get this wrong is to reuse the atomic write, which replaces the file.
  - **One file per day.** The file name rotates with the calendar date, and yesterday's
    file is never touched again.
  - **The main write stays exactly as atomic as it was.** The history is a convenience
    bolted onto the writer; it must not be able to truncate a data file, leave a temporary
    file behind, or turn a successful write into a raised failure.

    ./venv/bin/python tests/test_history_store.py
"""

import json
import sys
import tempfile

from datetime import datetime, timedelta, timezone
from os import environ, listdir, makedirs, path

### Make the repository root importable regardless of where this is run from
sys.path.insert(0, path.dirname(path.dirname(path.abspath(__file__))))

from backend import miscellaneous

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


class TempDirs:
    """Point every writer at a temporary directory for the duration of a test."""

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name

        self.data_dir = path.join(root, "data")
        self.ts_dir = path.join(self.data_dir, "timestamps")
        self.last_good_dir = path.join(root, "last-good")
        self.history_dir = path.join(root, "history")
        makedirs(self.ts_dir, exist_ok=True)

        self.original = (miscellaneous.PUBLIC_DIR, miscellaneous.STATE_DIR, miscellaneous.TIMESTAMP_DIR,
                         miscellaneous.LAST_GOOD_DIR, miscellaneous.HISTORY_DIR)
        miscellaneous.PUBLIC_DIR = self.data_dir
        miscellaneous.STATE_DIR = self.data_dir
        miscellaneous.TIMESTAMP_DIR = self.ts_dir
        miscellaneous.LAST_GOOD_DIR = self.last_good_dir
        miscellaneous.HISTORY_DIR = self.history_dir

        return self

    def __exit__(self, *exc):
        (miscellaneous.PUBLIC_DIR, miscellaneous.STATE_DIR,
         miscellaneous.TIMESTAMP_DIR,
         miscellaneous.LAST_GOOD_DIR, miscellaneous.HISTORY_DIR) = self.original
        self.tmp.cleanup()
        return False

    def lines(self, dataset):
        """Every history line of a dataset written today, decoded."""
        file_path = miscellaneous.history_file_path(dataset)

        if not path.exists(file_path):
            return []

        with open(file_path) as f:
            return [json.loads(line) for line in f if line.strip()]

    def raw_lines(self, dataset):
        """The same file, undecoded, so the line structure itself can be checked."""
        with open(miscellaneous.history_file_path(dataset)) as f:
            return f.read().splitlines()

    def datasets(self):
        """Which datasets have a directory in the store."""
        if not path.exists(self.history_dir):
            return []
        return sorted(listdir(self.history_dir))


### ===============================================================================
### Appending
### ===============================================================================


def test_a_write_appends_one_line():
    with TempDirs() as dirs:
        miscellaneous.write_json_to_file([{"i": "1"}], "market.json")

        assert len(dirs.lines("market")) == 1, f"got {dirs.lines('market')}"


def test_two_writes_append_two_lines():
    """The whole point. An atomic replace would leave one."""
    with TempDirs() as dirs:
        miscellaneous.write_json_to_file([{"i": "first"}], "market.json")
        miscellaneous.write_json_to_file([{"i": "second"}], "market.json")

        lines = dirs.lines("market")
        assert len(lines) == 2, f"expected two lines, got {len(lines)}: {lines}"
        assert [line["rows"] for line in lines] == [[{"i": "first"}], [{"i": "second"}]], \
            f"and in the order they were written, got {[line['rows'] for line in lines]}"


def test_a_line_carries_the_payload_verbatim():
    """A diff engine has to see exactly what the frontend sees, not a reduced version."""
    payload = [{"playerId": "42", "offerCount": 3, "price": None, "nested": {"a": [1, 2]}}]

    with TempDirs() as dirs:
        miscellaneous.write_json_to_file(payload, "market.json")

        assert dirs.lines("market")[0]["rows"] == payload, f"got {dirs.lines('market')[0]}"


def test_a_line_carries_a_readable_timestamp():
    with TempDirs() as dirs:
        miscellaneous.write_json_to_file([{"i": "1"}], "market.json")

        stamp = datetime.fromisoformat(dirs.lines("market")[0]["ts"])

        assert stamp.tzinfo is not None, \
            f"the timestamp needs an offset, or the store cannot be read across a DST change: {stamp}"


def test_one_run_is_one_line():
    """NDJSON, so an indented payload must not spread over the lines of its own snapshot."""
    with TempDirs() as dirs:
        miscellaneous.write_json_to_file([{"i": "1"}, {"i": "2"}], "market.json")
        miscellaneous.write_json_to_file([{"i": "3"}], "market.json")

        assert len(dirs.raw_lines("market")) == 2, \
            f"expected two physical lines, got {dirs.raw_lines('market')}"


def test_every_historicised_dataset_gets_its_own_directory():
    with TempDirs() as dirs:
        for dataset in sorted(miscellaneous.HISTORICISED_DATASETS):
            miscellaneous.write_json_to_file([{"i": "1"}], f"{dataset}.json")

        assert dirs.datasets() == sorted(miscellaneous.HISTORICISED_DATASETS), \
            f"got {dirs.datasets()}"


def test_the_datasets_the_market_features_need_are_historicised():
    """Named explicitly so shrinking the set is a deliberate act, not a refactor."""
    for dataset in ("market", "market_value_changes", "balances"):
        assert dataset in miscellaneous.HISTORICISED_DATASETS, \
            f"{dataset} has to be recorded, it cannot be reconstructed after the fact"


### ===============================================================================
### What is deliberately not recorded
### ===============================================================================


def test_a_timestamp_file_is_not_historicised():
    """It says when a write happened, which the "ts" of the history line already says."""
    with TempDirs() as dirs:
        miscellaneous.write_json_to_file({"time": "now"}, "ts_market.json")

        assert dirs.datasets() == [], f"the store gained {dirs.datasets()}"


def test_the_cumulative_and_static_datasets_are_not_historicised():
    """all_transfers and friends grow all season and are backfilled by the activity feed;
    snapshotting them six times a day costs quadratic disk for nothing."""
    with TempDirs() as dirs:
        for file_name in ("all_transfers.json", "turnovers.json", "revenue_sum.json",
                          "team_values.json", "league_user_stats.json", "achievements.json",
                          "free_players.json", "live_points.json", "match_days.json",
                          "STATIC_users.json", "STATIC_teams.json"):
            miscellaneous.write_json_to_file([{"i": "1"}], file_name)

        assert dirs.datasets() == [], f"the store gained {dirs.datasets()}"


### ===============================================================================
### One file per day
### ===============================================================================


def test_the_file_name_is_the_calendar_date():
    with TempDirs():
        moment = datetime(2026, 8, 13, 14, 10, tzinfo=timezone.utc)

        assert path.basename(miscellaneous.history_file_path("market", moment)) == "2026-08-13.ndjson", \
            f"got {miscellaneous.history_file_path('market', moment)}"


def test_two_runs_on_the_same_day_share_a_file():
    with TempDirs():
        morning = datetime(2026, 8, 13, 6, 10, tzinfo=timezone.utc)
        evening = datetime(2026, 8, 13, 18, 10, tzinfo=timezone.utc)

        assert miscellaneous.history_file_path("market", morning) == \
            miscellaneous.history_file_path("market", evening), \
            "runs six hours apart must not each get a file"


def test_the_next_day_gets_a_new_file():
    with TempDirs():
        today = datetime(2026, 8, 13, 22, 10, tzinfo=timezone.utc)
        tomorrow = today + timedelta(days=1)

        assert miscellaneous.history_file_path("market", today) != \
            miscellaneous.history_file_path("market", tomorrow), \
            "the file has to rotate with the date"


def test_the_date_in_a_line_matches_the_file_it_sits_in():
    """Otherwise reading a day's runs means opening two files and hoping."""
    with TempDirs() as dirs:
        miscellaneous.write_json_to_file([{"i": "1"}], "market.json")

        day = path.basename(miscellaneous.history_file_path("market")).removesuffix(".ndjson")

        assert dirs.lines("market")[0]["ts"].startswith(day), \
            f"line {dirs.lines('market')[0]['ts']} sits in {day}.ndjson"


def test_yesterdays_file_is_left_alone():
    """Rotation is only worth anything if the older file stays readable and complete."""
    with TempDirs() as dirs:
        yesterday = datetime.now(timezone.utc) - timedelta(days=1)
        yesterdays_file = miscellaneous.history_file_path("market", yesterday)

        makedirs(path.dirname(yesterdays_file), exist_ok=True)
        with open(yesterdays_file, "w") as f:
            f.write('{"ts": "yesterday", "rows": [{"i": "old"}]}\n')

        miscellaneous.write_json_to_file([{"i": "new"}], "market.json")
        miscellaneous.write_json_to_file([{"i": "newer"}], "market.json")

        with open(yesterdays_file) as f:
            kept = [json.loads(line) for line in f if line.strip()]

        assert len(kept) == 1 and kept[0]["rows"] == [{"i": "old"}], f"got {kept}"
        assert len(dirs.lines("market")) == 2, \
            f"today's runs belong in today's file, got {dirs.lines('market')}"


### ===============================================================================
### The main write stays untouched
### ===============================================================================


def test_the_store_stays_out_of_the_watched_directory():
    """Everything under frontend/src is watched by the dev server, and this store gains a
    line six times a day and never shrinks."""
    with TempDirs() as dirs:
        miscellaneous.write_json_to_file([{"i": "1"}], "market.json")

        assert sorted(listdir(dirs.data_dir)) == ["market.json", "timestamps"], \
            f"the data directory gained a file: {sorted(listdir(dirs.data_dir))}"
        assert dirs.datasets() == ["market"], f"got {dirs.datasets()}"


def test_the_data_file_is_still_written():
    with TempDirs() as dirs:
        miscellaneous.write_json_to_file([{"i": "1"}], "market.json")

        with open(path.join(dirs.data_dir, "market.json")) as f:
            assert json.load(f) == [{"i": "1"}]


def test_no_temporary_file_is_left_behind():
    with TempDirs() as dirs:
        miscellaneous.write_json_to_file([{"i": "1"}], "market.json")

        leftovers = [f for f in listdir(dirs.data_dir) if f.endswith(".tmp")]
        assert leftovers == [], f"temporary files left: {leftovers}"


def test_a_failed_write_still_does_not_replace_the_previous_file():
    """A set is not JSON serialisable, so the dump fails partway through."""
    with TempDirs() as dirs:
        miscellaneous.write_json_to_file([{"i": "good"}], "market.json")

        try:
            miscellaneous.write_json_to_file({"bad"}, "market.json")
        except TypeError:
            pass
        else:
            raise AssertionError("expected the unserialisable payload to raise")

        with open(path.join(dirs.data_dir, "market.json")) as f:
            assert json.load(f) == [{"i": "good"}], "the previous content must survive"


def test_a_failed_write_appends_nothing():
    """The store records what reached the disk. A payload that never landed is not history."""
    with TempDirs() as dirs:
        try:
            miscellaneous.write_json_to_file({"bad"}, "market.json")
        except TypeError:
            pass

        assert dirs.lines("market") == [], f"got {dirs.lines('market')}"


def test_a_broken_store_does_not_stop_the_write():
    """The history must never be the reason a run loses the data it fetched."""
    with TempDirs() as dirs:
        ### A file where the dataset's directory should be, so creating it fails
        makedirs(dirs.history_dir, exist_ok=True)
        with open(path.join(dirs.history_dir, "market"), "w") as f:
            f.write("not a directory")

        miscellaneous.write_json_to_file([{"i": "1"}], "market.json")

        with open(path.join(dirs.data_dir, "market.json")) as f:
            assert json.load(f) == [{"i": "1"}], "the write has to go through anyway"


def test_the_last_good_snapshot_still_happens():
    """The two safety nets are independent; adding one must not have unhooked the other."""
    with TempDirs():
        miscellaneous.write_json_to_file([{"i": "first"}], "market.json")
        miscellaneous.write_json_to_file([{"i": "second"}], "market.json")

        assert miscellaneous.read_last_good("market.json") == [{"i": "first"}], \
            f"got {miscellaneous.read_last_good('market.json')}"


def test_any_failure_in_the_append_leaves_the_write_a_success():
    """The data file is already replaced by the time the append runs, so anything escaping
    from it reports a write that did happen as a failed stage - and a failed stage reads as
    "restart the container", which would not have helped."""
    with TempDirs() as dirs:
        original = miscellaneous.history_file_path

        def boom(*args, **kwargs):
            ### Deliberately not an OSError: the handler used to list the exception types it
            ### expected, and this is the shape of the one it did not expect
            raise RuntimeError("something nobody thought of")

        miscellaneous.history_file_path = boom
        try:
            miscellaneous.write_json_to_file([{"i": "1"}], "market.json")
        finally:
            miscellaneous.history_file_path = original

        with open(path.join(dirs.data_dir, "market.json")) as f:
            assert json.load(f) == [{"i": "1"}], "the write has to stand"


### ===============================================================================
### A hostile environment
### ===============================================================================


class BrokenTZ:
    """Set TZ to something libc accepts and the tzdata lookup does not."""

    def __init__(self, value):
        self.value = value

    def __enter__(self):
        self.original = environ.get("TZ")
        environ["TZ"] = self.value
        return self

    def __exit__(self, *exc):
        if self.original is None:
            environ.pop("TZ", None)
        else:
            environ["TZ"] = self.original
        return False


def test_an_unusable_tz_does_not_fail_the_write():
    """TZ="" hands getenv() the empty string rather than its default, and a POSIX style TZ
    raises ZoneInfoNotFoundError - a KeyError, so it slips past a narrow handler. Either
    used to be raised after the data file had already been replaced."""
    for tz in ("", "CET-1CEST,M3.5.0,M10.5.0/3", "Nonsense/Zone", "/etc/localtime"):
        with TempDirs() as dirs, BrokenTZ(tz):
            miscellaneous.write_json_to_file([{"i": "1"}], "market.json")

            with open(path.join(dirs.data_dir, "market.json")) as f:
                assert json.load(f) == [{"i": "1"}], f"TZ={tz!r} broke the write"


def test_an_unusable_tz_still_records_the_line():
    """Falling back to the default zone beats losing the line: a day boundary an hour off is
    a rounding error, a missing line is unrecoverable."""
    for tz in ("", "CET-1CEST,M3.5.0,M10.5.0/3"):
        with TempDirs() as dirs, BrokenTZ(tz):
            miscellaneous.write_json_to_file([{"i": "1"}], "market.json")

            lines = dirs.lines("market")
            assert len(lines) == 1, f"TZ={tz!r} cost the line, got {lines}"
            assert lines[0]["rows"] == [{"i": "1"}], f"got {lines[0]}"


def test_a_dataset_name_cannot_leave_the_store():
    """The name becomes a path segment, and the diff engine will read this store by name."""
    with TempDirs():
        for hostile in ("../../etc", "..", "a/b", "", "market/../../x"):
            try:
                miscellaneous.history_file_path(hostile)
            except ValueError:
                continue

            raise AssertionError(f"'{hostile}' was accepted as a dataset name")


def test_a_real_dataset_name_is_accepted():
    """The guard is worthless if it turns away the names actually in use."""
    with TempDirs() as dirs:
        for dataset in sorted(miscellaneous.HISTORICISED_DATASETS):
            assert miscellaneous.history_file_path(dataset).startswith(dirs.history_dir), \
                f"{dataset} was refused or filed outside the store"


### ===============================================================================
### Recovering from a half written line
### ===============================================================================


def test_a_half_written_line_does_not_swallow_the_next_one():
    """A host that dies mid append leaves the file without its closing newline. Appending
    straight onto that fuses the broken half and the next intact line into one that parses
    as neither, so one crash would cost two runs."""
    with TempDirs() as dirs:
        miscellaneous.write_json_to_file([{"i": "first"}], "market.json")

        ### Cut the second line short, exactly as a crash between write() and fsync would
        with open(miscellaneous.history_file_path("market"), "a") as f:
            f.write('{"ts": "2026-08-13T14:10:00+02:00", "rows": [{"i": "cut')

        miscellaneous.write_json_to_file([{"i": "third"}], "market.json")

        with open(miscellaneous.history_file_path("market")) as f:
            raw = [line for line in f.read().splitlines() if line.strip()]

        parsed = []
        for line in raw:
            try:
                parsed.append(json.loads(line))
            except json.JSONDecodeError:
                pass

        assert len(raw) == 3, f"expected three physical lines, got {len(raw)}: {raw}"
        assert [entry["rows"] for entry in parsed] == [[{"i": "first"}], [{"i": "third"}]], \
            f"both intact lines have to survive, got {[e['rows'] for e in parsed]}"


def test_an_intact_file_gains_no_blank_line():
    """The repair must only fire on real damage, or every run adds an empty line."""
    with TempDirs() as dirs:
        miscellaneous.write_json_to_file([{"i": "first"}], "market.json")
        miscellaneous.write_json_to_file([{"i": "second"}], "market.json")
        miscellaneous.write_json_to_file([{"i": "third"}], "market.json")

        with open(miscellaneous.history_file_path("market")) as f:
            raw = f.read()

        assert raw.count("\n\n") == 0, f"blank line inserted: {raw!r}"
        assert len(raw.splitlines()) == 3, f"got {raw.splitlines()}"


### ===============================================================================

if __name__ == "__main__":
    print("appending")
    check("a write appends one line", test_a_write_appends_one_line)
    check("two writes append two lines", test_two_writes_append_two_lines)
    check("a line carries the payload verbatim", test_a_line_carries_the_payload_verbatim)
    check("a line carries a readable timestamp", test_a_line_carries_a_readable_timestamp)
    check("one run is one line", test_one_run_is_one_line)
    check("every historicised dataset gets its own directory", test_every_historicised_dataset_gets_its_own_directory)
    check("the market features' datasets are recorded", test_the_datasets_the_market_features_need_are_historicised)

    print("\nwhat is deliberately not recorded")
    check("timestamp files are not historicised", test_a_timestamp_file_is_not_historicised)
    check("cumulative and static datasets are not historicised", test_the_cumulative_and_static_datasets_are_not_historicised)

    print("\none file per day")
    check("the file name is the calendar date", test_the_file_name_is_the_calendar_date)
    check("two runs on the same day share a file", test_two_runs_on_the_same_day_share_a_file)
    check("the next day gets a new file", test_the_next_day_gets_a_new_file)
    check("the date in a line matches its file", test_the_date_in_a_line_matches_the_file_it_sits_in)
    check("yesterday's file is left alone", test_yesterdays_file_is_left_alone)

    print("\nthe main write stays untouched")
    check("the store stays out of the watched directory", test_the_store_stays_out_of_the_watched_directory)
    check("the data file is still written", test_the_data_file_is_still_written)
    check("no temporary file is left behind", test_no_temporary_file_is_left_behind)
    check("a failed write does not replace the previous file", test_a_failed_write_still_does_not_replace_the_previous_file)
    check("a failed write appends nothing", test_a_failed_write_appends_nothing)
    check("a broken store does not stop the write", test_a_broken_store_does_not_stop_the_write)
    check("the last good snapshot still happens", test_the_last_good_snapshot_still_happens)
    check("any append failure leaves the write a success", test_any_failure_in_the_append_leaves_the_write_a_success)

    print("\na hostile environment")
    check("an unusable TZ does not fail the write", test_an_unusable_tz_does_not_fail_the_write)
    check("an unusable TZ still records the line", test_an_unusable_tz_still_records_the_line)
    check("a dataset name cannot leave the store", test_a_dataset_name_cannot_leave_the_store)
    check("a real dataset name is accepted", test_a_real_dataset_name_is_accepted)

    print("\nrecovering from a half written line")
    check("a half written line does not swallow the next one", test_a_half_written_line_does_not_swallow_the_next_one)
    check("an intact file gains no blank line", test_an_intact_file_gains_no_blank_line)

    total, passed = len(PASSED), sum(PASSED)
    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)
