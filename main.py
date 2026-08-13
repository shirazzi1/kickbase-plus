import json
import time
import logging

from os import getenv, makedirs, path
from art import tprint
from sys import stdout
from logging.config import dictConfig
from datetime import datetime, timedelta, timezone

from backend import exceptions, miscellaneous, profiles, runs
from backend.kickbase.v4 import competitions, user, leagues
from backend.paths import LOG_DIR, DATA_DIR, TIMESTAMP_DIR

### -------------------------------------------------------------------
### -------------------------------------------------------------------
### -------------------------------------------------------------------

__version__ = getenv("REACT_APP_VERSION", "Warning: Couldn't load version")

### Log rotation. Size based on purpose: the old handlers rotated on a timer with
### backupCount=0, which never deletes anything - the DEBUG log had grown past 9 MB and
### kept growing with every run. A run writes a fixed amount, not a daily one, so a byte
### budget is what actually bounds the disk usage.
###
### Each file is capped at maxBytes and keeps that many rotated copies, so the ceiling is
### maxBytes * (backupCount + 1) per log.
LOG_MAX_BYTES = 5 * 1024 * 1024          # 5 MB per INFO log file, 20 MB in total
VERBOSE_LOG_MAX_BYTES = 10 * 1024 * 1024 # 10 MB per DEBUG log file, 40 MB in total
LOG_BACKUP_COUNT = 3


def build_logging_config(log_dir: str) -> dict:
    """### Build the logging configuration for a given log directory.

    Kept out of main() so a test can check the rotation settings without logging in to
    Kickbase first.

    Args:
        log_dir (str): The directory the log files are written to.

    Returns:
        dict: A configuration for logging.config.dictConfig().
    """
    ### Define the log file paths
    log_file_path = path.join(log_dir, "kickbase-insights.log")
    verbose_log_file_path = path.join(log_dir, "kickbase-insights-verbose.log")

    ### Set logging settings for the Python logging module
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "verbose": {
                "format": "[L] {asctime} [{levelname}] {pathname} - Line {lineno} - {message}",
                "style": "{",
                "datefmt": "%d.%m.%Y %H:%M:%S",
            },
            "simple": {
                "format": "[L] {asctime} [{levelname}] - {message}",
                "style": "{",
                "datefmt": "%d.%m.%Y %H:%M:%S",
            },
        },
        "handlers": {
            "file": { # Log only INFO and higher to file (simple format)
                "level": "INFO",
                "class": "logging.handlers.RotatingFileHandler",
                "filename": log_file_path,
                "maxBytes": LOG_MAX_BYTES,
                "backupCount": LOG_BACKUP_COUNT,
                "formatter": "simple",
                "encoding": "utf-8",
            },
            "verbose_file": { # Log EVERYTHING to file (verbose format)
                "level": "DEBUG",
                "class": "logging.handlers.RotatingFileHandler",
                "filename": verbose_log_file_path,
                "maxBytes": VERBOSE_LOG_MAX_BYTES,
                "backupCount": LOG_BACKUP_COUNT,
                "formatter": "verbose",
                "encoding": "utf-8",
            },
            "console": { # Log only INFO and higher to console (simple format)
                "level": "INFO",
                "class": "logging.StreamHandler",
                "stream": stdout,
                "formatter": "simple",
            },
        },
        "loggers": {
            "root": { # "Root" logger: Send all logging entries to the handlers
                "handlers": ["file", "verbose_file", "console"],
                "level": "DEBUG",
                "propagate": True,
            },
        },
    }


def build_stages(user_token: str, selected_league: object, own_user_id: str) -> list:
    """### The stages of a run, in the order they have to happen.

    The order is not free. market_value_changes() writes STATIC_users.json and
    STATIC_teams.json, which four later stages open; balances() reads the turnovers.json
    that turnovers() wrote on the previous run. A stage that fails therefore costs the
    stages behind it that depend on its files - but they fail on their own and say so,
    instead of never running at all.

    Args:
        user_token (str): The user's kkstrauth token.
        selected_league (object): The league to gather data for.
        own_user_id (str): The logged in user's ID.

    Returns:
        list: (name, callable) pairs. The names are what the manifest and the frontend
            know each stage by, so they are part of the contract.
    """
    return [
        ("gift", lambda: get_gift(user_token)),
        ("market", lambda: market(user_token, selected_league, own_user_id)),
        ("market_value_changes", lambda: market_value_changes(user_token, selected_league)),
        ("taken_free_players", lambda: taken_free_players(user_token, selected_league)),
        ("balances", lambda: balances(user_token, selected_league, own_user_id)),
        ("turnovers", lambda: turnovers(user_token, selected_league)),
        ("team_values", lambda: team_value_per_match_day(user_token, selected_league)),
        ("league_user_stats", lambda: league_user_stats_tables(user_token, selected_league)),
        ### Last, and deliberately so: it derives, it does not fetch. It reads the files
        ### the stages above wrote and the market value curves they left in the run cache,
        ### which is why it costs no API requests at all.
        ("manager_profiles", profiles.write_manager_profiles),
        # ("live_points", lambda: live_points(user_token, selected_league)), # needs to be run first to initialize the live_points.json file
    ]


def main(manifest: runs.RunManifest = None) -> runs.RunManifest:
    """### This is the main function of the Kickbase Insights program.

    It performs various tasks related to logging, user login, and data retrieval from the Kickbase API.

    Args:
        manifest (runs.RunManifest): The manifest to fill in. The caller passes one so it
            still holds the record if this function dies partway through.

    Returns:
        runs.RunManifest: What every stage of this run did. The caller writes it out and
            decides the exit code from it.
    """
    ### Ensure directories exist
    makedirs(LOG_DIR, exist_ok=True)
    makedirs(TIMESTAMP_DIR, exist_ok=True)

    ### Configure logging with the settings from the dictionary
    dictConfig(build_logging_config(LOG_DIR))

    if manifest is None:
        manifest = runs.RunManifest(runs.start_run())

    logging.info(f"Run {manifest.run_id} starting.")

    ### Validate START_DATE before doing any work.
    ### entrypoint.py checks this for Docker runs, but running main.py directly skips
    ### that check and would only fail minutes later, in turnovers().
    try:
        miscellaneous.get_start_datetime()
    except exceptions.KickbaseException as e:
        logging.error(f"{e} Exiting...")
        manifest.record_failure("start_date", f"{type(e).__name__}: {e}")
        return manifest

    ### Start every run with empty API caches, so a long lived process (app.py) never
    ### serves data from a previous run
    leagues.clear_caches()

    ### The login is not a stage. Every stage needs the token it returns, so there is
    ### nothing to isolate: without it the run has not begun.
    ###
    ### Caught broadly on purpose. The login path reaches Kickbase, parses its answer and
    ### writes STATIC_users.json, so it can fail in ways that are neither of this
    ### project's exception types - a changed API response is a KeyError, a full disk an
    ### OSError. A narrow tuple let those through, and the run then ended without leaving
    ### any record at all.
    try:
        selected_league, user_token, own_user_id = login()
    except Exception as e:
        logging.exception(f"Login failed, no stage can run: {e}")
        manifest.record_failure("login", f"{type(e).__name__}: {e}")
        return manifest

    ### Each stage on its own. One that fails costs its own datasets and nothing else,
    ### and the manifest says which ones those were.
    for name, stage in build_stages(user_token, selected_league, own_user_id):
        manifest.run(name, stage)

    return manifest


def login() -> tuple:
    """### Logs in to Kickbase and gathers various information.

    Returns:
        tuple: A tuple containing the following elements:
            -- selected_league (object): The league the user wants to get data from for the frontend.
            -- user_token (str): User token for authentication.
            -- own_user_id (str): The logged in user's ID. market() needs it to tell the
               user's own bids apart from anyone else's.
    """
    logging.info("Logging in...")

    ### Login to Kickbase using the credentials from the environment variables
    user_info, user_token = user.login(kb_mail, kb_password, discord_webhook)
    logging.info(f"Successfully logged in as {user_info.name}")

    ### Get all leagues the user is in.
    ###
    ### Raising rather than exit(): a function three levels down is not the place that
    ### decides the process is over. exit() raises SystemExit, which is a BaseException
    ### and slipped past every handler on the way up - the run then died without writing
    ### a manifest, left the previous run's one in place, and exited 0 while it was at it.
    league_list = leagues.get_league_list(user_token)
    if not league_list:
        raise exceptions.LoginException(
            "No leagues found for this Kickbase account. There is nothing to gather data for.")
    logging.info(f"Available leagues: {', '.join([league.name for league in league_list])}") # Print all available leagues the user is in

    return select_league(league_list), user_token, user_info.id


def select_league(league_list: list) -> object:
    """### Picks the league the frontend should show data for.

    Uses the league named in the `KB_LIGA` environment variable and falls back to the
    first league the user is in.

    Args:
        league_list (list): All leagues the user is in.

    Returns:
        object: The league the user wants to get data from for the frontend.
    """
    ### Fetch the preferred league name from the environment variable
    preferred_league_name = getenv("KB_LIGA")

    ### Initialize selected_league to None
    ### The selected_league will be the league the user wants to get the data from for the frontend
    selected_league = None

    ### Filter league_list to find the preferred league, default to the first league if not found
    if preferred_league_name:
        for league in league_list:
            if league.name == preferred_league_name:
                selected_league = league
                logging.info(f"Preferred league '{preferred_league_name}' found: {selected_league.name}")
                break
        if not selected_league:
            logging.warning(f"Preferred league '{preferred_league_name}' not found. Defaulting to the first league: {league_list[0].name}")
            selected_league = league_list[0]
    else:
        logging.info(f"No preferred league set. Using the first league in the list: {league_list[0].name}")
        selected_league = league_list[0]

    return selected_league


def get_gift(user_token: str) -> None:
    """### Collect the daily login gift in every available league.

    Args:
        user_token (str): The user's kkstrauth token.
    """
    gift = user.collect_gift(user_token)

    ### Check if response["it"] is not empty:
    if gift["it"]:
        logging.info(f"Gift available in league {gift['it'][0]['lnm']}!")
        miscellaneous.discord_notification("Kickbase Gift available!", f"Amount: {gift['it'][0]['v']}\nLevel: {gift['it'][0]['day']}", 6617600, discord_webhook) # TODO: Change color
    else:
        logging.info("Gift has already been collected!")


def market(user_token: str, selected_league: object, own_user_id: str) -> None:
    """### Retrieves all players listed on the transfer market.

    Player and Kickbase listings go into one file, marked by "isFreeAgent". They are
    the same decision for the user, so splitting them across two tables only meant
    comparing rows between them.

    Each row also carries the user's own bid, the status note from the player profile
    and the daily market value deltas. The profile and market value history are both
    cached per run and this function runs before market_value_changes(), which asks for
    both for every player in the competition anyway, so this costs no extra API calls.

    Args:
        user_token (str): The user's kkstrauth token.
        selected_league (object): The league the user wants to get data from for the frontend.
        own_user_id (str): The logged in user's ID, to identify their own bids.
    """
    logging.info("Getting players listed on transfer market...")

    ### Get all players on the market
    players_on_market = leagues.get_market(user_token, selected_league.id)

    players_on_the_market = []

    for player in players_on_market:
        if player.position not in miscellaneous.POSITIONS:
            logging.warning(f"Invalid position number: {player.position} for player {player.firstName} {player.lastName} (PID: {player.id})")
            player.position = 1 ### Default to "Torwart" (Goalkeeper)

        ### The status note only exists on the player profile, not on the market entry
        player_stats = leagues.player_statistics(user_token, selected_league.id, player.id)
        status_text = (player_stats.get("stxt") or "").strip() or None

        deltas = miscellaneous.market_value_deltas(leagues.player_marketvalue(user_token, player.id))

        own_bid = player.own_offer(own_user_id)

        ### Kickbase only sends an expiry ("exs") for its own listings, never for player
        ### ones, so that column stays empty for the latter. Written as ISO 8601 so it
        ### sorts chronologically in the frontend, which a dd.mm.yyyy string does not.
        if player.expiry is not None:
            expiration = (datetime.now(timezone.utc) + timedelta(seconds=player.expiry)).isoformat()
        else:
            expiration = None

        ### When the listing went up. Unlike the expiry, Kickbase sends this for every
        ### listing, so it is the one age signal that also exists for the user listings -
        ### exactly the rows where "Ablaufdatum" stays empty. Normalised to an explicit UTC
        ### offset, otherwise the frontend reads it as local time.
        listed_since = None

        if player.listedsince:
            try:
                listed_since = miscellaneous.parse_feed_timestamp(player.listedsince).isoformat()
            except ValueError:
                logging.warning(f"Couldn't read the listing date '{player.listedsince}' "
                                f"of player {player.firstName} {player.lastName} (PID: {player.id}).")

        player_info = {
            ### The row identity. The table used to key its rows by array position, so a
            ### sale shifted every row below it onto a different player.
            "playerId": player.id,
            "teamId": player.teamId,
            "position": miscellaneous.POSITIONS[player.position],
            "firstName": f"{player.firstName}",
            "lastName": f"{player.lastName}",
            "status": player.status,
            "statusText": status_text,
            "marketValue": player.marketValue,
            "price": player.price,
            "ownBid": own_bid,
            "seller": player.username or "Kickbase",
            ### Who to exclude from the bidders for this player. The display name alone
            ### cannot do it: the auction solver joins listings against balances.json, and
            ### two managers may well pick the same name. None for Kickbase's own listings.
            "sellerId": player.userId,
            "isFreeAgent": not player.username,
            "expiration": expiration,
            "listedSince": listed_since,
            ### How many managers are bidding. Kickbase never reveals whose bids they are,
            ### only how many there are.
            "offerCount": player.ofc,
            **deltas,
        }

        players_on_the_market.append(player_info)

        bid_note = f", own bid {own_bid}" if own_bid is not None else ""
        logging.debug(f"Player {player.firstName} {player.lastName} is listed by {player_info['seller']}{bid_note}!")

    free_agents = sum(1 for player in players_on_the_market if player["isFreeAgent"])
    own_bids = sum(1 for player in players_on_the_market if player["ownBid"] is not None)
    logging.info(f"Got all {len(players_on_the_market)} players listed on transfer market "
                 f"({free_agents} listed by Kickbase, {own_bids} with a bid of yours).")

    ### Save to file + timestamp
    miscellaneous.write_json_to_file(players_on_the_market, "market.json")
    miscellaneous.write_timestamp("ts_market.json", rows=len(players_on_the_market))


def market_value_changes(user_token: str, selected_league: object) -> None:
    """### Retrieves the market value changes for all players in the league.

    Args:
        user_token (str): The user's kkstrauth token.
        selected_league (object): The league the user wants to get data from for the frontend.
    """
    logging.info("Getting market value changes for all players...")

    players_LIST = []

    user_list = leagues.get_users(user_token, selected_league.id)
    ### Create a dictionary to map user IDs to user names
    user_id_to_name = {user["i"]: user["n"] for user in user_list}

    all_teams_in_competition = competitions.get_team_overview(user_token)

    ### Fetch every player's statistics and market value history up front. The loop below
    ### needs two requests per player, around a thousand in total, and doing them one at
    ### a time dominated the runtime of the whole program.
    all_player_ids = [player["i"] for team in all_teams_in_competition for player in team["players"]]
    leagues.prefetch_players(user_token, selected_league.id, all_player_ids)

    ### Loop through all teams
    for team in all_teams_in_competition:
        ### Loop through all players in the team
        for player in team["players"]:
            ### Get the market value changes for the player
            player_stats = leagues.player_statistics(user_token, selected_league.id, player["i"])
            player_marketvalue = leagues.player_marketvalue(user_token, player["i"])

            ### Check if player is owned by a user in this league.
            ### Ownership lives in the per-league "opl" list, not in the top level "oui".
            owner = miscellaneous.get_player_owner(player_stats, selected_league.id)

            if owner:
                manager = owner.get("onm") or user_id_to_name.get(owner["oui"], "Unknown")
            else:
                manager = "Kickbase"
                
            ### Check if position number is valid
            if player["pos"] not in miscellaneous.POSITIONS:
                logging.warning(f"Invalid position number: {player_stats['pos']} for player {player_stats['fn']} {player_stats['ln']} (PID: {player_stats['i']})")
                player["pos"] = 1 # Default to "Torwart" (Goalkeeper)

            ### Create a custom json dict for every player
            players_LIST.append({
                "teamId": player_stats["tid"],
                "position": miscellaneous.POSITIONS[player_stats["pos"]],
                "firstName": player_stats.get("fn", None), 
                "lastName": player_stats["ln"], 
                "marketValue": player_stats["mv"],
                **miscellaneous.market_value_deltas(player_marketvalue),
                "manager": manager,
            })
            logging.debug(f"Player {player_stats.get('fn', None)} {player_stats['ln']} has a market value of {player_stats['mv']} and is owned by {manager}.")

    logging.info("Got all market value changes for all players.")

    ### Save to file + timestamp
    miscellaneous.write_json_to_file(players_LIST, "market_value_changes.json")
    miscellaneous.write_timestamp("ts_market_value_changes.json", rows=len(players_LIST))


def taken_free_players(user_token: str, selected_league: object):
    """### Retrieves all taken and free players in the league.

    Args:
        user_token (str): The user's kkstrauth token.
        selected_league (object): The league the user wants to get data from for the frontend.
    """
    logging.info("Getting taken and free players...")

    taken_players = []
    free_players = []

    ### Get all users in the league
    with open(path.join(DATA_DIR, "STATIC_users.json"), "r") as f:
        league_users = json.load(f)

    ### Get all transfers in the league
    all_transfers = leagues.transfers(user_token, selected_league.id)

    ### Built once, not once per transfer: the old code rebuilt this reverse mapping inside
    ### the loop below, and a name it could not resolve landed under the key None, where no
    ### owner ever matches it. The buy price then silently fell back to the season start
    ### market value. resolve_user_id() says so out loud instead.
    name_to_id = miscellaneous.build_user_name_index(league_users)

    ### Create a dictionary to store buy prices from transfers
    buy_prices = {}
    for transfer in all_transfers:
        if "byr" in transfer["data"]:
            user_id = miscellaneous.resolve_user_id(transfer["data"]["byr"], name_to_id)

            if user_id is None:
                continue

            player_id = transfer["data"]["pi"]
            buy_price = transfer["data"]["trp"]

            if user_id not in buy_prices:
                buy_prices[user_id] = []

            buy_prices[user_id].append((player_id, buy_price))

    ### Cycle through all teams
    with open(path.join(DATA_DIR, "STATIC_teams.json"), "r") as f:
        all_teams = json.load(f)
    for team in all_teams:
        ### Cycle through all players of the team
        for player in team["players"]:

            ### Search the stats of the given player ID to fill the missing attributes for the player
            player_stats = leagues.player_statistics(user_token, selected_league.id, player["i"])

            ### Check if the player is owned by a user in this league.
            ### Ownership lives in the per-league "opl" list, not in the top level "oui".
            owner = miscellaneous.get_player_owner(player_stats, selected_league.id)

            if owner:
                logging.debug(f"Player {player_stats.get('fn', None)} {player['n']} is owned by user {owner.get('onm', 'Unknown')}!")

                ### Check if position number is valid
                if player["pos"] not in miscellaneous.POSITIONS:
                    logging.warning(f"Invalid position number: {player['pos']} for player {player_stats.get('fn', None)} {player['n']} (PID: {player['i']})")
                    player["pos"] = 1 ### Default to "Torwart" (Goalkeeper)

                ### Determine the buy price
                current_user_id = owner["oui"]
                buy_price = 0
                if current_user_id in buy_prices:
                    for pid, price in buy_prices[current_user_id]:
                        if pid == player["i"]:
                            buy_price = price
                            break

                if buy_price == 0:
                    ### Set the buyPrice to the START_DATE value in the player_marketvalues list
                    ### Do this because the player was assigned at the start of the season.
                    ### Market values exist per day, so only the date part is used here.
                    start_date = miscellaneous.get_start_datetime().strftime("%d.%m.%Y")

                    player_marketvalues = leagues.player_marketvalue(user_token, player["i"])

                    for marketValue in player_marketvalues:
                        ### Convert the Julian date to a standard date
                        market_value_date = miscellaneous.julian_to_date(marketValue["dt"])

                        if market_value_date == start_date:
                            buy_price = marketValue["mv"]
                            logging.debug(f"Player {player_stats.get('fn', None)} {player['n']} was assigned at the start of the season. Market value on START_DATE {start_date}: {buy_price}€.")
                            break

                ### Create a custom json dict for every taken player. This will be passed to the frontend later.
                taken_players.append({
                    "owner": owner.get("onm") or league_users.get(owner["oui"], "Unknown"),
                    "playerId": player["i"],
                    "teamId": player["tid"],
                    "position": miscellaneous.POSITIONS[player["pos"]],
                    "firstName": player_stats.get("fn", None),
                    "lastName": player["n"],
                    "buyPrice": buy_price,
                    "marketValue": player["mv"],
                    "status": player["st"],
                    "trend": player["mvt"],
                })
            else:
                ### Create a custom json dict for every free player. This will be passed to the frontend later.
                free_players.append({
                    "playerId": player["i"],
                    "teamId": player["tid"],
                    "position": miscellaneous.POSITIONS[player["pos"]],
                    "firstName": player_stats.get("fn", None),
                    "lastName": player["n"],
                    "marketValue": player["mv"],
                    "points": player_stats.get("tp", 0),
                    "status": player["st"],
                    "trend": player["mvt"],
                })

    logging.info("Got all taken and free players.")
    
    ### Save to file + timestamp
    miscellaneous.write_json_to_file(taken_players, "taken_players.json")
    miscellaneous.write_timestamp("ts_taken_players.json", rows=len(taken_players))

    ### Save to file + timestamp
    miscellaneous.write_json_to_file(free_players, "free_players.json")
    miscellaneous.write_timestamp("ts_free_players.json", rows=len(free_players))


def turnovers(user_token: str, selected_league: object) -> None:
    """### Retrieves all turnovers in the league.

    Args:
        user_token (str): The user's kkstrauth token.
        selected_league (object): The league the user wants to get data from for the frontend.
    """
    logging.info("Getting turnovers...")

    final_turnovers = []

    ### Load existing transfers from all_transfers.json which were saved in earlier runs
    all_transfers_path = path.join(DATA_DIR, "all_transfers.json")

    all_transfers = [] # Initialize as empty list first

    ### Check if all_transfers.json exists and load it
    if path.exists(all_transfers_path):
        try:
            with open(all_transfers_path, "r") as f:
                all_transfers = json.load(f)
            logging.debug(f"Loaded {len(all_transfers)} existing transfers from all_transfers.json")
        except json.JSONDecodeError:
            logging.warning(f"The file {all_transfers_path} is empty or contains invalid JSON. Initializing all_transfers as an empty list.")
    else:
        logging.debug(f"The file {all_transfers_path} does not exist. Initializing all_transfers as an empty list.")

    ### Get new transfers from the API
    new_transfers = leagues.transfers(user_token, selected_league.id)
    logging.debug(f"Found {len(new_transfers)} current transfers from the API")

    ### Append only new transfers (ignoring duplicates)
    current_transfer_ids = {item["i"] for item in all_transfers}  # Set of existing transfer IDs
    for transfer in new_transfers:
        if transfer["i"] not in current_transfer_ids:  # Check if the transfer is new
            all_transfers.append(transfer)
            current_transfer_ids.add(transfer["i"])  # Update the set to include the new transfer

    ### Sort transfers by date after appending new ones
    all_transfers.sort(key=lambda x: datetime.fromisoformat(x["dt"].replace("Z", "")))

    logging.debug(f"Total transfers after appending new ones: {len(all_transfers)}")

    ### Drop everything from before the season start or league reset.
    ### This runs on the merged list, so a cache still holding pre-reset events is
    ### repaired here instead of having to be deleted by hand.
    start_datetime = miscellaneous.get_start_datetime()
    transfer_count = len(all_transfers)
    all_transfers = miscellaneous.filter_transfers_from(all_transfers, start_datetime)

    dropped = transfer_count - len(all_transfers)
    if dropped:
        logging.info(f"Ignored {dropped} transfer(s) from before START_DATE ({start_datetime.isoformat()}).")

    ### Save updated transfers back to all_transfers.json.
    ### The cache stays the raw record of what the API said. Reverted bookings are
    ### dropped below, for the calculation only, so a later correction can still be seen.
    miscellaneous.write_json_to_file(all_transfers, "all_transfers.json")
    logging.debug("Updated all_transfers.json with new transfers")

    ### A booking an admin reverted stays in the feed, and an unpaired leftover sale would
    ### have a start of season market value invented for it as its buy price
    all_transfers = miscellaneous.drop_reverted_transfers(all_transfers)

    ### The feed names managers, it does not identify them. Everything downstream keys on
    ### the ID that this index resolves the name to.
    with open(path.join(DATA_DIR, "STATIC_users.json"), "r") as f:
        league_users = json.load(f)
    name_to_id = miscellaneous.build_user_name_index(league_users)

    ### Process the transfers as usual
    transfers = []

    ### Process each transfer item
    for item in all_transfers:
        ### Determine the transfer type based on the type and metadata
        if item["t"] == 15:
            if "slr" in item["data"] and "byr" in item["data"]:
                transfer_type = "sell"
                manager = item["data"]["slr"]
                trade_partner = item["data"]["byr"]
            elif "slr" in item["data"]:
                transfer_type = "sell"
                manager = item["data"]["slr"]
                trade_partner = "Kickbase"
            elif "byr" in item["data"]:
                transfer_type = "buy"
                manager = item["data"]["byr"]
                trade_partner = "Kickbase"
            else:
                transfer_type = "unknown"
        else:
            transfer_type = "unknown"

        ### A booking naming neither side cannot be attributed. Carrying on would reuse the
        ### previous iteration's manager and trade partner, which are still bound here.
        if transfer_type == "unknown":
            logging.warning(f"Skipping activity feed item {item['i']} from {item['dt']}: "
                            "it names neither a buyer nor a seller.")
            continue

        ### Search the stats of the given player ID to fill the missing attributes for the player
        player_stats = leagues.player_statistics(user_token, selected_league.id, item["data"]["pi"])

        ### "Kickbase" is this project's own label for the market, not a manager, so it has
        ### no user ID to look up
        trade_partner_id = None
        if trade_partner != "Kickbase":
            trade_partner_id = miscellaneous.resolve_user_id(trade_partner, name_to_id)

        ### Create a custom json dict for every transfer.
        ### The names stay in, because the frontend shows them. The IDs are what everything
        ### joins on: balances() and calculate_revenue_data_daily() both read this file back.
        transfers.append({
            "date": item["dt"],
            "type": transfer_type,
            "user": manager,
            "userId": miscellaneous.resolve_user_id(manager, name_to_id),
            "tradePartner": trade_partner,
            "tradePartnerId": trade_partner_id,
            "price": item["data"]["trp"],
            "playerId": item["data"]["pi"],
            "teamId": item["data"]["tid"],
            "firstName": player_stats.get("fn", None),
            "lastName": player_stats["ln"],
        })

    ### Removes duplicates given by the API (probably not needed since v4)
    transfers = list({frozenset(item.items()): item for item in transfers}.values())

    turnovers = []

    ### Iterate over every element in the "transfers" list (where "i" is the index) and save it to "buy_transfer"
    for i, buy_transfer in enumerate(transfers):
        ### Skip if the transfer is type "sell"
        if buy_transfer["type"] == "sell":
            continue

        ### This nested loop iterates over the remaining transfers (starting from the current buy transfer).
        ### It compares each of these transfers with the current buy transfer
        for sell_transfer in transfers[i:]:
            if sell_transfer["type"] == "buy":
                continue

            ### This condition checks if the player ID of the current sell transfer matches the player ID of the current buy transfer. 
            ### If there is a match, it means a corresponding buy-sell pair is found.
            if sell_transfer["playerId"] == buy_transfer["playerId"]:
                turnovers.append((buy_transfer, sell_transfer))
                break

    ### Revenue generated by randomly assigned players
    for transfer in transfers:
        ### Skip buy transfers
        if transfer["type"] == "buy":
            continue

        ### This condition checks if the current sell transfer is not already part of a buy-sell pair in the turnovers list.
        if transfer not in [turnover[1] for turnover in turnovers]:

            ### Loop through all marketValues of the player until the "day" matches the START_DATE.
            ### Market values exist per day, so only the date part is used here.
            start_date = start_datetime.strftime("%d.%m.%Y")

            ### Search the stats of the given player ID to fill the missing attributes for the player
            player_marketvalues = leagues.player_marketvalue(user_token, transfer["playerId"])

            ### Set the price to the START_DATE value in the player_marketvalues list
            ### Do this because the player was assigned at the start of the season
            price = None

            for marketValue in player_marketvalues:
                ### Convert the Julian date to a standard date
                market_value_date = miscellaneous.julian_to_date(marketValue["dt"])

                if market_value_date == start_date:
                    price = marketValue["mv"]
                    logging.debug(f"Starter player {transfer['firstName']} {transfer['lastName']} was sold! Market value on START_DATE {start_date}: {price}€.")
                    break

            ### Without a market value on START_DATE there is no buy price to work with.
            ### Skip the transfer instead of reusing the previous player's price, which
            ### would silently distort the revenue numbers.
            if price is None:
                logging.warning(f"No market value found for {transfer['firstName']} {transfer['lastName']} on START_DATE {start_date}. Skipping this sell transfer.")
                continue

            ### If an unmatched sell transfer is found, a simulated buy transfer is created with some default values
            date = start_datetime.isoformat()
            buy_transfer = {"date": date,
                            "type": "assigned_at_start",
                            "user": transfer["user"],
                            "userId": transfer["userId"],
                            "tradePartnerId": None,
                            "tradePartner": "Kickbase",
                            "price": price,
                            "playerId": transfer["playerId"],
                            "teamId": transfer["teamId"],
                            "firstName": transfer["firstName"],
                            "lastName": transfer["lastName"],
                        }

            turnovers.append((buy_transfer, transfer))

    final_turnovers += turnovers

    logging.info("Got all turnovers.")

    ### Save to file + timestamp
    miscellaneous.write_json_to_file(final_turnovers, "turnovers.json")
    miscellaneous.write_timestamp("ts_turnovers.json", rows=len(final_turnovers))

    ### Calculate revenue data for the graph
    miscellaneous.calculate_revenue_data_daily(final_turnovers)


def team_value_per_match_day(user_token: str, selected_league: object) -> None:
    """### Calculates the team value per match day for all users in the league.

    Args:
        user_token (str): The user's kkstrauth token.
        selected_league (object): The league the user wants to get data from for the frontend.
    """
    logging.info("Calculating team value per match day...")

    final_team_value = {}

    ### Get all match days of the season
    current_match_day, match_days_list = competitions.match_days(user_token)

    with open(path.join(DATA_DIR, "STATIC_users.json"), "r") as f:
        league_users = json.load(f)

    ### One request per match day, not one per manager and match day. A ranking response
    ### already carries every manager's team value, so asking again for each of them was
    ### 34 requests worth of information spread over 340.
    team_values_per_match_day = {}

    for match_day in match_days_list:
        ### Skip processing if the match day is in the future
        if match_day["day"] > current_match_day:
            continue

        ranking_data = leagues.ranking(user_token, selected_league.id, match_day["day"])
        team_values_per_match_day[match_day["day"]] = {
            real_user["i"]: real_user["tv"] for real_user in ranking_data["us"]
        }

    ### Loop through all users in the league
    for user_id, user_name in league_users.items():
        ### Get the team value for each match day
        team_value = {match_day: 0 for match_day in range(1, current_match_day + 1)}

        for day, team_values_by_user in team_values_per_match_day.items():
            ### A manager missing from a ranking has no team value for that day, which is
            ### not the same as a team value of zero
            if len(team_value) >= day:
                team_value[day] = team_values_by_user.get(user_id)

        final_team_value[user_name] = team_value

    logging.info("Calculated team value per match day.")

    ### Save to file + timestamp
    miscellaneous.write_json_to_file(final_team_value, "team_values.json")
    miscellaneous.write_timestamp("ts_team_values.json", rows=len(final_team_value))


def league_user_stats_tables(user_token: str, selected_league: object) -> None:
    """### Retrieves the statistics for all users in the league.

    Args:
        user_token (str): The user's kkstrauth token.
        selected_league (object): The league the user wants to get data from for the frontend.
    """
    logging.info("Getting league user stats...")

    final_user_stats = []

    ### Loop through all users in the league
    with open(path.join(DATA_DIR, "STATIC_users.json"), "r") as f:
        league_users = json.load(f)

    ### Normally a no-op, since balances() runs first and fills the cache
    miscellaneous.prefetch_profilepics(league_users.keys())

    for user_id, user_name in league_users.items():
        ### Get stats for each user
        user_stats = leagues.user_stats(user_token, selected_league.id, user_id)

        ### Find the user's points in the specific battle
        def get_user_points(battle_type):
            battles_data = leagues.battles(user_token, selected_league.id, battle_type)
            for entry in battles_data["us"]:
                if entry["u"]["i"] == user_id:
                    return entry["v"]
            return 0

        ### Create a custom json list for every user
        final_user_stats.append({
            ### Shared stats 
            "userId": user_id,
            "userName": user_name,
            "profilePic": miscellaneous.get_profilepic(user_id),
            "mdWins": user_stats["mdw"],
            "maxPoints": get_user_points(8),
            ### Stats for "Liga -> Tabelle" ONLY
            "placement": user_stats["pl"],
            "points": user_stats.get("tp", 0),
            "teamValue": user_stats["tv"],
            # "maxBuyPrice": user_stats["leagueUser"]["maxBuyPrice"],
            # "maxBuyFirstName": user_stats["leagueUser"]["maxBuyFirstName"],
            # "maxBuyLastName": user_stats["leagueUser"]["maxBuyLastName"],
            # "maxSellPrice": user_stats["leagueUser"]["maxSellPrice"],
            # "maxSellFirstName": user_stats["leagueUser"]["maxSellFirstName"],
            # "maxSellLastName": user_stats["leagueUser"]["maxSellLastName"]
            ### Stats for "Liga -> Saison Statistiken" ONLY
            "avgPoints": user_stats["ap"],
            # "minPoints": get_season_stat(user_stats, "minPoints"),
            # "bought": get_season_stat(user_stats, "bought"),
            # "sold": get_season_stat(user_stats, "sold"),
            "trades": user_stats.get("t", 0),
            ### Stats for "Liga -> Battles" ONLY
            "pointsGoalKeeper": get_user_points(4),
            "pointsDefenders": get_user_points(5),
            "pointsMidFielders": get_user_points(6),
            "pointsForwards": get_user_points(7),
            # "avgGoalKeeper": user_stats["seasons"][0]["averageGoalKeeper"],
            # "avgDefenders": user_stats["seasons"][0]["averageDefenders"],
            # "avgMidFielders": user_stats["seasons"][0]["averageMidFielders"],
            # "avgForwards": user_stats["seasons"][0]["averageForwards"],
        })            

    logging.info("Got league user stats.")

    ### Save to file + timestamp
    miscellaneous.write_json_to_file(final_user_stats, "league_user_stats.json")
    miscellaneous.write_timestamp("ts_league_user_stats.json", rows=len(final_user_stats))


def live_points(user_token: str, selected_league: object) -> list:
    """### Retrieves the live points for the players in a users team.

    Args:
        user_token (str): The user's kkstrauth token.
        selected_league (object): The league the user wants to get data from for the frontend.

    Returns:
        list: The live points of every user in the league, including their players.
    """
    logging.info("Getting live points...")

    ### Get the current live points
    live_points = leagues.live_points(user_token, selected_league.id)

    ### Create a custom json dict for every user and his players
    final_live_points = []

    for real_user in live_points["u"]:
        ### Create a custom json dict for every player of the user
        players = []

        for player in real_user["pl"]:
            players.append({
                "playerId": player["id"],
                "teamId": player["tid"],
                "firstName": player.get("fn", ""),
                "lastName": player["n"],
                "number": player["nr"],
                "points": player["t"],
                "goals": player["g"],
                "assists": player["a"],
                "redCards": player["r"],
                "yellowCards": player["y"],
                "yellowRedCards": player["yr"],
                ### Custom attributes for the frontend
                "fullName": f"{player.get('fn', '')} {player['n']} ({player['nr']})",
            })

        final_live_points.append({
            "userId": real_user["id"],
            "userName": real_user["n"],
            "livePoints": real_user["t"],
            "totalPoints": real_user["st"],
            "players": players,
        })

    logging.info("Got live points.")

    ### Save to file + timestamp
    miscellaneous.write_json_to_file(final_live_points, "live_points.json")
    miscellaneous.write_timestamp("ts_live_points.json", rows=len(final_live_points))

    return final_live_points


def max_bid(team_value: float, balance: float) -> float:
    """### The most a manager could bid, given a team value and a balance.

    A manager may go negative by up to a third of team value plus balance, so the room
    left is that limit reduced by however far they are in the red already.

    Args:
        team_value (float): The manager's team value.
        balance (float): The manager's balance.

    Returns:
        float: The highest possible bid, never below zero.
    """
    max_negative_balance = (team_value + balance) * 0.33

    if balance < 0:
        return max(0, max_negative_balance + balance)

    return max(0, max_negative_balance)


def balances(user_token: str, selected_league: object, own_user_id: str) -> None:
    """### Retrieves the estimated balances for all users in the league, together with the
    events that produced them.

    Every user is written twice: once counting transfers only, and once with the daily
    login bonus and the achievement rewards folded in. The second view is an estimate on
    two counts - a daily login is assumed for everyone, because the feed only reveals the
    logged in user's, and the achievements are derived from the current standings rather
    than read from the feed.

    Args:
        user_token (str): The user's kkstrauth token.
        selected_league (object): The league the user wants to get data from for the frontend.
        own_user_id (str): The logged in user's ID, to mark their own row as "isSelf".
    """
    logging.info("Getting balances...")

    initial_balance = float(getenv("START_MONEY", 50000000))
    final_balances = []

    ### Everything from before the season start or league reset belongs to a previous
    ### season and must not count towards this balance, the same cutoff turnovers() uses.
    start_datetime = miscellaneous.get_start_datetime()

    ### Get all transfers from the API
    all_transfers = leagues.transfers(user_token, selected_league.id)
    logging.debug(f"Found {len(all_transfers)} transfers in total")

    ### Cut to this season before looking for reverted bookings, the same order turnovers()
    ### uses. Across a reset the ownership chain contradicts itself by design - players are
    ### reassigned - and the warnings would name bookings nobody counts anyway.
    all_transfers = miscellaneous.filter_transfers_from(all_transfers, start_datetime)

    ### A booking an admin reverted stays in the feed, so it would be counted twice
    all_transfers = miscellaneous.drop_reverted_transfers(all_transfers)

    ### Read the league members
    with open(path.join(DATA_DIR, "STATIC_users.json"), "r") as f:
        league_users = json.load(f)

    ### The feed names managers by display name only, so every attribution below goes
    ### through this one index instead of comparing names directly
    name_to_id = miscellaneous.build_user_name_index(league_users)

    ### Look the profile pictures up all at once. A user without one costs a full
    ### timeout, so doing them one by one dominated the runtime of this function.
    miscellaneous.prefetch_profilepics(league_users.keys())

    ### Achievements earned in earlier runs. Detection looks at the current standings, so
    ### without this an achievement would vanish again once the condition stops holding -
    ### a team value can fall back below the threshold. The file also carries the date the
    ### running balance needs, since the real one cannot be derived.
    achievements_path = path.join(DATA_DIR, "achievements.json")
    earned_per_user = {}

    if path.exists(achievements_path):
        try:
            with open(achievements_path, "r") as f:
                earned_per_user = json.load(f)
        except json.JSONDecodeError:
            logging.warning(f"{achievements_path} is empty or invalid. Starting over.")

    ### Turnovers per manager, for the lucky touch family. turnovers() runs after this
    ### function, so the file is one run behind - an achievement shows up a run late.
    turnovers_by_user = {}
    turnovers_path = path.join(DATA_DIR, "turnovers.json")

    if path.exists(turnovers_path):
        try:
            with open(turnovers_path, "r") as f:
                for buy, sell in json.load(f):
                    ### A turnovers.json written before this change carries no "userId"
                    ### yet, so the display name is still good for one fallback
                    seller_id = sell.get("userId") or miscellaneous.resolve_user_id(sell["user"], name_to_id)
                    turnovers_by_user.setdefault(seller_id, []).append((buy, sell))
        except json.JSONDecodeError:
            logging.warning(f"{turnovers_path} is empty or invalid. No transfer achievements.")

    now = datetime.now(timezone.utc)

    ### The season titles only settle once the last matchday has been played
    season_over = miscellaneous.season_is_over(now)

    ### The same for every manager: a daily login is assumed for all of them alike, because
    ### the feed reveals the real bonuses only for the logged in user.
    bonus_events = miscellaneous.build_login_bonus_events(start_datetime, now)

    ### Loop through all users in the league
    for user_id, user_name in league_users.items():
        user_stats = leagues.user_stats(user_token, selected_league.id, user_id)
        team_value = user_stats["tv"]
        logging.debug(f"Team value of {user_name}: {team_value}")

        ### The events are the balance: the last one carries the current figure, and the
        ### frontend shows the same list behind the Kontostand column.
        events = miscellaneous.build_balance_events(all_transfers, user_id, name_to_id, initial_balance, start_datetime)
        balance = events[-1]["balance"]

        logging.debug(f"User: {user_name}; Starter balance: {initial_balance}; Balance after {len(events) - 1} transfer(s): {balance}")

        ### Everything below is an estimate, which is why it stays in its own fields.
        ### The team value reward is withheld in the red, so it has to be judged against
        ### the balance including the login bonuses - not against the transfers alone.
        balance_for_reward_check = balance + sum(e["amount"] for e in bonus_events)

        earned_now = miscellaneous.detect_achievements(
            user_stats.get("t", 0),
            team_value,
            balance_for_reward_check,
            turnovers_by_user.get(user_id, []),
            user_stats["mdw"],
            miscellaneous.matchday_points(
                leagues.user_performance(user_token, selected_league.id, user_id)),
            user_stats["pl"],
            season_over,
        )

        earned_per_user[user_id] = miscellaneous.merge_earned_achievements(
            earned_per_user.get(user_id, []), earned_now, now, start_datetime)

        achievement_events = [{
            "date": a["earnedAt"],
            "type": "achievement",
            "amount": a["amount"],
            "balance": None,
            "achievementName": a["name"],
            "playerName": None,
            "playerImage": None,
            "teamId": None,
            "tradePartner": None,
        } for a in earned_per_user[user_id]]

        events_with_bonuses = miscellaneous.merge_balance_events(
            events, bonus_events, achievement_events)
        balance_with_bonuses = events_with_bonuses[-1]["balance"]

        ### Create a custom json dict for every user
        final_balances.append({
            "userId": user_id,
            "username": user_name,
            ### Which of these managers is the user. The auction solver needs it to leave
            ### the user out of their own rival set and to cap a suggested bid at their own
            ### ceiling; nothing else in the frontend knew who "you" are.
            "isSelf": str(user_id) == str(own_user_id),
            "profilePic": miscellaneous.get_profilepic(user_id),
            "teamValue": team_value,
            "balance": balance,
            "maxBid": round(max_bid(team_value, balance), 0),
            "events": events,
            "balanceWithBonuses": balance_with_bonuses,
            "maxBidWithBonuses": round(max_bid(team_value, balance_with_bonuses), 0),
            "eventsWithBonuses": events_with_bonuses,
        })

    logging.info("Got balances.")

    ### Save to file + timestamp
    miscellaneous.write_json_to_file(final_balances, "balances.json")
    miscellaneous.write_json_to_file(earned_per_user, "achievements.json")
    miscellaneous.write_timestamp("ts_balances.json", rows=len(final_balances))

### -------------------------------------------------------------------
### -------------------------------------------------------------------
### -------------------------------------------------------------------

def run_once() -> runs.RunManifest:
    """### Run main() and leave a record of it, whatever happens.

    Lives out here rather than inside the `if __name__` block so it can be tested. The
    block below held the part that decides whether a run is reported as a success, which
    is exactly the part worth a test.

    Returns:
        runs.RunManifest: The record, already written to disk.
    """
    start_time = time.time()

    ### The manifest is created out here, not inside main(), so it survives main() dying.
    manifest = runs.RunManifest(runs.start_run())

    try:
        main(manifest)
    except BaseException as e:
        ### BaseException, not Exception. A bare exit() anywhere down the call stack
        ### raises SystemExit, which is not an Exception and slipped past every handler on
        ### the way up: the run died silently, the previous manifest stayed on disk, every
        ### dataset still carried that run's id, and the frontend rendered green over a run
        ### that never happened. On top of that, exit() with no argument is SystemExit(None),
        ### which the shell reads as success.
        ###
        ### A KeyboardInterrupt lands here too, and recording it as a failed run is the
        ### honest answer: the run really did not finish.
        logging.exception(f"The run ended before it could finish: {type(e).__name__}: {e}")
        manifest.record_failure("run", f"{type(e).__name__}: {e}")

    manifest.finish()

    ### The manifest first: it is what the timestamp below now depends on.
    miscellaneous.write_json_to_file(manifest.to_dict(), "ts_run_manifest.json")

    ### Timestamp for frontend.
    ###
    ### This used to be written unconditionally, with nothing but the time in it. A run
    ### that died at the first stage stamped itself as fresh and the frontend rendered it
    ### green - so hours old market values were indistinguishable from current ones, and
    ### the only way to find out was to notice the numbers had stopped moving.
    ###
    ### "time" therefore no longer means "the data is from now". It means "a run ended
    ### now", and "allOk" says whether that run produced anything worth trusting.
    miscellaneous.write_json_to_file({
        "time": datetime.now().isoformat(),
        "runId": manifest.run_id,
        "allOk": manifest.all_ok,
        "failedStages": manifest.failed_stages,
    }, "ts_main.json")

    runs.end_run()

    elapsed_time_seconds = time.time() - start_time
    minutes = int(elapsed_time_seconds // 60)
    seconds = int(elapsed_time_seconds % 60)
    logging.info(f"DONE in {minutes}m {seconds}s. {manifest.summary()}")

    return manifest


if __name__ == "__main__":
    ### Try to get the logins and Discord URL from the environment variables (Docker)
    kb_mail = getenv("KB_MAIL")
    kb_password = getenv("KB_PASSWORD")
    discord_webhook = getenv("DISCORD_WEBHOOK")

    ### -------------------------------------------------------------------

    tprint("\n\nKB-Insights")
    print("\x1B[3mby casudo\x1B[0m")
    print(f"\x1B[3m{__version__}\x1B[0m\n\n")

    ### The exit code is the other half of not lying. entrypoint.py runs this with
    ### subprocess.run() and has never looked at the result; once it does, a failed run
    ### can be seen from outside the container without reading a JSON file.
    exit(0 if run_once().all_ok else 1)