"""Tests for the merged transfer market table.

The two market tables were merged into one, and each row gained the logged in user's
own bid, the daily market value deltas and the status note from the player profile.

Shapes below are taken from real API responses (league Kickbase-Elite 26/27,
2026-08-12). Two things they establish:

  - "ofs" only ever holds the user's *own* offers; other managers' bids are not
    exposed. The same price is mirrored on the item as top level "uop", with "uoid"
    naming the bidder.
  - "exs" (expiry) is present on Kickbase listings only. All 84 user listings in that
    snapshot had none, which is why "Ablaufdatum" is empty for them.

    ./venv/bin/python tests/test_market_table.py
"""

import sys

from os import path

sys.path.insert(0, path.dirname(path.dirname(path.abspath(__file__))))

from backend import miscellaneous
from backend.kickbase.endpoints.leagues import Market_Players

### ===============================================================================

LEAGUE_ID = "11412166"
OWN_USER_ID = "3854976"
OTHER_USER_ID = "2592773"

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


def history(*values):
    """Build a market value history, oldest first, the way the API returns it."""
    return [{"dt": 20000 + i, "mv": mv} for i, mv in enumerate(values)]


def set_bep_days(growth, target):
    """Set or clear the two horizon variables."""
    from os import environ
    for name, value in (("BEP_GROWTH_DAYS", growth), ("BEP_TARGET_DAYS", target)):
        if value is None:
            environ.pop(name, None)
        else:
            environ[name] = value


def run_main_config_write():
    """Call the config writer with PUBLIC_DIR redirected, and return what it wrote."""
    import json
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        data_dir = path.join(tmp, "data")
        ts_dir = path.join(data_dir, "timestamps")
        from os import makedirs
        makedirs(ts_dir, exist_ok=True)

        import main

        original = (miscellaneous.PUBLIC_DIR, miscellaneous.STATE_DIR, miscellaneous.TIMESTAMP_DIR)
        miscellaneous.PUBLIC_DIR = data_dir
        miscellaneous.STATE_DIR = data_dir
        miscellaneous.TIMESTAMP_DIR = ts_dir
        try:
            main.write_bep_config()
            with open(path.join(data_dir, "config.json")) as f:
                return json.load(f)
        finally:
            miscellaneous.PUBLIC_DIR, miscellaneous.STATE_DIR, miscellaneous.TIMESTAMP_DIR = original


### ===============================================================================
### market_value_deltas()
### ===============================================================================


### 31 entries: enough for every delta. Index -1 is today, -31 is 30 days back.
FULL_HISTORY = history(*range(1_000_000, 1_000_000 + 31 * 10_000, 10_000))


def test_todays_delta_is_the_last_two_entries():
    deltas = miscellaneous.market_value_deltas(FULL_HISTORY)
    assert deltas["today"] == 10_000, f"expected 10000, got {deltas['today']}"


def test_yesterday_and_two_days_step_back_one_day_each():
    deltas = miscellaneous.market_value_deltas(history(100, 300, 600, 1000))
    assert deltas["today"] == 400, f"today: expected 400, got {deltas['today']}"
    assert deltas["yesterday"] == 300, f"yesterday: expected 300, got {deltas['yesterday']}"
    assert deltas["twoDays"] == 200, f"twoDays: expected 200, got {deltas['twoDays']}"


def test_seven_and_thirty_days_span_from_today():
    deltas = miscellaneous.market_value_deltas(FULL_HISTORY)
    assert deltas["sevenDaysAvg"] == 70_000, f"7d: expected 70000, got {deltas['sevenDaysAvg']}"
    assert deltas["thirtyDaysAvg"] == 300_000, f"30d: expected 300000, got {deltas['thirtyDaysAvg']}"


def test_deltas_can_be_negative():
    deltas = miscellaneous.market_value_deltas(history(1000, 900))
    assert deltas["today"] == -100, f"expected -100, got {deltas['today']}"


def test_short_history_yields_none_instead_of_raising():
    """The old code indexed history[-4] unguarded and killed the run on a new player."""
    deltas = miscellaneous.market_value_deltas(history(500, 700, 900))
    assert deltas["today"] == 200, f"today should still work, got {deltas['today']}"
    assert deltas["yesterday"] == 200, f"yesterday should still work, got {deltas['yesterday']}"
    assert deltas["twoDays"] is None, f"twoDays needs 4 entries, got {deltas['twoDays']}"
    assert deltas["sevenDaysAvg"] is None, f"7d needs 8 entries, got {deltas['sevenDaysAvg']}"
    assert deltas["thirtyDaysAvg"] is None, f"30d needs 31 entries, got {deltas['thirtyDaysAvg']}"


def test_single_entry_history_yields_all_none():
    deltas = miscellaneous.market_value_deltas(history(500))
    assert all(v is None for v in deltas.values()), f"expected all None, got {deltas}"


def test_empty_history_yields_all_none():
    deltas = miscellaneous.market_value_deltas([])
    assert all(v is None for v in deltas.values()), f"expected all None, got {deltas}"


def test_missing_history_yields_all_none():
    deltas = miscellaneous.market_value_deltas(None)
    assert all(v is None for v in deltas.values()), f"expected all None, got {deltas}"


def test_delta_keys_match_the_existing_frontend_contract():
    """MarketValueChangesTable reads these exact keys, so they must not drift."""
    deltas = miscellaneous.market_value_deltas(FULL_HISTORY)
    assert set(deltas) == {"today", "yesterday", "twoDays", "sevenDaysAvg", "thirtyDaysAvg"}, \
        f"unexpected keys: {sorted(deltas)}"


### ===============================================================================
### Market_Players.own_offer()
### ===============================================================================


### Salim Amani Musah: a Kickbase listing carrying our own bid
OWN_BID_VIA_OFS = {
    "i": "8289", "fn": "Salim Amani", "n": "Musah", "tid": "2", "pos": 3, "st": 0,
    "mvt": 1, "mv": 5103416, "prc": 5103416, "ofc": 1, "exs": 3915,
    "ofs": [{"u": OWN_USER_ID, "unm": "shirazzi", "uoid": OWN_USER_ID,
             "uop": 5222222, "st": 0, "uim": "user/91fd.jpe"}],
}

### Sacha Boey: the same bid expressed only through the top level mirror
OWN_BID_VIA_TOP_LEVEL = {
    "i": "3754", "fn": "Sacha", "n": "Boey", "tid": "14", "pos": 2, "st": 0,
    "mvt": 0, "mv": 523350, "prc": 523350, "ofc": 1, "exs": 7200,
    "uoid": OWN_USER_ID, "uop": 523350,
}

### Someone else's bid. Kickbase does not expose these today, but reading one as ours
### would put a wrong number in the "Dein Gebot" column.
FOREIGN_BID = {
    "i": "1767", "fn": "Some", "n": "Player", "tid": "5", "pos": 4, "st": 0,
    "mvt": 1, "mv": 800000, "prc": 800000, "ofc": 1, "exs": 3600,
    "ofs": [{"u": OTHER_USER_ID, "unm": "Meier", "uoid": OTHER_USER_ID, "uop": 999999}],
}

### Jeffrey Gouweleeuw: a Kickbase listing with no bid at all
NO_BID = {
    "i": "1811", "fn": "Jeffrey", "n": "Gouweleeuw", "tid": "13", "pos": 2, "st": 2,
    "mvt": 1, "mv": 10399428, "prc": 10399428, "ofc": 0, "exs": 13315,
}

### Matthias Ginter, listed by a league member above his market value.
### "dt" is the listing date. Kickbase sends it for every listing, unlike "exs".
USER_LISTED = {
    "i": "49", "fn": "Matthias", "n": "Ginter", "tid": "5", "pos": 2, "st": 0,
    "mvt": 1, "mv": 26260331, "prc": 32000000, "ofc": 3,
    "dt": "2026-08-12T09:15:00Z",
    "u": {"i": OWN_USER_ID, "n": "shirazzi", "uim": "user/91fd.jpe", "isvf": False, "st": 0},
}

### Kai Havertz, listed by a different league member - neither Kickbase nor us.
### Without this fixture, "sellerId" is only ever exercised against userId is None
### (Kickbase) or userId == OWN_USER_ID (ours), so a bug that carried every league
### member's listing as our own id would go unnoticed.
OTHER_MANAGER_LISTED = {
    "i": "223", "fn": "Kai", "n": "Havertz", "tid": "3", "pos": 3, "st": 0,
    "mvt": 1, "mv": 41000000, "prc": 45000000, "ofc": 0,
    "u": {"i": OTHER_USER_ID, "n": "Meier", "uim": "user/ab12.jpe", "isvf": False, "st": 0},
}


def test_own_bid_read_from_the_offers_list():
    player = Market_Players(OWN_BID_VIA_OFS)
    assert player.own_offer(OWN_USER_ID) == 5222222, \
        f"expected 5222222, got {player.own_offer(OWN_USER_ID)}"


def test_own_bid_read_from_the_top_level_mirror():
    player = Market_Players(OWN_BID_VIA_TOP_LEVEL)
    assert player.own_offer(OWN_USER_ID) == 523350, \
        f"expected 523350, got {player.own_offer(OWN_USER_ID)}"


def test_another_managers_bid_is_not_reported_as_ours():
    player = Market_Players(FOREIGN_BID)
    assert player.own_offer(OWN_USER_ID) is None, \
        f"a foreign bid must not surface as ours, got {player.own_offer(OWN_USER_ID)}"


def test_no_bid_means_none():
    player = Market_Players(NO_BID)
    assert player.own_offer(OWN_USER_ID) is None


def test_user_id_type_does_not_matter():
    """The id arrives as a string from the API and as an int from some call sites."""
    player = Market_Players(OWN_BID_VIA_OFS)
    assert player.own_offer(int(OWN_USER_ID)) == 5222222, "an int user id must match too"


### ===============================================================================
### player_statistics() localisation
### ===============================================================================


def test_player_statistics_asks_for_german():
    """The status note is the only prose in the response and defaults to English.

    Kickbase localises it on Accept-Language alone; a "lang" or "locale" query parameter
    is ignored. Without the header the tooltip silently reverts to English.
    """
    from backend.kickbase import http
    from backend.kickbase.v4 import leagues as leagues_v4

    captured = {}

    class FakeResponse:
        status_code = 200
        headers = {}

        def json(self):
            return {"i": "663", "stxt": "Muskuläre Probleme - verpasst nächsten beiden Testspiele"}

    class FakeSession:
        @staticmethod
        def get(url, headers=None, **kwargs):
            captured["headers"] = headers or {}
            return FakeResponse()

    leagues_v4.clear_caches()
    try:
        http.reset_session(FakeSession)
        leagues_v4.player_statistics("token", LEAGUE_ID, "663")
    finally:
        http.reset_session()
        leagues_v4.clear_caches()

    language = captured["headers"].get("Accept-Language", "")
    assert "de" in language, f"expected a German Accept-Language, got {language!r}"


### ===============================================================================
### market() end to end
### ===============================================================================


def run_market():
    """Run market() against stubbed API calls and return the rows it wrote."""
    import json
    import tempfile
    from os import makedirs

    import main
    from backend.kickbase.v4 import leagues

    market_items = [USER_LISTED, NO_BID, OWN_BID_VIA_OFS, OWN_BID_VIA_TOP_LEVEL, FOREIGN_BID,
                    OTHER_MANAGER_LISTED]

    ### Only the injured player carries a note, and the API leaves the trailing newline in
    stats_by_id = {
        "1811": {"i": "1811", "st": 2, "stl": [2], "stxt": "Muscle problems - out for weeks\n"},
    }

    with tempfile.TemporaryDirectory() as tmp:
        data_dir = path.join(tmp, "data")
        ts_dir = path.join(data_dir, "timestamps")
        makedirs(ts_dir, exist_ok=True)

        original = (miscellaneous.PUBLIC_DIR, miscellaneous.STATE_DIR,
                    miscellaneous.TIMESTAMP_DIR,
                    miscellaneous.LAST_GOOD_DIR, miscellaneous.HISTORY_DIR,
                    leagues.get_market, leagues.player_statistics, leagues.player_marketvalue)
        miscellaneous.PUBLIC_DIR = data_dir
        miscellaneous.STATE_DIR = data_dir
        miscellaneous.TIMESTAMP_DIR = ts_dir
        miscellaneous.LAST_GOOD_DIR = path.join(tmp, "last-good")
        miscellaneous.HISTORY_DIR = path.join(tmp, "history")

        try:
            leagues.get_market = lambda token, lid: [Market_Players(p) for p in market_items]
            leagues.player_statistics = lambda token, lid, pid: stats_by_id.get(str(pid), {"i": str(pid), "st": 0})
            ### 9 entries, uneven and partly falling before the last four settle into the
            ### 1000/1200/1500/1900 run the delta tests below depend on. The extra length
            ### lets a 7 day growth window (unlike the 3 day default) actually resolve.
            leagues.player_marketvalue = lambda token, pid: history(
                1400, 1300, 1500, 1250, 1600, 1000, 1200, 1500, 1900)

            class FakeLeague:
                id = LEAGUE_ID
                name = "Kickbase-Elite 26/27"

            main.market("token", FakeLeague(), OWN_USER_ID)

            with open(path.join(data_dir, "market.json")) as f:
                rows = json.load(f)
        finally:
            (miscellaneous.PUBLIC_DIR, miscellaneous.STATE_DIR,
             miscellaneous.TIMESTAMP_DIR,
             miscellaneous.LAST_GOOD_DIR, miscellaneous.HISTORY_DIR, leagues.get_market,
             leagues.player_statistics, leagues.player_marketvalue) = original

    return {row["lastName"]: row for row in rows}


def test_one_file_holds_both_listing_sources():
    rows = run_market()
    assert len(rows) == 6, f"expected all 6 market players in one file, got {sorted(rows)}"
    assert "Ginter" in rows, "the user listing is missing"
    assert "Gouweleeuw" in rows, "the Kickbase listing is missing"


def test_free_agent_flag_separates_the_two_sources():
    rows = run_market()
    assert rows["Ginter"]["isFreeAgent"] is False, "a user listing is not a free agent"
    assert rows["Gouweleeuw"]["isFreeAgent"] is True, "a Kickbase listing is a free agent"


def test_seller_names_kickbase_for_free_agents():
    rows = run_market()
    assert rows["Ginter"]["seller"] == "shirazzi", f"got {rows['Ginter']['seller']}"
    assert rows["Gouweleeuw"]["seller"] == "Kickbase", f"got {rows['Gouweleeuw']['seller']}"


def test_price_and_market_value_are_both_kept():
    """The bid surcharge is relative to the market value, so both numbers are needed."""
    rows = run_market()
    assert rows["Ginter"]["price"] == 32000000, f"got {rows['Ginter']['price']}"
    assert rows["Ginter"]["marketValue"] == 26260331, f"got {rows['Ginter']['marketValue']}"


def test_own_bid_lands_in_the_row():
    rows = run_market()
    assert rows["Musah"]["ownBid"] == 5222222, f"got {rows['Musah']['ownBid']}"
    assert rows["Boey"]["ownBid"] == 523350, f"got {rows['Boey']['ownBid']}"


def test_rows_without_our_bid_have_none():
    rows = run_market()
    assert rows["Gouweleeuw"]["ownBid"] is None, f"got {rows['Gouweleeuw']['ownBid']}"
    assert rows["Player"]["ownBid"] is None, "a foreign bid must not appear as ours"


def test_status_note_is_stripped_and_only_present_when_set():
    rows = run_market()
    assert rows["Gouweleeuw"]["statusText"] == "Muscle problems - out for weeks", \
        f"got {rows['Gouweleeuw']['statusText']!r}"
    assert rows["Ginter"]["statusText"] is None, \
        f"a fit player has no note, got {rows['Ginter']['statusText']!r}"


def test_deltas_are_attached_to_every_row():
    rows = run_market()
    for name, row in rows.items():
        assert row["today"] == 400, f"{name}: expected today 400, got {row['today']}"
        assert row["yesterday"] == 300, f"{name}: expected yesterday 300, got {row['yesterday']}"
        assert row["twoDays"] == 200, f"{name}: expected twoDays 200, got {row['twoDays']}"
        assert row["sevenDaysAvg"] == 600, f"{name}: expected sevenDaysAvg 600, got {row['sevenDaysAvg']}"


def test_trend_is_gone():
    """The delta columns replaced it, so carrying it would be dead weight."""
    rows = run_market()
    assert "trend" not in rows["Ginter"], "trend should no longer be written"


def test_expiration_is_iso_for_free_agents_and_none_for_user_listings():
    from datetime import datetime, timedelta, timezone

    rows = run_market()

    assert rows["Ginter"]["expiration"] is None, \
        f"the API gives no expiry for user listings, got {rows['Ginter']['expiration']!r}"

    raw = rows["Gouweleeuw"]["expiration"]
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None, f"expiration needs a UTC offset to sort right, got {raw!r}"

    ### exs was 13315 seconds; allow a minute of slack for the run itself
    expected = datetime.now(timezone.utc) + timedelta(seconds=13315)
    assert abs((parsed - expected).total_seconds()) < 60, \
        f"expected roughly {expected.isoformat()}, got {raw!r}"


def test_every_row_carries_its_player_id():
    """The table keyed its rows by array position, so a sale shifted every row below it."""
    rows = run_market()
    assert rows["Ginter"]["playerId"] == "49", f"got {rows['Ginter']}"
    assert rows["Gouweleeuw"]["playerId"] == "1811", f"got {rows['Gouweleeuw']}"

    ids = [row["playerId"] for row in rows.values()]
    assert len(set(ids)) == len(ids), f"player ids must be unique to key rows by, got {ids}"


def test_listed_since_survives_for_a_user_listing():
    """The one age signal the user listings have, where the expiry column stays empty."""
    from datetime import datetime

    rows = run_market()
    raw = rows["Ginter"]["listedSince"]
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))

    assert parsed.tzinfo is not None, \
        f"listedSince needs a UTC offset, else the frontend reads it as local time: {raw!r}"
    assert parsed.isoformat() == "2026-08-12T09:15:00+00:00", f"got {raw!r}"


def test_a_listing_without_a_date_stays_none():
    rows = run_market()
    assert rows["Gouweleeuw"]["listedSince"] is None, \
        f"the fixture carries no 'dt', got {rows['Gouweleeuw']['listedSince']!r}"


def test_offer_count_survives():
    rows = run_market()
    assert rows["Ginter"]["offerCount"] == 3, f"got {rows['Ginter']['offerCount']}"
    assert rows["Gouweleeuw"]["offerCount"] == 0, f"got {rows['Gouweleeuw']['offerCount']}"


def test_expiration_sorts_chronologically_as_a_string():
    """A dd.mm.yyyy string sorts wrongly across months, and this column is the default sort."""
    rows = run_market()
    earlier = rows["Musah"]["expiration"]   ### exs 3915
    later = rows["Gouweleeuw"]["expiration"]  ### exs 13315
    assert earlier < later, f"{earlier!r} should sort before {later!r}"


### ===============================================================================
### The fields the bid field needs
### ===============================================================================


def test_every_row_carries_the_player_id():
    """Without it a row cannot be addressed, so no bid can name a player."""
    rows = run_market()
    for name, row in rows.items():
        assert row.get("playerId"), f"row without a playerId: {row}"
    ### The ids are the "i" values from the market items
    assert {row["playerId"] for row in rows.values()} >= {"8289", "3754", "49"}, \
        f"expected the fixture player ids, got {[row['playerId'] for row in rows.values()]}"


def test_seller_id_identifies_the_users_own_listing():
    """You cannot bid on a player you listed yourself, so something has to say whose

    listing it is. main.py no longer flags this itself ("isOwnListing" was a second,
    independently computed answer to the same question the frontend's auction solver
    already answers from "sellerId" plus the logged-in manager's own id in
    balances.json) - it only ships the seller's id, and the comparison against the
    logged-in manager happens once, in the frontend.
    """
    rows = run_market()
    ### Ginter is listed by OWN_USER_ID in the fixtures
    assert rows["Ginter"]["sellerId"] == OWN_USER_ID, \
        f"expected Ginter's sellerId to be the user's own id, got {rows['Ginter']['sellerId']!r}"


def test_foreign_and_kickbase_listings_carry_a_different_seller_id():
    rows = run_market()
    assert rows["Musah"]["sellerId"] is None, \
        f"a Kickbase listing has no seller, got {rows['Musah']['sellerId']!r}"
    ### Havertz is listed by a different league member - the case a check that only
    ### looks at "sellerId is not None" would wrongly treat as ours
    assert rows["Havertz"]["sellerId"] == OTHER_USER_ID, \
        f"expected another manager's own id, got {rows['Havertz']['sellerId']!r}"
    assert rows["Havertz"]["sellerId"] != OWN_USER_ID, \
        "another manager's listing must not carry the user's own id"
    for name, row in rows.items():
        if row["isFreeAgent"]:
            assert row["sellerId"] is None, \
                f"a Kickbase listing has no seller to flag as the user's own: {name}"


def test_rows_carry_the_averaged_growth():
    rows = run_market()
    for name, row in rows.items():
        growth = row["avgDailyGrowth"]
        assert growth is None or isinstance(growth, (int, float)), \
            f"expected a number or None for {name}, got {growth!r}"


def test_the_growth_matches_the_three_day_deltas_at_the_default():
    """The same telescoping identity, now through market() itself."""
    set_bep_days(None, None)
    rows = run_market()
    for name, row in rows.items():
        if row["avgDailyGrowth"] is None:
            continue
        deltas = (row["today"], row["yesterday"], row["twoDays"])
        if any(delta is None for delta in deltas):
            continue
        expected = sum(deltas) / 3
        assert row["avgDailyGrowth"] == expected, \
            f"{name}: expected {expected}, got {row['avgDailyGrowth']}"


def test_a_wider_window_changes_the_growth():
    """Proves the env variable actually reaches market(), not just get_bep_days()."""
    set_bep_days("3", "3")
    narrow = {name: row["avgDailyGrowth"] for name, row in run_market().items()}
    set_bep_days("7", "3")
    wide = {name: row["avgDailyGrowth"] for name, row in run_market().items()}
    set_bep_days(None, None)

    differing = [name for name in narrow
                 if narrow[name] is not None and wide.get(name) is not None
                 and narrow[name] != wide[name]]
    assert differing, \
        f"expected at least one player's growth to change with the window, got {narrow} vs {wide}"


def test_config_json_is_written_with_both_horizons():
    set_bep_days("7", "14")
    config = run_main_config_write()
    set_bep_days(None, None)
    assert config == {"bepGrowthDays": 7, "bepTargetDays": 14}, \
        f"expected both horizons in config.json, got {config}"


### ===============================================================================

if __name__ == "__main__":
    print("market_value_deltas()")
    check("today is the last two entries", test_todays_delta_is_the_last_two_entries)
    check("yesterday and two days step back one day each", test_yesterday_and_two_days_step_back_one_day_each)
    check("7 and 30 days span from today", test_seven_and_thirty_days_span_from_today)
    check("deltas can be negative", test_deltas_can_be_negative)
    check("short history yields None instead of raising", test_short_history_yields_none_instead_of_raising)
    check("single entry history yields all None", test_single_entry_history_yields_all_none)
    check("empty history yields all None", test_empty_history_yields_all_none)
    check("missing history yields all None", test_missing_history_yields_all_none)
    check("keys match the existing frontend contract", test_delta_keys_match_the_existing_frontend_contract)

    print("\nMarket_Players.own_offer()")
    check("own bid read from the offers list", test_own_bid_read_from_the_offers_list)
    check("own bid read from the top level mirror", test_own_bid_read_from_the_top_level_mirror)
    check("another manager's bid is not ours", test_another_managers_bid_is_not_reported_as_ours)
    check("no bid means None", test_no_bid_means_none)
    check("user id type does not matter", test_user_id_type_does_not_matter)

    print("\nplayer_statistics()")
    check("asks for the status note in German", test_player_statistics_asks_for_german)

    print("\nmarket()")
    check("one file holds both listing sources", test_one_file_holds_both_listing_sources)
    check("free agent flag separates the sources", test_free_agent_flag_separates_the_two_sources)
    check("seller names Kickbase for free agents", test_seller_names_kickbase_for_free_agents)
    check("price and market value are both kept", test_price_and_market_value_are_both_kept)
    check("own bid lands in the row", test_own_bid_lands_in_the_row)
    check("rows without our bid have None", test_rows_without_our_bid_have_none)
    check("status note is stripped and conditional", test_status_note_is_stripped_and_only_present_when_set)
    check("deltas are attached to every row", test_deltas_are_attached_to_every_row)
    check("trend is gone", test_trend_is_gone)
    check("expiration is ISO for free agents, None for user listings", test_expiration_is_iso_for_free_agents_and_none_for_user_listings)
    check("expiration sorts chronologically as a string", test_expiration_sorts_chronologically_as_a_string)
    check("every row carries its player id", test_every_row_carries_its_player_id)
    check("listedSince survives for a user listing", test_listed_since_survives_for_a_user_listing)
    check("a listing without a date stays None", test_a_listing_without_a_date_stays_none)
    check("the offer count survives", test_offer_count_survives)

    print("\nthe fields the bid field needs")
    check("every row carries the player id", test_every_row_carries_the_player_id)
    check("seller id identifies the user's own listing", test_seller_id_identifies_the_users_own_listing)
    check("foreign and Kickbase listings carry a different seller id", test_foreign_and_kickbase_listings_carry_a_different_seller_id)
    check("rows carry the averaged growth", test_rows_carry_the_averaged_growth)
    check("the growth matches the three day deltas at the default", test_the_growth_matches_the_three_day_deltas_at_the_default)
    check("a wider window changes the growth", test_a_wider_window_changes_the_growth)
    check("config.json is written with both horizons", test_config_json_is_written_with_both_horizons)

    total, passed = len(PASSED), sum(PASSED)
    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)
