"""Display the modules and module items in one accessible Canvas course."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any

import _bootstrap  # noqa: F401 - adjusts sys.path for direct script execution

from src.canvas_client import CanvasClient
from src.cli import display_value, friendly_error_message
from src.config import load_config, project_root
from src.exceptions import CanvasError
from src.logging_utils import configure_file_logging


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--course", required=True, help="Canvas course ID")
    return parser


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(project_root()))
    except ValueError:
        return str(path)


def _print_module(number: int, module: dict[str, Any]) -> None:
    print(f"[{number}] {display_value(module.get('name'), 'Unnamed module')}")
    print(f"    Canvas ID: {display_value(module.get('id'))}")
    print(f"    Position: {display_value(module.get('position'))}")
    print(f"    Published: {display_value(module.get('published'))}")
    print(f"    Unlock at: {display_value(module.get('unlock_at'))}")
    prerequisites = module.get("prerequisite_module_ids") or []
    print(
        "    Prerequisite module IDs: "
        + (", ".join(str(value) for value in prerequisites) or "None")
    )
    items = module.get("items") or []
    if not items:
        print("    Items: None accessible")
    for item_number, item in enumerate(items, start=1):
        print(
            f"    {item_number}. {display_value(item.get('type'), 'Item')}: "
            f"{display_value(item.get('title'), 'Untitled')} "
            f"(ID {display_value(item.get('id'))})"
        )
    print()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config()
    except CanvasError as error:
        print(friendly_error_message(error))
        return 2

    log_path = configure_file_logging("list_modules", secrets=(config.api_token,))
    try:
        with CanvasClient(config.base_url, config.api_token) as client:
            course = client.get_course(args.course)
            modules = client.get_modules(args.course)
            for module in modules:
                module["items"] = client.get_module_items(args.course, module.get("id"))
    except CanvasError as error:
        print(friendly_error_message(error, config.base_url))
        print(f"\nLog: {_display_path(log_path)}")
        return 1

    print("Canvas course modules")
    print("=====================\n")
    print(f"Course: {display_value(course.get('name'), 'Unnamed course')}")
    print(f"Canvas ID: {display_value(course.get('id'))}\n")
    if modules:
        for number, module in enumerate(modules, start=1):
            _print_module(number, module)
    else:
        print("No accessible modules were returned.\n")
    print(f"Modules found: {len(modules)}")
    print(f"Log: {_display_path(log_path)}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (EOFError, KeyboardInterrupt):
        print("\nModule inspection cancelled.")
        sys.exit(130)
