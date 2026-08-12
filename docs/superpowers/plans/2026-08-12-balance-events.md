# Kontostand-Verlauf Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clicking a manager in the Balances table opens a dialog listing every event that moved their balance — the starting budget, then each buy and sell.

**Architecture:** The loop in `balances()` that computes the balance also records each step. The records ship as an `events` array inside `balances.json`, so the number in the table and the list behind it come from one calculation and cannot drift. The calculation itself moves into a pure, network-free helper in `backend/miscellaneous.py` so it can be tested.

**Tech Stack:** Python 3 (no test framework — tests are dependency-free scripts run with `./venv/bin/python`), React 18 with MUI v5 and `@mui/x-data-grid` v5.

**Spec:** `docs/superpowers/specs/2026-08-12-balance-events-design.md`

## Global Constraints

- All user-facing strings are German. Existing labels: „Manager", „Teamwert", „Kontostand", „Max. Gebot".
- Python comments in this repo use the `###` prefix; docstrings use the `"""### Summary` form. Follow it.
- JavaScript in `frontend/src` uses 4-space indent, double quotes and no semicolons. Follow the surrounding file.
- Tests are standalone scripts under `tests/`, run as `./venv/bin/python tests/<name>.py`, with a `check()` harness and a `PASSED` list. Copy the structure from `tests/test_start_date.py`.
- Player image base URL is `https://kickbase.b-cdn.net/` — verified to return `200 image/png` for a `pim` path from the feed.
- Team logos are local: `process.env.PUBLIC_URL + "/images/" + teamId + ".png"`, with an `onError` fallback to `/images/default.png`.
- Do not touch `turnovers()`, `revenue_sum.json` or any other table.

## File Structure

| File | Responsibility |
| --- | --- |
| `backend/miscellaneous.py` | Gains `PLAYER_IMAGE_BASE_URL` and `build_balance_events()` — the pure balance calculation, next to `filter_transfers_from()` and `parse_feed_timestamp()` which it uses. |
| `tests/test_balance_events.py` | New. Covers `build_balance_events()` with fixtures taken from the real feed. |
| `main.py` (`balances()`, lines 829-917) | Keeps the API calls and the `maxBid` math; delegates the balance to the helper and writes `events` into `balances.json`. |
| `frontend/src/components/BalanceEventsDialog.js` | New. Renders one manager's events as a dialog. Knows nothing about the Balances table. |
| `frontend/src/components/Balances.js` | Gains the selection state and the row click; stays the table. |

---

### Task 1: `build_balance_events()` in the backend

**Files:**
- Modify: `backend/miscellaneous.py` (add after `filter_transfers_from()`, which ends at line 299)
- Test: `tests/test_balance_events.py` (create)

**Interfaces:**
- Consumes: `filter_transfers_from(transfers, cutoff)` and `parse_feed_timestamp(timestamp)`, both already in `backend/miscellaneous.py`.
- Produces: `build_balance_events(transfers: list, user_name: str, initial_balance: float, start_datetime: datetime) -> list` and the module constant `PLAYER_IMAGE_BASE_URL`. Task 2 calls the function; the returned event dicts have exactly the keys `date`, `type`, `amount`, `balance`, `playerName`, `playerImage`, `teamId`, `tradePartner`, and Task 3 renders those keys.

**Background the implementer needs:**

An activity feed item with `"t": 15` is a transfer. Its `data` block names the two sides **by display name, not by user ID**:

```json
{
  "i": "12191501602", "t": 15, "coc": 0,
  "data": {
    "slr": "BenjaminScherner", "pi": "755", "pn": "Müller", "tid": "8",
    "t": 2, "trp": 2387664,
    "pim": "content/file/4b5913efdf3d4852a6f220421034c402.png",
    "tim": "content/file/fed743ec085c41a9b2453875157257fe.svg"
  },
  "dt": "2026-08-01T18:10:57Z"
}
```

`slr` is the seller, `byr` the buyer, `trp` the price, `pn` the player's last name, `pim` the player photo path, `tid` the team ID. Only one of `slr`/`byr` is present when the other side was Kickbase itself. Real samples live in `frontend/src/data/all_transfers.json`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_balance_events.py`:

```python
"""Tests for the per-manager balance event list.

build_balance_events() is the calculation behind the Kontostand column and behind the
event list the frontend shows when a manager is clicked. It is deliberately free of
network calls so it can be tested directly.

Shapes below are taken from real activity feed items in
frontend/src/data/all_transfers.json.

    ./venv/bin/python tests/test_balance_events.py
"""

import sys

from datetime import datetime, timezone
from os import path

### Make the repository root importable regardless of where this is run from
sys.path.insert(0, path.dirname(path.dirname(path.abspath(__file__))))

from backend import miscellaneous

### ===============================================================================

PASSED = []

START = datetime(2026, 8, 1, 18, 0, 0, tzinfo=timezone.utc)
INITIAL = 50_000_000
MANAGER = "Blida FC"


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


def transfer(dt, price, buyer=None, seller=None, name="Müller", team="8", image="content/file/abc.png"):
    """Build an activity feed transfer item like the API returns it."""
    data = {"pi": "755", "pn": name, "tid": team, "t": 2, "trp": price}

    if buyer:
        data["byr"] = buyer
    if seller:
        data["slr"] = seller
    if image:
        data["pim"] = image

    return {"i": dt, "t": 15, "coc": 0, "data": data, "dt": dt}


def build(transfers):
    """Run the function under test with the shared fixtures."""
    return miscellaneous.build_balance_events(transfers, MANAGER, INITIAL, START)


### ===============================================================================

def test_manager_without_transfers_gets_only_the_start_event():
    events = build([])

    assert len(events) == 1, f"expected a single event, got {events}"
    assert events[0]["type"] == "start", f"expected a start event, got {events[0]}"
    assert events[0]["amount"] == INITIAL, f"expected the starting budget, got {events[0]}"
    assert events[0]["balance"] == INITIAL, f"expected the starting balance, got {events[0]}"


def test_start_event_carries_no_player_or_partner():
    event = build([])[0]

    for field in ("playerName", "playerImage", "teamId", "tradePartner"):
        assert event[field] is None, f"expected {field} to be None on the start event, got {event}"


def test_transfers_of_other_managers_are_ignored():
    events = build([
        transfer("2026-08-02T10:00:00Z", 1_000_000, buyer="Jonny", seller="Gianluca"),
    ])

    assert len(events) == 1, f"expected only the start event, got {events}"


def test_last_event_balance_matches_the_running_sum():
    events = build([
        transfer("2026-08-02T10:00:00Z", 2_000_000, buyer=MANAGER, seller="Jonny"),
        transfer("2026-08-03T10:00:00Z", 5_000_000, seller=MANAGER, buyer="Jonny"),
    ])

    expected = INITIAL - 2_000_000 + 5_000_000
    assert events[-1]["balance"] == expected, \
        f"expected a final balance of {expected}, got {events[-1]}"


def test_events_come_back_in_chronological_order():
    ### The feed pages newest first, so the input is deliberately out of order here
    events = build([
        transfer("2026-08-05T10:00:00Z", 3_000_000, seller=MANAGER),
        transfer("2026-08-02T10:00:00Z", 1_000_000, buyer=MANAGER),
    ])

    dates = [event["date"] for event in events]
    assert dates == sorted(dates), f"expected chronological order, got {dates}"
    ### The running balance is only meaningful in that order
    assert events[1]["balance"] == INITIAL - 1_000_000, \
        f"expected the earlier buy to come first, got {events}"


def test_buy_is_negative_and_sell_is_positive():
    events = build([
        transfer("2026-08-02T10:00:00Z", 2_000_000, buyer=MANAGER, seller="Jonny"),
        transfer("2026-08-03T10:00:00Z", 5_000_000, seller=MANAGER, buyer="Jonny"),
    ])

    assert events[1]["type"] == "buy" and events[1]["amount"] == -2_000_000, \
        f"expected a negative buy, got {events[1]}"
    assert events[2]["type"] == "sell" and events[2]["amount"] == 5_000_000, \
        f"expected a positive sell, got {events[2]}"


def test_events_before_the_start_date_are_ignored():
    events = build([
        transfer("2026-07-30T10:00:00Z", 9_000_000, buyer=MANAGER),
        transfer("2026-08-02T10:00:00Z", 1_000_000, buyer=MANAGER),
    ])

    assert len(events) == 2, f"expected the pre-season transfer to be dropped, got {events}"
    assert events[-1]["balance"] == INITIAL - 1_000_000, \
        f"expected the dropped transfer not to move the balance, got {events}"


def test_trade_partner_is_the_other_manager():
    events = build([
        transfer("2026-08-02T10:00:00Z", 1_000_000, buyer=MANAGER, seller="Jonny"),
        transfer("2026-08-03T10:00:00Z", 2_000_000, seller=MANAGER, buyer="Gianluca"),
    ])

    assert events[1]["tradePartner"] == "Jonny", f"expected the seller as partner, got {events[1]}"
    assert events[2]["tradePartner"] == "Gianluca", f"expected the buyer as partner, got {events[2]}"


def test_trade_partner_is_none_for_a_one_sided_event():
    ### Bought from the Kickbase market: nobody is named on the other side
    events = build([transfer("2026-08-02T10:00:00Z", 1_000_000, buyer=MANAGER)])

    assert events[1]["tradePartner"] is None, \
        f"expected no trade partner, got {events[1]}"


def test_player_image_gets_the_cdn_prefix():
    events = build([transfer("2026-08-02T10:00:00Z", 1_000_000, buyer=MANAGER)])

    assert events[1]["playerImage"] == "https://kickbase.b-cdn.net/content/file/abc.png", \
        f"expected an absolute image URL, got {events[1]}"
    assert events[1]["playerName"] == "Müller", f"expected the player name, got {events[1]}"
    assert events[1]["teamId"] == "8", f"expected the team id, got {events[1]}"


def test_missing_player_image_stays_none():
    ### A relative path joined onto the CDN base would otherwise become the base URL itself
    events = build([transfer("2026-08-02T10:00:00Z", 1_000_000, buyer=MANAGER, image=None)])

    assert events[1]["playerImage"] is None, f"expected no image URL, got {events[1]}"


def test_the_input_list_is_not_reordered():
    ### The feed is cached per run and shared with turnovers(), so sorting it in place
    ### would silently reorder it for every other caller
    transfers = [
        transfer("2026-08-05T10:00:00Z", 3_000_000, seller=MANAGER),
        transfer("2026-08-02T10:00:00Z", 1_000_000, buyer=MANAGER),
    ]
    before = [item["dt"] for item in transfers]

    build(transfers)

    assert [item["dt"] for item in transfers] == before, \
        "expected the caller's list to be left alone"


### ===============================================================================

if __name__ == "__main__":
    print("build_balance_events()")
    check("a manager without transfers gets only the start event",
          test_manager_without_transfers_gets_only_the_start_event)
    check("the start event carries no player or partner",
          test_start_event_carries_no_player_or_partner)
    check("transfers of other managers are ignored",
          test_transfers_of_other_managers_are_ignored)
    check("the last event balance matches the running sum",
          test_last_event_balance_matches_the_running_sum)
    check("events come back in chronological order",
          test_events_come_back_in_chronological_order)
    check("a buy is negative and a sell is positive",
          test_buy_is_negative_and_sell_is_positive)
    check("events before START_DATE are ignored",
          test_events_before_the_start_date_are_ignored)
    check("the trade partner is the other manager",
          test_trade_partner_is_the_other_manager)
    check("the trade partner is none for a one sided event",
          test_trade_partner_is_none_for_a_one_sided_event)
    check("the player image gets the CDN prefix",
          test_player_image_gets_the_cdn_prefix)
    check("a missing player image stays none",
          test_missing_player_image_stays_none)
    check("the input list is not reordered",
          test_the_input_list_is_not_reordered)

    total, passed = len(PASSED), sum(PASSED)
    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./venv/bin/python tests/test_balance_events.py`
Expected: every check reports `ERROR ... AttributeError: module 'backend.miscellaneous' has no attribute 'build_balance_events'`, exit code 1.

- [ ] **Step 3: Add the constant and the function**

In `backend/miscellaneous.py`, add the constant next to the other module constants at the top (after `MAX_PROFILEPIC_WORKERS`, around line 29):

```python
### The activity feed references player photos as relative paths. This is the CDN that
### serves them.
PLAYER_IMAGE_BASE_URL = "https://kickbase.b-cdn.net/"
```

And the function directly after `filter_transfers_from()` (which ends at line 299):

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./venv/bin/python tests/test_balance_events.py`
Expected: `12/12 passed`, exit code 0.

- [ ] **Step 5: Run the existing tests to check nothing regressed**

Run: `./venv/bin/python tests/test_start_date.py && ./venv/bin/python tests/test_caching.py`
Expected: both report all checks passed.

- [ ] **Step 6: Commit**

```bash
git add backend/miscellaneous.py tests/test_balance_events.py
git commit -m "feat: build the balance event list for a manager"
```

---

### Task 2: Wire the helper into `balances()`

**Files:**
- Modify: `main.py:829-917` (`balances()`)

**Interfaces:**
- Consumes: `miscellaneous.build_balance_events(transfers, user_name, initial_balance, start_datetime)` and `miscellaneous.get_start_datetime()` from Task 1.
- Produces: each entry in `balances.json` gains an `events` key holding the array from Task 1. Task 3 reads it.

**What changes and why:**

`balances()` currently walks the feed itself (`main.py:865-883`) and never applies the `START_DATE` cutoff that `turnovers()` applies at `main.py:516-522`. Both go away in favour of the helper. The `user_balances` dict (`main.py:848`, `856`, `886`) is write-only bookkeeping around that loop and goes with it. `teamValue`, the `maxBid` math and the profile picture prefetch stay exactly as they are.

- [ ] **Step 1: Replace the body of the calculation**

In `main.py`, replace lines 838-886 — from `initial_balance = float(...)` down to and including `user_balances[user_id] = balance` — with:

```python
    initial_balance = float(getenv("START_MONEY", 50000000))
    final_balances = []

    ### Everything from before the season start or league reset belongs to a previous
    ### season and must not count towards this balance, the same cutoff turnovers() uses.
    start_datetime = miscellaneous.get_start_datetime()

    ### Get all transfers from the API
    all_transfers = leagues.transfers(user_token, selected_league.id)
    logging.debug(f"Found {len(all_transfers)} transfers in total")

    ### Read the league members
    with open(path.join(DATA_DIR, "STATIC_users.json"), "r") as f:
        league_users = json.load(f)

    ### Look the profile pictures up all at once. A user without one costs a full
    ### timeout, so doing them one by one dominated the runtime of this function.
    miscellaneous.prefetch_profilepics(league_users.keys())

    ### Loop through all users in the league
    for user_id, user_name in league_users.items():
        user_stats = leagues.user_stats(user_token, selected_league.id, user_id)
        team_value = user_stats["tv"]
        logging.debug(f"Team value of {user_name}: {team_value}")

        ### The events are the balance: the last one carries the current figure, and the
        ### frontend shows the same list behind the Kontostand column.
        events = miscellaneous.build_balance_events(all_transfers, user_name, initial_balance, start_datetime)
        balance = events[-1]["balance"]

        logging.debug(f"User: {user_name}; Starter balance: {initial_balance}; Balance after {len(events) - 1} transfer(s): {balance}")
```

- [ ] **Step 2: Add `events` to the written record**

In the same function, in the `final_balances.append({...})` block (was `main.py:904-911`), change the `balance` value and add `events`:

```python
        ### Create a custom json dict for every user
        final_balances.append({
            "userId": user_id,
            "username": user_name,
            "profilePic": miscellaneous.get_profilepic(user_id),
            "teamValue": team_value,
            "balance": balance,
            "maxBid": round(maxbid, 0),
            "events": events,
        })
```

`balance` is already rounded by the helper, so the old `round(balance, 0)` goes. Keeping it would round twice and could put a value in the table that no event produces.

- [ ] **Step 3: Update the docstring**

The docstring at `main.py:830` still promises only balances. Replace it with:

```python
    """### Retrieves the estimated balances for all users in the league, together with the
    events that produced them. Daily login bonus and money from achievements are not
    considered.

    Args:
        user_token (str): The user's kkstrauth token.
        selected_league (object): The league the user wants to get data from for the frontend.
    """
```

- [ ] **Step 4: Verify the function compiles and nothing else referenced the removed names**

Run:
```bash
./venv/bin/python -c "import main" && grep -n "user_balances" main.py
```
Expected: the import succeeds and `grep` prints nothing (exit code 1 from grep is fine).

- [ ] **Step 5: Regenerate `balances.json` from the committed fixtures**

The pipeline needs live Kickbase credentials, so generate the file from the data already in the repo instead. Write this to the scratchpad and run it:

```python
### /private/tmp/claude-501/-Users-maximilianshiraishi-Desktop-Projekte-kickbase-plus/ee158d31-0401-46c9-8b7e-4418658d2d23/scratchpad/regen_balances.py
import json
import sys

from datetime import datetime, timezone

sys.path.insert(0, ".")

from backend import miscellaneous

DATA = "frontend/src/data"

with open(f"{DATA}/all_transfers.json") as f:
    transfers = json.load(f)
with open(f"{DATA}/STATIC_users.json") as f:
    users = json.load(f)
with open(f"{DATA}/balances.json") as f:
    balances = json.load(f)

start = datetime(2026, 8, 1, 18, 0, 0, tzinfo=timezone.utc)

for entry in balances:
    entry["events"] = miscellaneous.build_balance_events(transfers, entry["username"], 50_000_000, start)
    print(f"{entry['username']}: {len(entry['events'])} events, "
          f"table {entry['balance']} vs events {entry['events'][-1]['balance']}")

with open(f"{DATA}/balances.json", "w") as f:
    json.dump(balances, f, indent=2)
```

Run, from the repository root (the script inserts `.` into `sys.path`):
```bash
./venv/bin/python /private/tmp/claude-501/-Users-maximilianshiraishi-Desktop-Projekte-kickbase-plus/ee158d31-0401-46c9-8b7e-4418658d2d23/scratchpad/regen_balances.py
```
Expected: one line per manager. The two balance figures should agree; report any manager where they differ instead of ignoring it — a mismatch means the old loop and the helper disagree.

- [ ] **Step 6: Commit**

```bash
git add main.py frontend/src/data/balances.json
git commit -m "feat: record the balance events in balances.json"
```

---

### Task 3: The dialog in the frontend

**Files:**
- Create: `frontend/src/components/BalanceEventsDialog.js`
- Modify: `frontend/src/components/Balances.js`

**Interfaces:**
- Consumes: the `events` array from Task 2, with keys `date`, `type` (`"start"` / `"buy"` / `"sell"`), `amount`, `balance`, `playerName`, `playerImage`, `teamId`, `tradePartner`.
- Consumes: `PagedDataGrid` (default export of `./PagedDataGrid`), plus `currencyFormatter`, `deltaCellClassName` and `deltaColumnStyles` from `./SharedConstants`.
- Produces: `BalanceEventsDialog` as the default export, with props `manager` (a Balances row, or `null`) and `onClose`.

**Two things that will bite otherwise:**

1. `PagedDataGrid` picks its page size from `rows.length` in a `useState` initialiser, which only runs on mount. A dialog that stays mounted with zero rows would keep a page size of 1 forever. Returning `null` when there is no manager keeps every open a fresh mount.
2. The start event has no player, no team and no partner. The `renderCell` functions must return `null` there rather than request `/images/null.png`.

- [ ] **Step 1: Create the dialog component**

```jsx
import React from "react"

import Avatar from "@mui/material/Avatar"
import Box from "@mui/material/Box"
import Button from "@mui/material/Button"
import Dialog from "@mui/material/Dialog"
import DialogActions from "@mui/material/DialogActions"
import DialogContent from "@mui/material/DialogContent"
import DialogTitle from "@mui/material/DialogTitle"

import PagedDataGrid from "./PagedDataGrid"
import { currencyFormatter, deltaCellClassName, deltaColumnStyles } from "./SharedConstants"

const eventTypeLabels = { start: "Startbudget", buy: "Kauf", sell: "Verkauf" }

// The feed only names a counterpart when another manager was on the other side of the
// transfer. A market purchase, a market sale and the starting budget all come from
// Kickbase itself.
const tradePartnerLabel = (partner) => partner || "Kickbase"

function BalanceEventsDialog({ manager, onClose }) {
    // Not just an optimisation: PagedDataGrid reads rows.length once, on mount, to pick
    // its page size. Mounting fresh per manager is what keeps that number right.
    if (!manager)
        return null

    const events = manager.events || []

    const columns = [
        {
            field: "teamId",
            headerName: "Verein",
            width: 70,
            headerAlign: "center",
            align: "center",
            sortable: false,
            // The start event has no team, and "/images/null.png" would only 404
            renderCell: (params) => params.value ? (
                <img
                    src={process.env.PUBLIC_URL + "/images/" + params.value + ".png"}
                    alt=""
                    width="30"
                    onError={(e) => {
                        e.target.onerror = null // Prevent infinite loop if default.png is also missing
                        e.target.src = process.env.PUBLIC_URL + "/images/default.png"
                    }}
                />
            ) : null,
        },
        {
            field: "playerName",
            headerName: "Spieler",
            flex: 2,
            headerAlign: "center",
            renderCell: (params) => params.value ? (
                <div style={{ display: "flex", alignItems: "center" }}>
                    <Avatar src={params.row.playerImage} alt="" sx={{ marginRight: 1, width: 30, height: 30 }} />
                    {params.value}
                </div>
            ) : null,
        },
        {
            field: "date",
            headerName: "Datum",
            type: "dateTime",
            flex: 2,
            headerAlign: "center",
            align: "center",
            // Sorting a mix of "…Z" and "…+00:00" strings would compare the offset
            // suffix, so hand the grid real dates
            valueGetter: ({ value }) => new Date(value),
            valueFormatter: ({ value }) => value.toLocaleString("de-DE"),
        },
        {
            field: "type",
            headerName: "Event",
            flex: 1,
            headerAlign: "center",
            align: "center",
            valueFormatter: ({ value }) => eventTypeLabels[value] || value,
        },
        {
            field: "tradePartner",
            headerName: "Handelspartner",
            flex: 2,
            headerAlign: "center",
            align: "center",
            valueFormatter: ({ value }) => tradePartnerLabel(value),
        },
        {
            field: "amount",
            headerName: "Betrag",
            type: "number",
            flex: 2,
            headerAlign: "center",
            valueFormatter: ({ value }) => currencyFormatter.format(Number(value)),
            cellClassName: deltaCellClassName,
        },
        {
            field: "balance",
            headerName: "Saldo",
            type: "number",
            flex: 2,
            headerAlign: "center",
            valueFormatter: ({ value }) => currencyFormatter.format(Number(value)),
            cellClassName: "font-tabular-nums",
        },
    ]

    // The backend returns the events oldest first, which is the only order in which the
    // Saldo column reads from top to bottom. No sort model, so that order is the default.
    const rows = events.map((event, i) => ({ id: i, ...event }))

    return (
        <Dialog open onClose={onClose} maxWidth="md" fullWidth>
            <DialogTitle>Kontostand-Verlauf: {manager.username}</DialogTitle>
            <DialogContent>
                <Box sx={deltaColumnStyles}>
                    <PagedDataGrid rows={rows} columns={columns} />
                </Box>
            </DialogContent>
            <DialogActions>
                <Button onClick={onClose}>Schließen</Button>
            </DialogActions>
        </Dialog>
    )
}

export default BalanceEventsDialog
```

- [ ] **Step 2: Open the dialog from the Balances table**

In `frontend/src/components/Balances.js`, change the import line and the component. The columns array stays untouched.

Replace line 1:

```jsx
import React, { useState } from "react"
```

Add after the `Avatar` import (line 5):

```jsx
import BalanceEventsDialog from "./BalanceEventsDialog"
```

Replace the row mapping and the return (lines 54-73) with:

```jsx
    // Fill the rows with the attributes from the JSON file
    const rows = data.map((row, i) => (
        {
            id: i,
            username: row.username,
            profilePic: row.profilePic,
            teamValue: row.teamValue,
            balance: row.balance,
            maxBid: row.maxBid,
            events: row.events,
        }
    ))

    // Populate the table
    return (
        <>
            <PagedDataGrid
                rows={rows}
                columns={columns}
                initialState={{ sorting: { sortModel: [{ field: "teamValue", sort: "desc" }] } }}
                onRowClick={(params) => setSelectedManager(params.row)}
                sx={{ "& .MuiDataGrid-row": { cursor: "pointer" } }}
            />
            <BalanceEventsDialog manager={selectedManager} onClose={() => setSelectedManager(null)} />
        </>
    )
```

And add the state as the first line of the `Balances()` body, before the `columns` definition:

```jsx
    const [selectedManager, setSelectedManager] = useState(null)
```

- [ ] **Step 3: Verify it builds**

Run: `cd frontend && npm run build`
Expected: `Compiled successfully.` — warnings that already existed before this change are fine, new ones are not. `react-scripts build` treats unused variables as warnings, so read the output rather than trusting the exit code.

- [ ] **Step 4: Check it in the browser**

Run: `cd frontend && npm start`

Then verify by hand:
1. Rows in the Balances tab show a pointer cursor.
2. Clicking a manager opens „Kontostand-Verlauf: <Name>".
3. The first row is „Startbudget" with +50.000.000 €, no player, no logo, and „Kickbase" as the partner.
4. The last row's Saldo equals the Kontostand in the table behind the dialog.
5. Buys are red, sells green with a leading „+".
6. Escape and „Schließen" both close it; reopening another manager shows that manager's events.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/BalanceEventsDialog.js frontend/src/components/Balances.js
git commit -m "feat: show a manager's balance events on row click"
```

---

### Task 4: Document the feature

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Note the behaviour change of the balance calculation**

`README.md` documents `START_DATE` as affecting "the transfer and revenue calculations". The balance now honours it too. Find that sentence in the environment variable table and change it to name the balance as well:

> The instant the season started or your league was reset, as an ISO 8601 timestamp with an explicit UTC offset, e.g. `2026-08-01T18:00:00Z`. Events in the Kickbase activity feed from before this instant are excluded from the transfer, revenue and balance calculations.

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: note that the balance honours START_DATE"
```

---

## Out of Scope

- Login bonuses and achievement rewards still do not appear in the calculation. The `balances()` docstring already says so; the event list makes the gap easier to see but does not cause it.
- No frontend test suite. The repo has none, and the spec asks for Python tests only.
- No changes to `turnovers()`, `revenue_sum.json` or the other tables.
