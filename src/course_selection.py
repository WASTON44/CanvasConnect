"""Course filtering and terminal selection helpers."""

from __future__ import annotations

import re
from typing import Any, Callable, Iterable

from .exceptions import CanvasError


class CourseSelectionError(CanvasError):
    """Raised when a requested course selection cannot be resolved."""


def course_label(course: dict[str, Any]) -> str:
    """Return a compact human-readable label for one course."""

    course_id = _text(course.get("id"), "unknown ID")
    code = _text(course.get("course_code"), "No code")
    name = _text(course.get("name"), "Unnamed course")
    term = course.get("term")
    term_name = term.get("name") if isinstance(term, dict) else None
    return f"{code} - {name} - {_text(term_name, 'No term')} (Canvas {course_id})"


def parse_selection(expression: str, course_count: int) -> list[int]:
    """Parse one-based indexes, comma/space lists, ranges, or ``all``."""

    value = expression.strip().lower()
    if course_count < 1:
        raise CourseSelectionError("Canvas did not return any selectable courses.")
    if value == "all":
        return list(range(course_count))
    if not value:
        raise CourseSelectionError("No courses were selected.")

    indexes: list[int] = []
    for token in re.split(r"[\s,;]+", value):
        if not token:
            continue
        if "-" in token:
            parts = token.split("-", 1)
            if len(parts) != 2 or not all(part.isdigit() for part in parts):
                raise CourseSelectionError(f"Invalid selection: {token}")
            start, end = (int(part) for part in parts)
            if start > end:
                raise CourseSelectionError(f"Invalid descending range: {token}")
            numbers: Iterable[int] = range(start, end + 1)
        elif token.isdigit():
            numbers = (int(token),)
        else:
            raise CourseSelectionError(f"Invalid selection: {token}")

        for number in numbers:
            if number < 1 or number > course_count:
                raise CourseSelectionError(
                    f"Course number {number} is outside the available list."
                )
            zero_based = number - 1
            if zero_based not in indexes:
                indexes.append(zero_based)
    if not indexes:
        raise CourseSelectionError("No courses were selected.")
    return indexes


def filter_courses(
    courses: Iterable[dict[str, Any]],
    *,
    search: str | None = None,
    term: str | None = None,
    role: str | None = None,
    state: str | None = None,
    favorite: bool = False,
) -> list[dict[str, Any]]:
    """Filter course display data without changing the saved discovery index.

    General search is a case-insensitive substring match across the course ID,
    name, code, term, account, role, and state fields. The specific role and
    state filters use case-insensitive exact matching; the term filter is a
    substring match so a year such as ``2026`` remains useful.
    """

    search_value = _normalise_filter(search)
    term_value = _normalise_filter(term)
    role_value = _normalise_filter(role)
    state_value = _normalise_filter(state)
    results: list[dict[str, Any]] = []
    for course in courses:
        searchable = _searchable_values(course)
        if search_value and not any(search_value in value for value in searchable):
            continue

        term_values = _mapping_values(course.get("term"), ("id", "name"))
        if term_value and not any(term_value in value for value in term_values):
            continue

        role_values = _course_roles(course)
        if role_value and role_value not in role_values:
            continue

        state_values = _course_states(course)
        if state_value and state_value not in state_values:
            continue
        if favorite and course.get("is_favorite") is not True:
            continue
        results.append(course)
    return results


def select_courses_interactively(
    courses: list[dict[str, Any]],
    *,
    initial_search: str | None = None,
    prompt_for_filter: bool = False,
    input_func: Callable[[str], str] = input,
    output_func: Callable[[str], None] = print,
) -> list[dict[str, Any]]:
    """Filter, display, and select courses from a numbered terminal list."""

    if not courses:
        raise CourseSelectionError("Canvas did not return any selectable courses.")

    search = initial_search
    if prompt_for_filter and initial_search is None:
        search = input_func(
            "Filter courses by name, code, term, role, or Canvas ID "
            "(press Enter for all):\n> "
        )

    while True:
        visible = filter_courses(courses, search=search)
        if not visible:
            output_func(f"\nNo courses matched {_text(search, 'that filter')!r}.")
            if not prompt_for_filter:
                raise CourseSelectionError("No accessible courses matched the filter.")
            search = input_func("Try another filter (press Enter for all):\n> ")
            continue

        output_func("\nSelect courses to back up:\n")
        output_func(f"Showing {len(visible)} of {len(courses)} accessible courses.\n")
        for index, course in enumerate(visible, start=1):
            output_func(f"[{index}] {course_label(course)}")

        expression = input_func(
            "\nEnter selections (for example 1,3-5; or all). "
            "Enter /filter followed by text to change the list:\n> "
        )
        if expression.strip().casefold() == "/filter":
            search = input_func("New filter (press Enter for all):\n> ")
            continue
        if expression.strip().casefold().startswith("/filter "):
            search = expression.strip()[8:].strip()
            continue
        try:
            indexes = parse_selection(expression, len(visible))
        except CourseSelectionError as error:
            output_func(f"\n{error}")
            continue
        return [visible[index] for index in indexes]


def select_courses_by_id(
    courses: list[dict[str, Any]],
    requested_ids: Iterable[str | int],
) -> list[dict[str, Any]]:
    """Resolve explicit Canvas IDs while preserving the requested order."""

    by_id = {str(course.get("id")): course for course in courses if course.get("id") is not None}
    selected: list[dict[str, Any]] = []
    missing: list[str] = []
    for requested in requested_ids:
        key = str(requested).strip()
        course = by_id.get(key)
        if course is None:
            missing.append(key)
        elif course not in selected:
            selected.append(course)
    if missing:
        raise CourseSelectionError(
            "These Canvas course IDs were not found in the accessible list: "
            + ", ".join(missing)
        )
    return selected


def select_courses_by_term(
    courses: list[dict[str, Any]],
    term: str,
) -> list[dict[str, Any]]:
    """Select courses whose returned term name or ID exactly matches."""

    wanted = term.strip().casefold()
    if not wanted:
        raise CourseSelectionError("A term name or Canvas term ID is required.")

    selected: list[dict[str, Any]] = []
    for course in courses:
        term_data = course.get("term")
        if not isinstance(term_data, dict):
            continue
        candidates = (term_data.get("name"), term_data.get("id"))
        if any(str(candidate).strip().casefold() == wanted for candidate in candidates if candidate is not None):
            selected.append(course)
    if not selected:
        raise CourseSelectionError(
            f"No accessible courses matched the Canvas term {term!r}."
        )
    return selected


def _text(value: Any, fallback: str) -> str:
    return fallback if value is None or str(value).strip() == "" else str(value).strip()


def _normalise_filter(value: Any) -> str:
    return "" if value is None else str(value).strip().casefold()


def _mapping_values(value: Any, fields: tuple[str, ...]) -> set[str]:
    if not isinstance(value, dict):
        return set()
    return {
        normalised
        for field in fields
        if (normalised := _normalise_filter(value.get(field)))
    }


def _iterable_values(value: Any) -> set[str]:
    if isinstance(value, (list, tuple, set)):
        return {
            normalised
            for item in value
            if (normalised := _normalise_filter(item))
        }
    normalised = _normalise_filter(value)
    return {normalised} if normalised else set()


def _course_roles(course: dict[str, Any]) -> set[str]:
    roles = _iterable_values(course.get("current_user_roles"))
    roles.update(_iterable_values(course.get("enrollment_types")))
    roles.update(_iterable_values(course.get("current_user_role")))
    roles.update(_iterable_values(course.get("enrollment_type")))
    return roles


def _course_states(course: dict[str, Any]) -> set[str]:
    states = _iterable_values(course.get("access_states"))
    states.update(_iterable_values(course.get("access_state")))
    states.update(_iterable_values(course.get("workflow_state")))
    if isinstance(course.get("published"), bool):
        states.add("published" if course["published"] else "unpublished")
    if course.get("concluded") is True:
        states.add("concluded")
    return states


def _searchable_values(course: dict[str, Any]) -> set[str]:
    values = {
        _normalise_filter(course.get(field))
        for field in ("id", "name", "original_name", "course_code")
    }
    values.update(_mapping_values(course.get("term"), ("id", "name")))
    values.update(_mapping_values(course.get("account"), ("id", "name")))
    values.update(_course_roles(course))
    values.update(_course_states(course))
    if course.get("is_favorite") is True:
        values.update({"favorite", "favourite", "favorited", "favourited"})
    return {value for value in values if value}


__all__ = [
    "CourseSelectionError",
    "course_label",
    "filter_courses",
    "parse_selection",
    "select_courses_by_id",
    "select_courses_by_term",
    "select_courses_interactively",
]
