# RF-03 Risk Scoring & Prioritization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver Risk & Compliance subfeature RF-03 — a configurable, versioned risk-scoring framework (likelihood/impact scales, scoring matrix, severity thresholds, priority mapping), a deterministic scoring engine, immutable per-risk score history with current-score selection, controlled override with justification, risk-register scoring integration (filters/sorts/current score), a prioritization overview (distribution + heat-map cells + top risks), and a full audit trail.

**Architecture:** New flat module `app/modules/risk_scoring/` (mirroring `risk/` and `assessment/`): `enums.py`, `errors.py`, `models.py`, `schemas.py`, `repository.py`, `service.py`, `engine.py`, `routes.py`, `seed.py`. One Alembic migration `0017` creates `risk_scoring_frameworks`, `risk_likelihood_levels`, `risk_impact_levels`, `risk_severity_thresholds`, `risk_scores`. Scoring logic lives in `engine.py` (ScoreCalculator / SeverityClassifier / PriorityClassifier) driven entirely by the selected framework's configuration — never hard-coded thresholds. Services integrate with `AuditService` (same-transaction events). RF-01's risk register gains an outer join to the current score for scoring filters/sorts and a `current_score` field on `RiskResponse`. Routes registered in `app/main.py` under `/api/v1/risk-scoring` (framework CRUD + overview) and score endpoints nested under `/api/v1/risks/{risk_id}/scores`.

**Tech Stack:** FastAPI + SQLAlchemy 2.0 + Alembic + PostgreSQL, Pydantic v2, pytest, ruff (line-length 100).

**Spec:** The in-session RF-03 plan (sections 1–67, supplied by the user) is the binding task-level plan; it argues from `docs/private/risk-and-compliance-management.md` §"Subfeature 3 — Risk Scoring & Prioritization" (line 342), §11 (MVP boundary), §16 (Feature-3 DoD), §17 (RF-03 name). This file restructures that plan into TDD tasks.

## Rulings applied (decided in-session, binding for all tasks)

1. **Backend-only.** The repo has no frontend (no package.json, no node tooling; RF-01 Ruling 1 and RF-02 Ruling 1, user-confirmed). RF-03 plan Steps 11–14 (Risk Register columns UI, Risk Detail UI, Risk Matrix UI, Prioritization page) and §§38–41 UX are deferred until a frontend exists. Their data requirements are satisfied at the API level: `RiskResponse.current_score` (§38), scoring filters/sorts on `GET /api/v1/risks` (§27/§42/§43), `GET /api/v1/risk-scoring/overview` returning distribution + heat-map cells + top-10 risks (§25/§26/§41). Cost if wrong: UI is built later against these endpoints.
2. **`organization_id` → `tenant_id`.** Auditra's tenancy column is `tenant_id` (UUID); services take `tenant_id` in the constructor; routes use `settings.default_tenant_id` (RF-01 Ruling 2, RF-02 Ruling 2).
3. **No roles/permissions exist → authorization = tenant isolation + validation.** Role rules (plan §20: Risk Manager/Governance Manager score; override restricted) are deferred with the auth system, exactly as RF-01 and RF-02 shipped (RF-02 Ruling 3). The override's real guards are enforced regardless: non-blank reason (422), score within the framework's valid range (400), tenant-scoped risk/framework (404), and a mandatory audit event.
4. **Framework versioning is combined into the framework row** (plan §44 explicitly allows combining version tables). `risk_scoring_frameworks.version` is an int, immutable after creation; every `RiskScore` snapshots `framework_version` plus `likelihood_label`/`impact_label`/`severity` so history reproduces without the version table. A v2 is a new row with the same `name` and `version=2` (unique per tenant). `PATCH` never changes `version`, `method`, levels, or thresholds — and levels/thresholds are immutable after creation (framework-builder UI is out of scope, plan §48). Cost if wrong: a future builder adds editable version rows.
5. **Severity labels are a fixed enum (`low|medium|high|critical`); ranges, order, and priority are configurable.** Plan §13/§49 pins exactly these four ratings; configurability applies to thresholds (min/max/priority/display_order), not to inventing new labels. Stored as `String(32)` + CHECK constraint (repo convention). Cost if wrong: custom labels need a migration.
6. **Priority is configuration, not a hard-coded map (plan §23):** each `RiskSeverityThreshold` carries a `priority` int (default: critical=1, high=2, medium=3, low=4), snapshotted onto `RiskScore.priority`. The engine derives priority from the threshold row, never from a literal dict.
7. **`RiskScore` is the single source of truth for scoring** (plan §14/§16): no denormalized likelihood/impact/score columns are added to `risks`. The register reads the current score via `LEFT JOIN risk_scores … WHERE is_current` (partial unique index guarantees at most one current row per risk).
8. **Scores are immutable history (plan §37):** `recalculate` and `override` INSERT new rows and flip `is_current`; they never UPDATE an existing score's numbers. An override copies `calculated_score`/`likelihood`/`impact` from the row it overrides, sets `override_score`, `effective_score = override_score`, `is_override=true`, `override_reason`, and derives `severity`/`priority` from the *effective* score. The calculated value is never discarded.
9. **Audit events (plan §53 + repo convention):** `risk.score_created`, `risk.score_recalculated`, `risk.score_overridden` with `resource_type="risk"` and `resource_id=str(risk_id)` (score id in `metadata`), plus `scoring_framework.created`/`scoring_framework.updated` with `resource_type="scoring_framework"`. Severity/priority changes ride inside the score's `after_state` — no separate `risk.severity_changed` events (avoids three events per scoring action). All match `EVENT_TYPE_PATTERN`.
10. **Register ordering (plan §43):** RF-01's default sort (`updated_at` desc) is unchanged; the plan's "Critical → High → Medium → Low, then score desc" ordering is implemented as (a) `sort_by=severity|priority` → `priority ASC NULLS LAST, effective_score DESC` (highest risk first regardless of sort_order) and (b) the `top_risks` ordering inside the overview endpoint. Score/likelihood/impact/scored_at sorts honor `sort_order` with `NULLS LAST` so unscored risks never float above scored ones.
11. **Assessment linkage is an optional reference (plan §19/§50):** `RiskScore.assessment_id` (nullable FK `risk_assessments.id`); when supplied it must exist in the tenant and belong to the risk (else 400 `RISK_SCORE_INVALID`). Likelihood/impact are always explicit inputs — never inferred from questionnaire answers.
12. **Archived risks reject scoring** with 409 `RISK_ARCHIVED` (RF-01 §20 semantics carried over exactly as RF-02 did). Archived risks are also excluded from the overview's distribution/cells/top-risks, matching the register's default active view.
13. **Overview default framework:** `GET /api/v1/risk-scoring/overview` accepts optional `framework_id`; without it, it uses the tenant's active system framework (the seeded Standard AI Risk Matrix). None exists and no id given → 404 `SCORING_FRAMEWORK_NOT_FOUND`. Cells are the cartesian product of the selected framework's active likelihood × impact levels; scores recorded under other frameworks whose values fall outside the grid are counted in the distribution but not plotted in a cell.

## Global Constraints

- Every scoring query is tenant-scoped: `tenant_id == settings.default_tenant_id` at the API layer, explicit `tenant_id` parameter in repository/service (plan §47). Unknown and cross-tenant ids are indistinguishable 404s.
- No physical deletes; scores are never edited or deleted (history is the product, plan §37); frameworks are disabled via `is_active` only.
- Out of scope (never build): risk treatment/mitigation, controls, compliance requirements, evidence, findings, remediation, residual-risk workflow (no residual columns — plan §18), LLM/AI recommendations, framework-builder UI, auth/roles. Schemas are `extra="forbid"` so out-of-scope fields cannot sneak in.
- Scoring must stay configurable: no `17–25 → critical` literals outside `seed.py` and tests; the engine reads thresholds from the framework (plan §3/§55).
- Migration `0017` must be reversible (upgrade + downgrade) and carry FKs, check constraints, unique constraints (including the partial `is_current` unique index), and the indexes listed in Task 2.
- The entire existing test suite must stay green after every task; OpenAPI contract count bumped exactly once (Task 7).
- `uv run ruff check .` clean; format only files this plan touches.
- Commit on branch `feature/rf03-risk-scoring`; push to `origin` only after Task 9 passes; open a PR against `main` (RF-01/RF-02 precedent; user instructed "push the code to github once the implementation is done").

## Review Focus

1. **Engine configurability** — classify/priority must come from the selected framework's threshold rows: with framework A (10–16 → high) and framework B (12–20 → high), `classify(11)` is `high` under A and `medium` under B. Pinned by Task 4 config-swap tests (plan §55).
2. **History immutability + single current score** — recalculate and override each INSERT a new row, flip the previous `is_current` to false in the same transaction, and leave the old row's numbers byte-identical; the partial unique index rejects two current rows. Pinned by Tasks 6–7.
3. **Override guards** — blank reason → 422; override score outside `[min(L)×min(I), max(L)×max(I)]` → 400 `RISK_SCORE_INVALID`; `calculated_score` still visible after override; exactly one `risk.score_overridden` audit event. Pinned by Tasks 6–7.
4. **Tenant isolation** — tenant B's framework/score/risk invisible and unusable from tenant A: 404 on get, 404 on scoring with a foreign framework or against a foreign risk (plan §47). Pinned by Tasks 6–7 and Task 9.
5. **Unscored ≠ Low (plan §28)** — a risk with no score returns `current_score: null` (never a fabricated low rating), `scored=false` filter isolates it, and score sorts leave it `NULLS LAST`. Pinned by Task 8.
6. **Threshold validation (plan §13)** — overlapping bands, gaps, inverted min/max, duplicate severities/priorities, and non-covering ranges rejected at framework creation with 400 `SCORING_FRAMEWORK_INVALID`; the seeded default passes. Pinned by Tasks 4/6.
7. **Register regression** — RF-01 list/detail/create/update/archive behavior and default sort unchanged after the scoring join is added; unknown query params still 400; archived risks still hidden by default. Pinned by Task 8 (existing RF-01 tests must stay green).
8. **Seed idempotency + conftest truncation** — `seed_scoring_framework` twice yields exactly one framework with 5+5 levels and 4 thresholds; the five new tables are in the `db` fixture's TRUNCATE list so API tests start clean. Pinned by Tasks 2–3.

---

### Task 1: Architecture inspection, plan + spec commit, green baseline

**Files:**
- Create: `docs/superpowers/plans/2026-11-18-rf03-risk-scoring.md` (this file)
- No production code changes.

**Interfaces:**
- Consumes: repo conventions (documented in this plan's Rulings).
- Produces: committed plan; verified baseline; initialized ledger.

- [ ] **Step 1: Verify workspace state**

Run `git status`, `git branch --show-current`. Expected: branch `feature/rf03-risk-scoring`, only this plan file untracked.

- [ ] **Step 2: Install dependencies**

Run `uv sync` (with `UV_LINK_MODE=copy`) from `backend/`. Expected: exits 0.

- [ ] **Step 3: Run the baseline suite**

Run `uv run pytest -q` from `backend/`. Expected: all existing tests pass (PostgreSQL reachable on localhost:5433). If failures pre-exist, stop and report.

- [ ] **Step 4: Commit plan**

Commit this plan file as `docs(risk): RF-03 risk scoring plan`. Expected: clean `git status` except future work.

- [ ] **Step 5: Initialize ledger**

Run `sdd-workspace` for this plan; create `progress.md` with first line `# SDD ledger — plan: docs/superpowers/plans/2026-11-18-rf03-risk-scoring.md`; write the pre-flight shared-interface rows (Task 2 models/enums/errors → Task 3 seed → Task 4 engine → Task 5 schemas → Task 6 service → Task 7 routes → Task 8 register integration → Task 9 e2e; plus Task 6 → Task 8: `risk.service` consuming `risk_scoring.repository.get_current_scores_by_risk_ids`, and Task 7 → Task 8: OpenAPI count bumped only in Task 7 — no conflicts, names fixed by this plan's Interfaces blocks) and the baseline test count.

---

### Task 2: Scoring domain foundation (enums, errors, models, migration 0017, conftest)

**Files:**
- Create: `backend/app/modules/risk_scoring/__init__.py`
- Create: `backend/app/modules/risk_scoring/enums.py`
- Create: `backend/app/modules/risk_scoring/errors.py`
- Create: `backend/app/modules/risk_scoring/models.py`
- Create: `backend/alembic/versions/0017_risk_scoring.py`
- Modify: `backend/tests/conftest.py` (TRUNCATE list: add `risk_scores, risk_severity_thresholds, risk_impact_levels, risk_likelihood_levels, risk_scoring_frameworks`)
- Modify: `backend/tests/integration/test_migration.py`
- Test: `backend/tests/unit/test_risk_scoring_models.py` (new)

**Interfaces:**
- Consumes: `app.core.db.Base`; `risks.id` and `risk_assessments.id` (migration `0016` is current head); `DomainError` from `app.modules.model_inventory.domain.errors`.
- Produces (relied on by later tasks):
  - `ScoringMethod(StrEnum)`: `LIKELIHOOD_IMPACT = "likelihood_impact"`.
  - `RiskSeverity(StrEnum)`: `LOW = "low"`, `MEDIUM = "medium"`, `HIGH = "high"`, `CRITICAL = "critical"`.
  - `ScoreSource(StrEnum)`: `MANUAL = "manual"`, `ASSESSMENT = "assessment"`.
  - Errors (all subclass `DomainError`): `RiskScoringError` (RISK_SCORING_ERROR, 400), `ScoringFrameworkNotFoundError` (SCORING_FRAMEWORK_NOT_FOUND, 404), `ScoringFrameworkInactiveError` (SCORING_FRAMEWORK_INACTIVE, 409), `DuplicateScoringFrameworkError` (SCORING_FRAMEWORK_DUPLICATE, 409), `InvalidScoringFrameworkError` (SCORING_FRAMEWORK_INVALID, 400), `InvalidScoringFrameworkQueryError` (SCORING_FRAMEWORK_QUERY_INVALID, 400), `RiskScoreNotFoundError` (RISK_SCORE_NOT_FOUND, 404), `InvalidRiskScoreError` (RISK_SCORE_INVALID, 400), `InvalidRiskScoreQueryError` (RISK_SCORE_QUERY_INVALID, 400), `UnsupportedScoringMethodError` (SCORING_METHOD_UNSUPPORTED, 400).
  - `models.RiskScoringFramework` — table `risk_scoring_frameworks`: `id`, `tenant_id`, `name` (String 100), `description` (Text, nullable), `version` (int, default 1, server_default 1), `method` (String 32, default `likelihood_impact`), `is_system` (bool default false), `is_active` (bool default true), `created_at`, `updated_at`. Constraints: `uq_risk_scoring_frameworks_tenant_name_version` UNIQUE(tenant_id, name, version); check `ck_risk_scoring_frameworks_method` method IN ('likelihood_impact'); check `ck_risk_scoring_frameworks_version` version >= 1; index `ix_risk_scoring_frameworks_tenant_active` (tenant_id, is_active).
  - `models.RiskLikelihoodLevel` — table `risk_likelihood_levels`: `id`, `framework_id` (FK risk_scoring_frameworks.id, RESTRICT), `value` (int), `name` (String 100), `description` (Text, nullable), `display_order` (int), `is_active` (bool default true), `created_at`, `updated_at`. Constraints: `uq_risk_likelihood_levels_framework_value` UNIQUE(framework_id, value); check `ck_risk_likelihood_levels_value` value >= 1; index `ix_risk_likelihood_levels_framework` (framework_id).
  - `models.RiskImpactLevel` — table `risk_impact_levels`: same shape as likelihood (its own unique/check/index names: `uq_risk_impact_levels_framework_value`, `ck_risk_impact_levels_value`, `ix_risk_impact_levels_framework`).
  - `models.RiskSeverityThreshold` — table `risk_severity_thresholds`: `id`, `framework_id` (FK RESTRICT), `minimum_score` (int), `maximum_score` (int), `severity` (String 32), `priority` (int), `display_order` (int), `created_at`, `updated_at`. Constraints: `uq_risk_severity_thresholds_framework_severity` UNIQUE(framework_id, severity); check `ck_risk_severity_thresholds_range` minimum_score >= 1 AND minimum_score <= maximum_score; check `ck_risk_severity_thresholds_severity` severity IN ('low','medium','high','critical'); check `ck_risk_severity_thresholds_priority` priority >= 1; index `ix_risk_severity_thresholds_framework` (framework_id).
  - `models.RiskScore` — table `risk_scores`: `id`, `tenant_id`, `risk_id` (FK risks.id, RESTRICT), `framework_id` (FK risk_scoring_frameworks.id, RESTRICT), `framework_version` (int), `likelihood_value` (int), `likelihood_label` (String 100), `impact_value` (int), `impact_label` (String 100), `calculated_score` (int), `severity` (String 32), `priority` (int), `override_score` (int, nullable), `effective_score` (int), `override_reason` (Text, nullable), `is_inherent` (bool default true), `is_current` (bool default false), `is_override` (bool default false), `assessment_id` (FK risk_assessments.id, RESTRICT, nullable), `source` (String 32, default `manual`), `scored_by` (String 255, nullable), `created_at` (DateTime tz, server_default now; no `updated_at` — rows are immutable). Constraints: check `ck_risk_scores_severity` severity IN (4 values); check `ck_risk_scores_source` source IN ('manual','assessment'); check `ck_risk_scores_override` (is_override = false AND override_score IS NULL) OR (is_override = true AND override_score IS NOT NULL); check `ck_risk_scores_framework_version` framework_version >= 1; partial unique `uq_risk_scores_risk_current` UNIQUE (risk_id) WHERE is_current (declared both in the migration via `op.create_index(..., unique=True, postgresql_where=sa.text("is_current"))` and in `__table_args__` via `Index(..., unique=True, postgresql_where=text("is_current"))`). Indexes: `ix_risk_scores_tenant_risk` (tenant_id, risk_id), `ix_risk_scores_risk_created` (risk_id, created_at), `ix_risk_scores_framework` (framework_id), `ix_risk_scores_tenant_severity` (tenant_id, severity), `ix_risk_scores_tenant_priority` (tenant_id, priority), `ix_risk_scores_tenant_created` (tenant_id, created_at), `ix_risk_scores_assessment` (assessment_id).
  - Migration revision `"0017"`, `down_revision="0016"`, file `0017_risk_scoring.py`, docstring header `"""risk scoring (RF-03)"""`, explicit `op.create_table` style matching `0016`, reversible downgrade (drop indexes, then tables, children first).
  - conftest TRUNCATE list includes the five new tables.

- [ ] **Step 1: Write the failing tests**

`backend/tests/unit/test_risk_scoring_models.py`:
- `test_scoring_enum_values` — `ScoringMethod` == `{likelihood_impact}`; `RiskSeverity` == `{low, medium, high, critical}`; `ScoreSource` == `{manual, assessment}`.
- `test_risk_scoring_table_shape` — `RiskScoringFramework.__tablename__ == "risk_scoring_frameworks"`; required columns present on `RiskScore` (`risk_id`, `framework_id`, `framework_version`, `likelihood_value`, `impact_value`, `calculated_score`, `severity`, `priority`, `override_score`, `effective_score`, `override_reason`, `is_inherent`, `is_current`, `is_override`, `assessment_id`, `source`, `scored_by`, `created_at`); `RiskLikelihoodLevel`, `RiskImpactLevel`, `RiskSeverityThreshold` table names correct.
- `test_risk_score_check_constraints` — inspect `RiskScore.__table__.constraints` contains the severity/source/override/framework_version checks with expected SQL fragments; `RiskSeverityThreshold` has range/severity/priority checks; `RiskScore` carries the partial unique `is_current` index (name `uq_risk_scores_risk_current`, `unique=True`); `RiskScore` has no `updated_at` column (immutability, plan §37).

Extend `backend/tests/integration/test_migration.py` with `test_risk_scoring_tables_exist` — reflects all five tables via `sa.inspect`, asserting columns (`framework_version`, `minimum_score`, `priority`, `effective_score`, `is_current`) and FKs to `risks`, `risk_assessments`, `risk_scoring_frameworks`.

- [ ] **Step 2: Run and watch them fail**

Run `uv run pytest tests/unit/test_risk_scoring_models.py tests/integration/test_migration.py -q`. Expected: FAIL/ERROR — `ModuleNotFoundError: app.modules.risk_scoring` (feature missing, not typo).

- [ ] **Step 3: Implement**

Create `__init__.py` (one-line docstring, risk/assessment style), `enums.py`, `errors.py`, `models.py` per Interfaces; write migration `0017_risk_scoring.py`; add the five tables to the conftest TRUNCATE list.

- [ ] **Step 4: Run and watch them pass**

Run `uv run pytest tests/unit/test_risk_scoring_models.py tests/integration/test_migration.py -q`. Expected: PASS. Then run the full suite `uv run pytest -q` — expected all green (conftest rebuilds the test DB from migrations each session).

- [ ] **Step 5: Lint + commit**

`uv run ruff check .` clean; `uv run ruff format` on touched files. Commit: `feat(risk-scoring): scoring domain models, enums, errors and migration 0017 (RF-03)`.

---

### Task 3: Idempotent default scoring-framework seed

**Files:**
- Create: `backend/app/modules/risk_scoring/seed.py`
- Modify: `backend/app/seed.py` (call `seed_scoring_framework` for the default tenant in `__main__`)
- Modify: `backend/tests/conftest.py` (`client` fixture seeds the framework like it seeds risk categories)
- Test: `backend/tests/integration/test_risk_scoring_seed.py` (new)

**Interfaces:**
- Consumes: models from Task 2, `settings.default_tenant_id`.
- Produces:
  - `STANDARD_AI_RISK_MATRIX_NAME = "Standard AI Risk Matrix"`.
  - `seed_scoring_framework(db: Session, tenant_id: UUID) -> int` — inserts the system framework if `(tenant_id, name, version=1)` is absent, with its levels and thresholds (`is_system=True`, `is_active=True`, `method="likelihood_impact"`), flushes, returns 1 when created / 0 when already present. Idempotent by construction and by the unique constraint.
  - Content (plan §49):
    - Framework name `Standard AI Risk Matrix`, description `Standard 5x5 AI risk matrix: score = likelihood x impact (RF-03 default framework).`, version 1.
    - Likelihood (value, name, description): `1 Rare` "The event is not expected to occur." / `2 Unlikely` "The event could occur but is not expected." / `3 Possible` "The event may occur under some circumstances." / `4 Likely` "The risk is expected to occur under normal operating conditions." / `5 Almost Certain` "The event is expected to occur in most circumstances."
    - Impact: `1 Negligible` "No meaningful harm to stakeholders or operations." / `2 Minor` "Limited harm that is easily contained." / `3 Moderate` "Noticeable harm requiring remediation." / `4 Major` "Severe harm to stakeholders, operations or compliance." / `5 Severe` "Critical or irreversible harm, including safety or regulatory breaches."
    - Thresholds (severity, min, max, priority, display_order): `low 1–4 priority 4 order 1`, `medium 5–9 priority 3 order 2`, `high 10–16 priority 2 order 3`, `critical 17–25 priority 1 order 4`.
    - Every level/threshold `display_order` sequential.

- [ ] **Step 1: Write the failing tests**

`tests/integration/test_risk_scoring_seed.py`:
- `test_seed_creates_standard_framework_with_levels_and_thresholds` — call once: returns 1; framework exists with name `Standard AI Risk Matrix`, `version == 1`, `method == "likelihood_impact"`, `is_system=True`, `is_active=True`; exactly 5 likelihood levels with values 1–5 named Rare/Unlikely/Possible/Likely/Almost Certain; exactly 5 impact levels named Negligible/Minor/Moderate/Major/Severe; exactly 4 thresholds whose (min, max) are (1,4),(5,9),(10,16),(17,25) with priorities 4,3,2,1 for low,medium,high,critical.
- `test_seed_is_idempotent` — call twice: second returns 0; still exactly 1 framework / 5 / 5 / 4 rows.
- `test_seed_scopes_by_tenant` — seed for a second tenant id: that tenant gets its own framework; default tenant unaffected.

- [ ] **Step 2: Run and watch them fail**

Run `uv run pytest tests/integration/test_risk_scoring_seed.py -q`. Expected: ERROR — `ModuleNotFoundError: app.modules.risk_scoring.seed`.

- [ ] **Step 3: Implement**

`app/modules/risk_scoring/seed.py` with the content constants + `seed_scoring_framework`; wire into `app/seed.py` `__main__` block after `seed_assessment_templates` (tenant-scoped, before `db.commit()`); add framework seeding to the conftest `client` fixture.

- [ ] **Step 4: Run and watch them pass**

`uv run pytest tests/integration/test_risk_scoring_seed.py -q` → PASS. Full suite `uv run pytest -q` → green.

- [ ] **Step 5: Lint + commit**

ruff clean. Commit: `feat(risk-scoring): idempotent Standard AI Risk Matrix seed (RF-03)`.

---

### Task 4: Scoring engine (ScoreCalculator, SeverityClassifier, PriorityClassifier)

**Files:**
- Create: `backend/app/modules/risk_scoring/engine.py`
- Test: `backend/tests/unit/test_risk_scoring_engine.py` (new)

**Interfaces:**
- Consumes: `RiskScoringFramework`, `RiskSeverityThreshold` models and `ScoringMethod` enum from Task 2; `UnsupportedScoringMethodError`, `InvalidScoringFrameworkError` from Task 2.
- Produces (relied on by Task 6) — pure, deterministic, DB-free (transient ORM objects or equivalent config rows):
  - `class ScoreCalculator` with classmethod/staticmethod `calculate(likelihood: int, impact: int, framework: RiskScoringFramework) -> int` — dispatch on `framework.method`; `LIKELIHOOD_IMPACT` → `likelihood * impact`; any other method → `UnsupportedScoringMethodError`.
  - `class SeverityClassifier` with `classify(score: int, thresholds: Sequence[RiskSeverityThreshold]) -> str` — return the `severity` value of the band where `minimum_score <= score <= maximum_score` (bands sorted by `minimum_score`); no band → `InvalidScoringFrameworkError(f"no severity threshold covers score {score}")`.
  - `class PriorityClassifier` with `assign(severity: str, thresholds: Sequence[RiskSeverityThreshold]) -> int` — return the `priority` of the threshold whose `severity` matches; none → `InvalidScoringFrameworkError(f"no severity threshold for '{severity}'")`.
  - No threshold literals anywhere in this file (plan §3).

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_risk_scoring_engine.py` (helper: build transient `RiskScoringFramework(method="likelihood_impact")` and threshold rows for the default 1–4/5–9/10–16/17–25 bands):
- `test_calculate_multiplies_likelihood_by_impact` — 4×5 → 20; 3×4 → 12; 2×3 → 6; 1×2 → 2 (plan §54 examples).
- `test_calculate_rejects_unsupported_method` — framework with `method="weighted"` → `UnsupportedScoringMethodError`.
- `test_classify_default_bands` — 20 → `critical`; 12 → `high`; 6 → `medium`; 2 → `low`; boundary scores 4→low, 5→medium, 9→medium, 10→high, 16→high, 17→critical, 25→critical.
- `test_classify_uses_framework_thresholds_not_literals` (plan §55) — framework B with bands 1–11 medium / 12–20 high / 21–25 critical (and a low band covering 1–4 style split as needed to stay contiguous — e.g. low 1–4, medium 5–11, high 12–20, critical 21–25): `classify(11, A) == "high"` while `classify(11, B) == "medium"`; `classify(12, B) == "high"`.
- `test_classify_score_outside_all_bands_raises` — thresholds [(17,25)] and score 10 → `InvalidScoringFrameworkError`.
- `test_priority_comes_from_threshold_rows` — default bands → severity `critical` → 1, `high` → 2, `medium` → 3, `low` → 4; swapped-priority config (e.g. low priority 9) → returns 9 (proves config, not a literal map).
- `test_assign_unknown_severity_raises` — severity `"catastrophic"` → `InvalidScoringFrameworkError`.

- [ ] **Step 2: Run and watch them fail**

Run `uv run pytest tests/unit/test_risk_scoring_engine.py -q`. Expected: FAIL/ERROR — `ModuleNotFoundError: app.modules.risk_scoring.engine`.

- [ ] **Step 3: Implement**

`engine.py` per Interfaces: three classes with static methods, dispatch on `framework.method`, linear scan over ordered thresholds. No DB access, no literals.

- [ ] **Step 4: Run and watch them pass**

`uv run pytest tests/unit/test_risk_scoring_engine.py -q` → PASS. Full suite → green.

- [ ] **Step 5: Lint + commit**

ruff clean. Commit: `feat(risk-scoring): configurable scoring engine (RF-03)`.

---

### Task 5: Pydantic schemas (frameworks, scores, overview)

**Files:**
- Create: `backend/app/modules/risk_scoring/schemas.py`
- Test: `backend/tests/unit/test_risk_scoring_schemas.py` (new)

**Interfaces:**
- Consumes: enums from Task 2 (`ScoringMethod`, `RiskSeverity`).
- Produces (relied on by Tasks 6–8) — all `extra="forbid"`:
  - `LikelihoodLevelResponse`: `id`, `value`, `name`, `description`, `display_order`, `is_active`.
  - `ImpactLevelResponse`: same shape.
  - `SeverityThresholdResponse`: `id`, `minimum_score`, `maximum_score`, `severity`, `priority`, `display_order`.
  - `ScoringFrameworkRef`: `id`, `name`, `version`, `method`.
  - `ScoringFrameworkResponse`: `id`, `tenant_id`, `name`, `description`, `version`, `method`, `is_system`, `is_active`, `created_at`, `updated_at` (+ tz-aware validators, RF-01 style).
  - `ScoringFrameworkDetailResponse`: framework fields + `likelihood_levels: list[LikelihoodLevelResponse]`, `impact_levels: list[ImpactLevelResponse]`, `severity_thresholds: list[SeverityThresholdResponse]`.
  - `ScoringFrameworkListResponse`: `items, page, page_size, total, total_pages`.
  - `ScoringFrameworkFilter` (extra="forbid"): `page`, `page_size` (le 100), `search` (max 255), `is_active`, `is_system` optional, `sort_by` whitelist (`name`, `created_at`, `updated_at`), `sort_order` default `asc`.
  - `LikelihoodLevelInput` / `ImpactLevelInput`: `value` int ge=1, `name` (strip, 1..100), `description` optional (max 10000), `display_order` optional int ge=1 (service defaults to list position), `is_active` default True.
  - `SeverityThresholdInput`: `severity` `RiskSeverity`, `minimum_score` int ge=1, `maximum_score` int ge=1, `priority` int ge=1, `display_order` optional int ge=1.
  - `ScoringFrameworkCreate` (extra="forbid"): `name` (strip, 1..100, required), `description` optional (max 10000), `method` default `LIKELIHOOD_IMPACT`, `version` int default 1 ge=1, `likelihood_levels: list[LikelihoodLevelInput]` min_length=1, `impact_levels: list[ImpactLevelInput]` min_length=1, `severity_thresholds: list[SeverityThresholdInput]` min_length=1, `is_active` default True. Blank `description` → None; blank names → `ValidationError`.
  - `ScoringFrameworkUpdate` (extra="forbid"): `name`, `description`, `is_active` all optional — never `version`/`method`/levels/thresholds (Ruling 4).
  - `RiskScoreCreate` (extra="forbid"): `framework_id` UUID required, `likelihood` int required, `impact` int required, `assessment_id` UUID | None = None. Membership/range checks happen in the service (plan §51).
  - `RiskScoreOverrideRequest` (extra="forbid"): `override_score` int required, `reason` str required `min_length=1, max_length=10000` (strip validator; blank → `ValidationError` → 422).
  - `RiskScoreResponse`: `id`, `risk_id`, `framework: ScoringFrameworkRef`, `framework_version`, `likelihood`, `likelihood_label`, `impact`, `impact_label`, `calculated_score`, `effective_score`, `severity: RiskSeverity`, `priority`, `override_score`, `override_reason`, `is_override`, `is_inherent`, `is_current`, `assessment_id`, `source`, `scored_by`, `created_at` (+ tz validator).
  - `RiskScoreListResponse`: `items, page, page_size, total, total_pages`.
  - `RiskScoreFilter` (extra="forbid"): `page`, `page_size` (le 100), `sort_by` whitelist (`created_at`), `sort_order` default `desc`.
  - `RiskDistributionResponse`: `critical`, `high`, `medium`, `low`, `unscored` (all int, default 0).
  - `MatrixCell`: `likelihood` int, `impact` int, `count` int, `risk_ids: list[UUID]`.
  - `OverviewTopRisk`: `risk_id`, `title`, `severity`, `effective_score`, `priority`.
  - `ScoringOverviewResponse`: `framework: ScoringFrameworkRef`, `distribution: RiskDistributionResponse`, `likelihood_levels: list[LikelihoodLevelResponse]`, `impact_levels: list[ImpactLevelResponse]`, `cells: list[MatrixCell]`, `top_risks: list[OverviewTopRisk]`.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_risk_scoring_schemas.py`:
- `test_framework_create_strips_name_and_defaults` (name "  Custom 4x4  " → stripped; method defaults to `likelihood_impact`; version defaults to 1).
- `test_framework_create_requires_levels_and_thresholds` (missing `likelihood_levels` or empty list → `ValidationError`).
- `test_framework_create_rejects_extra_fields` (`max_score: 25` → `ValidationError` — no flat score config).
- `test_framework_update_accepts_partial_payload` and `test_framework_update_rejects_version_field` (`version: 2` → `ValidationError`, Ruling 4) and `method`/`likelihood_levels` rejected too.
- `test_threshold_input_rejects_blank_severity` (bogus enum → `ValidationError`).
- `test_score_create_requires_framework_and_values` (missing `likelihood` → `ValidationError`).
- `test_score_create_rejects_extra_fields` (`calculated_score: 20` → `ValidationError` — computed server-side only).
- `test_override_request_requires_non_blank_reason` (blank/whitespace reason → `ValidationError`, plan §36).
- `test_override_request_strips_reason`.
- `test_score_filter_defaults` (page 1, page_size 25, sort_by created_at, sort_order desc).
- `test_framework_filter_defaults` (sort_by name, sort_order asc).

- [ ] **Step 2: Run and watch them fail**

Run `uv run pytest tests/unit/test_risk_scoring_schemas.py -q`. Expected: ERROR — `ModuleNotFoundError: app.modules.risk_scoring.schemas`.

- [ ] **Step 3: Implement**

`schemas.py` per Interfaces (strip validators and tz-aware timestamp validators mirror `risk/schemas.py`).

- [ ] **Step 4: Run and watch them pass**

`uv run pytest tests/unit/test_risk_scoring_schemas.py -q` → PASS. Full suite → green.

- [ ] **Step 5: Lint + commit**

ruff clean. Commit: `feat(risk-scoring): scoring and framework schemas with validation (RF-03)`.

---

### Task 6: Repository + service layer (config validation, scoring workflow, tenant isolation, audit)

**Files:**
- Create: `backend/app/modules/risk_scoring/repository.py`
- Create: `backend/app/modules/risk_scoring/service.py`
- Modify: `backend/app/modules/audit/enums.py` (add the 5 event types, Ruling 9)
- Test: `backend/tests/integration/test_risk_scoring_service.py` (new)
- Test: `backend/tests/integration/test_risk_scoring_framework_service.py` (new)

**Interfaces:**
- Consumes: models/enums/errors/schemas/engine (Tasks 2–5), `AuditService.record_event`, `Risk` from `app.modules.risk.models`, `RiskNotFoundError`/`RiskArchivedError` from `app.modules.risk.errors`, `AssessmentNotFoundError` from `app.modules.assessment.errors`, `RiskAssessment` from `app.modules.assessment.models`, repository patterns from `risk/repository.py`.
- Produces (relied on by Tasks 7–8):
  - Repository (module-level functions, `db` first, `tenant_id` second — RF-01 style):
    - Frameworks: `create_framework(db, **fields)`, `get_framework(db, tenant_id, framework_id)`, `find_framework_by_name_version(db, tenant_id, name, version)`, `find_default_framework(db, tenant_id)` (is_system and is_active, oldest first), `list_frameworks(db, tenant_id, filters) -> tuple[list, int]` with `FRAMEWORK_SORT_COLUMNS = {"name", "created_at", "updated_at"}` (unknown → `InvalidScoringFrameworkQueryError`), `get_framework_config(db, framework_id) -> tuple[list[RiskLikelihoodLevel], list[RiskImpactLevel], list[RiskSeverityThreshold]]` (levels by `display_order`, thresholds by `minimum_score`, ≤3 queries), `get_likelihood_level(db, framework_id, value)`, `get_impact_level(db, framework_id, value)`.
    - Scores: `create_score(db, **fields) -> RiskScore`, `get_current_score(db, tenant_id, risk_id) -> RiskScore | None`, `get_current_scores_by_risk_ids(db, tenant_id, risk_ids) -> dict[UUID, RiskScore]` (one query), `clear_current_score(db, tenant_id, risk_id, keep_id: UUID | None)` (sets `is_current=false` for other rows of that risk), `list_scores(db, tenant_id, risk_id, filters) -> tuple[list, int]` with `SCORE_SORT_COLUMNS = {"created_at": RiskScore.created_at}` (unknown → `InvalidRiskScoreQueryError`), `current_scores_with_risk(db, tenant_id) -> list[tuple[RiskScore, Risk]]` (active risks only, joined), `count_unscored_active_risks(db, tenant_id) -> int`.
    - `commit_score(db)` — wraps `db.commit()`, `IntegrityError` → rollback + `RiskScoringError`.
  - `ScoringFrameworkService(db, tenant_id, request_id=None, actor=None, source="api")`:
    - `create(payload: ScoringFrameworkCreate) -> RiskScoringFramework` — duplicate `(tenant, name, version)` → `DuplicateScoringFrameworkError` 409; `validate_config(...)` (below) → `InvalidScoringFrameworkError` 400; unsupported method → `UnsupportedScoringMethodError`; `is_system=False`; inserts framework + levels + thresholds (`display_order` defaults to list position); audit `scoring_framework.created`; single commit.
    - `get(framework_id)` — tenant-scoped 404 (cross-tenant indistinguishable).
    - `get_detail(framework_id) -> tuple[framework, levels, impacts, thresholds]`.
    - `list(filters) -> tuple[list, int]` — tenant-scoped, search on name, filters is_active/is_system, sort whitelist.
    - `update(framework_id, payload: ScoringFrameworkUpdate) -> RiskScoringFramework` — applies only non-None `name`/`description`/`is_active`; never touches `version`/`method`/levels/thresholds; audit `scoring_framework.updated` with `changed_fields`; single commit.
    - `static validate_config(method: str, likelihoods: list[int], impacts: list[int], thresholds: list[SeverityThresholdInput]) -> None` (plan §13/§51): values unique and ≥1; for `likelihood_impact` — `min_possible = min(likelihoods) * min(impacts)`, `max_possible = max(likelihoods) * max(impacts)`; thresholds sorted by `minimum_score` must have `first.minimum_score == min_possible`, `last.maximum_score == max_possible`, each `next.minimum_score == prev.maximum_score + 1` (contiguous, no overlap, no gap), `minimum_score <= maximum_score`, severities unique, priorities unique; any violation → `InvalidScoringFrameworkError` naming the problem.
  - `RiskScoringService(db, tenant_id, request_id=None, actor=None, source="api")`:
    - `calculate_score(likelihood: int, impact: int, framework) -> int` — delegates to `ScoreCalculator`.
    - `classify_severity(score: int, thresholds) -> str` — delegates to `SeverityClassifier`.
    - `calculate_priority(severity: str, thresholds) -> int` — delegates to `PriorityClassifier`.
    - `score_risk(risk_id, payload: RiskScoreCreate) -> RiskScore` — risk exists in tenant else `RiskNotFoundError` 404; not archived else `RiskArchivedError` 409; framework exists in tenant else `ScoringFrameworkNotFoundError` 404; active else `ScoringFrameworkInactiveError` 409; likelihood level exists in framework with `value == payload.likelihood` and `is_active` else `InvalidRiskScoreError` 400 (message names the bad value); same for impact; `assessment_id` when given: unknown/cross-tenant → `AssessmentNotFoundError` 404, `assessment.risk_id != risk_id` → `InvalidRiskScoreError` 400; computes via engine; `clear_current_score(..., keep_id=None)`; inserts new `RiskScore` (`framework_version=framework.version`, labels from level rows, `effective_score=calculated_score`, `is_current=True`, `is_inherent=True`, `source="assessment"` when `assessment_id` else `"manual"`, `scored_by=self.actor`); audit `risk.score_created` (`resource_type="risk"`, `resource_id=str(risk_id)`, `after_state=score_audit_state(score)`, `metadata={"score_id": str(score.id)}`); single commit.
    - `get_current_score(risk_id) -> RiskScore` — risk 404 first, then score else `RiskScoreNotFoundError` 404.
    - `get_score_history(risk_id, filters) -> tuple[list[RiskScore], int]` — risk 404 first; newest first by default.
    - `recalculate_risk(risk_id) -> RiskScore` — risk 404; current score else `RiskScoreNotFoundError` 404; framework re-fetched (FK guarantees existence); recomputes from the current row's `likelihood_value`/`impact_value`; `clear_current_score(..., keep_id=None)`; inserts a NEW row (`is_current=True`, same `assessment_id`, `source`, `scored_by` as the predecessor); audit `risk.score_recalculated` with `metadata={"score_id", "previous_score_id"}`; single commit. Never mutates the predecessor (plan §35).
    - `override_score(risk_id, payload: RiskScoreOverrideRequest) -> RiskScore` — risk 404; current score else 404; `payload.override_score` within `[min(L)×min(I), max(L)×max(I)]` of the current score's framework else `InvalidRiskScoreError` 400 (message states the allowed range); `clear_current_score(..., keep_id=None)`; inserts a NEW row copying `calculated_score`/`likelihood`/`impact`/labels/`framework_id`/`framework_version`/`assessment_id`/`source` from the current row, with `override_score=payload.override_score`, `effective_score=payload.override_score`, `override_reason=payload.reason`, `is_override=True`, `severity`/`priority` re-derived from the effective score; audit `risk.score_overridden` with `before_state=score_audit_state(previous)`, `after_state=score_audit_state(new)`, `metadata={"score_id", "previous_score_id"}`; single commit.
    - `get_overview(framework_id: UUID | None) -> ScoringOverviewResponse` dataclass/dict build — framework = given id (tenant-scoped 404) or `find_default_framework` else `ScoringFrameworkNotFoundError`; distribution over active risks' current scores + `unscored`; cells = cartesian product of active likelihood × impact levels with `risk_ids` of matching current scores (scores outside the grid excluded from cells, still counted in distribution); `top_risks` = active risks with a current score ordered `priority ASC, effective_score DESC` limit 10 with titles.
  - `score_audit_state(score: RiskScore) -> dict` module helper — governance projection: `risk_id`, `framework_id`, `framework_version`, `likelihood`, `impact`, `calculated_score`, `effective_score`, `severity`, `priority`, `override_score`, `override_reason`, `is_override`, `is_current`, `assessment_id`, `source`.
  - Audit: `AuditEventType` gains `RISK_SCORE_CREATED = "risk.score_created"`, `RISK_SCORE_RECALCULATED = "risk.score_recalculated"`, `RISK_SCORE_OVERRIDDEN = "risk.score_overridden"`, `SCORING_FRAMEWORK_CREATED = "scoring_framework.created"`, `SCORING_FRAMEWORK_UPDATED = "scoring_framework.updated"` (all match `EVENT_TYPE_PATTERN`).

- [ ] **Step 1: Write the failing tests**

`tests/integration/test_risk_scoring_framework_service.py` (fixture: seed framework):
- `test_create_framework_persists_with_levels_and_thresholds` (audit `scoring_framework.created`, resource_type `scoring_framework`)
- `test_create_duplicate_name_version_rejected` (409 `SCORING_FRAMEWORK_DUPLICATE`)
- `test_create_with_overlapping_thresholds_rejected` (e.g. low 1–6 + medium 5–9 → 400 `SCORING_FRAMEWORK_INVALID`)
- `test_create_with_gap_in_thresholds_rejected` (low 1–4 + medium 6–9 → 400)
- `test_create_with_thresholds_not_covering_score_range_rejected` (low 1–4 … critical 17–20 → 400, max must be 25)
- `test_create_with_duplicate_severity_or_priority_rejected` (400)
- `test_create_with_unsupported_method_rejected` (400 `SCORING_METHOD_UNSUPPORTED` if schema allows it through)
- `test_get_framework_from_other_tenant_returns_404`
- `test_update_framework_changes_name_and_is_active` (audit `scoring_framework.updated`, changed_fields)
- `test_update_framework_cannot_change_version_method_or_levels` (payload fields rejected at schema — assert service leaves `version`/`method` untouched)
- `test_list_frameworks_search_and_pagination`
- `test_list_frameworks_invalid_sort_returns_400`

`tests/integration/test_risk_scoring_service.py` (fixture: seed framework + create risk (RF-01 path) + a second tenant with its own risk/framework; helpers to build a second framework with different thresholds):
- `test_score_risk_persists_calculated_score_severity_priority` (4×5 → calculated 20, severity `critical`, priority 1, `is_current=True`, labels Likely/Severe, framework_version 1; audit `risk.score_created`, resource_type `risk`)
- `test_score_risk_unknown_risk_returns_404`
- `test_score_risk_cross_tenant_risk_returns_404`
- `test_score_risk_unknown_framework_returns_404`
- `test_score_risk_cross_tenant_framework_returns_404`
- `test_score_risk_inactive_framework_returns_409`
- `test_score_risk_invalid_likelihood_returns_400` (likelihood 6 → `RISK_SCORE_INVALID`; likelihood 0 → 400; likelihood 3 on a framework without value 3 → 400)
- `test_score_risk_invalid_impact_returns_400`
- `test_score_risk_on_archived_risk_returns_409`
- `test_score_risk_with_foreign_assessment_returns_404` and `test_score_risk_with_other_risks_assessment_returns_400`
- `test_scoring_a_second_time_flips_current` (first score `is_current` becomes False, newest True; both rows retained — plan §15 history)
- `test_get_current_score_returns_newest` and `test_get_current_score_unscored_returns_404`
- `test_get_score_history_newest_first` (3 scores → order 3,2,1; framework snapshot on each)
- `test_recalculate_creates_new_row_and_preserves_old` (old row numbers unchanged, `is_current` flipped, history +1; audit `risk.score_recalculated`)
- `test_recalculate_unscored_risk_returns_404`
- `test_override_requires_reason_at_schema_level` (blank reason → `ValidationError` from schema)
- `test_override_within_range_creates_new_row` (current calculated 12/high; override 16 → effective 16, calculated still 12, `is_override=True`, reason stored, severity re-derived from 16 → `high`, priority 2; audit `risk.score_overridden` with before/after states)
- `test_override_out_of_range_returns_400` (override 26 or 0 → `RISK_SCORE_INVALID` message contains allowed range)
- `test_override_unscored_risk_returns_404`
- `test_override_does_not_mutate_previous_rows` (predecessor row's `override_score` still NULL, `is_override` still False)
- `test_overview_distribution_cells_and_top_risks` (score 3 active risks + leave 1 unscored → distribution counts incl. `unscored == 1`; cells contain a cell with count ≥1 and matching `risk_ids`; `top_risks` ordered priority asc, effective desc, max 10, carries titles)
- `test_overview_excludes_archived_risks` (archived scored risk not counted in distribution/cells/top_risks)
- `test_overview_unknown_framework_returns_404`

- [ ] **Step 2: Run and watch them fail**

Run `uv run pytest tests/integration/test_risk_scoring_service.py tests/integration/test_risk_scoring_framework_service.py -q`. Expected: ERROR — `ModuleNotFoundError: app.modules.risk_scoring.repository` / service.

- [ ] **Step 3: Implement**

`repository.py` (tenant-scoped getters, filter builders, sort whitelists, count+offset lists, `clear_current_score` UPDATE, batch lookups — mirror `risk/repository.py`), `service.py` (two services + `validate_config` + `score_audit_state` + engine delegation), extend `audit/enums.py` with the five event types.

- [ ] **Step 4: Run and watch them pass**

`uv run pytest tests/integration/test_risk_scoring_service.py tests/integration/test_risk_scoring_framework_service.py -q` → PASS. Full suite → green.

- [ ] **Step 5: Lint + commit**

ruff clean. Commit: `feat(risk-scoring): scoring services with workflow, validation, tenant isolation and audit (RF-03)`.

---

### Task 7: REST API layer (risk-scoring + nested score endpoints) with API tests

**Files:**
- Create: `backend/app/modules/risk_scoring/routes.py`
- Modify: `backend/app/main.py` (include both routers)
- Modify: `backend/tests/api/test_models_api.py` (OpenAPI contract: 89 → 99, plus path-membership set)
- Test: `backend/tests/api/test_risk_scoring_api.py` (new)

**Interfaces:**
- Consumes: `ScoringFrameworkService`, `RiskScoringService`, schemas, `get_db`, `settings.default_tenant_id`, `Request.state.request_id` (as `risk/routes.py` does).
- Produces:
  - `router = APIRouter(prefix="/api/v1/risk-scoring")` and `scores_router = APIRouter(prefix="/api/v1/risks")` (both registered in `create_app()`).
  - `GET /api/v1/risk-scoring/frameworks` — query: page, page_size, search, is_active, is_system, sort_by, sort_order → `ScoringFrameworkListResponse`. FRAMEWORK_QUERY_KEYS set + `_reject_unknown_query` (unknown → 400 `SCORING_FRAMEWORK_QUERY_INVALID`).
  - `POST /api/v1/risk-scoring/frameworks` — body `ScoringFrameworkCreate` → 201 `ScoringFrameworkDetailResponse`.
  - `GET /api/v1/risk-scoring/frameworks/{framework_id}` — 200 detail (levels + thresholds), 404 envelope `SCORING_FRAMEWORK_NOT_FOUND`.
  - `PATCH /api/v1/risk-scoring/frameworks/{framework_id}` — body `ScoringFrameworkUpdate` → 200 `ScoringFrameworkResponse`.
  - `GET /api/v1/risk-scoring/overview` — optional query `framework_id` → 200 `ScoringOverviewResponse` (OVERVIEW_QUERY_KEYS = `{framework_id}`).
  - `GET /api/v1/risks/{risk_id}/scores` — query page, page_size, sort_by, sort_order → `RiskScoreListResponse` (history, newest first).
  - `POST /api/v1/risks/{risk_id}/scores` — body `RiskScoreCreate` → 201 `RiskScoreResponse` (plan §32 shape: likelihood, impact, calculated_score, severity, priority, is_current).
  - `GET /api/v1/risks/{risk_id}/scores/current` — 200 `RiskScoreResponse`; unscored → 404 `RISK_SCORE_NOT_FOUND`.
  - `POST /api/v1/risks/{risk_id}/scores/recalculate` — no body → 200 `RiskScoreResponse` (the NEW current row).
  - `POST /api/v1/risks/{risk_id}/scores/override` — body `RiskScoreOverrideRequest` → 200 `RiskScoreResponse`.
  - Errors use the existing envelope: 404 `SCORING_FRAMEWORK_NOT_FOUND`/`RISK_SCORE_NOT_FOUND`/`RISK_NOT_FOUND`/`ASSESSMENT_NOT_FOUND`, 409 `SCORING_FRAMEWORK_INACTIVE`/`SCORING_FRAMEWORK_DUPLICATE`/`RISK_ARCHIVED`, 400 `RISK_SCORE_INVALID`/`SCORING_FRAMEWORK_INVALID`/`*_QUERY_INVALID`, 422 `VALIDATION_ERROR` for malformed bodies/blank reason. Unknown query params → 400.
  - OpenAPI operation count: 89 + 10 new = 99 (verify the delta at runtime; adjust the asserted number to the real count if the existing 89 has drifted, and record the ruling).

- [ ] **Step 1: Write the failing tests**

`tests/api/test_risk_scoring_api.py` (fixtures: `client`, `db`; helper `_framework_id(client)` returning the seeded framework's id; helper to create a risk via API):
- `test_list_frameworks_includes_seeded_standard_matrix` (name present, `is_system` true, `version` 1, `method` `likelihood_impact`)
- `test_get_framework_detail_returns_levels_and_thresholds` (5 likelihood, 5 impact, 4 thresholds ordered by minimum_score)
- `test_create_custom_framework_returns_201_with_detail`
- `test_create_duplicate_framework_returns_409_envelope` (code `SCORING_FRAMEWORK_DUPLICATE`)
- `test_create_framework_overlapping_thresholds_returns_400` (code `SCORING_FRAMEWORK_INVALID`)
- `test_patch_framework_updates_name` and `test_patch_framework_rejects_version_field` (422 `VALIDATION_ERROR`)
- `test_get_unknown_framework_returns_404_envelope` (code `SCORING_FRAMEWORK_NOT_FOUND`)
- `test_list_frameworks_unknown_filter_rejected` (extra query param → 400)
- `test_list_frameworks_invalid_sort_returns_400` (code `SCORING_FRAMEWORK_QUERY_INVALID`)
- `test_create_score_returns_201_with_calculated_result` (framework seeded, likelihood 4, impact 5 → `calculated_score` 20, `effective_score` 20, `severity` `critical`, `priority` 1, `is_current` true, labels Likely/Severe)
- `test_create_score_unknown_risk_404_envelope` (code `RISK_NOT_FOUND`)
- `test_create_score_unknown_framework_404` (code `SCORING_FRAMEWORK_NOT_FOUND`)
- `test_create_score_inactive_framework_409` (code `SCORING_FRAMEWORK_INACTIVE`)
- `test_create_score_invalid_likelihood_returns_400` (likelihood 6 → code `RISK_SCORE_INVALID`)
- `test_create_score_validation_error_on_extra_field` (422 `VALIDATION_ERROR`, e.g. `calculated_score: 20`)
- `test_get_current_score_returns_latest` and `test_get_current_score_unscored_404` (code `RISK_SCORE_NOT_FOUND`)
- `test_score_history_lists_newest_first_with_framework_snapshot`
- `test_recalculate_creates_new_record` (history grows; previous row `is_current` false — assert via history payload)
- `test_override_without_reason_returns_422` and `test_override_with_reason_preserves_calculated_score` (calculated unchanged, effective == override, `is_override` true, reason echoed)
- `test_override_out_of_range_returns_400` (code `RISK_SCORE_INVALID`)
- `test_score_archived_risk_returns_409` (code `RISK_ARCHIVED`)
- `test_cross_tenant_framework_get_returns_404` (framework created directly in DB for tenant B)
- `test_overview_returns_distribution_cells_and_top_risks` (shape assertions + distribution counts)
- `test_openapi_lists_risk_scoring_paths` — `app.openapi()` paths contain `/api/v1/risk-scoring/frameworks`, `/api/v1/risk-scoring/frameworks/{framework_id}`, `/api/v1/risk-scoring/overview`, `/api/v1/risks/{risk_id}/scores`, `/api/v1/risks/{risk_id}/scores/current`, `/api/v1/risks/{risk_id}/scores/recalculate`, `/api/v1/risks/{risk_id}/scores/override`.

Also update `tests/api/test_models_api.py::test_openapi_contract`: add the new paths to the membership set and bump `== 89` to the verified new total (expected 99).

- [ ] **Step 2: Run and watch them fail**

Run `uv run pytest tests/api/test_risk_scoring_api.py tests/api/test_models_api.py::test_openapi_contract -q`. Expected: FAIL with 404 NOT_FOUND (routes missing) and contract count mismatch.

- [ ] **Step 3: Implement**

`routes.py` with the two routers; service factories passing `settings.default_tenant_id`, `request_id`, `actor=None`; `_build` filter wrappers (ValidationError → domain query error); register both routers in `create_app()` (before `audit_router`, mirroring existing include order).

- [ ] **Step 4: Run and watch them pass**

`uv run pytest tests/api/test_risk_scoring_api.py tests/api/test_models_api.py -q` → PASS. Full suite → green.

- [ ] **Step 5: Lint + commit**

ruff clean. Commit: `feat(risk-scoring): scoring framework and risk score REST APIs (RF-03)`.

---

### Task 8: Risk Register integration (current score, scoring filters and sorts)

**Files:**
- Modify: `backend/app/modules/risk/schemas.py` (`RiskScoreSummary`, `RiskResponse.current_score`, `RiskFilter` scoring fields)
- Modify: `backend/app/modules/risk/repository.py` (outer join to current score; score filter conditions; score sort branches)
- Modify: `backend/app/modules/risk/routes.py` (query keys, list params, response builders attach `current_score`)
- Modify: `backend/app/modules/risk/service.py` (`resolve_scores` helper)
- Test: `backend/tests/api/test_risk_register_scoring_api.py` (new)
- Test: `backend/tests/unit/test_risk_schemas.py` (extend if present, else create) — `RiskScoreSummary` shape

**Interfaces:**
- Consumes: `RiskScore` model + `risk_scoring.repository.get_current_scores_by_risk_ids` (Task 6), `RiskSeverity` enum (Task 2), existing `RiskFilter`/`RiskResponse`/`list_risks` (RF-01).
- Produces:
  - `risk/schemas.RiskScoreSummary`: `likelihood: int`, `impact: int`, `calculated_score: int`, `effective_score: int`, `severity: RiskSeverity`, `priority: int`, `is_override: bool`, `scored_at: datetime` (tz-aware validator). Scalar-only — no framework ref, so `risk/schemas.py` imports only `app.modules.risk_scoring.enums`.
  - `RiskResponse.current_score: RiskScoreSummary | None = None` — `null` means unscored (plan §28; never fabricated as low).
  - `RiskFilter` gains: `severity: RiskSeverity | None = None`, `priority: int | None = Field(default=None, ge=1)`, `scored: bool | None = None`, `likelihood: int | None = None`, `impact: int | None = None`, `framework_id: UUID | None = None`.
  - `risk/repository.py`: `list_risks` and its count always `LEFT JOIN RiskScore ON RiskScore.risk_id == Risk.id AND RiskScore.is_current == true` (alongside the existing asset outer join). Conditions: `scored` True → `RiskScore.id.is_not(None)`, False → `RiskScore.id.is_(None)`; `severity`/`priority`/`likelihood`/`impact`/`framework_id` → equality on the joined row (naturally implies scored). Sorts (Ruling 10): `score` → `RiskScore.effective_score`, `likelihood` → `likelihood_value`, `impact` → `impact_value`, `scored_at` → `RiskScore.created_at` — each honors `sort_order` with `.nulls_last()`; `severity`/`priority` → fixed `RiskScore.priority.asc().nulls_last(), RiskScore.effective_score.desc()` (highest risk first, `sort_order` ignored); existing RF-01 sorts unchanged. Unknown sort still → `InvalidRiskQueryError`.
  - `risk/routes.py`: `RISK_QUERY_KEYS` gains `severity, priority, scored, likelihood, impact, framework_id`; `list_risks` endpoint gains matching `Query` params (severity as `RiskSeverity | None`, priority `int` ge 1); `_risk_response(risk, categories, assets, scores)` and `_page_response` attach `current_score` built from `scores.get(risk.id)` (create/get/update responses resolve scores for the single risk).
  - `risk/service.RiskService.resolve_scores(risks: Sequence[Risk]) -> dict[UUID, RiskScore]` — delegates to `get_current_scores_by_risk_ids` (keeps routes free of cross-module repository calls).

- [ ] **Step 1: Write the failing tests**

`tests/api/test_risk_register_scoring_api.py` (fixtures: `client`, `db`; create risks via API; score them via `POST /api/v1/risks/{id}/scores`):
- `test_risk_list_includes_null_current_score_when_unscored` (`current_score is None` on an unscored risk — distinguishable from low, plan §28)
- `test_risk_list_current_score_after_scoring` (`current_score.severity`/`priority`/`effective_score` populated, `likelihood`/`impact` echoed)
- `test_risk_detail_includes_current_score`
- `test_filter_scored_true_and_false` (`scored=false` returns only unscored risks; `scored=true` only scored)
- `test_filter_by_severity` (`severity=critical` returns exactly the critical-scored risks)
- `test_filter_by_priority` (`priority=1`)
- `test_filter_by_likelihood_impact_and_framework_id`
- `test_sort_by_score_desc_puts_highest_first_and_unscored_last` (scores 20, 12, 6 + one unscored → order 20, 12, 6, unscored)
- `test_sort_by_severity_puts_critical_first` (critical before high before medium, score desc within)
- `test_sort_by_scored_at_and_likelihood` (honors sort_order, nulls last)
- `test_default_sort_unchanged` (no sort params → RF-01 ordering still applies, `updated_at` desc)
- `test_unknown_scoring_filter_rejected` (extra query param → 400 `RISK_QUERY_INVALID`)
- `test_invalid_scoring_sort_rejected` (`sort_by=calculated_score` → 400)
- `test_archived_risk_hidden_by_default_with_score` (scoring an archived risk is 409; default list never returns archived)
- `test_scoring_flow_does_not_break_rf01_mutations` (create → patch → archive still work with scoring join present)

Unit addition `RiskScoreSummary` test: rejects naive timestamps; defaults `current_score` to None on `RiskResponse`.

- [ ] **Step 2: Run and watch them fail**

Run `uv run pytest tests/api/test_risk_register_scoring_api.py -q`. Expected: FAIL — `current_score` missing from response / unknown query parameter (feature missing, not typo).

- [ ] **Step 3: Implement**

Per Interfaces: schemas first, then repository join/filters/sorts, then service `resolve_scores`, then routes wiring.

- [ ] **Step 4: Run and watch them pass**

`uv run pytest tests/api/test_risk_register_scoring_api.py -q` → PASS. Then RF-01 regression: `uv run pytest tests/api/test_risks_api.py tests/integration/test_risk_service.py tests/unit/test_risk_schemas.py -q` → PASS. Full suite → green.

- [ ] **Step 5: Lint + commit**

ruff clean. Commit: `feat(risk): risk register scoring integration with filters and sorting (RF-03)`.

---

### Task 9: End-to-end verification (RF-01 → RF-02 → RF-03 journey), docs, push

**Files:**
- Test: `backend/tests/e2e/test_risk_scoring_acceptance.py` (new)
- Modify: `README.md` (endpoint list + RF-03 mention + status checklist)
- Modify: this plan (check off completed tasks)

**Interfaces:**
- Consumes: everything from Tasks 2–8.
- Produces: green end-to-end proof; documented endpoints; pushed branch + PR.

- [ ] **Step 1: Write the failing/verifying end-to-end test**

`tests/e2e/test_risk_scoring_acceptance.py`:
- `test_full_risk_scoring_journey` (plan §61 user journey / §62 DoD):
  1. seed reference data, risk categories, assessment templates, scoring framework;
  2. create a discovered asset + risk (RF-01 path) + a second unscored risk;
  3. create an assessment on the risk (RF-02 path), record answers contextually — likelihood/impact chosen explicitly by the assessor (plan §50: no auto-inference);
  4. score the risk with the seeded framework, likelihood 4, impact 5, `assessment_id` linked → `calculated_score` 20, `severity` `critical`, `priority` 1, `is_current` true;
  5. assert `GET /api/v1/risks` shows `current_score` for the scored risk and `null` for the unscored one; `severity=critical` filter returns only the scored risk; `scored=false` returns the other;
  6. assert score history: one row, framework version 1, labels Likely/Severe;
  7. recalculate → history has 2 rows, previous `is_current` false, numbers unchanged;
  8. override with reason `"Expanded deployment exposure"` → new row: `calculated_score` still 20, `effective_score` == override, `is_override` true, reason stored; history has 3 rows;
  9. assert the audit trail in order with `resource_type="risk"`: `risk.score_created`, `risk.score_recalculated`, `risk.score_overridden` (query `AuditEvent` by resource_id = risk id, ordered by `sequence_no`);
  10. assert overview: distribution counts (1 critical, 1 unscored), cells contain the (4,5) cell with the risk id, `top_risks[0].risk_id` == scored risk;
  11. assert the register still supports RF-01 mutations and RF-02 assessment reads (regression).
- `test_cross_tenant_scoring_isolation` — tenant B's framework, risk, and score are unreachable from tenant A (404s), and tenant A cannot score tenant B's risk or use tenant B's framework (plan §47).

- [ ] **Step 2: Run it**

`uv run pytest tests/e2e/test_risk_scoring_acceptance.py -q` → PASS. If RED: debug with systematic-debugging before proceeding.

- [ ] **Step 3: Full acceptance sweep**

- `uv run pytest -q` → entire suite green (no RF-01/RF-02/Asset Discovery regression).
- `uv run ruff check .` → clean; `uv run ruff format --check .` on touched files.
- `uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head` → migration reversible (or assert via the Task 2 migration test if no dev DB).
- `uv run python -m app.seed` twice → idempotent (one framework with 5/5/4 config, 21 categories, one template, no duplicates).
- Verify OpenAPI: `/api/v1/risk-scoring/frameworks`, `/api/v1/risk-scoring/overview`, and the score subroutes appear in `app.openapi()` paths.

- [ ] **Step 4: Update README**

Add scoring endpoints to the README endpoint list (`/api/v1/risk-scoring/frameworks` + subroutes, `/api/v1/risk-scoring/overview`, `/api/v1/risks/{risk_id}/scores` + `current`/`recalculate`/`override`), a one-line RF-03 mention, and tick the status checklist.

- [ ] **Step 5: Commit, push, open PR**

Commit `feat(risk-scoring): risk scoring and prioritization acceptance and docs (RF-03)`. Check off tasks in this plan file (`docs: check off RF-03 plan tasks`). Push `feature/rf03-risk-scoring` to `origin`. Open PR against `main` with a summary of RF-03 scope + rulings. Expected: PR created; local suite green.

---

## Definition of Done (plan §62–§63 acceptance mapping)

- Select active framework, likelihood, impact → correct score, severity, priority (Tasks 4, 6, 7 tests).
- Default framework seeded; likelihood/impact levels, thresholds, priority configurable; framework versions preserved (Tasks 3, 6 config-validation tests; Ruling 4/6).
- Every scoring event retained; no overwrites; current score identifiable; framework version + labels snapshotted (Task 6 immutability tests + partial unique index, Task 2).
- Override: authorized-by-tenant, justification required, calculated score visible, effective score identified, audited (Tasks 6–7 override tests, Ruling 3).
- Prioritization: sort by score/severity/priority; filter severity/priority; unscored distinguishable (Task 8).
- Heat map + distribution + top risks available as data; matrix driven by configured framework (Task 7 overview tests, Ruling 13); UI deferred (Ruling 1).
- Security: tenant isolation enforced; unauthorized (cross-tenant) scoring and overrides rejected (Tasks 6, 7, 9).
- Integration: RF-01 register displays scoring data; RF-02 assessments intact; Asset Discovery unaffected; no duplicate Risk/Asset entities (Task 8 regression runs + Task 9 sweep).
- Auditable, tested, versioned: audit events (Task 6), full suite green (every task), migration reversible (Tasks 2, 9).
