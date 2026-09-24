from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.local_upload import (
    ActiveCourse,
    CanvasWriteClient,
    UploadError,
    _flatten_form,
    _map_by_name_and_position,
    _new_quiz_item,
)


def test_active_course_uses_the_target_id_encoded_in_the_working_copy_name(tmp_path: Path) -> None:
    root = tmp_path / "NTO1012_Example__38826"
    root.mkdir()
    (root / "course.json").write_text(
        json.dumps({"id": 34419, "course_code": "NTO1012", "name": "Example"}),
        encoding="utf-8",
    )

    course = ActiveCourse.load(root)

    assert course.source_course_id == 34419
    assert course.target_course_id == 38826
    assert course.course_code == "NTO1012"


def test_new_quiz_item_remaps_choice_ids_and_removes_signed_media_url() -> None:
    item = {
        "position": 1,
        "points_possible": 2,
        "properties": {},
        "entry_type": "Item",
        "entry": {
            "item_body": '<img src="https://files.example.edu/files/id/circuit-2.png?token=secret">',
            "interaction_data": {"choices": [{"id": "old-choice", "item_body": "A"}]},
            "scoring_data": {"value": "old-choice"},
            "interaction_type_slug": "choice",
        },
    }

    result = _new_quiz_item(
        item,
        lambda value: value,
        {"circuit-2.png": 12},
        {12: 99},
        "https://canvas.example.edu",
        38826,
    )

    choice_id = result["entry"]["interaction_data"]["choices"][0]["id"]
    assert choice_id != "old-choice"
    assert result["entry"]["scoring_data"]["value"] == choice_id
    assert "token=" not in result["entry"]["item_body"]
    assert "/courses/38826/files/99/preview" in result["entry"]["item_body"]


def test_new_quiz_item_can_omit_only_an_unavailable_image_tag() -> None:
    item = {
        "position": 1,
        "points_possible": 2,
        "properties": {},
        "entry_type": "Item",
        "entry": {
            "item_body": (
                '<p>Question text remains.</p><p><img src="https://files.example.edu/files/id/image.png?token=secret" '
                'data-old-link="/courses/1/files/2/preview"></p>'
            ),
            "interaction_data": {"choices": [{"id": "old-choice", "item_body": "A"}]},
            "scoring_data": {"value": "old-choice"},
            "interaction_type_slug": "choice",
        },
    }

    result = _new_quiz_item(
        item,
        lambda value: value,
        {},
        {},
        "https://canvas.example.edu",
        38829,
        omit_missing_image_tags=True,
    )

    body = result["entry"]["item_body"]
    assert "Question text remains." in body
    assert "<img" not in body
    assert "token=" not in body
    assert "data-old-link" not in body


def test_new_quiz_item_does_not_omit_missing_non_image_media() -> None:
    item = {
        "position": 1,
        "points_possible": 1,
        "properties": {},
        "entry_type": "Item",
        "entry": {
            "item_body": '<p><a href="https://files.example.edu/files/id/handout.pdf?token=secret">Handout</a></p>',
            "interaction_data": {},
            "scoring_data": {},
            "interaction_type_slug": "essay",
        },
    }

    with pytest.raises(UploadError, match="handout.pdf"):
        _new_quiz_item(
            item,
            lambda value: value,
            {},
            {},
            "https://canvas.example.edu",
            38829,
            omit_missing_image_tags=True,
        )


def test_flatten_form_keeps_nested_list_structure() -> None:
    assert _flatten_form("question", {"answers": [{"text": "A", "weight": 100}]}) == [
        ("question[answers][0][text]", "A"),
        ("question[answers][0][weight]", 100),
    ]


def test_resume_maps_repeated_assignment_group_names_by_position() -> None:
    source = [
        {"id": 1, "name": "Assignments", "position": 1},
        {"id": 2, "name": "Assignments", "position": 2},
    ]
    target = [
        {"id": 10, "name": "Assignments", "position": 1},
        {"id": 20, "name": "Assignments", "position": 2},
    ]

    assert _map_by_name_and_position(source, target, "assignment group") == {1: 10, 2: 20}


def test_write_client_requires_https() -> None:
    with pytest.raises(UploadError, match="HTTPS"):
        CanvasWriteClient("http://canvas.example.edu", "token")
