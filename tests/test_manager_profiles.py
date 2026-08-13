"""Tests for the manager profiles in backend/profiles.py.

Everything runs against a synthetic transfer history, so the four metrics and their n
fields are checked against numbers worked out by hand rather than against whatever the
league happens to hold today.

Dependency free on purpose: the project has no test framework, so this runs with the
project venv directly and needs no extra packages.

    ./venv/bin/python tests/test_manager_profiles.py
"""

import json
import sys
import tempfile

from datetime import date, timedelta
from os import environ, makedirs, path

### Make the repository root importable regardless of where this is run from
sys.path.insert(0, path.dirname(path.dirname(path.abspath(__file__))))

from backend import miscellaneous, profiles
from backend.kickbase.v4 import leagues

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


### ===============================================================================
### The synthetic league
### ===============================================================================

### Two managers plus a pair sharing a display name, which the feed cannot tell apart
USERS = {"1": "Alpha", "2": "Beta", "3": "Twin", "4": "Twin"}
NAME_TO_ID = miscellaneous.build_user_name_index(USERS)

TEAM_NAMES = {"2": "Bayern", "3": "Dortmund", "4": "Frankfurt"}

### The activity window is reported in local time, so the zone has to be fixed for the
### assertions to hold wherever this runs
environ["TZ"] = "Europe/Berlin"


def julian(day):
    """The market value history's date format for a calendar day."""
    return (day - date(1970, 1, 1)).days


def buy(name, player_id, price, dt, team_id="2", seller=None):
    """A feed item in which `name` bought a player.

    With a seller it is a booking between two managers: a purchase for the buyer and a
    sale for the seller at the same time, exactly as the real feed reports it.
    """
    data = {"byr": name, "pi": player_id, "pn": f"Player{player_id}",
            "tid": team_id, "t": 1, "trp": price}

    if seller is not None:
        data["slr"] = seller

    return {"i": f"buy-{name}-{player_id}-{dt}", "t": 15, "coc": 0, "data": data, "dt": dt}


def sell(name, player_id, price, dt, team_id="2"):
    """A feed item in which `name` sold a player back to Kickbase."""
    data = {"slr": name, "pi": player_id, "pn": f"Player{player_id}",
            "tid": team_id, "t": 2, "trp": price}

    return {"i": f"sell-{name}-{player_id}-{dt}", "t": 15, "coc": 0, "data": data, "dt": dt}


def turnover(user_id, player_id, bought, sold, buy_type="buy", seller_id=None,
             sold_to="Kickbase"):
    """A buy/sell pair in the shape turnovers.json holds, as a two element list."""
    return [
        {"date": bought, "type": buy_type, "user": USERS[user_id], "userId": user_id,
         "tradePartner": "Kickbase", "tradePartnerId": None, "price": 1_000_000,
         "playerId": player_id, "teamId": "2", "firstName": "F", "lastName": "L"},
        {"date": sold, "type": "sell", "user": USERS[seller_id or user_id],
         "userId": seller_id or user_id, "tradePartner": sold_to, "tradePartnerId": None,
         "price": 2_000_000, "playerId": player_id, "teamId": "2",
         "firstName": "F", "lastName": "L"},
    ]


def curve(player_id, points):
    """A market value history for one player, from {calendar day: market value}."""
    return {player_id: [{"dt": julian(day), "mv": mv} for day, mv in sorted(points.items())]}


def build(transfers=None, turnovers=None, market_values=None):
    """Build the whole document: the coverage header plus every manager."""
    return profiles.build_profiles(transfers or [], turnovers or [], NAME_TO_ID,
                                   market_values, TEAM_NAMES)


def managers(transfers=None, turnovers=None, market_values=None):
    """Build the document and return only the managers."""
    return build(transfers, turnovers, market_values)["managers"]


def profile_for(user_id, transfers=None, turnovers=None, market_values=None):
    """Build the profiles and return the one of a single manager."""
    return managers(transfers, turnovers, market_values)[user_id]


### ===============================================================================
### market_value_index()
### ===============================================================================


def test_index_keys_the_curve_by_calendar_day():
    day = date(2026, 8, 10)
    index = profiles.market_value_index(curve("100", {day: 1_000_000}))
    assert index == {"100": {day: 1_000_000}}, f"got {index}"


def test_index_converts_the_epoch_day():
    index = profiles.market_value_index({"100": [{"dt": 0, "mv": 5}]})
    assert index["100"] == {date(1970, 1, 1): 5}, f"got {index['100']}"


def test_index_skips_entries_without_a_value():
    index = profiles.market_value_index({"100": [{"dt": 1}, {"mv": 5}, {"dt": 2, "mv": 7}]})
    assert index["100"] == {date(1970, 1, 3): 7}, f"got {index['100']}"


def test_index_leaves_out_players_without_a_curve():
    index = profiles.market_value_index({"100": [], "101": None})
    assert index == {}, f"got {index}"


### ===============================================================================
### 1. Median hold duration
### ===============================================================================


def test_median_hold_duration_over_three_sales():
    ### Held for 2, 4 and 10 days
    turnovers = [
        turnover("1", "100", "2026-08-02T12:00:00Z", "2026-08-04T12:00:00Z"),
        turnover("1", "101", "2026-08-02T12:00:00Z", "2026-08-06T12:00:00Z"),
        turnover("1", "102", "2026-08-02T12:00:00Z", "2026-08-12T12:00:00Z"),
    ]
    metric = profile_for("1", turnovers=turnovers)["holdDuration"]
    assert metric == {"medianDays": 4.0, "medianSeconds": 345600,
                      "n": 3, "roundTripsWithinAnHour": 0}, f"got {metric}"


def test_median_hold_duration_averages_the_middle_two():
    ### Held for 1 and 4 days, so the median sits between them
    turnovers = [
        turnover("2", "100", "2026-08-02T12:00:00Z", "2026-08-03T12:00:00Z"),
        turnover("2", "101", "2026-08-02T12:00:00Z", "2026-08-06T12:00:00Z"),
    ]
    metric = profile_for("2", turnovers=turnovers)["holdDuration"]
    assert metric == {"medianDays": 2.5, "medianSeconds": 216000,
                      "n": 2, "roundTripsWithinAnHour": 0}, f"got {metric}"


def test_hold_duration_counts_part_days():
    turnovers = [turnover("1", "100", "2026-08-02T00:00:00Z", "2026-08-03T12:00:00Z")]
    metric = profile_for("1", turnovers=turnovers)["holdDuration"]
    assert metric == {"medianDays": 1.5, "medianSeconds": 129600,
                      "n": 1, "roundTripsWithinAnHour": 0}, f"got {metric}"


def test_hold_duration_counts_a_round_trip_through_the_market_without_dropping_it():
    ### Bought off the market and sold straight back to it: a trade count bonus, not a
    ### hold. It stays in the median and is counted, so the number can be read honestly.
    turnovers = [
        turnover("1", "100", "2026-08-09T09:15:37Z", "2026-08-09T09:15:53Z"),
        turnover("1", "101", "2026-08-02T12:00:00Z", "2026-08-06T12:00:00Z"),
    ]
    ### The median of sixteen seconds and four days, which is two days to the second
    metric = profile_for("1", turnovers=turnovers)["holdDuration"]
    assert metric == {"medianDays": 2.0, "medianSeconds": 172808,
                      "n": 2, "roundTripsWithinAnHour": 1}, f"got {metric}"


def test_a_median_of_seconds_stays_readable():
    ### Two round trips, 14 and 30 seconds. In days that is 0.0 at any sane number of
    ### decimals, which reads like missing data next to an n of 2 - so the exact value is
    ### reported in seconds as well.
    turnovers = [
        turnover("1", "100", "2026-08-09T09:15:37Z", "2026-08-09T09:15:51Z"),
        turnover("1", "101", "2026-08-09T10:00:00Z", "2026-08-09T10:00:30Z"),
    ]
    metric = profile_for("1", turnovers=turnovers)["holdDuration"]
    assert metric["medianSeconds"] == 22, f"got {metric}"
    assert metric["n"] == 2 and metric["roundTripsWithinAnHour"] == 2, f"got {metric}"


def test_a_median_of_hours_is_no_longer_zero_in_days():
    ### The case from the real league: a median of twelve minutes used to round to 0.0 days
    turnovers = [turnover("1", "100", "2026-08-09T09:00:00Z", "2026-08-09T09:12:00Z")]
    metric = profile_for("1", turnovers=turnovers)["holdDuration"]
    assert metric["medianDays"] == 0.008, f"got {metric}"
    assert metric["medianSeconds"] == 720, f"got {metric}"


def test_a_quick_sale_to_another_manager_is_no_round_trip():
    ### Only the market pays out instantly, so a fast sale to a manager is a real trade
    turnovers = [turnover("1", "100", "2026-08-09T09:15:37Z", "2026-08-09T09:45:52Z",
                          sold_to="Beta")]
    metric = profile_for("1", turnovers=turnovers)["holdDuration"]
    assert metric["roundTripsWithinAnHour"] == 0, f"got {metric}"


def test_a_sale_back_to_the_market_after_a_day_is_no_round_trip():
    turnovers = [turnover("1", "100", "2026-08-09T09:00:00Z", "2026-08-10T09:00:00Z")]
    metric = profile_for("1", turnovers=turnovers)["holdDuration"]
    assert metric["roundTripsWithinAnHour"] == 0, f"got {metric}"


def test_hold_duration_ignores_players_assigned_at_the_season_start():
    ### turnovers() dates these at START_DATE, so their duration says when the season
    ### began, not how long this manager sits on a player
    turnovers = [
        turnover("1", "100", "2026-08-02T12:00:00Z", "2026-08-04T12:00:00Z"),
        turnover("1", "101", "2026-08-01T18:00:00Z", "2026-08-31T18:00:00Z",
                 buy_type="assigned_at_start"),
    ]
    metric = profile_for("1", turnovers=turnovers)["holdDuration"]
    assert metric == {"medianDays": 2.0, "medianSeconds": 172800,
                      "n": 1, "roundTripsWithinAnHour": 0}, f"got {metric}"


def test_hold_duration_ignores_a_pair_the_buyer_did_not_sell():
    ### Alpha bought, Beta sold on: the pair belongs to neither fingerprint
    turnovers = [turnover("1", "100", "2026-08-02T12:00:00Z", "2026-08-09T12:00:00Z",
                          seller_id="2")]
    built = managers(turnovers=turnovers)
    assert built["1"]["holdDuration"]["n"] == 0, f"got {built['1']['holdDuration']}"
    assert built["2"]["holdDuration"]["n"] == 0, f"got {built['2']['holdDuration']}"


def test_hold_duration_without_a_single_sale():
    metric = profile_for("1")["holdDuration"]
    assert metric == {"medianDays": None, "medianSeconds": None,
                      "n": 0, "roundTripsWithinAnHour": 0}, f"got {metric}"


### ===============================================================================
### 2. Mean markup at purchase
### ===============================================================================

BUY_DAY = date(2026, 8, 10)
BUY_AT = "2026-08-10T12:00:00Z"


def markup_market_values():
    """Curves for three players, all worth a million on the day they were bought."""
    histories = {}
    histories.update(curve("100", {BUY_DAY: 1_000_000}))
    histories.update(curve("101", {BUY_DAY: 1_000_000}))
    histories.update(curve("102", {BUY_DAY: 1_000_000}))
    return profiles.market_value_index(histories)


def test_mean_and_median_markup_over_the_day_value():
    ### 10%, 20% and 60% over the market value of the day
    transfers = [
        buy("Alpha", "100", 1_100_000, BUY_AT),
        buy("Alpha", "101", 1_200_000, BUY_AT),
        buy("Alpha", "102", 1_600_000, BUY_AT),
    ]
    metric = profile_for("1", transfers, market_values=markup_market_values())["purchaseMarkup"]
    assert metric == {"meanPercent": 30.0, "medianPercent": 20.0, "n": 3, "buysConsidered": 3}, \
        f"got {metric}"


def test_markup_is_negative_below_the_day_value():
    transfers = [buy("Alpha", "100", 800_000, BUY_AT)]
    metric = profile_for("1", transfers, market_values=markup_market_values())["purchaseMarkup"]
    assert metric["meanPercent"] == -20.0, f"got {metric}"


def test_markup_n_counts_only_the_buys_the_curve_covers():
    ### The third player has no curve at all - the fetched window does not reach that far
    ### back, or he has left the competition. He raises buysConsidered, not n.
    market_values = markup_market_values()
    del market_values["102"]

    transfers = [
        buy("Alpha", "100", 1_100_000, BUY_AT),
        buy("Alpha", "101", 1_300_000, BUY_AT),
        buy("Alpha", "102", 5_000_000, BUY_AT),
    ]
    metric = profile_for("1", transfers, market_values=market_values)["purchaseMarkup"]
    assert metric == {"meanPercent": 20.0, "medianPercent": 20.0, "n": 2, "buysConsidered": 3}, \
        f"got {metric}"


def test_markup_needs_the_value_of_the_day_itself():
    ### The curve covers the days around the purchase but not the purchase
    market_values = profiles.market_value_index(
        curve("100", {BUY_DAY - timedelta(days=1): 1_000_000,
                      BUY_DAY + timedelta(days=1): 1_000_000}))
    transfers = [buy("Alpha", "100", 1_100_000, BUY_AT)]
    metric = profile_for("1", transfers, market_values=market_values)["purchaseMarkup"]
    assert metric["n"] == 0 and metric["meanPercent"] is None, f"got {metric}"


def test_markup_without_any_market_values():
    transfers = [buy("Alpha", "100", 1_100_000, BUY_AT),
                 buy("Alpha", "101", 1_200_000, BUY_AT)]
    metric = profile_for("1", transfers)["purchaseMarkup"]
    assert metric == {"meanPercent": None, "medianPercent": None, "n": 0, "buysConsidered": 2}, \
        f"got {metric}"


### ===============================================================================
### 3. Share of momentum buys
### ===============================================================================


def momentum_market_values():
    """Player 100 rising over the week, 101 falling, 102 without the earlier day."""
    week_before = BUY_DAY - timedelta(days=profiles.MOMENTUM_WINDOW_DAYS)

    histories = {}
    histories.update(curve("100", {week_before: 1_000_000, BUY_DAY: 1_400_000}))
    histories.update(curve("101", {week_before: 1_000_000, BUY_DAY: 800_000}))
    histories.update(curve("102", {BUY_DAY: 1_000_000}))
    return profiles.market_value_index(histories)


def test_momentum_share_counts_only_the_covered_buys():
    transfers = [
        buy("Alpha", "100", 1_400_000, BUY_AT),
        buy("Alpha", "101", 800_000, BUY_AT),
        buy("Alpha", "102", 1_000_000, BUY_AT),
    ]
    metric = profile_for("1", transfers, market_values=momentum_market_values())["momentumBuys"]
    assert metric == {"share": 0.5, "risingBuys": 1, "n": 2, "windowDays": 7}, f"got {metric}"


def test_momentum_needs_a_strictly_rising_trend():
    ### A flat week is not momentum
    week_before = BUY_DAY - timedelta(days=profiles.MOMENTUM_WINDOW_DAYS)
    market_values = profiles.market_value_index(
        curve("100", {week_before: 1_000_000, BUY_DAY: 1_000_000}))

    metric = profile_for("1", [buy("Alpha", "100", 1_000_000, BUY_AT)],
                         market_values=market_values)["momentumBuys"]
    assert metric == {"share": 0.0, "risingBuys": 0, "n": 1, "windowDays": 7}, f"got {metric}"


def test_momentum_without_any_market_values():
    metric = profile_for("1", [buy("Alpha", "100", 1_000_000, BUY_AT)])["momentumBuys"]
    assert metric == {"share": None, "risingBuys": 0, "n": 0, "windowDays": 7}, f"got {metric}"


### ===============================================================================
### 4. Top three clubs
### ===============================================================================


def test_top_clubs_are_the_three_most_bought_from():
    transfers = (
        [buy("Alpha", f"1{i}", 1_000_000, BUY_AT, team_id="2") for i in range(4)]
        + [buy("Alpha", f"2{i}", 1_000_000, BUY_AT, team_id="3") for i in range(2)]
        + [buy("Alpha", "30", 1_000_000, BUY_AT, team_id="4")]
        + [buy("Alpha", "40", 1_000_000, BUY_AT, team_id="9")]
    )
    metric = profile_for("1", transfers)["topClubs"]

    assert metric["n"] == 8, f"expected all eight buys counted, got {metric}"
    assert [club["teamId"] for club in metric["clubs"]] == ["2", "3", "4"], f"got {metric}"
    assert metric["clubs"][0] == {"teamId": "2", "teamName": "Bayern", "buys": 4}, f"got {metric}"


def test_top_clubs_break_a_tie_by_team_id():
    ### One buy each from teams 4 and 3: the order has to be the same on every run
    transfers = [buy("Alpha", "10", 1_000_000, BUY_AT, team_id="4"),
                 buy("Alpha", "11", 1_000_000, BUY_AT, team_id="3")]
    metric = profile_for("1", transfers)["topClubs"]
    assert [club["teamId"] for club in metric["clubs"]] == ["3", "4"], f"got {metric}"


def test_top_clubs_name_an_unknown_team_as_none():
    metric = profile_for("1", [buy("Alpha", "10", 1_000_000, BUY_AT, team_id="9")])["topClubs"]
    assert metric["clubs"] == [{"teamId": "9", "teamName": None, "buys": 1}], f"got {metric}"


def test_top_clubs_without_a_single_buy():
    metric = profile_for("1")["topClubs"]
    assert metric == {"clubs": [], "n": 0}, f"got {metric}"


def test_top_clubs_ignore_sales():
    ### The metric is about what the manager buys, not what leaves their squad
    metric = profile_for("1", [sell("Alpha", "10", 1_000_000, BUY_AT, team_id="3")])["topClubs"]
    assert metric == {"clubs": [], "n": 0}, f"got {metric}"


### ===============================================================================
### The activity window
### ===============================================================================


def test_activity_window_uses_local_hours():
    ### 20:30 UTC is 22:30 in Berlin in August
    metric = profile_for("1", [buy("Alpha", "10", 1_000_000, "2026-08-10T20:30:00Z")])["activityWindow"]
    assert metric["hourCounts"][22] == 1, f"expected the buy at 22 local, got {metric}"
    assert metric["peakHour"] == 22, f"got {metric['peakHour']}"
    assert metric["n"] == 1, f"got {metric['n']}"
    assert metric["timezone"] == "Europe/Berlin", f"got {metric['timezone']}"


def test_activity_window_counts_sales_too():
    transfers = [
        buy("Alpha", "10", 1_000_000, "2026-08-10T20:30:00Z"),
        buy("Alpha", "11", 1_000_000, "2026-08-11T20:40:00Z"),
        sell("Alpha", "12", 1_000_000, "2026-08-11T05:10:00Z"),
    ]
    metric = profile_for("1", transfers)["activityWindow"]
    assert metric["n"] == 3, f"got {metric['n']}"
    assert metric["hourCounts"][22] == 2, f"got {metric['hourCounts']}"
    assert metric["hourCounts"][7] == 1, f"expected the sale at 07 local, got {metric['hourCounts']}"
    assert metric["peakHour"] == 22, f"got {metric['peakHour']}"


def test_activity_window_without_a_single_booking():
    metric = profile_for("1")["activityWindow"]
    assert metric["peakHour"] is None, f"got {metric}"
    assert metric["n"] == 0 and metric["hourCounts"] == [0] * 24, f"got {metric}"


### ===============================================================================
### Attribution
### ===============================================================================


def test_every_league_manager_gets_a_profile():
    built = managers()
    ### The two managers sharing a display name are left out: their bookings cannot be
    ### told apart, so a fingerprint for either would be someone else's behaviour
    assert sorted(built) == ["1", "2"], f"got {sorted(built)}"
    assert built["2"]["managerName"] == "Beta", f"got {built['2']}"


def test_an_unknown_manager_gets_no_profile():
    built = managers([buy("Ghost", "10", 1_000_000, BUY_AT)])
    assert sorted(built) == ["1", "2"], f"got {sorted(built)}"
    assert built["1"]["topClubs"]["n"] == 0, f"got {built['1']['topClubs']}"


def test_a_booking_between_managers_counts_for_both_sides():
    ### Beta sells to Alpha: a purchase for Alpha, trading activity for both. turnovers()
    ### records such an item as a sale only, which is why the buy side is read from the feed.
    transfers = [buy("Alpha", "10", 1_000_000, "2026-08-10T20:30:00Z", seller="Beta")]
    built = managers(transfers)

    assert built["1"]["purchaseMarkup"]["buysConsidered"] == 1, f"got {built['1']}"
    assert built["2"]["purchaseMarkup"]["buysConsidered"] == 0, f"got {built['2']}"
    assert built["1"]["activityWindow"]["n"] == 1, f"got {built['1']['activityWindow']}"
    assert built["2"]["activityWindow"]["n"] == 1, f"got {built['2']['activityWindow']}"


### ===============================================================================
### The market value coverage header
### ===============================================================================


def test_coverage_counts_the_bought_players_that_have_a_curve():
    market_values = markup_market_values()
    del market_values["102"]

    transfers = [buy("Alpha", "100", 1_100_000, BUY_AT),
                 buy("Beta", "101", 1_100_000, BUY_AT),
                 buy("Alpha", "102", 1_100_000, BUY_AT)]
    coverage = build(transfers, market_values=market_values)["marketValueCoverage"]
    assert coverage == {"players": 2, "of": 3}, f"got {coverage}"


def test_coverage_says_zero_of_n_when_the_cache_is_empty():
    ### This is the number that tells a consumer "the stage that fetches the curves died
    ### this run" apart from "this league never buys anything"
    transfers = [buy("Alpha", "100", 1_100_000, BUY_AT),
                 buy("Alpha", "101", 1_100_000, BUY_AT)]
    coverage = build(transfers)["marketValueCoverage"]
    assert coverage == {"players": 0, "of": 2}, f"got {coverage}"


def test_coverage_counts_a_player_two_managers_bought_once():
    transfers = [buy("Alpha", "100", 1_100_000, BUY_AT),
                 buy("Beta", "100", 1_200_000, BUY_AT)]
    coverage = build(transfers, market_values=markup_market_values())["marketValueCoverage"]
    assert coverage == {"players": 1, "of": 1}, f"got {coverage}"


def test_coverage_leaves_out_players_that_were_only_sold():
    ### A sale needs no market value, so it does not belong in the denominator
    transfers = [sell("Alpha", "100", 1_100_000, BUY_AT)]
    coverage = build(transfers)["marketValueCoverage"]
    assert coverage == {"players": 0, "of": 0}, f"got {coverage}"


def test_coverage_without_any_transfers():
    assert build()["marketValueCoverage"] == {"players": 0, "of": 0}, f"got {build()}"


### ===============================================================================
### write_manager_profiles() as a stage
### ===============================================================================


def stage_files(without=None):
    """The four files the stage reads, with the option to leave one out."""
    files = {
        "STATIC_users.json": USERS,
        "STATIC_teams.json": [{"teamId": "2", "teamName": "Bayern", "players": []}],
        "all_transfers.json": [
            buy("Alpha", "100", 1_100_000, BUY_AT),
            sell("Alpha", "100", 1_500_000, "2026-08-12T12:00:00Z"),
        ],
        "turnovers.json": [turnover("1", "100", BUY_AT, "2026-08-12T12:00:00Z")],
    }

    if without is not None:
        del files[without]

    return files


def run_stage(files, cached=None):
    """Run the stage against a temporary data directory holding `files`.

    `cached` stands in for the market value curves the run left in leagues.py - the public
    accessor is replaced, so nothing can reach the API even by accident.
    """
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = path.join(tmp, "data")
        ts_dir = path.join(data_dir, "timestamps")
        makedirs(ts_dir, exist_ok=True)

        for file_name, content in files.items():
            with open(path.join(data_dir, file_name), "w") as f:
                json.dump(content, f)

        original = (profiles.PUBLIC_DIR, profiles.STATE_DIR,
                    miscellaneous.PUBLIC_DIR, miscellaneous.STATE_DIR, miscellaneous.TIMESTAMP_DIR,
                    leagues.cached_market_value)
        profiles.PUBLIC_DIR = data_dir
        profiles.STATE_DIR = data_dir
        miscellaneous.PUBLIC_DIR = data_dir
        miscellaneous.STATE_DIR = data_dir
        miscellaneous.TIMESTAMP_DIR = ts_dir
        leagues.cached_market_value = lambda player_id: (cached or {}).get(str(player_id))

        try:
            profiles.write_manager_profiles()

            with open(path.join(data_dir, "manager_profiles.json")) as f:
                document = json.load(f)
            with open(path.join(ts_dir, "ts_manager_profiles.json")) as f:
                stamp = json.load(f)

            return document, stamp
        finally:
            (profiles.PUBLIC_DIR, profiles.STATE_DIR,
             miscellaneous.PUBLIC_DIR, miscellaneous.STATE_DIR, miscellaneous.TIMESTAMP_DIR,
             leagues.cached_market_value) = original


def stage_error(files):
    """Run the stage expecting it to fail, and return the message."""
    try:
        run_stage(files)
    except Exception as e:
        return str(e)

    raise AssertionError("expected the stage to fail")


def test_the_stage_writes_the_file_from_the_run_cache():
    """The stage reads the run's files, uses the cached curves, and writes the profiles."""
    histories = curve("100", {BUY_DAY - timedelta(days=profiles.MOMENTUM_WINDOW_DAYS): 1_000_000,
                              BUY_DAY: 1_000_000})
    document, stamp = run_stage(stage_files(), cached=histories)

    assert sorted(document) == ["managers", "marketValueCoverage"], f"got {sorted(document)}"
    assert document["marketValueCoverage"] == {"players": 1, "of": 1}, \
        f"expected the cached curve counted, got {document['marketValueCoverage']}"
    assert sorted(document["managers"]) == ["1", "2"], f"got {sorted(document['managers'])}"

    alpha = document["managers"]["1"]
    assert alpha["holdDuration"] == {"medianDays": 2.0, "medianSeconds": 172800, "n": 1,
                                     "roundTripsWithinAnHour": 0}, f"got {alpha['holdDuration']}"
    assert alpha["purchaseMarkup"]["n"] == 1, f"expected the cached curve used, got {alpha}"
    assert alpha["purchaseMarkup"]["meanPercent"] == 10.0, f"got {alpha['purchaseMarkup']}"
    assert alpha["momentumBuys"]["n"] == 1, f"got {alpha['momentumBuys']}"
    assert alpha["topClubs"]["clubs"] == [{"teamId": "2", "teamName": "Bayern", "buys": 1}], \
        f"got {alpha['topClubs']}"
    assert alpha["activityWindow"]["n"] == 2, f"got {alpha['activityWindow']}"
    assert stamp["rows"] == 2, f"expected the timestamp to count the profiles, got {stamp}"


def test_the_stage_reports_an_empty_cache_as_zero_coverage():
    document, _ = run_stage(stage_files())
    assert document["marketValueCoverage"] == {"players": 0, "of": 1}, \
        f"got {document['marketValueCoverage']}"
    assert document["managers"]["1"]["purchaseMarkup"]["n"] == 0, "expected no markup data"
    ### The metrics that need no curve are unaffected by the dead upstream stage
    assert document["managers"]["1"]["holdDuration"]["n"] == 1, "expected the hold time kept"


def test_the_stage_names_the_missing_transfer_file_and_the_stage_behind_it():
    message = stage_error(stage_files(without="all_transfers.json"))
    assert "all_transfers.json" in message, f"error should name the file, got: {message}"
    assert "turnovers" in message, f"error should name the stage, got: {message}"


def test_the_stage_names_market_value_changes_for_the_user_index():
    ### STATIC_users.json is written by leagues.get_users(), whose only caller is
    ### market_value_changes(). Naming login() would send a reader to the wrong stage.
    message = stage_error(stage_files(without="STATIC_users.json"))
    assert "STATIC_users.json" in message, f"error should name the file, got: {message}"
    assert "market_value_changes" in message, f"error should name the stage, got: {message}"
    assert "login" not in message, f"login does not write this file, got: {message}"


def test_the_stage_names_market_value_changes_for_the_team_list():
    message = stage_error(stage_files(without="STATIC_teams.json"))
    assert "STATIC_teams.json" in message, f"error should name the file, got: {message}"
    assert "market_value_changes" in message, f"error should name the stage, got: {message}"


### ===============================================================================

if __name__ == "__main__":
    print("market_value_index()")
    check("keys the curve by calendar day", test_index_keys_the_curve_by_calendar_day)
    check("converts the epoch day", test_index_converts_the_epoch_day)
    check("skips entries without a value", test_index_skips_entries_without_a_value)
    check("leaves out players without a curve", test_index_leaves_out_players_without_a_curve)

    print("\n1. median hold duration")
    check("median over three sales", test_median_hold_duration_over_three_sales)
    check("averages the middle two", test_median_hold_duration_averages_the_middle_two)
    check("counts part days", test_hold_duration_counts_part_days)
    check("counts a round trip through the market without dropping it",
          test_hold_duration_counts_a_round_trip_through_the_market_without_dropping_it)
    check("a median of seconds stays readable", test_a_median_of_seconds_stays_readable)
    check("a median of minutes is no longer zero in days",
          test_a_median_of_hours_is_no_longer_zero_in_days)
    check("a quick sale to another manager is no round trip",
          test_a_quick_sale_to_another_manager_is_no_round_trip)
    check("a sale back to the market after a day is no round trip",
          test_a_sale_back_to_the_market_after_a_day_is_no_round_trip)
    check("ignores players assigned at the season start",
          test_hold_duration_ignores_players_assigned_at_the_season_start)
    check("ignores a pair the buyer did not sell",
          test_hold_duration_ignores_a_pair_the_buyer_did_not_sell)
    check("without a single sale", test_hold_duration_without_a_single_sale)

    print("\n2. mean markup at purchase")
    check("mean and median over the day value", test_mean_and_median_markup_over_the_day_value)
    check("negative below the day value", test_markup_is_negative_below_the_day_value)
    check("n counts only the covered buys", test_markup_n_counts_only_the_buys_the_curve_covers)
    check("needs the value of the day itself", test_markup_needs_the_value_of_the_day_itself)
    check("without any market values", test_markup_without_any_market_values)

    print("\n3. share of momentum buys")
    check("counts only the covered buys", test_momentum_share_counts_only_the_covered_buys)
    check("needs a strictly rising trend", test_momentum_needs_a_strictly_rising_trend)
    check("without any market values", test_momentum_without_any_market_values)

    print("\n4. top three clubs")
    check("the three most bought from", test_top_clubs_are_the_three_most_bought_from)
    check("breaks a tie by team id", test_top_clubs_break_a_tie_by_team_id)
    check("names an unknown team as none", test_top_clubs_name_an_unknown_team_as_none)
    check("without a single buy", test_top_clubs_without_a_single_buy)
    check("ignores sales", test_top_clubs_ignore_sales)

    print("\nactivity window")
    check("uses local hours", test_activity_window_uses_local_hours)
    check("counts sales too", test_activity_window_counts_sales_too)
    check("without a single booking", test_activity_window_without_a_single_booking)

    print("\nattribution")
    check("every league manager gets a profile", test_every_league_manager_gets_a_profile)
    check("an unknown manager gets no profile", test_an_unknown_manager_gets_no_profile)
    check("a booking between managers counts for both sides",
          test_a_booking_between_managers_counts_for_both_sides)

    print("\nmarket value coverage")
    check("counts the bought players that have a curve",
          test_coverage_counts_the_bought_players_that_have_a_curve)
    check("says zero of n when the cache is empty",
          test_coverage_says_zero_of_n_when_the_cache_is_empty)
    check("counts a player two managers bought once",
          test_coverage_counts_a_player_two_managers_bought_once)
    check("leaves out players that were only sold",
          test_coverage_leaves_out_players_that_were_only_sold)
    check("without any transfers", test_coverage_without_any_transfers)

    print("\nwrite_manager_profiles()")
    check("writes the file from the run cache", test_the_stage_writes_the_file_from_the_run_cache)
    check("reports an empty cache as zero coverage",
          test_the_stage_reports_an_empty_cache_as_zero_coverage)
    check("names the missing transfer file and the stage behind it",
          test_the_stage_names_the_missing_transfer_file_and_the_stage_behind_it)
    check("names market_value_changes for the user index",
          test_the_stage_names_market_value_changes_for_the_user_index)
    check("names market_value_changes for the team list",
          test_the_stage_names_market_value_changes_for_the_team_list)

    total, passed = len(PASSED), sum(PASSED)
    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)
