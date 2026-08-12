"""Tests for dropping bookings that a league admin reverted.

Kickbase emits no cancellation event. A reverted transfer simply stays in the activity
feed next to the booking that replaced it, and both get counted. What gives it away is
ownership: nobody can sell a player they do not own.

The two fixtures at the bottom are the real incident from league Kickbase-Elite 26/27 on
2026-08-08, where a swap between shirazzi and Reddy was booked twice. Checked against the
live squads afterwards: Seiwald ended up with Reddy and Gomis with shirazzi, so the later
booking is the one that stuck.

    ./venv/bin/python tests/test_reverted_transfers.py
"""

import sys

from os import path

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


def transfer(tid, dt, player, price, buyer=None, seller=None):
    """Build an activity feed transfer item like the API returns it."""
    data = {"pi": player, "pn": f"Player{player}", "tid": "8", "t": 2, "trp": price}

    if buyer:
        data["byr"] = buyer
    if seller:
        data["slr"] = seller

    return {"i": tid, "t": 15, "coc": 0, "data": data, "dt": dt}


def ids(transfers):
    """The surviving transfer IDs, for readable assertions."""
    return [item["i"] for item in transfers]


### ===============================================================================

def test_a_clean_feed_is_returned_unchanged():
    feed = [
        transfer("1", "2026-08-02T10:00:00Z", "100", 5_000_000, buyer="Anna"),
        transfer("2", "2026-08-03T10:00:00Z", "100", 6_000_000, seller="Anna", buyer="Ben"),
        transfer("3", "2026-08-04T10:00:00Z", "100", 7_000_000, seller="Ben"),
    ]

    assert ids(miscellaneous.drop_reverted_transfers(feed)) == ["1", "2", "3"], \
        "expected a consistent chain to survive untouched"


def test_a_start_squad_sale_is_not_an_anomaly():
    ### Nobody bought the player, so the first seller had them assigned at season start
    feed = [transfer("1", "2026-08-02T10:00:00Z", "100", 5_000_000, seller="Anna")]

    assert ids(miscellaneous.drop_reverted_transfers(feed)) == ["1"], \
        "expected a start squad sale to survive"


def test_the_earlier_of_two_sales_by_the_same_manager_is_dropped():
    ### The Seiwald case: sold to Ben, reverted, sold to Ben again at a different price
    feed = [
        transfer("1", "2026-08-02T23:26:18Z", "6176", 18_000_000, buyer="Anna"),
        transfer("2", "2026-08-08T17:12:55Z", "6176", 18_900_001, seller="Anna", buyer="Ben"),
        transfer("3", "2026-08-08T17:19:55Z", "6176", 19_000_000, seller="Anna", buyer="Ben"),
    ]

    assert ids(miscellaneous.drop_reverted_transfers(feed)) == ["1", "3"], \
        "expected the reverted booking to go and the buy plus the later sale to stay"


def test_a_sale_of_an_already_free_player_drops_the_earlier_sale():
    ### The Gomis case: sold to the market, reverted, sold directly to another manager
    feed = [
        transfer("1", "2026-08-08T17:08:45Z", "10113", 4_962_082, seller="Ben"),
        transfer("2", "2026-08-08T17:17:44Z", "10113", 4_962_082, seller="Ben", buyer="Anna"),
    ]

    assert ids(miscellaneous.drop_reverted_transfers(feed)) == ["2"], \
        "expected the reverted market sale to go"


def test_buying_a_player_someone_else_owns_drops_the_earlier_booking():
    feed = [
        transfer("1", "2026-08-02T10:00:00Z", "100", 5_000_000, buyer="Anna"),
        transfer("2", "2026-08-03T10:00:00Z", "100", 6_000_000, buyer="Ben"),
    ]

    assert ids(miscellaneous.drop_reverted_transfers(feed)) == ["2"], \
        "expected the superseded purchase to go"


def test_a_player_traded_back_and_forth_is_left_alone():
    ### Anna sells to Ben, buys them back from Ben, sells them on. All legitimate.
    feed = [
        transfer("1", "2026-08-02T10:00:00Z", "100", 5_000_000, buyer="Anna"),
        transfer("2", "2026-08-03T10:00:00Z", "100", 6_000_000, seller="Anna", buyer="Ben"),
        transfer("3", "2026-08-04T10:00:00Z", "100", 7_000_000, seller="Ben", buyer="Anna"),
        transfer("4", "2026-08-05T10:00:00Z", "100", 8_000_000, seller="Anna", buyer="Ben"),
    ]

    assert ids(miscellaneous.drop_reverted_transfers(feed)) == ["1", "2", "3", "4"], \
        "expected a legitimate back and forth to survive"


def test_other_players_are_untouched_by_one_players_anomaly():
    feed = [
        transfer("1", "2026-08-02T10:00:00Z", "100", 1_000_000, seller="Anna", buyer="Ben"),
        transfer("2", "2026-08-03T10:00:00Z", "200", 2_000_000, seller="Anna", buyer="Ben"),
        transfer("3", "2026-08-04T10:00:00Z", "100", 3_000_000, seller="Anna", buyer="Ben"),
    ]

    ### Player 100 is the anomaly; player 200 has nothing to do with it
    assert ids(miscellaneous.drop_reverted_transfers(feed)) == ["2", "3"], \
        "expected only the reverted booking of player 100 to go"


def test_the_input_order_is_preserved():
    ### The feed pages newest first, and callers sort it themselves
    feed = [
        transfer("3", "2026-08-04T10:00:00Z", "100", 7_000_000, seller="Ben"),
        transfer("1", "2026-08-02T10:00:00Z", "100", 5_000_000, buyer="Anna"),
        transfer("2", "2026-08-03T10:00:00Z", "100", 6_000_000, seller="Anna", buyer="Ben"),
    ]

    assert ids(miscellaneous.drop_reverted_transfers(feed)) == ["3", "1", "2"], \
        "expected the caller's order back, not chronological order"


def test_the_input_list_is_not_mutated():
    feed = [
        transfer("1", "2026-08-02T10:00:00Z", "100", 5_000_000, buyer="Anna"),
        transfer("2", "2026-08-03T10:00:00Z", "100", 6_000_000, buyer="Ben"),
    ]
    before = ids(feed)

    miscellaneous.drop_reverted_transfers(feed)

    assert ids(feed) == before, "expected the caller's list to be left alone"


def test_handles_an_empty_list():
    assert miscellaneous.drop_reverted_transfers([]) == [], "expected an empty list back"


def test_the_real_incident_from_2026_08_08():
    ### Verbatim from frontend/src/data/all_transfers.json
    seiwald_buy = {"i": "12195697057", "t": 15, "coc": 0, "dt": "2026-08-02T23:26:18Z",
                   "data": {"byr": "shirazzi", "pi": "6176", "pn": "Seiwald", "tid": "40",
                            "t": 2, "trp": 18000000}}
    gomis_reverted = {"i": "12221601310", "t": 15, "coc": 0, "dt": "2026-08-08T17:08:45Z",
                      "data": {"slr": "Reddy", "pi": "10113", "pn": "Gomis", "tid": "24",
                               "t": 2, "trp": 4962082}}
    seiwald_reverted = {"i": "12221615722", "t": 15, "coc": 0, "dt": "2026-08-08T17:12:55Z",
                        "data": {"slr": "shirazzi", "byr": "Reddy", "pi": "6176",
                                 "pn": "Seiwald", "tid": "40", "t": 2, "trp": 18900001}}
    gomis_final = {"i": "12221633380", "t": 15, "coc": 0, "dt": "2026-08-08T17:17:44Z",
                   "data": {"slr": "Reddy", "byr": "shirazzi", "pi": "10113", "pn": "Gomis",
                            "tid": "24", "t": 2, "trp": 4962082}}
    seiwald_final = {"i": "12221642212", "t": 15, "coc": 0, "dt": "2026-08-08T17:19:55Z",
                     "data": {"slr": "shirazzi", "byr": "Reddy", "pi": "6176", "pn": "Seiwald",
                              "tid": "40", "t": 2, "trp": 19000000}}

    feed = [seiwald_buy, gomis_reverted, seiwald_reverted, gomis_final, seiwald_final]
    kept = ids(miscellaneous.drop_reverted_transfers(feed))

    assert kept == ["12195697057", "12221633380", "12221642212"], \
        f"expected both reverted bookings to go, kept {kept}"


### ===============================================================================

if __name__ == "__main__":
    print("drop_reverted_transfers()")
    check("a clean feed is returned unchanged", test_a_clean_feed_is_returned_unchanged)
    check("a start squad sale is not an anomaly", test_a_start_squad_sale_is_not_an_anomaly)
    check("the earlier of two sales by the same manager is dropped",
          test_the_earlier_of_two_sales_by_the_same_manager_is_dropped)
    check("a sale of an already free player drops the earlier sale",
          test_a_sale_of_an_already_free_player_drops_the_earlier_sale)
    check("buying a player someone else owns drops the earlier booking",
          test_buying_a_player_someone_else_owns_drops_the_earlier_booking)
    check("a player traded back and forth is left alone",
          test_a_player_traded_back_and_forth_is_left_alone)
    check("other players are untouched by one player's anomaly",
          test_other_players_are_untouched_by_one_players_anomaly)
    check("the input order is preserved", test_the_input_order_is_preserved)
    check("the input list is not mutated", test_the_input_list_is_not_mutated)
    check("handles an empty list", test_handles_an_empty_list)
    check("the real incident from 2026-08-08", test_the_real_incident_from_2026_08_08)

    total, passed = len(PASSED), sum(PASSED)
    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)
