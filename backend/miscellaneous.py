"""
### This module holds all functions and constants that are not related to Kickbase API in any point.

TODO: Maybe list all functions here automatically?
"""

import requests
import json
import logging

import pandas as pd
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta, timezone
from os import getenv, path, makedirs
from zoneinfo import ZoneInfo
from backend.paths import DATA_DIR, TIMESTAMP_DIR

from backend import exceptions

### ===============================================================================

### Seconds to wait for a profile picture before giving up and treating it as unset.
### The CDN answers in well under a second when a picture exists, but takes about 20
### seconds to report a missing one, with GET and with HEAD alike. That accounted for
### 253 of the 259 seconds balances() spent.
PROFILEPIC_TIMEOUT = 5

### How many profile picture lookups to run at once. They are almost entirely spent
### waiting, so threads suit them, and a league has at most a few dozen managers.
MAX_PROFILEPIC_WORKERS = 16

### The activity feed references player photos as relative paths. This is the CDN that
### serves them.
PLAYER_IMAGE_BASE_URL = "https://kickbase.b-cdn.net/"

### The daily login bonus grows by this much per day and stops at the cap. Confirmed
### against the real type 22 feed events: day 2 pays 10.000, day 11 and every day after
### pay 100.000.
LOGIN_BONUS_STEP = 10_000
LOGIN_BONUS_CAP = 100_000

### Per-run cache for profile pictures. Each lookup downloads the full image, and both
### balances() and league_user_stats_tables() ask for every user.
_profilepic_cache = {}


def clear_caches() -> None:
    """### Empty the per-run caches held in this module."""
    _profilepic_cache.clear()


POSITIONS = {1: "TW", 2: "ABW", 3: "MF", 4: "ANG"}
### 0 = Vereinslos oder sehr neue Spieler in der Liga

### TREND (can be found via player stats)
# 0: Gleichbleibend (500k player) (Welcher Zeitraum?)
# 1: Steigt
# 2: Sinkt
### Conversion from number to icon for the frontend in "SharedConstants.js"

### STATUS (can be found via player stats)
# 0: Fit (Green Checkmark)
# 1: Verletzt (Red Cross)
# 2: Angeschlagen (bandage)
# 4: Aufbautraining (Orange Cone)
# 8: Rote Karte (Red Card)
# 32: 5. Gelbe Karte (Yellow Card)
# 128: Raus aus der Liga (Red Arrow)
# 256: Abwesend (Grey Clock)
### Conversion from number to icon for the frontend in "SharedConstants.js"

### TYPE (from Activity Feed v4)
# Type 3: New on Transfer Market/Free player listed by Kickbase (Cannot be seen when using Postman?! Only seen in the app (probably because target is set))
# Type 5: User joined the Kickbase league
# Type 15 + data[byr]: User bought player from Kickbase
# Type 15 + data[slr]: User sold player to Kickbase
# Type 15 + data[slr] + data[byr]: User sold player to User
# Type 17: Matchday final points and ranking
# Type 22: Daily Login Bonus
# Type 26: Achievement

### ===============================================================================

def discord_notification(title: str, message: str, color: int, webhook_url: str) -> None:
    """### Send a Discord notification to a webhook.

    Args:
        title (str): Title of the notification.
        message (str): Message of the notification.
        color (int): Color of the notification.
        webhook_url (str): Webhook URL to send the notification to.

    Raises:
        WIP! TODO!
    """
    url = webhook_url
    headers = {"Content-Type": "application/json"}
    payload = {
        "username": "Kickbase",
        "avatar_url": "https://upload.wikimedia.org/wikipedia/commons/2/2c/Kickbase_Logo.jpg",
        "embeds": [
            {
                "title": title,
                "description": message,
                "color": color
            }
        ]
    }

    ### Send POST request to Webhook
    try:
        requests.post(url, json=payload, headers=headers)
    except:
        raise exceptions.NotificatonException("Notification failed! Please check your Discord Webhook URL.")


def calculate_revenue_data_daily(turnovers: dict) -> None:
    """### Calculate daily revenue data.

    Args:
        turnovers (dict): A dictionary containing all buy-sell pairs.
    """
    logging.info("Calculating daily revenue data...")

    ### Load STATIC_users.json
    with open(path.join(DATA_DIR, "STATIC_users.json"), "r") as f:
        league_users = json.load(f)

    ### Create an empty dict with all user names as keys
    user_transfer_revenue = {user_name: [] for user_name in league_users.values()}

    ### This loop iterates over each buy-sell pair in the turnovers list. It calculates the revenue by subtracting the buy value from the sell value.
    ### The revenue and the date of the sell transfer are then appended to the corresponding user's list in user_transfer_revenue.
    
    for buy, sell in turnovers:
        revenue = sell["price"] - buy["price"]
        if buy["user"] in league_users.values():
            user_transfer_revenue[buy["user"]].append((revenue, sell["date"]))

    ### Add start and end points for the graph.
    ### Both are timezone aware UTC, so they line up with the feed timestamps that make
    ### up the rest of the series instead of being shifted by the local timezone.
    for _, data in user_transfer_revenue.items():
        data.append((0, get_start_datetime()))
        data.append((0, datetime.now(timezone.utc)))

    ### This section converts the data in user_transfer_revenue into Pandas DataFrames.
    ### It performs operations to aggregate daily revenues and calculates cumulative sums.
    ### The resulting DataFrames are stored in the dataframes dictionary.
    dataframes = {}
    for user, data in user_transfer_revenue.items():
        df = pd.DataFrame(data, columns=["revenue", "date"])
        df["date"] = pd.to_datetime(df["date"], utc=True)
        df = df.groupby(pd.Grouper(key="date", freq="D"))["revenue"] \
            .sum().reset_index().sort_values("date")
        df["revenue"] = df["revenue"].cumsum()
        df["date"] = df["date"].dt.strftime("%Y-%m-%d")

        dataframes[user] = df

    ### Here, the data is formatted into a dictionary called data.
    ### Each user's name is a key, and the corresponding value is a list of tuples containing revenue and date information
    data = {user_name: [] for user_name in league_users.values()}
    for user, df in dataframes.items():
        for entry in df.to_numpy().tolist():
            data[user].append((entry[0], entry[1]))

    logging.info("Calculated daily revenue data.")

    ### Save to file + timestamp
    write_json_to_file(data, "revenue_sum.json")
    write_json_to_file({"time": datetime.now().isoformat()}, "ts_revenue_sum.json")


def get_player_owner(player_stats: dict, league_id: str) -> dict:
    """### Find out which manager owns a player in the given league.

    Kickbase reports ownership per league in the "opl" list, one entry per league the
    player is owned in. The top level "oui" field still exists but is always "0", so it
    cannot be used: checking it classified every player as free.

    Args:
        player_stats (dict): A player_statistics response.
        league_id (str): The league to look up ownership for.

    An entry for the league is present even when nobody owns the player, carrying an
    owner id of "0". Matching on the league alone therefore reports every unowned player
    as owned by "Unknown", so the owner id has to be checked as well.

    Returns:
        dict: The matching "opl" entry, holding the owner id in "oui" and the owner name
            in "onm". None if nobody in this league owns the player.
    """
    for entry in player_stats.get("opl") or []:
        if entry.get("li") != league_id:
            continue

        ### "0", "", None and a missing key all mean nobody owns the player
        owner_id = entry.get("oui")
        if not owner_id or str(owner_id) == "0":
            return None

        return entry

    return None


def market_value_deltas(market_value_history: list) -> dict:
    """### Work out how much a player's market value moved over the usual periods.

    The history is oldest first, one entry per day, so the last entry is today.

    Any delta the history is too short to cover is None rather than an error: a player
    who was only recently added to the competition has just a handful of entries, and
    indexing past the start of the list would kill the whole run.

    The key names are the ones the frontend already reads in
    "MarketValueChangesTable.js". The two "Avg" keys are differences over the period,
    not averages, but renaming them is a frontend change for no gain.

    Args:
        market_value_history (list): A player_marketvalue response, each entry with "mv".

    Returns:
        dict: today, yesterday, twoDays, sevenDaysAvg and thirtyDaysAvg.
    """
    history = market_value_history or []

    def delta(newer: int, older: int):
        ### Both indices count back from the end, so the older one decides the length needed
        if len(history) < abs(older):
            return None
        return history[newer]["mv"] - history[older]["mv"]

    return {
        "today": delta(-1, -2),
        "yesterday": delta(-2, -3),
        "twoDays": delta(-3, -4),
        "sevenDaysAvg": delta(-1, -8),
        "thirtyDaysAvg": delta(-1, -31),
    }


def get_start_datetime() -> datetime:
    """### Parse the START_DATE environment variable.

    START_DATE is the instant the season started or the league was reset. Activity feed
    events from before it are ignored, so it has to be an exact instant: a league can be
    reset partway through a day.

    Raises:
        exceptions.KickbaseException: If START_DATE is missing or not a valid ISO 8601
            timestamp with an explicit UTC offset.

    Returns:
        datetime: The start instant, as a timezone aware UTC datetime.
    """
    raw = getenv("START_DATE")

    if not raw:
        raise exceptions.KickbaseException(
            "START_DATE is not set. Set it to the instant your season started or your "
            "league was reset, e.g. 2026-08-01T18:00:00Z."
        )

    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise exceptions.KickbaseException(
            f"START_DATE '{raw}' is not a valid ISO 8601 timestamp. Use e.g. "
            "2026-08-01T18:00:00Z. The old dd.mm.yyyy format is no longer accepted, "
            "because reading it as midnight would silently shift every result."
        )

    ### Without an offset there is no way to tell UTC from local time, and the feed is UTC
    if parsed.tzinfo is None:
        raise exceptions.KickbaseException(
            f"START_DATE '{raw}' has no UTC offset. Add one, e.g. 2026-08-01T18:00:00Z, "
            "so the cutoff cannot shift with the local timezone."
        )

    return parsed.astimezone(timezone.utc)


def parse_feed_timestamp(timestamp: str) -> datetime:
    """### Convert an activity feed timestamp to a timezone aware UTC datetime.

    Args:
        timestamp (str): A feed timestamp, e.g. "2026-08-01T16:43:17Z".

    Returns:
        datetime: The timestamp as a timezone aware UTC datetime.
    """
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone(timezone.utc)


def filter_transfers_from(transfers: list, cutoff: datetime) -> list:
    """### Drop activity feed items from before the cutoff.

    Used to ignore events that happened before a league reset. The boundary is
    inclusive: an item exactly on the cutoff is kept.

    Args:
        transfers (list): Activity feed items, each with a "dt" timestamp.
        cutoff (datetime): The timezone aware start instant.

    Returns:
        list: The items at or after the cutoff, in their original order.
    """
    return [item for item in transfers if parse_feed_timestamp(item["dt"]) >= cutoff]


### Sentinel for "nobody has bought this player yet", which is not the same as "free".
### A manager selling a player nobody ever bought had them assigned at the season start.
_UNCLAIMED = object()


def drop_reverted_transfers(transfers: list) -> list:
    """### Drop activity feed bookings that a league admin reverted.

    Kickbase emits no cancellation event. A reverted transfer stays in the feed next to
    the booking that replaced it, and both get counted: the seller is paid twice, the
    buyer charged twice, and turnovers() invents a second sale out of the leftover.

    Ownership is what gives it away, because nobody can sell a player they do not own.
    Replaying each player's chain finds the contradiction, and the later booking is the
    one that stuck - verified against the live squads for the 2026-08-08 incident in
    league Kickbase-Elite 26/27.

    A reversal with no replacement booking leaves no contradiction behind and cannot be
    found this way. Only the live squads would show it.

    Args:
        transfers (list): Activity feed items with "t" == 15, in any order.

    Returns:
        list: The surviving items, in the order they were passed in.
    """
    def owner_after(item):
        """Who holds the player once this booking went through. None means Kickbase."""
        return item["data"].get("byr")

    def is_possible(item, owner):
        """Whether this booking can follow on from the current owner."""
        seller = item["data"].get("slr")

        if seller is not None:
            ### Selling requires owning, or having been assigned the player at the start
            return owner is _UNCLAIMED or owner == seller

        ### Buying off the market requires the player to be on it
        return owner is _UNCLAIMED or owner is None

    by_player = {}
    for item in sorted(transfers, key=lambda item: parse_feed_timestamp(item["dt"])):
        by_player.setdefault(item["data"]["pi"], []).append(item)

    reverted = set()

    for player_id, chain in by_player.items():
        kept = []
        ### owners[-1] is the owner after everything in kept; owners[0] is the start state
        owners = [_UNCLAIMED]

        for item in chain:
            ### Walk back over already accepted bookings until this one becomes possible.
            ### Never past the start of the chain: dropping everything would lose more
            ### than the reversal did.
            while not is_possible(item, owners[-1]) and kept:
                dropped = kept.pop()
                owners.pop()
                reverted.add(dropped["i"])

                logging.warning(
                    f"Ignoring reverted booking {dropped['i']} from {dropped['dt']}: "
                    f"{dropped['data'].get('pn')} for {dropped['data']['trp']}€ "
                    f"(seller {dropped['data'].get('slr') or 'Kickbase'}, "
                    f"buyer {dropped['data'].get('byr') or 'Kickbase'}). It contradicts "
                    f"booking {item['i']} from {item['dt']}, which superseded it."
                )

            kept.append(item)
            owners.append(owner_after(item))

    return [item for item in transfers if item["i"] not in reverted]


def build_balance_events(transfers: list, user_name: str, initial_balance: float, start_datetime: datetime) -> list:
    """### Build the list of events that produced a manager's balance.

    The first event is always the starting budget, followed by every buy and sell of that
    manager, oldest first. Each event carries the running balance after it, so the last
    event's balance is the manager's current balance.

    Events from before the start instant are ignored, the same rule turnovers() applies.

    Args:
        transfers (list): Activity feed items with "t" == 15, in any order.
        user_name (str): The manager's display name. The feed names buyer ("byr") and
            seller ("slr") by display name, not by user ID.
        initial_balance (float): The budget every manager starts out with.
        start_datetime (datetime): The season start or league reset instant.

    Returns:
        list: Event dicts, oldest first, starting with the "start" event.
    """
    ### sorted() returns a new list on purpose. The feed is cached per run and shared with
    ### turnovers(), so sorting in place would reorder it for every other caller.
    relevant = sorted(
        filter_transfers_from(transfers, start_datetime),
        key=lambda item: parse_feed_timestamp(item["dt"]),
    )

    balance = initial_balance

    events = [{
        "date": start_datetime.isoformat(),
        "type": "start",
        "amount": round(initial_balance),
        "balance": round(balance),
        "playerName": None,
        "playerImage": None,
        "teamId": None,
        "tradePartner": None,
    }]

    for item in relevant:
        data = item["data"]
        price = data["trp"]

        ### Only one side of a transfer is named when the other side was Kickbase itself
        if data.get("byr") == user_name:
            event_type, amount, trade_partner = "buy", -price, data.get("slr")
        elif data.get("slr") == user_name:
            event_type, amount, trade_partner = "sell", price, data.get("byr")
        else:
            continue

        balance += amount

        player_image = data.get("pim")

        events.append({
            "date": item["dt"],
            "type": event_type,
            "amount": amount,
            "balance": round(balance),
            "playerName": data.get("pn"),
            "playerImage": PLAYER_IMAGE_BASE_URL + player_image if player_image else None,
            "teamId": data.get("tid"),
            "tradePartner": trade_partner,
        })

    return events


def build_login_bonus_events(start_datetime: datetime, until: datetime) -> list:
    """### Build the estimated daily login bonus events for one manager.

    Day 1 is the day the season started and pays nothing. Every day after that pays
    10.000 more than the one before, up to 100.000, which is then paid every day.

    The day counter runs over calendar days in the app timezone rather than over elapsed
    hours. The real feed settles it: day 11 arrived at 01:13 UTC and day 12 at 22:03 UTC
    on the same UTC date, which are two different days in Europe/Berlin. Counting hours
    would fall a day behind and keep drifting.

    The bonus is an assumption. Type 22 feed events exist only for the logged in user, so
    there is no way to tell whether another manager logged in on a given day. Assuming it
    for everyone at least treats them alike.

    Args:
        start_datetime (datetime): The season start or league reset instant.
        until (datetime): The instant to count up to, normally now.

    Returns:
        list: Event dicts of type "login_bonus", oldest first, without a running balance.
    """
    zone = ZoneInfo(getenv("TZ", "Europe/Berlin"))

    first_day = start_datetime.astimezone(zone).date()
    last_day = until.astimezone(zone).date()

    events = []

    for offset in range(1, (last_day - first_day).days + 1):
        day = first_day + timedelta(days=offset)

        events.append({
            ### Dated at the start of that day in the app timezone. The real collection
            ### time depends on when the manager opened the app, which we cannot know.
            "date": datetime.combine(day, time.min, tzinfo=zone).astimezone(timezone.utc).isoformat(),
            "type": "login_bonus",
            "amount": min(LOGIN_BONUS_CAP, offset * LOGIN_BONUS_STEP),
            "balance": None,
            "playerName": None,
            "playerImage": None,
            "teamId": None,
            "tradePartner": None,
        })

    return events


def write_json_to_file(data, file_name: str) -> None:
    """Writes a JSON object to a file.

    Args:
        data (any): data to be written to the file
        file_name (str): file name
    """
    ### Make sure the data directories exist, since app.py can write files before main.py ever ran
    makedirs(TIMESTAMP_DIR, exist_ok=True)

    ### Check if it is a data or timestamp file
    try:
        if file_name.startswith("ts_"):
            file_path = path.join(TIMESTAMP_DIR, file_name)
            with open(file_path, "w") as f:
                json.dump(data, f)
            logging.debug(f"Created timestamp file {file_name}")
        else:
            file_path = path.join(DATA_DIR, file_name)
            with open(file_path, "w") as f:
                json.dump(data, f, indent=2)
            logging.debug(f"Created file {file_name}")
    except Exception as e:
        logging.error(f"Failed to write JSON to {file_path}: {e}")


def julian_to_date(julian_date: int) -> str:
    """Convert a Julian date to a standard date format (YYYY-MM-DD)."""
    reference_date = datetime(1970, 1, 1)
    converted_date = reference_date + timedelta(days=julian_date)
    return converted_date.strftime("%d.%m.%Y")


def get_profilepic(user_id: str) -> str:
    """### Get the profile picture of a user.

    Cached per user for the duration of the run. Each call downloads the full image, and
    balances() and league_user_stats_tables() both ask for every user.

    Call prefetch_profilepics() first to fill the cache concurrently, otherwise every
    user without a picture costs a full timeout one after another.

    Args:
        user_id (str): The user ID.

    Returns:
        str: The URL of the profile picture.
    """
    cache_key = str(user_id)
    if cache_key in _profilepic_cache:
        return _profilepic_cache[cache_key]

    profile_pic = _fetch_profilepic(cache_key)
    _profilepic_cache[cache_key] = profile_pic

    return profile_pic


def prefetch_profilepics(user_ids) -> None:
    """### Look up several profile pictures at once and fill the cache.

    The lookups are independent and almost entirely spent waiting, so they run
    concurrently. For a league where nobody has a picture set this turns one timeout per
    manager into a single timeout for all of them.

    Args:
        user_ids (iterable): The user IDs to look up.
    """
    missing = sorted({str(user_id) for user_id in user_ids} - set(_profilepic_cache))

    if not missing:
        return

    logging.debug(f"Prefetching {len(missing)} profile picture(s)...")

    with ThreadPoolExecutor(max_workers=min(len(missing), MAX_PROFILEPIC_WORKERS)) as executor:
        results = list(executor.map(_fetch_profilepic, missing))

    for user_id, profile_pic in zip(missing, results):
        _profilepic_cache[user_id] = profile_pic


def _fetch_profilepic(user_id: str) -> str:
    """### Ask the CDN for one profile picture, without touching the cache.

    Args:
        user_id (str): The user ID.

    Returns:
        str: The URL of the profile picture, or None if it is not set.
    """
    url = f"https://cdn.kickbase.com/files/users/{user_id}/0"
    headers = {
        "Content-Type": "image/jpeg",
    }

    ### Send GET request to get the profile picture
    try:
        response = requests.get(url, headers=headers, timeout=PROFILEPIC_TIMEOUT)
        if response.status_code == 200:
            profile_pic = response.url # Profile pic is set
        elif response.status_code == 404:
            profile_pic = None # Profile pic is not set
        else:
            response.raise_for_status()
            profile_pic = None
    except requests.exceptions.Timeout:
        ### The CDN takes about 20 seconds to answer for a user without a picture, so a
        ### timeout is the normal case rather than a failure. A missing picture is not
        ### worth holding up the run for.
        logging.debug(f"Profile picture lookup for user {user_id} timed out, treating it as unset.")
        profile_pic = None
    except requests.exceptions.RequestException as e:
        raise exceptions.NotificatonException("Notification failed! Please check your Discord Webhook URL.") from e

    return profile_pic