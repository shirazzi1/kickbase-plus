# Editable bid field in the transfer market table

Date: 2026-08-13

## Problem

The "Dein Gebot" column shows a bid that already exists and nothing else. Deciding what
to bid still happens in your head, and placing the bid happens in the Kickbase app —
with the numbers you just read here retyped there.

Two gaps, then. The column knows the market value and the growth of the last days but
does not say what a *sensible* bid would be. And the whole project is read-only: every
Kickbase call in `backend/` is a GET, so acting on what the table shows means leaving it.

## What a sensible bid is

A bid breaks even once the market value has grown into it. At an average daily growth
`g`, a bid of `marketValue + n · g` is exactly the market value in `n` days. So the bid
that breaks even at a chosen horizon is a projection, and the number of days a given
price needs is the same line read the other way:

```
breakEvenBid    = marketValue + targetDays · g      (solve for price)
daysToBreakEven = (price − marketValue) / g         (solve for days)
```

The asking price does not enter the first equation. Break-even is a statement about
market value and growth; the price is what you compare the result against.

`marketFormulas.daysToBreakEven` already implements the second form, with `g` hardcoded
as the mean of `today`, `yesterday` and `twoDays` — the only three deltas that happen to
be in `market.json`.

## Configuration

Two horizons, deliberately separate, because they answer different questions: how far
back to measure the pace, and how far ahead to demand the payback.

| Variable | Default | Meaning |
| --- | --- | --- |
| `BEP_GROWTH_DAYS` | `3` | Days the daily growth is averaged over |
| `BEP_TARGET_DAYS` | `3` | Horizon the suggested bid must break even within |

**Both are read in Python only.** The average needs the full market value history, which
only `main.py` holds; a `REACT_APP_*` twin would be a second home for the same number,
free to drift from the first. The frontend receives the values as data instead.

The defaults reproduce today's numbers **exactly**, and that is provable rather than
asserted. The daily deltas telescope:

```
today + yesterday + twoDays = (mv[-1] − mv[-2]) + (mv[-2] − mv[-3]) + (mv[-3] − mv[-4])
                            =  mv[-1] − mv[-4]
```

so `(today + yesterday + twoDays) / 3` is `(mv[-1] − mv[-1-3]) / 3`, which is what
`average_daily_growth(history, 3)` returns. This is the regression test that guards the
refactor.

## Open questions, to be settled against the live API before implementing

The write endpoints are the one part of this design that cannot be derived from the
codebase. Nothing in `backend/` writes, so both the paths and the payload shapes are
community knowledge, not established fact here.

**The recorded market response carries no offer id.** From the snapshot in
`2026-08-12-merged-market-table-design.md` (league `Kickbase-Elite 26/27`):

```json
"ofs": [{"u": "3854976", "unm": "shirazzi", "uoid": "3854976",
         "uop": 5222222, "st": 0, "uim": "user/91fd....jpe"}]
```

No `i`. Either the snapshot was trimmed, or v4 does not expose one — in which case a
`DELETE .../offers/{offerId}` route cannot be addressed at all and removal must work
without an id. This decides the shape of the delete path, so it is probed first.

The probe, in order, each step gating the next:

1. **Read-only.** GET the market and dump the raw `ofs` of a player carrying an own bid.
   Settles the offer id question without writing anything.
2. **Harmless write.** `POST .../market/{playerId}/offers` with `{"price": 1}`. A 400 or
   422 proves the route exists and rejects the price; a 404 means the path is wrong.
   No bid is placed either way.
3. **Real round trip.** A bid at the market value on the cheapest player on the market,
   then remove it again. Confirms the success payload (and whether it returns an offer
   id) and the delete route in the same pass.

Findings get written back into this document under *Evidence from the live API* before
any implementation code is written. The probe itself is a throwaway script in the
scratchpad and is not committed.

## Decisions

1. **The suggestion is greyed-out data, not a placeholder.** It sits in the cell at 60%
   opacity, in the same tabular figures as a real bid. Sorting, however, stays on the
   *real* bid, with bid-less rows last. Mixing suggestions into the sort would make the
   column unreadable: a row's position would no longer say whether a bid exists.
2. **No suggestion means a dash, and the cell stays clickable.** When growth is flat,
   negative or the history is too short, the cell shows `–` — the same answer "Tage bis
   BEP" already gives in that case — but still opens an empty input. The tool declines to
   recommend; it does not decline to act.
3. **Offer ids are never stored.** The delete path looks the offer up in a fresh market
   read at the moment of deletion. An id written into `market.json` hours earlier would
   be stale, and the `uoid`/`uop` mirror fallback in `own_offer()` yields no id at all.
4. **The league id is resolved server-side.** Like `/api/livepoints`, the endpoints call
   `select_league()` themselves rather than trusting a league id from the browser.
5. **The confirmed bid is read back from Kickbase.** The cell shows what Kickbase
   confirms, not what was typed. A silently clamped or rounded bid would otherwise be
   displayed as the typed value and believed.
6. **No client-side minimum-bid check.** Kickbase rejects bids below the market value;
   duplicating that rule here would mean maintaining a second copy of someone else's
   validation. The error path carries the API's own message instead — which makes the
   error path load-bearing rather than decorative.
7. **One X icon with two meanings.** With a bid placed it withdraws the bid; without one
   it cancels editing. Two icons for "make this go away" would be worse; the tooltip
   names which one applies.
8. **CRA proxy instead of CORS.** The browser talks only to the dev server on 3000,
   which forwards `/api/*` to Flask on 5000. Works locally and in the container — the
   container runs the dev server anyway — and needs no host-guessing env variable. It is
   also what the commented-out `fetch("/api/livepoints")` in `App.js:78` intended.

## Design

### Backend: configuration

**`miscellaneous.get_bep_days()`** returns `(growth_days, target_days)`, following the
precedent of `get_start_datetime()`: read the environment, validate, raise
`exceptions.KickbaseException` with a message naming the offending value. Both must be
positive integers. `BEP_GROWTH_DAYS` is additionally capped at 364, since the history
holds 365 entries and a larger window can never be filled.

`entrypoint.py` adds both to its boot checks, next to `START_MONEY`. `.env.example`
documents both, including that changing them changes what the table recommends.

### Backend: the growth average

**`miscellaneous.average_daily_growth(history, days)`** returns
`(history[-1]["mv"] − history[-1-days]["mv"]) / days`, or `None` when the history is
shorter than `days + 1` entries. A too-short history is not a zero: a player added to
the competition last week has no 30-day pace, and claiming one of zero would rank them
alongside a genuinely stagnant player.

`market_value_deltas()` is left alone. It feeds `market_value_changes.json` as well, and
that file has no use for the new field.

**`market()`** gains `avgDailyGrowth` per row, computed from the history it already
fetches for the deltas. No additional API calls.

### Backend: the two new row fields

```json
{ "playerId": "49", "isOwnListing": false, "avgDailyGrowth": 14705, ... }
```

- **`playerId`** — `player.id`. The row is not addressable today; without it no request
  can name a player.
- **`isOwnListing`** — `player.userId == own_user_id`. You cannot bid on a player you
  listed yourself.
- **`avgDailyGrowth`** — as above, `null` when the history is too short.

### Backend: the config file

**`frontend/src/data/config.json`**, written by `main()`:

```json
{ "bepGrowthDays": 3, "bepTargetDays": 3 }
```

Two constants do not belong in all 120 market rows, and `App.js` needs them for the
`HelpIcon` text without importing `market.json`. Per-player data stays in `market.json`;
configuration sits beside it.

Per `CLAUDE.md` this is another runtime-generated file a fresh worktree must copy from
the main checkout before `npm run build` or `npm start`. It is covered by the existing
`*.json` rule in `.gitignore`.

### Backend: the write calls

New in `backend/kickbase/v4/leagues.py`:

```python
place_offer(token, league_id, player_id, price)      # POST   .../market/{playerId}/offers
remove_offer(token, league_id, player_id, offer_id)  # DELETE .../market/{playerId}/offers/{offerId}
```

Exact paths and the `offer_id` parameter are subject to the probe above.

Both depart from the surrounding module on purpose. The existing 15 call sites wrap a
bare `except:` around `.json()` and raise
`NotificatonException("Notification failed! Please check your Discord Webhook URL.")`,
which is wrong for every one of them and unusable for a bid: a rejected bid must say why
it was rejected. These two check the status code, parse the API's error message and raise
a new `exceptions.KickbaseWriteException` carrying both. Both also pass a timeout —
today not a single Kickbase call has one, so one hung socket parks the caller forever.

**`own_offer_id(own_user_id)`** on `Market_Players`, next to the existing `own_offer()`
and sharing a private `_own_offer_entry()` helper with it, so the "never read a foreign
bid as ours" check exists once rather than twice. Returns `None` when the entry carries
no id — which the probe may show is always.

### Backend: the endpoints

Both in `app.py`, both logging in per request as `/api/livepoints` already does.

| Route | Steps |
| --- | --- |
| `POST /api/market/<player_id>/bid` | login → league → validate → `place_offer` → re-read market → `own_offer()` → patch `market.json` → `{"ownBid": n}` |
| `DELETE /api/market/<player_id>/bid` | login → league → read market → find own offer → `remove_offer` → patch `market.json` → `{"ownBid": null}` |

Validation on POST, server-side and not merely in the browser: `price` must be a
positive integer; the player must currently be on the market; the listing must not be the
user's own. Each failure answers 4xx with a German message the cell can display
verbatim.

**Patching `market.json`** means reading the file, finding the row by `playerId`,
setting `ownBid` and writing it back through `write_json_to_file()`. An unknown
`playerId` writes nothing and answers 404.

Two costs are accepted rather than solved here:

- **The patch can race a concurrent `main.py` write.** Non-atomic JSON writes are an
  open item in `docs/improvement-plan-2026-08-12.md` (cluster B) affecting every write in
  the project; the next scrape repairs the row either way.
- **Every bid costs a fresh login.** `app.py` already logs in per request. Token caching
  is likewise its own item in that plan.

Both are named so the next reader knows they were seen and deferred, not missed.

### Frontend: the formulas

`marketFormulas.js`, after the refactor:

```js
usableGrowth({ avgDailyGrowth })   // the growth, or null — the guard both columns share

daysToBreakEven({ marketValue, price, avgDailyGrowth })   // (price − mv) / growth, floored at 0
breakEvenBid(row, targetDays)                             // marketValue + days · growth
```

A separate `projectedMarketValue` would be a second name for `breakEvenBid` with exactly
one caller, so the projection is written inside it rather than beside it.

The averaging leaves the frontend: it only ever lived here because the three fields it
needed happened to be present, and it cannot express any other window. What remains is
one guard — missing market value, missing history, growth ≤ 0 all give `null` — and the
two readings of the same line. "Tage bis BEP" keeps its dash and its nulls-last sort
unchanged.

`targetDays` is a parameter rather than a module constant so the functions stay testable
across horizons. `MarketTable` passes `bepTargetDays` from `config.json`; nothing in
`marketFormulas.js` reads configuration itself.

`App.js:159` describes "Dein Gebot" as display-only and hardcodes "letzten drei Tage" in
the `HelpIcon` text. Both are rewritten from `config.json`, so `BEP_GROWTH_DAYS=7` does
not leave the help text talking about three days.

### Frontend: the cell

New `BidCell.js`, rendered by the `ownBid` column:

```
idle, no bid          1.180.000          grey, clickable
idle, bid placed      1.250.000 (+8 %)   as today
idle, own listing     (empty)            locked, tooltip
editing               [1.180.000] ✓ ✗
in flight             [1.180.000]  ⟳
```

- **Click** opens a `NumericFormat` input (German thousands separators, following
  `LineupPlanner.js:171`), prefilled with the running bid, else the suggestion, else
  empty, with the text selected.
- **✓ / Enter** submits. At or above twice the suggestion — twice the market value when
  there is no suggestion — a confirmation dialog first. An extra digit is always a factor
  of ten, so this threshold catches that typo on a 500.000 € player and on a 12 M € one
  alike, which a fixed euro threshold does not.
- **✗ / Escape** withdraws the bid when one is placed, otherwise cancels editing.
- **Failure** shows the Kickbase message in a snackbar and leaves the cell in editing
  state with the typed value intact.
- **No API reachable** is its own message, not a generic failure. `main.py` can be run
  without `app.py`, and that is the common local setup, so a bid attempt then fails on a
  network error rather than an HTTP status. The snackbar names the cause — the Flask API
  is not running — because "Gebot fehlgeschlagen" would send you looking at Kickbase.
- The tooltip on the grey value explains the calculation and notes when the suggestion
  is *below* the asking price — the bid is valid then, but the seller is unlikely to take
  it.

Editing state lives in `MarketTable`, not in the cell: `renderCell` re-runs on scroll and
on sort, and state held inside the cell would not survive either. `MarketTable` also
holds a `{playerId: ownBid}` override map, merged over the row data, so a confirmed bid
shows immediately without waiting for the rebuild the `market.json` patch triggers.

### Frontend: reaching Flask

`"proxy": "http://localhost:5000"` in `frontend/package.json`; requests go to relative
`/api/...` paths. No new environment variable, no CORS.

## Testing

- **`marketFormulas.test.js`** — existing `daysToBreakEven` cases ported to
  `avgDailyGrowth`; `breakEvenBid` for positive, zero, negative and missing growth and a
  missing market value; and the inversion property: `daysToBreakEven` given
  `breakEvenBid` as its price returns the target horizon, checked across several
  horizons.
- **`tests/test_bep_config.py`** (new) — `get_bep_days()` defaults, non-integers,
  zero, negative, the 364 cap; `average_daily_growth()` against a real history including
  the telescoping identity at `days=3`, and `None` for a too-short history.
- **`tests/test_market_bid.py`** (new) — request construction and error parsing for
  `place_offer`/`remove_offer` against recorded responses; `own_offer_id()` including the
  mirror fallback and the foreign-bid rejection; the `market.json` patch, including that
  an unknown `playerId` leaves the file untouched; and each server-side validation rule.

Both Python files are standalone scripts run as `./venv/bin/python tests/<name>.py`,
following `tests/test_start_date.py`.

## Out of scope

Accepting or declining offers on your own listings, listing a player, and any other
write action. Atomic JSON writes and auth token caching, both already tracked in
`docs/improvement-plan-2026-08-12.md`. Runtime JSON fetching to replace the build-time
imports — the largest item in that plan, and the reason a confirmed bid needs a local
override at all.
