"""Tests for the JSON writer being atomic and for keeping the previous copy.

The old writer opened the target and wrote into it. Two consequences:

  - A crash halfway through left a truncated file. That file sits in the directory the
    create-react-app dev server watches, so the broken JSON was compiled into the bundle
    and blanked the whole UI until the next successful run.
  - Any write failure was caught and logged, and the run carried on reporting success
    over a file that had never been written.

    ./venv/bin/python tests/test_atomic_writes.py
"""

import json
import sys
import tempfile

from os import listdir, makedirs, path

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
        ### Redirected as well, otherwise every write in here appends a line to the real
        ### history store in the repository
        self.history_dir = path.join(root, "history")
        makedirs(self.ts_dir, exist_ok=True)

        self.original = (miscellaneous.DATA_DIR, miscellaneous.TIMESTAMP_DIR,
                         miscellaneous.LAST_GOOD_DIR, miscellaneous.HISTORY_DIR)
        miscellaneous.DATA_DIR = self.data_dir
        miscellaneous.TIMESTAMP_DIR = self.ts_dir
        miscellaneous.LAST_GOOD_DIR = self.last_good_dir
        miscellaneous.HISTORY_DIR = self.history_dir

        return self

    def __exit__(self, *exc):
        (miscellaneous.DATA_DIR, miscellaneous.TIMESTAMP_DIR,
         miscellaneous.LAST_GOOD_DIR, miscellaneous.HISTORY_DIR) = self.original
        self.tmp.cleanup()
        return False

    def read(self, file_name):
        with open(path.join(self.data_dir, file_name)) as f:
            return json.load(f)

    def read_raw(self, file_name):
        with open(path.join(self.data_dir, file_name)) as f:
            return f.read()


### ===============================================================================
### The write itself
### ===============================================================================


def test_a_data_file_is_written():
    with TempDirs() as dirs:
        miscellaneous.write_json_to_file([{"i": "1"}], "market.json")
        assert dirs.read("market.json") == [{"i": "1"}], f"got {dirs.read('market.json')}"


def test_a_data_file_stays_indented():
    """The files are read by hand often enough that this is worth keeping."""
    with TempDirs() as dirs:
        miscellaneous.write_json_to_file([{"i": "1"}], "market.json")
        assert "\n" in dirs.read_raw("market.json"), "expected indented JSON"


def test_a_timestamp_file_lands_in_the_timestamp_directory():
    with TempDirs() as dirs:
        miscellaneous.write_json_to_file({"time": "now"}, "ts_market.json")

        assert path.exists(path.join(dirs.ts_dir, "ts_market.json")), \
            f"timestamp directory holds {listdir(dirs.ts_dir)}"


def test_no_temporary_file_is_left_behind():
    with TempDirs() as dirs:
        miscellaneous.write_json_to_file([{"i": "1"}], "market.json")

        leftovers = [f for f in listdir(dirs.data_dir) if f.endswith(".tmp")]
        assert leftovers == [], f"temporary files left: {leftovers}"


def test_a_failed_write_does_not_replace_the_previous_file():
    """A set is not JSON serialisable, so the dump fails partway through."""
    with TempDirs() as dirs:
        miscellaneous.write_json_to_file([{"i": "good"}], "market.json")

        try:
            miscellaneous.write_json_to_file({"bad"}, "market.json")
        except TypeError:
            pass
        else:
            raise AssertionError("expected the unserialisable payload to raise")

        assert dirs.read("market.json") == [{"i": "good"}], \
            f"the previous content must survive, got {dirs.read('market.json')}"


def test_a_failed_write_leaves_no_temporary_file():
    with TempDirs() as dirs:
        try:
            miscellaneous.write_json_to_file({"bad"}, "market.json")
        except TypeError:
            pass

        leftovers = [f for f in listdir(dirs.data_dir) if f.endswith(".tmp")]
        assert leftovers == [], f"temporary files left: {leftovers}"


def test_a_write_failure_is_raised_rather_than_swallowed():
    """It used to be logged, and the run then reported success over a file it never wrote."""
    with TempDirs():
        try:
            miscellaneous.write_json_to_file({"bad"}, "market.json")
        except TypeError:
            return

    raise AssertionError("expected the write failure to reach the caller")


### ===============================================================================
### The .last-good snapshot
### ===============================================================================


def test_the_previous_content_is_kept_before_it_is_replaced():
    with TempDirs():
        miscellaneous.write_json_to_file([{"i": "first"}], "market.json")
        miscellaneous.write_json_to_file([{"i": "second"}], "market.json")

        assert miscellaneous.read_last_good("market.json") == [{"i": "first"}], \
            f"got {miscellaneous.read_last_good('market.json')}"


def test_the_first_write_has_nothing_to_keep():
    with TempDirs():
        miscellaneous.write_json_to_file([{"i": "first"}], "market.json")

        assert miscellaneous.read_last_good("market.json") is None, \
            f"got {miscellaneous.read_last_good('market.json')}"


def test_a_file_that_does_not_parse_is_not_promoted_to_being_the_good_one():
    with TempDirs() as dirs:
        with open(path.join(dirs.data_dir, "market.json"), "w") as f:
            f.write('[{"i": "trunca')

        miscellaneous.write_json_to_file([{"i": "fresh"}], "market.json")

        assert miscellaneous.read_last_good("market.json") is None, \
            "broken content must never become the last good copy"


def test_the_snapshots_stay_out_of_the_watched_directory():
    """Everything under frontend/src is watched, and a second file per dataset there
    would double the rebuilds without a single component importing it."""
    with TempDirs() as dirs:
        miscellaneous.write_json_to_file([{"i": "first"}], "market.json")
        miscellaneous.write_json_to_file([{"i": "second"}], "market.json")

        assert sorted(listdir(dirs.data_dir)) == ["market.json", "timestamps"], \
            f"the data directory gained a file: {sorted(listdir(dirs.data_dir))}"
        assert path.exists(path.join(dirs.last_good_dir, "market.json.last-good")), \
            f"last-good directory holds {listdir(dirs.last_good_dir)}"


def test_timestamp_files_are_not_snapshotted():
    """They are rewritten every run and carry nothing worth recovering."""
    with TempDirs() as dirs:
        miscellaneous.write_json_to_file({"time": "a"}, "ts_market.json")
        miscellaneous.write_json_to_file({"time": "b"}, "ts_market.json")

        assert not path.exists(dirs.last_good_dir) or listdir(dirs.last_good_dir) == [], \
            f"timestamps were snapshotted: {listdir(dirs.last_good_dir)}"


def test_a_snapshot_failure_does_not_stop_the_write():
    """The snapshot is a convenience. It must never be the reason a run loses its data."""
    with TempDirs() as dirs:
        miscellaneous.write_json_to_file([{"i": "first"}], "market.json")

        ### A file where the snapshot directory should be, so creating it fails
        with open(dirs.last_good_dir, "w") as f:
            f.write("not a directory")

        miscellaneous.write_json_to_file([{"i": "second"}], "market.json")

        assert dirs.read("market.json") == [{"i": "second"}], \
            f"the write must go through anyway, got {dirs.read('market.json')}"


def test_reading_a_missing_snapshot_gives_none():
    with TempDirs():
        assert miscellaneous.read_last_good("never-written.json") is None


### ===============================================================================
### write_timestamp()
### ===============================================================================


def test_a_timestamp_carries_the_run_that_produced_it():
    from backend import runs

    with TempDirs() as dirs:
        runs.start_run("20260813T120000Z-test")
        try:
            miscellaneous.write_timestamp("ts_market.json", rows=91)
        finally:
            runs.end_run()

        with open(path.join(dirs.ts_dir, "ts_market.json")) as f:
            stamp = json.load(f)

    assert stamp["runId"] == "20260813T120000Z-test", f"got {stamp}"
    assert stamp["rows"] == 91, f"got {stamp}"


def test_a_timestamp_keeps_the_time_field_it_always_had():
    """Anything still reading only "time" has to keep working."""
    from datetime import datetime
    from backend import runs

    with TempDirs() as dirs:
        runs.start_run("20260813T120000Z-test")
        try:
            miscellaneous.write_timestamp("ts_market.json")
        finally:
            runs.end_run()

        with open(path.join(dirs.ts_dir, "ts_market.json")) as f:
            stamp = json.load(f)

    ### Raises if it is not a readable timestamp
    datetime.fromisoformat(stamp["time"])


def test_a_write_outside_a_run_carries_no_run_id():
    """app.py writes data files too, and it is not a run."""
    from backend import runs

    with TempDirs() as dirs:
        runs.end_run()
        miscellaneous.write_timestamp("ts_live_points.json")

        with open(path.join(dirs.ts_dir, "ts_live_points.json")) as f:
            stamp = json.load(f)

    assert stamp["runId"] is None, f"got {stamp}"


### ===============================================================================

if __name__ == "__main__":
    print("the write itself")
    check("a data file is written", test_a_data_file_is_written)
    check("a data file stays indented", test_a_data_file_stays_indented)
    check("a timestamp file lands in the timestamp directory", test_a_timestamp_file_lands_in_the_timestamp_directory)
    check("no temporary file is left behind", test_no_temporary_file_is_left_behind)
    check("a failed write does not replace the previous file", test_a_failed_write_does_not_replace_the_previous_file)
    check("a failed write leaves no temporary file", test_a_failed_write_leaves_no_temporary_file)
    check("a write failure is raised, not swallowed", test_a_write_failure_is_raised_rather_than_swallowed)

    print("\nthe .last-good snapshot")
    check("the previous content is kept", test_the_previous_content_is_kept_before_it_is_replaced)
    check("the first write has nothing to keep", test_the_first_write_has_nothing_to_keep)
    check("broken content is never promoted", test_a_file_that_does_not_parse_is_not_promoted_to_being_the_good_one)
    check("snapshots stay out of the watched directory", test_the_snapshots_stay_out_of_the_watched_directory)
    check("timestamps are not snapshotted", test_timestamp_files_are_not_snapshotted)
    check("a snapshot failure does not stop the write", test_a_snapshot_failure_does_not_stop_the_write)
    check("reading a missing snapshot gives None", test_reading_a_missing_snapshot_gives_none)

    print("\nwrite_timestamp()")
    check("carries the run that produced it", test_a_timestamp_carries_the_run_that_produced_it)
    check("keeps the time field it always had", test_a_timestamp_keeps_the_time_field_it_always_had)
    check("a write outside a run carries no run id", test_a_write_outside_a_run_carries_no_run_id)

    total, passed = len(PASSED), sum(PASSED)
    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)
