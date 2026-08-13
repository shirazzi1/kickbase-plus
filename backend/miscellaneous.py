"""
### This module holds all functions and constants that are not related to Kickbase API in any point.

TODO: Maybe list all functions here automatically?
"""

import requests
import json
import logging

import pandas as pd
from collections import Counter
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

### Achievement rewards, from help.kickbase.com/help/erfolge.
###
### Not in here on purpose: the league size achievements (600 Kreisliga, 601 Regionalliga,
### 602 2. Liga). The app shows 1.000.000 each, but they do not reach the balance - three
### real balances only add up without them, and a cutoff at the league reset cannot
### explain it either, since "First deal" was awarded before the reset too and does count.
###
### Where the numbers come from, because they have three different sources:
###
###   - The matchday, lucky touch and season title families, with their thresholds and
###     amounts, are documented on help.kickbase.com/help/erfolge.
###   - The transfer count and team value families are not on that page at all. Their
###     thresholds and amounts were read out of the Kickbase app by the league owner on
###     2026-08-12 and passed on. Reliable, but not independently checkable from here.
###   - The league size achievements (600 Kreisliga, 601 Regionalliga, 602 2. Liga) are
###     deliberately absent. The app shows 1.000.000 each, but they do not reach the
###     balance: three real balances only add up without them, and a cutoff at the league
###     reset cannot explain it either, since "First deal" was awarded before the reset
###     too and does count.
###
### Ids 400, 500 and 501 are Kickbase's own, read off type 26 feed events. The rest are
### ours: those achievements never appeared in a feed we could read, so there was no id to
### copy. They are only ever used as keys into achievements.json.
###
### Not verified against a real balance: the tiers above bronze. None of the three
### managers whose balance was checked against the app crosses one of those thresholds,
### so they carry the app reading alone. If a balance is ever off by exactly one of these
### amounts, this table is where to look.
ACHIEVEMENTS = {
    500: {"name": "First deal", "amount": 100_000},
    501: {"name": "Transfer King bronze", "amount": 250_000},
    502: {"name": "Transfer King silber", "amount": 500_000},
    503: {"name": "Transfer King gold", "amount": 1_000_000},
    400: {"name": "Team value bronze", "amount": 100_000},
    401: {"name": "Team value silber", "amount": 250_000},
    402: {"name": "Team value gold", "amount": 500_000},
    403: {"name": "Team value platin", "amount": 1_000_000},
    404: {"name": "Team value galaktisch", "amount": 2_000_000},
    700: {"name": "Spieltagssieger", "amount": 1_000_000},
    701: {"name": "Spieltagspunkte Silber", "amount": 250_000},
    702: {"name": "Spieltagspunkte Gold", "amount": 500_000},
    703: {"name": "Jahrhundertspiel", "amount": 1_000_000},
    800: {"name": "Meister", "amount": 2_000_000},
    801: {"name": "Vizemeister", "amount": 1_000_000},
}

### Trades needed per tier. Trades between managers count towards these.
TRANSFER_TIERS = [(1, 500), (50, 501), (250, 502), (500, 503)]

### Team value needed per tier. All of them also require a balance in the black.
TEAM_VALUE_TIERS = [(125_000_000, 400), (150_000_000, 401), (200_000_000, 402),
                    (250_000_000, 403), (350_000_000, 404)]

### Profit with a single player and what it pays. Tiers stack, so 6 Mio pays the first two.
### The threshold doubles as the id: these never appeared in the feed, so there is no
### Kickbase id to reuse and none to collide with.
LUCKY_TOUCH_TIERS = [
    (3_000_000, "Bronzenes Händchen", 250_000),
    (5_000_000, "Silbernes Händchen", 500_000),
    (10_000_000, "Goldenes Händchen", 1_000_000),
    (25_000_000, "Königstransfer", 2_000_000),
]

### Points in a single matchday and the achievement they earn. Tiers stack.
MATCHDAY_POINT_TIERS = [(1_000, 701), (1_500, 702), (2_000, 703)]

### Per-run cache for profile pictures. Each lookup downloads the full image, and both
### balances() and league_user_stats_tables() ask for every user.
_profilepic_cache = {}

### Display names out of the activity feed that no manager in the league carries. Kept so
### the warning is logged once per name instead of once per booking.
_unresolved_user_names = set()


def clear_caches() -> None:
    """### Empty the per-run caches held in this module."""
    _profilepic_cache.clear()
    _unresolved_user_names.clear()


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

    ### Create an empty dict with all user IDs as keys. The chart legend needs the names,
    ### but the grouping goes by ID: a manager who renamed themselves mid-season would
    ### otherwise end up as two series, one of them empty.
    user_transfer_revenue = {user_id: [] for user_id in league_users}

    name_to_id = build_user_name_index(league_users)

    ### This loop iterates over each buy-sell pair in the turnovers list. It calculates the revenue by subtracting the buy value from the sell value.
    ### The revenue and the date of the sell transfer are then appended to the corresponding user's list in user_transfer_revenue.

    for buy, sell in turnovers:
        revenue = sell["price"] - buy["price"]
        ### turnovers.json written before this change carries no "userId" yet, so the
        ### display name is still good for one fallback
        user_id = buy.get("userId") or resolve_user_id(buy["user"], name_to_id)

        if user_id in user_transfer_revenue:
            user_transfer_revenue[user_id].append((revenue, sell["date"]))

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
    ### Each user's name is a key, and the corresponding value is a list of tuples containing revenue and date information.
    ### The chart uses the key as the series label, so the ID is translated back here - at
    ### the last possible moment, after all the grouping has been done on the ID.
    data = {user_name: [] for user_name in league_users.values()}
    for user_id, df in dataframes.items():
        user_name = league_users[user_id]

        for entry in df.to_numpy().tolist():
            data[user_name].append((entry[0], entry[1]))

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


### How far back a market value history has to reach.
###
### Two callers decide this. market_value_deltas() reads the last 31 entries, and
### taken_free_players() and turnovers() scan back to START_DATE for the value a player
### assigned at the season start counts as their buy price. So the answer is "31 days, or
### back to the season start, whichever is further" - which early in a season is a small
### fraction of the year that was downloaded for every player before.
###
### The upper bound is the window the API is known to serve, and the one every request used
### to ask for.
MIN_MARKET_VALUE_DAYS = 31
MAX_MARKET_VALUE_DAYS = 365


def market_value_days(now: datetime = None) -> int:
    """### How many days of market value history a run actually needs.

    Falls back to the full year if START_DATE cannot be read. Asking for too much only
    costs bandwidth; asking for too little would invent buy prices.

    Args:
        now (datetime): The instant to measure the season length against, normally now.

    Returns:
        int: A window between MIN_MARKET_VALUE_DAYS and MAX_MARKET_VALUE_DAYS.
    """
    try:
        start_datetime = get_start_datetime()
    except exceptions.KickbaseException:
        return MAX_MARKET_VALUE_DAYS

    now = now or datetime.now(timezone.utc)

    ### Two days of slack: one for the partial day the season started on, one so the
    ### START_DATE entry is never the very first entry of the response
    needed = (now - start_datetime).days + 2

    return max(MIN_MARKET_VALUE_DAYS, min(MAX_MARKET_VALUE_DAYS, needed))


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


def build_user_name_index(league_users: dict) -> dict:
    """### Map manager display names to their user IDs.

    Everything downstream is keyed by user ID, but the activity feed names buyer ("byr")
    and seller ("slr") by display name only - a feed item carries no user ID at all. The
    name therefore has to be resolved exactly once, and this is where it happens. Building
    the same dict inside a transfer loop, as taken_free_players() used to, is both slower
    and one more place for the two to drift apart.

    Two managers sharing a display name are left out rather than guessed at. Attributing
    their bookings to either of them would be wrong, and the old reverse dict silently
    picked whichever came last.

    Args:
        league_users (dict): STATIC_users.json, mapping user ID to display name.

    Returns:
        dict: Display name to user ID, without the ambiguous names.
    """
    ids_by_name = {}

    for user_id, user_name in league_users.items():
        ids_by_name.setdefault(user_name, []).append(user_id)

    index = {}

    for user_name, user_ids in ids_by_name.items():
        if len(user_ids) > 1:
            logging.warning(
                f"{len(user_ids)} managers share the display name '{user_name}' "
                f"(IDs {', '.join(sorted(user_ids))}). The activity feed names them by that "
                "name alone, so their transfers cannot be told apart and are left out."
            )
            continue

        index[user_name] = user_ids[0]

    return index


def resolve_user_id(user_name: str, name_to_id: dict) -> str:
    """### Look up a manager's user ID by the display name the activity feed uses.

    Returns None when no manager in the league carries the name: they renamed themselves
    since the booking, they left the league, or they share the name with someone else. The
    booking then cannot be attributed - which used to happen silently and zeroed the buy
    price of every player involved.

    Args:
        user_name (str): The display name from a feed item, e.g. data["byr"].
        name_to_id (dict): The index from build_user_name_index().

    Returns:
        str: The user ID, or None if the name cannot be resolved.
    """
    if user_name is None:
        return None

    user_id = name_to_id.get(user_name)

    if user_id is None and user_name not in _unresolved_user_names:
        _unresolved_user_names.add(user_name)
        logging.warning(
            f"No manager in this league is called '{user_name}'. Their bookings cannot be "
            "attributed - the name may have changed since, or two managers share it."
        )

    return user_id


def build_balance_events(transfers: list, user_id: str, name_to_id: dict, initial_balance: float, start_datetime: datetime) -> list:
    """### Build the list of events that produced a manager's balance.

    The first event is always the starting budget, followed by every buy and sell of that
    manager, oldest first. Each event carries the running balance after it, so the last
    event's balance is the manager's current balance.

    Events from before the start instant are ignored, the same rule turnovers() applies.

    The manager is identified by user ID, not by display name. The feed only names them,
    so each name is resolved through the shared index first: a manager who renamed
    themselves mid-season no longer quietly collects an empty event list, and two managers
    sharing a name no longer collect each other's transfers.

    Args:
        transfers (list): Activity feed items with "t" == 15, in any order.
        user_id (str): The manager's user ID.
        name_to_id (dict): The index from build_user_name_index().
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

        ### Only one side of a transfer is named when the other side was Kickbase itself.
        ### An unresolvable name gives None, which must never match a manager, so both
        ### sides are checked for that explicitly.
        buyer_id = resolve_user_id(data.get("byr"), name_to_id)
        seller_id = resolve_user_id(data.get("slr"), name_to_id)

        if buyer_id is not None and buyer_id == user_id:
            event_type, amount, trade_partner = "buy", -price, data.get("slr")
        elif seller_id is not None and seller_id == user_id:
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


def detect_achievements(trades: int, team_value: float, balance: float, turnovers: list,
                        matchday_wins: int, matchday_points: list, placement: int,
                        season_over: bool) -> list:
    """### Work out which achievements a manager has earned.

    Only the ones that can be derived from data the project already fetches. Every
    achievement counts once per season, except the matchday win, which pays per win.

    Args:
        trades (int): Transfers made this season. Trades between managers count.
        team_value (float): The manager's current team value.
        balance (float): The manager's balance. Team value rewards are withheld when it
            is negative.
        turnovers (list): The manager's buy/sell pairs, as turnovers.json holds them.
        matchday_wins (int): How many matchdays the manager won.
        matchday_points (list): Points scored on each matchday played so far.
        placement (int): The manager's position in the league.
        season_over (bool): Whether every matchday has been played. The season titles pay
            only then, since the placement moves until the last whistle.

    Returns:
        list: Dicts of {"id", "name", "amount"} for everything earned.
    """
    earned = []

    def award(achievement_id, name=None, amount=None):
        """Record one earned achievement, defaulting to the catalogue entry."""
        entry = ACHIEVEMENTS.get(achievement_id, {})

        earned.append({
            "id": achievement_id,
            "name": name or entry["name"],
            "amount": amount if amount is not None else entry["amount"],
        })

    ### Tiers stack: 250 trades earn the first deal and both Transfer King tiers below it
    for threshold, achievement_id in TRANSFER_TIERS:
        if trades >= threshold:
            award(achievement_id)

    ### Withheld in the red, whatever the team value. This is the rule that explained
    ### three real balances that no simpler model fit.
    ###
    ### The boundary is a guess: the FAQ says "positive account balance", which reads as
    ### strictly greater than zero, and none of the three managers sat at exactly zero to
    ### settle it. If a reward ever goes missing at a balance of 0, this is the line.
    if balance > 0:
        for threshold, achievement_id in TEAM_VALUE_TIERS:
            if team_value >= threshold:
                award(achievement_id)

    ### One entry per win: the only repeatable achievement we can derive
    for _ in range(matchday_wins):
        award(700)

    ### The best single matchday decides which point tiers were reached, each once
    best_matchday = max(matchday_points, default=0)
    for threshold, achievement_id in MATCHDAY_POINT_TIERS:
        if best_matchday >= threshold:
            award(achievement_id)

    ### The placement only settles once the last matchday has been played
    if season_over and placement == 1:
        award(800)
    elif season_over and placement == 2:
        award(801)

    ### The lucky touch family. Both sides have to go through the market, the player must
    ### not have been assigned at the start, and the best single profit decides which
    ### tiers are reached - each of them once for the whole season.
    best_profit = 0
    for buy, sell in turnovers:
        if buy.get("type") == "assigned_at_start":
            continue
        if buy.get("tradePartner") != "Kickbase" or sell.get("tradePartner") != "Kickbase":
            continue

        best_profit = max(best_profit, sell["price"] - buy["price"])

    for threshold, name, amount in LUCKY_TOUCH_TIERS:
        if best_profit >= threshold:
            award(threshold, name=name, amount=amount)

    return earned


def merge_earned_achievements(stored: list, earned_now: list, now: datetime, start_datetime: datetime) -> list:
    """### Fold the achievements detected in this run into the ones already on record.

    Two things have to survive, and keying by id alone loses one of them:

    - **The date of the first sighting.** Most achievements cannot be dated from the data
      we have, so the moment we first saw one is the best date there is.
    - **The count.** The matchday win pays once per win, so three wins have to stay three
      entries. Folding them into one would freeze the counter for the rest of the season,
      because the single stored entry then satisfies every later run.

    Entries earned before start_datetime belong to a previous season or to the state
    before a league reset and are dropped, the same cutoff the transfers use. Without it
    they would keep paying, and merge_balance_events() would sort them in front of the
    starting budget, which breaks reading the balance column downwards.

    A stored entry keeps the amount it was recorded with. Correcting a figure in
    ACHIEVEMENTS therefore only affects achievements sighted after the correction - the
    record stays a record of what was granted, not of what the catalogue says today. To
    apply a correction retroactively, delete achievements.json and let it rebuild.

    Args:
        stored (list): What achievements.json holds for this manager, each with "earnedAt".
        earned_now (list): What detect_achievements() returned, without a date.
        now (datetime): The date to stamp on newly sighted achievements.
        start_datetime (datetime): The season start or league reset instant.

    Returns:
        list: The merged record, oldest first, ready to be written back.
    """
    kept = [a for a in stored if parse_feed_timestamp(a["earnedAt"]) >= start_datetime]

    expired = len(stored) - len(kept)
    if expired:
        logging.info(f"Ignoring {expired} achievement(s) earned before {start_datetime.isoformat()}.")

    on_record = Counter((a["id"], a["name"]) for a in kept)
    detected = Counter((a["id"], a["name"]) for a in earned_now)
    template = {(a["id"], a["name"]): a for a in earned_now}

    ### Only the surplus is new. A repeatable achievement seen three times but recorded
    ### twice adds one entry, not three.
    for key, count in detected.items():
        for _ in range(count - on_record[key]):
            kept.append({**template[key], "earnedAt": now.isoformat()})

    return sorted(kept, key=lambda a: a["earnedAt"])


def merge_balance_events(*streams) -> list:
    """### Merge event streams into one chronological list with a running balance.

    The streams come from build_balance_events(), build_login_bonus_events() and the
    achievement events. Each event carries its own "amount"; the balance is recomputed
    across the merge, because a bonus between two transfers shifts everything after it.

    Args:
        *streams (list): Event lists, each in the shape build_balance_events() produces.

    Returns:
        list: A new list of new dicts, oldest first, with "balance" filled in.
    """
    merged = sorted(
        (dict(event) for stream in streams for event in stream),
        key=lambda event: parse_feed_timestamp(event["date"]),
    )

    balance = 0
    for event in merged:
        balance += event["amount"]
        event["balance"] = round(balance)

    return merged


def matchday_points(performance: dict) -> list:
    """### Read the points a manager scored on each matchday played.

    The performance response nests a list of seasons, each holding its matchdays. Only
    matchdays that have been played carry "mdp".

    Args:
        performance (dict): A /managers/{id}/performance response.

    Returns:
        list: Points per played matchday, current season only.
    """
    seasons = performance.get("it") or []

    if not seasons:
        return []

    ### The current season is the last one in the list
    return [day["mdp"] for day in seasons[-1].get("it", []) if "mdp" in day]


def season_is_over(now: datetime) -> bool:
    """### Whether every matchday of the season has been played.

    Reads match_days.json, which team_value_per_match_day() writes. Without it there is
    no way to tell, and treating the season as running is the safe answer: it only
    withholds the season titles.

    Args:
        now (datetime): The instant to judge against.

    Returns:
        bool: True once the last match of the season is over.
    """
    match_days_path = path.join(DATA_DIR, "match_days.json")

    if not path.exists(match_days_path):
        return False

    try:
        with open(match_days_path, "r") as f:
            match_days = json.load(f)
    except json.JSONDecodeError:
        return False

    if not match_days:
        return False

    return parse_feed_timestamp(match_days[-1]["lastMatch"]) < now


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