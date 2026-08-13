"""
### This module holds all necessary functions to call Kickbase `/user/...` API endpoints.

TODO: Maybe list all functions here automatically?
"""

import logging

from backend import exceptions, miscellaneous
from backend.kickbase import http
from backend.kickbase.endpoints.user import User

### -------------------------------------------------------------------


def _announce_login_failure(reason: str, discord_webhook: str) -> None:
    """### Try to tell Discord that the login failed, without hiding why it failed.

    The notification runs inside the handler for the login error. If it raised, the
    Discord problem would replace the Kickbase one on its way up and the log would name
    the wrong subsystem - the exact confusion this whole layer exists to end.

    Args:
        reason (str): What to tell the user.
        discord_webhook (str): The webhook URL.
    """
    try:
        miscellaneous.discord_notification("Login failed!", reason, 16711680, discord_webhook)
    except exceptions.NotificatonException as e:
        logging.error(f"Could not announce the failed login on Discord either: {e}")


def login(email: str, password: str, discord_webhook: str) -> tuple:
    """### Logs in the user with the provided email and password.

    Args:
        email (str): The email of the user.
        password (str): The password of the user.
        discord_webhook (str): The Discord webhook URL to send a notification in case of an error.

    Raises:
        exceptions.LoginException: Raised if the login fails.

    Returns:
        tuple: A tuple containing the user info and token.
    """
    url = "https://api.kickbase.com/v4/user/login"
    payload = {
        "em": email,
        "pass": password,
        "ext": True, # TODO: What is this?
        "loy": False, # TODO: What is this?
        "rep": {} # TODO: What is this?
    }

    ### Try to login with the given credentials via POST request.
    ### The old bare except reported every failure as wrong credentials, so a Kickbase
    ### outage read as "your password is wrong" - and sent that to Discord. Only a
    ### rejected login says anything about the credentials; everything else says what it
    ### actually was.
    try:
        json_response = http.post_json(url, payload)
    except exceptions.AuthExpiredException as e:
        _announce_login_failure("Please check your credentials.", discord_webhook)
        raise exceptions.LoginException("[CRITICAL] Login failed! Please check your credentials.") from e
    except exceptions.HttpException as e:
        _announce_login_failure(f"Kickbase could not be reached: {e}", discord_webhook)
        raise exceptions.LoginException(f"[CRITICAL] Login failed! {e}") from e

    ### Create an object "user" with the User class with json_response["u"] as parameter (dict)
    user = User(json_response["u"])
    ### Save the token
    token = json_response["tkn"]

    ### TODO: Set return type
    return user, token


def collect_gift(token: str) -> dict:
    """### Collects the current gift of the user in every league.

    Args:
        token (str): The token of the user.

    Returns:
        dict: The response of the API call.
    """
    url = "https://api.kickbase.com/v4/bonus/collect"

    ### Send GET request to get the current gift
    return http.get_json(url, token)