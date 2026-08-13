"""
### This module holds the paths for all logs and data files.

These live here instead of in `main.py` so that both `main.py` and the modules it
imports (e.g. `backend/miscellaneous.py`) can use them without a circular import.
"""

from os import path, getcwd

### ===============================================================================

### Get the current working directory dynamically
### NOTE: This means main.py/app.py have to be run from the root of the repository.
BASE_PATH = getcwd()
### Paths for logs and data files
LOG_DIR = path.join(BASE_PATH, "logs")
DATA_DIR = path.join(BASE_PATH, "frontend", "src", "data")
TIMESTAMP_DIR = path.join(DATA_DIR, "timestamps")

### Where the previous good copy of each data file is kept, so a run that succeeds but
### writes rubbish can still be compared against what stood there before.
###
### Deliberately outside DATA_DIR. Everything under frontend/src is watched by the
### create-react-app dev server that serves this project in production, so a second file
### next to every dataset would double the rebuilds without a single component importing
### it. Nothing in the frontend reads these.
LAST_GOOD_DIR = path.join(BASE_PATH, "data", "last-good")

### Where the append-only history of the datasets accumulates: one NDJSON line per run,
### one file per dataset per day.
###
### Outside DATA_DIR for the same reason LAST_GOOD_DIR is, only more so. Everything under
### frontend/src is watched by the create-react-app dev server that serves this project in
### production, and this store gains a line six times a day and never shrinks - watching it
### would mean a rebuild per append over a file that grows all season. Nothing in the
### frontend reads these; they are the raw material for diffing snapshots against each
### other.
###
### Shares the "data" parent with LAST_GOOD_DIR on purpose, so a single volume mount of
### /code/data makes everything that has to survive an image pull survive it.
HISTORY_DIR = path.join(BASE_PATH, "data", "history")

### Where the market value curves are kept between runs: one file per player, holding the
### curve plus the token that says whether it is still current. See
### backend/market_value_cache.py for the format and what invalidates an entry.
###
### Under "data" like the two above, and for a third reason: this one only pays off if it
### survives the image pull. A cache that is empty on every start re-downloads the same 466
### curves six times a day, which is exactly the traffic it exists to avoid.
MARKET_VALUE_CACHE_DIR = path.join(BASE_PATH, "data", "market-values")

### Where the ids of the teams in a competition are remembered, so the next run does not
### have to find them by probing 97 ids to discover 18. One file per competition, refreshed
### on a daily clock. See backend/kickbase/v4/competitions.py.
TEAM_CACHE_DIR = path.join(BASE_PATH, "data", "teams")
