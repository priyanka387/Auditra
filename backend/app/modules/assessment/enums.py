from enum import StrEnum


class AssessmentStatus(StrEnum):
    """Lifecycle state of an AI risk assessment (RF-02 §5).

    RF-02 ships the controlled workflow only: DRAFT (created, being
    answered), IN_PROGRESS (first answer saved), SUBMITTED (handed to a
    reviewer), UNDER_REVIEW (reviewer engaged), APPROVED (immutable) and
    REJECTED (returned with a reason, revisable). COMPLETED/RESOLVED/
    CLOSED are deliberately absent — they would collide with the later
    remediation workflows of RF-04/RF-08; the status column and its
    check constraint are the extension point where they will be added.
    """

    DRAFT = "draft"
    IN_PROGRESS = "in_progress"
    SUBMITTED = "submitted"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    REJECTED = "rejected"


class AssessmentType(StrEnum):
    """What triggered an assessment (RF-02 §6).

    Types are data, not screens: the backend carries the full
    enumeration so later features (periodic reviews, incident triggers,
    vendor reviews) can create assessments without a schema change, and
    the seeded template declares the type it was written for while any
    assessment may be recorded against any type.
    """

    INITIAL = "initial"
    PRE_DEPLOYMENT = "pre_deployment"
    PERIODIC = "periodic"
    CHANGE = "change"
    INCIDENT_TRIGGERED = "incident_triggered"
    VENDOR = "vendor"
    OTHER = "other"


class QuestionType(StrEnum):
    """Supported questionnaire answer shapes (RF-02 §14).

    The six initial types cover the seeded template end to end. DATE,
    FILE, RATING, TABLE and CONDITIONAL are future additions; the
    response validator and the check constraint are the extension
    points where they will be added. Conditional rules are not built
    yet (RF-02 §16) — the API renders every active question.
    """

    TEXT = "text"
    LONG_TEXT = "long_text"
    SINGLE_SELECT = "single_select"
    MULTI_SELECT = "multi_select"
    BOOLEAN = "boolean"
    NUMBER = "number"
