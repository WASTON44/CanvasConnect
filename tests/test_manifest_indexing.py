"""Offline tests for manifests and course indexes."""

from __future__ import annotations

import json
from pathlib import Path

from src.indexing import (
    content_index_markdown,
    create_content_index,
    generate_global_indexes,
    write_content_indexes,
)
from src.manifest import add_warning, create_manifest, finalise_manifest, save_manifest


COURSE = {
    "id": 123,
    "name": "Engineering Dynamics",
    "course_code": "ENG101",
    "term": {"id": 9, "name": "2025/26"},
}


def test_manifest_creation_and_partial_finalisation(tmp_path: Path) -> None:
    manifest = create_manifest(
        COURSE,
        canvas_base_url="https://canvas.example.edu",
        course_root=tmp_path,
        resume=True,
    )
    add_warning(manifest, "New Quizzes unavailable", endpoint="new_quizzes")
    finalise_manifest(manifest)
    path = save_manifest(tmp_path, manifest)
    stored = json.loads(path.read_text(encoding="utf-8"))

    assert stored["application_version"] == "0.1.0"
    assert stored["backup_status"] == "partial"
    assert stored["resume_requested"] is True
    assert stored["course"]["id"] == 123
    assert stored["inaccessible_endpoints"] == ["new_quizzes"]
    assert "token" not in json.dumps(stored).casefold()


def test_content_index_maps_module_items_to_relative_local_content(tmp_path: Path) -> None:
    modules = [
        {
            "id": 2,
            "name": "Week 1",
            "position": 1,
            "items": [
                {
                    "id": 20,
                    "content_id": 30,
                    "type": "File",
                    "title": "Lecture.pdf",
                    "position": 1,
                }
            ],
        }
    ]
    index = create_content_index(
        COURSE,
        modules,
        {"files": {"30": "files/Week 1/Lecture.pdf"}},
        [],
    )

    json_path, markdown_path = write_content_indexes(tmp_path, index)
    markdown = markdown_path.read_text(encoding="utf-8")

    assert json.loads(json_path.read_text(encoding="utf-8"))["course_id"] == 123
    assert index["modules"][0]["items"][0]["local_path"] == "files/Week 1/Lecture.pdf"
    assert "../files/Week%201" not in markdown
    assert "../files/Week 1/Lecture.pdf" in markdown
    assert markdown == content_index_markdown(index)


def test_global_course_indexes_are_generated_from_manifests(tmp_path: Path) -> None:
    course_root = tmp_path / "2025-26" / "ENG101_Dynamics__123"
    manifest = create_manifest(
        COURSE,
        canvas_base_url="https://canvas.example.edu",
        course_root=course_root,
        resume=False,
    )
    finalise_manifest(manifest)
    save_manifest(course_root, manifest)

    markdown_path, json_path = generate_global_indexes(tmp_path)
    markdown = markdown_path.read_text(encoding="utf-8")
    payload = json.loads(json_path.read_text(encoding="utf-8"))

    assert payload["course_count"] == 1
    assert payload["courses"][0]["course_id"] == 123
    assert "## 2025/26" in markdown
    assert "2025-26/ENG101_Dynamics__123/indexes/content_index.md" in markdown

