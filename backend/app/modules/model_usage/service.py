import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.modules.model_usage import repository
from app.modules.model_usage.enums import TokenUsageSource
from app.modules.model_usage.errors import (
    BatchTooLargeError,
    DuplicateEventIdError,
    IdempotencyConflictError,
    ModelUsageNotFoundError,
    UsageRelationshipError,
)
from app.modules.model_usage.schemas import (
    BatchItemResult,
    UsageEventBatchResponse,
    UsageEventCreate,
    UsageEventFilter,
    UsageEventResponse,
    UsageStatsPeriod,
    UsageStatsQuery,
    UsageStatsResponse,
    UsageStatsTotals,
    UsageTimeBucket,
    payload_fingerprint,
)
from app.modules.model_inventory.domain.errors import (
    AgentNotFoundError,
    ApplicationNotFoundError,
    DeploymentNotFoundError,
    DomainError,
    ModelNotFoundError,
    ModelVersionMismatchError,
    ModelVersionNotFoundError,
)
from app.modules.model_inventory.models import (
    Agent,
    AgentModelAssociation,
    Application,
    Model,
    ModelDeployment,
    ModelVersion,
)

logger = logging.getLogger("auditra.usage")

_STAT_TOTAL_KEYS = (
    "requests",
    "successful_requests",
    "failed_requests",
    "error_rate",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "avg_latency_ms",
    "min_latency_ms",
    "max_latency_ms",
)


def normalize_event(event: UsageEventCreate) -> UsageEventCreate:
    updates: dict = {}
    if event.duration_ms is None and event.completed_at is not None:
        updates["duration_ms"] = max(
            0, int((event.completed_at - event.started_at).total_seconds() * 1000)
        )
    if (
        event.total_tokens is None
        and event.input_tokens is not None
        and event.output_tokens is not None
        and event.token_usage_source != TokenUsageSource.PROVIDER_REPORTED
    ):
        updates["total_tokens"] = event.input_tokens + event.output_tokens
        updates["token_usage_source"] = TokenUsageSource.CALCULATED
    return event.model_copy(update=updates) if updates else event


class ModelUsageService:
    def __init__(
        self,
        db: Session,
        tenant_id: UUID,
        request_id: str | None = None,
        actor: str | None = None,
    ) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self.request_id = request_id
        self.actor = actor

    def record_event(self, payload: UsageEventCreate) -> tuple[UsageEventResponse, bool]:
        event = normalize_event(payload)
        self._validate_relationships(event)
        fingerprint = payload_fingerprint(event)

        existing = repository.get_by_event_id(self.db, self.tenant_id, event.event_id)
        if existing is not None:
            return self._resolve_duplicate(existing, fingerprint), False

        entity = repository.create_event(
            self.db,
            tenant_id=self.tenant_id,
            event_id=event.event_id,
            payload_hash=fingerprint,
            model_id=event.model_id,
            model_version_id=event.model_version_id,
            deployment_id=event.deployment_id,
            application_id=event.application_id,
            agent_id=event.agent_id,
            status=event.status.value,
            source=event.source.value,
            started_at=event.started_at,
            completed_at=event.completed_at,
            duration_ms=event.duration_ms,
            input_tokens=event.input_tokens,
            output_tokens=event.output_tokens,
            total_tokens=event.total_tokens,
            cached_input_tokens=event.cached_input_tokens,
            reasoning_tokens=event.reasoning_tokens,
            token_usage_source=event.token_usage_source.value,
            estimated_cost=event.estimated_cost,
            cost_currency=event.cost_currency,
            cost_source=event.cost_source.value,
            request_id=event.request_id,
            trace_id=event.trace_id,
            parent_run_id=event.parent_run_id,
            provider_request_id=event.provider_request_id,
            environment=event.environment,
            region=event.region,
            host=event.host,
            runtime=event.runtime,
            framework=event.framework,
            framework_version=event.framework_version,
            sdk_version=event.sdk_version,
            operation_name=event.operation_name,
            error_type=event.error_type,
            error_code=event.error_code,
            error_message=event.error_message,
            retry_count=event.retry_count,
            provider_status_code=event.provider_status_code,
            tags=event.tags,
            metadata_=event.metadata or {},
        )
        try:
            repository.commit(self.db)
        except DuplicateEventIdError:
            raced = repository.get_by_event_id(self.db, self.tenant_id, event.event_id)
            if raced is None:
                raise
            return self._resolve_duplicate(raced, fingerprint), False

        logger.info(
            "usage event persisted",
            extra={
                "event_id": event.event_id,
                "model_id": str(event.model_id),
                "request_id": event.request_id,
                "trace_id": event.trace_id,
                "status": event.status.value,
                "source": event.source.value,
                "duration_ms": event.duration_ms,
            },
        )
        return UsageEventResponse.model_validate(entity, from_attributes=True), True

    def record_batch(self, events: list[UsageEventCreate]) -> UsageEventBatchResponse:
        if len(events) > settings.usage_batch_size:
            raise BatchTooLargeError(
                f"batch size {len(events)} exceeds limit {settings.usage_batch_size}"
            )
        results: list[BatchItemResult] = []
        for payload in events:
            try:
                response, created = self.record_event(payload)
                results.append(
                    BatchItemResult(
                        event_id=payload.event_id,
                        status="accepted" if created else "duplicate",
                        event_id_persisted=response.id,
                    )
                )
            except DomainError as exc:
                logger.info(
                    "usage event rejected",
                    extra={"event_id": payload.event_id, "reason": exc.code},
                )
                results.append(
                    BatchItemResult(
                        event_id=payload.event_id, status="rejected", error=exc.message
                    )
                )
        return UsageEventBatchResponse(
            accepted=sum(1 for r in results if r.status == "accepted"),
            duplicates=sum(1 for r in results if r.status == "duplicate"),
            rejected=sum(1 for r in results if r.status == "rejected"),
            results=results,
        )

    def get_event(self, identifier: str) -> UsageEventResponse:
        event = None
        try:
            event = repository.get_event(self.db, self.tenant_id, UUID(identifier))
        except ValueError:
            pass
        if event is None:
            event = repository.get_by_event_id(self.db, self.tenant_id, identifier)
        if event is None:
            raise ModelUsageNotFoundError(f"usage event '{identifier}' not found")
        return UsageEventResponse.model_validate(event, from_attributes=True)

    def list_events(
        self, filters: UsageEventFilter
    ) -> tuple[list[UsageEventResponse], int]:
        events, total = repository.list_events(self.db, self.tenant_id, filters)
        return [UsageEventResponse.model_validate(e, from_attributes=True) for e in events], total

    def get_stats(self, query: UsageStatsQuery) -> UsageStatsResponse:
        totals = UsageStatsTotals(**repository.aggregate_stats(self.db, self.tenant_id, query))
        buckets: list[UsageTimeBucket] = []
        if query.granularity != "total":
            for row in repository.bucket_stats(self.db, self.tenant_id, query):
                bucket_totals = {key: row[key] for key in _STAT_TOTAL_KEYS}
                buckets.append(
                    UsageTimeBucket(
                        bucket_start=row["bucket_start"],
                        totals=UsageStatsTotals(**bucket_totals),
                    )
                )
        return UsageStatsResponse(
            period=UsageStatsPeriod(from_=query.started_from, to=query.started_to),
            filters=self._applied_filters(query),
            totals=totals,
            buckets=buckets,
        )

    def _applied_filters(self, query: UsageStatsQuery) -> dict:
        filters = {}
        for name in (
            "model_id",
            "model_version_id",
            "deployment_id",
            "application_id",
            "agent_id",
            "environment",
        ):
            value = getattr(query, name)
            if value is not None:
                filters[name] = str(value)
        return filters

    def _resolve_duplicate(self, existing, fingerprint: str) -> UsageEventResponse:
        if existing.payload_hash != fingerprint:
            logger.info(
                "usage event conflict",
                extra={"event_id": existing.event_id, "model_id": str(existing.model_id)},
            )
            raise IdempotencyConflictError(
                f"event_id '{existing.event_id}' was already ingested with a different payload"
            )
        logger.info("usage event duplicate", extra={"event_id": existing.event_id})
        return UsageEventResponse.model_validate(existing, from_attributes=True)

    def _validate_relationships(self, event: UsageEventCreate) -> None:
        model = self.db.get(Model, event.model_id)
        if model is None or model.tenant_id != self.tenant_id:
            raise ModelNotFoundError(f"model '{event.model_id}' not found")

        if event.model_version_id is not None:
            version = self.db.get(ModelVersion, event.model_version_id)
            if version is None or version.tenant_id != self.tenant_id:
                raise ModelVersionNotFoundError(
                    f"model version '{event.model_version_id}' not found"
                )
            if version.model_id != event.model_id:
                raise ModelVersionMismatchError(
                    "model version does not belong to the referenced model"
                )

        if event.deployment_id is not None:
            deployment = self.db.get(ModelDeployment, event.deployment_id)
            if deployment is None or deployment.tenant_id != self.tenant_id:
                raise DeploymentNotFoundError(f"deployment '{event.deployment_id}' not found")
            version = self.db.get(ModelVersion, deployment.model_version_id)
            if version is None or version.model_id != event.model_id:
                raise UsageRelationshipError(
                    "deployment does not belong to the referenced model"
                )

        if event.application_id is not None:
            application = self.db.get(Application, event.application_id)
            if application is None or application.tenant_id != self.tenant_id:
                raise ApplicationNotFoundError(f"application '{event.application_id}' not found")

        if event.agent_id is not None:
            agent = self.db.get(Agent, event.agent_id)
            if agent is None or agent.tenant_id != self.tenant_id:
                raise AgentNotFoundError(f"agent '{event.agent_id}' not found")
            if event.application_id is not None and agent.application_id != event.application_id:
                raise UsageRelationshipError("agent does not belong to the application")
            association = self.db.scalar(
                select(AgentModelAssociation.id).where(
                    AgentModelAssociation.tenant_id == self.tenant_id,
                    AgentModelAssociation.agent_id == event.agent_id,
                    AgentModelAssociation.model_id == event.model_id,
                    AgentModelAssociation.status == "ACTIVE",
                )
            )
            if association is None:
                raise UsageRelationshipError("agent is not associated with the model")
