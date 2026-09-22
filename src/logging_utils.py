"""Safe file logging for local CLI commands."""

from __future__ import annotations

from datetime import datetime
import logging
from pathlib import Path
from typing import Iterable

from .config import project_root


class _RedactingFilter(logging.Filter):
    """Remove configured secret strings before a record reaches a log file."""

    def __init__(self, secrets: Iterable[str]) -> None:
        super().__init__()
        self._secrets = tuple(secret for secret in secrets if secret)

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._secrets:
            return True
        rendered = record.getMessage()
        for secret in self._secrets:
            rendered = rendered.replace(secret, "<redacted>")
        record.msg = rendered
        record.args = ()
        return True


def configure_file_logging(
    command_name: str,
    *,
    secrets: Iterable[str] = (),
    root: Path | None = None,
) -> Path:
    """Create a timestamped local log and return its path."""

    log_directory = (root or project_root()) / "logs"
    log_directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    log_path = log_directory / f"{command_name}_{timestamp}.log"

    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setLevel(logging.INFO)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    handler.addFilter(_RedactingFilter(secrets))

    application_logger = logging.getLogger("src")
    application_logger.setLevel(logging.INFO)
    application_logger.addHandler(handler)
    application_logger.propagate = False
    return log_path

