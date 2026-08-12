"""Tests for the estimated daily login bonus.

Kickbase pays a login bonus that grows by 10.000 a day up to 100.000 and stays there.
The amounts below are the real type 22 feed events of one manager, so the formula is
checked against reality, not against itself.

The day counter runs over calendar days in the app timezone. The real feed proves it:
day 11 at 2026-08-11T01:13:09Z and day 12 at 2026-08-11T22:03:39Z are 21 hours apart
but fall on different days in Europe/Berlin.

    ./venv/bin/python tests/test_login_bonus.py
"""

import sys

from datetime import datetime, timezone
from os import path

### Make the repository root importable regardless of where this is run from
sys.path.insert(0, path.dirname(path.dirname(path.abspath(__file__))))

from backend import miscellaneous

### ===============================================================================

PASSED = []

START = datetime(2026, 8, 1, 18, 0, 0, tzinfo=timezone.utc)

### The real series collected by shirazzi, day -> amount
REAL = {2: 10_000, 3: 20_000, 4: 30_000, 5: 40_000, 6: 50_000, 7: 60_000,
        8: 70_000, 9: 80_000, 10: 90_000, 11: 100_000, 12: 100_000}


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


def at(day, hour=12):
    """A UTC instant on the given day of August 2026."""
    return datetime(2026, 8, day, hour, 0, 0, tzinfo=timezone.utc)


### ===============================================================================

def test_the_start_day_pays_nothing():
    assert miscellaneous.build_login_bonus_events(START, at(1, 20)) == [], \
        "expected no event on day one"


def test_the_second_day_pays_ten_thousand():
    events = miscellaneous.build_login_bonus_events(START, at(2))

    assert len(events) == 1, f"expected one event, got {events}"
    assert events[0]["amount"] == 10_000, f"expected 10000, got {events[0]}"
    assert events[0]["type"] == "login_bonus", f"expected a login_bonus event, got {events[0]}"


def test_the_amounts_match_the_real_feed():
    events = miscellaneous.build_login_bonus_events(START, at(12, 12))
    amounts = [e["amount"] for e in events]

    assert amounts == [REAL[d] for d in sorted(REAL)], \
        f"expected the real series {[REAL[d] for d in sorted(REAL)]}, got {amounts}"


def test_the_total_after_twelve_days():
    events = miscellaneous.build_login_bonus_events(START, at(12, 12))

    assert sum(e["amount"] for e in events) == 650_000, \
        f"expected 650000 in total, got {sum(e['amount'] for e in events)}"


def test_the_amount_is_capped_at_one_hundred_thousand():
    events = miscellaneous.build_login_bonus_events(START, at(31, 23))

    assert max(e["amount"] for e in events) == 100_000, "expected the cap to hold"
    assert events[-1]["amount"] == 100_000, "expected the last day to pay the cap"


def test_the_counter_uses_calendar_days_not_elapsed_hours():
    ### 2026-08-11T22:03Z is 2026-08-12 in Europe/Berlin, so it has to be day 12.
    ### Counting elapsed hours from the 18:00 start would still say day 11.
    events = miscellaneous.build_login_bonus_events(
        START, datetime(2026, 8, 11, 22, 3, tzinfo=timezone.utc))

    assert len(events) == 11, f"expected days 2 to 12, got {len(events)} events"
    assert events[-1]["amount"] == 100_000, f"expected day 12, got {events[-1]}"


def test_events_are_chronological_and_carry_no_player():
    events = miscellaneous.build_login_bonus_events(START, at(5))

    dates = [e["date"] for e in events]
    assert dates == sorted(dates), f"expected chronological order, got {dates}"
    for e in events:
        for field in ("playerName", "playerImage", "teamId", "tradePartner"):
            assert e[field] is None, f"expected {field} to be None, got {e}"


def test_nothing_before_the_season_starts():
    assert miscellaneous.build_login_bonus_events(START, at(1, 1)) == [], \
        "expected no events before the season start"


### ===============================================================================

if __name__ == "__main__":
    print("build_login_bonus_events()")
    check("the start day pays nothing", test_the_start_day_pays_nothing)
    check("the second day pays ten thousand", test_the_second_day_pays_ten_thousand)
    check("the amounts match the real feed", test_the_amounts_match_the_real_feed)
    check("the total after twelve days is 650000", test_the_total_after_twelve_days)
    check("the amount is capped at 100000", test_the_amount_is_capped_at_one_hundred_thousand)
    check("the counter uses calendar days", test_the_counter_uses_calendar_days_not_elapsed_hours)
    check("events are chronological and carry no player",
          test_events_are_chronological_and_carry_no_player)
    check("nothing before the season starts", test_nothing_before_the_season_starts)

    total, passed = len(PASSED), sum(PASSED)
    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)
