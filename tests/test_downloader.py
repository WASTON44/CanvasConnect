"""Offline tests for safe, atomic Canvas file downloads."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
import requests

from src.canvas_client import CanvasClient
from src.downloader import FileDownloader, file_is_unchanged, sha256_file
from src.exceptions import CanvasConnectionError, CanvasError, CanvasPermissionError


class FakeResponse:
    def __init__(self, status_code: int = 200, chunks: tuple[bytes, ...] = ()) -> None:
        self.status_code = status_code
        self.headers: dict[str, str] = {}
        self._chunks = chunks
        self.closed = False

    def iter_content(self, chunk_size: int):  # type: ignore[no-untyped-def]
        assert chunk_size > 0
        yield from self._chunks

    def close(self) -> None:
        self.closed = True


class FakeClient:
    base_url = "https://canvas.example.edu"
    timeout = (1.0, 2.0)

    def __init__(self, response: FakeResponse | None = None) -> None:
        self.response = response
        self.requested: list[str] = []

    @staticmethod
    def _origin(url: str):  # type: ignore[no-untyped-def]
        return CanvasClient._origin(url)

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.requested.append(url)
        assert kwargs == {"stream": True}
        assert self.response is not None
        return self.response


def test_successful_cross_origin_download_is_atomic_and_has_checksum(
    tmp_path: Path,
) -> None:
    client = FakeClient()
    downloader = FileDownloader(client)  # type: ignore[arg-type]
    response = FakeResponse(chunks=(b"canvas ", b"content"))
    calls = []

    def fake_get(url: str, **kwargs: Any) -> FakeResponse:
        calls.append((url, kwargs, dict(downloader._session.headers)))
        return response

    downloader._session.get = fake_get  # type: ignore[method-assign]
    destination = tmp_path / "nested" / "notes.pdf"

    result = downloader.download("https://files.example.edu/download?token=secret", destination)

    expected = b"canvas content"
    assert destination.read_bytes() == expected
    assert result.size == len(expected)
    assert result.sha256 == hashlib.sha256(expected).hexdigest()
    assert response.closed is True
    assert not (destination.parent / ".notes.pdf.part").exists()
    assert "Authorization" not in calls[0][2]
    downloader.close()


def test_same_origin_download_uses_authenticated_canvas_client(tmp_path: Path) -> None:
    response = FakeResponse(chunks=(b"same origin",))
    client = FakeClient(response)
    downloader = FileDownloader(client)  # type: ignore[arg-type]

    downloader.download("https://canvas.example.edu/files/12/download", tmp_path / "x")

    assert client.requested == ["https://canvas.example.edu/files/12/download"]


def test_failed_download_removes_partial_file(tmp_path: Path) -> None:
    downloader = FileDownloader(FakeClient())  # type: ignore[arg-type]
    response = FakeResponse(status_code=403)
    downloader._session.get = lambda *args, **kwargs: response  # type: ignore[method-assign]
    destination = tmp_path / "denied.pdf"

    with pytest.raises(CanvasPermissionError):
        downloader.download("https://files.example.edu/denied", destination)

    assert not destination.exists()
    assert not (tmp_path / ".denied.pdf.part").exists()
    assert response.closed is True


def test_size_mismatch_preserves_an_existing_good_file(tmp_path: Path) -> None:
    downloader = FileDownloader(FakeClient())  # type: ignore[arg-type]
    response = FakeResponse(chunks=(b"short",))
    downloader._session.get = lambda *args, **kwargs: response  # type: ignore[method-assign]
    destination = tmp_path / "lecture.pdf"
    destination.write_bytes(b"previous-good-copy")

    with pytest.raises(CanvasError, match="size did not match"):
        downloader.download(
            "https://files.example.edu/lecture.pdf",
            destination,
            expected_size=100,
        )

    assert destination.read_bytes() == b"previous-good-copy"
    assert not (tmp_path / ".lecture.pdf.part").exists()


def test_timeout_has_friendly_exception_and_no_partial_file(tmp_path: Path) -> None:
    downloader = FileDownloader(FakeClient())  # type: ignore[arg-type]

    def timeout(*args: Any, **kwargs: Any) -> FakeResponse:
        raise requests.Timeout("do not expose a URL")

    downloader._session.get = timeout  # type: ignore[method-assign]
    destination = tmp_path / "timeout.pdf"

    with pytest.raises(CanvasConnectionError, match="timed out"):
        downloader.download("https://files.example.edu/file", destination)

    assert not destination.exists()


def test_external_rate_limit_honours_capped_retry_after(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    downloader = FileDownloader(FakeClient(), max_retries=1)  # type: ignore[arg-type]
    limited = FakeResponse(status_code=429)
    limited.headers["Retry-After"] = "9999"
    success = FakeResponse(chunks=(b"ok",))
    responses = iter([limited, success])
    delays: list[float] = []
    downloader._session.get = lambda *args, **kwargs: next(responses)  # type: ignore[method-assign]
    monkeypatch.setattr("src.downloader.time.sleep", delays.append)

    result = downloader.download("https://files.example.edu/file", tmp_path / "file")

    assert result.size == 2
    assert delays == [60.0]
    assert limited.closed is True


@pytest.mark.parametrize(
    "url",
    [
        "http://files.example.edu/file",
        "https://user:password@files.example.edu/file",
        "https://files.example.edu\\@attacker.example/file",
        "javascript:alert(1)",
    ],
)
def test_invalid_or_unsafe_download_urls_are_rejected(url: str, tmp_path: Path) -> None:
    downloader = FileDownloader(FakeClient())  # type: ignore[arg-type]
    with pytest.raises(CanvasError, match="invalid"):
        downloader.download(url, tmp_path / "file")


def test_sha256_and_incremental_comparison(tmp_path: Path) -> None:
    local = tmp_path / "lecture.pdf"
    local.write_bytes(b"abc")
    checksum = sha256_file(local)
    previous = {
        "id": 9,
        "size": 3,
        "updated_at": "2026-01-01T00:00:00Z",
        "sha256": checksum,
    }
    current = {
        "id": 9,
        "size": 3,
        "updated_at": "2026-01-01T00:00:00Z",
    }

    assert checksum == hashlib.sha256(b"abc").hexdigest()
    assert file_is_unchanged(previous, current, local) is True
    local.write_bytes(b"xyz")
    assert file_is_unchanged(previous, current, local) is False
    local.write_bytes(b"abc")
    current["updated_at"] = "2026-01-02T00:00:00Z"
    assert file_is_unchanged(previous, current, local) is False
