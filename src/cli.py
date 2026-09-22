"""Shared terminal presentation and friendly error messages."""

from __future__ import annotations

from typing import Any

from .exceptions import (
    CanvasAuthenticationError,
    CanvasConfigurationError,
    CanvasConnectionError,
    CanvasError,
    CanvasPermissionError,
    CanvasRateLimitError,
    ReadOnlyCanvasError,
)


def friendly_error_message(error: CanvasError, base_url: str | None = None) -> str:
    """Translate an expected application exception into terminal guidance."""

    if isinstance(error, CanvasConfigurationError):
        return str(error)
    if isinstance(error, CanvasAuthenticationError):
        return (
            "Canvas authentication failed.\n\n"
            "Please check your Canvas URL and API token.\n\n"
            "Run:\n\n"
            "    python scripts/setup.py\n\n"
            "to update your credentials."
        )
    if isinstance(error, CanvasConnectionError):
        current = f"\n\nCurrent Canvas URL:\n{base_url}" if base_url else ""
        return (
            "Unable to connect to Canvas."
            f"{current}\n\n"
            "Check your internet connection and Canvas address, then try again."
        )
    if isinstance(error, CanvasPermissionError):
        return (
            "Canvas denied access to the requested resource.\n\n"
            "The configured account or token may not have permission to use this "
            "endpoint."
        )
    if isinstance(error, CanvasRateLimitError):
        return (
            "Canvas is temporarily rate-limiting requests.\n\n"
            "Wait a short while, then try again."
        )
    if isinstance(error, ReadOnlyCanvasError):
        return str(error)
    return f"Canvas returned an unexpected API error.\n\n{error}"


def display_value(value: Any, fallback: str = "Not available") -> str:
    """Format sparse Canvas values consistently for terminal output."""

    if value is None or value == "":
        return fallback
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value)

