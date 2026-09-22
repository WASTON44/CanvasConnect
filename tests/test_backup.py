"""End-to-end offline test of a small incremental course archive."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.backup import CourseBackup
from src.downloader import DownloadResult


class FakeCanvasClient:
    base_url = "https://canvas.example.edu"

    def get_course(self, course_id: Any) -> dict[str, Any]:
        return {
            "id": course_id,
            "name": "Dynamics",
            "course_code": "ENG101",
            "term": {"id": 2, "name": "Autumn 2026"},
            "syllabus_body": "<h1>Syllabus</h1>",
        }

    def get_modules(self, course_id: Any) -> list[dict[str, Any]]:
        return [{"id": 4, "name": "Week 1", "position": 1, "published": True}]

    def get_module_items(self, course_id: Any, module_id: Any) -> list[dict[str, Any]]:
        return [
            {
                "id": 5,
                "content_id": 10,
                "type": "File",
                "title": "Lecture.pdf",
                "position": 1,
                "published": True,
            }
        ]

    def get_pages(self, course_id: Any) -> list[dict[str, Any]]:
        return [{"page_id": 6, "url": "welcome", "title": "Welcome"}]

    def get_page(self, course_id: Any, page: Any) -> dict[str, Any]:
        return {
            "page_id": 6,
            "url": "welcome",
            "title": "Welcome",
            "body": '<p>Read <a href="https://reference.example/book">this</a>.</p>',
            "last_edited_by": {"id": 99, "name": "Person"},
        }

    def get_folders(self, course_id: Any) -> list[dict[str, Any]]:
        return [{"id": 8, "full_name": "course files/Week 1"}]

    def get_files(self, course_id: Any) -> list[dict[str, Any]]:
        return [
            {
                "id": 10,
                "folder_id": 8,
                "display_name": "Lecture.pdf",
                "size": 3,
                "updated_at": "2026-09-01T00:00:00Z",
                "url": "https://files.example.edu/download?token=do-not-store",
            }
        ]

    def get_assignments(self, course_id: Any) -> list[dict[str, Any]]:
        return [
            {
                "id": 11,
                "name": "Problem sheet",
                "description": "<p>Questions</p>",
                "submission": {"user_id": 99},
            }
        ]

    def get_assignment_groups(self, course_id: Any) -> list[dict[str, Any]]:
        return [{"id": 12, "name": "Coursework"}]

    def get_rubrics(self, course_id: Any) -> list[dict[str, Any]]:
        return [{"id": 13, "title": "Rubric", "assessments": [{"user_id": 99}]}]

    def get_classic_quizzes(self, course_id: Any) -> list[dict[str, Any]]:
        return [{"id": 14, "title": "Check-in"}]

    def get_classic_quiz_questions(self, course_id: Any, quiz_id: Any) -> list[dict[str, Any]]:
        return [{"id": 15, "question_text": "2 + 2?", "answers": [{"text": "4"}]}]

    def get_new_quizzes(self, course_id: Any) -> list[dict[str, Any]]:
        return []

    def get_new_quiz_items(self, course_id: Any, quiz_id: Any) -> list[dict[str, Any]]:
        raise AssertionError("No New Quiz item request was expected")

    def get_discussions(self, course_id: Any) -> list[dict[str, Any]]:
        return [
            {
                "id": 16,
                "title": "Welcome discussion",
                "message": "<p>Discuss</p>",
                "author": {"id": 77, "name": "Person"},
                "entries": [{"user_id": 99}],
            }
        ]

    def get_announcements(self, course_id: Any) -> list[dict[str, Any]]:
        return [{"id": 17, "title": "Reminder", "message": "<p>Hello</p>"}]


class FakeDownloader:
    calls = 0

    def __init__(self, client: Any) -> None:
        pass

    def __enter__(self):  # type: ignore[no-untyped-def]
        return self

    def __exit__(self, *args: Any) -> None:
        pass

    def download(
        self,
        url: str,
        destination: Path,
        *,
        expected_size: int | None = None,
    ) -> DownloadResult:
        assert "do-not-store" in url
        assert expected_size == 3
        type(self).calls += 1
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"abc")
        return DownloadResult(size=3, sha256="ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")


def test_full_backup_then_incremental_rerun(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("src.backup.FileDownloader", FakeDownloader)
    FakeDownloader.calls = 0
    selected = {
        "id": 1,
        "name": "Dynamics",
        "course_code": "ENG101",
        "term": {"id": 2, "name": "Autumn 2026"},
    }
    manager = CourseBackup(FakeCanvasClient(), tmp_path)  # type: ignore[arg-type]

    first = manager.run(selected)
    second = manager.run(selected, resume=True)

    assert first.status == "complete"
    assert second.status == "complete"
    assert FakeDownloader.calls == 1
    assert second.counts["files_unchanged"] == 1
    assert second.course_root is not None
    course_root = second.course_root
    assert (course_root / "syllabus" / "syllabus.html").exists()
    assert (course_root / "files" / "Week 1" / "Lecture.pdf").read_bytes() == b"abc"
    assert (course_root / "indexes" / "content_index.md").exists()
    assert (tmp_path / "COURSE_INDEX.md").exists()

    manifest = json.loads((course_root / "manifest.json").read_text(encoding="utf-8"))
    files = json.loads((course_root / "files" / "files.json").read_text(encoding="utf-8"))
    assignments = (course_root / "assignments" / "assignments.json").read_text(
        encoding="utf-8"
    )
    discussions = (course_root / "discussions" / "discussions.json").read_text(
        encoding="utf-8"
    )
    all_json = "\n".join(
        path.read_text(encoding="utf-8") for path in course_root.rglob("*.json")
    )

    assert manifest["checksums"][0]["sha256"].startswith("ba7816")
    assert files["files"][0]["backup_state"] == "unchanged"
    assert "do-not-store" not in all_json
    assert "submission" not in assignments
    assert "user_id" not in discussions
    assert "last_edited_by" not in all_json

    missing_client = FakeCanvasClient()
    missing_client.get_files = lambda course_id: []  # type: ignore[method-assign]
    third = CourseBackup(missing_client, tmp_path).run(selected)  # type: ignore[arg-type]
    retained = json.loads(
        (third.course_root / "files" / "files.json").read_text(encoding="utf-8")
    )["files"][0]
    assert retained["backup_state"] == "retained_not_present_in_canvas"
    assert retained["present_in_latest_canvas_inventory"] is False
    assert (third.course_root / retained["local_path"]).read_bytes() == b"abc"
