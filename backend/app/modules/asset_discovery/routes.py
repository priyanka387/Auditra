from datetime import datetime
from math import ceil
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Query, Request, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.modules.asset_discovery.enums import (
    AssetType,
    DiscoveredAssetStatus,
    DiscoveryRunStatus,
    DiscoverySourceStatus,
    DiscoverySourceType,
    ReconciliationStatus,
)
from app.modules.asset_discovery.errors import (
    InvalidDiscoveredAssetQueryError,
    InvalidDiscoveryRunQueryError,
    InvalidDiscoverySourceQueryError,
)
from app.modules.asset_discovery.execution import (
    DiscoveryExecutionResult,
    DiscoveryExecutionService,
)
from app.modules.asset_discovery.schemas import (
    DiscoveredAssetFilter,
    DiscoveredAssetListResponse,
    DiscoveredAssetResponse,
    DiscoveryExecutionIssueResponse,
    DiscoveryExecutionResponse,
    DiscoveryExecutionSourceResponse,
    DiscoveryObservationResponse,
    DiscoveryRunCreate,
    DiscoveryRunExecuteRequest,
    DiscoveryRunFilter,
    DiscoveryRunListResponse,
    DiscoveryRunResponse,
    DiscoverySourceCreate,
    DiscoverySourceFilter,
    DiscoverySourceListResponse,
    DiscoverySourceResponse,
    DiscoverySourceUpdate,
)
from app.modules.asset_discovery.service import (
    DiscoveredAssetService,
    DiscoveryRunService,
    DiscoverySourceService,
)

router = APIRouter(prefix="/api/v1/discovery/sources")
runs_router = APIRouter(prefix="/api/v1/discovery/runs")
assets_router = APIRouter(prefix="/api/v1/discovery/assets")


def _service(request: Request, db: Session) -> DiscoverySourceService:
    return DiscoverySourceService(
        db, settings.default_tenant_id, request_id=request.state.request_id, actor=None
    )


def _run_service(request: Request, db: Session) -> DiscoveryRunService:
    return DiscoveryRunService(db, settings.default_tenant_id, request_id=request.state.request_id)


def _execution_service(request: Request, db: Session) -> DiscoveryExecutionService:
    return DiscoveryExecutionService(
        db, settings.default_tenant_id, request_id=request.state.request_id
    )


def _build(**kwargs) -> DiscoverySourceFilter:
    try:
        return DiscoverySourceFilter(**kwargs)
    except ValidationError as exc:
        message = exc.errors()[0].get("msg", "invalid query") if exc.errors() else "invalid query"
        raise InvalidDiscoverySourceQueryError(message) from None


def _build_run(**kwargs) -> DiscoveryRunFilter:
    try:
        return DiscoveryRunFilter(**kwargs)
    except ValidationError as exc:
        message = exc.errors()[0].get("msg", "invalid query") if exc.errors() else "invalid query"
        raise InvalidDiscoveryRunQueryError(message) from None


def _asset_service(request: Request, db: Session) -> DiscoveredAssetService:
    return DiscoveredAssetService(
        db, settings.default_tenant_id, request_id=request.state.request_id
    )


def _build_assets(**kwargs) -> DiscoveredAssetFilter:
    try:
        return DiscoveredAssetFilter(**kwargs)
    except ValidationError as exc:
        message = exc.errors()[0].get("msg", "invalid query") if exc.errors() else "invalid query"
        raise InvalidDiscoveredAssetQueryError(message) from None


def _response(source) -> DiscoverySourceResponse:
    return DiscoverySourceResponse.model_validate(source, from_attributes=True)


def _run_response(run) -> DiscoveryRunResponse:
    return DiscoveryRunResponse.model_validate(run, from_attributes=True)


def _asset_response(asset) -> DiscoveredAssetResponse:
    return DiscoveredAssetResponse.model_validate(asset, from_attributes=True)


def _execution_response(result: DiscoveryExecutionResult) -> DiscoveryExecutionResponse:
    return DiscoveryExecutionResponse(
        source=DiscoveryExecutionSourceResponse(
            id=result.source.id,
            name=result.source.name,
            source_type=result.source.source_type,
        ),
        run=_run_response(result.run),
        status=result.run.status,
        connector=result.connector,
        files_scanned=result.files_scanned,
        observation_count=len(result.observations),
        observations=[
            DiscoveryObservationResponse(
                observation_type=observation.observation_type,
                name=observation.name,
                provider=observation.provider,
                file_path=observation.file_path,
                line_number=observation.line_number,
                detection_rule=observation.detection_rule,
                metadata=observation.metadata,
            )
            for observation in result.observations
        ],
        errors=[
            DiscoveryExecutionIssueResponse(
                file_path=issue.file_path,
                reason=issue.reason,
                message=issue.message,
            )
            for issue in result.issues
        ],
    )


@router.post("", status_code=201, response_model=DiscoverySourceResponse)
def create_source(
    payload: DiscoverySourceCreate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> DiscoverySourceResponse:
    return _response(_service(request, db).create(payload))


@router.get("", response_model=DiscoverySourceListResponse)
def list_sources(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    source_type: DiscoverySourceType | None = None,
    status: DiscoverySourceStatus | None = None,
    search: str | None = None,
    include_archived: bool = False,
    sort_by: str = "updated_at",
    sort_order: str = "desc",
) -> DiscoverySourceListResponse:
    filters = _build(
        page=page,
        page_size=page_size,
        source_type=source_type,
        status=status,
        search=search,
        include_archived=include_archived,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    items, total = _service(request, db).list(filters)
    return DiscoverySourceListResponse(
        items=[_response(item) for item in items],
        page=page,
        page_size=page_size,
        total=total,
        total_pages=ceil(total / page_size) if total else 0,
    )


@router.get("/{source_id}", response_model=DiscoverySourceResponse)
def get_source(
    source_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> DiscoverySourceResponse:
    return _response(_service(request, db).get(source_id))


@router.patch("/{source_id}", response_model=DiscoverySourceResponse)
def update_source(
    source_id: UUID,
    payload: DiscoverySourceUpdate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> DiscoverySourceResponse:
    return _response(_service(request, db).update(source_id, payload))


@router.delete("/{source_id}", status_code=204, response_class=Response)
def delete_source(
    source_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    _service(request, db).delete(source_id)
    return Response(status_code=204)


@router.post("/{source_id}/runs", status_code=201, response_model=DiscoveryRunResponse)
def create_run(
    source_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    # empty body for now: exists so unexpected fields (e.g. attempts to
    # force an initial status) are rejected
    payload: Annotated[DiscoveryRunCreate | None, Body()] = None,
) -> DiscoveryRunResponse:
    return _run_response(_run_service(request, db).create(source_id))


@runs_router.post("/{run_id}/execute", status_code=200, response_model=DiscoveryExecutionResponse)
def execute_run(
    run_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    payload: Annotated[DiscoveryRunExecuteRequest | None, Body()] = None,
) -> DiscoveryExecutionResponse:
    """Synchronously execute discovery for a PENDING run.

    Transitions the run PENDING -> RUNNING -> COMPLETED | PARTIAL (or
    FAILED via the error envelope) and returns the structured result:
    source, run, observation count, observations and recoverable errors.
    """
    return _execution_response(_execution_service(request, db).execute(run_id))


@runs_router.get("", response_model=DiscoveryRunListResponse)
def list_runs(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    source_id: UUID | None = None,
    status: DiscoveryRunStatus | None = None,
    started_from: datetime | None = None,
    started_to: datetime | None = None,
    sort_by: str = "started_at",
    sort_order: str = "desc",
) -> DiscoveryRunListResponse:
    filters = _build_run(
        page=page,
        page_size=page_size,
        source_id=source_id,
        status=status,
        started_from=started_from,
        started_to=started_to,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    items, total = _run_service(request, db).list(filters)
    return DiscoveryRunListResponse(
        items=[_run_response(item) for item in items],
        page=page,
        page_size=page_size,
        total=total,
        total_pages=ceil(total / page_size) if total else 0,
    )


@runs_router.get("/{run_id}", response_model=DiscoveryRunResponse)
def get_run(
    run_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> DiscoveryRunResponse:
    return _run_response(_run_service(request, db).get(run_id))


@assets_router.get("", response_model=DiscoveredAssetListResponse)
def list_discovered_assets(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    asset_type: AssetType | None = None,
    source_id: UUID | None = None,
    status: DiscoveredAssetStatus | None = None,
    reconciliation_status: ReconciliationStatus | None = None,
    search: str | None = None,
    sort_by: str = "last_seen_at",
    sort_order: str = "desc",
) -> DiscoveredAssetListResponse:
    """List discovered assets (catalog state only; read-only)."""
    filters = _build_assets(
        page=page,
        page_size=page_size,
        asset_type=asset_type,
        source_id=source_id,
        status=status,
        reconciliation_status=reconciliation_status,
        search=search,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    items, total = _asset_service(request, db).list(filters)
    return DiscoveredAssetListResponse(
        items=[_asset_response(item) for item in items],
        page=page,
        page_size=page_size,
        total=total,
        total_pages=ceil(total / page_size) if total else 0,
    )


@assets_router.get("/{asset_id}", response_model=DiscoveredAssetResponse)
def get_discovered_asset(
    asset_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> DiscoveredAssetResponse:
    return _asset_response(_asset_service(request, db).get(asset_id))
