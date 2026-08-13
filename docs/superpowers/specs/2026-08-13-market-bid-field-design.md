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

## Evidence from the live API

Probed against league `Kickbase-Elite 26/27` on 2026-08-13. 92 players on the market,
none of them carrying an offer when the probe started.

### The offer keys exist only while an offer does

A listing with no offer on it carries no `ofs`, no `uop` and no `uoid` at all. Its full
key set:

```
dt exs fn i iposl isn mv mvt n ofc pim pos prc prob st tid
```

So the read-only step could not observe the offer shape — there was nothing to observe.
The round trip below had to create one first, which is why it stopped being optional.

### Placing: `POST .../offers` with `{"price": N}`

| Request | Response |
| --- | --- |
| `POST /v4/leagues/{lid}/market/{pid}/offers` + `{"price": 1271013}` | `200` `{"ofi":"3854976"}` |
| same path + `{"prc": 1}` | `400` `{"err":6,"errMsg":"InvalidData","svcs":[]}` |
| `POST .../market/{pid}/offer` (singular) + `{"price": 1}` | `404` |
| the confirmed path + `{"price": 1}` | `500` `{"err":5080,"errMsg":"UnderpayNotAllowed","svcs":[]}` |

The body key is `price`. `prc` — the key a *listing* uses for its asking price — is
rejected as invalid data, so the two are not interchangeable.

`ofi` in the success body is the **logged-in user's own id**, not a separate offer
identifier.

### A rejected bid arrives as HTTP 500

`UnderpayNotAllowed` for a 1 € bid on a player worth 1.271.013 € comes back with status
**500**, not 4xx. Three consequences for the error path:

- The status cannot be handed to the browser unchanged. A bid below the market value is
  the user's doing; reporting it as a server fault would send them looking in the wrong
  place, and would bury real 500s among ordinary rejections in the logs.
- The message lives in **`errMsg`**. `err` is a numeric code (`5080`, `6`). Reading `err`
  as a message would show the user "5080".
- `errMsg` is technical English. The codes seen here need a German mapping, with `errMsg`
  as the fallback for codes not yet seen.

### Offers carry no id, and removal is keyed by user

Immediately after the successful POST, `ofs` held exactly one entry:

```json
"ofs": [{"u": "3854976", "unm": "shirazzi", "uoid": "3854976",
         "uop": 1271013, "st": 0, "uim": "user/91fd....jpe"}]
```

**No `i`.** The 2026-08-12 snapshot was not trimmed after all — v4 genuinely exposes no
per-offer id.

| Request | Response |
| --- | --- |
| `DELETE .../market/{pid}/offers` | `405` |
| `DELETE .../market/{pid}/offers/{ownUserId}` | `200` `{}` — `ofs` and `uop` gone, `ofc` back to `0` |

`405` rather than `404` on the collection says the path is right but needs an identifier
appended, and that identifier is the user's own id — the same value the POST returned as
`ofi`. A user holds at most one offer per player, so keying by user is enough.

**This retires the planned `own_offer_id()` helper entirely.** It would have read
`offer.get("i")`, always found `None`, fallen back to the collection route, and hit the
405 on every withdrawal.

### What the round trip cost

One real bid, placed and withdrawn: 1.271.013 € on Kevin Müller, the cheapest listing and
a Kickbase one, priced at exactly the market value so the bid carried no markup. The first
withdrawal attempt used the collection route and failed, so the bid stood for a few
minutes until the id-addressed route was found. Had it not been found, the fallback was
the Kickbase app.

The probe script was a throwaway in the scratchpad and is not committed.

## Decisions

1. **The suggestion is greyed-out data, not a placeholder.** It sits in the cell at 60%
   opacity, in the same tabular figures as a real bid. Sorting, however, stays on the
   *real* bid, with bid-less rows last. Mixing suggestions into the sort would make the
   column unreadable: a row's position would no longer say whether a bid exists.
2. **No suggestion means a dash, and the cell stays clickable.** When growth is flat,
   negative or the history is too short, the cell shows `–` — the same answer "Tage bis
   BEP" already gives in that case — but still opens an empty input. The tool declines to
   recommend; it does not decline to act.
3. **The offer is addressed by the user's own id, resolved server-side.** There is no
   offer id to store — v4 exposes none, as the evidence above shows. The delete path still
   reads the market fresh first, to confirm an offer of ours actually exists before asking
   Kickbase to remove one.
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
place_offer(token, league_id, player_id, price)         # POST   .../market/{playerId}/offers
remove_offer(token, league_id, player_id, own_user_id)  # DELETE .../market/{playerId}/offers/{ownUserId}
```

Both depart from the surrounding module on purpose. The existing 15 call sites wrap a
bare `except:` around `.json()` and raise
`NotificatonException("Notification failed! Please check your Discord Webhook URL.")`,
which is wrong for every one of them and unusable for a bid: a rejected bid must say why
it was rejected. These two check the status code, read the API's error message and raise
a new `exceptions.KickbaseWriteException` carrying both. Both also pass a timeout —
today not a single Kickbase call has one, so one hung socket parks the caller forever.

**Reading the error message** takes `errMsg` and never `err`, which is a numeric code.
Known codes get a German sentence, because `UnderpayNotAllowed` is not something to put
in front of a user:

```python
OFFER_ERRORS = {
    5080: "Das Gebot liegt unter dem Marktwert.",
    6: "Kickbase hat das Gebot als ungültig abgewiesen.",
}
```

Anything unmapped falls back to `errMsg`, and anything without an `errMsg` to the status.

**No `own_offer_id()`.** There is no offer id to read. `own_offer()` keeps its existing
shape, and the delete path passes the logged-in user's id straight through.

### Backend: the endpoints

Both in `app.py`, both logging in per request as `/api/livepoints` already does.

| Route | Steps |
| --- | --- |
| `POST /api/market/<player_id>/bid` | login → league → validate → `place_offer` → re-read market → `own_offer()` → patch `market.json` → `{"ownBid": n}` |
| `DELETE /api/market/<player_id>/bid` | login → league → read market → confirm an own offer exists → `remove_offer` → patch `market.json` → `{"ownBid": null}` |

Validation on POST, server-side and not merely in the browser: `price` must be a
positive integer; the player must currently be on the market; the listing must not be the
user's own. Each failure answers 4xx with a German message the cell can display
verbatim.

**A Kickbase 5xx that carries an error code is answered as 400, not passed through.**
`UnderpayNotAllowed` arrives as a 500, but the bid was the user's to get wrong; forwarding
the 500 would blame the server for it and drown genuine 500s in the logs. A 5xx *without*
an error code — a real Kickbase outage — is forwarded as 502.

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
