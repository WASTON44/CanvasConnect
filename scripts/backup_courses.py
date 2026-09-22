"""Select and back up accessible Canvas teaching-content courses."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sys
from typing import Any

import _bootstrap  # noqa: F401 - adjusts sys.path for direct script execution

from src.backup import BackupResult, CourseBackup, dry_run_course
from src.canvas_client import CanvasClient
from src.cli import friendly_error_message
from src.config import load_config, project_root
from src.course_discovery import default_course_index_path, discover_courses
from src.course_selection import (
    CourseSelectionError,
    course_label,
    filter_courses,
    select_courses_by_id,
    select_courses_by_term,
    select_courses_interactively,
)
from src.exceptions import CanvasError
from src.logging_utils import configure_file_logging


LOGGER = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--course",
        nargs="+",
        metavar="ID",
        help="one or more accessible Canvas course IDs",
    )
    selection.add_argument(
        "--term",
        help="exact Canvas term name or term ID",
    )
    parser.add_argument(
        "--search",
        help="filter courses by text in name, code, ID, term, account, role, or state",
    )
    parser.add_argument(
        "--filter-term",
        help="filter the displayed list by a partial Canvas term name or ID",
    )
    parser.add_argument(
        "--role",
        help="filter by an exact current-user role or enrolment type",
    )
    parser.add_argument(
        "--state",
        help="filter by an exact course/access state (for example completed)",
    )
    parser.add_argument(
        "--favorite",
        "--favourite",
        dest="favorite",
        action="store_true",
        help="show/select only courses marked as a Canvas favorite",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="inspect content counts and size without creating a backup",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="resume/incrementally update an existing local archive",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="skip the final interactive confirmation (useful for scripts)",
    )
    return parser


def _select_courses(
    courses: list[dict[str, Any]],
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    filtered = filter_courses(
        courses,
        search=args.search if (args.course or args.term) else None,
        term=args.filter_term,
        role=args.role,
        state=args.state,
        favorite=args.favorite,
    )
    if not filtered:
        raise CourseSelectionError("No accessible courses matched the supplied filters.")
    if args.course:
        return select_courses_by_id(filtered, args.course)
    if args.term:
        return select_courses_by_term(filtered, args.term)
    return select_courses_interactively(
        filtered,
        initial_search=args.search,
        prompt_for_filter=True,
    )


def _print_selection(courses: list[dict[str, Any]]) -> None:
    print("\nSelected courses")
    print("================\n")
    for number, course in enumerate(courses, start=1):
        print(f"[{number}] {course_label(course)}")


def _confirmed() -> bool:
    answer = input("\nProceed with backup? [y/N]\n> ").strip().casefold()
    return answer in {"y", "yes"}


def _print_dry_run(result: BackupResult) -> None:
    counts = result.counts
    print(f"\nCourse: {result.course_name}")
    print(f"Canvas ID: {result.course_id}")
    print(f"Modules: {counts.get('modules', 0)}")
    print(f"Module items: {counts.get('module_items', 0)}")
    print(f"Pages: {counts.get('pages', 0)}")
    print(f"Files: {counts.get('files', 0)}")
    print(f"Assignments: {counts.get('assignments', 0)}")
    print(f"Assignment groups: {counts.get('assignment_groups', 0)}")
    print(f"Rubrics: {counts.get('rubrics', 0)}")
    print(f"Quizzes: {counts.get('quizzes', 0)}")
    print(f"Discussions: {counts.get('discussions', 0)}")
    print(f"Announcements: {counts.get('announcements', 0)}")
    print(
        "Estimated download size: "
        + _format_size(result.estimated_download_bytes)
    )
    if result.warnings:
        print(f"Warnings: {len(result.warnings)}")


def _print_summary(results: list[BackupResult], log_path: Path) -> None:
    counts: dict[str, int] = {}
    for result in results:
        for name, count in result.counts.items():
            counts[name] = counts.get(name, 0) + count
    print("\nBackup complete")
    print("===============\n")
    print(f"Courses: {len(results)}")
    for name in (
        "modules",
        "pages",
        "files",
        "files_downloaded",
        "files_unchanged",
        "assignments",
        "quizzes",
        "discussions",
        "announcements",
    ):
        print(f"{name.replace('_', ' ').title()}: {counts.get(name, 0)}")
    print(f"Warnings: {sum(len(result.warnings) for result in results)}")
    print(f"Errors: {sum(len(result.errors) for result in results)}")
    print(f"Log: {_display_path(log_path)}")
    print("Backup root: backups")


def _format_size(value: int | None) -> str:
    if value is None:
        return "Unknown"
    amount = float(value)
    units = ("bytes", "KB", "MB", "GB", "TB")
    unit = units[0]
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            break
        amount /= 1024
    if unit == "bytes":
        return f"{int(amount)} bytes"
    return f"{amount:.1f} {unit}"


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

    log_path = configure_file_logging("backup", secrets=(config.api_token,))
    try:
        with CanvasClient(config.base_url, config.api_token) as client:
            discovery = discover_courses(client, default_course_index_path())
            selected = _select_courses(discovery["courses"], args)
            _print_selection(selected)

            if args.dry_run:
                print("\nDry run — inspecting Canvas; no archive files will be created.")
                results = []
                for course in selected:
                    result = dry_run_course(client, course)
                    results.append(result)
                    _print_dry_run(result)
                print("\nNo files downloaded.")
                print(f"Log: {_display_path(log_path)}")
                return 0 if not any(result.errors for result in results) else 1

            if not args.yes and not _confirmed():
                print("\nBackup cancelled. No course archive was created.")
                return 0

            manager = CourseBackup(client, project_root() / "backups")
            results = []
            for course in selected:
                print(f"\nBacking up: {course_label(course)}")
                try:
                    result = manager.run(course, resume=args.resume)
                except CanvasError as error:
                    LOGGER.exception("Course %s backup failed.", course.get("id"))
                    print(f"  Failed: {friendly_error_message(error, config.base_url)}")
                    result = BackupResult(
                        course_id=course.get("id"),
                        course_name=str(course.get("name") or "Unnamed course"),
                        status="failed",
                        counts={},
                        warnings=[],
                        errors=[str(error)],
                        estimated_download_bytes=None,
                    )
                results.append(result)
                print(f"  Status: {result.status}")
                if result.course_root:
                    print(f"  Saved: {_display_path(result.course_root)}")
            _print_summary(results, log_path)
            return (
                1
                if any(result.status == "failed" or result.errors for result in results)
                else 0
            )
    except CourseSelectionError as error:
        print(f"\nUnable to select courses.\n\n{error}")
        print(f"\nLog: {_display_path(log_path)}")
        return 2
    except CanvasError as error:
        print(friendly_error_message(error, config.base_url))
        print(f"\nLog: {_display_path(log_path)}")
        return 1
    except Exception:
        LOGGER.exception("Unexpected backup command failure.")
        print("An unexpected local error stopped the backup.")
        print(f"See the diagnostic log: {_display_path(log_path)}")
        return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (EOFError, KeyboardInterrupt):
        print("\nBackup cancelled.")
        sys.exit(130)
