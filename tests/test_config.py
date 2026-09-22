"""Tests for local Canvas configuration handling."""

from pathlib import Path

import pytest

from src.config import (
    CanvasConfig,
    config_exists,
    load_config,
    normalize_canvas_url,
    reset_config,
    save_config,
)
from src.exceptions import CanvasConfigurationError


@pytest.mark.parametrize(
    ("raw_url", "expected"),
    [
        ("https://canvas.example.ac.uk", "https://canvas.example.ac.uk"),
        (" https://canvas.example.ac.uk/// ", "https://canvas.example.ac.uk"),
        ("https://canvas.example.ac.uk/api/v1/", "https://canvas.example.ac.uk"),
        ("HTTPS://CANVAS.EXAMPLE.AC.UK/", "https://canvas.example.ac.uk"),
        (
            "https://example.ac.uk/canvas/api/v1",
            "https://example.ac.uk/canvas",
        ),
        ("http://localhost:8000/", "http://localhost:8000"),
    ],
)
def test_normalize_canvas_url(raw_url: str, expected: str) -> None:
    assert normalize_canvas_url(raw_url) == expected


@pytest.mark.parametrize(
    "raw_url",
    [
        "",
        "canvas.example.ac.uk",
        "ftp://canvas.example.ac.uk",
        "http://canvas.example.ac.uk",
        "https://user:password@canvas.example.ac.uk",
        "https://canvas.example.ac.uk/?access_token=do-not-store-this",
        "https://canvas.example.ac.uk/#fragment",
    ],
)
def test_normalize_canvas_url_rejects_unsafe_or_invalid_values(raw_url: str) -> None:
    with pytest.raises(CanvasConfigurationError):
        normalize_canvas_url(raw_url)


def test_load_config_from_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        'CANVAS_BASE_URL="https://canvas.example.ac.uk/"\n'
        'CANVAS_API_TOKEN="local-test-token"\n',
        encoding="utf-8",
    )

    config = load_config(env_file, environ={})

    assert config.base_url == "https://canvas.example.ac.uk"
    assert config.api_base_url == "https://canvas.example.ac.uk/api/v1"
    assert config.api_token == "local-test-token"


def test_environment_overrides_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "CANVAS_BASE_URL=https://file.example.ac.uk\n"
        "CANVAS_API_TOKEN=file-token\n",
        encoding="utf-8",
    )

    config = load_config(
        env_file,
        environ={
            "CANVAS_BASE_URL": "https://environment.example.ac.uk/",
            "CANVAS_API_TOKEN": "environment-token",
        },
    )

    assert config.base_url == "https://environment.example.ac.uk"
    assert config.api_token == "environment-token"


@pytest.mark.parametrize(
    "contents",
    [
        "",
        "CANVAS_BASE_URL=https://canvas.example.ac.uk\n",
        "CANVAS_API_TOKEN=test-token\n",
        "CANVAS_BASE_URL=\nCANVAS_API_TOKEN=\n",
    ],
)
def test_missing_configuration_raises_helpful_error(
    tmp_path: Path, contents: str
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(contents, encoding="utf-8")

    with pytest.raises(CanvasConfigurationError) as captured:
        load_config(env_file, environ={})

    message = str(captured.value)
    assert "has not been configured" in message
    assert "python scripts/setup.py" in message
    assert config_exists(env_file, environ={}) is False


def test_missing_env_file_raises_configuration_error(tmp_path: Path) -> None:
    with pytest.raises(CanvasConfigurationError):
        load_config(tmp_path / "does-not-exist.env", environ={})


def test_config_repr_never_exposes_token() -> None:
    token = "super-secret-test-token"
    config = CanvasConfig("https://canvas.example.ac.uk", token)

    assert token not in repr(config)
    assert "<redacted>" in repr(config)


def test_invalid_token_is_not_exposed_in_error() -> None:
    token = "secret-prefix\nsecret-suffix"

    with pytest.raises(CanvasConfigurationError) as captured:
        CanvasConfig("https://canvas.example.ac.uk", token)

    assert token not in str(captured.value)
    assert token not in repr(captured.value)


def test_save_load_and_reset_round_trip(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    original = CanvasConfig(
        "https://canvas.example.ac.uk/",
        'opaque-token-with-#-and-"quotes"',
    )

    assert save_config(original, env_file) == env_file
    assert config_exists(env_file, environ={}) is True
    assert load_config(env_file, environ={}) == original
    assert reset_config(env_file) is True
    assert reset_config(env_file) is False


def test_interrupted_save_removes_git_sensitive_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    config = CanvasConfig("https://canvas.example.ac.uk", "private-token")

    def interrupt_replace(source: Path, destination: Path) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("src.config.os.replace", interrupt_replace)

    with pytest.raises(KeyboardInterrupt):
        save_config(config, env_file)

    assert not env_file.exists()
    assert list(tmp_path.glob(".env.*.tmp")) == []
    assert list(tmp_path.glob("..env.*.tmp")) == []
