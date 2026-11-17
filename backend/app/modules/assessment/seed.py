"""Idempotent seed for the system assessment template (RF-02 §23).

The General AI Risk Assessment questionnaire is data, not code: its
sections and questions live in ``assessment_sections`` /
``assessment_questions`` so the template can be extended without an
application change (RF-02 §3). ``seed_assessment_templates`` is safe to
run any number of times — an existing ``(tenant_id, key)`` pair is
skipped in Python and the unique constraint backstops races — which is
what makes ``python -m app.seed`` and the test ``client`` fixture
re-runnable.
"""

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.assessment.enums import QuestionType
from app.modules.assessment.models import (
    AssessmentQuestion,
    AssessmentSection,
    AssessmentTemplate,
)

# Immutable identity of the system template, per-tenant unique.
GENERAL_AI_RISK_ASSESSMENT_KEY = "general-ai-risk-assessment"

# Ordered (title, description) sections — RF-02 §23 content.
SECTIONS: list[tuple[str, str | None]] = [
    ("System Purpose & Context", "Why the system exists and what it decides."),
    ("Data & Privacy", "What data the system processes, including personal data."),
    ("Stakeholder Impact", "Who is affected by the system's outputs and how severely."),
    ("Human Oversight", "Where humans review or override the system."),
    ("Safety & Reliability", "Failure modes and their consequences."),
    ("Security & Robustness", "Threats and abuse vectors the system faces."),
    (
        "Governance & Accountability",
        "Ownership, regulatory scope, and review history.",
    ),
]

# (section index (0-based), key, type, required, text, options,
#  validation_rules, description, help_text)
QUESTIONS: list[
    tuple[
        int,
        str,
        str,
        bool,
        str,
        list[dict[str, str]] | None,
        dict | None,
        str | None,
        str | None,
    ]
] = [
    (
        0,
        "purpose",
        QuestionType.TEXT.value,
        True,
        "What is the intended purpose of the AI system?",
        None,
        None,
        None,
        "Describe the outcome the system is meant to produce, not its implementation.",
    ),
    (
        0,
        "influenced-decisions",
        QuestionType.LONG_TEXT.value,
        False,
        "What decisions are influenced by the system?",
        None,
        None,
        None,
        None,
    ),
    (
        1,
        "data-types",
        QuestionType.MULTI_SELECT.value,
        True,
        "What types of data does the system process?",
        [
            {"value": "training_data", "label": "Training data"},
            {"value": "personal_data", "label": "Personal data"},
            {"value": "sensitive_data", "label": "Sensitive data"},
            {"value": "publicly_available", "label": "Publicly available"},
            {"value": "third_party", "label": "Third-party data"},
        ],
        None,
        None,
        None,
    ),
    (
        1,
        "processes_personal_data",
        QuestionType.BOOLEAN.value,
        True,
        "Does the system process personal or sensitive information?",
        None,
        None,
        None,
        None,
    ),
    (
        2,
        "affected-stakeholders",
        QuestionType.LONG_TEXT.value,
        True,
        "Who is affected by the system's outputs?",
        None,
        None,
        None,
        None,
    ),
    (
        3,
        "human-oversight",
        QuestionType.BOOLEAN.value,
        True,
        "Is human oversight available for consequential decisions?",
        None,
        None,
        None,
        None,
    ),
    (
        3,
        "oversight-mechanism",
        QuestionType.TEXT.value,
        False,
        "Describe the human oversight mechanism.",
        None,
        None,
        None,
        None,
    ),
    (
        4,
        "incorrect-output",
        QuestionType.LONG_TEXT.value,
        True,
        "What happens when the system produces an incorrect output?",
        None,
        None,
        None,
        None,
    ),
    (
        4,
        "impact-severity",
        QuestionType.SINGLE_SELECT.value,
        True,
        "What is the potential severity of harm if the system fails?",
        [
            {"value": "none", "label": "None"},
            {"value": "minor", "label": "Minor"},
            {"value": "moderate", "label": "Moderate"},
            {"value": "significant", "label": "Significant"},
            {"value": "severe", "label": "Severe"},
        ],
        None,
        None,
        None,
    ),
    (
        5,
        "security-threats",
        QuestionType.MULTI_SELECT.value,
        False,
        "What are the main security threats associated with this system?",
        [
            {"value": "adversarial_attacks", "label": "Adversarial attacks"},
            {"value": "data_poisoning", "label": "Data poisoning"},
            {"value": "model_extraction", "label": "Model extraction"},
            {"value": "unauthorized_access", "label": "Unauthorized access"},
            {"value": "other", "label": "Other"},
        ],
        None,
        None,
        None,
    ),
    (
        5,
        "misuse-potential",
        QuestionType.BOOLEAN.value,
        True,
        "Can the system be misused or abused?",
        None,
        None,
        None,
        None,
    ),
    (
        6,
        "accountable-owner",
        QuestionType.TEXT.value,
        True,
        "Who is accountable for the system's operation?",
        None,
        None,
        None,
        None,
    ),
    (
        6,
        "regulatory-scope",
        QuestionType.SINGLE_SELECT.value,
        False,
        "Is the system subject to regulatory requirements?",
        [
            {"value": "not_applicable", "label": "Not applicable"},
            {"value": "uncertain", "label": "Uncertain"},
            {"value": "applicable", "label": "Applicable"},
        ],
        None,
        None,
        None,
    ),
    (
        6,
        "governance-review",
        QuestionType.NUMBER.value,
        False,
        "How many governance reviews has the system undergone in the last 12 months?",
        None,
        {"min": 0, "max": 100},
        None,
        None,
    ),
]

TEMPLATE_DESCRIPTION = (
    "Standard initial assessment covering the purpose, data, impact, oversight, "
    "safety, security, and governance of an AI system."
)


def seed_assessment_templates(db: Session, tenant_id: UUID) -> int:
    """Insert the system assessment template for one tenant; 1 = created.

    Idempotent: if the ``(tenant_id, key)`` pair already exists the seed
    is a no-op returning 0 — custom edits to a seeded template survive
    re-seeding, and the unique constraint backstops concurrent runs.
    """
    existing = db.scalar(
        select(AssessmentTemplate.id).where(
            AssessmentTemplate.tenant_id == tenant_id,
            AssessmentTemplate.key == GENERAL_AI_RISK_ASSESSMENT_KEY,
        )
    )
    if existing is not None:
        return 0

    template = AssessmentTemplate(
        id=uuid4(),
        tenant_id=tenant_id,
        key=GENERAL_AI_RISK_ASSESSMENT_KEY,
        name="General AI Risk Assessment",
        description=TEMPLATE_DESCRIPTION,
        assessment_type="initial",
        version=1,
        is_system=True,
        is_active=True,
    )
    db.add(template)
    db.flush()

    section_rows: list[AssessmentSection] = []
    for order, (title, description) in enumerate(SECTIONS, start=1):
        section = AssessmentSection(
            id=uuid4(),
            template_id=template.id,
            title=title,
            description=description,
            display_order=order,
            is_active=True,
        )
        db.add(section)
        section_rows.append(section)
    db.flush()

    next_order: dict[int, int] = {}
    for (
        section_index,
        key,
        question_type,
        required,
        text,
        options,
        rules,
        description,
        help_text,
    ) in QUESTIONS:
        display_order = next_order.get(section_index, 0) + 1
        next_order[section_index] = display_order
        db.add(
            AssessmentQuestion(
                id=uuid4(),
                section_id=section_rows[section_index].id,
                key=key,
                question_text=text,
                description=description,
                question_type=question_type,
                required=required,
                display_order=display_order,
                options=options,
                validation_rules=rules,
                help_text=help_text,
                is_active=True,
            )
        )
    db.flush()
    return 1
