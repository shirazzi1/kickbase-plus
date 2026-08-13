"""Tests for what the auction solver needs from the backend, and for its calibration.

The solver itself is frontend arithmetic and tested in `marketFormulas.test.js`. Two things
it cannot derive on its own had to come from the scraper, and both are tested here:

  - `market.json` now carries `sellerId`. Excluding the seller from the bidders for their
    own listing by display name alone breaks as soon as two managers pick the same name.
  - `balances.json` now carries `isSelf`. Nothing in the frontend knew who the logged in
    user is, so neither the own budget cap nor "everyone except me" was expressible.

The rest covers `tests/calibrate_min_bid.py`, which reconstructs every manager's bidding
ceiling at a past instant. That reconstruction is the whole calibration, so it is checked
against a hand-built timeline rather than trusted.

    ./venv/bin/python tests/test_auction_solver.py
"""

import json
import sys
import tempfile

from datetime import datetime, timezone
from os import environ, makedirs, path

sys.path.insert(0, path.dirname(path.dirname(path.abspath(__file__))))
sys.path.insert(0, path.dirname(path.abspath(__file__)))

import calibrate_min_bid

from backend import miscellaneous
from backend.kickbase.endpoints.leagues import Market_Players

### ===============================================================================

LEAGUE_ID = "11412166"
OWN_USER_ID = "3854976"
OTHER_USER_ID = "2592773"
START_DATE = "2026-08-01T18:00:00Z"

PASSED = []


def check(name, fn):
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


### ===============================================================================
### market() -> sellerId
### ===============================================================================


### Listed by a league member. "u" carries both their id and their name.
USER_LISTED = {
    "i": "49", "fn": "Matthias", "n": "Ginter", "tid": "5", "pos": 2, "st": 0,
    "mvt": 1, "mv": 26260331, "prc": 32000000, "ofc": 3,
    "dt": "2026-08-12T09:15:00Z",
    "u": {"i": OTHER_USER_ID, "n": "Meier", "uim": "user/91fd.jpe", "isvf": False, "st": 0},
}

### A Kickbase listing: no "u" at all, so there is no seller to exclude
FREE_AGENT = {
    "i": "1811", "fn": "Jeffrey", "n": "Gouweleeuw", "tid": "13", "pos": 2, "st": 0,
    "mvt": 1, "mv": 10399428, "prc": 10399428, "ofc": 0, "exs": 13315,
}


def run_market():
    """Run market() against stubbed API calls and return the rows it wrote, by last name."""
    import main
    from backend.kickbase.v4 import leagues

    market_items = [USER_LISTED, FREE_AGENT]

    with tempfile.TemporaryDirectory() as tmp:
        data_dir = path.join(tmp, "data")
        ts_dir = path.join(data_dir, "timestamps")
        makedirs(ts_dir, exist_ok=True)

        original = (miscellaneous.PUBLIC_DIR, miscellaneous.STATE_DIR,
                    miscellaneous.TIMESTAMP_DIR,
                    miscellaneous.LAST_GOOD_DIR,
                    leagues.get_market, leagues.player_statistics, leagues.player_marketvalue)
        miscellaneous.PUBLIC_DIR = data_dir
        miscellaneous.STATE_DIR = data_dir
        miscellaneous.TIMESTAMP_DIR = ts_dir
        miscellaneous.LAST_GOOD_DIR = path.join(tmp, "last-good")

        try:
            leagues.get_market = lambda token, lid: [Market_Players(p) for p in market_items]
            leagues.player_statistics = lambda token, lid, pid: {"i": str(pid), "st": 0}
            leagues.player_marketvalue = lambda token, pid: [
                {"dt": 20000 + i, "mv": mv} for i, mv in enumerate((1000, 1200, 1500, 1900))]

            class FakeLeague:
                id = LEAGUE_ID
                name = "Kickbase-Elite 26/27"

            main.market("token", FakeLeague(), OWN_USER_ID)

            with open(path.join(data_dir, "market.json")) as f:
                rows = json.load(f)
        finally:
            (miscellaneous.PUBLIC_DIR, miscellaneous.STATE_DIR,
             miscellaneous.TIMESTAMP_DIR, miscellaneous.LAST_GOOD_DIR,
             leagues.get_market, leagues.player_statistics,
             leagues.player_marketvalue) = original

    return {row["lastName"]: row for row in rows}


def test_seller_id_lands_next_to_the_seller_name():
    """The name cannot key the join: two managers may pick the same one."""
    rows = run_market()
    assert rows["Ginter"]["sellerId"] == OTHER_USER_ID, f"got {rows['Ginter']['sellerId']!r}"
    assert rows["Ginter"]["seller"] == "Meier", "the display name stays, the column shows it"


def test_a_kickbase_listing_has_no_seller_id():
    """Nobody is excluded from bidding on a free agent, so the id must not be invented."""
    rows = run_market()
    assert rows["Gouweleeuw"]["sellerId"] is None, f"got {rows['Gouweleeuw']['sellerId']!r}"
    assert rows["Gouweleeuw"]["seller"] == "Kickbase"


### ===============================================================================
### balances() -> isSelf
### ===============================================================================


def run_balances(own_user_id=OWN_USER_ID):
    """Run balances() against stubbed API calls and return the rows it wrote, by user id."""
    import main
    from backend.kickbase.v4 import leagues

    league_users = {OWN_USER_ID: "shirazzi", OTHER_USER_ID: "Meier"}

    ### One sale from Meier to the user, so both managers have a transfer of their own
    transfers = [{
        "i": "1", "t": 15, "coc": 0,
        "dt": "2026-08-05T10:00:00Z",
        "data": {"slr": "Meier", "byr": "shirazzi", "pi": "49", "pn": "Ginter",
                 "tid": "5", "t": 1, "trp": 4000000},
    }]

    with tempfile.TemporaryDirectory() as tmp:
        data_dir = path.join(tmp, "data")
        ts_dir = path.join(data_dir, "timestamps")
        makedirs(ts_dir, exist_ok=True)

        with open(path.join(data_dir, "STATIC_users.json"), "w") as f:
            json.dump(league_users, f)

        original = (main.PUBLIC_DIR, main.STATE_DIR,
                    miscellaneous.PUBLIC_DIR, miscellaneous.STATE_DIR, miscellaneous.TIMESTAMP_DIR,
                    miscellaneous.LAST_GOOD_DIR, miscellaneous.prefetch_profilepics,
                    miscellaneous.get_profilepic, leagues.transfers, leagues.user_stats,
                    leagues.user_performance, environ.get("START_DATE"),
                    environ.get("START_MONEY"))
        main.PUBLIC_DIR = data_dir
        main.STATE_DIR = data_dir
        miscellaneous.PUBLIC_DIR = data_dir
        miscellaneous.STATE_DIR = data_dir
        miscellaneous.TIMESTAMP_DIR = ts_dir
        miscellaneous.LAST_GOOD_DIR = path.join(tmp, "last-good")

        try:
            environ["START_DATE"] = START_DATE
            environ["START_MONEY"] = "50000000"

            miscellaneous.prefetch_profilepics = lambda user_ids: None
            miscellaneous.get_profilepic = lambda user_id: None
            leagues.transfers = lambda token, lid: transfers
            leagues.user_stats = lambda token, lid, user_id: {
                "tv": 100000000, "t": 1, "mdw": 0, "pl": 0}
            leagues.user_performance = lambda token, lid, user_id: {}

            class FakeLeague:
                id = LEAGUE_ID
                name = "Kickbase-Elite 26/27"

            main.balances("token", FakeLeague(), own_user_id)

            with open(path.join(data_dir, "balances.json")) as f:
                rows = json.load(f)
        finally:
            (main.PUBLIC_DIR, main.STATE_DIR,
             miscellaneous.PUBLIC_DIR, miscellaneous.STATE_DIR, miscellaneous.TIMESTAMP_DIR,
             miscellaneous.LAST_GOOD_DIR, miscellaneous.prefetch_profilepics,
             miscellaneous.get_profilepic, leagues.transfers, leagues.user_stats,
             leagues.user_performance, start_date, start_money) = original

            for name, value in (("START_DATE", start_date), ("START_MONEY", start_money)):
                if value is None:
                    environ.pop(name, None)
                else:
                    environ[name] = value

    return {row["userId"]: row for row in rows}


def test_exactly_one_manager_is_flagged_as_the_user():
    rows = run_balances()
    flagged = [user_id for user_id, row in rows.items() if row["isSelf"]]
    assert flagged == [OWN_USER_ID], f"expected only {OWN_USER_ID}, got {flagged}"


def test_every_other_manager_is_flagged_false_rather_than_left_out():
    """The frontend reads isSelf === true, so a missing key would be a silent maybe."""
    rows = run_balances()
    assert rows[OTHER_USER_ID]["isSelf"] is False, f"got {rows[OTHER_USER_ID]['isSelf']!r}"


def test_user_id_type_does_not_matter():
    """The id comes from the API as a string and from some call sites as an int."""
    rows = run_balances(own_user_id=int(OWN_USER_ID))
    assert rows[OWN_USER_ID]["isSelf"] is True, "an int own id must still match"


def test_nobody_is_flagged_when_the_user_is_not_in_the_league():
    """Better no self at all than the wrong one: the frontend then skips the own cap."""
    rows = run_balances(own_user_id="999999")
    assert not any(row["isSelf"] for row in rows.values()), "no manager may be flagged"


def test_the_max_bids_the_solver_reads_are_still_there():
    """The solver joins on these two fields, so they are part of the contract now."""
    rows = run_balances()
    row = rows[OWN_USER_ID]
    assert row["maxBid"] is not None and row["maxBidWithBonuses"] is not None, f"got {row}"
    assert row["maxBidWithBonuses"] >= row["maxBid"], \
        f"the bonus view cannot be the smaller one: {row['maxBidWithBonuses']} < {row['maxBid']}"


def test_the_balances_stage_is_told_who_the_user_is():
    """The wiring, not the output: build_stages() has to pass the id through."""
    import main

    seen = {}
    original = main.balances

    try:
        main.balances = lambda token, league, own_id: seen.setdefault("own", own_id)

        class FakeLeague:
            id = LEAGUE_ID
            name = "Kickbase-Elite 26/27"

        stages = dict(main.build_stages("token", FakeLeague(), OWN_USER_ID))
        stages["balances"]()
    finally:
        main.balances = original

    assert seen.get("own") == OWN_USER_ID, f"got {seen!r}"


### ===============================================================================
### calibrate_min_bid: the reconstruction
### ===============================================================================


def manager(events, team_value=100000000):
    """A balances.json row reduced to what the reconstruction reads."""
    return {
        "userId": "1",
        "username": "Anna",
        "teamValue": team_value,
        "events": events,
        "eventsWithBonuses": events,
    }


def event(date, event_type, amount, balance):
    return {"date": date, "type": event_type, "amount": amount, "balance": balance}


TIMELINE = [
    event(START_DATE, "start", 50000000, 50000000),
    event("2026-08-03T12:00:00Z", "buy", -10000000, 40000000),
    event("2026-08-05T12:00:00Z", "sell", 4000000, 44000000),
]


def at(timestamp):
    return miscellaneous.parse_feed_timestamp(timestamp)


def test_balance_at_reads_the_last_event_before_the_moment():
    timeline = calibrate_min_bid.balance_timeline(manager(TIMELINE))
    assert calibrate_min_bid.balance_at(timeline, at("2026-08-04T00:00:00Z")) == 40000000
    assert calibrate_min_bid.balance_at(timeline, at("2026-08-06T00:00:00Z")) == 44000000


def test_balance_at_excludes_an_event_on_the_moment_itself():
    """The transfer being judged is itself on this timeline.

    Including it would hold the price paid against a balance that has already paid it, so
    every purchase would look affordable by construction.
    """
    timeline = calibrate_min_bid.balance_timeline(manager(TIMELINE))
    assert calibrate_min_bid.balance_at(timeline, at("2026-08-03T12:00:00Z")) == 50000000, \
        "the balance just before the buy is the one that had to cover it"


def test_balance_at_has_no_answer_before_the_timeline_starts():
    timeline = calibrate_min_bid.balance_timeline(manager(TIMELINE))
    assert calibrate_min_bid.balance_at(timeline, at("2026-07-01T00:00:00Z")) is None


def test_team_value_is_walked_back_along_the_transfers():
    """A buy after the moment was not in the squad yet; a sale after it still was."""
    ### Before the 10.000.000 buy and the 4.000.000 sale
    early = calibrate_min_bid.team_value_at(manager(TIMELINE), at("2026-08-02T00:00:00Z"))
    assert early == 100000000 - 10000000 + 4000000, f"got {early}"

    ### Between them: the buy already happened, the sale has not
    middle = calibrate_min_bid.team_value_at(manager(TIMELINE), at("2026-08-04T00:00:00Z"))
    assert middle == 100000000 + 4000000, f"got {middle}"

    ### After both, today's value stands
    late = calibrate_min_bid.team_value_at(manager(TIMELINE), at("2026-08-06T00:00:00Z"))
    assert late == 100000000, f"got {late}"


def test_the_flat_team_value_skips_the_walk_back():
    """The sensitivity run: how much does the correction move the numbers?"""
    flat = calibrate_min_bid.team_value_at(manager(TIMELINE), at("2026-08-02T00:00:00Z"), flat=True)
    assert flat == 100000000, f"got {flat}"


def test_the_start_event_is_not_mistaken_for_a_transfer():
    """It carries the starting budget as its amount, which is no part of any team value."""
    value = calibrate_min_bid.team_value_at(
        manager([event(START_DATE, "start", 50000000, 50000000)]), at("2026-07-01T00:00:00Z"))
    assert value == 100000000, f"the start event must not move the team value, got {value}"


def test_ceilings_leave_out_a_manager_whose_timeline_has_not_started():
    ceilings = calibrate_min_bid.ceilings_at([manager(TIMELINE)], at("2026-07-01T00:00:00Z"))
    assert ceilings == {}, f"a guessed ceiling is worse than none, got {ceilings}"


def test_ceilings_use_the_same_formula_the_frontend_shows():
    import main

    when = at("2026-08-04T00:00:00Z")
    row = manager(TIMELINE)
    ceilings = calibrate_min_bid.ceilings_at([row], when)

    expected = main.max_bid(calibrate_min_bid.team_value_at(row, when), 40000000)
    assert ceilings["1"] == expected, f"expected {expected}, got {ceilings['1']}"


### ===============================================================================
### calibrate_min_bid: the solver, mirroring minWinningBid()
### ===============================================================================


CEILINGS = {"1": 30000000, "2": 20000000, "3": 10000000, "4": 40000000, "5": 25000000}


def test_the_bid_beats_the_richest_affordable_rival_by_one():
    bid, rivals = calibrate_min_bid.suggested_bid(15000000, CEILINGS, seller_id="4", buyer_id="5")
    assert (bid, rivals) == (30000001, 2), f"got {(bid, rivals)}"


def test_the_seller_and_the_buyer_are_not_rivals():
    """The seller holds the biggest ceiling and the buyer the second, so a leak would show.

    With both out, the richest remaining rival is manager 5 at 25.000.000.
    """
    bid, rivals = calibrate_min_bid.suggested_bid(15000000, CEILINGS, seller_id="4", buyer_id="1")
    assert (bid, rivals) == (25000001, 2), f"got {(bid, rivals)}"


def test_the_asking_price_is_the_floor():
    """Kickbase rejects a bid below the price, so a lower minimum would be no bid at all."""
    bid, _ = calibrate_min_bid.suggested_bid(35000000, {"1": 10000000}, seller_id=None, buyer_id="5")
    assert bid == 35000000, f"got {bid}"


def test_a_phantom_auction_costs_the_asking_price():
    bid, rivals = calibrate_min_bid.suggested_bid(35000000, CEILINGS, seller_id="4", buyer_id="5")
    assert (bid, rivals) == (35000000, 0), f"got {(bid, rivals)}"


def test_a_ceiling_exactly_at_the_price_still_counts_as_a_rival():
    bid, rivals = calibrate_min_bid.suggested_bid(30000000, CEILINGS, seller_id="4", buyer_id="5")
    assert (bid, rivals) == (30000001, 1), f"got {(bid, rivals)}"


### ===============================================================================
### calibrate_min_bid: the record it is held against
### ===============================================================================


NAME_TO_ID = miscellaneous.build_user_name_index({OWN_USER_ID: "shirazzi", OTHER_USER_ID: "Meier"})


def transfer(date, price, buyer=None, seller=None):
    data = {"pi": "49", "pn": "Ginter", "tid": "5", "t": 1, "trp": price}

    if buyer:
        data["byr"] = buyer
    if seller:
        data["slr"] = seller

    return {"i": "1", "t": 15, "coc": 0, "dt": date, "data": data}


def test_purchases_keep_both_sides_when_both_are_named():
    found = calibrate_min_bid.purchases(
        [transfer("2026-08-05T10:00:00Z", 4000000, buyer="shirazzi", seller="Meier")],
        NAME_TO_ID, at(START_DATE))

    assert len(found) == 1, f"got {found}"
    when, price, buyer_id, seller_id, player = found[0]
    assert (price, buyer_id, seller_id) == (4000000, OWN_USER_ID, OTHER_USER_ID), f"got {found[0]}"


def test_a_purchase_from_kickbase_has_no_seller():
    found = calibrate_min_bid.purchases(
        [transfer("2026-08-05T10:00:00Z", 4000000, buyer="shirazzi")], NAME_TO_ID, at(START_DATE))

    assert found[0][3] is None, f"Kickbase is not a manager, got {found[0][3]!r}"


def test_a_sale_to_kickbase_is_not_a_purchase():
    """Without a buyer there is no ceiling to hold the price against."""
    found = calibrate_min_bid.purchases(
        [transfer("2026-08-05T10:00:00Z", 4000000, seller="Meier")], NAME_TO_ID, at(START_DATE))

    assert found == [], f"got {found}"


def test_a_buyer_who_is_not_in_the_league_is_skipped():
    """A manager who left is named in the feed but has no balance to reconstruct."""
    found = calibrate_min_bid.purchases(
        [transfer("2026-08-05T10:00:00Z", 4000000, buyer="Weg")], NAME_TO_ID, at(START_DATE))

    assert found == [], f"got {found}"


def test_transfers_from_before_the_season_start_are_dropped():
    found = calibrate_min_bid.purchases(
        [transfer("2026-07-20T10:00:00Z", 4000000, buyer="shirazzi")], NAME_TO_ID, at(START_DATE))

    assert found == [], f"a previous season must not enter the calibration, got {found}"


def test_percentile_walks_the_sorted_sample():
    values = [1, 2, 3, 4, 5]
    assert calibrate_min_bid.percentile(values, 0.0) == 1
    assert calibrate_min_bid.percentile(values, 0.5) == 3
    assert calibrate_min_bid.percentile(values, 1.0) == 5


def test_percentile_of_an_empty_sample_is_none():
    assert calibrate_min_bid.percentile([], 0.5) is None


def test_collect_solves_every_purchase_it_can_place_on_the_timeline():
    balances = [
        {**manager(TIMELINE), "userId": OWN_USER_ID, "username": "shirazzi"},
        {**manager(TIMELINE), "userId": OTHER_USER_ID, "username": "Meier"},
    ]

    cases = calibrate_min_bid.collect(
        balances,
        calibrate_min_bid.purchases(
            [transfer("2026-08-04T10:00:00Z", 4000000, buyer="shirazzi", seller="Meier"),
             ### Before the timeline starts, so no ceiling exists for it
             transfer("2026-08-01T18:00:00Z", 4000000, buyer="shirazzi")],
            NAME_TO_ID, at(START_DATE)),
        with_bonuses=True, flat_team_value=False)

    assert len(cases) == 1, f"only the purchase inside the timeline can be judged, got {cases}"
    assert cases[0]["fromManager"] is True, f"got {cases[0]}"
    assert cases[0]["rivals"] == 0, "the only other manager is the seller"
    assert cases[0]["bid"] == 4000000, f"a phantom auction costs the price, got {cases[0]['bid']}"


### ===============================================================================

if __name__ == "__main__":
    print("market() -> sellerId")
    check("the seller id lands next to the seller name", test_seller_id_lands_next_to_the_seller_name)
    check("a Kickbase listing has no seller id", test_a_kickbase_listing_has_no_seller_id)

    print("\nbalances() -> isSelf")
    check("exactly one manager is flagged as the user", test_exactly_one_manager_is_flagged_as_the_user)
    check("every other manager is flagged false", test_every_other_manager_is_flagged_false_rather_than_left_out)
    check("the own user id type does not matter", test_user_id_type_does_not_matter)
    check("nobody is flagged when the user is not in the league", test_nobody_is_flagged_when_the_user_is_not_in_the_league)
    check("the max bids the solver reads are still there", test_the_max_bids_the_solver_reads_are_still_there)
    check("the balances stage is told who the user is", test_the_balances_stage_is_told_who_the_user_is)

    print("\ncalibrate_min_bid: reconstruction")
    check("balance_at reads the last event before the moment", test_balance_at_reads_the_last_event_before_the_moment)
    check("balance_at excludes an event on the moment itself", test_balance_at_excludes_an_event_on_the_moment_itself)
    check("balance_at has no answer before the timeline starts", test_balance_at_has_no_answer_before_the_timeline_starts)
    check("the team value is walked back along the transfers", test_team_value_is_walked_back_along_the_transfers)
    check("the flat team value skips the walk back", test_the_flat_team_value_skips_the_walk_back)
    check("the start event is not mistaken for a transfer", test_the_start_event_is_not_mistaken_for_a_transfer)
    check("ceilings leave out a manager without a timeline", test_ceilings_leave_out_a_manager_whose_timeline_has_not_started)
    check("ceilings use the formula the frontend shows", test_ceilings_use_the_same_formula_the_frontend_shows)

    print("\ncalibrate_min_bid: the solver")
    check("the bid beats the richest affordable rival by one", test_the_bid_beats_the_richest_affordable_rival_by_one)
    check("the seller and the buyer are not rivals", test_the_seller_and_the_buyer_are_not_rivals)
    check("the asking price is the floor", test_the_asking_price_is_the_floor)
    check("a phantom auction costs the asking price", test_a_phantom_auction_costs_the_asking_price)
    check("a ceiling exactly at the price counts as a rival", test_a_ceiling_exactly_at_the_price_still_counts_as_a_rival)

    print("\ncalibrate_min_bid: the record")
    check("purchases keep both sides when both are named", test_purchases_keep_both_sides_when_both_are_named)
    check("a purchase from Kickbase has no seller", test_a_purchase_from_kickbase_has_no_seller)
    check("a sale to Kickbase is not a purchase", test_a_sale_to_kickbase_is_not_a_purchase)
    check("a buyer who is not in the league is skipped", test_a_buyer_who_is_not_in_the_league_is_skipped)
    check("transfers from before the season start are dropped", test_transfers_from_before_the_season_start_are_dropped)
    check("percentile walks the sorted sample", test_percentile_walks_the_sorted_sample)
    check("percentile of an empty sample is None", test_percentile_of_an_empty_sample_is_none)
    check("collect solves every purchase on the timeline", test_collect_solves_every_purchase_it_can_place_on_the_timeline)

    total, passed = len(PASSED), sum(PASSED)
    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)
