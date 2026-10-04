import pytest

from app.modules.model_inventory.domain.identity import build_canonical_key


def test_canonical_key_deterministic():
    assert build_canonical_key("OpenAI", "  gpt-4o ") == "openai|gpt-4o"


def test_canonical_key_empty_raises():
    with pytest.raises(ValueError):
        build_canonical_key("", "gpt-4o")
    with pytest.raises(ValueError):
        build_canonical_key("   ", "gpt-4o")
    with pytest.raises(ValueError):
        build_canonical_key("openai", "")
    with pytest.raises(ValueError):
        build_canonical_key("openai", "   ")


def test_canonical_key_case_preserved_for_native():
    assert build_canonical_key("OpenAI", "GPT-4") == "openai|GPT-4"
