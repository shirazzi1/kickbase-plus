"""Tests for the requests a run makes, and the one it must keep making in full.

  - team_value_per_match_day() called ranking() once per manager and match day. One
    ranking response already carries every manager's team value, so a 13 manager league
    made 340 requests where 34 hold the same information. That saving stands.
  - player_marketvalue() was narrowed to the days a run reads and had to be put back:
    /marketValue/31 answered 200 with at most one point per player, which left every
    delta in market_value_changes.json null. The window is a constant again, and these
    tests hold it there - see miscellaneous.MARKET_VALUE_DAYS for the whole story.

    ./venv/bin/python tests/test_api_traffic.py
"""

import json
import sys
import tempfile

from os import environ, makedirs, path

### Make the repository root importable regardless of where this is run from
sys.path.insert(0, path.dirname(path.dirname(path.abspath(__file__))))

from backend import miscellaneous
from backend.kickbase import http
from backend.kickbase.v4 import competitions, leagues

### ===============================================================================

PASSED = []

START = "2026-08-01T18:00:00Z"


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


def set_start_date(value):
    """Set or clear the START_DATE environment variable."""
    if value is None:
        environ.pop("START_DATE", None)
    else:
        environ["START_DATE"] = value


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.headers = {}

    def json(self):
        return self._payload


### ===============================================================================
### MARKET_VALUE_DAYS
###
### This is a tripwire, not a law. The window may be narrowed again - but only after a
### manual request against the live API has shown that the narrower one answers with a
### full curve, because the last attempt did not:
###
###   Phase 0 made the window grow with the season, which meant 31 days early on.
###   Kickbase answered every /marketValue/31 of 2026-08-13 with HTTP 200 and at most one
###   point, so all 466 curves in market_value_changes.json came out null, 1935 sell
###   transfers found no market value on START_DATE, and 55 of 172 taken players ended up
###   at a buy price of 0. Nothing in the logs says why.
###
### Whoever moves the window changes the number below and reads this first.
### ===============================================================================


def test_the_window_is_the_full_year():
    assert miscellaneous.MARKET_VALUE_DAYS == 365, \
        f"got {miscellaneous.MARKET_VALUE_DAYS} - read the note above this test"


def test_the_window_does_not_depend_on_the_season_or_the_run():
    """A window that varied per run is what produced the empty curves. One value, always -
    which is also what lets the disk cache validate an entry against it."""
    set_start_date(START)
    fake = use_fake(lambda url: FakeResponse({"it": [{"dt": 1, "mv": 100}]}))

    try:
        leagues.player_marketvalue("token", "755")
        set_start_date(None)
        leagues.clear_caches()
        leagues.player_marketvalue("token", "755")
    finally:
        set_start_date(START)
        leagues.clear_caches()
        http.reset_session()

    windows = {url.rsplit("/", 1)[-1] for url in fake.urls}
    assert windows == {str(miscellaneous.MARKET_VALUE_DAYS)}, \
        f"expected one window whatever the season, got {sorted(windows)}"


### ===============================================================================
### player_marketvalue()
### ===============================================================================


def use_fake(handler):
    """Swap the pooled HTTP session and reset the caches."""
    class FakeSession:
        def __init__(self):
            self.urls = []

        def get(self, url, headers=None, timeout=None):
            self.urls.append(url)
            return handler(url)

    fake = FakeSession()
    http.reset_session(fake)
    leagues.clear_caches()
    return fake


def test_the_requested_window_is_the_one_the_constant_names():
    set_start_date(START)
    fake = use_fake(lambda url: FakeResponse({"it": [{"dt": 1, "mv": 100}]}))

    try:
        leagues.player_marketvalue("token", "755")
    finally:
        leagues.clear_caches()
        http.reset_session()

    assert fake.urls[0].endswith(f"/marketValue/{miscellaneous.MARKET_VALUE_DAYS}"), \
        f"got {fake.urls[0]}"


def test_one_request_per_player_and_nothing_probed_first():
    """The narrower window brought a per-run negotiation with it: one window tried, a
    second one on the fallback path. With one window there is nothing to negotiate, and a
    curve that cannot be had costs exactly one request."""
    set_start_date(START)
    fake = use_fake(lambda url: FakeResponse({"it": [{"dt": 1, "mv": 100}]}))

    try:
        leagues.player_marketvalue("token", "755")
        leagues.player_marketvalue("token", "756")
    finally:
        leagues.clear_caches()
        http.reset_session()

    assert len(fake.urls) == 2, f"expected one request per player, got {fake.urls}"


def test_a_history_that_cannot_be_read_at_all_raises():
    """Silently returning an empty history would invent buy prices of zero."""
    from backend import exceptions

    set_start_date(START)
    fake = use_fake(lambda url: FakeResponse({}, status_code=400))

    try:
        leagues.player_marketvalue("token", "755")
    except exceptions.KickbaseException:
        pass
    else:
        raise AssertionError("expected a KickbaseException")
    finally:
        leagues.clear_caches()
        http.reset_session()

    assert len(fake.urls) == 1, f"a rejected request must not be repeated: {fake.urls}"


def test_a_server_error_is_a_real_outage():
    """There is no second window to try, so the run must stop.

    Swallowing this would hand every caller an empty history, and an empty history is
    what invents buy prices of zero.
    """
    from backend import exceptions

    set_start_date(START)
    fake = use_fake(lambda url: FakeResponse({}, status_code=503))

    try:
        leagues.player_marketvalue("token", "755")
    except exceptions.ApiUnavailableException:
        pass
    except Exception as e:
        raise AssertionError(f"expected ApiUnavailableException, got {type(e).__name__}: {e}")
    else:
        raise AssertionError("expected ApiUnavailableException")
    finally:
        leagues.clear_caches()
        http.reset_session()

    assert len(fake.urls) == 1, f"there is no second window to fall back to: {fake.urls}"


def test_an_expired_token_travels_on_untouched():
    """It is not an answer about the history, so it must not be read as one."""
    from backend import exceptions

    set_start_date(START)
    fake = use_fake(lambda url: FakeResponse({}, status_code=401))

    try:
        leagues.player_marketvalue("token", "755")
    except exceptions.AuthExpiredException:
        pass
    except Exception as e:
        raise AssertionError(f"expected AuthExpiredException, got {type(e).__name__}: {e}")
    else:
        raise AssertionError("expected AuthExpiredException")
    finally:
        leagues.clear_caches()
        http.reset_session()

    assert len(fake.urls) == 1, f"an auth failure must not be retried: {fake.urls}"


### ===============================================================================
### team_value_per_match_day()
### ===============================================================================


def run_team_values(users, played_match_days, total_match_days, absent_from_ranking=()):
    """Run team_value_per_match_day() against stubbed API calls.

    Returns the written team values and which match days a ranking was requested for.
    """
    import main

    calls = []

    def fake_ranking(token, league_id, match_day):
        calls.append(match_day)
        return {"us": [{"i": user_id, "tv": 1_000_000 + int(user_id) + match_day}
                       for user_id in users if user_id not in absent_from_ranking]}

    match_days_list = [{"day": day, "firstMatch": "", "lastMatch": ""}
                       for day in range(1, total_match_days + 1)]

    with tempfile.TemporaryDirectory() as tmp:
        data_dir = path.join(tmp, "data")
        ts_dir = path.join(data_dir, "timestamps")
        makedirs(ts_dir, exist_ok=True)

        with open(path.join(data_dir, "STATIC_users.json"), "w") as f:
            json.dump(users, f)

        original = (main.DATA_DIR, miscellaneous.DATA_DIR, miscellaneous.TIMESTAMP_DIR,
                    miscellaneous.LAST_GOOD_DIR, miscellaneous.HISTORY_DIR,
                    leagues.ranking, competitions.match_days)
        main.DATA_DIR = data_dir
        miscellaneous.DATA_DIR = data_dir
        miscellaneous.TIMESTAMP_DIR = ts_dir
        miscellaneous.LAST_GOOD_DIR = path.join(tmp, "last-good")
        miscellaneous.HISTORY_DIR = path.join(tmp, "history")

        try:
            leagues.ranking = fake_ranking
            competitions.match_days = lambda token: (played_match_days, match_days_list)

            class FakeLeague:
                id = "1"
                name = "Test"

            main.team_value_per_match_day("token", FakeLeague())

            with open(path.join(data_dir, "team_values.json")) as f:
                return json.load(f), calls
        finally:
            (main.DATA_DIR, miscellaneous.DATA_DIR, miscellaneous.TIMESTAMP_DIR,
             miscellaneous.LAST_GOOD_DIR, miscellaneous.HISTORY_DIR,
             leagues.ranking, competitions.match_days) = original


### 13 managers, the size of the league this project was written for
USERS = {str(i): f"Manager {i}" for i in range(1, 14)}


def test_one_ranking_request_per_played_match_day():
    _, calls = run_team_values(USERS, played_match_days=3, total_match_days=34)

    assert calls == [1, 2, 3], \
        f"expected one request per played match day, got {len(calls)}: {calls}"


def test_future_match_days_are_not_requested():
    _, calls = run_team_values(USERS, played_match_days=2, total_match_days=34)

    assert max(calls) == 2, f"a future match day was requested: {calls}"


def test_every_manager_still_gets_their_own_team_value():
    values, _ = run_team_values(USERS, played_match_days=3, total_match_days=34)

    ### fake_ranking returns 1000000 + user id + match day
    assert values["Manager 7"]["2"] == 1_000_000 + 7 + 2, \
        f"got {values['Manager 7']}"
    assert values["Manager 1"]["3"] == 1_000_000 + 1 + 3, \
        f"got {values['Manager 1']}"


def test_unplayed_match_days_stay_at_zero():
    values, _ = run_team_values(USERS, played_match_days=3, total_match_days=34)

    assert list(values["Manager 1"]) == ["1", "2", "3"], \
        f"expected one entry per played match day, got {sorted(values['Manager 1'])}"


def test_a_manager_missing_from_a_ranking_has_no_team_value():
    """Not the same as a team value of zero - a manager who joined mid-season had none.

    This is what the per-manager scan through ranking_data["us"] produced before the
    hoist, and the frontend chart depends on the difference.
    """
    values, _ = run_team_values(USERS, played_match_days=2, total_match_days=34,
                                absent_from_ranking={"7"})

    assert values["Manager 7"]["1"] is None, \
        f"expected no team value, got {values['Manager 7']}"
    assert values["Manager 6"]["1"] == 1_000_000 + 6 + 1, \
        f"the other managers must be unaffected, got {values['Manager 6']}"


### ===============================================================================

if __name__ == "__main__":
    print("MARKET_VALUE_DAYS")
    check("the window is the full year", test_the_window_is_the_full_year)
    check("the window does not depend on the season or the run",
          test_the_window_does_not_depend_on_the_season_or_the_run)

    print("\nplayer_marketvalue()")
    check("the requested window is the one the constant names",
          test_the_requested_window_is_the_one_the_constant_names)
    check("one request per player and nothing probed first",
          test_one_request_per_player_and_nothing_probed_first)
    check("an unreadable history raises", test_a_history_that_cannot_be_read_at_all_raises)
    check("a 5xx is a real outage", test_a_server_error_is_a_real_outage)
    check("an expired token travels on untouched", test_an_expired_token_travels_on_untouched)

    print("\nteam_value_per_match_day()")
    check("one ranking request per played match day", test_one_ranking_request_per_played_match_day)
    check("future match days are not requested", test_future_match_days_are_not_requested)
    check("every manager still gets their own team value", test_every_manager_still_gets_their_own_team_value)
    check("unplayed match days stay at zero", test_unplayed_match_days_stay_at_zero)
    check("a manager missing from a ranking has no team value", test_a_manager_missing_from_a_ranking_has_no_team_value)

    total, passed = len(PASSED), sum(PASSED)
    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)
