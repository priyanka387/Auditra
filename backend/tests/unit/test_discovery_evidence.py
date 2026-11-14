"""Unit tests: Discovery Evidence (Sub-feature 9)."""

from uuid import uuid4

import pytest

from app.modules.asset_discovery.connectors.base import (
    MODEL_REFERENCE,
    PROVIDER_REFERENCE,
    DiscoveryObservation,
)
from app.modules.asset_discovery.enums import EvidenceType
from app.modules.asset_discovery.errors import InvalidDiscoveryEvidenceError
from app.modules.asset_discovery.evidence import EvidenceDraft, build_evidence


def test_evidence_type_vocabulary_matches_observation_types():
    assert [member.value for member in EvidenceType] == [
        "provider_reference",
        "framework_reference",
        "model_reference",
        "agent_reference",
        "workflow_reference",
    ]


def test_evidence_type_values_equal_connector_observation_constants():
    from app.modules.asset_discovery.connectors import base

    assert EvidenceType.PROVIDER_REFERENCE == base.PROVIDER_REFERENCE
    assert EvidenceType.FRAMEWORK_REFERENCE == base.FRAMEWORK_REFERENCE
    assert EvidenceType.MODEL_REFERENCE == base.MODEL_REFERENCE
    assert EvidenceType.AGENT_REFERENCE == base.AGENT_REFERENCE
    assert EvidenceType.WORKFLOW_REFERENCE == base.WORKFLOW_REFERENCE


def _observation(**overrides) -> DiscoveryObservation:
    fields = {
        "source_id": uuid4(),
        "run_id": uuid4(),
        "observation_type": PROVIDER_REFERENCE,
        "name": "openai",
        "provider": "openai",
        "file_path": "app/main.py",
        "line_number": 1,
        "detection_rule": "provider.openai.import",
        "metadata": {},
    }
    fields.update(overrides)
    return DiscoveryObservation(**fields)


def test_build_evidence_returns_structured_fields_only():
    draft = build_evidence(_observation(metadata={"framework": "langchain"}))
    assert isinstance(draft, EvidenceDraft)
    assert draft.evidence_type == "provider_reference"
    assert draft.detection_rule == "provider.openai.import"
    assert draft.file_path == "app/main.py"
    assert draft.line_number == 1
    assert draft.metadata == {"framework": "langchain", "provider": "openai"}


def test_build_evidence_maps_model_reference_type():
    draft = build_evidence(
        _observation(
            observation_type=MODEL_REFERENCE,
            name="gpt-4o",
            provider=None,
            detection_rule="model.assignment",
            metadata={"key": "model"},
        )
    )
    assert draft.evidence_type == "model_reference"
    assert draft.metadata == {"key": "model"}  # no provider key when unknown


def test_build_evidence_rejects_unknown_observation_type():
    with pytest.raises(InvalidDiscoveryEvidenceError, match="unsupported evidence type"):
        build_evidence(_observation(observation_type="mystery_signal"))


def test_build_evidence_rejects_absolute_path():
    with pytest.raises(InvalidDiscoveryEvidenceError, match="relative to the source root"):
        build_evidence(_observation(file_path="/etc/passwd"))


def test_build_evidence_rejects_invalid_line_number():
    for bad in (0, -1, True, 10_000_001):
        with pytest.raises(InvalidDiscoveryEvidenceError, match="line_number"):
            build_evidence(_observation(line_number=bad))


def test_build_evidence_rejects_secret_shaped_metadata_key():
    with pytest.raises(InvalidDiscoveryEvidenceError, match="metadata"):
        build_evidence(_observation(metadata={"api_key": "value"}))


def test_build_evidence_rejects_empty_or_overlong_detection_rule():
    with pytest.raises(InvalidDiscoveryEvidenceError, match="detection_rule"):
        build_evidence(_observation(detection_rule=""))
    with pytest.raises(InvalidDiscoveryEvidenceError, match="detection_rule"):
        build_evidence(_observation(detection_rule="x" * 256))
