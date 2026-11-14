"""Discovery Evidence domain (Sub-feature 9).

Evidence answers *what was observed and where*; classification answers
*what it represents* (no classification logic lives here). Evidence rows
carry structured fields only — never raw source content, snippets or
credentials.

Pure functions with no database access: persistence is orchestrated by
``DiscoveredAssetService`` together with ``repository.record_evidence``.
"""

from dataclasses import dataclass
from typing import Any

from app.modules.asset_discovery.catalog import MAX_LINE_NUMBER, validate_file_path
from app.modules.asset_discovery.connectors.base import DiscoveryObservation
from app.modules.asset_discovery.enums import EvidenceType
from app.modules.asset_discovery.errors import (
    InvalidDiscoveredAssetError,
    InvalidDiscoveryEvidenceError,
)
from app.modules.model_inventory.schemas.model import validate_metadata

MAX_DETECTION_RULE_LENGTH = 255


@dataclass(frozen=True)
class EvidenceDraft:
    """Validated, structured evidence for one observation."""

    evidence_type: str
    detection_rule: str
    file_path: str
    line_number: int | None
    metadata: dict[str, Any]


def build_evidence(observation: DiscoveryObservation) -> EvidenceDraft:
    """Turn one raw observation into a validated evidence draft.

    Raises ``InvalidDiscoveryEvidenceError`` for anything that cannot be
    represented as a structured evidence record: unknown observation
    types, invalid paths or line numbers, empty or over-long detection
    rules, and secret-shaped metadata keys.
    """
    try:
        evidence_type = EvidenceType(observation.observation_type).value
    except ValueError:
        raise InvalidDiscoveryEvidenceError(
            f"unsupported evidence type '{observation.observation_type}'"
        ) from None

    try:
        file_path = validate_file_path(observation.file_path)
    except InvalidDiscoveredAssetError as exc:
        raise InvalidDiscoveryEvidenceError(str(exc)) from None

    detection_rule = (observation.detection_rule or "").strip()
    if not detection_rule or len(detection_rule) > MAX_DETECTION_RULE_LENGTH:
        raise InvalidDiscoveryEvidenceError(
            f"detection_rule must be non-empty and at most "
            f"{MAX_DETECTION_RULE_LENGTH} characters"
        )

    line_number = observation.line_number
    if line_number is not None and (
        isinstance(line_number, bool)
        or not isinstance(line_number, int)
        or line_number < 1
        or line_number > MAX_LINE_NUMBER
    ):
        raise InvalidDiscoveryEvidenceError("line_number is invalid")

    metadata = dict(observation.metadata or {})
    if observation.provider:
        metadata["provider"] = observation.provider
    try:
        validate_metadata(metadata)
    except (TypeError, ValueError) as exc:
        raise InvalidDiscoveryEvidenceError(f"evidence metadata is invalid: {exc}") from None

    return EvidenceDraft(
        evidence_type=evidence_type,
        detection_rule=detection_rule,
        file_path=file_path,
        line_number=line_number,
        metadata=metadata,
    )
