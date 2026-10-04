# Application / Agent Association (AI Model Inventory Sub-feature 4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add governed Application → Agent → Model association inventory to the Auditra backend (headless, no frontend).

**Architecture:** Extend the existing `app/modules/model_inventory` modular monolith with three new tables (`applications`, `agents`, `agent_model_associations`), following the established layering: `api → services → repositories → models(ORM)` plus `schemas/` (Pydantic) and `domain/` (pure logic/events). Reuse existing Model/ModelVersion entities, error envelope, pagination, tenant scoping, secret rejection, and event dispatch verbatim.

**Tech Stack:** Python 3.11+, uv, FastAPI, Pydantic, SQLAlchemy 2, Alembic, PostgreSQL 16 (docker compose, port 5433), pytest, ruff (line-length 100).

**Spec:** Session-attached spec `AUDITRA_Inventory_Application_Agent_Association_spec.md`. During Task 1, copy it into the repo at `docs/features/model-inventory/application-agent-association-spec.md` so executors can read it.

## Global Constraints

- Structure: only `backend/app/modules/model_inventory/` grows; no new top-level packages, no second ORM/architecture.
- Migration: `backend/alembic/versions/0004_application_agent_association.py`, `revision="0004"`, `down_revision="0003"`, mako-style header, working `downgrade()`.
- Enums are `String(32)`/`String(64)` columns + `CheckConstraint` + `server_default` — never `postgresql.ENUM`.
- PKs: `Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)`; all business tables carry indexed `tenant_id: Mapped[UUID]` (no FK), scoped by `settings.default_tenant_id` in every service/repo call.
- Application status: `ACTIVE | INACTIVE | ARCHIVED` (CheckConstraint `ck_applications_status`, server_default `ACTIVE`); Agent identical (`ck_agents_status`); Association status: `ACTIVE | DISABLED` (`ck_agent_model_associations_status`, server_default `ACTIVE`).
- Association role (controlled): `PRIMARY, FALLBACK, EMBEDDING, RERANKER, VISION, MULTIMODAL, MODERATION, OTHER` (`ck_agent_model_associations_role`).
- `source` is `String(64)`, schema default `"manual"`, validated against `{manual, api, connector, discovery, system}` (same set as deployments).
- Framework on Agent: free-form `String(64)`, lowercased in schema (no enum) — extensible per FR-07.
- Identity uniques: `uq_applications_tenant_slug (tenant_id, slug)`; `uq_agents_tenant_application_slug (tenant_id, application_id, slug)`; association partial uniques on **active rows only**: `uq_ama_agent_role_priority_active UNIQUE (agent_id, role, selection_priority) WHERE status='ACTIVE'` and `uq_ama_agent_model_role_active UNIQUE (agent_id, model_id, role) WHERE status='ACTIVE'` (documented invariant: nullable `model_version_id` is NOT part of identity; disabled rows don't block re-association).
- FKs: `ondelete="RESTRICT"` on `agents.application_id`, `agent_model_associations.{agent_id,model_id,model_version_id}`. No cascading deletes; archive/disable at service level.
- Errors: new subclasses appended to `app/modules/model_inventory/domain/errors.py` (`DomainError` + `code` + `http_status`); never leak SQLAlchemy/PG errors — repository `commit()` catches `IntegrityError` → domain 409.
- List envelope: `items, page, page_size, total, total_pages`; query params `page (ge=1)=1`, `page_size (ge=1, le=100)=25`, `sort_by` allowlisted per repository (unknown → `InvalidSortFieldError` 400), `sort_order: Literal["asc","desc"]="desc"`; secondary order `id.asc()`.
- Services: `XService(db, tenant_id, request_id=None, actor=None)`; routers build with `settings.default_tenant_id` and `request.state.request_id`, `actor=None` (matches `api/deployments.py:28-31`).
- Cross-tenant/unknown → 404 (repo filters `tenant_id` first). No auth dependencies exist; keep it that way (tenant scoping is the boundary).
- Secret rejection: reuse `validate_metadata()` from `schemas/model.py` on `metadata` AND `configuration` fields (rejects secret-looking keys, 10 KB cap).
- Events: frozen dataclasses appended to `domain/events.py`, `Event` union widened, dispatched after `commit()` (pattern: `deployment_service.py:219-234`). No broker, no persistence.
- Slug rules: `mode="before"` normalize (trim, lowercase, runs of non `[a-z0-9]` → `-`, strip leading/trailing `-`), then must match `^[a-z0-9]+(?:-[a-z0-9]+)*$`, length 1–128.
- Status transitions (domain, `domain/lifecycle.py` addition): `ACTIVE↔INACTIVE`, `INACTIVE→ARCHIVED`, `ACTIVE→ARCHIVED`; `ARCHIVED` terminal (409). Associations: `ACTIVE↔DISABLED` freely.
- Lifecycle guards: ARCHIVED application → no new agents (409); ARCHIVED/INACTIVE agent → no new *active* association (409); model `lifecycle_state in {RETIRED, ARCHIVED}` → no new *active* association (409 `MODEL_NOT_ASSOCIABLE`); DEPRECATED still associable; pinned `model_version_id` must exist, same tenant, and `model_version.model_id == payload.model_id` (409 `MODEL_VERSION_BELONGS_TO_DIFFERENT_MODEL`).
- Delete endpoints are archive/disable semantics, `204`, idempotent (already-archived → 204 no-op like `archive_deployment`).
- No Redis, RustFS, message broker, frontend, usage tracking, discovery, auth system.
- Every new barrel (`models/__init__.py`, `schemas/__init__.py`, `repositories/__init__.py`, `services/__init__.py`, `domain/__init__.py`, `api/__init__.py`) exports the new names; `app/main.py` includes new routers.
- `tests/conftest.py` TRUNCATE list gains `agent_model_associations, agents, applications` (children before parents).
- ruff: `uv run ruff check .` and `uv run ruff format .` clean before each commit.

## Review Focus

- **Slug uniqueness across tenants** — same slug in two tenants must succeed; same slug twice in one tenant → 409. Test: `test_duplicate_slug_conflict` + cross-tenant test in persistence/API tests.
- **Disabled-then-recreated association** — disabling a PRIMARY (role, priority) row must free the (role, priority) slot for a new model; partial indexes are the mechanism. Test: `test_reassociate_after_disable_succeeds` (persistence).
- **Model-version mismatch** — version belonging to a different model must 409, version of another *tenant* must 404. Test: `test_version_model_mismatch_rejected`, `test_cross_tenant_version_not_found` (service/API).
- **Lifecycle guards vs. history** — archived agent keeps its associations queryable; retired model keeps existing associations but rejects new active ones; archived application rejects new agents but lists existing ones. Tests: `test_archived_agent_associations_remain_queryable`, `test_retired_model_rejects_new_active_association`, `test_archived_application_rejects_new_agent`.
- **Concurrent duplicate insert** — second insert of same (agent, role, priority) must hit the DB partial unique index even if service-level check is raced → IntegrityError mapped to 409. Test: direct ORM double-insert in persistence test.

---

### Task 1: Schema foundation — migration, ORM models, conftest, spec copy

**Files:**
- Create: `backend/alembic/versions/0004_application_agent_association.py`
- Create: `backend/app/modules/model_inventory/models/application_agent.py`
- Modify: `backend/app/modules/model_inventory/models/__init__.py`
- Modify: `backend/tests/conftest.py` (TRUNCATE list)
- Create: `docs/features/model-inventory/application-agent-association-spec.md` (verbatim copy of attached spec)
- Test: `backend/tests/integration/test_application_agent_persistence.py`

**Interfaces:**
- Produces: `Application`, `Agent`, `AgentModelAssociation` ORM classes (exported from `app.modules.model_inventory.models`); tables `applications`, `agents`, `agent_model_associations`.
- Columns (all three get `id`, `tenant_id`, `name`, `slug`, `display_name`, `description`, `status`, `source`, `owner_name`, `team_name`, `metadata_` mapped to `"metadata"` JSON/JSONB, `tags` JSON/JSONB, `created_at`, `updated_at`, `created_by`, `updated_by`, `archived_at`).
  - `Application.application_type: str | None (String(64))`
  - `Agent.application_id: UUID FK applications.id RESTRICT, agent_type, framework (String(64) nullable), framework_version (String(64) nullable), runtime_identifier (String(255) nullable), capabilities: dict JSONB nullable`
  - `AgentModelAssociation.agent_id FK RESTRICT, model_id FK models.id RESTRICT, model_version_id FK model_versions.id RESTRICT nullable, role String(32), selection_priority int, status String(32), configuration dict JSONB nullable, disabled_at timestamptz nullable`
- Uniques/indexes: see Global Constraints; plus `ix_applications_tenant_id_status`, `ix_applications_tenant_id_created_at DESC`; `ix_agents_tenant_id_status`, `ix_agents_application_id`, `ix_agents_framework`; associations: `ix_agent_model_associations_agent_id_status`, `ix_agent_model_associations_model_id_status`, `ix_agent_model_associations_model_version_id`, `ix_agent_model_associations_tenant_id_created_at DESC` (plain `agent_id`/`model_id` indexes omitted — covered by composite prefixes).

- [ ] **Step 1: Write the failing persistence test** `tests/integration/test_application_agent_persistence.py` covering: tables exist; round-trip insert of Application→Agent→Association; FK violation on bogus `application_id` raises `IntegrityError`; duplicate `(tenant_id, slug)` raises; duplicate active `(agent_id, role, selection_priority)` raises; duplicate active `(agent_id, model_id, role)` raises; same (role, priority) allowed again after first row `status="DISABLED"`; archived rows still selectable. Use `db` fixture + `parent_model` for FK targets.
- [ ] **Step 2: Run it, verify it fails** — `uv run pytest tests/integration/test_application_agent_persistence.py -v` (backend/ workdir) → ImportError/table missing.
- [ ] **Step 3: Write migration `0004_application_agent_association.py`** — `op.create_table` × 3, check constraints, FKs `ondelete="RESTRICT"`, indexes, two partial unique indexes (`postgresql_where=text("status = 'ACTIVE'")`), full `downgrade()` (drop indexes → drop tables).
- [ ] **Step 4: Write `models/application_agent.py`** mirroring migration exactly; export from `models/__init__.py`; update TRUNCATE in `tests/conftest.py` to `agent_model_associations, agents, applications, model_deployments, ...`.
- [ ] **Step 5: Copy spec** into `docs/features/model-inventory/application-agent-association-spec.md`.
- [ ] **Step 6: Run test to pass** — `uv run pytest tests/integration/test_application_agent_persistence.py -v` → PASS.
- [ ] **Step 7: Commit** — `feat: applications, agents, and agent-model association tables with constraints`

### Task 2: Domain — errors, status lifecycle, events, Pydantic schemas

**Files:**
- Modify: `backend/app/modules/model_inventory/domain/errors.py`
- Modify: `backend/app/modules/model_inventory/domain/lifecycle.py`
- Modify: `backend/app/modules/model_inventory/domain/events.py`
- Modify: `backend/app/modules/model_inventory/domain/__init__.py`
- Create: `backend/app/modules/model_inventory/schemas/application_agent.py`
- Modify: `backend/app/modules/model_inventory/schemas/__init__.py`
- Test: `backend/tests/unit/test_application_agent_schemas.py`, `backend/tests/unit/test_application_agent_lifecycle.py`, extend existing events unit test file (`tests/unit/test_events.py` or nearest equivalent — follow existing naming)

**Interfaces:**
- New errors (code → status): `ApplicationNotFound(404)`, `DuplicateApplicationError(409)`, `ApplicationArchivedError(409)`, `AgentNotFound(404)`, `DuplicateAgentError(409)`, `AgentArchivedError(409)`, `AgentNotActiveError(409)`, `AssociationNotFound(404)`, `DuplicateAssociationError(409)`, `ModelNotAssociableError(409)`, `ModelVersionMismatchError(409)`, `InvalidStatusTransitionError(409)`, `SecretMetadataRejectedError` — NOT needed (schema 422 covers it).
- Lifecycle: `STATUS_ALLOWED_TRANSITIONS: dict[str, set[str]]` and `can_status_transition(current: str, target: str) -> bool` for ACTIVE/INACTIVE/ARCHIVED; `ASSOCIATION_STATUSES = {"ACTIVE", "DISABLED"}`.
- Events: `ApplicationEvent(event_id, event_type, tenant_id, application_id, occurred_at, actor, change_summary, request_id)`, `AgentEvent(..., application_id, agent_id, ...)`, `AgentModelAssociationEvent(..., agent_id, model_id, association_id, ...)`; widen `Event` union; event types `application.created|updated|archived`, `agent.created|updated|archived`, `agent.model_associated|model_association_updated|model_disassociated`.
- Schemas (all `ConfigDict(extra="forbid")`, `_strip_strings` before-validators on string fields, house style from `schemas/deployment.py`):
  `ASSOCIATION_ROLES`, `ASSOCIATION_STATUSES`, `ENTITY_STATUSES`, `SOURCES` constants; `normalize_slug(value) -> str` helper; `ApplicationCreate/Update/Response/ListResponse`; `AgentCreate/Update/Response/ListResponse`; `AgentModelAssociationCreate/Update/Response/ListResponse`; `ModelAgentLinkResponse` (association fields + `agent_id/name/slug/status` + `application_id/name/slug`). `AgentCreate` has NO `application_id` in body (path-provided). Update schemas: only mutable fields (name, display_name, description, type/framework fields, owner/team, tags, metadata, status, configuration, role, priority, model_version_id) — never ids/tenant/timestamps.

- [ ] **Step 1: Write failing unit tests** — schema tests: slug normalization cases (`"My App!" → "my-app"`, empty → error, bad chars after normalize → error), empty name → error, invalid role → error, `selection_priority=0` → error, secret key in configuration/metadata → error, framework `"LangGraph"` → `"langgraph"`, update schema rejects `id` field. Lifecycle tests: each allowed transition True, `ARCHIVED→ACTIVE` False, unknown False. Events test: register handler, dispatch new event types, assert payload fields.
- [ ] **Step 2: Run, verify fail** — `uv run pytest tests/unit/test_application_agent_schemas.py tests/unit/test_application_agent_lifecycle.py -v`.
- [ ] **Step 3: Implement** errors, lifecycle additions, events, schemas per Interfaces.
- [ ] **Step 4: Run, verify pass** + full unit dir: `uv run pytest tests/unit -v`.
- [ ] **Step 5: Commit** — `feat: application/agent domain errors, status lifecycle, events, and pydantic schemas`

### Task 3: Application — repository, service, API, tests

**Files:**
- Create: `backend/app/modules/model_inventory/repositories/application_repository.py`
- Create: `backend/app/modules/model_inventory/services/application_service.py`
- Create: `backend/app/modules/model_inventory/api/applications.py`
- Modify: `repositories/__init__.py`, `services/__init__.py`, `api/__init__.py`, `app/main.py`
- Test: `backend/tests/integration/test_application_service.py`, `backend/tests/api/test_applications_api.py`

**Interfaces:**
- Repo: `ApplicationFilters(search, status, application_type, owner, team, created_after/before, updated_after/before)` dataclass; `get_application(db, tenant_id, id)`, `find_duplicate_slug(db, tenant_id, slug)`, `create_application(db, **fields)`, `query_applications(db, tenant_id, filters, page, page_size, sort_by, sort_order, include_archived) -> (list, int)` with `ALLOWED_SORT_FIELDS = {created_at, updated_at, name, slug, status, application_type}`, `commit(db)` (IntegrityError → `DuplicateApplicationError`).
- Service: `ApplicationService(db, tenant_id, request_id=None, actor=None)` with `create_application(payload) -> Application`, `get_application(id)`, `list_applications(...)`, `update_application(id, payload)` (applies mutable fields, enforces `can_status_transition`, sets `archived_at`/`status="ARCHIVED"` on archive, dispatches events), `archive_application(id)` (idempotent; if agents exist → still archive but do NOT delete them; never hard delete).
- API: routes per spec §21 — `POST /api/v1/applications` 201, `GET /api/v1/applications`, `GET/PATCH /api/v1/applications/{application_id}`, `DELETE /api/v1/applications/{application_id}` 204. List supports `page, page_size, search, status, application_type, owner, team, include_archived, sort_by, sort_order`.
- [ ] **Step 1: Write failing API tests** (`client` fixture): create 201 + body, get 200, list paginated envelope, patch 200, delete 204 then get shows `status="ARCHIVED"`, duplicate slug → 409 envelope, invalid body (empty name) → 422, unknown id → 404, archived application PATCH back allowed only per transition rules (`ARCHIVED→ACTIVE` → 409).
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement repo, service, router, barrel exports, `main.py` include.**
- [ ] **Step 4: Run tests (API + integration service tests covering create/list/filter/sort/duplicate/lifecycle/event dispatch) → PASS.**
- [ ] **Step 5: Commit** — `feat: application CRUD API with slug uniqueness and archive lifecycle`

### Task 4: Agent — repository, service, API, tests

**Files:**
- Create: `repositories/agent_repository.py`, `services/agent_service.py`, `api/agents.py`
- Modify: three barrels + `app/main.py`
- Test: `tests/integration/test_agent_service.py`, `tests/api/test_agents_api.py`

**Interfaces:**
- Repo: `AgentFilters(search, status, framework, agent_type, application_id, owner, team, created/updated bounds)`; `get_agent(db, tenant_id, id)`, `find_duplicate_slug(db, tenant_id, application_id, slug)`, `create_agent(db, **fields)`, `query_agents(...)` (`ALLOWED_SORT_FIELDS = {created_at, updated_at, name, slug, status, framework, agent_type}`), `commit(db)` → `DuplicateAgentError`.
- Service: `AgentService(...)` — `create_agent(application_id, payload)` (404 unknown/cross-tenant app; 409 `ApplicationArchivedError` if app archived), `get_agent`, `list_agents(application_id|None, filters, ...)`, `update_agent(id, payload)` (status transitions via `can_status_transition`), `archive_agent(id)` (idempotent, never deletes associations).
- API: `POST /api/v1/applications/{application_id}/agents` 201; `GET /api/v1/applications/{application_id}/agents`; `GET /api/v1/agents` (search endpoint, `application_id` optional filter); `GET/PATCH /api/v1/agents/{agent_id}`; `DELETE /api/v1/agents/{agent_id}` 204.
- [ ] **Step 1: Failing tests** — API: create under app 201, duplicate slug in same app 409, same slug in *different* app 201, create under archived app 409, unknown app 404, list filtered by framework/agent_type/search, patch, archive, get archived agent still 200. Service: cross-tenant app → 404.
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** repo/service/router/barrels/main.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** — `feat: agent CRUD API with application scoping and lifecycle rules`

### Task 5: Agent ↔ Model association — repository, service, API, reverse lookup, tests

**Files:**
- Create: `repositories/agent_model_association_repository.py`, `services/agent_association_service.py`, `api/agent_models.py`
- Modify: three barrels + `app/main.py`
- Test: `tests/integration/test_agent_association_service.py`, `tests/api/test_agent_models_api.py`

**Interfaces:**
- Repo: `AssociationFilters(status, role, model_id, model_version_id, application_id)`; `get_association(db, tenant_id, id)`, `find_duplicate(db, tenant_id, agent_id, model_id, role, priority)` (active rows), `create_association(db, **fields)`, `query_agent_associations(db, tenant_id, agent_id, filters, page, page_size, sort_by, sort_order)` with `ALLOWED_SORT_FIELDS = {created_at, updated_at, role, selection_priority, status}`, `query_model_agents(db, tenant_id, model_id, filters, page, page_size, sort_by, sort_order)` (joins Agent + Application, returns `(association, agent, application)` tuples), `commit(db)` → `DuplicateAssociationError`.
- Service `AgentAssociationService(db, tenant_id, request_id=None, actor=None)`:
  - `create_association(agent_id, payload)` — full transaction order per spec §29: agent 404 (tenant-scoped) → agent status guard (archived/inactive + ACTIVE payload → 409) → model `db.get(Model, payload.model_id)` tenant-checked 404 → model lifecycle guard (`RETIRED|ARCHIVED` + ACTIVE payload → 409 `ModelNotAssociableError`) → optional version: `db.get(ModelVersion, ...)` tenant-checked 404, `version.model_id == payload.model_id` else 409 `ModelVersionMismatchError` → duplicate check 409 → insert → commit → dispatch `agent.model_associated`.
  - `get_association(agent_id, association_id)` — verifies association belongs to agent AND agent to tenant (else 404).
  - `list_associations(agent_id, filters, pagination...)`.
  - `update_association(agent_id, id, payload)` — re-runs version validation + duplicate check for changed role/priority (excluding self), applies mutable fields (role, selection_priority, model_version_id, status, configuration, metadata), sets `disabled_at` when status→DISABLED and clears it when →ACTIVE, dispatches `agent.model_association_updated`.
  - `disable_association(agent_id, id)` — sets `status="DISABLED"`, `disabled_at=now`, idempotent 204, dispatches `agent.model_disassociated`.
  - `list_model_agents(model_id, filters, pagination...)` — model 404 tenant-scoped first.
- API (`api/agent_models.py`): `POST /api/v1/agents/{agent_id}/models` 201; `GET /api/v1/agents/{agent_id}/models` (filters `status`, `role`, `active_only: bool = False`→maps to status=ACTIVE, default sort `selection_priority` asc); `GET|PATCH /api/v1/agents/{agent_id}/models/{association_id}`; `DELETE .../{association_id}` 204; `GET /api/v1/models/{model_id}/agents` paginated `ModelAgentLinkListResponse` with filters `active_only, role, application_id`.
- [ ] **Step 1: Failing service tests** (integration): primary assoc success; fallback + embedding same priority different roles success; duplicate (role, priority) → `DuplicateAssociationError`; version mismatch → `ModelVersionMismatchError`; retired model → `ModelNotAssociableError`; archived agent → `AgentNotActiveError`; unknown agent/model → 404; cross-tenant model → 404; re-associate after disable succeeds; disabled rows returned in listings; reverse lookup returns agent+application.
- [ ] **Step 2: Failing API tests**: full acceptance scenario from spec §53 (create app → agent → associate → list agent models → reverse lookup → repeat create → 409 envelope with code `ASSOCIATION_DUPLICATE`); PATCH role change; DELETE disables (row still queryable via GET with `status=DISABLED`); unknown association 404; association-of-other-agent path 404; pagination + role filter + sort by priority.
- [ ] **Step 3: Run, verify fail.**
- [ ] **Step 4: Implement** repo/service/router/barrels/main.
- [ ] **Step 5: Run → PASS.**
- [ ] **Step 6: Commit** — `feat: agent-model association API with lifecycle guards and reverse lookup`

### Task 6: Migration round-trip test, LangGraph inventory test, docs, final verification

**Files:**
- Modify: `tests/integration/test_migration.py` (new case), `README.md` (status checklist `[ ] Agent Association` → `[x]`), `tests/conftest.py` only if fixtures needed
- Create: `tests/integration/test_langgraph_inventory.py`
- Create: `docs/features/model-inventory/application-agent-association.md`

**Interfaces:** consumes everything from Tasks 1–5; LangGraph test builds the spec §41 inventory (Application "Enterprise Knowledge Assistant", Agent "Knowledge Research Agent" `framework="langgraph"`, PRIMARY + EMBEDDING associations to two registered models) purely through services/API — no external API calls, no LangGraph dependency added (metadata-only representation).

- [ ] **Step 1: Failing migration test** — `test_application_agent_migration_downgrade_and_upgrade`: assert 3 tables + partial unique indexes (`uq_ama_agent_role_priority_active` unique=True) + FK `ondelete=RESTRICT` on `agents.application_id`; `downgrade("0003")` drops them; `upgrade("head")` restores.
- [ ] **Step 2: Failing LangGraph inventory test** — create inventory via services, assert `Agent.framework == "langgraph"`, list models shows PRIMARY/EMBEDDING, reverse lookup resolves Application, archived-agent associations still queryable.
- [ ] **Step 3: Run both → PASS.**
- [ ] **Step 4: Write feature doc** `docs/features/model-inventory/application-agent-association.md` following `model-deployment.md` template (numbered sections incl. domain model, DB schema + `0004` migration name, API table, validation rules, security, testing file list + counts, Mermaid diagram from spec §56, known limitations, uniqueness invariant note).
- [ ] **Step 5: README checklist update.**
- [ ] **Step 6: Full verification** — `docker compose up -d` (repo root); `uv run ruff check .`; `uv run ruff format .`; `uv run pytest -v` (expect all green, report exact counts).
- [ ] **Step 7: Commit** — `test: migration round-trip and langgraph inventory coverage; docs: application-agent association feature`
