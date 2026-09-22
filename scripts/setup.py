"""Interactive first-run setup for Canvas VLE Manager."""

from __future__ import annotations

from getpass import getpass
import os
from pathlib import Path
import sys

import _bootstrap  # noqa: F401 - adjusts sys.path for direct script execution

from src.canvas_client import CanvasClient
from src.cli import friendly_error_message
from src.config import (
    CANVAS_API_TOKEN,
    CANVAS_BASE_URL,
    CanvasConfig,
    config_exists,
    default_env_path,
    load_config,
    reset_config,
    save_config,
)
from src.exceptions import CanvasConfigurationError, CanvasError
from src.logging_utils import configure_file_logging


ENVIRONMENT_KEYS = (CANVAS_BASE_URL, CANVAS_API_TOKEN)


def _heading() -> None:
    print("Canvas VLE Manager")
    print("==================")


def _prompt_url(current: str | None = None) -> str:
    label = "Canvas URL"
    if current:
        label += f" [{current}]"
    value = input(f"{label}:\n> ").strip()
    return value or (current or "")


def _prompt_token() -> str:
    return getpass("Canvas API Token:\n> ").strip()


def _test_connection(config: CanvasConfig) -> bool:
    print("\nTesting Canvas connection...\n")
    configure_file_logging("setup", secrets=(config.api_token,))
    try:
        with CanvasClient(config.base_url, config.api_token) as client:
            user = client.get_current_user()
            courses = client.get_courses()
    except CanvasError as error:
        print(friendly_error_message(error, config.base_url))
        return False

    user_name = user.get("name") or user.get("short_name") or "Name not available"
    print("Connection successful.\n")
    print("Authenticated as:")
    print(user_name)
    print("\nCanvas:")
    print(config.base_url)
    print("\nAccessible courses:")
    print(len(courses))
    return True


def _save_and_test(config: CanvasConfig) -> bool:
    location = save_config(config)
    overrides = _environment_override_names()
    if overrides:
        print(
            "\nNote: these process environment variables take precedence over .env:"
        )
        print(", ".join(overrides))
        print("Testing the effective environment-overridden configuration.")
    effective_config = load_config()
    succeeded = _test_connection(effective_config)
    print("\nConfiguration saved locally:")
    print(_display_path(location))
    if not succeeded:
        print("\nThe saved values can be changed by running setup again.")
    return succeeded


def _environment_override_names() -> list[str]:
    return [key for key in ENVIRONMENT_KEYS if key in os.environ]


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def _first_run() -> int:
    print(
        "\nThis application connects to your Canvas account using the Canvas API.\n"
        "Enter your institution's normal HTTPS Canvas address; do not add /api/v1.\n"
        "The token is entered privately and will not be displayed.\n"
    )
    try:
        config = CanvasConfig(base_url=_prompt_url(), api_token=_prompt_token())
        return 0 if _save_and_test(config) else 1
    except CanvasConfigurationError as error:
        print(f"\n{friendly_error_message(error)}")
        return 2


def _existing_configuration_menu() -> int:
    overrides = _environment_override_names()
    if overrides:
        print(
            "\nProcess environment values currently override .env for: "
            + ", ".join(overrides)
        )
        print("Unset an overridden variable before changing it through this menu.")

    while True:
        print("\nCanvas configuration is available.\n")
        print("1. Test current connection")
        print("2. Change Canvas URL")
        print("3. Replace API token")
        print("4. Reset local configuration")
        print("5. Exit")
        selection = input("\nSelect an option:\n> ").strip()

        try:
            if selection == "1":
                return 0 if _test_connection(load_config()) else 1
            if selection == "2":
                if CANVAS_BASE_URL in os.environ:
                    print(
                        "\nCANVAS_BASE_URL is set in the process environment. "
                        "Unset it before changing the URL here."
                    )
                    continue
                current = load_config()
                replacement = CanvasConfig(
                    base_url=_prompt_url(current.base_url),
                    api_token=current.api_token,
                )
                return 0 if _save_and_test(replacement) else 1
            if selection == "3":
                if CANVAS_API_TOKEN in os.environ:
                    print(
                        "\nCANVAS_API_TOKEN is set in the process environment. "
                        "Unset it before replacing the token here."
                    )
                    continue
                current = load_config()
                replacement = CanvasConfig(
                    base_url=current.base_url,
                    api_token=_prompt_token(),
                )
                return 0 if _save_and_test(replacement) else 1
            if selection == "4":
                confirmation = input(
                    "Type RESET to remove the local .env configuration:\n> "
                ).strip()
                if confirmation != "RESET":
                    print("\nReset cancelled.")
                    continue
                removed = reset_config()
                print("\nLocal configuration removed." if removed else "\nNo .env file found.")
                remaining_overrides = _environment_override_names()
                if remaining_overrides:
                    print(
                        "Process environment configuration remains active for: "
                        + ", ".join(remaining_overrides)
                    )
                return 0
            if selection == "5":
                return 0
        except CanvasError as error:
            print(f"\n{friendly_error_message(error)}")
            return 2

        print("\nPlease enter a number from 1 to 5.")


def main() -> int:
    _heading()
    try:
        if config_exists():
            return _existing_configuration_menu()
        return _first_run()
    except (EOFError, KeyboardInterrupt):
        print("\n\nSetup cancelled. No token was displayed.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
