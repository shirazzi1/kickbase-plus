"""### Behavioural fingerprints per manager, derived from the files a run already wrote.

The API shows only *completed* bookings - never a lost bid, never a counter-bid sequence.
So this module claims four things it can actually observe, and each of them carries the
number of transfers it rests on:

    1. median hold duration      - from the buy/sell pairs in turnovers.json
    2. mean markup at purchase   - price paid against the player's market value that day
    3. share of momentum buys    - bought while the 7 day market value trend was rising
    4. top three clubs           - which teams the manager buys from

Plus an activity window: at which hours of the day the manager trades.

Two of those need a market value *per day*, which no file on disk holds. The run does:
market_value_changes() fetches every competition player's history and leagues.py keeps it
cached for the rest of the run, so this module reads that cache instead of asking Kickbase
again - see market_values_from_run_cache(). What the cache cannot answer stays uncounted:
a metric's "n" is the honest coverage, never a filled-in guess. The known gaps are

    -- a player who has left the competition is not in the cache at all,
    -- the fetched window is miscellaneous.MARKET_VALUE_DAYS long, so a player whose curve
       does not reach that far back has no 7 day trend behind an early buy to read,
    -- if market_value_changes() failed this run, the cache is empty and both market value
       metrics report n = 0 for everyone.

Which is why the file leads with a "marketValueCoverage" header: n = 0 everywhere otherwise
looks the same whether nobody ever bought anything or the stage that fetches the curves died
this run. See build_profiles().

The hold duration has a wrinkle of its own, and QUICK_ROUND_TRIP is where it is written
down: managers buy a player off the market and sell them straight back seconds later to
collect the trade count bonus, which drags the median down to a few hours for the busiest
of them. Those bookings are counted next to the median rather than filtered out of it.

Nothing here calls the API, so the stage costs no requests and can be rerun freely.
"""

import json
import logging

from collections import Counter
from datetime import date, timedelta
from os import getenv, path
from statistics import mean, median
from zoneinfo import ZoneInfo

from backend import datasets, exceptions, miscellaneous
from backend.kickbase.v4 import leagues
from backend.paths import PUBLIC_DIR, STATE_DIR

### ===============================================================================

### How far back a "rising trend" is measured. Kickbase itself shows a seven day change,
### and it is the window the momentum metric was specified with.
MOMENTUM_WINDOW_DAYS = 7

### How many favourite clubs the fingerprint names
TOP_CLUB_COUNT = 3

### A player bought off the market and sold straight back to it counts as a round trip.
###
### These are real bookings, not an artefact: in the league this was built against, eight
### of thirteen managers did it, twelve times inside five minutes and at the same price to
### the euro. The mechanic they fit is miscellaneous.TRANSFER_TIERS - Kickbase pays a bonus
### for 50, 250 and 500 trades, and a round trip through the market costs nothing but earns
### one. They are the reason a busy manager's median hold duration reads as a few hours.
###
### They are counted, not dropped. The durations run continuously from fourteen seconds to
### a day, so any cutoff that removed them would be an invented one - and a metric quietly
### filtered by a made up threshold is worse than a metric reported next to the number of
### bookings that pull it down. The hour is therefore a disclosed convention for the count
### only; it changes no median.
QUICK_ROUND_TRIP = timedelta(hours=1)

### The market value history dates its entries in days since this epoch
_JULIAN_EPOCH = date(1970, 1, 1)


def julian_to_day(julian_date: int) -> date:
    """### Convert a market value history date to a calendar day.

    miscellaneous.julian_to_date() does the same conversion but returns dd.mm.yyyy for
    display. A date object is what a lookup keyed by day needs.

    Args:
        julian_date (int): The "dt" of a market value history entry.

    Returns:
        date: The calendar day the entry belongs to.
    """
    return _JULIAN_EPOCH + timedelta(days=julian_date)


def market_value_index(histories: dict) -> dict:
    """### Turn market value histories into a lookup by player and day.

    Args:
        histories (dict): Player ID to a leagues.player_marketvalue() response.

    Returns:
        dict: Player ID to {date: market value}.
    """
    index = {}

    for player_id, history in histories.items():
        by_day = {}

        for entry in history or []:
            ### An entry without both fields is not a data point. Skipping it beats
            ### raising: one odd entry must not cost the whole player's curve.
            if "dt" not in entry or "mv" not in entry:
                continue

            by_day[julian_to_day(entry["dt"])] = entry["mv"]

        if by_day:
            index[str(player_id)] = by_day

    return index


def market_values_from_run_cache(player_ids) -> dict:
    """### Read the market value curves this run already fetched, without fetching more.

    Only the cache is consulted. A player missing from it means less coverage for the
    market value metrics, which the "n" then states - it never means a fresh request,
    because that would turn a derivation stage into a few hundred API calls.

    Args:
        player_ids (iterable): The players to look up.

    Returns:
        dict: Player ID to {date: market value}, for the players the cache knows.
    """
    histories = {}

    for player_id in player_ids:
        history = leagues.cached_market_value(player_id)

        if history:
            histories[str(player_id)] = history

    return market_value_index(histories)


def app_timezone() -> ZoneInfo:
    """### The timezone the activity window is reported in.

    Feed timestamps are UTC. "This manager trades late at night" is a statement about
    their local clock, so the histogram is built in the same zone the rest of the project
    uses for day boundaries.

    Returns:
        ZoneInfo: The configured timezone, Europe/Berlin unless TZ says otherwise.
    """
    return ZoneInfo(getenv("TZ", "Europe/Berlin"))


def build_profiles(transfers: list, turnovers: list, name_to_id: dict,
                   market_values: dict = None, team_names: dict = None) -> dict:
    """### Build the whole profiles document: the coverage header and one entry per manager.

    Every manager in the league index gets an entry, even one who has not traded yet: an
    honest n = 0 is readable, a missing key is a bug the frontend has to guess at.

    The feed names managers, it does not identify them, so both sides of every booking are
    resolved through the shared name index. A booking that cannot be attributed is left
    out - see miscellaneous.resolve_user_id() for when that happens.

    "marketValueCoverage" is the header that keeps the file honest about its own inputs. Two
    market value metrics depend on curves this module does not fetch, so an empty cache and
    a manager who has never bought anything both end up at n = 0 - which a reader cannot
    tell apart from the profiles alone. The header states how many of the players anyone
    bought had a curve to look up, so "0 of 178" reads as a dead upstream stage rather than
    as a league that never trades.

    Args:
        transfers (list): Activity feed items, as all_transfers.json holds them.
        turnovers (list): Buy/sell pairs, as turnovers.json holds them.
        name_to_id (dict): The index from miscellaneous.build_user_name_index().
        market_values (dict): Player ID to {date: market value}. Without it the markup and
            momentum metrics report n = 0.
        team_names (dict): Team ID to team name, for the favourite clubs.

    Returns:
        dict: marketValueCoverage, and managers as manager ID to their fingerprint.
    """
    market_values = market_values or {}
    team_names = team_names or {}

    names_by_id = {user_id: name for name, user_id in name_to_id.items()}

    holds = _hold_durations(turnovers, set(names_by_id))
    buys, activity_hours = _buys_and_activity(transfers, name_to_id)

    profiles = {}

    for manager_id in sorted(names_by_id):
        manager_buys = buys.get(manager_id, [])

        profiles[manager_id] = {
            "managerId": manager_id,
            "managerName": names_by_id[manager_id],
            "holdDuration": _hold_duration_metric(holds.get(manager_id, [])),
            "purchaseMarkup": _markup_metric(manager_buys, market_values),
            "momentumBuys": _momentum_metric(manager_buys, market_values),
            "topClubs": _top_clubs_metric(manager_buys, team_names),
            "activityWindow": _activity_window_metric(activity_hours.get(manager_id, [])),
        }

    return {
        "marketValueCoverage": _coverage(buys, market_values),
        "managers": profiles,
    }


def _coverage(buys: dict, market_values: dict) -> dict:
    """### How much of what the market value metrics need was actually available.

    The denominator is the players somebody bought, because those are the only curves the
    two market value metrics ever consult. Counting the whole competition would make the
    coverage look terrible for no reason.

    Args:
        buys (dict): Manager ID to their purchases, from _buys_and_activity().
        market_values (dict): Player ID to {date: market value}.

    Returns:
        dict: players - how many had a curve - and of, how many were needed.
    """
    bought = {buy["playerId"] for manager_buys in buys.values() for buy in manager_buys
              if buy["playerId"] is not None}

    return {
        "players": sum(1 for player_id in bought if market_values.get(player_id)),
        "of": len(bought),
    }


def _hold_durations(turnovers: list, known_ids: set) -> dict:
    """### Collect how long each manager held the players they bought and sold again.

    Pairs of type "assigned_at_start" are left out. turnovers() invents their buy date as
    START_DATE, so their duration measures when the season began, not how long the manager
    likes to sit on a player.

    Args:
        turnovers (list): Buy/sell pairs, as turnovers.json holds them.
        known_ids (set): The manager IDs the league index resolves.

    Returns:
        dict: Manager ID to a list of {"days", "roundTrip"} - how long the player was held,
            and whether the sale was a round trip through the market (QUICK_ROUND_TRIP).
    """
    durations = {}

    for pair in turnovers:
        ### JSON has no tuples, so a pair read back from disk is a two element list
        if len(pair) != 2:
            logging.debug(f"Skipping a turnover entry that is not a buy/sell pair: {pair}")
            continue

        buy, sell = pair

        if buy.get("type") == "assigned_at_start":
            continue

        manager_id = buy.get("userId")

        ### The seller has to be the buyer. turnovers() pairs on the player ID alone, so a
        ### player bought and sold on by two managers could otherwise land in the wrong
        ### fingerprint.
        if manager_id is None or manager_id != sell.get("userId"):
            continue

        if manager_id not in known_ids:
            continue

        held = (miscellaneous.parse_feed_timestamp(sell["date"])
                - miscellaneous.parse_feed_timestamp(buy["date"]))

        ### A sale dated before its purchase is not a hold time. That should not happen,
        ### but inventing a negative duration would drag the median down silently.
        if held.total_seconds() < 0:
            logging.warning(f"Ignoring a turnover of player {buy.get('playerId')} for manager "
                            f"{manager_id}: it was sold before it was bought.")
            continue

        ### Both sides have to be the market itself: a manager who buys from another
        ### manager cannot have the player again minutes later
        round_trip = (held <= QUICK_ROUND_TRIP
                      and buy.get("tradePartner") == "Kickbase"
                      and sell.get("tradePartner") == "Kickbase")

        durations.setdefault(manager_id, []).append({
            "days": held.total_seconds() / 86400,
            "roundTrip": round_trip,
        })

    return durations


def _buys_and_activity(transfers: list, name_to_id: dict) -> tuple:
    """### Sort the feed into each manager's purchases and their trading hours.

    A booking between two managers is one purchase and one sale at once, so both sides are
    attributed. turnovers() only records such an item as a sale, which is why the buy side
    is read from the feed here rather than from turnovers.json.

    Args:
        transfers (list): Activity feed items, as all_transfers.json holds them.
        name_to_id (dict): The index from miscellaneous.build_user_name_index().

    Returns:
        tuple: (buys, activity_hours). Buys is manager ID to a list of
            {"day", "price", "playerId", "teamId"}; activity_hours is manager ID to a list
            of local hours, one per booking the manager was part of.
    """
    zone = app_timezone()

    buys = {}
    activity_hours = {}

    for item in transfers:
        data = item.get("data") or {}

        booked_at = miscellaneous.parse_feed_timestamp(item["dt"])
        hour = booked_at.astimezone(zone).hour

        buyer_id = miscellaneous.resolve_user_id(data.get("byr"), name_to_id)
        seller_id = miscellaneous.resolve_user_id(data.get("slr"), name_to_id)

        if buyer_id is not None:
            buys.setdefault(buyer_id, []).append({
                ### The market value curve has one entry per day, and the existing
                ### START_DATE lookups compare UTC days, so this one does too
                "day": booked_at.date(),
                "price": data.get("trp"),
                "playerId": str(data["pi"]) if data.get("pi") is not None else None,
                "teamId": data.get("tid"),
            })
            activity_hours.setdefault(buyer_id, []).append(hour)

        if seller_id is not None:
            activity_hours.setdefault(seller_id, []).append(hour)

    return buys, activity_hours


def _hold_duration_metric(holds: list) -> dict:
    """### The median hold duration and how many sales it rests on.

    Args:
        holds (list): The manager's holds from _hold_durations().

    Returns:
        dict: medianDays and medianSeconds (both None without data), n, and
            roundTripsWithinAnHour - see QUICK_ROUND_TRIP for why those are counted next to
            the median instead of being taken out of it.
    """
    days = [hold["days"] for hold in holds]

    ### One median, reported in two units, because the durations in this data span five
    ### orders of magnitude. Days is the unit the metric is about, at three decimals rather
    ### than one: rounded to one, the managers whose median is a round trip read as
    ### "0.0 days" next to an n that says there were six sales - a real number that looks
    ### like a missing one. Three decimals cover the normal range, and medianSeconds carries
    ### the exact value for the fast end, where even three decimals still round to zero.
    median_days = median(days) if days else None

    return {
        "medianDays": round(median_days, 3) if days else None,
        "medianSeconds": round(median_days * 86400) if days else None,
        "n": len(days),
        "roundTripsWithinAnHour": sum(1 for hold in holds if hold["roundTrip"]),
    }


def _markup_metric(manager_buys: list, market_values: dict) -> dict:
    """### How much over the day's market value the manager pays.

    Args:
        manager_buys (list): The manager's purchases from _buys_and_activity().
        market_values (dict): Player ID to {date: market value}.

    Returns:
        dict: meanPercent and medianPercent (None without data), n, and buysConsidered -
            the purchases that were looked at, so the gap to n is visible.
    """
    markups = []

    for buy in manager_buys:
        market_value = _market_value_on(buy, market_values, offset_days=0)

        ### A market value of zero has no percentage over it, and a missing price cannot
        ### be compared to anything
        if not market_value or buy["price"] is None:
            continue

        markups.append((buy["price"] - market_value) / market_value * 100)

    return {
        "meanPercent": round(mean(markups), 1) if markups else None,
        "medianPercent": round(median(markups), 1) if markups else None,
        "n": len(markups),
        "buysConsidered": len(manager_buys),
    }


def _momentum_metric(manager_buys: list, market_values: dict) -> dict:
    """### How often the manager buys into a rising market value.

    A purchase counts as momentum when the player's market value on the day of the buy is
    above its value MOMENTUM_WINDOW_DAYS earlier. Both days have to be known, so buys in
    the first days of a season - where the fetched window does not reach back far enough -
    are not counted either way.

    Args:
        manager_buys (list): The manager's purchases from _buys_and_activity().
        market_values (dict): Player ID to {date: market value}.

    Returns:
        dict: share (None without data), risingBuys, n and windowDays.
    """
    rising = 0
    counted = 0

    for buy in manager_buys:
        market_value = _market_value_on(buy, market_values, offset_days=0)
        earlier = _market_value_on(buy, market_values, offset_days=-MOMENTUM_WINDOW_DAYS)

        ### Only a missing day is skipped here, where _markup_metric() also skips a value of
        ### zero. The asymmetry is deliberate: a ratio needs a denominator, a comparison does
        ### not, so a market value of zero is a rising or falling trend like any other.
        if market_value is None or earlier is None:
            continue

        counted += 1

        if market_value > earlier:
            rising += 1

    return {
        "share": round(rising / counted, 3) if counted else None,
        "risingBuys": rising,
        "n": counted,
        "windowDays": MOMENTUM_WINDOW_DAYS,
    }


def _top_clubs_metric(manager_buys: list, team_names: dict) -> dict:
    """### The clubs the manager buys from most.

    Args:
        manager_buys (list): The manager's purchases from _buys_and_activity().
        team_names (dict): Team ID to team name.

    Returns:
        dict: clubs, at most TOP_CLUB_COUNT of them, and n - the purchases that named a
            club.
    """
    counts = Counter(buy["teamId"] for buy in manager_buys if buy["teamId"] is not None)

    ### Sorted by buys, then by team ID, so an equal count does not reorder between runs
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))

    return {
        "clubs": [{
            "teamId": team_id,
            "teamName": team_names.get(team_id),
            "buys": buy_count,
        } for team_id, buy_count in ranked[:TOP_CLUB_COUNT]],
        "n": sum(counts.values()),
    }


def _activity_window_metric(hours: list) -> dict:
    """### At which hours of the day the manager trades.

    Args:
        hours (list): Local hours, one per booking the manager was part of.

    Returns:
        dict: hourCounts (24 entries, index is the hour), peakHour (None without data), n
            and the timezone the hours are in.
    """
    hour_counts = [0] * 24

    for hour in hours:
        hour_counts[hour] += 1

    ### The earliest hour wins a tie, so the peak does not jump between equal runs
    peak_hour = max(range(24), key=lambda hour: (hour_counts[hour], -hour)) if hours else None

    return {
        "hourCounts": hour_counts,
        "peakHour": peak_hour,
        "n": len(hours),
        "timezone": str(app_timezone()),
    }


def _market_value_on(buy: dict, market_values: dict, offset_days: int):
    """### A player's market value on the day of a purchase, or a number of days off it.

    Args:
        buy (dict): A purchase from _buys_and_activity().
        market_values (dict): Player ID to {date: market value}.
        offset_days (int): Days to shift from the day of the purchase.

    Returns:
        int: The market value, or None if that day is not covered.
    """
    if buy["playerId"] is None:
        return None

    by_day = market_values.get(buy["playerId"])

    if not by_day:
        return None

    return by_day.get(buy["day"] + timedelta(days=offset_days))


def team_names_from_static(teams: list) -> dict:
    """### Map team IDs to team names.

    Args:
        teams (list): STATIC_teams.json, one entry per team.

    Returns:
        dict: Team ID to team name.
    """
    return {str(team["teamId"]): team.get("teamName") for team in teams if "teamId" in team}


def _load_json(file_name: str, written_by: str):
    """### Read one of the run's data files.

    Args:
        file_name (str): The dataset to read. Which directory it lives in follows from the
            registry - this stage reads both kinds: turnovers.json is served to the browser,
            the other three are backend-private.
        written_by (str): The stage that produces it, named in the error so a failure
            points at the stage that actually went wrong.

    Returns:
        any: The parsed content.

    Raises:
        exceptions.KickbaseException: If the file is missing or does not parse.
    """
    directory = STATE_DIR if datasets.dataset_kind(file_name) == datasets.STATE else PUBLIC_DIR
    file_path = path.join(directory, file_name)

    try:
        with open(file_path, "r") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise exceptions.KickbaseException(
            f"Cannot build the manager profiles without {file_name}, which the "
            f"'{written_by}' stage writes: {e}"
        )


def write_manager_profiles() -> None:
    """### Stage: derive every manager's fingerprint and write manager_profiles.json.

    Runs last in a run, because it reads what earlier stages wrote: the feed cache and
    turnovers.json from turnovers(), and both STATIC_users.json and STATIC_teams.json from
    market_value_changes() - which also leaves the market value curves in the cache the two
    market value metrics read. STATIC_users.json is written by leagues.get_users(), and
    market_value_changes() is its only caller, whatever the comment on login() says.

    Raises:
        exceptions.KickbaseException: If one of those files is missing or unreadable. The
            manifest then records this stage as failed and manager_profiles.json keeps
            whatever the last successful run wrote.
    """
    logging.info("Building manager profiles...")

    transfers = _load_json("all_transfers.json", written_by="turnovers")
    turnovers = _load_json("turnovers.json", written_by="turnovers")
    league_users = _load_json("STATIC_users.json", written_by="market_value_changes")
    teams = _load_json("STATIC_teams.json", written_by="market_value_changes")

    name_to_id = miscellaneous.build_user_name_index(league_users)
    team_names = team_names_from_static(teams)

    ### Reverted bookings stay in the feed cache on purpose, so that a later correction can
    ### still be seen. They are not behaviour, so they go before anything is counted.
    transfers = miscellaneous.drop_reverted_transfers(transfers)

    player_ids = {str(item["data"]["pi"]) for item in transfers
                  if (item.get("data") or {}).get("pi") is not None}
    market_values = market_values_from_run_cache(player_ids)

    document = build_profiles(transfers, turnovers, name_to_id, market_values, team_names)

    miscellaneous.write_json_to_file(document, "manager_profiles.json")
    miscellaneous.write_timestamp("ts_manager_profiles.json", rows=len(document["managers"]))

    ### Coverage goes in the file as well as in the log. The log is where an empty cache is
    ### noticed while looking at a run; the file is where a consumer can tell "nobody buys
    ### anything" apart from "the stage that fetches the curves died this run".
    coverage = document["marketValueCoverage"]
    logging.info(f"Built profiles for {len(document['managers'])} managers. Market value "
                 f"curves available for {coverage['players']} of {coverage['of']} bought "
                 "players.")

    if coverage["of"] and not coverage["players"]:
        logging.warning("No market value curve was cached for any bought player, so the "
                        "markup and momentum metrics are empty. This run's "
                        "market_value_changes stage is what fills that cache.")
