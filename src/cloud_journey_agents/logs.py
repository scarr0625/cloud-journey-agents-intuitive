"""Structured JSON logging for the shared agent infrastructure.

Each record includes a timestamp, severity, logger name, and message.
Callers attach operation-specific values through journey_fields so batch
and conversation events can be correlated without parsing message text.

The configured handler writes to stderr, leaving batch stdout available
for its single JSON result. Repeated configuration reuses an existing
handler on the shared logger instead of emitting each record twice.
"""

from datetime import datetime, timezone
import json
import logging


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "severity": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update(getattr(record, "journey_fields", {}))
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    logger = logging.getLogger("cloud_journey_agents")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
