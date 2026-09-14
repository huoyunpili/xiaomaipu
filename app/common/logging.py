import json
import logging
from contextvars import ContextVar
from datetime import UTC, datetime

request_id_context: ContextVar[str] = ContextVar("request_id", default="")


class SafeJsonFormatter(logging.Formatter):
    """Log only explicitly selected metadata, never arbitrary request/error text."""

    def format(self, record):
        return json.dumps(
            {
                "time": datetime.now(UTC).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "event": getattr(record, "event_code", "application_log"),
                "request_id": request_id_context.get(),
                "status": getattr(record, "status_code", None),
                "exception_type": record.exc_info[0].__name__
                if record.exc_info and record.exc_info[0]
                else None,
            },
            ensure_ascii=False,
        )
