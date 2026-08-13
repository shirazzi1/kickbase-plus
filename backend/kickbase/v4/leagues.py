"""
### This module holds all necessary functions to call Kickbase `/leagues/...` API endpoints.

TODO: Maybe list all functions here automatically?
"""

import logging
import requests

from concurrent.futures import ThreadPoolExecutor

from backend import exceptions, miscellaneous
from backend.kickbase.endpoints.leagues import League_Info, Market_Players

### -------------------------------------------------------------------

### Per-run caches.
### main.py walks every player twice, in market_value_changes() and in
### taken_free_players(), and pages the activity feed three times. None of that changes
### during a run, so each response is fetched once and reused.
### One run is one process, so these live for the lifetime of the process. Call
### clear_caches() to start over.
### How many player lookups to run at once. Kept modest on purpose: this runs against
### the user's own Kickbase account, and being throttled costs more than it saves.
MAX_PLAYER_WORKERS = 8

### Seconds to wait for a write to Kickbase. Short on purpose: the user is waiting in
### front of the field, and no other call in this module has a timeout at all.
OFFER_TIMEOUT = 15

### Seconds to wait for a market read. get_market() is the confirming read-back app.py
### calls after place_offer()/remove_offer() have already moved money - a hang there
### would block the response indefinitely in exactly the window app.py's 502 "could not
### confirm" outcome exists for. main.py's plain market scrape shares the same call and
### this bound is safe for it too. The other read functions in this module are
### deliberately left untimed; giving them one as well is a separate concern.
MARKET_TIMEOUT = 15

_player_statistics_cache = {}
_player_marketvalue_cache = {}
_transfers_cache = {}
_user_stats_cache = {}
_user_performance_cache = {}
_battles_cache = {}


def clear_caches() -> None:
    """### Empty the per-run API caches."""
    _player_statistics_cache.clear()
    _player_marketvalue_cache.clear()
    _transfers_cache.clear()
    _user_stats_cache.clear()
    _user_performance_cache.clear()
    _battles_cache.clear()

    miscellaneous.clear_caches()


def get_league_list(token: str) -> list:
    """Get a list of all leagues the user is in.

    Args:
        user_token (str): The user token to authenticate the user.

    Returns:
        list: List of all leagues the user is in.
    """
    url = "https://api.kickbase.com/v4/leagues/selection"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Cookie": f"kkstrauth={token};",
    }

    ### Send GET request
    try:
        json_response = requests.get(url, headers=headers).json()
    except:
        raise exceptions.KickbaseException("An exception was raised.") # TODO: Change
    
    ### Iterating over the json response, where each entry is expected to be a dictionary. For each entry, it creates a new Leagues_Info object.
    league_list = [League_Info(entry) for entry in json_response["it"]]

    return league_list


def get_market(token: str, league_id: str):
    """
    ### Get the current players on the market in the league

    Given a timeout (MARKET_TIMEOUT), unlike the other read functions in this module.
    app.py calls this twice per write - once to check the market before place_offer()/
    remove_offer(), once as the read-back that confirms the write afterwards - and that
    second call runs after Kickbase has already accepted the write. A hung socket there
    would block the response indefinitely in exactly the window app.py's 502 "could not
    confirm" outcome exists for. main.py's plain scrape goes through the same call and
    tolerates the bound fine.

    Expected response:
    ```json
    {
        "it": [ ... ],
        "nps": 41,
        "tv": 69420,
        "mvud": "2023-11-24T21:00:00Z",
        "dt": "2023-11-24T19:30:00Z",
        "day": 12   
    }
    ```
    Obviously the "it" list is filled with all players on the market.
    """
    url = f"https://api.kickbase.com/v4/leagues/{league_id}/market"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Cookie": f"kkstrauth={token};",
    }

    ### Send GET request to get all free players in the given league
    try:
        json_response = requests.get(url, headers=headers, timeout=MARKET_TIMEOUT).json()
        ### Create a new object for every entry in the json_response["it"] list. Kept
        ### inside the try: a response with no "it" key (an expired token, seen live) is
        ### as much a failed read as the request itself, and both callers of this
        ### function - app.py and main.py - need one exception to handle, not a request
        ### failure here and a bare KeyError there.
        players_on_market = [Market_Players(player) for player in json_response["it"]]
    except (requests.RequestException, ValueError, KeyError, TypeError):
        raise exceptions.NotificatonException("Notification failed! Please check your Discord Webhook URL.") # TODO: Change exception

    return players_on_market


### German for the Kickbase error codes seen live. "UnderpayNotAllowed" is accurate but
### not a sentence to put in front of a user.
OFFER_ERRORS = {
    5080: "Das Gebot liegt unter dem Marktwert.",
    6: "Kickbase hat das Gebot als ungültig abgewiesen.",
}


def _offer_headers(token: str) -> dict:
    """### The headers every offer call sends."""
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Cookie": f"kkstrauth={token};",
    }


def _offer_failure(response) -> "exceptions.KickbaseWriteException":
    """### Turn a refused offer write into an exception worth showing a user.

    Two corrections happen here, both of them things the live API forced:

    Kickbase reports a refused bid with HTTP 500 - a bid below the market value comes
    back as {"err": 5080, "errMsg": "UnderpayNotAllowed"}. Forwarding that status would
    blame the server for the user's typo and bury real outages among ordinary
    rejections, so a 5xx carrying an error code becomes a 400. A 5xx without one is a
    genuine outage and becomes a 502. A 4xx is already right and passes through.

    And the message comes from "errMsg". "err" is a numeric code; reading it as a message
    would show the user "5080".
    """
    try:
        body = response.json()
    except ValueError:
        body = None

    if not isinstance(body, dict):
        body = {}

    code = body.get("err")
    ### The mapping first, then Kickbase's own English, then the bare status. Never "err".
    message = (OFFER_ERRORS.get(code)
               or body.get("errMsg")
               or f"Kickbase antwortete mit HTTP {response.status_code}.")

    if response.status_code < 500:
        status = response.status_code
    elif code is not None:
        status = 400
    else:
        status = 502

    return exceptions.KickbaseWriteException(status, message)


def place_offer(token: str, league_id: str, player_id: str, price: int) -> dict:
    """### Place a bid on a player listed on the transfer market.

    The first write in this project. It deliberately does not follow the bare `except:`
    around `.json()` that the reads in this module use: that pattern reports every
    failure as a Discord webhook problem, and a bid needs its actual reason.

    Args:
        token (str): The user's kkstrauth token.
        league_id (str): The league the player is listed in.
        player_id (str): The player to bid on.
        price (int): The bid, in whole euros.

    Raises:
        exceptions.KickbaseWriteException: If Kickbase rejects the bid or cannot be
            reached. Carries the HTTP status and Kickbase's own message.

    Returns:
        dict: The response body, or an empty dict when there is none.
    """
    url = f"https://api.kickbase.com/v4/leagues/{league_id}/market/{player_id}/offers"

    try:
        response = requests.post(url, json={"price": price},
                                 headers=_offer_headers(token), timeout=OFFER_TIMEOUT)
    except requests.exceptions.RequestException as e:
        ### The exception message reaches the user, so the urllib3 detail goes to the log
        ### instead of into the sentence they read
        logging.error(f"Kickbase write to {url} failed: {e}")
        raise exceptions.KickbaseWriteException(
            504, "Kickbase ist nicht erreichbar. Bitte versuche es in einem Moment erneut."
        ) from e

    if response.status_code >= 400:
        raise _offer_failure(response)

    try:
        return response.json() if response.content else {}
    except ValueError:
        return {}


def remove_offer(token: str, league_id: str, player_id: str, own_user_id: str) -> None:
    """### Withdraw the user's own bid on a player.

    The offer is addressed by the user's own id, because that is the only identifier the
    API exposes for it: "ofs" entries carry no offer id, and the POST that places a bid
    hands the user id back as "ofi". DELETE on the bare collection answers 405, so the id
    is not optional. A user holds at most one offer per player, which is what makes
    keying by user sufficient.

    Args:
        token (str): The user's kkstrauth token.
        league_id (str): The league the player is listed in.
        player_id (str): The player whose bid is withdrawn.
        own_user_id (str): The logged in user's ID, which identifies their offer.

    Raises:
        exceptions.KickbaseWriteException: If Kickbase rejects the removal or cannot be
            reached.
    """
    url = (f"https://api.kickbase.com/v4/leagues/{league_id}/market/{player_id}"
           f"/offers/{own_user_id}")

    try:
        response = requests.delete(url, headers=_offer_headers(token), timeout=OFFER_TIMEOUT)
    except requests.exceptions.RequestException as e:
        ### The exception message reaches the user, so the urllib3 detail goes to the log
        ### instead of into the sentence they read
        logging.error(f"Kickbase write to {url} failed: {e}")
        raise exceptions.KickbaseWriteException(
            504, "Kickbase ist nicht erreichbar. Bitte versuche es in einem Moment erneut."
        ) from e

    if response.status_code >= 400:
        raise _offer_failure(response)


def prefetch_players(token: str, league_id: str, player_ids) -> None:
    """### Fetch statistics and market value history for many players at once.

    market_value_changes() needs both for every player in the competition, which is two
    requests each and around a thousand in total. They are independent and almost
    entirely spent waiting, so they run concurrently and fill the same caches the
    individual functions use. Those functions then find their answers already there.

    Args:
        token (str): The user's kkstrauth token.
        league_id (str): The league to fetch statistics for.
        player_ids (iterable): The player IDs to fetch.
    """
    ids = sorted({str(player_id) for player_id in player_ids})

    missing_statistics = [p for p in ids if (league_id, p) not in _player_statistics_cache]
    missing_marketvalues = [p for p in ids if p not in _player_marketvalue_cache]

    if not missing_statistics and not missing_marketvalues:
        return

    logging.debug(f"Prefetching {len(missing_statistics)} player statistic(s) "
                  f"and {len(missing_marketvalues)} market value history/histories...")

    with ThreadPoolExecutor(max_workers=MAX_PLAYER_WORKERS) as executor:
        futures = [executor.submit(player_statistics, token, league_id, p)
                   for p in missing_statistics]
        futures += [executor.submit(player_marketvalue, token, p)
                    for p in missing_marketvalues]

        ### Surface any exception rather than letting it disappear into the pool
        for future in futures:
            future.result()


def player_statistics(token: str, league_id: str, player_id: str):
    """
    ### Get the statistics of a given player.

    Cached per league and player for the duration of the run. The response carries
    league specific data (ownership in "opl"), so the league is part of the key.
    """
    cache_key = (league_id, str(player_id))
    if cache_key in _player_statistics_cache:
        return _player_statistics_cache[cache_key]

    url = f"https://api.kickbase.com/v4/competitions/1/players/{player_id}?leagueId={league_id}"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        ### The status note ("stxt") is the only prose in this response and defaults to
        ### English. The frontend is German, so ask for German. Kickbase localises on this
        ### header only: a "lang"/"locale" query parameter is ignored.
        "Accept-Language": "de-DE,de;q=0.9",
        "Cookie": f"kkstrauth={token};",
    }

    ### Send GET request to get the market value changes of ALL players in the league
    try:
        json_response = requests.get(url, headers=headers).json()
    except:
        raise exceptions.NotificatonException("Notification failed! Please check your Discord Webhook URL.") # TODO: Change exception

    _player_statistics_cache[cache_key] = json_response

    return json_response


def player_marketvalue(token: str, player_id: str):
    """
    ### Get the market value history of a given player.

    Cached per player for the duration of the run.
    """
    cache_key = str(player_id)
    if cache_key in _player_marketvalue_cache:
        return _player_marketvalue_cache[cache_key]

    url_1year = f"https://api.kickbase.com/v4/competitions/1/players/{player_id}/marketValue/365"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Cookie": f"kkstrauth={token};",
    }

    ### Send GET request to get the market value changes of ALL players in the league
    try:
        json_response = requests.get(url_1year, headers=headers).json()
    except:
        raise exceptions.NotificatonException("Notification failed! Please check your Discord Webhook URL.") # TODO: Change exception

    _player_marketvalue_cache[cache_key] = json_response["it"]

    return json_response["it"] ### Only return the "it" list


def get_users(token: str, league_id: str):
    """
    ### Get all users and their IDs in the lague.
    """
    url = f"https://api.kickbase.com/v4/leagues/{league_id}/overview?includeManagersAndBattles=true"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Cookie": f"kkstrauth={token};",
    }

    ### Send GET request to get the market value changes of ALL players in the league
    try:
        json_response = requests.get(url, headers=headers).json()
    except:
        raise exceptions.NotificatonException("Notification failed! Please check your Discord Webhook URL.") # TODO: Change exception
    
    ### Create a dictionary to map user IDs to user names
    user_id_to_name = {user["i"]: user["n"] for user in json_response["us"]}
    miscellaneous.write_json_to_file(user_id_to_name, "STATIC_users.json")
    
    return json_response["us"] ### Only return the "us" list which contains alls usernames and IDs


def transfers(token: str, league_id: str) -> dict:
    """### Get all transfers of all users in a league.

    Args:
        token (str): The user's kkstrauth token.
        league_id (str): The league ID.

    Returns:
        dict: A dictionary containing the user's players.

    Cached per league for the duration of the run. main.py asks for the feed in
    taken_free_players(), turnovers() and balances(), and each call pages through the
    whole thing.
    """
    if league_id in _transfers_cache:
        return _transfers_cache[league_id]

    start_point = 0
    user_transfers = []

    while True:
        query_params = f"?max=26&start={start_point}"
        url = f"https://api.kickbase.com/v4/leagues/{league_id}/activitiesFeed/{query_params}"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Cookie": f"kkstrauth={token};",
        }

        ### Send GET request to get the next 26 entries
        try:
            json_response = requests.get(url, headers=headers).json()
        except Exception as e:
            raise exceptions.NotificatonException(f"Notification failed! Please check your Discord Webhook URL. Error: {e}") # TODO: Change exception

        ### Filter transfers where "t" == 15
        filtered_transfers = [entry for entry in json_response.get("af", []) if entry.get("t") == 15]
        user_transfers += filtered_transfers

        ### Check if there are more entries to fetch
        if not json_response.get("af"):
            break

        start_point += 26

    _transfers_cache[league_id] = user_transfers

    return user_transfers


def user_stats(token: str, league_id: str, user_id: str) -> dict:
    """
    Get the statistics of a given user in the given league.

    Cached per league and user for the duration of the run. balances() and
    league_user_stats_tables() both ask for every user.
    """
    cache_key = (league_id, str(user_id))
    if cache_key in _user_stats_cache:
        return _user_stats_cache[cache_key]

    url = f"https://api.kickbase.com/v4/leagues/{league_id}/managers/{user_id}/dashboard"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Cookie": f"kkstrauth={token};",
    }

    ### Send GET request to get the statistics of a given user in the given league
    try:
        json_response = requests.get(url, headers=headers).json()
    except:
        raise exceptions.NotificatonException("Notification failed! Please check your Discord Webhook URL.") ### TODO: Change exception

    _user_stats_cache[cache_key] = json_response

    return json_response


def user_performance(token: str, league_id: str, user_id: str) -> dict:
    """
    Get the performance of a given user in the given league.

    Cached per league and user for the duration of the run, like user_stats() next to it.
    balances() asks for every manager, and nothing about a past matchday changes mid-run.
    """
    cache_key = (league_id, str(user_id))
    if cache_key in _user_performance_cache:
        return _user_performance_cache[cache_key]

    url = f"https://api.kickbase.com/v4/leagues/{league_id}/managers/{user_id}/performance"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Cookie": f"kkstrauth={token};",
    }

    ### Send GET request to get the statistics of a given user in the given league
    try:
        json_response = requests.get(url, headers=headers).json()
    except:
        raise exceptions.NotificatonException("Notification failed! Please check your Discord Webhook URL.") ### TODO: Change exception

    _user_performance_cache[cache_key] = json_response

    return json_response


def ranking(token: str, league_id: str, match_day: int) -> dict:
    """
    ### Get the ranking of the league.
    """
    query_params = f"?dayNumber={match_day}"
    url = f"https://api.kickbase.com/v4/leagues/{league_id}/ranking/{query_params}"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Cookie": f"kkstrauth={token};",
    }

    ### Send GET request to get the ranking of the league
    try:
        json_response = requests.get(url, headers=headers).json()
    except:
        raise exceptions.NotificatonException("Notification failed! Please check your Discord Webhook URL.") ### TODO: Change exception
    
    return json_response


def live_points(token: str, league_id: str) -> dict:
    """
    ### Get the live points of all users in the given league.

    Expected response:
    ```json
    {
        "u": [
            {
                "id": "xxxx",       ### User ID
                "n": "USERNAME",
                "t": 419,           ### Live points
                "st": 12199,        ### Total points
                "pl": [ ... ]       ### Players of the user
            }
        ]
    }
    ```

    NOTE: This still targets the legacy (v1) `/leagues/{id}/live` endpoint, since
    Kickbase has no v4 equivalent implemented here yet. The live points feature is
    on-hold, so this call is unverified against the current API.
    """
    url = f"https://api.kickbase.com/leagues/{league_id}/live"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Cookie": f"kkstrauth={token};",
    }

    ### Send GET request to get the live points of the league
    try:
        json_response = requests.get(url, headers=headers).json()
    except:
        raise exceptions.KickbaseException("Couldn't get the live points of the league.")

    return json_response


def battles(token: str, league_id: str, battle_id: int) -> dict:
    """
    ### Get the battles of the league.

    Cached per league and battle type for the duration of the run. The response holds
    the standings for the whole league, but league_user_stats_tables() asks for the same
    five battle types once per user and then scans the result for that one user.
    """
    cache_key = (league_id, battle_id)
    if cache_key in _battles_cache:
        return _battles_cache[cache_key]

    url = f"https://api.kickbase.com/v4/leagues/{league_id}/battles/{battle_id}/users"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Cookie": f"kkstrauth={token};",
    }

    ### Send GET request to get the battles of the league
    try:
        json_response = requests.get(url, headers=headers).json()
    except:
        raise exceptions.NotificatonException("Notification failed! Please check your Discord Webhook URL.") ### TODO: Change exception

    _battles_cache[cache_key] = json_response

    return json_response