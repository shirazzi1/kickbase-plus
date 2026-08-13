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

### Where the datasets the browser reads are written.
###
### These used to live in frontend/src/data, which meant the React build imported them at
### compile time and only a create-react-app dev server recompiling in production could
### ever show fresh numbers. app.py serves this directory read-only under /api/data/<name>
### instead, so a write here reaches the browser on its next fetch and nothing is rebuilt.
###
### Under "data" with everything else that outlives a container: not because these files
### cannot be fetched again - the next run rewrites all of them - but because a mount that
### covers data/ then also covers the moment between "the image was pulled" and "the first
### run finished", where the alternative is an empty dashboard.
PUBLIC_DIR = path.join(BASE_PATH, "data", "public")
TIMESTAMP_DIR = path.join(PUBLIC_DIR, "timestamps")

### Where the datasets only the backend reads are written. See backend/datasets.py for
### which those are and why each one is in this list.
###
### They were in frontend/src/data too, where nothing imported them and writing them still
### triggered a webpack rebuild. Keeping them out of PUBLIC_DIR is also what makes the
### /api/data allowlist trustworthy: a file that is not in the served directory cannot be
### served by mistake. all_transfers.json in particular is a season of the activity feed.
STATE_DIR = path.join(BASE_PATH, "data", "state")

### Where both of the above used to live. Read only by the one-time migration at startup
### (see backend/state_migration.py); nothing else may reach for it.
LEGACY_DATA_DIR = path.join(BASE_PATH, "frontend", "src", "data")

### Where the previous good copy of each data file is kept, so a run that succeeds but
### writes rubbish can still be compared against what stood there before.
LAST_GOOD_DIR = path.join(BASE_PATH, "data", "last-good")

### Where the append-only history of the datasets accumulates: one NDJSON line per run,
### one file per dataset per day.
###
### Nothing in the frontend reads these; they are the raw material for diffing snapshots
### against each other, they gain a line six times a day and they never shrink.
###
### Shares the "data" parent with everything else on purpose, so a single volume mount of
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

### Which events the diff engine has already announced on Discord (see backend/events.py).
###
### The events themselves are rebuilt from the history store on every run, so this file is
### the only thing that knows an alert has been sent - without it the same "Preis gesenkt"
### message goes out six times a day. Which is why it sits in the same "data" parent as the
### history store and the .last-good snapshots: one volume mount, and everything that has to
### survive an image pull survives it.
EVENTS_STATE_PATH = path.join(BASE_PATH, "data", "events-state.json")
