"""### Tests for placing and withdrawing a bid on the transfer market.

Dependency free on purpose: the project has no test framework, so this runs with the
project venv directly and needs no extra packages. The HTTP layer is replaced by a fake
rather than mocked with a library, for the same reason.

Shapes are the ones recorded in
docs/superpowers/specs/2026-08-13-market-bid-field-design.md.

    ./venv/bin/python tests/test_market_bid.py
"""

import json
import shutil
import sys
import tempfile

from os import makedirs, path

sys.path.insert(0, path.dirname(path.dirname(path.abspath(__file__))))

from backend import exceptions, miscellaneous
from backend.kickbase import http
from backend.kickbase.endpoints.leagues import Market_Players
from backend.kickbase.v4 import leagues

### ===============================================================================

OWN_USER_ID = "3854976"
OTHER_USER_ID = "2592773"
LEAGUE_ID = "11412166"
PLAYER_ID = "8289"

### The token client_with() sets flask_app.bid_token to, so the suite needs no BID_TOKEN
### in the environment and is not accidentally green just because one happens to be
### unset. Kept ASCII: the tests that specifically exercise a non-ASCII token set their
### own value, so what varies there stays legible.
TEST_BID_TOKEN = "test-suite-bid-token"

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


class FakeSession:
    """Stands in for the pooled session http.request() asks for.

    place_offer()/remove_offer() go through backend.kickbase.http now, the same client
    every read in this project uses - see backend/kickbase/http.py and its own
    tests/test_http.py, which this class mirrors. A session records every call it
    receives, so a test can assert on the method, url, payload, headers and timeout
    without needing a real socket.
    """

    def __init__(self, response=None, raises=None):
        ### Either a single response reused for every call, or one to raise instead
        self.response = response
        self.raises = raises
        self.calls = []

    def _answer(self, method, url, headers, timeout, payload=None):
        self.calls.append({"method": method, "url": url, "headers": headers,
                           "timeout": timeout, "payload": payload})

        if self.raises is not None:
            raise self.raises

        return self.response

    def get(self, url, headers=None, timeout=None):
        return self._answer("GET", url, headers, timeout)

    def post(self, url, json=None, headers=None, timeout=None):
        return self._answer("POST", url, headers, timeout, payload=json)

    def delete(self, url, headers=None, timeout=None):
        return self._answer("DELETE", url, headers, timeout)


def with_session(fake, fn):
    """Run fn() with the pooled (retrying) session replaced by fake, and return it.

    Only the retrying session is replaced - the probe one (retry=False) is left alone,
    so a test that wants to check *which* session place_offer()/remove_offer() actually
    used can tell the two apart. See with_both_sessions() for that.
    """
    http.reset_session(fake)
    try:
        fn()
    finally:
        http.reset_session()
    return fake


def with_both_sessions(retrying, probe, fn):
    """Run fn() with the retrying and the probe (retry=False) sessions both replaced.

    Args:
        retrying: The fake to install as the session retry=True calls use.
        probe: The fake to install as the session retry=False calls use.
        fn (callable): The code under test.
    """
    http.reset_session(retrying, probe)
    try:
        fn()
    finally:
        http.reset_session()


### ===============================================================================
### get_market()
### ===============================================================================


def test_get_market_sends_a_timeout():
    """Finding 3: the confirming read-back must not be able to hang indefinitely.

    get_market() is what app.py calls right after place_offer()/remove_offer() to
    confirm the write, and previously had no timeout at all - a hung socket there would
    block the response in exactly the window the 502 "could not confirm" outcome exists
    for, after the money had already moved.

    Now goes through http.get_json() like every other read in this module, so the bound
    is http.DEFAULT_TIMEOUT rather than a bespoke MARKET_TIMEOUT - see leagues.py for why
    that constant was removed rather than kept alongside the shared one.
    """
    fake = with_session(FakeSession(FakeResponse(200, {"it": []})), lambda:
        leagues.get_market("tok", LEAGUE_ID))

    assert fake.calls[0]["timeout"] == http.DEFAULT_TIMEOUT, \
        f"expected {http.DEFAULT_TIMEOUT}, got {fake.calls[0]['timeout']}"


### ===============================================================================
### place_offer()
### ===============================================================================


def test_place_offer_posts_the_price_to_the_player():
    fake = with_session(FakeSession(FakeResponse(200, {})), lambda:
        leagues.place_offer("tok", LEAGUE_ID, PLAYER_ID, 1180000))

    call = fake.calls[0]
    assert call["method"] == "POST", f"expected POST, got {call['method']}"
    assert LEAGUE_ID in call["url"] and PLAYER_ID in call["url"], \
        f"url should name league and player, got {call['url']}"
    assert call["payload"] == {"price": 1180000}, f"unexpected body {call['payload']}"
    assert call["headers"]["Cookie"] == "kkstrauth=tok;", \
        f"expected the auth cookie, got {call['headers']}"


def test_place_offer_sends_a_timeout():
    """Shorter than http.DEFAULT_TIMEOUT's read half: the user is waiting in front of
    the field for this one, unlike the reads that share the longer default."""
    fake = with_session(FakeSession(FakeResponse(200, {})), lambda:
        leagues.place_offer("tok", LEAGUE_ID, PLAYER_ID, 1180000))
    assert fake.calls[0]["timeout"] == leagues.OFFER_TIMEOUT, \
        f"expected {leagues.OFFER_TIMEOUT}, got {fake.calls[0]['timeout']}"


def test_place_offer_does_not_use_the_retrying_session():
    """The subtlety in moving onto the shared client: retries must stay off.

    POST is not in http.RETRY_METHODS, so the retrying session would not actually repeat
    this call either way - but place_offer() has to pass retry=False regardless, since
    depending on that asymmetry surviving is exactly the kind of thing the next person to
    touch RETRY_METHODS would not know to check. This test would fail either way if the
    call stopped asking for the non-retrying session.
    """
    retrying = FakeSession()
    probe = FakeSession(FakeResponse(200, {}))

    with_both_sessions(retrying, probe, lambda:
        leagues.place_offer("tok", LEAGUE_ID, PLAYER_ID, 1180000))

    assert not retrying.calls, f"expected no call on the retrying session, got {retrying.calls}"
    assert len(probe.calls) == 1, f"expected the call on the probe session, got {probe.calls}"


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
        with_session(FakeSession(FakeResponse(500, body)), place)
    except exceptions.OfferRejectedException as e:
        assert e.status_code == 400, \
            f"a semantic rejection is a 400, not a 500, got {e.status_code}"
        assert "Marktwert" in str(e), f"expected the German message, got: {e}"
        assert "5080" not in str(e), f"the numeric code must never reach the user, got: {e}"
        assert "UnderpayNotAllowed" not in str(e), f"expected German, got: {e}"
    else:
        raise AssertionError("expected an OfferRejectedException for a rejected bid")


def test_place_offer_falls_back_to_errmsg_for_an_unknown_code():
    """An unmapped code still has to say something better than its number."""
    body = {"err": 9999, "errMsg": "SomethingNewWentWrong", "svcs": []}

    def place():
        leagues.place_offer("tok", LEAGUE_ID, PLAYER_ID, 1)

    try:
        with_session(FakeSession(FakeResponse(500, body)), place)
    except exceptions.OfferRejectedException as e:
        assert "SomethingNewWentWrong" in str(e), f"expected the errMsg fallback, got: {e}"
        assert "9999" not in str(e), f"the numeric code must never be the message, got: {e}"
    else:
        raise AssertionError("expected an OfferRejectedException for an unknown code")


def test_place_offer_forwards_a_real_outage_as_502():
    """A 5xx with no error code is Kickbase being broken, not the bid being wrong."""
    def place():
        leagues.place_offer("tok", LEAGUE_ID, PLAYER_ID, 1)

    try:
        with_session(FakeSession(FakeResponse(500, None)), place)
    except exceptions.OfferRejectedException as e:
        assert e.status_code == 502, f"expected 502 for a codeless 5xx, got {e.status_code}"
        assert "500" in str(e), f"message should name the upstream status, got: {e}"
    else:
        raise AssertionError("expected an OfferRejectedException for a 500")


def test_place_offer_passes_a_4xx_through_unchanged():
    """Kickbase's own 4xx needs no reinterpretation."""
    def place():
        leagues.place_offer("tok", LEAGUE_ID, PLAYER_ID, 1)

    try:
        with_session(FakeSession(FakeResponse(400, {"err": 6, "errMsg": "InvalidData"})), place)
    except exceptions.OfferRejectedException as e:
        assert e.status_code == 400, f"expected 400 passed through, got {e.status_code}"
    else:
        raise AssertionError("expected an OfferRejectedException for a 400")


def test_place_offer_reports_a_transport_failure_as_the_clients_own_exception_type():
    """A hung socket or a refused connection is http.request()'s job to translate, not
    place_offer()'s own.

    Before the HTTP client existed, place_offer() caught requests.exceptions.RequestException
    itself and re-raised a bespoke German message. That duplicated exactly what
    http.py's _request() already does for every other Kickbase call, so it is gone now:
    the transport failure surfaces as exceptions.ApiUnreachableException, the same type
    get_market() or any other read raises for the same cause, and app.py's generic
    KickbaseException handler already answers it with a German message of its own.
    """
    import requests as real_requests

    def place():
        leagues.place_offer("tok", LEAGUE_ID, PLAYER_ID, 1180000)

    try:
        with_session(FakeSession(raises=real_requests.exceptions.ConnectTimeout("timed out")),
                     place)
    except exceptions.ApiUnreachableException as e:
        assert issubclass(type(e), exceptions.KickbaseException), \
            "app.py's generic handler must still catch this"
    else:
        raise AssertionError("expected an ApiUnreachableException for a connection failure")


### ===============================================================================
### remove_offer()
### ===============================================================================


def test_remove_offer_addresses_the_offer_by_user_id():
    """The identifier is the user id, and this is the test that pins it down.

    Live evidence: DELETE on the collection answers 405, and the only identifier the API
    ever hands back is the user's own id, returned by the POST as "ofi". A user holds at
    most one offer per player, so keying by user is enough.
    """
    fake = with_session(FakeSession(FakeResponse(200, {})), lambda:
        leagues.remove_offer("tok", LEAGUE_ID, PLAYER_ID, OWN_USER_ID))

    call = fake.calls[0]
    assert call["method"] == "DELETE", f"expected DELETE, got {call['method']}"
    assert call["url"].endswith(f"/market/{PLAYER_ID}/offers/{OWN_USER_ID}"), \
        f"expected the offer addressed by user id, got {call['url']}"
    assert call["headers"]["Cookie"] == "kkstrauth=tok;"


def test_remove_offer_never_calls_the_bare_collection():
    """That route answers 405, so hitting it would fail every withdrawal."""
    fake = with_session(FakeSession(FakeResponse(200, {})), lambda:
        leagues.remove_offer("tok", LEAGUE_ID, PLAYER_ID, OWN_USER_ID))

    assert not fake.calls[0]["url"].endswith("/offers"), \
        f"the collection route takes no DELETE, got {fake.calls[0]['url']}"


def test_remove_offer_does_not_use_the_retrying_session():
    """The whole reason this migration needed a decision rather than a find-and-replace.

    DELETE *is* in http.RETRY_METHODS. Without retry=False, a rejected withdrawal - also
    reported as a 5xx, same as a rejected bid - would be retried three times by the
    pooled client before ever reaching _offer_failure(), and would then be reported as
    an outage instead of the rejection it actually is. This is the regression guard for
    that: it fails if remove_offer() ever starts asking for the retrying session.
    """
    retrying = FakeSession()
    probe = FakeSession(FakeResponse(200, {}))

    with_both_sessions(retrying, probe, lambda:
        leagues.remove_offer("tok", LEAGUE_ID, PLAYER_ID, OWN_USER_ID))

    assert not retrying.calls, f"expected no call on the retrying session, got {retrying.calls}"
    assert len(probe.calls) == 1, f"expected the call on the probe session, got {probe.calls}"


def test_remove_offer_surfaces_the_api_message():
    def remove():
        leagues.remove_offer("tok", LEAGUE_ID, PLAYER_ID, OWN_USER_ID)

    try:
        with_session(FakeSession(FakeResponse(404, {"err": 1, "errMsg": "OfferNotFound"})),
                     remove)
    except exceptions.OfferRejectedException as e:
        assert e.status_code == 404, f"expected status 404, got {e.status_code}"
        assert "OfferNotFound" in str(e), f"expected the errMsg, got: {e}"
    else:
        raise AssertionError("expected an OfferRejectedException for a 404")


def test_remove_offer_reports_a_transport_failure_as_the_clients_own_exception_type():
    """Symmetric with place_offer(): see the matching test above for why."""
    import requests as real_requests

    def remove():
        leagues.remove_offer("tok", LEAGUE_ID, PLAYER_ID, OWN_USER_ID)

    try:
        with_session(FakeSession(raises=real_requests.exceptions.ConnectTimeout("timed out")),
                     remove)
    except exceptions.ApiUnreachableException:
        pass
    else:
        raise AssertionError("expected an ApiUnreachableException for a connection failure")


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
    """Run fn with PUBLIC_DIR pointed at a temporary market.json, and return its rows."""
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = path.join(tmp, "data")
        ts_dir = path.join(data_dir, "timestamps")
        from os import makedirs
        makedirs(ts_dir, exist_ok=True)

        with open(path.join(data_dir, "market.json"), "w") as f:
            json.dump(rows, f)

        original = (miscellaneous.PUBLIC_DIR, miscellaneous.STATE_DIR, miscellaneous.TIMESTAMP_DIR)
        miscellaneous.PUBLIC_DIR = data_dir
        miscellaneous.STATE_DIR = data_dir
        miscellaneous.TIMESTAMP_DIR = ts_dir
        try:
            result = fn()
            with open(path.join(data_dir, "market.json")) as f:
                return result, json.load(f)
        finally:
            miscellaneous.PUBLIC_DIR, miscellaneous.STATE_DIR, miscellaneous.TIMESTAMP_DIR = original


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
        original = miscellaneous.PUBLIC_DIR, miscellaneous.STATE_DIR
        miscellaneous.PUBLIC_DIR = path.join(tmp, "data")
        miscellaneous.STATE_DIR = path.join(tmp, "data")
        try:
            assert miscellaneous.patch_market_bid("8289", 1) is False
        finally:
            miscellaneous.PUBLIC_DIR, miscellaneous.STATE_DIR = original


def test_patch_survives_an_unreadable_file():
    """Finding 4: an OSError from open() (permissions, a stale mount) must not escape.

    By the time patch_market_bid() runs, the bid has already been placed and read back
    by the caller - a stale market.json is not a failed bid, so this has to answer
    False, the same contract every other "nothing was patched" branch here already
    honours, rather than raise and leave app.py's caller to turn it into a 500.
    """
    def raise_permission_denied(*a, **k):
        raise OSError(13, "Permission denied")

    with tempfile.TemporaryDirectory() as tmp:
        data_dir = path.join(tmp, "data")
        makedirs(data_dir, exist_ok=True)
        with open(path.join(data_dir, "market.json"), "w") as f:
            json.dump(market_rows(), f)

        original_data_dir = miscellaneous.PUBLIC_DIR, miscellaneous.STATE_DIR
        miscellaneous.PUBLIC_DIR = data_dir
        miscellaneous.STATE_DIR = data_dir
        ### Injected as a module global so it shadows the builtin only inside
        ### miscellaneous.py, without touching open() anywhere else in the process.
        miscellaneous.open = raise_permission_denied
        try:
            assert miscellaneous.patch_market_bid("8289", 1) is False
        finally:
            del miscellaneous.open
            miscellaneous.PUBLIC_DIR, miscellaneous.STATE_DIR = original_data_dir


def test_patch_propagates_a_write_failure_rather_than_swallowing_it():
    """write_json_to_file() now raises on a failed write instead of logging and
    swallowing it (see its docstring: "this used to swallow every write error and let
    the run report success over a file that was never written").

    patch_market_bid() must let that through rather than translating it into the same
    plain False a missing file or an unmatched player id gets - those two mean "nothing
    to patch"; this means "there was something to patch, and the write for it failed".
    app.py's callers (place_bid()/withdraw_bid()) are what turn this into the
    "could not confirm" response instead of an uncaught 500 - see
    test_post_patch_failure_does_not_confirm_the_bid below for that half of the fix.
    """
    def raise_disk_full(*a, **k):
        raise OSError(28, "No space left on device")

    original_write = miscellaneous.write_json_to_file
    miscellaneous.write_json_to_file = raise_disk_full
    try:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = path.join(tmp, "data")
            makedirs(data_dir, exist_ok=True)
            with open(path.join(data_dir, "market.json"), "w") as f:
                json.dump(market_rows(), f)

            original_data_dir = miscellaneous.PUBLIC_DIR, miscellaneous.STATE_DIR
            miscellaneous.PUBLIC_DIR = data_dir
            miscellaneous.STATE_DIR = data_dir
            try:
                try:
                    miscellaneous.patch_market_bid("8289", 1)
                except OSError:
                    pass
                else:
                    raise AssertionError(
                        "expected the write failure to propagate rather than return False")
            finally:
                miscellaneous.PUBLIC_DIR, miscellaneous.STATE_DIR = original_data_dir
    finally:
        miscellaneous.write_json_to_file = original_write


### ===============================================================================
### The endpoints
### ===============================================================================


def bid_row():
    """The market.json row client_with() seeds for PLAYER_ID.

    A successful POST/DELETE patches this row, and every test that reaches a 200
    reads it back to confirm the wiring - not just the JSON response.
    """
    return {"playerId": PLAYER_ID, "lastName": "Musah", "ownBid": None, "marketValue": 5000000}


def bid_headers():
    """The X-Bid-Token header that matches the token app.py currently holds.

    Read fresh on every call, rather than cached into a module constant, so the
    fail-closed test below (which monkeypatches flask_app.bid_token to None) is not
    fighting a stale value captured at import time. Every test that exercises the
    endpoints' existing behaviour (not the token check itself) sends this, so the token
    check passes through to whatever was already being tested. Always call this after
    client_with(), which is what sets flask_app.bid_token to TEST_BID_TOKEN in the first
    place - calling it before would read whatever BID_TOKEN happens to be in the
    environment, or None.
    """
    import app as flask_app
    return {"X-Bid-Token": flask_app.bid_token}


def client_with(market, place=None, remove=None, own_user_id=OWN_USER_ID, market_rows=None):
    """A Flask test client with login, market, the write calls and market.json faked out.

    PUBLIC_DIR/TIMESTAMP_DIR are redirected to a private temporary directory seeded with
    market_rows (a row for PLAYER_ID by default), so a call that reaches
    patch_market_bid() patches that copy rather than the real
    frontend/src/data/market.json. restore() removes it again.

    Also sets flask_app.bid_token to TEST_BID_TOKEN, restored the same way, so the whole
    suite is independent of whether BID_TOKEN is set in the environment - setting it via
    os.environ would only work by the accident that `import app` happens inside test
    functions rather than at module load time.
    """
    import app as flask_app
    import main

    class FakeUser:
        id = own_user_id
        name = "shirazzi"

    class FakeLeague:
        id = LEAGUE_ID
        name = "Test"

    tmp_root = tempfile.mkdtemp()
    data_dir = path.join(tmp_root, "data")
    ts_dir = path.join(data_dir, "timestamps")
    makedirs(ts_dir, exist_ok=True)
    with open(path.join(data_dir, "market.json"), "w") as f:
        json.dump(market_rows if market_rows is not None else [bid_row()], f)

    original = (flask_app.user.login, flask_app.leagues.get_league_list,
                flask_app.leagues.get_market, flask_app.leagues.place_offer,
                flask_app.leagues.remove_offer, main.select_league,
                miscellaneous.PUBLIC_DIR, miscellaneous.STATE_DIR, miscellaneous.TIMESTAMP_DIR, flask_app.bid_token,
                tmp_root)

    flask_app.user.login = lambda *a, **k: (FakeUser(), "tok")
    flask_app.leagues.get_league_list = lambda token: [FakeLeague()]
    main.select_league = lambda league_list: FakeLeague()
    flask_app.leagues.get_market = lambda token, lid: [Market_Players(i) for i in market()]
    flask_app.leagues.place_offer = place or (lambda *a, **k: {})
    flask_app.leagues.remove_offer = remove or (lambda *a, **k: None)
    miscellaneous.PUBLIC_DIR = data_dir
    miscellaneous.STATE_DIR = data_dir
    miscellaneous.TIMESTAMP_DIR = ts_dir
    flask_app.bid_token = TEST_BID_TOKEN

    flask_app.app.config["TESTING"] = True
    return flask_app.app.test_client(), original, data_dir


def restore(original):
    """Undo client_with()'s patching and remove its temporary data directory.

    Always call this in a finally block: a test that raises mid-way must not leave
    the data directories pointing at a temporary directory that no longer exists.
    """
    import app as flask_app
    import main
    (flask_app.user.login, flask_app.leagues.get_league_list, flask_app.leagues.get_market,
     flask_app.leagues.place_offer, flask_app.leagues.remove_offer,
     main.select_league, miscellaneous.PUBLIC_DIR, miscellaneous.STATE_DIR, miscellaneous.TIMESTAMP_DIR,
     flask_app.bid_token, tmp_root) = original
    shutil.rmtree(tmp_root, ignore_errors=True)


def read_own_bid(data_dir):
    """Read back ownBid for PLAYER_ID from the faked market.json in data_dir."""
    with open(path.join(data_dir, "market.json")) as f:
        rows = json.load(f)
    return next(row["ownBid"] for row in rows if str(row["playerId"]) == PLAYER_ID)


def post_bid(market, price, place=None):
    """POST a bid and return (status, body, own_bid_in_file).

    own_bid_in_file is read back from the faked market.json before restore() removes
    its temporary directory - reading it after would just be a FileNotFoundError.
    """
    client, original, data_dir = client_with(market, place=place)
    try:
        response = client.post(f"/api/market/{PLAYER_ID}/bid", json={"price": price},
                                headers=bid_headers())
        return response.status_code, response.get_json(), read_own_bid(data_dir)
    finally:
        restore(original)


def plain_market():
    return [market_item()]


def bid_market():
    return [market_item(ofs=[{"i": "77", "u": OWN_USER_ID, "uoid": OWN_USER_ID,
                              "uop": 5200000}])]


def market_then(first, second):
    """A market() fake answering `first` once, then `second` for every call after.

    Both write endpoints call get_market() twice per request now - once before the write
    to check the current state, once as the read-back that confirms it - and several
    tests need those two calls to answer differently. `second` (or `first`) may be a
    callable returning rows, or an exception instance to raise instead.
    """
    calls = []

    def market():
        calls.append(None)
        step = first if len(calls) == 1 else second

        if isinstance(step, Exception):
            raise step
        return step()

    return market


def test_post_rejects_a_non_positive_price():
    status, body, _ = post_bid(plain_market, 0)
    assert status == 400, f"expected 400, got {status} {body}"
    assert "error" in body and body["error"], f"expected a German message, got {body}"


def test_post_rejects_a_non_integer_price():
    status, body, _ = post_bid(plain_market, "viel")
    assert status == 400, f"expected 400, got {status} {body}"


def test_post_rejects_a_player_not_on_the_market():
    client, original, _ = client_with(plain_market)
    try:
        response = client.post("/api/market/999999/bid", json={"price": 1180000},
                                headers=bid_headers())
        assert response.status_code == 404, \
            f"expected 404, got {response.status_code} {response.get_json()}"
    finally:
        restore(original)


def test_post_refuses_an_own_listing():
    """Nobody bids on their own player, and the server must not rely on the browser."""
    def own_listing():
        return [market_item(u={"i": OWN_USER_ID, "n": "shirazzi"})]

    status, body, _ = post_bid(own_listing, 1180000)
    assert status == 409, f"expected 409, got {status} {body}"


def test_post_returns_the_bid_read_back_from_kickbase():
    """Not the typed value: a silently clamped bid would otherwise be shown as typed."""
    calls = []
    status, body, own_bid_in_file = post_bid(bid_market, 1180000,
                            place=lambda *a, **k: calls.append(a) or {})
    assert status == 200, f"expected 200, got {status} {body}"
    ### The faked market reports 5.200.000 regardless of what was sent
    assert body == {"ownBid": 5200000}, f"expected the read-back bid, got {body}"
    assert calls, "expected place_offer to have been called"
    assert own_bid_in_file == 5200000, \
        "expected the faked market.json row to carry the read-back bid"


def test_post_passes_the_kickbase_rejection_through():
    def rejecting(*a, **k):
        raise exceptions.OfferRejectedException(
            "Offer price is below the market value", status_code=400)

    status, body, _ = post_bid(plain_market, 1, place=rejecting)
    assert status == 400, f"expected the API status passed through, got {status}"
    assert "below the market value" in body["error"], \
        f"expected the API message passed through, got {body}"


def test_delete_withdraws_and_reports_no_bid():
    removed = []
    ### The pre-check must see the offer (else the endpoint would 409 before ever calling
    ### remove_offer); the read-back that follows the actual removal must not, since that
    ### is what a real successful withdrawal looks like once Kickbase applied it.
    market = market_then(bid_market, plain_market)
    ### Seeded with a pre-existing bid, so the ownBid-cleared assertion below proves the
    ### withdrawal actually happened rather than the row having started out empty.
    client, original, data_dir = client_with(
        market, remove=lambda *a, **k: removed.append(a),
        market_rows=[dict(bid_row(), ownBid=5200000)])
    try:
        response = client.delete(f"/api/market/{PLAYER_ID}/bid", headers=bid_headers())
        assert response.status_code == 200, \
            f"expected 200, got {response.status_code} {response.get_json()}"
        assert response.get_json() == {"ownBid": None}, \
            f"expected a cleared bid, got {response.get_json()}"
        assert removed, "expected remove_offer to have been called"
        assert read_own_bid(data_dir) is None, \
            "expected the faked market.json row's ownBid cleared"
    finally:
        restore(original)


def test_delete_without_a_bid_is_a_conflict():
    client, original, _ = client_with(plain_market)
    try:
        response = client.delete(f"/api/market/{PLAYER_ID}/bid", headers=bid_headers())
        assert response.status_code == 409, \
            f"expected 409, got {response.status_code} {response.get_json()}"
    finally:
        restore(original)


### ===============================================================================
### Confirming a write - Kickbase accepted it, but the interface must not guess
### ===============================================================================
###
### All three tests below share one shape: a write that Kickbase accepted, followed by a
### read-back that cannot confirm it (raises, or shows a state inconsistent with the
### write having landed). None of the three may report success, none may patch
### market.json, and all three must send the user to the Kickbase app instead of
### inviting a retry on an action that may already have gone through.


def test_post_read_back_failure_does_not_confirm_the_bid():
    """Finding 1: place_offer succeeds, but the read-back that follows it raises.

    Reproduces the live bug, updated for what get_market() actually raises after the
    merge with the shared HTTP client: exceptions.ApiUnreachableException, an
    HttpException subclass - never exceptions.NotificatonException, which get_market()
    has not raised since it moved onto http.get_json(). Before this fix, the handler
    only caught NotificatonException, so this exact exception fell through to the
    generic 502 below instead of the "could not confirm" one - the same live bug, just
    with the exception shape the merge actually produces rather than the one the
    original bug report happened to use.
    """
    market = market_then(plain_market, exceptions.ApiUnreachableException(
        "GET .../market timed out.", url="https://api.kickbase.com/v4/leagues/x/market"))
    status, body, own_bid_in_file = post_bid(market, 1180000)

    assert status == 502, f"expected 502, got {status} {body}"
    assert "Kickbase-App" in body.get("error", ""), \
        f"expected a message pointing at the Kickbase app, got {body}"
    assert "bevor du erneut bietest" in body.get("error", ""), \
        f"expected a warning against bidding again, got {body}"
    assert own_bid_in_file is None, \
        f"expected market.json left untouched, got ownBid={own_bid_in_file!r}"


def test_post_read_back_auth_failure_also_does_not_confirm_the_bid():
    """A different HttpException subclass than the one above - the catch in place_bid()
    has to be exceptions.HttpException itself, not one specific descendant of it, or an
    expired token during the read-back would fall through to the generic 502 exactly
    the way the original NotificatonException bug did.
    """
    market = market_then(plain_market, exceptions.AuthExpiredException(
        "GET .../market answered 401.", url="https://api.kickbase.com/v4/leagues/x/market",
        status_code=401))
    status, body, own_bid_in_file = post_bid(market, 1180000)

    assert status == 502, f"expected 502, got {status} {body}"
    assert "bevor du erneut bietest" in body.get("error", ""), \
        f"expected a warning against bidding again, got {body}"
    assert own_bid_in_file is None, \
        f"expected market.json left untouched, got ownBid={own_bid_in_file!r}"


def test_post_read_back_with_no_own_offer_does_not_confirm_the_bid():
    """Finding 2: place_offer succeeds, but the read-back shows no offer of ours.

    Before the fix this fell through as ownBid=null with HTTP 200 - byte-identical to
    what a successful withdrawal reports - and market.json was patched to null even
    though Kickbase had just accepted the bid.
    """
    status, body, own_bid_in_file = post_bid(plain_market, 1180000)

    assert status == 502, f"expected 502, got {status} {body}"
    assert "ownBid" not in body, \
        f"a placement must never answer with ownBid here, got {body}"
    assert "Kickbase-App" in body.get("error", ""), \
        f"expected a message pointing at the Kickbase app, got {body}"
    assert "bevor du erneut bietest" in body.get("error", ""), \
        f"expected a warning against bidding again, got {body}"
    assert own_bid_in_file is None, \
        f"expected market.json left untouched, got ownBid={own_bid_in_file!r}"


def test_delete_read_back_showing_the_offer_survived_does_not_confirm_the_withdrawal():
    """Finding 3: remove_offer answers success, but the read-back still shows our offer.

    Before the fix DELETE never read back at all: it patched market.json to null and
    answered {"ownBid": null} unconditionally, even if an idempotent 200 or a seller
    accepting the offer between the pre-read and the DELETE left it standing.
    """
    removed = []
    client, original, data_dir = client_with(
        bid_market, remove=lambda *a, **k: removed.append(a),
        market_rows=[dict(bid_row(), ownBid=5200000)])
    try:
        response = client.delete(f"/api/market/{PLAYER_ID}/bid", headers=bid_headers())
        body = response.get_json()

        assert response.status_code == 502, f"expected 502, got {response.status_code} {body}"
        assert "Kickbase-App" in body.get("error", ""), \
            f"expected a message pointing at the Kickbase app, got {body}"
        assert "bevor du erneut bietest" in body.get("error", ""), \
            f"expected a warning against bidding again, got {body}"
        assert removed, "expected remove_offer to have been called"
        assert read_own_bid(data_dir) == 5200000, \
            f"expected market.json left untouched, got ownBid={read_own_bid(data_dir)!r}"
    finally:
        restore(original)


def test_delete_read_back_failure_does_not_confirm_the_withdrawal():
    """The DELETE-side twin of test_post_read_back_failure_does_not_confirm_the_bid:
    remove_offer() succeeds, but the read-back that follows it raises outright, rather
    than merely showing a state that disagrees with the removal.
    """
    removed = []
    market = market_then(bid_market, exceptions.ApiUnreachableException(
        "GET .../market timed out.", url="https://api.kickbase.com/v4/leagues/x/market"))
    client, original, data_dir = client_with(
        market, remove=lambda *a, **k: removed.append(a),
        market_rows=[dict(bid_row(), ownBid=5200000)])
    try:
        response = client.delete(f"/api/market/{PLAYER_ID}/bid", headers=bid_headers())
        body = response.get_json()

        assert response.status_code == 502, f"expected 502, got {response.status_code} {body}"
        assert "bevor du erneut bietest" in body.get("error", ""), \
            f"expected a warning against bidding again, got {body}"
        assert removed, "expected remove_offer to have been called"
        assert read_own_bid(data_dir) == 5200000, \
            f"expected market.json left untouched, got ownBid={read_own_bid(data_dir)!r}"
    finally:
        restore(original)


### ===============================================================================
### A confirmed write whose local cache patch then fails
### ===============================================================================
###
### Distinct from the section above: here the bid or withdrawal is confirmed by the
### read-back - Kickbase's own state is known - and only patch_market_bid()'s write to
### market.json fails. write_json_to_file() now raises rather than logging and
### swallowing a write failure, so without this fix the exception would propagate past
### every handler in place_bid()/withdraw_bid() (none of them catch a bare OSError) as
### an uncaught 500 - which the frontend renders as "Flask API not reachable" with the
### draft still open, inviting a second bid on top of one that already landed. The fix
### answers the same "could not confirm" outcome the section above uses, even though
### here the write to Kickbase is not actually in doubt - only the local copy of it.


def test_post_patch_failure_does_not_confirm_the_bid():
    def raise_disk_full(*a, **k):
        raise OSError(28, "No space left on device")

    original_patch = miscellaneous.patch_market_bid
    miscellaneous.patch_market_bid = raise_disk_full
    try:
        status, body, own_bid_in_file = post_bid(bid_market, 1180000)
    finally:
        miscellaneous.patch_market_bid = original_patch

    assert status == 502, f"expected 502, got {status} {body}"
    assert "bevor du erneut bietest" in body.get("error", ""), \
        f"expected a warning against bidding again, got {body}"
    ### patch_market_bid() itself was replaced wholesale above, so it never touched the
    ### faked market.json at all - own_bid_in_file staying None is that, not evidence
    ### the real function would have left the row alone on a write failure (it cannot:
    ### the row is patched in place before write_json_to_file() is called).
    assert own_bid_in_file is None, f"got ownBid={own_bid_in_file!r}"


def test_delete_patch_failure_does_not_confirm_the_withdrawal():
    def raise_disk_full(*a, **k):
        raise OSError(28, "No space left on device")

    removed = []
    original_patch = miscellaneous.patch_market_bid
    miscellaneous.patch_market_bid = raise_disk_full
    client, original, data_dir = client_with(
        bid_market, remove=lambda *a, **k: removed.append(a),
        market_rows=[dict(bid_row(), ownBid=5200000)])
    try:
        response = client.delete(f"/api/market/{PLAYER_ID}/bid", headers=bid_headers())
        body = response.get_json()

        assert response.status_code == 502, f"expected 502, got {response.status_code} {body}"
        assert "bevor du erneut bietest" in body.get("error", ""), \
            f"expected a warning against bidding again, got {body}"
        assert removed, "expected remove_offer to have been called"
    finally:
        miscellaneous.patch_market_bid = original_patch
        restore(original)


### ===============================================================================
### CORS - regression guard for removing the blanket CORS(app) policy
### ===============================================================================


def test_cross_origin_preflight_grants_no_origin():
    """Without flask_cors, Flask's automatic OPTIONS handling adds no CORS headers.

    Guards against CORS(app) being reintroduced: that call reflects any Origin sent to
    it, so a cross-origin preflight would come back approved for that origin and any
    page could then POST/DELETE a bid. Needs no fixture - Flask answers OPTIONS itself
    without calling the view function, so no login/market faking applies here.
    """
    import app as flask_app

    response = flask_app.app.test_client().options(
        f"/api/market/{PLAYER_ID}/bid", headers={"Origin": "https://evil.example"})

    allowed_origin = response.headers.get("Access-Control-Allow-Origin")
    assert allowed_origin is None, \
        f"expected no Access-Control-Allow-Origin header, got {allowed_origin!r}"


### ===============================================================================
### X-Bid-Token
### ===============================================================================


def test_post_without_a_token_is_unauthorized():
    client, original, _ = client_with(plain_market)
    try:
        response = client.post(f"/api/market/{PLAYER_ID}/bid", json={"price": 1180000})
        assert response.status_code == 401, \
            f"expected 401, got {response.status_code} {response.get_json()}"
    finally:
        restore(original)


def test_post_with_the_wrong_token_is_unauthorized():
    client, original, _ = client_with(plain_market)
    try:
        response = client.post(f"/api/market/{PLAYER_ID}/bid", json={"price": 1180000},
                                headers={"X-Bid-Token": "definitely-wrong"})
        assert response.status_code == 401, \
            f"expected 401, got {response.status_code} {response.get_json()}"
    finally:
        restore(original)


def test_delete_without_a_token_is_unauthorized():
    client, original, _ = client_with(
        bid_market, market_rows=[dict(bid_row(), ownBid=5200000)])
    try:
        response = client.delete(f"/api/market/{PLAYER_ID}/bid")
        assert response.status_code == 401, \
            f"expected 401, got {response.status_code} {response.get_json()}"
    finally:
        restore(original)


def test_delete_with_the_wrong_token_is_unauthorized():
    client, original, _ = client_with(
        bid_market, market_rows=[dict(bid_row(), ownBid=5200000)])
    try:
        response = client.delete(f"/api/market/{PLAYER_ID}/bid",
                                  headers={"X-Bid-Token": "definitely-wrong"})
        assert response.status_code == 401, \
            f"expected 401, got {response.status_code} {response.get_json()}"
    finally:
        restore(original)


def test_an_unset_bid_token_no_longer_blocks_bidding():
    """BID_TOKEN used to be the only token, so an unset one had to fail closed with a 503.

    It is not the only one any more. app.py generates a token per start and hands it to the
    browser as a cookie with index.html, because the carrier BID_TOKEN used to have - the CRA
    dev server's proxy - does not run in a container any more. There is therefore no
    "unconfigured" state left: a request carrying the boot token works with BID_TOKEN unset,
    and one carrying anything else is the caller's mistake and stays 401.
    """
    import app as flask_app

    client, original, _ = client_with(bid_market)
    previous_token = flask_app.bid_token
    flask_app.bid_token = None
    try:
        accepted = client.post(f"/api/market/{PLAYER_ID}/bid", json={"price": 1180000},
                               headers={"X-Bid-Token": flask_app.BOOT_BID_TOKEN})
        assert accepted.status_code == 200, \
            f"expected the boot token to work, got {accepted.status_code} {accepted.get_json()}"

        refused = client.post(f"/api/market/{PLAYER_ID}/bid", json={"price": 1180000},
                              headers={"X-Bid-Token": "any-token-at-all"})
        assert refused.status_code == 401, \
            f"expected 401, got {refused.status_code} {refused.get_json()}"
    finally:
        flask_app.bid_token = previous_token
        restore(original)


def test_the_boot_token_is_accepted_alongside_the_configured_one():
    """Two carriers, both valid: the cookie the page was served with, and the env var the dev
    proxy attaches. Neither may lock the other out."""
    import app as flask_app

    client, original, _ = client_with(bid_market)
    try:
        for token in (flask_app.BOOT_BID_TOKEN, TEST_BID_TOKEN):
            response = client.post(f"/api/market/{PLAYER_ID}/bid", json={"price": 1180000},
                                   headers={"X-Bid-Token": token})
            assert response.status_code == 200, \
                f"expected {token[:8]}… to be accepted, got {response.status_code}"
    finally:
        restore(original)


def test_the_boot_token_is_not_a_guessable_constant():
    """It is what stands between a page on another origin and a bid in a real league, so it
    has to be generated rather than hardcoded, and long enough not to be guessed."""
    import app as flask_app

    assert len(flask_app.BOOT_BID_TOKEN) >= 32, \
        f"expected a long token, got {len(flask_app.BOOT_BID_TOKEN)} characters"
    assert flask_app.BOOT_BID_TOKEN != flask_app.bid_token, \
        "the generated token must not be the configured one"


def test_post_with_a_non_ascii_token_succeeds():
    """Finding 2: a legitimate non-ASCII BID_TOKEN must not crash the comparison.

    hmac.compare_digest() raises TypeError as soon as either argument is a non-ASCII
    str - reproducible with hmac.compare_digest("ä", "x") - and nothing in
    .env.example or README.md tells an operator to avoid an umlaut in the token they
    pick. Encoding both sides to bytes before comparing (the fix for this finding)
    means a matching non-ASCII token authenticates cleanly rather than 500ing on every
    single bid.
    """
    import app as flask_app

    ### The pre-check must see the offer (else the endpoint would 409 before reaching
    ### remove_offer); the read-back that follows must not, matching how a real
    ### successful withdrawal looks once Kickbase applied it - see
    ### test_delete_withdraws_and_reports_no_bid for the same shape without the token.
    client, original, data_dir = client_with(
        market_then(bid_market, plain_market), market_rows=[dict(bid_row(), ownBid=5200000)])
    previous_token = flask_app.bid_token
    non_ascii_token = "Bietertöken-äöü-ß"
    flask_app.bid_token = non_ascii_token
    try:
        response = client.delete(f"/api/market/{PLAYER_ID}/bid",
                                  headers={"X-Bid-Token": non_ascii_token})
        assert response.status_code == 200, \
            (f"expected the matching non-ASCII token to succeed, got "
             f"{response.status_code} {response.get_json()}")
    finally:
        flask_app.bid_token = previous_token
        restore(original)


def test_post_with_a_non_ascii_header_against_an_ascii_token_is_unauthorized():
    """Finding 2: a wrong non-ASCII header must fail cleanly at 401, not crash at 500.

    Werkzeug decodes headers as latin-1, so a caller sending "ä" arrives as an ordinary
    (non-ASCII) str, not bytes. Before encoding both sides of the comparison to bytes,
    this raised an uncaught TypeError - a 500 with no JSON error body, indistinguishable
    from the Flask API being unreachable at all.
    """
    client, original, _ = client_with(plain_market)
    try:
        response = client.post(f"/api/market/{PLAYER_ID}/bid", json={"price": 1180000},
                                headers={"X-Bid-Token": "ä-definitely-wrong"})
        assert response.status_code == 401, \
            f"expected a clean 401, got {response.status_code} {response.get_json()}"
    finally:
        restore(original)


def test_a_missing_token_is_401_rather_than_a_server_error():
    """A request with no header at all is the caller's mistake, not the server's.

    This used to have a sibling asserting that an unset BID_TOKEN answered 503 instead - see
    test_an_unset_bid_token_no_longer_blocks_bidding() for why that state no longer exists.
    """
    client, original, _ = client_with(plain_market)
    try:
        for headers in ({}, {"X-Bid-Token": ""}, {"X-Bid-Token": "definitely-wrong"}):
            response = client.post(f"/api/market/{PLAYER_ID}/bid", json={"price": 1180000},
                                   headers=headers)
            assert response.status_code == 401, \
                f"expected 401 for {headers}, got {response.status_code} {response.get_json()}"
            assert "BID_TOKEN" not in response.get_json().get("error", ""), \
                "the internal variable name should not be shown to the browser"
    finally:
        restore(original)


### ===============================================================================

def test_post_reports_a_transport_failure_on_the_write_as_unconfirmed():
    """A timeout on place_offer() itself cannot tell us whether the bid was recorded.

    ApiUnreachableException covers a refused connection (never arrived) and a read
    timeout (may have arrived, answer lost) alike, and the client does not distinguish
    them. Before this, the failure fell to the generic handler claiming "Kickbase konnte
    das Gebot nicht verarbeiten" - an assertion we cannot make, and one that invites a
    second bid on a bid that may be standing.
    """
    def unreachable(*a, **k):
        raise exceptions.ApiUnreachableException(
            "POST .../offers timed out.",
            url="https://api.kickbase.com/v4/leagues/x/market/1/offers")

    status, body, own_bid_in_file = post_bid(plain_market, 1180000, place=unreachable)

    assert status == 502, f"expected 502, got {status} {body}"
    assert "bevor du erneut bietest" in body.get("error", ""), \
        f"expected the unconfirmed warning, got {body}"
    assert "nicht verarbeiten" not in body.get("error", ""), \
        f"must not claim the bid failed, got {body}"
    assert own_bid_in_file is None, \
        f"expected market.json left untouched, got ownBid={own_bid_in_file!r}"


def test_delete_reports_a_transport_failure_on_the_write_as_unconfirmed():
    """The same for remove_offer(): the withdrawal may have been applied regardless."""
    def unreachable(*a, **k):
        raise exceptions.ApiUnreachableException(
            "DELETE .../offers timed out.",
            url="https://api.kickbase.com/v4/leagues/x/market/1/offers/2")

    ### The pre-check has to see the offer, or the endpoint 409s before remove_offer runs
    client, original, data_dir = client_with(
        market_then(bid_market, bid_market), remove=unreachable,
        market_rows=[dict(bid_row(), ownBid=5200000)])
    try:
        response = client.delete(f"/api/market/{PLAYER_ID}/bid", headers=bid_headers())
        body = response.get_json()
        assert response.status_code == 502, f"expected 502, got {response.status_code} {body}"
        assert "bevor du erneut bietest" in body.get("error", ""), \
            f"expected the unconfirmed warning, got {body}"
        ### The standing bid must survive: claiming it is gone is the wrong guess here
        assert read_own_bid(data_dir) == 5200000, \
            f"expected market.json left untouched, got {read_own_bid(data_dir)!r}"
    finally:
        restore(original)


def test_a_rejected_bid_is_not_reported_as_unconfirmed():
    """The ordering guard for the narrow catch above.

    OfferRejectedException is an HttpException too. Catching HttpException around the
    write instead of ApiUnreachableException would swallow a refusal Kickbase stated
    plainly and answer "we could not confirm" - throwing away both the normalised status
    and the German message _offer_failure() built from errMsg.
    """
    def rejecting(*a, **k):
        raise exceptions.OfferRejectedException(
            "Das Gebot liegt unter dem Marktwert.", status_code=400)

    status, body, _ = post_bid(plain_market, 1, place=rejecting)

    assert status == 400, f"expected the normalised 400, not an unconfirmed 502, got {status}"
    assert "Marktwert" in body.get("error", ""), \
        f"expected the rejection message, got {body}"
    assert "bevor du erneut bietest" not in body.get("error", ""), \
        f"a stated refusal is not unconfirmed, got {body}"


def test_a_transport_failure_before_the_write_is_not_reported_as_unconfirmed():
    """Nothing was sent yet, so "your bid may be standing" would be a false alarm.

    Pins the new catch to the write call only: it must not widen to cover the pre-write
    listing check.
    """
    market = market_then(
        exceptions.ApiUnreachableException(
            "GET .../market timed out.", url="https://api.kickbase.com/v4/leagues/x/market"),
        plain_market)
    status, body, own_bid_in_file = post_bid(market, 1180000)

    assert status == 502, f"expected 502, got {status} {body}"
    assert "bevor du erneut bietest" not in body.get("error", ""), \
        f"nothing was sent, so this must not warn about a standing bid: {body}"
    assert own_bid_in_file is None, \
        f"expected market.json left untouched, got ownBid={own_bid_in_file!r}"


if __name__ == "__main__":
    print("get_market()")
    check("sends a timeout", test_get_market_sends_a_timeout)

    print("\nplace_offer()")
    check("posts the price to the player", test_place_offer_posts_the_price_to_the_player)
    check("sends a timeout", test_place_offer_sends_a_timeout)
    check("does not use the retrying session", test_place_offer_does_not_use_the_retrying_session)
    check("translates a known error code", test_place_offer_translates_a_known_error_code)
    check("falls back to errMsg for an unknown code",
          test_place_offer_falls_back_to_errmsg_for_an_unknown_code)
    check("forwards a real outage as 502", test_place_offer_forwards_a_real_outage_as_502)
    check("passes a 4xx through unchanged", test_place_offer_passes_a_4xx_through_unchanged)
    check("reports a transport failure as the client's own exception type",
          test_place_offer_reports_a_transport_failure_as_the_clients_own_exception_type)

    print("\nremove_offer()")
    check("addresses the offer by user id", test_remove_offer_addresses_the_offer_by_user_id)
    check("never calls the bare collection", test_remove_offer_never_calls_the_bare_collection)
    check("does not use the retrying session", test_remove_offer_does_not_use_the_retrying_session)
    check("surfaces the API message", test_remove_offer_surfaces_the_api_message)
    check("reports a transport failure as the client's own exception type",
          test_remove_offer_reports_a_transport_failure_as_the_clients_own_exception_type)

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
    check("survives an unreadable file", test_patch_survives_an_unreadable_file)
    check("propagates a write failure rather than swallowing it",
          test_patch_propagates_a_write_failure_rather_than_swallowing_it)

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

    print("\nConfirming a write")
    check("POST read-back failure does not confirm the bid",
          test_post_read_back_failure_does_not_confirm_the_bid)
    check("POST read-back auth failure also does not confirm the bid",
          test_post_read_back_auth_failure_also_does_not_confirm_the_bid)
    check("POST read-back with no own offer does not confirm the bid",
          test_post_read_back_with_no_own_offer_does_not_confirm_the_bid)
    check("DELETE read-back showing the offer survived does not confirm the withdrawal",
          test_delete_read_back_showing_the_offer_survived_does_not_confirm_the_withdrawal)
    check("DELETE read-back failure does not confirm the withdrawal",
          test_delete_read_back_failure_does_not_confirm_the_withdrawal)

    print("\nA confirmed write whose local cache patch then fails")
    check("POST patch failure does not confirm the bid",
          test_post_patch_failure_does_not_confirm_the_bid)
    check("DELETE patch failure does not confirm the withdrawal",
          test_delete_patch_failure_does_not_confirm_the_withdrawal)

    print("\nCORS")
    check("cross-origin preflight grants no origin",
          test_cross_origin_preflight_grants_no_origin)

    print("\nX-Bid-Token")
    check("POST without a token is unauthorized", test_post_without_a_token_is_unauthorized)
    check("POST with the wrong token is unauthorized",
          test_post_with_the_wrong_token_is_unauthorized)
    check("DELETE without a token is unauthorized",
          test_delete_without_a_token_is_unauthorized)
    check("DELETE with the wrong token is unauthorized",
          test_delete_with_the_wrong_token_is_unauthorized)
    check("an unset BID_TOKEN no longer blocks bidding",
          test_an_unset_bid_token_no_longer_blocks_bidding)
    check("the boot token is accepted alongside the configured one",
          test_the_boot_token_is_accepted_alongside_the_configured_one)
    check("the boot token is generated, not hardcoded",
          test_the_boot_token_is_not_a_guessable_constant)
    check("a non-ASCII token succeeds", test_post_with_a_non_ascii_token_succeeds)
    check("a non-ASCII header against an ASCII token is unauthorized",
          test_post_with_a_non_ascii_header_against_an_ascii_token_is_unauthorized)
    check("a missing or wrong token is 401", test_a_missing_token_is_401_rather_than_a_server_error)
    check("a transport failure on the POST write is unconfirmed",
          test_post_reports_a_transport_failure_on_the_write_as_unconfirmed)
    check("a transport failure on the DELETE write is unconfirmed",
          test_delete_reports_a_transport_failure_on_the_write_as_unconfirmed)
    check("a rejected bid is not reported as unconfirmed",
          test_a_rejected_bid_is_not_reported_as_unconfirmed)
    check("a transport failure before the write is not unconfirmed",
          test_a_transport_failure_before_the_write_is_not_reported_as_unconfirmed)

    total, passed = len(PASSED), sum(PASSED)
    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)
