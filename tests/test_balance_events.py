"""Tests for the per-manager balance event list.

build_balance_events() is the calculation behind the Kontostand column and behind the
event list the frontend shows when a manager is clicked. It is deliberately free of
network calls so it can be tested directly.

Shapes below are taken from real activity feed items in
frontend/src/data/all_transfers.json.

    ./venv/bin/python tests/test_balance_events.py
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
INITIAL = 50_000_000
MANAGER = "Blida FC"


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


def transfer(dt, price, buyer=None, seller=None, name="Müller", team="8", image="content/file/abc.png"):
    """Build an activity feed transfer item like the API returns it."""
    data = {"pi": "755", "pn": name, "tid": team, "t": 2, "trp": price}

    if buyer:
        data["byr"] = buyer
    if seller:
        data["slr"] = seller
    if image:
        data["pim"] = image

    return {"i": dt, "t": 15, "coc": 0, "data": data, "dt": dt}


def build(transfers):
    """Run the function under test with the shared fixtures."""
    return miscellaneous.build_balance_events(transfers, MANAGER, INITIAL, START)


### ===============================================================================

def test_manager_without_transfers_gets_only_the_start_event():
    events = build([])

    assert len(events) == 1, f"expected a single event, got {events}"
    assert events[0]["type"] == "start", f"expected a start event, got {events[0]}"
    assert events[0]["amount"] == INITIAL, f"expected the starting budget, got {events[0]}"
    assert events[0]["balance"] == INITIAL, f"expected the starting balance, got {events[0]}"


def test_start_event_carries_no_player_or_partner():
    event = build([])[0]

    for field in ("playerName", "playerImage", "teamId", "tradePartner"):
        assert event[field] is None, f"expected {field} to be None on the start event, got {event}"


def test_transfers_of_other_managers_are_ignored():
    events = build([
        transfer("2026-08-02T10:00:00Z", 1_000_000, buyer="Jonny", seller="Gianluca"),
    ])

    assert len(events) == 1, f"expected only the start event, got {events}"


def test_last_event_balance_matches_the_running_sum():
    events = build([
        transfer("2026-08-02T10:00:00Z", 2_000_000, buyer=MANAGER, seller="Jonny"),
        transfer("2026-08-03T10:00:00Z", 5_000_000, seller=MANAGER, buyer="Jonny"),
    ])

    expected = INITIAL - 2_000_000 + 5_000_000
    assert events[-1]["balance"] == expected, \
        f"expected a final balance of {expected}, got {events[-1]}"


def test_events_come_back_in_chronological_order():
    ### The feed pages newest first, so the input is deliberately out of order here
    events = build([
        transfer("2026-08-05T10:00:00Z", 3_000_000, seller=MANAGER),
        transfer("2026-08-02T10:00:00Z", 1_000_000, buyer=MANAGER),
    ])

    dates = [event["date"] for event in events]
    assert dates == sorted(dates), f"expected chronological order, got {dates}"
    ### The running balance is only meaningful in that order
    assert events[1]["balance"] == INITIAL - 1_000_000, \
        f"expected the earlier buy to come first, got {events}"


def test_buy_is_negative_and_sell_is_positive():
    events = build([
        transfer("2026-08-02T10:00:00Z", 2_000_000, buyer=MANAGER, seller="Jonny"),
        transfer("2026-08-03T10:00:00Z", 5_000_000, seller=MANAGER, buyer="Jonny"),
    ])

    assert events[1]["type"] == "buy" and events[1]["amount"] == -2_000_000, \
        f"expected a negative buy, got {events[1]}"
    assert events[2]["type"] == "sell" and events[2]["amount"] == 5_000_000, \
        f"expected a positive sell, got {events[2]}"


def test_events_before_the_start_date_are_ignored():
    events = build([
        transfer("2026-07-30T10:00:00Z", 9_000_000, buyer=MANAGER),
        transfer("2026-08-02T10:00:00Z", 1_000_000, buyer=MANAGER),
    ])

    assert len(events) == 2, f"expected the pre-season transfer to be dropped, got {events}"
    assert events[-1]["balance"] == INITIAL - 1_000_000, \
        f"expected the dropped transfer not to move the balance, got {events}"


def test_trade_partner_is_the_other_manager():
    events = build([
        transfer("2026-08-02T10:00:00Z", 1_000_000, buyer=MANAGER, seller="Jonny"),
        transfer("2026-08-03T10:00:00Z", 2_000_000, seller=MANAGER, buyer="Gianluca"),
    ])

    assert events[1]["tradePartner"] == "Jonny", f"expected the seller as partner, got {events[1]}"
    assert events[2]["tradePartner"] == "Gianluca", f"expected the buyer as partner, got {events[2]}"


def test_trade_partner_is_none_for_a_one_sided_event():
    ### Bought from the Kickbase market: nobody is named on the other side
    events = build([transfer("2026-08-02T10:00:00Z", 1_000_000, buyer=MANAGER)])

    assert events[1]["tradePartner"] is None, \
        f"expected no trade partner, got {events[1]}"


def test_player_image_gets_the_cdn_prefix():
    events = build([transfer("2026-08-02T10:00:00Z", 1_000_000, buyer=MANAGER)])

    assert events[1]["playerImage"] == "https://kickbase.b-cdn.net/content/file/abc.png", \
        f"expected an absolute image URL, got {events[1]}"
    assert events[1]["playerName"] == "Müller", f"expected the player name, got {events[1]}"
    assert events[1]["teamId"] == "8", f"expected the team id, got {events[1]}"


def test_missing_player_image_stays_none():
    ### A relative path joined onto the CDN base would otherwise become the base URL itself
    events = build([transfer("2026-08-02T10:00:00Z", 1_000_000, buyer=MANAGER, image=None)])

    assert events[1]["playerImage"] is None, f"expected no image URL, got {events[1]}"


def test_the_input_list_is_not_reordered():
    ### The feed is cached per run and shared with turnovers(), so sorting it in place
    ### would silently reorder it for every other caller
    transfers = [
        transfer("2026-08-05T10:00:00Z", 3_000_000, seller=MANAGER),
        transfer("2026-08-02T10:00:00Z", 1_000_000, buyer=MANAGER),
    ]
    before = [item["dt"] for item in transfers]

    build(transfers)

    assert [item["dt"] for item in transfers] == before, \
        "expected the caller's list to be left alone"


### ===============================================================================

if __name__ == "__main__":
    print("build_balance_events()")
    check("a manager without transfers gets only the start event",
          test_manager_without_transfers_gets_only_the_start_event)
    check("the start event carries no player or partner",
          test_start_event_carries_no_player_or_partner)
    check("transfers of other managers are ignored",
          test_transfers_of_other_managers_are_ignored)
    check("the last event balance matches the running sum",
          test_last_event_balance_matches_the_running_sum)
    check("events come back in chronological order",
          test_events_come_back_in_chronological_order)
    check("a buy is negative and a sell is positive",
          test_buy_is_negative_and_sell_is_positive)
    check("events before START_DATE are ignored",
          test_events_before_the_start_date_are_ignored)
    check("the trade partner is the other manager",
          test_trade_partner_is_the_other_manager)
    check("the trade partner is none for a one sided event",
          test_trade_partner_is_none_for_a_one_sided_event)
    check("the player image gets the CDN prefix",
          test_player_image_gets_the_cdn_prefix)
    check("a missing player image stays none",
          test_missing_player_image_stays_none)
    check("the input list is not reordered",
          test_the_input_list_is_not_reordered)

    total, passed = len(PASSED), sum(PASSED)
    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)
