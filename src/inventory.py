"""Read-only inspection of the teaching content available in one course."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any, Callable

from .canvas_client import CanvasClient
from .exceptions import CanvasError


LOGGER = logging.getLogger(__name__)


@dataclass
class CourseInventory:
    """A transient inventory; nothing here is written until backup begins."""

    course: dict[str, Any]
    modules: list[dict[str, Any]] = field(default_factory=list)
    pages: list[dict[str, Any]] = field(default_factory=list)
    folders: list[dict[str, Any]] = field(default_factory=list)
    files: list[dict[str, Any]] = field(default_factory=list)
    assignments: list[dict[str, Any]] = field(default_factory=list)
    assignment_groups: list[dict[str, Any]] = field(default_factory=list)
    rubrics: list[dict[str, Any]] = field(default_factory=list)
    classic_quizzes: list[dict[str, Any]] = field(default_factory=list)
    new_quizzes: list[dict[str, Any]] = field(default_factory=list)
    discussions: list[dict[str, Any]] = field(default_factory=list)
    announcements: list[dict[str, Any]] = field(default_factory=list)
    category_status: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    inaccessible_endpoints: list[str] = field(default_factory=list)

    @property
    def estimated_download_bytes(self) -> int | None:
        total = 0
        for file_data in self.files:
            try:
                size = int(file_data["size"])
            except (KeyError, TypeError, ValueError):
                return None
            if size < 0:
                return None
            total += size
        return total

    @property
    def counts(self) -> dict[str, int]:
        return {
            "modules": len(self.modules),
            "module_items": sum(
                len(module.get("items", [])) for module in self.modules
            ),
            "pages": len(self.pages),
            "files": len(self.files),
            "assignments": len(self.assignments),
            "assignment_groups": len(self.assignment_groups),
            "rubrics": len(self.rubrics),
            "classic_quizzes": len(self.classic_quizzes),
            "new_quizzes": len(self.new_quizzes),
            "quizzes": len(self.classic_quizzes) + len(self.new_quizzes),
            "discussions": len(self.discussions),
            "announcements": len(self.announcements),
        }


def inspect_course(
    client: CanvasClient,
    selected_course: dict[str, Any],
) -> CourseInventory:
    """Inspect all v0.1 teaching-content categories without downloading files."""

    course_id = selected_course.get("id")
    inventory = CourseInventory(course=dict(selected_course))
    LOGGER.info("Inspecting Canvas course %s.", course_id)

    try:
        inventory.course = client.get_course(course_id)
        inventory.category_status["course"] = "complete"
    except CanvasError as error:
        _warn(inventory, "course", error)

    inventory.modules = _list_or_warning(
        inventory, "modules", lambda: client.get_modules(course_id)
    )
    module_item_failures = False
    for module in inventory.modules:
        module_copy = dict(module)
        try:
            module_copy["items"] = client.get_module_items(course_id, module.get("id"))
        except CanvasError as error:
            module_copy["items"] = []
            module_item_failures = True
            _warn(inventory, "module_items", error, endpoint=False)
        module.clear()
        module.update(module_copy)
    if inventory.category_status.get("modules") == "complete":
        inventory.category_status["modules"] = (
            "partial" if module_item_failures else "complete"
        )

    getters: tuple[tuple[str, str, Callable[[], list[dict[str, Any]]]], ...] = (
        ("pages", "pages", lambda: client.get_pages(course_id)),
        ("folders", "folders", lambda: client.get_folders(course_id)),
        ("files", "files", lambda: client.get_files(course_id)),
        ("assignments", "assignments", lambda: client.get_assignments(course_id)),
        (
            "assignment_groups",
            "assignment_groups",
            lambda: client.get_assignment_groups(course_id),
        ),
        ("rubrics", "rubrics", lambda: client.get_rubrics(course_id)),
        (
            "classic_quizzes",
            "classic_quizzes",
            lambda: client.get_classic_quizzes(course_id),
        ),
        ("new_quizzes", "new_quizzes", lambda: client.get_new_quizzes(course_id)),
        ("discussions", "discussions", lambda: client.get_discussions(course_id)),
        (
            "announcements",
            "announcements",
            lambda: client.get_announcements(course_id),
        ),
    )
    for attribute, category, getter in getters:
        setattr(inventory, attribute, _list_or_warning(inventory, category, getter))
    return inventory


def _list_or_warning(
    inventory: CourseInventory,
    category: str,
    getter: Callable[[], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    try:
        values = getter()
    except CanvasError as error:
        _warn(inventory, category, error)
        return []
    inventory.category_status[category] = "complete"
    return values


def _warn(
    inventory: CourseInventory,
    category: str,
    error: CanvasError,
    *,
    endpoint: bool = True,
) -> None:
    message = f"{category}: {error}"
    inventory.warnings.append(message)
    inventory.category_status[category] = "inaccessible" if endpoint else "partial"
    if endpoint and category not in inventory.inaccessible_endpoints:
        inventory.inaccessible_endpoints.append(category)
    LOGGER.warning("Course %s %s", inventory.course.get("id"), message)


__all__ = ["CourseInventory", "inspect_course"]
