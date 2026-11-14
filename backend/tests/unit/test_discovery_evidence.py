"""Unit tests: Discovery Evidence (Sub-feature 9)."""

from app.modules.asset_discovery.enums import EvidenceType


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
