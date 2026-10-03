# Model Registration V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Auditra AI Model Inventory Model Registration V1 backend — a validated, searchable, auditable model registry API on FastAPI + PostgreSQL.

**Architecture:** Modular monolith, single FastAPI app with a `model_inventory` module split into api/schemas/domain/services/repositories/models layers. PostgreSQL is the sole source of truth (no Redis/RustFS). Domain rules (canonical identity, lifecycle transitions, duplicate detection) live in the service/domain layers; DB unique constraints are the final correctness boundary.

**Tech Stack:** Python 3.11+, uv (isolated venv), FastAPI, Pydantic v2, SQLAlchemy 2.0 (sync, psycopg3), Alembic, PostgreSQL 16 in Docker, pytest + httpx TestClient, ruff.

**Spec:** `docs/features/model-inventory/spec.md` (authoritative; the plan argues from it — executors read both)

## Global Constraints

- Python >= 3.11; every command runs via `uv run` from `backend/` (uv-managed isolated venv). No global pip installs.
- PostgreSQL 16 only, via root `docker-compose.yml`: db `auditra`, user `auditra`, password `auditra`, host port 5433 mapped to container 5432 (host machine runs PostgreSQL 18 on 5432); test db `auditra_test`. No Redis, RustFS, vector DB, Kafka, OpenSearch (spec §43).
- Backend only. No frontend, no microservices, no auth system (spec §4 non-goals).
- Sync SQLAlchemy 2.0 (`Mapped[]` declarative) + psycopg3; FastAPI sync route handlers (`def`, not `async def`). Decision recorded here because spec leaves async "where appropriate".
- `canonical_key = f"{provider_slug.strip().lower()}|{native_model_id.strip()}"` — provider slug lowercased, native id case-preserved, both trimmed. Unique per `(tenant_id, canonical_key)` (spec §9.2/§9.3).
- Lifecycle states: `REGISTERED, ACTIVE, DEPRECATED, RETIRED, ARCHIVED`. Create permits initial states `{REGISTERED, ACTIVE}` only. Transitions exactly per spec §12.2; `ARCHIVED` terminal.
- Tenant: `settings.default_tenant_id` (fixed dev UUID). Every repository query takes `tenant_id` as a required argument (spec §15).
- Pagination: `page` >= 1 default 1, `page_size` default 25 max 100 (over-max => 422). Default sort `updated_at desc`. Sort allowlist: `name, created_at, updated_at, lifecycle_state, provider, model_type`; always secondary-order by `id ASC` (spec §23).
- `metadata` JSON: serialized size <= 10,000 bytes; keys matching `(?i)(api[_-]?key|secret|token|password|credential|private[_-]?key)` => 422 (spec §25.3/§25.4).
- Error envelope: `{"error": {"code", "message", "details", "request_id"}}`. Error codes/HTTP: MODEL_NOT_FOUND 404, MODEL_ALREADY_EXISTS 409, MODEL_IDENTITY_CONFLICT 409, PROVIDER_NOT_FOUND 404, PROVIDER_INACTIVE 409, MODEL_TYPE_NOT_FOUND 404, INVALID_LIFECYCLE_TRANSITION 409, MODEL_ARCHIVED 409, INVALID_SORT_FIELD 400, VALIDATION_ERROR 422, INTERNAL_ERROR 500 (spec §19).
- `DELETE /models/{id}` = archive (204), never physical delete (spec §18.5/ADR-006).
- Immutable via PATCH: `provider_slug`, `native_model_id` => 409 MODEL_IDENTITY_CONFLICT (spec §18.4).
- `source_type` allowlist: `MANUAL, SDK, API, IMPORT`. `hosting_mode` allowlist: `CLOUD_API, SAAS_API, SELF_HOSTED, LOCAL, ON_PREMISE, EDGE, EMBEDDED, CUSTOM` (spec §13.3).
- Domain events `model.created / model.updated / model.archived` via in-process dispatcher only (spec §24).

## Review Focus

Spec-implied failure modes no single happy-path test covers; each has a test in the task that owns it:

1. **Concurrent duplicate registration** — pre-check passes, second INSERT hits unique constraint: must become 409, not 500. Test: Task 5 `test_duplicate_race_integrity_error_mapped` (find_by_canonical_key monkeypatched to None, real DB constraint does the rejecting).
2. **Identity-changing PATCH** — payload with `native_model_id`/`provider_slug` must return 409 and leave the record unchanged. Test: Task 6 `test_patch_identity_fields_rejected`.
3. **Archived record semantics** — excluded from default list, present with `include_archived=true`, GET by id still 200, PATCH => 409 MODEL_ARCHIVED, re-DELETE => 204 idempotent. Tests: Task 5 `test_list_excludes_archived_by_default`, `test_update_archived_rejected`; Task 6 `test_archive_workflow`.
4. **Lifecycle transition enforcement** — server-side matrix, ARCHIVED terminal. Tests: Task 5 `test_invalid_transition_rejected`, `test_archived_is_terminal`; Task 6 `test_patch_invalid_lifecycle_returns_409`.
5. **Hostile inputs** — secret-like metadata keys 422, oversized metadata 422, `sort_by` never interpolated as SQL (garbage => 400 INVALID_SORT_FIELD), no stack traces in any error body. Tests: Task 4 `test_secret_metadata_key_rejected`, `test_oversized_metadata_rejected`; Task 6 `test_invalid_sort_field_returns_400`, `test_error_body_has_no_traceback`.

---

## File Structure

```text
C:\Auditra\
├── docker-compose.yml                    # PostgreSQL 16 only
├── docs/
│   ├── features/model-inventory/spec.md  # staged already (authoritative spec)
│   └── superpowers/plans/2026-10-04-model-registration-v1.md
├── backend/
│   ├── pyproject.toml                    # uv project: deps + ruff + pytest config
│   ├── alembic.ini
│   ├── alembic/env.py                    # url from AUDITRA_DATABASE_URL or settings
│   ├── alembic/versions/0001_model_registration.py
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                       # create_app(), /health, request-id middleware, router+handlers wiring
│   │   ├── seed.py                       # idempotent provider/model-type seed (python -m app.seed)
│   │   ├── core/
│   │   │   ├── config.py                 # Settings (database_url, default_tenant_id, ...)
│   │   │   ├── db.py                     # engine, SessionLocal, get_db
│   │   │   └── errors.py                 # DomainError -> envelope handlers, RequestValidationError handler
│   │   └── modules/model_inventory/
│   │       ├── models/                   # SQLAlchemy ORM: provider.py (ModelProvider, ModelType),
│   │       │                             # model.py (Model, ModelTag, ModelTagLink)
│   │       ├── domain/
│   │       │   ├── identity.py           # build_canonical_key
│   │       │   ├── lifecycle.py          # states, ALLOWED_TRANSITIONS, INITIAL_STATES, can_transition
│   │       │   ├── errors.py             # DomainError hierarchy (code + http_status)
│   │       │   └── events.py             # ModelEvent dataclass, register_handler, dispatch, reset_handlers
│   │       ├── schemas/model.py          # ModelCreate/Update/Response/ListResponse, summaries, parse_tags
│   │       ├── repositories/model_repository.py
│   │       ├── services/model_service.py
│   │       └── api/routes.py             # all 8 endpoints
│   └── tests/
│       ├── conftest.py                   # PG test-db fixtures (alembic upgrade, per-test truncate), client
│       ├── unit/test_identity.py
│       ├── unit/test_lifecycle.py
│       ├── unit/test_events.py
│       ├── unit/test_schemas.py
│       ├── integration/test_persistence.py
│       ├── integration/test_service.py
│       └── api/test_models_api.py
```

Tests live in `backend/tests/` (pytest convention) rather than inside the module package — spec §17 says the architectural boundary matters more than exact folder names.

---

### Task 1: Project scaffold, tooling, Docker PostgreSQL

**Files:**
- Create: `backend/pyproject.toml`, `backend/app/__init__.py`, `backend/app/main.py`, `backend/app/core/__init__.py`, `backend/app/core/config.py`, `backend/tests/test_health.py`, `docker-compose.yml`, `.env.example`
- Test: `backend/tests/test_health.py`

**Interfaces:**
- Produces: `create_app() -> FastAPI`; `app = create_app()` export in `app.main`; `settings: Settings` in `app.core.config` with fields `database_url: str`, `default_tenant_id: UUID`; `GET /health` returns `{"status": "ok"}`; root `docker-compose.yml` service `postgres`.

- [ ] **Step 1: Initialize uv project and add dependencies**

```powershell
uv init --bare backend
# in backend/:
uv add fastapi "uvicorn[standard]" pydantic-settings "sqlalchemy>=2.0" alembic "psycopg[binary]>=3.11"
uv add --dev pytest httpx ruff
```

In `backend/pyproject.toml` add: `[tool.pytest.ini_options] testpaths = ["tests"]`, `[tool.ruff] line-length = 100`, and `requires-python = ">=3.11"` if missing.

- [ ] **Step 2: Write the failing health test**

```python
# backend/tests/test_health.py
from fastapi.testclient import TestClient

def test_health_returns_ok():
    from app.main import app
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
```

- [ ] **Step 3: Run test, verify it fails**

Run: `uv run pytest tests/test_health.py -v` (cwd `backend/`)
Expected: FAIL — `ModuleNotFoundError: No module named 'app'` (or import error for `app.main`)

- [ ] **Step 4: Implement config + app factory**

`app/core/config.py`: `class Settings(BaseSettings)` with `database_url: str = "postgresql+psycopg://auditra:auditra@localhost:5433/auditra"`, `default_tenant_id: UUID = UUID("11111111-1111-1111-1111-111111111111")`, `model_config = SettingsConfigDict(env_prefix="AUDITRA_", env_file=".env")`; module-level `settings = Settings()`.

`app/main.py`: `def create_app() -> FastAPI` building app with `title="Auditra"`, registering `GET /health` returning `{"status": "ok"}`; module-level `app = create_app()`.

- [ ] **Step 5: Run test, verify it passes**

Run: `uv run pytest tests/test_health.py -v`
Expected: PASS

- [ ] **Step 6: Docker Compose PostgreSQL + env file**

Root `docker-compose.yml`: service `postgres`, image `postgres:16-alpine`, ports `"5433:5432"`, env `POSTGRES_USER=auditra`, `POSTGRES_PASSWORD=auditra`, `POSTGRES_DB=auditra`, named volume `pgdata`, healthcheck `pg_isready -U auditra`. Root `.env.example` with `AUDITRA_DATABASE_URL` and `AUDITRA_DEFAULT_TENANT_ID` commented examples.

Run: `docker compose up -d; docker compose ps`
Expected: `postgres` container state `healthy`

- [ ] **Step 7: Commit**

```powershell
git add backend docs docker-compose.yml .env.example
git commit -m "feat: scaffold uv/FastAPI backend with Docker PostgreSQL"
```

---

### Task 2: Persistence — ORM models, Alembic migration, seed data

**Files:**
- Create: `backend/app/core/db.py`, `backend/app/modules/model_inventory/__init__.py`, `.../models/__init__.py`, `.../models/provider.py`, `.../models/model.py`, `backend/app/seed.py`, `backend/alembic.ini`, `backend/alembic/env.py`, `backend/alembic/versions/0001_model_registration.py`, `backend/tests/conftest.py`, `backend/tests/integration/__init__.py` (and `__init__.py` for other test dirs)
- Test: `backend/tests/integration/test_persistence.py`

**Interfaces:**
- Produces: ORM classes `ModelProvider`, `ModelType`, `Model`, `ModelTag`, `ModelTagLink` (SQLAlchemy 2.0 `Mapped`); `engine`, `SessionLocal`, `get_db() -> Iterator[Session]` in `app.core.db`; `seed_reference_data(db: Session) -> None` (idempotent) in `app.seed`; pytest fixtures `db` (function-scoped Session on `auditra_test`, truncated after test).
- Tables exactly per spec §14.5/§14.3/§14.4/§14.6: `models` (incl. `hosting_mode`, `runtime_hint` VARCHAR(64) NULL, `record_version BIGINT NOT NULL DEFAULT 1`), `model_providers`, `model_types`, `model_tags`, `model_tag_links`.

- [ ] **Step 1: Write failing persistence tests**

```python
# backend/tests/integration/test_persistence.py
import pytest
from sqlalchemy.exc import IntegrityError
from app.modules.model_inventory.models import Model, ModelProvider, ModelTag

def _mk_model(db, key="openai|gpt-test", tenant="11111111-1111-1111-1111-111111111111"):
    ...  # helper: persist provider+type via seed_reference_data, then Model(...) with that key

def test_canonical_key_unique_constraint(db):
    _mk_model(db, key="openai|dup")
    with pytest.raises(IntegrityError):
        _mk_model(db, key="openai|dup")
        db.flush()

def test_provider_slug_unique(db): ...          # duplicate slug => IntegrityError
def test_tag_unique_per_tenant(db): ...         # same (tenant, key, value) twice => IntegrityError
def test_provider_fk_enforced(db): ...          # Model with provider_id=uuid4() => IntegrityError
def test_seed_idempotent(db): ...               # seed twice => 13 providers, 15 model_types
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `uv run pytest tests/integration/test_persistence.py -v`
Expected: FAIL — module `app.modules.model_inventory.models` not found

- [ ] **Step 3: Implement ORM, db session, seed**

ORM per spec column tables (UUID `id` default `uuid4`, `TIMESTAMPTZ` via `DateTime(timezone=True)`, `metadata` JSONB via `sa.JSON().with_variant(JSONB, "postgresql")`, `metadata` attribute named `metadata_` on `Model`/`ModelProvider` mapped to column `metadata` to avoid SQLAlchemy declarative clash). Unique constraints: `models(tenant_id, canonical_key)`, `model_providers(slug)`, `model_types(slug)`, `model_tags(tenant_id, key, value)`; PK `(model_id, tag_id)` on `model_tag_links`; indexes per spec §14.8.

`app/seed.py`: providers list `[("openai","OpenAI"),("anthropic","Anthropic"),("google","Google"),("meta","Meta"),("mistral","Mistral"),("cohere","Cohere"),("huggingface","Hugging Face"),("aws","AWS"),("azure","Azure"),("gcp","GCP"),("ollama","Ollama"),("vllm","vLLM"),("internal","Internal / Custom")]`; types list `[("llm","LLM"),("generative-ai","Generative AI"),("embedding","Embedding Model"),("reranker","Reranker"),("nlp","NLP Model"),("computer-vision","Computer Vision Model"),("multimodal","Multimodal Model"),("classification","Classification Model"),("object-detection","Object Detection Model"),("recommendation","Recommendation Model"),("forecasting","Forecasting Model"),("speech","Speech Model"),("deep-learning","Deep Learning Model"),("reinforcement-learning","Reinforcement Learning Model"),("custom-ml","Custom ML Model")]`. Skip rows whose slug exists. `if __name__ == "__main__":` opens `SessionLocal()`.

- [ ] **Step 4: Alembic init + first migration**

Run (cwd `backend/`, compose up): `uv run alembic init alembic` (merge generated env.py with: read `os.environ.get("AUDITRA_DATABASE_URL") or settings.database_url`; `target_metadata = Base.metadata`; `render_as_batch = True` not needed for PG).
Then: `uv run alembic revision --autogenerate -m "model registration tables"`, review the generated file for parity with spec §14, then `uv run alembic upgrade head`.

- [ ] **Step 5: Test conftest (test DB + truncate)**

`tests/conftest.py`: session-scoped fixture sets `AUDITRA_DATABASE_URL` to `postgresql+psycopg://auditra:auditra@localhost:5433/auditra_test` before importing app config; connects to `postgres` maintenance DB, `DROP ... CREATE DATABASE auditra_test`, runs `alembic.command.upgrade(cfg, "head")`. Function-scoped `db` fixture yields Session then runs `TRUNCATE model_tag_links, model_tags, models, model_types, model_providers RESTART IDENTITY CASCADE`.

- [ ] **Step 6: Run tests, verify they pass**

Run: `uv run pytest tests/integration/test_persistence.py -v`
Expected: PASS (docker `postgres` must be healthy)

- [ ] **Step 7: Commit**

```powershell
git add backend
git commit -m "feat: model inventory ORM, migration, and reference seed"
```

---

### Task 3: Domain — canonical identity, lifecycle, events

**Files:**
- Create: `backend/app/modules/model_inventory/domain/__init__.py`, `identity.py`, `lifecycle.py`, `events.py`
- Test: `backend/tests/unit/test_identity.py`, `test_lifecycle.py`, `test_events.py`

**Interfaces:**
- Produces:
  - `build_canonical_key(provider_slug: str, native_model_id: str) -> str` — raises `ValueError` if either side empty after strip; returns `"{slug.lower()}|{native_id}"`.
  - `LifecycleState = Literal["REGISTERED","ACTIVE","DEPRECATED","RETIRED","ARCHIVED"]`
  - `INITIAL_STATES: set[str]` = `{REGISTERED, ACTIVE}`; `ALLOWED_TRANSITIONS: dict[str, set[str]]` exactly spec §12.2; `can_transition(current: str, target: str) -> bool` (False for same-state no-op except identity unknown).
  - `@dataclass(frozen=True) ModelEvent(event_id: str, event_type: str, model_id: UUID, tenant_id: UUID, occurred_at: datetime, actor: str | None, change_summary: list[str], request_id: str | None)`
  - `register_handler(fn: Callable[[ModelEvent], None]) -> None`, `dispatch_event(event: ModelEvent) -> None`, `reset_handlers() -> None` (tests).

- [ ] **Step 1: Write failing unit tests**

```python
def test_canonical_key_deterministic():
    assert build_canonical_key("OpenAI", "  gpt-4o ") == "openai|gpt-4o"
def test_canonical_key_empty_raises(): ...      # "", "   " on either arg => ValueError
def test_canonical_key_case_preserved_for_native(): ...  # "openai|GPT-4" stays "GPT-4"
def test_allowed_transitions_match_spec(): ...  # spot-check every row of spec 12.2 matrix
def test_archived_is_terminal(): ...            # ARCHIVED -> anything => False
def test_initial_states(): ...                  # INITIAL_STATES == {"REGISTERED", "ACTIVE"}
def test_event_dispatch_reaches_handler(): ...  # register_handler + dispatch_event => captured
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `uv run pytest tests/unit -v` — Expected: FAIL, domain module missing

- [ ] **Step 3: Implement `identity.py`, `lifecycle.py`, `events.py`**

Signatures as in Interfaces; transition matrix written directly from spec §12.2 (nine allowed edges, everything else False).

- [ ] **Step 4: Run tests, verify they pass**

Run: `uv run pytest tests/unit -v` — Expected: PASS

- [ ] **Step 5: Commit**

```powershell
git add backend/app/modules/model_inventory/domain backend/tests/unit
git commit -m "feat: canonical identity, lifecycle matrix, domain events"
```

---

### Task 4: Pydantic request/response schemas

**Files:**
- Create: `backend/app/modules/model_inventory/schemas/__init__.py`, `schemas/model.py`
- Test: `backend/tests/unit/test_schemas.py`

**Interfaces:**
- Produces (all `pydantic.BaseModel`, `model_config = ConfigDict(extra="forbid")` on inputs):
  - `ProviderSummary(id, slug, name)`, `ModelTypeSummary(id, slug, name)`, `TagResponse(key, value)`
  - `ModelCreate`: `provider_slug: str` (strip, min 1), `model_type_slug: str`, `name: str` (1..255), `native_model_id: str` (1..512), `description: str | None` (max 4000), `owner_name/max 255`, `owner_contact/max 512` (must not match email-less secrets — plain str), `team_name/max 255`, `hosting_mode: str | None` in hosting allowlist, `runtime_hint: str | None` (max 64), `lifecycle_state: LifecycleState = "REGISTERED"` must be in `INITIAL_STATES`, `source_type: str = "MANUAL"` in source allowlist, `source_reference: str | None` (max 512), `tags: list[str] = []` (max 50), `metadata: dict[str, Any] = {}`.
  - `ModelUpdate`: `name`, `description`, `owner_name`, `owner_contact`, `team_name`, `hosting_mode`, `runtime_hint`, `lifecycle_state`, `source_reference`, `tags`, `metadata`, `model_type_slug` — all optional; **plus** `provider_slug: str | None = None` and `native_model_id: str | None = None` present so the service can reject them with 409 (spec §18.4) rather than 422.
  - `ModelResponse`: `id, tenant_id, name, native_model_id, canonical_key, description, provider: ProviderSummary, model_type: ModelTypeSummary, hosting_mode, runtime_hint, lifecycle_state, source_type, source_reference, owner_name, owner_contact, team_name, tags: list[TagResponse], metadata: dict, created_at, updated_at, archived_at, created_by: UUID | None, updated_by: UUID | None, version: int` (from `record_version`; created/updated_by always null in V1 — kept for spec §13.2 parity).
  - `ModelListResponse`: `items: list[ModelResponse]`, `page: int`, `page_size: int`, `total: int`, `total_pages: int`.
  - Module functions: `parse_tags(raw: list[str]) -> list[tuple[str, str]]` (each entry `"key"` or `"key=value"`; empty key => `ValueError`), `validate_metadata(md: dict) -> None` (10,000-byte `json.dumps` cap + secret-key regex => `ValueError`).

- [ ] **Step 1: Write failing schema tests**

```python
def test_create_minimal_ok(): ...               # ModelCreate(provider_slug="openai", model_type_slug="llm", name="Customer Support LLM", native_model_id="gpt-4o") valid
def test_empty_name_rejected(): ...             # name="" => ValidationError
def test_secret_metadata_key_rejected(): ...    # metadata={"api_key": "x"} => ValueError from validate_metadata
def test_oversized_metadata_rejected(): ...     # metadata value padded past 10_000 bytes => ValueError
def test_bad_source_type_rejected(): ...        # source_type="WHATEVER" => ValidationError
def test_create_archived_rejected(): ...        # lifecycle_state="ARCHIVED" on create => ValidationError
def test_parse_tags_key_value(): ...            # ["a", "b=c"] -> [("a",""), ("b","c")]
def test_parse_tags_empty_key_rejected(): ...   # ["=x"] => ValueError
def test_update_accepts_identity_fields(): ...  # ModelUpdate(native_model_id="x") parses (service will 409)
def test_extra_field_forbidden(): ...           # ModelCreate(bogus=1) => ValidationError
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `uv run pytest tests/unit/test_schemas.py -v` — Expected: FAIL, schemas module missing

- [ ] **Step 3: Implement `schemas/model.py`**

Validators: `@field_validator`/`@model_validator` calling `validate_metadata` and enforcing allowlists/lengths from Interfaces; strip-whitespace validators on all string ids/names.

- [ ] **Step 4: Run tests, verify they pass**

Run: `uv run pytest tests/unit/test_schemas.py -v` — Expected: PASS

- [ ] **Step 5: Commit**

```powershell
git add backend/app/modules/model_inventory/schemas backend/tests/unit/test_schemas.py
git commit -m "feat: API schemas with validation and metadata hardening"
```

---

### Task 5: Repository + service (registration, queries, lifecycle, events)

**Files:**
- Create: `backend/app/modules/model_inventory/domain/errors.py`, `repositories/__init__.py`, `repositories/model_repository.py`, `services/__init__.py`, `services/model_service.py`
- Test: `backend/tests/integration/test_service.py`

**Interfaces:**
- Consumes: ORM from Task 2, domain from Task 3, schemas (validation helpers) from Task 4.
- Produces:
  - `@dataclass ModelFilters`: `provider: str | None, model_type: str | None, lifecycle_state: str | None, owner: str | None, team: str | None, source_type: str | None, tag: str | None, search: str | None, created_after/created_before/updated_after/updated_before: datetime | None` (all default None).
  - Domain errors in `domain/errors.py`: `class DomainError(Exception)` with `code: str`, `http_status: int`, `message: str`; subclasses `ModelNotFoundError(404, MODEL_NOT_FOUND)`, `DuplicateModelError(409, MODEL_ALREADY_EXISTS)`, `IdentityImmutableError(409, MODEL_IDENTITY_CONFLICT)`, `ProviderNotFoundError(404, PROVIDER_NOT_FOUND)`, `ProviderInactiveError(409, PROVIDER_INACTIVE)`, `ModelTypeNotFoundError(404, MODEL_TYPE_NOT_FOUND)`, `InvalidTransitionError(409, INVALID_LIFECYCLE_TRANSITION)`, `ModelArchivedError(409, MODEL_ARCHIVED)`, `InvalidSortFieldError(400, INVALID_SORT_FIELD)`.
  - Repository (free functions, all take `db: Session, tenant_id: UUID`): `get_provider_by_slug`, `list_providers`, `get_model_type_by_slug`, `list_model_types`, `find_by_canonical_key(db, tenant_id, canonical_key) -> Model | None`, `create_model(db, **fields) -> Model`, `get_model(db, tenant_id, model_id) -> Model | None`, `query_models(db, tenant_id, filters: ModelFilters, page: int, page_size: int, sort_by: str, sort_order: str, include_archived: bool) -> tuple[list[Model], int]`, `commit(db)` (maps `IntegrityError` -> `DuplicateModelError`).
  - `ALLOWED_SORT_FIELDS` mapping `str -> Column` incl. join columns `provider` -> `ModelProvider.name`, `model_type` -> `ModelType.name`.
  - `class ModelService:` — `__init__(self, db: Session, tenant_id: UUID, request_id: str | None = None, actor: str | None = None)`; methods `register_model(payload: ModelCreate) -> Model`, `get_model(model_id: UUID) -> Model`, `list_models(...) -> tuple[list[Model], int]`, `update_model(model_id: UUID, payload: ModelUpdate) -> Model`, `archive_model(model_id: UUID) -> None`. Each emits the matching `ModelEvent` via `dispatch_event` (created/updated with `change_summary` of changed field names/archived).

- [ ] **Step 1: Write failing service tests**

Conftest additions for this task: function fixture `service` -> `ModelService(db, tenant_id=settings.default_tenant_id)`; each test seeds reference data first via `seed_reference_data(db)`.

```python
# tests/integration/test_service.py  (uses seeded reference data + db fixture)
def test_register_creates_model_and_dispatches_event(db): ...
def test_duplicate_canonical_key_rejected(db): ...          # register twice, same provider+native id => DuplicateModelError
def test_duplicate_race_integrity_error_mapped(db, monkeypatch): ...  # monkeypatch find_by_canonical_key -> None; real INSERT hits constraint => DuplicateModelError not IntegrityError
def test_provider_inactive_rejected(db): ...                # is_active=False => ProviderInactiveError
def test_invalid_transition_rejected(db, service): ...      # REGISTERED -> RETIRED => InvalidTransitionError
def test_archived_is_terminal(db, service): ...             # archive then update lifecycle => ModelArchivedError
def test_list_excludes_archived_by_default(db, service): ...
def test_list_include_archived(db, service): ...
def test_list_filters_and_search(db, service): ...          # provider slug, lifecycle_state, tag, owner, search="support"
def test_list_sort_allowlist(db, service): ...              # sort_by="provider" works; sort_by="name; drop" => InvalidSortFieldError
def test_pagination_total(db, service): ...                 # 30 rows, page_size 25 => 25 items, total 30, total_pages 2
def test_update_changes_fields_and_bumps_version(db, service): ...
def test_update_identity_immutable(db, service): ...        # native_model_id set => IdentityImmutableError, record unchanged
def test_update_archived_rejected(db, service): ...
def test_tenant_scoping(db, service): ...                   # row written with other tenant_id invisible to list/get
def test_archive_sets_state_and_timestamp(db, service): ...
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `uv run pytest tests/integration/test_service.py -v` — Expected: FAIL, service module missing

- [ ] **Step 3: Implement errors, repository, service**

Repository: SQLAlchemy 2.0 `select()`; `query_models` builds filters (provider slug join, type join, state, owner/team exact, source_type, tag EXISTS subquery on key or `key=value`, `search` -> `or_` of `ilike` over name/native_model_id/description/canonical_key/provider name+slug, four date bounds), `COUNT` with same where-clause; order by allowlisted column + `Model.id.asc()` secondary. Service: `parse_tags(payload.tags)` normalizes tags before persist; provider/type lookup + active check -> `build_canonical_key` -> pre-check `find_by_canonical_key` -> insert via repo (IntegrityError -> `DuplicateModelError`) -> `dispatch_event(model.created)`. `update_model`: archived check, identity-field check (raise `IdentityImmutableError` if `provider_slug`/`native_model_id` provided and differ), `can_transition` check if `lifecycle_state` present, apply whitelisted fields, `record_version += 1`, dispatch `model.updated`. `archive_model`: set `ARCHIVED` + `archived_at=now(UTC)`, dispatch `model.archived`; already archived => no-op.

- [ ] **Step 4: Run tests, verify they pass**

Run: `uv run pytest tests/integration/test_service.py -v` — Expected: PASS

- [ ] **Step 5: Commit**

```powershell
git add backend/app/modules/model_inventory backend/tests/integration/test_service.py
git commit -m "feat: model repository and registration service with domain rules"
```

---

### Task 6: API routes, error envelope, request-id middleware

**Files:**
- Create: `backend/app/modules/model_inventory/api/__init__.py`, `api/routes.py`, `backend/app/core/errors.py`
- Modify: `backend/app/main.py` (mount router, exception handlers, request-id middleware)
- Test: `backend/tests/api/__init__.py`, `backend/tests/api/test_models_api.py`

**Interfaces:**
- Consumes: `ModelService`, domain errors (Task 5), schemas (Task 4), `get_db` (Task 2).
- Produces base path `/api/v1`:
  - `POST /models` -> 201 `ModelResponse` | 409 | 404 | 422
  - `GET /models` -> 200 `ModelListResponse`; query params `page, page_size, provider, model_type, lifecycle_state, owner, team, source_type, tag, search, created_after, created_before, updated_after, updated_before, include_archived: bool = False, sort_by: str = "updated_at", sort_order: str = "desc"`
  - `GET /models/{model_id}` -> 200 | 404 (archived record: 200)
  - `PATCH /models/{model_id}` -> 200 | 404 | 409 | 422
  - `DELETE /models/{model_id}` -> 204 (idempotent if already archived)
  - `GET /model-providers` -> 200 `list[ProviderSummary]`; `GET /model-types` -> 200 `list[ModelTypeSummary]`
  - `GET /health` unchanged. FastAPI `Depends(get_db)`; tenant = `settings.default_tenant_id`; `request_id` from `request.state` (middleware sets `X-Request-ID` header on request+response, generating a UUID when absent).
  - `core/errors.py`: `domain_error_handler` -> envelope with `code/http_status/message/details={}/request_id`; `validation_error_handler(RequestValidationError)` -> 422 envelope `VALIDATION_ERROR`; `unhandled_error_handler` -> 500 `INTERNAL_ERROR` (message "Internal server error", no exception text).
  - Structured logging (spec §35): `logging.basicConfig` with `%(asctime)s %(levelname)s %(name)s %(message)s`; request-id middleware logs one line per request: `method path status_code request_id duration_ms`.

- [ ] **Step 1: Write failing API tests**

```python
# tests/api/test_models_api.py  (TestClient fixture from conftest; real PG)
def test_e2e_registration_workflow(client): ...   # spec §37: POST 201 -> GET list once -> GET id returns canonical_key -> PATCH 200 updated_at changes -> duplicate POST before archive => 409 -> DELETE 204
def test_get_missing_returns_404_envelope(client): ...   # code MODEL_NOT_FOUND, has request_id, no "Traceback"
def test_patch_identity_fields_rejected(client): ...     # native_model_id in PATCH => 409 MODEL_IDENTITY_CONFLICT; subsequent GET shows original value
def test_patch_invalid_lifecycle_returns_409(client): ...# REGISTERED -> RETIRED => 409 INVALID_LIFECYCLE_TRANSITION
def test_archive_workflow(client): ...                  # DELETE 204 -> absent from list, present with include_archived=true, GET id 200, PATCH 409 MODEL_ARCHIVED, DELETE again 204
def test_search_filter_sort_pagination(client): ...     # search match/no-match; provider filter; sort_by=name&sort_order=asc ordering; page_size=2&page=2 slice; page_size=500 => 422
def test_invalid_sort_field_returns_400(client): ...    # sort_by=nope => 400 INVALID_SORT_FIELD
def test_secret_metadata_returns_422(client): ...       # {"api_key": "x"} => 422, envelope code VALIDATION_ERROR
def test_oversized_metadata_returns_422(client): ...
def test_provider_inactive_returns_409(client): ...     # deactivated provider slug => 409 PROVIDER_INACTIVE (seed fixture flips is_active)
def test_lookup_endpoints(client): ...                  # providers >= 13, types >= 15, 200
def test_openapi_contract(client): ...                  # app.openapi() paths contain the 8 routes above
def test_error_body_has_no_traceback(client): ...       # 404 + 500 paths: "traceback" not in body text.lower()
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `uv run pytest tests/api -v` — Expected: FAIL (404 on /api/v1/models)

- [ ] **Step 3: Implement routes, handlers, middleware, wire `create_app`**

Route functions per Interfaces; each maps `DomainError` naturally through registered handlers; `ModelResponse` built by `ModelResponse.model_validate` from ORM with `from_attributes=True` (schema config) plus tags/provider/type eagerly loaded via `selectinload`/`joinedload` in repository `get_model`/`query_models`.

- [ ] **Step 4: Run full suite**

Run: `uv run pytest -v` — Expected: ALL PASS

- [ ] **Step 5: Commit**

```powershell
git add backend
git commit -m "feat: model registration API with error envelope and request ids"
```

---

### Task 7: Acceptance sweep — clean-DB run, lint, README

**Files:**
- Create: `README.md` (root — quickstart: prerequisites, `docker compose up -d`, `uv sync`, `uv run alembic upgrade head`, `uv run python -m app.seed`, `uv run uvicorn app.main:app --reload`, test command)
- Modify: none expected; fix only what the sweep uncovers.

**Interfaces:**
- Consumes: everything. Produces: verified spec §44 acceptance checklist.

- [ ] **Step 1: Clean-database migration verification**

```powershell
docker compose down -v
docker compose up -d
# wait until healthy, then from backend/:
uv run alembic upgrade head
uv run python -m app.seed
uv run pytest -v
```
Expected: migration applies from empty DB, seed prints 13 providers / 15 types (or silently skips), full suite PASS.

- [ ] **Step 2: Lint/format**

Run: `uv run ruff check .` then `uv run ruff format .` (if format changed files, re-run `uv run pytest -q`)
Expected: `All checks passed!`; suite still green.

- [ ] **Step 3: Spec acceptance checklist walk**

Walk spec §44 (Functional / Identity / Provider-taxonomy / Metadata-ownership / Lifecycle / Querying / API quality / Persistence / Future extensibility / Testing): for each checkbox, point to the passing test name or Step 1 command output that proves it. For "Future extensibility" items (versioning/deployment/association referencable), verify by inspection: `models` table has no version/deployment/agent columns, `canonical_key`+UUID identity stable, event contract exists. Record any gap and fix it before proceeding.

- [ ] **Step 4: README + commit**

```powershell
git add README.md docs
git commit -m "docs: backend quickstart and acceptance verification"
```

---

## Out-of-scope guard (spec §43 — do not build)

No versioning, deployment, endpoints, agents/applications, usage, discovery connectors, LangChain coupling, audit subsystem, risk/eval/cost, frontend, provider secrets in metadata, hard-coded provider enum, Redis/RustFS, Kafka, vector DB, microservices.
