"""
### The market value curves that survive between runs.

A run downloads one market value curve per player in the competition - 466 of them - and
does it six times a day. The curves do not move six times a day: Kickbase updates market
values once, in the evening, and says so in the `mvud` field of the market response. Five
of those six downloads therefore hand back a curve byte for byte identical to the one the
previous run already had.

This module is the disk layer that stops that happening. `leagues.player_marketvalue()`
asks here first and only reaches Kickbase on a miss.

### Cache format

One file per player, `data/market-values/<player_id>.json`:

```json
{
    "version": 1,
    "playerId": "755",
    "mvud": "2026-08-13T21:00:00Z",
    "days": 31,
    "fetchedAt": "2026-08-13T09:12:44.101+02:00",
    "history": [{"dt": 20678, "mv": 12300000}, ...]
}
```

  - `mvud` is the market value update marker the market response carried when the curve was
    fetched. It is the invalidation token.
  - `days` is the window that was asked for, because the run needs a curve reaching back to
    START_DATE and that window grows with the season.
  - `fetchedAt` is diagnostic only. Nothing reads it; it is there so a human staring at the
    cache directory can tell how old an entry is.
  - `history` is the API's "it" list, verbatim.

`playerId` is redundant with the file name and kept for the same reason: a file that has to
be read by hand should say what it is.

### What invalidates an entry

An entry is used only if all four hold. Any of them failing means a normal fetch, so
every unknown lands on the old behaviour rather than on stale data:

1. **`version` matches.** The format changed, the entry is unreadable, throw it away.
2. **`mvud` equals the marker of the current run.** If the run does not know its marker -
    the market stage failed before it got one - the disk layer is off entirely.
3. **`days` is at least the window this run needs.** A 31 day curve cached in August cannot
    answer a February run that has to reach back to the season start.
4. **The newest entry of the curve is dated today or yesterday.** This is the belt to the
    `mvud` braces, and it is what makes the whole thing safe: it checks the curve itself
    rather than trusting a field whose exact meaning is not verifiable from here.

Point 4 tolerates one day rather than demanding today, and that is deliberate. The example
response in `leagues.get_market()` has `dt` (the response time) at 19:30Z and `mvud` at
21:00Z of the same day, so `mvud` names the *next* update, and during the day the newest
curve entry is the one from the previous evening's update - yesterday's date. Demanding
today would mean never hitting the cache at all between midnight and the evening update,
which is most of the day.

### Assumption

That `mvud` changes exactly once per market value update and is stable in between. Whether
it names the last update or the next one does not matter - either way it changes once a day,
at the update. If it were something else entirely, the failure is benign: a value that
changes on every request means a permanent miss and the old behaviour, and a value that
never changes is caught by point 4 within two days.
"""

import json
import logging
import os
import re
import tempfile

from datetime import datetime, timezone

from backend import exceptions
from backend.paths import MARKET_VALUE_CACHE_DIR

### ===============================================================================

### Bumped when the meaning of a field changes, so entries written by an older version are
### dropped instead of being misread
CACHE_VERSION = 1

### How many days the newest curve entry may lag behind today before the entry is refetched.
### One, because during the day the newest entry is the previous evening's update - see the
### module docstring.
MAX_CURVE_LAG_DAYS = 1

### The market value update marker this run is caching against, from the market response.
### None means "not known", which switches the disk layer off rather than guessing.
_mvud = None


def remember_mvud(market_response: dict) -> None:
    """### Take the market value update marker out of a market response.

    Called by `leagues.get_market()`, which is the only response in this project known to
    carry the marker. The market stage runs first, so the marker is known before the first
    curve is asked for.

    A response without the field leaves the marker unset, and an unset marker means every
    curve is fetched exactly as it was before this module existed.

    Args:
        market_response (dict): The decoded `/leagues/{id}/market` response.
    """
    global _mvud

    marker = (market_response or {}).get("mvud")

    if not marker:
        logging.info("The market response carried no 'mvud', so market value curves are "
                     "fetched fresh this run instead of being read from the disk cache.")
        _mvud = None
        return

    if marker != _mvud:
        logging.debug(f"Market value update marker for this run: {marker}")

    _mvud = str(marker)


def current_mvud() -> str:
    """### The marker this run caches against, or None if it is not known.

    Returns:
        str: The marker, or None.
    """
    return _mvud


def forget_mvud() -> None:
    """### Drop the marker, so the next run has to learn it again.

    Called from `leagues.clear_caches()`. Only the marker goes: the curves on disk are the
    point of this module and outlive any number of runs.
    """
    global _mvud

    _mvud = None


def _entry_path(player_id: str) -> str:
    """### Where one player's cached curve lives.

    Args:
        player_id (str): The player id.

    Raises:
        exceptions.KickbaseException: If the id could not be used as a file name. Refused
            rather than sanitised: the id comes from an API response and becomes a path
            segment, so a "../.." that quietly resolved would read and write outside the
            mounted volume.

    Returns:
        str: The absolute path of the entry, whose directory may not exist yet.
    """
    name = str(player_id)

    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise exceptions.KickbaseException(
            f"'{player_id}' cannot be a market value cache key: only letters, digits, "
            "underscores and dashes, so the name can never leave MARKET_VALUE_CACHE_DIR.")

    return os.path.join(MARKET_VALUE_CACHE_DIR, f"{name}.json")


def _newest_entry_day(history: list) -> int:
    """### The day number of the newest point of a curve.

    The API dates each point with the number of days since 1970-01-01 - the same encoding
    `miscellaneous.julian_to_date()` decodes, and the one `taken_free_players()` already
    relies on to find the season start value.

    Args:
        history (list): The curve, oldest first.

    Returns:
        int: The day number, or None if the curve does not carry a readable one.
    """
    if not history:
        return None

    day = history[-1].get("dt") if isinstance(history[-1], dict) else None

    ### bool is an int as far as isinstance is concerned, and a "dt" of True is not a date
    if isinstance(day, bool) or not isinstance(day, int):
        return None

    return day


def read(player_id: str, days: int, today: int = None) -> list:
    """### The cached curve for a player, if it is still good for this run.

    Every reason to say no is logged at DEBUG and answered with None, which the caller
    reads as "fetch it". Nothing in here can make a run fail: a cache that cannot be read
    costs a request, and a request is what used to happen anyway.

    Args:
        player_id (str): The player to look up.
        days (int): The window this run needs the curve to cover.
        today (int): Today as a day number since 1970-01-01, for tests. Defaults to now.

    Returns:
        list: The cached curve, or None if there is no usable entry.
    """
    if _mvud is None:
        return None

    try:
        entry_path = _entry_path(player_id)
    except exceptions.KickbaseException as e:
        logging.warning(f"Not reading a market value cache entry: {e}")
        return None

    try:
        with open(entry_path, "r") as f:
            entry = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as e:
        logging.debug(f"Market value cache entry for player {player_id} is unreadable: {e}")
        return None

    if not isinstance(entry, dict) or entry.get("version") != CACHE_VERSION:
        logging.debug(f"Market value cache entry for player {player_id} is from another "
                      f"format version, ignoring it.")
        return None

    if entry.get("mvud") != _mvud:
        logging.debug(f"Market values moved since player {player_id} was cached "
                      f"({entry.get('mvud')} -> {_mvud}).")
        return None

    cached_days = entry.get("days")
    if not isinstance(cached_days, int) or cached_days < days:
        logging.debug(f"Cached curve of player {player_id} covers {cached_days} day(s), "
                      f"this run needs {days}.")
        return None

    history = entry.get("history")
    if not isinstance(history, list) or not history:
        logging.debug(f"Cached curve of player {player_id} holds no points.")
        return None

    newest = _newest_entry_day(history)
    if newest is None:
        logging.debug(f"Cached curve of player {player_id} has no readable date on its "
                      f"newest point.")
        return None

    if today is None:
        today = julian_today()

    if newest < today - MAX_CURVE_LAG_DAYS:
        logging.debug(f"Cached curve of player {player_id} ends {today - newest} day(s) "
                      f"before today, so it no longer covers the current day.")
        return None

    return history


def write(player_id: str, days: int, history: list) -> None:
    """### Keep a freshly fetched curve for the next run.

    Written atomically, and a failure is logged and shrugged off - the same deal as the
    `.last-good` snapshots. A cache that could not be written must never be the reason a
    run loses data it already has in hand.

    Nothing is written while the marker is unknown. An entry without one could never be
    validated, so it would sit on the disk forever being ignored.

    Args:
        player_id (str): The player the curve belongs to.
        days (int): The window that was asked for.
        history (list): The curve as the API returned it.
    """
    if _mvud is None or not history:
        return

    entry = {
        "version": CACHE_VERSION,
        "playerId": str(player_id),
        "mvud": _mvud,
        "days": days,
        "fetchedAt": datetime.now().astimezone().isoformat(),
        "history": history,
    }

    try:
        entry_path = _entry_path(player_id)
        os.makedirs(MARKET_VALUE_CACHE_DIR, exist_ok=True)

        ### The temporary file sits in the target directory, because os.replace() is only
        ### atomic within one filesystem and this directory is a volume mount
        handle, temp_path = tempfile.mkstemp(dir=MARKET_VALUE_CACHE_DIR,
                                            prefix=f".{player_id}.", suffix=".tmp")

        try:
            with os.fdopen(handle, "w") as f:
                json.dump(entry, f, separators=(",", ":"))
            os.replace(temp_path, entry_path)
        except Exception:
            try:
                os.remove(temp_path)
            except OSError:
                pass
            raise
    except Exception as e:
        logging.warning(f"Could not cache the market value curve of player {player_id}, "
                        f"carrying on: {type(e).__name__}: {e}")


def entries_on_disk() -> int:
    """### How many curves are cached at all, whether or not they are still current.

    Read for one reason: telling "the market values moved, so everything was refetched"
    apart from "nothing is being cached at all". The first is what this module does once a
    day; the second is a broken cache, and without this number the two look identical in the
    log - both are simply zero cache hits. See prefetch_players().

    Returns:
        int: The number of entries, 0 if the directory does not exist or cannot be listed.
    """
    try:
        return sum(1 for name in os.listdir(MARKET_VALUE_CACHE_DIR)
                   if name.endswith(".json"))
    except OSError:
        return 0


def julian_today() -> int:
    """### Today as the API dates its curve points: days since 1970-01-01, in UTC.

    UTC because that is what the rest of the project assumes of these day numbers -
    `taken_free_players()` compares one against a START_DATE rendered in UTC.

    Returns:
        int: The day number.
    """
    return (datetime.now(timezone.utc).date() - datetime(1970, 1, 1).date()).days
