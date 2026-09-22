"""Application-specific exception hierarchy.

The classes intentionally contain no request or credential state.  Callers should
use concise, user-facing messages and must not attach API tokens or Authorization
headers to exceptions.
"""


class CanvasError(Exception):
    """Base class for expected Canvas VLE Manager failures."""


class CanvasConfigurationError(CanvasError):
    """Raised when local Canvas configuration is missing or invalid."""


class CanvasAuthenticationError(CanvasError):
    """Raised when Canvas rejects the configured credentials."""


class CanvasPermissionError(CanvasError):
    """Raised when the account cannot access a requested Canvas resource."""


class CanvasConnectionError(CanvasError):
    """Raised when the configured Canvas server cannot be reached."""


class CanvasRateLimitError(CanvasError):
    """Raised when Canvas rate-limits a request that cannot be retried."""


class ReadOnlyCanvasError(CanvasError):
    """Raised when code attempts a mutating Canvas request."""


class BackupError(CanvasError):
    """Raised when a course backup cannot be completed safely."""


__all__ = [
    "BackupError",
    "CanvasAuthenticationError",
    "CanvasConfigurationError",
    "CanvasConnectionError",
    "CanvasError",
    "CanvasPermissionError",
    "CanvasRateLimitError",
    "ReadOnlyCanvasError",
]
