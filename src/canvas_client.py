"""Read-only access to the Canvas LMS REST API."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
import logging
import time
from typing import Any, Final
from urllib.parse import quote, urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import normalize_canvas_url
from .exceptions import (
    CanvasAuthenticationError,
    CanvasConnectionError,
    CanvasError,
    CanvasPermissionError,
    CanvasRateLimitError,
    ReadOnlyCanvasError,
)
from .version import __version__


LOGGER = logging.getLogger(__name__)

READ_ONLY_METHODS: Final[frozenset[str]] = frozenset({"GET", "HEAD"})
RETRYABLE_STATUS_CODES: Final[tuple[int, ...]] = (500, 502, 503, 504)
DEFAULT_TIMEOUT: Final[tuple[float, float]] = (5.0, 30.0)
MAX_RATE_LIMIT_DELAY: Final[float] = 60.0
COURSE_ENROLLMENT_STATES: Final[tuple[str, ...]] = (
    "active",
    "invited_or_pending",
    "completed",
)


class CanvasClient:
    """A small Canvas API client with an enforced read-only boundary.

    Only ``GET`` and ``HEAD`` requests can pass through :meth:`request`. Any
    other method is rejected before the configured HTTP session is called.
    """

    def __init__(
        self,
        base_url: str,
        api_token: str,
        *,
        timeout: tuple[float, float] = DEFAULT_TIMEOUT,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
        session: requests.Session | None = None,
    ) -> None:
        if not isinstance(api_token, str) or not api_token.strip():
            raise CanvasAuthenticationError("A Canvas API token is required.")
        token = api_token.strip()

        self.base_url = normalize_canvas_url(base_url)
        self.api_root = f"{self.base_url}/api/v1"
        self.timeout = timeout
        self.max_retries = max(0, max_retries)
        self.backoff_factor = max(0.0, backoff_factor)
        self._session = session or requests.Session()
        self._owns_session = session is None

        self._session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "User-Agent": f"Canvas-VLE-Manager/{__version__} (read-only)",
            }
        )

        if self._owns_session:
            retry_policy = Retry(
                total=self.max_retries,
                connect=self.max_retries,
                read=self.max_retries,
                status=self.max_retries,
                allowed_methods=READ_ONLY_METHODS,
                status_forcelist=RETRYABLE_STATUS_CODES,
                backoff_factor=self.backoff_factor,
                # 429/legacy-403 throttling is handled explicitly below so one
                # bounded retry budget applies and Retry-After cannot multiply
                # retries invisibly inside urllib3.
                respect_retry_after_header=False,
                raise_on_status=False,
            )
            adapter = HTTPAdapter(max_retries=retry_policy)
            self._session.mount("https://", adapter)
            self._session.mount("http://", adapter)

    def __repr__(self) -> str:
        """Return a representation that cannot disclose the API token."""

        return f"{type(self).__name__}(base_url={self.base_url!r}, read_only=True)"

    def __enter__(self) -> CanvasClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying HTTP session when the client created it."""

        if self._owns_session:
            self._session.close()

    def request(
        self,
        method: str,
        endpoint: str,
        *,
        params: Mapping[str, Any] | list[tuple[str, Any]] | None = None,
        timeout: tuple[float, float] | float | None = None,
        **kwargs: Any,
    ) -> requests.Response:
        """Send a safe request after enforcing the Version 0.1 guard."""

        normalized_method = method.upper().strip()
        if normalized_method not in READ_ONLY_METHODS:
            raise ReadOnlyCanvasError(
                "Canvas VLE Manager v0.1 operates in read-only mode. "
                "Write requests are disabled."
            )

        url = self._build_url(endpoint)
        safe_target = urlsplit(url).path
        LOGGER.info("Canvas %s %s", normalized_method, safe_target)

        rate_limit_attempt = 0
        while True:
            try:
                response = self._session.request(
                    normalized_method,
                    url,
                    params=params,
                    timeout=self.timeout if timeout is None else timeout,
                    **kwargs,
                )
            except requests.Timeout as exc:
                LOGGER.warning("Canvas request timed out for API path %s.", safe_target)
                raise CanvasConnectionError(
                    "The request to Canvas timed out. Please try again."
                ) from exc
            except requests.RequestException as exc:
                LOGGER.warning("Canvas connection failed for API path %s.", safe_target)
                raise CanvasConnectionError(
                    "Unable to connect to Canvas. Check your internet connection "
                    "and configured Canvas URL."
                ) from exc

            if not self._is_rate_limited(response):
                break
            if rate_limit_attempt >= self.max_retries:
                self._raise_rate_limit(response)

            delay = self._rate_limit_delay(response, rate_limit_attempt)
            LOGGER.warning(
                "Canvas rate limit reached; retrying API path %s in %.1f seconds.",
                safe_target,
                delay,
            )
            time.sleep(delay)
            rate_limit_attempt += 1

        self._raise_for_status(response, safe_target)
        self._log_rate_limit(response)
        return response

    def get(
        self,
        endpoint: str,
        *,
        params: Mapping[str, Any] | list[tuple[str, Any]] | None = None,
        **kwargs: Any,
    ) -> requests.Response:
        """Send a GET request."""

        return self.request("GET", endpoint, params=params, **kwargs)

    def head(
        self,
        endpoint: str,
        *,
        params: Mapping[str, Any] | list[tuple[str, Any]] | None = None,
        **kwargs: Any,
    ) -> requests.Response:
        """Send a HEAD request."""

        return self.request("HEAD", endpoint, params=params, **kwargs)

    def get_json(
        self,
        endpoint: str,
        *,
        params: Mapping[str, Any] | list[tuple[str, Any]] | None = None,
    ) -> Any:
        """Return decoded JSON from a Canvas GET response."""

        response = self.get(endpoint, params=params)
        try:
            return response.json()
        except (TypeError, ValueError) as exc:
            raise CanvasError(
                "Canvas returned a response that was not valid JSON."
            ) from exc

    def iter_paginated(
        self,
        endpoint: str,
        *,
        params: Mapping[str, Any] | list[tuple[str, Any]] | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield every object from a paginated Canvas collection.

        Canvas documents pagination links as opaque, absolute URLs. Parameters
        are therefore supplied only for the initial request and subsequent
        ``next`` links are followed without rebuilding them.
        """

        next_endpoint: str | None = endpoint
        next_params = params
        visited: set[str] = set()

        while next_endpoint:
            absolute_url = self._build_url(next_endpoint)
            if absolute_url in visited:
                raise CanvasError("Canvas returned a repeated pagination link.")
            visited.add(absolute_url)

            response = self.get(next_endpoint, params=next_params)
            try:
                payload = response.json()
            except (TypeError, ValueError) as exc:
                raise CanvasError(
                    "Canvas returned a paginated response that was not valid JSON."
                ) from exc

            if not isinstance(payload, list):
                raise CanvasError(
                    "Canvas returned an unexpected response for a list endpoint."
                )

            for item in payload:
                if not isinstance(item, dict):
                    raise CanvasError(
                        "Canvas returned an unexpected item in a list response."
                    )
                yield item

            next_link = response.links.get("next", {}).get("url")
            next_endpoint = str(next_link) if next_link else None
            next_params = None

    def get_current_user(self) -> dict[str, Any]:
        """Return the authenticated user's Canvas profile."""

        profile = self.get_json("users/self/profile")
        if not isinstance(profile, dict):
            raise CanvasError("Canvas returned an unexpected user profile response.")
        return profile

    def get_courses(self) -> list[dict[str, Any]]:
        """Return all non-deleted courses visible to the current user."""

        courses: list[dict[str, Any]] = []
        positions_by_id: dict[str, int] = {}

        # Canvas accepts only one enrollment_state value per request. Query all
        # documented buckets so concluded teaching remains discoverable even
        # when its Course workflow state is still "available".
        for enrollment_state in COURSE_ENROLLMENT_STATES:
            params: list[tuple[str, Any]] = [
                ("per_page", 100),
                ("include[]", "term"),
                ("include[]", "account"),
                ("include[]", "concluded"),
                ("include[]", "favorites"),
                ("state[]", "unpublished"),
                ("state[]", "available"),
                ("state[]", "completed"),
                ("enrollment_state", enrollment_state),
            ]
            for course in self.iter_paginated("courses", params=params):
                identity = course.get("id", course.get("uuid"))
                if identity is None:
                    courses.append(dict(course))
                    continue

                key = str(identity)
                existing_position = positions_by_id.get(key)
                if existing_position is None:
                    positions_by_id[key] = len(courses)
                    courses.append(self._copy_course(course))
                else:
                    self._merge_course(courses[existing_position], course)
        return courses

    def get_course(self, course_id: str | int) -> dict[str, Any]:
        """Return safe-to-request course details, including the syllabus."""

        course = self.get_json(
            f"courses/{self._path_segment(course_id)}",
            params=[
                ("include[]", "syllabus_body"),
                ("include[]", "term"),
                ("include[]", "account"),
                ("include[]", "concluded"),
            ],
        )
        return self._expect_mapping(course, "course")

    def get_modules(self, course_id: str | int) -> list[dict[str, Any]]:
        """Return every module in a course."""

        course = self._path_segment(course_id)
        return list(
            self.iter_paginated(
                f"courses/{course}/modules",
                params={"per_page": 100},
            )
        )

    def get_module_items(
        self,
        course_id: str | int,
        module_id: str | int,
    ) -> list[dict[str, Any]]:
        """Return every item in a module, including content lock details."""

        course = self._path_segment(course_id)
        module = self._path_segment(module_id)
        return list(
            self.iter_paginated(
                f"courses/{course}/modules/{module}/items",
                params=[("per_page", 100), ("include[]", "content_details")],
            )
        )

    def get_pages(
        self,
        course_id: str | int,
        *,
        include_body: bool = False,
    ) -> list[dict[str, Any]]:
        """Return every accessible Canvas page in a course."""

        params: list[tuple[str, Any]] = [("per_page", 100)]
        if include_body:
            params.append(("include[]", "body"))
        return list(
            self.iter_paginated(
                f"courses/{self._path_segment(course_id)}/pages",
                params=params,
            )
        )

    def get_page(
        self,
        course_id: str | int,
        page_url_or_id: str | int,
    ) -> dict[str, Any]:
        """Return the complete content of one Canvas page."""

        page = self.get_json(
            "courses/"
            f"{self._path_segment(course_id)}/pages/"
            f"{self._path_segment(page_url_or_id)}"
        )
        return self._expect_mapping(page, "page")

    def get_folders(self, course_id: str | int) -> list[dict[str, Any]]:
        """Return the flat list of all course folders."""

        return list(
            self.iter_paginated(
                f"courses/{self._path_segment(course_id)}/folders",
                params={"per_page": 100},
            )
        )

    def get_files(self, course_id: str | int) -> list[dict[str, Any]]:
        """Return every accessible course file without uploader information."""

        return list(
            self.iter_paginated(
                f"courses/{self._path_segment(course_id)}/files",
                params={"per_page": 100},
            )
        )

    def get_assignments(self, course_id: str | int) -> list[dict[str, Any]]:
        """Return assignment definitions without submissions or visibility lists."""

        return list(
            self.iter_paginated(
                f"courses/{self._path_segment(course_id)}/assignments",
                params={"per_page": 100},
            )
        )

    def get_assignment_groups(self, course_id: str | int) -> list[dict[str, Any]]:
        """Return assignment groups without submission-related includes."""

        return list(
            self.iter_paginated(
                f"courses/{self._path_segment(course_id)}/assignment_groups",
                params={"per_page": 100},
            )
        )

    def get_rubrics(self, course_id: str | int) -> list[dict[str, Any]]:
        """Return course rubrics without student assessment records."""

        return list(
            self.iter_paginated(
                f"courses/{self._path_segment(course_id)}/rubrics",
                params={"per_page": 100},
            )
        )

    def get_classic_quizzes(self, course_id: str | int) -> list[dict[str, Any]]:
        """Return Classic Quiz definitions."""

        return list(
            self.iter_paginated(
                f"courses/{self._path_segment(course_id)}/quizzes",
                params={"per_page": 100},
            )
        )

    def get_classic_quiz_questions(
        self,
        course_id: str | int,
        quiz_id: str | int,
    ) -> list[dict[str, Any]]:
        """Return current Classic Quiz questions, never submission questions."""

        return list(
            self.iter_paginated(
                "courses/"
                f"{self._path_segment(course_id)}/quizzes/"
                f"{self._path_segment(quiz_id)}/questions",
                params={"per_page": 100},
            )
        )

    def get_new_quizzes(self, course_id: str | int) -> list[dict[str, Any]]:
        """Return New Quiz definitions when the installation exposes the API."""

        return list(
            self.iter_paginated(
                f"api/quiz/v1/courses/{self._path_segment(course_id)}/quizzes",
                params={"per_page": 100},
            )
        )

    def get_new_quiz_items(
        self,
        course_id: str | int,
        assignment_id: str | int,
    ) -> list[dict[str, Any]]:
        """Return New Quiz items by the quiz's assignment ID."""

        return list(
            self.iter_paginated(
                "api/quiz/v1/courses/"
                f"{self._path_segment(course_id)}/quizzes/"
                f"{self._path_segment(assignment_id)}/items",
                params={"per_page": 100},
            )
        )

    def get_discussions(self, course_id: str | int) -> list[dict[str, Any]]:
        """Return topic definitions only; discussion entries are never requested."""

        return list(
            self.iter_paginated(
                f"courses/{self._path_segment(course_id)}/discussion_topics",
                params={"per_page": 100, "only_announcements": False},
            )
        )

    def get_announcements(self, course_id: str | int) -> list[dict[str, Any]]:
        """Return course announcements across a deliberately broad date range."""

        end_date = (datetime.now(UTC).date() + timedelta(days=3650)).isoformat()
        return list(
            self.iter_paginated(
                "announcements",
                params=[
                    ("per_page", 100),
                    ("context_codes[]", f"course_{course_id}"),
                    ("start_date", "1970-01-01"),
                    ("end_date", end_date),
                    ("active_only", False),
                    ("latest_only", False),
                ],
            )
        )

    def _build_url(self, endpoint: str) -> str:
        value = str(endpoint).strip()
        if not value:
            raise CanvasError("A Canvas API endpoint is required.")
        if "\\" in value or any(
            character.isspace() or ord(character) < 32 or ord(character) == 127
            for character in value
        ):
            raise CanvasError("Canvas returned an invalid API URL.")

        try:
            parsed = urlsplit(value)
            parsed.port
        except ValueError:
            raise CanvasError("Canvas returned an invalid API URL.") from None
        if parsed.scheme or parsed.netloc:
            if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
                raise CanvasError("Canvas returned an invalid API URL.")
            return self._prepare_trusted_url(value)

        relative = value.lstrip("/")
        if relative.startswith("api/"):
            candidate = f"{self.base_url}/{relative}"
        else:
            candidate = f"{self.api_root}/{relative}"
        return self._prepare_trusted_url(candidate)

    def _prepare_trusted_url(self, url: str) -> str:
        try:
            parsed = urlsplit(url)
            parsed.port
            if parsed.username is not None or parsed.password is not None:
                raise ValueError
            prepared_url = requests.Request("GET", url).prepare().url
            prepared_base_url = requests.Request("GET", self.base_url).prepare().url
            if not prepared_url or not prepared_base_url:
                raise ValueError
        except (requests.RequestException, TypeError, UnicodeError, ValueError):
            raise CanvasError("Canvas returned an invalid API URL.") from None

        # Validate the exact form Requests will send, not just urllib's initial
        # interpretation. This closes parser-differential credential leaks.
        if self._origin(prepared_url) != self._origin(prepared_base_url):
            raise CanvasError(
                "Canvas returned a pagination URL for a different server; "
                "the request was blocked to protect the API token."
            )
        return prepared_url

    @staticmethod
    def _copy_course(course: dict[str, Any]) -> dict[str, Any]:
        copied = dict(course)
        if isinstance(course.get("enrollments"), list):
            copied["enrollments"] = list(course["enrollments"])
        return copied

    @staticmethod
    def _merge_course(existing: dict[str, Any], incoming: dict[str, Any]) -> None:
        for key, value in incoming.items():
            if key == "enrollments":
                continue
            if key not in existing or existing[key] in (None, "", [], {}):
                existing[key] = value

        incoming_enrollments = incoming.get("enrollments")
        if not isinstance(incoming_enrollments, list):
            return
        current_enrollments = existing.get("enrollments")
        if not isinstance(current_enrollments, list):
            current_enrollments = []
            existing["enrollments"] = current_enrollments
        for enrollment in incoming_enrollments:
            if enrollment not in current_enrollments:
                current_enrollments.append(enrollment)

    @staticmethod
    def _path_segment(value: str | int) -> str:
        text = str(value).strip()
        if not text:
            raise CanvasError("A Canvas resource ID is required.")
        return quote(text, safe="")

    @staticmethod
    def _expect_mapping(value: Any, resource_name: str) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise CanvasError(
                f"Canvas returned an unexpected {resource_name} response."
            )
        return value

    @staticmethod
    def _origin(url: str) -> tuple[str, str, int | None]:
        parsed = urlsplit(url)
        scheme = parsed.scheme.lower()
        default_port = 443 if scheme == "https" else 80 if scheme == "http" else None
        return scheme, (parsed.hostname or "").lower(), parsed.port or default_port

    @staticmethod
    def _raise_for_status(response: requests.Response, safe_target: str) -> None:
        status = response.status_code
        if status >= 400:
            LOGGER.warning("Canvas returned HTTP %d for API path %s.", status, safe_target)
        if status == 401:
            raise CanvasAuthenticationError(
                "Canvas authentication failed. Please check your API token."
            )
        if status == 403:
            raise CanvasPermissionError(
                "Canvas denied access to this resource for the authenticated account."
            )
        if status >= 400:
            raise CanvasError(
                f"Canvas returned HTTP {status} for API path {safe_target}."
            )

    @staticmethod
    def _log_rate_limit(response: requests.Response) -> None:
        remaining = response.headers.get("X-Rate-Limit-Remaining")
        if remaining is None:
            return
        try:
            if float(remaining) < 50:
                LOGGER.warning("Canvas rate-limit quota is low (%s remaining).", remaining)
        except ValueError:
            LOGGER.debug("Canvas returned an unrecognised rate-limit header.")

    @staticmethod
    def _is_rate_limited(response: requests.Response) -> bool:
        if response.status_code == 429:
            return True
        if response.status_code != 403:
            return False

        remaining = response.headers.get("X-Rate-Limit-Remaining")
        try:
            if remaining is not None and float(remaining) <= 0:
                return True
        except ValueError:
            pass
        return "rate limit exceeded" in response.text.lower()

    @staticmethod
    def _raise_rate_limit(response: requests.Response) -> None:
        retry_after = response.headers.get("Retry-After")
        suffix = f" Retry-After: {retry_after}." if retry_after else ""
        LOGGER.warning("Canvas rate limit persisted after the configured retries.")
        raise CanvasRateLimitError(
            "Canvas temporarily rate-limited the request after retries." + suffix
        )

    def _rate_limit_delay(
        self,
        response: requests.Response,
        attempt: int,
    ) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return min(MAX_RATE_LIMIT_DELAY, max(0.0, float(retry_after)))
            except ValueError:
                try:
                    retry_time = parsedate_to_datetime(retry_after)
                except (TypeError, ValueError, OverflowError):
                    pass
                else:
                    if retry_time.tzinfo is None:
                        retry_time = retry_time.replace(tzinfo=UTC)
                    return min(
                        MAX_RATE_LIMIT_DELAY,
                        max(
                            0.0,
                            retry_time.timestamp() - datetime.now(UTC).timestamp(),
                        ),
                    )
        return min(MAX_RATE_LIMIT_DELAY, self.backoff_factor * (2**attempt))
