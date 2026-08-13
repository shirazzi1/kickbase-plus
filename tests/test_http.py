"""Tests for the shared HTTP layer.

Every Kickbase call used to look like this:

    try:
        json_response = requests.get(url, headers=headers).json()
    except:
        raise exceptions.NotificatonException("Notification failed! Please check your Discord Webhook URL.")

No timeout on any of the fifteen call sites, no retries, and one exception for every
possible cause - naming a Discord webhook that had nothing to do with any of them.

The three cases the plan named are the first three sections below: 401 becomes an auth
error, 429 is retried honouring Retry-After, and a hung socket becomes a timeout instead
of parking the run forever.

    ./venv/bin/python tests/test_http.py
"""

import sys

from os import path

### Make the repository root importable regardless of where this is run from
sys.path.insert(0, path.dirname(path.dirname(path.abspath(__file__))))

import requests

from backend import exceptions
from backend.kickbase import http

### ===============================================================================

PASSED = []

URL = "https://api.kickbase.com/v4/leagues/selection"


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
    """Stands in for a requests.Response."""

    def __init__(self, payload=None, status_code=200, headers=None, body=None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}
        self._body = body

    def json(self):
        if self._body is not None:
            ### What .json() does with a body that is not JSON, e.g. an HTML error page
            raise ValueError(f"Expecting value: {self._body[:20]}")
        return self._payload


class FakeSession:
    """Stands in for the pooled session and records every call."""

    def __init__(self, responses=None, raises=None):
        ### Either a list consumed one per call, or a single response reused
        self.responses = responses
        self.raises = raises
        self.calls = []

    def _answer(self, method, url, headers, timeout, payload=None):
        self.calls.append({"method": method, "url": url, "headers": headers,
                           "timeout": timeout, "payload": payload})

        if self.raises is not None:
            raise self.raises

        if isinstance(self.responses, list):
            return self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]

        return self.responses

    def get(self, url, headers=None, timeout=None):
        return self._answer("GET", url, headers, timeout)

    def post(self, url, json=None, headers=None, timeout=None):
        return self._answer("POST", url, headers, timeout, payload=json)


def with_session(fake, fn):
    """Run fn() with the pooled session replaced."""
    http.reset_session(fake)
    try:
        return fn()
    finally:
        http.reset_session()


def expect_raises(exception_type, fn):
    """Assert that fn() raises the given exception type and return the exception."""
    try:
        fn()
    except exception_type as e:
        return e
    except Exception as e:
        raise AssertionError(f"expected {exception_type.__name__}, got {type(e).__name__}: {e}")

    raise AssertionError(f"expected {exception_type.__name__}, nothing was raised")


### ===============================================================================
### Timeouts
### ===============================================================================


def test_every_request_carries_a_timeout():
    """Not one of the fifteen call sites had one, and requests waits forever by default."""
    fake = FakeSession(FakeResponse({"it": []}))
    with_session(fake, lambda: http.get_json(URL, "token"))

    assert fake.calls[0]["timeout"] == http.DEFAULT_TIMEOUT, \
        f"expected {http.DEFAULT_TIMEOUT}, got {fake.calls[0]['timeout']}"


def test_the_timeout_bounds_both_halves():
    connect, read = http.DEFAULT_TIMEOUT
    assert connect > 0 and read > 0, f"both halves must be finite, got {http.DEFAULT_TIMEOUT}"


def test_a_hung_socket_becomes_an_unreachable_error():
    fake = FakeSession(raises=requests.exceptions.Timeout("timed out"))

    error = with_session(fake, lambda: expect_raises(
        exceptions.ApiUnreachableException, lambda: http.get_json(URL, "token")))

    assert URL in str(error), f"the message must name the call, got {error}"


def test_a_refused_connection_becomes_an_unreachable_error():
    fake = FakeSession(raises=requests.exceptions.ConnectionError("refused"))

    with_session(fake, lambda: expect_raises(
        exceptions.ApiUnreachableException, lambda: http.get_json(URL, "token")))


def test_an_unreachable_api_is_still_a_kickbase_exception():
    """main() and app.py catch KickbaseException, and that must keep working."""
    assert issubclass(exceptions.ApiUnreachableException, exceptions.KickbaseException)


### ===============================================================================
### Status codes
### ===============================================================================


def test_401_becomes_an_auth_error():
    fake = FakeSession(FakeResponse(status_code=401))

    error = with_session(fake, lambda: expect_raises(
        exceptions.AuthExpiredException, lambda: http.get_json(URL, "expired")))

    assert error.status_code == 401, f"got {error.status_code}"


def test_403_becomes_an_auth_error_too():
    fake = FakeSession(FakeResponse(status_code=403))

    with_session(fake, lambda: expect_raises(
        exceptions.AuthExpiredException, lambda: http.get_json(URL, "token")))


def test_429_becomes_a_rate_limit_error():
    fake = FakeSession(FakeResponse(status_code=429, headers={"Retry-After": "30"}))

    error = with_session(fake, lambda: expect_raises(
        exceptions.RateLimitedException, lambda: http.get_json(URL, "token")))

    assert "30" in str(error), f"the message should carry Retry-After, got {error}"


def test_another_4xx_becomes_a_request_error():
    """The one error a caller may read as an answer: _fetch_marketvalue() does."""
    fake = FakeSession(FakeResponse(status_code=400))

    error = with_session(fake, lambda: expect_raises(
        exceptions.ApiRequestException, lambda: http.get_json(URL, "token")))

    assert error.status_code == 400, f"got {error.status_code}"


def test_a_404_is_not_an_auth_error():
    fake = FakeSession(FakeResponse(status_code=404))

    with_session(fake, lambda: expect_raises(
        exceptions.ApiRequestException, lambda: http.get_json(URL, "token")))


def test_5xx_becomes_an_unavailable_error():
    fake = FakeSession(FakeResponse(status_code=503))

    error = with_session(fake, lambda: expect_raises(
        exceptions.ApiUnavailableException, lambda: http.get_json(URL, "token")))

    assert error.status_code == 503, f"got {error.status_code}"


def test_the_error_types_are_distinct():
    """The whole point: an expired token used to look like a hung socket."""
    for wrong, right in ((exceptions.AuthExpiredException, exceptions.RateLimitedException),
                         (exceptions.RateLimitedException, exceptions.ApiUnavailableException),
                         (exceptions.ApiRequestException, exceptions.ApiUnreachableException)):
        assert not issubclass(right, wrong), f"{right.__name__} must not be a {wrong.__name__}"


### ===============================================================================
### Bodies
### ===============================================================================


def test_a_body_that_is_not_json_becomes_a_response_error():
    """Kickbase answers HTML on some error paths, and .json() then raises."""
    fake = FakeSession(FakeResponse(body="<html>Gateway Timeout</html>"))

    with_session(fake, lambda: expect_raises(
        exceptions.ApiResponseException, lambda: http.get_json(URL, "token")))


def test_a_good_body_comes_back_decoded():
    fake = FakeSession(FakeResponse({"it": [{"i": "1"}]}))

    result = with_session(fake, lambda: http.get_json(URL, "token"))

    assert result == {"it": [{"i": "1"}]}, f"got {result}"


### ===============================================================================
### Headers
### ===============================================================================


def test_the_token_travels_as_the_kickbase_cookie():
    fake = FakeSession(FakeResponse({}))
    with_session(fake, lambda: http.get_json(URL, "abc123"))

    assert fake.calls[0]["headers"]["Cookie"] == "kkstrauth=abc123;", \
        f"got {fake.calls[0]['headers']}"


def test_a_call_without_a_token_sends_no_cookie():
    """The login itself has no token yet."""
    fake = FakeSession(FakeResponse({}))
    with_session(fake, lambda: http.post_json(URL, {"em": "a"}))

    assert "Cookie" not in fake.calls[0]["headers"], f"got {fake.calls[0]['headers']}"


def test_extra_headers_are_merged_in():
    """player_statistics() asks for German, and the status note reverts to English without it."""
    fake = FakeSession(FakeResponse({}))
    with_session(fake, lambda: http.get_json(URL, "token",
                                             extra_headers={"Accept-Language": "de-DE"}))

    headers = fake.calls[0]["headers"]
    assert headers["Accept-Language"] == "de-DE", f"got {headers}"
    assert headers["Accept"] == "application/json", f"the defaults must survive, got {headers}"


def test_post_sends_the_payload():
    fake = FakeSession(FakeResponse({"tkn": "t"}))
    result = with_session(fake, lambda: http.post_json(URL, {"em": "a@b.c", "pass": "x"}))

    assert fake.calls[0]["payload"] == {"em": "a@b.c", "pass": "x"}, f"got {fake.calls[0]}"
    assert result == {"tkn": "t"}, f"got {result}"


### ===============================================================================
### The retry policy
### ===============================================================================


def test_the_retry_policy_covers_rate_limits_and_server_errors():
    retries = http.session().get_adapter("https://api.kickbase.com").max_retries

    assert retries.total == http.RETRY_TOTAL, f"got {retries.total}"
    assert set(retries.status_forcelist) == set(http.RETRY_STATUS_FORCELIST), \
        f"got {retries.status_forcelist}"


def test_the_retry_policy_honours_retry_after():
    """Kickbase says how long to wait; guessing instead is what gets an account banned."""
    retries = http.session().get_adapter("https://api.kickbase.com").max_retries

    assert retries.respect_retry_after_header is True


def test_the_retry_policy_waits_longer_each_time():
    retries = http.session().get_adapter("https://api.kickbase.com").max_retries

    assert retries.backoff_factor > 0, f"got {retries.backoff_factor}"


def test_the_exhausted_retry_still_carries_its_status():
    """With raise_on_status=True the status is lost, and 429 cannot be told from 503."""
    retries = http.session().get_adapter("https://api.kickbase.com").max_retries

    assert retries.raise_on_status is False


def test_a_post_is_not_repeated():
    """A login is not a request to send twice on a whim."""
    retries = http.session().get_adapter("https://api.kickbase.com").max_retries

    assert "POST" not in retries.allowed_methods, f"got {retries.allowed_methods}"
    assert "GET" in retries.allowed_methods, f"got {retries.allowed_methods}"


def test_the_probe_session_does_not_retry():
    """get_team_overview() probes 97 team ids, most of which do not exist."""
    retries = http.session(retry=False).get_adapter("https://api.kickbase.com").max_retries

    assert retries.total == 0, f"got {retries.total}"


def test_the_session_is_pooled_rather_than_rebuilt():
    """A fresh TLS handshake per call is the traffic shape that gets an account throttled."""
    assert http.session() is http.session(), "the retrying session must be reused"
    assert http.session(retry=False) is http.session(retry=False), \
        "the probe session must be reused"
    assert http.session() is not http.session(retry=False), \
        "the two policies must not share one session"


### ===============================================================================
### The Discord webhook
### ===============================================================================


def test_the_discord_webhook_has_a_timeout_too():
    """The one other outbound call. Without a timeout a stalled Discord parks the run."""
    fake = FakeSession(FakeResponse({}, status_code=204))
    with_session(fake, lambda: http.post_no_json("https://discord.test/hook", {"a": 1}))

    assert fake.calls[0]["timeout"] == http.DEFAULT_TIMEOUT, f"got {fake.calls[0]}"


def test_the_discord_webhook_sends_no_kickbase_headers():
    fake = FakeSession(FakeResponse({}, status_code=204))
    with_session(fake, lambda: http.post_no_json("https://discord.test/hook", {"a": 1}))

    assert "Cookie" not in fake.calls[0]["headers"], f"got {fake.calls[0]['headers']}"


### ===============================================================================
### No module talks to requests directly any more
### ===============================================================================


### The modules that must not talk to the network on their own any more
V4_MODULES = ("backend/kickbase/v4/leagues.py", "backend/kickbase/v4/competitions.py",
              "backend/kickbase/v4/user.py")


def parse(module):
    """Parse one project module into an AST."""
    import ast

    root = path.dirname(path.dirname(path.abspath(__file__)))
    with open(path.join(root, module)) as f:
        return ast.parse(f.read(), filename=module)


def test_no_kickbase_module_calls_requests_directly():
    """Every call site has to go through here, or it has no timeout and no retries.

    Read off the syntax tree rather than grepped, so a mention in a docstring - this file
    and http.py both quote the old pattern - does not read as a call site.
    """
    import ast

    offenders = []

    for module in V4_MODULES:
        for node in ast.walk(parse(module)):
            if not isinstance(node, ast.Call):
                continue

            function = node.func
            if (isinstance(function, ast.Attribute)
                    and isinstance(function.value, ast.Name)
                    and function.value.id == "requests"):
                offenders.append(f"{module}:{node.lineno} requests.{function.attr}()")

    assert offenders == [], f"these still bypass the HTTP layer: {offenders}"


def test_no_bare_excepts_are_left_in_the_kickbase_modules():
    """`except:` swallowed the cause and replaced it with a Discord webhook complaint."""
    import ast

    offenders = []

    for module in V4_MODULES + ("backend/kickbase/http.py",):
        for node in ast.walk(parse(module)):
            if isinstance(node, ast.ExceptHandler) and node.type is None:
                offenders.append(f"{module}:{node.lineno}")

    assert offenders == [], f"bare excepts left: {offenders}"


def test_the_misleading_discord_message_is_gone():
    """It was raised for expired tokens, rate limits and hung sockets alike."""
    root = path.dirname(path.dirname(path.abspath(__file__)))
    offenders = []

    for module in V4_MODULES:
        with open(path.join(root, module)) as f:
            if "Please check your Discord Webhook URL" in f.read():
                offenders.append(module)

    assert offenders == [], f"the misleading message is still raised in: {offenders}"


### ===============================================================================

if __name__ == "__main__":
    print("timeouts")
    check("every request carries a timeout", test_every_request_carries_a_timeout)
    check("the timeout bounds both halves", test_the_timeout_bounds_both_halves)
    check("a hung socket becomes an unreachable error", test_a_hung_socket_becomes_an_unreachable_error)
    check("a refused connection becomes an unreachable error", test_a_refused_connection_becomes_an_unreachable_error)
    check("an unreachable API is still a KickbaseException", test_an_unreachable_api_is_still_a_kickbase_exception)

    print("\nstatus codes")
    check("401 becomes an auth error", test_401_becomes_an_auth_error)
    check("403 becomes an auth error too", test_403_becomes_an_auth_error_too)
    check("429 becomes a rate limit error", test_429_becomes_a_rate_limit_error)
    check("another 4xx becomes a request error", test_another_4xx_becomes_a_request_error)
    check("a 404 is not an auth error", test_a_404_is_not_an_auth_error)
    check("5xx becomes an unavailable error", test_5xx_becomes_an_unavailable_error)
    check("the error types are distinct", test_the_error_types_are_distinct)

    print("\nbodies")
    check("a body that is not JSON becomes a response error", test_a_body_that_is_not_json_becomes_a_response_error)
    check("a good body comes back decoded", test_a_good_body_comes_back_decoded)

    print("\nheaders")
    check("the token travels as the Kickbase cookie", test_the_token_travels_as_the_kickbase_cookie)
    check("a call without a token sends no cookie", test_a_call_without_a_token_sends_no_cookie)
    check("extra headers are merged in", test_extra_headers_are_merged_in)
    check("post sends the payload", test_post_sends_the_payload)

    print("\nthe retry policy")
    check("covers rate limits and server errors", test_the_retry_policy_covers_rate_limits_and_server_errors)
    check("honours Retry-After", test_the_retry_policy_honours_retry_after)
    check("waits longer each time", test_the_retry_policy_waits_longer_each_time)
    check("the exhausted retry still carries its status", test_the_exhausted_retry_still_carries_its_status)
    check("a POST is not repeated", test_a_post_is_not_repeated)
    check("the probe session does not retry", test_the_probe_session_does_not_retry)
    check("the session is pooled rather than rebuilt", test_the_session_is_pooled_rather_than_rebuilt)

    print("\nthe Discord webhook")
    check("has a timeout too", test_the_discord_webhook_has_a_timeout_too)
    check("sends no Kickbase headers", test_the_discord_webhook_sends_no_kickbase_headers)

    print("\nno module talks to requests directly")
    check("no Kickbase module calls requests directly", test_no_kickbase_module_calls_requests_directly)
    check("no bare excepts are left", test_no_bare_excepts_are_left_in_the_kickbase_modules)
    check("the misleading Discord message is gone", test_the_misleading_discord_message_is_gone)

    total, passed = len(PASSED), sum(PASSED)
    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)
