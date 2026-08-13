"""
### Moving the datasets out of frontend/src/data, once, on the first start of this version.

Until this version every dataset was written into `frontend/src/data`, because the React
build imported them from there at compile time. They are now split into `data/public` (what
the browser fetches) and `data/state` (what only the backend reads), and this module walks
the old directory once and puts each file where it now belongs.

Three properties matter, and each of them is a bug that would otherwise happen:

  - **Never overwrite.** A file that already exists in the new location was written by this
    version and is newer than anything left over from the old one. The old copy is left
    where it is rather than deleted, so a second look is always possible.
  - **Move, do not rename.** `data/` is the documented volume mount, so the source and the
    target are routinely on different filesystems. `os.rename()` fails with EXDEV across
    those; `shutil.move()` falls back to copy-and-delete.
  - **Never fatal.** Everything here is recoverable by the next scheduled run - it refetches
    all of it. A migration that raises would turn a cosmetic problem into a container that
    does not start, so failures are logged per file and the rest continues.

Only files the dataset registry knows are touched. Anything else in the old directory is
left alone and named in the log, because a file this project does not recognise is not this
module's to move.
"""

import logging
import shutil

from os import listdir, makedirs, path

from backend import datasets
from backend.paths import LEGACY_DATA_DIR, PUBLIC_DIR, STATE_DIR, TIMESTAMP_DIR

### ===============================================================================

### The subdirectory of the old data directory that held the timestamps. Its contents are
### routed by the same rule as everything else - a "ts_" name lands in TIMESTAMP_DIR - so it
### only has to be found, not treated specially.
LEGACY_TIMESTAMP_DIRNAME = "timestamps"


def _move(source: str, target: str) -> bool:
    """### Move one file, unless something is already there.

    Args:
        source (str): The file to move.
        target (str): Where it should end up.

    Returns:
        bool: True if the file was moved.
    """
    if path.exists(target):
        logging.debug(f"Not migrating {source}: {target} already exists.")
        return False

    try:
        makedirs(path.dirname(target), exist_ok=True)
        ### Not os.replace(): data/ is a volume mount in every documented deployment, so
        ### this crosses a filesystem boundary and a rename would fail with EXDEV.
        shutil.move(source, target)
    except OSError as e:
        logging.warning(f"Could not migrate {source} to {target}: {e}")
        return False

    logging.info(f"Migrated {path.basename(source)} to {path.dirname(target)}")

    return True


def _migrate_directory(source_dir: str) -> int:
    """### Move every dataset in one directory to where the registry says it belongs.

    Args:
        source_dir (str): The directory to empty.

    Returns:
        int: How many files were moved.
    """
    moved = 0

    if not path.isdir(source_dir):
        return 0

    try:
        entries = sorted(listdir(source_dir))
    except OSError as e:
        logging.warning(f"Could not read {source_dir} while migrating: {e}")
        return 0

    for entry in entries:
        source = path.join(source_dir, entry)

        if not path.isfile(source) or not entry.endswith(".json"):
            continue

        try:
            ### Resolved against this module's own names rather than through
            ### datasets.dataset_dir(), so a test can point the migration at a temporary
            ### tree the same way every other test points a run at one.
            target_dir = {
                datasets.TIMESTAMP: TIMESTAMP_DIR,
                datasets.PUBLIC: PUBLIC_DIR,
                datasets.STATE: STATE_DIR,
            }[datasets.dataset_kind(entry)]
        except datasets.UnknownDatasetError:
            ### Not ours to move. Named rather than swallowed: on a real deployment this is
            ### how a dataset that was renamed at some point makes itself known.
            logging.info(f"Leaving {source} where it is, it is not a known dataset.")
            continue

        if _move(source, path.join(target_dir, entry)):
            moved += 1

    return moved


def migrate_legacy_layout() -> int:
    """### Put the old frontend/src/data contents into data/public and data/state.

    Safe to call on every start: with nothing left to move it does nothing but a directory
    listing, and it never touches a file that already exists in the new location.

    Args:
        None

    Returns:
        int: How many files were moved, so the caller can log whether anything happened.
    """
    ### The new directories exist from here on even when there is nothing to migrate, so a
    ### fresh deployment serves an empty directory rather than a 404 from a missing one.
    for directory in (PUBLIC_DIR, TIMESTAMP_DIR, STATE_DIR):
        try:
            makedirs(directory, exist_ok=True)
        except OSError as e:
            logging.warning(f"Could not create {directory}: {e}")

    if not path.isdir(LEGACY_DATA_DIR):
        return 0

    moved = _migrate_directory(LEGACY_DATA_DIR)
    moved += _migrate_directory(path.join(LEGACY_DATA_DIR, LEGACY_TIMESTAMP_DIRNAME))

    if moved:
        logging.info(f"Migrated {moved} file(s) out of {LEGACY_DATA_DIR}.")

    return moved
