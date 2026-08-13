"""Tests for the split between the data the browser reads and the data only the backend does.

Everything used to be written into frontend/src/data, where the React build imported it at
compile time. Now `data/public` is served by app.py and `data/state` is not served at all,
which makes two things testable that were not before: that each dataset lands in the right
directory, and that the one-time migration out of the old one cannot lose a file.

Dependency free on purpose: the project has no test framework, so this runs with the project
venv directly and needs no extra packages.

    ./venv/bin/python tests/test_data_plane.py
"""

import json
import sys
import tempfile

from os import makedirs, path

### Make the repository root importable regardless of where this is run from
sys.path.insert(0, path.dirname(path.dirname(path.abspath(__file__))))

from backend import datasets, miscellaneous, profiles, state_migration

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


class TempTree:
    """The three directories a run writes into, plus the one it migrates out of.

    Distinct directories on purpose. Most of the suite points public and state at the same
    temporary directory, because the tests there are about other things; here the whole
    point is that a reader looking in the wrong one fails.
    """

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name

        self.public_dir = path.join(root, "public")
        self.state_dir = path.join(root, "state")
        self.ts_dir = path.join(self.public_dir, "timestamps")
        self.legacy_dir = path.join(root, "legacy")
        self.last_good_dir = path.join(root, "last-good")
        self.history_dir = path.join(root, "history")

        makedirs(self.ts_dir, exist_ok=True)
        makedirs(self.state_dir, exist_ok=True)

        self.original = (
            miscellaneous.PUBLIC_DIR, miscellaneous.STATE_DIR, miscellaneous.TIMESTAMP_DIR,
            miscellaneous.LAST_GOOD_DIR, miscellaneous.HISTORY_DIR,
            profiles.PUBLIC_DIR, profiles.STATE_DIR,
            state_migration.PUBLIC_DIR, state_migration.STATE_DIR,
            state_migration.TIMESTAMP_DIR, state_migration.LEGACY_DATA_DIR)

        miscellaneous.PUBLIC_DIR = self.public_dir
        miscellaneous.STATE_DIR = self.state_dir
        miscellaneous.TIMESTAMP_DIR = self.ts_dir
        miscellaneous.LAST_GOOD_DIR = self.last_good_dir
        miscellaneous.HISTORY_DIR = self.history_dir
        profiles.PUBLIC_DIR = self.public_dir
        profiles.STATE_DIR = self.state_dir
        state_migration.PUBLIC_DIR = self.public_dir
        state_migration.STATE_DIR = self.state_dir
        state_migration.TIMESTAMP_DIR = self.ts_dir
        state_migration.LEGACY_DATA_DIR = self.legacy_dir

        return self

    def __exit__(self, *exc):
        (miscellaneous.PUBLIC_DIR, miscellaneous.STATE_DIR, miscellaneous.TIMESTAMP_DIR,
         miscellaneous.LAST_GOOD_DIR, miscellaneous.HISTORY_DIR,
         profiles.PUBLIC_DIR, profiles.STATE_DIR,
         state_migration.PUBLIC_DIR, state_migration.STATE_DIR,
         state_migration.TIMESTAMP_DIR,
         state_migration.LEGACY_DATA_DIR) = self.original
        self.tmp.cleanup()
        return False

    def legacy(self, file_name, payload, subdir=None):
        """Write a file the way the previous version of this project would have."""
        directory = self.legacy_dir if subdir is None else path.join(self.legacy_dir, subdir)
        makedirs(directory, exist_ok=True)

        with open(path.join(directory, file_name), "w") as f:
            json.dump(payload, f)


### ===============================================================================
### The registry


def test_the_two_sets_do_not_overlap():
    """A dataset in both lists would be served and hidden at the same time."""
    both = datasets.PUBLIC_DATASETS & datasets.STATE_DATASETS

    assert both == frozenset(), f"in both lists: {sorted(both)}"


def test_a_public_dataset_is_routed_to_the_public_directory():
    assert datasets.dataset_kind("market.json") == datasets.PUBLIC


def test_a_state_dataset_is_routed_to_the_state_directory():
    assert datasets.dataset_kind("STATIC_users.json") == datasets.STATE


def test_a_timestamp_is_routed_to_the_timestamp_directory():
    assert datasets.dataset_kind("ts_market.json") == datasets.TIMESTAMP


def test_an_unknown_dataset_raises_instead_of_guessing():
    """Guessing 'public' would publish a file nobody meant to publish."""
    try:
        datasets.dataset_kind("secrets.json")
    except datasets.UnknownDatasetError as e:
        assert "secrets.json" in str(e), f"the error must name the file, got: {e}"
    else:
        raise AssertionError("an unknown dataset must not be routed anywhere")


def test_every_dataset_the_frontend_fetches_is_allowed():
    """The allowlist and the frontend's contract table are two halves of one boundary.

    A dataset the UI fetches and the allowlist does not name is a 404 in production and
    nothing at all in a test - which is exactly the failure this asserts away.
    """
    contract = path.join(path.dirname(path.dirname(path.abspath(__file__))),
                         "frontend", "src", "hooks", "dataContracts.js")

    with open(contract, "r") as f:
        text = f.read()

    ### The contract file lists its datasets as quoted "<name>.json" keys
    names = {line.split('"')[1] for line in text.splitlines()
             if line.strip().startswith('"') and '.json"' in line}

    assert names, f"found no dataset names in {contract}"

    missing = names - datasets.PUBLIC_DATASETS
    assert not missing, f"the frontend fetches these but they are not public: {sorted(missing)}"

    unused = datasets.PUBLIC_DATASETS - names
    assert not unused, f"served but nothing fetches them: {sorted(unused)}"


### ===============================================================================
### Writing


def test_a_public_dataset_is_written_into_the_public_directory():
    with TempTree() as tree:
        miscellaneous.write_json_to_file([{"playerId": "1"}], "market.json")

        assert path.exists(path.join(tree.public_dir, "market.json")), \
            "market.json belongs in the public directory"
        assert not path.exists(path.join(tree.state_dir, "market.json")), \
            "market.json must not also land in the state directory"


def test_a_state_dataset_is_written_into_the_state_directory():
    """The whole reason for this phase: nothing serves data/state, so nothing can leak it."""
    with TempTree() as tree:
        miscellaneous.write_json_to_file({"1": "Meier"}, "STATIC_users.json")

        assert path.exists(path.join(tree.state_dir, "STATIC_users.json")), \
            "STATIC_users.json belongs in the state directory"
        assert not path.exists(path.join(tree.public_dir, "STATIC_users.json")), \
            "STATIC_users.json must never sit in the directory app.py serves"


def test_a_timestamp_is_written_into_the_timestamp_directory():
    with TempTree() as tree:
        miscellaneous.write_json_to_file({"time": "now"}, "ts_market.json")

        assert path.exists(path.join(tree.ts_dir, "ts_market.json")), \
            "ts_market.json belongs in the timestamp directory"


def test_an_unknown_dataset_is_not_written_at_all():
    with TempTree() as tree:
        try:
            miscellaneous.write_json_to_file([], "whatever.json")
        except datasets.UnknownDatasetError:
            pass
        else:
            raise AssertionError("an unroutable name must raise rather than pick a directory")

        assert not path.exists(path.join(tree.public_dir, "whatever.json"))
        assert not path.exists(path.join(tree.state_dir, "whatever.json"))


def test_a_state_dataset_still_gets_a_last_good_snapshot():
    """all_transfers.json is a season of the activity feed and has no second source."""
    with TempTree() as tree:
        miscellaneous.write_json_to_file([{"i": "1"}], "all_transfers.json")
        miscellaneous.write_json_to_file([{"i": "2"}], "all_transfers.json")

        assert miscellaneous.read_last_good("all_transfers.json") == [{"i": "1"}], \
            "the previous content of a state file must still be kept"


### ===============================================================================
### Reading


def test_the_transfer_history_is_read_from_the_state_directory():
    with TempTree() as tree:
        with open(path.join(tree.state_dir, miscellaneous.ALL_TRANSFERS_FILE), "w") as f:
            json.dump([{"i": "1"}], f)

        assert miscellaneous.load_known_transfers() == [{"i": "1"}], \
            "load_known_transfers() must look in the state directory"


def test_a_transfer_history_left_in_the_public_directory_is_not_read():
    """Belt and braces: the reader must not fall back to where the file used to be."""
    with TempTree() as tree:
        with open(path.join(tree.public_dir, miscellaneous.ALL_TRANSFERS_FILE), "w") as f:
            json.dump([{"i": "1"}], f)

        assert miscellaneous.load_known_transfers() == [], \
            "the public directory is not where the transfer history lives"


def test_the_profiles_stage_reads_both_directories():
    """It is the one stage that needs a public dataset and three private ones at once."""
    with TempTree() as tree:
        with open(path.join(tree.public_dir, "turnovers.json"), "w") as f:
            json.dump(["public"], f)
        with open(path.join(tree.state_dir, "STATIC_users.json"), "w") as f:
            json.dump({"1": "Meier"}, f)

        assert profiles._load_json("turnovers.json", written_by="turnovers") == ["public"]
        assert profiles._load_json("STATIC_users.json", written_by="x") == {"1": "Meier"}


### ===============================================================================
### The one-time migration


def test_the_migration_sorts_the_old_directory_into_the_new_two():
    with TempTree() as tree:
        tree.legacy("market.json", [{"playerId": "1"}])
        tree.legacy("STATIC_users.json", {"1": "Meier"})
        tree.legacy("ts_market.json", {"time": "then"}, subdir="timestamps")

        moved = state_migration.migrate_legacy_layout()

        assert moved == 3, f"expected three files moved, got {moved}"
        assert path.exists(path.join(tree.public_dir, "market.json")), "market.json"
        assert path.exists(path.join(tree.state_dir, "STATIC_users.json")), "STATIC_users.json"
        assert path.exists(path.join(tree.ts_dir, "ts_market.json")), "ts_market.json"


def test_the_migration_moves_rather_than_copies():
    """A file left behind in both places is a second source of truth waiting to be read."""
    with TempTree() as tree:
        tree.legacy("market.json", [{"playerId": "1"}])

        state_migration.migrate_legacy_layout()

        assert not path.exists(path.join(tree.legacy_dir, "market.json")), \
            "the old copy must be gone"


def test_the_migration_never_overwrites_a_newer_file():
    """Whatever this version wrote is newer than anything the old layout left behind."""
    with TempTree() as tree:
        tree.legacy("market.json", [{"from": "the old layout"}])

        with open(path.join(tree.public_dir, "market.json"), "w") as f:
            json.dump([{"from": "this version"}], f)

        state_migration.migrate_legacy_layout()

        with open(path.join(tree.public_dir, "market.json")) as f:
            assert json.load(f) == [{"from": "this version"}], "the newer file must survive"

        assert path.exists(path.join(tree.legacy_dir, "market.json")), \
            "and the old one is left where it is rather than deleted"


def test_the_migration_runs_twice_without_complaining():
    """It is called on every start, so the second call has to be a no-op."""
    with TempTree() as tree:
        tree.legacy("market.json", [{"playerId": "1"}])

        assert state_migration.migrate_legacy_layout() == 1
        assert state_migration.migrate_legacy_layout() == 0, \
            "a second run has nothing left to move"


def test_the_migration_leaves_files_it_does_not_recognise():
    """A name this project does not know is not this module's to move."""
    with TempTree() as tree:
        tree.legacy("something_else.json", {"a": 1})

        assert state_migration.migrate_legacy_layout() == 0
        assert path.exists(path.join(tree.legacy_dir, "something_else.json")), \
            "an unknown file stays put instead of being guessed at"


def test_the_migration_creates_the_directories_even_with_nothing_to_move():
    """A fresh deployment has no old directory, and app.py still has to serve something."""
    with TempTree() as tree:
        assert state_migration.migrate_legacy_layout() == 0
        assert path.isdir(tree.public_dir), "the public directory must exist"
        assert path.isdir(tree.state_dir), "the state directory must exist"
        assert path.isdir(tree.ts_dir), "the timestamp directory must exist"


### ===============================================================================

if __name__ == "__main__":
    print("the dataset registry")
    check("the two lists do not overlap", test_the_two_sets_do_not_overlap)
    check("a public dataset is public", test_a_public_dataset_is_routed_to_the_public_directory)
    check("a state dataset is private", test_a_state_dataset_is_routed_to_the_state_directory)
    check("a timestamp is a timestamp", test_a_timestamp_is_routed_to_the_timestamp_directory)
    check("an unknown dataset raises", test_an_unknown_dataset_raises_instead_of_guessing)
    check("the allowlist matches what the frontend fetches",
          test_every_dataset_the_frontend_fetches_is_allowed)

    print("\nwriting")
    check("a public dataset lands in data/public",
          test_a_public_dataset_is_written_into_the_public_directory)
    check("a state dataset lands in data/state",
          test_a_state_dataset_is_written_into_the_state_directory)
    check("a timestamp lands in data/public/timestamps",
          test_a_timestamp_is_written_into_the_timestamp_directory)
    check("an unknown dataset is not written", test_an_unknown_dataset_is_not_written_at_all)
    check("a state dataset keeps its .last-good snapshot",
          test_a_state_dataset_still_gets_a_last_good_snapshot)

    print("\nreading")
    check("the transfer history comes from data/state",
          test_the_transfer_history_is_read_from_the_state_directory)
    check("and never from data/public",
          test_a_transfer_history_left_in_the_public_directory_is_not_read)
    check("the profiles stage reads both", test_the_profiles_stage_reads_both_directories)

    print("\nthe one-time migration")
    check("sorts the old directory into the new two",
          test_the_migration_sorts_the_old_directory_into_the_new_two)
    check("moves rather than copies", test_the_migration_moves_rather_than_copies)
    check("never overwrites a newer file", test_the_migration_never_overwrites_a_newer_file)
    check("runs twice without complaining", test_the_migration_runs_twice_without_complaining)
    check("leaves files it does not recognise",
          test_the_migration_leaves_files_it_does_not_recognise)
    check("creates the directories anyway",
          test_the_migration_creates_the_directories_even_with_nothing_to_move)

    total, passed = len(PASSED), sum(PASSED)
    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)
