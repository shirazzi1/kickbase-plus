"""### The event stream: what changed between two snapshots of the same dataset.

Every run appends one line per dataset to the history store (see
miscellaneous.HISTORICISED_DATASETS). This module reads those lines back, compares
consecutive ones and turns the differences into typed events with a German sentence each -
the raw material for the Tagesplan tab and for the Discord pushes.

Six types, and what each of them rests on:

    neue_listung   - a playerId in the current market snapshot that the previous one did
                     not have.
    preissenkung   - the same listing at a lower asking price than in the previous snapshot.
    mv_sprung      - a market value that moved further than MV_JUMP_SHARE between two
                     market_value_changes snapshots.
    laeuft_ab      - a listing whose expiry is less than EXPIRY_WARN_HOURS away. Read off
                     the current snapshot, not off a diff.
    zwangsverkauf  - a manager whose remaining bidding room has almost run out, from the
                     balances snapshots.
    cash_hortung   - a manager whose budget only grew over the window and who bought
                     nothing in it.

Severity is a 1-3 scale, and it is what decides whether Discord hears about an event at all
(DISCORD_MIN_SEVERITY, default SEVERITY_ACT):

    1 SEVERITY_NOTE  - worth having in the list, not worth an interruption.
    2 SEVERITY_WATCH - a thing to look at when you next open the tab.
    3 SEVERITY_ACT   - acting later probably means acting too late: a listing under market
                       value, a big price cut, an expiry inside the hours before the next
                       run, a manager one bid away from having to sell.

Three properties this module is built around, because the store it reads is younger than it
is:

  - **An empty history is the normal first state.** In production nothing has been recorded
    yet the first time this runs, and a missing day is what a failed run leaves behind. Every
    reader here treats "no file", "no line", "one line" and "a truncated last line" as
    ordinary, not as an error.
  - **Nothing is invented.** Where the data cannot answer a question, the event is not
    emitted. The clearest case is laeuft_ab: Kickbase sends an expiry only for its own
    listings, never for a manager's (see main.market()), so listings by managers produce no
    expiry event rather than one from a guessed listing lifetime.
  - **The same event is announced once.** Events are rebuilt from scratch on every run over
    the whole window, so the run six times a day would otherwise push the same alert six
    times. Every event carries a key that is stable across runs, and the keys already pushed
    are remembered in EVENTS_STATE_PATH.

Nothing here calls the Kickbase API, so the stage costs no requests and can be rerun freely.
"""

import json
import logging
import os
import tempfile

from datetime import datetime, timedelta, timezone
from os import getenv, makedirs, path

from backend import miscellaneous
from backend.paths import DATA_DIR, EVENTS_STATE_PATH

### ===============================================================================

### The three severities. Numbers rather than names in the file, so the frontend can sort
### and filter by them and DISCORD_MIN_SEVERITY can be a single figure in the environment.
SEVERITY_NOTE = 1
SEVERITY_WATCH = 2
SEVERITY_ACT = 3

### How much of the past the Tagesplan shows. Two days is what "heute und gestern" needs,
### and it is short enough that the whole window can be rebuilt from scratch every run.
EVENT_WINDOW_HOURS = 48

### How many day files to read per dataset. Three, because the window reaches 48 hours back
### and a diff needs the snapshot *before* the oldest one in the window as its baseline:
### files for today, yesterday and the day before always span at least those 48 hours,
### whatever time of day the run happens.
HISTORY_LOOKBACK_DAYS = 3

### A price cut below this share of the previous price is noise, not news: asking prices are
### whole euros and a manager nudging a listing by a few hundred euros is not a signal.
PRICE_CUT_MIN_SHARE = 0.01

### A cut at or beyond this share is the one worth interrupting someone for
BIG_PRICE_CUT_SHARE = 0.10

### How far a market value has to move to count as a jump: both a share of the value and an
### absolute floor, so a 6% move on a 200.000 € bench player does not read like the same
### event as a 6% move on a 20 Mio € striker.
MV_JUMP_SHARE = 0.05
BIG_MV_JUMP_SHARE = 0.10
MV_JUMP_MIN_ABSOLUTE = 50_000

### How long before a listing expires to say so. Has to exceed the scrape cadence (six runs
### a day, so roughly four hours) or a listing could pass from "plenty of time" to "gone"
### without a single run seeing it in between.
EXPIRY_WARN_HOURS = 6

### When a manager's bidding room counts as gone. maxBid in balances.json is what is left of
### the overdraft Kickbase allows (a third of team value plus balance, see main.max_bid), so
### these are shares of that allowance, not euro amounts - the allowance itself scales with
### the squad.
FORCED_SALE_CRITICAL_SHARE = 0.10
FORCED_SALE_WARNING_SHARE = 0.25

### What counts as hoarding: a budget that only ever grew, over at least this many snapshots,
### by at least this much, with no purchase in the window. Three snapshots because two are a
### single sale, and a single sale is not a pattern.
CASH_HOARD_MIN_SNAPSHOTS = 3
CASH_HOARD_MIN_GROWTH = 1_000_000

### How long a pushed event key is remembered. Anything older than the window can never be
### emitted again, so the extra days are only there to cover a run that is late or a clock
### that jumped.
STATE_RETENTION_DAYS = 7

### How many events go into one Discord message. The embed description has a length limit,
### and a message with forty lines is not read on a phone; the rest stay unreported and go
### out with the next run.
MAX_DISCORD_EVENTS = 10

### Discord embed colours, by the highest severity in the message
COLOUR_ACT = 16711680      # red
COLOUR_WATCH = 16753920    # orange

### The default is the top severity on purpose: this webhook already carries the run
### failures from the supervisor, and an alert channel that cries wolf gets muted, at which
### point it protects nothing.
DEFAULT_DISCORD_MIN_SEVERITY = SEVERITY_ACT


def discord_min_severity() -> int:
    """### The lowest severity still worth a Discord message.

    Returns:
        int: DISCORD_MIN_SEVERITY from the environment, or DEFAULT_DISCORD_MIN_SEVERITY if
            it is unset or not a number. An unreadable setting must not silence the channel
            and must not flood it either, so it falls back rather than raising.
    """
    raw = getenv("DISCORD_MIN_SEVERITY")

    if raw is None or not raw.strip():
        return DEFAULT_DISCORD_MIN_SEVERITY

    try:
        return int(raw)
    except ValueError:
        logging.warning(
            f"DISCORD_MIN_SEVERITY='{raw}' is not a number. Falling back to "
            f"{DEFAULT_DISCORD_MIN_SEVERITY}."
        )
        return DEFAULT_DISCORD_MIN_SEVERITY


### ===============================================================================
### Reading the store
### ===============================================================================

def read_snapshots(dataset: str, now: datetime = None, days: int = HISTORY_LOOKBACK_DAYS) -> list:
    """### Read the recent history of one dataset, oldest snapshot first.

    Goes through miscellaneous.history_file_path() rather than building the path here, so
    the reader cannot drift from the writer on the directory layout or the date format.

    Every kind of damage a file can carry is skipped rather than raised over: the store has
    no backfill, so the alternative to reading nine good lines and ignoring a tenth is
    reading nothing at all. A line cut short by a crashed host is the case that actually
    happens (see miscellaneous._lacks_trailing_newline).

    Args:
        dataset (str): The dataset name without ".json", e.g. "market".
        now (datetime): The instant the newest day file is dated by, normally now.
        days (int): How many day files back to read, including today's.

    Returns:
        list: Snapshots as {"ts": aware datetime, "rows": list}, sorted by "ts". Empty if
            nothing has been recorded yet, which is the normal state of a fresh install.
    """
    now = now or datetime.now(timezone.utc)
    snapshots = []

    for offset in range(days - 1, -1, -1):
        file_path = miscellaneous.history_file_path(dataset, now - timedelta(days=offset))

        if not path.exists(file_path):
            continue

        try:
            with open(file_path, "r") as f:
                lines = f.readlines()
        except OSError as e:
            logging.warning(f"Could not read {file_path}: {type(e).__name__}: {e}")
            continue

        for line in lines:
            snapshot = _parse_snapshot(line)

            if snapshot is not None:
                snapshots.append(snapshot)

    ### Sorted rather than trusted: the files are read in order and appended in order, but a
    ### run straddling midnight or a TZ change can put a line in the neighbouring file.
    snapshots.sort(key=lambda snapshot: snapshot["ts"])

    return snapshots


def _parse_snapshot(line: str):
    """### Turn one NDJSON line of the history store into a snapshot.

    Args:
        line (str): One line as written by miscellaneous._append_history().

    Returns:
        dict: {"ts": aware datetime, "rows": list}, or None if the line is unusable - blank,
            truncated, not an object, without a timestamp, or with a payload that is not a
            list of rows.
    """
    line = line.strip()

    if not line:
        return None

    try:
        parsed = json.loads(line)
    except json.JSONDecodeError:
        logging.warning("Skipping a history line that does not parse (an append was cut short).")
        return None

    if not isinstance(parsed, dict) or not isinstance(parsed.get("rows"), list):
        return None

    moment = _parse_timestamp(parsed.get("ts"))

    if moment is None:
        return None

    return {"ts": moment, "rows": parsed["rows"]}


def _parse_timestamp(raw):
    """### Read a history line's "ts" as an aware datetime.

    Args:
        raw (str): The "ts" of a history line.

    Returns:
        datetime: An aware datetime, or None if it cannot be read. A line written without an
            offset is read in the timezone the store files its days under, which is the
            timezone the writer used.
    """
    if not isinstance(raw, str):
        return None

    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return None

    if moment.tzinfo is None:
        return moment.replace(tzinfo=miscellaneous.history_timezone())

    return moment


def _rows_by_key(rows: list, key_of) -> dict:
    """### Index a snapshot's rows by a key, ignoring rows that have none.

    Args:
        rows (list): The "rows" of a snapshot.
        key_of (callable): Row to key, returning None for a row that cannot be identified.

    Returns:
        dict: Key to row.
    """
    indexed = {}

    for row in rows:
        if not isinstance(row, dict):
            continue

        key = key_of(row)

        if key is not None:
            indexed[key] = row

    return indexed


def _pairs(snapshots: list, window_start: datetime) -> list:
    """### The consecutive snapshot pairs whose newer half falls inside the window.

    The older half is deliberately allowed to lie outside it: a diff needs something to
    compare against, and on the first run of a day that something is yesterday's last
    snapshot.

    Args:
        snapshots (list): Snapshots from read_snapshots(), oldest first.
        window_start (datetime): The start of the reported window.

    Returns:
        list: (previous, current) pairs. Empty when there is at most one snapshot, which is
            what a store on its first run looks like.
    """
    return [(snapshots[index - 1], snapshots[index])
            for index in range(1, len(snapshots))
            if snapshots[index]["ts"] >= window_start]


### ===============================================================================
### German rendering
### ===============================================================================

def format_euro(value) -> str:
    """### Render an amount the way the frontend does: German separators, short scale.

    Args:
        value (float): An amount in euros.

    Returns:
        str: e.g. "1,25 Mio. €", "850 Tsd. €", "-4.200 €", or "unbekannt" for None.
    """
    if value is None:
        return "unbekannt"

    value = round(value)
    absolute = abs(value)

    if absolute >= 1_000_000:
        return f"{_german_number(value / 1_000_000, 2)} Mio. €"

    ### Below ten thousand the "Tsd." rounding starts hiding the figure ("3 Tsd. €" for
    ### anything from 2.500 to 3.499), so small amounts stay unrounded.
    if absolute >= 10_000:
        return f"{_german_number(value / 1_000, 0)} Tsd. €"

    return f"{_german_number(value, 0)} €"


def format_delta(value) -> str:
    """### The same as format_euro(), with a sign in front of a gain.

    Args:
        value (float): A change in euros.

    Returns:
        str: e.g. "+1,25 Mio. €" or "-850 Tsd. €".
    """
    if value is None:
        return "unbekannt"

    return f"+{format_euro(value)}" if value > 0 else format_euro(value)


def format_share(share) -> str:
    """### Render a ratio as a signed whole percentage.

    Args:
        share (float): A ratio, e.g. -0.1 for a tenth lost.

    Returns:
        str: e.g. "-10 %".
    """
    return f"{share * 100:+.0f} %".replace("+0 %", "0 %")


def _german_number(value, decimals: int) -> str:
    """### Format a number with a German thousands separator and decimal comma.

    Args:
        value (float): The number.
        decimals (int): How many decimal places.

    Returns:
        str: e.g. "1,25" or "12.500".
    """
    ### Python's separators are the English ones round the wrong way, so they are swapped
    ### through a placeholder rather than one after the other
    return f"{value:,.{decimals}f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def player_name(row: dict) -> str:
    """### A player's name as the tables show it.

    Args:
        row (dict): A market or market_value_changes row.

    Returns:
        str: "Vorname Nachname", or just the surname when the API sent no first name, which
            it does for players who go by one name.
    """
    parts = [row.get("firstName"), row.get("lastName")]

    return " ".join(part for part in parts if part) or "Unbekannter Spieler"


### ===============================================================================
### The event types
### ===============================================================================

def _event(event_type: str, severity: int, moment: datetime, text: str,
           key: str, player_id=None, manager_id=None) -> dict:
    """### One event, in the shape the frontend and the Discord push both read.

    Args:
        event_type (str): One of the six types named in the module docstring.
        severity (int): SEVERITY_NOTE, SEVERITY_WATCH or SEVERITY_ACT.
        moment (datetime): When the event happened - the timestamp of the snapshot it was
            derived from, not the time of this run.
        text (str): The German sentence shown in the Tagesplan and pushed to Discord.
        key (str): The identity of this event across runs. Two runs deriving the same event
            from the same snapshots have to produce the same key, or the alert goes out
            again every four hours.
        player_id (str): The player the event is about, if it is about a player.
        manager_id (str): The manager the event is about, if it is about a manager.

    Returns:
        dict: The event, with "ts" still a datetime - _finalise() renders it.
    """
    return {
        "key": key,
        "type": event_type,
        "severity": severity,
        "ts": moment,
        "playerId": player_id,
        "managerId": manager_id,
        "text": text,
    }


def listing_events(snapshots: list, window_start: datetime) -> list:
    """### New listings and price cuts, from the market snapshots.

    A listing that vanished is not an event here: it was sold, pulled or expired, and which
    of those it was cannot be told from the market file alone. turnovers.json covers the
    sales.

    Args:
        snapshots (list): market snapshots from read_snapshots().
        window_start (datetime): The start of the reported window.

    Returns:
        list: neue_listung and preissenkung events.
    """
    events = []

    for previous, current in _pairs(snapshots, window_start):
        before = _rows_by_key(previous["rows"], lambda row: row.get("playerId"))
        moment = current["ts"]

        for row in current["rows"]:
            if not isinstance(row, dict) or row.get("playerId") is None:
                continue

            player_id = str(row["playerId"])
            price = _number(row.get("price"))
            market_value = _number(row.get("marketValue"))
            old = before.get(row["playerId"])

            if old is None:
                events.append(_new_listing_event(row, player_id, price, market_value, moment))
                continue

            cut = _price_cut_event(row, old, player_id, price, market_value, moment)

            if cut is not None:
                events.append(cut)

    return events


def _new_listing_event(row: dict, player_id: str, price, market_value, moment: datetime) -> dict:
    """### A player who was not on the market in the previous snapshot.

    Args:
        row (dict): The market row.
        player_id (str): The player's ID.
        price (float): The asking price, or None if the row carries none.
        market_value (float): The player's market value, or None.
        moment (datetime): The snapshot's timestamp.

    Returns:
        dict: A neue_listung event.
    """
    ### An asking price under the market value is the one case worth an interruption: it is
    ### the listing every other manager in the league is also being shown.
    bargain = _below_market_value(price, market_value)

    seller = row.get("seller") or "Kickbase"
    note = ", unter Marktwert" if bargain else ""

    return _event(
        "neue_listung",
        SEVERITY_ACT if bargain else SEVERITY_WATCH,
        moment,
        f"Neu auf dem Markt: {player_name(row)} für {format_euro(price)} "
        f"(Marktwert {format_euro(market_value)}{note}), gelistet von {seller}.",
        key=f"neue_listung|{player_id}|{moment.isoformat()}",
        player_id=player_id,
    )


def _price_cut_event(row: dict, old: dict, player_id: str, price, market_value,
                     moment: datetime):
    """### A listing whose asking price fell since the previous snapshot.

    Args:
        row (dict): The current market row.
        old (dict): The same listing in the previous snapshot.
        player_id (str): The player's ID.
        price (float): The current asking price, or None.
        market_value (float): The player's market value, or None.
        moment (datetime): The snapshot's timestamp.

    Returns:
        dict: A preissenkung event, or None if the price did not fall far enough to mean
            anything.
    """
    old_price = _number(old.get("price"))

    if price is None or old_price is None or old_price <= 0 or price >= old_price:
        return None

    share = (price - old_price) / old_price

    if abs(share) < PRICE_CUT_MIN_SHARE:
        return None

    ### Two ways a cut becomes urgent: it is a big one, or it has taken the price below what
    ### the player is worth.
    severity = (SEVERITY_ACT if abs(share) >= BIG_PRICE_CUT_SHARE
                or _below_market_value(price, market_value) else SEVERITY_WATCH)

    return _event(
        "preissenkung",
        severity,
        moment,
        f"Preis gesenkt: {player_name(row)} von {format_euro(old_price)} auf "
        f"{format_euro(price)} ({format_share(share)}, Marktwert {format_euro(market_value)}).",
        key=f"preissenkung|{player_id}|{moment.isoformat()}",
        player_id=player_id,
    )


def expiry_events(snapshot, now: datetime) -> list:
    """### Listings that are about to run out, from the newest market snapshot.

    The only event type that is not a diff: an expiry is a fact about the clock, and it has
    to be reported while the listing is still there rather than after it changed.

    Kickbase sends an expiry ("exs") only for its own listings, never for one a manager put
    up (see main.market(), where the field becomes "expiration"). Listings by managers
    therefore produce nothing here. That is a gap, not an oversight: nothing in this project
    knows how long a manager's listing lives, and a countdown from an invented lifetime
    would be worse than no countdown - it would be a number that looks measured.

    Args:
        snapshot (dict): The newest market snapshot, or None if there is none yet.
        now (datetime): The instant to measure the remaining time against.

    Returns:
        list: laeuft_ab events.
    """
    if not snapshot:
        return []

    events = []

    for row in snapshot["rows"]:
        if not isinstance(row, dict) or row.get("playerId") is None:
            continue

        expires_at = _parse_timestamp(row.get("expiration"))

        if expires_at is None:
            continue

        remaining = expires_at - now

        ### Already gone, or still far enough out that the next run will catch it in time
        if remaining <= timedelta(0) or remaining > timedelta(hours=EXPIRY_WARN_HOURS):
            continue

        player_id = str(row["playerId"])
        price = _number(row.get("price"))
        market_value = _number(row.get("marketValue"))
        offers = row.get("offerCount")

        ### A listing that is not actually cheap running out is not a missed chance, so it
        ### stays a thing to look at rather than something to be interrupted for. Which is
        ### nearly all of them: Kickbase asks exactly the market value for its own listings,
        ### and those are the only ones that carry an expiry at all.
        events.append(_event(
            "laeuft_ab",
            SEVERITY_ACT if _below_market_value(price, market_value) else SEVERITY_WATCH,
            ### The snapshot's timestamp, not the expiry: the Tagesplan groups by day, and an
            ### expiry can fall on tomorrow while the observation belongs to today.
            snapshot["ts"],
            f"Läuft {_format_remaining(remaining)} ab: {player_name(row)} für "
            f"{format_euro(price)} (Marktwert {format_euro(market_value)}), "
            f"{_format_offers(offers)}.",
            ### Keyed by the expiry, not by the snapshot: the same listing is seen again by
            ### the next run and must not be announced twice.
            key=f"laeuft_ab|{player_id}|{expires_at.isoformat()}",
            player_id=player_id,
        ))

    return events


def market_value_events(snapshots: list, window_start: datetime) -> list:
    """### Market values that moved further than MV_JUMP_SHARE between two snapshots.

    Rows are matched by playerId, and by name and team where a snapshot predates the
    playerId being written into market_value_changes.json. A row whose counterpart cannot be
    found produces nothing at all: the alternative would be to read "this player is new to
    me" as "this player's value jumped", which is how a rename or a transfer would turn into
    a fake alert for every player in the competition.

    Args:
        snapshots (list): market_value_changes snapshots from read_snapshots().
        window_start (datetime): The start of the reported window.

    Returns:
        list: mv_sprung events.
    """
    events = []

    for previous, current in _pairs(snapshots, window_start):
        before = _rows_by_key(previous["rows"], _market_value_key)
        moment = current["ts"]

        for row in current["rows"]:
            if not isinstance(row, dict):
                continue

            key = _market_value_key(row)
            old = before.get(key) if key is not None else None

            if old is None:
                continue

            value = _number(row.get("marketValue"))
            old_value = _number(old.get("marketValue"))

            if value is None or old_value is None or old_value <= 0:
                continue

            delta = value - old_value
            share = delta / old_value

            if abs(delta) < MV_JUMP_MIN_ABSOLUTE or abs(share) < MV_JUMP_SHARE:
                continue

            direction = "gestiegen" if delta > 0 else "gefallen"
            owner = row.get("manager")
            holder = f" - im Kader von {owner}" if owner and owner != "Kickbase" else ""
            player_id = str(row["playerId"]) if row.get("playerId") is not None else None

            events.append(_event(
                "mv_sprung",
                SEVERITY_ACT if abs(share) >= BIG_MV_JUMP_SHARE else SEVERITY_WATCH,
                moment,
                f"Marktwert {direction}: {player_name(row)} {format_delta(delta)} auf "
                f"{format_euro(value)} ({format_share(share)}){holder}.",
                key=f"mv_sprung|{key}|{moment.isoformat()}",
                player_id=player_id,
            ))

    return events


def _market_value_key(row: dict):
    """### How a market_value_changes row is identified across snapshots.

    Args:
        row (dict): A market_value_changes row.

    Returns:
        str: The playerId if the row has one. Lines written before main.market_value_changes()
            started keeping it fall back to name and team, which is unique in practice and
            costs nothing to be wrong about: a mismatch drops an event, it never invents one.
            None if the row carries neither.
    """
    if row.get("playerId") is not None:
        return str(row["playerId"])

    last_name = row.get("lastName")

    if not last_name:
        return None

    return f"{row.get('firstName') or ''} {last_name}@{row.get('teamId')}"


def forced_sale_events(snapshots: list, window_start: datetime) -> list:
    """### Managers whose bidding room has nearly run out, from the balances snapshots.

    "maxBid" is what is left of the overdraft Kickbase allows (see main.max_bid). Once it
    approaches zero the manager cannot answer a bid without selling first, which is the
    moment their players become gettable.

    Judged per snapshot rather than per diff, because the interesting thing is a state, not
    a change. The key is the calendar day, so a manager who stays broke all week is reported
    once a day rather than six times a day.

    Args:
        snapshots (list): balances snapshots from read_snapshots().
        window_start (datetime): The start of the reported window.

    Returns:
        list: zwangsverkauf events.
    """
    events = []

    for snapshot in snapshots:
        if snapshot["ts"] < window_start:
            continue

        for row in snapshot["rows"]:
            if not isinstance(row, dict):
                continue

            balance = _number(row.get("balance"))
            max_bid = _number(row.get("maxBid"))

            if balance is None or max_bid is None or balance >= 0:
                continue

            ### The allowance itself, reconstructed: maxBid is the allowance reduced by how
            ### far into it the manager already is, so adding the (negative) balance back
            ### gives what they were granted in the first place.
            allowance = max_bid - balance

            if allowance <= 0:
                continue

            share = max_bid / allowance

            if share > FORCED_SALE_WARNING_SHARE:
                continue

            manager_id = str(row.get("userId")) if row.get("userId") is not None else None
            name = row.get("username") or "Ein Manager"
            day = snapshot["ts"].date().isoformat()

            events.append(_event(
                "zwangsverkauf",
                SEVERITY_ACT if share <= FORCED_SALE_CRITICAL_SHARE else SEVERITY_WATCH,
                snapshot["ts"],
                f"Zwangsverkauf droht: {name} steht bei {format_euro(balance)} und kann noch "
                f"{format_euro(max_bid)} bieten.",
                key=f"zwangsverkauf|{manager_id or name}|{day}",
                manager_id=manager_id,
            ))

    return events


def cash_hoarding_events(snapshots: list, transfers, window_start: datetime) -> list:
    """### Managers whose budget only grew over the window, without buying anything.

    A budget that climbs while nothing comes in is a manager saving up for a bid - the
    counterpart of the forced sale. "Bought nothing" is read off all_transfers.json rather
    than off the balance itself, because the balance in the snapshots moves on sales too and
    a manager who sold for six million and spent four still looks like a saver.

    Without all_transfers.json nothing is emitted: "no purchase" would then be an assumption
    rather than an observation.

    Args:
        snapshots (list): balances snapshots from read_snapshots().
        transfers (list): all_transfers.json, or None if it could not be read.
        window_start (datetime): The start of the reported window.

    Returns:
        list: cash_hortung events.
    """
    if transfers is None:
        return []

    in_window = [snapshot for snapshot in snapshots if snapshot["ts"] >= window_start]

    if len(in_window) < CASH_HOARD_MIN_SNAPSHOTS:
        return []

    ### Names to IDs, from the snapshots themselves rather than from STATIC_users.json: the
    ### balances rows carry both, and the feed names a buyer by display name only.
    name_to_id = miscellaneous.build_user_name_index({
        str(row["userId"]): row.get("username")
        for snapshot in in_window for row in snapshot["rows"]
        if isinstance(row, dict) and row.get("userId") is not None
    })

    buyers = _buyers_since(transfers, in_window[0]["ts"], name_to_id)

    balances_by_manager = {}
    names = {}

    for snapshot in in_window:
        for row in snapshot["rows"]:
            if not isinstance(row, dict) or row.get("userId") is None:
                continue

            balance = _number(row.get("balance"))

            if balance is None:
                continue

            manager_id = str(row["userId"])
            balances_by_manager.setdefault(manager_id, []).append(balance)
            names[manager_id] = row.get("username") or "Ein Manager"

    events = []
    moment = in_window[-1]["ts"]

    for manager_id, series in balances_by_manager.items():
        if len(series) < CASH_HOARD_MIN_SNAPSHOTS or manager_id in buyers:
            continue

        ### Monotone, not just up over the window: a manager who spent and then sold again
        ### is not hoarding, however the two ends happen to compare.
        if any(later < earlier for earlier, later in zip(series, series[1:])):
            continue

        growth = series[-1] - series[0]

        if growth < CASH_HOARD_MIN_GROWTH:
            continue

        events.append(_event(
            "cash_hortung",
            ### Intelligence, not urgency: it says a bid is coming, not that one is on the
            ### table. Below the Discord threshold by design.
            SEVERITY_WATCH,
            moment,
            f"{names[manager_id]} hortet Geld: {format_delta(growth)} über "
            f"{len(series)} Snapshots, kein Kauf in {EVENT_WINDOW_HOURS} Std.",
            key=f"cash_hortung|{manager_id}|{moment.date().isoformat()}",
            manager_id=manager_id,
        ))

    return events


def _buyers_since(transfers: list, since: datetime, name_to_id: dict) -> set:
    """### Which managers bought a player at or after an instant.

    Args:
        transfers (list): all_transfers.json, activity feed items.
        since (datetime): The start of the window.
        name_to_id (dict): Display name to user ID, from build_user_name_index().

    Returns:
        set: User IDs. A buyer whose display name cannot be resolved is not in it, which
            makes them look like a saver - the same limitation every other consumer of the
            feed has, and the reason resolve_user_id() says so in the log.
    """
    buyers = set()

    for item in transfers:
        if not isinstance(item, dict) or not isinstance(item.get("data"), dict):
            continue

        try:
            moment = miscellaneous.parse_feed_timestamp(item["dt"])
        except (KeyError, AttributeError, ValueError):
            continue

        if moment < since:
            continue

        buyer_id = miscellaneous.resolve_user_id(item["data"].get("byr"), name_to_id)

        if buyer_id is not None:
            buyers.add(str(buyer_id))

    return buyers


def _below_market_value(price, market_value) -> bool:
    """### Whether a listing is asking less than the player is worth.

    Strictly less, and that is the whole point of this function existing. Kickbase prices its
    own listings at *exactly* the market value - every free agent in a real market.json does,
    to the euro - so "price at or below market value" is true for all of them and would make
    every expiring free agent an urgent alert, six times a day. A discount is a discount when
    somebody actually gave one.

    Args:
        price (float): The asking price, or None.
        market_value (float): The player's market value, or None.

    Returns:
        bool: False if either figure is missing - an absent price is not a bargain.
    """
    if price is None or market_value is None:
        return False

    return price < market_value


def _number(value):
    """### A row's field as a number, or None if it is not one.

    Args:
        value (any): The field.

    Returns:
        float: The number, or None. Booleans are refused: they are ints in Python, and an
            "isFreeAgent" read as a price would compare perfectly happily.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None

    return value


def _format_remaining(remaining: timedelta) -> str:
    """### How long until a listing expires, in German.

    Args:
        remaining (timedelta): The time left.

    Returns:
        str: e.g. "in 3 Std." or "in unter 1 Std.".
    """
    hours = int(remaining.total_seconds() // 3600)

    return f"in {hours} Std." if hours >= 1 else "in unter 1 Std."


def _format_offers(offers) -> str:
    """### How many managers are bidding, in German.

    Args:
        offers (int): The "offerCount" of a market row.

    Returns:
        str: e.g. "1 Gebot", "3 Gebote", or "Gebote unbekannt".
    """
    if not isinstance(offers, int) or isinstance(offers, bool):
        return "Gebote unbekannt"

    if offers == 0:
        return "kein Gebot"

    return "1 Gebot" if offers == 1 else f"{offers} Gebote"


### ===============================================================================
### Putting a run's events together
### ===============================================================================

def build_events(now: datetime = None) -> list:
    """### Every event of the last EVENT_WINDOW_HOURS, newest first.

    Rebuilt from the store on every run rather than appended to, so a fixed bug or a changed
    threshold applies to the whole window instead of only to what happens next.

    Args:
        now (datetime): The instant the window ends, normally now.

    Returns:
        list: Events with "ts" as an ISO string, newest first, highest severity first within
            the same instant. Empty when the store holds nothing yet, which is what a fresh
            install looks like until the second run.
    """
    now = now or datetime.now(timezone.utc)
    window_start = now - timedelta(hours=EVENT_WINDOW_HOURS)

    market = read_snapshots("market", now)
    market_values = read_snapshots("market_value_changes", now)
    balances = read_snapshots("balances", now)

    events = []
    events += listing_events(market, window_start)
    events += expiry_events(market[-1] if market else None, now)
    events += market_value_events(market_values, window_start)
    events += forced_sale_events(balances, window_start)
    events += cash_hoarding_events(balances, _read_transfers(), window_start)

    return _finalise(events, window_start)


def _finalise(events: list, window_start: datetime) -> list:
    """### Drop what falls outside the window, collapse duplicates, sort for display.

    Two events sharing a key are the same event: laeuft_ab seen by two runs, a manager broke
    in every snapshot of the day. The one with the higher severity wins, so a state that got
    worse during the day is reported at its worst.

    Args:
        events (list): Events with "ts" as a datetime.
        window_start (datetime): The start of the reported window.

    Returns:
        list: Events with "ts" as an ISO string, newest first.
    """
    by_key = {}

    for event in events:
        if event["ts"] < window_start:
            continue

        previous = by_key.get(event["key"])

        if previous is None or event["severity"] > previous["severity"]:
            by_key[event["key"]] = event

    ordered = sorted(by_key.values(), key=lambda event: (event["ts"], event["severity"]),
                     reverse=True)

    return [{**event, "ts": event["ts"].isoformat()} for event in ordered]


def _read_transfers():
    """### Read all_transfers.json, the record of who bought what.

    Returns:
        list: The transfers, or None if the file is missing or unreadable. None is not an
            empty list: an empty list says nobody bought anything, and the difference decides
            whether a cash_hortung event may be claimed at all.
    """
    file_path = path.join(DATA_DIR, "all_transfers.json")

    try:
        with open(file_path, "r") as f:
            transfers = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logging.warning(
            f"Cannot read {file_path} ({type(e).__name__}: {e}), so no cash_hortung events "
            "this run: without the transfers, 'bought nothing' would be a guess."
        )
        return None

    return transfers if isinstance(transfers, list) else None


### ===============================================================================
### Remembering what was already announced
### ===============================================================================

def load_reported() -> dict:
    """### The event keys already pushed to Discord, with when they were pushed.

    Returns:
        dict: Key to ISO timestamp. Empty on the first ever run, and empty rather than fatal
            when the file cannot be read - a lost state file costs a repeated alert, which is
            a great deal better than a stage that fails over it.
    """
    try:
        with open(EVENTS_STATE_PATH, "r") as f:
            state = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as e:
        logging.warning(
            f"Could not read {EVENTS_STATE_PATH} ({type(e).__name__}: {e}). Starting over, "
            "so some events may be announced a second time."
        )
        return {}

    reported = state.get("reported") if isinstance(state, dict) else None

    return reported if isinstance(reported, dict) else {}


def save_reported(reported: dict, now: datetime = None) -> None:
    """### Write the announced keys back, pruned to STATE_RETENTION_DAYS.

    Written through a temporary file and os.replace() for the same reason every data file is:
    a crash halfway through must not leave a half written state file, which would read as
    "nothing was ever announced" and repeat every alert of the last two days.

    Args:
        reported (dict): Key to ISO timestamp.
        now (datetime): The instant to prune against, normally now.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=STATE_RETENTION_DAYS)

    pruned = {}

    for key, stamp in reported.items():
        moment = _parse_timestamp(stamp)

        ### An unreadable timestamp is kept rather than dropped: dropping it would announce
        ### its event again, which is the one thing this file exists to prevent.
        if moment is None or moment >= cutoff:
            pruned[key] = stamp

    try:
        makedirs(path.dirname(EVENTS_STATE_PATH), exist_ok=True)

        handle, temp_path = tempfile.mkstemp(dir=path.dirname(EVENTS_STATE_PATH),
                                             prefix=".events-state.", suffix=".tmp")

        with os.fdopen(handle, "w") as f:
            json.dump({"reported": pruned}, f, indent=2)
            f.flush()
            os.fsync(f.fileno())

        os.replace(temp_path, EVENTS_STATE_PATH)
    except OSError as e:
        logging.warning(
            f"Could not write {EVENTS_STATE_PATH} ({type(e).__name__}: {e}). The events "
            "announced this run may be announced again next run."
        )


def pending_pushes(events: list, reported: dict, min_severity: int) -> list:
    """### The events that are severe enough and have not been announced yet.

    Args:
        events (list): This run's events, newest first.
        reported (dict): The keys already announced, from load_reported().
        min_severity (int): The lowest severity worth a message.

    Returns:
        list: Events to push, oldest first - a message reads as a timeline, not as a stack.
    """
    pending = [event for event in events
               if event["severity"] >= min_severity and event["key"] not in reported]

    return list(reversed(pending))


### ===============================================================================
### The stage
### ===============================================================================

def push_events(events: list, webhook: str, now: datetime = None) -> int:
    """### Announce the new, severe enough events on Discord.

    A webhook problem is logged and shrugged off, and only the events that actually went out
    are remembered as announced - a Discord outage must neither fail the stage nor silently
    swallow the alerts it prevented.

    Args:
        events (list): This run's events, newest first.
        webhook (str): The Discord webhook URL, or None.
        now (datetime): The instant the state file is stamped with, normally now.

    Returns:
        int: How many events were announced.
    """
    now = now or datetime.now(timezone.utc)
    reported = load_reported()
    pending = pending_pushes(events, reported, discord_min_severity())

    if not pending:
        ### Still written: the pruning is what keeps the file from growing all season
        save_reported(reported, now)
        return 0

    if not webhook:
        logging.warning(f"No Discord webhook configured, not announcing {len(pending)} event(s).")
        return 0

    included = pending[:MAX_DISCORD_EVENTS]
    lines = [f"{_severity_marker(event['severity'])} {event['text']}" for event in included]

    left_over = len(pending) - len(included)

    if left_over:
        lines.append(f"… und {left_over} weitere Ereignisse im Tagesplan.")

    title = ("1 neues Ereignis" if len(included) == 1
             else f"{len(included)} neue Ereignisse")
    colour = (COLOUR_ACT if any(event["severity"] >= SEVERITY_ACT for event in included)
              else COLOUR_WATCH)

    if not _post(f"Kickbase: {title}", "\n".join(lines), colour, webhook):
        return 0

    for event in included:
        reported[event["key"]] = now.isoformat()

    save_reported(reported, now)
    logging.info(f"Announced {len(included)} event(s) on Discord.")

    return len(included)


def _severity_marker(severity: int) -> str:
    """### The prefix a Discord line gets, so severity survives plain text.

    Args:
        severity (int): The event's severity.

    Returns:
        str: A marker, e.g. "[!!]" for SEVERITY_ACT.
    """
    if severity >= SEVERITY_ACT:
        return "[!!]"

    return "[!]" if severity == SEVERITY_WATCH else "[i]"


def _post(title: str, message: str, colour: int, webhook: str) -> bool:
    """### Send one Discord message, reporting whether it landed.

    Makes the same promise supervisor.notify() does - a webhook problem never ends the run -
    but hands the answer back, because the caller must not record an alert as announced when
    nobody received it.

    Caught broadly rather than as NotificatonException: today that is the only type
    discord_notification() lets out, and this promise should not depend on that staying true.

    Args:
        title (str): Embed title.
        message (str): Embed body.
        colour (int): Embed colour.
        webhook (str): The Discord webhook URL.

    Returns:
        bool: Whether the message was accepted.
    """
    try:
        miscellaneous.discord_notification(title, message, colour, webhook)
        return True
    except Exception as e:
        logging.error(f"Could not announce the events on Discord: {type(e).__name__}: {e}")
        return False


def write_events() -> None:
    """### Stage: diff the history store, write events.json, announce what is urgent.

    Runs late in a run, after every stage whose dataset it reads has appended its line to
    the history store - the newest snapshot of this run is the one the diff needs.

    The file is written before Discord is touched, so a webhook problem cannot cost the
    Tagesplan its data.
    """
    logging.info("Building the event stream...")

    now = datetime.now(timezone.utc)
    events = build_events(now)

    miscellaneous.write_json_to_file(events, "events.json")
    miscellaneous.write_timestamp("ts_events.json", rows=len(events))

    by_severity = {}

    for event in events:
        by_severity[event["severity"]] = by_severity.get(event["severity"], 0) + 1

    logging.info(f"{len(events)} event(s) in the last {EVENT_WINDOW_HOURS} hours "
                 f"(by severity: {dict(sorted(by_severity.items(), reverse=True))}).")

    if not events:
        ### Says which of the two empty states this is, because they need opposite reactions:
        ### an empty store is a young install, an empty window is a quiet market.
        logging.info("No events. On a fresh install the history store needs a second run "
                     "before there is anything to diff.")

    push_events(events, getenv("DISCORD_WEBHOOK"), now)
