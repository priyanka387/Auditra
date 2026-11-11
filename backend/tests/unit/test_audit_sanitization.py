import json
from datetime import UTC, datetime
from uuid import uuid4

from app.modules.audit.sanitization import REDACTED, sanitize_state


def test_redacts_sensitive_keys():
    result = sanitize_state(
        {
            "password": "hunter2",
            "api_key": "abc",
            "auth_reference": "secret://vault/x",
            "owner_name": "AI Team",
        }
    )
    assert result["password"] == REDACTED
    assert result["api_key"] == REDACTED
    assert result["auth_reference"] == REDACTED
    assert result["owner_name"] == "AI Team"


def test_redacts_nested_and_list_items():
    result = sanitize_state(
        {
            "endpoints": [
                {"auth_type": "bearer", "secret": "s3cr3t"},
                {"auth_type": "none", "label": "public"},
            ],
            "outer": {"refresh_token": "rt-1", "name": "ok"},
        }
    )
    assert result["endpoints"][0]["secret"] == REDACTED
    assert result["endpoints"][1]["label"] == "public"
    assert result["outer"]["refresh_token"] == REDACTED
    assert result["outer"]["name"] == "ok"


def test_redacts_secret_values_in_strings():
    result = sanitize_state(
        {
            "header": "Bearer abc.def.ghi",
            "key": "sk-abcdef123456",
            "conn": "password=hunter2 extra",
        }
    )
    assert "abc.def.ghi" not in result["header"]
    assert "abcdef123456" not in result["key"]
    assert "hunter2" not in result["conn"]


def test_converts_datetime_and_uuid():
    uid = uuid4()
    when = datetime(2026, 10, 7, tzinfo=UTC)
    result = sanitize_state({"id": uid, "created_at": when, "count": 3})
    assert result["id"] == str(uid)
    assert result["created_at"] == when.isoformat()
    assert isinstance(result["created_at"], str)
    assert result["count"] == 3
    json.dumps(result)


def test_passthrough_primitives():
    assert sanitize_state(None) is None
    assert sanitize_state({"a": 1, "b": True, "c": [1, "two", None]}) == {
        "a": 1,
        "b": True,
        "c": [1, "two", None],
    }
    assert sanitize_state("plain") == "plain"
