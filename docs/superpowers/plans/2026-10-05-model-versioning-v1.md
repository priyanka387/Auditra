# Model Versioning V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a first-class, tenant-scoped `ModelVersion` entity under the existing `Model` registry — canonical version identity, duplicate prevention, lifecycle, metadata, archive, and six nested FastAPI endpoints.

**Architecture:** Extend the existing `model_inventory` modular monolith (same FastAPI app, same layered split: api/schemas/domain/services/repositories/models). New table `model_versions` FK'd to `models`, unique on `(tenant_id, model_id, canonical_version_key)`. Identity and lifecycle rules live in the existing `domain/` package as pure functions; the service owns business rules; the repository owns persistence and maps DB races to domain errors. Optimistic concurrency via SQLAlchemy `version_id_col` on `record_version`.

**Tech Stack:** Python 3.11+, uv (isolated venv), FastAPI, Pydantic v2, SQLAlchemy 2.0 (sync, psycopg3), Alembic, PostgreSQL 16 in Docker (host port 5433), pytest + httpx TestClient, ruff (line-length 100).

**Spec:** `docs/features/model-inventory/versioning-spec.md` (authoritative; the plan argues from it — executors read both)

## Global Constraints

- Every command runs via `uv run` from `backend/` (uv-managed isolated venv). No global pip installs.
- PostgreSQL 16 only, via root `docker-compose.yml`: db `auditra`, user `auditra`, password `auditra`, host port 5433; test db `auditra_test` (conftest drops/recreates it per session). No Redis, RustFS, Kafka, vector DB.
- Backend only. No frontend, no microservices, no auth system.
- Model Registration owns `Model` identity; this feature owns `ModelVersion` identity only. No Deployment/Endpoint/Environment/Usage/Discovery/Evaluation/Audit tables or endpoints.
- `canonical_version_key = f"{identity_type.strip().lower()}:{(native_version_id.strip() or version_label.strip())}"` — identity type lowercased, identifier case-preserved, both trimmed; native id wins over label when both present (spec §11/§13/§86).
- `identity_type` allowlist (lowercase, input normalized): `native, revision, release, checkpoint, label, opaque` (spec §12). Never a Postgres enum.
- `source_type` for versions: input case-insensitive, stored lowercase, allowlist `manual, import` (spec §36, §44, §78). Deviation from `Model`'s uppercase `MANUAL/SDK/API/IMPORT`: spec §121 makes the spec authoritative for ModelVersion domain behavior and its examples use `"manual"` in both request and response.
- Version lifecycle: `DRAFT, ACTIVE, DEPRECATED, RETIRED, ARCHIVED`. Initial state always `DRAFT`, not client-settable (spec §30; `lifecycle_state` absent from create schema → 422 if sent). Transitions exactly the 10 edges of spec §29; `ARCHIVED` terminal; same-state transition → 409 (matches existing Model convention, spec §42 option "existing project idempotency convention").
- Multiple `ACTIVE` versions per model allowed; no `current_version_id` on Model (ADR-006/007).
- Tenant: `settings.default_tenant_id`. Every repository query takes `tenant_id` as a required argument.
- Pagination: `page` ≥ 1 default 1; `page_size` default 20 max 100 (over-max → 422); version list defaults `sort_by=created_at`, `sort_order=desc`, `include_archived=false` (spec §41.2; differs from model list's 25/updated_at on purpose). Sort allowlist: `created_at, updated_at, version_label, lifecycle_state`; anything else → 400 `INVALID_SORT_FIELD`; always secondary-order `id ASC`.
- Search fields: `version_label, display_name, native_version_id, canonical_version_key` (spec §49).
- Field limits: `version_label` 1–255, `native_version_id` 1–512 (None allowed), `display_name` ≤255, `source_reference` ≤512, `description` ≤4000, `metadata` ≤10 000 serialized bytes and no secret-looking keys (reuse `validate_metadata`).
- `DELETE` = archive (204, idempotent), never physical delete. Archived versions: excluded from default list, GET still 200, PATCH/lifecycle → 409 `MODEL_VERSION_ALREADY_ARCHIVED`.
- Immutable via PATCH → 409 `MODEL_VERSION_IDENTITY_IMMUTABLE`: `model_id, identity_type, native_version_key/canonical_version_key, version_label, native_version_id` (spec §45). Mutable: `display_name, description, metadata, source_reference`.
- Error envelope unchanged: `{"error": {code, message, details, request_id, http_status}}`. New codes: `MODEL_VERSION_NOT_FOUND` 404, `MODEL_VERSION_ALREADY_EXISTS` 409, `MODEL_VERSION_IDENTITY_IMMUTABLE` 409, `INVALID_MODEL_VERSION_LIFECYCLE_TRANSITION` 409, `MODEL_VERSION_ALREADY_ARCHIVED` 409, `VERSION_CONCURRENCY_CONFLICT` 409. Reused: `MODEL_NOT_FOUND` 404, `MODEL_ARCHIVED` 409, `INVALID_SORT_FIELD` 400, `VALIDATION_ERROR` 422, `INTERNAL_ERROR` 500.
- Domain events `model_version.created / model_version.updated / model_version.lifecycle_changed / model_version.archived` through the existing in-process handler registry (spec §52). No outbox, no Kafka.
- Optimistic concurrency: `__mapper_args__ = {"version_id_col": record_version}` on the ORM class; `commit()` maps `StaleDataError` → `VersionConcurrencyConflictError` (spec §50). Service never bumps `record_version` by hand.
- No `if provider == ...` branches, no framework objects in the domain, no deployment/artifact columns on `model_versions`.

## Review Focus

Spec-implied failure modes no happy-path test covers; each has a test in the task that owns it:

1. **Concurrent duplicate create** — both requests pass the application pre-check; the DB unique constraint must reject the loser as a domain error, not a 500. Tests: Task 4 `test_duplicate_race_integrity_error_mapped` (pre-check monkeypatched to `None`, real constraint rejects) and `test_concurrent_duplicate_create_one_wins` (two sessions, barrier, one `created` + one `conflict`).
2. **Identity-bearing PATCH** — payload carrying `identity_type`/`native_version_id`/`version_label`/`canonical_version_key`/`model_id` must 409 and leave the record byte-identical. Test: Task 5 `test_patch_identity_fields_rejected`.
3. **Stale metadata update** — two writers, second commits against a version it read before the first committed; must be 409 `VERSION_CONCURRENCY_CONFLICT`, never a silent overwrite. Test: Task 4 `test_stale_metadata_update_conflict`.
4. **Archived semantics** — archived version: out of default list, present with `include_archived=true`, GET 200, PATCH 409, lifecycle 409, re-DELETE 204; creating a version under an archived parent → 409 `MODEL_ARCHIVED`. Tests: Task 5 `test_archive_workflow`, `test_create_under_archived_model_returns_409`; Task 4 `test_create_under_archived_model_rejected`.
5. **Lifecycle matrix** — `RETIRED→ACTIVE` 409, `ARCHIVED→ACTIVE` 409, same-state 409, `DRAFT→ACTIVE` 200, `DEPRECATED→ACTIVE` 200. Tests: Task 4 `test_lifecycle_transitions_match_spec` (pure matrix) + `test_lifecycle_invalid_and_same_state_rejected`; Task 5 `test_lifecycle_transitions_via_api`.
6. **Hostile inputs** — secret metadata keys 422, oversized metadata 422, unknown `identity_type` 422, `page_size=500` 422, `sort_by` never interpolated (garbage → 400), no stack traces in any error body. Tests: Task 3 `test_secret_metadata_rejected`, Task 5 `test_invalid_sort_field_returns_400`, `test_validation_errors`, `test_error_body_has_no_traceback`.

---

## File Structure

```text
C:\Auditra\
├── docker-compose.yml                              # unchanged (PostgreSQL 16 only)
├── README.md                                       # modify: list version endpoints
├── docs/
│   ├── features/model-inventory/
│   │   ├── spec.md                                 # Model Registration spec (existing)
│   │   └── versioning-spec.md                      # created: authoritative Model Versioning spec
│   └── superpowers/plans/
│       ├── 2026-10-04-model-registration-v1.md     # existing
│       └── 2026-10-05-model-versioning-v1.md       # this plan
└── backend/
    ├── alembic/versions/
    │   ├── 0001_model_registration.py              # existing
    │   └── 0002_model_versioning.py                # create
    ├── app/modules/model_inventory/
    │   ├── models/
    │   │   ├── model_version.py                    # create: ModelVersion ORM
    │   │   └── __init__.py                         # modify: export ModelVersion
    │   ├── domain/
    │   │   ├── identity.py                         # modify: VERSION_IDENTITY_TYPES, build_canonical_version_key
    │   │   ├── lifecycle.py                        # modify: VersionLifecycleState, VERSION_ALLOWED_TRANSITIONS, can_version_transition
    │   │   ├── errors.py                           # modify: 6 version error classes
    │   │   ├── events.py                           # modify: ModelVersionEvent, Event union
    │   │   └── __init__.py                         # modify: re-export
    │   ├── schemas/
    │   │   ├── model_version.py                    # create: Create/Update/Lifecycle/Response/ListResponse
    │   │   └── __init__.py                         # modify: re-export
    │   ├── repositories/
    │   │   ├── model_version_repository.py         # create: filters, queries, commit
    │   │   └── __init__.py                         # modify: re-export
    │   ├── services/
    │   │   ├── model_version_service.py            # create: ModelVersionService
    │   │   └── __init__.py                         # modify: re-export
    │   └── api/
    │       └── routes.py                           # modify: 6 nested version endpoints
    └── tests/
        ├── conftest.py                             # modify: TRUNCATE model_versions, parent_model fixture
        ├── unit/test_version_identity.py           # create
        ├── unit/test_version_lifecycle.py          # create
        ├── unit/test_version_schemas.py            # create
        ├── unit/test_events.py                     # modify: version event dispatch test
        ├── integration/test_version_persistence.py # create
        ├── integration/test_version_service.py     # create
        ├── integration/test_version_concurrency.py # create
        ├── integration/test_migration.py           # create
        ├── api/test_versions_api.py                # create
        └── api/test_models_api.py                  # modify: openapi contract 8 → 14 operations
```

---

### Task 1: Version Domain Layer (identity, lifecycle, errors, events)

**Files:**
- Create: `docs/features/model-inventory/versioning-spec.md` (already staged this session; verify it exists)
- Modify: `backend/app/modules/model_inventory/domain/identity.py`
- Modify: `backend/app/modules/model_inventory/domain/lifecycle.py`
- Modify: `backend/app/modules/model_inventory/domain/errors.py`
- Modify: `backend/app/modules/model_inventory/domain/events.py`
- Modify: `backend/app/modules/model_inventory/domain/__init__.py`
- Test: `backend/tests/unit/test_version_identity.py`, `backend/tests/unit/test_version_lifecycle.py`, `backend/tests/unit/test_events.py`

**Interfaces:**
- Consumes: existing `DomainError` hierarchy, `ModelEvent` + `register_handler/dispatch_event/reset_handlers`.
- Produces (everything later tasks import from `app.modules.model_inventory.domain`):
  - `VERSION_IDENTITY_TYPES: set[str]`
  - `build_canonical_version_key(identity_type: str, version_label: str, native_version_id: str | None) -> str` (raises `ValueError` on unknown identity type or empty label)
  - `VersionLifecycleState = Literal["DRAFT", "ACTIVE", "DEPRECATED", "RETIRED", "ARCHIVED"]`
  - `VERSION_INITIAL_STATE = "DRAFT"`, `VERSION_ALLOWED_TRANSITIONS: dict[str, set[str]]`, `can_version_transition(current: str, target: str) -> bool`
  - `ModelVersionEvent(event_id, event_type, tenant_id, model_id, model_version_id, occurred_at, actor, change_summary, request_id)` frozen dataclass
  - Error classes in `domain.errors`: `ModelVersionNotFoundError` 404, `DuplicateModelVersionError` 409, `VersionIdentityImmutableError` 409, `InvalidVersionTransitionError` 409 (code `INVALID_MODEL_VERSION_LIFECYCLE_TRANSITION`), `ModelVersionArchivedError` 409, `VersionConcurrencyConflictError` 409

- [ ] **Step 1: Write the failing unit tests**

`tests/unit/test_version_identity.py`:

```python
import pytest
from app.modules.model_inventory.domain.identity import (
    VERSION_IDENTITY_TYPES,
    build_canonical_version_key,
)

def test_identity_types_match_spec():
    assert VERSION_IDENTITY_TYPES == {"native", "revision", "release", "checkpoint", "label", "opaque"}

def test_canonical_key_uses_native_id_when_present():
    assert build_canonical_version_key("native", "2025-04-14", "2025-04-14") == "native:2025-04-14"
    assert build_canonical_version_key("revision", "main", "abc123") == "revision:abc123"
    assert build_canonical_version_key("release", "v2.1.0", "release-210") == "release:release-210"

def test_canonical_key_falls_back_to_version_label():
    assert build_canonical_version_key("label", "v1.2.0", None) == "label:v1.2.0"
    assert build_canonical_version_key("release", "v2.1.0", "") == "release:v2.1.0"

def test_identity_type_normalized_identifiers_case_preserved():
    assert build_canonical_version_key("  Native ", "2025-04-14", None) == "native:2025-04-14"
    assert build_canonical_version_key("REVISION", " main ", "  ABC123 ") == "revision:ABC123"

def test_empty_version_label_rejected():
    with pytest.raises(ValueError, match="version_label"):
        build_canonical_version_key("label", "   ", None)

def test_unknown_identity_type_rejected():
    with pytest.raises(ValueError, match="identity_type"):
        build_canonical_version_key("semver", "v1", None)

def test_repeated_normalization_is_idempotent():
    key = build_canonical_version_key("NATIVE", " 2025-04-14 ", None)
    assert key == "native:2025-04-14"
    assert build_canonical_version_key("native", "2025-04-14", None) == key
```

`tests/unit/test_version_lifecycle.py`:

```python
from app.modules.model_inventory.domain.lifecycle import (
    VERSION_ALLOWED_TRANSITIONS,
    VERSION_INITIAL_STATE,
    VersionLifecycleState,
    can_version_transition,
)

STATES = ["DRAFT", "ACTIVE", "DEPRECATED", "RETIRED", "ARCHIVED"]
SPEC_EDGES = {
    ("DRAFT", "ACTIVE"), ("DRAFT", "DEPRECATED"), ("DRAFT", "ARCHIVED"),
    ("ACTIVE", "DEPRECATED"), ("ACTIVE", "RETIRED"), ("ACTIVE", "ARCHIVED"),
    ("DEPRECATED", "ACTIVE"), ("DEPRECATED", "RETIRED"), ("DEPRECATED", "ARCHIVED"),
    ("RETIRED", "ARCHIVED"),
}

def test_version_transitions_match_spec():
    declared = {(c, t) for c, targets in VERSION_ALLOWED_TRANSITIONS.items() for t in targets}
    assert declared == SPEC_EDGES and len(SPEC_EDGES) == 10
    for current in STATES:
        for target in STATES:
            assert can_version_transition(current, target) is ((current, target) in SPEC_EDGES)

def test_archived_is_terminal():
    for target in STATES:
        assert can_version_transition("ARCHIVED", target) is False

def test_version_initial_state_is_draft():
    assert VERSION_INITIAL_STATE == "DRAFT"
    assert set(VERSION_ALLOWED_TRANSITIONS) == set(STATES)
    assert str(VersionLifecycleState.__args__) is not None  # Literal carries all 5 states
```

In `tests/unit/test_events.py` append:

```python
from app.modules.model_inventory.domain.events import ModelVersionEvent, dispatch_event

def _version_event():
    return ModelVersionEvent(
        event_id=str(uuid4()), event_type="model_version.created",
        tenant_id=uuid4(), model_id=uuid4(), model_version_id=uuid4(),
        occurred_at=datetime.now(UTC), actor="tester",
        change_summary=[], request_id="req-1",
    )

def test_version_event_dispatch_reaches_handler():
    reset_handlers()
    captured = []
    register_handler(captured.append)
    event = _version_event()
    dispatch_event(event)
    assert captured == [event]
    reset_handlers()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_version_identity.py tests/unit/test_version_lifecycle.py tests/unit/test_events.py -v`
Expected: FAIL with `ImportError: cannot import name 'build_canonical_version_key'`.

- [ ] **Step 3: Implement the domain additions**

`domain/identity.py` — append after the existing `build_canonical_key` (do not modify it):

```python
VERSION_IDENTITY_TYPES = {"native", "revision", "release", "checkpoint", "label", "opaque"}

def build_canonical_version_key(identity_type: str, version_label: str, native_version_id: str | None) -> str:
    itype = (identity_type or "").strip().lower()
    if itype not in VERSION_IDENTITY_TYPES:
        raise ValueError(f"identity_type must be one of {sorted(VERSION_IDENTITY_TYPES)}")
    label = (version_label or "").strip()
    if not label:
        raise ValueError("version_label must be non-empty")
    native = (native_version_id or "").strip()
    return f"{itype}:{native or label}"
```

`domain/lifecycle.py` — append alongside the existing model matrix (do not rename existing names):

```python
VersionLifecycleState = Literal["DRAFT", "ACTIVE", "DEPRECATED", "RETIRED", "ARCHIVED"]
VERSION_INITIAL_STATE = "DRAFT"
VERSION_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "DRAFT": {"ACTIVE", "DEPRECATED", "ARCHIVED"},
    "ACTIVE": {"DEPRECATED", "RETIRED", "ARCHIVED"},
    "DEPRECATED": {"ACTIVE", "RETIRED", "ARCHIVED"},
    "RETIRED": {"ARCHIVED"},
    "ARCHIVED": set(),
}
def can_version_transition(current: str, target: str) -> bool: ...
```

(`can_version_transition` mirrors `can_transition`: unknown state → False, `current == target` → False, else `target in VERSION_ALLOWED_TRANSITIONS[current]`.)

`domain/errors.py` — append six `DomainError` subclasses with the exact `code`/`http_status` pairs listed in Global Constraints, mirroring the existing class style (one-line `code`/`http_status`, no `__init__`).

`domain/events.py` — add `ModelVersionEvent` frozen dataclass with the fields in Interfaces; change the registry annotations to `Event = ModelEvent | ModelVersionEvent` and `_handlers: list[Callable[[Event], None]]`, widening `register_handler`/`dispatch_event` to accept `Event`. `reset_handlers` already clears the single list — leave it.

`domain/__init__.py` — re-export everything listed in Interfaces alongside the existing exports.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_version_identity.py tests/unit/test_version_lifecycle.py tests/unit/test_events.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add docs/features/model-inventory/versioning-spec.md docs/superpowers/plans/2026-10-05-model-versioning-v1.md backend/app/modules/model_inventory/domain backend/tests/unit
git commit -m "feat: model version domain identity, lifecycle, errors, and events"
```

---

### Task 2: ModelVersion ORM + Alembic Migration

**Files:**
- Create: `backend/app/modules/model_inventory/models/model_version.py`
- Modify: `backend/app/modules/model_inventory/models/__init__.py`
- Create: `backend/alembic/versions/0002_model_versioning.py`
- Modify: `backend/tests/conftest.py` (TRUNCATE list + `parent_model` fixture)
- Test: `backend/tests/integration/test_version_persistence.py`, `backend/tests/integration/test_migration.py`

**Interfaces:**
- Consumes: `app.core.db.Base`, `Model` table (`models.id`), conftest `db` fixture.
- Produces: `ModelVersion` ORM class (exported from `app.modules.model_inventory.models`) with columns `id, tenant_id, model_id, identity_type, version_label, native_version_id, canonical_version_key, display_name, description, lifecycle_state, metadata_ (column "metadata"), source_type, source_reference, created_at, updated_at, archived_at, created_by, updated_by, record_version`; fixture `parent_model(db) -> Model`.

- [ ] **Step 1: Add conftest fixtures + write failing persistence tests**

`tests/conftest.py` changes:
- TRUNCATE list becomes `"TRUNCATE model_versions, model_tag_links, model_tags, models, model_types, model_providers RESTART IDENTITY CASCADE"`.
- Add import of `Model` and this fixture (after `service`):

```python
@pytest.fixture
def parent_model(db):
    seed_reference_data(db)
    provider = db.scalar(sa.select(ModelProvider).where(ModelProvider.slug == "openai"))
    model_type = db.scalar(sa.select(ModelType).where(ModelType.slug == "llm"))
    model = Model(
        tenant_id=settings.default_tenant_id, provider_id=provider.id, model_type_id=model_type.id,
        name="Versioned Model", native_model_id="versioned-1", canonical_key="openai|versioned-1",
        source_type="MANUAL",
    )
    db.add(model)
    db.commit()
    return model
```

`tests/integration/test_version_persistence.py`:

```python
import pytest, sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from app.core.config import settings
from app.modules.model_inventory.models import ModelVersion

def _mk_version(db, model, key="native:2025-04-14", **over):
    fields = dict(tenant_id=settings.default_tenant_id, model_id=model.id,
                  identity_type="native", version_label="2025-04-14",
                  native_version_id="2025-04-14", canonical_version_key=key,
                  lifecycle_state="DRAFT", source_type="manual")
    fields.update(over)
    db.add(ModelVersion(**fields)); db.flush()
    return db.scalar(sa.select(ModelVersion).where(ModelVersion.canonical_version_key == key))

def test_canonical_version_key_unique_constraint(db, parent_model):
    _mk_version(db, parent_model, key="native:dup")
    db.add(ModelVersion(tenant_id=settings.default_tenant_id, model_id=parent_model.id,
        identity_type="native", version_label="v2", canonical_version_key="native:dup",
        lifecycle_state="DRAFT", source_type="manual"))
    with pytest.raises(IntegrityError):
        db.flush()

def test_model_fk_enforced(db, parent_model):
    db.add(ModelVersion(tenant_id=settings.default_tenant_id, model_id=uuid4(),
        identity_type="label", version_label="v1", canonical_version_key="label:v1",
        lifecycle_state="DRAFT", source_type="manual"))
    with pytest.raises(IntegrityError):
        db.flush()

def test_tenant_model_key_uniqueness_is_scoped(db, parent_model):
    # same key under a different tenant is allowed
    _mk_version(db, parent_model, key="native:scoped")
    db.add(ModelVersion(tenant_id=uuid4(), model_id=parent_model.id, identity_type="native",
        version_label="2025-04-14", canonical_version_key="native:scoped",
        lifecycle_state="DRAFT", source_type="manual"))
    db.flush()

def test_timestamps_timezone_aware_and_metadata_jsonb(db, parent_model):
    version = _mk_version(db, parent_model, key="native:ts")
    db.commit()  # forces server_default round-trip
    fresh = db.get(ModelVersion, version.id)
    assert fresh.created_at.tzinfo is not None and fresh.updated_at.tzinfo is not None
    assert fresh.metadata_ == {} and fresh.record_version == 1
    cols = {c["name"]: str(c["type"]) for c in sa.inspect(db.get_bind()).get_columns("model_versions")}
    assert cols["metadata"].lower().startswith("jsonb")   # JSONB on postgres

def test_record_version_increments_on_flush(db, parent_model):
    version = _mk_version(db, parent_model, key="native:rv")
    version.display_name = "Renamed"
    db.flush()
    assert version.record_version == 2

def test_required_indexes_exist(db, parent_model):
    idx = {i["name"] for i in sa.inspect(db.get_bind()).get_indexes("model_versions")}
    assert {"ix_model_versions_tenant_id_model_id_created_at",
            "ix_model_versions_tenant_id_model_id_lifecycle_state",
            "ix_model_versions_tenant_id_model_id_updated_at",
            "ix_model_versions_tenant_id_model_id_version_label",
            "ix_model_versions_tenant_id_model_id_native_version_id"} <= idx
    cons = {c["name"] for c in sa.inspect(db.get_bind()).get_unique_constraints("model_versions")}
    assert "uq_model_versions_tenant_model_canonical_key" in cons
```

`tests/integration/test_migration.py`:

```python
from pathlib import Path
import pytest, sqlalchemy as sa
from alembic.config import Config
from alembic import command
from app.core.config import settings

def test_model_versioning_migration_downgrade_and_upgrade(database):
    cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    engine = sa.create_engine(settings.database_url, poolclass=sa.pool.NullPool)
    try:
        assert sa.inspect(engine).has_table("model_versions")
        try:
            command.downgrade(cfg, "0001")
            insp = sa.inspect(engine)
            assert not insp.has_table("model_versions")
            assert insp.has_table("models")          # registration data untouched
        finally:
            command.upgrade(cfg, "head")
        assert sa.inspect(engine).has_table("model_versions")
    finally:
        engine.dispose()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_version_persistence.py tests/integration/test_migration.py -v`
Expected: FAIL with `ImportError: cannot import name 'ModelVersion'` (or `relation "model_versions" does not exist`).

- [ ] **Step 3: Implement `ModelVersion` ORM + migration**

`models/model_version.py` — mirror `models/model.py` style exactly:

```python
class ModelVersion(Base):
    __tablename__ = "model_versions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "model_id", "canonical_version_key",
                         name="uq_model_versions_tenant_model_canonical_key"),
        CheckConstraint("lifecycle_state IN ('DRAFT','ACTIVE','DEPRECATED','RETIRED','ARCHIVED')",
                        name="ck_model_versions_lifecycle_state"),
        Index("ix_model_versions_tenant_id_model_id_created_at", "tenant_id", "model_id", desc("created_at")),
        Index("ix_model_versions_tenant_id_model_id_lifecycle_state", "tenant_id", "model_id", "lifecycle_state"),
        Index("ix_model_versions_tenant_id_model_id_updated_at", "tenant_id", "model_id", desc("updated_at")),
        Index("ix_model_versions_tenant_id_model_id_version_label", "tenant_id", "model_id", "version_label"),
        Index("ix_model_versions_tenant_id_model_id_native_version_id", "tenant_id", "model_id", "native_version_id"),
    )
    __mapper_args__ = {"version_id_col": record_version}   # placed AFTER the column declarations
```

Column declarations copy the `Model` conventions: `id` PK `default=uuid4`; `tenant_id`/`model_id` UUID (`model_id` = `ForeignKey("models.id")`, no `ondelete` — Postgres `NO ACTION` already blocks parent deletion, matching the existing FK style); `identity_type String(64)`, `version_label String(255)`, `native_version_id String(512) | None`, `canonical_version_key String(1024)`, `display_name String(255) | None`, `description Text | None`, `lifecycle_state String(32) default/server_default "DRAFT"`, `metadata_: Mapped[dict] = mapped_column("metadata", JSON().with_variant(JSONB, "postgresql"), default=dict, server_default=text("'{}'"))`, `source_type String(64)`, `source_reference String(512) | None`, `created_at`/`updated_at` `DateTime(timezone=True) server_default=func.now()` (`updated_at` also `onupdate=func.now()`), `archived_at DateTime(timezone=True) | None`, `created_by`/`updated_by String(255) | None`, `record_version BigInteger default=1 server_default=text("1")`.

`models/__init__.py` — add `ModelVersion` to the import and `__all__`.

`alembic/versions/0002_model_versioning.py` — `revision = "0002"`, `down_revision = "0001"`. Hand-written to match: `op.create_table("model_versions", ...)` with `sa.Uuid()` id, `sa.String(length=...)` per above, `sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")`, `server_default=sa.text("'{}'")` for metadata, `server_default=sa.text("now()")` for timestamps, `server_default="DRAFT"` for lifecycle_state, `server_default=sa.text("1")` for record_version, the CheckConstraint, the UniqueConstraint, `sa.ForeignKeyConstraint(["model_id"], ["models.id"])`, `sa.PrimaryKeyConstraint("id")`; then `op.create_index(...)` for the five indexes (descending ones use `sa.literal_column("created_at DESC")` exactly like `0001`). `downgrade()` drops the five indexes then `op.drop_table("model_versions")` — nothing else.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_version_persistence.py tests/integration/test_migration.py -v`
Expected: all PASS (conftest re-runs `alembic upgrade head` against a freshly created `auditra_test`).

- [ ] **Step 5: Commit**

```bash
git add backend/app/modules/model_inventory/models backend/alembic/versions/0002_model_versioning.py backend/tests/conftest.py backend/tests/integration/test_version_persistence.py backend/tests/integration/test_migration.py
git commit -m "feat: model_versions table, constraints, indexes, and migration"
```

---

### Task 3: Pydantic Schemas

**Files:**
- Create: `backend/app/modules/model_inventory/schemas/model_version.py`
- Modify: `backend/app/modules/model_inventory/schemas/__init__.py`
- Test: `backend/tests/unit/test_version_schemas.py`

**Interfaces:**
- Consumes: `VERSION_IDENTITY_TYPES`, `VersionLifecycleState`, `validate_metadata` (existing), `VERSION_INITIAL_STATE`.
- Produces: `VERSION_SOURCE_TYPES = {"manual", "import"}`; `ModelVersionCreate(identity_type, version_label, native_version_id?, display_name?, description?, metadata={}, source_type="manual", source_reference?)`; `ModelVersionUpdate(model_id?, identity_type?, version_label?, native_version_id?, canonical_version_key?, display_name?, description?, metadata?, source_reference?)`; `ModelVersionLifecycleUpdate(target_state)`; `ModelVersionResponse` (with `metadata` via `AliasChoices("metadata_", "metadata")` and `version` via `AliasChoices("version", "record_version")`); `ModelVersionListResponse(items, page, page_size, total, total_pages)`.

- [ ] **Step 1: Write the failing schema tests**

`tests/unit/test_version_schemas.py` (style mirrors `tests/unit/test_schemas.py`):

```python
import pytest
from pydantic import ValidationError
from app.modules.model_inventory.schemas import (
    ModelVersionCreate, ModelVersionLifecycleUpdate, ModelVersionUpdate,
)

def _create(**over):
    base = {"identity_type": "release", "version_label": "v2.1.0", "native_version_id": "release-210"}
    base.update(over)
    return ModelVersionCreate(**base)

def test_create_minimal_defaults():
    m = _create()
    assert m.source_type == "manual" and m.metadata == {} and m.native_version_id == "release-210"

def test_create_normalizes_identity_type_and_source_type():
    assert _create(identity_type=" Native ", version_label="x", native_version_id=None).identity_type == "native"
    assert _create(source_type="MANUAL").source_type == "manual"

def test_unknown_identity_type_rejected():
    with pytest.raises(ValidationError): _create(identity_type="semver")

def test_unknown_source_type_rejected():
    with pytest.raises(ValidationError): _create(source_type="sdk")

def test_empty_version_label_rejected():
    with pytest.raises(ValidationError): _create(version_label="   ", native_version_id=None)

def test_whitespace_native_version_id_rejected():
    with pytest.raises(ValidationError): _create(native_version_id="  ")

def test_create_server_owned_fields_forbidden():
    for field in ("lifecycle_state", "canonical_version_key", "record_version", "tenant_id"):
        with pytest.raises(ValidationError): _create(**{field: "x"})

def test_secret_and_oversized_metadata_rejected():
    with pytest.raises(ValueError): _create(metadata={"api_key": "x"})
    with pytest.raises(ValueError): _create(metadata={"blob": "x" * 10_001})

def test_update_accepts_identity_fields_for_explicit_rejection():
    u = ModelVersionUpdate(model_id=None, identity_type="native", version_label="v1",
                            native_version_id="n", canonical_version_key="native:n")
    assert u.identity_type == "native"

def test_update_mutable_fields_and_extra_forbidden():
    u = ModelVersionUpdate(display_name="Friendly", description="d", metadata={"a": 1}, source_reference="ref")
    assert u.display_name == "Friendly"
    with pytest.raises(ValidationError): ModelVersionUpdate(lifecycle_state="ACTIVE")
    with pytest.raises(ValidationError): ModelVersionUpdate(bogus=1)

def test_lifecycle_target_state_validated():
    assert ModelVersionLifecycleUpdate(target_state="RETIRED").target_state == "RETIRED"
    with pytest.raises(ValidationError): ModelVersionLifecycleUpdate(target_state="NOPE")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_version_schemas.py -v`
Expected: FAIL with `ImportError: cannot import name 'ModelVersionCreate'`.

- [ ] **Step 3: Implement `schemas/model_version.py`**

Mirror `schemas/model.py`: `ConfigDict(extra="forbid")` on every schema; a shared `mode="before"` `_strip_strings` validator covering the string fields (so `Field(min_length=1)` sees the trimmed value); allowlist validators `_check_identity_type` (`.strip().lower()` then membership in `VERSION_IDENTITY_TYPES`) and `_check_source_type` (`.strip().lower()` then membership in `VERSION_SOURCE_TYPES`); `_check_metadata` delegating to the existing `validate_metadata`.

Field constraints per Global Constraints. `native_version_id: str | None = Field(default=None, min_length=1, max_length=512)`. `ModelVersionResponse` fields exactly as in Interfaces (types: `lifecycle_state: VersionLifecycleState`, `created_by/updated_by: str | None`). Add the five schemas + `VERSION_SOURCE_TYPES` to `schemas/__init__.py` re-exports.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_version_schemas.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/modules/model_inventory/schemas backend/tests/unit/test_version_schemas.py
git commit -m "feat: model version pydantic schemas with identity and metadata validation"
```

---

### Task 4: Repository + Service Layer

**Files:**
- Create: `backend/app/modules/model_inventory/repositories/model_version_repository.py`
- Modify: `backend/app/modules/model_inventory/repositories/__init__.py`
- Create: `backend/app/modules/model_inventory/services/model_version_service.py`
- Modify: `backend/app/modules/model_inventory/services/__init__.py`
- Test: `backend/tests/integration/test_version_service.py`, `backend/tests/integration/test_version_concurrency.py`

**Interfaces:**
- Consumes: `ModelVersion` (Task 2), `ModelVersionCreate/Update/LifecycleUpdate` (Task 3), domain functions/errors/events (Task 1), existing `ModelRepository.get_model` for parent lookup, existing `ModelService`-style constructor conventions.
- Produces (used by Task 5 routes):
  - `ModelVersionFilters(lifecycle_state, identity_type, source_type, search, created_after, created_before, updated_after, updated_before)` dataclass, `ALLOWED_SORT_FIELDS = {"created_at", "updated_at", "version_label", "lifecycle_state"}`
  - repository: `get_model_version(db, tenant_id, model_id, version_id) -> ModelVersion | None`; `find_by_canonical_key(db, tenant_id, model_id, canonical_key) -> ModelVersion | None`; `create_model_version(db, **fields) -> ModelVersion`; `query_model_versions(db, tenant_id, model_id, filters, page, page_size, sort_by, sort_order, include_archived) -> tuple[list[ModelVersion], int]`; `commit(db) -> None`
  - `ModelVersionService(db, tenant_id, request_id=None, actor=None)` with `create_version(model_id, payload) -> ModelVersion`, `get_version(model_id, version_id) -> ModelVersion`, `list_versions(model_id, filters, page=1, page_size=20, sort_by="created_at", sort_order="desc", include_archived=False) -> tuple[list, int]`, `update_version(model_id, version_id, payload) -> ModelVersion`, `transition_version(model_id, version_id, payload) -> ModelVersion`, `archive_version(model_id, version_id) -> None`

- [ ] **Step 1: Write the failing integration tests**

`tests/integration/test_version_service.py` — fixture `_reset_event_handlers` autouse (copy from `test_service.py`), `_payload(**overrides)` returning `ModelVersionCreate`, and these tests:

- `test_create_version_dispatches_event` — create → `lifecycle_state == "DRAFT"`, `canonical_version_key == "release:release-210"`, `record_version == 1`; handler captured exactly one event with `event_type == "model_version.created"`, `model_version_id == version.id`, `actor == "alice"`, `request_id == "req-1"`.
- `test_duplicate_canonical_key_rejected` — second create with same `native_version_id` but different `version_label` (e.g. `version_label="January Release"`) still raises `DuplicateModelVersionError`, `code == "MODEL_VERSION_ALREADY_EXISTS"`, `http_status == 409`, and only one row exists (spec §115).
- `test_duplicate_race_integrity_error_mapped(db, parent_model, monkeypatch)` — create once, then `monkeypatch.setattr("app.modules.model_inventory.services.model_version_service.find_by_canonical_key", lambda *_a, **_k: None)` and create again → `DuplicateModelVersionError`, one row remains.
- `test_create_missing_model_rejected` → `ModelNotFoundError`, `code == "MODEL_NOT_FOUND"`.
- `test_create_under_archived_model_rejected` — archive parent (`model.lifecycle_state = "ARCHIVED"; model.archived_at = now; db.commit()`), then create → `ModelArchivedError`, `code == "MODEL_ARCHIVED"`.
- `test_create_and_read_scoped_by_tenant_and_parent` — version with `tenant_id=uuid4()` inserted directly; `get_version(model_id, that_id)` → `ModelVersionNotFoundError`; version id belonging to a different model → `ModelVersionNotFoundError`.
- `test_update_metadata_and_bumps_record_version` — update `display_name/description/metadata/source_reference` → new values, `record_version == 2`, event `model_version.updated` with `change_summary == ["description", "display_name", "metadata"]` (sorted); identity fields untouched.
- `test_update_identity_fields_rejected` — for each of `model_id`, `identity_type`, `native_version_id`, `canonical_version_key`, `version_label` differing from current → `VersionIdentityImmutableError`, `code == "MODEL_VERSION_IDENTITY_IMMUTABLE"`; sending the *current* value is a no-op allowed (mirrors `test_update_identity_immutable`); after rejections the record still has `record_version == 1`.
- `test_update_archived_rejected` → `ModelVersionArchivedError`, `code == "MODEL_VERSION_ALREADY_ARCHIVED"`.
- `test_lifecycle_transitions_allowed` — `DRAFT→ACTIVE` then `ACTIVE→DEPRECATED` then `DEPRECATED→ACTIVE` then `DEPRECATED→RETIRED`; event `model_version.lifecycle_changed` emitted with `change_summary == ["DRAFT->ACTIVE"]` on the first.
- `test_lifecycle_invalid_and_same_state_rejected` — `RETIRED→ACTIVE` → `InvalidVersionTransitionError` (`INVALID_MODEL_VERSION_LIFECYCLE_TRANSITION`, 409); `ACTIVE→ACTIVE` same-state → same error (spec §42 idempotency choice: existing project convention rejects); archived version + any target → `ModelVersionArchivedError`.
- `test_lifecycle_to_archived_sets_archived_at`.
- `test_archive_sets_state_and_timestamp` — archive → `lifecycle_state == "ARCHIVED"`, `archived_at` tz-aware, default list excludes it, `include_archived=True` includes it, second `archive_version` returns `None` (idempotent), event `model_version.archived`.
- `test_list_filters_search_sort_pagination` — create versions with distinct `version_label/display_name/identity_type/source_type/lifecycle_state`; assert `lifecycle_state=`/`identity_type=`/`source_type=` filters, `search=` hit and miss, `sort_by=version_label asc/desc`, `page_size=2` pagination totals, and `pytest.raises(InvalidSortFieldError)` for `sort_by="name; drop"` (code `INVALID_SORT_FIELD`, 400).
- `test_multiple_active_versions_allowed` — two versions both transitioned to `ACTIVE` under one model succeed (ADR-006).

`tests/integration/test_version_concurrency.py`:

```python
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from app.core.config import settings
from app.core.db import SessionLocal
from app.modules.model_inventory.domain.errors import DuplicateModelVersionError, VersionConcurrencyConflictError
from app.modules.model_inventory.schemas import ModelVersionCreate, ModelVersionUpdate
from app.modules.model_inventory.services import ModelVersionService

def _payload():
    return ModelVersionCreate(identity_type="native", version_label="2025-04-14",
                              native_version_id="2025-04-14", source_type="manual")

def test_concurrent_duplicate_create_one_wins(db, parent_model, monkeypatch):
    import app.modules.model_inventory.services.model_version_service as mvs
    barrier = Barrier(2, timeout=15)
    real_check = mvs.find_by_canonical_key
    monkeypatch.setattr(mvs, "find_by_canonical_key",
                        lambda *a, **k: (barrier.wait(), real_check(*a, **k))[1])
    results = []

    def worker():
        session = SessionLocal()
        try:
            ModelVersionService(session, tenant_id=settings.default_tenant_id
                                ).create_version(parent_model.id, _payload())
            results.append("created")
        except DuplicateModelVersionError:
            results.append("conflict")
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: worker(), range(2)))

    assert sorted(results) == ["conflict", "created"]
    # exactly one row in the database
    from sqlalchemy import func, select
    from app.modules.model_inventory.models import ModelVersion
    assert db.scalar(select(func.count()).select_from(ModelVersion)) == 1

def test_stale_metadata_update_conflict(db, parent_model):
    service = ModelVersionService(db, tenant_id=settings.default_tenant_id)
    version = service.create_version(parent_model.id, _payload())
    loaded = service.get_version(parent_model.id, version.id)   # db session snapshot
    assert loaded.record_version == 1

    other = SessionLocal()
    try:
        theirs = other.get(type(loaded), version.id)
        theirs.display_name = "Other Writer"
        other.commit()
    finally:
        other.close()

    with pytest.raises(VersionConcurrencyConflictError) as exc:
        service.update_version(parent_model.id, version.id, ModelVersionUpdate(description="mine"))
    assert exc.value.code == "VERSION_CONCURRENCY_CONFLICT"
    assert exc.value.http_status == 409
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_version_service.py tests/integration/test_version_concurrency.py -v`
Expected: FAIL with `ImportError: cannot import name 'ModelVersionService'`.

- [ ] **Step 3: Implement repository + service**

`repositories/model_version_repository.py` — copy the shape of `model_repository.py`: `ALLOWED_SORT_FIELDS` (the four allowed columns), `ModelVersionFilters` dataclass, `get_model_version` (`where tenant_id, model_id, id`), `find_by_canonical_key` (`where tenant_id, model_id, canonical_version_key`), `create_model_version`, `query_model_versions` (count subquery, allowlisted sort with `direction, ModelVersion.id.asc()` tiebreaker, offset/limit — no joins needed), and:

```python
def commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise DuplicateModelVersionError("model version with this identity already exists") from None
    except StaleDataError:
        db.rollback()
        raise VersionConcurrencyConflictError("model version was modified by another request") from None
```

(`StaleDataError` from `sqlalchemy.orm.exc`.)

`services/model_version_service.py` — constructor and `_dispatch` mirror `ModelService` (event dataclass is `ModelVersionEvent`, event types `model_version.created|updated|lifecycle_changed|archived`, `change_summary` as specified in the tests).

Flow rules (all tenant-scoped through `_get_model_or_404(model_id)` → `ModelNotFoundError` and `_get_or_404(model_id, version_id)` → `ModelVersionNotFoundError`):
1. **create** — parent archived → `ModelArchivedError`; `build_canonical_version_key(...)`; `find_by_canonical_key(...)` non-None → `DuplicateModelVersionError`; `create_model_version(id=uuid4(), tenant_id=..., model_id=model_id, identity_type=payload.identity_type, version_label=payload.version_label, native_version_id=payload.native_version_id, canonical_version_key=key, display_name=..., description=..., lifecycle_state=VERSION_INITIAL_STATE, metadata_=payload.metadata, source_type=payload.source_type, source_reference=payload.source_reference)`; `commit(db)`; dispatch `model_version.created` with `change_summary=[]`.
2. **update** — archived → `ModelVersionArchivedError`; for each of `model_id, identity_type, native_version_id, canonical_version_key, version_label`: if payload value is not None and differs from current → `VersionIdentityImmutableError`; then apply `display_name, description, source_reference` (skip `None`) and `metadata` when provided, collecting `changed` field names like `ModelService` does; `commit(db)` (StaleDataError → conflict); dispatch `model_version.updated`.
3. **transition** — archived → `ModelVersionArchivedError`; `can_version_transition(current, target)` False → `InvalidVersionTransitionError(f"cannot transition from {current} to {target}")`; set `lifecycle_state`; if target == `"ARCHIVED"` set `archived_at = datetime.now(UTC)`; `commit`; dispatch `model_version.lifecycle_changed` with `change_summary=[f"{old}->{new}"]`.
4. **archive** — already `ARCHIVED` → return; else set `ARCHIVED` + `archived_at`, `commit`, dispatch `model_version.archived` with `change_summary=["archived"]` (no `can_version_transition` check — every non-archived state may reach `ARCHIVED`, same as `ModelService.archive_model`).
5. **list** — parent must exist (404), then `query_model_versions`.

Both `__init__.py` files re-export the new names.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_version_service.py tests/integration/test_version_concurrency.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/modules/model_inventory/repositories backend/app/modules/model_inventory/services backend/tests/integration/test_version_service.py backend/tests/integration/test_version_concurrency.py
git commit -m "feat: model version repository and service with identity, lifecycle, and concurrency rules"
```

---

### Task 5: FastAPI Routes + API Tests

**Files:**
- Modify: `backend/app/modules/model_inventory/api/routes.py` (append after the model routes, before the lookup endpoints or at the end — no existing route path collides)
- Modify: `backend/tests/api/test_models_api.py` (`test_openapi_contract` only)
- Test: `backend/tests/api/test_versions_api.py`

**Interfaces:**
- Consumes: `ModelVersionService`, the five schemas, `ModelVersionFilters`, existing `_service()`/`_as_utc()` helpers, error handlers already registered in `app/main.py`.
- Produces: endpoints `POST/GET /api/v1/models/{model_id}/versions`, `GET/PATCH/DELETE /api/v1/models/{model_id}/versions/{version_id}`, `POST .../{version_id}/lifecycle`.

- [ ] **Step 1: Write the failing API tests + update the OpenAPI contract test**

`tests/api/test_versions_api.py` (fixtures: `client`, `db`; helper `_model(client)` → `client.post(f"{API}/models", json={...existing _payload...})` returning id; `_version(**overrides)` payload; `_error(response)`):

- `test_e2e_version_workflow` — POST version → 201 with `lifecycle_state == "DRAFT"`, `canonical_version_key == "release:release-210"`, `version == 1`, `tenant_id`/`model_id` set, `metadata` echoed; GET by id → 200; GET collection → `total == 1`; PATCH `{"description": "d2", "metadata": {"framework": "pytorch"}}` → 200, `version == 2`; POST lifecycle `{"target_state": "ACTIVE"}` → 200 `lifecycle_state == "ACTIVE"`; DELETE → 204 with empty body; collection default → `total == 0`; `include_archived=true` → `total == 1`.
- `test_duplicate_version_returns_409_envelope` — same identity, different label → 409, `code == "MODEL_VERSION_ALREADY_EXISTS"`, envelope has `message/details/request_id/http_status`.
- `test_get_missing_returns_404_envelope` — random UUID → 404 `MODEL_VERSION_NOT_FOUND`, `request_id` echoed from `X-Request-ID` header, no traceback.
- `test_get_version_of_other_model_returns_404` — create second model, GET version under wrong `model_id` → 404.
- `test_patch_identity_fields_rejected` — for each of `identity_type`, `native_version_id`, `version_label`, `canonical_version_key`, `model_id` (payload differs) → 409 `MODEL_VERSION_IDENTITY_IMMUTABLE`; then GET shows unchanged identity and `canonical_version_key`; `version` still 1.
- `test_patch_lifecycle_via_patch_rejected` — `{"lifecycle_state": "ACTIVE"}` → 422 `VALIDATION_ERROR` (lifecycle only through the lifecycle endpoint).
- `test_lifecycle_transitions_via_api` — `DRAFT→ACTIVE` 200; `ACTIVE→DEPRECATED` 200; `DEPRECATED→ACTIVE` 200; `DEPRECATED→RETIRED` 200; `RETIRED→ACTIVE` 409 `INVALID_MODEL_VERSION_LIFECYCLE_TRANSITION`; archive then `ARCHIVED→ACTIVE` 409 `MODEL_VERSION_ALREADY_ARCHIVED`.
- `test_create_under_archived_model_returns_409` — archive parent via `DELETE /models/{id}` → POST version → 409 `MODEL_ARCHIVED`.
- `test_create_missing_model_returns_404` — random model id → 404 `MODEL_NOT_FOUND`.
- `test_archive_workflow` — covered in e2e plus: PATCH on archived → 409 `MODEL_VERSION_ALREADY_ARCHIVED`; lifecycle on archived → 409; second DELETE → 204 (idempotent); explicit `lifecycle_state=ARCHIVED` filter without `include_archived` → 0 (mirrors model behavior).
- `test_search_filter_sort_pagination` — three versions; `search=` by label and by `native_version_id`; `lifecycle_state=`, `identity_type=`, `source_type=` filters; `sort_by=version_label&sort_order=asc` ordering; `page_size=2&page=2` returns remaining item with `total_pages == 2`; `page_size=500` → 422 `VALIDATION_ERROR`.
- `test_date_range_filters` — rewrite two versions' `created_at` via `db.execute(sa.update(ModelVersion)...)` (mirrors `test_date_range_filters` in `test_models_api.py`), then assert `created_after`/`created_before` each return exactly the expected one.
- `test_invalid_sort_field_returns_400` — `sort_by=nope` → 400 `INVALID_SORT_FIELD` with envelope.
- `test_validation_errors` — secret metadata key → 422 (loc `["body","metadata"]`); 20 000-byte metadata → 422; unknown `identity_type` → 422; `source_type="sdk"` → 422.
- `test_cross_tenant_version_returns_404` — insert `ModelVersion` with `tenant_id=uuid4()` directly via `db`, GET it → 404.
- `test_error_body_has_no_traceback` — monkeypatch `ModelVersionService.get_version` to raise `RuntimeError`; GET → 500 `INTERNAL_ERROR`, message `"Internal server error"`, no `"boom internals"`.

In `tests/api/test_models_api.py`, update `test_openapi_contract`:
- required path set gains `"/api/v1/models/{model_id}/versions"` and `"/api/v1/models/{model_id}/versions/{version_id}"` and `"/api/v1/models/{model_id}/versions/{version_id}/lifecycle"`;
- add `assert {"post", "get"} <= set(paths["/api/v1/models/{model_id}/versions"])`, `assert {"get", "patch", "delete"} <= set(paths["/api/v1/models/{model_id}/versions/{version_id}"])`, `assert {"post"} <= set(paths["/api/v1/models/{model_id}/versions/{version_id}/lifecycle"])`;
- change `== 8` to `== 14`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/api/test_versions_api.py tests/api/test_models_api.py::test_openapi_contract -v`
Expected: FAIL — version paths 404 (route missing) / `AssertionError` on path set.

- [ ] **Step 3: Implement the six endpoints**

Append to `api/routes.py`. Add a `_version_service(request, db)` helper next to `_service` building `ModelVersionService(db, settings.default_tenant_id, request_id=request.state.request_id, actor=None)`, and reuse `_as_utc` for the date params. Signatures:

```python
@router.post("/models/{model_id}/versions", status_code=201, response_model=ModelVersionResponse)
def create_version(model_id: UUID, payload: ModelVersionCreate, request: Request, db: ...) -> ModelVersionResponse

@router.get("/models/{model_id}/versions", response_model=ModelVersionListResponse)
def list_versions(model_id: UUID, request: Request, db: ..., page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20, search: str | None = None,
    lifecycle_state: str | None = None, identity_type: str | None = None, source_type: str | None = None,
    created_after/created_before/updated_after/updated_before: datetime | None = None,
    include_archived: bool = False, sort_by: str = "created_at",
    sort_order: Literal["asc", "desc"] = "desc") -> ModelVersionListResponse

@router.get("/models/{model_id}/versions/{version_id}", response_model=ModelVersionResponse)
def get_version(model_id: UUID, version_id: UUID, request: Request, db: ...) -> ModelVersionResponse

@router.patch("/models/{model_id}/versions/{version_id}", response_model=ModelVersionResponse)
def update_version(model_id: UUID, version_id: UUID, payload: ModelVersionUpdate, request: Request, db: ...) -> ModelVersionResponse

@router.post("/models/{model_id}/versions/{version_id}/lifecycle", response_model=ModelVersionResponse)
def transition_version(model_id: UUID, version_id: UUID, payload: ModelVersionLifecycleUpdate, request: Request, db: ...) -> ModelVersionResponse

@router.delete("/models/{model_id}/versions/{version_id}", status_code=204, response_class=Response)
def archive_version(model_id: UUID, version_id: UUID, request: Request, db: ...) -> Response
```

List response assembly mirrors `list_models`: `ModelVersionListResponse(items=[ModelVersionResponse.model_validate(i, from_attributes=True) for i in items], page=page, page_size=page_size, total=total, total_pages=ceil(total / page_size))`. No business logic in routes; errors flow to the existing `DomainError` handler.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/api -v`
Expected: all PASS (old and new).

- [ ] **Step 5: Commit**

```bash
git add backend/app/modules/model_inventory/api backend/tests/api
git commit -m "feat: model version API with nested routes and openapi contract"
```

---

### Task 6: Full Verification, Docs, and Delivery

**Files:**
- Modify: `README.md`
- No production code changes expected (fixes only if verification finds defects)

- [ ] **Step 1: Lint**

Run from `backend/`: `uv run ruff format .` then `uv run ruff check .`
Expected: no remaining findings (fix any introduced by this work; do not reformat unrelated files — check `git diff` first).

- [ ] **Step 2: Full test suite**

Run: `uv run pytest -v`
Expected: ALL PASS. Record the exact pass/fail counts in the final report. This includes unit, integration (persistence, service, concurrency, migration up/down), API, and health tests.

- [ ] **Step 3: Docker workflow verification**

From the repo root:
```powershell
docker compose up -d
cd backend
uv run alembic upgrade head        # auditra db: 0001 + 0002, no errors
uv run python -m app.seed          # idempotent
```
Start `uv run uvicorn app.main:app --port 8000` in the background, then:
- `GET http://127.0.0.1:8000/health` → `{"status":"ok"}`
- `GET http://127.0.0.1:8000/openapi.json` → contains the three version paths and the `ModelVersionCreate`/`ModelVersionResponse` schemas
- exercise one create→lifecycle→archive round-trip with `Invoke-RestMethod` against a model created via `POST /api/v1/models`
Stop the server afterwards.

- [ ] **Step 4: Update README**

Add the six version endpoints to the `Endpoints:` line (nested under `/api/v1/models/{model_id}/versions...`) and mention Model Versioning V1 in the opening sentence next to Model Registration V1.

- [ ] **Step 5: Spec review (§120 checklist)**

Walk `docs/features/model-inventory/versioning-spec.md` §102–§107/§120 and confirm each box against the code/tests. Specifically re-verify the six Review Focus items at the top of this plan. If anything fails, fix it (with a test) and re-run Step 2.

- [ ] **Step 6: Commit and push**

```powershell
git status            # review: only intended files
git diff              # review the full diff
git add README.md backend docs
git commit -m "feat: model versioning V1 backend with tests and docs"
git push origin HEAD
```
Do not commit `.env`, `.ruff_cache`, `uvicorn_smoke.log`, or any local database files (`.gitignore` already excludes `.env`; verify `git status` shows none of them staged).
