from __future__ import annotations

from pathlib import Path

import pytest

from src.normalise import (
    canvas_folder_parts,
    conflict_filename,
    course_backup_path,
    course_folder_name,
    ensure_within,
    sanitize_component,
    sanitize_filename,
)


def test_windows_invalid_characters_and_trailing_dots_are_removed() -> None:
    assert sanitize_component(' Week <1>: "Intro" / files? * . ') == "Week _1_ _Intro_ _ files_ _"


@pytest.mark.parametrize("name", ["CON", "con.txt", "LPT1", "aux.pdf", "NUL"])
def test_windows_reserved_names_are_prefixed(name: str) -> None:
    assert sanitize_filename(name).startswith("_")


def test_long_filename_preserves_extension() -> None:
    result = sanitize_filename("a" * 200 + ".pdf", max_length=40)
    assert len(result) == 40
    assert result.endswith(".pdf")


def test_course_folder_is_readable_and_contains_canvas_id(tmp_path: Path) -> None:
    course = {
        "id": 12345,
        "name": "Engineering: Dynamics",
        "course_code": "ENG/101",
        "term": {"name": "2026/27"},
    }
    assert course_folder_name(course) == "ENG_101_Engineering_ Dynamics__12345"
    assert course_backup_path(tmp_path, course) == (
        tmp_path / "2026_27" / "ENG_101_Engineering_ Dynamics__12345"
    )


def test_canvas_folder_hierarchy_is_safe() -> None:
    assert canvas_folder_parts(r"course files\Week 1\CON\..\Slides") == [
        "Week 1",
        "_CON",
        "Slides",
    ]
    assert conflict_filename("lecture.notes.pdf", 99) == "lecture.notes__canvas_99.pdf"


def test_ensure_within_rejects_traversal(tmp_path: Path) -> None:
    root = tmp_path / "course"
    assert ensure_within(root, root / "files" / "item.pdf") == (
        root / "files" / "item.pdf"
    ).resolve()
    with pytest.raises(ValueError):
        ensure_within(root, root / ".." / "outside.txt")

