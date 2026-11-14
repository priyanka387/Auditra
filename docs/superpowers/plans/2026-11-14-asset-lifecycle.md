# Sub-feature 8 — Asset Lifecycle (implementation plan)

Authoritative spec: `docs/private/asset-discovery.md` §11.

## Design decisions (derived from spec + existing architecture)

- **States**: `ACTIVE`, `STALE`, `DISAPPEARED`, `IGNORED` (extend
  `DiscoveredAssetStatus` + `ck_discovered_assets_status`).
  The spec lists `DISCOVERED` among "possible states", but its own
  transition example resolves a new discovery directly to `ACTIVE`
  ("Run 1: gpt-4o discovered → ACTIVE"), so `DISCOVERED` is not used as a
  persisted state. `IGNORED` is sticky/reserved (no producer yet), mirroring
  how `ReconciliationStatus.IGNORED` was implemented in SF7.
- **Policy**: run-count based, source-scoped, derived from persisted data
  (no new counter column):
  - only `COMPLETED` runs count as absence signals; `FAILED`/`RUNNING`/
    `PENDING` never do; `PARTIAL` never counts as absence (positive
    observations still reactivate).
  - `missed = #{COMPLETED runs of the asset's source with completed_at >
    asset.last_seen_at and id != asset.last_seen_run_id}`
  - `missed >= disappeared_after` (default 3) → `DISAPPEARED`
  - `missed >= stale_after` (default 1) → `STALE`
  - `missed == 0` → `ACTIVE` (reactivation, identity preserved)
  - `IGNORED` never transitioned.
- **Config**: two `Settings` fields (`discovery_lifecycle_stale_after_missed_runs`,
  `discovery_lifecycle_disappeared_after_missed_runs`), validated
  (disappeared >= stale >= 1).
- **Evaluation entry point**: `AssetLifecycleService.evaluate_source_lifecycle(source_id, run_id)`
  called from `DiscoveryExecutionService.execute` *after* the run reaches
  `COMPLETED`/`PARTIAL`; guarded so non-evaluable run statuses raise and
  change nothing.
- **Timestamps**: lifecycle never writes `first_seen_at`/`last_seen_at`.
- **Reconciliation / dedup**: lifecycle writes only `status`.
- **API**: no new endpoints; existing `GET /discovery/assets?status=` accepts
  the extended vocabulary automatically.
- **Migration**: `0013_asset_lifecycle` recreates `ck_discovered_assets_status`
  with the four states (existing rows stay `ACTIVE`).

## Steps (TDD)

1. RED: update schema unit tests for new vocabulary + new
   `tests/unit/test_asset_lifecycle.py` policy tests.
2. GREEN: enum extension + `asset_lifecycle.py` policy functions.
3. RED: `tests/integration/test_asset_lifecycle.py` (service + execution
   integration scenarios 1-5, guards, timestamps, isolation, batch).
4. GREEN: lifecycle service, repository batch functions, errors, model
   constraint, migration 0013, execution hook.
5. RED/GREEN: migration test for 0013; API status-filter test.
6. Regression: full suite + ruff; fix fallout (schema tests vocabulary).
7. Commit + push (no force; branch `feature/asset-discovery`).
