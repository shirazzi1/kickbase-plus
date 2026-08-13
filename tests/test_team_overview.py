"""Tests for finding the teams of a competition.

There is no endpoint listing them, so team ids used to be probed one by one: the
competition has 18 teams and the probe covers ids 2 to 100, so 97 requests found 18
teams, six times a day.

Now three sources are tried, cheapest first - the ids remembered from an earlier run, the
ids in the competition's matchday schedule, and only then the probe. Every one of them is
checked against the result: a source that does not produce a full competition is not
believed, and the next one runs.

    ./venv/bin/python tests/test_team_overview.py
"""

import json
import sys
import tempfile

from datetime import datetime, timedelta, timezone
from os import makedirs, path

sys.path.insert(0, path.dirname(path.dirname(path.abspath(__file__))))

from backend import miscellaneous
from backend.kickbase import http
from backend.kickbase.v4 import competitions

### ===============================================================================

PASSED = []


def check(name, fn):
    ### Writes go to a temporary directory, never the real data directory
    with tempfile.TemporaryDirectory() as tmp:
        original = (miscellaneous.DATA_DIR, miscellaneous.TIMESTAMP_DIR,
                    miscellaneous.LAST_GOOD_DIR, miscellaneous.HISTORY_DIR,
                    competitions.TEAM_CACHE_DIR)
        miscellaneous.DATA_DIR = tmp
        miscellaneous.TIMESTAMP_DIR = path.join(tmp, "timestamps")
        ### Every write snapshots the file it replaces; without this the snapshots land
        ### in the repository's own data directory
        miscellaneous.LAST_GOOD_DIR = path.join(tmp, "last-good")
        miscellaneous.HISTORY_DIR = path.join(tmp, "history")
        ### The remembered team ids survive runs on purpose, so they need redirecting too
        competitions.TEAM_CACHE_DIR = path.join(tmp, "teams")
        competitions.clear_caches()
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
        finally:
            (miscellaneous.DATA_DIR, miscellaneous.TIMESTAMP_DIR,
             miscellaneous.LAST_GOOD_DIR, miscellaneous.HISTORY_DIR,
             competitions.TEAM_CACHE_DIR) = original
            competitions.clear_caches()


### The 18 team ids that "exist" in the fake competition, with the holes the real one has
REAL_TEAMS = {team_id: f"Team {team_id}" for team_id in
              (2, 3, 4, 5, 6, 7, 8, 9, 10, 13, 14, 15, 18, 28, 29, 40, 43, 77)}


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.headers = {}

    def json(self):
        return self._payload


def matchday_payload(team_ids, matches_per_day=9):
    """A matchdays response pairing the given team ids up, the way the real one does."""
    ordered = sorted(team_ids)
    matches = [{"t1": str(ordered[i]), "t2": str(ordered[i + 1]), "dt": "2026-08-01T18:30:00Z"}
               for i in range(0, len(ordered) - 1, 2)][:matches_per_day]

    return {"day": 1, "it": [{"day": 1, "it": matches}]}


class FakeApi:
    """Stands in for the pooled session and answers the team profile and matchday calls."""

    def __init__(self, delay=0, matchdays=None, broken_teams=()):
        self.urls = []
        self.delay = delay
        self.matchdays = matchdays
        self.broken_teams = set(broken_teams)

    @property
    def probed_team_ids(self):
        """The team ids whose profile was asked for."""
        return [int(u.rstrip("/").split("/teams/")[1].split("/")[0])
                for u in self.urls if "/teams/" in u]

    @property
    def matchday_calls(self):
        return sum(1 for u in self.urls if u.endswith("/matchdays"))

    def get(self, url, headers=None, timeout=None):
        import time

        self.urls.append(url)
        if self.delay:
            time.sleep(self.delay)

        if url.endswith("/matchdays"):
            if self.matchdays is None:
                return FakeResponse({}, status_code=500)
            return FakeResponse(self.matchdays)

        team_id = int(url.rstrip("/").split("/teams/")[1].split("/")[0])

        if team_id in REAL_TEAMS and team_id not in self.broken_teams:
            return FakeResponse({
                "tid": str(team_id),
                "tn": REAL_TEAMS[team_id],
                "it": [{"i": f"p{team_id}", "n": "Player"}],
            })

        ### A team id that does not exist. The probe reads the 404 as "no such team",
        ### which is the one HTTP error this project treats as an answer.
        return FakeResponse({}, status_code=404)


def with_api(api, fn):
    http.reset_session(api)
    try:
        return fn()
    finally:
        http.reset_session()


def remember(team_ids, age_hours=0):
    """Write a remembered team id list of a given age, as an earlier run would have."""
    makedirs(competitions.TEAM_CACHE_DIR, exist_ok=True)
    fetched_at = datetime.now(timezone.utc) - timedelta(hours=age_hours)

    with open(competitions._team_cache_path(1), "w") as f:
        json.dump({"fetchedAt": fetched_at.isoformat(),
                   "teamIds": [str(t) for t in team_ids]}, f)


### ===============================================================================
### The probe, which is still the source of last resort
### ===============================================================================


def test_finds_every_existing_team():
    """Nothing is remembered and the matchdays cannot be read, so this is the probe."""
    api = FakeApi()
    teams = with_api(api, lambda: competitions.get_team_overview("token"))

    found = {t["teamName"] for t in teams}
    assert found == set(REAL_TEAMS.values()), f"expected {len(REAL_TEAMS)}, got {len(found)}"


def test_keeps_teams_in_id_order():
    """STATIC_teams.json should not reshuffle between runs."""
    api = FakeApi()
    teams = with_api(api, lambda: competitions.get_team_overview("token"))

    ids = [int(t["teamId"]) for t in teams]
    assert ids == sorted(ids), f"teams came back out of order: {ids}"


def test_still_skips_the_broken_team_ids():
    """33 and 38 return 500s and were skipped before."""
    api = FakeApi()
    with_api(api, lambda: competitions.get_team_overview("token"))

    probed = set(api.probed_team_ids)
    assert 33 not in probed, "team id 33 should be skipped"
    assert 38 not in probed, "team id 38 should be skipped"


def test_probes_concurrently():
    """99 probes to find 18 teams must not run one after another."""
    import time

    delay = 0.01
    api = FakeApi(delay=delay)

    start = time.time()
    with_api(api, lambda: competitions.get_team_overview("token"))
    elapsed = time.time() - start

    sequential = delay * len(api.urls)
    assert elapsed < sequential / 3, \
        f"took {elapsed:.2f}s, sequential would be {sequential:.2f}s - not concurrent"


### ===============================================================================
### The matchday schedule as the team list
### ===============================================================================


def test_the_matchday_schedule_replaces_the_probe():
    api = FakeApi(matchdays=matchday_payload(REAL_TEAMS))
    teams = with_api(api, lambda: competitions.get_team_overview("token"))

    assert len(teams) == len(REAL_TEAMS), f"expected every team, got {len(teams)}"
    assert set(api.probed_team_ids) == set(REAL_TEAMS), \
        f"expected only the scheduled teams to be asked for, got {sorted(set(api.probed_team_ids))}"


def test_the_matchday_schedule_costs_one_request():
    api = FakeApi(matchdays=matchday_payload(REAL_TEAMS))
    with_api(api, lambda: competitions.get_team_overview("token"))

    assert len(api.urls) == len(REAL_TEAMS) + 1, \
        f"expected 18 team profiles plus one matchday call, got {len(api.urls)}"


def test_match_days_reuses_the_response_the_team_list_already_fetched():
    """Which is what makes the shortcut free: the run fetches the matchdays anyway."""
    api = FakeApi(matchdays=matchday_payload(REAL_TEAMS))

    def run():
        competitions.get_team_overview("token")
        return competitions.match_days("token")

    current_day, days = with_api(api, run)

    assert api.matchday_calls == 1, f"the matchdays were fetched {api.matchday_calls} times"
    assert current_day == 1, f"got {current_day}"
    assert days and days[0]["day"] == 1, f"got {days}"


def test_an_incomplete_schedule_falls_back_to_the_probe():
    """A schedule naming only half the competition must not cost the other half."""
    half = sorted(REAL_TEAMS)[:8]
    api = FakeApi(matchdays=matchday_payload(half))

    teams = with_api(api, lambda: competitions.get_team_overview("token"))

    assert len(teams) == len(REAL_TEAMS), \
        f"expected the probe to find every team after the short schedule, got {len(teams)}"
    assert 77 in set(api.probed_team_ids), "expected the probe to run"


def test_a_schedule_in_an_unexpected_shape_falls_back_to_the_probe():
    """The "t1"/"t2" key names are an assumption; being wrong may only cost requests."""
    api = FakeApi(matchdays={"day": 1, "it": [{"day": 1, "it": [
        {"homeTeam": "2", "awayTeam": "3", "dt": "2026-08-01T18:30:00Z"}]}]})

    teams = with_api(api, lambda: competitions.get_team_overview("token"))

    assert len(teams) == len(REAL_TEAMS), f"expected the probe to find every team, got {len(teams)}"


### ===============================================================================
### The remembered ids
### ===============================================================================


def test_a_fresh_remembered_list_is_used_as_it_is():
    remember(REAL_TEAMS)
    api = FakeApi(matchdays=matchday_payload(REAL_TEAMS))

    teams = with_api(api, lambda: competitions.get_team_overview("token"))

    assert len(teams) == len(REAL_TEAMS), f"expected every team, got {len(teams)}"
    assert len(api.urls) == len(REAL_TEAMS), \
        f"expected 18 requests and nothing else, got {len(api.urls)}"
    assert api.matchday_calls == 0, "a remembered list must not cost a matchday call"


def test_a_successful_run_remembers_the_ids_it_used():
    api = FakeApi()
    with_api(api, lambda: competitions.get_team_overview("token"))

    with open(competitions._team_cache_path(1)) as f:
        entry = json.load(f)

    assert sorted(int(t) for t in entry["teamIds"]) == sorted(REAL_TEAMS), \
        f"expected the found teams to be remembered, got {entry['teamIds']}"


def test_a_list_older_than_a_day_is_asked_again():
    """A promoted team has to show up without anyone deleting a file."""
    remember([2, 3], age_hours=competitions.TEAM_CACHE_MAX_AGE_HOURS + 1)
    api = FakeApi(matchdays=matchday_payload(REAL_TEAMS))

    teams = with_api(api, lambda: competitions.get_team_overview("token"))

    assert len(teams) == len(REAL_TEAMS), f"expected every team, got {len(teams)}"
    assert api.matchday_calls == 1, "expected the competition to be asked again"


def test_a_remembered_list_missing_a_team_is_not_believed():
    remember(sorted(REAL_TEAMS)[:10])
    api = FakeApi(matchdays=matchday_payload(REAL_TEAMS))

    teams = with_api(api, lambda: competitions.get_team_overview("token"))

    assert len(teams) == len(REAL_TEAMS), \
        f"expected the next source to fill the gap, got {len(teams)}"


def test_an_unreadable_remembered_list_is_ignored():
    makedirs(competitions.TEAM_CACHE_DIR, exist_ok=True)
    with open(competitions._team_cache_path(1), "w") as f:
        f.write("{not json")

    api = FakeApi(matchdays=matchday_payload(REAL_TEAMS))
    teams = with_api(api, lambda: competitions.get_team_overview("token"))

    assert len(teams) == len(REAL_TEAMS), f"expected every team, got {len(teams)}"


### ===============================================================================
### What must not happen
### ===============================================================================


def test_a_team_that_answers_500_does_not_cost_the_others():
    """Not even when it drops the count below what a competition is expected to have.

    There is nothing better left to try after the probe, and 17 teams is worth more than a
    stage that fails over one team profile.
    """
    api = FakeApi(broken_teams={77})
    teams = with_api(api, lambda: competitions.get_team_overview("token"))

    assert len(teams) == len(REAL_TEAMS) - 1, f"expected 17 teams, got {len(teams)}"


def test_a_rate_limit_is_not_read_as_an_unusable_schedule():
    """Walking into 97 probes during a live rate limit spends the rest of the budget and then
    blames a competition with no teams in it."""
    from backend import exceptions

    class RateLimited(FakeApi):
        def get(self, url, headers=None, timeout=None):
            self.urls.append(url)
            if url.endswith("/matchdays"):
                return FakeResponse({}, status_code=429)
            return super().get(url, headers=headers, timeout=timeout)

    api = RateLimited()

    try:
        with_api(api, lambda: competitions.get_team_overview("token"))
    except exceptions.RateLimitedException:
        pass
    except Exception as e:
        raise AssertionError(f"expected RateLimitedException, got {type(e).__name__}: {e}")
    else:
        raise AssertionError("expected RateLimitedException")

    assert api.probed_team_ids == [], \
        f"the probe ran into the rate limit: {len(api.probed_team_ids)} team requests"


def test_an_expired_token_is_not_read_as_an_unusable_schedule():
    from backend import exceptions

    class TokenGone(FakeApi):
        def get(self, url, headers=None, timeout=None):
            self.urls.append(url)
            return FakeResponse({}, status_code=401)

    api = TokenGone()

    try:
        with_api(api, lambda: competitions.get_team_overview("token"))
    except exceptions.AuthExpiredException:
        pass
    except Exception as e:
        raise AssertionError(f"expected AuthExpiredException, got {type(e).__name__}: {e}")
    else:
        raise AssertionError("expected AuthExpiredException")

    assert api.probed_team_ids == [], \
        f"the probe ran on with a dead token: {len(api.probed_team_ids)} team requests"


def test_a_server_error_on_the_schedule_still_falls_back_to_the_probe():
    """The shortcut is optional; a broken matchday endpoint must not cost the team overview."""
    api = FakeApi(matchdays=None)  ### answers 500

    teams = with_api(api, lambda: competitions.get_team_overview("token"))

    assert len(teams) == len(REAL_TEAMS), f"expected the probe to find every team, got {len(teams)}"


def test_a_competition_with_no_teams_at_all_fails_loudly():
    """Writing an empty STATIC_teams.json would blank four later stages instead."""
    from backend import exceptions

    class NothingExists(FakeApi):
        def get(self, url, headers=None, timeout=None):
            self.urls.append(url)
            return FakeResponse({}, status_code=404)

    api = NothingExists()

    try:
        with_api(api, lambda: competitions.get_team_overview("token"))
    except exceptions.KickbaseException:
        pass
    else:
        raise AssertionError("expected a KickbaseException")

    assert not path.exists(path.join(miscellaneous.DATA_DIR, "STATIC_teams.json")), \
        "an empty team overview must not be written"


### ===============================================================================

if __name__ == "__main__":
    print("get_team_overview() - the probe")
    check("finds every existing team", test_finds_every_existing_team)
    check("keeps teams in id order", test_keeps_teams_in_id_order)
    check("still skips the broken team ids", test_still_skips_the_broken_team_ids)
    check("probes concurrently", test_probes_concurrently)

    print("\nthe matchday schedule as the team list")
    check("the matchday schedule replaces the probe", test_the_matchday_schedule_replaces_the_probe)
    check("the matchday schedule costs one request", test_the_matchday_schedule_costs_one_request)
    check("match_days() reuses the response the team list fetched",
          test_match_days_reuses_the_response_the_team_list_already_fetched)
    check("an incomplete schedule falls back to the probe",
          test_an_incomplete_schedule_falls_back_to_the_probe)
    check("a schedule in an unexpected shape falls back to the probe",
          test_a_schedule_in_an_unexpected_shape_falls_back_to_the_probe)

    print("\nthe remembered ids")
    check("a fresh remembered list is used as it is", test_a_fresh_remembered_list_is_used_as_it_is)
    check("a successful run remembers the ids it used",
          test_a_successful_run_remembers_the_ids_it_used)
    check("a list older than a day is asked again", test_a_list_older_than_a_day_is_asked_again)
    check("a remembered list missing a team is not believed",
          test_a_remembered_list_missing_a_team_is_not_believed)
    check("an unreadable remembered list is ignored", test_an_unreadable_remembered_list_is_ignored)

    print("\nwhat must not happen")
    check("a team that answers 500 does not cost the others",
          test_a_team_that_answers_500_does_not_cost_the_others)
    check("a rate limit is not read as an unusable schedule",
          test_a_rate_limit_is_not_read_as_an_unusable_schedule)
    check("an expired token is not read as an unusable schedule",
          test_an_expired_token_is_not_read_as_an_unusable_schedule)
    check("a server error on the schedule still falls back to the probe",
          test_a_server_error_on_the_schedule_still_falls_back_to_the_probe)
    check("a competition with no teams at all fails loudly",
          test_a_competition_with_no_teams_at_all_fails_loudly)

    total, passed = len(PASSED), sum(PASSED)
    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)
