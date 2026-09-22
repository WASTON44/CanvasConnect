"""Offline behavioural tests for the user-facing command-line scripts."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIRECTORY = PROJECT_ROOT / "scripts"


def _load_script(name: str, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Import a script without running its ``__main__`` entry point."""

    monkeypatch.syspath_prepend(str(SCRIPTS_DIRECTORY))
    path = SCRIPTS_DIRECTORY / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"test_{name}_script", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("script_name", "script_args"),
    [
        ("list_courses", []),
        ("list_modules", ["--course", "123"]),
        ("backup_courses", []),
    ],
)
def test_commands_without_configuration_are_friendly_and_have_no_traceback(
    script_name: str,
    script_args: list[str],
) -> None:
    environment = os.environ.copy()
    # Explicit empty values take precedence over any developer .env file, making
    # this subprocess deterministic while retaining the environment Python needs.
    environment["CANVAS_BASE_URL"] = ""
    environment["CANVAS_API_TOKEN"] = ""
    environment["PYTHONIOENCODING"] = "utf-8"

    completed = subprocess.run(
        [sys.executable, str(SCRIPTS_DIRECTORY / f"{script_name}.py"), *script_args],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    output = completed.stdout + completed.stderr

    assert completed.returncode == 2
    assert "Canvas VLE Manager has not been configured." in output
    assert "python scripts/setup.py" in output
    assert "Traceback" not in output
    assert "Canvas API token is required" not in output


def test_setup_reads_token_with_getpass_and_never_prints_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    setup_script = _load_script("setup", monkeypatch)
    token = "highly-sensitive-test-token"
    prompts: list[str] = []

    def hidden_input(prompt: str) -> str:
        prompts.append(prompt)
        return f"  {token}  "

    monkeypatch.setattr(setup_script, "getpass", hidden_input)

    assert setup_script._prompt_token() == token
    captured = capsys.readouterr()
    assert prompts == ["Canvas API Token:\n> "]
    assert token not in captured.out
    assert token not in captured.err


def test_setup_tests_effective_environment_overrides_after_save(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    setup_script = _load_script("setup", monkeypatch)
    entered = setup_script.CanvasConfig(
        "https://entered.example.ac.uk", "entered-token"
    )
    effective = setup_script.CanvasConfig(
        "https://environment.example.ac.uk", "environment-token"
    )
    tested: list[object] = []

    monkeypatch.setenv("CANVAS_BASE_URL", effective.base_url)
    monkeypatch.setattr(setup_script, "save_config", lambda config: PROJECT_ROOT / ".env")
    monkeypatch.setattr(setup_script, "load_config", lambda: effective)
    monkeypatch.setattr(
        setup_script,
        "_test_connection",
        lambda config: tested.append(config) or True,
    )

    assert setup_script._save_and_test(entered) is True

    output = capsys.readouterr().out
    assert tested == [effective]
    assert "CANVAS_BASE_URL" in output
    assert "take precedence" in output
    assert "entered-token" not in output
    assert "environment-token" not in output


def test_list_courses_can_print_a_sparse_record(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    list_courses_script = _load_script("list_courses", monkeypatch)

    list_courses_script._print_course(1, {})

    output = capsys.readouterr().out
    assert "[1]" in output
    assert "Unnamed course" in output
    assert "Not available" in output
    assert "Traceback" not in output


def test_course_commands_accept_both_favorite_spellings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    list_script = _load_script("list_courses", monkeypatch)
    backup_script = _load_script("backup_courses", monkeypatch)

    assert list_script.build_parser().parse_args(["--favorite"]).favorite is True
    assert list_script.build_parser().parse_args(["--favourite"]).favorite is True
    assert backup_script.build_parser().parse_args(["--favorite"]).favorite is True
    assert backup_script.build_parser().parse_args(["--favourite"]).favorite is True
