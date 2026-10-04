import pytest

from app.modules.model_inventory.domain.identity import (
    VERSION_IDENTITY_TYPES,
    build_canonical_version_key,
)


def test_identity_types_match_spec():
    assert VERSION_IDENTITY_TYPES == {
        "native",
        "revision",
        "release",
        "checkpoint",
        "label",
        "opaque",
    }


def test_canonical_key_uses_native_id_when_present():
    assert build_canonical_version_key("native", "2025-04-14", "2025-04-14") == "native:2025-04-14"
    assert build_canonical_version_key("revision", "main", "abc123") == "revision:abc123"
    assert build_canonical_version_key("release", "v2.1.0", "release-210") == "release:release-210"


def test_canonical_key_falls_back_to_version_label():
    assert build_canonical_version_key("label", "v1.2.0", None) == "label:v1.2.0"
    assert build_canonical_version_key("release", "v2.1.0", "") == "release:v2.1.0"


def test_identity_type_normalized_identifiers_case_preserved():
    assert build_canonical_version_key("  Native ", "2025-04-14", None) == "native:2025-04-14"
    assert build_canonical_version_key("REVISION", " main ", "  ABC123 ") == "revision:ABC123"


def test_empty_version_label_rejected():
    with pytest.raises(ValueError, match="version_label"):
        build_canonical_version_key("label", "   ", None)


def test_unknown_identity_type_rejected():
    with pytest.raises(ValueError, match="identity_type"):
        build_canonical_version_key("semver", "v1", None)


def test_repeated_normalization_is_idempotent():
    key = build_canonical_version_key("NATIVE", " 2025-04-14 ", None)
    assert key == "native:2025-04-14"
    assert build_canonical_version_key("native", "2025-04-14", None) == key
