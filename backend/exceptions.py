"""
### This module defines all custom exceptions that are used in this project.
"""

class KickbaseException(Exception):
    """Base class for exceptions in this module."""
    pass

class LoginException(Exception):
    """Exception raised for errors in the login process."""
    pass

class NotificatonException(Exception):
    """Exception raised for errors in the notification process."""
    pass


### ===============================================================================
### HTTP errors
###
### Every one of these used to be the same bare `except:` raising
### NotificatonException("Notification failed! Please check your Discord Webhook URL.") -
### a message that named the wrong subsystem whatever had actually gone wrong. An expired
### token, a rate limit and a hung socket were indistinguishable in the log.
###
### They all derive from KickbaseException, so the top level handlers in main.py and
### app.py keep catching them exactly as before. Handling one of them specifically is a
### choice a call site makes, not something it is forced into.
### ===============================================================================

class HttpException(KickbaseException):
    """Base class for anything that went wrong talking to an HTTP API."""

    def __init__(self, message: str, url: str = None, status_code: int = None):
        super().__init__(message)
        self.url = url
        self.status_code = status_code


class AuthExpiredException(HttpException):
    """The API rejected the token (401) or refused the request for it (403).

    Retrying is pointless; the run needs a new token.
    """
    pass


class RateLimitedException(HttpException):
    """The API answered 429 and kept doing so after the retries ran out.

    The retries already honoured Retry-After, so this means the budget is genuinely
    spent rather than that a single request came too early.
    """
    pass


class ApiRequestException(HttpException):
    """The API rejected the request itself (a 4xx other than 401, 403 and 429).

    Repeating it unchanged changes nothing, so this is the one HTTP error a caller may
    reasonably treat as an answer - see _fetch_marketvalue(), which reads it as "this
    window is not served".
    """
    pass


class ApiUnavailableException(HttpException):
    """The API answered 5xx and kept doing so after the retries ran out."""
    pass


class ApiUnreachableException(HttpException):
    """The request never got an answer: DNS, connection or timeout.

    This is the failure the timeouts were added for. Without them a hung socket parked
    the scheduler until the container was restarted.
    """
    pass


class ApiResponseException(HttpException):
    """The API answered, but the body was not the JSON the caller expected."""
    pass
