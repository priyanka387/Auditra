from enum import StrEnum


class RiskStatus(StrEnum):
    """Lifecycle state of a risk in the RF-01 register.

    RF-01 ships the minimal lifecycle: OPEN (observed default),
    IN_REVIEW (active governance review) and ACCEPTED (a deliberate
    acceptance decision). Treatment/remediation states (mitigated,
    resolved, pending verification, closed) belong to RF-04/RF-08 and
    must not appear here; the status column and its check constraint
    are the extension point where they will be added.
    """

    OPEN = "open"
    IN_REVIEW = "in_review"
    ACCEPTED = "accepted"


class RiskSource(StrEnum):
    """Where a risk came from (RF-01 §7).

    The register UI may surface a subset (manual, asset_discovery,
    other); the backend carries the full enumeration so later features
    can create risks programmatically — assessments (RF-02), incidents,
    audits, policy and vendor reviews — without a schema change.
    """

    MANUAL = "manual"
    ASSET_DISCOVERY = "asset_discovery"
    ASSESSMENT = "assessment"
    INCIDENT = "incident"
    AUDIT = "audit"
    POLICY_REVIEW = "policy_review"
    VENDOR_REVIEW = "vendor_review"
    OTHER = "other"
