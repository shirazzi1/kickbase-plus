"""### Tests for placing and withdrawing a bid on the transfer market.

Dependency free on purpose: the project has no test framework, so this runs with the
project venv directly and needs no extra packages. The HTTP layer is replaced by a fake
rather than mocked with a library, for the same reason.

Shapes are the ones recorded in
docs/superpowers/specs/2026-08-13-market-bid-field-design.md.

    ./venv/bin/python tests/test_market_bid.py
"""

import json
import sys
import tempfile

from os import path

sys.path.insert(0, path.dirname(path.dirname(path.abspath(__file__))))

from backend import exceptions, miscellaneous
from backend.kickbase.endpoints.leagues import Market_Players
from backend.kickbase.v4 import leagues

### ===============================================================================

OWN_USER_ID = "3854976"
OTHER_USER_ID = "2592773"
LEAGUE_ID = "11412166"
PLAYER_ID = "8289"

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


class FakeResponse:
    """Enough of a requests.Response for the two write calls."""

    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self._body = body
        self.text = "" if body is None else json.dumps(body)
        self.content = self.text.encode()

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class Recorder:
    """Stands in for requests.post/requests.delete and records the call."""

    def __init__(self, response):
        self.response = response
        self.url = None
        self.json = None
        self.headers = None
        self.timeout = None

    def __call__(self, url, json=None, headers=None, timeout=None):
        self.url, self.json, self.headers, self.timeout = url, json, headers, timeout
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def with_fake(method, response, fn):
    """Run fn with requests.<method> replaced, and return the recorder."""
    recorder = Recorder(response)
    original = getattr(leagues.requests, method)
    setattr(leagues.requests, method, recorder)
    try:
        fn()
    finally:
        setattr(leagues.requests, method, original)
    return recorder


### ===============================================================================
### place_offer()
### ===============================================================================


def test_place_offer_posts_the_price_to_the_player():
    recorder = with_fake("post", FakeResponse(200, {}), lambda:
        leagues.place_offer("tok", LEAGUE_ID, PLAYER_ID, 1180000))

    assert LEAGUE_ID in recorder.url and PLAYER_ID in recorder.url, \
        f"url should name league and player, got {recorder.url}"
    assert recorder.json == {"price": 1180000}, f"unexpected body {recorder.json}"
    assert recorder.headers["Cookie"] == "kkstrauth=tok;", \
        f"expected the auth cookie, got {recorder.headers}"


def test_place_offer_sends_a_timeout():
    """No Kickbase call in this project has one today; one hung socket parks the API."""
    recorder = with_fake("post", FakeResponse(200, {}), lambda:
        leagues.place_offer("tok", LEAGUE_ID, PLAYER_ID, 1180000))
    assert recorder.timeout, f"expected a timeout, got {recorder.timeout!r}"


def test_place_offer_translates_a_known_error_code():
    """The real shape, recorded live: a bid below the market value comes back as a 500.

    Two things have to happen to it. The status is normalised to 400, because the bid was
    the user's to get wrong and a forwarded 500 would blame the server. And the message
    becomes German, because "UnderpayNotAllowed" is not a sentence to show a user.
    """
    body = {"err": 5080, "errMsg": "UnderpayNotAllowed", "svcs": []}

    def place():
        leagues.place_offer("tok", LEAGUE_ID, PLAYER_ID, 1)

    try:
        with_fake("post", FakeResponse(500, body), place)
    except exceptions.KickbaseWriteException as e:
        assert e.status == 400, f"a semantic rejection is a 400, not a 500, got {e.status}"
        assert "Marktwert" in str(e), f"expected the German message, got: {e}"
        assert "5080" not in str(e), f"the numeric code must never reach the user, got: {e}"
        assert "UnderpayNotAllowed" not in str(e), f"expected German, got: {e}"
    else:
        raise AssertionError("expected a KickbaseWriteException for a rejected bid")


def test_place_offer_falls_back_to_errmsg_for_an_unknown_code():
    """An unmapped code still has to say something better than its number."""
    body = {"err": 9999, "errMsg": "SomethingNewWentWrong", "svcs": []}

    def place():
        leagues.place_offer("tok", LEAGUE_ID, PLAYER_ID, 1)

    try:
        with_fake("post", FakeResponse(500, body), place)
    except exceptions.KickbaseWriteException as e:
        assert "SomethingNewWentWrong" in str(e), f"expected the errMsg fallback, got: {e}"
        assert "9999" not in str(e), f"the numeric code must never be the message, got: {e}"
    else:
        raise AssertionError("expected a KickbaseWriteException for an unknown code")


def test_place_offer_forwards_a_real_outage_as_502():
    """A 5xx with no error code is Kickbase being broken, not the bid being wrong."""
    def place():
        leagues.place_offer("tok", LEAGUE_ID, PLAYER_ID, 1)

    try:
        with_fake("post", FakeResponse(500, None), place)
    except exceptions.KickbaseWriteException as e:
        assert e.status == 502, f"expected 502 for a codeless 5xx, got {e.status}"
        assert "500" in str(e), f"message should name the upstream status, got: {e}"
    else:
        raise AssertionError("expected a KickbaseWriteException for a 500")


def test_place_offer_passes_a_4xx_through_unchanged():
    """Kickbase's own 4xx needs no reinterpretation."""
    def place():
        leagues.place_offer("tok", LEAGUE_ID, PLAYER_ID, 1)

    try:
        with_fake("post", FakeResponse(400, {"err": 6, "errMsg": "InvalidData"}), place)
    except exceptions.KickbaseWriteException as e:
        assert e.status == 400, f"expected 400 passed through, got {e.status}"
    else:
        raise AssertionError("expected a KickbaseWriteException for a 400")


def test_place_offer_reports_an_unreachable_api():
    import requests as real_requests

    def place():
        leagues.place_offer("tok", LEAGUE_ID, PLAYER_ID, 1180000)

    try:
        with_fake("post", real_requests.exceptions.ConnectTimeout("timed out"), place)
    except exceptions.KickbaseWriteException as e:
        assert e.status >= 500, f"a transport failure is not the user's fault, got {e.status}"
    else:
        raise AssertionError("expected a KickbaseWriteException for a connection failure")


### The message urllib3 actually produces for a real ConnectTimeout - long, English, and
### full of internals no user should ever see.
REALISTIC_CONNECT_TIMEOUT = (
    "HTTPSConnectionPool(host='api.kickbase.com', port=443): Max retries exceeded with "
    "url: /v4/leagues/11412166/market/8289/offers (Caused by "
    "ConnectTimeoutError(<urllib3.connection.HTTPSConnection object>, "
    "'Connection to api.kickbase.com timed out.'))"
)


def test_place_offer_transport_failure_message_is_clean_german():
    """The exception message reaches the browser, so it must read like a sentence.

    A raw ConnectTimeout carries a wall of urllib3 internals in English. None of that
    belongs in front of a user standing at the market waiting to find out why their bid
    did not go through.
    """
    import requests as real_requests

    def place():
        leagues.place_offer("tok", LEAGUE_ID, PLAYER_ID, 1180000)

    try:
        with_fake("post", real_requests.exceptions.ConnectTimeout(REALISTIC_CONNECT_TIMEOUT),
                   place)
    except exceptions.KickbaseWriteException as e:
        message = str(e)
        assert "HTTPSConnectionPool" not in message, f"leaked urllib3 detail: {message}"
        assert "Max retries" not in message, f"leaked urllib3 detail: {message}"
        assert "ConnectTimeoutError" not in message, f"leaked urllib3 detail: {message}"
        assert "Kickbase" in message and "erreichbar" in message, \
            f"expected a clean German sentence, got: {message}"
    else:
        raise AssertionError("expected a KickbaseWriteException for a connection failure")


### ===============================================================================
### remove_offer()
### ===============================================================================


def test_remove_offer_addresses_the_offer_by_user_id():
    """The identifier is the user id, and this is the test that pins it down.

    Live evidence: DELETE on the collection answers 405, and the only identifier the API
    ever hands back is the user's own id, returned by the POST as "ofi". A user holds at
    most one offer per player, so keying by user is enough.
    """
    recorder = with_fake("delete", FakeResponse(200, {}), lambda:
        leagues.remove_offer("tok", LEAGUE_ID, PLAYER_ID, OWN_USER_ID))

    assert recorder.url.endswith(f"/market/{PLAYER_ID}/offers/{OWN_USER_ID}"), \
        f"expected the offer addressed by user id, got {recorder.url}"
    assert recorder.headers["Cookie"] == "kkstrauth=tok;"


def test_remove_offer_never_calls_the_bare_collection():
    """That route answers 405, so hitting it would fail every withdrawal."""
    recorder = with_fake("delete", FakeResponse(200, {}), lambda:
        leagues.remove_offer("tok", LEAGUE_ID, PLAYER_ID, OWN_USER_ID))

    assert not recorder.url.endswith("/offers"), \
        f"the collection route takes no DELETE, got {recorder.url}"


def test_remove_offer_surfaces_the_api_message():
    def remove():
        leagues.remove_offer("tok", LEAGUE_ID, PLAYER_ID, OWN_USER_ID)

    try:
        with_fake("delete", FakeResponse(404, {"err": 1, "errMsg": "OfferNotFound"}), remove)
    except exceptions.KickbaseWriteException as e:
        assert e.status == 404, f"expected status 404, got {e.status}"
        assert "OfferNotFound" in str(e), f"expected the errMsg, got: {e}"
    else:
        raise AssertionError("expected a KickbaseWriteException for a 404")


def test_remove_offer_reports_a_clean_german_message_on_transport_failure():
    """remove_offer() had no transport-failure coverage at all before this test.

    Same requirement as place_offer(): the message reaches the browser, so the urllib3
    wall of English internals must not reach it either.
    """
    import requests as real_requests

    def remove():
        leagues.remove_offer("tok", LEAGUE_ID, PLAYER_ID, OWN_USER_ID)

    try:
        with_fake("delete",
                   real_requests.exceptions.ConnectTimeout(REALISTIC_CONNECT_TIMEOUT), remove)
    except exceptions.KickbaseWriteException as e:
        assert e.status >= 500, f"a transport failure is not the user's fault, got {e.status}"
        message = str(e)
        assert "HTTPSConnectionPool" not in message, f"leaked urllib3 detail: {message}"
        assert "Max retries" not in message, f"leaked urllib3 detail: {message}"
        assert "ConnectTimeoutError" not in message, f"leaked urllib3 detail: {message}"
        assert "Kickbase" in message and "erreichbar" in message, \
            f"expected a clean German sentence, got: {message}"
    else:
        raise AssertionError("expected a KickbaseWriteException for a connection failure")


### ===============================================================================
### own_offer(), against the offer shape recorded live
### ===============================================================================


def market_item(**overrides):
    """A market item as get_market() receives it."""
    item = {"i": PLAYER_ID, "fn": "Salim Amani", "n": "Musah", "tid": "2", "pos": 3,
            "st": 0, "mv": 5000000, "prc": 5200000}
    item.update(overrides)
    return item


### The exact ofs entry a real POST produced on 2026-08-13. Note the absence of any id
### field: this is the shape that ruled out addressing a delete route by offer id.
LIVE_OWN_OFFER = {"u": OWN_USER_ID, "unm": "shirazzi", "uoid": OWN_USER_ID,
                  "uop": 1271013, "st": 0, "uim": "user/91fd.jpe"}


def test_own_offer_reads_the_live_offer_shape():
    player = Market_Players(market_item(ofs=[dict(LIVE_OWN_OFFER)]))
    assert player.own_offer(OWN_USER_ID) == 1271013, \
        f"expected 1271013, got {player.own_offer(OWN_USER_ID)}"


def test_the_live_offer_shape_carries_no_offer_id():
    """Documents why remove_offer() takes a user id: there is no offer id to take.

    If Kickbase ever starts sending one, this test fails and is the place to decide
    whether to switch to it.
    """
    assert "i" not in LIVE_OWN_OFFER, \
        "an offer id appeared in the recorded shape - revisit how removal is addressed"


def test_own_offer_reads_the_top_level_mirror():
    """Some items carry only the mirror, with no ofs list at all."""
    mirrored = Market_Players(market_item(uoid=OWN_USER_ID, uop=523350))
    assert mirrored.own_offer(OWN_USER_ID) == 523350


def test_own_offer_ignores_a_foreign_offer():
    """A foreign bid must never be reported as ours, whatever the API starts exposing."""
    foreign = Market_Players(market_item(
        ofs=[{"u": OTHER_USER_ID, "uoid": OTHER_USER_ID, "uop": 999999}]))
    assert foreign.own_offer(OWN_USER_ID) is None

    mirrored_foreign = Market_Players(market_item(uoid=OTHER_USER_ID, uop=999999))
    assert mirrored_foreign.own_offer(OWN_USER_ID) is None


def test_own_offer_is_none_without_any_offer():
    """The normal case: a listing carries no ofs/uop/uoid keys at all until an offer exists."""
    assert Market_Players(market_item()).own_offer(OWN_USER_ID) is None


### ===============================================================================
### patch_market_bid()
### ===============================================================================


def with_market_file(rows, fn):
    """Run fn with DATA_DIR pointed at a temporary market.json, and return its rows."""
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = path.join(tmp, "data")
        ts_dir = path.join(data_dir, "timestamps")
        from os import makedirs
        makedirs(ts_dir, exist_ok=True)

        with open(path.join(data_dir, "market.json"), "w") as f:
            json.dump(rows, f)

        original = (miscellaneous.DATA_DIR, miscellaneous.TIMESTAMP_DIR)
        miscellaneous.DATA_DIR = data_dir
        miscellaneous.TIMESTAMP_DIR = ts_dir
        try:
            result = fn()
            with open(path.join(data_dir, "market.json")) as f:
                return result, json.load(f)
        finally:
            miscellaneous.DATA_DIR, miscellaneous.TIMESTAMP_DIR = original


def market_rows():
    return [
        {"playerId": "8289", "lastName": "Musah", "ownBid": None, "marketValue": 5000000},
        {"playerId": "3754", "lastName": "Boey", "ownBid": 523350, "marketValue": 500000},
    ]


def test_patch_writes_the_confirmed_bid():
    result, rows = with_market_file(market_rows(), lambda:
        miscellaneous.patch_market_bid("8289", 5200000))

    assert result is True, "expected the patch to report success"
    assert rows[0]["ownBid"] == 5200000, f"expected the bid written, got {rows[0]}"


def test_patch_clears_a_withdrawn_bid():
    result, rows = with_market_file(market_rows(), lambda:
        miscellaneous.patch_market_bid("3754", None))

    assert result is True
    assert rows[1]["ownBid"] is None, f"expected the bid cleared, got {rows[1]}"


def test_patch_leaves_other_rows_alone():
    _, rows = with_market_file(market_rows(), lambda:
        miscellaneous.patch_market_bid("8289", 5200000))
    assert rows[1]["ownBid"] == 523350, f"expected Boey untouched, got {rows[1]}"


def test_patch_of_an_unknown_player_changes_nothing():
    result, rows = with_market_file(market_rows(), lambda:
        miscellaneous.patch_market_bid("999999", 1))

    assert result is False, "expected the patch to report that nothing matched"
    assert rows == market_rows(), f"expected the file untouched, got {rows}"


def test_patch_survives_a_missing_file():
    """app.py can serve a request before main.py ever ran."""
    with tempfile.TemporaryDirectory() as tmp:
        original = miscellaneous.DATA_DIR
        miscellaneous.DATA_DIR = path.join(tmp, "data")
        try:
            assert miscellaneous.patch_market_bid("8289", 1) is False
        finally:
            miscellaneous.DATA_DIR = original


### ===============================================================================
### The endpoints
### ===============================================================================


def client_with(market, place=None, remove=None, own_user_id=OWN_USER_ID):
    """A Flask test client with login, market and the write calls faked out."""
    import app as flask_app
    import main

    class FakeUser:
        id = own_user_id
        name = "shirazzi"

    class FakeLeague:
        id = LEAGUE_ID
        name = "Test"

    original = (flask_app.user.login, flask_app.leagues.get_league_list,
                flask_app.leagues.get_market, flask_app.leagues.place_offer,
                flask_app.leagues.remove_offer, main.select_league)

    flask_app.user.login = lambda *a, **k: (FakeUser(), "tok")
    flask_app.leagues.get_league_list = lambda token: [FakeLeague()]
    main.select_league = lambda league_list: FakeLeague()
    flask_app.leagues.get_market = lambda token, lid: [Market_Players(i) for i in market()]
    flask_app.leagues.place_offer = place or (lambda *a, **k: {})
    flask_app.leagues.remove_offer = remove or (lambda *a, **k: None)

    flask_app.app.config["TESTING"] = True
    return flask_app.app.test_client(), original


def restore(original):
    import app as flask_app
    import main
    (flask_app.user.login, flask_app.leagues.get_league_list, flask_app.leagues.get_market,
     flask_app.leagues.place_offer, flask_app.leagues.remove_offer,
     main.select_league) = original


def post_bid(market, price, place=None):
    """POST a bid and return (status, body)."""
    client, original = client_with(market, place=place)
    try:
        response = client.post(f"/api/market/{PLAYER_ID}/bid", json={"price": price})
        return response.status_code, response.get_json()
    finally:
        restore(original)


def plain_market():
    return [market_item()]


def bid_market():
    return [market_item(ofs=[{"i": "77", "u": OWN_USER_ID, "uoid": OWN_USER_ID,
                              "uop": 5200000}])]


def test_post_rejects_a_non_positive_price():
    status, body = post_bid(plain_market, 0)
    assert status == 400, f"expected 400, got {status} {body}"
    assert "error" in body and body["error"], f"expected a German message, got {body}"


def test_post_rejects_a_non_integer_price():
    status, body = post_bid(plain_market, "viel")
    assert status == 400, f"expected 400, got {status} {body}"


def test_post_rejects_a_player_not_on_the_market():
    client, original = client_with(plain_market)
    try:
        response = client.post("/api/market/999999/bid", json={"price": 1180000})
        assert response.status_code == 404, \
            f"expected 404, got {response.status_code} {response.get_json()}"
    finally:
        restore(original)


def test_post_refuses_an_own_listing():
    """Nobody bids on their own player, and the server must not rely on the browser."""
    def own_listing():
        return [market_item(u={"i": OWN_USER_ID, "n": "shirazzi"})]

    status, body = post_bid(own_listing, 1180000)
    assert status == 409, f"expected 409, got {status} {body}"


def test_post_returns_the_bid_read_back_from_kickbase():
    """Not the typed value: a silently clamped bid would otherwise be shown as typed."""
    calls = []
    status, body = post_bid(bid_market, 1180000,
                            place=lambda *a, **k: calls.append(a) or {})
    assert status == 200, f"expected 200, got {status} {body}"
    ### The faked market reports 5.200.000 regardless of what was sent
    assert body == {"ownBid": 5200000}, f"expected the read-back bid, got {body}"
    assert calls, "expected place_offer to have been called"


def test_post_passes_the_kickbase_rejection_through():
    def rejecting(*a, **k):
        raise exceptions.KickbaseWriteException(400, "Offer price is below the market value")

    status, body = post_bid(plain_market, 1, place=rejecting)
    assert status == 400, f"expected the API status passed through, got {status}"
    assert "below the market value" in body["error"], \
        f"expected the API message passed through, got {body}"


def test_delete_withdraws_and_reports_no_bid():
    removed = []
    client, original = client_with(
        bid_market, remove=lambda *a, **k: removed.append(a))
    try:
        response = client.delete(f"/api/market/{PLAYER_ID}/bid")
        assert response.status_code == 200, \
            f"expected 200, got {response.status_code} {response.get_json()}"
        assert response.get_json() == {"ownBid": None}, \
            f"expected a cleared bid, got {response.get_json()}"
        assert removed, "expected remove_offer to have been called"
    finally:
        restore(original)


def test_delete_without_a_bid_is_a_conflict():
    client, original = client_with(plain_market)
    try:
        response = client.delete(f"/api/market/{PLAYER_ID}/bid")
        assert response.status_code == 409, \
            f"expected 409, got {response.status_code} {response.get_json()}"
    finally:
        restore(original)


### ===============================================================================

if __name__ == "__main__":
    print("place_offer()")
    check("posts the price to the player", test_place_offer_posts_the_price_to_the_player)
    check("sends a timeout", test_place_offer_sends_a_timeout)
    check("translates a known error code", test_place_offer_translates_a_known_error_code)
    check("falls back to errMsg for an unknown code",
          test_place_offer_falls_back_to_errmsg_for_an_unknown_code)
    check("forwards a real outage as 502", test_place_offer_forwards_a_real_outage_as_502)
    check("passes a 4xx through unchanged", test_place_offer_passes_a_4xx_through_unchanged)
    check("reports an unreachable API", test_place_offer_reports_an_unreachable_api)
    check("transport failure message is clean German",
          test_place_offer_transport_failure_message_is_clean_german)

    print("\nremove_offer()")
    check("addresses the offer by user id", test_remove_offer_addresses_the_offer_by_user_id)
    check("never calls the bare collection", test_remove_offer_never_calls_the_bare_collection)
    check("surfaces the API message", test_remove_offer_surfaces_the_api_message)
    check("reports a clean German message on transport failure",
          test_remove_offer_reports_a_clean_german_message_on_transport_failure)

    print("\nown_offer()")
    check("reads the live offer shape", test_own_offer_reads_the_live_offer_shape)
    check("the live shape carries no offer id", test_the_live_offer_shape_carries_no_offer_id)
    check("reads the top level mirror", test_own_offer_reads_the_top_level_mirror)
    check("ignores a foreign offer", test_own_offer_ignores_a_foreign_offer)
    check("is none without any offer", test_own_offer_is_none_without_any_offer)

    print("\npatch_market_bid()")
    check("writes the confirmed bid", test_patch_writes_the_confirmed_bid)
    check("clears a withdrawn bid", test_patch_clears_a_withdrawn_bid)
    check("leaves other rows alone", test_patch_leaves_other_rows_alone)
    check("changes nothing for an unknown player", test_patch_of_an_unknown_player_changes_nothing)
    check("survives a missing file", test_patch_survives_a_missing_file)

    print("\nPOST /api/market/<id>/bid")
    check("rejects a non positive price", test_post_rejects_a_non_positive_price)
    check("rejects a non integer price", test_post_rejects_a_non_integer_price)
    check("rejects a player not on the market", test_post_rejects_a_player_not_on_the_market)
    check("refuses an own listing", test_post_refuses_an_own_listing)
    check("returns the bid read back from Kickbase",
          test_post_returns_the_bid_read_back_from_kickbase)
    check("passes the Kickbase rejection through", test_post_passes_the_kickbase_rejection_through)

    print("\nDELETE /api/market/<id>/bid")
    check("withdraws and reports no bid", test_delete_withdraws_and_reports_no_bid)
    check("is a conflict without a bid", test_delete_without_a_bid_is_a_conflict)

    total, passed = len(PASSED), sum(PASSED)
    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)
