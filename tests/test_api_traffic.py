"""Tests for the two places a run asked Kickbase for far more than it reads.

  - team_value_per_match_day() called ranking() once per manager and match day. One
    ranking response already carries every manager's team value, so a 13 manager league
    made 340 requests where 34 hold the same information.
  - player_marketvalue() asked for /marketValue/365 for every player, every run, while
    only the days back to START_DATE are ever read.

    ./venv/bin/python tests/test_api_traffic.py
"""

import json
import sys
import tempfile

from datetime import datetime, timedelta, timezone
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
### market_value_days()
### ===============================================================================


def days_after_start(days):
    """The instant `days` days after START."""
    return datetime.fromisoformat(START.replace("Z", "+00:00")) + timedelta(days=days)


def test_a_young_season_still_asks_for_the_delta_window():
    """market_value_deltas() reads 31 entries whatever the season length."""
    set_start_date(START)
    assert miscellaneous.market_value_days(days_after_start(3)) == 31, \
        f"got {miscellaneous.market_value_days(days_after_start(3))}"


def test_a_long_season_asks_back_to_the_start_date():
    set_start_date(START)
    ### 200 days in, plus the two days of slack around the START_DATE entry
    assert miscellaneous.market_value_days(days_after_start(200)) == 202, \
        f"got {miscellaneous.market_value_days(days_after_start(200))}"


def test_the_window_never_exceeds_a_year():
    set_start_date(START)
    assert miscellaneous.market_value_days(days_after_start(900)) == 365, \
        f"got {miscellaneous.market_value_days(days_after_start(900))}"


def test_a_missing_start_date_falls_back_to_the_full_year():
    """Asking for too much only costs bandwidth; too little would invent buy prices."""
    set_start_date(None)
    try:
        assert miscellaneous.market_value_days() == 365, \
            f"got {miscellaneous.market_value_days()}"
    finally:
        set_start_date(START)


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


def test_the_requested_window_is_the_one_the_run_needs():
    set_start_date(START)
    fake = use_fake(lambda url: FakeResponse({"it": [{"dt": 1, "mv": 100}]}))

    try:
        leagues.player_marketvalue("token", "755")
    finally:
        leagues.clear_caches()

    assert fake.urls[0].endswith("/marketValue/365") is False, \
        f"still asking for a full year: {fake.urls[0]}"
    assert "/marketValue/31" in fake.urls[0], f"got {fake.urls[0]}"


def test_a_window_kickbase_rejects_falls_back_to_a_year():
    set_start_date(START)

    def handler(url):
        if url.endswith("/marketValue/365"):
            return FakeResponse({"it": [{"dt": 1, "mv": 100}]})
        return FakeResponse({}, status_code=400)

    fake = use_fake(handler)

    try:
        history = leagues.player_marketvalue("token", "755")
    finally:
        leagues.clear_caches()

    assert history == [{"dt": 1, "mv": 100}], f"expected the fallback history, got {history}"
    assert fake.urls[-1].endswith("/marketValue/365"), f"got {fake.urls}"


def test_the_fallback_is_remembered_for_the_rest_of_the_run():
    """Otherwise a rejected window costs one wasted request per player, not one per run."""
    set_start_date(START)

    def handler(url):
        if url.endswith("/marketValue/365"):
            return FakeResponse({"it": [{"dt": 1, "mv": 100}]})
        return FakeResponse({}, status_code=400)

    fake = use_fake(handler)

    try:
        leagues.player_marketvalue("token", "755")
        after_first = len(fake.urls)
        leagues.player_marketvalue("token", "756")
    finally:
        leagues.clear_caches()

    assert after_first == 2, f"expected the rejected window plus the fallback, got {fake.urls}"
    assert len(fake.urls) == 3, f"the second player probed the short window again: {fake.urls}"


def test_a_history_that_cannot_be_read_at_all_raises():
    """Silently returning an empty history would invent buy prices of zero."""
    from backend import exceptions

    set_start_date(START)
    use_fake(lambda url: FakeResponse({}, status_code=500))

    try:
        leagues.player_marketvalue("token", "755")
    except exceptions.KickbaseException:
        pass
    else:
        raise AssertionError("expected a KickbaseException")
    finally:
        leagues.clear_caches()
        http.reset_session()


def test_a_server_error_on_the_short_window_still_tries_the_full_year():
    """A 500 is the likeliest way Kickbase says "I do not serve that".

    The only evidence in this repository of how it answers for a resource it does not
    have is the note in competitions.get_team_overview(): team ids 33 and 38 give a 500,
    not a 404. So the shorter window - which nobody has been able to verify against the
    live API - may well be rejected the same way, and reading that as an outage would
    make the fallback unreachable.

    The cost of being wrong is one extra request per run; the cost of not trying would be
    every run failing until somebody reads the log.
    """
    from backend import exceptions

    set_start_date(START)
    fake = use_fake(lambda url: FakeResponse({}, status_code=500))

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

    assert len(fake.urls) == 2, f"expected the short window and then the year, got {fake.urls}"
    assert fake.urls[-1].endswith("/marketValue/365"), f"got {fake.urls}"


def test_a_server_error_on_the_full_year_is_a_real_outage():
    """Once the widest window fails there is nothing left to try, so the run must stop.

    Swallowing this would hand every caller an empty history, and an empty history is
    what invents buy prices of zero.
    """
    from backend import exceptions

    set_start_date(START)
    fake = use_fake(lambda url: FakeResponse({}, status_code=503))

    try:
        leagues.player_marketvalue("token", "755", days=365)
    except exceptions.ApiUnavailableException:
        pass
    except Exception as e:
        raise AssertionError(f"expected ApiUnavailableException, got {type(e).__name__}: {e}")
    else:
        raise AssertionError("expected ApiUnavailableException")
    finally:
        leagues.clear_caches()
        http.reset_session()

    assert len(fake.urls) == 1, f"there is no wider window to fall back to: {fake.urls}"


def test_an_expired_token_is_never_read_as_an_unserved_window():
    """Widening the window would spend a second request on a token that is simply gone."""
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

    assert len(fake.urls) == 1, f"an auth failure must not be retried wider: {fake.urls}"


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
                    leagues.ranking, competitions.match_days)
        main.DATA_DIR = data_dir
        miscellaneous.DATA_DIR = data_dir
        miscellaneous.TIMESTAMP_DIR = ts_dir

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
    print("market_value_days()")
    check("a young season still asks for the delta window", test_a_young_season_still_asks_for_the_delta_window)
    check("a long season asks back to START_DATE", test_a_long_season_asks_back_to_the_start_date)
    check("the window never exceeds a year", test_the_window_never_exceeds_a_year)
    check("a missing START_DATE falls back to a year", test_a_missing_start_date_falls_back_to_the_full_year)

    print("\nplayer_marketvalue()")
    check("the requested window is the one the run needs", test_the_requested_window_is_the_one_the_run_needs)
    check("a rejected window falls back to a year", test_a_window_kickbase_rejects_falls_back_to_a_year)
    check("the fallback is remembered for the run", test_the_fallback_is_remembered_for_the_rest_of_the_run)
    check("an unreadable history raises", test_a_history_that_cannot_be_read_at_all_raises)
    check("a 5xx on the short window still tries the year", test_a_server_error_on_the_short_window_still_tries_the_full_year)
    check("a 5xx on the full year is a real outage", test_a_server_error_on_the_full_year_is_a_real_outage)
    check("an expired token is never read as an unserved window", test_an_expired_token_is_never_read_as_an_unserved_window)

    print("\nteam_value_per_match_day()")
    check("one ranking request per played match day", test_one_ranking_request_per_played_match_day)
    check("future match days are not requested", test_future_match_days_are_not_requested)
    check("every manager still gets their own team value", test_every_manager_still_gets_their_own_team_value)
    check("unplayed match days stay at zero", test_unplayed_match_days_stay_at_zero)
    check("a manager missing from a ranking has no team value", test_a_manager_missing_from_a_ranking_has_no_team_value)

    total, passed = len(PASSED), sum(PASSED)
    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)
