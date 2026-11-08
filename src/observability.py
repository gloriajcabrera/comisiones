"""Logging estandar, estructurado y seguro para ejecucion Linux."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
import re
import sys
from typing import TextIO


_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|token|secret|authorization|api[_-]?key)"
    r"\s*[:=]\s*([^\s,;]+)"
)
_URL_CREDENTIALS = re.compile(r"(://)[^/@\s:]+:[^/@\s]+@")


def safe_error_message(error: BaseException | str) -> str:
    message = str(error).replace("\r", " ").replace("\n", " ")
    message = _SECRET_ASSIGNMENT.sub(r"\1=[REDACTED]", message)
    message = _URL_CREDENTIALS.sub(r"\1[REDACTED]@", message)
    return message[:2000]


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for name in ("event", "run_code", "status", "actor_user_id", "period"):
            value = getattr(record, name, None)
            if value is not None:
                payload[name] = value
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def configure_logging(stream: TextIO | None = None) -> None:
    level_name = os.getenv("COMMISSIONS_LOG_LEVEL", "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    handler = logging.StreamHandler(stream or sys.stdout)
    if os.getenv("COMMISSIONS_LOG_FORMAT", "json").strip().lower() == "json":
        handler.setFormatter(JsonLogFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s"
        ))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
