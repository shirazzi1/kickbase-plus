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

class KickbaseWriteException(KickbaseException):
    """Exception raised when Kickbase rejects a write.

    Carries the HTTP status and the message the API returned. Every other call in this
    project answers a failure with "Please check your Discord Webhook URL", which is
    unusable here: a rejected bid has to say why it was rejected, and the user is
    standing in front of the field waiting to find out.
    """

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status