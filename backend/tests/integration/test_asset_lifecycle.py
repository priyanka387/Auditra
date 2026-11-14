"""Integration tests: Asset Lifecycle across discovery runs (Sub-feature 8).

Covers the master spec §11 behaviour end-to-end against PostgreSQL:

- observed assets stay ACTIVE across completed runs,
- an asset missing from a completed run ages STALE -> DISAPPEARED at the
  configured thresholds,
- FAILED runs (and runs that are not yet finished) never age assets,
- PARTIAL runs are not absence signals (their observations still count),
- evaluation is scoped to the source of the evaluated run,
- a reappearing asset reactivates the *same* catalog asset (identity
  preserved, first_seen_at untouched, last_seen_at only moves on real
  observations),
- IGNORED is sticky, reconciliation state and timestamps are untouched,
- the service uses a fixed number of statements regardless of scope size.
"""

import shutil
from uuid import uuid4

import pytest
from sqlalchemy import event, select

from app.core.config import settings
from app.modules.asset_discovery.asset_lifecycle import AssetLifecycleService
from app.modules.asset_discovery.connectors.base import DiscoveryObservation
from app.modules.asset_discovery.enums import (
    DiscoveryRunStatus,
    DiscoverySourceType,
    ReconciliationStatus,
)
from app.modules.asset_discovery.errors import (
    DiscoveryRunNotFoundError,
    DiscoverySourceUnreachableError,
    InvalidAssetLifecycleEvaluationError,
)
from app.modules.asset_discovery.execution import DiscoveryExecutionService
from app.modules.asset_discovery.reconciliation import AssetReconciliationService
from app.modules.asset_discovery.schemas import (
    DiscoveredAssetFilter,
    DiscoverySourceCreate,
)
from app.modules.asset_discovery.service import (
    DiscoveredAssetService,
    DiscoveryRunService,
    DiscoverySourceService,
)
from app.modules.model_inventory.models import Model, ModelProvider, ModelType
from app.seed import seed_reference_data

TENANT = settings.default_tenant_id

ACTIVE = "ACTIVE"
STALE = "STALE"
DISAPPEARED = "DISAPPEARED"
IGNORED = "IGNORED"


# --- fixtures & helpers -----------------------------------------------------


def _make_source(db, location, name):
    return DiscoverySourceService(db, TENANT).create(
        DiscoverySourceCreate(
            name=name,
            source_type=DiscoverySourceType.REPOSITORY,
            config={"location": str(location)},
        )
    )


@pytest.fixture
def source(db, tmp_path):
    return _make_source(db, tmp_path, name="payments-service")


def _runs(db):
    return DiscoveryRunService(db, TENANT)


def _record(db, source, run, name):
    observation = DiscoveryObservation(
        source_id=source.id,
        run_id=run.id,
        observation_type="model_reference",
        name=name,
        provider=None,
        file_path="app/llm.py",
        line_number=3,
        detection_rule="model.assignment",
        metadata={},
    )
    asset, _ = DiscoveredAssetService(db, TENANT).record_observation(observation)
    assert asset is not None, f"observation '{name}' was skipped"
    return asset


def _run_with(db, source, names, *, terminal="COMPLETED"):
    """A run that observes ``names`` and reaches the requested terminal state."""
    service = _runs(db)
    run = service.create(source.id)
    service.start(run.id)
    for name in names:
        _record(db, source, run, name)
    if terminal == "PARTIAL":
        return service.partial(run.id, error_summary="1 recoverable error(s): decode=1")
    return service.complete(run.id)


def _empty_run(db, source, *, terminal="COMPLETED"):
    """A run with no observations in the requested state."""
    service = _runs(db)
    run = service.create(source.id)
    if terminal == "PENDING":
        return run
    service.start(run.id)
    if terminal == "RUNNING":
        return run
    if terminal == "PARTIAL":
        return service.partial(run.id, error_summary="1 recoverable error(s): decode=1")
    if terminal == "FAILED":
        return service.fail(run.id, error_summary="discovery aborted: boom")
    return service.complete(run.id)


def _evaluate(db, source, run):
    return AssetLifecycleService(db, TENANT).evaluate_source_lifecycle(source.id, run.id)


def _items(db, source):
    items, _ = DiscoveredAssetService(db, TENANT).list(
        DiscoveredAssetFilter(source_id=source.id, page_size=100)
    )
    return items


def _statuses(db, source) -> dict[str, str]:
    return {item.name: item.status for item in _items(db, source)}


def _snapshot(db, source) -> dict:
    return {
        item.id: (item.status, item.first_seen_at, item.last_seen_at) for item in _items(db, source)
    }


def _register_model(db, native_id="gpt-4o"):
    seed_reference_data(db)
    provider = db.scalar(select(ModelProvider).where(ModelProvider.slug == "openai"))
    model_type = db.scalar(select(ModelType).where(ModelType.slug == "llm"))
    model = Model(
        tenant_id=TENANT,
        provider_id=provider.id,
        model_type_id=model_type.id,
        name=f"OpenAI {native_id}",
        native_model_id=native_id,
        canonical_key=f"openai|{native_id}",
        source_type="MANUAL",
        lifecycle_state="REGISTERED",
    )
    db.add(model)
    db.commit()
    return model


# --- Scenario 1: active -----------------------------------------------------


def test_observed_assets_remain_active_across_completed_runs(db, source):
    run1 = _run_with(db, source, ["gpt-4o", "claude-3"])
    summary1 = _evaluate(db, source, run1)
    assert _statuses(db, source) == {"gpt-4o": ACTIVE, "claude-3": ACTIVE}
    assert summary1.scope == 2 and summary1.observed == 2
    assert summary1.activated == 0 and summary1.unchanged == 2

    run2 = _run_with(db, source, ["gpt-4o", "claude-3"])
    observed = _snapshot(db, source)  # catalog already refreshed by observation
    summary2 = _evaluate(db, source, run2)

    assert _statuses(db, source) == {"gpt-4o": ACTIVE, "claude-3": ACTIVE}
    assert summary2.transitioned == 0 and summary2.unchanged == 2
    # evaluation itself never moves timestamps; only observation does
    assert _snapshot(db, source) == observed


def test_first_seen_never_moves_while_last_seen_advances_on_observation(db, source):
    run1 = _run_with(db, source, ["gpt-4o"])
    _evaluate(db, source, run1)
    first, last = _items(db, source)[0].first_seen_at, _items(db, source)[0].last_seen_at

    run2 = _run_with(db, source, ["gpt-4o"])  # observed again
    _evaluate(db, source, run2)
    asset = _items(db, source)[0]

    assert asset.status == ACTIVE
    assert asset.first_seen_at == first  # first_seen_at is never reset
    assert asset.last_seen_at > last  # last_seen_at only moves on observation


# --- Scenario 2: missing asset ----------------------------------------------


def test_asset_missing_from_a_completed_run_becomes_stale(db, source):
    run1 = _run_with(db, source, ["gpt-4o", "claude-3"])
    _evaluate(db, source, run1)

    run2 = _run_with(db, source, ["gpt-4o"])  # claude-3 absent
    last_seen_before = next(
        item for item in _items(db, source) if item.name == "claude-3"
    ).last_seen_at
    summary = _evaluate(db, source, run2)

    assert _statuses(db, source) == {"gpt-4o": ACTIVE, "claude-3": STALE}
    assert summary.stale == 1
    assert summary.activated == 0 and summary.disappeared == 0
    # aging never refreshes last_seen_at: claude-3 was last seen in run 1
    claude = next(item for item in _items(db, source) if item.name == "claude-3")
    assert claude.last_seen_at == last_seen_before


def test_repeated_absence_reaches_the_disappearance_threshold(db, source):
    run1 = _run_with(db, source, ["gpt-4o", "claude-3"])
    _evaluate(db, source, run1)

    # gpt-4o keeps being observed while claude-3 never appears again
    for expected in (STALE, STALE, DISAPPEARED):
        run = _run_with(db, source, ["gpt-4o"])
        _evaluate(db, source, run)
        statuses = _statuses(db, source)
        assert statuses["gpt-4o"] == ACTIVE
        assert statuses["claude-3"] == expected

    # one more completed run changes nothing: thresholds are crossed, not re-run
    summary = _evaluate(db, source, _run_with(db, source, ["gpt-4o"]))
    assert summary.disappeared == 0 and summary.unchanged == 2


# --- Scenario 3: failed runs ------------------------------------------------


def test_evaluation_refuses_runs_that_are_not_valid_boundaries(db, source):
    run1 = _run_with(db, source, ["gpt-4o"])
    _evaluate(db, source, run1)
    before = _snapshot(db, source)

    for state in (
        DiscoveryRunStatus.PENDING,
        DiscoveryRunStatus.RUNNING,
        DiscoveryRunStatus.FAILED,
    ):
        run = _empty_run(db, source, terminal=state.value)
        with pytest.raises(InvalidAssetLifecycleEvaluationError):
            _evaluate(db, source, run)
        assert _snapshot(db, source) == before  # nothing aged


def test_failed_scan_does_not_mark_previously_observed_assets_gone(db, tmp_path):
    repo = tmp_path / "vanishing-service"
    repo.mkdir()
    (repo / "main.py").write_text('model = "gpt-4o"\n', encoding="utf-8")
    source = _make_source(db, repo, name="vanishing-service")

    # run 1 succeeds and observes the asset
    DiscoveryExecutionService(db, TENANT).execute(_runs(db).create(source.id).id)
    assert _statuses(db, source)["gpt-4o"] == ACTIVE

    # the source disappears: run 2 fails outright
    shutil.rmtree(repo)
    run2 = _runs(db).create(source.id)
    with pytest.raises(DiscoverySourceUnreachableError):
        DiscoveryExecutionService(db, TENANT).execute(run2.id)
    assert _runs(db).get(run2.id).status == DiscoveryRunStatus.FAILED

    assert _statuses(db, source)["gpt-4o"] == ACTIVE  # not STALE, not DISAPPEARED


def test_evaluation_rejects_unknown_run_and_foreign_source(db, source, tmp_path):
    other = _make_source(db, tmp_path / "elsewhere", name="other-service")
    foreign_run = _empty_run(db, other)

    with pytest.raises(DiscoveryRunNotFoundError):
        AssetLifecycleService(db, TENANT).evaluate_source_lifecycle(source.id, uuid4())
    with pytest.raises(InvalidAssetLifecycleEvaluationError):
        # run exists but belongs to a different source than the scope
        _evaluate(db, source, foreign_run)


# --- Scenario: partial runs -------------------------------------------------


def test_partial_runs_are_not_absence_signals(db, source):
    run1 = _run_with(db, source, ["gpt-4o"])
    _evaluate(db, source, run1)

    # two partial scans miss the asset: it must not age
    for _ in range(2):
        partial = _empty_run(db, source, terminal="PARTIAL")
        _evaluate(db, source, partial)
        assert _statuses(db, source)["gpt-4o"] == ACTIVE

    # only completed runs count: the first *completed* miss -> STALE
    completed = _empty_run(db, source, terminal="COMPLETED")
    _evaluate(db, source, completed)
    assert _statuses(db, source)["gpt-4o"] == STALE


def test_partial_run_observation_reactivates_a_stale_asset(db, source):
    run1 = _run_with(db, source, ["gpt-4o"])
    _evaluate(db, source, run1)
    _evaluate(db, source, _empty_run(db, source))  # one completed miss
    assert _statuses(db, source)["gpt-4o"] == STALE

    partial = _run_with(db, source, ["gpt-4o"], terminal="PARTIAL")
    summary = _evaluate(db, source, partial)
    assert _statuses(db, source)["gpt-4o"] == ACTIVE
    assert summary.activated == 1


# --- Scenario 4: source isolation -------------------------------------------


def test_lifecycle_evaluation_never_crosses_sources(db, tmp_path):
    repo_a = tmp_path / "service-a"
    repo_b = tmp_path / "service-b"
    repo_a.mkdir()
    repo_b.mkdir()
    source_a = _make_source(db, repo_a, name="service-a")
    source_b = _make_source(db, repo_b, name="service-b")

    _evaluate(db, source_a, _run_with(db, source_a, ["gpt-4o"]))
    _evaluate(db, source_b, _run_with(db, source_b, ["claude-3"]))

    # source A stops observing its asset; source B is never evaluated here
    summary_a = _evaluate(db, source_a, _empty_run(db, source_a))

    assert summary_a.scope == 1  # only source A's asset is in scope
    assert _statuses(db, source_a) == {"gpt-4o": STALE}
    assert _statuses(db, source_b) == {"claude-3": ACTIVE}


# --- Scenario 5: reappearance -----------------------------------------------


def test_disappeared_asset_reobservation_reactivates_the_same_asset(db, source):
    run1 = _run_with(db, source, ["gpt-4o"])
    _evaluate(db, source, run1)
    for _ in range(3):
        _evaluate(db, source, _empty_run(db, source))
    assert _statuses(db, source)["gpt-4o"] == DISAPPEARED

    original = _items(db, source)[0]
    original_id, original_identity = original.id, original.normalized_identity
    original_first, original_last = original.first_seen_at, original.last_seen_at
    run = _run_with(db, source, ["gpt-4o"])  # asset reappears
    summary = _evaluate(db, source, run)

    items = _items(db, source)
    assert len(items) == 1  # deduplication identity preserved: no duplicate
    asset = items[0]
    assert asset.id == original_id
    assert asset.status == ACTIVE
    assert asset.normalized_identity == original_identity
    assert asset.first_seen_at == original_first  # never reset
    assert asset.last_seen_at > original_last  # moved by observation
    assert summary.activated == 1


# --- IGNORED -----------------------------------------------------------------


def test_ignored_assets_are_never_transitioned(db, source):
    run1 = _run_with(db, source, ["gpt-4o", "claude-3"])
    _evaluate(db, source, run1)

    # an intentional ignore decision (reserved vocabulary: no API flow
    # produces it yet, mirroring reconciliation's IGNORED)
    gpt = next(item for item in _items(db, source) if item.name == "gpt-4o")
    gpt.status = IGNORED
    db.commit()

    # absence would otherwise mean DISAPPEARED after three completed runs
    for _ in range(3):
        _evaluate(db, source, _run_with(db, source, ["claude-3"]))
    statuses = _statuses(db, source)
    assert statuses["gpt-4o"] == IGNORED
    assert statuses["claude-3"] == ACTIVE  # control: observation keeps ACTIVE

    # a later observation must not silently overwrite the ignore decision
    summary = _evaluate(db, source, _run_with(db, source, ["gpt-4o"]))
    assert _statuses(db, source)["gpt-4o"] == IGNORED
    assert summary.ignored == 1


def test_ignored_assets_are_not_aged_by_absence(db, source):
    run1 = _run_with(db, source, ["gpt-4o", "claude-3"])
    _evaluate(db, source, run1)
    gpt = next(item for item in _items(db, source) if item.name == "gpt-4o")
    gpt.status = IGNORED
    db.commit()

    for _ in range(3):
        _evaluate(db, source, _empty_run(db, source))

    statuses = _statuses(db, source)
    assert statuses["gpt-4o"] == IGNORED  # never aged despite three misses
    assert statuses["claude-3"] == DISAPPEARED  # control


# --- transactional / idempotent behaviour -----------------------------------


def test_evaluation_is_idempotent_and_leaves_state_untouched(db, source):
    run1 = _run_with(db, source, ["gpt-4o", "claude-3"])
    _evaluate(db, source, run1)

    miss = _empty_run(db, source)
    first = _evaluate(db, source, miss)
    assert first.stale == 2  # one completed miss -> both STALE
    before = _snapshot(db, source)

    # replaying the exact same evaluation writes nothing
    replay = _evaluate(db, source, miss)
    assert replay.transitioned == 0 and replay.unchanged == 2
    assert _snapshot(db, source) == before

    # a second distinct miss keeps them STALE (below disappearance threshold)
    second = _evaluate(db, source, _empty_run(db, source))
    assert second.transitioned == 0 and second.disappeared == 0
    assert _snapshot(db, source) == before


def test_evaluation_creates_no_assets_and_preserves_identity(db, source):
    run1 = _run_with(db, source, ["gpt-4o", "claude-3"])
    _evaluate(db, source, run1)
    before = {item.id: item.normalized_identity for item in _items(db, source)}

    for _ in range(3):
        _evaluate(db, source, _empty_run(db, source))

    after = {item.id: item.normalized_identity for item in _items(db, source)}
    assert after == before
    assert len(after) == 2  # no new discovered assets, no identity rewrites


# --- reconciliation independence --------------------------------------------


def test_lifecycle_never_changes_reconciliation_state(db, source):
    model = _register_model(db)
    _run_with(db, source, ["gpt-4o"])
    asset = next(item for item in _items(db, source) if item.name == "gpt-4o")

    outcome = AssetReconciliationService(db, TENANT).reconcile(asset)
    assert outcome.status == ReconciliationStatus.MATCHED.value
    assert asset.reconciled_model_id == model.id

    for _ in range(3):  # three completed misses -> DISAPPEARED
        _evaluate(db, source, _empty_run(db, source))

    refetched = next(item for item in _items(db, source) if item.name == "gpt-4o")
    assert refetched.status == DISAPPEARED
    assert refetched.reconciliation_status == ReconciliationStatus.MATCHED.value
    assert refetched.reconciled_model_id == model.id  # link survives aging


# --- discovery execution wiring ---------------------------------------------


def test_execution_run_completion_drives_lifecycle_end_to_end(db, tmp_path):
    repo = tmp_path / "payments-service"
    repo.mkdir()
    (repo / "main.py").write_text('model = "gpt-4o"\n', encoding="utf-8")
    (repo / "requirements.txt").write_text("langchain>=0.1.0\n", encoding="utf-8")
    source = _make_source(db, repo, name="payments-service")

    execution = DiscoveryExecutionService(db, TENANT)
    execution.execute(_runs(db).create(source.id).id)
    assert _statuses(db, source) == {
        "gpt-4o": ACTIVE,
        "langchain": ACTIVE,
    }

    # the dependency disappears from the source
    (repo / "requirements.txt").unlink()
    execution.execute(_runs(db).create(source.id).id)
    statuses = _statuses(db, source)
    assert statuses["gpt-4o"] == ACTIVE
    assert statuses["langchain"] == STALE  # missed one completed run


def test_evaluation_uses_a_constant_number_of_statements(db, tmp_path):
    statement_counts = []
    for size, name in ((3, "service-small"), (9, "service-large")):
        location = tmp_path / name
        location.mkdir()
        src = _make_source(db, location, name=name)
        _evaluate(db, src, _run_with(db, src, [f"model-{i}" for i in range(size)]))
        pending = _empty_run(db, src)  # every asset will age at once

        statements: list[str] = []

        def _count(conn, cursor, statement, parameters, context, executemany, _log=statements):
            _log.append(statement)

        bind = db.get_bind()
        event.listen(bind, "before_cursor_execute", _count)
        try:
            _evaluate(db, src, pending)
        finally:
            event.remove(bind, "before_cursor_execute", _count)
        statement_counts.append(len(statements))

    # no per-asset queries: a scope three times larger costs the same
    assert statement_counts[0] == statement_counts[1]
    assert all(count <= 12 for count in statement_counts)
