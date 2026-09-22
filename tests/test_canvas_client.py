"""Offline contract tests for the read-only Canvas API client."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import pytest
import requests

from src.canvas_client import CanvasClient
from src.exceptions import (
    CanvasAuthenticationError,
    CanvasConnectionError,
    CanvasError,
    CanvasPermissionError,
    CanvasRateLimitError,
    ReadOnlyCanvasError,
)


class FakeResponse:
    """Minimal response double implementing the attributes CanvasClient uses."""

    def __init__(
        self,
        *,
        status_code: int = 200,
        payload: Any = None,
        headers: Mapping[str, str] | None = None,
        links: Mapping[str, Mapping[str, str]] | None = None,
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.headers = dict(headers or {})
        self.links = dict(links or {})
        self.text = text

    def json(self) -> Any:
        if isinstance(self._payload, BaseException):
            raise self._payload
        return self._payload


class FakeSession:
    """Requests session double that can never perform real network I/O."""

    def __init__(self, *outcomes: FakeResponse | BaseException) -> None:
        self.headers: dict[str, str] = {}
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self.outcomes:
            raise AssertionError("Unexpected HTTP request from CanvasClient")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def close(self) -> None:
        self.closed = True


def make_client(
    session: FakeSession,
    *,
    token: str = "super-secret-test-token",
    max_retries: int = 3,
    backoff_factor: float = 0.5,
) -> CanvasClient:
    return CanvasClient(
        "https://canvas.example.ac.uk/canvas",
        token,
        session=session,  # type: ignore[arg-type]
        max_retries=max_retries,
        backoff_factor=backoff_factor,
    )


def test_url_construction_handles_relative_api_and_absolute_urls() -> None:
    opaque_next = (
        "https://canvas.example.ac.uk/api/v1/courses?"
        "page=opaque%2Bcursor%3D%3D&per_page=100"
    )
    session = FakeSession(*(FakeResponse(payload={}) for _ in range(4)))
    client = make_client(session)

    client.get("courses")
    client.get("/courses/42")
    client.get("/api/v1/users/self/profile")
    client.get(opaque_next)

    assert [call["url"] for call in session.calls] == [
        "https://canvas.example.ac.uk/canvas/api/v1/courses",
        "https://canvas.example.ac.uk/canvas/api/v1/courses/42",
        "https://canvas.example.ac.uk/canvas/api/v1/users/self/profile",
        opaque_next,
    ]


def test_get_uses_authenticated_session_without_exposing_token(caplog: Any) -> None:
    token = "never-log-this-token"
    session = FakeSession(FakeResponse(payload={"ok": True}))
    client = make_client(session, token=f"  {token}  ")

    with caplog.at_level(logging.DEBUG, logger="src.canvas_client"):
        response = client.get("courses", params={"per_page": 10})
    client.close()

    assert response.json() == {"ok": True}
    assert session.headers["Authorization"] == f"Bearer {token}"
    assert session.headers["Accept"] == "application/json"
    assert session.headers["User-Agent"].endswith("(read-only)")
    assert session.calls == [
        {
            "method": "GET",
            "url": "https://canvas.example.ac.uk/canvas/api/v1/courses",
            "params": {"per_page": 10},
            "timeout": (5.0, 30.0),
        }
    ]
    assert session.closed is False
    assert token not in repr(client)
    assert token not in caplog.text


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_write_methods_are_blocked_before_the_session_is_called(method: str) -> None:
    session = FakeSession()
    client = make_client(session)

    with pytest.raises(ReadOnlyCanvasError, match="read-only mode"):
        client.request(method, "courses/42", json={"name": "unsafe"})

    assert session.calls == []


def test_pagination_follows_opaque_next_link_and_only_sends_initial_params() -> None:
    next_link = (
        "https://canvas.example.ac.uk/api/v1/courses?"
        "page=opaque%2Fcursor%3Fkeep%3Dexact&per_page=2"
    )
    session = FakeSession(
        FakeResponse(
            payload=[{"id": 1}],
            links={"next": {"url": next_link}},
        ),
        FakeResponse(payload=[{"id": 2}]),
    )
    client = make_client(session)
    params = [("per_page", 2), ("include[]", "term")]

    assert list(client.iter_paginated("courses", params=params)) == [
        {"id": 1},
        {"id": 2},
    ]
    assert session.calls[0]["params"] == params
    assert session.calls[1]["url"] == next_link
    assert session.calls[1]["params"] is None


def test_cross_origin_pagination_is_blocked_before_follow_up_request() -> None:
    token = "must-not-cross-origins"
    session = FakeSession(
        FakeResponse(
            payload=[],
            links={
                "next": {
                    "url": "https://attacker.example/api/v1/courses?page=2"
                }
            },
        )
    )
    client = make_client(session, token=token)

    with pytest.raises(CanvasError, match="different server") as captured:
        list(client.iter_paginated("courses"))

    assert len(session.calls) == 1
    assert token not in str(captured.value)


def test_get_current_user_uses_self_profile_endpoint_without_params() -> None:
    profile = {"id": 7, "name": "Test User"}
    session = FakeSession(FakeResponse(payload=profile))
    client = make_client(session)

    assert client.get_current_user() == profile
    assert session.calls == [
        {
            "method": "GET",
            "url": (
                "https://canvas.example.ac.uk/canvas/api/v1/"
                "users/self/profile"
            ),
            "params": None,
            "timeout": (5.0, 30.0),
        }
    ]


def test_get_courses_requests_all_expected_states_and_includes() -> None:
    session = FakeSession(
        FakeResponse(payload=[{"id": 42}]),
        FakeResponse(payload=[]),
        FakeResponse(payload=[]),
    )
    client = make_client(session)

    assert client.get_courses() == [{"id": 42}]
    assert len(session.calls) == 3
    assert all(call["url"].endswith("/api/v1/courses") for call in session.calls)
    expected_common = [
        ("per_page", 100),
        ("include[]", "term"),
        ("include[]", "account"),
        ("include[]", "concluded"),
        ("include[]", "favorites"),
        ("state[]", "unpublished"),
        ("state[]", "available"),
        ("state[]", "completed"),
    ]
    assert [call["params"] for call in session.calls] == [
        expected_common + [("enrollment_state", "active")],
        expected_common + [("enrollment_state", "invited_or_pending")],
        expected_common + [("enrollment_state", "completed")],
    ]


def test_get_courses_merges_duplicate_courses_and_multiple_enrollments() -> None:
    teacher = {"type": "TeacherEnrollment", "enrollment_state": "active"}
    designer = {"type": "DesignerEnrollment", "enrollment_state": "completed"}
    session = FakeSession(
        FakeResponse(payload=[{"id": 42, "name": "Course", "enrollments": [teacher]}]),
        FakeResponse(payload=[{"id": 42, "term": {"name": "Term"}}]),
        FakeResponse(payload=[{"id": "42", "enrollments": [designer]}]),
    )
    client = make_client(session)

    assert client.get_courses() == [
        {
            "id": 42,
            "name": "Course",
            "term": {"name": "Term"},
            "enrollments": [teacher, designer],
        }
    ]


def test_parser_differential_url_is_blocked_before_token_can_leak() -> None:
    token = "never-send-to-attacker"
    session = FakeSession()
    client = make_client(session, token=token)

    malicious = "https://evil.example\\@canvas.example.ac.uk/api/v1/courses"
    with pytest.raises(CanvasError, match="invalid API URL") as captured:
        client.get(malicious)

    assert session.calls == []
    assert token not in str(captured.value)


def test_internationalised_canvas_host_is_compared_in_prepared_form() -> None:
    session = FakeSession(FakeResponse(payload={}))
    client = CanvasClient(
        "https://münich.example",
        "test-token",
        session=session,  # type: ignore[arg-type]
    )

    client.get("courses")

    assert session.calls[0]["url"].startswith("https://xn--mnich-kva.example/")


def test_401_becomes_authentication_error_without_token_leak() -> None:
    token = "private-auth-token"
    session = FakeSession(FakeResponse(status_code=401, text="Unauthorized"))
    client = make_client(session, token=token)

    with pytest.raises(CanvasAuthenticationError) as captured:
        client.get("users/self/profile")

    assert token not in str(captured.value)
    assert len(session.calls) == 1


def test_ordinary_403_becomes_permission_error() -> None:
    session = FakeSession(
        FakeResponse(
            status_code=403,
            headers={"X-Rate-Limit-Remaining": "12"},
            text="Forbidden",
        )
    )
    client = make_client(session)

    with pytest.raises(CanvasPermissionError):
        client.get("courses/42")

    assert len(session.calls) == 1


@pytest.mark.parametrize(
    ("headers", "body"),
    [
        ({"X-Rate-Limit-Remaining": "0"}, "Forbidden"),
        ({}, "Canvas API rate limit exceeded; wait before retrying"),
    ],
)
def test_403_rate_limit_signatures_become_rate_limit_error(
    headers: dict[str, str], body: str
) -> None:
    session = FakeSession(
        FakeResponse(status_code=403, headers=headers, text=body)
    )
    client = make_client(session, max_retries=0)

    with pytest.raises(CanvasRateLimitError):
        client.get("courses")

    assert len(session.calls) == 1


def test_429_is_retried_and_can_recover(monkeypatch: pytest.MonkeyPatch) -> None:
    sleep_calls: list[float] = []
    monkeypatch.setattr("src.canvas_client.time.sleep", sleep_calls.append)
    session = FakeSession(
        FakeResponse(
            status_code=429,
            headers={"Retry-After": "0.25"},
            text="Too Many Requests",
        ),
        FakeResponse(payload={"recovered": True}),
    )
    client = make_client(session, max_retries=2)

    assert client.get_json("courses/42") == {"recovered": True}
    assert len(session.calls) == 2
    assert sleep_calls == [0.25]


def test_429_exhausts_configured_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    sleep_calls: list[float] = []
    monkeypatch.setattr("src.canvas_client.time.sleep", sleep_calls.append)
    responses = [
        FakeResponse(
            status_code=429,
            headers={"Retry-After": "0"},
            text="Too Many Requests",
        )
        for _ in range(3)
    ]
    session = FakeSession(*responses)
    client = make_client(session, max_retries=2)

    with pytest.raises(CanvasRateLimitError, match="after retries"):
        client.get("courses")

    assert len(session.calls) == 3
    assert sleep_calls == [0.0, 0.0]


def test_transport_does_not_apply_a_second_implicit_429_retry_budget() -> None:
    client = CanvasClient(
        "https://canvas.example.ac.uk",
        "test-token",
        max_retries=3,
    )
    adapter = client._session.get_adapter("https://")
    retry_policy = adapter.max_retries

    assert retry_policy.respect_retry_after_header is False
    assert 429 not in retry_policy.status_forcelist
    assert retry_policy.is_retry("GET", 429, has_retry_after=True) is False
    client.close()


def test_retry_after_is_capped_to_prevent_indefinite_cli_pause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep_calls: list[float] = []
    monkeypatch.setattr("src.canvas_client.time.sleep", sleep_calls.append)
    session = FakeSession(
        FakeResponse(status_code=429, headers={"Retry-After": "86400"}),
        FakeResponse(payload={"ok": True}),
    )
    client = make_client(session, max_retries=1)

    assert client.get_json("courses") == {"ok": True}
    assert sleep_calls == [60.0]


def test_timeout_becomes_connection_error() -> None:
    session = FakeSession(requests.Timeout("simulated offline timeout"))
    client = make_client(session)

    with pytest.raises(CanvasConnectionError, match="timed out") as captured:
        client.get("courses")

    assert isinstance(captured.value.__cause__, requests.Timeout)
    assert len(session.calls) == 1


def test_get_json_rejects_invalid_json() -> None:
    session = FakeSession(FakeResponse(payload=ValueError("invalid JSON")))
    client = make_client(session)

    with pytest.raises(CanvasError, match="not valid JSON"):
        client.get_json("users/self/profile")


def test_paginated_response_rejects_invalid_json() -> None:
    session = FakeSession(FakeResponse(payload=ValueError("invalid JSON")))
    client = make_client(session)

    with pytest.raises(CanvasError, match="paginated response.*not valid JSON"):
        list(client.iter_paginated("courses"))


def test_teaching_content_methods_use_verified_read_only_collection_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession()
    client = make_client(session)
    calls: list[tuple[str, Any]] = []

    def fake_iter(endpoint: str, *, params: Any = None):  # type: ignore[no-untyped-def]
        calls.append((endpoint, params))
        return iter(())

    monkeypatch.setattr(client, "iter_paginated", fake_iter)

    assert client.get_modules(12) == []
    assert client.get_module_items(12, 34) == []
    assert client.get_pages(12) == []
    assert client.get_folders(12) == []
    assert client.get_files(12) == []
    assert client.get_assignments(12) == []
    assert client.get_assignment_groups(12) == []
    assert client.get_rubrics(12) == []
    assert client.get_classic_quizzes(12) == []
    assert client.get_classic_quiz_questions(12, 56) == []
    assert client.get_new_quizzes(12) == []
    assert client.get_new_quiz_items(12, 78) == []
    assert client.get_discussions(12) == []
    assert client.get_announcements(12) == []

    endpoints = [call[0] for call in calls]
    assert endpoints == [
        "courses/12/modules",
        "courses/12/modules/34/items",
        "courses/12/pages",
        "courses/12/folders",
        "courses/12/files",
        "courses/12/assignments",
        "courses/12/assignment_groups",
        "courses/12/rubrics",
        "courses/12/quizzes",
        "courses/12/quizzes/56/questions",
        "api/quiz/v1/courses/12/quizzes",
        "api/quiz/v1/courses/12/quizzes/78/items",
        "courses/12/discussion_topics",
        "announcements",
    ]
    serialized = repr(calls).casefold()
    assert "student_id" not in serialized
    assert "submission" not in serialized
    assert "assessment" not in serialized
    announcement_params = dict(calls[-1][1])
    assert announcement_params["context_codes[]"] == "course_12"
    assert announcement_params["start_date"] == "1970-01-01"


def test_page_identifier_is_encoded_as_one_path_segment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = make_client(FakeSession())
    called: list[str] = []

    def fake_json(endpoint: str, *, params: Any = None) -> dict[str, Any]:
        called.append(endpoint)
        return {"page_id": 1}

    monkeypatch.setattr(client, "get_json", fake_json)

    assert client.get_page(12, "week one/overview") == {"page_id": 1}
    assert called == ["courses/12/pages/week%20one%2Foverview"]
