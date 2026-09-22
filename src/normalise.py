"""Windows-safe, deterministic local path naming."""

from __future__ import annotations

from pathlib import Path
import re
import unicodedata
from typing import Any


INVALID_WINDOWS_CHARACTERS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


def sanitize_component(
    value: Any,
    *,
    fallback: str = "unnamed",
    max_length: int = 100,
) -> str:
    """Return a Windows-safe single path component."""

    text = unicodedata.normalize("NFKC", str(value or ""))
    text = INVALID_WINDOWS_CHARACTERS.sub("_", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    text = re.sub(r"_+", "_", text)
    if not text or text in {".", ".."}:
        text = fallback
    base_name = text.split(".", 1)[0].upper()
    if base_name in WINDOWS_RESERVED_NAMES:
        text = f"_{text}"
    if len(text) > max_length:
        text = text[:max_length].rstrip(" .") or fallback
    return text


def sanitize_filename(
    value: Any,
    *,
    fallback: str = "unnamed_file",
    max_length: int = 120,
) -> str:
    """Sanitise a filename while retaining a short extension when possible."""

    cleaned = sanitize_component(value, fallback=fallback, max_length=32_767)
    if len(cleaned) <= max_length:
        return cleaned

    stem, separator, suffix = cleaned.rpartition(".")
    if not separator or not stem or len(suffix) > 16:
        return cleaned[:max_length].rstrip(" .") or fallback
    available = max_length - len(suffix) - 1
    return f"{stem[:available].rstrip(' .')}.{suffix}"


def term_folder_name(course: dict[str, Any]) -> str:
    """Use Canvas term data without assuming an academic-year convention."""

    term = course.get("term")
    if isinstance(term, dict):
        name = term.get("name")
        if name is not None and str(name).strip():
            return sanitize_component(name, fallback="no_term", max_length=60)
    return "no_term"


def course_folder_name(course: dict[str, Any]) -> str:
    """Build a readable collision-resistant course directory name."""

    code = sanitize_component(course.get("course_code"), fallback="no_code", max_length=50)
    name = sanitize_component(course.get("name"), fallback="unnamed_course", max_length=90)
    course_id = sanitize_component(course.get("id"), fallback="unknown_id", max_length=40)
    combined = f"{code}_{name}__{course_id}"
    return combined[:180].rstrip(" .") or f"course__{course_id}"


def course_backup_path(backup_root: Path, course: dict[str, Any]) -> Path:
    """Return the deterministic local root for a course archive."""

    return Path(backup_root) / term_folder_name(course) / course_folder_name(course)


def canvas_folder_parts(full_name: Any) -> list[str]:
    """Convert a Canvas folder path into safe relative components."""

    raw_parts = re.split(r"[/\\]+", str(full_name or ""))
    parts = [part for part in raw_parts if part and part not in {".", ".."}]
    if parts and parts[0].strip().casefold() == "course files":
        parts = parts[1:]
    return [
        sanitize_component(part, fallback="unnamed_folder", max_length=70)
        for part in parts
    ]


def conflict_filename(filename: str, canvas_id: Any) -> str:
    """Add a deterministic Canvas ID suffix before the file extension."""

    safe_id = sanitize_component(canvas_id, fallback="unknown", max_length=40)
    safe_filename = sanitize_filename(filename)
    path = Path(safe_filename)
    suffix = path.suffix
    stem = safe_filename[: -len(suffix)] if suffix else safe_filename
    tag = f"__canvas_{safe_id}"
    maximum = 120
    available = maximum - len(tag) - len(suffix)
    stem = stem[: max(1, available)].rstrip(" .") or "file"
    return f"{stem}{tag}{suffix}"


def ensure_within(root: Path, candidate: Path) -> Path:
    """Resolve a candidate and reject any path traversal outside ``root``."""

    resolved_root = Path(root).resolve()
    resolved_candidate = Path(candidate).resolve()
    try:
        resolved_candidate.relative_to(resolved_root)
    except ValueError:
        raise ValueError("Generated archive path escaped its course root.") from None
    return resolved_candidate


__all__ = [
    "canvas_folder_parts",
    "conflict_filename",
    "course_backup_path",
    "course_folder_name",
    "ensure_within",
    "sanitize_component",
    "sanitize_filename",
    "term_folder_name",
]
