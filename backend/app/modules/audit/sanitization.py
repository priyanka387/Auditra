import re
from datetime import datetime
from typing import Any
from uuid import UUID

REDACTED = "REDACTED"

_SENSITIVE_KEY = re.compile(
    r"(?i)(password|passwd|secret|token|api[_-]?key|credential|private[_-]?key"
    r"|authorization|auth[_-]?reference|bearer)"
)

_VALUE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)\bbearer\s+\S+"), "Bearer REDACTED"),
    (
        re.compile(r"(?i)\b(api[_-]?key|token|secret|password)\s*[=:]\s*\S+"),
        r"\1=REDACTED",
    ),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"), "REDACTED"),
]


def sanitize_state(value: Any) -> Any:
    """Return a JSON-safe copy of an audit state payload with secrets removed.

    Central sanitizer for all audit before/after/metadata payloads (spec §17):
    sensitive keys are kept but redacted, secret-like string values are
    scrubbed, and non-JSON types (datetime, UUID) are converted.
    """
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _sanitize_str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {
            key: REDACTED
            if isinstance(key, str) and _SENSITIVE_KEY.search(key)
            else sanitize_state(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [sanitize_state(item) for item in value]
    return str(value)


def _sanitize_str(value: str) -> str:
    for pattern, replacement in _VALUE_PATTERNS:
        value = pattern.sub(replacement, value)
    return value
