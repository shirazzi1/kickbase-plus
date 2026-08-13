"""### Tests for the break-even horizons BEP_GROWTH_DAYS and BEP_TARGET_DAYS.

Dependency free on purpose: the project has no test framework, so this runs with the
project venv directly and needs no extra packages.

    ./venv/bin/python tests/test_bep_config.py
"""

import sys

from os import environ, path

### Make the repository root importable regardless of where this is run from
sys.path.insert(0, path.dirname(path.dirname(path.abspath(__file__))))

from backend import exceptions, miscellaneous

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


def set_days(growth, target):
    """Set or clear both horizon variables."""
    for name, value in (("BEP_GROWTH_DAYS", growth), ("BEP_TARGET_DAYS", target)):
        if value is None:
            environ.pop(name, None)
        else:
            environ[name] = value


def expect_rejected(growth, target, expected_in_message):
    """Assert that get_bep_days() rejects the pair and names the offending variable."""
    set_days(growth, target)
    try:
        miscellaneous.get_bep_days()
    except exceptions.KickbaseException as e:
        assert expected_in_message in str(e), \
            f"error should name {expected_in_message}, got: {e}"
    else:
        raise AssertionError(f"expected a KickbaseException for {growth!r}/{target!r}")


### ===============================================================================
### get_bep_days()
### ===============================================================================


def test_defaults_to_three_and_three():
    """The defaults are what the frontend hardcoded before, so nothing moves."""
    set_days(None, None)
    assert miscellaneous.get_bep_days() == (3, 3), \
        f"expected (3, 3), got {miscellaneous.get_bep_days()}"


def test_reads_both_values():
    set_days("7", "14")
    assert miscellaneous.get_bep_days() == (7, 14), \
        f"expected (7, 14), got {miscellaneous.get_bep_days()}"


def test_values_are_independent():
    """The two horizons answer different questions and must not be coupled."""
    set_days("7", "3")
    assert miscellaneous.get_bep_days() == (7, 3)
    set_days("3", "7")
    assert miscellaneous.get_bep_days() == (3, 7)


def test_rejects_non_integers():
    expect_rejected("drei", "3", "BEP_GROWTH_DAYS")
    expect_rejected("3", "3.5", "BEP_TARGET_DAYS")


def test_rejects_zero_and_negative():
    ### A zero window would divide by zero; a negative one is meaningless
    expect_rejected("0", "3", "BEP_GROWTH_DAYS")
    expect_rejected("-1", "3", "BEP_GROWTH_DAYS")
    expect_rejected("3", "0", "BEP_TARGET_DAYS")
    expect_rejected("3", "-5", "BEP_TARGET_DAYS")


def test_rejects_growth_window_beyond_the_history():
    """The history holds 365 entries, so a 365-day window can never be filled."""
    expect_rejected("365", "3", "BEP_GROWTH_DAYS")
    set_days("364", "3")
    assert miscellaneous.get_bep_days() == (364, 3), "364 is the largest usable window"


def test_rejects_an_empty_value():
    expect_rejected("", "3", "BEP_GROWTH_DAYS")


### ===============================================================================

if __name__ == "__main__":
    print("get_bep_days()")
    check("defaults to three and three", test_defaults_to_three_and_three)
    check("reads both values", test_reads_both_values)
    check("keeps the two values independent", test_values_are_independent)
    check("rejects non-integers", test_rejects_non_integers)
    check("rejects zero and negative values", test_rejects_zero_and_negative)
    check("rejects a growth window beyond the history", test_rejects_growth_window_beyond_the_history)
    check("rejects an empty value", test_rejects_an_empty_value)

    total, passed = len(PASSED), sum(PASSED)
    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)
