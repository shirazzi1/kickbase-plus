"""Tests fuer die Saison-Gesamtpunkte in taken_players.json.

`player_statistics()` liefert "tp", die Gesamtpunkte der Saison. Die Response wird ohnehin
pro Spieler geholt, um den Besitzer zu bestimmen — das Feld kostet also keinen Request.
Weil taken_players historisiert wird (miscellaneous.HISTORICISED_DATASETS), ergibt die
Differenz zweier aufeinanderfolgender Snapshots die Punkte eines Spieltags pro Spieler.
Genau das faellt weg, sobald das Feld wieder verschwindet, deshalb diese Tests.

Die freien Spieler tragen dasselbe "tp" seit jeher unter dem Namen "points"
(FreePlayersTable.js liest darauf). Die Benennung weicht also bewusst ab; der letzte Test
haelt beide Namen fest, damit ein Umbenennen eine Entscheidung bleibt und kein Versehen.

Dependency free wie der Rest: kein Test-Framework, laeuft mit dem Projekt-venv.

    ./venv/bin/python tests/test_taken_player_points.py
"""

import json
import sys
import tempfile

from os import environ, makedirs, path

### Make the repository root importable regardless of where this is run from
sys.path.insert(0, path.dirname(path.dirname(path.abspath(__file__))))

from backend import miscellaneous

### ===============================================================================

LEAGUE_ID = "11412166"

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


### Ein Spieler pro Lage: einer gehoert Meier, einer niemandem. Die "opl"-Eintraege sind die
### Form, die die API wirklich zurueckgibt — ein freier Spieler steht dort mit Besitzer "0".
def owned_stats(total_points=None):
    stats = {
        "i": "14300",
        "fn": "Paul",
        "ln": "Okon-Engstler",
        "oui": "0",
        "opl": [{"li": LEAGUE_ID, "oui": "2592773", "onm": "Meier"}],
    }
    if total_points is not None:
        stats["tp"] = total_points
    return stats


def free_stats(total_points=None):
    stats = {
        "i": "173",
        "fn": "Jonathan",
        "ln": "Tah",
        "oui": "0",
        "opl": [{"li": LEAGUE_ID, "oui": "0"}],
    }
    if total_points is not None:
        stats["tp"] = total_points
    return stats


def run_taken_free_players(stats_by_id):
    """### Run taken_free_players() against stubbed API calls.

    Args:
        stats_by_id (dict): The player_statistics() response per player id.

    Returns:
        tuple: (taken_players, free_players, history_lines) — die geschriebenen Dateien und
            die NDJSON-Zeilen, die der History-Store fuer taken_players angelegt hat.
    """
    import main
    from backend.kickbase.v4 import leagues

    teams = [{
        "teamId": "28",
        "teamName": "Koeln",
        "players": [
            {"i": "14300", "n": "Okon-Engstler", "pos": 3, "mv": 3062573, "st": 0, "mvt": 1, "tid": "28"},
            {"i": "173", "n": "Tah", "pos": 2, "mv": 36549128, "st": 0, "mvt": 1, "tid": "2"},
        ],
    }]

    with tempfile.TemporaryDirectory() as tmp:
        data_dir = path.join(tmp, "data")
        ts_dir = path.join(data_dir, "timestamps")
        history_dir = path.join(tmp, "history")
        makedirs(ts_dir, exist_ok=True)

        with open(path.join(data_dir, "STATIC_users.json"), "w") as f:
            json.dump({"2592773": "Meier"}, f)
        with open(path.join(data_dir, "STATIC_teams.json"), "w") as f:
            json.dump(teams, f)

        original = (main.PUBLIC_DIR, main.STATE_DIR,
                    miscellaneous.PUBLIC_DIR, miscellaneous.STATE_DIR, miscellaneous.TIMESTAMP_DIR,
                    miscellaneous.LAST_GOOD_DIR, miscellaneous.HISTORY_DIR,
                    leagues.transfers, leagues.player_statistics, leagues.player_marketvalue)
        main.PUBLIC_DIR = data_dir
        main.STATE_DIR = data_dir
        miscellaneous.PUBLIC_DIR = data_dir
        miscellaneous.STATE_DIR = data_dir
        miscellaneous.TIMESTAMP_DIR = ts_dir
        miscellaneous.LAST_GOOD_DIR = path.join(tmp, "last-good")
        miscellaneous.HISTORY_DIR = history_dir

        try:
            environ["START_DATE"] = "2026-08-01T18:00:00Z"
            leagues.transfers = lambda token, lid: []
            leagues.player_statistics = lambda token, lid, pid: stats_by_id[str(pid)]
            leagues.player_marketvalue = lambda token, pid: []

            class FakeLeague:
                id = LEAGUE_ID
                name = "Kickbase-Elite 26/27"

            main.taken_free_players("token", FakeLeague())

            with open(path.join(data_dir, "taken_players.json")) as f:
                taken = json.load(f)
            with open(path.join(data_dir, "free_players.json")) as f:
                free = json.load(f)

            history = []
            history_path = miscellaneous.history_file_path("taken_players")
            if path.exists(history_path):
                with open(history_path) as f:
                    history = [json.loads(line) for line in f if line.strip()]

            return taken, free, history
        finally:
            (main.PUBLIC_DIR, main.STATE_DIR,
             miscellaneous.PUBLIC_DIR, miscellaneous.STATE_DIR, miscellaneous.TIMESTAMP_DIR,
             miscellaneous.LAST_GOOD_DIR, miscellaneous.HISTORY_DIR,
             leagues.transfers, leagues.player_statistics,
             leagues.player_marketvalue) = original


### ===============================================================================
### taken_free_players() — Punkte pro Spieler
### ===============================================================================


def test_taken_player_carries_the_season_points():
    stats = {"14300": owned_stats(421), "173": free_stats(87)}
    taken, _, _ = run_taken_free_players(stats)

    assert taken[0]["totalPoints"] == 421, \
        f"expected the 'tp' of the statistics response, got {taken[0]}"


def test_missing_tp_becomes_zero():
    """Ein fehlendes "tp" darf keinen Lauf kosten — 0 statt KeyError, wie bei free_players."""
    stats = {"14300": owned_stats(), "173": free_stats()}
    taken, _, _ = run_taken_free_players(stats)

    assert taken[0]["totalPoints"] == 0, \
        f"a statistics response without 'tp' must yield 0, got {taken[0]}"


def test_zero_points_stay_zero():
    """0 ist ein echter Wert (noch nicht gespielt), kein 'fehlt'."""
    stats = {"14300": owned_stats(0), "173": free_stats(0)}
    taken, _, _ = run_taken_free_players(stats)

    assert taken[0]["totalPoints"] == 0, f"expected 0 points, got {taken[0]}"


def test_the_history_snapshot_carries_the_points():
    """Der ganze Zweck: erst im historisierten Snapshot werden Differenzen moeglich."""
    stats = {"14300": owned_stats(421), "173": free_stats(87)}
    _, _, history = run_taken_free_players(stats)

    assert len(history) == 1, f"expected exactly one history line, got {len(history)}"

    rows = history[0]["rows"]
    assert rows and rows[0].get("totalPoints") == 421, \
        f"without the points in the snapshot there is nothing to subtract, got {rows}"


def test_free_players_keep_the_name_points():
    """Die Benennung weicht bewusst ab: das Frontend liest 'points' auf freien Spielern."""
    stats = {"14300": owned_stats(421), "173": free_stats(87)}
    taken, free, _ = run_taken_free_players(stats)

    assert free[0]["points"] == 87, \
        f"free players must keep their long standing 'points' field, got {free[0]}"
    assert "points" not in taken[0], \
        f"taken players carry the value as 'totalPoints', not as 'points', got {taken[0]}"


### ===============================================================================

if __name__ == "__main__":
    print("taken_free_players() points")
    check("a taken player carries the season points", test_taken_player_carries_the_season_points)
    check("a missing 'tp' becomes 0", test_missing_tp_becomes_zero)
    check("zero points stay zero", test_zero_points_stay_zero)
    check("the history snapshot carries the points", test_the_history_snapshot_carries_the_points)
    check("free players keep the name 'points'", test_free_players_keep_the_name_points)

    total, passed = len(PASSED), sum(PASSED)
    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)
