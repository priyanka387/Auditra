import re

ERROR_TYPE_MAX = 255
ERROR_CODE_MAX = 100
ERROR_MESSAGE_MAX = 2000

_REDACTIONS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)\b(bearer)\s+\S+"), r"\1 REDACTED"),
    (
        re.compile(r"(?i)\b(api[_-]?key|token|secret|password|authorization)\s*[=:]\s*\S+"),
        r"\1=REDACTED",
    ),
    (re.compile(r"(?i)\bsk-[A-Za-z0-9_\-]{8,}"), "REDACTED"),
]
_STACK_TRACE_MARKER = "Traceback (most recent call last)"


def sanitize_error_message(raw: str | None) -> str | None:
    if raw is None:
        return None
    message = raw
    marker_at = message.find(_STACK_TRACE_MARKER)
    if marker_at != -1:
        message = message[:marker_at].rstrip()
    for pattern, replacement in _REDACTIONS:
        message = pattern.sub(replacement, message)
    return message[:ERROR_MESSAGE_MAX]


def truncate(value: str | None, limit: int) -> str | None:
    return value[:limit] if value is not None else None
