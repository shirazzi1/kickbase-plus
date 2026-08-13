"""
### This module holds all necessary functions to call Kickbase `/leagues/...` API endpoints.

TODO: Maybe list all functions here automatically?
"""

import logging

from concurrent.futures import ThreadPoolExecutor

from backend import exceptions, market_value_cache, miscellaneous
from backend.kickbase import http
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

### Seconds to wait for a write to Kickbase. Shorter than http.DEFAULT_TIMEOUT's read
### half on purpose: the user is waiting in front of the field for this one, unlike every
### read in this module, which now shares DEFAULT_TIMEOUT by going through http.get_json().
OFFER_TIMEOUT = 15

_player_statistics_cache = {}
_player_marketvalue_cache = {}
_transfers_cache = {}
_user_stats_cache = {}
_user_performance_cache = {}
_battles_cache = {}

### The market value window this run settled on. Decided on the first request and only
### widened if Kickbase does not serve it, so the fallback costs one request, not one per
### player. See player_marketvalue().
_market_value_days = None


def clear_caches() -> None:
    """### Empty the per-run API caches.

    The memory caches go, and so does the market value update marker the disk cache
    validates against - both belong to one run. The market value curves on disk stay: they
    are the same for every run until Kickbase moves them, which is the whole point of
    keeping them. See backend/market_value_cache.py.
    """
    global _market_value_days

    _player_statistics_cache.clear()
    _player_marketvalue_cache.clear()
    _transfers_cache.clear()
    _user_stats_cache.clear()
    _user_performance_cache.clear()
    _battles_cache.clear()
    _market_value_days = None

    market_value_cache.forget_mvud()
    miscellaneous.clear_caches()


def get_league_list(token: str) -> list:
    """Get a list of all leagues the user is in.

    Args:
        user_token (str): The user token to authenticate the user.

    Returns:
        list: List of all leagues the user is in.
    """
    url = "https://api.kickbase.com/v4/leagues/selection"

    ### Send GET request
    json_response = http.get_json(url, token)

    ### Iterating over the json response, where each entry is expected to be a dictionary. For each entry, it creates a new Leagues_Info object.
    league_list = [League_Info(entry) for entry in json_response["it"]]

    return league_list


def get_market(token: str, league_id: str):
    """
    ### Get the current players on the market in the league

    Called twice by app.py's write endpoints - once to check the market before
    place_offer()/remove_offer(), once as the read-back that confirms the write
    afterwards - and the second call runs after Kickbase has already accepted the
    write. http.get_json()'s DEFAULT_TIMEOUT bounds that read the same way it bounds
    every other Kickbase call in this module, so a hung socket there still cannot block
    the response indefinitely in the window app.py's 502 "could not confirm" outcome
    exists for.

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

    The "mvud" is not read for the market itself. It is the marker that tells the market
    value curve cache whether the curves have moved since the last run, and this is the only
    response in the project known to carry it - which is why the market stage runs first.
    See backend/market_value_cache.py.
    """
    url = f"https://api.kickbase.com/v4/leagues/{league_id}/market"

    ### Send GET request to get all free players in the given league
    json_response = http.get_json(url, token)

    market_value_cache.remember_mvud(json_response)

    ### Create a new object for every entry in the json_response["it"] list.
    players_on_market = [Market_Players(player) for player in json_response["it"]]

    return players_on_market


### German for the Kickbase error codes seen live. "UnderpayNotAllowed" is accurate but
### not a sentence to put in front of a user.
OFFER_ERRORS = {
    5080: "Das Gebot liegt unter dem Marktwert.",
    6: "Kickbase hat das Gebot als ungültig abgewiesen.",
}


def _offer_failure(response, url: str) -> "exceptions.OfferRejectedException":
    """### Turn a refused offer write into an exception worth showing a user.

    Two corrections happen here, both of them things the live API forced:

    Kickbase reports a refused bid with HTTP 500 - a bid below the market value comes
    back as {"err": 5080, "errMsg": "UnderpayNotAllowed"}. Forwarding that status would
    blame the server for the user's typo and bury real outages among ordinary
    rejections, so a 5xx carrying an error code becomes a 400. A 5xx without one is a
    genuine outage and becomes a 502. A 4xx is already right and passes through.

    And the message comes from "errMsg". "err" is a numeric code; reading it as a message
    would show the user "5080".

    Args:
        response (requests.Response): The response http.request() returned, not yet
            raised on - its body is exactly what is being read here.
        url (str): The URL that was called, for the exception's own url field.
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

    return exceptions.OfferRejectedException(message, url=url, status_code=status)


def place_offer(token: str, league_id: str, player_id: str, price: int) -> dict:
    """### Place a bid on a player listed on the transfer market.

    The first write in this project. Goes through http.request() rather than
    http.post_json(): that function raises before the body of an error response is
    read, and a rejected bid's reason lives in that body (see _offer_failure()).

    Args:
        token (str): The user's kkstrauth token.
        league_id (str): The league the player is listed in.
        player_id (str): The player to bid on.
        price (int): The bid, in whole euros.

    Raises:
        exceptions.OfferRejectedException: Kickbase answered the write with an error
            status. Carries the normalised HTTP status and Kickbase's own message.
        exceptions.ApiUnreachableException: No answer at all (timeout, connection
            refused). Raised by http.request() itself and left untouched here - it is
            already a KickbaseException, and app.py's generic handler already answers
            it with a German "could not process the bid" message.

    Returns:
        dict: The response body, or an empty dict when there is none.
    """
    url = f"https://api.kickbase.com/v4/leagues/{league_id}/market/{player_id}/offers"

    ### retry=False: Kickbase answers a rejected bid with a 5xx (see OFFER_ERRORS
    ### above), and the pooled client's retry policy treats every 5xx as transient and
    ### worth repeating. POST is not in RETRY_METHODS, so this makes no practical
    ### difference here - but remove_offer() below needs it for DELETE, which is, and
    ### both calls pass it for the same reason rather than depend on that asymmetry.
    response = http.request("POST", url, payload={"price": price}, token=token,
                            timeout=OFFER_TIMEOUT, retry=False)

    if response.status_code >= 400:
        raise _offer_failure(response, url)

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
        exceptions.OfferRejectedException: Kickbase answered the removal with an error
            status.
        exceptions.ApiUnreachableException: No answer at all - see place_offer().
    """
    url = (f"https://api.kickbase.com/v4/leagues/{league_id}/market/{player_id}"
           f"/offers/{own_user_id}")

    ### retry=False: DELETE is in RETRY_METHODS, so without this a rejected withdrawal
    ### (also a 5xx, same as a rejected bid) would be retried three times by the pooled
    ### client before ever reaching _offer_failure() below, and would then be reported
    ### as an outage instead of the rejection it actually is.
    response = http.request("DELETE", url, token=token, timeout=OFFER_TIMEOUT, retry=False)

    if response.status_code >= 400:
        raise _offer_failure(response, url)


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

    ### The curves that are still current on disk are read here rather than in the pool
    ### below, so the thread pool is only spun up for the ones that really need a request
    ### and the log line says how many that is
    wanted_curves = len(missing_marketvalues)
    from_disk = _fill_from_disk(missing_marketvalues)
    missing_marketvalues = [p for p in missing_marketvalues if p not in from_disk]

    if wanted_curves:
        _report_disk_cache(len(from_disk), wanted_curves)

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


def _report_disk_cache(served: int, wanted: int) -> None:
    """### Say what the market value disk cache did, including when it did nothing.

    Unconditional on purpose. Every single reason an entry is not used is a DEBUG line per
    player, so a cache that never works at all shows up in the INFO log as exactly nothing -
    the same silence as a cache that is working perfectly and simply had its daily miss.
    This line is the difference, and the count of entries on disk is what makes it readable
    without turning DEBUG on:

      - Zero hits, entries on disk: the market values moved. Expected once a day.
      - Zero hits, no entries at all: nothing is being written. Either no run ever got a
        market value update marker, or the curves that come back are empty - which is what
        an empty "it" list does, and it costs the frontend its deltas either way.

    Args:
        served (int): How many curves came from the disk.
        wanted (int): How many were needed.
    """
    stored = market_value_cache.entries_on_disk()

    logging.info(f"{served} of {wanted} market value curve(s) came from the disk cache "
                 f"({stored} entr{'y' if stored == 1 else 'ies'} stored, "
                 f"{wanted - served} to fetch).")

    if served or stored:
        return

    if market_value_cache.current_mvud() is None:
        logging.warning("Nothing is cached and this run has no market value update marker, "
                        "so every curve is fetched fresh. The market response carried no "
                        "'mvud' - see get_market().")
    else:
        logging.warning("Nothing is cached even though this run has a market value update "
                        "marker. A curve is only stored when it holds at least one point, so "
                        "check whether the API is answering with an empty history - that "
                        "leaves the value deltas empty too, cache or no cache.")


def _fill_from_disk(player_ids: list) -> set:
    """### Move every still current curve from the disk cache into the memory cache.

    Args:
        player_ids (list): The players whose curves are not in memory yet.

    Returns:
        set: The ids that were served from disk.
    """
    global _market_value_days

    if not player_ids or market_value_cache.current_mvud() is None:
        return set()

    if _market_value_days is None:
        _market_value_days = miscellaneous.market_value_days()

    served = set()

    for player_id in player_ids:
        history = market_value_cache.read(player_id, _market_value_days)

        if history is not None:
            _player_marketvalue_cache[player_id] = history
            served.add(player_id)

    return served


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

    ### Send GET request to get the market value changes of ALL players in the league.
    ### The status note ("stxt") is the only prose in this response and defaults to
    ### English. The frontend is German, so ask for German. Kickbase localises on this
    ### header only: a "lang"/"locale" query parameter is ignored.
    json_response = http.get_json(url, token,
                                  extra_headers={"Accept-Language": "de-DE,de;q=0.9"})

    _player_statistics_cache[cache_key] = json_response

    return json_response


def player_marketvalue(token: str, player_id: str, days: int = None):
    """
    ### Get the market value history of a given player.

    Cached twice over: per player for the duration of the run, and on disk between runs.
    The disk cache only answers while Kickbase says the market values have not moved -
    see backend/market_value_cache.py for the format and the four things that invalidate an
    entry. A miss there fetches exactly as before.

    Only as many days as the run actually reads are requested, instead of a full year for
    every player on every run. See miscellaneous.market_value_days() for what decides the
    window.

    Args:
        token (str): The user's kkstrauth token.
        player_id (str): The player to fetch the history for.
        days (int): Override the window. Defaults to what the run needs.

    Returns:
        list: The history, oldest first, one entry per day.
    """
    global _market_value_days

    cache_key = str(player_id)
    if cache_key in _player_marketvalue_cache:
        return _player_marketvalue_cache[cache_key]

    if days is None:
        if _market_value_days is None:
            _market_value_days = miscellaneous.market_value_days()
        days = _market_value_days

    cached = market_value_cache.read(cache_key, days)
    if cached is not None:
        _player_marketvalue_cache[cache_key] = cached
        return cached

    history = _fetch_marketvalue(token, player_id, days)

    ### /marketValue/365 is the only window this project has ever asked for, so a shorter
    ### one going unanswered is a real possibility. Falling back keeps the run alive, and
    ### remembering the fallback keeps it to one wasted request instead of one per player.
    ### If Kickbase is simply down, this costs exactly one extra request before the wider
    ### window fails too and the run stops - see _fetch_marketvalue() for why the 5xx is
    ### not assumed to be an outage on the first attempt.
    if history is None and days != miscellaneous.MAX_MARKET_VALUE_DAYS:
        logging.warning(f"Kickbase did not answer a {days} day market value window (the reason "
                        f"is in the DEBUG log). Falling back to "
                        f"{miscellaneous.MAX_MARKET_VALUE_DAYS} days for the rest of this run.")
        _market_value_days = miscellaneous.MAX_MARKET_VALUE_DAYS
        ### The window that was actually served, so the disk cache records the wider one
        days = _market_value_days
        history = _fetch_marketvalue(token, player_id, days)

    if history is None:
        raise exceptions.KickbaseException(
            f"Couldn't get the market value history of player {player_id}.")

    _player_marketvalue_cache[cache_key] = history

    market_value_cache.write(cache_key, days, history)

    return history


def cached_market_value(player_id: str):
    """### The market value history of a player, but only if this run already fetched it.

    A read-only look into the cache player_marketvalue() fills. Derivation steps that want
    the curve of many players - backend/profiles.py - can then use what the run happened to
    fetch anyway and report honestly on what is missing, instead of turning into a few
    hundred extra requests.

    Args:
        player_id (str): The player to look up.

    Returns:
        list: The history, or None if this run has not fetched it.
    """
    return _player_marketvalue_cache.get(str(player_id))


def _fetch_marketvalue(token: str, player_id: str, days: int):
    """### Ask for one player's market value history over a given window.

    Args:
        token (str): The user's kkstrauth token.
        player_id (str): The player to fetch the history for.
        days (int): How many days to ask for.

    Returns:
        list: The "it" list, or None if Kickbase did not answer with one.
    """
    url = f"https://api.kickbase.com/v4/competitions/1/players/{player_id}/marketValue/{days}"

    ### A window the API does not serve reads as an answer here rather than as a failure,
    ### so player_marketvalue() can widen it. Which statuses mean that is not obvious:
    ### the only evidence in this repository of how Kickbase answers for a resource it
    ### does not have is the note in competitions.get_team_overview(), and it says 500.
    ### So a server error counts as "not served" too - but only while there is a wider
    ### window left to try. Once the request is already at MAX_MARKET_VALUE_DAYS, the
    ### same 5xx is a real outage and travels on, so a broken Kickbase still fails loudly
    ### instead of silently producing histories nobody can read.
    ###
    ### Everything else - an expired token, a rate limit, a hung socket - always travels
    ### on to the caller.
    unserved = (exceptions.ApiRequestException, exceptions.ApiResponseException)

    if days != miscellaneous.MAX_MARKET_VALUE_DAYS:
        unserved += (exceptions.ApiUnavailableException,)

    ### Send GET request to get the market value history of the given player
    try:
        json_response = http.get_json(url, token)
    except unserved as e:
        logging.debug(f"Kickbase did not answer a {days} day market value window for "
                      f"player {player_id}: {e}")
        return None

    return json_response.get("it")


def get_users(token: str, league_id: str):
    """
    ### Get all users and their IDs in the lague.
    """
    url = f"https://api.kickbase.com/v4/leagues/{league_id}/overview?includeManagersAndBattles=true"

    ### Send GET request to get the market value changes of ALL players in the league
    json_response = http.get_json(url, token)

    ### Create a dictionary to map user IDs to user names
    user_id_to_name = {user["i"]: user["n"] for user in json_response["us"]}
    miscellaneous.write_json_to_file(user_id_to_name, "STATIC_users.json")
    
    return json_response["us"] ### Only return the "us" list which contains alls usernames and IDs


### How many activity feed entries one page holds. The API's own maximum.
FEED_PAGE_SIZE = 26


def transfers(token: str, league_id: str, known_transfers: list = None) -> list:
    """### Get all transfers of all users in a league.

    Cached per league for the duration of the run. main.py asks for the feed in
    taken_free_players(), turnovers() and balances(), and each of them wants the whole
    season.

    The feed is not walked to the end every time. It pages newest first, and everything
    older than the newest transfer this project already recorded is by definition already
    recorded - so the walk stops at the first known transfer id and the recorded ones are
    merged back in. That turns a cost which grew with the season into a constant one or two
    pages, without changing what the callers get: the same complete list of "t" == 15 items,
    newest first.

    Two things are deliberately *not* assumed. That every returned entry is a transfer -
    the watermark is only ever compared against ids of "t" == 15 items, the only kind this
    project records - and that a page which happens to contain no transfer at all means the
    end. The walk still stops only on an empty page or on a known id.

    Reverted bookings are untouched by any of this. The reversal and the booking that
    replaced it are both in the feed, both get recorded, and drop_reverted_transfers()
    still sees both - it runs on the merged list in main.py, exactly as before.

    Args:
        token (str): The user's kkstrauth token.
        league_id (str): The league ID.
        known_transfers (list): The transfers earlier runs recorded, which is where the
            watermark comes from. Defaults to what is on disk; pass an empty list to force
            a full walk.

    Returns:
        list: The activity feed items with "t" == 15, newest first.
    """
    if league_id in _transfers_cache:
        return _transfers_cache[league_id]

    if known_transfers is None:
        known_transfers = miscellaneous.load_known_transfers()

    ### Only ids of items this project actually records can serve as a watermark
    known_ids = {item["i"] for item in known_transfers if item.get("i") is not None}

    start_point = 0
    new_transfers = []
    pages = 0

    while True:
        query_params = f"?max={FEED_PAGE_SIZE}&start={start_point}"
        url = f"https://api.kickbase.com/v4/leagues/{league_id}/activitiesFeed/{query_params}"

        ### Send GET request to get the next 26 entries
        json_response = http.get_json(url, token)
        pages += 1

        entries = json_response.get("af") or []

        ### Filter transfers where "t" == 15
        filtered_transfers = [entry for entry in entries if entry.get("t") == 15]
        new_transfers += [entry for entry in filtered_transfers
                          if entry.get("i") not in known_ids]

        ### Check if there are more entries to fetch
        if not entries:
            break

        ### A page holding a transfer that is already recorded is where the known part of
        ### the feed begins. The rest of the page is processed first - the feed is ordered
        ### by time, not by whether this project has seen an entry - and then the walk is
        ### over.
        if known_ids and any(entry.get("i") in known_ids for entry in filtered_transfers):
            logging.debug(f"Reached an already known transfer after {pages} feed page(s), "
                          f"stopping the walk.")
            break

        start_point += FEED_PAGE_SIZE

    if known_ids:
        logging.info(f"Walked {pages} activity feed page(s) and found {len(new_transfers)} "
                     f"new transfer(s) on top of the {len(known_ids)} already recorded.")
    else:
        logging.info(f"No transfers recorded yet, so the whole activity feed was walked: "
                     f"{pages} page(s), {len(new_transfers)} transfer(s).")

    ### Newest first, the order the pages arrive in, so the callers see what a full walk
    ### would have handed them
    user_transfers = new_transfers + [item for item in reversed(known_transfers)
                                      if item.get("t") == 15]

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

    ### Send GET request to get the statistics of a given user in the given league
    json_response = http.get_json(url, token)

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

    ### Send GET request to get the statistics of a given user in the given league
    json_response = http.get_json(url, token)

    _user_performance_cache[cache_key] = json_response

    return json_response


def ranking(token: str, league_id: str, match_day: int) -> dict:
    """
    ### Get the ranking of the league.
    """
    query_params = f"?dayNumber={match_day}"
    url = f"https://api.kickbase.com/v4/leagues/{league_id}/ranking/{query_params}"

    ### Send GET request to get the ranking of the league
    return http.get_json(url, token)


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

    ### Send GET request to get the live points of the league
    return http.get_json(url, token)


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

    ### Send GET request to get the battles of the league
    json_response = http.get_json(url, token)

    _battles_cache[cache_key] = json_response

    return json_response