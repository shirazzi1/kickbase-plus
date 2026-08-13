"""
### This module holds all necessary functions to call Kickbase `/competitions/...` API endpoints.

TODO: Maybe list all functions here automatically?
"""

import json
import logging
import os
import tempfile

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from backend import exceptions, miscellaneous
from backend.kickbase import http
from backend.paths import TEAM_CACHE_DIR

### -------------------------------------------------------------------

### How many team ids to probe at once. Kept modest on purpose: this runs against the
### user's own Kickbase account, and being throttled costs more than it saves.
MAX_TEAM_WORKERS = 8

### The ids the probe of last resort walks, and the two it leaves out because they answer
### "500 Internal Server Error" rather than "no such team".
PROBE_RANGE = range(2, 101)
BROKEN_TEAM_IDS = (33, 38)

### How many teams a competition has to have for a shortcut to be believed. The Bundesliga
### has had 18 since 1965, and this is a check on the *result*, not on the candidate list:
### a shortcut that produces fewer teams than this had the wrong ids in it, whatever the
### reason, and the probe runs after all. Getting it wrong costs 18 wasted requests once;
### not checking would cost a whole team's players missing from STATIC_teams.json, which
### four later stages read.
MIN_EXPECTED_TEAMS = 18

### How long a remembered team id list is used before it is checked against the competition
### again. A day, because that is the clock a promoted or relegated team moves on - the list
### changes between seasons, not between runs.
TEAM_CACHE_MAX_AGE_HOURS = 24

### The matchday response, per competition, for the duration of the run. Two callers now:
### match_days() and the team id harvest below, which is what makes the harvest free.
_matchdays_cache = {}


def clear_caches() -> None:
    """### Empty the per-run caches held in this module.

    Called by main.py at the start of every run, next to leagues.clear_caches(). The team id
    list on disk is not touched - it survives runs on purpose, on its own daily clock.
    """
    _matchdays_cache.clear()


def get_team_overview(token: str) -> dict:
    """### Get all team names + ID and their players.

    One request per team is unavoidable: the players only come from the team profile. What
    was avoidable is the *search* for the team ids. There is no endpoint listing them, so
    this used to probe ids 2 to 100 - 97 requests, six times a day, to discover the same 18
    teams.

    Three sources of candidate ids are tried in order, cheapest first:

    1. The ids remembered from an earlier run, if they are less than a day old.
    2. The team ids in the competition's own matchday response, which the run fetches
       anyway (see `match_days()`), so this costs nothing.
    3. The probe, unchanged.

    Whatever the source, the result has to hold at least MIN_EXPECTED_TEAMS teams with
    players. It does not, the next source is tried - so a stale list after a promotion, or a
    wrong guess about the matchday response, costs requests and never data.

    If not even the probe reaches the threshold, the fullest answer any source gave is used
    anyway. There is nothing better left to try, and 17 teams is worth more than a hard
    failure over a team profile that happened to answer 500.

    Args:
        token (str): The user's kkstrauth token.

    Returns:
        dict: A dictionary containing all team ids + names and players.
    """
    logging.info("Getting team overview...")

    all_teams = []

    for source, team_ids in _candidate_team_ids(token):
        if not team_ids:
            continue

        logging.info(f"Fetching {len(team_ids)} team profile(s) ({source}).")
        teams = _fetch_teams(token, team_ids)

        if len(teams) >= MIN_EXPECTED_TEAMS:
            all_teams = teams
            break

        if len(teams) > len(all_teams):
            all_teams = teams

        logging.warning(f"{source} produced {len(teams)} team(s) with players, fewer than "
                        f"the {MIN_EXPECTED_TEAMS} a competition is expected to have, so it "
                        f"is not trusted on its own.")

    if all_teams and len(all_teams) < MIN_EXPECTED_TEAMS:
        logging.warning(f"No source produced a full competition. Using the fullest answer "
                        f"any of them gave: {len(all_teams)} team(s).")

    if not all_teams:
        raise exceptions.KickbaseException(
            "Couldn't find a single team with players in the competition, not even by "
            "probing every team id. STATIC_teams.json would have been emptied, and four "
            "later stages read it.")

    logging.info(f"Got all {len(all_teams)} teams.")

    ### Remember what worked, so the next run starts at source 1
    _remember_team_ids([team["teamId"] for team in all_teams])

    ### Save to file
    miscellaneous.write_json_to_file(all_teams, "STATIC_teams.json")

    return all_teams


def _candidate_team_ids(token: str):
    """### The team id lists to try, cheapest first.

    A generator, so a source that costs a request is only asked once the cheaper one has
    been found wanting.

    Args:
        token (str): The user's kkstrauth token.

    Yields:
        tuple: (where it came from, the ids) - the first element is for the log.
    """
    remembered = _remembered_team_ids()
    if remembered:
        yield "remembered from an earlier run", remembered

    yield "from the competition's matchdays", team_ids_from_matchdays(token)

    yield ("probed", [team_id for team_id in PROBE_RANGE if team_id not in BROKEN_TEAM_IDS])


def _fetch_teams(token: str, team_ids: list) -> list:
    """### Ask for the team profile of every given id and keep the ones that exist.

    Args:
        token (str): The user's kkstrauth token.
        team_ids (list): The ids to ask for.

    Returns:
        list: One entry per team that exists and has players, in team id order.
    """
    url = "https://api.kickbase.com/v4/competitions/1/teams/{team_id}/teamprofile"

    def fetch_team(team_id):
        """Ask for one team. Returns the team info, or None if there is no such team."""
        ### When this is the probe most of these ids do not exist, so a failure here is the
        ### expected answer and not worth a retry with backoff - 95 of them would turn a
        ### probe that takes seconds into one that takes minutes.
        try:
            json_response = http.get_json(url.format(team_id=team_id), token, retry=False)
        except exceptions.ApiResponseException as e:
            logging.warning(f"Failed to decode JSON for team id {team_id}: {e}")
            return None
        except exceptions.AuthExpiredException:
            ### Not a missing team: the token is gone and every further request would fail
            ### the same way. Let it out.
            raise
        except exceptions.HttpException as e:
            logging.debug(f"Failed to get team id {team_id}: {e}")
            return None

        ### Check if team has players
        if not json_response.get("it"):
            return None

        ### Get team id, name, and players
        return {
            "teamId": json_response["tid"],
            "teamName": json_response["tn"],
            "players": json_response["it"],
        }

    ### Each request is almost entirely spent waiting, so they run concurrently. map keeps
    ### the results in the order the ids came in, which keeps STATIC_teams.json stable
    ### between runs.
    with ThreadPoolExecutor(max_workers=MAX_TEAM_WORKERS) as executor:
        results = list(executor.map(fetch_team, sorted(team_ids, key=_sort_key)))

    return [team for team in results if team]


def _sort_key(team_id):
    """### Sort team ids numerically where they are numbers, and last where they are not.

    The ids arrive as strings from the API and as ints from the probe range, and
    STATIC_teams.json should not reshuffle between runs just because the source changed.

    Args:
        team_id: A team id.

    Returns:
        tuple: A key that orders numeric ids by value.
    """
    text = str(team_id)

    return (0, int(text), "") if text.isdigit() else (1, 0, text)


def team_ids_from_matchdays(token: str, competition_id: int = 1) -> list:
    """### The team ids playing in a competition, read off its matchday schedule.

    Every team plays on every matchday, so the schedule names all of them. This is the
    closest thing to a team list the API is known to offer, and the response is one the run
    fetches anyway - `match_days()` shares this module's cache - so reading it costs nothing.

    ASSUMPTION: that a match names its two teams in "t1" and "t2". Unverifiable from here
    (this project has no credentials in the test environment) and deliberately not relied
    on: anything else yields no ids at all, and `get_team_overview()` then probes exactly as
    it did before. A partial answer is caught by its MIN_EXPECTED_TEAMS check.

    Args:
        token (str): The user's kkstrauth token.
        competition_id (int): The competition ID (default: 1 which is the Bundesliga).

    Returns:
        list: The team ids as the API spells them, in ascending numeric order. Empty if the
            response does not carry them.
    """
    try:
        response = _matchdays_response(token, competition_id)
    except exceptions.KickbaseException as e:
        ### A shortcut is not worth failing a run over. The probe below does not need this.
        logging.warning(f"Could not read the matchdays to find the team ids ({e}). Falling "
                        "back to probing them.")
        return []

    team_ids = set()

    for match_day in response.get("it") or []:
        for match in match_day.get("it") or []:
            for key in ("t1", "t2"):
                team_id = match.get(key)
                if team_id is not None and str(team_id):
                    team_ids.add(str(team_id))

    if not team_ids:
        logging.info("The matchday response named no team ids, so they get probed.")

    return sorted(team_ids, key=_sort_key)


def _team_cache_path(competition_id: int) -> str:
    """### Where the remembered team ids of one competition live.

    Args:
        competition_id (int): The competition ID.

    Returns:
        str: The absolute path, whose directory may not exist yet.
    """
    return os.path.join(TEAM_CACHE_DIR, f"teams-{int(competition_id)}.json")


def _remembered_team_ids(competition_id: int = 1) -> list:
    """### The team ids an earlier run found, if they are still fresh enough to trust.

    Format, `data/teams/teams-<competition_id>.json`:

    ```json
    {"fetchedAt": "2026-08-13T09:12:44.101+02:00", "teamIds": ["2", "3", ...]}
    ```

    `fetchedAt` is the invalidation: past TEAM_CACHE_MAX_AGE_HOURS the list is ignored and
    the competition is asked again, so a promoted team appears without anyone deleting a
    file. Nothing in here can fail a run - every problem is answered with an empty list,
    which means "find them the expensive way".

    Args:
        competition_id (int): The competition ID.

    Returns:
        list: The remembered ids, or an empty list.
    """
    cache_path = _team_cache_path(competition_id)

    try:
        with open(cache_path, "r") as f:
            entry = json.load(f)
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError) as e:
        logging.debug(f"Remembered team ids are unreadable ({type(e).__name__}: {e}).")
        return []

    if not isinstance(entry, dict) or not isinstance(entry.get("teamIds"), list):
        logging.debug("Remembered team ids are not in the expected format.")
        return []

    try:
        fetched_at = datetime.fromisoformat(entry["fetchedAt"])
    except (KeyError, TypeError, ValueError):
        logging.debug("Remembered team ids carry no readable date.")
        return []

    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.astimezone()

    age_hours = (datetime.now(timezone.utc) - fetched_at).total_seconds() / 3600

    if age_hours >= TEAM_CACHE_MAX_AGE_HOURS or age_hours < 0:
        logging.info(f"The remembered team ids are {age_hours:.1f} hours old, so the "
                     "competition gets asked again.")
        return []

    return [str(team_id) for team_id in entry["teamIds"] if str(team_id)]


def _remember_team_ids(team_ids: list, competition_id: int = 1) -> None:
    """### Write down the ids that produced a full team overview.

    Written atomically, and a failure is logged and shrugged off: the run has its teams,
    and the cost of not remembering them is next run's requests, not this run's data.

    Args:
        team_ids (list): The ids to remember.
        competition_id (int): The competition ID.
    """
    entry = {
        "fetchedAt": datetime.now().astimezone().isoformat(),
        "teamIds": [str(team_id) for team_id in team_ids],
    }

    try:
        os.makedirs(TEAM_CACHE_DIR, exist_ok=True)
        cache_path = _team_cache_path(competition_id)

        ### The temporary file sits in the target directory, because os.replace() is only
        ### atomic within one filesystem and this directory is a volume mount
        handle, temp_path = tempfile.mkstemp(dir=TEAM_CACHE_DIR, prefix=".teams.",
                                            suffix=".tmp")

        try:
            with os.fdopen(handle, "w") as f:
                json.dump(entry, f)
            os.replace(temp_path, cache_path)
        except Exception:
            try:
                os.remove(temp_path)
            except OSError:
                pass
            raise
    except Exception as e:
        logging.warning(f"Could not remember the team ids, carrying on: "
                        f"{type(e).__name__}: {e}")


def _matchdays_response(token: str, competition_id: int = 1) -> dict:
    """### The raw matchday response, fetched once per run.

    Args:
        token (str): The user's kkstrauth token.
        competition_id (int): The competition ID.

    Returns:
        dict: The decoded response.
    """
    if competition_id in _matchdays_cache:
        return _matchdays_cache[competition_id]

    url = f"https://api.kickbase.com/v4/competitions/{competition_id}/matchdays"

    ### A failure here used to be logged and then walked straight past, into a NameError
    ### on the next line because "response" was never assigned. It travels on now.
    response = http.get_json(url, token)

    _matchdays_cache[competition_id] = response

    return response


def match_days(token: str, competition_id: int = 1) -> tuple:
    """### Fetch all matches for every match day in the current season and save to JSON

    Args:
        token (str): The user's kkstrauth token
        competition_id (int): The competition ID (default: 1 which is the Bundesliga)

    Returns:
        tuple: A tuple containing the current match day number and a list of dictionaries. Each dictionary contains the match day number, the start date & time of the first match, and the start date & time of the last match.
    """
    match_days = []

    logging.info("Fetching match days...")

    response = _matchdays_response(token, competition_id)

    current_match_day = response["day"]

    if response["it"]:
        for match_day in response["it"]:
            first_match = match_day["it"][0]["dt"] ### Start date & time of the first match
            last_match = match_day["it"][-1]["dt"] ### Start date & time of the last match

            match_days.append({
                "day": match_day["day"],
                "firstMatch": first_match,
                "lastMatch": last_match,
            })

    logging.info("Match days fetched.")

    ### Save to file
    miscellaneous.write_json_to_file(match_days, "match_days.json")

    ### TODO: Timestamp needed here?

    return current_match_day, match_days
