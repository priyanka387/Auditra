# Model Deployment V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a tenant-scoped `ModelDeployment` inventory entity with lifecycle transitions and a separate `DeploymentEndpoint` entity (primary-inference rule, URL validation), exposed through eleven FastAPI routes under `/api/v1`.

**Architecture:** Extend the existing `model_inventory` modular monolith (same FastAPI app, same layered split: api/schemas/domain/services/repositories/models). Two new tables: `model_deployments` FK'd to `model_versions` (ON DELETE RESTRICT) and `deployment_endpoints` FK'd to `model_deployments`. Deployment/endpoint lifecycle rules live in `domain/` as pure functions; services own business rules (deployability guard, primary-endpoint rule, transition legality); repositories own persistence and map DB races to domain errors. Governance record only — no infrastructure provisioning, no URL fetching, no Redis/RustFS.

**Tech Stack:** Python 3.11+, uv (isolated venv), FastAPI, Pydantic v2, SQLAlchemy 2.0 (sync, psycopg3), Alembic, PostgreSQL 16 in Docker (host port 5433), pytest + httpx TestClient, ruff (line-length 100).

**Spec:** `AUDITRA_AI_MODEL_INVENTORY_MODEL_DEPLOYMENT_SPEC (1).md` at repo root (authoritative; the plan argues from it — executors read both).

## Global Constraints

- Every command runs via `uv run` from `backend/` (uv-managed isolated venv). No global pip installs.
- PostgreSQL 16 only, via root `docker-compose.yml`: db `auditra`, user `auditra`, password `auditra`, host port 5433; test db `auditra_test` (conftest drops/recreates it per session). No Redis, RustFS, Kafka, object storage.
- Backend only. No frontend, no auth system, no connectors, no health-check workers, no inference proxy.
- Model Versioning owns `ModelVersion` identity and lifecycle; this feature consumes it via `ModelVersionService`-level guard, never re-implements version lifecycle.
- Deployment status values (lowercase, per spec §16): `planned, deploying, active, degraded, failed, stopping, stopped, deprecated`. Initial status always `planned`, not client-settable (absent from create schema → 422 if sent). Transition edges exactly the matrix of spec §17; `deprecated` terminal; same-state → 409.
- `environment` allowlist (lowercase): `development, staging, production` (spec §8).
- `deployment_kind` allowlist (lowercase): `online_inference, batch, embedded, edge, scheduled, other` (spec §9).
- `source` / `status_source` allowlist (lowercase): `manual, api, connector, discovery, system` (spec §19/§114). `status_source` = payload `source` at create, `api` after any transition.
- `target_type`, `runtime`, `serving_framework` are free lowercase strings (no catalog): lengths ≤ 64 / 128 / 128 (spec §10/§11).
- Endpoint: `endpoint_type` ∈ `inference, health`; `protocol` ∈ `http, https, grpc, grpcs` (lowercase); `status` ∈ `active, inactive`; `health_status` ∈ `unknown, healthy, unhealthy`; `auth_type` ∈ `none, api_key, bearer, basic, mtls, custom, external_secret` (spec §20–§24, §53).
- Endpoint URL: max 2048 chars, `urlsplit` must parse, scheme must equal `protocol`, non-empty netloc, no embedded credentials, no fragment. URLs are stored, never fetched (spec §25, §69).
- `auth_reference`: optional, ≤ 255 chars, must contain `://` (it is a reference like `secret://prod/fraud`, never a raw secret); required-shape violation → 409 `INVALID_AUTH_REFERENCE`; `auth_type=none` + reference → 409 (spec §24, §56).
- Only one active (`archived_at IS NULL`) primary `inference` endpoint per deployment: application check + PostgreSQL partial unique index `uq_deployment_endpoints_primary_inference`, both → 409 `ENDPOINT_DUPLICATE_PRIMARY`. `is_primary=true` with `endpoint_type != inference` → 409 `ENDPOINT_INVALID_PRIMARY`.
- Uniqueness: `(tenant_id, model_version_id, environment, target_name, namespace)` on `model_deployments`; NULL `target_name`/`namespace` means Postgres does not collide them (documented limitation, spec §12).
- Deployment `DELETE` = archive: `status=deprecated`, `archived_at=now()` → 204, idempotent; endpoint `DELETE` = `archived_at=now()` → 204, idempotent. No physical deletes anywhere (spec §47, §52, §76).
- PATCH is metadata-only: schema `extra="forbid"` and simply omits `id, model_version_id, status, environment, deployment_kind, target_type, source` → sending any → 422 (repo precedent: lifecycle-via-PATCH → 422). Transition only via `POST /deployments/{id}/transition` (spec §18, §45).
- Model version guard at create: version must exist in tenant (else 404 `MODEL_VERSION_NOT_FOUND`) and `lifecycle_state ∈ {DRAFT, ACTIVE}` (else 409 `MODEL_VERSION_NOT_DEPLOYABLE`); archived version also rejected. Deployment creation never mutates the version (spec §14, §15, §77).
- `model_version_id` immutable after create (spec §46).
- Tenant: `settings.default_tenant_id`. Every repository query takes `tenant_id` as a required argument.
- Pagination: `page ≥ 1` default 1; `page_size` default 20 max 100 (over-max → 422). List defaults `sort_by=created_at`, `sort_order=desc`, `include_archived=false`. Sort allowlist deployments: `created_at, updated_at, name, environment, status, deployed_at, last_seen_at`; endpoints: `created_at, updated_at, name`; anything else → 400 `INVALID_SORT_FIELD`; always secondary-order `id ASC`.
- Search fields (deployments): `name, target_name, cluster_name, namespace, runtime, serving_framework, source_reference`; excludes archived by default (spec §42).
- Field limits: `name` 1–128 (trimmed, non-empty), `target_type` ≤ 64, `target_name/cluster_name/namespace` ≤ 255, `region/runtime/serving_framework` ≤ 128, `image_uri` free text, `desired_replicas/observed_replicas ≥ 0`, `source_reference` ≤ 255.
- `metadata` and `configuration` reuse `validate_metadata` (10 000 serialized bytes, recursive secret-key rejection) — configuration holds only non-secret serving config (spec §33, §34).
- Error envelope unchanged: `{"error": {code, message, details, request_id, http_status}}`. New codes: `DEPLOYMENT_NOT_FOUND` 404, `DEPLOYMENT_ALREADY_ARCHIVED` 409, `DEPLOYMENT_INVALID_TRANSITION` 409, `DEPLOYMENT_DUPLICATE` 409, `DEPLOYMENT_CONCURRENCY_CONFLICT` 409, `MODEL_VERSION_NOT_DEPLOYABLE` 409, `ENDPOINT_NOT_FOUND` 404, `ENDPOINT_INVALID_URL` 409, `ENDPOINT_DUPLICATE_PRIMARY` 409, `ENDPOINT_INVALID_PRIMARY` 409, `ENDPOINT_ALREADY_ARCHIVED` 409, `ENDPOINT_DUPLICATE` 409, `INVALID_AUTH_REFERENCE` 409. Reused: `MODEL_VERSION_NOT_FOUND` 404, `INVALID_SORT_FIELD` 400, `VALIDATION_ERROR` 422, `INTERNAL_ERROR` 500.
- Domain events through the existing in-process handler registry: `deployment.created/updated/status_changed/archived`, `deployment_endpoint.created/updated/archived` (spec §62). New frozen dataclasses `DeploymentEvent`, `DeploymentEndpointEvent`; `Event` union widened. No outbox, no audit tables.
- Optimistic concurrency: `__mapper_args__ = {"version_id_col": record_version}` on both ORM classes; `commit()` maps `StaleDataError` → `DeploymentConcurrencyConflictError`, `IntegrityError` → duplicate error (spec §74).
- Timestamps: `DateTime(timezone=True)` everywhere; API coerces naive datetimes to UTC (existing `_as_utc` convention).
- No new dependencies in `pyproject.toml`. Stdlib `urllib.parse` for URL checks.

## Review Focus

Spec-implied failure modes no happy-path test covers; each has a test in the task that owns it:

1. **Invalid lifecycle jump** — `active → stopped` and `deprecated → anything` must 409 `DEPLOYMENT_INVALID_TRANSITION`, never silently apply. Tests: Task 1 `test_deployment_transition_matrix_matches_spec`, Task 6 `test_invalid_transition_returns_409`.
2. **Duplicate primary inference endpoint** — two racing creates, one passing the app pre-check; DB partial unique index must reject the loser as 409, not 500. Tests: Task 4 `test_duplicate_primary_constrained_by_partial_index`, Task 6 `test_duplicate_primary_returns_409`.
3. **Hostile endpoint URLs** — `https://user:pass@host/x`, `https://host/x#frag`, scheme/protocol mismatch, non-URL garbage, >2048 chars → rejected at 409/422 without any network call. Tests: Task 3 `test_endpoint_url_validation`.
4. **Non-deployable version** — create deployment under `RETIRED`/`ARCHIVED` version → 409 `MODEL_VERSION_NOT_DEPLOYABLE`; unknown version → 404; archived deployment blocks new endpoints → 409. Tests: Task 5 `test_create_rejects_non_deployable_versions`, Task 6 `test_create_endpoint_on_archived_deployment_returns_409`.
5. **Archived semantics** — archived deployment out of default list, GET 200, PATCH/transition 409, re-DELETE 204; archived endpoint behaves the same; version hard-delete blocked by deployment FK. Tests: Task 6 `test_archive_workflow`, Task 4 `test_version_delete_blocked_by_deployment_fk`.
6. **PATCH bypass** — `status`/`model_version_id`/`environment` sent to PATCH → 422, record unchanged. Test: Task 6 `test_patch_immutable_fields_rejected`.

---

## File Structure

```text
C:\Auditra\
├── README.md                                       # modify: deployment endpoints + status line
├── AUDITRA_AI_MODEL_INVENTORY_MODEL_DEPLOYMENT_SPEC (1).md   # spec (existing)
├── docs/
│   ├── features/model-inventory/
│   │   └── model-deployment.md                     # create: feature documentation
│   └── superpowers/plans/
│       └── 2026-10-05-model-deployment-v1.md       # this plan
└── backend/
    ├── alembic/versions/
    │   └── 0003_model_deployment.py                # create
    ├── app/modules/model_inventory/
    │   ├── models/
    │   │   ├── deployment.py                       # create: ModelDeployment, DeploymentEndpoint
    │   │   └── __init__.py                         # modify: export both
    │   ├── domain/
    │   │   ├── lifecycle.py                        # modify: DeploymentStatus, DEPLOYMENT_ALLOWED_TRANSITIONS, can_deployment_transition, DEPLOYABLE_VERSION_STATES
    │   │   ├── errors.py                           # modify: 13 deployment/endpoint error classes
    │   │   ├── events.py                           # modify: DeploymentEvent, DeploymentEndpointEvent, Event union
    │   │   └── __init__.py                         # modify: re-export
    │   ├── schemas/
    │   │   ├── deployment.py                       # create
    │   │   ├── deployment_endpoint.py              # create
    │   │   └── __init__.py                         # modify: re-export
    │   ├── repositories/
    │   │   ├── deployment_repository.py            # create
    │   │   ├── deployment_endpoint_repository.py   # create
    │   │   └── __init__.py                         # modify: re-export
    │   ├── services/
    │   │   ├── deployment_service.py               # create
    │   │   ├── deployment_endpoint_service.py      # create
    │   │   └── __init__.py                         # modify: re-export
    │   └── api/
    │       ├── deployments.py                      # create: all 11 routes, own APIRouter(prefix="/api/v1")
    │       └── __init__.py                         # modify: include deployment router
    ├── app/main.py                                 # unchanged (router pulled via model_inventory.api)
    └── tests/
        ├── conftest.py                             # modify: truncate new tables, add parent_version fixture
        ├── unit/
        │   ├── test_deployment_lifecycle.py        # create
        │   ├── test_deployment_schemas.py          # create
        │   └── test_endpoint_schemas.py            # create
        ├── api/
        │   └── test_deployments_api.py             # create
        └── integration/
            ├── test_deployment_persistence.py      # create
            ├── test_deployment_service.py          # create
            └── test_migration.py                   # modify: 0002→0003 downgrade/upgrade test
```

---

### Task 1: Deployment domain — lifecycle, errors, events

**Files:**
- Modify: `backend/app/modules/model_inventory/domain/lifecycle.py`
- Modify: `backend/app/modules/model_inventory/domain/errors.py`
- Modify: `backend/app/modules/model_inventory/domain/events.py`
- Modify: `backend/app/modules/model_inventory/domain/__init__.py`
- Test: `backend/tests/unit/test_deployment_lifecycle.py`

**Interfaces:**
- Produces: `DeploymentStatus = Literal["planned","deploying","active","degraded","failed","stopping","stopped","deprecated"]`; `DEPLOYMENT_ALLOWED_TRANSITIONS: dict[str, set[str]]` (exact edges of spec §17); `can_deployment_transition(current: str, target: str) -> bool` (False for unknown state, same state, terminal `deprecated`); `DEPLOYABLE_VERSION_STATES: frozenset[str] = {"DRAFT", "ACTIVE"}`; error classes `DeploymentNotFoundError(404)`, `DeploymentAlreadyArchivedError(409)`, `InvalidDeploymentTransitionError(409, code=DEPLOYMENT_INVALID_TRANSITION)`, `DuplicateDeploymentError(409, code=DEPLOYMENT_DUPLICATE)`, `DeploymentConcurrencyConflictError(409)`, `ModelVersionNotDeployableError(409)`, `DeploymentEndpointNotFoundError(404, ENDPOINT_NOT_FOUND)`, `DuplicateEndpointError(409, ENDPOINT_DUPLICATE)`, `DuplicatePrimaryEndpointError(409, ENDPOINT_DUPLICATE_PRIMARY)`, `InvalidPrimaryEndpointError(409, ENDPOINT_INVALID_PRIMARY)`, `EndpointArchivedError(409, ENDPOINT_ALREADY_ARCHIVED)`, `InvalidEndpointUrlError(409, ENDPOINT_INVALID_URL)`, `InvalidAuthReferenceError(409, INVALID_AUTH_REFERENCE)`; dataclasses `DeploymentEvent(event_id, event_type, tenant_id, deployment_id, model_version_id, occurred_at, actor, change_summary, request_id)` and `DeploymentEndpointEvent(event_id, event_type, tenant_id, deployment_id, endpoint_id, occurred_at, actor, change_summary, request_id)`; `Event = ModelEvent | ModelVersionEvent | DeploymentEvent | DeploymentEndpointEvent`.

- [ ] **Step 1: Write failing unit tests** — `tests/unit/test_deployment_lifecycle.py`
  - `test_transition_matrix_matches_spec`: every edge of spec §17 asserts `can_deployment_transition(src, dst) is True` (planned→deploying/active/failed; deploying→active/degraded/failed; active→degraded/stopping/deprecated; degraded→active/stopping/failed/deprecated; failed→deploying/stopping/deprecated; stopping→stopped/failed; stopped→deploying/deprecated).
  - `test_invalid_transitions_rejected`: `active→stopped`, `planned→stopped`, `stopped→active`, `deprecated→active`, `deprecated→stopped`, same-state pairs (`active→active`, `planned→planned`), unknown states (`archived`, `""`) all False.
  - `test_deployable_version_states`: `DEPLOYABLE_VERSION_STATES == {"DRAFT", "ACTIVE"}`.
  - `test_error_codes_and_statuses`: each error class from Interfaces has expected `code`/`http_status`.
  - `test_event_dataclasses_are_frozen`: assigning any field raises `FrozenInstanceError`.
- [ ] **Step 2: Run and confirm failure** — `uv run pytest tests/unit/test_deployment_lifecycle.py -v` → ImportError (FAIL).
- [ ] **Step 3: Implement** in the three domain files + `domain/__init__.py` re-exports (`DEPLOYMENT_ALLOWED_TRANSITIONS`, `DEPLOYABLE_VERSION_STATES`, `can_deployment_transition`, `DeploymentStatus`, both event dataclasses).
- [ ] **Step 4: Run until green** — `uv run pytest tests/unit/test_deployment_lifecycle.py -v` → PASS.
- [ ] **Step 5: Commit** — `git add backend/app/modules/model_inventory/domain backend/tests/unit/test_deployment_lifecycle.py; git commit -m "feat: deployment lifecycle matrix, domain errors, and events"`

### Task 2: ORM models + Alembic migration

**Files:**
- Create: `backend/app/modules/model_inventory/models/deployment.py`
- Modify: `backend/app/modules/model_inventory/models/__init__.py`
- Create: `backend/alembic/versions/0003_model_deployment.py`
- Modify: `backend/tests/integration/test_migration.py`
- Modify: `backend/tests/conftest.py` (truncate list + `parent_version` fixture)

**Interfaces:**
- Produces: `ModelDeployment` (`__tablename__="model_deployments"`, columns exactly per spec §29 plus repo conventions `tenant_id`, `created_by`, `updated_by`, `record_version`, JSONB-variant `metadata_`/`configuration`, `__mapper_args__ version_id_col`), `DeploymentEndpoint` (`__tablename__="deployment_endpoints"`, spec §30 columns + `tenant_id`, `created_by`, `updated_by`, `record_version`). Both exported from `models/__init__.py`.
- Constraints: `uq_model_deployments_tenant_version_env_target_ns` on `(tenant_id, model_version_id, environment, target_name, namespace)`; CHECKs `ck_model_deployments_environment`, `ck_model_deployments_deployment_kind`, `ck_model_deployments_status`, `ck_model_deployments_replicas` (`desired_replicas >= 0 AND observed_replicas >= 0`); CHECKs `ck_deployment_endpoints_endpoint_type`, `ck_deployment_endpoints_protocol`, `ck_deployment_endpoints_status`, `ck_deployment_endpoints_health_status`; FK `model_deployments.model_version_id → model_versions.id ON DELETE RESTRICT`, `deployment_endpoints.deployment_id → model_deployments.id ON DELETE RESTRICT`; partial unique `uq_deployment_endpoints_primary_inference ON (deployment_id) WHERE is_primary AND endpoint_type='inference' AND archived_at IS NULL`.
- Indexes: `ix_model_deployments_tenant_model_version_env (tenant_id, model_version_id, environment)`, `ix_model_deployments_tenant_status`, `ix_model_deployments_tenant_environment_status`, `ix_model_deployments_tenant_target_type`, `ix_model_deployments_tenant_last_seen_at`, `ix_model_deployments_tenant_created_at`; `ix_deployment_endpoints_deployment_id`, `ix_deployment_endpoints_endpoint_type`, `ix_deployment_endpoints_tenant_status`.
- `parent_version` fixture in conftest: seeds reference data, builds on `parent_model`, creates `ModelVersion(lifecycle_state="DRAFT", identity_type="release", version_label="v1", canonical_version_key="release:v1", source_type="manual")`, commits, returns it.
- conftest truncate line becomes: `TRUNCATE model_deployments, deployment_endpoints, model_versions, model_tag_links, model_tags, models, model_types, model_providers RESTART IDENTITY CASCADE`.

- [ ] **Step 1: Write failing migration test** — add `test_model_deployment_migration_downgrade_and_upgrade(database)`: asserts `model_deployments` and `deployment_endpoints` exist; `command.downgrade(cfg, "0002")` removes both while `model_versions` stays; `command.upgrade(cfg, "head")` restores both; also asserts `sa.inspect(engine).get_unique_constraints("deployment_endpoints")` contains `uq_deployment_endpoints_primary_inference` after upgrade.
- [ ] **Step 2: Run and confirm failure** — `uv run pytest tests/integration/test_migration.py -v` → table-missing FAIL.
- [ ] **Step 3: Implement ORM** in `models/deployment.py` following `model_version.py` style (SQLAlchemy 2 `Mapped[]`/`mapped_column()`, `JSON().with_variant(JSONB, "postgresql")` for both JSONB columns, `CheckConstraint`/`Index`/`UniqueConstraint` in `__table_args__`), export from `models/__init__.py`.
- [ ] **Step 4: Write migration** `0003_model_deployment.py` mirroring the ORM (revision `"0003"`, down_revision `"0001"` → no: `"0002"`; `op.create_table` ×2, all indexes incl. `op.create_index(..., postgresql_where=sa.text("is_primary AND endpoint_type = 'inference' AND archived_at IS NULL"))`, reversible `downgrade()` dropping indexes then tables).
- [ ] **Step 5: Update conftest** truncate list and add `parent_version` fixture.
- [ ] **Step 6: Run** `uv run pytest tests/integration/test_migration.py -v` → PASS, then `uv run pytest -v` → whole suite still green (conftest change).
- [ ] **Step 7: Commit** — `git commit -m "feat: model_deployments and deployment_endpoints tables, constraints, indexes, and migration"`

### Task 3: Pydantic schemas (deployment + endpoint)

**Files:**
- Create: `backend/app/modules/model_inventory/schemas/deployment.py`
- Create: `backend/app/modules/model_inventory/schemas/deployment_endpoint.py`
- Modify: `backend/app/modules/model_inventory/schemas/__init__.py`
- Test: `backend/tests/unit/test_deployment_schemas.py`, `backend/tests/unit/test_endpoint_schemas.py`

**Interfaces:**
- Produces (deployment): module constants `DEPLOYMENT_ENVIRONMENTS`, `DEPLOYMENT_KINDS`, `DEPLOYMENT_SOURCES`, `DEPLOYMENT_STATUS_SOURCES`; schemas `DeploymentCreate` (extra=forbid; `name` 1–128; `environment`, `deployment_kind`, `target_type`, `source` required with allowlist/lowercase-normalize validators; optional `target_name, region, cluster_name, namespace, runtime, serving_framework, image_uri, desired_replicas, observed_replicas, configuration, metadata, source_reference`; NO `status`/`status_source`/`model_version_id` fields), `DeploymentUpdate` (extra=forbid; only `name, target_name, region, cluster_name, namespace, runtime, serving_framework, image_uri, desired_replicas, observed_replicas, configuration, metadata, source_reference, last_seen_at`), `DeploymentTransitionRequest` (extra=forbid; `status: DeploymentStatus` required; `reason: str|None` ≤ 1000; `observed_at: datetime|None`), `DeploymentResponse` (repo convention: includes `tenant_id`, `version` aliased from `record_version`, `metadata` aliased from `metadata_`), `DeploymentListResponse` (`items, page, page_size, total, total_pages`).
- Produces (endpoint): constants `ENDPOINT_TYPES, ENDPOINT_PROTOCOLS, ENDPOINT_STATUSES, HEALTH_STATUSES, AUTH_TYPES`; helper `validate_endpoint_url(protocol: str, url: str) -> str` (rules in Global Constraints); helper `validate_auth_reference(auth_type: str, auth_reference: str | None) -> None`; schemas `DeploymentEndpointCreate` (extra=forbid; `name, endpoint_type, protocol, url, auth_type` required; `route, auth_reference, is_primary=False, status="active", health_status="unknown", metadata` optional; `model_validator` cross-checks `is_primary`/`endpoint_type` → raises `ENDPOINT_INVALID_PRIMARY` message and `auth_reference` shape), `DeploymentEndpointUpdate` (extra=forbid; `name, protocol, url, route, auth_type, auth_reference, is_primary, status, metadata, health_status, last_health_check_at` — no `deployment_id`, no `endpoint_type`), `DeploymentEndpointResponse`, `DeploymentEndpointListResponse`.
- Validators strip whitespace on all strings; lowercase-normalize enums; `metadata`/`configuration` run through `validate_metadata`; replicas `ge=0`.
- [ ] **Step 1: Write failing tests** — `tests/unit/test_deployment_schemas.py`: `test_create_normalizes_and_accepts_valid`, `test_create_rejects_bad_environment_kind_source` (422-style `ValidationError`), `test_create_rejects_status_field`, `test_negative_replicas_rejected`, `test_name_rules` (empty/whitespace/129 chars rejected, padded string trimmed), `test_metadata_and_configuration_reject_secret_keys` (`{"api_key": "x"}`), `test_update_rejects_immutable_fields` (`model_version_id`, `status`, `environment` raise `ValidationError`).
  `tests/unit/test_endpoint_schemas.py`: `test_valid_urls_accepted` (`https://ai.example.com/v1/fraud`, `http://10.0.10.20:8000/infer`, `grpc://fraud.internal:9000`), `test_rejects_embedded_credentials`, `test_rejects_fragment`, `test_rejects_scheme_protocol_mismatch` (`protocol="https"` with `http://` URL), `test_rejects_missing_netloc_and_garbage` (`not a url`, `https://`), `test_rejects_oversized_url` (2049 chars), `test_auth_reference_rules` (`"sk-live-123"` rejected, `"secret://prod/fraud"` accepted, `auth_type="none"` + reference rejected), `test_primary_with_health_type_rejected`.
- [ ] **Step 2: Run** `uv run pytest tests/unit/test_deployment_schemas.py tests/unit/test_endpoint_schemas.py -v` → ImportError FAIL.
- [ ] **Step 3: Implement** both schema modules + `schemas/__init__.py` re-exports.
- [ ] **Step 4: Run** both files → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat: deployment and endpoint pydantic schemas with url, auth, and enum validation"`

### Task 4: Repositories + persistence guarantees

**Files:**
- Create: `backend/app/modules/model_inventory/repositories/deployment_repository.py`
- Create: `backend/app/modules/model_inventory/repositories/deployment_endpoint_repository.py`
- Modify: `backend/app/modules/model_inventory/repositories/__init__.py`
- Test: `backend/tests/integration/test_deployment_persistence.py`

**Interfaces:**
- Produces (deployment repo): `DeploymentFilters` dataclass (`environment, status, deployment_kind, target_type, runtime, region, model_version_id, model_id, search, created_after/before, updated_after/before, last_seen_after/before`); `ALLOWED_SORT_FIELDS: dict[str, Any]` (the seven allowlisted columns); `get_deployment(db, tenant_id, deployment_id) -> ModelDeployment | None`; `find_duplicate(db, tenant_id, model_version_id, environment, target_name, namespace) -> ModelDeployment | None`; `create_deployment(db, **fields) -> ModelDeployment`; `query_deployments(db, tenant_id, filters, page, page_size, sort_by, sort_order, include_archived) -> tuple[list, int]` (joins `ModelVersion` only when `filters.model_id` set; `InvalidSortFieldError` on bad sort; archived excluded unless requested); `commit(db)` mapping `IntegrityError → DuplicateDeploymentError`, `StaleDataError → DeploymentConcurrencyConflictError`.
- Produces (endpoint repo): `get_endpoint(db, tenant_id, endpoint_id)`, `list_endpoints(db, tenant_id, deployment_id, include_archived) -> list[DeploymentEndpoint]`, `find_primary_inference(db, tenant_id, deployment_id, exclude_id=None)`, `create_endpoint(db, **fields)`, `commit(db)` mapping `IntegrityError → DuplicatePrimaryEndpointError`, `StaleDataError → DeploymentConcurrencyConflictError`.
- [ ] **Step 1: Write failing integration tests** — `tests/integration/test_deployment_persistence.py` (fixture-driven against real Postgres, using `parent_version`):
  - `test_round_trip_and_jsonb_persistence` — insert deployment with `configuration={"tensor_parallel_size": 2}` + metadata, re-fetch, values survive; timestamps tz-aware.
  - `test_unique_identity_constraint_maps_to_domain_error` — second insert with same `(tenant_id, model_version_id, environment, target_name, namespace)` → `DuplicateDeploymentError` (call repo `commit`).
  - `test_null_target_name_does_not_collide` — two rows with `target_name=None` are both insertable (documented Postgres NULL semantics).
  - `test_version_delete_blocked_by_deployment_fk` — `sa.delete(ModelVersion)` on referenced version → `IntegrityError` (FK RESTRICT).
  - `test_partial_unique_primary_endpoint` — insert primary inference endpoint, second insert with different name but `is_primary=True` → `IntegrityError`; same row after `archived_at` set → allowed.
  - `test_query_filters_and_archive_exclusion` — status/environment/runtime filters, search on `target_name`, archived hidden by default.
  - `test_cross_tenant_isolation` — row under `tenant_id=uuid4()` invisible to `settings.default_tenant_id` queries.
- [ ] **Step 2: Run** → ImportError FAIL.
- [ ] **Step 3: Implement** both repository modules + `__init__.py` re-exports (copy structure from `model_version_repository.py`; keep business policy out).
- [ ] **Step 4: Run** file → PASS; then `uv run pytest -v` full suite green.
- [ ] **Step 5: Commit** — `git commit -m "feat: deployment and endpoint repositories with filters, pagination, and constraint mapping"`

### Task 5: Services (business rules)

**Files:**
- Create: `backend/app/modules/model_inventory/services/deployment_service.py`
- Create: `backend/app/modules/model_inventory/services/deployment_endpoint_service.py`
- Modify: `backend/app/modules/model_inventory/services/__init__.py`
- Test: `backend/tests/integration/test_deployment_service.py`

**Interfaces:**
- Produces: `DeploymentService(db, tenant_id, request_id=None, actor=None)` with `create_deployment(model_version_id: UUID, payload: DeploymentCreate) -> ModelDeployment`, `get_deployment(deployment_id) -> ModelDeployment`, `list_deployments(filters, page=1, page_size=20, sort_by="created_at", sort_order="desc", include_archived=False) -> tuple[list, int]`, `update_deployment(deployment_id, payload: DeploymentUpdate) -> ModelDeployment`, `transition_deployment(deployment_id, payload: DeploymentTransitionRequest) -> ModelDeployment` (409 on archived/illegal/same-state; sets `status_source="api"`, `last_seen_at=payload.observed_at` when given; reason goes to event `change_summary`), `archive_deployment(deployment_id) -> None` (idempotent; `status="deprecated"`, `archived_at=now`). Emits `deployment.created/updated/status_changed/archived`.
- Produces: `DeploymentEndpointService(db, tenant_id, request_id=None, actor=None)` with `create_endpoint(deployment_id, payload)`, `get_endpoint(endpoint_id)`, `list_endpoints(deployment_id, include_archived=False)`, `update_endpoint(endpoint_id, payload)`, `archive_endpoint(endpoint_id)`. Enforces: deployment exists & not archived (`DEPLOYMENT_ALREADY_ARCHIVED` 409 on create), primary rule (app check → `DuplicatePrimaryEndpointError`), idempotent archive. Emits `deployment_endpoint.created/updated/archived`.
- Shared private `_get_or_404`, `_dispatch` mirroring `ModelVersionService`.
- [ ] **Step 1: Write failing tests** — `tests/integration/test_deployment_service.py`:
  - `test_create_sets_planned_and_initial_status_source` (status `planned`, `status_source == payload.source`, `version == 1`).
  - `test_create_rejects_non_deployable_versions` — version flipped to `RETIRED` then `ARCHIVED` → 409 `MODEL_VERSION_NOT_DEPLOYABLE`; unknown id → 404; version from another tenant → 404.
  - `test_create_rejects_duplicate_identity` → 409 `DEPLOYMENT_DUPLICATE`.
  - `test_transition_happy_path_and_guards` — `planned→deploying→active` ok; `active→stopped` → 409; same-state → 409; after `archive`, transition → 409 `DEPLOYMENT_ALREADY_ARCHIVED`.
  - `test_patch_metadata_fields_applied` and `test_patch_immutable_model_version_rejected` (schema-level 422 via `DeploymentUpdate`).
  - `test_archive_is_idempotent_and_sets_state`.
  - `test_endpoint_primary_rules` — second primary inference → 409; primary on `health` type → 409; archive primary then create new primary → succeeds.
  - `test_endpoint_on_archived_deployment_rejected`.
  - `test_events_dispatched` — register a handler, assert event types in order for the create → endpoint → transition → archive flow, then `reset_handlers()`.
- [ ] **Step 2: Run** → ImportError FAIL.
- [ ] **Step 3: Implement** both services + `services/__init__.py` re-exports; deployability guard reads `ModelVersion` directly via existing `get_model_version`-style query filtered by tenant (no version-lifecycle duplication — only the `DEPLOYABLE_VERSION_STATES` membership test).
- [ ] **Step 4: Run** file → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat: deployment and endpoint services with lifecycle, deployability, and primary-endpoint rules"`

### Task 6: FastAPI routes + wiring

**Files:**
- Create: `backend/app/modules/model_inventory/api/deployments.py`
- Modify: `backend/app/modules/model_inventory/api/__init__.py` (include new router)
- Test: `backend/tests/api/test_deployments_api.py`

**Interfaces:**
- Consumes: services from Task 5, schemas from Task 3, error handler already registered in `app/main.py` (`DomainError` → envelope).
- Produces: `router = APIRouter(prefix="/api/v1")` in `api/deployments.py` with exactly the eleven routes of spec §38; dependency helpers `_deployment_service(request, db)` / `_endpoint_service(request, db)` mirroring `routes.py` (`settings.default_tenant_id`, `request.state.request_id`); `_as_utc` reused for `last_seen_at`/`observed_at`. Tag: `AI Model Inventory — Deployments`. `api/__init__.py` becomes `app.include_router(...)`-ready: `router` stays the model router; deployment router included from `api/__init__.py` via a new exported `deployment_router`, wired in `app/main.py` (single added line `app.include_router(deployment_router)`).
- Routes: `POST /model-versions/{model_version_id}/deployments` 201; `GET /deployments`; `GET /deployments/{id}`; `PATCH /deployments/{id}`; `DELETE /deployments/{id}` 204; `POST /deployments/{id}/transition` 200; `POST /deployments/{id}/endpoints` 201; `GET /deployments/{id}/endpoints`; `GET /deployment-endpoints/{id}`; `PATCH /deployment-endpoints/{id}`; `DELETE /deployment-endpoints/{id}` 204.
- [ ] **Step 1: Write failing API tests** — `tests/api/test_deployments_api.py`:
  - `test_acceptance_scenario` (spec §126): create model + version via API → `POST .../deployments` (201, `status="planned"`) → add endpoint (201, `is_primary=true`) → transition to `active` (200) → `GET /deployments` shows name/environment/status/runtime → `active→stopped` → 409 `DEPLOYMENT_INVALID_TRANSITION`.
  - `test_list_filters_search_sort_pagination`: filters `environment`, `status`, `deployment_kind`, `target_type`, `runtime`, `model_version_id`, combination `production+active`; `search` hits `name`/`target_name`/`cluster_name`/`namespace`/`runtime`/`source_reference` and never returns archived; `sort_by=name asc` order; `page_size=2&page=2` math; `page_size=500` → 422; `sort_by=nope` → 400 `INVALID_SORT_FIELD`.
  - `test_get_patch_delete_workflow`: GET 200; PATCH metadata fields 200 + `version` bumps; PATCH `{"status": "active"}` → 422; PATCH `{"model_version_id": ...}` → 422; DELETE → 204, then list hides it, `include_archived=true` shows it, GET still 200, PATCH → 409, re-DELETE → 204.
  - `test_missing_and_cross_tenant_return_404_envelope`: unknown deployment/endpoint ids and wrong-parent lookups → 404 with correct code, `request_id` echo, no traceback; `POST /model-versions/{uuid4()}/deployments` → 404 `MODEL_VERSION_NOT_FOUND`.
  - `test_invalid_transition_returns_409` and `test_duplicate_primary_returns_409` (second endpoint with `is_primary=true` → `ENDPOINT_DUPLICATE_PRIMARY`).
  - `test_endpoint_crud_workflow`: list endpoints (1 item), PATCH endpoint (200), DELETE (204), archived endpoint hidden from list, PATCH archived → 409, endpoint GET → 200.
  - `test_hostile_endpoint_urls_rejected`: embedded creds / fragment / scheme mismatch → 409 `ENDPOINT_INVALID_URL` (domain) or 422 (schema) — assert code and that no request ever takes >1s (proves no network call).
  - `test_create_endpoint_on_archived_deployment_returns_409`.
  - `test_validation_error_envelope`: missing required field → 422 `VALIDATION_ERROR` with `loc` details.
- [ ] **Step 2: Run** → 404 FAIL (router not registered).
- [ ] **Step 3: Implement** `api/deployments.py` + wiring in `api/__init__.py` and `main.py`.
- [ ] **Step 4: Run** `uv run pytest tests/api/test_deployments_api.py -v` → PASS; then `uv run pytest -v` full suite.
- [ ] **Step 5: Commit** — `git commit -m "feat: model deployment API with lifecycle transitions and endpoint routes"`

### Task 7: Documentation, review, full verification

**Files:**
- Create: `docs/features/model-inventory/model-deployment.md`
- Modify: `README.md`

- [ ] **Step 1: Write feature documentation** covering the fifteen sections required by spec §120 (purpose, scope, architecture, domain model, deployment lifecycle, environment model, endpoint model, database schema, API specification, validation rules, security, testing, connector extension points, known limitations, examples) — include the known limitations: no connector/health-check (metadata only), `target_name`/`namespace` NULL uniqueness semantics, `model_version_id` immutability, single-active-primary inference rule, no Redis/RustFS.
- [ ] **Step 2: Update README** — deployment endpoints list, feature status line `Model Deployment ✅`.
- [ ] **Step 3: Architecture review (spec §122 Phase 10)** — grep for raw SQL in routers, business policy in repositories, secrets in columns, URL fetching (`requests|httpx|urlopen`) anywhere under `app/`; confirm no new dependencies in `pyproject.toml`; confirm no `status` mutation outside `transition_deployment`; confirm every repository call is tenant-scoped.
- [ ] **Step 4: Full verification** — from `backend/`: `uv run ruff check .` (clean), `uv run ruff format .`, `uv run pytest -v` (all green), `uv run alembic upgrade head` on `auditra` db (applies 0003 cleanly), `uv run uvicorn app.main:app` smoke: `GET /docs` shows the new tagged routes.
- [ ] **Step 5: Commit** — `git commit -m "docs: model deployment feature documentation and README status"`
- [ ] **Step 6: Final diff review + push** — `git diff main...HEAD` read end-to-end, then push the feature branch to `origin` (per user instruction: push once the feature is successfully implemented).
