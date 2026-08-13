"""
### The one process a browser talks to.

Until this version there were two: a create-react-app dev server on port 3000 serving a
bundle with the data compiled into it, and this Flask app on port 5000 serving the API.
Fresh data reached a browser only because the dev server noticed a JSON file change and
recompiled - in production.

Now Flask serves both halves:

  - `/api/data/<name>` hands out the datasets in `data/public`, from an allowlist rather
    than by passing a path through. `/api/data/timestamps` returns every `ts_*.json` in one
    response, which is what the per-tab freshness markers are read from.
  - everything else is the prebuilt React app from `frontend/build`.

That collapses the port split and makes the relative `fetch("/api/...")` calls in the
frontend work in production for the first time.

Not part of this: binding to localhost and scoping who may reach this port at all. That is
the piece of hardening the bid token below explicitly does *not* replace.
"""

import hmac
import json
import logging
import secrets

from glob import glob
from os import getenv, path
from flask import Flask, jsonify, request, send_from_directory

import main
from backend import datasets, exceptions, health, miscellaneous, state_migration
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
### The token the bid endpoints require.
###
### It used to come from exactly one place: BID_TOKEN in the environment, attached to every
### proxied request by the create-react-app dev server (frontend/src/setupProxy.js). That
### worked because the dev server was also what served the app - in production it served
### nothing, and this file now does, so the token had nowhere to come from. Every bid would
### have answered 401.
###
### Two sources are accepted instead:
###
###   1. A token this process generates once at boot and hands to the browser as a cookie
###      when it serves index.html. The frontend reads the cookie and sends it back as
###      X-Bid-Token. Regenerated on every restart, never written down anywhere.
###   2. BID_TOKEN from the environment, when it is set. That keeps setupProxy.js working in
###      development and keeps a script able to bid without loading the page.
###
### **What this is and is not.** It is CSRF protection, and nothing more. A page on another
### origin cannot read the cookie (same-origin policy on document.cookie) and cannot send the
### header (no CORS, so the browser refuses a cross-origin request carrying it), which is what
### stops a random page the user opens from spending money in their league. It is *not* access
### control: anyone who can load the dashboard can read the cookie and therefore bid - exactly
### as anyone who could reach the dev proxy could bid before. Keeping strangers off the port
### is a separate change (bind to localhost or put a real auth layer in front), and it is
### still open.
BID_TOKEN_COOKIE = "bid_token"
BOOT_BID_TOKEN = secrets.token_hex(32)
bid_token = getenv("BID_TOKEN")

### ===============================================================================

### static_url_path="" puts the build's own assets at the root, which is where index.html
### expects them ("/static/js/main.js", "/favicon.png"). Requests that match no file fall
### through to the 404 handler below and get index.html.
app = Flask(__name__, static_folder=FRONTEND_BUILD_DIR, static_url_path="")
### No CORS here, and none should be added back. One origin serves both the dashboard and the
### API now, so Flask never needs to answer a cross-origin request at all. A blanket CORS(app)
### reflects any Origin, which means any page the user's browser opens could POST/DELETE a bid
### that spends real money in their real league - and it would defeat the bid token above,
### whose whole protection is that a foreign page cannot send that header. Do not add it back
### to make some other cross-origin case convenient.


### The datasets moved out of frontend/src/data in this version. Called lazily rather than at
### import time: importing this module must not create directories, or a test that only wants
### the Flask app ends up writing into the checkout.
_migrated = False


def _migrate_once():
    """### Move the old frontend/src/data layout into data/public and data/state.

    Runs before the first request that could read a dataset. entrypoint.py already does this
    before either child starts, so in a container this finds nothing; it is here for the case
    where app.py is started on its own.
    """
    global _migrated

    if _migrated:
        return

    _migrated = True
    state_migration.migrate_legacy_layout()


def _send_index():
    """### Serve the built frontend's entry document, with the bid token attached.

    The cookie is set here rather than on a login: there is no login, and this is the one
    response that is only ever produced for a browser that is about to run the app.

    `httponly` is deliberately False. The frontend has to read this value and put it in a
    header - a cookie the browser attaches by itself would be sent by a cross-site request
    too, which is precisely what has to be prevented. Reading it from JavaScript is what makes
    the same-origin policy the boundary.

    `secure` is deliberately not set. This is routinely deployed over plain HTTP on a LAN, and
    a Secure cookie would simply never arrive there - which would turn every bid into a 401
    with no hint as to why.

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

    response = send_from_directory(FRONTEND_BUILD_DIR, "index.html")
    response.set_cookie(BID_TOKEN_COOKIE, BOOT_BID_TOKEN, samesite="Strict", path="/")

    return response


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
    _migrate_once()

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

    _migrate_once()

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
    and its timestamp into the frontend data directory.

    NOTE: The live points feature is on-hold, so the underlying Kickbase endpoint is
    unverified against the current API.
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


def _accepted_bid_tokens():
    """### The tokens a bid request may carry.

    Always the boot token, which the browser gets as a cookie with index.html. Plus
    BID_TOKEN from the environment when it is set, which is how the CRA dev server's
    setupProxy.js reaches these endpoints in development and how a script would.

    Never empty, so there is no "unconfigured" state left to fail closed on. That mattered
    while the only token came from an env var an operator could forget; a token this process
    generates itself cannot be forgotten.

    Returns:
        list: The accepted tokens, as UTF-8 bytes.
    """
    tokens = [BOOT_BID_TOKEN]

    if bid_token:
        tokens.append(bid_token)

    return [token.encode("utf-8") for token in tokens]


def _check_bid_token():
    """### Rejects the request unless it carries one of the accepted X-Bid-Token values.

    Both write endpoints call this before doing anything else. See BOOT_BID_TOKEN at the top
    of this module for where the token comes from and, more importantly, for what this
    protects against (a foreign page spending money in your league) and what it does not
    (anyone who can load the dashboard).

    The comparison uses hmac.compare_digest() rather than != so a byte-by-byte early exit
    cannot be timed from outside. Every accepted token is compared, without short-circuiting
    on the first match, so the number of comparisons does not depend on which one matched.

    Both sides are compared as UTF-8 bytes. Werkzeug decodes headers as latin-1, so a header
    value can arrive as a str holding a non-ASCII character, and compare_digest() raises
    TypeError as soon as either argument is a non-ASCII str - reproducible with
    hmac.compare_digest("ä", "x"). Uncaught, that would turn an unauthenticated request into
    a bare 500 with no JSON error body, and would turn an operator's own choice of an umlaut
    in BID_TOKEN into a 500 on every single bid. Comparing bytes sidesteps the restriction.

    Returns:
        A (response, status) tuple to return immediately if the request is rejected,
        or None if the request may proceed.
    """
    supplied = request.headers.get("X-Bid-Token", "").encode("utf-8")

    matched = False

    for accepted in _accepted_bid_tokens():
        if hmac.compare_digest(supplied, accepted):
            matched = True

    if not matched:
        return jsonify({
            "error": "Ungültiges oder fehlendes Token für diese Aktion. Lade die Seite neu."
        }), 401

    return None


### Shown whenever a write may have already gone through but the confirmation could not
### be read back. Deliberately neither a success nor an ordinary failure: Kickbase's own
### state is unknown from here, and guessing wrong in either direction is worse than
### saying so. Points at the Kickbase app rather than at a retry, because retrying an
### action that already went through is exactly what this message exists to prevent.
BID_UNCONFIRMED_MESSAGE = ("Kickbase hat die Aktion möglicherweise bereits verarbeitet, die "
                           "Bestätigung ist aber fehlgeschlagen. Bitte prüfe die Kickbase-App, "
                           "bevor du erneut bietest.")


def _unconfirmed(context: str):
    """### The one response for "maybe done, but we could not confirm it".

    Shared by every place a write can end up in this state: the read-back after
    `place_offer` raising outright, that same read-back showing no trace of the bid just
    placed, and the read-back after `remove_offer` still showing the offer it was
    supposed to remove. The caller must not patch market.json when it reaches here - a
    value that could not be confirmed is worse to write than a stale file - and this is
    what keeps the message identical across all three call sites.

    Args:
        context (str): What was being confirmed, for the log line only.

    Returns:
        A (response, status) tuple to return directly from the view function.
    """
    logging.error(f"Flask API: could not confirm {context}.")
    return jsonify({"error": BID_UNCONFIRMED_MESSAGE}), 502


@app.route("/api/market/<player_id>/bid", methods=["POST"])
def place_bid(player_id):
    """### Places a bid on a player on the transfer market.

    Answers with the bid Kickbase confirms rather than the one that was sent, so a
    silently clamped or rounded bid is not displayed as the typed value.
    """
    rejection = _check_bid_token()
    if rejection is not None:
        return rejection

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

        ### A transport failure on the write itself cannot tell us whether Kickbase
        ### recorded the bid: ApiUnreachableException covers a refused connection or a DNS
        ### failure (the request never arrived) and a read timeout (it may have arrived and
        ### the answer was lost) alike, and the client does not distinguish them. Reporting
        ### it as a failure would invite a second bid on one that may be standing, so this
        ### joins the unconfirmed outcome. The two mistakes do not cost the same: being
        ### over-cautious costs a glance at the app, being under-cautious costs a bid.
        ###
        ### Caught narrowly rather than as HttpException, because OfferRejectedException is
        ### an HttpException too - a rejected bid has to keep the normalised status and the
        ### German message _offer_failure() built for it, and must not be reported as
        ### unconfirmed when Kickbase told us plainly that it refused.
        try:
            leagues.place_offer(user_token, selected_league.id, player_id, price)
        except exceptions.ApiUnreachableException:
            return _unconfirmed(f"the bid on player {player_id}")

        ### Read back what Kickbase recorded, rather than trusting what we sent. Kickbase
        ### already accepted the write above, so a read-back that raises, or one that
        ### shows no trace of this bid, means the *confirmation* failed - not the bid.
        ### Guessing "no bid" here would be exactly as wrong as guessing "yes", so neither
        ### branch below patches market.json or reports success.
        ###
        ### get_market() goes through http.get_json() now, which raises HttpException
        ### subclasses (AuthExpiredException, ApiUnreachableException, ...), never
        ### NotificatonException - that was the bare `except:` this module used to have
        ### before every Kickbase call went through one HTTP client. Catching the
        ### exception nothing here actually raises would leave this handler unreachable
        ### and let the failure fall through to the generic 502 below, which claims the
        ### bid failed when it may well have gone through.
        try:
            confirmed = _listing(user_token, selected_league.id, player_id)
        except exceptions.HttpException:
            return _unconfirmed(f"the bid on player {player_id}")

        own_bid = confirmed.own_offer(user_info.id) if confirmed else None
        if own_bid is None:
            return _unconfirmed(f"the bid on player {player_id}")

        ### The bid is confirmed by this point - only the local market.json cache is
        ### left to update, and write_json_to_file() now raises on a failed write
        ### instead of swallowing it (see its docstring). Left uncaught, that exception
        ### would propagate past every handler below (none of them are KickbaseException,
        ### an OSError or a TypeError is neither) as a bare 500, which the frontend shows
        ### as "Flask API not reachable" with the draft still open - inviting a second
        ### bid on top of one that already landed. Answering _unconfirmed() instead
        ### under-states what is actually known here (the bid did happen, only the cache
        ### write failed), but it is the one response that does not invite a retry.
        try:
            miscellaneous.patch_market_bid(player_id, own_bid)
        except Exception as e:
            logging.error(f"Flask API: the bid on player {player_id} was confirmed, but "
                          f"market.json could not be updated: {e}")
            return _unconfirmed(f"the bid on player {player_id}")
    except exceptions.OfferRejectedException as e:
        logging.error(f"Flask API: Kickbase rejected the bid: {e}")
        return jsonify({"error": str(e)}), e.status_code
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
    rejection = _check_bid_token()
    if rejection is not None:
        return rejection

    logging.info(f"Flask API: Withdrawing the bid on player {player_id}...")

    try:
        user_info, user_token, selected_league = _connect()

        listing = _listing(user_token, selected_league.id, player_id)
        if listing is None:
            return jsonify({"error": "Dieser Spieler steht nicht auf dem Transfermarkt."}), 404

        if listing.own_offer(user_info.id) is None:
            return jsonify({"error": "Auf diesen Spieler hast du kein Gebot abgegeben."}), 409

        ### The user's own id is the offer's identifier - Kickbase exposes no offer id.
        ### A transport failure here is unconfirmed for the same reason as in place_bid():
        ### the withdrawal may have been processed with the answer lost on the way back,
        ### and "it did not work" would send the user off believing a bid still stands that
        ### may already be gone - or the reverse. Narrow catch for the same reason too.
        try:
            leagues.remove_offer(user_token, selected_league.id, player_id, user_info.id)
        except exceptions.ApiUnreachableException:
            return _unconfirmed(f"the withdrawal on player {player_id}")

        ### Symmetric with the POST above: read back to confirm the offer is actually
        ### gone rather than trusting the 200. An idempotent 200, or the seller accepting
        ### the offer between the pre-read above and this DELETE, can both leave it
        ### standing - and reporting it gone either way is exactly the wrong guess.
        ###
        ### See the matching comment in place_bid(): get_market() raises HttpException
        ### subclasses now, never NotificatonException.
        try:
            confirmed = _listing(user_token, selected_league.id, player_id)
        except exceptions.HttpException:
            return _unconfirmed(f"the withdrawal on player {player_id}")

        if confirmed is not None and confirmed.own_offer(user_info.id) is not None:
            return _unconfirmed(f"the withdrawal on player {player_id}")

        ### See the matching comment in place_bid(): a patch failure here must not
        ### escape as a 500 either, now that write_json_to_file() raises.
        try:
            miscellaneous.patch_market_bid(player_id, None)
        except Exception as e:
            logging.error(f"Flask API: the withdrawal on player {player_id} was "
                          f"confirmed, but market.json could not be updated: {e}")
            return _unconfirmed(f"the withdrawal on player {player_id}")
    except exceptions.OfferRejectedException as e:
        logging.error(f"Flask API: Kickbase rejected the withdrawal: {e}")
        return jsonify({"error": str(e)}), e.status_code
    except exceptions.LoginException as e:
        logging.error(f"Flask API: {e}")
        return jsonify({"error": "Login bei Kickbase fehlgeschlagen. Bitte Zugangsdaten prüfen."}), 502
    except exceptions.KickbaseException as e:
        logging.error(f"Flask API: {e}")
        return jsonify({"error": "Kickbase konnte das Gebot nicht zurückziehen."}), 502

    logging.info(f"Flask API: Bid on player {player_id} withdrawn.")

    return jsonify({"ownBid": None})


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
