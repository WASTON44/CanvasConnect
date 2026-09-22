"""Tests for student-data exclusion and signed-URL redaction."""

from __future__ import annotations

from src.privacy import (
    redact_sensitive_query,
    safe_course_metadata,
    safe_file_metadata,
    sanitize_assignment,
    sanitize_discussion,
    sanitize_rubric,
)


def test_signed_url_query_is_redacted() -> None:
    url = (
        "https://files.example.edu/item?download=1&token=secret&"
        "X-Amz-Signature=signature&filename=notes.pdf"
    )
    result = redact_sensitive_query(url)
    assert "secret" not in result
    assert "signature" not in result
    assert "download=1" in result
    assert "filename=notes.pdf" in result


def test_assignment_and_rubric_student_records_are_removed_recursively() -> None:
    assignment = {
        "id": 1,
        "name": "Essay",
        "submission": {"user_id": 99},
        "nested": {"submissions": [{"student": "A"}]},
    }
    rubric = {"id": 2, "criteria": [], "assessments": [{"user_id": 99}]}

    cleaned_assignment = sanitize_assignment(assignment)
    cleaned_rubric = sanitize_rubric(rubric)

    assert cleaned_assignment == {"id": 1, "name": "Essay", "nested": {}}
    assert cleaned_rubric == {"id": 2, "criteria": []}


def test_discussion_keeps_teaching_message_but_removes_people_and_replies() -> None:
    topic = {
        "id": 4,
        "title": "Week one",
        "message": "<p>Welcome</p>",
        "author": {"id": 7, "display_name": "Lecturer"},
        "entries": [{"user_id": 99, "message": "student response"}],
        "permissions": {"attach": True},
    }

    cleaned = sanitize_discussion(topic)

    assert cleaned["message"] == "<p>Welcome</p>"
    assert "author" not in cleaned
    assert "entries" not in cleaned
    assert cleaned["permissions"] == {"attach": True}


def test_file_metadata_never_stores_download_urls_or_uploader() -> None:
    cleaned = safe_file_metadata(
        {
            "id": 3,
            "display_name": "slides.pdf",
            "size": 42,
            "url": "https://files.example.edu/?token=secret",
            "preview_url": "https://canvas.example.edu/preview",
            "user": {"id": 99, "name": "Student"},
        }
    )
    assert cleaned == {"id": 3, "display_name": "slides.pdf", "size": 42}


def test_course_metadata_is_projected_onto_safe_fields() -> None:
    cleaned = safe_course_metadata(
        {
            "id": 5,
            "name": "Dynamics",
            "course_code": "ENG5",
            "calendar": {"ics": "https://example/?token=secret"},
            "teachers": [{"name": "Private Person"}],
            "term": {"id": 8, "name": "Autumn"},
        },
        "https://canvas.example.edu",
    )
    assert cleaned["id"] == 5
    assert cleaned["term"] == {"id": 8, "name": "Autumn"}
    assert "calendar" not in cleaned
    assert "teachers" not in cleaned

