"""Tests for the market value curves that survive between runs.

A run downloads one curve per player in the competition - 466 of them - six times a day,
and Kickbase moves market values once a day. Five of those six downloads hand back the same
bytes.

The disk cache under data/market-values stops that. What it must never do is hand back a
curve that no longer covers the current day, because market_value_deltas() reads the newest
entry as "today" and taken_free_players() reads back to START_DATE for a buy price.

    ./venv/bin/python tests/test_market_value_cache.py
"""

import json
import sys
import tempfile

from datetime import datetime, timezone
from os import environ, listdir, makedirs, path

### Make the repository root importable regardless of where this is run from
sys.path.insert(0, path.dirname(path.dirname(path.abspath(__file__))))

from backend import market_value_cache, miscellaneous
from backend.kickbase import http
from backend.kickbase.v4 import leagues

### ===============================================================================

PASSED = []

START = "2026-08-01T18:00:00Z"

### The marker the market response carries. Whether it names the last update or the next one
### does not matter - it changes once a day, at the update.
MVUD = "2026-08-13T21:00:00Z"
NEXT_MVUD = "2026-08-14T21:00:00Z"


def check(name, fn):
    """Run a single test with a cache directory of its own."""
    environ["START_DATE"] = START

    with tempfile.TemporaryDirectory() as tmp:
        original = market_value_cache.MARKET_VALUE_CACHE_DIR
        market_value_cache.MARKET_VALUE_CACHE_DIR = path.join(tmp, "market-values")
        leagues.clear_caches()

        try:
            fn()
        except AssertionError as e:
            print(f"  FAIL  {name}\n        {e}")
            PASSED.append(False)
        except Exception as e:
            print(f"  ERROR {name}\n        {type(e).__name__}: {e}")
            PASSED.append(False)
        else:
            print(f"  ok    {name}")
            PASSED.append(True)
        finally:
            market_value_cache.MARKET_VALUE_CACHE_DIR = original
            leagues.clear_caches()
            http.reset_session()


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.headers = {}

    def json(self):
        return self._payload


def today():
    """Today as the API dates its curve points: days since 1970-01-01."""
    return market_value_cache.julian_today()


def curve(newest_day=None, length=31, value=10_000_000):
    """A market value curve, oldest first, ending on the given day."""
    newest_day = today() if newest_day is None else newest_day

    return [{"dt": newest_day - offset, "mv": value + offset}
            for offset in reversed(range(length))]


class FakeApi:
    """Answers the market endpoint and every market value curve request."""

    def __init__(self, mvud=MVUD, newest_day=None):
        self.urls = []
        self.mvud = mvud
        self.newest_day = newest_day

    @property
    def curve_calls(self):
        return [u for u in self.urls if "/marketValue/" in u]

    def get(self, url, headers=None, timeout=None):
        self.urls.append(url)

        if url.endswith("/market"):
            payload = {"it": []}
            if self.mvud is not None:
                payload["mvud"] = self.mvud
            return FakeResponse(payload)

        return FakeResponse({"it": curve(self.newest_day)})


def run_with(api, fn):
    http.reset_session(api)
    try:
        return fn()
    finally:
        http.reset_session()


def a_run(api, player_ids=("755", "756")):
    """One run: read the market (which learns the marker), then fetch the curves."""
    leagues.get_market("token", "1")

    return [leagues.player_marketvalue("token", player_id) for player_id in player_ids]


def entries():
    """The cache files that exist, by player id."""
    directory = market_value_cache.MARKET_VALUE_CACHE_DIR

    if not path.isdir(directory):
        return {}

    found = {}
    for name in listdir(directory):
        if name.endswith(".json"):
            with open(path.join(directory, name)) as f:
                found[name[:-len(".json")]] = json.load(f)

    return found


def write_entry(player_id, **overrides):
    """Put an entry on the disk as a previous run would have left it."""
    entry = {
        "version": market_value_cache.CACHE_VERSION,
        "playerId": str(player_id),
        "mvud": MVUD,
        "days": miscellaneous.MARKET_VALUE_DAYS,
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
        "history": curve(),
    }
    entry.update(overrides)

    makedirs(market_value_cache.MARKET_VALUE_CACHE_DIR, exist_ok=True)
    with open(path.join(market_value_cache.MARKET_VALUE_CACHE_DIR, f"{player_id}.json"), "w") as f:
        json.dump(entry, f)

    return entry


### ===============================================================================
### The saving this exists for
### ===============================================================================


def test_a_curve_is_fetched_once_and_kept():
    api = FakeApi()
    run_with(api, lambda: a_run(api, ["755"]))

    assert len(api.curve_calls) == 1, f"got {api.curve_calls}"
    assert "755" in entries(), f"expected a cache entry, got {sorted(entries())}"


def test_the_next_run_of_the_same_day_fetches_no_curve_at_all():
    api = FakeApi()
    run_with(api, lambda: a_run(api))

    ### A new run: the memory caches go, the disk stays
    leagues.clear_caches()
    api.urls.clear()
    run_with(api, lambda: a_run(api))

    assert api.curve_calls == [], f"expected no curve requests, got {api.curve_calls}"


def test_the_cached_curve_is_the_one_the_api_served():
    api = FakeApi()
    fresh = run_with(api, lambda: a_run(api, ["755"]))[0]

    leagues.clear_caches()
    cached = run_with(api, lambda: a_run(api, ["755"]))[0]

    assert cached == fresh, "the cached curve differs from the one the API served"


def test_a_moved_market_value_invalidates_every_curve():
    api = FakeApi()
    run_with(api, lambda: a_run(api))

    ### The evening update happened: a new marker, and the curves have a new point
    leagues.clear_caches()
    api.urls.clear()
    api.mvud = NEXT_MVUD
    run_with(api, lambda: a_run(api))

    assert len(api.curve_calls) == 2, \
        f"expected both curves to be refetched, got {api.curve_calls}"


def test_the_refetched_curve_replaces_the_stale_entry():
    api = FakeApi()
    run_with(api, lambda: a_run(api, ["755"]))

    leagues.clear_caches()
    api.mvud = NEXT_MVUD
    api.newest_day = today() + 1
    run_with(api, lambda: a_run(api, ["755"]))

    entry = entries()["755"]
    assert entry["mvud"] == NEXT_MVUD, f"got {entry['mvud']}"
    assert entry["history"][-1]["dt"] == today() + 1, f"got {entry['history'][-1]}"


### ===============================================================================
### What must never be served
### ===============================================================================


def test_a_curve_that_no_longer_covers_the_current_day_is_refetched():
    """market_value_deltas() reads the newest point as today. Two days stale is a lie."""
    write_entry("755", history=curve(newest_day=today() - 3))
    api = FakeApi()

    run_with(api, lambda: a_run(api, ["755"]))

    assert len(api.curve_calls) == 1, f"expected a refetch, got {api.curve_calls}"


def test_yesterdays_newest_point_is_still_current():
    """The market value update runs in the evening, so for most of the day the newest point
    is dated yesterday. Refusing that would mean never hitting the cache."""
    write_entry("755", history=curve(newest_day=today() - 1))
    api = FakeApi()

    run_with(api, lambda: a_run(api, ["755"]))

    assert api.curve_calls == [], f"expected the cache to answer, got {api.curve_calls}"


def test_a_window_narrower_than_the_run_needs_is_refetched():
    """The runs of 2026-08-13 asked for 31 days and cached what came back. Those entries
    cover a fraction of miscellaneous.MARKET_VALUE_DAYS, so they have to be refetched
    instead of answering - which is how the cache heals itself after that window change."""
    write_entry("755", days=31)
    api = FakeApi()

    run_with(api, lambda: leagues.get_market("token", "1"))
    served = run_with(api, lambda: leagues.player_marketvalue("token", "755"))

    assert len(api.curve_calls) == 1, f"expected a refetch, got {api.curve_calls}"
    assert served, "expected a curve back"


def test_a_wider_cached_window_still_answers():
    """Only a narrower window is a reason to refetch. One that covers more than the run
    asks for holds every point the run reads."""
    write_entry("755", days=miscellaneous.MARKET_VALUE_DAYS + 30, history=curve(length=365))
    api = FakeApi()

    run_with(api, lambda: a_run(api, ["755"]))

    assert api.curve_calls == [], f"expected the cache to answer, got {api.curve_calls}"


def test_an_entry_from_another_format_version_is_ignored():
    write_entry("755", version=market_value_cache.CACHE_VERSION + 1)
    api = FakeApi()

    run_with(api, lambda: a_run(api, ["755"]))

    assert len(api.curve_calls) == 1, f"expected a refetch, got {api.curve_calls}"


def test_an_unreadable_entry_costs_a_request_and_nothing_else():
    makedirs(market_value_cache.MARKET_VALUE_CACHE_DIR, exist_ok=True)
    with open(path.join(market_value_cache.MARKET_VALUE_CACHE_DIR, "755.json"), "w") as f:
        f.write("{ this is not json")

    api = FakeApi()
    served = run_with(api, lambda: a_run(api, ["755"]))[0]

    assert len(api.curve_calls) == 1, f"expected a refetch, got {api.curve_calls}"
    assert served, "expected a curve back"


def test_a_curve_with_no_readable_date_is_refetched():
    write_entry("755", history=[{"mv": 100}])
    api = FakeApi()

    run_with(api, lambda: a_run(api, ["755"]))

    assert len(api.curve_calls) == 1, f"expected a refetch, got {api.curve_calls}"


def test_an_empty_cached_curve_is_refetched():
    """An empty history is what invents buy prices of zero."""
    write_entry("755", history=[])
    api = FakeApi()

    run_with(api, lambda: a_run(api, ["755"]))

    assert len(api.curve_calls) == 1, f"expected a refetch, got {api.curve_calls}"


### ===============================================================================
### Without a marker, nothing changes
### ===============================================================================


def test_a_market_response_without_the_marker_switches_the_disk_layer_off():
    """The one field this depends on. Missing it has to mean the old behaviour, not stale
    data."""
    write_entry("755")
    api = FakeApi(mvud=None)

    run_with(api, lambda: a_run(api, ["755"]))

    assert len(api.curve_calls) == 1, f"expected a fetch, got {api.curve_calls}"


def test_nothing_is_written_while_the_marker_is_unknown():
    """An entry that could never be validated would sit on the disk being ignored."""
    api = FakeApi(mvud=None)
    run_with(api, lambda: a_run(api, ["755"]))

    assert entries() == {}, f"expected no cache entries, got {sorted(entries())}"


def test_a_run_that_never_read_the_market_fetches_every_curve():
    """The market stage can fail before it gets a marker. Then this is the old behaviour."""
    write_entry("755")
    api = FakeApi()

    served = run_with(api, lambda: leagues.player_marketvalue("token", "755"))

    assert len(api.curve_calls) == 1, f"expected a fetch, got {api.curve_calls}"
    assert served, "expected a curve back"


### ===============================================================================
### clear_caches() and the prefetch
### ===============================================================================


def test_clear_caches_empties_the_memory_and_keeps_the_disk():
    api = FakeApi()
    run_with(api, lambda: a_run(api, ["755"]))

    leagues.clear_caches()

    assert market_value_cache.current_mvud() is None, "the marker belongs to one run"
    assert "755" in entries(), "the curves on disk must survive clear_caches()"


def test_the_prefetch_skips_the_players_the_disk_can_answer():
    api = FakeApi()
    run_with(api, lambda: a_run(api, ["755", "756", "757"]))

    leagues.clear_caches()
    api.urls.clear()

    def run():
        leagues.get_market("token", "1")
        leagues.prefetch_players("token", "1", ["755", "756", "757"])

    run_with(api, run)

    assert api.curve_calls == [], f"expected no curve requests, got {api.curve_calls}"
    ### The statistics are a different dataset and this change does not cache them
    assert len([u for u in api.urls if "?leagueId=" in u]) == 3, \
        f"expected three statistics requests, got {api.urls}"


def test_the_prefetch_still_fetches_what_the_disk_cannot_answer():
    api = FakeApi()
    run_with(api, lambda: a_run(api, ["755"]))

    leagues.clear_caches()
    api.urls.clear()

    def run():
        leagues.get_market("token", "1")
        leagues.prefetch_players("token", "1", ["755", "756"])

    run_with(api, run)

    assert len(api.curve_calls) == 1, \
        f"expected only the uncached player to be fetched, got {api.curve_calls}"
    assert "756" in api.curve_calls[0], f"the wrong player was fetched: {api.curve_calls}"


### ===============================================================================
### A cache that never works has to be visible in the log
### ===============================================================================


class CapturedLog:
    """Collects the records the code under test logs."""

    def __init__(self):
        self.records = []

    def handle(self, record):
        self.records.append(record)

    def messages(self, level=None):
        return [r.getMessage() for r in self.records
                if level is None or r.levelno >= level]

    def __enter__(self):
        import logging

        self.handler = logging.Handler()
        self.handler.emit = self.handle
        logging.getLogger().addHandler(self.handler)
        self.previous = logging.getLogger().level
        logging.getLogger().setLevel(logging.DEBUG)

        return self

    def __exit__(self, *exc):
        import logging

        logging.getLogger().removeHandler(self.handler)
        logging.getLogger().setLevel(self.previous)


def prefetch_log(api, player_ids):
    """Run a prefetch and return what it logged."""
    def run():
        leagues.get_market("token", "1")
        leagues.prefetch_players("token", "1", player_ids)

    with CapturedLog() as log:
        run_with(api, run)

    return log


def test_the_hit_count_is_logged_even_when_it_is_zero():
    """Every reason to skip an entry is a DEBUG line, so a cache that never works is silent
    in the INFO log - indistinguishable from one that is working."""
    import logging

    api = FakeApi()
    log = prefetch_log(api, ["755", "756"])

    assert any("0 of 2 market value curve(s) came from the disk cache" in m
               for m in log.messages(logging.INFO)), \
        f"expected the miss to be reported, got {log.messages(logging.INFO)}"


def test_the_hit_count_is_logged_when_the_cache_answers():
    import logging

    api = FakeApi()
    run_with(api, lambda: a_run(api, ["755", "756"]))
    leagues.clear_caches()

    log = prefetch_log(api, ["755", "756"])

    assert any("2 of 2 market value curve(s) came from the disk cache" in m
               for m in log.messages(logging.INFO)), \
        f"expected the hits to be reported, got {log.messages(logging.INFO)}"


def test_an_empty_cache_directory_is_called_out():
    """Zero hits with entries on disk is the daily miss. Zero hits with nothing on disk is a
    cache that is not being written - the case the count exists to separate."""
    import logging

    api = FakeApi()
    log = prefetch_log(api, ["755"])

    assert any("Nothing is cached" in m for m in log.messages(logging.WARNING)), \
        f"expected a warning about the empty cache, got {log.messages(logging.WARNING)}"


def test_a_missing_marker_is_named_as_the_reason():
    import logging

    api = FakeApi(mvud=None)
    log = prefetch_log(api, ["755"])

    assert any("no market value update marker" in m for m in log.messages(logging.WARNING)), \
        f"expected the missing marker to be named, got {log.messages(logging.WARNING)}"


def test_an_empty_history_is_named_as_the_reason():
    """What /marketValue/<days> answering with an empty "it" list looks like from here."""
    import logging

    class NoPoints(FakeApi):
        def get(self, url, headers=None, timeout=None):
            if "/marketValue/" in url:
                self.urls.append(url)
                return FakeResponse({"it": []})
            return super().get(url, headers=headers, timeout=timeout)

    api = NoPoints()
    log = prefetch_log(api, ["755"])

    assert any("empty history" in m for m in log.messages(logging.WARNING)), \
        f"expected the empty history to be named, got {log.messages(logging.WARNING)}"


def test_the_daily_miss_does_not_warn():
    """Entries on disk plus zero hits is the expected once-a-day refetch, not a fault."""
    import logging

    api = FakeApi()
    run_with(api, lambda: a_run(api, ["755"]))
    leagues.clear_caches()

    api.mvud = NEXT_MVUD
    log = prefetch_log(api, ["755"])

    assert not any("Nothing is cached" in m for m in log.messages(logging.WARNING)), \
        f"the daily refetch must not warn, got {log.messages(logging.WARNING)}"
    assert any("1 entry stored" in m for m in log.messages(logging.INFO)), \
        f"expected the stored count to be reported, got {log.messages(logging.INFO)}"


def test_entries_on_disk_counts_nothing_when_there_is_no_directory():
    assert market_value_cache.entries_on_disk() == 0, "expected zero for a missing directory"


### ===============================================================================
### The deltas the frontend reads must not change because of the cache
### ===============================================================================


def test_the_deltas_are_the_same_from_the_cache_as_from_the_api():
    api = FakeApi()
    fresh = miscellaneous.market_value_deltas(run_with(api, lambda: a_run(api, ["755"]))[0])

    leagues.clear_caches()
    cached = miscellaneous.market_value_deltas(run_with(api, lambda: a_run(api, ["755"]))[0])

    assert cached == fresh, f"expected {fresh}, got {cached}"


def test_a_player_id_that_is_not_a_file_name_is_refused():
    """The id comes from an API response and becomes a path segment."""
    market_value_cache.remember_mvud({"mvud": MVUD})

    market_value_cache.write("../../escaped", 31, curve())

    assert not path.exists(path.join(market_value_cache.MARKET_VALUE_CACHE_DIR,
                                     "..", "..", "escaped.json")), \
        "an entry escaped the cache directory"
    assert market_value_cache.read("../../escaped", 31) is None, \
        "expected the read to refuse the id as well"


### ===============================================================================

if __name__ == "__main__":
    print("the saving")
    check("a curve is fetched once and kept", test_a_curve_is_fetched_once_and_kept)
    check("the next run of the same day fetches no curve at all",
          test_the_next_run_of_the_same_day_fetches_no_curve_at_all)
    check("the cached curve is the one the API served",
          test_the_cached_curve_is_the_one_the_api_served)
    check("a moved market value invalidates every curve",
          test_a_moved_market_value_invalidates_every_curve)
    check("the refetched curve replaces the stale entry",
          test_the_refetched_curve_replaces_the_stale_entry)

    print("\nwhat must never be served")
    check("a curve that no longer covers the current day is refetched",
          test_a_curve_that_no_longer_covers_the_current_day_is_refetched)
    check("yesterday's newest point is still current",
          test_yesterdays_newest_point_is_still_current)
    check("a window narrower than the run needs is refetched",
          test_a_window_narrower_than_the_run_needs_is_refetched)
    check("a wider cached window still answers", test_a_wider_cached_window_still_answers)
    check("an entry from another format version is ignored",
          test_an_entry_from_another_format_version_is_ignored)
    check("an unreadable entry costs a request and nothing else",
          test_an_unreadable_entry_costs_a_request_and_nothing_else)
    check("a curve with no readable date is refetched",
          test_a_curve_with_no_readable_date_is_refetched)
    check("an empty cached curve is refetched", test_an_empty_cached_curve_is_refetched)

    print("\nwithout a marker, nothing changes")
    check("a market response without the marker switches the disk layer off",
          test_a_market_response_without_the_marker_switches_the_disk_layer_off)
    check("nothing is written while the marker is unknown",
          test_nothing_is_written_while_the_marker_is_unknown)
    check("a run that never read the market fetches every curve",
          test_a_run_that_never_read_the_market_fetches_every_curve)

    print("\nclear_caches() and the prefetch")
    check("clear_caches() empties the memory and keeps the disk",
          test_clear_caches_empties_the_memory_and_keeps_the_disk)
    check("the prefetch skips the players the disk can answer",
          test_the_prefetch_skips_the_players_the_disk_can_answer)
    check("the prefetch still fetches what the disk cannot answer",
          test_the_prefetch_still_fetches_what_the_disk_cannot_answer)

    print("\na cache that never works has to be visible")
    check("the hit count is logged even when it is zero",
          test_the_hit_count_is_logged_even_when_it_is_zero)
    check("the hit count is logged when the cache answers",
          test_the_hit_count_is_logged_when_the_cache_answers)
    check("an empty cache directory is called out", test_an_empty_cache_directory_is_called_out)
    check("a missing marker is named as the reason", test_a_missing_marker_is_named_as_the_reason)
    check("an empty history is named as the reason", test_an_empty_history_is_named_as_the_reason)
    check("the daily miss does not warn", test_the_daily_miss_does_not_warn)
    check("entries_on_disk() counts nothing when there is no directory",
          test_entries_on_disk_counts_nothing_when_there_is_no_directory)

    print("\nthe deltas the frontend reads")
    check("the deltas are the same from the cache as from the API",
          test_the_deltas_are_the_same_from_the_cache_as_from_the_api)
    check("a player id that is not a file name is refused",
          test_a_player_id_that_is_not_a_file_name_is_refused)

    total, passed = len(PASSED), sum(PASSED)
    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)
