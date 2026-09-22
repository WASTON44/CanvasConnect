"""Discover and display every Canvas course visible to the current user."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any

import _bootstrap  # noqa: F401 - adjusts sys.path for direct script execution

from src.canvas_client import CanvasClient
from src.cli import display_value, friendly_error_message
from src.config import load_config, project_root
from src.course_discovery import default_course_index_path, discover_courses
from src.course_selection import filter_courses
from src.exceptions import CanvasError
from src.logging_utils import configure_file_logging


def _print_course(index: int, course: dict[str, Any]) -> None:
    term = course.get("term") or {}
    account = course.get("account") or {}
    roles = course.get("current_user_roles") or []
    if not roles and course.get("current_user_role"):
        roles = [course["current_user_role"]]

    print(f"[{index}]")
    print("\nCourse:")
    print(display_value(course.get("name"), "Unnamed course"))
    print("\nCourse code:")
    print(display_value(course.get("course_code")))
    print("\nCanvas ID:")
    print(display_value(course.get("id")))
    print("\nTerm:")
    print(display_value(term.get("name")))
    print("\nAccount:")
    print(display_value(account.get("name")))
    print("\nRole:")
    print(", ".join(roles) if roles else "Not available")
    print("\nAccess state:")
    print(display_value(course.get("access_state")))
    print("\nCourse state:")
    print(display_value(course.get("workflow_state")))
    print("\nPublished:")
    print(display_value(course.get("published")))
    print("\nFavorite:")
    print(display_value(course.get("is_favorite")))
    print("\nStart:")
    print(display_value(course.get("start_at")))
    print("\nEnd:")
    print(display_value(course.get("end_at")))
    print()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--search",
        help="show courses matching text in name, code, ID, term, account, role, or state",
    )
    parser.add_argument(
        "--term",
        help="show courses whose Canvas term name or ID contains this text",
    )
    parser.add_argument(
        "--role",
        help="show courses with this exact current-user role or enrolment type",
    )
    parser.add_argument(
        "--state",
        help="show courses with this exact course/access state (for example completed)",
    )
    parser.add_argument(
        "--favorite",
        "--favourite",
        dest="favorite",
        action="store_true",
        help="show only courses marked as a Canvas favorite",
    )
    return parser


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(project_root()))
    except ValueError:
        return str(path)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config()
    except CanvasError as error:
        print(friendly_error_message(error))
        return 2

    log_path = configure_file_logging("list_courses", secrets=(config.api_token,))
    destination = default_course_index_path()
    try:
        with CanvasClient(config.base_url, config.api_token) as client:
            discovery = discover_courses(client, destination)
    except CanvasError as error:
        print(friendly_error_message(error, config.base_url))
        print(f"\nLog:\n{_display_path(log_path)}")
        return 1

    print("Canvas courses")
    print("==============\n")
    all_courses = discovery["courses"]
    courses = filter_courses(
        all_courses,
        search=args.search,
        term=args.term,
        role=args.role,
        state=args.state,
        favorite=args.favorite,
    )
    if courses:
        for index, course in enumerate(courses, start=1):
            _print_course(index, course)
    else:
        if all_courses:
            print("No accessible courses matched the supplied filters.\n")
        else:
            print("No accessible courses were returned by Canvas.\n")

    print(f"Courses shown: {len(courses)} of {len(all_courses)}")
    print(f"Full discovery index saved: {_display_path(destination)}")
    print(f"Log: {_display_path(log_path)}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (EOFError, KeyboardInterrupt):
        print("\nCourse discovery cancelled.")
        sys.exit(130)
