"""Integration tests: reconciliation from discovered assets to canonical models.

Covers the spec's three outcomes end-to-end against PostgreSQL:

- MATCHED  — a deterministic identity links the observation to exactly
  one canonical Model Inventory row (foreign key, explainable reason).
- UNMATCHED — no canonical entity found; the asset stays a valid
  discovered asset and no canonical model is ever auto-created.
- AMBIGUOUS — several canonical candidates; never linked arbitrarily.

Plus idempotency, stable links, tenant isolation and constraint-level
validation of persisted reconciliation state.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.modules.asset_discovery.connectors.base import DiscoveryObservation
from app.modules.asset_discovery.enums import (
    AssetType,
    DiscoverySourceType,
    ReconciliationStatus,
)
from app.modules.asset_discovery.errors import DiscoveredAssetNotFoundError
from app.modules.asset_discovery.models import DiscoveredAsset
from app.modules.asset_discovery.reconciliation import (
    AssetReconciliationService,
    ReconciliationOutcome,
    ReconciliationSummary,
)
from app.modules.asset_discovery.schemas import DiscoveredAssetFilter, DiscoverySourceCreate
from app.modules.asset_discovery.service import (
    DiscoveredAssetService,
    DiscoveryRunService,
    DiscoverySourceService,
)
from app.modules.model_inventory.models import Model, ModelProvider, ModelType
from app.seed import seed_reference_data

TENANT = settings.default_tenant_id
OTHER_TENANT = uuid4()


@pytest.fixture
def source(db, tmp_path):
    return DiscoverySourceService(db, TENANT).create(
        DiscoverySourceCreate(
            name="payments-service",
            source_type=DiscoverySourceType.REPOSITORY,
            config={"location": str(tmp_path)},
        )
    )


@pytest.fixture
def run(db, source):
    return DiscoveryRunService(db, TENANT).create(source.id)


def _service(db, tenant_id=None) -> AssetReconciliationService:
    return AssetReconciliationService(db, tenant_id or TENANT)


def _observation(source, run, **overrides) -> DiscoveryObservation:
    payload = {
        "source_id": source.id,
        "run_id": run.id,
        "observation_type": "model_reference",
        "name": "gpt-4o",
        "provider": None,
        "file_path": "app/llm.py",
        "line_number": 2,
        "detection_rule": "model.assignment",
        "metadata": {"key": "model"},
    }
    payload.update(overrides)
    return DiscoveryObservation(**payload)


def _record(db, source, run, **overrides) -> DiscoveredAsset:
    asset, _ = DiscoveredAssetService(db, TENANT).record_observation(
        _observation(source, run, **overrides)
    )
    return asset


def _register_model(
    db,
    provider_slug="openai",
    native_id="gpt-4o",
    *,
    tenant_id=TENANT,
    archived=False,
    name=None,
) -> Model:
    seed_reference_data(db)
    provider = db.scalar(select(ModelProvider).where(ModelProvider.slug == provider_slug))
    model_type = db.scalar(select(ModelType).where(ModelType.slug == "llm"))
    model = Model(
        tenant_id=tenant_id,
        provider_id=provider.id,
        model_type_id=model_type.id,
        name=name or f"{provider_slug} {native_id}",
        native_model_id=native_id,
        canonical_key=f"{provider_slug}|{native_id}",
        source_type="MANUAL",
        lifecycle_state="ARCHIVED" if archived else "REGISTERED",
        archived_at=datetime.now(UTC) if archived else None,
    )
    db.add(model)
    db.commit()
    return model


def _archive(db, model: Model) -> None:
    """Archive a canonical model exactly like ModelService.archive_model."""
    model.lifecycle_state = "ARCHIVED"
    model.archived_at = datetime.now(UTC)
    db.commit()


def _model_asset(db, source, name="gpt-4o", metadata=None) -> DiscoveredAsset:
    """A catalog row with asset_type MODEL (no producing detection rule yet)."""
    asset = DiscoveredAsset(
        tenant_id=TENANT,
        source_id=source.id,
        asset_type=AssetType.MODEL.value,
        name=name,
        normalized_identity=f"MODEL|{name}",
        metadata_=dict(metadata or {}),
        first_seen_at=datetime.now(UTC),
        last_seen_at=datetime.now(UTC),
    )
    db.add(asset)
    db.commit()
    return asset


def _refetch(db, asset_id) -> DiscoveredAsset:
    db.expunge_all()
    return db.get(DiscoveredAsset, asset_id)


# --- successful reconciliation ---------------------------------------------


def test_reconcile_matches_via_provider_and_model_identifier(db, source, run):
    model = _register_model(db, "openai", "gpt-4o")
    asset = _record(db, source, run, name="openai:gpt-4o")

    outcome = _service(db).reconcile(asset)

    assert outcome.status == ReconciliationStatus.MATCHED.value
    assert outcome.reason == "provider_and_model_identifier"
    assert outcome.model_id == model.id

    stored = _refetch(db, asset.id)
    assert stored.reconciliation_status == ReconciliationStatus.MATCHED.value
    assert stored.reconciliation_reason == "provider_and_model_identifier"
    assert stored.reconciled_model_id == model.id


def test_reconcile_matches_via_exact_model_identifier_without_provider(db, source, run):
    model = _register_model(db, "openai", "gpt-4o")
    asset = _record(db, source, run, name="gpt-4o")

    outcome = _service(db).reconcile(asset)

    assert outcome.status == ReconciliationStatus.MATCHED.value
    assert outcome.reason == "model_identifier"
    assert outcome.model_id == model.id
    assert _refetch(db, asset.id).reconciled_model_id == model.id


def test_reconcile_matches_model_typed_asset_via_name_identity(db, source):
    model = _register_model(db, "anthropic", "claude-3-opus")
    asset = _model_asset(db, source, name="claude-3-opus")

    outcome = _service(db).reconcile(asset)

    assert outcome.status == ReconciliationStatus.MATCHED.value
    assert outcome.reason == "model_identifier"
    assert outcome.model_id == model.id


# --- unresolved: no canonical entity, never auto-created -------------------


def test_unmatched_when_no_canonical_model_exists(db, source, run):
    asset = _record(db, source, run)

    outcome = _service(db).reconcile(asset)

    assert outcome == ReconciliationOutcome(
        status=ReconciliationStatus.UNMATCHED.value,
        reason="no_canonical_model_match",
        model_id=None,
    )
    stored = _refetch(db, asset.id)
    assert stored.reconciliation_status == ReconciliationStatus.UNMATCHED.value
    assert stored.reconciliation_reason == "no_canonical_model_match"
    assert stored.reconciled_model_id is None
    # reconciliation never registers canonical models
    assert db.scalar(select(func.count()).select_from(Model)) == 0


def test_archived_model_is_not_matched(db, source, run):
    _register_model(db, "openai", "gpt-4o", archived=True)
    asset = _record(db, source, run)

    outcome = _service(db).reconcile(asset)

    assert outcome.status == ReconciliationStatus.UNMATCHED.value
    assert outcome.model_id is None
    assert _refetch(db, asset.id).reconciled_model_id is None


def test_canonical_model_of_another_tenant_is_never_matched(db, source, run):
    _register_model(db, "openai", "gpt-4o", tenant_id=OTHER_TENANT)
    asset = _record(db, source, run)

    outcome = _service(db).reconcile(asset)

    assert outcome.status == ReconciliationStatus.UNMATCHED.value
    assert outcome.model_id is None


def test_ineligible_asset_types_stay_unreconciled(db, source, run):
    ai_service = _record(
        db, source, run, name="openai", observation_type="provider_reference", provider="openai"
    )

    outcome = _service(db).reconcile(ai_service)

    assert outcome.status == ReconciliationStatus.UNMATCHED.value
    assert outcome.reason is None  # not attempted — no canonical target
    assert outcome.model_id is None
    stored = _refetch(db, ai_service.id)
    assert stored.reconciliation_reason is None


# --- ambiguous: multiple candidates, never linked --------------------------


def test_ambiguous_when_two_providers_share_the_native_id(db, source, run):
    _register_model(db, "openai", "gpt-4o")
    _register_model(db, "azure", "gpt-4o")
    asset = _record(db, source, run)

    outcome = _service(db).reconcile(asset)

    assert outcome.status == ReconciliationStatus.AMBIGUOUS.value
    assert outcome.reason == "multiple_model_identifier_matches"
    assert outcome.model_id is None
    stored = _refetch(db, asset.id)
    assert stored.reconciliation_status == ReconciliationStatus.AMBIGUOUS.value
    assert stored.reconciled_model_id is None  # never linked arbitrarily


def test_ambiguity_resolves_when_one_candidate_is_archived(db, source, run):
    _register_model(db, "openai", "gpt-4o")
    azure = _register_model(db, "azure", "gpt-4o")
    asset = _record(db, source, run)
    service = _service(db)
    assert service.reconcile(asset).status == ReconciliationStatus.AMBIGUOUS.value

    _archive(db, azure)

    outcome = _service(db).reconcile(asset)
    assert outcome.status == ReconciliationStatus.MATCHED.value
    assert outcome.reason == "model_identifier"


def test_known_provider_with_no_canonical_row_is_unmatched_not_ambiguous(db, source, run):
    # anthropic's model must never surface for an openai-qualified asset
    _register_model(db, "anthropic", "gpt-4o")
    asset = _record(db, source, run, name="openai:gpt-4o")

    outcome = _service(db).reconcile(asset)

    assert outcome.status == ReconciliationStatus.UNMATCHED.value
    assert outcome.model_id is None


# --- idempotency and stable links ------------------------------------------


def test_repeated_reconciliation_keeps_the_same_link(db, source, run):
    model = _register_model(db, "openai", "gpt-4o")
    asset = _record(db, source, run)

    first = _service(db).reconcile(asset)
    second = _service(db).reconcile(asset)
    third = _service(db).reconcile(asset)

    for outcome in (first, second, third):
        assert outcome.status == ReconciliationStatus.MATCHED.value
        assert outcome.model_id == model.id
    _, total = DiscoveredAssetService(db, TENANT).list(DiscoveredAssetFilter())
    assert total == 1  # no duplicate reconciliation rows — state lives on the asset
    assert _refetch(db, asset.id).reconciled_model_id == model.id


def test_matched_link_is_stable_when_a_stronger_candidate_appears(db, source, run):
    azure = _register_model(db, "azure", "gpt-4o")
    asset = _record(db, source, run)
    first = _service(db).reconcile(asset)
    assert first.model_id == azure.id  # unique exact native id match

    # a tier-1 canonical identity appears later; the existing confident
    # link must not be re-matched on every discovery
    _register_model(db, "openai", "gpt-4o")
    again = _service(db).reconcile(asset)

    assert again.status == ReconciliationStatus.MATCHED.value
    assert again.model_id == azure.id
    assert _refetch(db, asset.id).reconciled_model_id == azure.id


def test_matched_link_survives_archival_of_the_canonical_model(db, source, run):
    model = _register_model(db, "openai", "gpt-4o")
    asset = _record(db, source, run)
    assert _service(db).reconcile(asset).model_id == model.id

    _archive(db, model)
    outcome = _service(db).reconcile(asset)

    assert outcome.status == ReconciliationStatus.MATCHED.value
    assert outcome.model_id == model.id


def test_unmatched_asset_reconciles_after_model_registration(db, source, run):
    asset = _record(db, source, run)
    assert _service(db).reconcile(asset).status == ReconciliationStatus.UNMATCHED.value

    model = _register_model(db, "openai", "gpt-4o")
    outcome = _service(db).reconcile(asset)

    assert outcome.status == ReconciliationStatus.MATCHED.value
    assert outcome.model_id == model.id


def test_ignored_asset_is_never_auto_reconciled(db, source, run):
    asset = _record(db, source, run)
    asset.reconciliation_status = ReconciliationStatus.IGNORED.value
    db.commit()
    _register_model(db, "openai", "gpt-4o")

    outcome = _service(db).reconcile(asset)

    assert outcome.status == ReconciliationStatus.IGNORED.value
    assert outcome.model_id is None
    assert _refetch(db, asset.id).reconciled_model_id is None


def test_reconcile_rejects_asset_from_another_tenant(db, source, run):
    asset = _record(db, source, run)
    foreign_service = AssetReconciliationService(db, OTHER_TENANT)
    with pytest.raises(DiscoveredAssetNotFoundError):
        foreign_service.reconcile(asset)


# --- batch reconciliation ---------------------------------------------------


def test_reconcile_many_returns_status_counts(db, source, run):
    matched_asset = _record(db, source, run, name="gpt-4o")
    ineligible = _record(db, source, run, name="langchain", observation_type="framework_reference")
    _register_model(db, "openai", "gpt-4o")
    assets = [matched_asset, ineligible]

    first = _service(db).reconcile_many(assets)
    assert isinstance(first, ReconciliationSummary)
    assert first.matched == 1
    assert first.unmatched == 0
    assert first.skipped == 1
    assert first.unchanged == 1  # only the skipped asset needed no write
    assert first.attempted == 1

    second = _service(db).reconcile_many(assets)
    assert second.matched == 1
    assert second.skipped == 1
    assert second.unchanged == 2  # nothing to write on a repeated pass


def test_reconcile_many_unmatched_counts(db, source, run):
    asset = _record(db, source, run)
    summary = _service(db).reconcile_many([asset])

    assert summary.unmatched == 1
    assert summary.matched == 0
    assert summary.ambiguous == 0
    assert summary.unchanged == 0  # first attempt persisted the reason


# --- database-level validation ---------------------------------------------


def test_matched_state_without_model_is_rejected(db, source, run):
    asset = _record(db, source, run)
    asset.reconciliation_status = ReconciliationStatus.MATCHED.value
    asset.reconciliation_reason = "model_identifier"
    asset.reconciled_model_id = None
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_unknown_reconciliation_reason_is_rejected(db, source, run):
    asset = _record(db, source, run)
    asset.reconciliation_reason = "vibes_based_match"
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
