"""Human- and machine-readable indexes for local Canvas archives."""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Any

from .course_discovery import write_json_atomic


def create_content_index(
    course: dict[str, Any],
    modules: list[dict[str, Any]],
    local_references: dict[str, dict[str, str]],
    external_links: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the course/module/item relationship graph used by Codex."""

    indexed_modules: list[dict[str, Any]] = []
    for module in sorted(modules, key=lambda value: _position(value.get("position"))):
        indexed_items = []
        for item in sorted(
            module.get("items", []), key=lambda value: _position(value.get("position"))
        ):
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "Unknown")
            reference = _local_reference(item_type, item, local_references)
            indexed_items.append(
                {
                    "module_item_id": item.get("id"),
                    "type": item_type,
                    "title": item.get("title"),
                    "canvas_id": item.get("content_id"),
                    "position": item.get("position"),
                    "published": item.get("published"),
                    "indent": item.get("indent"),
                    "page_url": item.get("page_url"),
                    "external_url": item.get("external_url"),
                    "local_path": reference,
                }
            )
        indexed_modules.append(
            {
                "module_id": module.get("id"),
                "name": module.get("name"),
                "position": module.get("position"),
                "published": module.get("published"),
                "unlock_at": module.get("unlock_at"),
                "prerequisite_module_ids": module.get("prerequisite_module_ids"),
                "items": indexed_items,
            }
        )

    term = course.get("term") if isinstance(course.get("term"), dict) else None
    return {
        "schema_version": 1,
        "course_id": course.get("id"),
        "course_name": course.get("name"),
        "course_code": course.get("course_code"),
        "term": term,
        "modules": indexed_modules,
        "unplaced_content": _unplaced_content(local_references, indexed_modules),
        "external_links": external_links,
    }


def write_content_indexes(course_root: Path, index: dict[str, Any]) -> tuple[Path, Path]:
    """Write both index formats under the course's indexes directory."""

    directory = Path(course_root) / "indexes"
    json_path = directory / "content_index.json"
    markdown_path = directory / "content_index.md"
    write_json_atomic(json_path, index)
    _write_text_atomic(markdown_path, content_index_markdown(index))
    return json_path, markdown_path


def content_index_markdown(index: dict[str, Any]) -> str:
    """Render a compact Markdown map whose links are relative to the index."""

    lines = [f"# {_text(index.get('course_name'), 'Unnamed Canvas course')}", ""]
    lines.extend(
        [
            f"- Canvas course ID: {_text(index.get('course_id'))}",
            f"- Course code: {_text(index.get('course_code'))}",
            f"- Term: {_term_name(index.get('term'))}",
            "",
        ]
    )
    modules = index.get("modules") or []
    if not modules:
        lines.extend(["_No accessible modules were returned._", ""])
    for number, module in enumerate(modules, start=1):
        lines.extend(
            [
                f"## Module {number} — {_text(module.get('name'), 'Unnamed module')}",
                "",
            ]
        )
        items = module.get("items") or []
        if not items:
            lines.extend(["_No accessible module items._", ""])
            continue
        for item in items:
            item_type = _text(item.get("type"), "Item")
            title = _text(item.get("title"), "Untitled")
            lines.append(f"- {item_type}: {title}")
            local_path = item.get("local_path")
            if local_path:
                from_index = PurePosixPath("..") / PurePosixPath(str(local_path))
                lines.append(f"  - Local: [{local_path}]({from_index.as_posix()})")
            elif item.get("external_url"):
                lines.append(f"  - External URL: {item['external_url']}")
        lines.append("")

    unplaced = index.get("unplaced_content") or []
    if unplaced:
        lines.extend(["## Other archived content", ""])
        for entry in unplaced:
            path = entry.get("local_path")
            label = _text(entry.get("title"), path or "Archived item")
            if path:
                target = (PurePosixPath("..") / PurePosixPath(path)).as_posix()
                lines.append(f"- {entry.get('type', 'Content')}: [{label}]({target})")
            else:
                lines.append(f"- {entry.get('type', 'Content')}: {label}")
        lines.append("")

    links = index.get("external_links") or []
    if links:
        lines.extend(
            [
                "## External references",
                "",
                "These links are recorded only; the external sites were not downloaded.",
                "",
            ]
        )
        for link in links:
            lines.append(
                f"- [{_text(link.get('title') or link.get('source_title'), 'External link')}]"
                f"({link.get('url')})"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def generate_global_indexes(backup_root: Path) -> tuple[Path, Path]:
    """Rebuild the global indexes from locally present course manifests."""

    root = Path(backup_root)
    root.mkdir(parents=True, exist_ok=True)
    courses: list[dict[str, Any]] = []
    for manifest_path in root.glob("*/*/manifest.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        course = manifest.get("course") or {}
        relative_root = manifest_path.parent.relative_to(root).as_posix()
        courses.append(
            {
                "course_id": course.get("id"),
                "course_name": course.get("name"),
                "course_code": course.get("course_code"),
                "term": course.get("term"),
                "backup_status": manifest.get("backup_status"),
                "completed_at": manifest.get("completed_at"),
                "course_root": relative_root,
                "manifest": f"{relative_root}/manifest.json",
                "content_index": f"{relative_root}/indexes/content_index.md",
            }
        )
    courses.sort(
        key=lambda item: (
            _term_name(item.get("term")).casefold(),
            _text(item.get("course_name"), "").casefold(),
            str(item.get("course_id")),
        )
    )

    payload = {"schema_version": 1, "course_count": len(courses), "courses": courses}
    json_path = root / "course_index.json"
    markdown_path = root / "COURSE_INDEX.md"
    write_json_atomic(json_path, payload)

    lines = ["# Canvas Course Archive", ""]
    if not courses:
        lines.extend(["_No local course backups were found._", ""])
    current_term: str | None = None
    for course in courses:
        term = _term_name(course.get("term"))
        if term != current_term:
            lines.extend([f"## {term}", ""])
            current_term = term
        name = _text(course.get("course_name"), "Unnamed Canvas course")
        lines.extend(
            [
                f"### {name}",
                "",
                f"- Canvas ID: {_text(course.get('course_id'))}",
                f"- Course code: {_text(course.get('course_code'))}",
                f"- Backup status: {_text(course.get('backup_status'))}",
                f"- [Content index]({course['content_index']})",
                f"- [Manifest]({course['manifest']})",
                "",
            ]
        )
    _write_text_atomic(markdown_path, "\n".join(lines).rstrip() + "\n")
    return markdown_path, json_path


def _local_reference(
    item_type: str,
    item: dict[str, Any],
    references: dict[str, dict[str, str]],
) -> str | None:
    type_key = item_type.casefold().replace(" ", "_")
    type_alias = {
        "file": "files",
        "page": "pages",
        "assignment": "assignments",
        "quiz": "quizzes",
        "discussion": "discussions",
    }.get(type_key, f"{type_key}s")
    values = references.get(type_alias, {})
    candidates = (item.get("content_id"), item.get("page_url"), item.get("url"))
    for candidate in candidates:
        if candidate is not None and str(candidate) in values:
            return values[str(candidate)]
    return None


def _unplaced_content(
    references: dict[str, dict[str, str]],
    modules: list[dict[str, Any]],
) -> list[dict[str, str]]:
    placed = {
        str(item.get("local_path"))
        for module in modules
        for item in module.get("items", [])
        if item.get("local_path")
    }
    results = []
    seen_paths = set(placed)
    for content_type, values in sorted(references.items()):
        for identifier, path in values.items():
            if path not in seen_paths:
                seen_paths.add(path)
                results.append(
                    {
                        "type": content_type.rstrip("s").replace("_", " ").title(),
                        "canvas_reference": identifier,
                        "title": Path(path).stem,
                        "local_path": path,
                    }
                )
    return results


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _position(value: Any) -> tuple[int, str]:
    try:
        return int(value), ""
    except (TypeError, ValueError):
        return 2**31 - 1, str(value or "")


def _text(value: Any, fallback: str = "Not available") -> str:
    return fallback if value is None or str(value).strip() == "" else str(value).strip()


def _term_name(value: Any) -> str:
    if isinstance(value, dict):
        return _text(value.get("name"), "No term")
    return "No term"


__all__ = [
    "content_index_markdown",
    "create_content_index",
    "generate_global_indexes",
    "write_content_indexes",
]
