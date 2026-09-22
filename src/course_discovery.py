"""Course discovery and privacy-conscious local indexing."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import logging
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .canvas_client import CanvasClient
from .version import __version__


LOGGER = logging.getLogger(__name__)

SAFE_TERM_FIELDS = ("id", "name", "start_at", "end_at", "workflow_state")
SAFE_ACCOUNT_FIELDS = ("id", "name")
SAFE_ENROLLMENT_FIELDS = ("type", "role", "role_id", "enrollment_state")


def discover_courses(
    client: CanvasClient,
    destination: Path | None = None,
) -> dict[str, Any]:
    """Retrieve visible courses, save a safe local index, and return it.

    Canvas can return more data than course discovery needs. The stored form is
    deliberately projected onto course, term, account, and current-user role
    fields so that incidental grade or user data is not retained.
    """

    LOGGER.info("Discovering courses visible to the authenticated Canvas account.")
    courses = [normalise_course(course, client.base_url) for course in client.get_courses()]
    result: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "application_version": __version__,
        "canvas_base_url": client.base_url,
        "course_count": len(courses),
        "courses": courses,
    }

    if destination is not None:
        write_json_atomic(destination, result)
        LOGGER.info("Saved %d course records to %s.", len(courses), destination)
    else:
        LOGGER.info("Discovered %d course records.", len(courses))
    return result


def normalise_course(course: dict[str, Any], base_url: str) -> dict[str, Any]:
    """Return the subset of Canvas course data used by local discovery."""

    course_id = course.get("id")
    workflow_state = course.get("workflow_state")
    enrollments = _safe_enrollments(course.get("enrollments"))
    roles = _unique_values(enrollments, "role")
    enrollment_types = _unique_values(enrollments, "type")
    access_states = _unique_values(enrollments, "enrollment_state")

    published = course.get("published")
    if not isinstance(published, bool):
        published = workflow_state in {"available", "completed"}

    # Build the normal course URL from trusted local components. Canvas course
    # objects normally include html_url, but persisting a server-supplied query
    # string would create an unnecessary signed-token risk.
    html_url = (
        f"{base_url}/courses/{quote(str(course_id), safe='')}"
        if course_id is not None
        else None
    )

    result = {
        "id": course_id,
        "name": course.get("name"),
        "original_name": course.get("original_name"),
        "course_code": course.get("course_code"),
        "workflow_state": workflow_state,
        "published": published,
        "start_at": course.get("start_at"),
        "end_at": course.get("end_at"),
        "created_at": course.get("created_at"),
        "time_zone": course.get("time_zone"),
        "term": _safe_mapping(course.get("term"), SAFE_TERM_FIELDS),
        "term_id": course.get("enrollment_term_id"),
        "account": _safe_mapping(course.get("account"), SAFE_ACCOUNT_FIELDS),
        "account_id": course.get("account_id"),
        "canvas_url": html_url,
        "access_restricted_by_date": course.get("access_restricted_by_date"),
        "concluded": course.get("concluded"),
        "enrollment_type": enrollment_types[0] if enrollment_types else None,
        "enrollment_types": enrollment_types,
        "current_user_role": roles[0] if roles else (
            enrollment_types[0] if enrollment_types else None
        ),
        "current_user_roles": roles,
        "access_state": access_states[0] if access_states else None,
        "access_states": access_states,
        "enrollments": enrollments,
    }
    if isinstance(course.get("is_favorite"), bool):
        result["is_favorite"] = course["is_favorite"]
    return result


def default_course_index_path(project_root: Path | None = None) -> Path:
    """Return the default Git-ignored course discovery path."""

    root = project_root or Path(__file__).resolve().parents[1]
    return root / "user_data" / "courses.json"


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON via a sibling temporary file to avoid partial indexes."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _safe_mapping(value: Any, fields: tuple[str, ...]) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {field: value.get(field) for field in fields if field in value}


def _safe_enrollments(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [
        safe
        for enrollment in value
        if (safe := _safe_mapping(enrollment, SAFE_ENROLLMENT_FIELDS)) is not None
    ]


def _unique_values(items: list[dict[str, Any]], field: str) -> list[str]:
    values: list[str] = []
    for item in items:
        value = item.get(field)
        if value is not None and str(value) not in values:
            values.append(str(value))
    return values
