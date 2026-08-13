"""Tests for walking the activity feed only back to what is already recorded.

The feed pages 26 entries at a time, newest first, and every run walked it to the end - the
whole season, six times a day, growing with every matchday. Everything older than the newest
transfer in all_transfers.json is already recorded, so the walk stops there.

What must not change: the callers get the same complete list of transfers they got from a
full walk, and drop_reverted_transfers() still sees both halves of a reverted booking.

    ./venv/bin/python tests/test_feed_watermark.py
"""

import json
import sys
import tempfile

from os import environ, makedirs, path

### Make the repository root importable regardless of where this is run from
sys.path.insert(0, path.dirname(path.dirname(path.abspath(__file__))))

from backend import miscellaneous
from backend.kickbase import http
from backend.kickbase.v4 import leagues

### ===============================================================================

PASSED = []

START = "2026-08-01T18:00:00Z"


def check(name, fn):
    """Run a single test with a data directory of its own."""
    environ["START_DATE"] = START

    with tempfile.TemporaryDirectory() as tmp:
        original = (miscellaneous.PUBLIC_DIR, miscellaneous.STATE_DIR,
                    miscellaneous.TIMESTAMP_DIR,
                    miscellaneous.LAST_GOOD_DIR, miscellaneous.HISTORY_DIR)
        miscellaneous.PUBLIC_DIR = tmp
        miscellaneous.STATE_DIR = tmp
        miscellaneous.TIMESTAMP_DIR = path.join(tmp, "timestamps")
        miscellaneous.LAST_GOOD_DIR = path.join(tmp, "last-good")
        miscellaneous.HISTORY_DIR = path.join(tmp, "history")
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
            (miscellaneous.PUBLIC_DIR, miscellaneous.STATE_DIR,
             miscellaneous.TIMESTAMP_DIR,
             miscellaneous.LAST_GOOD_DIR, miscellaneous.HISTORY_DIR) = original
            leagues.clear_caches()
            http.reset_session()


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.headers = {}

    def json(self):
        return self._payload


def transfer(number):
    """A transfer feed item. Higher numbers are newer."""
    day = 1 + number // 24
    hour = number % 24

    return {
        "i": f"t{number:04d}",
        "t": 15,
        "coc": 0,
        "dt": f"2026-08-{day + 1:02d}T{hour:02d}:00:00Z",
        "data": {"pi": str(1000 + number), "pn": f"Player{number}", "tid": "8", "t": 2,
                 "trp": 1_000_000 + number, "byr": "Anna"},
    }


def noise(number):
    """A feed item that is not a transfer: matchday points, a gift, a comment."""
    return {"i": f"n{number:04d}", "t": 8, "coc": 0,
            "dt": "2026-08-05T12:00:00Z", "data": {}}


class FakeFeed:
    """Answers the activity feed, newest first, 26 entries a page."""

    def __init__(self, items):
        ### The API hands out the newest first
        self.items = list(reversed(items))
        self.urls = []

    @property
    def pages(self):
        return len(self.urls)

    def start_of(self, url):
        return int(url.split("start=")[1])

    def get(self, url, headers=None, timeout=None):
        self.urls.append(url)
        start = self.start_of(url)

        return FakeResponse({"af": self.items[start:start + leagues.FEED_PAGE_SIZE]})


def with_feed(items, fn):
    feed = FakeFeed(items)
    http.reset_session(feed)
    try:
        return feed, fn()
    finally:
        http.reset_session()


def record(items):
    """Write all_transfers.json as turnovers() would have, oldest first."""
    makedirs(miscellaneous.STATE_DIR, exist_ok=True)
    with open(path.join(miscellaneous.STATE_DIR, miscellaneous.ALL_TRANSFERS_FILE), "w") as f:
        json.dump(items, f)


def ids(transfers):
    return [item["i"] for item in transfers]


### ===============================================================================
### Nothing recorded yet: the full walk, exactly as before
### ===============================================================================


def test_with_nothing_recorded_the_whole_feed_is_walked():
    items = [transfer(n) for n in range(60)]
    feed, result = with_feed(items, lambda: leagues.transfers("token", "1"))

    ### 60 items is three pages, plus the empty page that ends the walk
    assert feed.pages == 4, f"expected the whole feed to be walked, got {feed.pages} page(s)"
    assert len(result) == 60, f"expected every transfer, got {len(result)}"


def test_a_missing_all_transfers_file_is_a_full_walk():
    """The state of a fresh container."""
    items = [transfer(n) for n in range(10)]
    feed, result = with_feed(items, lambda: leagues.transfers("token", "1"))

    assert len(result) == 10, f"expected every transfer, got {len(result)}"


def test_an_unreadable_all_transfers_file_is_a_full_walk():
    makedirs(miscellaneous.STATE_DIR, exist_ok=True)
    with open(path.join(miscellaneous.STATE_DIR, miscellaneous.ALL_TRANSFERS_FILE), "w") as f:
        f.write("{ not json")

    items = [transfer(n) for n in range(10)]
    feed, result = with_feed(items, lambda: leagues.transfers("token", "1"))

    assert len(result) == 10, f"expected every transfer, got {len(result)}"


def test_an_empty_all_transfers_file_is_a_full_walk():
    record([])

    items = [transfer(n) for n in range(60)]
    feed, result = with_feed(items, lambda: leagues.transfers("token", "1"))

    assert feed.pages == 4, f"expected the whole feed to be walked, got {feed.pages} page(s)"
    assert len(result) == 60, f"expected every transfer, got {len(result)}"


def test_the_feed_is_returned_newest_first():
    """The order a full walk produced, and the one taken_free_players() depends on: it takes
    the first buy price it finds for a player."""
    items = [transfer(n) for n in range(5)]
    _, result = with_feed(items, lambda: leagues.transfers("token", "1"))

    assert ids(result) == ["t0004", "t0003", "t0002", "t0001", "t0000"], f"got {ids(result)}"


### ===============================================================================
### The watermark
### ===============================================================================


def test_the_walk_stops_at_the_first_recorded_transfer():
    all_items = [transfer(n) for n in range(60)]
    ### Everything but the newest two was recorded by an earlier run
    record(all_items[:58])

    feed, result = with_feed(all_items, lambda: leagues.transfers("token", "1"))

    assert feed.pages == 1, f"expected a single page, got {feed.pages}"
    assert len(result) == 60, f"the callers must still see every transfer, got {len(result)}"


def test_the_result_is_the_same_as_a_full_walk():
    all_items = [transfer(n) for n in range(60)]

    _, full = with_feed(all_items, lambda: leagues.transfers("token", "1"))

    leagues.clear_caches()
    record(all_items[:58])
    _, watermarked = with_feed(all_items, lambda: leagues.transfers("token", "1"))

    assert ids(watermarked) == ids(full), \
        f"expected the same list; full walk gave {len(full)}, watermark gave {len(watermarked)}"


def test_nothing_new_still_costs_one_page():
    all_items = [transfer(n) for n in range(60)]
    record(all_items)

    feed, result = with_feed(all_items, lambda: leagues.transfers("token", "1"))

    assert feed.pages == 1, f"expected a single page, got {feed.pages}"
    assert len(result) == 60, f"got {len(result)}"


def test_a_recorded_transfer_is_not_returned_twice():
    all_items = [transfer(n) for n in range(30)]
    record(all_items[:29])

    _, result = with_feed(all_items, lambda: leagues.transfers("token", "1"))

    assert len(ids(result)) == len(set(ids(result))), f"duplicates in {ids(result)}"


def test_pages_of_other_event_types_do_not_end_the_walk():
    """A page can hold nothing but matchday points. That is not the end of the feed."""
    all_items = [transfer(n) for n in range(4)]
    all_items += [noise(n) for n in range(30)]
    all_items += [transfer(n) for n in range(100, 102)]
    record([transfer(n) for n in range(4)])

    feed, result = with_feed(all_items, lambda: leagues.transfers("token", "1"))

    assert len(result) == 6, f"expected four recorded and two new transfers, got {len(result)}"
    assert feed.pages >= 2, f"expected the walk to page past the noise, got {feed.pages}"


def test_the_rest_of_the_page_holding_the_watermark_is_still_read():
    """The feed is ordered by time, not by what this project has seen, so the entries next to
    a known one on the same page still count."""
    all_items = [transfer(n) for n in range(10)]
    ### Recorded: everything except number 5, which sits in the middle of the first page
    record([item for item in all_items if item["i"] != "t0005"])

    _, result = with_feed(all_items, lambda: leagues.transfers("token", "1"))

    assert "t0005" in ids(result), f"an entry next to the watermark was lost: {ids(result)}"
    assert len(result) == 10, f"got {len(result)}"


def test_an_explicit_empty_list_forces_a_full_walk():
    all_items = [transfer(n) for n in range(60)]
    record(all_items)

    feed, result = with_feed(
        all_items, lambda: leagues.transfers("token", "1", known_transfers=[]))

    assert feed.pages == 4, f"expected the whole feed to be walked, got {feed.pages}"
    assert len(result) == 60, f"got {len(result)}"


def test_the_result_is_still_cached_per_run():
    all_items = [transfer(n) for n in range(10)]
    record(all_items[:9])

    def twice():
        leagues.transfers("token", "1")
        return leagues.transfers("token", "1")

    feed, _ = with_feed(all_items, twice)

    assert feed.pages == 1, f"expected one walk for the whole run, got {feed.pages} page(s)"


### ===============================================================================
### Reverted bookings survive the watermark
### ===============================================================================


def test_both_halves_of_a_reverted_booking_reach_drop_reverted_transfers():
    """The real incident from 2026-08-08. The reversal was recorded by an earlier run and the
    replacement arrives now, so they only meet if the watermark hands back both."""
    seiwald_buy = {"i": "12195697057", "t": 15, "coc": 0, "dt": "2026-08-02T23:26:18Z",
                   "data": {"byr": "shirazzi", "pi": "6176", "pn": "Seiwald", "tid": "40",
                            "t": 2, "trp": 18000000}}
    seiwald_reverted = {"i": "12221615722", "t": 15, "coc": 0, "dt": "2026-08-08T17:12:55Z",
                        "data": {"slr": "shirazzi", "byr": "Reddy", "pi": "6176",
                                 "pn": "Seiwald", "tid": "40", "t": 2, "trp": 18900001}}
    seiwald_final = {"i": "12221642212", "t": 15, "coc": 0, "dt": "2026-08-08T17:19:55Z",
                     "data": {"slr": "shirazzi", "byr": "Reddy", "pi": "6176", "pn": "Seiwald",
                              "tid": "40", "t": 2, "trp": 19000000}}

    record([seiwald_buy, seiwald_reverted])

    _, result = with_feed([seiwald_buy, seiwald_reverted, seiwald_final],
                          lambda: leagues.transfers("token", "1"))

    kept = ids(miscellaneous.drop_reverted_transfers(result))

    assert kept == ["12221642212", "12195697057"], \
        f"expected the reverted booking to be dropped and the other two kept, got {kept}"


def test_a_recorded_reversal_is_not_dropped_from_what_is_returned():
    """drop_reverted_transfers() needs to see the reversal to find it. The watermark must not
    quietly filter the feed on its own."""
    all_items = [transfer(n) for n in range(6)]
    record(all_items[:5])

    _, result = with_feed(all_items, lambda: leagues.transfers("token", "1"))

    assert ids(result) == ["t0005", "t0004", "t0003", "t0002", "t0001", "t0000"], \
        f"got {ids(result)}"


### ===============================================================================
### load_known_transfers()
### ===============================================================================


def test_load_known_transfers_reads_what_turnovers_wrote():
    items = [transfer(n) for n in range(3)]
    record(items)

    assert ids(miscellaneous.load_known_transfers()) == ids(items), "expected the file back"


def test_load_known_transfers_shrugs_off_a_file_that_is_not_a_list():
    makedirs(miscellaneous.STATE_DIR, exist_ok=True)
    with open(path.join(miscellaneous.STATE_DIR, miscellaneous.ALL_TRANSFERS_FILE), "w") as f:
        json.dump({"transfers": []}, f)

    assert miscellaneous.load_known_transfers() == [], "expected an empty list"


### ===============================================================================

if __name__ == "__main__":
    print("nothing recorded yet")
    check("with nothing recorded the whole feed is walked",
          test_with_nothing_recorded_the_whole_feed_is_walked)
    check("a missing all_transfers.json is a full walk", test_a_missing_all_transfers_file_is_a_full_walk)
    check("an unreadable all_transfers.json is a full walk",
          test_an_unreadable_all_transfers_file_is_a_full_walk)
    check("an empty all_transfers.json is a full walk", test_an_empty_all_transfers_file_is_a_full_walk)
    check("the feed is returned newest first", test_the_feed_is_returned_newest_first)

    print("\nthe watermark")
    check("the walk stops at the first recorded transfer",
          test_the_walk_stops_at_the_first_recorded_transfer)
    check("the result is the same as a full walk", test_the_result_is_the_same_as_a_full_walk)
    check("nothing new still costs one page", test_nothing_new_still_costs_one_page)
    check("a recorded transfer is not returned twice", test_a_recorded_transfer_is_not_returned_twice)
    check("pages of other event types do not end the walk",
          test_pages_of_other_event_types_do_not_end_the_walk)
    check("the rest of the page holding the watermark is still read",
          test_the_rest_of_the_page_holding_the_watermark_is_still_read)
    check("an explicit empty list forces a full walk", test_an_explicit_empty_list_forces_a_full_walk)
    check("the result is still cached per run", test_the_result_is_still_cached_per_run)

    print("\nreverted bookings")
    check("both halves of a reverted booking reach drop_reverted_transfers()",
          test_both_halves_of_a_reverted_booking_reach_drop_reverted_transfers)
    check("a recorded reversal is not dropped from what is returned",
          test_a_recorded_reversal_is_not_dropped_from_what_is_returned)

    print("\nload_known_transfers()")
    check("reads what turnovers() wrote", test_load_known_transfers_reads_what_turnovers_wrote)
    check("shrugs off a file that is not a list",
          test_load_known_transfers_shrugs_off_a_file_that_is_not_a_list)

    total, passed = len(PASSED), sum(PASSED)
    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)
