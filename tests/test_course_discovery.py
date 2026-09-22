"""Offline tests for privacy-conscious Canvas course discovery."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any

import pytest

from src.course_discovery import discover_courses, normalise_course, write_json_atomic
from src.version import __version__


class StubCanvasClient:
    """Small duck-typed client that guarantees discovery never needs a network."""

    base_url = "https://canvas.example.ac.uk"

    def __init__(self, courses: list[dict[str, Any]]) -> None:
        self._courses = courses

    def get_courses(self) -> list[dict[str, Any]]:
        return self._courses


def test_discover_courses_keeps_metadata_but_drops_incidental_private_data(
    tmp_path: Path,
) -> None:
    secret_token = "token-that-must-not-be-persisted"
    raw_course = {
        "id": 12345,
        "name": "Engineering Dynamics",
        "original_name": "Engineering Dynamics 2026",
        "course_code": "ENG101",
        "workflow_state": "available",
        "published": True,
        "start_at": "2026-09-15T08:00:00Z",
        "end_at": "2027-06-30T17:00:00Z",
        "created_at": "2026-03-01T12:00:00Z",
        "time_zone": "Europe/London",
        "term": {
            "id": 26,
            "name": "2026/27",
            "start_at": "2026-09-01T00:00:00Z",
            "end_at": "2027-08-31T23:59:59Z",
            "workflow_state": "active",
            "sis_term_id": "private-sis-term",
            "student_grades": {"Student One": "A"},
        },
        "enrollment_term_id": 26,
        "account": {
            "id": 8,
            "name": "School of Engineering",
            "sis_account_id": "private-sis-account",
            "students": [{"name": "Student One"}],
        },
        "account_id": 8,
        "html_url": "https://canvas.example.ac.uk/courses/12345",
        "access_restricted_by_date": False,
        "concluded": False,
        "is_favorite": True,
        "enrollments": [
            {
                "type": "TeacherEnrollment",
                "role": "Teacher",
                "role_id": 4,
                "enrollment_state": "active",
                "user_id": 999,
                "user": {
                    "name": "Student One",
                    "email": "student.one@example.ac.uk",
                },
                "grades": {
                    "current_score": 68.5,
                    "current_grade": "B",
                },
                "computed_final_score": 68.5,
            },
            {
                "type": "TeacherEnrollment",
                "role": "Teacher",
                "role_id": 4,
                "enrollment_state": "active",
                "last_activity_at": "2026-09-14T11:22:33Z",
            },
        ],
        "students": [
            {
                "id": 999,
                "name": "Student One",
                "email": "student.one@example.ac.uk",
            }
        ],
        "total_students": 42,
        "student_view_student": {"id": 1000, "name": "Test Student"},
        "permissions": {"view_all_grades": True},
        "access_token": secret_token,
    }
    destination = tmp_path / "private" / "courses.json"

    discovery = discover_courses(StubCanvasClient([raw_course]), destination)  # type: ignore[arg-type]

    assert discovery["schema_version"] == 1
    assert discovery["application_version"] == __version__
    assert discovery["canvas_base_url"] == StubCanvasClient.base_url
    assert discovery["course_count"] == 1
    generated_at = datetime.fromisoformat(discovery["generated_at"])
    assert generated_at.tzinfo is not None

    course = discovery["courses"][0]
    assert course == {
        "id": 12345,
        "name": "Engineering Dynamics",
        "original_name": "Engineering Dynamics 2026",
        "course_code": "ENG101",
        "workflow_state": "available",
        "published": True,
        "start_at": "2026-09-15T08:00:00Z",
        "end_at": "2027-06-30T17:00:00Z",
        "created_at": "2026-03-01T12:00:00Z",
        "time_zone": "Europe/London",
        "term": {
            "id": 26,
            "name": "2026/27",
            "start_at": "2026-09-01T00:00:00Z",
            "end_at": "2027-08-31T23:59:59Z",
            "workflow_state": "active",
        },
        "term_id": 26,
        "account": {"id": 8, "name": "School of Engineering"},
        "account_id": 8,
        "canvas_url": "https://canvas.example.ac.uk/courses/12345",
        "access_restricted_by_date": False,
        "concluded": False,
        "is_favorite": True,
        "enrollment_type": "TeacherEnrollment",
        "enrollment_types": ["TeacherEnrollment"],
        "current_user_role": "Teacher",
        "current_user_roles": ["Teacher"],
        "access_state": "active",
        "access_states": ["active"],
        "enrollments": [
            {
                "type": "TeacherEnrollment",
                "role": "Teacher",
                "role_id": 4,
                "enrollment_state": "active",
            },
            {
                "type": "TeacherEnrollment",
                "role": "Teacher",
                "role_id": 4,
                "enrollment_state": "active",
            },
        ],
    }

    persisted = json.loads(destination.read_text(encoding="utf-8"))
    assert persisted == discovery
    serialized = destination.read_text(encoding="utf-8")
    for private_value in (
        "Student One",
        "student.one@example.ac.uk",
        "private-sis-term",
        "private-sis-account",
        secret_token,
        "current_score",
        "computed_final_score",
        "user_id",
    ):
        assert private_value not in serialized


@pytest.mark.parametrize(
    ("raw_course", "expected_url", "expected_published"),
    [
        ({}, None, False),
        (
            {
                "id": 91,
                "html_url": "   ",
                "term": "not-a-mapping",
                "account": [],
                "enrollments": None,
            },
            "https://canvas.example.ac.uk/courses/91",
            False,
        ),
        (
            {
                "id": 92,
                "workflow_state": "completed",
                "enrollments": [None, "bad item", 12],
            },
            "https://canvas.example.ac.uk/courses/92",
            True,
        ),
    ],
)
def test_normalise_course_tolerates_sparse_or_malformed_optional_fields(
    raw_course: dict[str, Any],
    expected_url: str | None,
    expected_published: bool,
) -> None:
    course = normalise_course(raw_course, "https://canvas.example.ac.uk")

    assert course["id"] == raw_course.get("id")
    assert course["canvas_url"] == expected_url
    assert course["published"] is expected_published
    assert course["term"] is None
    assert course["account"] is None
    assert course["enrollments"] == []
    assert course["enrollment_types"] == []
    assert course["current_user_roles"] == []
    assert course["access_states"] == []
    assert course["current_user_role"] is None


def test_normalise_course_does_not_persist_a_server_supplied_signed_url() -> None:
    course = normalise_course(
        {
            "id": 42,
            "name": "Safe URL",
            "html_url": (
                "https://canvas.example.ac.uk/courses/42?access_token=secret"
            ),
        },
        "https://canvas.example.ac.uk",
    )
    assert course["canvas_url"] == "https://canvas.example.ac.uk/courses/42"
    assert "secret" not in repr(course)


def test_write_json_atomic_creates_folders_and_uses_sibling_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "new" / "nested" / "courses.json"
    payload = {"courses": [{"name": "Mécanique"}]}
    replacements: list[tuple[Path, Path, bool]] = []
    original_replace = Path.replace

    def observe_replace(source: Path, target: Path) -> Path:
        target = Path(target)
        replacements.append((source, target, source.exists()))
        assert source.parent == target.parent
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", observe_replace)

    write_json_atomic(destination, payload)

    assert json.loads(destination.read_text(encoding="utf-8")) == payload
    assert destination.read_text(encoding="utf-8").endswith("\n")
    assert replacements == [
        (destination.with_name(".courses.json.tmp"), destination, True)
    ]
    assert not destination.with_name(".courses.json.tmp").exists()
