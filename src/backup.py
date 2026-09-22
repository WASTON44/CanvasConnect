"""Create privacy-conscious, incremental local archives of Canvas courses."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from .canvas_client import CanvasClient
from .content_links import extract_html_links, module_external_links
from .course_discovery import write_json_atomic
from .downloader import FileDownloader, file_is_unchanged
from .exceptions import CanvasError
from .indexing import create_content_index, generate_global_indexes, write_content_indexes
from .inventory import CourseInventory, inspect_course
from .manifest import (
    add_error,
    add_warning,
    create_manifest,
    finalise_manifest,
    save_manifest,
    set_category,
)
from .normalise import (
    canvas_folder_parts,
    conflict_filename,
    course_backup_path,
    ensure_within,
    sanitize_component,
    sanitize_filename,
)
from .privacy import (
    safe_course_metadata,
    safe_file_metadata,
    sanitize_assignment,
    sanitize_discussion,
    sanitize_metadata,
    sanitize_page,
    sanitize_rubric,
)


LOGGER = logging.getLogger(__name__)


@dataclass
class BackupResult:
    course_id: Any
    course_name: str
    status: str
    counts: dict[str, int]
    warnings: list[str]
    errors: list[str]
    estimated_download_bytes: int | None
    downloaded_bytes: int = 0
    course_root: Path | None = None


def dry_run_course(client: CanvasClient, course: dict[str, Any]) -> BackupResult:
    """Inspect a course and return counts without creating an archive."""

    inventory = inspect_course(client, course)
    return BackupResult(
        course_id=course.get("id"),
        course_name=str(course.get("name") or "Unnamed course"),
        status="dry_run",
        counts=inventory.counts,
        warnings=list(inventory.warnings),
        errors=[],
        estimated_download_bytes=inventory.estimated_download_bytes,
    )


class CourseBackup:
    """Orchestrate one full, checkpointed course backup."""

    def __init__(self, client: CanvasClient, backup_root: Path) -> None:
        self.client = client
        self.backup_root = Path(backup_root)

    def run(
        self,
        selected_course: dict[str, Any],
        *,
        resume: bool = False,
        inventory: CourseInventory | None = None,
    ) -> BackupResult:
        course_id = selected_course.get("id")
        inventory = inventory or inspect_course(self.client, selected_course)
        course_for_path = {**selected_course, **inventory.course}
        if not isinstance(course_for_path.get("term"), dict):
            course_for_path["term"] = selected_course.get("term")
        course_root = course_backup_path(self.backup_root, course_for_path)
        course_root.mkdir(parents=True, exist_ok=True)

        manifest = create_manifest(
            course_for_path,
            canvas_base_url=self.client.base_url,
            course_root=course_root,
            resume=resume,
        )
        for message in inventory.warnings:
            add_warning(manifest, message)
        for endpoint in inventory.inaccessible_endpoints:
            if endpoint not in manifest["inaccessible_endpoints"]:
                manifest["inaccessible_endpoints"].append(endpoint)
        for category, status in inventory.category_status.items():
            if category in manifest["categories"]:
                set_category(manifest, category, status=status, count=0)
        manifest["estimated_download_bytes"] = inventory.estimated_download_bytes
        manifest["counts"].update(inventory.counts)
        save_manifest(course_root, manifest)

        LOGGER.info("Backing up Canvas course %s to %s.", course_id, course_root)
        references: dict[str, dict[str, str]] = {}
        external_links = module_external_links(
            inventory.modules, canvas_base_url=self.client.base_url
        )

        self._write_course(course_root, course_for_path, manifest)
        self._write_modules(course_root, inventory, manifest)
        page_references, page_links = self._write_pages(
            course_root, inventory, manifest
        )
        references["pages"] = page_references
        external_links.extend(page_links)
        references["files"] = self._write_files(course_root, inventory, manifest)
        references["assignments"] = self._write_assignments(
            course_root, inventory, manifest
        )
        self._write_simple_category(
            course_root,
            "assignment_groups",
            "assignment_groups.json",
            inventory.assignment_groups,
            sanitize_assignment,
            manifest,
            inventory,
        )
        self._write_simple_category(
            course_root,
            "rubrics",
            "rubrics.json",
            inventory.rubrics,
            sanitize_rubric,
            manifest,
            inventory,
        )
        references["quizzes"] = self._write_quizzes(
            course_root, inventory, manifest
        )
        references["discussions"] = self._write_html_category(
            course_root,
            "discussions",
            inventory.discussions,
            manifest,
            inventory,
            body_field="message",
            sanitizer=sanitize_discussion,
        )
        self._write_html_category(
            course_root,
            "announcements",
            inventory.announcements,
            manifest,
            inventory,
            body_field="message",
            sanitizer=sanitize_discussion,
        )

        external_links = _deduplicate_links(external_links)
        write_json_atomic(
            course_root / "indexes" / "external_links.json",
            {"schema_version": 1, "links": external_links},
        )
        set_category(
            manifest,
            "external_links",
            status="complete",
            count=len(external_links),
        )
        manifest["counts"]["external_links"] = len(external_links)

        index = create_content_index(
            safe_course_metadata(course_for_path, self.client.base_url),
            sanitize_metadata(inventory.modules),
            references,
            external_links,
        )
        write_content_indexes(course_root, index)
        finalise_manifest(manifest)
        save_manifest(course_root, manifest)
        generate_global_indexes(self.backup_root)
        LOGGER.info(
            "Course %s backup finished with status %s.",
            course_id,
            manifest["backup_status"],
        )
        return BackupResult(
            course_id=course_id,
            course_name=str(course_for_path.get("name") or "Unnamed course"),
            status=str(manifest["backup_status"]),
            counts=dict(manifest["counts"]),
            warnings=list(manifest["warnings"]),
            errors=list(manifest["errors"]),
            estimated_download_bytes=manifest["estimated_download_bytes"],
            downloaded_bytes=int(manifest["downloaded_bytes"]),
            course_root=course_root,
        )

    def _write_course(
        self,
        course_root: Path,
        course: dict[str, Any],
        manifest: dict[str, Any],
    ) -> None:
        safe_course = safe_course_metadata(course, self.client.base_url)
        syllabus = safe_course.pop("syllabus_body", None)
        write_json_atomic(course_root / "course.json", safe_course)
        course_status = "complete"
        if manifest.get("categories", {}).get("course", {}).get("status") == "inaccessible":
            course_status = "partial"
        set_category(manifest, "course", status=course_status, count=1)
        if syllabus:
            _write_text_atomic(course_root / "syllabus" / "syllabus.html", str(syllabus))
            set_category(manifest, "syllabus", status="complete", count=1)
        else:
            set_category(manifest, "syllabus", status="complete", count=0)
        save_manifest(course_root, manifest)

    def _write_modules(
        self,
        course_root: Path,
        inventory: CourseInventory,
        manifest: dict[str, Any],
    ) -> None:
        write_json_atomic(
            course_root / "modules" / "modules.json",
            {"schema_version": 1, "modules": sanitize_metadata(inventory.modules)},
        )
        status = inventory.category_status.get("modules", "complete")
        set_category(
            manifest,
            "modules",
            status=status,
            count=len(inventory.modules),
        )
        save_manifest(course_root, manifest)

    def _write_pages(
        self,
        course_root: Path,
        inventory: CourseInventory,
        manifest: dict[str, Any],
    ) -> tuple[dict[str, str], list[dict[str, Any]]]:
        directory = course_root / "pages"
        records: list[dict[str, Any]] = []
        references: dict[str, str] = {}
        links: list[dict[str, Any]] = []
        failures = 0
        for summary in inventory.pages:
            page_reference = summary.get("url", summary.get("page_id", summary.get("id")))
            try:
                page = self.client.get_page(inventory.course.get("id"), page_reference)
                safe_page = sanitize_page(page)
                body = safe_page.pop("body", None)
                stem = _content_stem(
                    page.get("url") or page.get("title"),
                    page.get("page_id", page.get("id")),
                    "page",
                )
                html_relative = _posix(Path("pages") / f"{stem}.html")
                json_relative = _posix(Path("pages") / f"{stem}.json")
                safe_page["local_html_path"] = html_relative
                write_json_atomic(course_root / json_relative, safe_page)
                _write_text_atomic(course_root / html_relative, str(body or ""))
                records.append(safe_page)
                for identifier in (
                    page.get("page_id"),
                    page.get("id"),
                    page.get("url"),
                    page_reference,
                ):
                    if identifier is not None:
                        references[str(identifier)] = html_relative
                links.extend(
                    extract_html_links(
                        str(body or ""),
                        canvas_base_url=self.client.base_url,
                        source=html_relative,
                        title=str(page.get("title") or "Canvas page"),
                    )
                )
            except CanvasError as error:
                failures += 1
                message = f"Page {page_reference!r} could not be backed up: {error}"
                add_error(manifest, message)
                LOGGER.warning(message)
        write_json_atomic(
            directory / "pages.json", {"schema_version": 1, "pages": records}
        )
        status = _content_status(inventory, "pages", failures)
        set_category(manifest, "pages", status=status, count=len(records))
        save_manifest(course_root, manifest)
        return references, links

    def _write_files(
        self,
        course_root: Path,
        inventory: CourseInventory,
        manifest: dict[str, Any],
    ) -> dict[str, str]:
        files_root = course_root / "files"
        metadata_path = files_root / "files.json"
        previous = _load_previous_file_records(metadata_path)
        previous_by_id = {
            str(record.get("id")): record
            for record in previous
            if record.get("id") is not None
        }
        folder_paths = {
            str(folder.get("id")): canvas_folder_parts(folder.get("full_name"))
            for folder in inventory.folders
            if folder.get("id") is not None
        }
        write_json_atomic(
            files_root / "folders.json",
            {
                "schema_version": 1,
                "folders": sanitize_metadata(inventory.folders),
            },
        )
        set_category(
            manifest,
            "folders",
            status=inventory.category_status.get("folders", "complete"),
            count=len(inventory.folders),
        )

        records: list[dict[str, Any]] = []
        references: dict[str, str] = {}
        used: dict[str, str] = {}
        # Reserve paths belonging to old records too. A newly created Canvas
        # file must never overwrite a conservatively retained local file.
        for old_record in previous:
            old_relative = old_record.get("local_path")
            old_id = old_record.get("id")
            if not isinstance(old_relative, str) or old_id is None:
                continue
            try:
                old_path = ensure_within(files_root, course_root / old_relative)
            except ValueError:
                continue
            used[str(old_path).casefold()] = str(old_id)
        seen_ids: set[str] = set()
        failures = 0
        with FileDownloader(self.client) as downloader:
            for file_data in inventory.files:
                file_id = str(file_data.get("id"))
                seen_ids.add(file_id)
                previous_record = previous_by_id.get(file_id)
                relative = self._file_relative_path(
                    files_root,
                    course_root,
                    file_data,
                    folder_paths,
                    previous_record,
                    used,
                )
                destination = ensure_within(course_root, course_root / relative)
                metadata = safe_file_metadata(file_data)
                metadata["original_filename"] = file_data.get("display_name") or file_data.get(
                    "filename"
                )
                metadata["local_path"] = relative
                metadata["present_in_latest_canvas_inventory"] = True
                try:
                    if file_is_unchanged(previous_record, file_data, destination):
                        metadata["sha256"] = previous_record.get("sha256")
                        metadata["backup_state"] = "unchanged"
                        manifest["counts"]["files_unchanged"] += 1
                        LOGGER.info("Canvas file %s is unchanged; skipping.", file_id)
                    else:
                        url = file_data.get("url")
                        if not isinstance(url, str) or not url.strip():
                            raise CanvasError("Canvas did not provide a file download URL.")
                        expected_size = _non_negative_integer(file_data.get("size"))
                        result = downloader.download(
                            url,
                            destination,
                            expected_size=expected_size,
                        )
                        metadata["sha256"] = result.sha256
                        metadata["downloaded_size"] = result.size
                        metadata["backup_state"] = "downloaded"
                        manifest["counts"]["files_downloaded"] += 1
                        manifest["downloaded_bytes"] += result.size
                    references[file_id] = relative
                    manifest["checksums"].append(
                        {
                            "canvas_file_id": file_data.get("id"),
                            "local_path": relative,
                            "sha256": metadata.get("sha256"),
                        }
                    )
                except CanvasError as error:
                    failures += 1
                    metadata["backup_state"] = "failed"
                    metadata["error"] = str(error)
                    message = f"Canvas file {file_id} could not be downloaded: {error}"
                    add_error(manifest, message)
                    LOGGER.warning(message)
                records.append(metadata)
                _save_file_records(metadata_path, records)
                save_manifest(course_root, manifest)

        for old_record in previous:
            old_id = str(old_record.get("id"))
            if old_id in seen_ids:
                continue
            retained = dict(old_record)
            inventory_inaccessible = (
                inventory.category_status.get("files") == "inaccessible"
            )
            retained["present_in_latest_canvas_inventory"] = (
                None if inventory_inaccessible else False
            )
            retained["backup_state"] = (
                "retained_inventory_inaccessible"
                if inventory_inaccessible
                else "retained_not_present_in_canvas"
            )
            records.append(retained)
            if inventory_inaccessible:
                message = (
                    f"Retained local file {old_id}; the latest Canvas file inventory "
                    "was inaccessible."
                )
            else:
                message = (
                    f"Retained local file {old_id}; it was not present in the latest "
                    "Canvas inventory."
                )
            manifest["skipped_content"].append(message)
        _save_file_records(metadata_path, records)
        status = _content_status(inventory, "files", failures)
        set_category(manifest, "files", status=status, count=len(inventory.files))
        save_manifest(course_root, manifest)
        return references

    def _file_relative_path(
        self,
        files_root: Path,
        course_root: Path,
        file_data: dict[str, Any],
        folder_paths: dict[str, list[str]],
        previous: dict[str, Any] | None,
        used: dict[str, str],
    ) -> str:
        file_id = str(file_data.get("id"))
        if previous and isinstance(previous.get("local_path"), str):
            candidate = course_root / previous["local_path"]
            try:
                ensure_within(files_root, candidate)
                key = str(candidate.resolve()).casefold()
                if key not in used or used[key] == file_id:
                    used[key] = file_id
                    return _posix(Path(previous["local_path"]))
            except ValueError:
                pass

        parts = folder_paths.get(str(file_data.get("folder_id")), [])
        name = sanitize_filename(
            file_data.get("display_name") or file_data.get("filename"),
            fallback=f"canvas_file_{file_id}",
        )
        relative_path = Path("files", *parts, name)
        key = str((course_root / relative_path).resolve()).casefold()
        if key in used and used[key] != file_id:
            relative_path = relative_path.with_name(conflict_filename(name, file_id))
            key = str((course_root / relative_path).resolve()).casefold()
        used[key] = file_id
        return _posix(relative_path)

    def _write_assignments(
        self,
        course_root: Path,
        inventory: CourseInventory,
        manifest: dict[str, Any],
    ) -> dict[str, str]:
        references: dict[str, str] = {}
        records = []
        for assignment in inventory.assignments:
            safe = sanitize_assignment(assignment)
            description = safe.pop("description", None)
            stem = _content_stem(
                assignment.get("name"), assignment.get("id"), "assignment"
            )
            json_relative = _posix(Path("assignments") / f"{stem}.json")
            html_relative = _posix(Path("assignments") / f"{stem}.html")
            safe["local_description_path"] = html_relative
            write_json_atomic(course_root / json_relative, safe)
            _write_text_atomic(course_root / html_relative, str(description or ""))
            records.append(safe)
            if assignment.get("id") is not None:
                references[str(assignment["id"])] = html_relative
        write_json_atomic(
            course_root / "assignments" / "assignments.json",
            {"schema_version": 1, "assignments": records},
        )
        set_category(
            manifest,
            "assignments",
            status=inventory.category_status.get("assignments", "complete"),
            count=len(records),
        )
        save_manifest(course_root, manifest)
        return references

    def _write_quizzes(
        self,
        course_root: Path,
        inventory: CourseInventory,
        manifest: dict[str, Any],
    ) -> dict[str, str]:
        references: dict[str, str] = {}
        classic_records = []
        classic_failures = 0
        for quiz in inventory.classic_quizzes:
            safe = sanitize_metadata(quiz)
            try:
                safe["questions"] = sanitize_metadata(
                    self.client.get_classic_quiz_questions(
                        inventory.course.get("id"), quiz.get("id")
                    )
                )
            except CanvasError as error:
                classic_failures += 1
                safe["questions"] = []
                add_warning(
                    manifest,
                    f"Classic quiz {quiz.get('id')} questions were inaccessible: {error}",
                )
            stem = _content_stem(quiz.get("title"), quiz.get("id"), "classic_quiz")
            relative = _posix(Path("quizzes", "classic") / f"{stem}.json")
            write_json_atomic(course_root / relative, safe)
            classic_records.append(safe)
            if quiz.get("id") is not None:
                references[str(quiz["id"])] = relative
        write_json_atomic(
            course_root / "quizzes" / "classic" / "quizzes.json",
            {"schema_version": 1, "quizzes": classic_records},
        )
        set_category(
            manifest,
            "classic_quizzes",
            status=_content_status(inventory, "classic_quizzes", classic_failures),
            count=len(classic_records),
        )

        new_records = []
        new_failures = 0
        for quiz in inventory.new_quizzes:
            safe = sanitize_metadata(quiz)
            assignment_id = quiz.get("assignment_id", quiz.get("id"))
            try:
                safe["items"] = sanitize_metadata(
                    self.client.get_new_quiz_items(
                        inventory.course.get("id"), assignment_id
                    )
                )
            except CanvasError as error:
                new_failures += 1
                safe["items"] = []
                add_warning(
                    manifest,
                    f"New Quiz {assignment_id} items were inaccessible: {error}",
                )
            stem = _content_stem(
                quiz.get("title"), assignment_id, "new_quiz"
            )
            relative = _posix(Path("quizzes", "new") / f"{stem}.json")
            write_json_atomic(course_root / relative, safe)
            new_records.append(safe)
            for identifier in (quiz.get("id"), assignment_id):
                if identifier is not None:
                    references[str(identifier)] = relative
        write_json_atomic(
            course_root / "quizzes" / "new" / "quizzes.json",
            {"schema_version": 1, "quizzes": new_records},
        )
        set_category(
            manifest,
            "new_quizzes",
            status=_content_status(inventory, "new_quizzes", new_failures),
            count=len(new_records),
        )
        save_manifest(course_root, manifest)
        return references

    def _write_simple_category(
        self,
        course_root: Path,
        category: str,
        filename: str,
        values: list[dict[str, Any]],
        sanitizer: Callable[[Any], Any],
        manifest: dict[str, Any],
        inventory: CourseInventory,
    ) -> None:
        write_json_atomic(
            course_root / category / filename,
            {"schema_version": 1, category: sanitizer(values)},
        )
        set_category(
            manifest,
            category,
            status=inventory.category_status.get(category, "complete"),
            count=len(values),
        )
        save_manifest(course_root, manifest)

    def _write_html_category(
        self,
        course_root: Path,
        category: str,
        values: list[dict[str, Any]],
        manifest: dict[str, Any],
        inventory: CourseInventory,
        *,
        body_field: str,
        sanitizer: Callable[[Any], Any],
    ) -> dict[str, str]:
        records = []
        references: dict[str, str] = {}
        for value in values:
            safe = sanitizer(value)
            body = safe.pop(body_field, None)
            stem = _content_stem(
                value.get("title"), value.get("id"), category.rstrip("s")
            )
            json_relative = _posix(Path(category) / f"{stem}.json")
            html_relative = _posix(Path(category) / f"{stem}.html")
            safe[f"local_{body_field}_path"] = html_relative
            write_json_atomic(course_root / json_relative, safe)
            _write_text_atomic(course_root / html_relative, str(body or ""))
            records.append(safe)
            if value.get("id") is not None:
                references[str(value["id"])] = html_relative
        write_json_atomic(
            course_root / category / f"{category}.json",
            {"schema_version": 1, category: records},
        )
        set_category(
            manifest,
            category,
            status=inventory.category_status.get(category, "complete"),
            count=len(records),
        )
        save_manifest(course_root, manifest)
        return references


def _save_file_records(path: Path, records: list[dict[str, Any]]) -> None:
    write_json_atomic(path, {"schema_version": 1, "files": records})


def _load_previous_file_records(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return []
    values = payload.get("files") if isinstance(payload, dict) else None
    return [value for value in values or [] if isinstance(value, dict)]


def _content_stem(title: Any, canvas_id: Any, fallback: str) -> str:
    safe_title = sanitize_component(title, fallback=fallback, max_length=80)
    safe_id = sanitize_component(canvas_id, fallback="unknown", max_length=36)
    return sanitize_filename(f"{safe_title}__canvas_{safe_id}", max_length=120)


def _content_status(
    inventory: CourseInventory,
    category: str,
    failures: int,
) -> str:
    original = inventory.category_status.get(category, "complete")
    if original == "inaccessible":
        return "inaccessible"
    if original == "partial" or failures:
        return "partial"
    return "complete"


def _deduplicate_links(links: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results = []
    seen: set[tuple[str, str, str]] = set()
    for link in links:
        key = (
            str(link.get("url")),
            str(link.get("source")),
            str(link.get("module_item_id")),
        )
        if key not in seen:
            seen.add(key)
            results.append(sanitize_metadata(link))
    return results


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _posix(path: Path) -> str:
    return PurePosixPath(*path.parts).as_posix()


def _non_negative_integer(value: Any) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


__all__ = ["BackupResult", "CourseBackup", "dry_run_course"]
