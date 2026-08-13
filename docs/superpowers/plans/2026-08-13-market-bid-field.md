# Editable bid field on the transfer market — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The "Dein Gebot" column shows, in grey, the bid that breaks even at a configurable horizon, and clicking it places or withdraws that bid through the Kickbase API.

**Architecture:** Two env variables (`BEP_GROWTH_DAYS`, `BEP_TARGET_DAYS`) are read in Python only; `main.py` writes the averaged daily growth per market row plus a small `config.json`, so the frontend needs no env variable of its own. Two new Flask endpoints wrap `POST`/`DELETE` on Kickbase's offer routes, read the resulting bid back from a fresh market fetch, and patch the row in `market.json`. The frontend keeps a local override map so a confirmed bid shows before the rebuild that patch triggers.

**Tech Stack:** Python 3.12 + Flask + requests (backend), React 18 + MUI 5 + `@mui/x-data-grid` v5 (MIT) + `react-number-format` (frontend). No test framework on the Python side — tests are standalone scripts. Jest via `react-scripts` on the frontend.

**Spec:** `docs/superpowers/specs/2026-08-13-market-bid-field-design.md`. Read it before starting; it carries the reasoning this plan only executes.

## Global Constraints

- **Work in the worktree `.claude/worktrees/market-bid-field` on branch `feat/market-bid-field`.** It already exists and already has `frontend/src/data/*.json` copied in. Never work in the main checkout.
- **Never stage blindly.** No `git add -A`, no `git commit -a`. Name paths explicitly and run `git status` before every commit — another agent's staged files would otherwise ride along.
- **User-facing frontend text is German.** Error messages, tooltips, dialogs, snackbars.
- **Python comments start with `###`, docstrings with `"""### Zusammenfassung`.**
- **JavaScript in `frontend/src`: four-space indent, double quotes, no semicolons.**
- **Python tests are standalone scripts** under `tests/`, no framework, run as `./venv/bin/python tests/<name>.py`, structured like `tests/test_start_date.py`: a `check(name, fn)` helper, a `PASSED` list, a `__main__` block printing `n/m passed` and exiting non-zero on failure.
- **Frontend tests** run as `cd frontend && CI=true npm test -- --watchAll=false`.
- **Defaults must be behaviour-neutral.** `BEP_GROWTH_DAYS=3` and `BEP_TARGET_DAYS=3` must leave every number in the "Tage bis BEP" column exactly as it is today.
- **`frontend/src/data/*.json` is runtime-generated and gitignored** (`.gitignore` ignores `*.json` except `package.json`/`package-lock.json`). Never commit these; never assume a file is there without checking.

---

### Task 1: Probe the Kickbase write endpoints against the live API

Nothing in `backend/` writes, so the offer routes, their payload keys and whether an offer id even exists are unverified. **The recorded market response on file carries no offer id** — `ofs` entries are `{"u", "unm", "uoid", "uop", "st", "uim"}` — which would rule out addressing a delete route by one. Tasks 7 and 8 cannot be written correctly before this is settled.

This task writes no product code. Its deliverable is an *Evidence from the live API* section appended to the spec.

**Files:**
- Create (throwaway, **not** committed): `<scratchpad>/probe_offers.py`
- Modify: `docs/superpowers/specs/2026-08-13-market-bid-field-design.md` (add the evidence section, replacing the *Open questions* section's promise)

**Interfaces:**
- Consumes: nothing.
- Produces: the confirmed values Tasks 7 and 8 build on — the POST path, the POST body key for the price, the success payload shape, whether an offer id is exposed and where, the DELETE path, and the HTTP status plus body shape of a rejection.

- [ ] **Step 1: Write the read-only probe**

Create `probe_offers.py` in the scratchpad directory. Step 1 reads only — it places nothing.

```python
"""Probe the Kickbase offer endpoints. Throwaway, not committed.

    set -a; source .env; set +a
    ./venv/bin/python <scratchpad>/probe_offers.py read
"""

import json
import sys

from os import getenv, path

sys.path.insert(0, "/Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/.claude/worktrees/market-bid-field")

import requests

from backend.kickbase.v4 import leagues, user

TOKEN = None
LEAGUE_ID = None


def connect():
    """Log in and pick the same league the frontend shows."""
    global TOKEN, LEAGUE_ID
    user_info, TOKEN = user.login(getenv("KB_MAIL"), getenv("KB_PASSWORD"), getenv("DISCORD_WEBHOOK"))
    league_list = leagues.get_league_list(TOKEN)
    wanted = getenv("KB_LIGA")
    league = next((l for l in league_list if l.name == wanted), league_list[0])
    LEAGUE_ID = league.id
    print(f"user {user_info.id} ({user_info.name}), league {league.name} ({LEAGUE_ID})")
    return user_info.id


def headers():
    return {"Content-Type": "application/json", "Accept": "application/json",
            "Cookie": f"kkstrauth={TOKEN};"}


def raw_market():
    """The market as the API sends it, before Market_Players trims it."""
    url = f"https://api.kickbase.com/v4/leagues/{LEAGUE_ID}/market"
    return requests.get(url, headers=headers(), timeout=15).json()


def probe_read(own_user_id):
    """Step 1: does an own offer carry an id, and under which key?"""
    market = raw_market()
    with_offers = [item for item in market["it"] if item.get("ofs") or item.get("uop")]

    print(f"\n{len(market['it'])} listings, {len(with_offers)} carrying an offer\n")
    for item in with_offers:
        print(f"--- {item.get('fn')} {item.get('n')} (player {item['i']}) ---")
        print(f"    top level uoid={item.get('uoid')} uop={item.get('uop')} ofc={item.get('ofc')}")
        print(f"    ofs: {json.dumps(item.get('ofs'), indent=6)}")

    if not with_offers:
        print("No own offer on the market right now. Place one in the Kickbase app on any")
        print("player, re-run this, and the ofs shape will show whether it carries an id.")

    cheapest = min(market["it"], key=lambda item: item.get("prc") or item["mv"])
    print(f"\ncheapest listing: {cheapest.get('fn')} {cheapest.get('n')} "
          f"player {cheapest['i']}, prc {cheapest.get('prc')}, mv {cheapest['mv']}")


if __name__ == "__main__":
    own = connect()
    mode = sys.argv[1] if len(sys.argv) > 1 else "read"
    if mode == "read":
        probe_read(own)
```

- [ ] **Step 2: Run the read-only probe and record the offer shape**

```bash
cd /Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus
set -a; source .env; set +a
./venv/bin/python <scratchpad>/probe_offers.py read
```

Write down verbatim: the full `ofs` entry of an own offer, whether it holds an id and under which key, and the player id plus price of the cheapest listing (needed in step 4).

**If no own offer exists on the market**, the `ofs` shape cannot be observed. Stop and ask the user to place any bid in the Kickbase app, then re-run. Do not guess the key.

- [ ] **Step 3: Probe the POST route with a deliberately invalid price**

Add to the probe script:

```python
def probe_reject():
    """Step 2: does the POST route exist? A 4xx proves it; a 404 means the path is wrong.

    A price of 1 is below every market value, so nothing can be placed here.
    """
    for candidate in [
        f"https://api.kickbase.com/v4/leagues/{LEAGUE_ID}/market/{PLAYER_ID}/offers",
        f"https://api.kickbase.com/v4/leagues/{LEAGUE_ID}/market/{PLAYER_ID}/offer",
    ]:
        for body in [{"price": 1}, {"prc": 1}]:
            response = requests.post(candidate, json=body, headers=headers(), timeout=15)
            print(f"POST {candidate}\n  body {body} -> {response.status_code} {response.text[:300]}\n")
```

Set `PLAYER_ID` to the cheapest listing from step 2 and run it. Record for each combination the status and the body.

A 404 on both paths means the route is different — stop and report rather than proceeding on a guess.

- [ ] **Step 4: Probe the real round trip on the cheapest listing**

Add to the probe script, using only the path and body key that step 3 confirmed:

```python
def probe_round_trip(own_user_id, price):
    """Step 3: place a real bid at the market value, then remove it again."""
    url = f"https://api.kickbase.com/v4/leagues/{LEAGUE_ID}/market/{PLAYER_ID}/offers"

    placed = requests.post(url, json={"price": price}, headers=headers(), timeout=15)
    print(f"POST -> {placed.status_code} {placed.text[:500]}\n")

    ### Read the offer back: this is where an offer id would appear
    item = next(i for i in raw_market()["it"] if str(i["i"]) == str(PLAYER_ID))
    print(f"after POST, ofs: {json.dumps(item.get('ofs'), indent=4)}")
    print(f"after POST, top level uoid={item.get('uoid')} uop={item.get('uop')}\n")

    ### Try the delete forms in order of likelihood, stopping at the first success
    offer_id = (item.get("ofs") or [{}])[0].get("i")
    candidates = [f"{url}/{offer_id}"] if offer_id else []
    candidates.append(url)

    for candidate in candidates:
        response = requests.delete(candidate, headers=headers(), timeout=15)
        print(f"DELETE {candidate} -> {response.status_code} {response.text[:300]}")
        if response.status_code < 400:
            break

    item = next(i for i in raw_market()["it"] if str(i["i"]) == str(PLAYER_ID))
    print(f"\nafter DELETE, ofs={item.get('ofs')} uop={item.get('uop')} "
          f"-> {'withdrawn' if not (item.get('ofs') or item.get('uop')) else 'STILL PLACED'}")
```

Run it with `price` set to the cheapest listing's market value.

**If the final line says `STILL PLACED`, stop and tell the user immediately** — a real bid is standing in their league and must be withdrawn in the app. Do not continue to Task 2 with an open bid.

- [ ] **Step 5: Write the findings into the spec**

Replace the *Open questions, to be settled against the live API before implementing* section of `docs/superpowers/specs/2026-08-13-market-bid-field-design.md` with an *Evidence from the live API* section in the style of `2026-08-12-merged-market-table-design.md`: the league and date probed, the confirmed POST path and body key, the success payload, whether an offer id exists and where, the confirmed DELETE form, and the status plus body of the rejected 1 € bid. Quote real JSON.

Then correct the `place_offer`/`remove_offer` signatures in the spec's *Backend: the write calls* section to match what was found — in particular, drop the `offer_id` parameter if removal needs none.

- [ ] **Step 6: Commit the evidence**

```bash
cd /Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/.claude/worktrees/market-bid-field
git status
git add docs/superpowers/specs/2026-08-13-market-bid-field-design.md
git commit -m "docs: record the probed Kickbase offer endpoints"
```

The probe script stays in the scratchpad and is not committed.

---

### Task 2: `BEP_GROWTH_DAYS` and `BEP_TARGET_DAYS`

**Files:**
- Create: `tests/test_bep_config.py`
- Modify: `backend/miscellaneous.py` (add `get_bep_days()` near `get_start_datetime()`, around line 311)
- Modify: `main.py:92-98` (the START_DATE validation block, extended)
- Modify: `entrypoint.py:70-88` (boot checks, after the START_DATE check)
- Modify: `.env.example` (the "Optional" section)

**Interfaces:**
- Consumes: nothing.
- Produces: `miscellaneous.get_bep_days() -> tuple[int, int]`, returning `(growth_days, target_days)`. Raises `exceptions.KickbaseException` on an invalid value.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_bep_config.py`:

```python
"""### Tests for the break-even horizons BEP_GROWTH_DAYS and BEP_TARGET_DAYS.

Dependency free on purpose: the project has no test framework, so this runs with the
project venv directly and needs no extra packages.

    ./venv/bin/python tests/test_bep_config.py
"""

import sys

from os import environ, path

### Make the repository root importable regardless of where this is run from
sys.path.insert(0, path.dirname(path.dirname(path.abspath(__file__))))

from backend import exceptions, miscellaneous

### ===============================================================================

PASSED = []


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


def set_days(growth, target):
    """Set or clear both horizon variables."""
    for name, value in (("BEP_GROWTH_DAYS", growth), ("BEP_TARGET_DAYS", target)):
        if value is None:
            environ.pop(name, None)
        else:
            environ[name] = value


def expect_rejected(growth, target, expected_in_message):
    """Assert that get_bep_days() rejects the pair and names the offending variable."""
    set_days(growth, target)
    try:
        miscellaneous.get_bep_days()
    except exceptions.KickbaseException as e:
        assert expected_in_message in str(e), \
            f"error should name {expected_in_message}, got: {e}"
    else:
        raise AssertionError(f"expected a KickbaseException for {growth!r}/{target!r}")


### ===============================================================================
### get_bep_days()
### ===============================================================================


def test_defaults_to_three_and_three():
    """The defaults are what the frontend hardcoded before, so nothing moves."""
    set_days(None, None)
    assert miscellaneous.get_bep_days() == (3, 3), \
        f"expected (3, 3), got {miscellaneous.get_bep_days()}"


def test_reads_both_values():
    set_days("7", "14")
    assert miscellaneous.get_bep_days() == (7, 14), \
        f"expected (7, 14), got {miscellaneous.get_bep_days()}"


def test_values_are_independent():
    """The two horizons answer different questions and must not be coupled."""
    set_days("7", "3")
    assert miscellaneous.get_bep_days() == (7, 3)
    set_days("3", "7")
    assert miscellaneous.get_bep_days() == (3, 7)


def test_rejects_non_integers():
    expect_rejected("drei", "3", "BEP_GROWTH_DAYS")
    expect_rejected("3", "3.5", "BEP_TARGET_DAYS")


def test_rejects_zero_and_negative():
    ### A zero window would divide by zero; a negative one is meaningless
    expect_rejected("0", "3", "BEP_GROWTH_DAYS")
    expect_rejected("-1", "3", "BEP_GROWTH_DAYS")
    expect_rejected("3", "0", "BEP_TARGET_DAYS")
    expect_rejected("3", "-5", "BEP_TARGET_DAYS")


def test_rejects_growth_window_beyond_the_history():
    """The history holds 365 entries, so a 365-day window can never be filled."""
    expect_rejected("365", "3", "BEP_GROWTH_DAYS")
    set_days("364", "3")
    assert miscellaneous.get_bep_days() == (364, 3), "364 is the largest usable window"


def test_rejects_an_empty_value():
    expect_rejected("", "3", "BEP_GROWTH_DAYS")


### ===============================================================================

if __name__ == "__main__":
    print("get_bep_days()")
    check("defaults to three and three", test_defaults_to_three_and_three)
    check("reads both values", test_reads_both_values)
    check("keeps the two values independent", test_values_are_independent)
    check("rejects non-integers", test_rejects_non_integers)
    check("rejects zero and negative values", test_rejects_zero_and_negative)
    check("rejects a growth window beyond the history", test_rejects_growth_window_beyond_the_history)
    check("rejects an empty value", test_rejects_an_empty_value)

    total, passed = len(PASSED), sum(PASSED)
    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/.claude/worktrees/market-bid-field
./venv/bin/python tests/test_bep_config.py
```

Expected: every test ERRORs with `AttributeError: module 'backend.miscellaneous' has no attribute 'get_bep_days'`.

Note the worktree has no `venv/` of its own — use the main checkout's interpreter and run from the worktree root:
`/Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/venv/bin/python tests/test_bep_config.py`

- [ ] **Step 3: Add the constants and `get_bep_days()`**

In `backend/miscellaneous.py`, next to the other module constants (after `LOGIN_BONUS_CAP`, around line 41):

```python
### The break-even horizons, and their defaults. Both defaults reproduce the numbers the
### frontend produced when it averaged today, yesterday and vorgestern itself: the daily
### deltas telescope, so their mean over three days is exactly the three-day average.
DEFAULT_BEP_GROWTH_DAYS = 3
DEFAULT_BEP_TARGET_DAYS = 3

### The market value history covers 365 days, so a window of 365 can never be filled -
### it needs days + 1 entries to measure a difference across.
MAX_BEP_GROWTH_DAYS = 364
```

Then, after `get_start_datetime()` (which ends around line 349):

```python
def get_bep_days() -> tuple:
    """### Read the two break-even horizons from the environment.

    They answer different questions and are therefore separate variables:

      - BEP_GROWTH_DAYS is how far back the daily market value growth is averaged.
      - BEP_TARGET_DAYS is how far ahead a suggested bid has to break even.

    A 14 day payback judged on three days of momentum is a different statement from one
    judged on fourteen, and both are legitimate - so neither is derived from the other.

    Raises:
        exceptions.KickbaseException: If either value is not a positive integer, or if
            BEP_GROWTH_DAYS exceeds what the market value history can cover.

    Returns:
        tuple: (growth_days, target_days), both int.
    """
    def positive_int(name: str, default: int) -> int:
        raw = getenv(name)

        if raw is None or raw == "":
            return default

        try:
            value = int(raw)
        except ValueError:
            raise exceptions.KickbaseException(
                f"{name} '{raw}' is not a whole number of days. Use e.g. {name}=7."
            )

        if value < 1:
            raise exceptions.KickbaseException(
                f"{name} is {value}, but a horizon has to be at least one day."
            )

        return value

    growth_days = positive_int("BEP_GROWTH_DAYS", DEFAULT_BEP_GROWTH_DAYS)
    target_days = positive_int("BEP_TARGET_DAYS", DEFAULT_BEP_TARGET_DAYS)

    ### A window wider than the history is not a smaller answer, it is no answer: every
    ### player would show a dash in both break-even columns, with nothing saying why.
    if growth_days > MAX_BEP_GROWTH_DAYS:
        raise exceptions.KickbaseException(
            f"BEP_GROWTH_DAYS is {growth_days}, but the market value history covers 365 "
            f"days, so at most {MAX_BEP_GROWTH_DAYS} can be averaged over."
        )

    return growth_days, target_days
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd /Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/.claude/worktrees/market-bid-field
/Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/venv/bin/python tests/test_bep_config.py
```

Expected: `7/7 passed`.

- [ ] **Step 5: Validate at startup, in both entry points**

`main.py` validates `START_DATE` early so a bad value fails immediately rather than minutes later. The horizons get the same treatment. Replace the block at `main.py:92-98`:

```python
    ### Validate START_DATE before doing any work.
    ### entrypoint.py checks this for Docker runs, but running main.py directly skips
    ### that check and would only fail minutes later, in turnovers().
    try:
        miscellaneous.get_start_datetime()
        miscellaneous.get_bep_days()
    except exceptions.KickbaseException as e:
        logging.error(f"{e} Exiting...")
        exit(1)
```

In `entrypoint.py`, after the START_DATE check (which ends around line 76) and before the START_MONEY check:

```python
### Check the break-even horizons. Same parser as main.py, so both agree on what a valid
### value is.
try:
    bep_growth_days, bep_target_days = miscellaneous.get_bep_days()
    print(f"  ✅ Break-even horizons: {bep_growth_days} day growth average, "
          f"{bep_target_days} day payback.")
except exceptions.KickbaseException as e:
    print(f"  ❌ {e} Exiting...")
    exit()
```

- [ ] **Step 6: Document both variables**

In `.env.example`, in the `### --- Optional ---` section after the `START_MONEY` block:

```
### The two break-even horizons behind the transfer market table.
### BEP_GROWTH_DAYS is the window the daily market value growth is averaged over,
### BEP_TARGET_DAYS the horizon a suggested bid has to break even within. Both
### default to 3, which reproduces the numbers the table showed before they existed.
### Changing them changes what the "Dein Gebot" column recommends and what
### "Tage bis BEP" counts. BEP_GROWTH_DAYS is capped at 364 by the history length.
BEP_GROWTH_DAYS=3
BEP_TARGET_DAYS=3
```

- [ ] **Step 7: Verify both entry points still start**

```bash
cd /Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/.claude/worktrees/market-bid-field
set -a; source /Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/.env; set +a
BEP_GROWTH_DAYS=999 /Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/venv/bin/python -c "
import sys; sys.path.insert(0, '.')
from backend import exceptions, miscellaneous
try:
    miscellaneous.get_bep_days()
except exceptions.KickbaseException as e:
    print('rejected as expected:', e)
"
```

Expected: a message naming `BEP_GROWTH_DAYS` and the 364 cap.

- [ ] **Step 8: Commit**

```bash
cd /Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/.claude/worktrees/market-bid-field
git status
git add tests/test_bep_config.py backend/miscellaneous.py main.py entrypoint.py .env.example
git commit -m "feat: make the break-even horizons configurable"
```

---

### Task 3: `average_daily_growth()`

**Files:**
- Modify: `tests/test_bep_config.py` (add a second section)
- Modify: `backend/miscellaneous.py` (add after `market_value_deltas()`, which ends around line 308)

**Interfaces:**
- Consumes: `miscellaneous.get_bep_days()` from Task 2 (only in the test, to confirm the default window).
- Produces: `miscellaneous.average_daily_growth(market_value_history: list, days: int) -> float | None` — the mean daily change over the last `days` days, or `None` when the history is shorter than `days + 1` entries.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_bep_config.py`, before the `if __name__ == "__main__":` block:

```python
### ===============================================================================
### average_daily_growth()
### ===============================================================================


def history(*values):
    """Build a market value history, oldest first, as the API sends it."""
    return [{"mv": value} for value in values]


def test_averages_the_daily_change():
    ### Four entries, three days of change: +100, +200, +300 -> mean 200
    result = miscellaneous.average_daily_growth(history(1000, 1100, 1300, 1600), 3)
    assert result == 200, f"expected 200, got {result}"


def test_ignores_history_older_than_the_window():
    """Only the last `days` days count, whatever happened before them."""
    result = miscellaneous.average_daily_growth(history(1, 999999, 1000, 1100, 1300, 1600), 3)
    assert result == 200, f"expected 200, got {result}"


def test_reports_a_falling_market_value_as_negative():
    result = miscellaneous.average_daily_growth(history(1600, 1500, 1400, 1300), 3)
    assert result == -100, f"expected -100, got {result}"


def test_reports_a_flat_market_value_as_zero():
    result = miscellaneous.average_daily_growth(history(1000, 1000, 1000, 1000), 3)
    assert result == 0, f"expected 0, got {result}"


def test_a_too_short_history_has_no_answer():
    """Not a zero: a newly added player has no pace, rather than a pace of nothing."""
    ### Three entries cover two days of change, so a three day window cannot be filled
    assert miscellaneous.average_daily_growth(history(1000, 1100, 1300), 3) is None
    assert miscellaneous.average_daily_growth(history(1000), 3) is None
    assert miscellaneous.average_daily_growth([], 3) is None
    assert miscellaneous.average_daily_growth(None, 3) is None


def test_exactly_enough_history_is_enough():
    ### days + 1 entries is the boundary: four entries for a three day window
    assert miscellaneous.average_daily_growth(history(1000, 1100, 1300, 1600), 3) == 200


def test_honours_a_wider_window():
    ### Seven days from 1000 to 1700 is 100 a day
    values = [1000, 1100, 1200, 1300, 1400, 1500, 1600, 1700]
    assert miscellaneous.average_daily_growth(history(*values), 7) == 100


def test_matches_the_three_day_mean_the_frontend_used_to_compute():
    """The refactor is behaviour-neutral at the default, and this is why.

    The daily deltas telescope: (mv[-1] - mv[-2]) + (mv[-2] - mv[-3]) + (mv[-3] - mv[-4])
    collapses to mv[-1] - mv[-4]. So the mean of today, yesterday and twoDays is exactly
    average_daily_growth(history, 3), and the "Tage bis BEP" column cannot move.
    """
    ### An uneven, partly falling history, so the identity is not proven on a straight line
    values = [900000, 1000000, 980000, 1030000, 1120000, 1115000, 1200000]
    deltas = miscellaneous.market_value_deltas(history(*values))
    old_way = (deltas["today"] + deltas["yesterday"] + deltas["twoDays"]) / 3
    new_way = miscellaneous.average_daily_growth(history(*values), 3)
    assert new_way == old_way, f"expected {old_way} (the old three day mean), got {new_way}"


def test_the_identity_holds_when_the_default_window_is_in_use():
    """Ties the identity to the actual default rather than to a literal 3."""
    set_days(None, None)
    growth_days, _ = miscellaneous.get_bep_days()
    values = [900000, 1000000, 980000, 1030000, 1120000]
    deltas = miscellaneous.market_value_deltas(history(*values))
    old_way = (deltas["today"] + deltas["yesterday"] + deltas["twoDays"]) / 3
    assert miscellaneous.average_daily_growth(history(*values), growth_days) == old_way
```

And extend the `__main__` block, after the `get_bep_days()` checks:

```python
    print("\naverage_daily_growth()")
    check("averages the daily change", test_averages_the_daily_change)
    check("ignores history older than the window", test_ignores_history_older_than_the_window)
    check("reports a falling market value as negative", test_reports_a_falling_market_value_as_negative)
    check("reports a flat market value as zero", test_reports_a_flat_market_value_as_zero)
    check("has no answer for a too short history", test_a_too_short_history_has_no_answer)
    check("accepts exactly enough history", test_exactly_enough_history_is_enough)
    check("honours a wider window", test_honours_a_wider_window)
    check("matches the three day mean the frontend used to compute",
          test_matches_the_three_day_mean_the_frontend_used_to_compute)
    check("holds the identity at the default window",
          test_the_identity_holds_when_the_default_window_is_in_use)
```

- [ ] **Step 2: Run the tests to verify the new ones fail**

```bash
cd /Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/.claude/worktrees/market-bid-field
/Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/venv/bin/python tests/test_bep_config.py
```

Expected: the seven `get_bep_days()` checks pass; the nine new ones ERROR with `AttributeError: ... has no attribute 'average_daily_growth'`.

- [ ] **Step 3: Implement `average_daily_growth()`**

In `backend/miscellaneous.py`, after `market_value_deltas()`:

```python
def average_daily_growth(market_value_history: list, days: int):
    """### The mean daily market value change over the last `days` days.

    Measured as one difference across the window rather than as a mean of daily deltas,
    which is the same number - the deltas telescope - and needs one subtraction instead
    of `days` of them.

    A history too short for the window has no answer rather than an answer of zero. A
    player added to the competition last week has no 30 day pace, and calling it zero
    would rank them alongside a genuinely stagnant one.

    Args:
        market_value_history (list): A player_marketvalue response, oldest first, each
            entry with "mv".
        days (int): The window to average over. Needs days + 1 entries to measure across.

    Returns:
        float: The mean daily change, negative for a falling market value. None if the
            history does not cover the window.
    """
    history = market_value_history or []

    if len(history) < days + 1:
        return None

    return (history[-1]["mv"] - history[-1 - days]["mv"]) / days
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd /Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/.claude/worktrees/market-bid-field
/Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/venv/bin/python tests/test_bep_config.py
```

Expected: `16/16 passed`.

- [ ] **Step 5: Commit**

```bash
cd /Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/.claude/worktrees/market-bid-field
git status
git add tests/test_bep_config.py backend/miscellaneous.py
git commit -m "feat: average the daily market value growth over a chosen window"
```

---

### Task 4: `market.json` gains `playerId`, `isOwnListing` and `avgDailyGrowth`; `config.json` is written

**Files:**
- Modify: `tests/test_market_table.py` (extend; it already exercises `market()` end to end)
- Modify: `main.py:212-286` (`market()`)
- Modify: `main.py` (`main()`, the validation block from Task 2 — write `config.json` there)

**Interfaces:**
- Consumes: `miscellaneous.get_bep_days()`, `miscellaneous.average_daily_growth(history, days)`.
- Produces:
  - `market.json` rows gain `"playerId": str`, `"isOwnListing": bool`, `"avgDailyGrowth": float | None`.
  - `frontend/src/data/config.json`: `{"bepGrowthDays": int, "bepTargetDays": int}`.

- [ ] **Step 1: Read the existing test to match its harness**

```bash
cd /Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/.claude/worktrees/market-bid-field
sed -n 100,290p tests/test_market_table.py
```

It builds real market items, monkeypatches `leagues.get_market`, `leagues.player_statistics` and `leagues.player_marketvalue`, redirects `DATA_DIR`, calls `main.market()` and reads the written `market.json`. Reuse that harness rather than writing a second one — and note the market fixtures already include an own listing (Ginter, `"u": {"i": OWN_USER_ID}}`) and a foreign bid (`OTHER_USER_ID`), which are exactly the two cases below.

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_market_table.py`, before its `if __name__ == "__main__":` block. Follow the existing file's own naming for the harness helper that runs `market()` — read it in step 1 and call it the same way.

```python
### ===============================================================================
### The fields the bid field needs
### ===============================================================================


def test_every_row_carries_the_player_id():
    """Without it a row cannot be addressed, so no bid can name a player."""
    rows = run_market()
    for row in rows:
        assert row.get("playerId"), f"row without a playerId: {row}"
    ### The ids are the "i" values from the market items
    assert {row["playerId"] for row in rows} >= {"8289", "3754", "49"}, \
        f"expected the fixture player ids, got {[r['playerId'] for r in rows]}"


def test_own_listing_is_flagged():
    """You cannot bid on a player you listed yourself, so the cell has to know."""
    rows = {row["playerId"]: row for row in run_market()}
    ### Ginter is listed by OWN_USER_ID in the fixtures
    assert rows["49"]["isOwnListing"] is True, "expected Ginter flagged as an own listing"


def test_foreign_and_kickbase_listings_are_not_flagged():
    rows = {row["playerId"]: row for row in run_market()}
    assert rows["8289"]["isOwnListing"] is False, "expected a foreign listing unflagged"
    for row in rows.values():
        if row["isFreeAgent"]:
            assert row["isOwnListing"] is False, \
                f"a Kickbase listing is nobody's own listing: {row}"


def test_rows_carry_the_averaged_growth():
    rows = {row["playerId"]: row for row in run_market()}
    for player_id, row in rows.items():
        growth = row["avgDailyGrowth"]
        assert growth is None or isinstance(growth, (int, float)), \
            f"expected a number or None for {player_id}, got {growth!r}"


def test_the_growth_matches_the_three_day_deltas_at_the_default():
    """The same telescoping identity, now through market() itself."""
    set_bep_days(None, None)
    rows = run_market()
    for row in rows:
        if row["avgDailyGrowth"] is None:
            continue
        deltas = (row["today"], row["yesterday"], row["twoDays"])
        if any(delta is None for delta in deltas):
            continue
        expected = sum(deltas) / 3
        assert row["avgDailyGrowth"] == expected, \
            f"{row['lastName']}: expected {expected}, got {row['avgDailyGrowth']}"


def test_a_wider_window_changes_the_growth():
    """Proves the env variable actually reaches market(), not just get_bep_days()."""
    set_bep_days("3", "3")
    narrow = {row["playerId"]: row["avgDailyGrowth"] for row in run_market()}
    set_bep_days("7", "3")
    wide = {row["playerId"]: row["avgDailyGrowth"] for row in run_market()}
    set_bep_days(None, None)

    differing = [pid for pid in narrow
                 if narrow[pid] is not None and wide.get(pid) is not None
                 and narrow[pid] != wide[pid]]
    assert differing, \
        f"expected at least one player's growth to change with the window, got {narrow} vs {wide}"


def test_config_json_is_written_with_both_horizons():
    set_bep_days("7", "14")
    config = run_main_config_write()
    set_bep_days(None, None)
    assert config == {"bepGrowthDays": 7, "bepTargetDays": 14}, \
        f"expected both horizons in config.json, got {config}"
```

Add the two helpers this needs near the top of the file's helper section:

```python
def set_bep_days(growth, target):
    """Set or clear the two horizon variables."""
    from os import environ
    for name, value in (("BEP_GROWTH_DAYS", growth), ("BEP_TARGET_DAYS", target)):
        if value is None:
            environ.pop(name, None)
        else:
            environ[name] = value


def run_main_config_write():
    """Call the config writer with DATA_DIR redirected, and return what it wrote."""
    import json
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        data_dir = path.join(tmp, "data")
        ts_dir = path.join(data_dir, "timestamps")
        from os import makedirs
        makedirs(ts_dir, exist_ok=True)

        original = (miscellaneous.DATA_DIR, miscellaneous.TIMESTAMP_DIR)
        miscellaneous.DATA_DIR = data_dir
        miscellaneous.TIMESTAMP_DIR = ts_dir
        try:
            main.write_bep_config()
            with open(path.join(data_dir, "config.json")) as f:
                return json.load(f)
        finally:
            miscellaneous.DATA_DIR, miscellaneous.TIMESTAMP_DIR = original
```

The fixture histories in the file may be too short for a seven-day window. If
`test_a_wider_window_changes_the_growth` cannot find a differing player, lengthen one
fixture history to nine entries with an uneven shape — do not weaken the assertion.

- [ ] **Step 3: Run the tests to verify the new ones fail**

```bash
cd /Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/.claude/worktrees/market-bid-field
/Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/venv/bin/python tests/test_market_table.py
```

Expected: the pre-existing checks pass; the new ones FAIL on the missing keys and ERROR on `main.write_bep_config` not existing.

- [ ] **Step 4: Add the three row fields**

In `main.py`, in `market()`. Read the horizon once at the top of the function, after the log line at 229:

```python
    ### The growth window is configuration, so it is read once per run rather than per player
    growth_days, _ = miscellaneous.get_bep_days()
```

Then replace the delta block at line 245 so the history is kept:

```python
        ### Kept in a variable: the deltas and the averaged growth read the same history,
        ### and the fetch is cached per run anyway
        market_value_history = leagues.player_marketvalue(user_token, player.id)
        deltas = miscellaneous.market_value_deltas(market_value_history)
        avg_daily_growth = miscellaneous.average_daily_growth(market_value_history, growth_days)
```

And extend the `player_info` dict (line 258) with the three fields:

```python
        player_info = {
            ### Addresses the row: the bid endpoints name a player by this id
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
            ### Nobody bids on their own listing, so the frontend locks the cell
            "isOwnListing": player.userId is not None and str(player.userId) == str(own_user_id),
            "seller": player.username or "Kickbase",
            "isFreeAgent": not player.username,
            "expiration": expiration,
            ### The pace both break-even columns are computed from
            "avgDailyGrowth": avg_daily_growth,
            **deltas,
        }
```

- [ ] **Step 5: Write `config.json`**

In `main.py`, add next to the other top-level functions (after `get_gift()`, before `market()`):

```python
def write_bep_config() -> None:
    """### Hand the break-even horizons to the frontend as data.

    The frontend needs both numbers - one to compute the suggested bid, one to say in the
    help text what it is measuring - but it must not read them from its own environment.
    The growth average needs the full market value history, which only this process has,
    so a REACT_APP_ twin would be a second home for the same number, free to drift.

    Two constants also do not belong in all 120 market rows, which is why this is a file
    of its own rather than more fields on each row.
    """
    growth_days, target_days = miscellaneous.get_bep_days()

    miscellaneous.write_json_to_file(
        {"bepGrowthDays": growth_days, "bepTargetDays": target_days}, "config.json")

    logging.info(f"Break-even horizons written: {growth_days} day growth average, "
                 f"{target_days} day payback.")
```

Call it in `main()`, right after the validation block from Task 2 and before `login()`:

```python
    ### Written before any API call: it depends only on the environment, and the frontend
    ### fails to build without it
    write_bep_config()
```

- [ ] **Step 6: Run the tests to verify they pass**

```bash
cd /Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/.claude/worktrees/market-bid-field
/Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/venv/bin/python tests/test_market_table.py
/Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/venv/bin/python tests/test_bep_config.py
```

Expected: both fully green. Run the second one too — `market()` now calls into it.

- [ ] **Step 7: Generate the real files against the live API**

The frontend tasks need a real `config.json` and a `market.json` carrying the new fields.

```bash
cd /Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/.claude/worktrees/market-bid-field
set -a; source /Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/.env; set +a
/Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/venv/bin/python main.py
```

Then confirm the shape:

```bash
/Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/venv/bin/python -c "
import json
print(json.load(open('frontend/src/data/config.json')))
rows = json.load(open('frontend/src/data/market.json'))
print(f'{len(rows)} rows')
print({k: rows[0][k] for k in ('playerId', 'isOwnListing', 'avgDailyGrowth', 'marketValue', 'price')})
print('own listings:', sum(r['isOwnListing'] for r in rows))
print('rows without growth:', sum(r['avgDailyGrowth'] is None for r in rows))
"
```

Expected: a config with both horizons, every row carrying a `playerId`, and `avgDailyGrowth` a number on most rows.

- [ ] **Step 8: Commit**

Both generated JSON files are gitignored, so only code is staged.

```bash
cd /Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/.claude/worktrees/market-bid-field
git status
git add tests/test_market_table.py main.py
git commit -m "feat: carry the player id, own listings and the averaged growth to the frontend"
```

---

### Task 5: Refactor the break-even formulas onto `avgDailyGrowth`

Behaviour-neutral by design: at the defaults the "Tage bis BEP" column must show exactly the same numbers as before this task.

**Files:**
- Modify: `frontend/src/components/marketFormulas.js`
- Modify: `frontend/src/components/marketFormulas.test.js`
- Modify: `frontend/src/components/MarketTable.js:14` (import), `:219-253` (row mapping)

**Interfaces:**
- Consumes: `market.json` rows carrying `avgDailyGrowth` (Task 4), `config.json` carrying `bepTargetDays` (Task 4).
- Produces, from `marketFormulas.js`:
  - `relativeChange(delta, marketValue)` — unchanged.
  - `daysToBreakEven({ marketValue, price, avgDailyGrowth })` — number, `0`, or `null`.
  - `breakEvenBid(row, targetDays)` — whole euros as a number, or `null`. `row` needs `marketValue` and `avgDailyGrowth`.
  - Row fields added in `MarketTable`: `avgDailyGrowth`, `suggestedBid`, `isOwnListing`, `playerId`.

- [ ] **Step 1: Rewrite the tests**

Replace the `daysToBreakEven` block in `frontend/src/components/marketFormulas.test.js` (from line 20 to the end of that `describe`) and add a `breakEvenBid` block. Keep the `relativeChange` block untouched.

```js
import { relativeChange, daysToBreakEven, breakEvenBid } from "./marketFormulas"

// A row as market.json holds it, reduced to what the two formulas read
const row = (marketValue, avgDailyGrowth, price) => ({ marketValue, avgDailyGrowth, price })

describe("daysToBreakEven", () => {
    it("counts the days the market value needs to reach the price", () => {
        // 100.000 a day closing a 300.000 gap
        expect(daysToBreakEven(row(1000000, 100000, 1300000))).toBeCloseTo(3)
        expect(daysToBreakEven(row(1000000, 60000, 1300000))).toBeCloseTo(5)
    })

    it("is zero when the listing is already worth what it costs", () => {
        // Free agents are listed at exactly the market value
        expect(daysToBreakEven(row(1000000, 100000, 1000000))).toBe(0)
        expect(daysToBreakEven(row(1000000, 100000, 900000))).toBe(0)
    })

    it("has no answer when the market value is flat or falling", () => {
        expect(daysToBreakEven(row(1000000, -10000, 1300000))).toBeNull()
        expect(daysToBreakEven(row(1000000, 0, 1300000))).toBeNull()
    })

    it("has no answer without a growth figure", () => {
        // A history too short for the window, which is not the same as a growth of zero
        expect(daysToBreakEven(row(1000000, null, 1300000))).toBeNull()
        expect(daysToBreakEven(row(1000000, undefined, 1300000))).toBeNull()
    })

    it("has no answer without a market value or a price", () => {
        expect(daysToBreakEven(row(null, 100000, 1300000))).toBeNull()
        expect(daysToBreakEven(row(1000000, 100000, null))).toBeNull()
    })
})

describe("breakEvenBid", () => {
    it("projects the market value forward to the target horizon", () => {
        expect(breakEvenBid(row(1000000, 100000), 3)).toBe(1300000)
        expect(breakEvenBid(row(1000000, 100000), 7)).toBe(1700000)
        expect(breakEvenBid(row(1000000, 100000), 14)).toBe(2400000)
    })

    it("returns whole euros", () => {
        // Kickbase takes integers, and an averaged growth rarely is one
        expect(breakEvenBid(row(1000000, 33333.333), 3)).toBe(1100000)
        expect(Number.isInteger(breakEvenBid(row(1234567, 4321.7), 3))).toBe(true)
    })

    it("has no answer when the market value is flat or falling", () => {
        // Refusing to recommend is the point: such a player never pays for itself
        expect(breakEvenBid(row(1000000, 0), 3)).toBeNull()
        expect(breakEvenBid(row(1000000, -50000), 3)).toBeNull()
    })

    it("has no answer without a growth figure or a market value", () => {
        expect(breakEvenBid(row(1000000, null), 3)).toBeNull()
        expect(breakEvenBid(row(1000000, undefined), 3)).toBeNull()
        expect(breakEvenBid(row(null, 100000), 3)).toBeNull()
        expect(breakEvenBid(row(0, 100000), 3)).toBeNull()
    })

    it("is the inverse of daysToBreakEven", () => {
        // The property that ties the two columns together: bidding the suggestion means
        // breaking even exactly at the horizon
        for (const targetDays of [1, 3, 4, 7, 14, 30]) {
            for (const growth of [1, 12345, 100000, 987654.321]) {
                const base = row(1000000, growth)
                const bid = breakEvenBid(base, targetDays)
                expect(daysToBreakEven({ ...base, price: bid })).toBeCloseTo(targetDays, 4)
            }
        }
    })
})
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/.claude/worktrees/market-bid-field/frontend
CI=true npm test -- --watchAll=false --testPathPattern=marketFormulas
```

Expected: `breakEvenBid is not a function`, and the `daysToBreakEven` cases failing because it still reads `today`/`yesterday`/`twoDays`.

If `npm test` cannot start because `node_modules` is missing in the worktree, run `npm install` there first.

- [ ] **Step 3: Rewrite `marketFormulas.js`**

Replace `daysToBreakEven` (lines 19-46) and keep `relativeChange` as it is:

```js
/**
 * The average daily growth a row can be judged by, or null when it cannot.
 *
 * Shared by both break-even columns so the "no answer" rule exists once. A missing
 * figure means the history is too short for the configured window; a figure at or below
 * zero means the market value never catches up. Neither is a number of days.
 */
function usableGrowth({ avgDailyGrowth }) {
    if (isMissing(avgDailyGrowth) || avgDailyGrowth <= 0)
        return null

    return avgDailyGrowth
}

/**
 * How many days the market value needs to grow into the asking price.
 *
 * Null means the question has no answer rather than a large one. The averaging itself
 * happens in the backend, which is the only place that holds the full history - this
 * used to average the three deltas that happened to be in market.json, and could
 * therefore express no other window.
 */
export function daysToBreakEven({ marketValue, price, avgDailyGrowth }) {
    if (!marketValue || isMissing(price))
        return null

    const markup = price - marketValue

    // Free agents are listed at exactly the market value, and a listing below it is
    // already worth more than it costs
    if (markup <= 0)
        return 0

    const growth = usableGrowth({ avgDailyGrowth })
    if (growth === null)
        return null

    return markup / growth
}

/**
 * The bid that breaks even exactly at the target horizon: what the market value will be
 * worth in targetDays days, at the pace measured over the growth window.
 *
 * The same line as daysToBreakEven, solved for the price instead of for the days. The
 * asking price does not enter it - break even is a statement about market value and
 * growth, and the price is what you compare the result against.
 *
 * Whole euros, because that is what Kickbase accepts. Null when there is nothing to
 * recommend: declining to suggest a bid is an answer, a bid on a falling market value
 * is not.
 */
export function breakEvenBid(row, targetDays) {
    const growth = usableGrowth(row)

    if (!row.marketValue || growth === null)
        return null

    return Math.round(row.marketValue + targetDays * growth)
}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd /Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/.claude/worktrees/market-bid-field/frontend
CI=true npm test -- --watchAll=false --testPathPattern=marketFormulas
```

Expected: all `relativeChange`, `daysToBreakEven` and `breakEvenBid` suites pass.

- [ ] **Step 5: Feed the new fields through `MarketTable`**

In `frontend/src/components/MarketTable.js`, change the import on line 14:

```js
import { relativeChange, daysToBreakEven, breakEvenBid } from "./marketFormulas"
import config from "../data/config.json"
```

In the row mapping (line 219 onwards), add four fields. `daysToBep` needs no change to its call — `row` now carries `avgDailyGrowth` instead of being averaged from the deltas inside the formula:

```js
            // Addresses the row for the bid endpoints
            playerId: row.playerId,
            // Nobody bids on their own listing
            isOwnListing: row.isOwnListing,
            // The pace both break-even figures come from, averaged in the backend over
            // BEP_GROWTH_DAYS
            avgDailyGrowth: row.avgDailyGrowth,
            // Days for the market value to grow into the asking price at that pace
            daysToBep: daysToBreakEven(row),
            // The bid that would break even after BEP_TARGET_DAYS days. Kept apart from
            // ownBid so the column still sorts by the real bid.
            suggestedBid: breakEvenBid(row, config.bepTargetDays),
```

- [ ] **Step 6: Verify the column did not move**

This is the behaviour-neutrality check, and it must be run rather than assumed. Before the change the numbers came from the three deltas; after it from `avgDailyGrowth`.

```bash
cd /Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/.claude/worktrees/market-bid-field
/Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/venv/bin/python -c "
import json
rows = json.load(open('frontend/src/data/market.json'))
mismatch = 0
for r in rows:
    deltas = (r['today'], r['yesterday'], r['twoDays'])
    if any(d is None for d in deltas) or r['avgDailyGrowth'] is None:
        continue
    old = sum(deltas) / 3
    if old != r['avgDailyGrowth']:
        mismatch += 1
        print('differs:', r['lastName'], old, r['avgDailyGrowth'])
print(f'{len(rows)} rows, {mismatch} mismatches')
"
```

Expected: `0 mismatches` — with `BEP_GROWTH_DAYS` at its default of 3. This confirms every "Tage bis BEP" value is unchanged.

- [ ] **Step 7: Start the frontend and check the table renders**

```bash
cd /Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/.claude/worktrees/market-bid-field/frontend
npm start
```

Open the Transfermarkt table. "Tage bis BEP" must still show numbers and dashes as before, and no console error about `config.json`. Stop the server afterwards.

- [ ] **Step 8: Commit**

```bash
cd /Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/.claude/worktrees/market-bid-field
git status
git add frontend/src/components/marketFormulas.js frontend/src/components/marketFormulas.test.js frontend/src/components/MarketTable.js
git commit -m "refactor: compute both break-even columns from one growth figure"
```

---

### Task 6: The cell's resting states — grey suggestion, locked own listing, tooltip

Display only. No writing, no editing; that is Task 9.

**Files:**
- Create: `frontend/src/components/BidCell.js`
- Create: `frontend/src/components/BidCell.test.js`
- Modify: `frontend/src/components/MarketTable.js:159-188` (the `ownBid` column)
- Modify: `frontend/src/App.js:159` (the `HelpIcon` text)

**Interfaces:**
- Consumes: `daysToBreakEven`, `breakEvenBid`, `config.bepGrowthDays`, `config.bepTargetDays`; row fields `ownBid`, `suggestedBid`, `marketValue`, `price`, `isOwnListing`.
- Produces: `<BidCell row={row} growthDays={n} targetDays={n} onEdit={fn} />`. `onEdit` is called with no arguments when the cell is clicked; Task 6 passes a no-op, Task 9 wires it up.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/BidCell.test.js`:

```js
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import BidCell from "./BidCell"

// A row as MarketTable builds it, reduced to what the cell reads
const row = (overrides) => ({
    playerId: "49",
    marketValue: 1000000,
    price: 1300000,
    ownBid: null,
    suggestedBid: 1180000,
    isOwnListing: false,
    ...overrides
})

const cell = (overrides, props) => render(
    <BidCell row={row(overrides)} growthDays={3} targetDays={3} onEdit={() => {}} {...props} />
)

describe("BidCell at rest", () => {
    it("shows the suggestion when no bid is placed", () => {
        cell()
        expect(screen.getByText("1.180.000 €")).toBeInTheDocument()
    })

    it("shows a placed bid with its surcharge on the market value", () => {
        cell({ ownBid: 1250000 })
        expect(screen.getByText("1.250.000 €")).toBeInTheDocument()
        // 1.250.000 / 1.000.000 - 1 = +25 %
        expect(screen.getByText("(+25 %)")).toBeInTheDocument()
    })

    it("prefers the placed bid over the suggestion", () => {
        cell({ ownBid: 1250000 })
        expect(screen.queryByText("1.180.000 €")).not.toBeInTheDocument()
    })

    it("shows a dash when there is nothing to suggest", () => {
        // Flat or falling market value, or a history too short for the window
        cell({ suggestedBid: null })
        expect(screen.getByText("–")).toBeInTheDocument()
    })

    it("stays clickable without a suggestion", async () => {
        // Declining to recommend must not mean declining to act
        const onEdit = jest.fn()
        cell({ suggestedBid: null }, { onEdit })
        await userEvent.click(screen.getByText("–"))
        expect(onEdit).toHaveBeenCalled()
    })

    it("opens editing when clicked", async () => {
        const onEdit = jest.fn()
        cell({}, { onEdit })
        await userEvent.click(screen.getByText("1.180.000 €"))
        expect(onEdit).toHaveBeenCalled()
    })

    it("renders nothing clickable for an own listing", async () => {
        const onEdit = jest.fn()
        cell({ isOwnListing: true, suggestedBid: 1180000 }, { onEdit })
        expect(screen.queryByText("1.180.000 €")).not.toBeInTheDocument()
        const locked = screen.getByLabelText("Eigenes Angebot")
        await userEvent.click(locked)
        expect(onEdit).not.toHaveBeenCalled()
    })

    it("explains the suggestion in German, naming both horizons", () => {
        cell()
        const hint = screen.getByText("1.180.000 €").closest("[title]")
        expect(hint.getAttribute("title")).toMatch(/3 Tage/)
        expect(hint.getAttribute("title")).toMatch(/Break-Even/)
    })

    it("warns when the suggestion is below the asking price", () => {
        // Valid, but the seller is unlikely to take it
        cell({ suggestedBid: 1100000, price: 1300000 })
        const hint = screen.getByText("1.100.000 €").closest("[title]")
        expect(hint.getAttribute("title")).toMatch(/unter dem Preis/)
    })

    it("does not warn when the suggestion clears the asking price", () => {
        cell({ suggestedBid: 1400000, price: 1300000 })
        const hint = screen.getByText("1.400.000 €").closest("[title]")
        expect(hint.getAttribute("title")).not.toMatch(/unter dem Preis/)
    })
})
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/.claude/worktrees/market-bid-field/frontend
CI=true npm test -- --watchAll=false --testPathPattern=BidCell
```

Expected: `Cannot find module './BidCell'`.

- [ ] **Step 3: Write `BidCell.js`**

```js
import { Box, Tooltip, Typography } from "@mui/material"
import { currencyFormatter, percentFormatter } from "./SharedConstants"

// What the grey suggestion means, spelled out. Both horizons are named because they are
// configurable: at BEP_GROWTH_DAYS=7 a text about three days would be wrong.
function suggestionTooltip(row, growthDays, targetDays) {
    if (row.suggestedBid === null || row.suggestedBid === undefined)
        return `Kein Vorschlag: der Marktwert steigt über die letzten ${growthDays} Tage `
            + "nicht, oder die Historie ist zu kurz. Klicken, um trotzdem ein Gebot abzugeben."

    let text = `Gebot, das beim durchschnittlichen Zuwachs der letzten ${growthDays} Tage `
        + `in ${targetDays} Tagen Break-Even erreicht. Klicken, um zu bieten.`

    // A bid under the asking price is valid; it just tends not to be accepted
    if (row.price !== null && row.price !== undefined && row.suggestedBid < row.price)
        text += ` Liegt unter dem Preis von ${currencyFormatter.format(row.price)} – `
            + "gültig, aber der Verkäufer nimmt es kaum an."

    return text
}

// The cell in the "Dein Gebot" column at rest: a placed bid, or the greyed-out bid that
// would break even at the target horizon, or a dash when there is nothing to suggest.
function BidCell({ row, growthDays, targetDays, onEdit }) {
    // You cannot bid on a player you listed yourself - there you receive offers
    if (row.isOwnListing)
        return (
            <Tooltip title="Dein eigenes Angebot – hier bieten andere." arrow>
                <Box aria-label="Eigenes Angebot" sx={{ width: "100%", height: "100%" }} />
            </Tooltip>
        )

    const hasBid = row.ownBid !== null && row.ownBid !== undefined

    if (hasBid) {
        // How far the bid sits above (or below) the current market value
        const surcharge = row.marketValue
            ? percentFormatter.format(row.ownBid / row.marketValue - 1)
            : null

        return (
            <Tooltip title="Dein laufendes Gebot. Klicken, um es zu ändern oder zurückzuziehen." arrow>
                <Box onClick={onEdit} sx={{ cursor: "pointer", width: "100%", textAlign: "right" }}>
                    {currencyFormatter.format(Number(row.ownBid))}
                    {surcharge && (
                        <Typography component="span" variant="body2" sx={{ opacity: 0.6, marginLeft: "6px" }}>
                            ({surcharge})
                        </Typography>
                    )}
                </Box>
            </Tooltip>
        )
    }

    const suggestion = row.suggestedBid === null || row.suggestedBid === undefined
        ? "–"
        : currencyFormatter.format(Number(row.suggestedBid))

    return (
        <Tooltip title={suggestionTooltip(row, growthDays, targetDays)} arrow>
            {/* Greyed out to read as a proposal rather than as a fact, but in the same
                tabular figures as a real bid so the column still lines up */}
            <Box onClick={onEdit} sx={{ cursor: "pointer", opacity: 0.6, width: "100%", textAlign: "right" }}>
                {suggestion}
            </Box>
        </Tooltip>
    )
}

export default BidCell
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd /Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/.claude/worktrees/market-bid-field/frontend
CI=true npm test -- --watchAll=false --testPathPattern=BidCell
```

Expected: all eleven pass. MUI's `Tooltip` puts its text on the child's `title` attribute, which is what the tooltip assertions read.

- [ ] **Step 5: Render the cell from the column**

In `frontend/src/components/MarketTable.js`, add the import next to the others:

```js
import BidCell from "./BidCell"
```

Replace the `renderCell` of the `ownBid` column (lines 167-187) with:

```js
            renderCell: (params) => (
                <BidCell
                    row={params.row}
                    growthDays={config.bepGrowthDays}
                    targetDays={config.bepTargetDays}
                    onEdit={() => {}}
                />
            )
```

Leave the column's `field`, `type`, `width`, `headerAlign`, `align`, `cellClassName` and its sort behaviour alone: sorting stays on the real `ownBid`, so a row's position keeps saying whether a bid exists.

- [ ] **Step 6: Update the help text**

In `frontend/src/App.js`, the `HelpIcon` on line 159 describes "Dein Gebot" as display-only and hardcodes "letzten drei Tage". Add the import at the top of the file:

```js
import config from "./data/config.json"
```

and replace the `text` prop with a template string:

```jsx
<HelpIcon text={`Alle Spieler auf dem Transfermarkt. Hellblau hinterlegte Zeilen sind Free Agents, also direkt von Kickbase gelistet; alle anderen sind von Nutzern aus der Liga gelistet. 'Dein Gebot' zeigt dein laufendes Gebot und den Aufschlag auf den aktuellen Marktwert; ist keines abgegeben, steht dort grau das Gebot, das beim durchschnittlichen Zuwachs der letzten ${config.bepGrowthDays} Tage in ${config.bepTargetDays} Tagen Break-Even erreicht. Ein Klick macht das Feld editierbar und gibt das Gebot direkt bei Kickbase ab. Ein Ablaufdatum liefert Kickbase nur für die eigenen Angebote. 'Tage bis BEP' sind die Tage, die der Marktwert beim Zuwachs der letzten ${config.bepGrowthDays} Tage braucht, um den Preis einzuholen; ein Strich heißt, dass der Marktwert gerade nicht steigt. Neben jeder Euro-Spalte steht derselbe Zuwachs relativ zum aktuellen Marktwert.`}/>
```

- [ ] **Step 7: Look at it in the browser**

```bash
cd /Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/.claude/worktrees/market-bid-field/frontend
npm start
```

Check all four resting states in the Transfermarkt table: grey suggestions on rows without a bid, a normal bid where one exists, an empty cell on your own listings, a dash where the market value is flat or falling. Hover each to read the tooltip. Confirm the help text names the configured horizons. Stop the server.

- [ ] **Step 8: Commit**

```bash
cd /Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/.claude/worktrees/market-bid-field
git status
git add frontend/src/components/BidCell.js frontend/src/components/BidCell.test.js frontend/src/components/MarketTable.js frontend/src/App.js
git commit -m "feat: suggest the break-even bid in the market table"
```

---

### Task 7: The Kickbase write calls

**Use the paths, body keys and delete form Task 1 recorded.** The values below are the pre-probe assumption; where the evidence in the spec disagrees, the evidence wins.

**Files:**
- Create: `tests/test_market_bid.py`
- Modify: `backend/exceptions.py`
- Modify: `backend/kickbase/v4/leagues.py` (add after `get_market()`, which ends around line 108)
- Modify: `backend/kickbase/endpoints/leagues.py:217-244` (`own_offer()`, plus a shared helper)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `exceptions.KickbaseWriteException(status: int, message: str)`, subclass of `KickbaseException`, with a `.status` attribute.
  - `leagues.place_offer(token, league_id, player_id, price) -> dict`
  - `leagues.remove_offer(token, league_id, player_id, offer_id=None) -> None`
  - `Market_Players.own_offer_id(own_user_id) -> str | None`
  - `Market_Players.own_offer(own_user_id)` — unchanged behaviour, now sharing `_own_offer_entry()`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_market_bid.py`:

```python
"""### Tests for placing and withdrawing a bid on the transfer market.

Dependency free on purpose: the project has no test framework, so this runs with the
project venv directly and needs no extra packages. The HTTP layer is replaced by a fake
rather than mocked with a library, for the same reason.

Shapes are the ones recorded in
docs/superpowers/specs/2026-08-13-market-bid-field-design.md.

    ./venv/bin/python tests/test_market_bid.py
"""

import json
import sys
import tempfile

from os import path

sys.path.insert(0, path.dirname(path.dirname(path.abspath(__file__))))

from backend import exceptions, miscellaneous
from backend.kickbase.endpoints.leagues import Market_Players
from backend.kickbase.v4 import leagues

### ===============================================================================

OWN_USER_ID = "3854976"
OTHER_USER_ID = "2592773"
LEAGUE_ID = "11412166"
PLAYER_ID = "8289"

PASSED = []


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


class FakeResponse:
    """Enough of a requests.Response for the two write calls."""

    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self._body = body
        self.text = "" if body is None else json.dumps(body)
        self.content = self.text.encode()

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class Recorder:
    """Stands in for requests.post/requests.delete and records the call."""

    def __init__(self, response):
        self.response = response
        self.url = None
        self.json = None
        self.headers = None
        self.timeout = None

    def __call__(self, url, json=None, headers=None, timeout=None):
        self.url, self.json, self.headers, self.timeout = url, json, headers, timeout
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def with_fake(method, response, fn):
    """Run fn with requests.<method> replaced, and return the recorder."""
    recorder = Recorder(response)
    original = getattr(leagues.requests, method)
    setattr(leagues.requests, method, recorder)
    try:
        fn()
    finally:
        setattr(leagues.requests, method, original)
    return recorder


### ===============================================================================
### place_offer()
### ===============================================================================


def test_place_offer_posts_the_price_to_the_player():
    recorder = with_fake("post", FakeResponse(200, {}), lambda:
        leagues.place_offer("tok", LEAGUE_ID, PLAYER_ID, 1180000))

    assert LEAGUE_ID in recorder.url and PLAYER_ID in recorder.url, \
        f"url should name league and player, got {recorder.url}"
    assert recorder.json == {"price": 1180000}, f"unexpected body {recorder.json}"
    assert recorder.headers["Cookie"] == "kkstrauth=tok;", \
        f"expected the auth cookie, got {recorder.headers}"


def test_place_offer_sends_a_timeout():
    """No Kickbase call in this project has one today; one hung socket parks the API."""
    recorder = with_fake("post", FakeResponse(200, {}), lambda:
        leagues.place_offer("tok", LEAGUE_ID, PLAYER_ID, 1180000))
    assert recorder.timeout, f"expected a timeout, got {recorder.timeout!r}"


def test_place_offer_surfaces_the_api_message():
    """A rejected bid has to say why, which the surrounding module's bare except cannot."""
    body = {"message": "Offer price is below the market value"}

    def place():
        leagues.place_offer("tok", LEAGUE_ID, PLAYER_ID, 1)

    try:
        with_fake("post", FakeResponse(400, body), place)
    except exceptions.KickbaseWriteException as e:
        assert e.status == 400, f"expected status 400 on the exception, got {e.status}"
        assert "below the market value" in str(e), f"expected the API message, got: {e}"
    else:
        raise AssertionError("expected a KickbaseWriteException for a 400")


def test_place_offer_survives_an_error_body_without_a_message():
    def place():
        leagues.place_offer("tok", LEAGUE_ID, PLAYER_ID, 1)

    try:
        with_fake("post", FakeResponse(500, None), place)
    except exceptions.KickbaseWriteException as e:
        assert e.status == 500, f"expected status 500, got {e.status}"
        assert "500" in str(e), f"message should name the status, got: {e}"
    else:
        raise AssertionError("expected a KickbaseWriteException for a 500")


def test_place_offer_reports_an_unreachable_api():
    import requests as real_requests

    def place():
        leagues.place_offer("tok", LEAGUE_ID, PLAYER_ID, 1180000)

    try:
        with_fake("post", real_requests.exceptions.ConnectTimeout("timed out"), place)
    except exceptions.KickbaseWriteException as e:
        assert e.status >= 500, f"a transport failure is not the user's fault, got {e.status}"
    else:
        raise AssertionError("expected a KickbaseWriteException for a connection failure")


### ===============================================================================
### remove_offer()
### ===============================================================================


def test_remove_offer_addresses_the_offer():
    recorder = with_fake("delete", FakeResponse(200, {}), lambda:
        leagues.remove_offer("tok", LEAGUE_ID, PLAYER_ID, "999"))

    assert PLAYER_ID in recorder.url, f"url should name the player, got {recorder.url}"
    assert recorder.headers["Cookie"] == "kkstrauth=tok;"


def test_remove_offer_surfaces_the_api_message():
    def remove():
        leagues.remove_offer("tok", LEAGUE_ID, PLAYER_ID, "999")

    try:
        with_fake("delete", FakeResponse(404, {"message": "Offer not found"}), remove)
    except exceptions.KickbaseWriteException as e:
        assert e.status == 404, f"expected status 404, got {e.status}"
        assert "not found" in str(e), f"expected the API message, got: {e}"
    else:
        raise AssertionError("expected a KickbaseWriteException for a 404")


### ===============================================================================
### own_offer_id()
### ===============================================================================


def market_item(**overrides):
    """A market item as get_market() receives it."""
    item = {"i": PLAYER_ID, "fn": "Salim Amani", "n": "Musah", "tid": "2", "pos": 3,
            "st": 0, "mv": 5000000, "prc": 5200000}
    item.update(overrides)
    return item


def test_own_offer_id_reads_the_id_from_the_own_offer():
    player = Market_Players(market_item(
        ofs=[{"i": "77", "u": OWN_USER_ID, "uoid": OWN_USER_ID, "uop": 5222222}]))
    assert player.own_offer_id(OWN_USER_ID) == "77", \
        f"expected 77, got {player.own_offer_id(OWN_USER_ID)}"


def test_own_offer_id_ignores_a_foreign_offer():
    """A foreign bid must never be treated as ours, whatever the API starts exposing."""
    player = Market_Players(market_item(
        ofs=[{"i": "77", "u": OTHER_USER_ID, "uoid": OTHER_USER_ID, "uop": 999999}]))
    assert player.own_offer_id(OWN_USER_ID) is None


def test_own_offer_id_is_none_when_the_offer_carries_no_id():
    """The recorded response has no id in ofs, so this is the normal case, not an edge."""
    player = Market_Players(market_item(
        ofs=[{"u": OWN_USER_ID, "uoid": OWN_USER_ID, "uop": 5222222}]))
    assert player.own_offer_id(OWN_USER_ID) is None


def test_own_offer_id_is_none_without_any_offer():
    assert Market_Players(market_item()).own_offer_id(OWN_USER_ID) is None


def test_own_offer_still_reads_the_price():
    """The refactor to a shared helper must not change what own_offer() returns."""
    from_ofs = Market_Players(market_item(
        ofs=[{"i": "77", "u": OWN_USER_ID, "uoid": OWN_USER_ID, "uop": 5222222}]))
    assert from_ofs.own_offer(OWN_USER_ID) == 5222222

    ### The mirror on the item itself, which some items carry alone
    mirrored = Market_Players(market_item(uoid=OWN_USER_ID, uop=523350))
    assert mirrored.own_offer(OWN_USER_ID) == 523350

    foreign = Market_Players(market_item(
        ofs=[{"u": OTHER_USER_ID, "uoid": OTHER_USER_ID, "uop": 999999}]))
    assert foreign.own_offer(OWN_USER_ID) is None


### ===============================================================================

if __name__ == "__main__":
    print("place_offer()")
    check("posts the price to the player", test_place_offer_posts_the_price_to_the_player)
    check("sends a timeout", test_place_offer_sends_a_timeout)
    check("surfaces the API message", test_place_offer_surfaces_the_api_message)
    check("survives an error body without a message",
          test_place_offer_survives_an_error_body_without_a_message)
    check("reports an unreachable API", test_place_offer_reports_an_unreachable_api)

    print("\nremove_offer()")
    check("addresses the offer", test_remove_offer_addresses_the_offer)
    check("surfaces the API message", test_remove_offer_surfaces_the_api_message)

    print("\nown_offer_id()")
    check("reads the id from the own offer", test_own_offer_id_reads_the_id_from_the_own_offer)
    check("ignores a foreign offer", test_own_offer_id_ignores_a_foreign_offer)
    check("is none when the offer carries no id",
          test_own_offer_id_is_none_when_the_offer_carries_no_id)
    check("is none without any offer", test_own_offer_id_is_none_without_any_offer)
    check("leaves own_offer() unchanged", test_own_offer_still_reads_the_price)

    total, passed = len(PASSED), sum(PASSED)
    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/.claude/worktrees/market-bid-field
/Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/venv/bin/python tests/test_market_bid.py
```

Expected: everything ERRORs on the missing `place_offer`, `remove_offer`, `own_offer_id` and `KickbaseWriteException`.

- [ ] **Step 3: Add the exception**

In `backend/exceptions.py`:

```python
class KickbaseWriteException(KickbaseException):
    """Exception raised when Kickbase rejects a write.

    Carries the HTTP status and the message the API returned. Every other call in this
    project answers a failure with "Please check your Discord Webhook URL", which is
    unusable here: a rejected bid has to say why it was rejected, and the user is
    standing in front of the field waiting to find out.
    """

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
```

- [ ] **Step 4: Add the two write calls**

In `backend/kickbase/v4/leagues.py`, next to the module constants near the top:

```python
### Seconds to wait for a write to Kickbase. Short on purpose: the user is waiting in
### front of the field, and no other call in this module has a timeout at all.
OFFER_TIMEOUT = 15
```

And after `get_market()`:

```python
def _offer_headers(token: str) -> dict:
    """### The headers every offer call sends."""
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Cookie": f"kkstrauth={token};",
    }


def _offer_error(response) -> str:
    """### The message Kickbase gave for a rejected write.

    Falls back to naming the status, which is still more than "check your webhook URL".
    """
    try:
        body = response.json()
    except ValueError:
        return f"Kickbase antwortete mit HTTP {response.status_code}."

    if isinstance(body, dict):
        for key in ("message", "msg", "err", "error"):
            if body.get(key):
                return str(body[key])

    return f"Kickbase antwortete mit HTTP {response.status_code}."


def place_offer(token: str, league_id: str, player_id: str, price: int) -> dict:
    """### Place a bid on a player listed on the transfer market.

    The first write in this project. It deliberately does not follow the bare `except:`
    around `.json()` that the reads in this module use: that pattern reports every
    failure as a Discord webhook problem, and a bid needs its actual reason.

    Args:
        token (str): The user's kkstrauth token.
        league_id (str): The league the player is listed in.
        player_id (str): The player to bid on.
        price (int): The bid, in whole euros.

    Raises:
        exceptions.KickbaseWriteException: If Kickbase rejects the bid or cannot be
            reached. Carries the HTTP status and Kickbase's own message.

    Returns:
        dict: The response body, or an empty dict when there is none.
    """
    url = f"https://api.kickbase.com/v4/leagues/{league_id}/market/{player_id}/offers"

    try:
        response = requests.post(url, json={"price": price},
                                 headers=_offer_headers(token), timeout=OFFER_TIMEOUT)
    except requests.exceptions.RequestException as e:
        raise exceptions.KickbaseWriteException(
            504, f"Kickbase ist nicht erreichbar: {e}") from e

    if response.status_code >= 400:
        raise exceptions.KickbaseWriteException(response.status_code, _offer_error(response))

    try:
        return response.json() if response.content else {}
    except ValueError:
        return {}


def remove_offer(token: str, league_id: str, player_id: str, offer_id: str = None) -> None:
    """### Withdraw the user's own bid on a player.

    `offer_id` is optional because the market response does not reliably expose one; see
    the evidence section of the bid field spec. Without it the offer is addressed by
    player alone, which works because a user can hold only one offer per player.

    Args:
        token (str): The user's kkstrauth token.
        league_id (str): The league the player is listed in.
        player_id (str): The player whose bid is withdrawn.
        offer_id (str): The offer to withdraw, when Kickbase exposed one.

    Raises:
        exceptions.KickbaseWriteException: If Kickbase rejects the removal or cannot be
            reached.
    """
    url = f"https://api.kickbase.com/v4/leagues/{league_id}/market/{player_id}/offers"
    if offer_id:
        url = f"{url}/{offer_id}"

    try:
        response = requests.delete(url, headers=_offer_headers(token), timeout=OFFER_TIMEOUT)
    except requests.exceptions.RequestException as e:
        raise exceptions.KickbaseWriteException(
            504, f"Kickbase ist nicht erreichbar: {e}") from e

    if response.status_code >= 400:
        raise exceptions.KickbaseWriteException(response.status_code, _offer_error(response))
```

`requests.delete` is called with `json=None` by the test recorder's signature, which the real function does not pass — that is fine, the recorder accepts it as a default.

- [ ] **Step 5: Share one offer lookup between price and id**

In `backend/kickbase/endpoints/leagues.py`, replace `own_offer()` (lines 217-244) with a shared helper and two readers, so the "never read a foreign bid as ours" check exists once:

```python
    def _own_offer_entry(self, own_user_id: str):
        """### The logged in user's own offer on this player, if there is one.

        Kickbase only reveals the user's own offers: "ofs" never contains another
        manager's bid. The same price is mirrored on the item itself as "uop", with
        "uoid" naming the bidder, and some items carry only that mirror. "ofs" is read
        first and the mirror serves as the fallback.

        The bidder is checked against the user's own ID in both places. A foreign bid
        must never be reported as the user's own, whatever the API starts exposing - and
        this check living in one place is why own_offer() and own_offer_id() cannot
        disagree about it.

        Args:
            own_user_id (str): The logged in user's ID.

        Returns:
            dict: The offer, shaped like an "ofs" entry. None if no offer of theirs is
                placed. The mirror fallback yields {"uop": ...} with no offer id.
        """
        wanted = str(own_user_id)

        for offer in self.offers or []:
            bidder = offer.get("u") or offer.get("uoid")
            if bidder is not None and str(bidder) == wanted:
                return offer

        if self.ownOfferUserId is not None and str(self.ownOfferUserId) == wanted:
            return {"uop": self.ownOfferPrice}

        return None

    def own_offer(self, own_user_id: str) -> float:
        """### What the logged in user currently bids for this player, if anything.

        Args:
            own_user_id (str): The logged in user's ID.

        Returns:
            float: The user's own offer price, or None if no offer of theirs is placed.
        """
        offer = self._own_offer_entry(own_user_id)

        return offer.get("uop") if offer else None

    def own_offer_id(self, own_user_id: str) -> str:
        """### The id of the user's own offer, when Kickbase exposes one.

        The one recorded market response carries no id in its "ofs" entries, so None is
        the expected answer rather than an edge case - which is why remove_offer() can
        work without one.

        Args:
            own_user_id (str): The logged in user's ID.

        Returns:
            str: The offer id, or None when there is no offer or it carries no id.
        """
        offer = self._own_offer_entry(own_user_id)

        return offer.get("i") if offer else None
```

- [ ] **Step 6: Run the tests to verify they pass**

```bash
cd /Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/.claude/worktrees/market-bid-field
/Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/venv/bin/python tests/test_market_bid.py
/Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/venv/bin/python tests/test_market_table.py
```

Expected: `12/12 passed` for the first, and the second still green — it exercises `own_offer()`, which just changed shape underneath.

- [ ] **Step 7: Commit**

```bash
cd /Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/.claude/worktrees/market-bid-field
git status
git add tests/test_market_bid.py backend/exceptions.py backend/kickbase/v4/leagues.py backend/kickbase/endpoints/leagues.py
git commit -m "feat: place and withdraw bids through the Kickbase API"
```

---

### Task 8: The two Flask endpoints

**Files:**
- Modify: `tests/test_market_bid.py` (add two sections)
- Modify: `backend/miscellaneous.py` (add `patch_market_bid()` near `write_json_to_file()`, around line 784)
- Modify: `app.py`

**Interfaces:**
- Consumes: `leagues.place_offer`, `leagues.remove_offer`, `Market_Players.own_offer`, `Market_Players.own_offer_id`, `exceptions.KickbaseWriteException`, `main.select_league`.
- Produces:
  - `miscellaneous.patch_market_bid(player_id: str, own_bid) -> bool` — True when a row was found and rewritten.
  - `POST /api/market/<player_id>/bid`, body `{"price": int}` → `{"ownBid": int|null}` on success.
  - `DELETE /api/market/<player_id>/bid` → `{"ownBid": null}` on success.
  - Both answer errors as `{"error": "<German sentence>"}` with a 4xx/5xx status.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_market_bid.py`, before its `__main__` block:

```python
### ===============================================================================
### patch_market_bid()
### ===============================================================================


def with_market_file(rows, fn):
    """Run fn with DATA_DIR pointed at a temporary market.json, and return its rows."""
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = path.join(tmp, "data")
        ts_dir = path.join(data_dir, "timestamps")
        from os import makedirs
        makedirs(ts_dir, exist_ok=True)

        with open(path.join(data_dir, "market.json"), "w") as f:
            json.dump(rows, f)

        original = (miscellaneous.DATA_DIR, miscellaneous.TIMESTAMP_DIR)
        miscellaneous.DATA_DIR = data_dir
        miscellaneous.TIMESTAMP_DIR = ts_dir
        try:
            result = fn()
            with open(path.join(data_dir, "market.json")) as f:
                return result, json.load(f)
        finally:
            miscellaneous.DATA_DIR, miscellaneous.TIMESTAMP_DIR = original


def market_rows():
    return [
        {"playerId": "8289", "lastName": "Musah", "ownBid": None, "marketValue": 5000000},
        {"playerId": "3754", "lastName": "Boey", "ownBid": 523350, "marketValue": 500000},
    ]


def test_patch_writes_the_confirmed_bid():
    result, rows = with_market_file(market_rows(), lambda:
        miscellaneous.patch_market_bid("8289", 5200000))

    assert result is True, "expected the patch to report success"
    assert rows[0]["ownBid"] == 5200000, f"expected the bid written, got {rows[0]}"


def test_patch_clears_a_withdrawn_bid():
    result, rows = with_market_file(market_rows(), lambda:
        miscellaneous.patch_market_bid("3754", None))

    assert result is True
    assert rows[1]["ownBid"] is None, f"expected the bid cleared, got {rows[1]}"


def test_patch_leaves_other_rows_alone():
    _, rows = with_market_file(market_rows(), lambda:
        miscellaneous.patch_market_bid("8289", 5200000))
    assert rows[1]["ownBid"] == 523350, f"expected Boey untouched, got {rows[1]}"


def test_patch_of_an_unknown_player_changes_nothing():
    result, rows = with_market_file(market_rows(), lambda:
        miscellaneous.patch_market_bid("999999", 1))

    assert result is False, "expected the patch to report that nothing matched"
    assert rows == market_rows(), f"expected the file untouched, got {rows}"


def test_patch_survives_a_missing_file():
    """app.py can serve a request before main.py ever ran."""
    with tempfile.TemporaryDirectory() as tmp:
        original = miscellaneous.DATA_DIR
        miscellaneous.DATA_DIR = path.join(tmp, "data")
        try:
            assert miscellaneous.patch_market_bid("8289", 1) is False
        finally:
            miscellaneous.DATA_DIR = original


### ===============================================================================
### The endpoints
### ===============================================================================


def client_with(market, place=None, remove=None, own_user_id=OWN_USER_ID):
    """A Flask test client with login, market and the write calls faked out."""
    import app as flask_app
    import main

    class FakeUser:
        id = own_user_id
        name = "shirazzi"

    class FakeLeague:
        id = LEAGUE_ID
        name = "Test"

    original = (flask_app.user.login, flask_app.leagues.get_league_list,
                flask_app.leagues.get_market, flask_app.leagues.place_offer,
                flask_app.leagues.remove_offer, main.select_league)

    flask_app.user.login = lambda *a, **k: (FakeUser(), "tok")
    flask_app.leagues.get_league_list = lambda token: [FakeLeague()]
    main.select_league = lambda league_list: FakeLeague()
    flask_app.leagues.get_market = lambda token, lid: [Market_Players(i) for i in market()]
    flask_app.leagues.place_offer = place or (lambda *a, **k: {})
    flask_app.leagues.remove_offer = remove or (lambda *a, **k: None)

    flask_app.app.config["TESTING"] = True
    return flask_app.app.test_client(), original


def restore(original):
    import app as flask_app
    import main
    (flask_app.user.login, flask_app.leagues.get_league_list, flask_app.leagues.get_market,
     flask_app.leagues.place_offer, flask_app.leagues.remove_offer,
     main.select_league) = original


def post_bid(market, price, place=None):
    """POST a bid and return (status, body)."""
    client, original = client_with(market, place=place)
    try:
        response = client.post(f"/api/market/{PLAYER_ID}/bid", json={"price": price})
        return response.status_code, response.get_json()
    finally:
        restore(original)


def plain_market():
    return [market_item()]


def bid_market():
    return [market_item(ofs=[{"i": "77", "u": OWN_USER_ID, "uoid": OWN_USER_ID,
                              "uop": 5200000}])]


def test_post_rejects_a_non_positive_price():
    status, body = post_bid(plain_market, 0)
    assert status == 400, f"expected 400, got {status} {body}"
    assert "error" in body and body["error"], f"expected a German message, got {body}"


def test_post_rejects_a_non_integer_price():
    status, body = post_bid(plain_market, "viel")
    assert status == 400, f"expected 400, got {status} {body}"


def test_post_rejects_a_player_not_on_the_market():
    client, original = client_with(plain_market)
    try:
        response = client.post("/api/market/999999/bid", json={"price": 1180000})
        assert response.status_code == 404, \
            f"expected 404, got {response.status_code} {response.get_json()}"
    finally:
        restore(original)


def test_post_refuses_an_own_listing():
    """Nobody bids on their own player, and the server must not rely on the browser."""
    def own_listing():
        return [market_item(u={"i": OWN_USER_ID, "n": "shirazzi"})]

    status, body = post_bid(own_listing, 1180000)
    assert status == 409, f"expected 409, got {status} {body}"


def test_post_returns_the_bid_read_back_from_kickbase():
    """Not the typed value: a silently clamped bid would otherwise be shown as typed."""
    calls = []
    status, body = post_bid(bid_market, 1180000,
                            place=lambda *a, **k: calls.append(a) or {})
    assert status == 200, f"expected 200, got {status} {body}"
    ### The faked market reports 5.200.000 regardless of what was sent
    assert body == {"ownBid": 5200000}, f"expected the read-back bid, got {body}"
    assert calls, "expected place_offer to have been called"


def test_post_passes_the_kickbase_rejection_through():
    def rejecting(*a, **k):
        raise exceptions.KickbaseWriteException(400, "Offer price is below the market value")

    status, body = post_bid(plain_market, 1, place=rejecting)
    assert status == 400, f"expected the API status passed through, got {status}"
    assert "below the market value" in body["error"], \
        f"expected the API message passed through, got {body}"


def test_delete_withdraws_and_reports_no_bid():
    removed = []
    client, original = client_with(
        bid_market, remove=lambda *a, **k: removed.append(a))
    try:
        response = client.delete(f"/api/market/{PLAYER_ID}/bid")
        assert response.status_code == 200, \
            f"expected 200, got {response.status_code} {response.get_json()}"
        assert response.get_json() == {"ownBid": None}, \
            f"expected a cleared bid, got {response.get_json()}"
        assert removed, "expected remove_offer to have been called"
    finally:
        restore(original)


def test_delete_without_a_bid_is_a_conflict():
    client, original = client_with(plain_market)
    try:
        response = client.delete(f"/api/market/{PLAYER_ID}/bid")
        assert response.status_code == 409, \
            f"expected 409, got {response.status_code} {response.get_json()}"
    finally:
        restore(original)
```

Extend the `__main__` block:

```python
    print("\npatch_market_bid()")
    check("writes the confirmed bid", test_patch_writes_the_confirmed_bid)
    check("clears a withdrawn bid", test_patch_clears_a_withdrawn_bid)
    check("leaves other rows alone", test_patch_leaves_other_rows_alone)
    check("changes nothing for an unknown player", test_patch_of_an_unknown_player_changes_nothing)
    check("survives a missing file", test_patch_survives_a_missing_file)

    print("\nPOST /api/market/<id>/bid")
    check("rejects a non positive price", test_post_rejects_a_non_positive_price)
    check("rejects a non integer price", test_post_rejects_a_non_integer_price)
    check("rejects a player not on the market", test_post_rejects_a_player_not_on_the_market)
    check("refuses an own listing", test_post_refuses_an_own_listing)
    check("returns the bid read back from Kickbase",
          test_post_returns_the_bid_read_back_from_kickbase)
    check("passes the Kickbase rejection through", test_post_passes_the_kickbase_rejection_through)

    print("\nDELETE /api/market/<id>/bid")
    check("withdraws and reports no bid", test_delete_withdraws_and_reports_no_bid)
    check("is a conflict without a bid", test_delete_without_a_bid_is_a_conflict)
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/.claude/worktrees/market-bid-field
set -a; source /Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/.env; set +a
/Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/venv/bin/python tests/test_market_bid.py
```

Expected: the twelve Task 7 checks pass, the new ones ERROR on `patch_market_bid` missing and 404 on the unregistered routes. `app.py` reads credentials from the environment at import, hence the `source`.

- [ ] **Step 3: Add the market.json patch**

In `backend/miscellaneous.py`, after `write_json_to_file()`:

```python
def patch_market_bid(player_id: str, own_bid) -> bool:
    """### Write a confirmed bid into the market.json row it belongs to.

    The frontend imports market.json at build time, so a bid placed through the API is
    invisible until the next scrape. Patching the row bridges that gap, and survives a
    page reload the way a value held only in React state would not.

    This can race a main.py run writing the same file. The writes in this project are
    not atomic - an open item in docs/improvement-plan-2026-08-12.md, cluster B - and
    the next scrape repairs the row either way, so the race is accepted rather than
    solved here.

    Args:
        player_id (str): The player whose row is patched.
        own_bid: The confirmed bid, or None when it was withdrawn.

    Returns:
        bool: True when a row matched and the file was rewritten.
    """
    market_path = path.join(DATA_DIR, "market.json")

    if not path.exists(market_path):
        logging.warning(f"{market_path} does not exist yet, so no bid was patched into it.")
        return False

    try:
        with open(market_path, "r") as f:
            rows = json.load(f)
    except json.JSONDecodeError:
        logging.warning(f"{market_path} is empty or invalid, so no bid was patched into it.")
        return False

    for row in rows:
        if str(row.get("playerId")) == str(player_id):
            row["ownBid"] = own_bid
            write_json_to_file(rows, "market.json")
            return True

    logging.warning(f"No market.json row for player {player_id}, so no bid was patched in.")
    return False
```

- [ ] **Step 4: Add the endpoints**

In `app.py`, extend the imports:

```python
from flask import Flask, jsonify, request

import main
from backend import exceptions, miscellaneous
from backend.kickbase.v4 import leagues, user
```

Then, after the `/api/livepoints` route:

```python
def _connect():
    """### Log in and pick the league the frontend shows.

    The league is resolved here rather than taken from the request: a league id from the
    browser would be a league id we did not check.

    Returns:
        tuple: (user_info, user_token, selected_league).

    Raises:
        exceptions.LoginException: If the login fails.
        exceptions.KickbaseException: If the account is in no league.
    """
    user_info, user_token = user.login(kb_mail, kb_password, discord_webhook)

    league_list = leagues.get_league_list(user_token)
    if not league_list:
        raise exceptions.KickbaseException("No leagues found for this Kickbase account.")

    return user_info, user_token, main.select_league(league_list)


def _listing(user_token: str, league_id: str, player_id: str):
    """### The market entry for one player, or None if they are not listed.

    Fetched fresh every time: get_market() is not cached, which is what makes this
    usable both to check before a write and to read the result back after one.
    """
    for listing in leagues.get_market(user_token, league_id):
        if str(listing.id) == str(player_id):
            return listing

    return None


@app.route("/api/market/<player_id>/bid", methods=["POST"])
def place_bid(player_id):
    """### Places a bid on a player on the transfer market.

    Answers with the bid Kickbase confirms rather than the one that was sent, so a
    silently clamped or rounded bid is not displayed as the typed value.
    """
    payload = request.get_json(silent=True) or {}
    price = payload.get("price")

    ### bool is an int in Python, and True would otherwise pass as a price of 1
    if isinstance(price, bool) or not isinstance(price, int) or price <= 0:
        return jsonify({"error": "Das Gebot muss eine positive ganze Zahl sein."}), 400

    logging.info(f"Flask API: Placing a bid of {price} on player {player_id}...")

    try:
        user_info, user_token, selected_league = _connect()

        listing = _listing(user_token, selected_league.id, player_id)
        if listing is None:
            return jsonify({"error": "Dieser Spieler steht nicht auf dem Transfermarkt."}), 404

        ### Nobody bids on their own listing. Checked here as well as in the frontend,
        ### because a check only in the browser is not a check.
        if listing.userId is not None and str(listing.userId) == str(user_info.id):
            return jsonify({"error": "Auf ein eigenes Angebot kannst du nicht bieten."}), 409

        leagues.place_offer(user_token, selected_league.id, player_id, price)

        ### Read back what Kickbase recorded, rather than trusting what we sent
        confirmed = _listing(user_token, selected_league.id, player_id)
        own_bid = confirmed.own_offer(user_info.id) if confirmed else None

        miscellaneous.patch_market_bid(player_id, own_bid)
    except exceptions.KickbaseWriteException as e:
        logging.error(f"Flask API: Kickbase rejected the bid: {e}")
        return jsonify({"error": str(e)}), e.status
    except exceptions.LoginException as e:
        logging.error(f"Flask API: {e}")
        return jsonify({"error": "Login bei Kickbase fehlgeschlagen. Bitte Zugangsdaten prüfen."}), 502
    except exceptions.KickbaseException as e:
        logging.error(f"Flask API: {e}")
        return jsonify({"error": "Kickbase konnte das Gebot nicht verarbeiten."}), 502

    logging.info(f"Flask API: Bid on player {player_id} is now {own_bid}.")

    return jsonify({"ownBid": own_bid})


@app.route("/api/market/<player_id>/bid", methods=["DELETE"])
def withdraw_bid(player_id):
    """### Withdraws the user's own bid on a player.

    The offer is looked up in a fresh market read rather than from an id the frontend
    remembered: an id written into market.json hours ago would be stale, and the
    recorded response carries none in the first place.
    """
    logging.info(f"Flask API: Withdrawing the bid on player {player_id}...")

    try:
        user_info, user_token, selected_league = _connect()

        listing = _listing(user_token, selected_league.id, player_id)
        if listing is None:
            return jsonify({"error": "Dieser Spieler steht nicht auf dem Transfermarkt."}), 404

        if listing.own_offer(user_info.id) is None:
            return jsonify({"error": "Auf diesen Spieler hast du kein Gebot abgegeben."}), 409

        leagues.remove_offer(user_token, selected_league.id, player_id,
                             listing.own_offer_id(user_info.id))

        miscellaneous.patch_market_bid(player_id, None)
    except exceptions.KickbaseWriteException as e:
        logging.error(f"Flask API: Kickbase rejected the withdrawal: {e}")
        return jsonify({"error": str(e)}), e.status
    except exceptions.LoginException as e:
        logging.error(f"Flask API: {e}")
        return jsonify({"error": "Login bei Kickbase fehlgeschlagen. Bitte Zugangsdaten prüfen."}), 502
    except exceptions.KickbaseException as e:
        logging.error(f"Flask API: {e}")
        return jsonify({"error": "Kickbase konnte das Gebot nicht zurückziehen."}), 502

    logging.info(f"Flask API: Bid on player {player_id} withdrawn.")

    return jsonify({"ownBid": None})
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd /Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/.claude/worktrees/market-bid-field
set -a; source /Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/.env; set +a
/Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/venv/bin/python tests/test_market_bid.py
```

Expected: `25/25 passed`.

- [ ] **Step 6: Commit**

```bash
cd /Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/.claude/worktrees/market-bid-field
git status
git add tests/test_market_bid.py backend/miscellaneous.py app.py
git commit -m "feat: expose placing and withdrawing a bid over the API"
```

---

### Task 9: Wire the cell up — edit, submit, withdraw

**Files:**
- Modify: `frontend/package.json` (add `proxy`)
- Modify: `frontend/src/components/BidCell.js` (editing and pending states)
- Modify: `frontend/src/components/BidCell.test.js` (extend)
- Modify: `frontend/src/components/MarketTable.js` (state, callbacks, dialog, snackbar)
- Modify: `README.md` (document the feature and that it needs the Flask API)

**Interfaces:**
- Consumes: `POST`/`DELETE /api/market/<player_id>/bid` (Task 8), `BidCell` (Task 6).
- Produces: `<BidCell row editing draft pending growthDays targetDays onEdit onDraftChange onSubmit onWithdraw onCancel />`. `editing` and `pending` are booleans; `draft` is the numeric string in the input; `onDraftChange` takes the new string.

- [ ] **Step 1: Add the dev-server proxy**

In `frontend/package.json`, next to `"homepage"`:

```json
    "proxy": "http://localhost:5000",
```

This is what lets the frontend call relative `/api/...` paths in development and in the container alike — the browser only ever talks to the dev server, which forwards to Flask. It is also what the commented-out `fetch("/api/livepoints")` in `App.js` originally assumed.

- [ ] **Step 2: Write the failing tests**

Append to `frontend/src/components/BidCell.test.js`:

```js
describe("BidCell while editing", () => {
    const editing = (overrides, props) => render(
        <BidCell
            row={row(overrides)}
            growthDays={3}
            targetDays={3}
            editing
            draft={props?.draft ?? "1180000"}
            onEdit={() => {}}
            onDraftChange={() => {}}
            onSubmit={() => {}}
            onWithdraw={() => {}}
            onCancel={() => {}}
            {...props}
        />
    )

    it("shows the draft in an input with German thousands separators", () => {
        editing()
        expect(screen.getByRole("textbox")).toHaveValue("1.180.000")
    })

    it("submits on the checkmark", async () => {
        const onSubmit = jest.fn()
        editing({}, { onSubmit })
        await userEvent.click(screen.getByLabelText("Gebot abgeben"))
        expect(onSubmit).toHaveBeenCalled()
    })

    it("submits on Enter", async () => {
        const onSubmit = jest.fn()
        editing({}, { onSubmit })
        await userEvent.type(screen.getByRole("textbox"), "{Enter}")
        expect(onSubmit).toHaveBeenCalled()
    })

    it("cancels on the X when no bid is placed", async () => {
        const onCancel = jest.fn()
        const onWithdraw = jest.fn()
        editing({ ownBid: null }, { onCancel, onWithdraw })
        const x = screen.getByLabelText("Abbrechen")
        await userEvent.click(x)
        expect(onCancel).toHaveBeenCalled()
        expect(onWithdraw).not.toHaveBeenCalled()
    })

    it("withdraws on the X when a bid is placed", async () => {
        // Same icon, two meanings - the tooltip and the label say which one applies
        const onCancel = jest.fn()
        const onWithdraw = jest.fn()
        editing({ ownBid: 1250000 }, { onCancel, onWithdraw })
        await userEvent.click(screen.getByLabelText("Gebot zurückziehen"))
        expect(onWithdraw).toHaveBeenCalled()
        expect(onCancel).not.toHaveBeenCalled()
    })

    it("cancels on Escape", async () => {
        const onCancel = jest.fn()
        editing({}, { onCancel })
        await userEvent.type(screen.getByRole("textbox"), "{Escape}")
        expect(onCancel).toHaveBeenCalled()
    })

    it("reports the typed value as digits only", async () => {
        const onDraftChange = jest.fn()
        editing({}, { onDraftChange, draft: "" })
        await userEvent.type(screen.getByRole("textbox"), "1200000")
        expect(onDraftChange).toHaveBeenLastCalledWith("1200000")
    })

    it("disables both actions while a request is in flight", () => {
        render(
            <BidCell
                row={row()} growthDays={3} targetDays={3}
                editing pending draft="1180000"
                onEdit={() => {}} onDraftChange={() => {}} onSubmit={() => {}}
                onWithdraw={() => {}} onCancel={() => {}}
            />
        )
        expect(screen.getByRole("progressbar")).toBeInTheDocument()
        expect(screen.queryByLabelText("Gebot abgeben")).not.toBeInTheDocument()
    })

    it("cannot submit an empty draft", () => {
        editing({}, { draft: "" })
        expect(screen.getByLabelText("Gebot abgeben")).toBeDisabled()
    })
})
```

- [ ] **Step 3: Run the tests to verify they fail**

```bash
cd /Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/.claude/worktrees/market-bid-field/frontend
CI=true npm test -- --watchAll=false --testPathPattern=BidCell
```

Expected: the resting-state suite passes, the editing suite fails — `BidCell` ignores the new props.

- [ ] **Step 4: Add the editing and pending states**

In `frontend/src/components/BidCell.js`, extend the imports:

```js
import { Box, CircularProgress, IconButton, Tooltip, Typography } from "@mui/material"
import CheckIcon from "@mui/icons-material/Check"
import CloseIcon from "@mui/icons-material/Close"
import { NumericFormat } from "react-number-format"
import { currencyFormatter, percentFormatter } from "./SharedConstants"
```

Add the editing branch at the top of the component body, after the `isOwnListing` guard:

```js
    const hasBid = row.ownBid !== null && row.ownBid !== undefined

    if (editing) {
        // The X does double duty: there is only one "make this go away" gesture, and
        // which one it is depends on whether a bid is standing
        const dismissLabel = hasBid ? "Gebot zurückziehen" : "Abbrechen"

        return (
            <Box sx={{ display: "flex", alignItems: "center", gap: "2px", width: "100%" }}>
                <NumericFormat
                    value={draft}
                    thousandSeparator="."
                    decimalScale={0}
                    allowNegative={false}
                    disabled={pending}
                    autoFocus
                    onFocus={(e) => e.target.select()}
                    // The raw digits, so the caller never has to strip separators
                    onValueChange={({ value }) => onDraftChange(value)}
                    onKeyDown={(e) => {
                        if (e.key === "Enter" && draft)
                            onSubmit()
                        else if (e.key === "Escape")
                            onCancel()
                    }}
                    style={{ width: "100%", textAlign: "right", font: "inherit",
                             background: "transparent", color: "inherit",
                             border: "1px solid currentColor", borderRadius: "4px" }}
                />
                {pending ? (
                    <CircularProgress size={18} sx={{ margin: "0 8px" }} />
                ) : (
                    <>
                        <Tooltip title="Gebot abgeben" arrow>
                            <IconButton aria-label="Gebot abgeben" size="small"
                                        disabled={!draft} onClick={onSubmit}>
                                <CheckIcon fontSize="small" />
                            </IconButton>
                        </Tooltip>
                        <Tooltip title={dismissLabel} arrow>
                            <IconButton aria-label={dismissLabel} size="small"
                                        onClick={hasBid ? onWithdraw : onCancel}>
                                <CloseIcon fontSize="small" />
                            </IconButton>
                        </Tooltip>
                    </>
                )}
            </Box>
        )
    }
```

Update the signature and delete the now-duplicated `hasBid` line further down:

```js
function BidCell({ row, growthDays, targetDays, editing, draft, pending,
                   onEdit, onDraftChange, onSubmit, onWithdraw, onCancel }) {
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd /Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/.claude/worktrees/market-bid-field/frontend
CI=true npm test -- --watchAll=false --testPathPattern=BidCell
```

Expected: all twenty pass. A disabled MUI `IconButton` renders a disabled `<button>`, which is what `toBeDisabled()` reads.

- [ ] **Step 6: Own the state in `MarketTable`**

In `frontend/src/components/MarketTable.js`, add the imports:

```js
import { useState } from "react"
import {
    Box, Button, Dialog, DialogActions, DialogContent, DialogContentText, DialogTitle,
    Snackbar, Alert, alpha, useTheme
} from "@mui/material"
```

Inside `MarketTable`, before the column definitions:

```js
    // The editing state lives here rather than in the cell: renderCell re-runs on every
    // scroll and every sort, and state held inside a cell would not survive either.
    const [edit, setEdit] = useState(null)          // { playerId, draft }
    const [pendingId, setPendingId] = useState(null)
    // Confirmed bids, keyed by player. market.json is imported at build time, so this is
    // what shows a bid before the patched file has been picked up.
    const [bids, setBids] = useState({})
    const [error, setError] = useState(null)
    const [confirming, setConfirming] = useState(null)   // { playerId, price }

    const closeEdit = () => setEdit(null)

    // The suggestion is the honest yardstick for a typo: one digit too many is always a
    // factor of ten, so twice the suggestion catches it on a cheap player as well as on
    // an expensive one, which a fixed euro threshold does not.
    const needsConfirmation = (row, price) => {
        const reference = row.suggestedBid || row.marketValue
        return Boolean(reference) && price >= 2 * reference
    }

    const send = async (playerId, price) => {
        setPendingId(playerId)
        setConfirming(null)

        try {
            const response = await fetch(`/api/market/${playerId}/bid`, {
                method: price === null ? "DELETE" : "POST",
                headers: { "Content-Type": "application/json" },
                body: price === null ? undefined : JSON.stringify({ price })
            })
            const body = await response.json().catch(() => ({}))

            if (!response.ok) {
                setError(body.error || `Kickbase antwortete mit HTTP ${response.status}.`)
                return
            }

            // What Kickbase confirmed, not what was typed
            setBids((current) => ({ ...current, [playerId]: body.ownBid }))
            closeEdit()
        } catch (e) {
            // A network failure rather than an HTTP status: naming the cause beats
            // "Gebot fehlgeschlagen", which would send you looking at Kickbase
            setError("Die Flask-API ist nicht erreichbar. Läuft app.py?")
        } finally {
            setPendingId(null)
        }
    }

    const submit = (row) => {
        const price = Number(edit.draft)
        if (!price)
            return

        if (needsConfirmation(row, price))
            setConfirming({ playerId: row.playerId, price })
        else
            send(row.playerId, price)
    }
```

Replace the `ownBid` column's `renderCell` with the wired version:

```js
            renderCell: (params) => (
                <BidCell
                    row={params.row}
                    growthDays={config.bepGrowthDays}
                    targetDays={config.bepTargetDays}
                    editing={edit?.playerId === params.row.playerId}
                    draft={edit?.playerId === params.row.playerId ? edit.draft : ""}
                    pending={pendingId === params.row.playerId}
                    onEdit={() => setEdit({
                        playerId: params.row.playerId,
                        // The running bid if there is one, else the suggestion, else empty
                        draft: String(params.row.ownBid ?? params.row.suggestedBid ?? "")
                    })}
                    onDraftChange={(draft) => setEdit((current) => ({ ...current, draft }))}
                    onSubmit={() => submit(params.row)}
                    onWithdraw={() => send(params.row.playerId, null)}
                    onCancel={closeEdit}
                />
            )
```

In the row mapping, let a confirmed bid win over the file:

```js
            // A bid confirmed this session overrides what market.json was built with
            ownBid: row.playerId in bids ? bids[row.playerId] : row.ownBid,
```

Then add the dialog and the snackbar inside the returned `<Box>`, after `<PagedDataGrid ... />`:

```js
            <Dialog open={Boolean(confirming)} onClose={() => setConfirming(null)}>
                <DialogTitle>Gebot bestätigen</DialogTitle>
                <DialogContent>
                    <DialogContentText>
                        {confirming && `Wirklich ${currencyFormatter.format(confirming.price)} bieten? `
                            + "Das ist mindestens das Doppelte des Vorschlags."}
                    </DialogContentText>
                </DialogContent>
                <DialogActions>
                    <Button onClick={() => setConfirming(null)}>Abbrechen</Button>
                    <Button onClick={() => send(confirming.playerId, confirming.price)} autoFocus>
                        Gebot abgeben
                    </Button>
                </DialogActions>
            </Dialog>

            <Snackbar open={Boolean(error)} autoHideDuration={8000} onClose={() => setError(null)}>
                <Alert severity="error" onClose={() => setError(null)}>{error}</Alert>
            </Snackbar>
```

The failing cell deliberately stays in editing state with the typed value intact — `closeEdit()` runs only on success, so a rejected bid is not thrown away.

- [ ] **Step 7: Run every test**

```bash
cd /Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/.claude/worktrees/market-bid-field/frontend
CI=true npm test -- --watchAll=false
```

Expected: all suites green, including `marketFormulas` and `BidCell`.

- [ ] **Step 8: Document it**

In `README.md`, in the section describing the Transfermarkt table, add:

```markdown
The "Dein Gebot" column shows, greyed out, the bid that would break even after
`BEP_TARGET_DAYS` days at the average daily market value growth of the last
`BEP_GROWTH_DAYS` days. Clicking the cell makes it editable: the checkmark places the bid
with Kickbase, the X withdraws a bid that is already standing. A dash means the market
value is currently flat or falling, or that the history is too short — the cell can still
be clicked to bid anyway.

This is the one feature that writes to Kickbase, so it needs the Flask API (`app.py`)
running alongside the frontend. Without it the cell reports that the API is unreachable;
everything else in the table keeps working.
```

- [ ] **Step 9: Commit**

```bash
cd /Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/.claude/worktrees/market-bid-field
git status
git add frontend/package.json frontend/src/components/BidCell.js frontend/src/components/BidCell.test.js frontend/src/components/MarketTable.js README.md
git commit -m "feat: place and withdraw bids from the market table"
```

---

### Task 10: End-to-end verification against the real league

Everything so far is tested against fakes. This task runs it for real, once, and is the gate before the pull request.

**Files:** none changed unless a defect turns up.

**Interfaces:**
- Consumes: everything.
- Produces: a verified feature, or a defect list.

- [ ] **Step 1: Start both processes**

Two terminals, both from the worktree:

```bash
cd /Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/.claude/worktrees/market-bid-field
set -a; source /Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/.env; set +a
/Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/venv/bin/python -m flask run --port=5000
```

```bash
cd /Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/.claude/worktrees/market-bid-field/frontend
npm start
```

- [ ] **Step 2: Place a real bid on the cheapest player**

In the Transfermarkt table, sort by Marktwert ascending, click the grey suggestion on the cheapest player, submit with the checkmark.

Expected: the spinner appears, then the cell shows the bid in normal weight with its surcharge — **and the figure is the one Kickbase confirmed**. Cross-check it in the Kickbase app.

- [ ] **Step 3: Confirm it survives a reload**

Reload the browser. The bid must still be there — that is the `market.json` patch working. Also check the file directly:

```bash
cd /Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/.claude/worktrees/market-bid-field
/Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/venv/bin/python -c "
import json
rows = json.load(open('frontend/src/data/market.json'))
print([(r['lastName'], r['ownBid']) for r in rows if r['ownBid'] is not None])
"
```

- [ ] **Step 4: Withdraw it again**

Click the bid, then the X. Expected: the cell falls back to the grey suggestion, and the bid is gone in the Kickbase app too.

**If it does not disappear in the app, stop and report** — the DELETE form from Task 1 is wrong and a real bid is standing.

- [ ] **Step 5: Exercise the guardrails**

Four checks, each in the browser:

1. **Own listing** — find a player you listed yourself; the cell must be empty and unclickable.
2. **Typo dialog** — type ten times the suggestion; the confirmation dialog must appear and naming the amount; cancel it and confirm no bid was placed.
3. **Below the market value** — bid 1 €; Kickbase must reject it and the snackbar must show *its* message, with your typed value still in the field.
4. **API down** — stop the Flask process, try to bid; the snackbar must name the Flask API rather than blaming Kickbase. Restart it afterwards.

- [ ] **Step 6: Confirm a wider horizon changes the suggestion**

The point of the env variables:

```bash
cd /Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/.claude/worktrees/market-bid-field
set -a; source /Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/.env; set +a
BEP_GROWTH_DAYS=7 BEP_TARGET_DAYS=14 /Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/venv/bin/python main.py
```

Expected in the browser after the rebuild: visibly higher suggestions, "Tage bis BEP" recomputed off the seven-day pace, and the help text now saying 7 and 14 rather than 3 and 3. Then restore the defaults and re-run `main.py`.

- [ ] **Step 7: Run the whole test suite once more**

```bash
cd /Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/.claude/worktrees/market-bid-field
set -a; source /Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/.env; set +a
for t in tests/test_bep_config.py tests/test_market_bid.py tests/test_market_table.py \
         tests/test_start_date.py tests/test_balance_events.py tests/test_achievements.py \
         tests/test_login_bonus.py tests/test_ownership.py tests/test_reverted_transfers.py \
         tests/test_caching.py tests/test_team_overview.py tests/test_profilepic.py; do
    echo "=== $t"
    /Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/venv/bin/python "$t" || echo "FAILED: $t"
done
cd frontend && CI=true npm test -- --watchAll=false
```

Expected: every script exits 0 and jest is green. Report any failure with its output rather than summarising it.

- [ ] **Step 8: Open the pull request**

```bash
cd /Users/maximilianshiraishi/Desktop/Projekte/kickbase-plus/.claude/worktrees/market-bid-field
git status
git log --oneline main..HEAD
git push -u origin feat/market-bid-field
gh pr create --base main --title "Editierbares Gebot-Feld im Transfermarkt" --body "..."
```

The body should cover: what the column now does, the two env variables and their defaults, the fact that the defaults are behaviour-neutral and why, the probed endpoints from Task 1, and the two accepted costs (the `market.json` write race, the per-request login).

---

## Self-Review

**Spec coverage.** Every section maps to a task: configuration → 2, growth average → 3, row fields and `config.json` → 4, formula refactor → 5, resting cell states and help text → 6, write calls and exception → 7, endpoints and patch → 8, editing, proxy and README → 9, live verification → 10. The spec's open questions are Task 1, which gates 7 and 8. Decisions 1–8 each land in a step: greyed-out suggestion with sorting untouched (6.5), dash but clickable (6.1 test, 6.3), no stored offer ids (8.4), server-side league (8.4 `_connect`), read-back (8.4 and its test), no client-side minimum (7 error path, verified 10.5.3), one X with two meanings (9.4), CRA proxy (9.1).

**Placeholders.** None: every code step carries the code, every test step the assertions, every run step the command and the expected output. Two places intentionally defer to reality rather than to a later decision — Task 7 defers paths to Task 1's evidence, and Task 4 step 2 says to lengthen a fixture if a seven-day window cannot be filled. Both name the resolution rather than leaving it open.

**Type consistency.** `avgDailyGrowth` is the JSON key and the property in every task. `get_bep_days()` returns `(growth_days, target_days)` in Tasks 2, 3, 4. `breakEvenBid(row, targetDays)` matches the spec after the collapse of `projectedMarketValue`. `patch_market_bid(player_id, own_bid) -> bool` is identical in Task 8's test and implementation. `BidCell`'s props are the same eleven names in Task 6 (four of them) and Task 9 (all eleven). `KickbaseWriteException(status, message)` with `.status` is raised in Task 7 and read in Task 8.
