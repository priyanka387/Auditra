# Discovery Evidence Implementation Plan (Sub-feature 9)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve append-only, structured evidence for every discovery observation so each discovered asset answers "why does Auditra believe this exists, where was it found, and which detection signal identified it?" — without re-running discovery or storing raw source content.

**Architecture:** A new `discovery_evidence` table holds one row per observation per run, foreign-keyed to `discovered_assets`, `discovery_sources` and `discovery_runs`. Rows are written inside the existing catalog ingestion path (prepare → create/refresh) from the `DiscoveryObservation` the pipeline already produced, and read back through a nested read-only API on the discovered asset. Evidence never touches classification, deduplication, reconciliation or lifecycle.

**Tech Stack:** FastAPI + SQLAlchemy 2.0 + Alembic + PostgreSQL (JSONB), Pydantic v2, pytest, ruff (line-length 100).

**Spec:** `docs/private/asset-discovery.md` §12 (Discovery Evidence), §16 (Database), §17 (Security), §22 (Definition of Done). Design decisions approved in-session; this plan argues from that spec.

## Global Constraints

- Static analysis only: never execute scanned content, never store raw source files, code snippets, credentials, tokens or secrets (spec §17). Evidence stores path/line/rule/structured metadata only.
- Evidence is append-only: no update or delete code paths anywhere; a new run only ever inserts new rows (spec §12, brief §14).
- Evidence is generated from observations the existing pipeline already produced — never by re-discovering (brief §1).
- Boundaries: evidence does not classify, deduplicate, reconcile or drive lifecycle (brief §10–13); those modules are not modified except for the two documented hooks below.
- PostgreSQL is the source of truth; every schema change ships as a reversible Alembic migration (spec §16, §22).
- The entire existing test suite must stay green after every task (spec §22).
- Vocabulary must be deterministic and constraint-enforced: `evidence_type IN ('provider_reference', 'framework_reference', 'model_reference', 'agent_reference', 'workflow_reference')` — exactly the observation types `connectors/base.py` produces; no invented types.
- Work on branch `feature/asset-discovery`; no force-push; push to `origin` only after Task 5 passes.
- ruff: `ruff check .` clean; format only files this plan touches.

## Review Focus

1. **Secret-shaped observations must produce neither asset nor evidence** — a `model = "sk-proj-..."` assignment is skipped by the catalog guard; if evidence were written before the guard, credentials would leak. Pinned by `test_skipped_secret_observation_produces_no_asset_and_no_evidence` (Task 3).
2. **Evidence rows must accumulate across runs without mutating old rows** — run 2 against an unchanged source appends new rows while run 1's rows keep their ids and `observed_at`. Pinned by `test_second_run_appends_evidence_without_mutating_previous_run_rows` (Task 3).
3. **Multiple evidence rows must not create or split assets** — two files importing `openai` yield ONE `AI_SERVICE|openai` asset with TWO evidence rows; asset count is unchanged. Pinned by `test_multiple_observations_of_one_asset_produce_multiple_evidence_rows` (Task 3).
4. **Lifecycle transitions must never delete evidence** — a DISAPPEARED asset keeps its historical rows. Pinned by `test_evidence_survives_lifecycle_status_change` (Task 3).
5. **Cross-tenant and unknown assets must 404 on the evidence endpoint** — tenant-scoped existence check before any listing; invalid `sort_by`/filter values return 400 `DISCOVERY_EVIDENCE_QUERY_INVALID`, never 500. Pinned by `test_evidence_for_unknown_asset_returns_404` and `test_invalid_sort_field_returns_query_error` (Task 4).

---

### Task 1: Evidence schema foundation (enum, errors, model, migration 0014)

**Files:**
- Modify: `backend/app/modules/asset_discovery/enums.py`
- Modify: `backend/app/modules/asset_discovery/errors.py`
- Modify: `backend/app/modules/asset_discovery/models.py`
- Create: `backend/alembic/versions/0014_discovery_evidence.py`
- Modify: `backend/tests/conftest.py` (TRUNCATE list)
- Test: `backend/tests/integration/test_migration.py`, `backend/tests/unit/test_discovery_evidence.py`

**Interfaces:**
- Consumes: `app.core.db.Base`, existing `discovery_sources`/`discovery_runs`/`discovered_assets` tables (migration chain head is `0013`).
- Produces (relied on by later tasks):
  - `EvidenceType(str, Enum)` with members `PROVIDER_REFERENCE = "provider_reference"`, `FRAMEWORK_REFERENCE = "framework_reference"`, `MODEL_REFERENCE = "model_reference"`, `AGENT_REFERENCE = "agent_reference"`, `WORKFLOW_REFERENCE = "workflow_reference"` (values mirror the constants in `connectors/base.py`; comment that tie).
  - `DiscoveryEvidenceError` (`code="DISCOVERY_EVIDENCE_ERROR"`, 400), `InvalidDiscoveryEvidenceError` (`code="DISCOVERY_EVIDENCE_INVALID"`, 422), `InvalidDiscoveryEvidenceQueryError` (`code="DISCOVERY_EVIDENCE_QUERY_INVALID"`, 400) — all subclass `DomainError` via `DiscoveryEvidenceError`.
  - `models.DiscoveryEvidence` — table `discovery_evidence`.
  - Migration revision `"0014"`, `down_revision="0013"`.

- [x] **Step 1: Write the failing tests**

Add to `backend/tests/unit/test_discovery_evidence.py` (new file):

```python
"""Unit tests: Discovery Evidence (Sub-feature 9)."""

from app.modules.asset_discovery.enums import EvidenceType


def test_evidence_type_vocabulary_matches_observation_types():
    assert [member.value for member in EvidenceType] == [
        "provider_reference",
        "framework_reference",
        "model_reference",
        "agent_reference",
        "workflow_reference",
    ]


def test_evidence_type_values_equal_connector_observation_constants():
    from app.modules.asset_discovery.connectors import base

    assert EvidenceType.PROVIDER_REFERENCE == base.PROVIDER_REFERENCE
    assert EvidenceType.FRAMEWORK_REFERENCE == base.FRAMEWORK_REFERENCE
    assert EvidenceType.MODEL_REFERENCE == base.MODEL_REFERENCE
    assert EvidenceType.AGENT_REFERENCE == base.AGENT_REFERENCE
    assert EvidenceType.WORKFLOW_REFERENCE == base.WORKFLOW_REFERENCE
```

Add to `backend/tests/integration/test_migration.py`, following the SF7/SF8 pattern:

```python
def test_discovery_evidence_migration_downgrade_and_upgrade(database):
    """SF9 adds the append-only discovery evidence table."""
    cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    engine = sa.create_engine(settings.database_url, poolclass=sa.pool.NullPool)
    try:
        insp = sa.inspect(engine)
        assert insp.has_table("discovery_evidence")
        indexes = {i["name"]: i for i in insp.get_indexes("discovery_evidence")}
        unique = indexes.get("uq_discovery_evidence_observation")
        assert unique is not None and unique["unique"] is True
        assert unique["column_names"] == [
            "discovered_asset_id", "discovery_run_id", "detection_rule",
            "file_path", "line_number",
        ]
        for name in (
            "ix_discovery_evidence_tenant_asset_observed_at",
            "ix_discovery_evidence_tenant_run",
        ):
            assert name in indexes, name
        fks = {fk["referred_table"]: fk for fk in insp.get_foreign_keys("discovery_evidence")}
        assert set(fks) == {"discovered_assets", "discovery_sources", "discovery_runs"}
        assert all(fk["options"].get("ondelete") == "RESTRICT" for fk in fks.values())
        checks = {c["name"] for c in insp.get_check_constraints("discovery_evidence")}
        assert "ck_discovery_evidence_evidence_type" in checks
        assert "ck_discovery_evidence_line_number" in checks
        try:
            command.downgrade(cfg, "0013")
            insp = sa.inspect(engine)
            assert not insp.has_table("discovery_evidence")
            assert insp.has_table("discovered_asset_runs")
        finally:
            command.upgrade(cfg, "head")
            assert sa.inspect(engine).has_table("discovery_evidence")
    finally:
        engine.dispose()
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/unit/test_discovery_evidence.py tests/integration/test_migration.py::test_discovery_evidence_migration_downgrade_and_upgrade -v`
Expected: FAIL — `ImportError: cannot import name 'EvidenceType'` and `assert False: discovery_evidence`.

- [x] **Step 3: Implement enum, errors, model, migration, conftest**

- `enums.py`: add `EvidenceType` as specified in Interfaces, with a docstring stating these are the persisted signal categories produced by detection rules and that the exact mechanism lives in `detection_rule`.
- `errors.py`: add the three error classes as specified (mirror `AssetLifecycleError` block style).
- `models.py`: add

```python
class DiscoveryEvidence(Base):
    """Append-only provenance of one discovery observation (Sub-feature 9). ..."""
    __tablename__ = "discovery_evidence"
    __table_args__ = (
        CheckConstraint(
            "evidence_type IN ('provider_reference', 'framework_reference', "
            "'model_reference', 'agent_reference', 'workflow_reference')",
            name="ck_discovery_evidence_evidence_type",
        ),
        CheckConstraint(
            "line_number IS NULL OR (line_number >= 1 AND line_number <= 10000000)",
            name="ck_discovery_evidence_line_number",
        ),
        Index(
            "uq_discovery_evidence_observation",
            "discovered_asset_id", "discovery_run_id", "detection_rule",
            "file_path", "line_number",
            unique=True,
        ),
        Index("ix_discovery_evidence_tenant_asset_observed_at",
              "tenant_id", "discovered_asset_id", "observed_at"),
        Index("ix_discovery_evidence_tenant_run", "tenant_id", "discovery_run_id"),
    )
```

Columns (in order): `id` (pk, default `uuid4`), `tenant_id`, `discovered_asset_id` (FK `discovered_assets.id` `ondelete="RESTRICT"`), `discovery_source_id` (FK `discovery_sources.id` RESTRICT), `discovery_run_id` (FK `discovery_runs.id` RESTRICT), `evidence_type` `String(32)`, `detection_rule` `String(255)`, `file_path` `String(1024)`, `line_number` `Integer` nullable, `observed_at` `DateTime(timezone=True)`, `metadata_` mapped to column `"metadata"` (JSON with JSONB variant, `default=dict`, `server_default=text("'{}'")`) — copy the exact `metadata_` idiom from `DiscoveredAsset`, `created_at`/`updated_at` (`server_default=func.now()`, `onupdate=func.now()` on updated). Class docstring must state: append-only (never updated/deleted), written only by discovery execution, no raw source content.

- Migration `0014_discovery_evidence.py` (copy the header/style of `0013_asset_lifecycle.py`, `revision="0014"`, `down_revision="0013"`): `upgrade()` creates the table with `sa.Column`s matching the model exactly (`sa.Uuid()`, `sa.DateTime(timezone=True)`, `sa.JSON().with_variant(postgresql.JSONB, "postgresql")` with `server_default=sa.text("'{}'")`, `created_at`/`updated_at` with `server_default=sa.text("now()")`), the two check constraints, three FKs (`ondelete="RESTRICT"`), `sa.PrimaryKeyConstraint("id")`, then `op.create_index` for the unique index and two query indexes (unique index via `op.create_index(name, table, [...], unique=True)`). `downgrade()` drops the two non-unique indexes, the unique index, then `op.drop_table("discovery_evidence")` (FKs go with the table). Comment: no data migration needed — the table is new and empty on upgrade; downgrade only drops evidence history (acceptable: it is derived from re-runnable scans).
- `tests/conftest.py`: prepend `discovery_evidence,` to the TRUNCATE table list in the `db` fixture (line ~55).

- [x] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/unit/test_discovery_evidence.py tests/integration/test_migration.py -v`
Expected: PASS (all migration tests, both new unit tests).

- [x] **Step 5: Commit**

```bash
git add backend/app/modules/asset_discovery/enums.py backend/app/modules/asset_discovery/errors.py backend/app/modules/asset_discovery/models.py backend/alembic/versions/0014_discovery_evidence.py backend/tests/conftest.py backend/tests/unit/test_discovery_evidence.py backend/tests/integration/test_migration.py
git commit -m "feat(asset-discovery): discovery evidence model, enum, errors, and migration 0014 (SF9)"
```

---

### Task 2: Evidence builders (`evidence.py`)

**Files:**
- Create: `backend/app/modules/asset_discovery/evidence.py`
- Test: `backend/tests/unit/test_discovery_evidence.py` (extend)

**Interfaces:**
- Consumes: `DiscoveryObservation` (`connectors/base.py`), `EvidenceType` (Task 1), `catalog.validate_file_path`, `catalog.MAX_LINE_NUMBER` (= 10_000_000), `model_inventory.schemas.model.validate_metadata`, `InvalidDiscoveryEvidenceError` (Task 1).
- Produces (relied on by Task 3):
  - `EvidenceType`-backed frozen dataclass `EvidenceDraft` with fields `evidence_type: str`, `detection_rule: str`, `file_path: str`, `line_number: int | None`, `metadata: dict[str, Any]`.
  - `build_evidence(observation: DiscoveryObservation) -> EvidenceDraft` — raises `InvalidDiscoveryEvidenceError` for unknown observation types, empty/over-long detection rules, invalid line numbers, invalid paths (wrapping `InvalidDiscoveredAssetError` from `validate_file_path`) and invalid metadata.

- [x] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_discovery_evidence.py`:

```python
import pytest

from app.modules.asset_discovery.connectors.base import (
    MODEL_REFERENCE,
    PROVIDER_REFERENCE,
    DiscoveryObservation,
)
from app.modules.asset_discovery.errors import InvalidDiscoveryEvidenceError
from app.modules.asset_discovery.evidence import EvidenceDraft, build_evidence
from uuid import uuid4


def _observation(**overrides) -> DiscoveryObservation:
    fields = dict(
        source_id=uuid4(),
        run_id=uuid4(),
        observation_type=PROVIDER_REFERENCE,
        name="openai",
        provider="openai",
        file_path="app/main.py",
        line_number=1,
        detection_rule="provider.openai.import",
        metadata={},
    )
    fields.update(overrides)
    return DiscoveryObservation(**fields)


def test_build_evidence_returns_structured_fields_only():
    draft = build_evidence(_observation(metadata={"framework": "langchain"}))
    assert isinstance(draft, EvidenceDraft)
    assert draft.evidence_type == "provider_reference"
    assert draft.detection_rule == "provider.openai.import"
    assert draft.file_path == "app/main.py"
    assert draft.line_number == 1
    assert draft.metadata == {"framework": "langchain", "provider": "openai"}


def test_build_evidence_maps_model_reference_type():
    draft = build_evidence(
        _observation(
            observation_type=MODEL_REFERENCE,
            name="gpt-4o",
            provider=None,
            detection_rule="model.assignment",
            metadata={"key": "model"},
        )
    )
    assert draft.evidence_type == "model_reference"
    assert draft.metadata == {"key": "model"}  # no provider key when unknown


def test_build_evidence_rejects_unknown_observation_type():
    with pytest.raises(InvalidDiscoveryEvidenceError, match="unsupported evidence type"):
        build_evidence(_observation(observation_type="mystery_signal"))


def test_build_evidence_rejects_absolute_path():
    with pytest.raises(InvalidDiscoveryEvidenceError, match="relative to the source root"):
        build_evidence(_observation(file_path="/etc/passwd"))


def test_build_evidence_rejects_invalid_line_number():
    for bad in (0, -1, True, 10_000_001):
        with pytest.raises(InvalidDiscoveryEvidenceError, match="line_number"):
            build_evidence(_observation(line_number=bad))


def test_build_evidence_rejects_secret_shaped_metadata_key():
    with pytest.raises(InvalidDiscoveryEvidenceError, match="metadata"):
        build_evidence(_observation(metadata={"api_key": "value"}))


def test_build_evidence_rejects_empty_or_overlong_detection_rule():
    with pytest.raises(InvalidDiscoveryEvidenceError, match="detection_rule"):
        build_evidence(_observation(detection_rule=""))
    with pytest.raises(InvalidDiscoveryEvidenceError, match="detection_rule"):
        build_evidence(_observation(detection_rule="x" * 256))
```

(Replace the awkward conditional in `test_build_evidence_rejects_absolute_path` with a plain `InvalidDiscoveryEvidenceError` assertion — `build_evidence` wraps path failures.)

- [x] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/unit/test_discovery_evidence.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.modules.asset_discovery.evidence'`.

- [x] **Step 3: Implement `build_evidence` in `backend/app/modules/asset_discovery/evidence.py`**

Module docstring: Discovery Evidence domain (Sub-feature 9) — evidence answers *what was observed and where*; classification answers *what it represents* (no classification logic here); structured fields only, never raw source content; pure functions, no database access (persistence is `DiscoveredAssetService` + `repository.record_evidence`).

```python
MAX_DETECTION_RULE_LENGTH = 255

@dataclass(frozen=True)
class EvidenceDraft:
    evidence_type: str
    detection_rule: str
    file_path: str
    line_number: int | None
    metadata: dict[str, Any]

def build_evidence(observation: DiscoveryObservation) -> EvidenceDraft: ...
```

Body decisions (the tests pin these): map `observation.observation_type` through `EvidenceType(...)` catching `ValueError` → `InvalidDiscoveryEvidenceError(f"unsupported evidence type '{...}'")`; `file_path = validate_file_path(observation.file_path)` wrapped so `InvalidDiscoveredAssetError` becomes `InvalidDiscoveryEvidenceError` with the original message; `detection_rule` stripped and checked non-empty and `len <= MAX_DETECTION_RULE_LENGTH`; `line_number` validated exactly like `catalog.build_asset_metadata` does (reject `bool`, non-`int`, `< 1`, `> MAX_LINE_NUMBER` with `"line_number is invalid"` message); `metadata = dict(observation.metadata or {})`, add `"provider": observation.provider` only when truthy, then `validate_metadata(metadata)` wrapped `(TypeError, ValueError)` → `InvalidDiscoveryEvidenceError(f"evidence metadata is invalid: {exc}")`.

- [x] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/unit/test_discovery_evidence.py -v`
Expected: PASS (all tests in the file).

- [x] **Step 5: Commit**

```bash
git add backend/app/modules/asset_discovery/evidence.py backend/tests/unit/test_discovery_evidence.py
git commit -m "feat(asset-discovery): evidence builders for discovery observations (SF9)"
```

---

### Task 3: Catalog write path persists evidence

**Files:**
- Modify: `backend/app/modules/asset_discovery/repository.py`
- Modify: `backend/app/modules/asset_discovery/service.py`
- Test: `backend/tests/integration/test_discovery_evidence.py` (new)

**Interfaces:**
- Consumes: `EvidenceDraft`, `build_evidence` (Task 2); `DiscoveredAssetService._prepare`/`_ingest`/`_create`/`_refresh` and `_Prepared` (existing, `service.py`); `pg_insert` idiom from `repository.record_asset_run`.
- Produces (relied on by Task 4 and tests):
  - `repository.record_evidence(db: Session, **fields) -> None` — `pg_insert(DiscoveryEvidence.__table__).values(**fields).on_conflict_do_nothing(index_elements=["discovered_asset_id", "discovery_run_id", "detection_rule", "file_path", "line_number"])`, executed **without commit** (the following `record_asset_run` commit persists asset + link + evidence together). `fields` keys use column names, i.e. `metadata=` (not `metadata_=`), plus `id`, `tenant_id`, `discovered_asset_id`, `discovery_source_id`, `discovery_run_id`, `evidence_type`, `detection_rule`, `file_path`, `line_number`, `observed_at`.
  - `_Prepared.evidence: EvidenceDraft | None` — `None` exactly when `skip_reason` is set.
  - `_create(..., evidence: EvidenceDraft)` and `_refresh(..., evidence: EvidenceDraft)` record one evidence row before `repository.record_asset_run`.

- [x] **Step 1: Write the failing integration tests**

New `backend/tests/integration/test_discovery_evidence.py`, using the service/execution helpers pattern from `test_discovery_catalog_execution.py`:

```python
"""Integration tests: evidence rows produced by discovery execution (SF9)."""

import pytest

from app.core.config import settings
from app.modules.asset_discovery.enums import DiscoverySourceType
from app.modules.asset_discovery.execution import DiscoveryExecutionService
from app.modules.asset_discovery.schemas import DiscoverySourceCreate
from app.modules.asset_discovery.service import (
    DiscoveryRunService,
    DiscoverySourceService,
)

TENANT = settings.default_tenant_id


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text(
        'import openai\nmodel = "gpt-4o"\n', encoding="utf-8"
    )
    (tmp_path / "app" / "client.py").write_text("import openai\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("langchain>=0.1.0\n", encoding="utf-8")
    return tmp_path


def _source(db, location, name="payments-service"):
    return DiscoverySourceService(db, TENANT).create(
        DiscoverySourceCreate(
            name=name, source_type=DiscoverySourceType.REPOSITORY,
            config={"location": str(location)},
        )
    )


def _execute(db, source):
    run = DiscoveryRunService(db, TENANT).create(source.id)
    return DiscoveryExecutionService(db, TENANT).execute(run.id)


def _evidence_rows(db, asset_id):
    from sqlalchemy import select
    from app.modules.asset_discovery.models import DiscoveryEvidence

    return list(
        db.scalars(
            select(DiscoveryEvidence)
            .where(DiscoveryEvidence.tenant_id == TENANT,
                   DiscoveryEvidence.discovered_asset_id == asset_id)
            .order_by(DiscoveryEvidence.file_path, DiscoveryEvidence.line_number)
        )
    )
```

Tests (names are the contract):

1. `test_execution_records_one_evidence_row_per_observation` — execute against `repo`; fetch the three assets (`AI_SERVICE|openai` has 2 rows from `app/main.py` + `app/client.py`; `MODEL_REFERENCE|gpt-4o` and `TOOL|langchain` have 1 each); total rows == `len(result.observations)`; every row has `evidence_type` in the 5-value vocabulary, non-empty `detection_rule`, relative `file_path`, `observed_at` between run start/completion, `discovery_run_id == run.id`, `discovery_source_id == source.id`, `discovered_asset_id == asset.id`; for `MODEL_REFERENCE|gpt-4o` assert `file_path == "app/main.py"`, `line_number == 2`, `detection_rule == "model.assignment"`, `metadata == {"key": "model"}`.
2. `test_multiple_observations_of_one_asset_produce_multiple_evidence_rows` — `AI_SERVICE|openai` asset count is 1, its evidence rows are exactly `{("app/client.py", 1, "provider.openai.import"), ("app/main.py", 1, "provider.openai.import")}`.
3. `test_second_run_appends_evidence_without_mutating_previous_run_rows` — snapshot `{(row.id, row.observed_at, row.file_path)}` after run 1; execute run 2; snapshot is a subset unchanged; every run-2 row has `discovery_run_id == run2.id`; total rows == 2 × run-1 total; asset `first_seen_at` unchanged.
4. `test_skipped_secret_shaped_observation_produces_no_asset_and_no_evidence` — second fixture repo containing `model = "sk-proj-abcdef123"` in `app/main.py`; execute; assert no asset name/identity contains `sk-proj` and no evidence row's `metadata`/`file_path` contains it either (query all rows).
5. `test_evidence_survives_lifecycle_status_change` — execute; pick the `MODEL_REFERENCE|gpt-4o` asset; set `asset.status = "DISAPPEARED"` directly, `db.commit()`; evidence rows for that asset are unchanged (same ids) and still returned by `_evidence_rows`.
6. `test_evidence_recording_does_not_touch_reconciliation_or_identity` — after execute, `asset.reconciliation_status == "UNMATCHED"`, `asset.normalized_identity` and `first_seen_at`/`last_seen_at` equal the pre-evidence expectations (i.e. execution's normal values), proving evidence added columns only.

Note: do **not** import `list_asset_evidence` (Task 4); use the raw `select` helper above.

- [x] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/integration/test_discovery_evidence.py -v`
Expected: FAIL — 0 evidence rows found (table exists from Task 1 but nothing writes it); assertions fail with `total == 0`.

- [x] **Step 3: Implement `record_evidence` and wire the catalog**

- `repository.py`: add `record_evidence` exactly as specified in Interfaces (place it after `record_asset_run`; docstring: append-only insert, no commit, `ON CONFLICT DO NOTHING` keeps a duplicate observation race-free, the caller's `record_asset_run` commit persists asset + link + evidence together). Import `DiscoveryEvidence` in the existing models import block.
- `service.py`:
  - import `EvidenceDraft`, `build_evidence` from `app.modules.asset_discovery.evidence`;
  - `_Prepared`: add field `evidence: EvidenceDraft | None`;
  - `_prepare`: after computing `skip_reason` (the existing `name_too_long` / `secret_shaped_value` guards), set `evidence = None if skip_reason is not None else build_evidence(observation)` and pass it into `_Prepared` — evidence is built only for observations that will persist, so a skipped observation can never produce evidence, and an unusable observation raises before anything is written (preserving the batch-atomic prepare invariant);
  - `_ingest`: pass `prepared.evidence` to both `_refresh(existing, observation, prepared.metadata, prepared.evidence)` and `_create(observation, prepared.classification.name, key.asset_type, key.normalized_identity, prepared.metadata, prepared.evidence)`;
  - `_create`: after `repository.commit_asset(self.db)` succeeds and **before** `repository.record_asset_run(...)`, call `repository.record_evidence(...)` with `id=uuid4()`, `tenant_id=self.tenant_id`, `discovered_asset_id=asset.id`, `discovery_source_id=observation.source_id`, `discovery_run_id=observation.run_id`, the five `EvidenceDraft` fields (`evidence_type`, `detection_rule`, `file_path`, `line_number`, `metadata=evidence.metadata`), and `observed_at=now` (the same `now` used for `first_seen_at`); the race-recovery path `return self._refresh(raced, observation, metadata, evidence)` forwards `evidence`;
  - `_refresh`: accept `evidence: EvidenceDraft`; before `repository.record_asset_run(...)`, call `repository.record_evidence(...)` with `observed_at=asset.last_seen_at` (already refreshed) and the same field mapping.

- [x] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/integration/test_discovery_evidence.py -v`
Expected: PASS (6 tests).

- [x] **Step 5: Run the affected regression surface**

Run: `cd backend && uv run pytest tests/integration -k "discovery or catalog or asset" -v`
Expected: PASS — existing catalog/dedup/reconciliation/lifecycle integration tests unaffected (evidence is additive).

- [x] **Step 6: Commit**

```bash
git add backend/app/modules/asset_discovery/repository.py backend/app/modules/asset_discovery/service.py backend/tests/integration/test_discovery_evidence.py
git commit -m "feat(asset-discovery): catalog persists append-only evidence per observation (SF9)"
```

---

### Task 4: Evidence read service and nested API endpoint

**Files:**
- Modify: `backend/app/modules/asset_discovery/repository.py`
- Modify: `backend/app/modules/asset_discovery/schemas.py`
- Modify: `backend/app/modules/asset_discovery/evidence.py`
- Modify: `backend/app/modules/asset_discovery/routes.py`
- Test: `backend/tests/api/test_discovery_evidence_api.py` (new)

**Interfaces:**
- Consumes: `DiscoveryEvidence` model (Task 1), `EvidenceType`, `InvalidDiscoveryEvidenceQueryError`, `DiscoveredAssetNotFoundError` (existing), route/routers in `routes.py` (`assets_router`), `_error(response)["code"]` envelope convention.
- Produces:
  - `EVIDENCE_SORT_COLUMNS = {"observed_at": DiscoveryEvidence.observed_at, "created_at": DiscoveryEvidence.created_at}` and `repository.list_asset_evidence(db, tenant_id, asset_id, filters: DiscoveryEvidenceFilter) -> tuple[list[DiscoveryEvidence], int]` — conditions: tenant + `discovered_asset_id == asset_id`, plus optional `evidence_type`, `detection_rule`, `discovery_run_id`; count query then page query `order_by(sort_column.desc()|asc(), DiscoveryEvidence.id)` with offset/limit; unknown `sort_by` → `InvalidDiscoveryEvidenceQueryError(f"unsupported sort field '{filters.sort_by}'")`.
  - `DiscoveryEvidenceService(db, tenant_id, request_id=None)` in `evidence.py` with `list(self, asset_id: UUID, filters: DiscoveryEvidenceFilter) -> tuple[list[DiscoveryEvidence], int]` — first `repository.get_discovered_asset` tenant-scoped, `None` → `DiscoveredAssetNotFoundError(f"discovered asset '{asset_id}' not found")`, else `repository.list_asset_evidence`.
  - `schemas.py`: `DiscoveryEvidenceResponse` (fields `id, tenant_id, discovered_asset_id, discovery_source_id, discovery_run_id, evidence_type: EvidenceType, detection_rule: str, file_path: str, line_number: int | None, observed_at: datetime, metadata: dict[str, Any] = Field(default_factory=dict), created_at: datetime, updated_at: datetime`, `from_attributes=True`, plus the shared `_require_timezone` validator on `observed_at/created_at/updated_at`), `DiscoveryEvidenceListResponse` (`items, page, page_size, total, total_pages`), `DiscoveryEvidenceFilter` (`extra="forbid"`, `page ge=1`, `page_size ge=1 le=100`, `evidence_type: EvidenceType | None`, `detection_rule: str | None` `max_length=255`, `discovery_run_id: UUID | None`, `sort_by: str = "observed_at"`, `sort_order: str = "desc"`).
  - Route: `GET /api/v1/discovery/assets/{asset_id}/evidence` → `DiscoveryEvidenceListResponse`.

- [x] **Step 1: Write the failing API tests**

New `backend/tests/api/test_discovery_evidence_api.py`, reusing the `_source`/`_catalog` helpers and `repo` fixture shape from `tests/api/test_discovered_assets_api.py` (main.py with `import openai` + `model = "gpt-4o"`, requirements.txt with `langchain>=0.1.0`, settings.py with `INTERNAL_TOKEN = "hunter2-do-not-leak"`):

```python
ITEM_KEYS = {
    "id", "tenant_id", "discovered_asset_id", "discovery_source_id",
    "discovery_run_id", "evidence_type", "detection_rule", "file_path",
    "line_number", "observed_at", "metadata", "created_at", "updated_at",
}
```

Tests:
1. `test_asset_evidence_endpoint_returns_all_rows` — after `_catalog`, `GET f"{ASSETS_API}/{asset_id}/evidence"` → 200; envelope keys `{items, page, page_size, total, total_pages}`; `total == 3` across assets (`AI_SERVICE|openai`: 1, `MODEL_REFERENCE|gpt-4o`: 1, `TOOL|langchain`: 1); every item `set(item) == ITEM_KEYS`; `discovered_asset_id` matches; `discovery_run_id`/`discovery_source_id` match the run/source; `evidence_type` ∈ vocabulary; `line_number >= 1`.
2. `test_evidence_filter_by_type_and_rule` — `params={"evidence_type": "model_reference"}` → 1 row (`detection_rule == "model.assignment"`); `params={"detection_rule": "provider.openai.import"}` → 1 row; `params={"discovery_run_id": run_id}` → 3 rows; unknown value `evidence_type=not_a_type` → 400 `VALIDATION_ERROR`.
3. `test_evidence_sort_and_pagination` — `page_size=2` → `total == 3`, `total_pages == 2`, `len(items) == 2`; `sort_by=created_at&sort_order=asc` → 200; `sort_by=nope` → 400 `DISCOVERY_EVIDENCE_QUERY_INVALID`.
4. `test_evidence_for_unknown_asset_returns_404` — `GET f"{ASSETS_API}/{uuid4()}/evidence"` → 404, `_error(response)["code"] == "DISCOVERED_ASSET_NOT_FOUND"`, `http_status == 404`.
5. `test_evidence_response_leaks_no_secret_content` — the settings.py secret never appears: `assert "hunter2-do-not-leak" not in response.text`; also every `file_path` is relative (no leading `/`, no drive letter) and no `metadata` value matches a credential pattern (`^(?:sk|pk|ghp|AKIA)[-_]`).
6. `test_asset_detail_response_shape_unchanged` — `GET f"{ASSETS_API}/{asset_id}"` still returns the original `ITEM_KEYS` from `test_discovered_assets_api.py` (regression: evidence did not leak into the asset payload).

- [x] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/api/test_discovery_evidence_api.py -v`
Expected: FAIL — 404 route not found (`DISCOVERY_EVIDENCE_QUERY_INVALID`/envelope absent) or import errors for the new schemas.

- [x] **Step 3: Implement repository, schemas, service, route**

- `repository.py`: `EVIDENCE_SORT_COLUMNS` + `list_asset_evidence` as specified in Interfaces (mirror `list_discovered_assets` structure exactly; import `DiscoveryEvidence` and `DiscoveryEvidenceFilter`).
- `schemas.py`: three classes as specified in Interfaces (mirror `DiscoveredAssetResponse`/`DiscoveredAssetListResponse`/`DiscoveredAssetFilter` block style; import `EvidenceType`).
- `evidence.py`: add `DiscoveryEvidenceService` as specified (imports: `Session`, `UUID`, `repository`, `DiscoveryEvidenceFilter`, `DiscoveredAssetNotFoundError`); keep `logger = logging.getLogger("auditra.asset_discovery")` and log nothing per row (reads are silent, matching `DiscoveredAssetService.list`).
- `routes.py`: import `EvidenceType`, `DiscoveryEvidenceFilter`, `DiscoveryEvidenceListResponse`, `DiscoveryEvidenceResponse`, `InvalidDiscoveryEvidenceQueryError`, `DiscoveryEvidenceService`; add `_evidence_service(request, db)` and `_build_evidence(**kwargs)` helpers mirroring `_asset_service`/`_build_assets`; add the endpoint:

```python
@assets_router.get("/{asset_id}/evidence", response_model=DiscoveryEvidenceListResponse)
def list_asset_evidence(
    asset_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    evidence_type: EvidenceType | None = None,
    detection_rule: Annotated[str | None, Query(max_length=255)] = None,
    discovery_run_id: UUID | None = None,
    sort_by: str = "observed_at",
    sort_order: str = "desc",
) -> DiscoveryEvidenceListResponse:
    """List the append-only evidence records for one discovered asset (read-only)."""
```

Body mirrors `list_discovered_assets`: build filter (ValidationError → `InvalidDiscoveryEvidenceQueryError`), call service, build response with `total_pages=ceil(total / page_size) if total else 0`.

- [x] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/api/test_discovery_evidence_api.py -v`
Expected: PASS (6 tests).

- [x] **Step 5: Run route regression**

Run: `cd backend && uv run pytest tests/api -v`
Expected: PASS — all existing API tests (evidence route must not shadow `/assets/{asset_id}`).

- [x] **Step 6: Commit**

```bash
git add backend/app/modules/asset_discovery/repository.py backend/app/modules/asset_discovery/schemas.py backend/app/modules/asset_discovery/evidence.py backend/app/modules/asset_discovery/routes.py backend/tests/api/test_discovery_evidence_api.py
git commit -m "feat(asset-discovery): asset evidence read service and nested API endpoint (SF9)"
```

---

### Task 5: Full regression, lint, and push

**Files:**
- Modify: this plan (check off boxes), nothing else expected.

**Interfaces:**
- Consumes: everything above.
- Produces: green suite on `feature/asset-discovery` pushed to `origin`.

- [x] **Step 1: Full test suite**

Run: `cd backend && uv run pytest`
Expected: PASS — baseline is 900+ tests (last recorded full run: 905 passed) plus this plan's new tests; zero failures, zero new errors.

- [x] **Step 2: Lint**

Run: `cd backend && uv run ruff check . && uv run ruff format --check <files touched by this plan>`
Expected: `ruff check` clean; format check clean for touched files (pre-existing drift in untouched files is out of scope).

- [x] **Step 3: Final commit and push**

```bash
git add -A
git commit -m "test(asset-discovery): discovery evidence regression and lint pass (SF9)"   # only if anything changed
git push origin feature/asset-discovery
```

No force-push. If the push is rejected, pull/rebase and re-run Step 1.
