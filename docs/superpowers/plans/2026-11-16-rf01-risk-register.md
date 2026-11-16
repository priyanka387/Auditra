# RF-01 Risk Register & Taxonomy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver Risk & Compliance subfeature RF-01 — a centralized, tenant-scoped Risk Register with a configurable risk taxonomy, linking risks to discovered AI assets, with full CRUD, search/filter/pagination, soft archival, and audit trail.

**Architecture:** New flat module `app/modules/risk/` (mirroring `asset_discovery`): `enums.py`, `errors.py`, `models.py`, `schemas.py`, `repository.py`, `service.py`, `routes.py`, `seed.py`. One Alembic migration `0015` creates `risk_categories` + `risks`. Services integrate with the existing `AuditService` (same-transaction events). Routes registered in `app/main.py` under `/api/v1/risks` and `/api/v1/risk-categories`.

**Tech Stack:** FastAPI + SQLAlchemy 2.0 + Alembic + PostgreSQL, Pydantic v2, pytest, ruff (line-length 100).

**Spec:** `docs/private/risk-and-compliance-management.md` §4 (Subfeature 1), §5–7 (data model, relationships, API), §9 (audit trail), §17 (RF-01 name). The RF-01 plan supplied in-session (sections 1–37) is the binding task-level plan; this file is that plan restructured into TDD tasks. Design decisions approved in-session; this plan argues from that spec.

## Rulings applied (decided in-session, binding for all tasks)

1. **Backend-only.** The repo has no frontend (no package.json, no node tooling; README: "AI governance backend"; all prior subfeatures shipped API + tests). Plan Steps 9–11 (Risk Register UI, Create Risk UI, Risk Detail UI) are deferred until a frontend exists. Confirmed by user.
2. **`organization_id` → `tenant_id`.** Auditra's tenancy column is `tenant_id` (UUID); services take `tenant_id` in the constructor; routes use `settings.default_tenant_id`. All scoping uses that existing mechanism.
3. **Owner/team are free-text strings, not FKs.** No User/Team/Organization model exists anywhere in the codebase, and the plan forbids creating duplicate identity structures. `Risk.owner` and `Risk.team` are optional trimmed strings (max 255), filterable and searchable. Cross-tenant user/team validation is therefore N/A; cross-tenant *category* and *asset* validation still applies (FKs exist for those). When an identity model lands, these columns migrate to FKs.
4. **`created_by`/`updated_by` are nullable strings**, populated from the service's `actor` parameter (currently `None` at the API layer, exactly like `AuditEvent.actor_id`). Not required at the API surface — there is no auth system to supply them.
5. **Archive = `archived_at` soft-delete; status stays in `{open, in_review, accepted}`.** Plan §6 lists "Archived" as a lifecycle state, but §20 mandates `archived_at` archival and §16's separate `archived` query param only makes sense if archival is not a status value. `DELETE /api/v1/risks/{id}` archives idempotently (existing `DiscoverySourceService.delete` convention); no `POST /archive` route, no physical deletes.
6. **Audit events:** `risk.created`, `risk.updated`, `risk.archived` added to `AuditEventType`. Field-level events (owner_changed, status_changed, …) are folded into `risk.updated` with `changed_fields`, matching the `discovery_source.updated` convention.
7. **AI asset = `discovered_assets` row** (Asset Discovery is the plan's stated source). FK `asset_id → discovered_assets.id ON DELETE RESTRICT`, tenant-checked at service level; unknown and cross-tenant asset ids both return 404 (no existence leak, spec §29).
8. **Risk statuses:** `open`, `in_review`, `accepted`. Sources: `manual`, `asset_discovery`, `assessment`, `incident`, `audit`, `policy_review`, `vendor_review`, `other` (full backend enumeration per plan §7).

## Global Constraints

- Every risk/category query is tenant-scoped: `tenant_id == settings.default_tenant_id` at the API layer, explicit `tenant_id` parameter in repository/service (spec §14; mirrors asset discovery).
- No physical deletes anywhere in the risk module; archival only via `archived_at`.
- Out of scope (never build): assessment questionnaires, likelihood/impact scoring, heatmaps, inherent/residual scores, treatments, mitigations, controls, frameworks, requirements, evidence, findings, remediation, compliance scoring (plan §4).
- No new infrastructure: no microservices, brokers, Redis, workers, AI/LLM dependencies (plan §35).
- Migration `0015` must be reversible (upgrade + downgrade) and carry FKs, check constraints, and the indexes listed in Task 2.
- The entire existing test suite must stay green after every task.
- `uv run ruff check .` clean; format only files this plan touches.
- Commit on branch `feature/rf01-risk-register`; push to `origin` only after Task 7 passes; open a PR against `main` (user-confirmed).

## Review Focus

1. **Tenant isolation** — a risk, category, asset from tenant B must be invisible/unusable from tenant A: 404 on get/update/delete, rejected on create/update reference. Pinned by service-level cross-tenant tests (Task 5) and API tests (Task 6).
2. **Reference validation** — create/update must reject unknown or cross-tenant `category_id`/`asset_id` with 404 (no existence leak), unknown status/source with 422. Pinned by `test_create_risk_with_unknown_category_returns_404`, `test_create_risk_with_cross_tenant_asset_returns_404` (Task 5).
3. **Archive semantics** — archived risks leave the default list, remain retrievable by id, remain queryable with `archived=true`, keep their relationships; DELETE is idempotent. Pinned by `test_delete_risk_archives_and_preserves_relationships` (Task 5) and `test_delete_risk_is_idempotent` (Task 6).
4. **Seed idempotency** — `seed_risk_categories` twice yields exactly the 21 system categories, no duplicates; unique `(tenant_id, slug)` constraint backs it. Pinned by `test_seed_risk_categories_is_idempotent` (Task 3).
5. **Audit trail** — create/update/archive each write exactly one audit event in the same transaction with correct `event_type`, `resource_type="risk"`, and `changed_fields`. Pinned by `test_risk_mutations_record_audit_events` (Task 5).
6. **Search/filter/pagination correctness** — search covers title, description, owner, asset name; filters compose; `sort_by` whitelist rejects unknown fields with 400 (never 500). Pinned by Task 5/6 tests.

---

### Task 1: Architecture inspection, plan + spec commit, green baseline

**Files:**
- Create: `docs/superpowers/plans/2026-11-16-rf01-risk-register.md` (this file)
- Commit: `docs/private/risk-and-compliance-management.md` (the spec, currently untracked)
- No production code changes.

**Interfaces:**
- Consumes: repo conventions (documented in this plan's Rulings).
- Produces: committed plan + spec; verified baseline; ledger initialized.

- [ ] **Step 1: Verify workspace state**

Run `git status`, `git branch --show-current`. Expected: branch `feature/rf01-risk-register`, only the copied spec file untracked.

- [ ] **Step 2: Install dependencies**

Run `uv sync` from `backend/` (worktree has no `.venv`). Expected: exits 0.

- [ ] **Step 3: Run the baseline suite**

Run `uv run pytest -q` from `backend/`. Expected: all existing tests pass (PostgreSQL reachable on localhost:5433). If failures pre-exist, stop and report.

- [ ] **Step 4: Commit plan + spec**

Commit this plan file and `docs/private/risk-and-compliance-management.md` as `docs(risk): RF-01 risk register plan and feature spec`. Expected: clean `git status` except future work.

- [ ] **Step 5: Initialize ledger**

Run `sdd-workspace` for this plan; create `progress.md` with first line `# SDD ledger — plan: docs/superpowers/plans/2026-11-16-rf01-risk-register.md`; write pre-flight rows (shared interfaces: Task 2 models → Task 4 schemas → Task 5 service → Task 6 routes; no conflicts found — field names consistent throughout).

---

### Task 2: Risk domain foundation (enums, errors, models, migration 0015, conftest)

**Files:**
- Create: `backend/app/modules/risk/__init__.py`
- Create: `backend/app/modules/risk/enums.py`
- Create: `backend/app/modules/risk/errors.py`
- Create: `backend/app/modules/risk/models.py`
- Create: `backend/alembic/versions/0015_risk_register.py`
- Modify: `backend/tests/conftest.py` (TRUNCATE list: add `risks`, `risk_categories`)
- Modify: `backend/tests/integration/test_migration.py`
- Test: `backend/tests/unit/test_risk_models.py` (new)

**Interfaces:**
- Consumes: `app.core.db.Base`; `discovered_assets.id` (migration 0010 head is `0014`); `DomainError` from `app.modules.model_inventory.domain.errors`.
- Produces (relied on by later tasks):
  - `RiskStatus(StrEnum)`: `OPEN="open"`, `IN_REVIEW="in_review"`, `ACCEPTED="accepted"`.
  - `RiskSource(StrEnum)`: `MANUAL="manual"`, `ASSET_DISCOVERY="asset_discovery"`, `ASSESSMENT="assessment"`, `INCIDENT="incident"`, `AUDIT="audit"`, `POLICY_REVIEW="policy_review"`, `VENDOR_REVIEW="vendor_review"`, `OTHER="other"`.
  - Errors (all subclass `DomainError`): `RiskError` (RISK_ERROR, 400), `RiskNotFoundError` (404), `InvalidRiskQueryError` (400), `RiskCategoryError` (400), `RiskCategoryNotFoundError` (404), `DuplicateRiskCategoryError` (409), `InvalidRiskCategoryQueryError` (400), `RiskAssetNotFoundError` (404).
  - `models.RiskCategory` — table `risk_categories`: `id`, `tenant_id`, `name` (String 100), `description` (Text, nullable), `slug` (String 100), `is_system` (bool, server_default false), `is_active` (bool, server_default true), `created_at`, `updated_at`. Constraints: `uq_risk_categories_tenant_slug` UNIQUE(tenant_id, slug); index `ix_risk_categories_tenant_active` (tenant_id, is_active).
  - `models.Risk` — table `risks`: `id`, `tenant_id`, `title` (String 255), `description` (Text, nullable), `category_id` (FK risk_categories.id, RESTRICT, nullable=False), `source` (String 32), `asset_id` (FK discovered_assets.id, RESTRICT, nullable), `owner` (String 255, nullable), `team` (String 255, nullable), `status` (String 32, server_default 'open'), `created_by` (String 255, nullable), `updated_by` (String 255, nullable), `archived_at` (DateTime tz, nullable), `created_at`, `updated_at` (onupdate=now).
    - CheckConstraints: `ck_risks_status` status IN ('open','in_review','accepted'); `ck_risks_source` source IN (8 values above).
    - Indexes: `ix_risks_tenant_status` (tenant_id, status); `ix_risks_tenant_category` (tenant_id, category_id); `ix_risks_tenant_asset` (tenant_id, asset_id); `ix_risks_tenant_owner` (tenant_id, owner); `ix_risks_tenant_archived` (tenant_id, archived_at); `ix_risks_tenant_updated` (tenant_id, updated_at DESC).
  - Migration revision `"0015"`, `down_revision="0014"`, both tables + constraints/indexes, reversible downgrade.
  - conftest TRUNCATE list includes `risks, risk_categories`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/unit/test_risk_models.py`:
- `test_risk_status_values` — `RiskStatus` members equal exactly `{open, in_review, accepted}` (string values).
- `test_risk_source_values` — `RiskSource` equals the 8 spec values.
- `test_risk_table_shape` — `Risk.__tablename__ == "risks"`; required columns present (`title`, `category_id`, `source`, `status`, `tenant_id`, `archived_at`, `owner`, `team`); `RiskCategory.__tablename__ == "risk_categories"`.
- `test_risk_check_constraints` — inspect `Risk.__table__.constraints` contains the status and source check constraints with expected SQL fragments.

Extend `backend/tests/integration/test_migration.py` with `test_risk_tables_exist` — reflects `risk_categories` and `risks` via `sa.inspect`, asserting columns `slug`/`is_system` and `category_id`/`archived_at` exist plus FK to `discovered_assets`.

- [ ] **Step 2: Run and watch them fail**

Run `uv run pytest tests/unit/test_risk_models.py tests/integration/test_migration.py -q`. Expected: FAIL/ERROR — `ModuleNotFoundError: app.modules.risk` (feature missing, not typo).

- [ ] **Step 3: Implement**

Create `enums.py`, `errors.py`, `models.py`, `__init__.py` per Interfaces; write migration `0015_risk_register.py` (explicit `op.create_table` style matching `0014`); add `risks, risk_categories` to conftest TRUNCATE.

- [ ] **Step 4: Run and watch them pass**

Run `uv run pytest tests/unit/test_risk_models.py tests/integration/test_migration.py -q`. Expected: PASS. Then run the full suite `uv run pytest -q` — expected all green (conftest rebuilds the test DB from migrations each session).

- [ ] **Step 5: Lint + commit**

`uv run ruff check .` clean; `uv run ruff format` on touched files. Commit: `feat(risk): risk domain models, enums, errors and migration 0015 (RF-01)`.

---

### Task 3: Idempotent system risk-category seed

**Files:**
- Create: `backend/app/modules/risk/seed.py`
- Modify: `backend/app/seed.py` (call `seed_risk_categories` for the default tenant in `__main__`)
- Test: `backend/tests/integration/test_risk_seed.py` (new)

**Interfaces:**
- Consumes: `models.RiskCategory`, `settings.default_tenant_id`.
- Produces: `RISK_CATEGORIES` — exactly 21 `(name, slug, description)` tuples: Safety `safety`, Security `security`, Privacy `privacy`, Fairness / Bias `fairness-bias`, Reliability `reliability`, Accuracy `accuracy`, Robustness `robustness`, Explainability `explainability`, Transparency `transparency`, Accountability `accountability`, Data Quality `data-quality`, Data Governance `data-governance`, Legal / Regulatory `legal-regulatory`, Third-Party / Vendor `third-party-vendor`, Operational `operational`, Reputational `reputational`, Financial `financial`, Human Oversight `human-oversight`, Misuse / Abuse `misuse-abuse`, Model Risk `model-risk`, Other `other` (plan §27 list + `Other` from plan §10 taxonomy).
- Produces: `seed_risk_categories(db: Session, tenant_id: UUID) -> int` — inserts missing system categories (`is_system=True`, `is_active=True`), skips existing by `(tenant_id, slug)`, flushes, returns number newly inserted. Idempotent by construction and by unique constraint.

- [ ] **Step 1: Write the failing test**

`tests/integration/test_risk_seed.py`:
- `test_seed_risk_categories_creates_full_taxonomy` — call once: returns 21, DB has 21 rows for tenant, all `is_system`, slugs unique, names match spec list.
- `test_seed_risk_categories_is_idempotent` — call twice: second returns 0, DB still has 21 rows.
- `test_seed_risk_categories_scopes_by_tenant` — seed for a second tenant id: that tenant gets its own 21 rows; default tenant unaffected (still 21).

- [ ] **Step 2: Run and watch them fail**

`uv run pytest tests/integration/test_risk_seed.py -q`. Expected: ERROR — `ModuleNotFoundError: app.modules.risk.seed`.

- [ ] **Step 3: Implement**

`app/modules/risk/seed.py` with `RISK_CATEGORIES` + `seed_risk_categories`; wire into `app/seed.py` `__main__` block (after `seed_reference_data`, using `settings.default_tenant_id`) — not into `seed_reference_data` itself (that seeds global, tenant-less reference data).

- [ ] **Step 4: Run and watch them pass**

`uv run pytest tests/integration/test_risk_seed.py -q` → PASS. Full suite `uv run pytest -q` → green.

- [ ] **Step 5: Lint + commit**

ruff clean. Commit: `feat(risk): idempotent system risk taxonomy seed (RF-01)`.

---

### Task 4: Pydantic schemas (risk + category) with validation

**Files:**
- Create: `backend/app/modules/risk/schemas.py`
- Test: `backend/tests/unit/test_risk_schemas.py` (new)

**Interfaces:**
- Consumes: `enums.RiskStatus`, `enums.RiskSource`, models for `from_attributes` responses.
- Produces (relied on by Tasks 5–6):
  - `RiskCreate` (extra="forbid"): `title` str (before-validator strip, min_length 1, max_length 255), `description` str | None (max_length 10000), `category_id` UUID (required), `source` RiskSource = MANUAL, `asset_id` UUID | None = None, `owner` str | None (strip, max 255), `team` str | None (strip, max 255), `status` RiskStatus = OPEN.
  - `RiskUpdate` (extra="forbid"): all of the above optional (`title` etc. default None); `category_id` UUID | None; no field may be an empty string after strip (validator rejects blank title/owner/team when provided).
  - `RiskResponse`: `id`, `tenant_id`, `title`, `description`, `category_id`, `category: RiskCategoryRef`, `source`, `asset_id`, `asset: RiskAssetRef | None`, `owner`, `team`, `status`, `created_by`, `updated_by`, `archived_at`, `created_at`, `updated_at`; timezone-aware timestamp validator (mirrors `DiscoverySourceResponse`).
  - `RiskCategoryRef`: `id`, `name`, `slug`. `RiskAssetRef`: `id`, `name`.
  - `RiskListResponse`: `items`, `page`, `page_size`, `total`, `total_pages`.
  - `RiskFilter` (extra="forbid"): `page` (ge 1, default 1), `page_size` (ge 1, le 100, default 25), `search` (max 255, optional), `category_id`, `status`, `source`, `owner`, `team`, `asset_id` (all optional), `archived: bool = False` (True → archived only; False → active only), `sort_by` default `updated_at` (whitelist: `updated_at`, `created_at`, `title`, `status`), `sort_order` default `desc`.
  - `RiskCategoryCreate` (extra="forbid"): `name` (strip, 1..100, required), `description` optional (max 1000), `slug` optional (pattern `^[a-z0-9]+(-[a-z0-9]+)*$`, max 100; auto-slugified from name when absent).
  - `RiskCategoryUpdate` (extra="forbid"): `name`, `description`, `is_active` all optional.
  - `RiskCategoryResponse`: `id`, `tenant_id`, `name`, `description`, `slug`, `is_system`, `is_active`, `created_at`, `updated_at` (+ tz validator).
  - `RiskCategoryListResponse`: `items`, `page`, `page_size`, `total`, `total_pages`.
  - `RiskCategoryFilter` (extra="forbid"): `page`, `page_size`, `search`, `is_active` optional, `is_system` optional, `sort_by` (whitelist `name`, `created_at`, `updated_at`), `sort_order`.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_risk_schemas.py`:
- `test_risk_create_strips_title_and_owner`
- `test_risk_create_rejects_blank_title` (whitespace-only → ValidationError)
- `test_risk_create_rejects_oversized_title` (256 chars → ValidationError)
- `test_risk_create_requires_category_id` (missing → ValidationError)
- `test_risk_create_rejects_unknown_status_and_source` (bogus enum values → ValidationError)
- `test_risk_create_rejects_extra_fields` (`likelihood: 3` → ValidationError — out-of-scope field must not sneak in)
- `test_risk_update_accepts_partial_payload` (only `status` → valid, others None)
- `test_risk_update_rejects_blank_title`
- `test_risk_filter_defaults` (defaults: page 1, page_size 25, archived False, sort_by updated_at)
- `test_risk_category_create_autoslug` (name "Fairness / Bias" → slug `fairness-bias`)
- `test_risk_category_create_rejects_bad_slug` (slug "Bad Slug!" → ValidationError)
- `test_risk_category_create_requires_name`

- [ ] **Step 2: Run and watch them fail**

`uv run pytest tests/unit/test_risk_schemas.py -q`. Expected: ERROR — `ModuleNotFoundError: app.modules.risk.schemas`.

- [ ] **Step 3: Implement**

`schemas.py` per Interfaces (slugify helper: lowercase, non-alphanumeric → hyphens, collapse/trim hyphens).

- [ ] **Step 4: Run and watch them pass**

`uv run pytest tests/unit/test_risk_schemas.py -q` → PASS. Full suite → green.

- [ ] **Step 5: Lint + commit**

ruff clean. Commit: `feat(risk): risk and risk-category schemas with validation (RF-01)`.

---

### Task 5: Repository + service layer (business rules, tenant isolation, audit)

**Files:**
- Create: `backend/app/modules/risk/repository.py`
- Create: `backend/app/modules/risk/service.py`
- Modify: `backend/app/modules/audit/enums.py` (add RISK_CREATED/RISK_UPDATED/RISK_ARCHIVED = risk.created/risk.updated/risk.archived)
- Test: `backend/tests/integration/test_risk_service.py` (new)
- Test: `backend/tests/integration/test_risk_category_service.py` (new)

**Interfaces:**
- Consumes: models, enums, errors, schemas (Tasks 2–4), `AuditService.record_event`, `DiscoveredAsset` model for asset existence checks, asset_discovery repository patterns.
- Produces (relied on by Task 6):
  - `RiskCategoryService(db, tenant_id, request_id=None, actor=None, source="api")`:
    - `create(payload: RiskCategoryCreate) -> RiskCategory` — slug uniqueness per tenant (existing row → `DuplicateRiskCategoryError` 409); system rows get `is_system=False` (system only from seed).
    - `update(category_id, payload: RiskCategoryUpdate) -> RiskCategory` — 404 unknown/cross-tenant; records `risk_category.updated` audit with changed_fields; slug immutable.
    - `get(category_id) -> RiskCategory` — tenant-scoped 404.
    - `list(filters: RiskCategoryFilter) -> tuple[list, int]` — tenant-scoped, search on name/slug, sort whitelist (`InvalidRiskCategoryQueryError` 400 on unknown sort field).
  - `RiskService(db, tenant_id, request_id=None, actor=None, source="api")`:
    - `create(payload: RiskCreate) -> Risk` — validates category exists in tenant (else `RiskCategoryNotFoundError` 404), asset exists in tenant if given (else `RiskAssetNotFoundError` 404); persists trimmed values; audit `risk.created` (after_state); single commit.
    - `get(risk_id) -> Risk` — tenant-scoped 404 (cross-tenant indistinguishable from unknown).
    - `list(filters: RiskFilter) -> tuple[list[Risk], int]` — filters compose: tenant, archived (False → `archived_at IS NULL`, True → `IS NOT NULL`), category_id, status, source, owner (exact), team (exact), asset_id; `search` → OR ilike on title, description, owner + joined asset name (`discovered_assets.name`); sort whitelist (`InvalidRiskQueryError` 400); offset/limit pagination; total count.
    - `update(risk_id, payload: RiskUpdate) -> Risk` — re-validates provided category_id/asset_id (404s); applies only non-None fields; `updated_at` bumps (onupdate); audit `risk.updated` with `changed_fields` sorted; refuses updates to archived risks? **No** — plan does not forbid editing archived risks; but "not usable as if they were active" (§20). Ruling: updates to archived risks are rejected with 409 `RiskError`-derived... *(resolved below as `RISK_ARCHIVED` 409 `RiskArchivedError` added to errors — plan §20 "not be usable as if they were active risks")*.
    - `archive(risk_id) -> Risk` — idempotent soft archive: sets `archived_at=now` if unset, audit `risk.archived` only on first call, single commit.
    - `resolve_refs(risks: list[Risk]) -> dict[UUID, RiskCategoryRef]` + asset names helper (or equivalent) so routes can build responses without N+1 — fetch categories/assets for a page in at most 2 extra queries.
  - Error added in Task 2's errors file during this task if missing: `RiskArchivedError` (RISK_ARCHIVED, 409).

- [ ] **Step 1: Write the failing tests**

`tests/integration/test_risk_category_service.py`:
- `test_create_category_persists_and_generates_slug`
- `test_create_duplicate_slug_rejected` (409)
- `test_update_category_changes_name_and_is_active` (+ audit event `risk_category.updated`)
- `test_get_unknown_category_returns_404`
- `test_list_categories_search_and_pagination`
- `test_list_categories_invalid_sort_returns_400`

`tests/integration/test_risk_service.py` (fixture: seed categories + create a discovered asset + second tenant):
- `test_create_risk_persists_full_record` (audit `risk.created`, resource_type risk, after_state present)
- `test_create_risk_requires_existing_category` (unknown category → 404)
- `test_create_risk_with_cross_tenant_category_returns_404`
- `test_create_risk_with_cross_tenant_asset_returns_404`
- `test_create_risk_with_valid_asset_links_discovered_asset`
- `test_get_risk_from_other_tenant_returns_404`
- `test_update_risk_applies_fields_and_records_changed_fields` (status + owner change → audit changed_fields contains both)
- `test_update_risk_with_unknown_category_returns_404`
- `test_update_archived_risk_returns_409`
- `test_delete_risk_archives_and_preserves_relationships` (archived_at set; category/asset FKs intact; audit `risk.archived`)
- `test_delete_archived_risk_is_idempotent` (second call no new audit event)
- `test_list_risks_filters_by_status_category_owner_asset`
- `test_list_risks_search_covers_title_description_owner_and_asset_name`
- `test_list_risks_excludes_archived_by_default_and_includes_when_archived`
- `test_list_risks_pagination_and_total`
- `test_list_risks_invalid_sort_returns_400`

- [ ] **Step 2: Run and watch them fail**

`uv run pytest tests/integration/test_risk_service.py tests/integration/test_risk_category_service.py -q`. Expected: ERROR — `ModuleNotFoundError: app.modules.risk.repository` / service.

- [ ] **Step 3: Implement**

`repository.py` (tenant-scoped getters, `_conditions` filter builders, SORT_COLUMNS whitelists, count+offset/limit lists — mirror `asset_discovery/repository.py`), `service.py` (two services, audit integration, same-transaction `record_event` then single `db.commit()`), extend `audit/enums.py` with the three risk event types, add `RiskArchivedError` to `risk/errors.py`.

- [ ] **Step 4: Run and watch them pass**

`uv run pytest tests/integration/test_risk_service.py tests/integration/test_risk_category_service.py -q` → PASS. Full suite → green.

- [ ] **Step 5: Lint + commit**

ruff clean. Commit: `feat(risk): risk and category services with tenant isolation and audit (RF-01)`.

---

### Task 6: REST API layer (risks + risk-categories) with API tests

**Files:**
- Create: `backend/app/modules/risk/routes.py`
- Modify: `backend/app/main.py` (include both routers)
- Test: `backend/tests/api/test_risks_api.py` (new)
- Test: `backend/tests/api/test_risk_categories_api.py` (new)

**Interfaces:**
- Consumes: `RiskService`, `RiskCategoryService`, schemas, `get_db`, `settings.default_tenant_id`, `Request.state.request_id` (all as `asset_discovery/routes.py` does).
- Produces:
  - `GET /api/v1/risks` — query: page, page_size, search, category_id, status, source, owner, team, asset_id, archived, sort_by, sort_order → `RiskListResponse` (`items,page,page_size,total,total_pages`, `total_pages=ceil(total/page_size) if total else 0`).
  - `POST /api/v1/risks` — 201 `RiskResponse`.
  - `GET /api/v1/risks/{risk_id}` — 200 `RiskResponse` (includes resolved `category` and `asset` refs), 404 envelope.
  - `PATCH /api/v1/risks/{risk_id}` — 200 `RiskResponse`.
  - `DELETE /api/v1/risks/{risk_id}` — 204, soft archive, idempotent.
  - `GET /api/v1/risk-categories` — filters (page, page_size, search, is_active, is_system, sort) → `RiskCategoryListResponse`.
  - `POST /api/v1/risk-categories` — 201 `RiskCategoryResponse`.
  - `GET /api/v1/risk-categories/{category_id}` — 200.
  - `PATCH /api/v1/risk-categories/{category_id}` — 200.
  - Errors use the existing envelope (`DomainError` handler): 404 `RISK_NOT_FOUND`/`RISK_CATEGORY_NOT_FOUND`/`RISK_ASSET_NOT_FOUND`, 409 duplicates/archived, 400 query errors, 422 `VALIDATION_ERROR` for malformed bodies.
  - Query-enum parsing failures (bad status) surface as 422 VALIDATION_ERROR via FastAPI — acceptable (matches how `DiscoverySourceStatus` query params behave).

- [ ] **Step 1: Write the failing tests**

`tests/api/test_risks_api.py`:
- `test_create_risk_returns_201_and_persists` (category seeded first)
- `test_create_risk_validation_error_on_blank_title` (422 envelope, code VALIDATION_ERROR)
- `test_create_risk_with_unknown_category_returns_404`
- `test_create_risk_with_unknown_asset_returns_404`
- `test_get_risk_returns_resolved_category_and_asset`
- `test_get_unknown_risk_returns_404_envelope` (error.code == RISK_NOT_FOUND, request_id present)
- `test_patch_risk_updates_fields`
- `test_delete_risk_archives_then_get_still_returns_row`
- `test_delete_risk_is_idempotent` (two DELETEs → 204 both)
- `test_list_risks_pagination_metadata` (create 3, page_size 2 → page 1 has 2, total 3, total_pages 2)
- `test_list_risks_filters_by_status_and_category`
- `test_list_risks_search_by_title`
- `test_list_risks_archived_filter`
- `test_list_risks_invalid_sort_returns_400` (code RISK_QUERY_INVALID)
- `test_list_risks_unknown_filter_rejected` (extra query param → 400 via extra="forbid" filter build)

`tests/api/test_risk_categories_api.py`:
- `test_list_categories_includes_seeded_system_taxonomy` (21 items, is_system true)
- `test_create_custom_category_returns_201`
- `test_create_duplicate_category_returns_409`
- `test_patch_category_disables_it`
- `test_patch_unknown_category_returns_404`

- [ ] **Step 2: Run and watch them fail**

`uv run pytest tests/api/test_risks_api.py tests/api/test_risk_categories_api.py -q`. Expected: FAIL with 404 `NOT_FOUND` (route missing) — TestClient raises starlette 404s / assertion errors because endpoints don't exist.

- [ ] **Step 3: Implement**

`routes.py` with `router = APIRouter(prefix="/api/v1/risks")` and `categories_router = APIRouter(prefix="/api/v1/risk-categories")`; service factories `_service(request, db)` / `_category_service(request, db)` passing `settings.default_tenant_id`, `request_id`, `actor=None`; filter builders wrapping `ValidationError` → `InvalidRiskQueryError` (mirrors `_build` pattern); response builders resolving category/asset refs; register both routers in `create_app()`.

- [ ] **Step 4: Run and watch them pass**

`uv run pytest tests/api/test_risks_api.py tests/api/test_risk_categories_api.py -q` → PASS. Full suite → green.

- [ ] **Step 5: Lint + commit**

ruff clean. Commit: `feat(risk): risk register and category REST APIs (RF-01)`.

---

### Task 7: Integration verification (Asset Discovery → Risk Register), acceptance sweep, push

**Files:**
- Test: `backend/tests/e2e/test_risk_register_acceptance.py` (new)
- Modify: `README.md` (endpoint list + risk module mention)
- Modify: this plan (check off completed tasks)

**Interfaces:**
- Consumes: everything from Tasks 2–6.
- Produces: green end-to-end proof; documented endpoints; pushed branch + PR.

- [ ] **Step 1: Write the failing/verifying end-to-end test**

`tests/e2e/test_risk_register_acceptance.py` — `test_full_risk_register_journey`:
1. seed risk categories;
2. create a discovery source + run, execute a real observation through `DiscoveredAssetService.record_observation` (or insert a `DiscoveredAsset` directly if execution is heavier — prefer the real catalog path used by existing e2e tests) so an AI asset exists from Asset Discovery;
3. `RiskService.create` linking that asset, owner, team, status open;
4. list → visible; search by asset name → found;
5. update status → `in_review` (audit `risk.updated`);
6. archive via `RiskService.archive` → excluded from default list, still fetchable by id, `archived=true` filter finds it;
7. assert audit events for the journey exist with `resource_type="risk"` in order.

- [ ] **Step 2: Run it**

`uv run pytest tests/e2e/test_risk_register_acceptance.py -q` → PASS. If RED: debug with systematic-debugging before proceeding.

- [ ] **Step 3: Full acceptance sweep**

- `uv run pytest -q` → entire suite green (no Asset Discovery regression).
- `uv run ruff check .` → clean; `uv run ruff format --check .` on touched files.
- `uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head` → migration reversible (run against the dev DB if configured; otherwise assert via migration test coverage already added in Task 2).
- `uv run python -m app.seed` twice → idempotent (no duplicate categories).
- Manually verify OpenAPI: start app briefly or use TestClient to assert `/api/v1/risks` and `/api/v1/risk-categories` appear in `app.openapi()` paths.

- [ ] **Step 4: Update README**

Add risk endpoints to the README endpoint list (`/api/v1/risks`, `/api/v1/risks/{risk_id}`, `/api/v1/risk-categories`, `/api/v1/risk-categories/{category_id}`) and a one-line RF-01 mention.

- [ ] **Step 5: Commit, push, open PR**

Commit `feat(risk): risk register acceptance journey and docs (RF-01)`. Check off tasks in this plan file (`docs: check off RF-01 plan tasks`). Push `feature/rf01-risk-register` to `origin`. Open PR against `main` with a summary of RF-01 scope + rulings. Expected: PR created; all checks (local suite) green.

---

## Definition of Done (plan §36 acceptance mapping)

- CRUD: create/view/update/archive risks; archived retrievable (Tasks 5–6 tests).
- Taxonomy: 21 seeded system categories, DB-stored, duplicate-proof, assignable (Tasks 3, 5).
- Asset integration: discovered assets selectable, cross-tenant rejected (Task 5).
- Ownership: owner/team assignable strings, filterable (Task 5).
- Search/filter/pagination: title/description/owner/asset-name search; category/status/owner/asset/archived filters; pagination metadata (Tasks 5–6).
- Security: tenant isolation everywhere; 404 non-leaking (Task 5 cross-tenant tests).
- Auditability: created/updated/archived events in same transaction (Task 5).
- Quality: migration reversible; full suite green; ruff clean; no Asset Discovery regression (Task 7).
