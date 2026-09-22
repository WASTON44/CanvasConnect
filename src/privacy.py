"""Remove unnecessary student data and transient secret-bearing URLs."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .course_discovery import normalise_course


ASSIGNMENT_PRIVATE_FIELDS = {
    "submission",
    "submissions",
    "assignment_visibility",
    "observed_users",
    "score_statistics",
    "needs_grading_count_by_section",
}
DISCUSSION_PRIVATE_FIELDS = {
    "author",
    "user",
    "user_id",
    "user_name",
    "editor_id",
    "participants",
    "entries",
    "replies",
    "readers",
}
RUBRIC_PRIVATE_FIELDS = {
    "assessments",
    "assessment",
    "graded_assessments",
    "peer_assessments",
    "rubric_assessment",
}
PAGE_PRIVATE_FIELDS = {"last_edited_by"}
TRANSIENT_URL_FIELDS = {
    "mobile_url",
    "preview_url",
    "speedgrader_url",
    "quiz_extensions_url",
    "thumbnail_url",
}
SENSITIVE_QUERY_NAMES = {
    "access_token",
    "token",
    "verifier",
    "signature",
    "awsaccesskeyid",
    "key-pair-id",
    "policy",
}

COURSE_METADATA_FIELDS = (
    "id",
    "name",
    "original_name",
    "course_code",
    "workflow_state",
    "account_id",
    "root_account_id",
    "enrollment_term_id",
    "start_at",
    "end_at",
    "created_at",
    "locale",
    "time_zone",
    "default_view",
    "syllabus_body",
    "public_syllabus",
    "public_syllabus_to_auth",
    "storage_quota_mb",
    "storage_quota_used_mb",
    "restrict_enrollments_to_course_dates",
    "course_format",
    "access_restricted_by_date",
    "concluded",
    "blueprint",
    "template",
    "is_favorite",
)

FILE_METADATA_FIELDS = (
    "id",
    "uuid",
    "folder_id",
    "display_name",
    "filename",
    "content-type",
    "content_type",
    "size",
    "created_at",
    "updated_at",
    "modified_at",
    "unlock_at",
    "lock_at",
    "locked",
    "hidden",
    "hidden_for_user",
    "locked_for_user",
    "published",
    "visibility_level",
    "mime_class",
    "media_entry_id",
)


def safe_course_metadata(course: dict[str, Any], base_url: str) -> dict[str, Any]:
    """Project a detailed Course object onto teaching-context fields only."""

    discovery = normalise_course(course, base_url)
    result = {
        field: deepcopy(course[field])
        for field in COURSE_METADATA_FIELDS
        if field in course
    }
    result.update(
        {
            "term": discovery["term"],
            "account": discovery["account"],
            "canvas_url": discovery["canvas_url"],
            "enrollments": discovery["enrollments"],
            "current_user_roles": discovery["current_user_roles"],
            "access_states": discovery["access_states"],
        }
    )
    return sanitize_metadata(result)


def safe_file_metadata(file_data: dict[str, Any]) -> dict[str, Any]:
    """Return stable file metadata without download, preview, or uploader URLs."""

    result = {
        field: deepcopy(file_data[field])
        for field in FILE_METADATA_FIELDS
        if field in file_data
    }
    return sanitize_metadata(result)


def sanitize_metadata(
    value: Any,
    *,
    forbidden_fields: Iterable[str] = (),
) -> Any:
    """Recursively remove forbidden fields and redact secret URL parameters."""

    forbidden = {field.casefold() for field in forbidden_fields}
    return _sanitize(value, forbidden)


def sanitize_assignment(value: Any) -> Any:
    return sanitize_metadata(value, forbidden_fields=ASSIGNMENT_PRIVATE_FIELDS)


def sanitize_discussion(value: Any) -> Any:
    return sanitize_metadata(value, forbidden_fields=DISCUSSION_PRIVATE_FIELDS)


def sanitize_rubric(value: Any) -> Any:
    return sanitize_metadata(value, forbidden_fields=RUBRIC_PRIVATE_FIELDS)


def sanitize_page(value: Any) -> Any:
    return sanitize_metadata(value, forbidden_fields=PAGE_PRIVATE_FIELDS)


def _sanitize(value: Any, forbidden: set[str], key_name: str = "") -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, child in value.items():
            key_text = str(key)
            folded = key_text.casefold()
            if folded in forbidden or folded in TRANSIENT_URL_FIELDS:
                continue
            result[key_text] = _sanitize(child, forbidden, folded)
        return result
    if isinstance(value, list):
        return [_sanitize(item, forbidden, key_name) for item in value]
    if isinstance(value, tuple):
        return [_sanitize(item, forbidden, key_name) for item in value]
    if isinstance(value, str) and "url" in key_name and key_name not in {"body", "description", "message", "instructions"}:
        return redact_sensitive_query(value)
    return deepcopy(value)


def redact_sensitive_query(url: str) -> str:
    """Remove known access-token/signature parameters from a stored URL."""

    try:
        parsed = urlsplit(url)
    except ValueError:
        return url
    if not parsed.query:
        return url
    retained = []
    for name, value in parse_qsl(parsed.query, keep_blank_values=True):
        folded = name.casefold()
        if folded in SENSITIVE_QUERY_NAMES or folded.startswith("x-amz-"):
            continue
        retained.append((name, value))
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(retained, doseq=True), parsed.fragment)
    )


__all__ = [
    "redact_sensitive_query",
    "safe_course_metadata",
    "safe_file_metadata",
    "sanitize_assignment",
    "sanitize_discussion",
    "sanitize_metadata",
    "sanitize_page",
    "sanitize_rubric",
]
