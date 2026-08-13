"""
### The one process a browser talks to.

Until this version there were two: a create-react-app dev server on port 3000 serving a
bundle with the data compiled into it, and this Flask app on port 5000 serving exactly one
route. Fresh data reached a browser only because the dev server noticed a JSON file change
and recompiled - in production.

Now Flask serves both halves:

  - `/api/data/<name>` hands out the datasets in `data/public`, from an allowlist rather
    than by passing a path through. `/api/data/timestamps` returns every `ts_*.json` in one
    response, which is what the per-tab freshness markers are read from.
  - everything else is the prebuilt React app from `frontend/build`.

That collapses the port split, retires flask_cors (same origin, so there is no cross origin
request left to permit) and makes the relative `fetch("/api/...")` calls in the frontend work
for the first time - there was no proxy in package.json, so they could not have.

Not part of this: binding to localhost and scoping who may reach /api/livepoints. That
endpoint performs a real Kickbase login per request, and hardening it is its own change with
its own decisions - see the note above get_live_points().
"""

import json
import logging

from glob import glob
from os import getenv, path
from flask import Flask, jsonify, request, send_from_directory

import main
from backend import datasets, exceptions, health, state_migration
from backend.kickbase.v4 import leagues, user
from backend.paths import BASE_PATH, PUBLIC_DIR, TIMESTAMP_DIR

### ===============================================================================

### Get the needed environment variables
kb_mail = getenv("KB_MAIL")
kb_password = getenv("KB_PASSWORD")
discord_webhook = getenv("DISCORD_WEBHOOK")

### Where `npm run build` puts the frontend. The image builds it in its own stage and copies
### only the result, so in a container this directory is all that is left of frontend/.
FRONTEND_BUILD_DIR = path.join(BASE_PATH, "frontend", "build")

### ===============================================================================

### static_url_path="" puts the build's own assets at the root, which is where index.html
### expects them ("/static/js/main.js", "/favicon.png"). Requests that match no file fall
### through to the 404 handler below and get index.html.
app = Flask(__name__, static_folder=FRONTEND_BUILD_DIR, static_url_path="")

### The datasets moved out of frontend/src/data in this version. app.py can be the first
### process to touch them - the container starts it before the first scheduled run - so the
### migration runs here as well as in main.py, and either order is fine: it never overwrites
### and it is a no-op once there is nothing left to move.
state_migration.migrate_legacy_layout()


def _send_index():
    """### Serve the built frontend's entry document.

    Returns:
        A Flask response with index.html, or a 503 explaining that nothing was built.
    """
    if not path.isfile(path.join(FRONTEND_BUILD_DIR, "index.html")):
        ### A deployment that skipped the build stage. Said out loud rather than as a bare
        ### 404, because a 404 here reads as "wrong URL" when the answer is "wrong image".
        return jsonify({
            "error": "Das Frontend ist nicht gebaut. Im Container erledigt das die "
                     "Build-Stage des Images, lokal 'npm run build' in frontend/."
        }), 503

    return send_from_directory(FRONTEND_BUILD_DIR, "index.html")


@app.route("/api/health", methods=["GET"])
def get_health():
    """### Says whether this deployment is still doing its job.

    Answering at all shows Flask is up. The body says whether the data behind it is being
    kept current, read off the run manifest.

    The status code is a restart signal, so it deliberately does not follow "was the last
    run perfect". A stage that failed against a Kickbase outage answers 200: restarting
    the container would not have made Kickbase reply. A scheduler that has stopped
    answers 503, because that is a problem a restart does fix.
    """
    report = health.health_report()

    if health.is_healthy(report):
        return jsonify(report), 200

    logging.warning(f"Health check: {report['status']} - {report['reason']}")

    return jsonify(report), 503


@app.route("/api/data/timestamps", methods=["GET"])
def get_timestamps():
    """### Every ts_*.json in one response, keyed by dataset.

    One request rather than fourteen. The frontend needs all of them together anyway: a
    per-tab freshness marker is the dataset's timestamp *judged against the run manifest*,
    so a page that fetched them one at a time would render a marker it could not yet
    justify.

    The keys are the file names without "ts_" and without ".json", so "ts_market.json"
    arrives as "market" and the manifest as "run_manifest". Built from what is on disk, so a
    new timestamp file needs no change here.

    An unreadable file is left out of the answer rather than failing it. The frontend already
    has a word for a dataset it cannot judge ("unbekannt"), and one damaged file must not
    take the freshness of the other thirteen with it.

    Returns:
        A JSON object of dataset name to the timestamp document.
    """
    index = {}

    for file_path in sorted(glob(path.join(TIMESTAMP_DIR, f"{datasets.TIMESTAMP_PREFIX}*.json"))):
        name = path.basename(file_path)[len(datasets.TIMESTAMP_PREFIX):-len(".json")]

        try:
            with open(file_path, "r") as f:
                index[name] = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logging.warning(f"Skipping {path.basename(file_path)} in the timestamp index: {e}")

    return jsonify(index)


@app.route("/api/data/<name>", methods=["GET"])
def get_data(name):
    """### Serve one of the datasets the frontend reads.

    The name is checked against backend/datasets.py's allowlist, not turned into a path.
    That is the whole security model of this route and it is deliberately the boring one: a
    traversal attempt, a backend-private file and a typo all fail the same membership test
    before any path is built, so there is no directory to escape from.

    A dataset that has not been written yet answers 404 with a body saying so. That is a
    normal state, not an error - events.json exists only from the second run on,
    manager_profiles.json from the first, live_points.json only when the live endpoint has
    been called - and the frontend renders each of those as an empty state.

    Args:
        name (str): The dataset's file name, e.g. "market.json".

    Returns:
        The file, or a JSON error with 404.
    """
    if name not in datasets.PUBLIC_DATASETS:
        ### Deliberately the same answer for "not a dataset", "backend-private" and
        ### "../../etc/passwd": which of the three it was is not a browser's business.
        return jsonify({"error": f"Unbekannter Datensatz: {name}"}), 404

    if not path.isfile(path.join(PUBLIC_DIR, name)):
        return jsonify({
            "error": f"{name} wurde noch nicht geschrieben.",
            "written": False
        }), 404

    return send_from_directory(PUBLIC_DIR, name, mimetype="application/json")


@app.route("/api/livepoints", methods=["GET"])
def get_live_points():
    """### Fetches the current live points and returns them to the frontend.

    The payload is built by `main.live_points()`, which also writes `live_points.json`
    and its timestamp into the public data directory.

    NOTE: The live points feature is on-hold, so the underlying Kickbase endpoint is
    unverified against the current API.

    NOTE: This performs a full Kickbase login on every call, which is why nothing in the UI
    links to it: any client that can reach this port can make this container authenticate
    against Kickbase's auth endpoint. The Live tab therefore only re-reads
    live_points.json. Fixing this properly means caching the token and scoping who may
    reach the port, which is tracked separately (old plan §G).
    """
    logging.info("Flask API: Getting live points...")

    try:
        ### Login to Kickbase
        user_info, user_token = user.login(kb_mail, kb_password, discord_webhook)

        ### Get all leagues the user is in and pick the one to show data for
        league_list = leagues.get_league_list(user_token)
        if not league_list:
            logging.error("Flask API: No leagues found.")
            return jsonify({"error": "No leagues found for this Kickbase account."}), 502
        selected_league = main.select_league(league_list)

        ### Get the current live points (also writes live_points.json + timestamp)
        final_live_points = main.live_points(user_token, selected_league)
    except exceptions.LoginException as e:
        logging.error(f"Flask API: {e}")
        return jsonify({"error": "Login failed! Please check your credentials."}), 502
    except exceptions.KickbaseException as e:
        logging.error(f"Flask API: {e}")
        return jsonify({"error": "Couldn't get the live points from Kickbase."}), 502

    logging.info("Flask API: Got live points.")

    ### Return the live points
    return jsonify(final_live_points)


@app.route("/", methods=["GET"])
def serve_root():
    """### The dashboard itself."""
    return _send_index()


@app.errorhandler(404)
def serve_single_page_app(error):
    """### Anything that is not a file and not an API route is a route inside the app.

    Registered as the 404 handler rather than as a catch-all route, so Flask's own static
    handler keeps serving the build's assets and only what it could not find lands here.

    /api/ keeps its 404s. Handing index.html to a fetch() that asked for a dataset would
    turn a missing file into a JSON parse error somewhere else entirely.
    """
    if request.path.startswith("/api/"):
        return jsonify({"error": f"Unbekannte API-Route: {request.path}"}), 404

    return _send_index()


if __name__ == "__main__":
    app.run()
