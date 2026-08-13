"""
### This module holds all necessary functions to call Kickbase `/competitions/...` API endpoints.

TODO: Maybe list all functions here automatically?
"""

import logging

from concurrent.futures import ThreadPoolExecutor

from backend import exceptions, miscellaneous
from backend.kickbase import http

### -------------------------------------------------------------------

### How many team ids to probe at once. Kept modest on purpose: this runs against the
### user's own Kickbase account, and being throttled costs more than it saves.
MAX_TEAM_WORKERS = 8


def get_team_overview(token: str) -> dict:
    """### Get all team names + ID and their players.

    Args:
        token (str): The user's kkstrauth token.

    Returns:
        dict: A dictionary containing all team ids + names and players.
    """
    logging.info("Getting team overview...")

    url = "https://api.kickbase.com/v4/competitions/1/teams/{team_id}/teamprofile"

    ### There is no endpoint listing the teams of a competition, so the ids are probed.
    ### Team IDs 33 and 38 are skipped cuz they are leading to "500 Internal Server Error"
    team_ids = [team_id for team_id in range(2, 101) if team_id not in (33, 38)]

    def fetch_team(team_id):
        """Probe one team id. Returns the team info, or None if there is no such team."""
        ### Most of these ids do not exist, so a failure here is the expected answer and
        ### not worth a retry with backoff - 95 of them would turn a probe that takes
        ### seconds into one that takes minutes.
        try:
            json_response = http.get_json(url.format(team_id=team_id), token, retry=False)
        except exceptions.ApiResponseException as e:
            logging.warning(f"Failed to decode JSON for team id {team_id}: {e}")
            return None
        except exceptions.AuthExpiredException:
            ### Not a missing team: the token is gone and every further probe would fail
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

    ### Most of these ids do not exist, and each probe is almost entirely spent waiting,
    ### so they run concurrently. map keeps the results in team id order, which keeps
    ### STATIC_teams.json stable between runs.
    with ThreadPoolExecutor(max_workers=MAX_TEAM_WORKERS) as executor:
        results = list(executor.map(fetch_team, team_ids))

    all_teams = [team for team in results if team]

    logging.info("Got all teams.")

    ### Save to file
    miscellaneous.write_json_to_file(all_teams, "STATIC_teams.json")

    return all_teams


def match_days(token: str, competition_id: int = 1) -> tuple:
    """### Fetch all matches for every match day in the current season and save to JSON

    Args:
        token (str): The user's kkstrauth token
        competition_id (int): The competition ID (default: 1 which is the Bundesliga)
    
    Returns:
        tuple: A tuple containing the current match day number and a list of dictionaries. Each dictionary contains the match day number, the start date & time of the first match, and the start date & time of the last match.
    """
    url = f"https://api.kickbase.com/v4/competitions/{competition_id}/matchdays"

    match_days = []

    logging.info("Fetching match days...")

    ### A failure here used to be logged and then walked straight past, into a NameError
    ### on the next line because "response" was never assigned. It travels on now.
    response = http.get_json(url, token)

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