"""Structured logging configuration."""

import json
import logging
import sys
from typing import Any

from code_kg.config import Settings


class JsonFormatter(logging.Formatter):
    """JSON formatter for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        log_obj: dict[str, Any] = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)

        if hasattr(record, "extra") and record.extra:  # type: ignore
            log_obj.update(record.extra)  # type: ignore

        return json.dumps(log_obj)


def configure_logging(settings: Settings) -> None:
    """Configure logging based on settings."""
    root_logger = logging.getLogger()
    root_logger.setLevel(settings.log_level)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root_logger.addHandler(handler)

    logging.getLogger("code_kg").setLevel(settings.log_level)
