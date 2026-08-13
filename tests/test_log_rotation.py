"""Tests for the log handlers actually deleting old log data.

Both file handlers used to rotate on a timer with backupCount=0. That combination never
deletes anything: TimedRotatingFileHandler only removes backups, and with none kept there
is nothing to remove - the rolled over file just stays behind under its dated name. The
DEBUG log had grown past 9 MB in the deployment and kept growing with every run.

    ./venv/bin/python tests/test_log_rotation.py
"""

import logging
import sys
import tempfile

from glob import glob
from logging.config import dictConfig
from os import path

### Make the repository root importable regardless of where this is run from
sys.path.insert(0, path.dirname(path.dirname(path.abspath(__file__))))

import main

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


def handlers():
    """The two file handlers from the configuration, keyed by name."""
    config = main.build_logging_config("/tmp/does-not-need-to-exist")
    return {name: config["handlers"][name] for name in ("file", "verbose_file")}


### ===============================================================================
### build_logging_config()
### ===============================================================================


def test_both_file_handlers_rotate_on_size():
    for name, handler in handlers().items():
        assert handler["class"] == "logging.handlers.RotatingFileHandler", \
            f"{name} rotates on {handler['class']}, which does not bound the disk usage"


def test_both_file_handlers_keep_a_real_backup_count():
    """backupCount=0 was the bug: nothing is ever deleted."""
    for name, handler in handlers().items():
        assert handler["backupCount"] > 0, \
            f"{name} keeps {handler['backupCount']} backups, so nothing is ever deleted"


def test_disk_usage_is_bounded_per_log():
    for name, handler in handlers().items():
        ceiling = handler["maxBytes"] * (handler["backupCount"] + 1)
        assert 0 < ceiling <= 100 * 1024 * 1024, \
            f"{name} may grow to {ceiling} bytes, which is no useful bound"


def test_log_files_land_in_the_given_directory():
    config = main.build_logging_config(path.join("some", "where"))
    for name in ("file", "verbose_file"):
        filename = config["handlers"][name]["filename"]
        assert filename.startswith(path.join("some", "where")), \
            f"{name} writes to {filename}, outside the given log directory"


### ===============================================================================
### The configuration in action
### ===============================================================================


def test_rotation_actually_deletes_the_oldest_file():
    """Write past the cap and check that the number of files stops growing.

    The old configuration passed every reading of the settings but still filled the disk,
    so the handler is exercised rather than only inspected.
    """
    with tempfile.TemporaryDirectory() as log_dir:
        config = main.build_logging_config(log_dir)

        ### Small enough to roll over within the loop below
        for name in ("file", "verbose_file"):
            config["handlers"][name]["maxBytes"] = 1024

        ### Leave the console handler out: the test does not need its output
        config["handlers"].pop("console")
        config["loggers"]["root"]["handlers"] = ["file", "verbose_file"]

        try:
            dictConfig(config)

            for i in range(500):
                logging.info(f"line {i} " + "x" * 100)

            for handler in logging.getLogger().handlers:
                handler.close()

            files = glob(path.join(log_dir, "kickbase-insights.log*"))
            backups = config["handlers"]["file"]["backupCount"]

            assert len(files) == backups + 1, \
                f"expected {backups + 1} files at most, found {len(files)}: {sorted(files)}"

            biggest = max(path.getsize(f) for f in files)
            assert biggest < 4 * 1024, \
                f"a log file grew to {biggest} bytes despite a 1024 byte cap"
        finally:
            ### Detach the handlers again so they do not hold on to the temporary directory
            dictConfig({"version": 1, "disable_existing_loggers": False,
                        "loggers": {"root": {"handlers": [], "level": "WARNING"}}})


### ===============================================================================

if __name__ == "__main__":
    print("build_logging_config()")
    check("both file handlers rotate on size", test_both_file_handlers_rotate_on_size)
    check("both file handlers keep a real backup count", test_both_file_handlers_keep_a_real_backup_count)
    check("disk usage is bounded per log", test_disk_usage_is_bounded_per_log)
    check("log files land in the given directory", test_log_files_land_in_the_given_directory)

    print("\nthe configuration in action")
    check("rotation actually deletes the oldest file", test_rotation_actually_deletes_the_oldest_file)

    total, passed = len(PASSED), sum(PASSED)
    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)
