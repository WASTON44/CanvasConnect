"""Local, per-user Canvas configuration.

Configuration is read from ``.env`` at the project root.  Process environment
variables take precedence, which makes the module usable in shells and automated
tests without writing credentials to disk.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import os
from pathlib import Path
import tempfile
from urllib.parse import SplitResult, urlsplit, urlunsplit

from .exceptions import CanvasConfigurationError

CANVAS_BASE_URL = "CANVAS_BASE_URL"
CANVAS_API_TOKEN = "CANVAS_API_TOKEN"
_CONFIG_KEYS = (CANVAS_BASE_URL, CANVAS_API_TOKEN)

NOT_CONFIGURED_MESSAGE = (
    "Canvas VLE Manager has not been configured.\n\n"
    "Run:\n\n"
    "    python scripts/setup.py\n\n"
    "to connect your Canvas account."
)


def project_root() -> Path:
    """Return the application project root, independently of the current directory."""

    return Path(__file__).resolve().parent.parent


def default_env_path() -> Path:
    """Return the default location of the private local configuration file."""

    return project_root() / ".env"


def normalize_canvas_url(raw_url: str) -> str:
    """Validate and normalise a user-supplied Canvas installation URL.

    The returned URL never has a trailing slash or ``/api/v1`` suffix.  Query
    strings, fragments, and embedded credentials are rejected so secrets cannot
    accidentally become part of generated API URLs or saved indexes.
    """

    if not isinstance(raw_url, str) or not raw_url.strip():
        raise CanvasConfigurationError("A Canvas URL is required.")

    candidate = raw_url.strip()
    if "\r" in candidate or "\n" in candidate:
        raise CanvasConfigurationError("The Canvas URL is invalid.")

    try:
        parsed = urlsplit(candidate)
        # Accessing ``port`` performs validation that urlsplit otherwise defers.
        port = parsed.port
    except (TypeError, ValueError):
        raise CanvasConfigurationError("The Canvas URL is invalid.") from None

    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or any(character.isspace() for character in parsed.hostname)
    ):
        raise CanvasConfigurationError(
            "The Canvas URL must be a complete http:// or https:// address."
        )
    if parsed.scheme.lower() == "http" and parsed.hostname.lower() not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise CanvasConfigurationError(
            "The Canvas URL must use https:// (http:// is allowed only for "
            "local development)."
        )
    if parsed.username is not None or parsed.password is not None:
        raise CanvasConfigurationError(
            "The Canvas URL must not contain embedded credentials."
        )
    if parsed.query or parsed.fragment:
        raise CanvasConfigurationError(
            "The Canvas URL must not contain a query string or fragment."
        )

    path = parsed.path.rstrip("/")
    if path.lower().endswith("/api/v1"):
        path = path[: -len("/api/v1")].rstrip("/")

    host = parsed.hostname.lower()
    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc = f"{netloc}:{port}"

    normalised = SplitResult(
        scheme=parsed.scheme.lower(),
        netloc=netloc,
        path=path,
        query="",
        fragment="",
    )
    return urlunsplit(normalised)


@dataclass(frozen=True)
class CanvasConfig:
    """Validated Canvas connection settings.

    The token remains available to the API client through ``api_token`` but is
    deliberately omitted from object representations.
    """

    base_url: str
    api_token: str = field(repr=False)

    def __post_init__(self) -> None:
        normalised_url = normalize_canvas_url(self.base_url)
        if not isinstance(self.api_token, str) or not self.api_token.strip():
            raise CanvasConfigurationError("A Canvas API token is required.")
        if "\r" in self.api_token or "\n" in self.api_token:
            raise CanvasConfigurationError("The Canvas API token is invalid.")

        object.__setattr__(self, "base_url", normalised_url)
        object.__setattr__(self, "api_token", self.api_token.strip())

    @property
    def api_base_url(self) -> str:
        """Return the Canvas REST API root derived from the installation URL."""

        return f"{self.base_url}/api/v1"

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(base_url={self.base_url!r}, "
            "api_token=<redacted>)"
        )


def _parse_quoted_value(value: str) -> str:
    quote = value[0]
    result: list[str] = []
    escaped = False

    for index, character in enumerate(value[1:], start=1):
        if escaped:
            replacements = {"n": "\n", "r": "\r", "t": "\t"}
            result.append(replacements.get(character, character))
            escaped = False
            continue
        if quote == '"' and character == "\\":
            escaped = True
            continue
        if character == quote:
            remainder = value[index + 1 :].strip()
            if remainder and not remainder.startswith("#"):
                raise CanvasConfigurationError(
                    "The local Canvas configuration file is invalid."
                )
            return "".join(result)
        result.append(character)

    raise CanvasConfigurationError("The local Canvas configuration file is invalid.")


def _parse_env_value(raw_value: str) -> str:
    value = raw_value.strip()
    if value.startswith(("'", '"')):
        return _parse_quoted_value(value)

    # Match common .env behaviour: a # starts a comment only when preceded by
    # whitespace.  This preserves # characters that are part of an opaque token.
    for index, character in enumerate(value):
        if character == "#" and index > 0 and value[index - 1].isspace():
            return value[:index].rstrip()
    return value


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}

    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        raise CanvasConfigurationError(
            "Unable to read the local Canvas configuration."
        ) from None

    values: dict[str, str] = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue

        key, raw_value = line.split("=", 1)
        key = key.strip()
        if key in _CONFIG_KEYS:
            values[key] = _parse_env_value(raw_value)
    return values


def load_config(
    path: str | os.PathLike[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> CanvasConfig:
    """Load and validate local configuration.

    Values explicitly present in ``environ`` take precedence over values in the
    file.  Passing an empty mapping is useful when a caller wants file-only
    behaviour; omitting it uses the process environment.
    """

    env_path = Path(path) if path is not None else default_env_path()
    file_values = _read_env_file(env_path)
    environment = os.environ if environ is None else environ

    values: dict[str, str | None] = {}
    for key in _CONFIG_KEYS:
        values[key] = environment[key] if key in environment else file_values.get(key)

    base_url = values[CANVAS_BASE_URL]
    api_token = values[CANVAS_API_TOKEN]
    if not isinstance(base_url, str) or not base_url.strip():
        raise CanvasConfigurationError(NOT_CONFIGURED_MESSAGE)
    if not isinstance(api_token, str) or not api_token.strip():
        raise CanvasConfigurationError(NOT_CONFIGURED_MESSAGE)

    return CanvasConfig(base_url=base_url, api_token=api_token)


def _encode_env_value(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def save_config(
    config: CanvasConfig,
    path: str | os.PathLike[str] | None = None,
) -> Path:
    """Atomically save validated credentials to the local ``.env`` file."""

    if not isinstance(config, CanvasConfig):
        raise CanvasConfigurationError("Invalid Canvas configuration.")

    env_path = Path(path) if path is not None else default_env_path()
    payload = (
        f"{CANVAS_BASE_URL}={_encode_env_value(config.base_url)}\n"
        f"{CANVAS_API_TOKEN}={_encode_env_value(config.api_token)}\n"
    )
    temporary_path: Path | None = None

    try:
        env_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=env_path.parent,
            # For the default .env this produces .env.<random>.tmp, which is
            # covered by the repository's .env.* ignore rule even if the
            # process is terminated between writing and replacement.
            prefix=f"{env_path.name}.",
            suffix=".tmp",
            text=True,
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
        os.replace(temporary_path, env_path)
    except OSError:
        raise CanvasConfigurationError(
            "Unable to save the local Canvas configuration."
        ) from None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass

    return env_path


def reset_config(path: str | os.PathLike[str] | None = None) -> bool:
    """Delete the local configuration file, returning whether it existed."""

    env_path = Path(path) if path is not None else default_env_path()
    try:
        env_path.unlink()
    except FileNotFoundError:
        return False
    except OSError:
        raise CanvasConfigurationError(
            "Unable to reset the local Canvas configuration."
        ) from None
    return True


def config_exists(
    path: str | os.PathLike[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Return whether a complete, valid configuration is available."""

    try:
        load_config(path, environ=environ)
    except CanvasConfigurationError:
        return False
    return True


__all__ = [
    "CANVAS_API_TOKEN",
    "CANVAS_BASE_URL",
    "CanvasConfig",
    "NOT_CONFIGURED_MESSAGE",
    "config_exists",
    "default_env_path",
    "load_config",
    "normalize_canvas_url",
    "project_root",
    "reset_config",
    "save_config",
]
