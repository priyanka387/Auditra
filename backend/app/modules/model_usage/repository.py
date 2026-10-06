from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.model_usage.errors import DuplicateEventIdError
from app.modules.model_usage.models import ModelUsageEvent
from app.modules.model_usage.schemas import UsageEventFilter, UsageStatsQuery

_SORT_FIELDS: dict[str, Any] = {
    "started_at": ModelUsageEvent.started_at,
    "created_at": ModelUsageEvent.created_at,
    "duration_ms": ModelUsageEvent.duration_ms,
}

_DIMENSIONS = (
    "model_id",
    "model_version_id",
    "deployment_id",
    "application_id",
    "agent_id",
    "environment",
)


def create_event(db: Session, **fields: Any) -> ModelUsageEvent:
    event = ModelUsageEvent(**fields)
    db.add(event)
    return event


def commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise DuplicateEventIdError("an event with this event_id already exists") from None


def get_by_event_id(db: Session, tenant_id: UUID, event_id: str) -> ModelUsageEvent | None:
    return db.scalar(
        select(ModelUsageEvent).where(
            ModelUsageEvent.tenant_id == tenant_id,
            ModelUsageEvent.event_id == event_id,
        )
    )


def get_event(db: Session, tenant_id: UUID, event_id: UUID) -> ModelUsageEvent | None:
    return db.scalar(
        select(ModelUsageEvent).where(
            ModelUsageEvent.tenant_id == tenant_id,
            ModelUsageEvent.id == event_id,
        )
    )


def _dimension_conditions(tenant_id: UUID, target: Any) -> list[Any]:
    conditions: list[Any] = [ModelUsageEvent.tenant_id == tenant_id]
    for attribute in _DIMENSIONS:
        value = getattr(target, attribute, None)
        if value is not None:
            conditions.append(getattr(ModelUsageEvent, attribute) == value)
    return conditions


def _time_conditions(target: Any) -> list[Any]:
    conditions: list[Any] = []
    started_from = getattr(target, "started_from", None)
    started_to = getattr(target, "started_to", None)
    if started_from is not None:
        conditions.append(ModelUsageEvent.started_at >= started_from)
    if started_to is not None:
        conditions.append(ModelUsageEvent.started_at < started_to)
    return conditions


def list_events(
    db: Session,
    tenant_id: UUID,
    filters: UsageEventFilter,
) -> tuple[list[ModelUsageEvent], int]:
    sort_column = _SORT_FIELDS.get(filters.sort_by, ModelUsageEvent.started_at)
    conditions = _dimension_conditions(tenant_id, filters) + _time_conditions(filters)
    if filters.status is not None:
        conditions.append(ModelUsageEvent.status == filters.status.value)
    if filters.source is not None:
        conditions.append(ModelUsageEvent.source == filters.source.value)
    if filters.request_id is not None:
        conditions.append(ModelUsageEvent.request_id == filters.request_id)
    if filters.trace_id is not None:
        conditions.append(ModelUsageEvent.trace_id == filters.trace_id)
    if filters.operation_name is not None:
        conditions.append(ModelUsageEvent.operation_name == filters.operation_name)

    base = select(ModelUsageEvent).where(*conditions)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    direction = sort_column.desc() if filters.sort_order == "desc" else sort_column.asc()
    id_direction = (
        ModelUsageEvent.id.desc() if filters.sort_order == "desc" else ModelUsageEvent.id.asc()
    )
    stmt = (
        base.order_by(direction, id_direction)
        .offset((filters.page - 1) * filters.page_size)
        .limit(filters.page_size)
    )
    return list(db.scalars(stmt).all()), total


def _aggregate_select(tenant_id: UUID, query: UsageStatsQuery) -> Any:
    return select(
        func.count(),
        func.count().filter(ModelUsageEvent.status == "success"),
        func.count().filter(ModelUsageEvent.status == "error"),
        func.coalesce(func.sum(ModelUsageEvent.input_tokens), 0),
        func.coalesce(func.sum(ModelUsageEvent.output_tokens), 0),
        func.coalesce(func.sum(ModelUsageEvent.total_tokens), 0),
        func.avg(ModelUsageEvent.duration_ms),
        func.min(ModelUsageEvent.duration_ms),
        func.max(ModelUsageEvent.duration_ms),
    ).where(
        *_dimension_conditions(tenant_id, query),
        ModelUsageEvent.started_at >= query.started_from,
        ModelUsageEvent.started_at < query.started_to,
    )


def _totals(row: Any) -> dict[str, Any]:
    requests, succeeded, failed, input_tokens, output_tokens, total_tokens, avg, lo, hi = row
    return {
        "requests": requests,
        "successful_requests": succeeded,
        "failed_requests": failed,
        "error_rate": (failed / requests) if requests else 0.0,
        "input_tokens": int(input_tokens),
        "output_tokens": int(output_tokens),
        "total_tokens": int(total_tokens),
        "avg_latency_ms": float(avg) if avg is not None else None,
        "min_latency_ms": int(lo) if lo is not None else None,
        "max_latency_ms": int(hi) if hi is not None else None,
    }


def aggregate_stats(db: Session, tenant_id: UUID, query: UsageStatsQuery) -> dict[str, Any]:
    row = db.execute(_aggregate_select(tenant_id, query)).one()
    return _totals(row)


def bucket_stats(db: Session, tenant_id: UUID, query: UsageStatsQuery) -> list[dict[str, Any]]:
    bucket = func.timezone(
        "UTC",
        func.date_trunc(
            query.granularity,
            func.timezone("UTC", ModelUsageEvent.started_at),
        ),
    )
    stmt = (
        _aggregate_select(tenant_id, query)
        .add_columns(bucket.label("bucket_start"))
        .group_by(bucket)
        .order_by(bucket)
    )
    rows = db.execute(stmt).all()
    return [{"bucket_start": _as_utc(row.bucket_start), **_totals(row[0:9])} for row in rows]


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value
