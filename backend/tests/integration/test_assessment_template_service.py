"""Integration tests: assessment template service — CRUD, tenancy, audit (RF-02)."""

from uuid import uuid4

import pytest
import sqlalchemy as sa
from pydantic import ValidationError

from app.core.config import settings
from app.modules.assessment.errors import (
    AssessmentTemplateNotFoundError,
    DuplicateAssessmentTemplateError,
    InvalidAssessmentTemplateQueryError,
)
from app.modules.assessment.models import AssessmentTemplate
from app.modules.assessment.schemas import (
    AssessmentTemplateCreate,
    AssessmentTemplateFilter,
    AssessmentTemplateUpdate,
)
from app.modules.assessment.seed import GENERAL_AI_RISK_ASSESSMENT_KEY, seed_assessment_templates
from app.modules.assessment.service import AssessmentTemplateService
from app.modules.audit.models import AuditEvent

TENANT = settings.default_tenant_id
OTHER_TENANT = uuid4()


def _template_row(db, tenant_id=TENANT) -> AssessmentTemplate | None:
    return db.scalar(
        sa.select(AssessmentTemplate).where(
            AssessmentTemplate.tenant_id == tenant_id,
            AssessmentTemplate.key == GENERAL_AI_RISK_ASSESSMENT_KEY,
        )
    )


@pytest.fixture
def seeded(db) -> AssessmentTemplate:
    seed_assessment_templates(db, TENANT)
    db.commit()
    return _template_row(db)


@pytest.fixture
def custom(db) -> AssessmentTemplate:
    return _service(db).create(AssessmentTemplateCreate(name="Vendor Assessment"))


def _service(db, tenant_id=None) -> AssessmentTemplateService:
    return AssessmentTemplateService(
        db,
        tenant_id=tenant_id or TENANT,
        request_id="req-tpl-01",
        actor="user-7",
    )


def _events(db, resource_id) -> list[AuditEvent]:
    stmt = (
        sa.select(AuditEvent)
        .where(
            AuditEvent.resource_type == "assessment_template",
            AuditEvent.resource_id == str(resource_id),
        )
        .order_by(AuditEvent.sequence_no)
    )
    return list(db.scalars(stmt))


# --- create -----------------------------------------------------------------


def test_create_template_persists_and_derives_key(db):
    created = _service(db).create(AssessmentTemplateCreate(name="  Custom AI Assessment  "))
    created_id = created.id
    db.expunge_all()

    row = db.get(AssessmentTemplate, created_id)
    assert row is not None
    assert row.tenant_id == TENANT
    assert row.name == "Custom AI Assessment"
    assert row.key == "custom-ai-assessment"
    assert row.is_system is False
    assert row.is_active is True
    assert row.version == 1
    assert row.assessment_type == "initial"

    events = _events(db, created_id)
    assert [str(e.event_type) for e in events] == ["assessment_template.created"]


def test_create_duplicate_key_rejected(db, seeded):
    with pytest.raises(DuplicateAssessmentTemplateError) as exc:
        _service(db).create(
            AssessmentTemplateCreate(name="Whatever", key=GENERAL_AI_RISK_ASSESSMENT_KEY)
        )
    assert exc.value.http_status == 409
    # the earlier seed row is untouched
    assert _template_row(db) is not None


def test_create_derived_key_collision_rejected(db, seeded):
    # deriving the seeded key from an identical name must also 409
    with pytest.raises(DuplicateAssessmentTemplateError):
        _service(db).create(AssessmentTemplateCreate(name="General AI Risk Assessment"))


# --- read -------------------------------------------------------------------


def test_get_template_from_other_tenant_returns_404(db):
    seed_assessment_templates(db, OTHER_TENANT)
    db.commit()
    foreign_id = _template_row(db, OTHER_TENANT).id

    with pytest.raises(AssessmentTemplateNotFoundError) as exc:
        _service(db).get(foreign_id)
    assert exc.value.http_status == 404


def test_get_detail_returns_sections_and_questions(db, seeded):
    template_id = seeded.id
    template, sections, questions_by_section = _service(db).get_detail(template_id)

    assert template.id == template_id
    assert [section.display_order for section in sections] == list(range(1, 8))
    assert all(section.id in questions_by_section for section in sections)
    assert sum(len(group) for group in questions_by_section.values()) == 14


# --- update -----------------------------------------------------------------


def test_update_template_changes_name_and_is_active(db, custom):
    template_id = custom.id
    updated = _service(db).update(
        template_id, AssessmentTemplateUpdate(name="Renamed Vendor Assessment", is_active=False)
    )
    assert updated.name == "Renamed Vendor Assessment"
    assert updated.is_active is False

    events = [
        e for e in _events(db, template_id) if str(e.event_type) == "assessment_template.updated"
    ]
    assert len(events) == 1
    assert set(events[0].changed_fields or []) == {"name", "is_active"}


def test_update_template_cannot_change_version_or_key(db, custom):
    # schema layer rejects both outright (Ruling 4: snapshots stay stable)
    with pytest.raises(ValidationError):
        AssessmentTemplateUpdate(version=2)
    with pytest.raises(ValidationError):
        AssessmentTemplateUpdate(key="sneaky-key")

    template_id, original_key, original_version = custom.id, custom.key, custom.version
    _service(db).update(template_id, AssessmentTemplateUpdate(name="Renamed"))

    row = db.get(AssessmentTemplate, template_id)
    assert row.key == original_key
    assert row.version == original_version


def test_update_unknown_template_returns_404(db):
    with pytest.raises(AssessmentTemplateNotFoundError):
        _service(db).update(uuid4(), AssessmentTemplateUpdate(name="Nope"))


# --- list -------------------------------------------------------------------


def test_list_templates_search_and_pagination(db, seeded):
    service = _service(db)
    service.create(AssessmentTemplateCreate(name="Clinical Trial Review"))
    service.create(AssessmentTemplateCreate(name="Clinical Safety Check"))

    page, total = service.list(AssessmentTemplateFilter(search="clinical", page_size=2))
    assert total == 2
    assert len(page) == 2

    page_one, total_one = service.list(AssessmentTemplateFilter(page_size=1))
    page_two, _ = service.list(AssessmentTemplateFilter(page=2, page_size=1))
    assert total_one == 3
    assert [t.id for t in page_one] != [t.id for t in page_two]

    all_items, _ = service.list(AssessmentTemplateFilter())
    # the seeded system template is part of the default listing
    assert any(t.key == GENERAL_AI_RISK_ASSESSMENT_KEY for t in all_items)


def test_list_templates_invalid_sort_returns_400(db, seeded):
    with pytest.raises(InvalidAssessmentTemplateQueryError) as exc:
        _service(db).list(AssessmentTemplateFilter(sort_by="is_system"))
    assert exc.value.http_status == 400
