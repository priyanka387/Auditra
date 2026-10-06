from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa

from app.core.config import settings
from app.modules.model_inventory.domain.errors import (
    AgentNotFoundError,
    ApplicationNotFoundError,
    DeploymentNotFoundError,
    ModelNotFoundError,
    ModelVersionMismatchError,
    ModelVersionNotFoundError,
)
from app.modules.model_inventory.models import Model, ModelDeployment, ModelVersion
from app.modules.model_inventory.schemas import (
    AgentCreate,
    AgentModelAssociationCreate,
    ApplicationCreate,
)
from app.modules.model_inventory.services import (
    AgentAssociationService,
    AgentService,
    ApplicationService,
)
from app.modules.model_usage.errors import (
    BatchTooLargeError,
    IdempotencyConflictError,
    UsageRelationshipError,
)
from app.modules.model_usage.models import ModelUsageEvent
from app.modules.model_usage.schemas import (
    UsageEventBatchCreate,
    UsageEventCreate,
    UsageEventFilter,
    UsageStatsQuery,
)
from app.modules.model_usage.service import ModelUsageService

STARTED = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)


def _payload(model_id, **overrides):
    payload = {
        "event_id": f"evt-{uuid4()}",
        "model_id": model_id,
        "source": "api",
        "started_at": STARTED,
    }
    payload.update(overrides)
    return UsageEventCreate(**payload)


def _service(db):
    return ModelUsageService(db, settings.default_tenant_id)


def test_record_event_derives_duration_and_total_tokens(db, parent_model):
    payload = _payload(
        parent_model.id,
        completed_at=STARTED + timedelta(milliseconds=1842),
        input_tokens=100,
        output_tokens=50,
    )
    response, created = _service(db).record_event(payload)
    assert created is True
    assert response.duration_ms == 1842
    assert response.total_tokens == 150
    assert response.token_usage_source.value == "calculated"
    assert response.id is not None


def test_record_event_keeps_provider_reported_total(db, parent_model):
    payload = _payload(
        parent_model.id,
        input_tokens=100,
        output_tokens=50,
        total_tokens=170,
        token_usage_source="provider_reported",
    )
    response, _ = _service(db).record_event(payload)
    assert response.total_tokens == 170


def test_duplicate_same_payload_is_idempotent(db, parent_model):
    payload = _payload(parent_model.id, input_tokens=10)
    first, created_first = _service(db).record_event(payload)
    second, created_second = _service(db).record_event(payload)
    assert created_first is True
    assert created_second is False
    assert first.id == second.id
    count = db.scalar(sa.select(sa.func.count()).select_from(ModelUsageEvent))
    assert count == 1


def test_duplicate_conflicting_payload_raises(db, parent_model):
    event_id = f"evt-{uuid4()}"
    _service(db).record_event(_payload(parent_model.id, event_id=event_id, input_tokens=10))
    with pytest.raises(IdempotencyConflictError):
        _service(db).record_event(_payload(parent_model.id, event_id=event_id, input_tokens=99))


def test_unknown_model_rejected(db):
    with pytest.raises(ModelNotFoundError):
        _service(db).record_event(_payload(uuid4()))


def test_version_of_other_model_rejected(db, parent_model, parent_version):
    other_model = Model(
        tenant_id=settings.default_tenant_id,
        provider_id=parent_model.provider_id,
        model_type_id=parent_model.model_type_id,
        name="Other",
        native_model_id="other-1",
        canonical_key="openai|other-1",
        source_type="MANUAL",
    )
    db.add(other_model)
    db.commit()
    foreign_version = ModelVersion(
        tenant_id=settings.default_tenant_id,
        model_id=other_model.id,
        identity_type="release",
        version_label="v1",
        native_version_id="other-v1",
        canonical_version_key="release:other-v1",
        lifecycle_state="DRAFT",
        source_type="manual",
    )
    db.add(foreign_version)
    db.commit()

    with pytest.raises(ModelVersionMismatchError):
        _service(db).record_event(_payload(parent_model.id, model_version_id=foreign_version.id))

    response, _ = _service(db).record_event(
        _payload(parent_model.id, model_version_id=parent_version.id)
    )
    assert response.model_version_id == parent_version.id

    with pytest.raises(ModelVersionNotFoundError):
        _service(db).record_event(_payload(parent_model.id, model_version_id=uuid4()))


def test_deployment_of_other_model_rejected(db, parent_model, parent_version):
    other_model = Model(
        tenant_id=settings.default_tenant_id,
        provider_id=parent_model.provider_id,
        model_type_id=parent_model.model_type_id,
        name="Other",
        native_model_id="other-2",
        canonical_key="openai|other-2",
        source_type="MANUAL",
    )
    db.add(other_model)
    db.commit()
    other_version = ModelVersion(
        tenant_id=settings.default_tenant_id,
        model_id=other_model.id,
        identity_type="release",
        version_label="v1",
        native_version_id="other-2-v1",
        canonical_version_key="release:other-2-v1",
        lifecycle_state="DRAFT",
        source_type="manual",
    )
    db.add(other_version)
    db.commit()
    foreign_deployment = ModelDeployment(
        tenant_id=settings.default_tenant_id,
        model_version_id=other_version.id,
        name="other-prod",
        environment="production",
        deployment_kind="online_inference",
        status="active",
        status_source="manual",
        target_type="kubernetes",
        source="manual",
    )
    db.add(foreign_deployment)
    db.commit()

    with pytest.raises(UsageRelationshipError):
        _service(db).record_event(_payload(parent_model.id, deployment_id=foreign_deployment.id))

    deployment = ModelDeployment(
        tenant_id=settings.default_tenant_id,
        model_version_id=parent_version.id,
        name="prod",
        environment="production",
        deployment_kind="online_inference",
        status="active",
        status_source="manual",
        target_type="kubernetes",
        source="manual",
    )
    db.add(deployment)
    db.commit()

    response, _ = _service(db).record_event(_payload(parent_model.id, deployment_id=deployment.id))
    assert response.deployment_id == deployment.id

    with pytest.raises(DeploymentNotFoundError):
        _service(db).record_event(_payload(parent_model.id, deployment_id=uuid4()))


def test_agent_association_and_application_rules(db, parent_model):
    from app.seed import seed_reference_data

    seed_reference_data(db)
    db.commit()
    application = ApplicationService(db, settings.default_tenant_id).create_application(
        ApplicationCreate(name="Support App", slug="support-app")
    )
    agent = AgentService(db, settings.default_tenant_id).create_agent(
        application.id,
        AgentCreate(name="Support Agent", slug="support-agent", framework="langgraph"),
    )

    with pytest.raises(UsageRelationshipError):
        _service(db).record_event(
            _payload(parent_model.id, agent_id=agent.id, application_id=application.id)
        )

    AgentAssociationService(db, settings.default_tenant_id).create_association(
        agent.id, AgentModelAssociationCreate(model_id=parent_model.id, role="PRIMARY")
    )
    response, _ = _service(db).record_event(
        _payload(parent_model.id, agent_id=agent.id, application_id=application.id)
    )
    assert response.agent_id == agent.id
    assert response.application_id == application.id

    with pytest.raises(ApplicationNotFoundError):
        _service(db).record_event(
            _payload(parent_model.id, agent_id=agent.id, application_id=uuid4())
        )

    other_application = ApplicationService(db, settings.default_tenant_id).create_application(
        ApplicationCreate(name="Other App", slug="other-app")
    )
    with pytest.raises(UsageRelationshipError):
        _service(db).record_event(
            _payload(
                parent_model.id,
                agent_id=agent.id,
                application_id=other_application.id,
            )
        )

    with pytest.raises(AgentNotFoundError):
        _service(db).record_event(_payload(parent_model.id, agent_id=uuid4()))

    with pytest.raises(ApplicationNotFoundError):
        _service(db).record_event(_payload(parent_model.id, application_id=uuid4()))


def test_error_message_sanitized_before_persist(db, parent_model):
    payload = _payload(
        parent_model.id,
        status="error",
        error_type="HTTPError",
        error_message="401 Authorization: Bearer sk-abcdef123456 rejected",
    )
    response, _ = _service(db).record_event(payload)
    assert response.error_message is not None
    assert "sk-abcdef123456" not in response.error_message
    assert "REDACTED" in response.error_message


def test_batch_partial_success(db, parent_model):
    valid = _payload(parent_model.id)
    invalid = _payload(uuid4())
    batch = UsageEventBatchCreate(events=[valid, invalid])
    result = _service(db).record_batch(batch.events)
    assert result.accepted == 1
    assert result.rejected == 1
    assert result.duplicates == 0
    assert result.results[1].status == "rejected"
    assert "not found" in (result.results[1].error or "")

    again = _service(db).record_batch([valid])
    assert again.duplicates == 1
    assert again.results[0].status == "duplicate"


def test_batch_rejects_oversized_batches(db, parent_model, monkeypatch):
    monkeypatch.setattr(settings, "usage_batch_size", 2)
    events = [_payload(parent_model.id) for _ in range(3)]
    with pytest.raises(BatchTooLargeError):
        _service(db).record_batch(events)


def test_get_event_by_external_id_and_uuid(db, parent_model):
    response, _ = _service(db).record_event(_payload(parent_model.id))
    by_external = _service(db).get_event(response.event_id)
    by_uuid = _service(db).get_event(str(response.id))
    assert by_external is not None and by_external.id == response.id
    assert by_uuid is not None and by_uuid.id == response.id


def test_list_events_paginated(db, parent_model):
    for index in range(3):
        _service(db).record_event(
            _payload(parent_model.id, started_at=STARTED + timedelta(minutes=index))
        )
    items, total = _service(db).list_events(UsageEventFilter(page=1, page_size=2))
    assert total == 3
    assert len(items) == 2


def test_get_stats_response_shape(db, parent_model):
    _service(db).record_event(
        _payload(
            parent_model.id,
            input_tokens=100,
            output_tokens=50,
            completed_at=STARTED + timedelta(seconds=1),
        )
    )
    _service(db).record_event(_payload(parent_model.id, status="error", error_type="TimeoutError"))
    query = UsageStatsQuery(
        model_id=parent_model.id,
        started_from=datetime(2026, 10, 1, tzinfo=UTC),
        started_to=datetime(2026, 10, 6, tzinfo=UTC),
        granularity="day",
    )
    stats = _service(db).get_stats(query)
    assert stats.totals.requests == 2
    assert stats.totals.successful_requests == 1
    assert stats.totals.failed_requests == 1
    assert stats.totals.error_rate == 0.5
    assert stats.totals.total_tokens == 150
    assert stats.filters == {"model_id": str(parent_model.id)}
    assert len(stats.buckets) >= 1
    assert stats.period.from_ == datetime(2026, 10, 1, tzinfo=UTC)
