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
### average_daily_growth()
### ===============================================================================


def history(*values):
    """Build a market value history, oldest first, as the API sends it."""
    return [{"mv": value} for value in values]


def test_averages_the_daily_change():
    ### Four entries, three days of change: +100, +200, +300 -> mean 200
    result = miscellaneous.average_daily_growth(history(1000, 1100, 1300, 1600), 3)
    assert result == 200, f"expected 200, got {result}"


def test_ignores_history_older_than_the_window():
    """Only the last `days` days count, whatever happened before them."""
    result = miscellaneous.average_daily_growth(history(1, 999999, 1000, 1100, 1300, 1600), 3)
    assert result == 200, f"expected 200, got {result}"


def test_reports_a_falling_market_value_as_negative():
    result = miscellaneous.average_daily_growth(history(1600, 1500, 1400, 1300), 3)
    assert result == -100, f"expected -100, got {result}"


def test_reports_a_flat_market_value_as_zero():
    result = miscellaneous.average_daily_growth(history(1000, 1000, 1000, 1000), 3)
    assert result == 0, f"expected 0, got {result}"


def test_a_too_short_history_has_no_answer():
    """Not a zero: a newly added player has no pace, rather than a pace of nothing."""
    ### Three entries cover two days of change, so a three day window cannot be filled
    assert miscellaneous.average_daily_growth(history(1000, 1100, 1300), 3) is None
    assert miscellaneous.average_daily_growth(history(1000), 3) is None
    assert miscellaneous.average_daily_growth([], 3) is None
    assert miscellaneous.average_daily_growth(None, 3) is None


def test_exactly_enough_history_is_enough():
    ### days + 1 entries is the boundary: four entries for a three day window
    assert miscellaneous.average_daily_growth(history(1000, 1100, 1300, 1600), 3) == 200


def test_honours_a_wider_window():
    ### Seven days from 1000 to 1700 is 100 a day
    values = [1000, 1100, 1200, 1300, 1400, 1500, 1600, 1700]
    assert miscellaneous.average_daily_growth(history(*values), 7) == 100


def test_matches_the_three_day_mean_the_frontend_used_to_compute():
    """The refactor is behaviour-neutral at the default, and this is why.

    The daily deltas telescope: (mv[-1] - mv[-2]) + (mv[-2] - mv[-3]) + (mv[-3] - mv[-4])
    collapses to mv[-1] - mv[-4]. So the mean of today, yesterday and twoDays is exactly
    average_daily_growth(history, 3), and the "Tage bis BEP" column cannot move.
    """
    ### An uneven, partly falling history, so the identity is not proven on a straight line
    values = [900000, 1000000, 980000, 1030000, 1120000, 1115000, 1200000]
    deltas = miscellaneous.market_value_deltas(history(*values))
    old_way = (deltas["today"] + deltas["yesterday"] + deltas["twoDays"]) / 3
    new_way = miscellaneous.average_daily_growth(history(*values), 3)
    assert new_way == old_way, f"expected {old_way} (the old three day mean), got {new_way}"


def test_the_identity_holds_when_the_default_window_is_in_use():
    """Ties the identity to the actual default rather than to a literal 3."""
    set_days(None, None)
    growth_days, _ = miscellaneous.get_bep_days()
    values = [900000, 1000000, 980000, 1030000, 1120000]
    deltas = miscellaneous.market_value_deltas(history(*values))
    old_way = (deltas["today"] + deltas["yesterday"] + deltas["twoDays"]) / 3
    assert miscellaneous.average_daily_growth(history(*values), growth_days) == old_way


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

    print("\naverage_daily_growth()")
    check("averages the daily change", test_averages_the_daily_change)
    check("ignores history older than the window", test_ignores_history_older_than_the_window)
    check("reports a falling market value as negative", test_reports_a_falling_market_value_as_negative)
    check("reports a flat market value as zero", test_reports_a_flat_market_value_as_zero)
    check("has no answer for a too short history", test_a_too_short_history_has_no_answer)
    check("accepts exactly enough history", test_exactly_enough_history_is_enough)
    check("honours a wider window", test_honours_a_wider_window)
    check("matches the three day mean the frontend used to compute",
          test_matches_the_three_day_mean_the_frontend_used_to_compute)
    check("holds the identity at the default window",
          test_the_identity_holds_when_the_default_window_is_in_use)

    total, passed = len(PASSED), sum(PASSED)
    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)
