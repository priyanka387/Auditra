# Model Discovery (Feature 6) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add bounded Model Discovery V1 to Auditra: capture model observations from manual/LangChain/LangGraph sources, normalize identity, deduplicate idempotently, and reconcile against the canonical Model Registration registry.

**Architecture:** New top-level module `app/modules/model_discovery/` (mirrors the `model_usage` module layout from Feature 5). Observations flow adapter → `DiscoveryCreate` (common input contract) → `DiscoveryService.ingest()` → normalize/canonicalize → dedupe → reconcile via existing `find_by_canonical_key` → PostgreSQL table `model_discovery`. Discovery never writes canonical model records; registration handoff delegates to the existing `ModelService.register_model`.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 (sync), Alembic, PostgreSQL 16 (docker, port 5433), langchain-core 1.6.6, langgraph 1.2.12, pytest, ruff, uv.

**Spec:** `spec(3).md` (repo root) — sections cited inline as "spec §N".

## Global Constraints

- Discovery discovers; Model Registration owns canonical records — no second `models` table, no direct canonical inserts from discovery repositories (spec §6.1-2, §23).
- Canonical identity reuses `app.modules.model_inventory.domain.identity.build_canonical_key(provider, model_identifier)` → `"{provider.lower()}|{model_id}"`. This is the repo's existing registration format (spec §48: repository conventions win over spec §15's conceptual `:` form; reconciliation via `find_by_canonical_key` requires format parity).
- Sources V1: `manual`, `langchain`, `langgraph` only (spec §10).
- Statuses V1: `DISCOVERED`, `MATCHED`, `UNRESOLVED`, `REGISTERED`, `IGNORED`, `FAILED`; only diagram transitions (spec §20).
- No Redis, no RustFS, no auth subsystem, no frontend, no fuzzy matching, no network scanning (spec §42, §43, §39, §40, §17).
- Never persist/log: api_key, token, authorization, secret, password, prompts, model responses, `serialized` kwargs, invocation params (spec §12, §13, §39).
- Tenant scoping via `settings.default_tenant_id` threaded through service/repository (spec §41).
- Every test must pass against dockerized PostgreSQL at `localhost:5433`; run from `backend/` with `uv run`.
- Ruff: line-length 100; format + check must pass.
- Statuses stored UPPERCASE, source types stored lowercase (matches existing `models.lifecycle_state` / `model_usage_events.source` conventions).

## Review Focus

1. **Repeat observation must not lose first_seen_at or bump status across operator decisions** — repeated ingest keeps `first_seen_at`, increments `observation_count`, updates `last_seen_at`, merges metadata, and leaves `IGNORED`/`REGISTERED` sticky. Test: `test_repeated_observation_updates_count_and_preserves_first_seen`, `test_repeat_ingest_does_not_reset_ignored_status` (Task 5).
2. **Null source_identifier idempotency** — manual discovery has no `source_identifier`; two identical POSTs must dedupe on `(tenant, source_type, NULL, canonical_identity)` via `NULLS NOT DISTINCT`, not create two rows. Test: `test_manual_discovery_with_null_source_identifier_is_idempotent` (Task 5).
3. **Concurrent duplicate insert** — race on the unique constraint must recover into the update path, not 500. Test: `test_duplicate_insert_race_recovers_as_repeat` (Task 5) — simulate by inserting the conflicting row directly between find and create.
4. **Adapter must not persist prompts/credentials** — callback ignores `messages`/`prompts`/`serialized["kwargs"]`; observation metadata is an explicit allowlist. Tests: `test_extract_never_contains_prompt_or_secret_material` (Task 8), `test_langchain_invocation_persists_no_prompt_content` (Task 9), `test_langgraph_invocation_persists_no_prompt_content` (Task 10).
5. **Invalid lifecycle transitions rejected everywhere** — no generic status setter; match/ignore/register validate the spec §20 state machine. Tests: transition matrix in Task 1, `test_ignore_after_register_rejected` / `test_match_on_ignored_rejected` (Task 6/7).

---

### Task 1: Discovery domain (enums, errors, identity, transitions)

**Files:**
- Create: `backend/app/modules/model_discovery/__init__.py`
- Create: `backend/app/modules/model_discovery/enums.py`
- Create: `backend/app/modules/model_discovery/errors.py`
- Create: `backend/app/modules/model_discovery/domain.py`
- Test: `backend/tests/unit/test_discovery_domain.py`

**Interfaces:**
- Consumes: `build_canonical_key(provider_slug: str, native_model_id: str) -> str` from `app.modules.model_inventory.domain.identity`; `DomainError` from `app.modules.model_inventory.domain.errors`.
- Produces:
  - `DiscoverySourceType(str, Enum)`: `MANUAL="manual"`, `LANGCHAIN="langchain"`, `LANGGRAPH="langgraph"`
  - `DiscoveryStatus(str, Enum)`: `DISCOVERED/D MATCHED/UNRESOLVED/REGISTERED/IGNORED/FAILED` (values uppercase)
  - `normalize_provider(raw: str) -> str` — strip, reject blank (`ValueError`), lowercase, alias via `PROVIDER_ALIASES = {"open_ai": "openai"}`
  - `normalize_model_identifier(raw: str) -> str` — strip, reject blank (`ValueError`), preserve case
  - `canonical_identity(provider: str, model_identifier: str) -> str` — normalizes both, returns `build_canonical_key(...)`
  - `can_transition(current: DiscoveryStatus | str, target: DiscoveryStatus | str) -> bool` — exact spec §20 edge set; same-state returns True (idempotent refresh)
  - `DiscoveryError(code="DISCOVERY_ERROR", 400)`, `DiscoveryNotFoundError(404, "DISCOVERY_NOT_FOUND")`, `InvalidDiscoveryTransitionError(409, "DISCOVERY_TRANSITION_INVALID")`, `InvalidDiscoveryQueryError(400, "DISCOVERY_QUERY_INVALID")`, `DuplicateDiscoveryError(409, "DISCOVERY_DUPLICATE")`

- [x] **Step 1: Write failing unit tests** — `test_normalize_provider_lowercases_and_trims`, `test_normalize_provider_alias_open_ai`, `test_normalize_provider_blank_raises`, `test_normalize_model_identifier_preserves_case` (`"GPT-5.X"` → `"GPT-5.X"`), `test_canonical_identity_matches_registration_format` (assert `canonical_identity("OpenAI", "gpt-5.x") == "openai|gpt-5.x"`), `test_canonical_identity_blank_raises`, `test_valid_transitions_allowed` (parametrize the 9 diagram edges + same-state), `test_invalid_transitions_rejected` (parametrize `DISCOVERED→REGISTERED`, `MATCHED→UNRESOLVED`, `IGNORED→MATCHED`, `REGISTERED→IGNORED`, `FAILED→MATCHED`, `DISCOVERED→IGNORED`).
- [x] **Step 2: Run to verify failure** — `uv run pytest tests/unit/test_discovery_domain.py -v` → ImportError.
- [x] **Step 3: Implement the three modules** per Interfaces (domain edges: `DISCOVERED:{MATCHED,UNRESOLVED,FAILED}`, `UNRESOLVED:{MATCHED,REGISTERED,IGNORED}`, `MATCHED:{REGISTERED,IGNORED}`, `FAILED:{DISCOVERED,IGNORED}`, `REGISTERED:{}`, `IGNORED:{}`).
- [x] **Step 4: Verify pass** — `uv run pytest tests/unit/test_discovery_domain.py -v` → PASS.
- [x] **Step 5: Commit** — `git add backend/app/modules/model_discovery backend/tests/unit/test_discovery_domain.py; git commit -m "feat(ai-inventory): discovery domain enums, errors, identity and lifecycle transitions"`.

### Task 2: Pydantic schemas

**Files:**
- Create: `backend/app/modules/model_discovery/schemas.py`
- Test: `backend/tests/unit/test_discovery_schemas.py`

**Interfaces:**
- Consumes: `validate_metadata` from `app.modules.model_inventory.schemas.model` (10 KB cap + secret-key rejection), `DiscoverySourceType`/`DiscoveryStatus` (Task 1).
- Produces (all `ConfigDict(extra="forbid")`):
  - `DiscoveryCreate` — the common adapter/API input contract: `source_type: DiscoverySourceType`, `source_identifier: str | None` (max 255), `external_identifier: str | None` (max 255), `provider: str` (before-validator strip, non-empty, max 100), `model_identifier: str` (strip, non-empty, max 512), `model_type: str | None` (max 64), `display_name: str | None` (max 255), `metadata: dict[str, Any] = {}` (validated by `validate_metadata`)
  - `DiscoveryResponse` — `from_attributes=True`: `id, source_type, source_identifier, external_identifier, provider, model_identifier, model_type, display_name, canonical_identity, metadata` (via `AliasChoices("metadata_", "metadata")`), `status: DiscoveryStatus, matched_model_id: UUID | None, observation_count: int, first_seen_at, last_seen_at, created_at, updated_at` (tz-aware enforced by `_require_timezone` before-validators, copied from model_usage convention); `error_code, error_message` optional
  - `DiscoveryListResponse(items, page, page_size, total, total_pages)`
  - `DiscoveryFilter` — `page=1, page_size=25, source_type, provider, model_type, status, matched_model_id, source_identifier, canonical_identity, last_seen_from, last_seen_to, search, sort_by ∈ {last_seen_at, first_seen_at, observation_count, created_at, updated_at}, sort_order ∈ {asc, desc}`
  - `DiscoveryMatchRequest`, `DiscoveryIgnoreRequest` — empty bodies (`extra="forbid"`)
  - `DiscoveryRegisterRequest` — optional `name: str | None` (max 255), `model_type_slug: str | None` (max 64)

- [x] **Step 1: Write failing tests** — `test_create_strips_and_accepts_valid_payload`, `test_create_blank_provider_rejected` (ValueError→pydantic ValidationError), `test_create_secret_metadata_key_rejected` (`{"api_key": "x"}` raises), `test_create_oversized_metadata_rejected` (>10 000 bytes), `test_create_forbids_extra_fields`, `test_response_parses_orm_attributes` (build `SimpleNamespace`-like stub with `metadata_` attr → `DiscoveryResponse.model_validate(..., from_attributes=True)`), `test_response_rejects_naive_timestamps`.
- [x] **Step 2: Run to verify failure.**
- [x] **Step 3: Implement `schemas.py`.**
- [x] **Step 4: Verify pass** — `uv run pytest tests/unit/test_discovery_schemas.py -v`.
- [x] **Step 5: Commit** — `feat(ai-inventory): discovery pydantic schemas with metadata secret validation`.

### Task 3: ORM model + Alembic migration

**Files:**
- Create: `backend/app/modules/model_discovery/models.py`
- Create: `backend/alembic/versions/0006_model_discovery.py`
- Modify: `backend/tests/conftest.py:55-58` (TRUNCATE list — add `model_discovery`)
- Test: `backend/tests/integration/test_migration.py` (append one test)

**Interfaces:**
- Consumes: `Base` from `app.core.db`; migration head `0005`.
- Produces: ORM class `ModelDiscovery`, table `model_discovery`.

Column set (spec §24 + repo conventions):
```
id UUID PK default uuid4;  tenant_id UUID NOT NULL;
source_type VARCHAR(32) NOT NULL (ck: manual|langchain|langgraph);
source_identifier VARCHAR(255) NULL;  external_identifier VARCHAR(255) NULL;
provider VARCHAR(100) NOT NULL;  model_identifier VARCHAR(512) NOT NULL;
model_type VARCHAR(64) NULL;  display_name VARCHAR(255) NULL;
canonical_identity VARCHAR(1024) NOT NULL;
metadata JSON() variant JSONB NOT NULL server_default '{}';
status VARCHAR(32) NOT NULL (ck: DISCOVERED|MATCHED|UNRESOLVED|REGISTERED|IGNORED|FAILED);
matched_model_id UUID NULL FK models.id ondelete RESTRICT;
first_seen_at, last_seen_at TIMESTAMPTZ NOT NULL;
observation_count BIGINT NOT NULL default 1 (ck >= 1);
error_code VARCHAR(100) NULL;  error_message TEXT NULL;
created_at, updated_at TIMESTAMPTZ NOT NULL (server_default now(), onupdate)
```
Constraints/indexes (spec §25):
- `uq_model_discovery_tenant_source_identity` UNIQUE (tenant_id, source_type, source_identifier, canonical_identity) **with `postgresql_nulls_not_distinct=True`**
- `ix_model_discovery_tenant_canonical_identity` (tenant_id, canonical_identity)
- `ix_model_discovery_tenant_status` (tenant_id, status), `ix_model_discovery_tenant_source_type` (tenant_id, source_type), `ix_model_discovery_tenant_provider` (tenant_id, provider), `ix_model_discovery_matched_model_id` (matched_model_id), `ix_model_discovery_tenant_last_seen_at` (tenant_id, last_seen_at DESC)

- [x] **Step 1: Write failing migration test** — `test_model_discovery_migration_downgrade_and_upgrade` following the Task-usage pattern in `test_migration.py:107-142`: assert table exists; unique index present + unique; FK `models` with `ondelete=RESTRICT`; checks `ck_model_discovery_status`, `ck_model_discovery_observation_count`; downgrade to `0005` drops table (and keeps `model_usage_events`), upgrade to head restores it.
- [x] **Step 2: Run to verify failure** — `uv run pytest tests/integration/test_migration.py::test_model_discovery_migration_downgrade_and_upgrade -v` → FAIL (no table).
- [x] **Step 3: Implement `models.py` + `0006_model_discovery.py`** (revision `"0006"`, `down_revision="0005"`, full reversible upgrade/downgrade, `sa.UniqueConstraint(..., postgresql_nulls_not_distinct=True)`), add `model_discovery` to conftest TRUNCATE list.
- [x] **Step 4: Verify** — migration test PASS; then `uv run alembic upgrade head && uv run alembic downgrade 0005 && uv run alembic upgrade head` against docker Postgres succeeds.
- [x] **Step 5: Commit** — `feat(ai-inventory): model_discovery ORM, migration 0006 with idempotency constraint`.

### Task 4: Repository layer

**Files:**
- Create: `backend/app/modules/model_discovery/repository.py`

**Interfaces:**
- Consumes: `ModelDiscovery` (Task 3), `DiscoveryFilter` (Task 2), `DuplicateDiscoveryError` (Task 1).
- Produces (all take `db: Session` first; `tenant_id: UUID` on every query):
  - `create_discovery(db, **fields) -> ModelDiscovery`
  - `get_discovery(db, tenant_id, discovery_id) -> ModelDiscovery | None`
  - `find_for_ingest(db, tenant_id, source_type, source_identifier, canonical_identity) -> ModelDiscovery | None` (source_identifier may be None; `is NULL` predicate)
  - `list_discoveries(db, tenant_id, filters: DiscoveryFilter) -> tuple[list[ModelDiscovery], int]` — filters: exact `source_type/provider/model_type/status/matched_model_id/source_identifier/canonical_identity`, `last_seen_at` range, `search` ILIKE over `provider, model_identifier, display_name, canonical_identity`; `ORDER BY` allow-list mapping sort_by → column, default `last_seen_at DESC`; raises `InvalidDiscoveryQueryError` on unknown sort
  - `commit(db)` — `IntegrityError` → rollback → `DuplicateDiscoveryError` (copy of `model_usage/repository.py:35` pattern)

No governance decisions here (spec §37).

- [x] **Step 1: Write failing tests inside** `backend/tests/integration/test_discovery_service.py` (create file): `test_repository_roundtrip_and_filters` — create two rows with distinct sources/status, assert `get_discovery`, `find_for_ingest` hit/miss incl. `source_identifier=None` case, `list_discoveries` filter by status + provider + search + pagination total.
- [x] **Step 2: Run to verify failure.**
- [x] **Step 3: Implement `repository.py`.**
- [x] **Step 4: Verify pass** — `uv run pytest tests/integration/test_discovery_service.py -v`.
- [x] **Step 5: Commit** — `feat(ai-inventory): discovery repository persistence and queries`.

### Task 5: Service — ingest, dedupe, reconcile, get, list

**Files:**
- Create: `backend/app/modules/model_discovery/service.py`

**Interfaces:**
- Consumes: repository (Task 4), `canonical_identity` (Task 1), `find_by_canonical_key(db, tenant_id, key)` + `Model` from `app.modules.model_inventory.repositories.model_repository` (reconciliation, spec §17 step 1), `DiscoveryCreate`/`DiscoveryResponse`/`DiscoveryFilter` (Task 2).
- Produces: `class DiscoveryService` with repo-standard ctor `(db, tenant_id, request_id=None, actor=None)`:
  - `ingest(payload: DiscoveryCreate) -> tuple[DiscoveryResponse, bool]` — normalize → canonical → `find_for_ingest`:
    - miss → insert `status=DISCOVERED, observation_count=1, first_seen_at=last_seen_at=now(UTC)` → reconcile → commit; on `DuplicateDiscoveryError` → refetch → repeat path (race recovery)
    - hit → repeat path
    - repeat path: `observation_count += 1`, `last_seen_at = now`, `metadata = {**old, **new}` (shallow, incoming wins), re-reconcile only when status ∈ {DISCOVERED, UNRESOLVED, FAILED, MATCHED} (re-run keeps MATCHED or flips UNRESOLVED→MATCHED); status ∈ {IGNORED, REGISTERED} left untouched; returns `created=False`
  - `_reconcile(entity) -> None` — `find_by_canonical_key` → match: `status=MATCHED, matched_model_id=model.id`; miss: `status=UNRESOLVED, matched_model_id=None` (spec §18)
  - `get(discovery_id) -> DiscoveryResponse` (404 via `DiscoveryNotFoundError`)
  - `list(filters) -> tuple[list[DiscoveryResponse], int]`
  - `FAILED` is part of the state machine (Task 1) but has no V1 writer — no failure path persists partial rows in V1. Do not invent one; record as a documented limitation in Task 12.
  - Structured logs (`auditra.discovery`): ingest result with `discovery_id, source_type, canonical_identity, status, observation_count, created, request_id`; never log metadata contents. These logs are the V1 audit extension point (spec §57 event names documented as reserved for Feature 7 — do not add an event dispatcher).
- Response conversion: `DiscoveryResponse.model_validate(entity, from_attributes=True)`.

- [x] **Step 1: Write failing tests** (append to `tests/integration/test_discovery_service.py`):
  - `test_ingest_creates_unresolved_observation` — no canonical model → status UNRESOLVED, matched None, count 1
  - `test_ingest_matches_existing_canonical_model` — seed via `parent_model` fixture (`openai|versioned-1`) → ingest provider `"OpenAI "`, model `"versioned-1"` → MATCHED + `matched_model_id == parent_model.id`, no new `Model` row (count models before/after)
  - `test_repeated_observation_updates_count_and_preserves_first_seen` — two ingests → count 2, `first_seen_at` unchanged, `last_seen_at` advanced
  - `test_manual_discovery_with_null_source_identifier_is_idempotent` — same payload twice (`source_identifier=None`) → single row, count 2, API-level `created` flags `(True, False)`
  - `test_repeat_ingest_does_not_reset_ignored_status` — set status IGNORED directly, re-ingest → still IGNORED, count incremented
  - `test_duplicate_insert_race_recovers_as_repeat` — monkeypatch `repository.find_for_ingest` to return None once and pre-insert the conflicting row → ingest returns `created=False`, count 2, no exception
  - `test_list_filters_and_search` — provider/status/search filters + sort allow-list, unknown sort raises `InvalidDiscoveryQueryError`
- [x] **Step 2: Run to verify failure.**
- [x] **Step 3: Implement `service.py`.**
- [x] **Step 4: Verify pass** — `uv run pytest tests/integration/test_discovery_service.py -v`.
- [x] **Step 5: Commit** — `feat(ai-inventory): discovery service with idempotent ingest and reconciliation`.

### Task 6: Service actions — match, ignore, registration handoff

**Files:**
- Modify: `backend/app/modules/model_discovery/service.py`

**Interfaces:**
- Consumes: Task 5 service; `ModelService.register_model(payload: ModelCreate) -> Model` (spec §23 handoff); `ModelCreate` from `app.modules.model_inventory.schemas.model`; `InvalidDiscoveryTransitionError`/`can_transition` (Task 1).
- Produces on `DiscoveryService`:
  - `match(discovery_id) -> DiscoveryResponse` — fetch (404) → `_reconcile` → validate transition (`ensure_transition(entity, target)` raising `InvalidDiscoveryTransitionError` when current ∈ {IGNORED, REGISTERED} or target not allowed) → commit
  - `ignore(discovery_id) -> DiscoveryResponse` — `ensure_transition(current, IGNORED)` (allowed from UNRESOLVED/MATCHED/FAILED only, spec §20) → set IGNORED, `matched_model_id` untouched
  - `register(discovery_id, payload: DiscoveryRegisterRequest | None) -> DiscoveryResponse` — `ensure_transition(current, REGISTERED)` (UNRESOLVED or MATCHED only) → build `ModelCreate(provider_slug=entity.provider, model_type_slug=payload.model_type_slug or entity.model_type or "llm", name=payload.name or entity.display_name or entity.model_identifier, native_model_id=entity.model_identifier, source_type="IMPORT", source_reference=f"model-discovery:{entity.id}", metadata=entity.metadata_)` → `ModelService(self.db, self.tenant_id, request_id=self.request_id, actor=self.actor).register_model(...)` → set `status=REGISTERED, matched_model_id=model.id` → commit. Registration validation errors (`ProviderNotFoundError`, `DuplicateModelError`, …) propagate unchanged (governance preserved, spec §23).

- [x] **Step 1: Write failing tests** (append):
  - `test_match_links_newly_registered_model` — ingest UNRESOLVED → register model via `ModelService.register_model` directly → `service.match(id)` → MATCHED with model id
  - `test_ignore_from_unresolved_sets_ignored`; `test_ignore_after_register_rejected` (409 `DISCOVERY_TRANSITION_INVALID`); `test_match_on_ignored_rejected`
  - `test_register_creates_canonical_model_and_marks_registered` — seed_reference_data; handoff → status REGISTERED, `models` row exists with `canonical_key == entity.canonical_identity`, `source_reference == f"model-discovery:{id}"`, discovery `matched_model_id` set
  - `test_register_unknown_provider_propagates_registration_error` — provider `"nope"` → `ProviderNotFoundError` (discovery stays UNRESOLVED)
  - `test_register_is_idempotent_rejected` — second `register` → 409 transition error (REGISTERED has no outgoing edges)
- [x] **Step 2: Run to verify failure.**
- [x] **Step 3: Implement the three actions.**
- [x] **Step 4: Verify pass.**
- [x] **Step 5: Commit** — `feat(ai-inventory): discovery match/ignore actions and model registration handoff`.

### Task 7: FastAPI routes

**Files:**
- Create: `backend/app/modules/model_discovery/routes.py`
- Modify: `backend/app/main.py:98` (add `app.include_router(discovery_router)`)
- Test: `backend/tests/api/test_discovery_api.py`

**Interfaces:**
- Consumes: `DiscoveryService` (Tasks 5-6), schemas (Task 2), repo router conventions (`APIRouter(prefix="/api/v1/model-discovery")`, sync `def`, `Annotated[Session, Depends(get_db)]`, `_service(request, db)` helper with `settings.default_tenant_id`).
- Produces (spec §29):
  - `POST /api/v1/model-discovery` → 201 created / 200 repeat replay (`response.status_code` pattern from `model_usage/routes.py:57`), `response_model=DiscoveryResponse`
  - `GET /api/v1/model-discovery` → `DiscoveryListResponse` (all `DiscoveryFilter` query params, `page`/`page_size` bounded `Query(ge=1)`/`le=100`)
  - `GET /api/v1/model-discovery/{discovery_id}` → 200 / 404 envelope
  - `POST /api/v1/model-discovery/{discovery_id}/match` → 200, optional empty body `DiscoveryMatchRequest | None`
  - `POST /api/v1/model-discovery/{discovery_id}/ignore` → 200, optional empty body
  - `POST /api/v1/model-discovery/{discovery_id}/register` → 201, optional body `DiscoveryRegisterRequest | None`
  - No generic status PATCH; no retry endpoint (spec §29, §19).

- [x] **Step 1: Write failing API tests** — helper `_payload(**overrides)` / `_error(resp)` mirroring `tests/api/test_models_api.py`:
  - `test_create_returns_201_with_canonical_identity` (assert body `canonical_identity == "openai|gpt-5.x"`, `status == "UNRESOLVED"`, `observation_count == 1`)
  - `test_create_replay_returns_200_with_incremented_count`
  - `test_create_blank_provider_returns_422` with `error.code == "VALIDATION_ERROR"`
  - `test_create_secret_metadata_returns_422`
  - `test_get_missing_returns_404_envelope` (`DISCOVERY_NOT_FOUND`, `request_id` echoed via `X-Request-ID`)
  - `test_list_filters_by_status_and_provider_and_paginates`
  - `test_match_endpoint_reruns_reconciliation`; `test_ignore_endpoint_sets_ignored`
  - `test_register_endpoint_creates_model_and_returns_registered`
  - `test_invalid_transition_returns_409` (register twice → `DISCOVERY_TRANSITION_INVALID`)
  - `test_openapi_lists_discovery_paths`
- [x] **Step 2: Run to verify failure.**
- [x] **Step 3: Implement `routes.py` + wire `main.py`.**
- [x] **Step 4: Verify** — API tests PASS; full suite still green (`uv run pytest tests/api -v`).
- [x] **Step 5: Commit** — `feat(ai-inventory): model discovery REST API`.

### Task 8: Adapter boundary + LangChain adapter

**Files:**
- Create: `backend/app/modules/model_discovery/integrations/__init__.py`
- Create: `backend/app/modules/model_discovery/integrations/base.py`
- Create: `backend/app/modules/model_discovery/integrations/langchain.py`
- Test: `backend/tests/unit/test_discovery_langchain_adapter.py`

**Interfaces:**
- Consumes: `BaseCallbackHandler` from `langchain_core.callbacks` (public, same as `model_usage/integrations/langchain.py`), `DiscoveryCreate` (Task 2), `DiscoveryService.ingest` (Task 5).
- Produces:
  - `base.py`:
    - `InsufficientMetadataResult` frozen dataclass: `error_code: str` (`"INSUFFICIENT_METADATA"` | `"OBSERVATION_REJECTED"`), `reason: str`, `source_type: str`, `source_identifier: str | None`, `model_class: str | None`
    - `DiscoverySink(Protocol)` with `observe(payload: DiscoveryCreate) -> None` and `insufficient_metadata(result: InsufficientMetadataResult) -> None`
  - `langchain.py`:
    - `extract_observation(serialized, metadata, *, source: DiscoverySourceType, source_identifier: str | None) -> DiscoveryCreate | InsufficientMetadataResult` — pure, public-API-only:
      - provider = `metadata["auditra_provider"] or metadata["ls_provider"]`; model = `metadata["auditra_model_identifier"] or metadata["ls_model_name"]` (langchain-core 1.6.6 tracing params, verified in `chat_models.py:1540-1566`); missing either → `InsufficientMetadataResult(error_code="INSUFFICIENT_METADATA")`
      - `model_type` = `auditra_model_type` or `{"chat": "llm"}.get(ls_model_type, ls_model_type)`
      - `display_name` = `auditra_display_name` or model class name
      - `source_identifier` = `metadata["auditra_source_identifier"] or` ctor value
      - observation `metadata` built from **explicit allowlist only**: `framework` (`"langchain"`/`"langgraph"` from `source`), `framework_version` (`importlib.metadata.version("langchain-core")`), `model_class` (from `serialized["id"][-1]` when it is a non-empty list — kwargs NEVER read), `environment` (`auditra_environment`), `langgraph_node` (`metadata["langgraph_node"]`, present when called under LangGraph)
      - Pydantic `ValidationError` on `DiscoveryCreate.model_validate(...)` → `InsufficientMetadataResult(error_code="OBSERVATION_REJECTED")`
    - `class AuditraDiscoveryCallback(BaseCallbackHandler)` — ctor `(sink: DiscoverySink, *, source: DiscoverySourceType = LANGCHAIN, source_identifier: str | None = None)`; `on_chat_model_start`/`on_llm_start` call `extract_observation` and dispatch to sink; **`messages`/`prompts`/`kwargs` are never read**; sink exceptions swallowed unless `strict=True` (ctor flag) — mirrors `ServiceUsageCollector`
  - `service.py` gains `class ServiceDiscoverySink` — ctor `(service: DiscoveryService, *, strict: bool = False)`; `observe` → `service.ingest(payload)` (log, swallow unless strict); `insufficient_metadata` → `logger.warning` with `error_code/reason/source_type/model_class` only
- Adapter contract note (spec §38): future connectors emit `DiscoveryCreate` through a `DiscoverySink`; core reconciliation is untouched — documented, no speculative `discover(context)` pull interface shipped.

- [x] **Step 1: Write failing unit tests** (`test_discovery_langchain_adapter.py`):
  - `test_extract_from_langsmith_tracing_params` — `{"ls_provider": "openai", "ls_model_name": "gpt-5.x", "ls_model_type": "chat"}` → `DiscoveryCreate(provider="openai", model_identifier="gpt-5.x", model_type="llm")`
  - `test_extract_auditra_metadata_overrides_win`
  - `test_extract_missing_model_name_returns_insufficient` — `{"ls_provider": "x"}` → `error_code == "INSUFFICIENT_METADATA"`
  - `test_extract_reads_model_class_not_kwargs` — serialized `{"id": ["a","b","ChatFoo"], "kwargs": {"api_key": "sk-secret"}}` → `metadata["model_class"] == "ChatFoo"`, no `"api_key"`/`"sk-secret"` anywhere in the model dump
  - `test_extract_never_contains_prompt_or_secret_material` — assert `prompt not in json.dumps(payload.model_dump())`
  - `test_extract_rejects_secret_config_metadata` — `{"ls_provider": "openai", "ls_model_name": "m", "api_key": "sk-x"}`… note: secret arrives as *allowed key list* only, so instead pass `auditra_provider` + a `metadata` dict cannot carry extras — covered by DiscoveryCreate validation: simulate payload with `model_type` from config containing secret-looking `auditra_display_name`? Simpler real case: `auditra_environment="prod", api_key` is never copied because allowlist. Test: `extract` with metadata containing `{"ls_provider": "openai", "ls_model_name": "m", "authorization": "Bearer x"}` → payload contains no `authorization` value (allowlist proves it).
  - `test_callback_dispatches_observation_to_sink`; `test_callback_dispatches_insufficient_to_sink`
- [x] **Step 2: Run to verify failure.**
- [x] **Step 3: Implement `integrations/base.py`, `integrations/langchain.py`, `ServiceDiscoverySink`.**
- [x] **Step 4: Verify pass.**
- [x] **Step 5: Commit** — `feat(ai-inventory): LangChain discovery adapter and observation sink`.

### Task 9: LangChain integration test (real model run)

**Files:**
- Test: `backend/tests/integration/test_langchain_discovery.py`

**Interfaces:** Consumes Tasks 5-8 + `langchain_core.language_models.fake_chat_models.GenericFakeChatModel` (public test model, no paid API).

- [x] **Step 1: Write integration tests:**
  - helper: build `GenericFakeChatModel(messages=iter([AIMessage(content="hi")]))`, `ServiceDiscoverySink(DiscoveryService(db, tenant))`, `AuditraDiscoveryCallback(sink, source_identifier="support-service")`
  - `test_langchain_invocation_persists_observation_matched` — seed_reference_data + register `openai|gpt-5.x` via `ModelService`; invoke `model.invoke("hello", config={"callbacks": [cb], "metadata": {"auditra_provider": "openai", "auditra_model_identifier": "gpt-5.x", "auditra_environment": "test"}})` → row exists, `source_type=="langchain"`, `source_identifier=="support-service"`, `status=="MATCHED"`, `matched_model_id` set, `metadata["framework"]=="langchain"`, `metadata["model_class"]` present
  - `test_langchain_repeated_invocation_increments_count` — invoke twice → count 2, single row
  - `test_langchain_invocation_persists_no_prompt_content` — invoke with prompt `"SUPER-SECRET-PROMPT"` → `json.dumps(metadata)` contains neither the prompt nor response text
  - `test_langchain_insufficient_metadata_not_persisted` — invoke model with no ls_model_name and no overrides → sink recorded `InsufficientMetadataResult`, zero rows
- [x] **Step 2: Run to verify failure.**
- [x] **Step 3: Fix implementation only if tests reveal gaps (do not weaken assertions).**
- [x] **Step 4: Verify pass** — `uv run pytest tests/integration/test_langchain_discovery.py -v`.
- [x] **Step 5: Commit** — `test: langchain discovery integration coverage`.

### Task 10: LangGraph adapter + integration test

**Files:**
- Create: `backend/app/modules/model_discovery/integrations/langgraph.py`
- Test: `backend/tests/integration/test_langgraph_discovery.py`

**Interfaces:**
- Consumes: `AuditraDiscoveryCallback` (Task 8); langgraph `StateGraph` (dev dep 1.2.12); node metadata `langgraph_node` verified at `langgraph/pregel/_algo.py:656`.
- Produces: `langgraph_discovery_config(sink: DiscoverySink, *, source_identifier: str | None = None, environment: str | None = None, tags: list[str] | None = None) -> dict[str, Any]` — returns `{"tags": ["auditra", ...], "metadata": {"auditra_source_identifier":..., "auditra_environment":...}, "callbacks": [AuditraDiscoveryCallback(sink, source=DiscoverySourceType.LANGGRAPH, source_identifier=source_identifier)]}` (mirror of `model_usage/integrations/langgraph.py`).

- [x] **Step 1: Write failing tests** (`tests/integration/test_langgraph_discovery.py`):
  - helper builds `StateGraph` with one node `call_model(state)` invoking a `GenericFakeChatModel` under `langgraph_discovery_config(sink, source_identifier="support-graph", environment="test")`
  - `test_langgraph_run_persists_observation_with_node_context` — row `source_type=="langgraph"`, `source_identifier=="support-graph"`, `metadata["langgraph_node"] == "call_model"`, provider/model from `auditra_provider`/`auditra_model_identifier` metadata overrides
  - `test_langgraph_repeated_runs_are_idempotent` — two `graph.invoke` → one row, count 2
  - `test_langgraph_invocation_persists_no_prompt_content` — prompt text absent from stored metadata
- [x] **Step 2: Run to verify failure.**
- [x] **Step 3: Implement `integrations/langgraph.py`** (config helper only; extraction already shared).
- [x] **Step 4: Verify pass.**
- [x] **Step 5: Commit** — `feat(ai-inventory): LangGraph discovery integration`.

### Task 11: Security review + full verification

**Files:**
- Modify: only fixes discovered by review.

- [x] **Step 1: Run full suite + linters** — from `backend/`: `uv run ruff format . && uv run ruff check . && uv run pytest -v` (all existing + new tests green).
- [x] **Step 2: Secret-leak review** — grep new code/tables: no `api_key|token|password|authorization|secret` persisted; confirm adapter never reads `messages`, `prompts`, `serialized["kwargs"]`, `invocation_params`; confirm logs (`auditra.discovery`) contain only ids/source/canonical_identity/status/error_code; check test DB rows: `SELECT metadata FROM model_discovery` contains no prompt material (covered by tests).
- [x] **Step 3: Constraint/index review** — `\d model_discovery` in psql (docker) matches spec §25 list; unique constraint enforced (race test).
- [x] **Step 4: Lifecycle review** — no endpoint exposes generic status set; only match/ignore/register transitions exist.
- [x] **Step 5: Adapter boundary review** — no langchain/langgraph imports outside `integrations/` (grep); core domain framework-agnostic.
- [x] **Step 6: Commit any fixes** — `fix(ai-inventory): discovery review findings`.

### Task 12: Documentation + final report

**Files:**
- Create: `docs/features/model-inventory/model-discovery.md`
- Modify: `README.md:21` (`- [x] Model Discovery` → `- [x]`)

- [x] **Step 1: Write feature doc** covering spec §47's 16 sections (purpose, scope, architecture, sources, domain model, identity strategy incl. the `build_canonical_key` format decision, reconciliation, lifecycle state machine, database design, API reference, LangChain integration, LangGraph integration, security, testing, future connectors, limitations incl. FAILED-has-no-V1-writer) with the spec §8/§20 Mermaid diagrams. Include a §57 audit extension-point section: `model.discovered`-style names are reserved for Feature 7; structured logs are the V1 hook.
- [x] **Step 2: README checklist update.**
- [x] **Step 3: Final self-review** against spec §67 checklist + §63 acceptance criteria; fix gaps.
- [x] **Step 4: Commit** — `docs: model discovery feature documentation`.
- [x] **Step 5: Push branch** `feature/model-discovery` to GitHub and open PR (repo convention: feature branch → PR).

