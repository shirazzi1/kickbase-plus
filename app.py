import hmac
import logging

from os import getenv
from flask import Flask, jsonify, request

import main
from backend import exceptions, health, miscellaneous
from backend.kickbase.v4 import leagues, user

### ===============================================================================

### Get the needed environment variables
kb_mail = getenv("KB_MAIL")
kb_password = getenv("KB_PASSWORD")
discord_webhook = getenv("DISCORD_WEBHOOK")
bid_token = getenv("BID_TOKEN")

### ===============================================================================

app = Flask(__name__)
### No CORS here, and none should be added back. The frontend reaches this API through
### the CRA dev server's proxy (see frontend/src/setupProxy.js), which makes every real
### request same-origin - Flask itself never needs to answer a cross-origin request. A
### blanket CORS(app) reflects any Origin, which means any page the user's browser opens
### could POST/DELETE a bid that spends real money in their real league. Do not add it
### back to make some other cross-origin case convenient.


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


def _check_bid_token():
    """### Rejects the request unless it carries the correct X-Bid-Token header.

    Both write endpoints call this before doing anything else. The token is meant to
    reach Flask only from the CRA dev server's setupProxy.js, never from the browser
    directly - see frontend/src/setupProxy.js for where it is actually attached.

    Fails closed: if BID_TOKEN is unset or empty, every request is refused rather than
    let through. An unset secret must never be silently read as "no check configured",
    or removing the env var would turn the check off instead of tightening it. That case
    is a server misconfiguration, not a failed authentication - it answers 503 and never
    names the environment variable to the browser, unlike a wrong or missing token, which
    is the caller's mistake and stays 401.

    The comparison against the configured token uses hmac.compare_digest() rather than
    != so a byte-by-byte early exit cannot be timed from outside.

    Both sides are encoded to UTF-8 bytes before that comparison. Werkzeug decodes
    headers as latin-1, so a header value can arrive as a str holding a non-ASCII
    character, and compare_digest() raises TypeError as soon as either argument is a
    non-ASCII str - reproducible with hmac.compare_digest("ä", "x"). Uncaught, that
    would turn an unauthenticated request into a bare 500 with no JSON error body, and
    would turn an operator's own choice of an umlaut in BID_TOKEN into a 500 on every
    single bid. Comparing bytes instead sidesteps the restriction entirely.

    Returns:
        A (response, status) tuple to return immediately if the request is rejected,
        or None if the request may proceed.
    """
    if not bid_token:
        logging.error("Flask API: BID_TOKEN is not set, refusing all bid requests.")
        return jsonify({"error": "Der Server ist für Gebote derzeit nicht konfiguriert. "
                                  "Bitte informiere die Person, die den Server "
                                  "betreibt."}), 503

    supplied_token = request.headers.get("X-Bid-Token", "")
    if not hmac.compare_digest(supplied_token.encode("utf-8"), bid_token.encode("utf-8")):
        return jsonify({"error": "Ungültiges oder fehlendes Token für diese Aktion."}), 401

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


if __name__ == "__main__":
    app.run()
