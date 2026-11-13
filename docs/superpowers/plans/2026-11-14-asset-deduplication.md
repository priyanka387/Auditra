# Asset Deduplication (Sub-feature 6) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deterministically prevent the same discovered asset from appearing as multiple catalog records — across repeated observations, repeated runs, and concurrent discovery — while preserving `first_seen_at`, source boundaries, and per-run traceability.

**Architecture:** A new pure domain module `deduplication.py` owns identity resolution, duplicate decisions, and metadata merging; `DiscoveredAssetService` (Sub-feature 4) delegates to it, keeping persistence/logging where they are. Identity stays `"<asset_type>|<normalized name>"` scoped per `(tenant, source)` and enforced by the existing unique constraint — dedup matches only exact deterministic identity (no location, name-similarity, or cross-source matching). A new `discovered_asset_runs` join table (migration 0011) records which runs observed each asset, so refreshing `last_seen_run_id` no longer destroys history.

**Tech Stack:** Python 3.12 / SQLAlchemy 2.0 / Alembic / PostgreSQL (test DB on `localhost:5433`) / pytest / ruff (line-length 100).

**Spec:** `docs/private/asset-discovery.md` (§9 Asset Deduplication, §10 Discovery Runs & History, §16 Database Requirements, §18 Idempotency, §26 Principles 3/7).

## Global Constraints

- Implement **only** Sub-feature 6. No reconciliation (SF7), lifecycle states (SF8), or evidence records (SF9). A discovered `MODEL` stays a discovered asset.
- Deterministic identity only: no ML, embeddings, vector stores, fuzzy-matching libraries, LLM calls, Redis, background workers, event buses, or distributed locks.
- Never merge across discovery sources: deduplication is not reconciliation; never consult Model Inventory records for identity.
- Discovery content stays untrusted: no code execution, no shell, no dependency installs, no network calls, no secrets persisted; identity/path values validated before persistence (existing guards must not weaken).
- PostgreSQL is the source of truth; every migration must be reversible (`downgrade` tested).
- Existing behavior contract that must keep passing: identity format `MODEL_REFERENCE|gpt-4o`, first-seen metadata wins, `first_seen_at` never resets, unique constraint `uq_discovered_assets_source_identity`, run count constraints.
- **No public API changes**: no new endpoints, no schema/route edits (dedup integrates only into catalog persistence). Run traceability is exposed through `DiscoveredAssetService.observed_run_ids` for tests and future sub-features, not via REST.
- `uv run ruff check .` and `uv run ruff format --check .` must be clean; no type checker is configured.
- Git handling (commit/push) pending human confirmation — the task prompt contains conflicting instructions; do not commit until confirmed.

## Review Focus

1. **Metadata merge change (empty-fill) must not break first-seen-wins** — a re-observation must keep non-empty existing values (`app/llm.py`, `line_number: 2`) while filling a previously empty/absent value. Pinned by `test_reobservation_fills_previously_empty_metadata_value` (Task 3) plus the existing `test_discovered_asset_service.py` suite.
2. **Run-link insert ordering vs commit/autoflush** — on create, the link insert must happen *after* `commit_asset` so an `IntegrityError` race still converts to `DuplicateDiscoveredAssetError` recovery; on refresh it must be `ON CONFLICT DO NOTHING` so a duplicate link never fails a refresh. Pinned by `test_create_records_run_link`, `test_reobservation_in_same_run_records_single_link`, and the existing `test_uniqueness_constraint_rejects_duplicate_identity` (Task 4).
3. **`record_observations` restructure must preserve outcomes** — batch counts (created/updated/unchanged/skipped), secret/oversized skip logging, source/run verification errors, and race recovery must behave exactly as today, while an invalid observation now fails the batch before anything persists. Pinned by `test_record_observations_counts_batch_outcomes` (existing), `test_batch_with_invalid_observation_persists_nothing` and `test_batch_across_two_sources_prefetches_per_source` (Task 5).
4. **Cross-source false merges** — identical `normalized_identity` *and* identical `file_path` in two different sources must stay two assets; similar names (`gpt-4o` vs `gpt-4o-mini` vs `GPT-4o`) in one source must stay three. Pinned by the Task 6 matrix tests.
5. **Migration reversibility and test-DB hygiene** — `0011` downgrade must drop only `discovered_asset_runs` (leaving `discovered_assets` intact), and `tests/conftest.py` TRUNCATE must include the new table or later tests see stale links. Pinned by `test_discovered_asset_runs_migration_downgrade_and_upgrade` (Task 4) and the full suite (Task 7).

---

### Task 1: Baseline verification

**Files:** none (verification gate).

- [ ] **Step 1: Run ruff on the current tree**

Run: `uv run ruff check .` and `uv run ruff format --check .` (workdir `C:\Auditra\backend`)
Expected: no findings (baseline is clean; if not, record findings before proceeding).

- [ ] **Step 2: Run the full test suite as a baseline**

Run: `uv run pytest -q` (workdir `C:\Auditra\backend`, ~2–4 min)
Expected: all tests pass. Report the pass count; any pre-existing failure must be recorded, not fixed silently.

---

### Task 2: Deduplication domain module

**Files:**
- Create: `backend/app/modules/asset_discovery/deduplication.py`
- Test: `backend/tests/unit/test_asset_deduplication.py`

**Interfaces:**
- Consumes: `ClassificationResult` (`asset_type: AssetType`, `name: str`, `metadata`, `rule_id`) from `classification.py`; `build_normalized_identity(asset_type, name) -> str` from `catalog.py`; `DiscoveredAsset` ORM model; `repository.find_asset_by_identity(db, tenant_id, source_id, normalized_identity)`.
- Produces (relied on by Tasks 3–5):
  ```python
  @dataclass(frozen=True)
  class DeduplicationKey:
      tenant_id: UUID
      source_id: UUID
      asset_type: str            # AssetType.value
      normalized_identity: str

  def resolve_identity(classification: ClassificationResult) -> str
  def build_key(tenant_id: UUID, source_id: UUID, classification: ClassificationResult) -> DeduplicationKey
  def is_duplicate(asset: DiscoveredAsset | None, key: DeduplicationKey) -> bool
  def merge_metadata(existing: Mapping[str, Any] | None,
                     incoming: Mapping[str, Any] | None) -> tuple[dict[str, Any], bool]

  class AssetDeduplicationService:
      def __init__(self, db: Session, tenant_id: UUID, request_id: str | None = None) -> None
      def build_key(self, source_id: UUID, classification: ClassificationResult) -> DeduplicationKey
      def find_duplicate(self, key: DeduplicationKey) -> DiscoveredAsset | None
  ```

- [ ] **Step 1: Write the failing unit tests**

Create `backend/tests/unit/test_asset_deduplication.py` with these tests (assertions specified; helper `_classification(name, asset_type)` builds a `ClassificationResult`):

| Test | Key assertions |
|---|---|
| `test_resolve_identity_is_deterministic` | same input twice → `"MODEL_REFERENCE\|gpt-4o"` both times |
| `test_resolve_identity_includes_asset_type` | same name, `MODEL` vs `MODEL_REFERENCE` → identities differ |
| `test_resolve_identity_normalizes_whitespace` | name `"  gpt-4o "` → `"MODEL_REFERENCE\|gpt-4o"` |
| `test_resolve_identity_rejects_empty_name` | name `"   "` → `InvalidDiscoveredAssetError` |
| `test_resolve_identity_rejects_unknown_type` | `asset_type="THING"` → `InvalidDiscoveredAssetError` |
| `test_identity_ignores_location_and_metadata` | same name, different `file_path`/`metadata` inputs → identical identity (location is not identity) |
| `test_different_names_sharing_a_path_get_different_identities` | `"model-a"` vs `"model-b"` same path → identities differ (path never merges) |
| `test_build_key_scopes_to_source_and_tenant` | identical classification, different `source_id` → different keys; different `tenant_id` → different keys |
| `test_is_duplicate_requires_exact_match` | parametrized: exact match → `True`; `None` asset, other source, other tenant, other identity, other asset_type → all `False` |
| `test_is_duplicate_rejects_similar_names` | keys for `gpt-4o`, `gpt-4o-mini`, `GPT-4o` → no pair is duplicate |
| `test_merge_metadata_preserves_existing_non_empty` | `({"a": "1"}, {"a": "2"})` → `({"a": "1"}, False)` |
| `test_merge_metadata_fills_empty_existing_values` | `({"a": ""}, {"a": "x"})` → `({"a": "x"}, True)`; same for `None` |
| `test_merge_metadata_adds_new_keys` | `({"a": "1"}, {"b": 2})` → both present, `changed is True` |
| `test_merge_metadata_incoming_empty_never_erases` | `({"a": "1"}, {"a": ""})` → `({"a": "1"}, False)` |
| `test_merge_metadata_handles_none_arguments` | `(None, None)` → `({}, False)`; `(None, {"a": 1})` → `({"a": 1}, True)`; `({"a": 1}, None)` → `({"a": 1}, False)` |
| `test_merge_metadata_does_not_mutate_inputs` | inputs unchanged after merge |

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_asset_deduplication.py -q`
Expected: FAIL with `ModuleNotFoundError: ... 'deduplication'` (collection error).

- [ ] **Step 3: Implement `deduplication.py`**

Module docstring must state the identity hierarchy explicitly:
- **Tier 1 — explicit stable identity:** no V1 connector reports an external id; provider-qualified names already equal the normalized name, so tier 1 collapses into tier 2 today. No dead branch is added.
- **Tier 2 — deterministically normalized identity (`asset_type|normalized name`), scoped to `(tenant, source)`:** the only accepted match.
- **Tier 3+ (location, metadata similarity) are deliberately rejected** — weak similarity is not identity; false merges are worse than duplicates. Cross-source matches are rejected because deduplication is not reconciliation (SF7 owns that).

`resolve_identity` delegates to `build_normalized_identity(classification.asset_type, classification.name)`.
`build_key` fills the four `DeduplicationKey` fields (`asset_type=classification.asset_type.value`).
`is_duplicate` compares tenant, source, asset_type, normalized_identity (asset `None` → `False`).
`merge_metadata` algorithm:

```python
def _has_value(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}

def merge_metadata(existing, incoming):
    merged = dict(incoming or {})
    for key, value in (existing or {}).items():
        if _has_value(value):
            merged[key] = value            # first-seen non-empty value wins
        else:
            merged.setdefault(key, value)  # keep empties only when incoming lacks the key
    return merged, merged != (existing or {})
```

`AssetDeduplicationService.find_duplicate` = `repository.find_asset_by_identity(db, key.tenant_id, key.source_id, key.normalized_identity)` then verify with `is_duplicate` (returns `None` on any mismatch). No writes, no Model Inventory lookups.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_asset_deduplication.py -q`
Expected: PASS (16 tests).

- [ ] **Step 5: Lint**

Run: `uv run ruff check app/modules/asset_discovery/deduplication.py tests/unit/test_asset_deduplication.py && uv run ruff format --check app/modules/asset_discovery/deduplication.py tests/unit/test_asset_deduplication.py`
Expected: clean.

- [ ] **Step 6: Commit (pending human confirmation of git policy)**

```bash
git add backend/app/modules/asset_discovery/deduplication.py backend/tests/unit/test_asset_deduplication.py
git commit -m "feat(asset-discovery): deterministic deduplication domain and identity resolution"
```

---

### Task 3: Integrate deduplication into DiscoveredAssetService

**Files:**
- Modify: `backend/app/modules/asset_discovery/service.py` (`DiscoveredAssetService` only)
- Modify: `backend/app/modules/asset_discovery/catalog.py` (docstring pointer only — logic unchanged)
- Modify: `backend/app/modules/asset_discovery/models.py` (docstring pointer only)
- Modify: `backend/app/modules/asset_discovery/connectors/base.py` (docstring pointer only)
- Test: `backend/tests/integration/test_discovery_deduplication.py` (new file)

**Interfaces:**
- Consumes: everything from Task 2.
- Produces: `DiscoveredAssetService` gains `self.dedup: AssetDeduplicationService`; `record_observation` resolves `key = self.dedup.build_key(observation.source_id, classification)` and looks up `self.dedup.find_duplicate(key)`; `_refresh` merges via `dedup.merge_metadata`. Signatures of `record_observation`/`record_observations`/`get`/`list` unchanged.

- [ ] **Step 1: Write the failing integration tests**

Create `backend/tests/integration/test_discovery_deduplication.py`, reusing the `_observation`/`source`/`run` fixture pattern from `test_discovered_asset_service.py` (model_reference `gpt-4o` @ `app/llm.py`):

1. `test_dedup_service_returns_exact_match_and_none` — record one observation; `AssetDeduplicationService(db, TENANT).find_duplicate(build_key(...))` returns that asset; a key built for a different `source_id` (second source, same classification) returns `None`.
2. `test_reobservation_fills_previously_empty_metadata_value` — first observation carries `metadata={"framework": ""}`; second observation (same identity) carries `metadata={"framework": "langchain"}` → outcome `UPDATED`, `metadata_["framework"] == "langchain"`.
3. `test_reobservation_preserves_non_empty_metadata_over_new_value` — first `metadata={"framework": "langchain"}`, second `metadata={"framework": "llamaindex"}` → stays `"langchain"`, outcome `UNCHANGED`.
4. `test_same_name_with_different_asset_types_stays_separate` — one observation `provider_reference` name `openai` (→ `AI_SERVICE|openai`), one `framework_reference` name `openai` (→ `TOOL|openai`) → 2 assets.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_discovery_deduplication.py -q`
Expected: FAIL — test 2 fails (current merge keeps `""` over `"langchain"`); tests 1/3/4 may already pass (they pin current behavior).

- [ ] **Step 3: Wire `DiscoveredAssetService` to the dedup domain**

In `service.py`:
- `__init__`: add `self.dedup = AssetDeduplicationService(db, tenant_id, request_id=request_id)`.
- `record_observation`: replace inline `build_normalized_identity(...)` + `repository.find_asset_by_identity(...)` with `key = self.dedup.build_key(observation.source_id, classification)` and `existing = self.dedup.find_duplicate(key)`; pass `key.asset_type`/`key.normalized_identity` to `_create`. Guards (`MAX_NAME_LENGTH`, `has_secret_shaped_value`), `_verify_references`, and outcome logging stay exactly where they are.
- `_refresh`: replace `merged = {**metadata, **existing_metadata}` with the module-level `merged, changed = merge_metadata(existing_metadata, metadata)` (imported from `deduplication`; it is a pure function, not a service method).
- Update the `DiscoveredAssetService` class docstring: catalog-level identity now delegates to `deduplication` (SF6); cross-source identity remains SF7.
- Update stale docstrings: `catalog.py` module docstring ("belongs to Sub-feature 6" → "implemented in `deduplication`"), `models.py::DiscoveredAsset` and `connectors/base.py` module docstring (SF6 done, SF7/9 still pending). **No logic changes in these files.**

- [ ] **Step 4: Run new + regression tests**

Run: `uv run pytest tests/integration/test_discovery_deduplication.py tests/integration/test_discovered_asset_service.py tests/unit -q`
Expected: all PASS (16 unit-dedup + 18 service + unit suite).

- [ ] **Step 5: Lint**

Run: `uv run ruff check app/modules/asset_discovery tests/integration/test_discovery_deduplication.py && uv run ruff format --check app/modules/asset_discovery tests/integration/test_discovery_deduplication.py`
Expected: clean.

- [ ] **Step 6: Commit (pending confirmation)**

```bash
git add backend/app/modules/asset_discovery backend/tests/integration/test_discovery_deduplication.py
git commit -m "feat(asset-discovery): catalog persistence delegates identity and metadata merging to deduplication"
```

---

### Task 4: Discovery-run traceability (`discovered_asset_runs`)

Minimum architectural change so an asset observed in Run 1, 2, 3 keeps that history after `last_seen_run_id` advances. This is a run-link table only — no evidence content (SF9).

**Files:**
- Modify: `backend/app/modules/asset_discovery/models.py` (add `DiscoveredAssetRun`)
- Create: `backend/alembic/versions/0011_discovered_asset_runs.py`
- Modify: `backend/app/modules/asset_discovery/repository.py`
- Modify: `backend/app/modules/asset_discovery/service.py` (`_create`, `_refresh`, new `observed_run_ids`)
- Modify: `backend/tests/conftest.py` (TRUNCATE list)
- Test: `backend/tests/integration/test_discovery_deduplication.py`
- Test: `backend/tests/integration/test_migration.py`

**Interfaces:**
- Produces:
  ```python
  # models.py
  class DiscoveredAssetRun(Base):  # table discovered_asset_runs
      # id (uuid PK), tenant_id, discovered_asset_id (FK RESTRICT),
      # discovery_run_id (FK RESTRICT), first_observed_at, created_at, updated_at
      # uq_discovered_asset_runs_asset_run (discovered_asset_id, discovery_run_id) unique
      # ix_discovered_asset_runs_tenant_run (tenant_id, discovery_run_id)

  # repository.py
  def record_asset_run(db, *, discovered_asset_id: UUID, discovery_run_id: UUID,
                       observed_at: datetime) -> None   # upsert + commit
  def list_asset_run_ids(db, tenant_id: UUID, asset_id: UUID) -> list[UUID]
  def list_run_asset_ids(db, tenant_id: UUID, run_id: UUID) -> list[UUID]

  # service.py
  def observed_run_ids(self, asset_id: UUID) -> list[UUID]  # 404 via get() when unknown
  ```

- [ ] **Step 1: Write the failing migration test**

In `tests/integration/test_migration.py`, add `test_discovered_asset_runs_migration_downgrade_and_upgrade(database)` following the existing pattern: assert table exists; unique index `uq_discovered_asset_runs_asset_run` exists with `unique is True` and columns `[discovered_asset_id, discovery_run_id]`; index `ix_discovered_asset_runs_tenant_run` exists; FKs to `discovered_assets` and `discovery_runs` both `ondelete == "RESTRICT"`; `command.downgrade(cfg, "0010")` drops `discovered_asset_runs` but keeps `discovered_assets`; `command.upgrade(cfg, "head")` restores it.

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/integration/test_migration.py::test_discovered_asset_runs_migration_downgrade_and_upgrade -q`
Expected: FAIL — `has_table("discovered_assets_runs")` false / table missing.

- [ ] **Step 3: Write the failing traceability tests**

Add to `tests/integration/test_discovery_deduplication.py`:

1. `test_create_records_run_link` — after `record_observation`, `service.observed_run_ids(asset.id) == [run.id]`.
2. `test_reobservation_in_same_run_records_single_link` — two observations, same run → `observed_run_ids == [run.id]` (length 1).
3. `test_same_asset_across_three_runs_records_all_runs` — record against run1, then run2, then run3 with a `time.sleep(0.02)` between each (same pattern as the existing re-observation test) so `first_observed_at` values are strictly increasing → same `asset.id`; `first_seen_at` unchanged; `last_seen_at > first_seen_at`; `last_seen_run_id == run3.id`; `observed_run_ids(asset.id) == [run1.id, run2.id, run3.id]` ordered by `first_observed_at`.
4. `test_run_side_lookup_returns_observed_assets` — `repository.list_run_asset_ids(db, TENANT, run2.id)` contains the asset id after run2 observed it.
5. `test_observed_run_ids_rejects_unknown_asset` — `DiscoveredAssetNotFoundError`.

- [ ] **Step 4: Run them to verify they fail**

Run: `uv run pytest tests/integration/test_discovery_deduplication.py -q`
Expected: FAIL — `AttributeError`/`DiscoveredAssetNotFoundError` (method and table absent).

- [ ] **Step 5: Implement model + migration**

- `models.py`: add `DiscoveredAssetRun` exactly per the Interfaces block (follow `AgentModelAssociation` conventions: surrogate uuid PK, `func.now()` timestamps, named constraints). Import it where `DiscoveredAsset` is imported only as needed.
- `0011_discovered_asset_runs.py`: `revision="0011"`, `down_revision="0010"`; `create_table` with the columns/unique index/index/FKs above; `downgrade` drops index + table only. Include a comment: link rows are historical traceability, never mutated in place.
- `tests/conftest.py`: add `discovered_asset_runs` to the TRUNCATE table list.

- [ ] **Step 6: Implement repository + service wiring**

- `repository.record_asset_run`: `sqlalchemy.dialects.postgresql.insert(DiscoveredAssetRun.__table__).values(...).on_conflict_do_nothing()` → `db.execute(stmt)` → `db.commit()`. `ON CONFLICT DO NOTHING` keeps concurrent refreshes of the same (asset, run) pair race-free.
- `repository.list_asset_run_ids`: `select(discovery_run_id).where(discovered_asset_id == asset_id).order_by(first_observed_at, discovery_run_id)`.
- `repository.list_run_asset_ids`: `select(discovered_asset_id).where(tenant_id == tenant_id, discovery_run_id == run_id).order_by(discovered_asset_id)`.
- `service._create`: **after** the existing `commit_asset` success path, call `record_asset_run(...)` (asset row already committed, so the link insert cannot mask a duplicate-identity race). Keep the `DuplicateDiscoveredAssetError` recovery branch untouched.
- `service._refresh`: replace `self.db.commit()` with `record_asset_run(...)` — its `db.execute` autoflushes the pending metadata/timestamp updates and the upsert commits everything in one transaction.
- `service.observed_run_ids(asset_id)`: `self.get(asset_id)` first (tenant-scoped 404), then `repository.list_asset_run_ids`.

- [ ] **Step 7: Run new + regression tests**

Run: `uv run pytest tests/integration/test_discovery_deduplication.py tests/integration/test_migration.py tests/integration/test_discovered_asset_service.py tests/integration/test_discovery_catalog_execution.py -q`
Expected: all PASS.

- [ ] **Step 8: Lint**

Run: `uv run ruff check app/modules/asset_discovery alembic/versions/0011_discovered_asset_runs.py tests && uv run ruff format --check app/modules/asset_discovery alembic/versions/0011_discovered_asset_runs.py tests`
Expected: clean.

- [ ] **Step 9: Commit (pending confirmation)**

```bash
git add backend/app/modules/asset_discovery backend/alembic/versions/0011_discovered_asset_runs.py backend/tests/conftest.py backend/tests/integration
git commit -m "feat(asset-discovery): discovered asset run traceability table and migration 0011"
```

---

### Task 5: Batch-friendly catalog persistence (prefetch)

**Files:**
- Modify: `backend/app/modules/asset_discovery/repository.py` (add `find_assets_by_identities`)
- Modify: `backend/app/modules/asset_discovery/service.py` (`record_observations` restructure; `record_observation` shares helpers)
- Test: `backend/tests/integration/test_discovery_deduplication.py`

**Interfaces:**
- Produces:
  ```python
  def find_assets_by_identities(db, tenant_id: UUID, source_id: UUID,
                                identities: Sequence[str]) -> dict[str, DiscoveredAsset]
  ```
- `record_observation(observation) -> tuple[DiscoveredAsset | None, ObservationOutcome]` and
  `record_observations(observations) -> CatalogSummary` keep their exact signatures and outcomes.

- [ ] **Step 1: Write the failing tests**

Add to `tests/integration/test_discovery_deduplication.py`:

1. `test_batch_across_two_sources_prefetches_per_source` — one `record_observations` batch containing 2 observations for source A (`gpt-4o` twice) and 2 for source B (same identity) → `created == 2`, `unchanged == 2`, and 2 total assets (identities scoped per source).
2. `test_batch_with_invalid_observation_persists_nothing` — batch `[valid, observation with observation_type="mystery"]` → raises `InvalidDiscoveredAssetError` and `list(DiscoveredAssetFilter()).total == 0` (batch validates before persisting).
3. Re-run `test_record_observations_counts_batch_outcomes` (existing) as regression — counts must remain `created=2, unchanged=1, skipped=1`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_discovery_deduplication.py -q`
Expected: test 2 FAILS (today observation 1 persists before the invalid one raises); tests 1/3 pass.

- [ ] **Step 3: Implement prefetch**

- `repository.find_assets_by_identities`: one `select(...).where(tenant_id, source_id, normalized_identity.in_(identities))` → `{asset.normalized_identity: asset}`.
- `service.py`: extract a private `_Prepared` dataclass (`observation, classification, key, metadata, skip_reason`) and `_prepare(observation)` that runs classify → name check → `build_key` → `build_asset_metadata` → secret/oversize guards (returns `skip_reason` instead of logging).
- `record_observation`: `_prepare` → if skipped: `_log_skip` + return `(None, SKIPPED)` → else `_ingest(prepared, known=None)`.
- `record_observations`: prepare **all** observations first (an invalid one raises before any persistence — the intended atomic-validation change, pinned by test 2), then prefetch matches once per distinct `source_id` via `find_assets_by_identities` into `known: dict[tuple[UUID, str], DiscoveredAsset]`, then loop `_ingest(prepared, known)`; count outcomes and log the summary exactly as today.
- `_ingest(prepared, known)`: `existing = known.get((source_id, identity))` if `known` is not None, else `None`; fall back to `self.dedup.find_duplicate(key)`; then `_refresh` or `_create` as today; store the resulting asset in `known` (covers within-batch duplicates and race recovery). `_verify_references` stays inside `_ingest` for non-skipped observations only.

- [ ] **Step 4: Run new + regression tests**

Run: `uv run pytest tests/integration/test_discovery_deduplication.py tests/integration/test_discovered_asset_service.py tests/integration/test_discovery_catalog_execution.py tests/integration/test_discovery_classification.py -q`
Expected: all PASS.

- [ ] **Step 5: Lint**

Run: `uv run ruff check app/modules/asset_discovery tests && uv run ruff format --check app/modules/asset_discovery tests`
Expected: clean.

- [ ] **Step 6: Commit (pending confirmation)**

```bash
git add backend/app/modules/asset_discovery backend/tests/integration/test_discovery_deduplication.py
git commit -m "feat(asset-discovery): batch prefetch of existing assets in catalog persistence"
```

---

### Task 6: Deduplication acceptance suite (idempotency, cross-source, false merges)

**Files:**
- Test: `backend/tests/integration/test_discovery_deduplication.py` (extend)

**Interfaces:** consumes `DiscoveryExecutionService`, `DiscoveredAssetService`, `repository` as already used by existing tests.

- [ ] **Step 1: Write the failing acceptance tests**

Fixture `repo(tmp_path)` copied from `test_discovery_catalog_execution.py` (`app/main.py` with `import openai` + `model = "gpt-4o"`, `requirements.txt` with `langchain`).

1. `test_three_runs_against_unchanged_repo_yield_identical_catalog` — execute 3 runs against the same repo → asset-id set identical after every run; `created==3, updated==0, unchanged==0` on run 1 and `created==0, updated==0, unchanged==3` on runs 2 and 3 (run counts via `_refetch_run` pattern); each asset's `first_seen_at` frozen at run 1; `last_seen_run_id` == run 3; `observed_run_ids` == `[run1, run2, run3]`.
2. `test_same_path_and_name_in_two_sources_never_merges` — two sources, each with an observation name `gpt-4o` at identical `file_path` `app/llm.py` → 2 assets, distinct ids, same `normalized_identity` string, each with its own run link only.
3. `test_cross_source_shared_identity_stays_separate` — identical classification key built for two sources: `find_duplicate` on source B's key returns `None` even though source A holds the same identity (dedup ≠ reconciliation; SF7 will relate them).
4. `test_similar_names_do_not_merge_in_one_source` — observations `gpt-4o`, `gpt-4o-mini`, `GPT-4o` in one source → 3 assets.
5. `test_same_framework_in_two_sources_stays_separate` — `framework_reference` `langchain` in two sources → 2 assets.

- [ ] **Step 2: Run them to verify they pass (acceptance)**

Run: `uv run pytest tests/integration/test_discovery_deduplication.py -q`
Expected: PASS. If any fails, the implementation (not the test) is wrong unless the test itself contradicts the spec — re-read `docs/private/asset-discovery.md` §9/§18 before touching either.

- [ ] **Step 3: Commit (pending confirmation)**

```bash
git add backend/tests/integration/test_discovery_deduplication.py
git commit -m "test(asset-discovery): deduplication idempotency, cross-source, and false-merge coverage"
```

---

### Task 7: Full validation and regression

**Files:** none (verification gate; fix regressions caused by this work only).

- [ ] **Step 1: Formatting and lint**

Run: `uv run ruff check . && uv run ruff format --check .` (workdir `C:\Auditra\backend`)
Expected: clean; if `format --check` flags only files this plan touched, run `uv run ruff format <those files>` and re-run.

- [ ] **Step 2: Full test suite**

Run: `uv run pytest -q`
Expected: all tests pass (baseline count + all new tests). Any failure caused by this implementation must be fixed before proceeding; unrelated pre-existing failures must be reported, not papered over.

- [ ] **Step 3: Targeted confirmation of the required suites**

Run: `uv run pytest tests/unit/test_asset_deduplication.py tests/integration/test_discovery_deduplication.py tests/integration/test_discovered_asset_service.py tests/api/test_discovered_assets_api.py tests/api/test_discovery_execution_api.py tests/integration/test_migration.py -q`
Expected: all PASS (dedup + catalog service/APIs + migration, including the existing `test_uniqueness_constraint_rejects_duplicate_identity` DB-constraint test).

- [ ] **Step 4: Alembic sanity**

Run: `uv run alembic upgrade head && uv run alembic downgrade 0010 && uv run alembic upgrade head` (workdir `C:\Auditra\backend`)
Expected: no errors; final state at `0011`.

- [ ] **Step 5: Final report**

Report files created/modified, migration details, identity strategy, algorithm, test results, and anything deferred to SF7–9 (per the task's section 19). Do not push until the human confirms the git policy.
