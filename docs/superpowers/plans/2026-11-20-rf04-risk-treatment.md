# RF-04 Risk Treatment & Mitigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver Risk & Compliance subfeature RF-04 — risk treatment decisions (strategies MITIGATE/ACCEPT/AVOID/TRANSFER with rationale and owner), treatment lifecycle (NOT_STARTED → PLANNED → IN_PROGRESS → COMPLETED/CANCELLED), treatment actions with owners/priorities/due dates, derived progress and overdue metrics, expected residual risk computed through the RF-03 scoring engine, a treatment overview (status/strategy distribution, overdue actions, untreated high/critical risks), risk-register treatment integration, and a complete audit trail.

**Architecture:** New flat module `app/modules/risk_treatment/` (mirroring `risk/`, `assessment/`, `risk_scoring/`): `enums.py`, `errors.py`, `models.py`, `schemas.py`, `repository.py`, `service.py`, `metrics.py`, `routes.py`. One Alembic migration `0018` creates `risk_treatments`, `risk_treatment_actions`, `risk_treatment_residual_assessments`. Treatment/action state machines live in `enums.py`; derived metrics (progress, overdue) live in pure `metrics.py` — never stored. Residual scoring delegates to the RF-03 engine (`ScoreCalculator`/`SeverityClassifier` + the tenant's active framework thresholds) — no second scoring implementation. Services integrate with `AuditService` (same-transaction events). RF-01's risk register gains an outer join to the active treatment for treatment filters/sorts and a `treatment` field on `RiskResponse`. Routes registered in `app/main.py` under `/api/v1/risks/{risk_id}/treatments`, `/api/v1/treatments`, and `/api/v1/treatment-actions`.

**Tech Stack:** FastAPI + SQLAlchemy 2.0 + Alembic + PostgreSQL, Pydantic v2, pytest, ruff (line-length 100).

**Spec:** The in-session RF-04 plan (sections 1–71, supplied by the user) is the binding task-level plan; it argues from `docs/private/risk-and-compliance-management.md` — "Subfeature 4 — Risk Treatment & Mitigation" (line 428), §5 core data model (`RiskTreatment`), §6 (`Risk 1 ─── N RiskTreatment`), §7 API structure, §9 audit trail, §17 (RF-04 name). This file restructures that plan into TDD tasks.

## Rulings applied (decided in-session, binding for all tasks)

1. **Backend-only.** The repo has no frontend (no package.json, no node tooling; RF-01 Ruling 1, RF-02 Ruling 1, RF-03 Ruling 1). RF-04 plan §38–§45 (Risk Detail treatment panel, Treatment Detail page, treatment creation/action UIs, treatment dashboard UI, filters UI) are deferred until a frontend exists. Their data requirements are satisfied at the API level: `RiskResponse.treatment` summary (§38/§42), treatment filters on `GET /api/v1/risks` (§43), `GET /api/v1/treatments/overview` with status/strategy distribution, overdue actions and untreated high/critical counts (§44/§45), `is_overdue` derived on responses (§60). Cost if wrong: UI is built later against these endpoints.
2. **`owner` free-text, not `owner_id`.** Plan §9/§11 prescribes `owner_id` and says "use the existing Auditra user/team ownership model — do not create a second user-management system". Auditra has no identity model: `Risk.owner`/`Risk.team` are `String(255)` free text (RF-01 Ruling). Treatment and action owners are therefore `owner: str | None` / required `owner: str` (`String(255)`), and plan §50/§51's "owner belongs to organization" validation is vacuous until identity lands. Cost if wrong: FKs to users added later by migration; no data migration needed (free text keeps working).
3. **`tenant_id`, not `organization_id`.** Auditra's tenancy column is `tenant_id` (UUID); services take `tenant_id` in the constructor; routes use `settings.default_tenant_id` (RF-01/02/03 rulings).
4. **No roles/permissions exist → authorization = tenant isolation + validation.** Plan §25/§26/§50's "authorized role required for acceptance" is deferred with the auth system, exactly as RF-01, RF-02 and RF-03 shipped. What is enforced regardless: `ACCEPT` requires a non-blank rationale (service → 400), a mandatory `risk.treatment_acceptance_recorded` audit event on every acceptance, tenant-scoped everything, and archived risks reject treatment (409 `RISK_ARCHIVED`). Cost if wrong: role gating when auth lands; the same exposure RF-01's `ACCEPTED` status already carries.
5. **One active treatment per risk, enforced by a partial unique index.** Plan §48: `not_started`/`planned`/`in_progress` are the active states. Migration `0018` adds `uq_risk_treatments_risk_active UNIQUE (risk_id) WHERE status IN ('not_started','planned','in_progress')`, so a second active treatment → 409 `RISK_TREATMENT_ACTIVE_EXISTS` (race-safe) and the register's outer join can never fan out rows. Completed/cancelled treatments are retained as history (§49). Cost if wrong: parallel treatment strategies need the index widened.
6. **Status transitions.** Create → `not_started`. Valid (plan §55 + one gap fill): `not_started→planned`, `planned→in_progress`, `in_progress→completed` (via `POST .../complete`), `planned→cancelled` and `in_progress→cancelled` (via `POST .../cancel`), and **`not_started→cancelled` added** — the spec's list omits it, but without it a never-started treatment can never be cancelled (a lifecycle hole). Terminal states are immutable: `completed→in_progress` etc. → 409 `RISK_TREATMENT_STATUS_INVALID`. `PATCH` may move status only through `not_started→planned` and `planned→in_progress`; `completed`/`cancelled` are reachable only through the dedicated endpoints. Actions: `todo`/`in_progress` interconvert freely via `PATCH`; `complete` and `cancel` endpoints accept only non-terminal states; terminal action states are immutable → 409 `RISK_TREATMENT_ACTION_STATUS_INVALID`.
7. **Rationale for ACCEPT is a service-level rule (400), not a schema rule.** The requirement depends on `strategy` (plan §40/§50): `RiskTreatmentService.create/update` raises `RiskTreatmentError` (400, message "ACCEPT strategy requires a rationale") unless the effective rationale (payload or stored) is non-blank — uniform for create and PATCH-to-ACCEPT. Other strategies may carry an optional rationale (§10 recommends it; §9 field is nullable).
8. **Cancellation requires a reason and it is kept on the row.** Plan §35: `POST .../cancel` body `{"reason": ...}` non-blank → 422 when blank (schema). The reason is stored in `cancellation_reason` (plus `cancelled_at`) so the treatment/action detail can show it, and is echoed into the audit event's metadata (§35's "store the reason in the audit history"). No cascade: cancelling a treatment does not auto-cancel its actions (plan is silent; actions are cancelled individually and stay auditable).
9. **Overdue and progress are derived, never stored** (plan §17/§18/§19). `metrics.py` pure functions: treatment overdue = active status AND `target_completion_date < today`; action overdue = `status ∈ {todo, in_progress}` AND `due_date < today`. Plan §17 says `status != COMPLETED`, but a cancelled action past its due date is not something a governance user should see as overdue — cancelled is excluded (spec-literal behavior would be a one-line change). Progress = `completed_actions / total_actions × 100` integer percent (`0` when no actions); `remaining = total − completed − cancelled`. Responses expose `is_overdue` and `action_stats` computed at response time; no `is_overdue`/`progress` columns exist.
10. **Residual risk reuses the RF-03 engine — no second scoring implementation** (plan Step 6 architectural rule). `create_residual` fetches the tenant's active framework (`risk_scoring.repository.find_default_framework`, else 404 `SCORING_FRAMEWORK_NOT_FOUND`), validates likelihood/impact against its active levels (400 `RISK_TREATMENT_RESIDUAL_INVALID`), computes score via `ScoreCalculator` and severity via `SeverityClassifier`, and snapshots `{likelihood, impact, calculated_score, severity}` onto the assessment. Only `assessment_type=expected` is accepted (400 `RISK_TREATMENT_RESIDUAL_INVALID` for anything else); the DB check allows `expected|verified` so RF-07-era verification needs no redesign (plan §24) and the API boundary keeps "expected ≠ verified" honest (plan §23). `tenant_id` is added to plan §24's field list (tenancy requires it).
11. **Audit events (plan §37 + repo convention):** `risk.treatment_created`, `risk.treatment_updated`, `risk.treatment_strategy_changed`, `risk.treatment_owner_changed`, `risk.treatment_completed`, `risk.treatment_cancelled`, `risk.treatment_action_created`, `risk.treatment_action_updated`, `risk.treatment_action_completed`, `risk.treatment_action_cancelled`, `risk.treatment_residual_assessment_created`, `risk.treatment_acceptance_recorded` — all added to `AuditEventType` (all match `EVENT_TYPE_PATTERN`). Treatments use `resource_type="treatment"`, actions `resource_type="treatment_action"` (ids in `resource_id`, `risk_id`/`treatment_id` in `metadata`/states). A `PATCH` that changes strategy or owner emits `risk.treatment_updated` (with `changed_fields`) **plus** the dedicated `..._strategy_changed` / `..._owner_changed` event with before/after states (§37 wants both granularities). Creating or switching to `ACCEPT` emits `risk.treatment_acceptance_recorded` in addition to created/updated.
12. **API shape (plan §29, following RF-03 conventions):** three routers — nested `treatments_router` (`/api/v1/risks`), `treatments` (`/api/v1/treatments`, `overview` declared before `/{treatment_id}`), `actions` (`/api/v1/treatment-actions`). 15 operations: nested list/create treatment; treatment get/patch/complete/cancel; treatment actions list/create; treatment residual list/create; action get/patch/complete/cancel; overview. OpenAPI operation count 99 → 114 (verify at runtime; adjust to the real count and record the ruling if it drifted). Lists are paginated (`items/page/page_size/total/total_pages`) exactly like RF-01/RF-03 sub-resource lists.
13. **Register integration (plan §42/§43):** `RiskResponse.treatment: TreatmentSummary | None` — scalar-only (`strategy`, `status`, `owner`, `target_completion_date`, `is_overdue`) built from the active-treatment outer join (Ruling 5 makes it ≤1 row, no fan-out); `null` means untreated. `RiskFilter` gains `treatment_strategy`, `treatment_status`, `treatment_owner`, `treatment_overdue: bool | None`, `treated: bool | None` — all evaluated against the **active** treatment join, so `treatment_status=completed` legitimately returns nothing (history is queried via `GET /api/v1/treatments?status=completed`), and `treated=false` is plan §43's "No Treatment" view (needed server-side by §45 anyway). Sorts unchanged (RF-01/RF-03 ordering preserved).
14. **Treatment list responses are scalar-only; `action_stats`/`is_overdue` appear on detail responses** — computing per-row progress for a page would be N+1 (progress needs each treatment's actions). `TreatmentResponse.action_stats: TreatmentProgress | None = None` (set on detail), `is_overdue` computed for both list rows and detail (single query, no per-row cost).
15. **Action `owner` is required; treatment `owner` is optional** (plan §41's action form marks `Owner *`; §9's treatment owner column is nullable like `Risk.owner`). Action `priority` defaults to `medium` (§41 form has no asterisk; §31 example supplies it).
16. **Completion does not gate on action state** (plan §19: "a user should confirm treatment completion"; no rule blocks completion while actions remain). The `complete` endpoint records completion regardless and snapshots the action counts into the audit metadata; progress reaching 100% never auto-completes the treatment (§19), and completing a treatment never closes/archives the risk (§20 — RF-01 statuses untouched).
17. **Treatment dates are `Date` columns; date ordering is schema-validated (422).** `planned_start_date <= target_completion_date` (plan §16) is checked in `TreatmentCreate`/`TreatmentUpdate`/`ActionCreate`/`ActionUpdate` model validators when both dates are present (consistent with how blank-ness is handled at schema level elsewhere); `completed_at`/`cancelled_at` are timezone-aware `DateTime` set only by the dedicated endpoints.

## Global Constraints

- Every treatment query is tenant-scoped: `tenant_id == settings.default_tenant_id` at the API layer, explicit `tenant_id` parameter in repository/service (plan §51). Unknown and cross-tenant treatment/action/risk ids are indistinguishable 404s.
- No physical deletes; treatments/actions are cancelled, never removed (plan §49 history); residual assessments are append-only.
- Out of scope (never build): compliance frameworks, controls, evidence, findings, remediation (RF-06/07/08), verified residual risk, approval engines, roles/permissions, frontend, LLM features, Redis/queues/workflow engines (plan §67/§69). Schemas are `extra="forbid"` so out-of-scope fields cannot sneak in.
- Risk statuses (RF-01) and scores (RF-03) are never redefined or mutated by RF-04 (plan §21); RF-03's engine and thresholds are the only scoring code path (plan Step 6).
- Migration `0018` must be reversible (upgrade + downgrade) and carry FKs, check constraints, the partial unique active-treatment index, and the indexes listed in Task 2.
- The entire existing test suite must stay green after every task; OpenAPI contract count bumped exactly once (Task 7).
- `uv run ruff check .` clean; format only files this plan touches.
- Commit on branch `feature/rf04-risk-treatment`; push to `origin` only after Task 9 passes; open a PR (RF-01/RF-02/RF-03 precedent; user instructed "push the code to github once the implementation is done").

## Review Focus

1. **State machine (plan §55)** — valid transitions succeed; `completed→in_progress`, `cancelled→planned`, terminal action re-entry all rejected with 409; `PATCH` cannot reach `completed`/`cancelled` without the dedicated endpoints. Pinned by Tasks 4–5.
2. **One active treatment per risk** — partial unique index rejects a second active treatment (409), completed history retained, register join produces no duplicated risk rows. Pinned by Tasks 2, 4, 8.
3. **ACCEPT guards (plan §25/§26/§50)** — create/PATCH-to-ACCEPT without rationale → 400; `risk.treatment_acceptance_recorded` emitted exactly once per acceptance; tenant-scoped. Pinned by Tasks 4, 7.
4. **Tenant isolation (plan §51)** — tenant B's treatment/action/risk invisible and unusable from tenant A (404 on get/patch/complete/cancel/nested create). Pinned by Tasks 4–7 and Task 9.
5. **Derived metrics (plan §17/§18/§56/§57)** — progress 33/66/100 from action states, 0 with no actions; past-due `todo` action overdue, past-due `completed` action not overdue, past-due `cancelled` action not overdue; no stored `is_overdue`/`progress` columns. Pinned by Tasks 2, 5.
6. **Residual uses RF-03 engine (plan Step 6/§54/§64)** — score = likelihood×impact of the tenant's active framework, severity from that framework's thresholds (config, not literals); `verified` rejected; unscored framework missing → 404. Pinned by Task 6.
7. **Register regression** — RF-01 list/detail/create/update/archive and RF-03 scoring filters/sorts unchanged after the treatment join; unknown query params still 400; `current_score` still null-when-unscored. Pinned by Task 8 (existing RF-01/RF-03 tests must stay green).
8. **Audit completeness (plan §37)** — all 12 events emitted in the right situations, same transaction, `EVENT_TYPE_PATTERN`-valid; completion never touches the risk row. Pinned by Tasks 4–6, 9.

---

### Task 1: Architecture inspection, plan + spec commit, green baseline

**Files:**
- Create: `docs/superpowers/plans/2026-11-20-rf04-risk-treatment.md` (this file)
- No production code changes.

**Interfaces:**
- Consumes: repo conventions (documented in this plan's Rulings).
- Produces: committed plan; verified baseline; initialized ledger.

- [x] **Step 1: Verify workspace state**

Run `git status`, `git branch --show-current`. Expected: branch `feature/rf04-risk-treatment` created from `feature/rf03-risk-scoring` HEAD, only this plan file untracked.

- [x] **Step 2: Install dependencies**

Run `uv sync` (with `UV_LINK_MODE=copy`) from `backend/`. Expected: exits 0.

- [x] **Step 3: Run the baseline suite**

Run `uv run pytest -q` from `backend/`. Expected: all existing tests pass (PostgreSQL reachable on localhost:5433). If failures pre-exist, stop and report.

- [x] **Step 4: Commit plan**

Commit this plan file as `docs(risk): RF-04 risk treatment plan`. Expected: clean `git status` except future work.

- [x] **Step 5: Initialize ledger**

Run `sdd-workspace` for this plan; create `progress.md` with first line `# SDD ledger — plan: docs/superpowers/plans/2026-11-20-rf04-risk-treatment.md`; write the pre-flight shared-interface rows (Task 2 models/enums/errors → Tasks 3–8; Task 3 schemas → Tasks 4–8; Task 4 treatment service → Tasks 7–8; Task 5 action service + metrics → Tasks 7–8; Task 6 residual/overview → Task 7; Task 7 bumps the OpenAPI count once → Task 8 must not; Task 6 `resolve_active` → Task 8 register join — no conflicts, names fixed by this plan's Interfaces blocks) and the baseline test count.

---

### Task 2: Treatment domain foundation (enums, errors, models, migration 0018, conftest)

**Files:**
- Create: `backend/app/modules/risk_treatment/__init__.py`
- Create: `backend/app/modules/risk_treatment/enums.py`
- Create: `backend/app/modules/risk_treatment/errors.py`
- Create: `backend/app/modules/risk_treatment/models.py`
- Create: `backend/alembic/versions/0018_risk_treatment.py`
- Modify: `backend/tests/conftest.py` (TRUNCATE list: add `risk_treatment_residual_assessments, risk_treatment_actions, risk_treatments`)
- Modify: `backend/tests/integration/test_migration.py`
- Test: `backend/tests/unit/test_risk_treatment_models.py` (new)

**Interfaces:**
- Consumes: `app.core.db.Base`; `risks.id` (migration `0017` is current head); `DomainError` from `app.modules.model_inventory.domain.errors`.
- Produces (relied on by later tasks):
  - `RiskTreatmentStrategy(StrEnum)`: `MITIGATE = "mitigate"`, `ACCEPT = "accept"`, `AVOID = "avoid"`, `TRANSFER = "transfer"` (plan §4/§5 — configurable enum, no hard-coded treatment logic).
  - `RiskTreatmentStatus(StrEnum)`: `NOT_STARTED = "not_started"`, `PLANNED = "planned"`, `IN_PROGRESS = "in_progress"`, `COMPLETED = "completed"`, `CANCELLED = "cancelled"` (plan §6). Constant `ACTIVE_TREATMENT_STATUSES: frozenset[str] = {not_started, planned, in_progress}` (Ruling 5).
  - `TREATMENT_TRANSITIONS: dict[str, frozenset[str]]` (Ruling 6): `not_started → {planned, cancelled}`, `planned → {in_progress, cancelled}`, `in_progress → {completed, cancelled}`, `completed → ∅`, `cancelled → ∅`.
  - `TreatmentActionStatus(StrEnum)`: `TODO = "todo"`, `IN_PROGRESS = "in_progress"`, `COMPLETED = "completed"`, `CANCELLED = "cancelled"` (plan §13).
  - `ACTION_TRANSITIONS: dict[str, frozenset[str]]` (Ruling 6): `todo → {in_progress, completed, cancelled}`, `in_progress → {todo, completed, cancelled}`, `completed → ∅`, `cancelled → ∅`.
  - `TreatmentPriority(StrEnum)`: `LOW = "low"`, `MEDIUM = "medium"`, `HIGH = "high"`, `CRITICAL = "critical"` (plan §15 — treatment-action priority, deliberately separate from RF-03 risk priority).
  - `ResidualAssessmentType(StrEnum)`: `EXPECTED = "expected"`, `VERIFIED = "verified"` (plan §24; API accepts only `expected` — Ruling 10).
  - Errors (all subclass `DomainError`): `RiskTreatmentError` (RISK_TREATMENT_ERROR, 400), `RiskTreatmentNotFoundError` (RISK_TREATMENT_NOT_FOUND, 404), `RiskTreatmentActiveExistsError` (RISK_TREATMENT_ACTIVE_EXISTS, 409), `InvalidRiskTreatmentStatusError` (RISK_TREATMENT_STATUS_INVALID, 409), `InvalidRiskTreatmentQueryError` (RISK_TREATMENT_QUERY_INVALID, 400), `RiskTreatmentActionNotFoundError` (RISK_TREATMENT_ACTION_NOT_FOUND, 404), `InvalidRiskTreatmentActionStatusError` (RISK_TREATMENT_ACTION_STATUS_INVALID, 409), `InvalidRiskTreatmentResidualError` (RISK_TREATMENT_RESIDUAL_INVALID, 400).
  - `models.RiskTreatment` — table `risk_treatments`: `id`, `tenant_id`, `risk_id` (FK risks.id, RESTRICT), `strategy` (String 32), `status` (String 32, default `not_started`), `title` (String 255), `description` (Text, nullable), `rationale` (Text, nullable), `owner` (String 255, nullable), `created_by` (String 255, nullable), `planned_start_date` (Date, nullable), `target_completion_date` (Date, nullable), `completed_at` (DateTime tz, nullable), `cancelled_at` (DateTime tz, nullable), `cancellation_reason` (Text, nullable), `created_at`, `updated_at`. Constraints: check `ck_risk_treatments_strategy` strategy IN ('mitigate','accept','avoid','transfer'); check `ck_risk_treatments_status` status IN (5 values); check `ck_risk_treatments_dates` `planned_start_date IS NULL OR target_completion_date IS NULL OR planned_start_date <= target_completion_date`; check `ck_risk_treatments_completed` `(status = 'completed' AND completed_at IS NOT NULL) OR (status <> 'completed' AND completed_at IS NULL)`; check `ck_risk_treatments_cancelled` `(status = 'cancelled' AND cancelled_at IS NOT NULL AND cancellation_reason IS NOT NULL) OR (status <> 'cancelled' AND cancelled_at IS NULL)`; partial unique `uq_risk_treatments_risk_active` UNIQUE (risk_id) WHERE status IN ('not_started','planned','in_progress') (declared in migration AND in `__table_args__` via `Index(..., unique=True, postgresql_where=text(...))`); indexes `ix_risk_treatments_tenant_risk` (tenant_id, risk_id), `ix_risk_treatments_tenant_status` (tenant_id, status), `ix_risk_treatments_tenant_strategy` (tenant_id, strategy), `ix_risk_treatments_tenant_owner` (tenant_id, owner), `ix_risk_treatments_tenant_target` (tenant_id, target_completion_date), `ix_risk_treatments_risk_created` (risk_id, created_at).
  - `models.RiskTreatmentAction` — table `risk_treatment_actions`: `id`, `tenant_id`, `treatment_id` (FK risk_treatments.id, RESTRICT), `title` (String 255), `description` (Text, nullable), `status` (String 32, default `todo`), `owner` (String 255), `priority` (String 32, default `medium`), `planned_start_date` (Date, nullable), `due_date` (Date, nullable), `completed_at` (DateTime tz, nullable), `cancelled_at` (DateTime tz, nullable), `cancellation_reason` (Text, nullable), `created_by` (String 255, nullable), `created_at`, `updated_at`. Constraints: check `ck_risk_treatment_actions_status` status IN ('todo','in_progress','completed','cancelled'); check `ck_risk_treatment_actions_priority` priority IN ('low','medium','high','critical'); check `ck_risk_treatment_actions_dates` same planned<=due shape as treatments; same completed/cancelled invariant checks (`ck_risk_treatment_actions_completed`, `ck_risk_treatment_actions_cancelled`); indexes `ix_risk_treatment_actions_tenant` (tenant_id), `ix_risk_treatment_actions_treatment` (treatment_id), `ix_risk_treatment_actions_treatment_status` (treatment_id, status), `ix_risk_treatment_actions_tenant_owner` (tenant_id, owner), `ix_risk_treatment_actions_tenant_due` (tenant_id, due_date).
  - `models.RiskTreatmentResidualAssessment` — table `risk_treatment_residual_assessments`: `id`, `tenant_id`, `treatment_id` (FK risk_treatments.id, RESTRICT), `likelihood_value` (int), `impact_value` (int), `calculated_score` (int), `severity` (String 32), `assessment_type` (String 32, default `expected`), `created_by` (String 255, nullable), `created_at` (no `updated_at` — append-only). Constraints: check `ck_risk_treatment_residual_type` assessment_type IN ('expected','verified'); check `ck_risk_treatment_residual_severity` severity IN ('low','medium','high','critical'); check `ck_risk_treatment_residual_values` likelihood_value >= 1 AND impact_value >= 1 AND calculated_score >= 1; indexes `ix_risk_treatment_residual_tenant` (tenant_id), `ix_risk_treatment_residual_treatment` (treatment_id), `ix_risk_treatment_residual_treatment_created` (treatment_id, created_at).
  - Migration revision `"0018"`, `down_revision="0017"`, file `0018_risk_treatment.py`, docstring header `"""risk treatment (RF-04)"""`, explicit `op.create_table` style matching `0017`, reversible downgrade (drop indexes, then tables, children first).
  - conftest TRUNCATE list includes the three new tables.

- [x] **Step 1: Write the failing tests**

`backend/tests/unit/test_risk_treatment_models.py`:
- `test_treatment_enum_values` — `RiskTreatmentStrategy` == `{mitigate, accept, avoid, transfer}`; `RiskTreatmentStatus` == `{not_started, planned, in_progress, completed, cancelled}`; `TreatmentActionStatus` == `{todo, in_progress, completed, cancelled}`; `TreatmentPriority` == `{low, medium, high, critical}`; `ResidualAssessmentType` == `{expected, verified}`.
- `test_treatment_transition_maps` — `TREATMENT_TRANSITIONS` matches Ruling 6 exactly (including `not_started → {planned, cancelled}` and empty terminal sets); `ACTION_TRANSITIONS` matches (terminal sets empty, `todo`/`in_progress` each reach `{completed, cancelled}` and each other); `ACTIVE_TREATMENT_STATUSES` == the three active states.
- `test_risk_treatment_table_shape` — `RiskTreatment.__tablename__ == "risk_treatments"`; required columns present (`risk_id`, `strategy`, `status`, `title`, `rationale`, `owner`, `planned_start_date`, `target_completion_date`, `completed_at`, `cancelled_at`, `cancellation_reason`, `created_by`); `RiskTreatmentAction.__tablename__ == "risk_treatment_actions"` with (`treatment_id`, `status`, `owner`, `priority`, `due_date`, `completed_at`); `RiskTreatmentResidualAssessment.__tablename__ == "risk_treatment_residual_assessments"` with (`likelihood_value`, `impact_value`, `calculated_score`, `severity`, `assessment_type`, `created_at`) and NO `updated_at` column (append-only).
- `test_risk_treatment_check_constraints` — inspect `RiskTreatment.__table__.constraints` contains strategy/status/dates/completed/cancelled checks with expected SQL fragments; the partial unique index `uq_risk_treatments_risk_active` is present with `unique=True`; action table has status/priority/dates checks; residual table has type/severity/values checks.

Extend `backend/tests/integration/test_migration.py` with `test_risk_treatment_tables_exist` — reflects all three tables via `sa.inspect`, asserting columns (`strategy`, `cancellation_reason`, `priority`, `assessment_type`) and FKs to `risks`, `risk_treatments`.

- [x] **Step 2: Run and watch them fail**

Run `uv run pytest tests/unit/test_risk_treatment_models.py tests/integration/test_migration.py -q`. Expected: FAIL/ERROR — `ModuleNotFoundError: app.modules.risk_treatment` (feature missing, not typo).

- [x] **Step 3: Implement**

Create `__init__.py` (one-line docstring, risk/risk_scoring style), `enums.py`, `errors.py`, `models.py` per Interfaces; write migration `0018_risk_treatment.py`; add the three tables to the conftest TRUNCATE list (before `risks`).

- [x] **Step 4: Run and watch them pass**

Run `uv run pytest tests/unit/test_risk_treatment_models.py tests/integration/test_migration.py -q`. Expected: PASS. Then run the full suite `uv run pytest -q` — expected all green (conftest rebuilds the test DB from migrations each session).

- [x] **Step 5: Lint + commit**

`uv run ruff check .` clean; `uv run ruff format` on touched files. Commit: `feat(risk-treatment): treatment domain models, enums, errors and migration 0018 (RF-04)`.

---

### Task 3: Pydantic schemas (treatments, actions, residual, overview)

**Files:**
- Create: `backend/app/modules/risk_treatment/schemas.py`
- Test: `backend/tests/unit/test_risk_treatment_schemas.py` (new)

**Interfaces:**
- Consumes: enums from Task 2 (`RiskTreatmentStrategy`, `RiskTreatmentStatus`, `TreatmentActionStatus`, `TreatmentPriority`, `ResidualAssessmentType`), `RiskSeverity` from `app.modules.risk_scoring.enums`.
- Produces (relied on by Tasks 4–8) — all `extra="forbid"`:
  - `TreatmentCreate`: `title` (strip, 1..255, required), `strategy` `RiskTreatmentStrategy` required, `description` optional (max 10000, blank→None), `rationale` optional (max 10000, blank→None), `owner` optional (max 255, blank→None), `planned_start_date` `date | None`, `target_completion_date` `date | None`; model validator: both dates present → `planned_start_date <= target_completion_date` else `ValidationError` (422). No `status` field (always `not_started` — Ruling 6).
  - `TreatmentUpdate` (extra="forbid"): `title`, `description`, `rationale`, `owner`, `strategy`, `planned_start_date`, `target_completion_date`, `status: RiskTreatmentStatus | None` — all optional; same date cross-validator (both present). Never `completed_at`/`cancelled_at`/`cancellation_reason`/`risk_id`.
  - `TreatmentCancelRequest`: `reason` str required `min_length=1, max_length=10000` (strip validator; blank → `ValidationError` → 422) (plan §35, Ruling 8).
  - `TreatmentProgress`: `total_actions: int`, `completed_actions: int`, `in_progress_actions: int`, `cancelled_actions: int`, `remaining_actions: int`, `progress_percent: int` (plan §18).
  - `TreatmentResponse`: `id`, `tenant_id`, `risk_id`, `strategy`, `status`, `title`, `description`, `rationale`, `owner`, `created_by`, `planned_start_date`, `target_completion_date`, `completed_at`, `cancelled_at`, `cancellation_reason`, `is_overdue: bool`, `action_stats: TreatmentProgress | None = None`, `created_at`, `updated_at` (+ tz-aware validators on datetimes, RF-01 style).
  - `TreatmentListResponse`: `items, page, page_size, total, total_pages`.
  - `TreatmentFilter` (extra="forbid"): `page`, `page_size` (le 100), `risk_id` optional UUID, `strategy` optional, `status` optional, `owner` optional (max 255), `overdue: bool | None`, `sort_by` whitelist (`created_at`, `updated_at`, `target_completion_date`), `sort_order` default `desc`.
  - `TreatmentActionCreate` (extra="forbid"): `title` (strip, 1..255, required), `description` optional (max 10000, blank→None), `owner` (strip, 1..255, **required** — Ruling 15), `priority` `TreatmentPriority` default `MEDIUM`, `planned_start_date` `date | None`, `due_date` `date | None`; date cross-validator planned <= due.
  - `TreatmentActionUpdate` (extra="forbid"): `title`, `description`, `owner`, `priority`, `status: TreatmentActionStatus | None`, `planned_start_date`, `due_date` — all optional; date cross-validator when both present.
  - `TreatmentActionResponse`: `id`, `tenant_id`, `treatment_id`, `title`, `description`, `status`, `owner`, `priority`, `planned_start_date`, `due_date`, `completed_at`, `cancelled_at`, `cancellation_reason`, `is_overdue: bool`, `created_by`, `created_at`, `updated_at` (+ tz validators).
  - `TreatmentActionListResponse`: `items, page, page_size, total, total_pages`.
  - `TreatmentActionFilter` (extra="forbid"): `page`, `page_size` (le 100), `status` optional, `owner` optional, `sort_by` whitelist (`created_at`, `due_date`), `sort_order` default `asc`.
  - `ResidualAssessmentCreate` (extra="forbid"): `likelihood` int required ge=1, `impact` int required ge=1, `assessment_type: ResidualAssessmentType = EXPECTED`. Score/severity computed server-side only.
  - `ResidualAssessmentResponse`: `id`, `tenant_id`, `treatment_id`, `likelihood`, `impact`, `calculated_score`, `severity: RiskSeverity`, `assessment_type`, `created_by`, `created_at` (+ tz validator).
  - `ResidualAssessmentListResponse`: `items, page, page_size, total, total_pages`.
  - `TreatmentStatusDistribution`: `not_started`, `planned`, `in_progress`, `completed`, `cancelled` (all int, default 0).
  - `TreatmentStrategyDistribution`: `mitigate`, `accept`, `avoid`, `transfer` (all int, default 0).
  - `TreatmentOverviewResponse` (plan §44/§45): `total_risks`, `risks_with_treatment`, `risks_without_treatment`, `status_distribution`, `strategy_distribution`, `overdue_actions: int`, `overdue_treatments: int`, `untreated_critical: int`, `untreated_high: int`.
  - `TreatmentSummary` (register embed, Ruling 13): `strategy`, `status`, `owner`, `target_completion_date`, `is_overdue` — scalar-only so `risk/schemas.py` imports only `app.modules.risk_treatment.enums` (mirrors `RiskScoreSummary`).

- [x] **Step 1: Write the failing tests**

`tests/unit/test_risk_treatment_schemas.py`:
- `test_treatment_create_strips_title_and_defaults` (title "  Data Exposure Mitigation  " → stripped; status not settable).
- `test_treatment_create_rejects_extra_fields` (`risk_id: ...` in body → `ValidationError` — path param only; `completed_at` → `ValidationError`).
- `test_treatment_create_rejects_inverted_dates` (planned 2027-01-15, target 2026-12-01 → `ValidationError`).
- `test_treatment_create_accepts_single_date` (only target → valid; only planned → valid).
- `test_treatment_update_accepts_partial_payload` and `test_treatment_update_rejects_completed_at` (`completed_at` → `ValidationError`, endpoint-managed).
- `test_treatment_cancel_requires_non_blank_reason` (blank/whitespace → `ValidationError`, plan §35) and `test_treatment_cancel_strips_reason`.
- `test_action_create_requires_owner` (missing `owner` → `ValidationError`; blank → `ValidationError`, Ruling 15).
- `test_action_create_defaults_priority_to_medium` and `test_action_create_rejects_bogus_priority` (`urgent` → `ValidationError`).
- `test_action_create_rejects_inverted_dates` (planned > due → `ValidationError`).
- `test_action_update_accepts_status_and_rejects_completed_at`.
- `test_residual_create_defaults_to_expected` and `test_residual_create_rejects_bogus_type` (`"verified"` passes schema (enum has it) — assert it validates; the service rejects it (Task 6); `bogus` → `ValidationError`).
- `test_treatment_filter_defaults` (page 1, page_size 25, sort_by created_at, sort_order desc).
- `test_treatment_summary_is_scalar_only` (`TreatmentSummary.model_fields` exactly {strategy, status, owner, target_completion_date, is_overdue}).

- [x] **Step 2: Run and watch them fail**

Run `uv run pytest tests/unit/test_risk_treatment_schemas.py -q`. Expected: ERROR — `ModuleNotFoundError: app.modules.risk_treatment.schemas`.

- [x] **Step 3: Implement**

`schemas.py` per Interfaces (strip validators and tz-aware timestamp validators mirror `risk/schemas.py`; date cross-validators as `model_validator(mode="after")`).

- [x] **Step 4: Run and watch them pass**

Run `uv run pytest tests/unit/test_risk_treatment_schemas.py -q` → PASS. Full suite → green.

- [x] **Step 5: Lint + commit**

ruff clean. Commit: `feat(risk-treatment): treatment, action and residual schemas with validation (RF-04)`.

---

### Task 4: Repository + RiskTreatmentService (state machine, acceptance, tenant isolation, audit)

**Files:**
- Create: `backend/app/modules/risk_treatment/repository.py`
- Create: `backend/app/modules/risk_treatment/service.py`
- Modify: `backend/app/modules/audit/enums.py` (add the 12 event types, Ruling 11)
- Test: `backend/tests/integration/test_risk_treatment_service.py` (new)

**Interfaces:**
- Consumes: models/enums/errors/schemas (Tasks 2–3), `AuditService.record_event`, `Risk` from `app.modules.risk.models`, `RiskNotFoundError`/`RiskArchivedError` from `app.modules.risk.errors`, repository patterns from `risk/repository.py`.
- Produces (relied on by Tasks 5–8) — repository module-level functions (`db` first, `tenant_id` second, RF-01 style):
  - `create_treatment(db, **fields) -> RiskTreatment`, `get_treatment(db, tenant_id, treatment_id) -> RiskTreatment | None`, `get_active_treatment(db, tenant_id, risk_id) -> RiskTreatment | None` (active statuses, oldest first), `list_treatments(db, tenant_id, filters) -> tuple[list, int]` with `TREATMENT_SORT_COLUMNS = {"created_at", "updated_at", "target_completion_date"}` (unknown → `InvalidRiskTreatmentQueryError`), `count_treatments_by_risk(db, tenant_id, risk_id)`, `commit_treatment(db)` (IntegrityError → rollback + `RiskTreatmentActiveExistsError` when the partial active index raced, else `RiskTreatmentError`).
  - `RiskTreatmentService(db, tenant_id, request_id=None, actor=None, source="api")`:
    - `create(risk_id, payload: TreatmentCreate) -> RiskTreatment` — risk exists in tenant else `RiskNotFoundError` 404; not archived else `RiskArchivedError` 409; no active treatment else `RiskTreatmentActiveExistsError` 409; strategy `ACCEPT` → effective rationale non-blank else `RiskTreatmentError` 400 (Ruling 7); inserts with `status="not_started"`, `created_by=self.actor`; audit `risk.treatment_created` (+ `risk.treatment_acceptance_recorded` when strategy is `accept`) with `resource_type="treatment"`, `resource_id=str(id)`, `metadata={"risk_id": str(risk_id)}`; single commit.
    - `get(treatment_id) -> RiskTreatment` — tenant-scoped 404 (cross-tenant indistinguishable).
    - `list_by_risk(risk_id, filters) -> tuple[list, int]` — risk 404 first, then tenant-scoped list.
    - `list(filters) -> tuple[list, int]` — tenant-scoped; filters risk_id/strategy/status/owner; `overdue=True` → active statuses AND `target_completion_date < today`, `overdue=False` → negation (Ruling 9).
    - `update(treatment_id, payload: TreatmentUpdate) -> RiskTreatment` — tenant 404; terminal (completed/cancelled) → `InvalidRiskTreatmentStatusError` 409; `status` in payload → must be exactly `treatment.status`'s entry in `TREATMENT_TRANSITIONS` (i.e. a single valid forward step) else 409 (Ruling 6: PATCH reaches only `planned`/`in_progress`); strategy change → `ACCEPT` requires effective rationale else 400; applies non-None fields only; audit `risk.treatment_updated` with `changed_fields`, plus `risk.treatment_strategy_changed` / `risk.treatment_owner_changed` (before/after states) when those change, plus `risk.treatment_acceptance_recorded` when strategy becomes `accept`; single commit.
    - `complete(treatment_id) -> RiskTreatment` — tenant 404; status must be `in_progress` (the only state whose transition map contains `completed`) else `InvalidRiskTreatmentStatusError` 409 (e.g. `completed` → 409, no silent reopen per §55); sets `status="completed"`, `completed_at=now(UTC)`; audit `risk.treatment_completed` with `metadata={"risk_id", "completed_actions", "total_actions"}` (action counts via one count query — Ruling 16); single commit. Never touches the risk row (§20/§21).
    - `cancel(treatment_id, payload: TreatmentCancelRequest) -> RiskTreatment` — tenant 404; current status must allow `cancelled` in `TREATMENT_TRANSITIONS` else 409; sets `status="cancelled"`, `cancelled_at=now`, `cancellation_reason=payload.reason`; audit `risk.treatment_cancelled` with reason in metadata; single commit.
    - `resolve_active(risks: Sequence[Risk]) -> dict[UUID, RiskTreatment]` — one query for the register join (Task 8), keyed by risk_id, active statuses only.
    - `treatment_audit_state(treatment) -> dict` module helper (governance projection: `risk_id`, `strategy`, `status`, `title`, `owner`, `planned_start_date`, `target_completion_date`, `completed_at`, `cancelled_at`, `cancellation_reason`).
  - Audit: `AuditEventType` gains the six treatment members of Ruling 11 (`RISK_TREATMENT_CREATED`, `RISK_TREATMENT_UPDATED`, `RISK_TREATMENT_STRATEGY_CHANGED`, `RISK_TREATMENT_OWNER_CHANGED`, `RISK_TREATMENT_COMPLETED`, `RISK_TREATMENT_CANCELLED`, `RISK_TREATMENT_ACCEPTANCE_RECORDED` — seven values; action/residual events land in Tasks 5–6).

- [x] **Step 1: Write the failing tests**

`tests/integration/test_risk_treatment_service.py` (fixtures: `client`-style seeding via `db` + `seed_risk_categories`/`seed_reference_data`; helpers: create a risk through RF-01's `RiskService`, create a second tenant with its own risk):
- `test_create_treatment_persists_not_started_with_audit` (strategy `mitigate`, owner "Security Team", dates; status `not_started`; audit `risk.treatment_created`, resource_type `treatment`, metadata.risk_id; `created_by` null)
- `test_create_treatment_unknown_risk_returns_404`
- `test_create_treatment_cross_tenant_risk_returns_404`
- `test_create_treatment_on_archived_risk_returns_409` (RF-01 archive first)
- `test_create_second_active_treatment_rejected` (409 `RISK_TREATMENT_ACTIVE_EXISTS`)
- `test_completed_treatment_allows_a_new_treatment` (complete #1, create #2 — plan §49 history)
- `test_create_accept_requires_rationale` (strategy `accept`, blank rationale → 400 `RISK_TREATMENT_ERROR`; with rationale → created + audit `risk.treatment_acceptance_recorded` exactly once)
- `test_create_all_four_strategies` (mitigate/accept/avoid/transfer each create a valid treatment — plan §54)
- `test_get_treatment_tenant_scoped` (tenant B's treatment → 404 from tenant A's service)
- `test_list_treatments_filters_and_pagination` (strategy/status/owner filters; sort whitelist; unknown sort → 400 `RISK_TREATMENT_QUERY_INVALID`)
- `test_list_treatments_overdue_filter` (active treatment with past target → matches `overdue=True`; completed with past target → does not; future target → does not)
- `test_update_treatment_changes_owner_and_strategy_with_audit` (both dedicated events + `risk.treatment_updated` changed_fields)
- `test_update_treatment_status_forward_only` (`not_started→planned` OK; `not_started→in_progress` → 409; `planned→not_started` → 409)
- `test_update_terminal_treatment_rejected` (completed → any PATCH → 409 `RISK_TREATMENT_STATUS_INVALID`)
- `test_update_strategy_to_accept_requires_rationale` (mitigate→accept without rationale → 400; with payload rationale → 400? no — with rationale OK + acceptance event)
- `test_complete_treatment_from_in_progress` (status completed, `completed_at` tz-aware; audit `risk.treatment_completed`; risk row's `status` unchanged — §20)
- `test_complete_treatment_rejects_wrong_state` (`not_started` → 409; already `completed` → 409 — no silent reopen §55)
- `test_cancel_treatment_requires_reason` (blank → `ValidationError`; valid → `cancelled` + `cancelled_at` + reason stored + audit `risk.treatment_cancelled` metadata.reason)
- `test_cancel_not_started_treatment_allowed` (Ruling 6 gap fill)
- `test_cancel_completed_treatment_rejected` (409)
- `test_cross_tenant_mutations_rejected` (update/complete/cancel tenant B's treatment from tenant A → 404)

- [x] **Step 2: Run and watch them fail**

Run `uv run pytest tests/integration/test_risk_treatment_service.py -q`. Expected: ERROR — `ModuleNotFoundError: app.modules.risk_treatment.repository` / service.

- [x] **Step 3: Implement**

`repository.py` (tenant-scoped getters, filter builders, sort whitelist, count+offset list, `commit_treatment` mapping the active-index race), `service.py` (RiskTreatmentService + `treatment_audit_state`), extend `audit/enums.py` with the seven treatment event types.

- [x] **Step 4: Run and watch them pass**

`uv run pytest tests/integration/test_risk_treatment_service.py -q` → PASS. Full suite → green.

- [x] **Step 5: Lint + commit**

ruff clean. Commit: `feat(risk-treatment): treatment service with state machine, acceptance and audit (RF-04)`.

---

### Task 5: RiskTreatmentActionService + derived metrics (progress, overdue)

**Files:**
- Create: `backend/app/modules/risk_treatment/metrics.py`
- Modify: `backend/app/modules/risk_treatment/repository.py` (action repository functions)
- Modify: `backend/app/modules/risk_treatment/service.py` (RiskTreatmentActionService)
- Modify: `backend/app/modules/audit/enums.py` (4 action event types)
- Test: `backend/tests/unit/test_risk_treatment_metrics.py` (new)
- Test: `backend/tests/integration/test_risk_treatment_action_service.py` (new)

**Interfaces:**
- Consumes: Task 2 models/enums/errors, Task 3 schemas, `AuditService`, `RiskTreatmentService` patterns.
- Produces (relied on by Tasks 6–8):
  - `metrics.py` pure functions (no DB, no stored state — plan §17/§18):
    - `treatment_is_overdue(treatment, today: date) -> bool` — active status AND `target_completion_date` set AND `< today`.
    - `action_is_overdue(action, today: date) -> bool` — `status ∈ {todo, in_progress}` AND `due_date` set AND `< today` (Ruling 9: cancelled excluded).
    - `compute_progress(actions: Sequence) -> TreatmentProgress` — counts total/completed/in_progress/cancelled; `remaining = total − completed − cancelled`; `progress_percent = (completed * 100) // total` (0 when total is 0) — plan §56 expectations 33/66/100.
  - Repository: `create_action(db, **fields)`, `get_action(db, tenant_id, action_id)`, `list_actions(db, tenant_id, treatment_id, filters)` with `ACTION_SORT_COLUMNS = {"created_at", "due_date"}` (unknown → `InvalidRiskTreatmentQueryError`), `count_actions_by_treatment(db, treatment_id)`, `commit_action(db)` (IntegrityError → rollback + `RiskTreatmentError`), `get_actions_by_treatment(db, treatment_id) -> list` (one query for progress/audit counts).
  - `RiskTreatmentActionService(db, tenant_id, request_id=None, actor=None, source="api")`:
    - `create(treatment_id, payload: TreatmentActionCreate) -> RiskTreatmentAction` — treatment exists in tenant else `RiskTreatmentNotFoundError` 404; terminal treatment (completed/cancelled) → `InvalidRiskTreatmentStatusError` 409 (no new work on a closed decision); inserts (`status="todo"`, `created_by=self.actor`); audit `risk.treatment_action_created` (`resource_type="treatment_action"`, metadata `{"risk_id", "treatment_id"}`); single commit.
    - `get(action_id)` — tenant-scoped 404.
    - `list(treatment_id, filters) -> tuple[list, int]` — treatment 404 first; tenant-scoped.
    - `update(action_id, payload: TreatmentActionUpdate) -> RiskTreatmentAction` — tenant 404; terminal action → 409 `RISK_TREATMENT_ACTION_STATUS_INVALID`; `status` in payload must be in `ACTION_TRANSITIONS[current]` else 409; applies non-None fields; audit `risk.treatment_action_updated` with `changed_fields`; single commit.
    - `complete(action_id) -> RiskTreatmentAction` — tenant 404; current status must allow `completed` (`todo`/`in_progress`) else 409; sets `completed_at=now`; audit `risk.treatment_action_completed`; single commit. Treatment progress updates automatically (it is derived — §18/§34).
    - `cancel(action_id, payload: TreatmentCancelRequest) -> RiskTreatmentAction` — tenant 404; current status must allow `cancelled` else 409; sets `cancelled_at`, `cancellation_reason`; audit `risk.treatment_action_cancelled` with reason; single commit.
  - Audit: `AuditEventType` gains `RISK_TREATMENT_ACTION_CREATED`, `RISK_TREATMENT_ACTION_UPDATED`, `RISK_TREATMENT_ACTION_COMPLETED`, `RISK_TREATMENT_ACTION_CANCELLED`.

- [x] **Step 1: Write the failing tests**

`tests/unit/test_risk_treatment_metrics.py` (transient ORM objects; `today = date(2026, 11, 20)`):
- `test_progress_three_actions_one_completed` → 33% (plan §56), remaining 2, in_progress counted.
- `test_progress_two_completed` → 66%; `test_progress_all_completed` → 100%, remaining 0.
- `test_progress_no_actions_is_zero` → total 0, percent 0 (§18).
- `test_progress_cancelled_actions_do_not_count_as_remaining` (1 completed, 1 cancelled, 1 todo → percent 33, remaining 1).
- `test_action_overdue_todo_past_due` → True; `test_action_overdue_completed_past_due` → False (plan §57); `test_action_overdue_cancelled_past_due` → False (Ruling 9); `test_action_overdue_future_due` → False; `test_action_overdue_without_due_date` → False.
- `test_treatment_overdue_only_when_active` (active + past target → True; completed + past → False; planned + future → False; no target → False).

`tests/integration/test_risk_treatment_action_service.py` (fixture: create risk + treatment via services):
- `test_create_action_persists_with_defaults` (status `todo`, priority `medium`, audit `risk.treatment_action_created`)
- `test_create_action_requires_owner` (blank → `ValidationError` at schema; exercised via schema in unit tests — here assert service stores required owner)
- `test_create_action_unknown_treatment_404` / `test_create_action_cross_tenant_404`
- `test_create_action_on_terminal_treatment_409` (completed treatment → 409)
- `test_list_actions_tenant_scoped_and_sorted` (unknown sort → 400)
- `test_update_action_owner_priority_due_date` (audit `risk.treatment_action_updated` changed_fields)
- `test_update_action_status_forward_and_back` (`todo→in_progress` OK, `in_progress→todo` OK, `todo→completed` → 409 — must use complete endpoint, `completed→todo` → 409)
- `test_complete_action_sets_completed_at_and_audits` (tz-aware `completed_at`; audit `risk.treatment_action_completed`)
- `test_complete_terminal_action_409` (already completed → 409)
- `test_cancel_action_requires_reason_and_audits` (reason stored + audit metadata)
- `test_cancel_completed_action_409`
- `test_treatment_progress_reflects_actions` (via `RiskTreatmentService.get` + `metrics.compute_progress` on `get_actions_by_treatment`: 3 actions → 1 completed → 33%; complete second → 66%; third → 100% — plan §18/§56)
- `test_cross_tenant_action_mutations_rejected` (update/complete/cancel tenant B's action → 404)

- [x] **Step 2: Run and watch them fail**

Run `uv run pytest tests/unit/test_risk_treatment_metrics.py tests/integration/test_risk_treatment_action_service.py -q`. Expected: ERROR — `ModuleNotFoundError: app.modules.risk_treatment.metrics`.

- [x] **Step 3: Implement**

`metrics.py`, action repository functions, `RiskTreatmentActionService`, audit enum additions per Interfaces.

- [x] **Step 4: Run and watch them pass**

Run the two new files → PASS. Full suite → green.

- [x] **Step 5: Lint + commit**

ruff clean. Commit: `feat(risk-treatment): treatment actions with derived progress and overdue metrics (RF-04)`.

---

### Task 6: Expected residual risk (RF-03 engine) + treatment overview

**Files:**
- Modify: `backend/app/modules/risk_treatment/service.py` (residual + overview methods on `RiskTreatmentService`)
- Modify: `backend/app/modules/risk_treatment/repository.py` (residual + overview queries)
- Modify: `backend/app/modules/audit/enums.py` (2 event types: `RISK_TREATMENT_RESIDUAL_ASSESSMENT_CREATED`; `RISK_TREATMENT_ACCEPTANCE_RECORDED` already added in Task 4)
- Test: `backend/tests/integration/test_risk_treatment_residual_service.py` (new)
- Test: `backend/tests/integration/test_risk_treatment_overview_service.py` (new)

**Interfaces:**
- Consumes: `ScoreCalculator`, `SeverityClassifier` from `app.modules.risk_scoring.engine`; `find_default_framework`, `get_framework_config` from `app.modules.risk_scoring.repository`; `ScoringFrameworkNotFoundError` from `app.modules.risk_scoring.errors`; Task 2–5 services/metrics.
- Produces (relied on by Task 7):
  - `RiskTreatmentService.create_residual(treatment_id, payload: ResidualAssessmentCreate) -> RiskTreatmentResidualAssessment` — treatment exists in tenant else 404; `payload.assessment_type is not EXPECTED` → `InvalidRiskTreatmentResidualError` 400 (plan §23/§24, Ruling 10); active framework else `ScoringFrameworkNotFoundError` 404; likelihood must be an active level of that framework else `InvalidRiskTreatmentResidualError` 400 (message names the value — mirrors RF-03); same for impact; score via `ScoreCalculator.calculate`, severity via `SeverityClassifier.classify` (**engine, never literals** — plan Step 6); insert snapshot with `created_by=self.actor`; audit `risk.treatment_residual_assessment_created` (`resource_type="treatment"`, metadata `{"risk_id", "framework_id"}`); single commit.
  - `RiskTreatmentService.list_residual(treatment_id, filters) -> tuple[list, int]` — treatment 404 first; newest first default.
  - `RiskTreatmentService.get_overview() -> TreatmentOverviewResponse` (plan §44/§45):
    - `total_risks` = active (non-archived) risks of the tenant; `risks_with_treatment` = risks with an active treatment; `risks_without_treatment` = difference.
    - `status_distribution` / `strategy_distribution` over ALL the tenant's treatments (completed/cancelled included — §44 lists them).
    - `overdue_actions` = actions in `{todo, in_progress}` with `due_date < today`; `overdue_treatments` = active treatments with `target_completion_date < today` (both via `metrics` predicates' SQL equivalents).
    - `untreated_critical` / `untreated_high` = active risks whose current RF-03 score severity is `critical`/`high` AND that have no active treatment (§45) — join `risk_scores WHERE is_current` + anti-join active treatments.
  - Audit: `AuditEventType` gains `RISK_TREATMENT_RESIDUAL_ASSESSMENT_CREATED`.

- [x] **Step 1: Write the failing tests**

`tests/integration/test_risk_treatment_residual_service.py` (fixture: seeded scoring framework + risk + treatment):
- `test_create_expected_residual_computes_via_rf03_engine` (likelihood 2, impact 4 → `calculated_score` 8, `severity` `medium` per the seeded thresholds 5–9; `assessment_type` `expected`; audit `risk.treatment_residual_assessment_created`)
- `test_create_residual_unknown_treatment_404` / `test_create_residual_cross_tenant_404`
- `test_create_residual_verified_rejected` (`assessment_type="verified"` → 400 `RISK_TREATMENT_RESIDUAL_INVALID` — plan §23 boundary)
- `test_create_residual_invalid_likelihood_400` (likelihood 6 → 400; message names the value)
- `test_create_residual_without_framework_404` (delete/disable the tenant's framework → `SCORING_FRAMEWORK_NOT_FOUND`)
- `test_create_residual_never_stores_literals` — severity equals `SeverityClassifier.classify(8, thresholds)` computed in the test from the seeded framework's threshold rows (proves config-driven, plan Step 6)
- `test_list_residual_newest_first_and_tenant_scoped`
- `test_residual_history_preserved` (two assessments → both rows retained, append-only, no `updated_at`)

`tests/integration/test_risk_treatment_overview_service.py` (fixture: risks + scores + treatments in known states):
- `test_overview_counts_treated_and_untreated_risks` (3 active risks, 1 with active treatment → with=1, without=2, total=3)
- `test_overview_status_and_strategy_distributions` (e.g. 1 in_progress mitigate, 1 completed mitigate, 1 cancelled accept → status {in_progress:1, completed:1, cancelled:1}, strategy {mitigate:2, accept:1})
- `test_overview_counts_overdue_actions_and_treatments` (past-due todo action + active treatment past target → counted; completed action past due → not counted)
- `test_overview_untreated_critical_and_high` (scored critical risk without treatment → untreated_critical 1; scored high without treatment → 1; critical WITH active treatment → excluded; unscored risk → excluded)
- `test_overview_excludes_archived_risks`
- `test_overview_empty_tenant` (zeros everywhere — no division by zero)

- [x] **Step 2: Run and watch them fail**

Run the two new files → ERROR — `create_residual` / `get_overview` not found (feature missing, not typo).

- [x] **Step 3: Implement**

Residual + overview repository queries and service methods per Interfaces; audit enum addition. Severity/score always through RF-03 engine.

- [x] **Step 4: Run and watch them pass**

Run the two new files → PASS. Full suite → green.

- [x] **Step 5: Lint + commit**

ruff clean. Commit: `feat(risk-treatment): expected residual risk via RF-03 engine and treatment overview (RF-04)`.

---

### Task 7: REST API layer (treatments, actions, residual, overview) with API tests

**Files:**
- Create: `backend/app/modules/risk_treatment/routes.py`
- Modify: `backend/app/main.py` (include the three routers)
- Modify: `backend/tests/api/test_models_api.py` (OpenAPI contract: 99 → 114, plus path-membership set)
- Test: `backend/tests/api/test_risk_treatment_api.py` (new)

**Interfaces:**
- Consumes: `RiskTreatmentService`, `RiskTreatmentActionService`, schemas, `get_db`, `settings.default_tenant_id`, `Request.state.request_id` (as `risk/routes.py` does), `metrics` for response-time `is_overdue`/`action_stats`.
- Produces:
  - Three routers registered in `create_app()`: `treatments_nested_router = APIRouter(prefix="/api/v1/risks")`, `treatments_router = APIRouter(prefix="/api/v1/treatments")`, `treatment_actions_router = APIRouter(prefix="/api/v1/treatment-actions")`.
  - `GET /api/v1/risks/{risk_id}/treatments` — query page, page_size, strategy, status, owner, overdue, sort_by, sort_order → `TreatmentListResponse` (TREATMENT_NESTED_QUERY_KEYS + `_reject_unknown_query`, unknown → 400 `RISK_TREATMENT_QUERY_INVALID`).
  - `POST /api/v1/risks/{risk_id}/treatments` — body `TreatmentCreate` → 201 `TreatmentResponse`.
  - `GET /api/v1/treatments/overview` — no query → 200 `TreatmentOverviewResponse` (**declared before `/{treatment_id}`** so `overview` never hits the UUID parser).
  - `GET /api/v1/treatments/{treatment_id}` — 200 `TreatmentResponse` with `action_stats` from `get_actions_by_treatment` + `metrics.compute_progress` and `is_overdue` set; 404 `RISK_TREATMENT_NOT_FOUND`.
  - `PATCH /api/v1/treatments/{treatment_id}` — body `TreatmentUpdate` → 200 `TreatmentResponse`.
  - `POST /api/v1/treatments/{treatment_id}/complete` — no body → 200 `TreatmentResponse`.
  - `POST /api/v1/treatments/{treatment_id}/cancel` — body `TreatmentCancelRequest` → 200 `TreatmentResponse`.
  - `GET /api/v1/treatments/{treatment_id}/actions` — query page, page_size, status, owner, sort_by, sort_order → `TreatmentActionListResponse` (ACTION_QUERY_KEYS).
  - `POST /api/v1/treatments/{treatment_id}/actions` — body `TreatmentActionCreate` → 201 `TreatmentActionResponse`.
  - `GET /api/v1/treatments/{treatment_id}/residual-assessments` — query page, page_size → `ResidualAssessmentListResponse` (RESIDUAL_QUERY_KEYS = {page, page_size}).
  - `POST /api/v1/treatments/{treatment_id}/residual-assessments` — body `ResidualAssessmentCreate` → 201 `ResidualAssessmentResponse`.
  - `GET /api/v1/treatment-actions/{action_id}` — 200 `TreatmentActionResponse` (with `is_overdue`).
  - `PATCH /api/v1/treatment-actions/{action_id}` — body `TreatmentActionUpdate` → 200.
  - `POST /api/v1/treatment-actions/{action_id}/complete` — no body → 200.
  - `POST /api/v1/treatment-actions/{action_id}/cancel` — body `TreatmentCancelRequest` → 200.
  - Errors use the existing envelope: 404 `RISK_TREATMENT_NOT_FOUND`/`RISK_TREATMENT_ACTION_NOT_FOUND`/`RISK_NOT_FOUND`/`SCORING_FRAMEWORK_NOT_FOUND`, 409 `RISK_TREATMENT_ACTIVE_EXISTS`/`RISK_TREATMENT_STATUS_INVALID`/`RISK_TREATMENT_ACTION_STATUS_INVALID`/`RISK_ARCHIVED`, 400 `RISK_TREATMENT_ERROR`/`RISK_TREATMENT_QUERY_INVALID`/`RISK_TREATMENT_RESIDUAL_INVALID`, 422 `VALIDATION_ERROR` for malformed bodies/blank reasons/inverted dates. Unknown query params → 400.
  - OpenAPI operation count: 99 + 15 new = 114 (verify the delta at runtime; adjust the asserted number to the real count if the existing 99 has drifted, and record the ruling).

- [x] **Step 1: Write the failing tests**

`tests/api/test_risk_treatment_api.py` (fixtures: `client`, `db`; helpers: create a risk via API, create a treatment via API):
- `test_create_treatment_returns_201` (strategy mitigate, status `not_started`, owner echoed, dates echoed, `is_overdue` false, `action_stats` None)
- `test_create_treatment_unknown_risk_404_envelope` (code `RISK_NOT_FOUND`)
- `test_create_treatment_duplicate_active_409` (code `RISK_TREATMENT_ACTIVE_EXISTS`)
- `test_create_accept_without_rationale_returns_400` (code `RISK_TREATMENT_ERROR`)
- `test_create_treatment_inverted_dates_422` (code `VALIDATION_ERROR`)
- `test_create_treatment_validation_error_on_extra_field` (422, e.g. `completed_at`)
- `test_list_treatments_nested_under_risk` (only that risk's treatments; unknown query param → 400)
- `test_get_treatment_detail_includes_progress` (3 actions, 1 completed → `action_stats.progress_percent` == 33, `total_actions` 3, `remaining_actions` 2)
- `test_patch_treatment_updates_owner` and `test_patch_treatment_invalid_transition_409` (`not_started→in_progress`) and `test_patch_treatment_rejects_completed_at_422`
- `test_complete_treatment_flow` (not_started → PATCH planned → PATCH in_progress → complete → status `completed`, `completed_at` set; complete again → 409)
- `test_cancel_treatment_requires_reason_422` and `test_cancel_treatment_stores_reason` (cancelled + reason echoed)
- `test_treatment_404_envelope` (unknown UUID → code `RISK_TREATMENT_NOT_FOUND`)
- `test_create_action_returns_201_and_lists` (owner required → 422 when missing; priority default medium)
- `test_action_complete_and_cancel_endpoints` (complete → `completed_at`; cancel with reason → stored; complete a cancelled action → 409 code `RISK_TREATMENT_ACTION_STATUS_INVALID`)
- `test_action_unknown_404_and_cross_tenant_404`
- `test_residual_create_returns_201_with_engine_result` (likelihood 2, impact 4 → 8, `medium`, `expected`)
- `test_residual_verified_returns_400` (code `RISK_TREATMENT_RESIDUAL_INVALID`)
- `test_residual_list_newest_first`
- `test_overview_returns_distribution_and_untreated` (shape assertions: status/strategy keys, `overdue_actions`, `untreated_critical`, `untreated_high`, treated/untreated sums == total)
- `test_overview_route_not_shadowed_by_treatment_id` (GET `/api/v1/treatments/overview` → 200, not 422)
- `test_openapi_lists_risk_treatment_paths` — `app.openapi()` paths contain the 15 paths above.

Also update `tests/api/test_models_api.py::test_openapi_contract`: add the new paths to the membership set and bump `== 99` to the verified new total (expected 114).

- [x] **Step 2: Run and watch them fail**

Run `uv run pytest tests/api/test_risk_treatment_api.py tests/api/test_models_api.py::test_openapi_contract -q`. Expected: FAIL with 404 NOT_FOUND (routes missing) and contract count mismatch.

- [x] **Step 3: Implement**

`routes.py` with the three routers (overview declared first on the treatments router); service factories passing `settings.default_tenant_id`, `request_id`, `actor=None`; response builders attaching `is_overdue` (metrics with `date.today()`-equivalent UTC date) and `action_stats` on detail; register all three routers in `create_app()` (before `audit_router`, mirroring existing include order).

- [x] **Step 4: Run and watch them pass**

`uv run pytest tests/api/test_risk_treatment_api.py tests/api/test_models_api.py -q` → PASS. Full suite → green.

- [x] **Step 5: Lint + commit**

ruff clean. Commit: `feat(risk-treatment): treatment, action and residual REST APIs (RF-04)`.

---

### Task 8: Risk Register integration (treatment summary, treatment filters)

**Files:**
- Modify: `backend/app/modules/risk/schemas.py` (`TreatmentSummary`, `RiskResponse.treatment`, `RiskFilter` treatment fields)
- Modify: `backend/app/modules/risk/repository.py` (active-treatment outer join; treatment filter conditions)
- Modify: `backend/app/modules/risk/routes.py` (query keys, list params, response builders attach `treatment`)
- Modify: `backend/app/modules/risk/service.py` (`resolve_treatments` helper)
- Test: `backend/tests/api/test_risk_register_treatment_api.py` (new)
- Test: `backend/tests/unit/test_risk_schemas.py` (extend — `TreatmentSummary` shape)

**Interfaces:**
- Consumes: `RiskTreatment` model + `ACTIVE_TREATMENT_STATUSES` + `RiskTreatmentService.resolve_active` (Task 4), `RiskTreatmentStrategy`/`RiskTreatmentStatus` enums (Task 2), `metrics.treatment_is_overdue` (Task 5), existing `RiskFilter`/`RiskResponse`/`list_risks` (RF-01) and RF-03's score join.
- Produces:
  - `risk/schemas.TreatmentSummary` (Task 3's scalar schema re-exported/defined here like `RiskScoreSummary`): `strategy: RiskTreatmentStrategy`, `status: RiskTreatmentStatus`, `owner: str | None`, `target_completion_date: date | None`, `is_overdue: bool` — scalar-only, so `risk/schemas.py` imports only `app.modules.risk_treatment.enums` (plus `date`).
  - `RiskResponse.treatment: TreatmentSummary | None = None` — `null` means no active treatment (plan §42; never a fabricated status).
  - `RiskFilter` gains: `treatment_strategy: RiskTreatmentStrategy | None = None`, `treatment_status: RiskTreatmentStatus | None = None`, `treatment_owner: str | None = Field(default=None, max_length=255)`, `treatment_overdue: bool | None = None`, `treated: bool | None = None` (Ruling 13).
  - `risk/repository.py`: `list_risks` and its count gain `LEFT JOIN RiskTreatment ON RiskTreatment.risk_id == Risk.id AND RiskTreatment.status IN active` (alongside the existing asset + score outer joins — partial unique index guarantees ≤1 matched row, no fan-out, Ruling 5). Conditions: `treated` True → `RiskTreatment.id.is_not(None)`, False → `RiskTreatment.id.is_(None)`; strategy/status/owner → equality on the joined row (naturally implies treated); `treatment_overdue` True → joined `target_completion_date < today`, False → negation-or-null. Existing RF-01/RF-03 sorts and conditions unchanged; unknown sort still → `InvalidRiskQueryError`.
  - `risk/routes.py`: `RISK_QUERY_KEYS` gains `treatment_strategy, treatment_status, treatment_owner, treatment_overdue, treated`; `list_risks` gains matching `Query` params; `_risk_response` attaches `treatment` built from `treatments.get(risk.id)` (create/get/update responses resolve treatments for the single risk).
  - `risk/service.RiskTreatment`-aware helper: `RiskService.resolve_treatments(risks: Sequence[Risk]) -> dict[UUID, RiskTreatment]` — delegates to `RiskTreatmentService(...).resolve_active(risks)` (keeps routes free of cross-module repository calls; mirrors `resolve_scores`).

- [x] **Step 1: Write the failing tests**

`tests/api/test_risk_register_treatment_api.py` (fixtures: `client`, `db`; create risks via API, treatments via API):
- `test_risk_list_treatment_null_when_untreated` (`treatment is None` on a risk with no treatment)
- `test_risk_list_treatment_after_creation` (strategy/status/owner/target echoed; `is_overdue` false)
- `test_risk_detail_includes_treatment`
- `test_treated_true_and_false_filters` (treated=false returns only untreated risks)
- `test_filter_by_treatment_strategy_and_status_and_owner`
- `test_filter_treatment_overdue` (active treatment with past target matches `treatment_overdue=true`; completed past-target does not)
- `test_treatment_status_completed_returns_empty_on_register` (Ruling 13 documented behavior — history via `/api/v1/treatments?status=completed`)
- `test_default_sort_and_scoring_unchanged` (RF-03 sort_by=severity still orders by score; no treatment params → RF-01 `updated_at` desc; `current_score` still present)
- `test_unknown_treatment_filter_rejected` (extra query param → 400 `RISK_QUERY_INVALID`)
- `test_risk_mutations_untreated_by_treatment_join` (create → patch → archive still work)
- `test_register_no_fanout_with_history` (risk with completed treatment + new active treatment → list still returns exactly one row for that risk)

Unit addition in `tests/unit/test_risk_schemas.py`: `TreatmentSummary` scalar-only field set; `RiskResponse.treatment` defaults to None; naive `target_completion_date` is a date (type enforced).

- [x] **Step 2: Run and watch them fail**

Run `uv run pytest tests/api/test_risk_register_treatment_api.py -q`. Expected: FAIL — `treatment` missing from response / unknown query parameter (feature missing, not typo).

- [x] **Step 3: Implement**

Per Interfaces: `TreatmentSummary` + `RiskResponse.treatment` + `RiskFilter` fields first, then repository join/filters, then `resolve_treatments`, then routes wiring.

- [x] **Step 4: Run and watch them pass**

`uv run pytest tests/api/test_risk_register_treatment_api.py -q` → PASS. Then RF-01/RF-03 regression: `uv run pytest tests/api/test_risks_api.py tests/api/test_risk_register_scoring_api.py tests/integration/test_risk_service.py tests/unit/test_risk_schemas.py -q` → PASS. Full suite → green.

- [x] **Step 5: Lint + commit**

ruff clean. Commit: `feat(risk): risk register treatment integration with filters (RF-04)`.

---

### Task 9: End-to-end verification (RF-01 → RF-02 → RF-03 → RF-04 journey), docs, push

**Files:**
- Test: `backend/tests/e2e/test_risk_treatment_acceptance.py` (new)
- Modify: `README.md` (endpoint list + RF-04 mention + status checklist)
- Modify: this plan (check off completed tasks)

**Interfaces:**
- Consumes: everything from Tasks 2–8.
- Produces: green end-to-end proof; documented endpoints; pushed branch + PR.

- [x] **Step 1: Write the failing/verifying end-to-end test**

`tests/e2e/test_risk_treatment_acceptance.py`:
- `test_full_risk_treatment_journey` (plan §63 user journey / §64 example / §65 DoD):
  1. seed reference data, risk categories, assessment templates, scoring framework;
  2. create a discovered asset + risk (RF-01 path);
  3. create an assessment on the risk (RF-02 path) and score it likelihood 4, impact 5 → calculated 20, `critical`, priority 1 (RF-03 path);
  4. create a `mitigate` treatment with rationale, owner "Security Team", planned 2026-12-01, target 2027-01-15 → `not_started`;
  5. PATCH planned → PATCH in_progress → add three actions ("Restrict production access" completed, "Add PII filtering" in progress, "Review access logs" todo) → detail `action_stats` = {total 3, completed 1, in_progress 1, remaining 1, 33%} (plan §18/§64);
  6. complete the second action → 66%; complete the third → 100%; treatment still `in_progress` (§19 — no auto-complete);
  7. complete the treatment → `completed` + `completed_at`; assert the risk is still `open` in the register (§20/§21) and `current_score` still 20/critical (RF-03 intact);
  8. record expected residual likelihood 2, impact 4 → score 8, `medium`, `expected` (§22/§64: 20 Critical → Expected 8 Medium, never claimed as actual);
  9. create a second risk, score it critical, create an `accept` treatment **without** rationale → 400; with rationale → created + `risk.treatment_acceptance_recorded` in the audit trail (§25/§26);
  10. assert the register: risk rows carry `treatment` summaries; `treated=false` returns only untreated risks; `treatment_strategy=accept` returns the accepted risk; `severity=critical` filter still works (RF-03 regression);
  11. assert overview: `risks_with_treatment == 2`, `untreated_critical`/`untreated_high` as constructed, status/strategy distributions match, `overdue_actions` 0 (or constructed past-due todo → 1);
  12. assert the full audit trail in order with `resource_type="treatment"`: `risk.treatment_created`, `risk.treatment_updated` (strategy/owner changes if exercised), `risk.treatment_action_created`, `risk.treatment_action_completed`, `risk.treatment_completed`, `risk.treatment_acceptance_recorded`, `risk.treatment_residual_assessment_created` (query `AuditEvent` by resource_id, ordered by `sequence_no`);
  13. assert RF-01 mutations + RF-02 assessment reads + RF-03 recalculate still work (regression).
- `test_cross_tenant_treatment_isolation` — tenant B's treatment, action, residual assessment are unreachable from tenant A (404s), and tenant A cannot create a treatment on tenant B's risk (plan §51).
- `test_treatment_cancellation_journey` — create treatment → add action → cancel action with reason → cancel treatment with reason; both reasons stored and audited; cancelled treatment's risk still register-visible and `treated=false` (no active treatment).

- [x] **Step 2: Run it**

`uv run pytest tests/e2e/test_risk_treatment_acceptance.py -q` → PASS. If RED: debug with systematic-debugging before proceeding.

- [x] **Step 3: Full acceptance sweep**

- `uv run pytest -q` → entire suite green (no RF-01/RF-02/RF-03/Asset Discovery regression).
- `uv run ruff check .` → clean; `uv run ruff format --check .` on touched files.
- `uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head` → migration reversible (or assert via the Task 2 migration test if no dev DB).
- `uv run python -m app.seed` twice → idempotent (RF-04 adds no seed data — assert no duplicate categories/templates/frameworks).
- Verify OpenAPI: the 15 RF-04 paths appear in `app.openapi()` paths.

- [x] **Step 4: Update README**

Add treatment endpoints to the README endpoint list (`/api/v1/risks/{risk_id}/treatments`, `/api/v1/treatments` + `overview`/`complete`/`cancel`/`actions`/`residual-assessments`, `/api/v1/treatment-actions` + `complete`/`cancel`), a one-line RF-04 mention, and tick the status checklist.

- [x] **Step 5: Commit, push, open PR**

Commit `feat(risk): risk treatment and mitigation acceptance and docs (RF-04)`. Check off tasks in this plan file (`docs: check off RF-04 plan tasks`). Push `feature/rf04-risk-treatment` to `origin`. Open a PR against `main` (stack on `feature/rf03-risk-scoring` if RF-03 is still unmerged) with a summary of RF-04 scope + rulings. Expected: PR created; local suite green.

---

## Definition of Done (plan §65–§66 acceptance mapping)

- Open a prioritized risk → choose strategy → document rationale → assign owner → set dates → create actions → assign action owners → track progress → complete/cancel actions → complete/cancel treatment → record expected residual → view history (Tasks 4–9 journeys).
- Strategies: mitigate/accept/avoid/transfer each create a valid treatment; acceptance requires rationale + emits its audit event (Tasks 4, 7, 9; Rulings 4/7).
- Actions: multiple per treatment, owners required, statuses, priorities, due dates, complete/cancel (Tasks 5, 7).
- Progress: derived 33/66/100 (Tasks 5, 9); overdue correct; completed and cancelled actions never overdue (Task 5 metrics tests, Ruling 9).
- Residual: expected likelihood/impact/score/severity via RF-03 engine; `verified` rejected; expected vs actual distinction enforced (Task 6, Ruling 10).
- Auditability: all 12 events emitted and asserted (Tasks 4–6, 9; Ruling 11).
- Security: organization isolation enforced, cross-tenant treatments/actions/residuals rejected, unauthorized acceptance blocked as far as a role-less system allows (Tasks 4–7, 9; Ruling 4).
- Integration: register displays treatment status + filters (Task 8); RF-03 scores intact; RF-01 history intact; Asset Discovery unaffected (Task 8 regression runs + Task 9 sweep).
- Tested, auditable, tenant-isolated: full suite green every task; migration reversible (Tasks 2, 9); OpenAPI bumped once (Task 7).
