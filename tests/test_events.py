"""Tests for the diff engine that turns the history store into events.

The engine reads a store that, in production, starts out empty and grows one line per dataset
per run. Which is why these tests build the store by hand, line by line, in a temporary
directory: every case below is a shape the store really takes.

  - **One test per event type.** Each type also gets the case just under its threshold, since
    a signal that fires on everything is the same as no signal at all.
  - **Dedupe.** The engine rebuilds the whole window on every run, six times a day. The same
    event must reach Discord exactly once.
  - **An empty or missing history.** The normal first state in production: no directory, no
    file, a single line with nothing to diff against, and a line cut short by a crashed host.
  - **The 48 hour window.** A snapshot older than the window is still read - a diff needs a
    baseline - but nothing derived from it is reported.

    ./venv/bin/python tests/test_events.py
"""

import json
import logging
import sys
import tempfile

from datetime import datetime, timedelta, timezone
from os import environ, makedirs, path

### Make the repository root importable regardless of where this is run from
sys.path.insert(0, path.dirname(path.dirname(path.abspath(__file__))))

from backend import events, miscellaneous

### ===============================================================================

### The engine logs on purpose where it carries on over damage - a truncated line, a missing
### all_transfers.json. Those paths are exercised below, and their warnings on stderr would
### bury the results of the tests that provoke them.
logging.disable(logging.CRITICAL)

PASSED = []

### A fixed "now", so which day file a snapshot lands in never depends on when the suite runs.
### Midday UTC on purpose: the store dates its files in the app timezone, and a snapshot 50
### hours before midday still falls inside the three day files the engine reads.
NOW = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)


def check(name, fn):
    """Run a single test and record the result."""
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


def hours_ago(hours):
    """The instant a given number of hours before the fixed now."""
    return NOW - timedelta(hours=hours)


class TempStore:
    """A history store, a data directory and an event state file, all in a temporary place."""

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name

        self.history_dir = path.join(root, "history")
        self.data_dir = path.join(root, "data")
        self.state_path = path.join(root, "state", "events-state.json")
        makedirs(self.data_dir, exist_ok=True)

        self.original = (miscellaneous.HISTORY_DIR, events.STATE_DIR, events.EVENTS_STATE_PATH)
        ### The writer's path builder is what the reader goes through, so patching it here is
        ### what redirects the reads
        miscellaneous.HISTORY_DIR = self.history_dir
        events.STATE_DIR = self.data_dir
        events.EVENTS_STATE_PATH = self.state_path

        return self

    def __exit__(self, *exc):
        (miscellaneous.HISTORY_DIR, events.STATE_DIR,
         events.EVENTS_STATE_PATH) = self.original
        self.tmp.cleanup()
        return False

    def snapshot(self, dataset, moment, rows):
        """Append one snapshot to a dataset's history, the way write_json_to_file does."""
        file_path = miscellaneous.history_file_path(dataset, moment)
        makedirs(path.dirname(file_path), exist_ok=True)

        with open(file_path, "a") as f:
            f.write(json.dumps({"ts": moment.isoformat(), "rows": rows}) + "\n")

    def raw_line(self, dataset, moment, text):
        """Append a line verbatim, to build the damage a crashed host leaves behind."""
        file_path = miscellaneous.history_file_path(dataset, moment)
        makedirs(path.dirname(file_path), exist_ok=True)

        with open(file_path, "a") as f:
            f.write(text)

    def transfers(self, items):
        """Write all_transfers.json, the record cash_hortung needs."""
        with open(path.join(self.data_dir, "all_transfers.json"), "w") as f:
            json.dump(items, f)


def market_row(player_id, price, market_value, **extra):
    """A market.json row, as main.market() writes it."""
    row = {
        "playerId": player_id,
        "teamId": "2",
        "firstName": "Max",
        "lastName": f"Muster {player_id}",
        "marketValue": market_value,
        "price": price,
        "seller": "Zoe",
        "isFreeAgent": False,
        "expiration": None,
        "listedSince": "2026-08-12T08:00:00+00:00",
        "offerCount": 0,
    }
    row.update(extra)

    return row


def value_row(player_id, market_value, **extra):
    """A market_value_changes.json row, as main.market_value_changes() writes it."""
    row = {
        "playerId": player_id,
        "teamId": "2",
        "position": "MF",
        "firstName": "Max",
        "lastName": f"Muster {player_id}",
        "marketValue": market_value,
        "manager": "Zoe",
    }
    row.update(extra)

    return row


def balance_row(user_id, balance, max_bid, name=None):
    """A balances.json row, reduced to the fields the engine reads."""
    return {
        "userId": user_id,
        "username": name or f"Manager {user_id}",
        "teamValue": 100_000_000,
        "balance": balance,
        "maxBid": max_bid,
    }


def types_of(built):
    """The event types of a built list, for the assertions below."""
    return [event["type"] for event in built]


def only(built, event_type):
    """The single event of a type, failing loudly when there is not exactly one."""
    matching = [event for event in built if event["type"] == event_type]
    assert len(matching) == 1, f"expected exactly one {event_type}, got {types_of(built)}"

    return matching[0]


### ===============================================================================
### One test per event type
### ===============================================================================

def test_new_listing():
    """A player in the newer snapshot and not in the older one is a new listing."""
    with TempStore() as store:
        store.snapshot("market", hours_ago(8), [market_row("1", 2_000_000, 2_000_000)])
        store.snapshot("market", hours_ago(4), [
            market_row("1", 2_000_000, 2_000_000),
            market_row("2", 3_000_000, 2_000_000),
        ])

        built = events.build_events(NOW)
        event = only(built, "neue_listung")

        assert event["playerId"] == "2", event
        ### Asking above market value: worth seeing, not worth an interruption
        assert event["severity"] == events.SEVERITY_WATCH, event
        assert "Neu auf dem Markt" in event["text"], event
        assert "3,00 Mio. €" in event["text"], event


def test_new_listing_under_market_value_is_urgent():
    """A listing at or below market value is the case worth waking someone for."""
    with TempStore() as store:
        store.snapshot("market", hours_ago(8), [])
        store.snapshot("market", hours_ago(4), [market_row("2", 1_800_000, 2_000_000)])

        event = only(events.build_events(NOW), "neue_listung")

        assert event["severity"] == events.SEVERITY_ACT, event
        assert "unter Marktwert" in event["text"], event


def test_price_cut():
    """A lower asking price on a listing that was already there is a price cut."""
    with TempStore() as store:
        store.snapshot("market", hours_ago(8), [market_row("1", 5_000_000, 4_000_000)])
        store.snapshot("market", hours_ago(4), [market_row("1", 4_500_000, 4_000_000)])

        built = events.build_events(NOW)
        event = only(built, "preissenkung")

        assert event["playerId"] == "1", event
        ### A tenth off is the threshold for urgency, and this is exactly a tenth
        assert event["severity"] == events.SEVERITY_ACT, event
        assert "-10 %" in event["text"], event
        assert "neue_listung" not in types_of(built), types_of(built)


def test_price_nudge_is_not_an_event():
    """A cut below PRICE_CUT_MIN_SHARE is rounding, not news."""
    with TempStore() as store:
        store.snapshot("market", hours_ago(8), [market_row("1", 5_000_000, 4_000_000)])
        store.snapshot("market", hours_ago(4), [market_row("1", 4_995_000, 4_000_000)])

        assert types_of(events.build_events(NOW)) == [], types_of(events.build_events(NOW))


def test_market_value_jump():
    """A market value that moved further than the threshold between two snapshots."""
    with TempStore() as store:
        store.snapshot("market_value_changes", hours_ago(20), [value_row("7", 10_000_000)])
        store.snapshot("market_value_changes", hours_ago(2), [value_row("7", 8_800_000)])

        event = only(events.build_events(NOW), "mv_sprung")

        assert event["playerId"] == "7", event
        assert event["severity"] == events.SEVERITY_ACT, event
        assert "gefallen" in event["text"], event
        assert "-1,20 Mio. €" in event["text"], event
        assert "-12 %" in event["text"], event


def test_small_market_value_move_is_not_a_jump():
    """Both thresholds have to be cleared: the share and the absolute floor."""
    with TempStore() as store:
        ### 2%, well above the absolute floor
        store.snapshot("market_value_changes", hours_ago(20), [value_row("7", 10_000_000)])
        store.snapshot("market_value_changes", hours_ago(2), [value_row("7", 9_800_000)])

        assert types_of(events.build_events(NOW)) == [], "a 2% move is not a jump"

    with TempStore() as store:
        ### 20%, but only 40.000 € on a cheap player
        store.snapshot("market_value_changes", hours_ago(20), [value_row("8", 200_000)])
        store.snapshot("market_value_changes", hours_ago(2), [value_row("8", 160_000)])

        assert types_of(events.build_events(NOW)) == [], "40.000 € is below the floor"


def test_market_value_rows_match_without_a_player_id():
    """Snapshots from before playerId was written to market_value_changes still diff."""
    with TempStore() as store:
        older = value_row("7", 10_000_000)
        newer = value_row("7", 8_800_000)
        del older["playerId"]
        del newer["playerId"]

        store.snapshot("market_value_changes", hours_ago(20), [older])
        store.snapshot("market_value_changes", hours_ago(2), [newer])

        event = only(events.build_events(NOW), "mv_sprung")

        ### Matched on name and team, so the event stands - without a player to link to
        assert event["playerId"] is None, event


def test_expiring_listing():
    """A Kickbase listing inside the warning window is reported, a distant one is not."""
    with TempStore() as store:
        soon = (NOW + timedelta(hours=2)).isoformat()
        later = (NOW + timedelta(hours=20)).isoformat()

        store.snapshot("market", hours_ago(2), [
            market_row("1", 1_000_000, 2_000_000, isFreeAgent=True, seller="Kickbase",
                       expiration=soon, offerCount=2),
            market_row("2", 1_000_000, 2_000_000, isFreeAgent=True, seller="Kickbase",
                       expiration=later),
            ### A manager's listing: Kickbase sends no expiry for these at all
            market_row("3", 1_000_000, 2_000_000),
        ])

        event = only(events.build_events(NOW), "laeuft_ab")

        assert event["playerId"] == "1", event
        assert event["severity"] == events.SEVERITY_ACT, event
        assert "in 2 Std." in event["text"], event
        assert "2 Gebote" in event["text"], event


def test_expiring_above_market_value_is_only_a_warning():
    """An expiry only matters if the listing was worth taking."""
    with TempStore() as store:
        store.snapshot("market", hours_ago(2), [
            market_row("1", 3_000_000, 2_000_000, isFreeAgent=True, seller="Kickbase",
                       expiration=(NOW + timedelta(minutes=30)).isoformat()),
        ])

        event = only(events.build_events(NOW), "laeuft_ab")

        assert event["severity"] == events.SEVERITY_WATCH, event
        assert "in unter 1 Std." in event["text"], event


def test_a_listing_at_exactly_the_market_value_is_not_a_bargain():
    """The shape of every Kickbase listing in a real market.json, to the euro.

    If "at or below market value" counted as a discount, every free agent Kickbase puts up
    would be an urgent alert and the webhook would carry eight of them a day.
    """
    with TempStore() as store:
        store.snapshot("market", hours_ago(4), [
            market_row("1", 2_000_000, 2_000_000, isFreeAgent=True, seller="Kickbase",
                       expiration=(NOW + timedelta(hours=2)).isoformat()),
        ])
        store.snapshot("market", hours_ago(2), [
            market_row("1", 2_000_000, 2_000_000, isFreeAgent=True, seller="Kickbase",
                       expiration=(NOW + timedelta(hours=2)).isoformat()),
            market_row("2", 3_000_000, 3_000_000, isFreeAgent=True, seller="Kickbase"),
        ])

        built = events.build_events(NOW)

        assert only(built, "laeuft_ab")["severity"] == events.SEVERITY_WATCH, built
        assert only(built, "neue_listung")["severity"] == events.SEVERITY_WATCH, built
        assert "unter Marktwert" not in only(built, "neue_listung")["text"], built


def test_forced_sale():
    """A manager whose remaining bidding room has almost gone."""
    with TempStore() as store:
        store.snapshot("balances", hours_ago(3), [
            ### 500.000 left of a 10.500.000 allowance: under a twentieth
            balance_row("u1", -10_000_000, 500_000, name="Zoe"),
            ### A third of the allowance left: in the red, but not cornered
            balance_row("u2", -2_000_000, 1_000_000, name="Max"),
            balance_row("u3", 5_000_000, 20_000_000, name="Ich"),
        ])

        event = only(events.build_events(NOW), "zwangsverkauf")

        assert event["managerId"] == "u1", event
        assert event["severity"] == events.SEVERITY_ACT, event
        assert "Zoe" in event["text"], event


def test_forced_sale_is_reported_once_a_day():
    """A manager broke in every snapshot of the day is one event, not one per run."""
    with TempStore() as store:
        for hours in (10, 6, 2):
            store.snapshot("balances", hours_ago(hours),
                           [balance_row("u1", -10_000_000, 500_000)])

        built = [event for event in events.build_events(NOW) if event["type"] == "zwangsverkauf"]

        assert len(built) == 1, built


def test_cash_hoarding():
    """A budget that only grew, over three snapshots, with nothing bought."""
    with TempStore() as store:
        store.transfers([])

        for hours, balance in ((10, 1_000_000), (6, 2_500_000), (2, 4_000_000)):
            store.snapshot("balances", hours_ago(hours), [balance_row("u1", balance, 30_000_000)])

        event = only(events.build_events(NOW), "cash_hortung")

        assert event["managerId"] == "u1", event
        assert event["severity"] == events.SEVERITY_WATCH, event
        assert "+3,00 Mio. €" in event["text"], event
        ### The span the snapshots actually cover, not the width of the window. On a young
        ### store three snapshots are eight hours apart, and claiming 48 would be claiming an
        ### observation that never happened.
        assert "kein Kauf in den letzten 8 Std." in event["text"], event


def test_cash_hoarding_needs_no_purchase():
    """A manager who bought in the window is not hoarding, however the balance moved."""
    with TempStore() as store:
        store.transfers([{
            "i": "t1",
            "dt": hours_ago(5).isoformat().replace("+00:00", "Z"),
            "t": 15,
            "data": {"byr": "Manager u1", "trp": 1_000_000},
        }])

        for hours, balance in ((10, 1_000_000), (6, 2_500_000), (2, 4_000_000)):
            store.snapshot("balances", hours_ago(hours), [balance_row("u1", balance, 30_000_000)])

        assert "cash_hortung" not in types_of(events.build_events(NOW))


def test_cash_hoarding_is_not_claimed_without_the_transfers():
    """Without all_transfers.json, "bought nothing" would be an assumption."""
    with TempStore() as store:
        for hours, balance in ((10, 1_000_000), (6, 2_500_000), (2, 4_000_000)):
            store.snapshot("balances", hours_ago(hours), [balance_row("u1", balance, 30_000_000)])

        assert "cash_hortung" not in types_of(events.build_events(NOW))


def test_cash_hoarding_ignores_a_budget_that_dipped():
    """Monotone, not just higher at the end than at the start."""
    with TempStore() as store:
        store.transfers([])

        for hours, balance in ((10, 1_000_000), (6, 500_000), (2, 4_000_000)):
            store.snapshot("balances", hours_ago(hours), [balance_row("u1", balance, 30_000_000)])

        assert "cash_hortung" not in types_of(events.build_events(NOW))


### ===============================================================================
### Dedupe
### ===============================================================================

def test_the_same_event_is_announced_once():
    """Six runs a day rebuild the same window; Discord hears about an event once."""
    with TempStore() as store:
        store.snapshot("market", hours_ago(8), [])
        store.snapshot("market", hours_ago(4), [market_row("2", 1_800_000, 2_000_000)])

        sent = []
        original = miscellaneous.discord_notification
        miscellaneous.discord_notification = lambda title, message, colour, url: sent.append(message)

        try:
            built = events.build_events(NOW)
            assert events.push_events(built, "https://example.invalid/hook", NOW) == 1, sent

            ### The next run, over an unchanged store
            assert events.push_events(events.build_events(NOW), "https://example.invalid/hook",
                                      NOW + timedelta(hours=4)) == 0, sent
        finally:
            miscellaneous.discord_notification = original

        assert len(sent) == 1, sent
        assert "unter Marktwert" in sent[0], sent


def test_a_failed_push_is_not_remembered():
    """A webhook outage must not swallow the alert it prevented."""
    with TempStore() as store:
        store.snapshot("market", hours_ago(8), [])
        store.snapshot("market", hours_ago(4), [market_row("2", 1_800_000, 2_000_000)])

        attempts = []

        def failing(title, message, colour, url):
            attempts.append(message)
            raise RuntimeError("Discord is down")

        original = miscellaneous.discord_notification
        miscellaneous.discord_notification = failing

        try:
            assert events.push_events(events.build_events(NOW), "https://example.invalid/hook", NOW) == 0

            miscellaneous.discord_notification = lambda title, message, colour, url: attempts.append(message)

            assert events.push_events(events.build_events(NOW), "https://example.invalid/hook", NOW) == 1
        finally:
            miscellaneous.discord_notification = original

        assert len(attempts) == 2, attempts


def test_a_flood_is_capped_and_drained_oldest_first():
    """A busy window fills more than one message, and nothing falls out in between.

    The cap exists because the embed has a length limit and forty lines are not read on a
    phone. What must not happen is that the events beyond the cap are quietly dropped: they
    stay unannounced, and the next run takes them.
    """
    with TempStore():
        ### Twelve urgent events, newest first, the order build_events() hands them over in
        built = [{
            "key": f"neue_listung|{index}|x", "type": "neue_listung",
            "severity": events.SEVERITY_ACT,
            "ts": hours_ago(index).isoformat(), "playerId": str(index), "managerId": None,
            "text": f"Ereignis {index:02d}",
        } for index in range(12)]

        messages = []
        original = miscellaneous.discord_notification
        miscellaneous.discord_notification = lambda title, message, colour, url: messages.append(message)

        try:
            assert events.push_events(built, "https://example.invalid/hook", NOW) == events.MAX_DISCORD_EVENTS
            assert events.push_events(built, "https://example.invalid/hook", NOW) == 2
        finally:
            miscellaneous.discord_notification = original

        assert len(messages) == 2, messages

        ### Oldest first, so a message reads as a timeline: event 11 is the oldest of twelve
        assert "Ereignis 11" in messages[0], messages[0]
        assert "Ereignis 02" in messages[0], messages[0]
        assert "Ereignis 01" not in messages[0], messages[0]
        assert "und 2 weitere Ereignisse" in messages[0], messages[0]

        ### The overflow, and only the overflow, in the next run
        assert "Ereignis 01" in messages[1], messages[1]
        assert "Ereignis 00" in messages[1], messages[1]
        assert "Ereignis 02" not in messages[1], messages[1]
        assert "weitere Ereignisse" not in messages[1], messages[1]


def test_only_severe_events_are_pushed():
    """The default threshold keeps the channel for the events worth interrupting for."""
    with TempStore():
        built = [
            {"key": "a", "type": "cash_hortung", "severity": events.SEVERITY_WATCH,
             "ts": NOW.isoformat(), "text": "beobachten"},
            {"key": "b", "type": "neue_listung", "severity": events.SEVERITY_ACT,
             "ts": NOW.isoformat(), "text": "handeln"},
        ]

        pending = events.pending_pushes(built, {}, events.DEFAULT_DISCORD_MIN_SEVERITY)

        assert [event["key"] for event in pending] == ["b"], pending


def test_the_severity_threshold_comes_from_the_environment():
    """DISCORD_MIN_SEVERITY lowers the bar; nonsense in it does not raise."""
    original = environ.get("DISCORD_MIN_SEVERITY")

    try:
        environ["DISCORD_MIN_SEVERITY"] = "2"
        assert events.discord_min_severity() == 2

        environ["DISCORD_MIN_SEVERITY"] = "sehr wichtig"
        assert events.discord_min_severity() == events.DEFAULT_DISCORD_MIN_SEVERITY

        environ["DISCORD_MIN_SEVERITY"] = ""
        assert events.discord_min_severity() == events.DEFAULT_DISCORD_MIN_SEVERITY
    finally:
        if original is None:
            environ.pop("DISCORD_MIN_SEVERITY", None)
        else:
            environ["DISCORD_MIN_SEVERITY"] = original


### ===============================================================================
### An empty, missing or damaged history
### ===============================================================================

def test_an_empty_store_produces_no_events():
    """The first state in production: the directory does not even exist."""
    with TempStore():
        assert events.build_events(NOW) == []


def test_a_single_snapshot_produces_no_diff():
    """After the very first run there is one line and nothing to compare it against."""
    with TempStore() as store:
        store.snapshot("market", hours_ago(1), [market_row("1", 1_000_000, 2_000_000)])

        ### No diff, and no expiry either: a manager's listing carries no expiry at all
        assert events.build_events(NOW) == []


def test_a_missing_day_does_not_break_the_diff():
    """A failed run leaves a gap; the snapshots either side of it still diff."""
    with TempStore() as store:
        store.snapshot("market", hours_ago(40), [market_row("1", 5_000_000, 4_000_000)])
        store.snapshot("market", hours_ago(2), [market_row("1", 4_000_000, 4_000_000)])

        event = only(events.build_events(NOW), "preissenkung")

        assert event["severity"] == events.SEVERITY_ACT, event


def test_a_truncated_line_is_skipped():
    """A host that died mid-append costs its own line and nothing else."""
    with TempStore() as store:
        store.snapshot("market", hours_ago(8), [market_row("1", 5_000_000, 4_000_000)])
        store.raw_line("market", hours_ago(6), '{"ts": "2026-08-13T0')
        store.raw_line("market", hours_ago(6), "\n")
        store.snapshot("market", hours_ago(4), [market_row("1", 4_000_000, 4_000_000)])

        event = only(events.build_events(NOW), "preissenkung")

        assert "-20 %" in event["text"], event


def test_junk_rows_are_ignored():
    """A payload that is not the expected shape produces nothing, not an exception."""
    with TempStore() as store:
        store.snapshot("market", hours_ago(8), ["not a row", 42, None])
        store.snapshot("market", hours_ago(4), [{"playerId": None}, {"noPlayerId": True}])
        store.snapshot("balances", hours_ago(4), [{"userId": "u1"}])
        store.snapshot("market_value_changes", hours_ago(4), [{"lastName": None}])

        assert events.build_events(NOW) == []


### ===============================================================================
### The window
### ===============================================================================

def test_events_older_than_the_window_are_dropped():
    """A snapshot outside the window is a baseline, not a headline."""
    with TempStore() as store:
        ### 50 hours back: read, because a diff needs something to compare against
        store.snapshot("market", hours_ago(50), [market_row("1", 5_000_000, 4_000_000)])
        ### 49 hours back: the price cut derived from it falls outside the window
        store.snapshot("market", hours_ago(49), [market_row("1", 4_000_000, 4_000_000)])
        ### Inside the window, and only this one is reported
        store.snapshot("market", hours_ago(1), [
            market_row("1", 4_000_000, 4_000_000),
            market_row("9", 1_000_000, 2_000_000),
        ])

        built = events.build_events(NOW)

        assert types_of(built) == ["neue_listung"], built
        assert built[0]["playerId"] == "9", built


def test_events_are_newest_first():
    """The Tagesplan reads top down, so the file arrives in that order."""
    with TempStore() as store:
        store.snapshot("market", hours_ago(30), [])
        store.snapshot("market", hours_ago(20), [market_row("1", 3_000_000, 2_000_000)])
        store.snapshot("market", hours_ago(2), [
            market_row("1", 3_000_000, 2_000_000),
            market_row("2", 3_000_000, 2_000_000),
        ])

        built = events.build_events(NOW)
        stamps = [event["ts"] for event in built]

        assert len(built) == 2, built
        assert stamps == sorted(stamps, reverse=True), stamps


### ===============================================================================

if __name__ == "__main__":
    print("\nDiff engine, one test per event type:")
    check("a new listing is an event", test_new_listing)
    check("a listing under market value is urgent", test_new_listing_under_market_value_is_urgent)
    check("a price cut is an event", test_price_cut)
    check("a price nudge is not", test_price_nudge_is_not_an_event)
    check("a market value jump is an event", test_market_value_jump)
    check("a small market value move is not", test_small_market_value_move_is_not_a_jump)
    check("market values match without a player id", test_market_value_rows_match_without_a_player_id)
    check("an expiring listing is an event", test_expiring_listing)
    check("an expensive expiring listing is only a warning", test_expiring_above_market_value_is_only_a_warning)
    check("a listing at exactly the market value is no bargain", test_a_listing_at_exactly_the_market_value_is_not_a_bargain)
    check("a cornered manager is an event", test_forced_sale)
    check("a cornered manager is reported once a day", test_forced_sale_is_reported_once_a_day)
    check("a growing budget without a purchase is an event", test_cash_hoarding)
    check("a purchase in the window rules hoarding out", test_cash_hoarding_needs_no_purchase)
    check("hoarding is not claimed without the transfers", test_cash_hoarding_is_not_claimed_without_the_transfers)
    check("a budget that dipped is not hoarding", test_cash_hoarding_ignores_a_budget_that_dipped)

    print("\nDedupe:")
    check("the same event is announced once", test_the_same_event_is_announced_once)
    check("a failed push is not remembered", test_a_failed_push_is_not_remembered)
    check("a flood is capped and drained oldest first", test_a_flood_is_capped_and_drained_oldest_first)
    check("only severe events are pushed", test_only_severe_events_are_pushed)
    check("the threshold comes from the environment", test_the_severity_threshold_comes_from_the_environment)

    print("\nAn empty, missing or damaged history:")
    check("an empty store produces no events", test_an_empty_store_produces_no_events)
    check("a single snapshot produces no diff", test_a_single_snapshot_produces_no_diff)
    check("a missing day does not break the diff", test_a_missing_day_does_not_break_the_diff)
    check("a truncated line is skipped", test_a_truncated_line_is_skipped)
    check("junk rows are ignored", test_junk_rows_are_ignored)

    print("\nThe 48 hour window:")
    check("events older than the window are dropped", test_events_older_than_the_window_are_dropped)
    check("events are newest first", test_events_are_newest_first)

    print(f"\n{sum(1 for p in PASSED if p)}/{len(PASSED)} checks passed.\n")

    sys.exit(0 if all(PASSED) else 1)
