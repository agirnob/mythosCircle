"""JSON-lines logging setup (spec-1.7).

The operator config's ``[world].log_file`` (env override
``MYTHOSCIRCLE_LOG_FILE``) gets a JSON-lines file handler; console
logging stays. Called first in ``create_app`` so every handler — incl.
``handle_unexpected_error``'s ``logger.exception`` for the generic 500 —
writes structured lines a log shipper can read.
"""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.settings import configured_log_file


class _JsonLineFormatter(logging.Formatter):
    """One JSON object per line: ts, level, logger, message, exc."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload) + "\n"


def setup_logging() -> None:
    """Attach the JSON-lines file handler when a log file is configured."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    log_file = configured_log_file()
    if not log_file:
        return
    # One JSON-lines handler only — setup_logging is called on every
    # create_app and a duplicate would double-write every line. (The
    # guard is on OUR formatter, not any FileHandler: pytest's logging
    # plugin may attach its own file handler to the root.)
    if any(
        isinstance(h, logging.FileHandler) and isinstance(h.formatter, _JsonLineFormatter)
        for h in root.handlers
    ):
        return
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_file)
    handler.setFormatter(_JsonLineFormatter())
    root.addHandler(handler)
