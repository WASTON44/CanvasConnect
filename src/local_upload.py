"""Explicitly approved deployment of local Active course copies to Canvas.

This module is intentionally separate from :mod:`src.canvas_client`.  The
archive client stays permanently read-only; this client is used only by the
guarded deployment command after a user has approved an exact upload plan.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping
from urllib.parse import unquote_plus, urljoin, urlsplit

import requests
from requests.utils import parse_header_links

from .exceptions import CanvasError


class UploadError(CanvasError):
    """Raised when Canvas cannot safely complete a local deployment."""


ASSESSMENT_ITEM_TYPES = frozenset({"Assignment", "Quiz"})
SUPPORTED_MODULE_ITEM_TYPES = frozenset(
    {"File", "Page", "Discussion", "Assignment", "Quiz", "SubHeader", "ExternalUrl"}
)
SOURCE_FILE_URL = re.compile(
    r"(?:https?://[^\"'<>\s]+)?/api/v1/courses/(?P<course>\d+)/files/(?P<file>\d+)"
)
SOURCE_FILE_PREVIEW_URL = re.compile(
    r"(?<!/api/v1)(?:https?://[^\"'<>\s]+)?/courses/(?P<course>\d+)/files/"
    r"(?P<file>\d+)(?:/(?:preview|download))?(?:\?[^\"'<>\s]*)?"
)
SOURCE_PAGE_URL = re.compile(
    r"(?:https?://[^\"'<>\s]+)?/courses/(?P<course>\d+)/pages/(?P<page>[A-Za-z0-9._~-]+)"
)
SOURCE_ASSIGNMENT_URL = re.compile(
    r"(?:https?://[^\"'<>\s]+)?/courses/(?P<course>\d+)/assignments/(?P<assignment>\d+)"
)
SOURCE_QUIZ_URL = re.compile(
    r"(?:https?://[^\"'<>\s]+)?/courses/(?P<course>\d+)/quizzes/(?P<quiz>\d+)"
)
SOURCE_DISCUSSION_URL = re.compile(
    r"(?:https?://[^\"'<>\s]+)?/courses/(?P<course>\d+)/discussion_topics/(?P<topic>\d+)"
)
SIGNED_MEDIA_URL = re.compile(r"https?://[^\"'<>\s]+/files/[^\"'<>\s]+\?token=[^\"'<>\s]+")
HTML_IMAGE_TAG = re.compile(r"<img\b[^>]*>", re.IGNORECASE)


@dataclass(frozen=True)
class ActiveCourse:
    """A local working copy and the target Canvas course encoded in its name."""

    root: Path
    target_course_id: int
    source_course_id: int
    course_code: str
    course_name: str

    @classmethod
    def load(cls, root: Path) -> "ActiveCourse":
        match = re.search(r"__(\d+)$", root.name)
        if not match:
            raise UploadError(
                f"Active folder {root.name!r} does not end with a target Canvas ID."
            )
        course = _load_json(root / "course.json")
        source_id = course.get("id")
        course_code = course.get("course_code")
        if not isinstance(source_id, int) or not isinstance(course_code, str):
            raise UploadError(f"{root / 'course.json'} does not identify a source course.")
        return cls(
            root=root,
            target_course_id=int(match.group(1)),
            source_course_id=source_id,
            course_code=course_code,
            course_name=str(course.get("name") or root.name),
        )

    def records(self, relative_path: str, key: str) -> list[dict[str, Any]]:
        payload = _load_json(self.root / relative_path)
        values = payload.get(key)
        if not isinstance(values, list) or not all(isinstance(value, dict) for value in values):
            raise UploadError(f"{self.root / relative_path} has no valid {key!r} records.")
        return values

    def optional_records(self, relative_path: str, key: str) -> list[dict[str, Any]]:
        path = self.root / relative_path
        if not path.exists():
            return []
        return self.records(relative_path, key)

    def text_from_record(self, record: Mapping[str, Any], field: str) -> str:
        relative = record.get(field)
        if not isinstance(relative, str) or not relative:
            return ""
        path = _within(self.root, self.root / relative)
        try:
            return path.read_text(encoding="utf-8")
        except OSError as error:
            raise UploadError(f"Unable to read archived content at {path}.") from error


class CanvasWriteClient:
    """Small, token-redacting Canvas write client for approved deployments only."""

    def __init__(self, base_url: str, api_token: str, *, timeout: int = 90) -> None:
        parsed = urlsplit(base_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise UploadError("Canvas uploads require a normal HTTPS Canvas URL.")
        self.base_url = base_url.rstrip("/")
        self.api_root = f"{self.base_url}/api/v1/"
        self.quiz_root = f"{self.base_url}/api/quiz/v1/"
        self._origin = (parsed.scheme, parsed.hostname, parsed.port or 443)
        self.timeout = timeout
        self._api = requests.Session()
        self._api.headers.update({"Authorization": f"Bearer {api_token}", "Accept": "application/json"})
        self._uploads = requests.Session()

    def __enter__(self) -> "CanvasWriteClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"{type(self).__name__}(base_url={self.base_url!r}, approved_writes_only=True)"

    def close(self) -> None:
        self._api.close()
        self._uploads.close()

    def get_json(self, endpoint: str, *, quiz_api: bool = False) -> dict[str, Any]:
        response = self._request("GET", endpoint, quiz_api=quiz_api)
        return self._json_mapping(response, endpoint)

    def get_list(self, endpoint: str, *, quiz_api: bool = False) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        url = self._api_url(endpoint, quiz_api=quiz_api)
        params: dict[str, Any] | None = {"per_page": 100}
        while url:
            response = self._api.request("GET", url, params=params, timeout=self.timeout)
            self._raise_for_status(response, endpoint)
            try:
                payload = response.json()
            except ValueError as error:
                raise UploadError(f"Canvas returned invalid JSON for {endpoint}.") from error
            if not isinstance(payload, list) or not all(isinstance(value, dict) for value in payload):
                raise UploadError(f"Canvas returned an unexpected list response for {endpoint}.")
            values.extend(payload)
            url = self._next_link(response)
            params = None
        return values

    def post_form(self, endpoint: str, data: Mapping[str, Any] | Iterable[tuple[str, Any]], *, quiz_api: bool = False) -> dict[str, Any]:
        response = self._request("POST", endpoint, data=data, quiz_api=quiz_api)
        return self._json_mapping(response, endpoint)

    def put_form(self, endpoint: str, data: Mapping[str, Any] | Iterable[tuple[str, Any]], *, quiz_api: bool = False) -> dict[str, Any]:
        response = self._request("PUT", endpoint, data=data, quiz_api=quiz_api)
        return self._json_mapping(response, endpoint)

    def delete(self, endpoint: str) -> None:
        """Delete an explicitly identified accidental target item."""
        self._request("DELETE", endpoint)

    def upload_file(self, course_id: int, folder_id: int, source: Path, content_type: str | None) -> dict[str, Any]:
        if not source.is_file():
            raise UploadError(f"Archived file is missing: {source}")
        request = self.post_form(
            f"courses/{course_id}/files",
            {
                "name": source.name,
                "size": source.stat().st_size,
                "content_type": content_type or "application/octet-stream",
                "parent_folder_id": folder_id,
                "on_duplicate": "rename",
            },
        )
        upload_url = request.get("upload_url")
        upload_params = request.get("upload_params")
        if not isinstance(upload_url, str) or not isinstance(upload_params, dict):
            raise UploadError(f"Canvas did not provide an upload destination for {source.name}.")
        with source.open("rb") as stream:
            response = self._uploads.post(
                upload_url,
                data=upload_params,
                files={"file": (source.name, stream, content_type or "application/octet-stream")},
                allow_redirects=False,
                timeout=(15, 600),
            )
        if 300 <= response.status_code < 400:
            location = response.headers.get("Location")
            if not location:
                raise UploadError(f"Canvas did not complete the upload redirect for {source.name}.")
            completed = self._api.get(self._trusted_canvas_url(location), timeout=self.timeout)
            self._raise_for_status(completed, "file upload completion")
            return self._json_mapping(completed, "file upload completion")
        self._raise_for_status(response, "file upload")
        try:
            payload = response.json()
        except ValueError as error:
            raise UploadError(f"Canvas did not confirm the upload of {source.name}.") from error
        if isinstance(payload, dict):
            return payload
        location = response.headers.get("Location")
        if location:
            completed = self._api.get(self._trusted_canvas_url(location), timeout=self.timeout)
            self._raise_for_status(completed, "file upload completion")
            return self._json_mapping(completed, "file upload completion")
        raise UploadError(f"Canvas did not return a file record for {source.name}.")

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        data: Mapping[str, Any] | Iterable[tuple[str, Any]] | None = None,
        quiz_api: bool = False,
    ) -> requests.Response:
        response = self._api.request(
            method,
            self._api_url(endpoint, quiz_api=quiz_api),
            data=data,
            timeout=self.timeout,
        )
        self._raise_for_status(response, endpoint)
        return response

    def _api_url(self, endpoint: str, *, quiz_api: bool = False) -> str:
        value = endpoint.strip().lstrip("/")
        if not value or "://" in value or ".." in value.split("/"):
            raise UploadError("An unsafe Canvas API endpoint was rejected.")
        return urljoin(self.quiz_root if quiz_api else self.api_root, value)

    def _trusted_canvas_url(self, location: str) -> str:
        url = urljoin(self.base_url + "/", location)
        parsed = urlsplit(url)
        origin = (parsed.scheme, parsed.hostname, parsed.port or 443)
        if origin != self._origin:
            raise UploadError("Canvas returned an untrusted file-upload completion URL.")
        return url

    @staticmethod
    def _json_mapping(response: requests.Response, endpoint: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as error:
            raise UploadError(f"Canvas returned invalid JSON for {endpoint}.") from error
        if not isinstance(payload, dict):
            raise UploadError(f"Canvas returned an unexpected response for {endpoint}.")
        return payload

    def _next_link(self, response: requests.Response) -> str | None:
        header = response.headers.get("Link", "")
        for link in parse_header_links(header.rstrip(">")):
            if link.get("rel") == "next" and link.get("url"):
                return self._trusted_canvas_url(str(link["url"]))
        return None

    @staticmethod
    def _raise_for_status(response: requests.Response, endpoint: str) -> None:
        if response.status_code >= 400:
            raise UploadError(f"Canvas returned HTTP {response.status_code} for {endpoint}.")


@dataclass
class CourseDeployment:
    """Creates an empty target course from one local Active archive."""

    client: CanvasWriteClient
    course: ActiveCourse
    resume: bool = False
    omit_missing_quiz_media: bool = False
    omit_empty_files: bool = False
    events: list[dict[str, Any]] = field(default_factory=list)
    source_to_target_groups: dict[int, int] = field(default_factory=dict)
    source_to_target_folders: dict[int, int] = field(default_factory=dict)
    source_to_target_files: dict[int, int] = field(default_factory=dict)
    source_to_target_pages: dict[str, str] = field(default_factory=dict)
    source_to_target_assignments: dict[int, int] = field(default_factory=dict)
    source_to_target_quizzes: dict[int, int] = field(default_factory=dict)
    source_to_target_discussions: dict[int, int] = field(default_factory=dict)
    source_to_target_modules: dict[int, int] = field(default_factory=dict)
    existing_module_items: set[tuple[int, str, str, int]] = field(default_factory=set)

    def run(self) -> dict[str, Any]:
        self._preflight()
        groups = self.course.records("assignment_groups/assignment_groups.json", "assignment_groups")
        group_ids = self._create_assignment_groups(groups)
        folder_ids = self._create_folders()
        files = self.course.records("files/files.json", "files")
        modules = self.course.records("modules/modules.json", "modules")
        first_file_ids = self._first_module_file_ids(modules)
        self._remove_empty_file_placeholders(files)
        self._upload_files(files, folder_ids, first_file_ids)
        pages = self.course.records("pages/pages.json", "pages")
        self._create_pages(pages)
        assignments = self.course.records("assignments/assignments.json", "assignments")
        self._create_assignments(assignments, group_ids)
        self._create_classic_quizzes(group_ids)
        self._create_new_quizzes(group_ids, files)
        discussions = self.course.records("discussions/discussions.json", "discussions")
        self._create_discussions(discussions)
        self._create_modules(modules)
        self._finalise_modules(modules)
        self._finalise_pages(pages, modules)
        self._finalise_assignments(assignments)
        self._finalise_classic_quizzes()
        self._finalise_discussions(discussions)
        self._record("announcements", "skipped", count=len(self.course.records("announcements/announcements.json", "announcements")))
        return {
            "target_course_id": self.course.target_course_id,
            "source_course_id": self.course.source_course_id,
            "course_code": self.course.course_code,
            "events": self.events,
        }

    def _preflight(self) -> None:
        target = self.client.get_json(f"courses/{self.course.target_course_id}")
        target_code = target.get("course_code")
        if target_code != self.course.course_code:
            raise UploadError(
                f"Target {self.course.target_course_id} has course code {target_code!r}, "
                f"not {self.course.course_code!r}."
            )
        if target.get("workflow_state") != "unpublished":
            raise UploadError(f"Target {self.course.target_course_id} is not unpublished; upload stopped.")
        existing = {
            "modules": self.client.get_list(f"courses/{self.course.target_course_id}/modules"),
            "pages": self.client.get_list(f"courses/{self.course.target_course_id}/pages"),
            "files": self.client.get_list(f"courses/{self.course.target_course_id}/files"),
            "assignments": self.client.get_list(f"courses/{self.course.target_course_id}/assignments"),
            "discussions": self.client.get_list(f"courses/{self.course.target_course_id}/discussion_topics"),
            "assignment_groups": self.client.get_list(
                f"courses/{self.course.target_course_id}/assignment_groups"
            ),
        }
        occupied = {name: len(values) for name, values in existing.items() if values}
        if occupied and not self.resume:
            raise UploadError(
                f"Target {self.course.target_course_id} is no longer empty ({occupied}); upload stopped."
            )
        if self.resume:
            self._hydrate_existing(existing)
        self._validate_local_media()
        self._record("preflight", "succeeded", target_unpublished=True, resumed=self.resume)

    def _hydrate_existing(self, existing: Mapping[str, list[dict[str, Any]]]) -> None:
        """Map a stopped deployment back to the same local source without guessing.

        Resume is deliberately strict: ambiguous names or extra target content stop
        the run instead of creating duplicates in a course that is no longer blank.
        """
        groups = self.course.records("assignment_groups/assignment_groups.json", "assignment_groups")
        self.source_to_target_groups = _map_by_name_and_position(
            groups, existing["assignment_groups"], "assignment group"
        )

        target_folders = self.client.get_list(f"courses/{self.course.target_course_id}/folders")
        source_folders = self.course.records("files/folders.json", "folders")
        self.source_to_target_folders = _map_folders(source_folders, target_folders)

        source_files = self.course.records("files/files.json", "files")
        self.source_to_target_files = _map_files(source_files, existing["files"], source_folders, target_folders)

        pages = self.course.records("pages/pages.json", "pages")
        self.source_to_target_pages = _map_pages(pages, existing["pages"])

        assignments = self.course.records("assignments/assignments.json", "assignments")
        standard_assignments = [
            value for value in assignments
            if not value.get("is_quiz_assignment") and not value.get("is_quiz_lti_assignment")
        ]
        self.source_to_target_assignments = _map_by_unique_name(
            standard_assignments, existing["assignments"], "assignment"
        )

        modules = self.course.records("modules/modules.json", "modules")
        self.source_to_target_modules = _map_by_unique_name(modules, existing["modules"], "module")
        target_by_module = {module_id: module for module_id, module in self.source_to_target_modules.items()}
        for source_module in modules:
            source_id = _int_id(source_module, "source module")
            target_module_id = target_by_module.get(source_id)
            if target_module_id is None:
                continue
            for item in self.client.get_list(
                f"courses/{self.course.target_course_id}/modules/{target_module_id}/items"
            ):
                self.existing_module_items.add(_module_item_key(source_id, item))

    def _create_assignment_groups(self, groups: list[dict[str, Any]]) -> dict[int, int]:
        result: dict[int, int] = dict(self.source_to_target_groups)
        for group in sorted(groups, key=lambda value: _position(value)):
            source_id = _int_id(group, "assignment group")
            if source_id in result:
                continue
            payload = {
                "assignment_group[name]": group.get("name") or "Assignments",
                "assignment_group[position]": _position(group),
                "assignment_group[group_weight]": group.get("group_weight") or 0,
            }
            created = self.client.post_form(
                f"courses/{self.course.target_course_id}/assignment_groups", payload
            )
            result[source_id] = _int_id(created, "created assignment group")
            self._record("assignment_group", "created", title=group.get("name"), id=result[source_id])
        return result

    def _create_folders(self) -> dict[int, int]:
        folders = self.course.records("files/folders.json", "folders")
        root = self.client.get_json(f"courses/{self.course.target_course_id}/folders/root")
        root_id = _int_id(root, "target root folder")
        mappings: dict[int, int] = dict(self.source_to_target_folders)
        pending = sorted(folders, key=lambda value: str(value.get("full_name", "")).count("/"))
        for folder in pending:
            source_id = _int_id(folder, "source folder")
            if source_id in mappings:
                continue
            parent_source = folder.get("parent_folder_id")
            if parent_source is None:
                mappings[source_id] = root_id
                continue
            parent_id = mappings.get(int(parent_source))
            if parent_id is None:
                raise UploadError(f"Folder {folder.get('name')!r} has an unresolved parent.")
            created = self.client.post_form(
                f"folders/{parent_id}/folders", {"name": folder.get("name") or "Untitled folder"}
            )
            mappings[source_id] = _int_id(created, "created folder")
            self._record("folder", "created", title=folder.get("full_name"), id=mappings[source_id])
        return mappings

    def _upload_files(
        self, files: list[dict[str, Any]], folder_ids: Mapping[int, int], first_file_ids: set[int]
    ) -> None:
        for file_record in sorted(files, key=lambda value: str(value.get("local_path", ""))):
            source_id = _int_id(file_record, "source file")
            local_path = file_record.get("local_path")
            if not isinstance(local_path, str):
                raise UploadError(f"File {source_id} lacks a safe local path.")
            source = _within(self.course.root, self.course.root / local_path)
            if source.stat().st_size == 0:
                if not self.omit_empty_files:
                    raise UploadError(f"Archived file has no content and cannot be uploaded: {source.name!r}.")
                self._record(
                    "file",
                    "omitted",
                    title=source.name,
                    reason="The archived file is zero bytes and Canvas rejects empty uploads.",
                )
                continue
            if source_id in self.source_to_target_files:
                continue
            folder_source = file_record.get("folder_id")
            folder_id = folder_ids.get(int(folder_source)) if folder_source is not None else None
            if folder_id is None:
                raise UploadError(f"File {source.name} has an unresolved folder.")
            created = self.client.upload_file(
                self.course.target_course_id,
                folder_id,
                source,
                _string_or_none(file_record.get("content-type")),
            )
            target_id = _int_id(created, "uploaded file")
            self.source_to_target_files[source_id] = target_id
            visible = source_id in first_file_ids
            self.client.put_form(
                f"files/{target_id}", {"hidden": _canvas_bool(not visible), "locked": _canvas_bool(not visible)}
            )
            self._record("file", "created", title=source.name, id=target_id, visible=visible)

    def _remove_empty_file_placeholders(self, files: list[dict[str, Any]]) -> None:
        """Remove only a prior failed zero-byte upload when this recovery is approved."""
        if not self.omit_empty_files:
            return
        for file_record in files:
            source_id = _int_id(file_record, "source file")
            local_path = file_record.get("local_path")
            if not isinstance(local_path, str):
                continue
            source = _within(self.course.root, self.course.root / local_path)
            if source.stat().st_size != 0:
                continue
            target_id = self.source_to_target_files.pop(source_id, None)
            if target_id is None:
                continue
            self.client.delete(f"files/{target_id}")
            self._record(
                "file",
                "removed",
                title=source.name,
                id=target_id,
                reason="Removed failed zero-byte upload placeholder before the approved resume.",
            )

    def _create_pages(self, pages: list[dict[str, Any]]) -> None:
        for page in pages:
            source_url = page.get("url")
            if not isinstance(source_url, str):
                raise UploadError("An archived page does not have a Canvas page URL.")
            if source_url in self.source_to_target_pages:
                continue
            body = self._rewrite_content(self.course.text_from_record(page, "local_html_path"))
            created = self.client.post_form(
                f"courses/{self.course.target_course_id}/pages",
                {
                    "wiki_page[title]": page.get("title") or source_url,
                    "wiki_page[body]": body,
                    "wiki_page[published]": "false",
                    "wiki_page[hide_from_students]": "true",
                },
            )
            target_url = created.get("url")
            if not isinstance(target_url, str):
                raise UploadError("Canvas did not return the URL of a created page.")
            self.source_to_target_pages[source_url] = target_url
            self._record("page", "created", title=page.get("title"), url=target_url, visible=False)

    def _create_assignments(self, assignments: list[dict[str, Any]], group_ids: Mapping[int, int]) -> None:
        for assignment in assignments:
            if assignment.get("is_quiz_assignment") or assignment.get("is_quiz_lti_assignment"):
                continue
            source_id = _int_id(assignment, "source assignment")
            if source_id in self.source_to_target_assignments:
                continue
            group_id = _mapped_int(group_ids, assignment.get("assignment_group_id"), "assignment group")
            payload: list[tuple[str, Any]] = [
                ("assignment[name]", assignment.get("name") or "Untitled assignment"),
                ("assignment[assignment_group_id]", group_id),
                ("assignment[description]", self._rewrite_content(self.course.text_from_record(assignment, "local_description_path"))),
                ("assignment[points_possible]", assignment.get("points_possible") or 0),
                ("assignment[grading_type]", assignment.get("grading_type") or "points"),
                ("assignment[allowed_attempts]", assignment.get("allowed_attempts") or -1),
                ("assignment[published]", "false"),
            ]
            for submission_type in assignment.get("submission_types") or []:
                payload.append(("assignment[submission_types][]", submission_type))
            external_tool = assignment.get("external_tool_tag_attributes")
            if isinstance(external_tool, Mapping) and isinstance(external_tool.get("url"), str):
                payload.append(("assignment[external_tool_tag_attributes][url]", external_tool["url"]))
                payload.append(
                    (
                        "assignment[external_tool_tag_attributes][new_tab]",
                        _canvas_value(external_tool.get("new_tab", False)),
                    )
                )
            created = self.client.post_form(
                f"courses/{self.course.target_course_id}/assignments", payload
            )
            target_id = _int_id(created, "created assignment")
            self.source_to_target_assignments[source_id] = target_id
            self._record("assignment", "created", title=assignment.get("name"), id=target_id, visible=False)

    def _create_classic_quizzes(self, group_ids: Mapping[int, int]) -> None:
        quizzes = self.course.optional_records("quizzes/classic/quizzes.json", "quizzes")
        for quiz in quizzes:
            source_quiz_id = _int_id(quiz, "source classic quiz")
            if source_quiz_id in self.source_to_target_quizzes:
                continue
            payload: list[tuple[str, Any]] = [
                ("quiz[title]", quiz.get("title") or "Untitled quiz"),
                ("quiz[description]", self._rewrite_content(str(quiz.get("description") or ""))),
                ("quiz[quiz_type]", quiz.get("quiz_type") or "assignment"),
                ("quiz[points_possible]", quiz.get("points_possible") or 0),
                ("quiz[published]", "false"),
            ]
            group = quiz.get("assignment_group_id")
            if group is not None:
                payload.append(("quiz[assignment_group_id]", _mapped_int(group_ids, group, "quiz group")))
            for key in (
                "time_limit", "shuffle_answers", "hide_results", "show_correct_answers",
                "show_correct_answers_last_attempt", "allowed_attempts", "scoring_policy",
                "one_question_at_a_time", "cant_go_back", "one_time_results",
            ):
                if quiz.get(key) is not None:
                    payload.append((f"quiz[{key}]", _canvas_value(quiz[key])))
            created = self.client.post_form(f"courses/{self.course.target_course_id}/quizzes", payload)
            target_quiz_id = _int_id(created, "created classic quiz")
            self.source_to_target_quizzes[source_quiz_id] = target_quiz_id
            source_assignment_id = quiz.get("assignment_id")
            target_assignment_id = created.get("assignment_id")
            if source_assignment_id is not None and isinstance(target_assignment_id, int):
                self.source_to_target_assignments[int(source_assignment_id)] = target_assignment_id
            for question in quiz.get("questions") or []:
                self.client.post_form(
                    f"courses/{self.course.target_course_id}/quizzes/{target_quiz_id}/questions",
                    _classic_question_form(question, self._rewrite_content),
                )
            self._record("classic_quiz", "created", title=quiz.get("title"), id=target_quiz_id, visible=False)

    def _create_new_quizzes(self, group_ids: Mapping[int, int], files: list[dict[str, Any]]) -> None:
        media_by_name = _media_by_name(files)
        quizzes = self.course.optional_records("quizzes/new/quizzes.json", "quizzes")
        for quiz in quizzes:
            source_id = _int_id(quiz, "source new quiz")
            if source_id in self.source_to_target_quizzes:
                continue
            payload = _new_quiz_form(quiz, group_ids)
            created = self.client.post_form(
                f"courses/{self.course.target_course_id}/quizzes", payload, quiz_api=True
            )
            target_id = _int_id(created, "created new quiz")
            self.source_to_target_quizzes[source_id] = target_id
            self.source_to_target_assignments[source_id] = target_id
            for item in quiz.get("items") or []:
                clean_item = _new_quiz_item(
                    item,
                    self._rewrite_content,
                    media_by_name,
                    self.source_to_target_files,
                    self.client.base_url,
                    self.course.target_course_id,
                    omit_missing_image_tags=self.omit_missing_quiz_media,
                )
                self.client.post_form(
                    f"courses/{self.course.target_course_id}/quizzes/{target_id}/items",
                    _flatten_form("item", clean_item),
                    quiz_api=True,
                )
            self._record("new_quiz", "created", title=quiz.get("title"), id=target_id, visible=False)

    def _create_discussions(self, discussions: list[dict[str, Any]]) -> None:
        for discussion in discussions:
            if discussion.get("is_announcement"):
                continue
            source_id = _int_id(discussion, "source discussion")
            if source_id in self.source_to_target_discussions:
                continue
            created = self.client.post_form(
                f"courses/{self.course.target_course_id}/discussion_topics",
                {
                    "title": discussion.get("title") or "Untitled discussion",
                    "message": self._rewrite_content(self.course.text_from_record(discussion, "local_message_path")),
                    "discussion_type": discussion.get("discussion_type") or "threaded",
                    "published": "false",
                    "is_announcement": "false",
                    "pinned": _canvas_value(discussion.get("pinned", False)),
                    "require_initial_post": _canvas_value(discussion.get("require_initial_post", False)),
                },
            )
            target_id = _int_id(created, "created discussion")
            self.source_to_target_discussions[source_id] = target_id
            self._record("discussion", "created", title=discussion.get("title"), id=target_id, visible=False)

    def _create_modules(self, modules: list[dict[str, Any]]) -> None:
        module_ids: dict[int, int] = dict(self.source_to_target_modules)
        ordered = sorted(modules, key=_position)
        for module in ordered:
            source_id = _int_id(module, "source module")
            if source_id in module_ids:
                continue
            created = self.client.post_form(
                f"courses/{self.course.target_course_id}/modules",
                {
                    "module[name]": module.get("name") or "Untitled module",
                    "module[position]": _position(module),
                    "module[require_sequential_progress]": _canvas_value(module.get("require_sequential_progress", False)),
                    "module[published]": _canvas_bool(_position(module) == 1),
                },
            )
            module_ids[source_id] = _int_id(created, "created module")
            self._record("module", "created", title=module.get("name"), id=module_ids[source_id], visible=False)
        self.source_to_target_modules = module_ids
        for module in ordered:
            target_module_id = module_ids[_int_id(module, "source module")]
            is_first = _position(module) == 1
            for item in sorted(module.get("items") or [], key=_position):
                item_type = item.get("type")
                if item_type not in SUPPORTED_MODULE_ITEM_TYPES:
                    raise UploadError(f"Unsupported module item type {item_type!r} in {module.get('name')!r}.")
                if _module_item_key(_int_id(module, "source module"), item) in self.existing_module_items:
                    continue
                if (
                    item_type == "Discussion"
                    and _optional_mapped_int(self.source_to_target_discussions, item.get("content_id")) is None
                ):
                    self._record(
                        "module_item",
                        "skipped",
                        title=item.get("title"),
                        reason="The local archive has no corresponding discussion body.",
                    )
                    continue
                created = self.client.post_form(
                    f"courses/{self.course.target_course_id}/modules/{target_module_id}/items",
                    self._module_item_form(item, visible=is_first and item_type not in ASSESSMENT_ITEM_TYPES),
                )
                target_item_id = _int_id(created, "created module item")
                visible = is_first and item_type not in ASSESSMENT_ITEM_TYPES
                self._record("module_item", "created", title=item.get("title"), id=target_item_id, visible=visible)

    def _module_item_form(self, item: Mapping[str, Any], *, visible: bool) -> list[tuple[str, Any]]:
        item_type = str(item.get("type"))
        payload: list[tuple[str, Any]] = [
            ("module_item[type]", item_type),
            ("module_item[title]", item.get("title") or item_type),
            ("module_item[position]", _position(item)),
            ("module_item[indent]", item.get("indent") or 0),
            ("module_item[published]", _canvas_bool(visible)),
        ]
        if item_type == "File":
            payload.append(("module_item[content_id]", _mapped_int(self.source_to_target_files, item.get("content_id"), "file")))
        elif item_type == "Page":
            source_url = item.get("page_url")
            if not isinstance(source_url, str) or source_url not in self.source_to_target_pages:
                raise UploadError(f"Module page {item.get('title')!r} has no created target page.")
            payload.append(("module_item[page_url]", self.source_to_target_pages[source_url]))
        elif item_type == "Discussion":
            payload.append(("module_item[content_id]", _mapped_int(self.source_to_target_discussions, item.get("content_id"), "discussion")))
        elif item_type == "Assignment":
            payload.append(("module_item[content_id]", _mapped_int(self.source_to_target_assignments, item.get("content_id"), "assignment")))
        elif item_type == "Quiz":
            payload.append(("module_item[content_id]", _mapped_int(self.source_to_target_quizzes, item.get("content_id"), "quiz")))
        elif item_type == "ExternalUrl":
            payload.extend(
                [
                    ("module_item[external_url]", item.get("external_url") or ""),
                    ("module_item[new_tab]", _canvas_value(item.get("new_tab", False))),
                ]
            )
        return payload

    def _finalise_modules(self, modules: list[dict[str, Any]]) -> None:
        """Apply the requested module visibility after its items exist.

        Canvas accepts the visibility field while a module is created on some
        installations but does not consistently persist it.  Updating the
        module itself (rather than its individual items) keeps the first
        section available and every later section unavailable without relying
        on that creation-time behaviour.
        """
        for module in sorted(modules, key=_position):
            source_id = _int_id(module, "source module")
            target_id = self.source_to_target_modules.get(source_id)
            if target_id is None:
                target_id = _mapped_int(self.source_to_target_modules, source_id, "module")
            self.client.put_form(
                f"courses/{self.course.target_course_id}/modules/{target_id}",
                {"module[published]": _canvas_bool(_position(module) == 1)},
            )

    def _finalise_pages(self, pages: list[dict[str, Any]], modules: list[dict[str, Any]]) -> None:
        first_page_urls = {
            str(item.get("page_url"))
            for module in modules
            if _position(module) == 1
            for item in module.get("items") or []
            if item.get("type") == "Page" and isinstance(item.get("page_url"), str)
        }
        for page in pages:
            source_url = page.get("url")
            if not isinstance(source_url, str):
                continue
            target_url = self.source_to_target_pages[source_url]
            self.client.put_form(
                f"courses/{self.course.target_course_id}/pages/{target_url}",
                {
                    "wiki_page[body]": self._rewrite_content(self.course.text_from_record(page, "local_html_path")),
                    "wiki_page[published]": _canvas_bool(source_url in first_page_urls),
                    "wiki_page[hide_from_students]": _canvas_bool(source_url not in first_page_urls),
                },
            )

    def _finalise_assignments(self, assignments: list[dict[str, Any]]) -> None:
        for assignment in assignments:
            if assignment.get("is_quiz_assignment") or assignment.get("is_quiz_lti_assignment"):
                continue
            source_id = _int_id(assignment, "source assignment")
            target_id = self.source_to_target_assignments[source_id]
            self.client.put_form(
                f"courses/{self.course.target_course_id}/assignments/{target_id}",
                {
                    "assignment[description]": self._rewrite_content(
                        self.course.text_from_record(assignment, "local_description_path")
                    ),
                    "assignment[published]": "false",
                },
            )

    def _finalise_classic_quizzes(self) -> None:
        for quiz in self.course.optional_records("quizzes/classic/quizzes.json", "quizzes"):
            source_id = _int_id(quiz, "source classic quiz")
            target_id = self.source_to_target_quizzes[source_id]
            self.client.put_form(
                f"courses/{self.course.target_course_id}/quizzes/{target_id}",
                {
                    "quiz[description]": self._rewrite_content(str(quiz.get("description") or "")),
                    "quiz[published]": "false",
                    "quiz[notify_of_update]": "false",
                },
            )

    def _finalise_discussions(self, discussions: list[dict[str, Any]]) -> None:
        for discussion in discussions:
            if discussion.get("is_announcement"):
                continue
            source_id = _int_id(discussion, "source discussion")
            target_id = self.source_to_target_discussions[source_id]
            self.client.put_form(
                f"courses/{self.course.target_course_id}/discussion_topics/{target_id}",
                {
                    "message": self._rewrite_content(
                        self.course.text_from_record(discussion, "local_message_path")
                    ),
                    "published": "false",
                },
            )

    def _first_module_file_ids(self, modules: list[dict[str, Any]]) -> set[int]:
        first = min(modules, key=_position, default=None)
        if not first:
            return set()
        return {
            int(item["content_id"])
            for item in first.get("items") or []
            if item.get("type") == "File" and isinstance(item.get("content_id"), int)
        }

    def _validate_local_media(self) -> None:
        files = self.course.records("files/files.json", "files")
        media_by_name = _media_by_name(files)
        for quiz in self.course.optional_records("quizzes/new/quizzes.json", "quizzes"):
            for index, item in enumerate(quiz.get("items") or [], start=1):
                body = str((item.get("entry") or {}).get("item_body") or "")
                missing = _missing_signed_media_names(body, media_by_name)
                if not missing:
                    continue
                stripped = _drop_unavailable_image_tags(body, media_by_name)
                remaining = _missing_signed_media_names(stripped, media_by_name)
                if not self.omit_missing_quiz_media or remaining:
                    name = sorted(missing)[0]
                    raise UploadError(
                        f"Local archive lacks the media file {name!r} required by new quiz {quiz.get('title')!r}."
                    )
                self._record(
                    "new_quiz_media",
                    "omitted",
                    quiz=quiz.get("title"),
                    question=index,
                    filenames=sorted(missing),
                    reason="Missing local image tag omitted by approved deployment option.",
                )

    def _rewrite_content(self, value: str) -> str:
        text = value
        text = SOURCE_FILE_URL.sub(self._replace_file_api_url, text)
        text = SOURCE_FILE_PREVIEW_URL.sub(self._replace_file_preview_url, text)
        text = SOURCE_PAGE_URL.sub(self._replace_page_url, text)
        text = SOURCE_ASSIGNMENT_URL.sub(self._replace_assignment_url, text)
        text = SOURCE_QUIZ_URL.sub(self._replace_quiz_url, text)
        text = SOURCE_DISCUSSION_URL.sub(self._replace_discussion_url, text)
        return text

    def _replace_file_api_url(self, match: re.Match[str]) -> str:
        if int(match.group("course")) != self.course.source_course_id:
            return match.group(0)
        target = self.source_to_target_files.get(int(match.group("file")))
        if target is None:
            return match.group(0)
        return f"{self.client.base_url}/api/v1/courses/{self.course.target_course_id}/files/{target}"

    def _replace_file_preview_url(self, match: re.Match[str]) -> str:
        if int(match.group("course")) != self.course.source_course_id:
            return match.group(0)
        target = self.source_to_target_files.get(int(match.group("file")))
        if target is None:
            return match.group(0)
        return f"{self.client.base_url}/courses/{self.course.target_course_id}/files/{target}/preview"

    def _replace_page_url(self, match: re.Match[str]) -> str:
        if int(match.group("course")) != self.course.source_course_id:
            return match.group(0)
        target = self.source_to_target_pages.get(match.group("page"))
        if target is None:
            return match.group(0)
        return f"{self.client.base_url}/courses/{self.course.target_course_id}/pages/{target}"

    def _replace_assignment_url(self, match: re.Match[str]) -> str:
        return self._replace_numeric_url(match, self.source_to_target_assignments, "assignment", "assignments")

    def _replace_quiz_url(self, match: re.Match[str]) -> str:
        return self._replace_numeric_url(match, self.source_to_target_quizzes, "quiz", "quizzes")

    def _replace_discussion_url(self, match: re.Match[str]) -> str:
        return self._replace_numeric_url(match, self.source_to_target_discussions, "topic", "discussion_topics")

    def _replace_numeric_url(self, match: re.Match[str], mapping: Mapping[int, int], group: str, resource: str) -> str:
        if int(match.group("course")) != self.course.source_course_id:
            return match.group(0)
        target = mapping.get(int(match.group(group)))
        if target is None:
            return match.group(0)
        return f"{self.client.base_url}/courses/{self.course.target_course_id}/{resource}/{target}"

    def _record(self, category: str, status: str, **details: Any) -> None:
        self.events.append({"category": category, "status": status, **details})


def _new_quiz_form(quiz: Mapping[str, Any], group_ids: Mapping[int, int]) -> list[tuple[str, Any]]:
    values: list[tuple[str, Any]] = [
        ("quiz[title]", quiz.get("title") or "Untitled quiz"),
        ("quiz[points_possible]", quiz.get("points_possible") or 0),
        ("quiz[grading_type]", quiz.get("grading_type") or "points"),
        ("quiz[instructions]", str(quiz.get("instructions") or "")),
        ("quiz[published]", "false"),
    ]
    group = quiz.get("assignment_group_id")
    if group is not None:
        values.append(("quiz[assignment_group_id]", _mapped_int(group_ids, group, "new quiz group")))
    for key in ("calculator_type", "filter_ip_address", "one_at_a_time_type", "allow_backtracking", "shuffle_answers", "shuffle_questions", "require_student_access_code", "has_time_limit", "session_time_limit_in_seconds"):
        value = (quiz.get("quiz_settings") or {}).get(key)
        if value is not None:
            values.append((f"quiz[quiz_settings][{key}]", _canvas_value(value)))
    return values


def _new_quiz_item(
    item: Mapping[str, Any],
    rewrite: Any,
    media_by_name: Mapping[str, int],
    file_ids: Mapping[int, int],
    base_url: str,
    target_course_id: int,
    *,
    omit_missing_image_tags: bool = False,
) -> dict[str, Any]:
    entry = dict(item.get("entry") or {})
    interaction = dict(entry.get("interaction_data") or {})
    choices = interaction.get("choices")
    replacement_ids: dict[str, str] = {}
    if isinstance(choices, list):
        cleaned_choices = []
        for choice in choices:
            if not isinstance(choice, Mapping):
                continue
            clean_choice = dict(choice)
            old_id = clean_choice.get("id")
            new_id = str(uuid.uuid4())
            if isinstance(old_id, str):
                replacement_ids[old_id] = new_id
            clean_choice["id"] = new_id
            clean_choice.pop("position", None)
            cleaned_choices.append(clean_choice)
        interaction["choices"] = cleaned_choices
    entry["interaction_data"] = interaction
    for key in ("id", "created_at", "updated_at", "tag_associations"):
        entry.pop(key, None)
    body = rewrite(str(entry.get("item_body") or ""))
    if omit_missing_image_tags:
        body = _drop_unavailable_image_tags(body, media_by_name)
    entry["item_body"] = _replace_signed_media(body, media_by_name, file_ids, base_url, target_course_id)
    entry["scoring_data"] = _replace_exact_values(entry.get("scoring_data") or {}, replacement_ids)
    clean = {
        "position": item.get("position") or 1,
        "points_possible": item.get("points_possible") or 0,
        "properties": item.get("properties") or {},
        "entry_type": "Item",
        "entry": entry,
    }
    return clean


def _replace_signed_media(
    body: str,
    media_by_name: Mapping[str, int],
    file_ids: Mapping[int, int],
    base_url: str,
    target_course_id: int,
) -> str:
    def replacement(match: re.Match[str]) -> str:
        filename = unquote_plus(urlsplit(match.group(0)).path).rsplit("/", 1)[-1].casefold()
        source_id = media_by_name.get(filename)
        target_id = file_ids.get(source_id) if source_id is not None else None
        if target_id is None:
            raise UploadError(f"New quiz media {filename!r} was not uploaded.")
        return f"{base_url}/courses/{target_course_id}/files/{target_id}/preview"
    return SIGNED_MEDIA_URL.sub(replacement, body)


def _missing_signed_media_names(body: str, media_by_name: Mapping[str, int]) -> set[str]:
    return {
        unquote_plus(urlsplit(url).path).rsplit("/", 1)[-1].casefold()
        for url in SIGNED_MEDIA_URL.findall(body)
        if unquote_plus(urlsplit(url).path).rsplit("/", 1)[-1].casefold() not in media_by_name
    }


def _drop_unavailable_image_tags(body: str, media_by_name: Mapping[str, int]) -> str:
    """Remove only image tags whose signed source file is unavailable locally.

    A missing signed URL elsewhere in the HTML stays a hard stop: this narrowly
    scoped recovery option must never delete text, links, or non-image media.
    """
    def replacement(match: re.Match[str]) -> str:
        tag = match.group(0)
        if _missing_signed_media_names(tag, media_by_name):
            return ""
        return tag

    return HTML_IMAGE_TAG.sub(replacement, body)


def _classic_question_form(question: Mapping[str, Any], rewrite: Any) -> list[tuple[str, Any]]:
    payload = {
        "question_name": question.get("question_name") or "Question",
        "question_text": rewrite(str(question.get("question_text") or "")),
        "question_type": question.get("question_type") or "essay_question",
        "points_possible": question.get("points_possible") or 0,
        "correct_comments": question.get("correct_comments") or "",
        "incorrect_comments": question.get("incorrect_comments") or "",
        "neutral_comments": question.get("neutral_comments") or "",
        "answers": question.get("answers") or [],
        "matches": question.get("matches") or [],
    }
    return _flatten_form("question", payload)


def _flatten_form(prefix: str, value: Any) -> list[tuple[str, Any]]:
    result: list[tuple[str, Any]] = []
    def visit(key: str, current: Any) -> None:
        if isinstance(current, Mapping):
            for child_key, child_value in current.items():
                visit(f"{key}[{child_key}]", child_value)
        elif isinstance(current, list):
            for index, child_value in enumerate(current):
                visit(f"{key}[{index}]", child_value)
        elif current is not None:
            result.append((key, _canvas_value(current)))
    visit(prefix, value)
    return result


def _replace_exact_values(value: Any, replacements: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        return replacements.get(value, value)
    if isinstance(value, list):
        return [_replace_exact_values(item, replacements) for item in value]
    if isinstance(value, Mapping):
        return {key: _replace_exact_values(item, replacements) for key, item in value.items()}
    return value


def _media_by_name(files: list[dict[str, Any]]) -> dict[str, int]:
    result: dict[str, int] = {}
    ambiguous: set[str] = set()
    for file_record in files:
        source_id = _int_id(file_record, "source file")
        for key in ("display_name", "original_filename", "filename"):
            value = file_record.get(key)
            if not isinstance(value, str):
                continue
            name = unquote_plus(value).casefold()
            existing = result.get(name)
            if existing is not None and existing != source_id:
                ambiguous.add(name)
            else:
                result[name] = source_id
    for name in ambiguous:
        result.pop(name, None)
    return result


def _map_by_unique_name(
    source: list[dict[str, Any]], target: list[dict[str, Any]], label: str
) -> dict[int, int]:
    target_by_name: dict[str, dict[str, Any]] = {}
    duplicates: set[str] = set()
    for value in target:
        name = value.get("name") or value.get("title")
        if not isinstance(name, str):
            continue
        key = name.casefold()
        if key in target_by_name:
            duplicates.add(key)
        else:
            target_by_name[key] = value
    result: dict[int, int] = {}
    for value in source:
        source_id = _int_id(value, f"source {label}")
        name = value.get("name") or value.get("title")
        if not isinstance(name, str):
            continue
        key = name.casefold()
        if key in duplicates:
            raise UploadError(f"Cannot safely resume: target has duplicate {label} name {name!r}.")
        match = target_by_name.get(key)
        if match is not None:
            result[source_id] = _int_id(match, f"target {label}")
    return result


def _map_by_name_and_position(
    source: list[dict[str, Any]], target: list[dict[str, Any]], label: str
) -> dict[int, int]:
    """Resume records whose source is allowed to repeat a display name.

    Assignment groups commonly use the placeholder name ``Assignments`` more
    than once.  Their stable relative position disambiguates a deployment we
    created ourselves, while an identical name-and-position pair remains a
    safe stop rather than a guess.
    """
    target_by_key: dict[tuple[str, int], dict[str, Any]] = {}
    duplicates: set[tuple[str, int]] = set()
    for value in target:
        name = value.get("name") or value.get("title")
        if not isinstance(name, str):
            continue
        key = (name.casefold(), _position(value))
        if key in target_by_key:
            duplicates.add(key)
        else:
            target_by_key[key] = value

    result: dict[int, int] = {}
    source_keys: set[tuple[str, int]] = set()
    for value in source:
        source_id = _int_id(value, f"source {label}")
        name = value.get("name") or value.get("title")
        if not isinstance(name, str):
            continue
        key = (name.casefold(), _position(value))
        if key in source_keys:
            raise UploadError(f"Cannot safely resume: source has duplicate {label} {name!r} at position {key[1]}.")
        source_keys.add(key)
        if key in duplicates:
            raise UploadError(
                f"Cannot safely resume: target has duplicate {label} {name!r} at position {key[1]}."
            )
        match = target_by_key.get(key)
        if match is not None:
            result[source_id] = _int_id(match, f"target {label}")
    return result


def _map_folders(source: list[dict[str, Any]], target: list[dict[str, Any]]) -> dict[int, int]:
    target_by_name = {
        str(value.get("full_name")): value
        for value in target
        if isinstance(value.get("full_name"), str)
    }
    result: dict[int, int] = {}
    for value in source:
        full_name = value.get("full_name")
        if not isinstance(full_name, str):
            continue
        match = target_by_name.get(full_name)
        if match is not None:
            result[_int_id(value, "source folder")] = _int_id(match, "target folder")
    return result


def _map_files(
    source_files: list[dict[str, Any]],
    target_files: list[dict[str, Any]],
    source_folders: list[dict[str, Any]],
    target_folders: list[dict[str, Any]],
) -> dict[int, int]:
    source_folder_names = {
        _int_id(value, "source folder"): str(value.get("full_name")) for value in source_folders
    }
    target_folder_names = {
        _int_id(value, "target folder"): str(value.get("full_name")) for value in target_folders
    }
    target_by_location: dict[tuple[str, str], dict[str, Any]] = {}
    for value in target_files:
        folder_id = value.get("folder_id")
        display_name = value.get("display_name")
        if isinstance(folder_id, int) and isinstance(display_name, str):
            key = (target_folder_names.get(folder_id, ""), display_name.casefold())
            if key in target_by_location:
                raise UploadError(f"Cannot safely resume: duplicate target file {display_name!r}.")
            target_by_location[key] = value
    result: dict[int, int] = {}
    for value in source_files:
        source_id = _int_id(value, "source file")
        folder_id = value.get("folder_id")
        local_path = value.get("local_path")
        if not isinstance(folder_id, int) or not isinstance(local_path, str):
            continue
        key = (source_folder_names.get(folder_id, ""), Path(local_path).name.casefold())
        match = target_by_location.get(key)
        if match is not None:
            result[source_id] = _int_id(match, "target file")
    return result


def _map_pages(source: list[dict[str, Any]], target: list[dict[str, Any]]) -> dict[str, str]:
    target_by_url = {
        str(value.get("url")): value for value in target if isinstance(value.get("url"), str)
    }
    result: dict[str, str] = {}
    for value in source:
        source_url = value.get("url")
        if not isinstance(source_url, str):
            continue
        match = target_by_url.get(source_url)
        if match is not None:
            target_url = match.get("url")
            if isinstance(target_url, str):
                result[source_url] = target_url
    return result


def _module_item_key(module_id: int, item: Mapping[str, Any]) -> tuple[int, str, str, int]:
    item_type = item.get("type")
    title = item.get("title")
    if not isinstance(item_type, str) or not isinstance(title, str):
        raise UploadError("A module item lacks a type or title required for a safe resume.")
    return (module_id, item_type, title, _position(item))


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise UploadError(f"Unable to read valid JSON from {path}.") from error
    if not isinstance(payload, dict):
        raise UploadError(f"{path} does not contain a JSON object.")
    return payload


def _within(root: Path, candidate: Path) -> Path:
    resolved_root = root.resolve()
    resolved = candidate.resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise UploadError(f"Unsafe local archive path was rejected: {candidate}")
    return resolved


def _int_id(value: Mapping[str, Any], label: str) -> int:
    identifier = value.get("id")
    if isinstance(identifier, int):
        return identifier
    if isinstance(identifier, str) and identifier.isdigit():
        return int(identifier)
    raise UploadError(f"Canvas did not provide a valid {label} ID.")


def _mapped_int(mapping: Mapping[int, int], source_id: Any, label: str) -> int:
    try:
        key = int(source_id)
    except (TypeError, ValueError) as error:
        raise UploadError(f"An archived {label} reference is invalid.") from error
    target = mapping.get(key)
    if target is None:
        raise UploadError(f"An archived {label} reference could not be mapped to the target course.")
    return target


def _optional_mapped_int(mapping: Mapping[int, int], source_id: Any) -> int | None:
    try:
        return mapping.get(int(source_id))
    except (TypeError, ValueError):
        return None


def _position(value: Mapping[str, Any]) -> int:
    raw = value.get("position")
    return raw if isinstance(raw, int) and raw > 0 else 1


def _canvas_bool(value: bool) -> str:
    return "true" if value else "false"


def _canvas_value(value: Any) -> Any:
    if isinstance(value, bool):
        return _canvas_bool(value)
    return value


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


__all__ = ["ActiveCourse", "CanvasWriteClient", "CourseDeployment", "UploadError"]
