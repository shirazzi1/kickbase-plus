"""
### The one HTTP client every Kickbase API call goes through.

Before this module every call in `backend/kickbase/v4/` looked like this:

```python
try:
    json_response = requests.get(url, headers=headers).json()
except:
    raise exceptions.NotificatonException("Notification failed! Please check your Discord Webhook URL.")
```

Three separate problems in four lines:

  - **No timeout.** Not one of the fifteen call sites had one, so a socket that never
    answers parks the scheduler until the container is restarted. `requests` waits
    forever by default.
  - **No retries.** A single 502 during a run of roughly a thousand requests took the
    whole run down.
  - **One error for everything.** An expired token, a rate limit and a hung socket all
    arrived as the same exception, naming a Discord webhook that had nothing to do with
    any of them.

Every request made through this module has a timeout, retries what is worth retrying,
and fails with an exception that says what happened. See `backend/exceptions.py` for the
types.
"""

import logging
import requests

from requests.adapters import HTTPAdapter
from urllib.parse import urlsplit
from urllib3.util.retry import Retry

from backend import exceptions

### ===============================================================================

### Seconds to wait for the connection and then for the response. The read half is
### generous because some Kickbase endpoints are genuinely slow; the point is that it is
### finite at all.
DEFAULT_TIMEOUT = (5, 30)

### What is worth repeating: a rate limit, and the server errors that are transient by
### definition. A 4xx other than 429 means the request itself is wrong, and sending it
### again unchanged only spends the rate limit budget.
RETRY_TOTAL = 3
RETRY_BACKOFF_FACTOR = 1
RETRY_STATUS_FORCELIST = (429, 500, 502, 503, 504)

### Only methods that can be repeated without a second side effect. POST - which here
### means the login and the Discord webhook - is not among them, and the error messages
### read this same set so they cannot claim retries that never happened.
RETRY_METHODS = frozenset(["GET", "HEAD", "OPTIONS", "PUT", "DELETE", "TRACE"])

### How many connections to keep alive. A run walks every player in the competition, so
### the pool wants to be big enough that the concurrent prefetches do not evict each
### other's connections and hand back the TLS handshake savings.
POOL_MAXSIZE = 32

### What every Kickbase error message says about a rejected token. Passed in rather than
### hardcoded in _raise_for_status(), because that function also serves the Discord
### webhook, where a 401 has nothing to do with any Kickbase token.
KICKBASE_AUTH_MESSAGE = "The Kickbase token was rejected."
WEBHOOK_AUTH_MESSAGE = "The webhook rejected it - it may have been deleted or its token rotated."

_session = None
_probe_session = None


def host_only(url: str) -> str:
    """### Reduce a URL to scheme and host, dropping path and query.

    For the Discord webhook the path *is* the secret: whoever has the full URL can post
    into the channel. It must not reach a log file or a pasted stack trace, and both
    happen - main.py prints the exception, and _announce_login_failure() logs it.

    Args:
        url (str): The URL to reduce.

    Returns:
        str: Scheme and host, e.g. "https://discord.com".
    """
    parts = urlsplit(url)

    if not parts.netloc:
        return "<url>"

    return f"{parts.scheme}://{parts.netloc}"


def _build_session(retry: bool) -> requests.Session:
    """### Build a pooled session, with or without the retry policy.

    Args:
        retry (bool): Whether to retry 429 and 5xx responses.

    Returns:
        requests.Session: A session with the adapter mounted for HTTPS and HTTP.
    """
    session = requests.Session()

    if retry:
        ### raise_on_status=False on purpose. With the default the exhausted retry
        ### surfaces as a RetryError that no longer carries the status, so a 429 that
        ### survived every retry could not be told from a 503 that did. Handing the last
        ### response back instead is what makes the typed exceptions below possible.
        retries = Retry(
            total=RETRY_TOTAL,
            backoff_factor=RETRY_BACKOFF_FACTOR,
            status_forcelist=RETRY_STATUS_FORCELIST,
            respect_retry_after_header=True,
            raise_on_status=False,
            allowed_methods=RETRY_METHODS,
        )
    else:
        retries = Retry(total=0, raise_on_status=False)

    adapter = HTTPAdapter(max_retries=retries, pool_maxsize=POOL_MAXSIZE)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    return session


def session(retry: bool = True) -> requests.Session:
    """### The pooled session, built on first use.

    Args:
        retry (bool): False for probe style calls, where a failure is an answer and
            waiting out a backoff is wasted time.

    Returns:
        requests.Session: The shared session.
    """
    global _session, _probe_session

    if retry:
        if _session is None:
            _session = _build_session(retry=True)
        return _session

    if _probe_session is None:
        _probe_session = _build_session(retry=False)
    return _probe_session


def reset_session(replacement: requests.Session = None, probe_replacement: requests.Session = None) -> None:
    """### Replace or drop the pooled sessions.

    Tests inject a stub through this; passing nothing drops the sessions so the next
    call builds a real one.

    Args:
        replacement (requests.Session): The session to use for retrying calls.
        probe_replacement (requests.Session): The session to use for probe calls.
            Defaults to whatever `replacement` is, since a stub rarely needs to tell
            them apart.
    """
    global _session, _probe_session

    _session = replacement
    _probe_session = probe_replacement if probe_replacement is not None else replacement


def kickbase_headers(token: str = None, extra: dict = None) -> dict:
    """### The headers every Kickbase call sends.

    Args:
        token (str): The user's kkstrauth token. Omitted for the login itself.
        extra (dict): Additional headers, e.g. the Accept-Language that decides the
            language of the status note in player_statistics().

    Returns:
        dict: The headers.
    """
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    if token:
        headers["Cookie"] = f"kkstrauth={token};"

    if extra:
        headers.update(extra)

    return headers


def get_json(url: str, token: str = None, timeout=DEFAULT_TIMEOUT, extra_headers: dict = None,
             retry: bool = True):
    """### GET a URL and return the decoded JSON body.

    Args:
        url (str): The URL to call.
        token (str): The user's kkstrauth token.
        timeout: Connect and read timeout, as requests takes it.
        extra_headers (dict): Additional headers.
        retry (bool): False for probe style calls that expect to fail often.

    Raises:
        exceptions.AuthExpiredException: The token was rejected (401/403).
        exceptions.RateLimitedException: Still 429 after the retries.
        exceptions.ApiRequestException: Any other 4xx.
        exceptions.ApiUnavailableException: Still 5xx after the retries.
        exceptions.ApiUnreachableException: No answer at all.
        exceptions.ApiResponseException: The body was not JSON.

    Returns:
        The decoded JSON body.
    """
    return _request("GET", url, token=token, timeout=timeout, extra_headers=extra_headers,
                    retry=retry)


def post_json(url: str, payload: dict, token: str = None, timeout=DEFAULT_TIMEOUT,
              extra_headers: dict = None):
    """### POST a JSON payload and return the decoded JSON body.

    The retry policy leaves POST out on purpose - a POST cannot be assumed to be
    repeatable - so only a connection that was never established is retried here. Raises
    the same exceptions as get_json().

    Args:
        url (str): The URL to call.
        payload (dict): The JSON body to send.
        token (str): The user's kkstrauth token.
        timeout: Connect and read timeout, as requests takes it.
        extra_headers (dict): Additional headers.

    Returns:
        The decoded JSON body.
    """
    return _request("POST", url, payload=payload, token=token, timeout=timeout,
                    extra_headers=extra_headers, retry=True)


def _request(method: str, url: str, payload: dict = None, token: str = None,
             timeout=DEFAULT_TIMEOUT, extra_headers: dict = None, retry: bool = True):
    """### Send one request and turn whatever comes back into JSON or a typed exception.

    Args:
        method (str): "GET" or "POST".
        url (str): The URL to call.
        payload (dict): The JSON body, for POST.
        token (str): The user's kkstrauth token.
        timeout: Connect and read timeout, as requests takes it.
        extra_headers (dict): Additional headers.
        retry (bool): Which pooled session to use.

    Returns:
        The decoded JSON body.
    """
    headers = kickbase_headers(token, extra_headers)

    try:
        if method == "POST":
            response = session(retry).post(url, json=payload, headers=headers, timeout=timeout)
        else:
            response = session(retry).get(url, headers=headers, timeout=timeout)
    except requests.exceptions.Timeout as e:
        raise exceptions.ApiUnreachableException(
            f"{method} {url} timed out after {timeout}.", url=url) from e
    except requests.exceptions.RequestException as e:
        ### Covers connection errors, DNS failures and a retry budget spent on
        ### connection level errors rather than on statuses
        raise exceptions.ApiUnreachableException(
            f"{method} {url} could not be reached: {e}", url=url) from e

    _raise_for_status(method, url, response, KICKBASE_AUTH_MESSAGE, was_retried(method, retry))

    try:
        return response.json()
    except ValueError as e:
        ### Kickbase answers HTML on some error paths, and .json() then raises where the
        ### old bare except turned it into a Discord webhook complaint
        raise exceptions.ApiResponseException(
            f"{method} {url} answered {response.status_code} with a body that is not JSON.",
            url=url, status_code=response.status_code) from e


def was_retried(method: str, retry: bool) -> bool:
    """### Whether a failed request of this kind was actually repeated.

    A message claiming "still failing after 3 retries" about a POST is wrong twice over:
    the retry policy leaves POST out, so there was not one repeat. Naming the wrong cause
    is the habit this whole module exists to break, and it does not get an exception for
    its own error messages.

    Args:
        method (str): The HTTP method used.
        retry (bool): Whether the retrying session was used at all.

    Returns:
        bool: True if the retry policy applied to this request.
    """
    return retry and method in RETRY_METHODS


def _raise_for_status(method: str, url: str, response, auth_message: str, retried: bool) -> None:
    """### Turn an error status into the exception that describes it.

    Args:
        method (str): "GET" or "POST", for the message.
        url (str): The URL that was called. Already reduced to its host where the path
            carries a secret - see host_only().
        response: The response to judge.
        auth_message (str): What a 401 or 403 means for this caller. Kickbase and the
            Discord webhook have nothing in common here beyond the status code.
        retried (bool): Whether the request was actually repeated, from was_retried().
    """
    status = response.status_code

    if status < 400:
        return

    message = f"{method} {url} answered {status}."

    ### Only claim the retries that really happened
    after_retries = f" Still failing after {RETRY_TOTAL} retries." if retried else ""

    if status in (401, 403):
        raise exceptions.AuthExpiredException(
            f"{message} {auth_message}", url=url, status_code=status)

    if status == 429:
        retry_after = response.headers.get("Retry-After")
        waited = f" Retry-After was {retry_after}." if retry_after else ""
        limited = f" Still rate limited after {RETRY_TOTAL} retries." if retried else ""
        raise exceptions.RateLimitedException(
            f"{message}{limited}{waited}", url=url, status_code=status)

    if status < 500:
        raise exceptions.ApiRequestException(message, url=url, status_code=status)

    raise exceptions.ApiUnavailableException(
        f"{message}{after_retries}", url=url, status_code=status)


def post_no_json(url: str, payload: dict, timeout=DEFAULT_TIMEOUT) -> None:
    """### POST a JSON payload where the answer does not matter.

    The Discord webhook. It is not a Kickbase call, so it gets none of the Kickbase
    headers, none of the Kickbase wording, and its URL never appears in full: the path of
    a webhook URL is the credential, and these exceptions end up in the container log and
    in the rotating log files.

    That is also why the underlying requests error is neither passed through nor chained
    ("from None"): a ConnectionError carries the request path in its own message, and a
    chained cause is printed with the traceback. The exception class name says what went
    wrong; the urllib3 frames below it say nothing the host and the class do not.

    Args:
        url (str): The webhook URL.
        payload (dict): The JSON body to send.
        timeout: Connect and read timeout, as requests takes it.

    Raises:
        exceptions.ApiUnreachableException: No answer at all.
        exceptions.HttpException: An error status.
    """
    safe_url = host_only(url)

    try:
        response = session().post(url, json=payload,
                                  headers={"Content-Type": "application/json"},
                                  timeout=timeout)
    except requests.exceptions.Timeout:
        raise exceptions.ApiUnreachableException(
            f"POST {safe_url} timed out after {timeout}.", url=safe_url) from None
    except requests.exceptions.RequestException as e:
        raise exceptions.ApiUnreachableException(
            f"POST {safe_url} could not be reached ({type(e).__name__}).",
            url=safe_url) from None

    _raise_for_status("POST", safe_url, response, WEBHOOK_AUTH_MESSAGE,
                      was_retried("POST", retry=True))

    logging.debug(f"POST {safe_url} answered {response.status_code}.")
