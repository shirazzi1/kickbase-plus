"""
### Which dataset goes where, and which ones the browser is allowed to ask for.

Two directories, one rule. `backend/miscellaneous.py::write_json_to_file()` routes every
write through `dataset_kind()`, and `app.py` serves exactly `PUBLIC_DATASETS` under
`/api/data/<name>`. Both read the same lists, which is the point of this module: an
allowlist that lives next to the writer cannot drift away from it, and a file that is not
in `PUBLIC_DIR` cannot be served out of it by accident.

Adding a dataset means adding it to one of the two sets below. A name in neither is a hard
error at write time rather than a silent guess - guessing "public" would publish a file
nobody meant to publish, and guessing "state" would leave a table permanently empty.

This module deliberately answers with a *kind* rather than with a path. The directories are
module level names in the modules that use them, which is how every test in `tests/` points
a run at a temporary directory; resolving the path here would route writes past those
patch points and back to the real `data/`.
"""

from backend.paths import PUBLIC_DIR, STATE_DIR, TIMESTAMP_DIR

### ===============================================================================

### The datasets a browser reads. Every one of these is fetched by a component at runtime;
### the list is checked against the frontend's own contract table in
### tests/test_data_plane.py, so a dataset the UI reads and this set does not name fails a
### test rather than a fetch in production.
PUBLIC_DATASETS = frozenset({
    "market.json",
    "market_value_changes.json",
    "taken_players.json",
    "free_players.json",
    "turnovers.json",
    "revenue_sum.json",
    "team_values.json",
    "league_user_stats.json",
    "balances.json",
    "events.json",
    "manager_profiles.json",
    ### Read by the live swing meter for the kickoff windows of the current match day.
    ### Backend-private when the old plan was written, public since PR #13.
    "match_days.json",
    ### Written by app.py's /api/livepoints rather than by a scheduled run, so it is
    ### frequently absent. The Live tab renders that as an empty state.
    "live_points.json",
})

### The datasets no browser ever sees.
###
###   - all_transfers.json: the whole season's activity feed, the watermark the feed is
###     paged against, and the source the dossier and the balances are derived from.
###   - achievements.json: when each manager earned which achievement, used to keep the
###     estimated bonuses stable across runs.
###   - STATIC_users.json / STATIC_teams.json: the id-to-name tables five later stages open.
###     Names and ids of everyone in the league, which is exactly what does not need an
###     HTTP route.
STATE_DATASETS = frozenset({
    "all_transfers.json",
    "achievements.json",
    "STATIC_users.json",
    "STATIC_teams.json",
})

### The prefix that marks a timestamp file. They all land in TIMESTAMP_DIR and are served
### together as one index (see /api/data/timestamps), because the frontend wants all of
### them or none: a per-tab freshness marker is only meaningful next to the run manifest.
TIMESTAMP_PREFIX = "ts_"

### The three kinds of file, as answers rather than as paths. See the module docstring.
PUBLIC = "public"
STATE = "state"
TIMESTAMP = "timestamp"


class UnknownDatasetError(Exception):
    """### A dataset name that is in neither list.

    Raised rather than defaulted. See the module docstring for why neither default is safe.
    """


def is_timestamp(file_name: str) -> bool:
    """### Whether a file name is a timestamp rather than a dataset.

    Args:
        file_name (str): The file name, e.g. "ts_market.json".

    Returns:
        bool: True for timestamp files.
    """
    return file_name.startswith(TIMESTAMP_PREFIX)


def dataset_kind(file_name: str) -> str:
    """### Which of the three kinds a file name is.

    Args:
        file_name (str): The file name, e.g. "market.json".

    Returns:
        str: TIMESTAMP, PUBLIC or STATE.

    Raises:
        UnknownDatasetError: For a name in neither PUBLIC_DATASETS nor STATE_DATASETS.
    """
    if is_timestamp(file_name):
        return TIMESTAMP

    if file_name in PUBLIC_DATASETS:
        return PUBLIC

    if file_name in STATE_DATASETS:
        return STATE

    raise UnknownDatasetError(
        f"{file_name} is in neither PUBLIC_DATASETS nor STATE_DATASETS. Add it to "
        "backend/datasets.py - whether the frontend reads it decides which list.")


def dataset_dir(file_name: str) -> str:
    """### The real directory a dataset belongs in.

    For the migration and for app.py, which both work on the deployment's actual
    directories rather than on a test's temporary ones. Everything that writes during a run
    goes through dataset_kind() instead - see the module docstring.

    Args:
        file_name (str): The file name, e.g. "market.json".

    Returns:
        str: TIMESTAMP_DIR, PUBLIC_DIR or STATE_DIR.

    Raises:
        UnknownDatasetError: For a name in neither PUBLIC_DATASETS nor STATE_DATASETS.
    """
    return {
        TIMESTAMP: TIMESTAMP_DIR,
        PUBLIC: PUBLIC_DIR,
        STATE: STATE_DIR,
    }[dataset_kind(file_name)]
