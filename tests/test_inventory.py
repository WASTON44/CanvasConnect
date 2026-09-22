"""Dry-run inventory behaviour when individual Canvas endpoints differ."""

from __future__ import annotations

from typing import Any

from src.exceptions import CanvasPermissionError
from src.inventory import inspect_course


class SparseClient:
    def get_course(self, course_id: Any):  # type: ignore[no-untyped-def]
        return {"id": course_id, "name": "Course"}

    def get_modules(self, course_id: Any):  # type: ignore[no-untyped-def]
        return [{"id": 2, "name": "Module"}]

    def get_module_items(self, course_id: Any, module_id: Any):  # type: ignore[no-untyped-def]
        return [{"id": 3}]

    def get_pages(self, course_id: Any):  # type: ignore[no-untyped-def]
        return []

    def get_folders(self, course_id: Any):  # type: ignore[no-untyped-def]
        return []

    def get_files(self, course_id: Any):  # type: ignore[no-untyped-def]
        return [{"id": 4, "size": 1024}, {"id": 5, "size": 2048}]

    def get_assignments(self, course_id: Any):  # type: ignore[no-untyped-def]
        return []

    def get_assignment_groups(self, course_id: Any):  # type: ignore[no-untyped-def]
        return []

    def get_rubrics(self, course_id: Any):  # type: ignore[no-untyped-def]
        raise CanvasPermissionError("not available")

    def get_classic_quizzes(self, course_id: Any):  # type: ignore[no-untyped-def]
        return []

    def get_new_quizzes(self, course_id: Any):  # type: ignore[no-untyped-def]
        return []

    def get_discussions(self, course_id: Any):  # type: ignore[no-untyped-def]
        return []

    def get_announcements(self, course_id: Any):  # type: ignore[no-untyped-def]
        return []


def test_inventory_counts_sizes_and_continues_after_permission_error() -> None:
    inventory = inspect_course(SparseClient(), {"id": 1, "name": "Course"})  # type: ignore[arg-type]

    assert inventory.counts["modules"] == 1
    assert inventory.counts["module_items"] == 1
    assert inventory.counts["files"] == 2
    assert inventory.estimated_download_bytes == 3072
    assert inventory.category_status["rubrics"] == "inaccessible"
    assert inventory.inaccessible_endpoints == ["rubrics"]
    assert len(inventory.warnings) == 1


def test_inventory_size_is_unknown_if_canvas_omits_a_file_size() -> None:
    client = SparseClient()
    client.get_files = lambda course_id: [{"id": 4}]  # type: ignore[method-assign]
    inventory = inspect_course(client, {"id": 1})  # type: ignore[arg-type]
    assert inventory.estimated_download_bytes is None

