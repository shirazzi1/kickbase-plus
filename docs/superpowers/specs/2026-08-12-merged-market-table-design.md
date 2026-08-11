# Merged transfer market table

Date: 2026-08-12

## Problem

The transfer market is split across two tables, "Transfermarkt (Kickbase)" and
"Transfermarkt (Spieler)". Deciding what to bid on means comparing rows across both.
Neither table shows whether a bid is already placed, and the only market value signal
is a three-state `Trend` arrow, which says less than the daily deltas the
"Marktwertveränderungen" table already computes for every player in the league.

Separately, every table is hard-capped at ten rows per page. The market alone has 104
entries, so scanning it takes eleven clicks.

## Evidence from the live API

Probed against league `Kickbase-Elite 26/27` on 2026-08-12, 104 players on the market.

### Own bids: `ofs`

A market item carries an `ofs` list. Only *your* offers appear in it — other managers'
bids are not exposed:

```json
"ofs": [{"u": "3854976", "unm": "shirazzi", "uoid": "3854976",
         "uop": 5222222, "st": 0, "uim": "user/91fd....jpe"}]
```

The same price is mirrored on the item as top-level `uop`, with `uoid` naming the
bidder. Both sources appeared together on all items carrying a bid, so `ofs` is the
primary read and top-level `uop` the fallback. `ofc` is the offer count.

Identifying the entry requires the own user id, which `main.login()` currently
discards.

### Asking price vs market value

`prc` is the asking price, `mv` the current market value. For all 20 Kickbase-listed
players `prc == mv`; for user listings they differ (Ginter: `prc` 32,000,000 against
`mv` 26,260,331, +21.9%). The bid surcharge is therefore computed against `mv`.

### Status note: `stxt`, and its language

The player profile (`/competitions/1/players/{id}`) carries `stxt` only when the
player is not fit:

| Player | `st` | `stl` | `stxt` |
| --- | --- | --- | --- |
| Matthias Ginter | 0 | `[]` | absent |
| Robert Andrich | 2 | `[2]` | `"Muscle problems - will miss the next two friendly matches\n"` |

Note `stl` is a *list*: a player can hold several statuses at once. The frontend keys
a single `status` int into `statusIcons` with an unchecked lookup, so any code outside
that map throws and takes the whole table down.

The note defaults to English. Probing the request showed Kickbase localises it on
`Accept-Language` alone — `X-Language`, `?language=`, `?lang=` and `?locale=` are all
ignored:

| Request | `stxt` |
| --- | --- |
| no language header | `"Muscle problems - will miss the next two friendly matches"` |
| `Accept-Language: de` | `"Muskuläre Probleme - verpasst nächsten beiden Testspiele"` |
| `?lang=de` | English, unchanged |

`player_statistics()` therefore sends `Accept-Language: de-DE,de;q=0.9`. A test asserts the
header, because without it the tooltip silently reverts to English.

### Expiry is free-agent only

`exs` is present on 20/20 Kickbase-listed players and 0/84 user-listed ones. This is
why `expiration` is `null` for every row in today's `market_user.json`. The API no
longer exposes a deadline for user listings, so that column stays empty for them.

### Cost of the extra data

`market()` currently calls neither `player_statistics()` nor `player_marketvalue()`.
Both are cached per run, and `market()` runs before `market_value_changes()`, which
already requests both for every player in the competition. Adding them to `market()`
pulls those calls forward rather than adding them. **Net additional API calls: zero.**

## Decisions

1. **One table, one file.** `market_user.json` and `market_kickbase.json` are replaced
   by `market.json`, with an `isFreeAgent` flag per row. Free agents are the rows worth
   spotting quickly, so they get a background tint rather than a separate table.
2. **`expiration` becomes an ISO 8601 timestamp.** The current `"12.08.2026 18:31:54"`
   string sorts lexicographically, so a `dd.mm.yyyy` value orders wrongly across month
   boundaries — and the table's default sort is by that column. Formatting moves to the
   frontend.
3. **Deltas are computed in one place.** `market()` and `market_value_changes()` share
   a helper instead of duplicating the index arithmetic.
4. **Full width with a ceiling.** The container grows from 1000px to 1800px. Fifteen
   columns need the room; an unbounded width would spread the table across an
   ultrawide monitor.
5. **Empty rather than substituted.** No "listed since" fallback in the expiry column
   for user listings. An absent deadline reads more honestly as absent.

## Design

### Backend

**`miscellaneous.market_value_deltas(history)`** returns `today`, `yesterday`,
`twoDays`, `sevenDaysAvg`, `thirtyDaysAvg` from a market value history. Each delta the
history is too short for is `None`. The existing code in `market_value_changes()`
guards only the 7- and 30-day values, so `history[-4]` on a newly added player raises
`IndexError` and kills the run; the helper guards every delta. The existing JSON key
names carry over unchanged, so `MarketValueChangesTable` needs no edit beyond its
pagination.

**`main.login()`** returns `(selected_league, user_token, own_user_id)`. `app.py` calls
`user.login()` directly and is unaffected.

**`Market_Players`** gains `ownOfferPrice` (`uop`) and `ownOfferUserId` (`uoid`).

**`market(user_token, selected_league, own_user_id)`** writes one row per market
player to `market.json`:

```json
{ "teamId": "5", "position": "ABW", "firstName": "Matthias", "lastName": "Ginter",
  "status": 0, "statusText": null,
  "marketValue": 26260331, "price": 32000000, "ownBid": null,
  "today": 40231, "yesterday": 12004, "twoDays": -8120,
  "sevenDaysAvg": 96400, "thirtyDaysAvg": 1204000,
  "seller": "shirazzi", "isFreeAgent": false, "expiration": null }
```

- `ownBid`: `uop` from the `ofs` entry whose `u`/`uoid` matches the own user id;
  falling back to top-level `uop` when `uoid` matches. `None` otherwise. A bid by
  another manager must never be read as one of ours.
- `statusText`: `stxt` from `player_statistics()`, stripped; `None` when absent.
- `seller`: the listing user's name, or `"Kickbase"` for free agents.
- `expiration`: `now + exs` seconds as an ISO 8601 UTC string; `None` without `exs`.

Timestamp file: `ts_market.json`.

### Frontend

**`MarketTable.js`** replaces `MarketTableUser.js` and `MarketTableKickbase.js`.
Columns, with content-sized fixed widths and `Verkäufer` absorbing the remainder:

| Column | Field | Width |
| --- | --- | --- |
| Team | `teamLogo` | 60 |
| Position | `position` | 80 |
| Spieler | `firstName` + `lastName` | 200 |
| Status | `status` + `statusText` | 70 |
| Marktwert | `marketValue` | 120 |
| Preis | `price` | 120 |
| Aufpreis | `price / marketValue - 1` | 100 |
| Dein Gebot | `ownBid` | 175 |
| Heute | `today` | 110 |
| Gestern | `yesterday` | 110 |
| Vorgestern | `twoDays` | 115 |
| 7 Tage | `sevenDaysAvg` | 110 |
| 30 Tage | `thirtyDaysAvg` | 120 |
| Verkäufer | `seller` | flex 1 |
| Ablaufdatum | `expiration` | 150 |

First and last name are one column: two columns for one identity wasted the width the
delta columns need. Players without a first name in the API must not render a leading
space, so the parts are filtered before joining.

**Aufpreis** is what the asking price adds on top of the market value, red above and
green below. It is always 0 % for free agents, where Kickbase asks exactly the market
value. Its classes are deliberately not the delta ones, whose CSS prepends a `+` that
the percent formatter already supplies.

`Trend` is gone; the five delta columns take its place, coloured green/red with a `+`
prefix, reusing the class pattern from `MarketValueChangesTable`.

**Dein Gebot** renders `5.222.222 € (+2,3 %)`, the percentage relative to
`marketValue`. Empty when no bid is placed. `Marktwert` is shown so that percentage is
verifiable.

**Free agents** are tinted via `getRowClassName` and
`alpha(theme.palette.info.main, 0.12)`, resolved through `useTheme` so the tint holds
in dark mode.

**Status tooltip** shows the status label followed by `statusText` when present.
`statusIcons[value]` gets a fallback for unmapped codes.

**Default sort:** `expiration` ascending, `type: 'dateTime'` over a parsed `Date`, with a
`sortComparator` that treats a missing deadline as `Infinity`. MUI orders `null` *first*
ascending, which buried the 20 listings that actually run out beneath 84 that never do.
Sorting them last beats writing a sentinel date like 31.12.2999 into the data, which
would then have to be rendered as something other than what it says.

### Pagination

**`PagedDataGrid.js`** wraps `DataGrid` with `autoHeight`, a `pageSize` state
defaulting to **100**, and a custom `Pagination` component built on `TablePagination`
plus `useGridApiContext`. Options are **25 / 50 / 100 / Alle**; the custom component is
what makes the "Alle" label possible, since v5's `rowsPerPageOptions` takes plain
numbers and would render "104". All other props pass through.

**"Alle" is capped at 100 rows, which is a paid-tier gate rather than a choice.** The MIT
`DataGrid` throws `'props.pageSize' cannot exceed 100 in DataGrid` with the message "Only
page size below 100 is available in the MIT version. You need to upgrade to DataGridPro",
and `DATA_GRID_FORCED_PROPS` spreads `pagination: true` last, so pagination cannot be
switched off instead. `MAX_PAGE_SIZE = 100` is identical in v5.17, v6.20, v7.29 and v9.11,
so upgrading would not lift it. The guard therefore offers "Alle" only where it fits: the
104-row market table tops out at 100 per page, while every table under 100 rows gets a
real "Alle". The three fixed sizes are always offered so each table carries the same
control, and "Alle" is skipped when the row count is already one of them, which would put
two options with the same value into the select.

The cap is only checked against the *props*; the internal `apiRef.current.setPageSize()`
has no such guard, so an uncontrolled page size could route around it. That is
deliberately circumventing a paid-feature gate and is left to the project owner to
decide, not taken silently.

Applied to `MarketTable`, `MarketValueChangesTable`, `FreePlayersTable`,
`TakenPlayersTable`, `TurnoversTable`, `LeagueUserTable`, `SeasonStatsTable` and
`Balances`. Not applied to `LineupPlanner` (18 rows is the squad size), `Battles`
(already shows every user) or `LivePoints` (disabled).

### App.js

Container `maxWidth: "1000px"` becomes `maxWidth: "1800px", width: "100%"`. The two
market `Paper` blocks collapse into one titled "Transfermarkt", with help text covering
both listing sources. The "Market User" and "Market Kickbase" timestamp lines become a
single "Market" line reading `ts_market.json`.

## Testing

`tests/test_market_table.py`, following the existing suite's shape — stub API
responses, no network, run as `./venv/bin/python tests/test_market_table.py`:

- deltas over a full history, and `None` for each delta a short history cannot cover
- own bid read from `ofs`
- own bid read from top-level `uop` when `ofs` is absent
- another manager's bid is **not** reported as ours
- `statusText` is `None` for a fit player and stripped for an injured one
- `isFreeAgent` set from the absence of a listing user, `seller` reading `"Kickbase"`
- `expiration` is `None` without `exs`

Because `.gitignore` excludes `*.json`, the frontend's data files are runtime
artefacts. `market.json` and `ts_market.json` are generated by a real `market()` run at
the end; without them the static imports break the frontend build.

## Side fix: the "Compiled with problems" overlay

Verifying the table in a browser surfaced a pre-existing break that had nothing to do
with this work but sat on top of it. Every page load showed a red dev-server overlay:

```
[eslint] Failed to load plugin 'jest' declared in 'package.json » eslint-config-react-app/jest':
        Cannot read properties of undefined (reading 'Any')
[eslint] package.json » eslint-config-react-app/jest#overrides[0]:
        Environment key "jest/globals" is unknown
```

It also failed `npm run build` outright. `entrypoint.py` serves the frontend with
`npm start`, so the deployed container carried the overlay too.

The cause is not ESLint. `@typescript-eslint/type-utils@5.62.0`, pulled in transitively by
`eslint-config-react-app`, evaluates `ts.TypeFlags.Any | ts.TypeFlags.Unknown` at import
time. The installed TypeScript was **7.0.2**, where that is gone, so `ts.TypeFlags` was
`undefined`, the jest plugin failed to load, and its `jest/globals` environment was never
registered. `npm ls` flagged it directly: `typescript@7.0.2 invalid: "^3.2.1 || ^4" from
node_modules/react-scripts`. Moving `node_modules/typescript` aside made the plugin load,
which confirmed it.

The project has no TypeScript sources and does not declare typescript at all — npm had
pinned 7.0.2 in the lockfile as an auto-installed optional peer, so every `npm install`
reinstated it. Fixed with an `overrides` entry rather than a dependency, since nothing here
depends on typescript; only the toolchain's resolution of it needs bounding:

```json
"overrides": { "typescript": "^4.9.5" }
```

`^4.9.5` satisfies both react-scripts' `^3.2.1 || ^4` and @typescript-eslint 5, which needs
TypeScript below 5.1. `npm run build` now compiles with ESLint active and no warnings.
(`package.json` allows no comments, and npm rejects a `"//"` key inside `overrides`, which
is why the reasoning lives here.)

## Out of scope

- No deadline for user listings. The API does not provide one.
- `stl` (multiple simultaneous statuses) is not rendered. The unmapped-code fallback
  keeps it from crashing; showing several icons per player is a separate change.
- No DataGrid upgrade. Real column auto-sizing arrived in v6.5, but migrating all
  twelve `DataGrid` call sites is a larger and riskier change than this work needs.
