"""Tests for attributing activity feed bookings to a manager by user ID.

The feed names buyer ("byr") and seller ("slr") by display name and carries no user ID at
all - verified against the 381 real items in frontend/src/data/all_transfers.json, whose
"data" keys are exactly pi, pn, tid, t, trp, pim, tim, slr, byr. So the name has to be
resolved somewhere, and doing it in one place is what makes the failure cases visible:

  - Two managers sharing a display name used to be folded together by
    {value: key for key, value in league_users.items()}, which keeps whichever came last.
  - A name that resolves to nothing used to land under the key None, where no owner ever
    matches it, so the buy price silently fell back to the season start market value.

    ./venv/bin/python tests/test_user_attribution.py
"""

import json
import logging
import sys
import tempfile

from os import environ, makedirs, path

### Make the repository root importable regardless of where this is run from
sys.path.insert(0, path.dirname(path.dirname(path.abspath(__file__))))

from backend import miscellaneous

### ===============================================================================

PASSED = []

LEAGUE_USERS = {"3854976": "shirazzi", "2592773": "Reddy", "1234567": "Daniel"}


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


class CaptureWarnings(logging.Handler):
    """Collect warning messages emitted while the block runs."""

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())

    def __enter__(self):
        logging.getLogger().addHandler(self)
        ### Without this the root logger's default level swallows the records
        self.previous_level = logging.getLogger().level
        logging.getLogger().setLevel(logging.WARNING)
        return self

    def __exit__(self, *exc):
        logging.getLogger().removeHandler(self)
        logging.getLogger().setLevel(self.previous_level)
        return False


### ===============================================================================
### build_user_name_index()
### ===============================================================================


def test_index_maps_every_unique_name_to_its_id():
    index = miscellaneous.build_user_name_index(LEAGUE_USERS)
    assert index == {"shirazzi": "3854976", "Reddy": "2592773", "Daniel": "1234567"}, \
        f"got {index}"


def test_a_shared_display_name_is_left_out_instead_of_guessed_at():
    index = miscellaneous.build_user_name_index({"1": "Max", "2": "Max", "3": "Anna"})
    assert "Max" not in index, f"an ambiguous name must not resolve, got {index}"
    assert index["Anna"] == "3", f"the unambiguous names must survive, got {index}"


def test_a_shared_display_name_is_logged():
    with CaptureWarnings() as warnings:
        miscellaneous.build_user_name_index({"1": "Max", "2": "Max"})

    assert any("Max" in message for message in warnings.messages), \
        f"expected a warning naming the manager, got {warnings.messages}"


def test_an_empty_league_gives_an_empty_index():
    assert miscellaneous.build_user_name_index({}) == {}


### ===============================================================================
### resolve_user_id()
### ===============================================================================


def test_a_known_name_resolves_to_the_id():
    index = miscellaneous.build_user_name_index(LEAGUE_USERS)
    assert miscellaneous.resolve_user_id("Reddy", index) == "2592773"


def test_an_unknown_name_resolves_to_none():
    index = miscellaneous.build_user_name_index(LEAGUE_USERS)
    assert miscellaneous.resolve_user_id("Nobody", index) is None


def test_a_missing_name_resolves_to_none():
    """Only one side of a transfer is named when the other side was Kickbase itself."""
    index = miscellaneous.build_user_name_index(LEAGUE_USERS)
    assert miscellaneous.resolve_user_id(None, index) is None


def test_an_unknown_name_is_logged_once_per_name():
    index = miscellaneous.build_user_name_index(LEAGUE_USERS)
    miscellaneous.clear_caches()

    with CaptureWarnings() as warnings:
        for _ in range(5):
            miscellaneous.resolve_user_id("Nobody", index)

    hits = [message for message in warnings.messages if "Nobody" in message]
    assert len(hits) == 1, f"expected exactly one warning per name, got {hits}"


def test_the_warned_names_are_forgotten_between_runs():
    """main() calls clear_caches() at the start, so a second run warns again."""
    index = miscellaneous.build_user_name_index(LEAGUE_USERS)
    miscellaneous.clear_caches()
    miscellaneous.resolve_user_id("Nobody", index)
    miscellaneous.clear_caches()

    with CaptureWarnings() as warnings:
        miscellaneous.resolve_user_id("Nobody", index)

    assert any("Nobody" in message for message in warnings.messages), \
        f"expected the warning again after clear_caches(), got {warnings.messages}"


### ===============================================================================
### taken_free_players() buy prices
### ===============================================================================


def run_taken_free_players(transfers, league_users=LEAGUE_USERS, owner_id="2592773"):
    """Run taken_free_players() against stubbed API calls and return the taken players."""
    import main
    from backend.kickbase.v4 import leagues

    ### A buy price of 0 sends taken_free_players() looking for the START_DATE market value
    environ["START_DATE"] = "2026-08-01T18:00:00Z"

    ### One owned player, held by owner_id
    teams = [{"players": [{"i": "755", "tid": "8", "pos": 3, "n": "Müller",
                           "mv": 5_000_000, "st": 0, "mvt": 1}]}]
    stats = {"i": "755", "fn": "Thomas", "ln": "Müller",
             "opl": [{"li": "1", "oui": owner_id, "onm": league_users.get(owner_id)}]}

    with tempfile.TemporaryDirectory() as tmp:
        data_dir = path.join(tmp, "data")
        ts_dir = path.join(data_dir, "timestamps")
        makedirs(ts_dir, exist_ok=True)

        with open(path.join(data_dir, "STATIC_users.json"), "w") as f:
            json.dump(league_users, f)
        with open(path.join(data_dir, "STATIC_teams.json"), "w") as f:
            json.dump(teams, f)

        original = (main.DATA_DIR, miscellaneous.DATA_DIR, miscellaneous.TIMESTAMP_DIR,
                    miscellaneous.LAST_GOOD_DIR,
                    leagues.transfers, leagues.player_statistics, leagues.player_marketvalue)
        main.DATA_DIR = data_dir
        miscellaneous.DATA_DIR = data_dir
        miscellaneous.TIMESTAMP_DIR = ts_dir
        miscellaneous.LAST_GOOD_DIR = path.join(tmp, "last-good")

        try:
            leagues.transfers = lambda token, lid: transfers
            leagues.player_statistics = lambda token, lid, pid: stats
            ### No entry on START_DATE, so an unattributed buy falls back to 0
            leagues.player_marketvalue = lambda token, pid: []

            class FakeLeague:
                id = "1"
                name = "Test"

            main.taken_free_players("token", FakeLeague())

            with open(path.join(data_dir, "taken_players.json")) as f:
                return json.load(f)
        finally:
            (main.DATA_DIR, miscellaneous.DATA_DIR, miscellaneous.TIMESTAMP_DIR,
             miscellaneous.LAST_GOOD_DIR,
             leagues.transfers, leagues.player_statistics,
             leagues.player_marketvalue) = original


def buy_transfer(buyer, price=4_000_000, player_id="755"):
    """An activity feed item for a player bought off the market."""
    return {"i": f"t-{buyer}-{player_id}", "t": 15, "dt": "2026-08-02T10:00:00Z",
            "data": {"byr": buyer, "pi": player_id, "pn": "Müller", "tid": "8",
                     "t": 1, "trp": price}}


def test_the_buy_price_reaches_the_owner():
    players = run_taken_free_players([buy_transfer("Reddy")])
    assert players[0]["buyPrice"] == 4_000_000, \
        f"expected the recorded buy price, got {players[0]}"


def test_a_buy_by_an_unknown_name_does_not_reach_anyone():
    """It used to land under the key None, from where nothing could ever read it back."""
    players = run_taken_free_players([buy_transfer("Someone Who Left")])
    assert players[0]["buyPrice"] == 0, \
        f"an unattributable buy must not become someone's buy price, got {players[0]}"


def test_a_buy_by_a_namesake_does_not_reach_either_of_them():
    namesakes = {"2592773": "Reddy", "9999999": "Reddy", "3854976": "shirazzi"}
    players = run_taken_free_players([buy_transfer("Reddy")], league_users=namesakes)
    assert players[0]["buyPrice"] == 0, \
        f"an ambiguous name must not be credited to either manager, got {players[0]}"


### ===============================================================================

if __name__ == "__main__":
    print("build_user_name_index()")
    check("maps every unique name to its id", test_index_maps_every_unique_name_to_its_id)
    check("a shared display name is left out", test_a_shared_display_name_is_left_out_instead_of_guessed_at)
    check("a shared display name is logged", test_a_shared_display_name_is_logged)
    check("an empty league gives an empty index", test_an_empty_league_gives_an_empty_index)

    print("\nresolve_user_id()")
    check("a known name resolves to the id", test_a_known_name_resolves_to_the_id)
    check("an unknown name resolves to None", test_an_unknown_name_resolves_to_none)
    check("a missing name resolves to None", test_a_missing_name_resolves_to_none)
    check("an unknown name is logged once per name", test_an_unknown_name_is_logged_once_per_name)
    check("the warned names are forgotten between runs", test_the_warned_names_are_forgotten_between_runs)

    print("\ntaken_free_players() buy prices")
    check("the buy price reaches the owner", test_the_buy_price_reaches_the_owner)
    check("a buy by an unknown name reaches nobody", test_a_buy_by_an_unknown_name_does_not_reach_anyone)
    check("a buy by a namesake reaches neither", test_a_buy_by_a_namesake_does_not_reach_either_of_them)

    total, passed = len(PASSED), sum(PASSED)
    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)
