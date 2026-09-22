"""Resilient streaming downloads and incremental file checks."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import logging
import os
from pathlib import Path
import time
from typing import Any, Iterator
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .canvas_client import CanvasClient, READ_ONLY_METHODS, RETRYABLE_STATUS_CODES
from .exceptions import (
    CanvasConnectionError,
    CanvasError,
    CanvasPermissionError,
    CanvasRateLimitError,
)
from .version import __version__


LOGGER = logging.getLogger(__name__)
DOWNLOAD_CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class DownloadResult:
    size: int
    sha256: str


class FileDownloader:
    """Download Canvas file URLs without leaking bearer credentials cross-origin."""

    def __init__(self, client: CanvasClient, *, max_retries: int = 3) -> None:
        self.client = client
        self._max_retries = max(0, max_retries)
        self._session = requests.Session()
        self._session.headers.update(
            {"User-Agent": f"Canvas-VLE-Manager/{__version__} (file download)"}
        )
        retry = Retry(
            total=max(0, max_retries),
            connect=max(0, max_retries),
            read=max(0, max_retries),
            status=max(0, max_retries),
            allowed_methods=READ_ONLY_METHODS,
            status_forcelist=RETRYABLE_STATUS_CODES,
            backoff_factor=0.5,
            # 429 is handled explicitly below so Retry-After can be capped.
            respect_retry_after_header=False,
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

    def __enter__(self) -> FileDownloader:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._session.close()

    def download(
        self,
        download_url: str,
        destination: Path,
        *,
        expected_size: int | None = None,
    ) -> DownloadResult:
        """Stream one URL into place atomically and return size/checksum."""

        prepared_url = self._prepare_download_url(download_url)
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.part")
        parsed = urlsplit(prepared_url)
        # Signed download URLs can contain opaque secrets in either the query or
        # path, so logs deliberately retain only the destination host.
        LOGGER.info("Downloading a Canvas file from host %s", parsed.hostname)

        response: requests.Response | None = None
        try:
            if self._same_canvas_origin(prepared_url):
                response = self.client.get(prepared_url, stream=True)
            else:
                try:
                    response = self._get_external(prepared_url)
                except requests.Timeout as exc:
                    raise CanvasConnectionError("The Canvas file download timed out.") from exc
                except requests.RequestException as exc:
                    raise CanvasConnectionError("Unable to download a Canvas file.") from exc
                self._raise_download_status(response)

            digest = hashlib.sha256()
            size = 0
            with temporary.open("wb") as stream:
                for chunk in self._iter_content(response):
                    stream.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
            if expected_size is not None and size != expected_size:
                raise CanvasError(
                    "The downloaded Canvas file size did not match its metadata."
                )
            os.replace(temporary, target)
            return DownloadResult(size=size, sha256=digest.hexdigest())
        except OSError as exc:
            raise CanvasError("Unable to write a downloaded Canvas file locally.") from exc
        finally:
            if response is not None:
                response.close()
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _iter_content(response: requests.Response) -> Iterator[bytes]:
        try:
            for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                if chunk:
                    yield chunk
        except requests.RequestException as exc:
            raise CanvasConnectionError("The Canvas file download was interrupted.") from exc

    def _same_canvas_origin(self, url: str) -> bool:
        return self.client._origin(url) == self.client._origin(self.client.base_url)

    def _get_external(self, url: str) -> requests.Response:
        attempt = 0
        while True:
            response = self._session.get(
                url,
                stream=True,
                timeout=self.client.timeout,
            )
            if response.status_code != 429:
                return response
            if attempt >= self._max_retries:
                return response
            delay = self._retry_delay(response, attempt)
            LOGGER.warning(
                "Canvas file host rate limit reached; retrying in %.1f seconds.",
                delay,
            )
            response.close()
            time.sleep(delay)
            attempt += 1

    @staticmethod
    def _retry_delay(response: requests.Response, attempt: int) -> float:
        try:
            return min(60.0, max(0.0, float(response.headers.get("Retry-After", ""))))
        except ValueError:
            return min(60.0, 0.5 * (2**attempt))

    @staticmethod
    def _prepare_download_url(url: str) -> str:
        value = str(url).strip()
        if not value or "\\" in value or any(
            character.isspace() or ord(character) < 32 or ord(character) == 127
            for character in value
        ):
            raise CanvasError("Canvas returned an invalid file download URL.")
        try:
            parsed = urlsplit(value)
            parsed.port
            if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
                raise ValueError
            if parsed.username is not None or parsed.password is not None:
                raise ValueError
            if parsed.scheme.lower() == "http" and parsed.hostname.lower() not in {
                "localhost",
                "127.0.0.1",
                "::1",
            }:
                raise ValueError
            prepared = requests.Request("GET", value).prepare().url
            if not prepared:
                raise ValueError
            return prepared
        except (requests.RequestException, TypeError, UnicodeError, ValueError):
            raise CanvasError("Canvas returned an invalid file download URL.") from None

    @staticmethod
    def _raise_download_status(response: requests.Response) -> None:
        if response.status_code == 429:
            raise CanvasRateLimitError(
                "The Canvas file server continued to rate-limit the download."
            )
        if response.status_code in {401, 403}:
            raise CanvasPermissionError("Canvas denied access to a course file download.")
        if response.status_code >= 400:
            raise CanvasError(
                f"The Canvas file server returned HTTP {response.status_code}."
            )


def sha256_file(path: Path) -> str:
    """Calculate a file's SHA-256 checksum without loading it into memory."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(DOWNLOAD_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_is_unchanged(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
    local_path: Path,
) -> bool:
    """Conservatively decide whether a Canvas file can skip re-download."""

    if not previous or not Path(local_path).is_file():
        return False
    if str(previous.get("id")) != str(current.get("id")):
        return False

    remote_size = _integer_or_none(current.get("size"))
    previous_size = _integer_or_none(previous.get("size"))
    if remote_size is None or previous_size != remote_size:
        return False
    try:
        if Path(local_path).stat().st_size != remote_size:
            return False
    except OSError:
        return False

    comparable_fields = ("updated_at", "modified_at")
    compared = False
    for field in comparable_fields:
        current_value = current.get(field)
        previous_value = previous.get(field)
        if current_value is not None:
            compared = True
            if previous_value != current_value:
                return False
    previous_checksum = previous.get("sha256")
    if not compared or not isinstance(previous_checksum, str) or not previous_checksum:
        return False
    try:
        return sha256_file(local_path) == previous_checksum
    except OSError:
        return False


def _integer_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


__all__ = ["DownloadResult", "FileDownloader", "file_is_unchanged", "sha256_file"]
