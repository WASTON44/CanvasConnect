"""Course backup manifest creation and atomic checkpointing."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .course_discovery import write_json_atomic
from .version import __version__


CONTENT_CATEGORIES = (
    "course",
    "syllabus",
    "modules",
    "pages",
    "folders",
    "files",
    "assignments",
    "assignment_groups",
    "rubrics",
    "classic_quizzes",
    "new_quizzes",
    "discussions",
    "announcements",
    "external_links",
)


def create_manifest(
    course: dict[str, Any],
    *,
    canvas_base_url: str,
    course_root: Path,
    resume: bool,
) -> dict[str, Any]:
    """Create a privacy-conscious manifest ready for incremental checkpoints."""

    now = datetime.now(UTC).isoformat()
    term = course.get("term") if isinstance(course.get("term"), dict) else None
    return {
        "schema_version": 1,
        "application_version": __version__,
        "canvas_base_url": canvas_base_url,
        "course": {
            "id": course.get("id"),
            "name": course.get("name"),
            "course_code": course.get("course_code"),
            "term": term,
        },
        "started_at": now,
        "completed_at": None,
        "backup_status": "in_progress",
        "resume_requested": bool(resume),
        "categories": {
            name: {"status": "pending", "count": 0} for name in CONTENT_CATEGORIES
        },
        "counts": {
            "modules": 0,
            "module_items": 0,
            "pages": 0,
            "files": 0,
            "files_downloaded": 0,
            "files_unchanged": 0,
            "assignments": 0,
            "assignment_groups": 0,
            "rubrics": 0,
            "classic_quizzes": 0,
            "new_quizzes": 0,
            "quizzes": 0,
            "discussions": 0,
            "announcements": 0,
            "external_links": 0,
        },
        "estimated_download_bytes": None,
        "downloaded_bytes": 0,
        "skipped_content": [],
        "inaccessible_endpoints": [],
        "warnings": [],
        "errors": [],
        "local_paths": {
            "course_root": ".",
            "content_index_json": "indexes/content_index.json",
            "content_index_markdown": "indexes/content_index.md",
        },
        "checksums": [],
    }


def set_category(
    manifest: dict[str, Any],
    category: str,
    *,
    status: str,
    count: int = 0,
    message: str | None = None,
) -> None:
    """Record a category result and its optional non-secret message."""

    entry: dict[str, Any] = {"status": status, "count": int(count)}
    if message:
        entry["message"] = message
    manifest.setdefault("categories", {})[category] = entry


def add_warning(
    manifest: dict[str, Any],
    message: str,
    *,
    endpoint: str | None = None,
) -> None:
    """Record a warning and, where relevant, an inaccessible endpoint label."""

    warnings = manifest.setdefault("warnings", [])
    if message not in warnings:
        warnings.append(message)
    if endpoint:
        endpoints = manifest.setdefault("inaccessible_endpoints", [])
        if endpoint not in endpoints:
            endpoints.append(endpoint)


def add_error(manifest: dict[str, Any], message: str) -> None:
    errors = manifest.setdefault("errors", [])
    if message not in errors:
        errors.append(message)


def finalise_manifest(manifest: dict[str, Any]) -> None:
    """Mark a manifest complete, partial, or failed based on recorded outcomes."""

    manifest["completed_at"] = datetime.now(UTC).isoformat()
    errors = manifest.get("errors") or []
    categories = manifest.get("categories") or {}
    completed = sum(
        1 for value in categories.values() if value.get("status") == "complete"
    )
    if errors and completed == 0:
        manifest["backup_status"] = "failed"
    elif errors or manifest.get("warnings") or manifest.get("inaccessible_endpoints"):
        manifest["backup_status"] = "partial"
    else:
        manifest["backup_status"] = "complete"


def save_manifest(course_root: Path, manifest: dict[str, Any]) -> Path:
    path = Path(course_root) / "manifest.json"
    write_json_atomic(path, manifest)
    return path


__all__ = [
    "CONTENT_CATEGORIES",
    "add_error",
    "add_warning",
    "create_manifest",
    "finalise_manifest",
    "save_manifest",
    "set_category",
]
