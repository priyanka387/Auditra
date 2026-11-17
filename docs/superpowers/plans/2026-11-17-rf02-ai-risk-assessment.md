# RF-02 AI Risk Assessment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver Risk & Compliance subfeature RF-02 — a data-driven AI risk assessment workflow: seeded assessment templates with sections/questions, assessments linked to RF-01 risks, draft/resume via per-question responses, a controlled lifecycle (draft → in progress → submitted → under review → approved/rejected), rejection-with-reason, revision history, completion percentage, and audit trail.

**Architecture:** New flat module `app/modules/assessment/` (mirroring `risk/`): `enums.py`, `errors.py`, `models.py`, `schemas.py`, `repository.py`, `service.py`, `routes.py`, `seed.py`. One Alembic migration `0016` creates `assessment_templates`, `assessment_sections`, `assessment_questions`, `risk_assessments`, `assessment_responses`. Services integrate with the existing `AuditService` (same-transaction events). Routes registered in `app/main.py` under `/api/v1/risk-assessments` and `/api/v1/assessment-templates`.

**Tech Stack:** FastAPI + SQLAlchemy 2.0 + Alembic + PostgreSQL, Pydantic v2, pytest, ruff (line-length 100).

**Spec:** The in-session RF-02 plan (sections 1–52, supplied by the user) is the binding task-level plan; it argues from `docs/private/risk-and-compliance-management.md` §4 (Subfeature 2), §5–7 (data model, relationships, API), §9 (audit trail), §17 (RF-02 name). This file restructures that plan into TDD tasks.

## Rulings applied (decided in-session, binding for all tasks)

1. **Backend-only.** The repo has no frontend (no package.json, no node tooling; RF-01 Ruling 1, user-confirmed). RF-02 plan Steps 9–11 (Risk Detail integration, Assessment UI, Review UI) and §§35–38 UX are deferred until a frontend exists. "Risk detail shows assessments" is satisfied at the API level by `GET /risk-assessments?risk_id=…` plus the assessment detail payload. Confirmed by user.
2. **`organization_id` → `tenant_id`.** Auditra's tenancy column is `tenant_id` (UUID); services take `tenant_id` in the constructor; routes use `settings.default_tenant_id` (RF-01 Ruling 2).
3. **Assessor/reviewer are free-text strings, not user FKs.** No User/Team/Organization model exists anywhere (RF-01 Ruling 3), so RF-02 plan §20/§24 identity rules ("assessor belongs to organization", "reviewer_id = current_user") cannot be enforced; `RiskAssessment.assessor` and `.reviewer` are optional trimmed strings (max 255), like `Risk.owner`. Role-based permissions (plan §21) are deferred with the auth system; API-layer authorization = tenant isolation + state rules, exactly as RF-01 shipped. Cross-tenant *risk* and *template* validation still applies (FKs exist).
4. **Templates: combined template+version table (plan §41 allows it), with an immutable `key` for seed idempotency.** `assessment_templates` carries `version` (int, default 1) and `key` (unique per tenant, e.g. `general-ai-risk-assessment`); each assessment snapshots `template_version` at creation (plan §11). Sections/questions hang off `template_id` in MVP; question-authoring endpoints are out of scope (plan §22 MVP: backend template CRUD + seed; full template builder later). PATCH edits metadata only and never bumps `version`. Cost if wrong: a future template-builder adds version rows and re-points sections.
5. **System template is seeded per tenant** (RF-01 convention: `seed_risk_categories(db, tenant_id)`), satisfying plan §43 "system templates can be shared across tenants if the architecture supports this" — it doesn't; per-tenant rows do.
6. **Status machine is strict (plan §40/§5):** `draft → in_progress → submitted → under_review → approved | rejected`; `rejected → revision` creates a NEW assessment; `approved` is immutable. Submit is allowed from `draft` **or** `in_progress` (both are "editable" per §29; a draft with every required answer saved must submit). `approve`/`reject` require `under_review`, reached via `POST /start-review` — plan §30 mandates SUBMITTED→UNDER_REVIEW "when the reviewer opens" but §47's endpoint list has no route for it; the extra endpoint is the faithful implementation. Invalid transitions → 409 `ASSESSMENT_INVALID_STATE` with a clear message (§40).
7. **Revision copies responses.** `POST /revision` (only from `rejected`) creates a new DRAFT assessment with `original_assessment_id` = immediate predecessor, `revision_number` = predecessor + 1, same risk/template/template_version/assessment_type/assessor, and copies the predecessor's responses so the assessor revises instead of restarting. The predecessor row is never mutated (§32/§33).
8. **Completion percentage is derived on read, not persisted** (§28): `answered required / total required × 100` rounded to int; a template with zero required questions reports 100. It is a progress metric only — never a score (RF-03 boundary, §50).
9. **Response validation (plan §18):** `response_value` is `JSON().with_variant(JSONB, "postgresql")`; the service validates against the question's `question_type`: TEXT str ≤ 1000, LONG_TEXT str ≤ 10000, SINGLE_SELECT str ∈ option values, MULTI_SELECT list[str] ⊆ option values, BOOLEAN strict bool (not int), NUMBER int|float excluding bool. "Answered" for submit = row exists with a non-empty value (non-blank str, non-empty list, bool may be false, number may be 0). PATCH to a response is an upsert; response rows are only writable while the assessment is editable (§26/§27).
10. **No response clearing in MVP:** `response` in the PATCH body must be a value, not null (plan never specifies clearing; adds delete semantics later).
11. **Approve/reject take optional bodies:** `{reviewer?: str}` for approve, `{reason: str (required), reviewer?: str}` for reject (§32 mandates a reason). With no auth there is no `current_user`; reviewer is recorded when supplied (§3 ruling).
12. **Assessment list `search`** covers assessor and reviewer (ilike OR); `sort_by` whitelist = `created_at`, `updated_at`, `status` (default `updated_at` desc). `asset_id` filter joins through `risks.asset_id` (§9/§34 — asset is derived, never stored on the assessment).
13. **Archived risks reject new assessments** with 409 `RISK_ARCHIVED` (RF-01 §20 semantics carry over); existing assessments of a risk remain readable.
14. **Audit events (plan §44 + RF-01 precedent):** add `assessment.created`, `assessment.started` (first DRAFT→IN_PROGRESS), `assessment.response_updated`, `assessment.submitted`, `assessment.review_started`, `assessment.approved`, `assessment.rejected`, `assessment.revised`, plus `assessment_template.created`/`assessment_template.updated` (template management is governance-relevant; RF-01 audits taxonomy changes the same way). `resource_type` = `assessment` / `assessment_template`. One event per governance mutation, same transaction, single commit (RF-01 pattern); autosave flooding is a frontend concern that is deferred with the UI (§44).
15. **Out-of-scope guard:** no scoring, likelihood/impact, heatmaps, treatments, LLM/AI generation, conditional rules engine (`conditional_logic` column reserved-by-absence: plan §16 says "optional / future" — omit the column entirely). Schemas are `extra="forbid"` so out-of-scope fields cannot sneak in (RF-01 convention).

## Global Constraints

- Every assessment/template query is tenant-scoped: `tenant_id == settings.default_tenant_id` at the API layer, explicit `tenant_id` parameter in repository/service (spec §43).
- Unknown and cross-tenant risk/template ids are indistinguishable 404s (no existence leak).
- No physical deletes; assessments are never deleted (history is the product, §8); templates disabled via `is_active` only.
- Out of scope (never build): scoring, treatment, controls, requirements, evidence, findings, remediation, AI-generated assessments (plan §4).
- Migration `0016` must be reversible (upgrade + downgrade) and carry FKs, check constraints, unique constraints, and the indexes listed in Task 2.
- The entire existing test suite must stay green after every task.
- `uv run ruff check .` clean; format only files this plan touches.
- Commit on branch `feature/rf02-ai-risk-assessment`; push to `origin` only after Task 7 passes; open a PR against `main` (RF-01 precedent; user instructed "push the code to github once the implementation is done").

## Review Focus

1. **State machine** — every transition pair: valid ones succeed with correct timestamps; invalid ones (edit approved, submit submitted, approve draft, reject without under_review, revise non-rejected, start-review on draft) return 409 `ASSESSMENT_INVALID_STATE`, never 500. Pinned by Task 5 transition tests.
2. **Tenant isolation** — risk, template, assessment from tenant B invisible/unusable from tenant A: 404 on get, rejected on create reference. Pinned by Task 5 cross-tenant tests.
3. **Response validation** — wrong-type values (string for NUMBER, int for BOOLEAN, value not in options, oversize text) rejected with 400 `ASSESSMENT_RESPONSE_INVALID`; question not in the assessment's template rejected with 404. Pinned by Task 5 response tests.
4. **Submission completeness** — submit with any unanswered required question → 409 `ASSESSMENT_INCOMPLETE`; completion % = answered required / total required only (never counts optional questions in the denominator). Pinned by Task 5.
5. **Immutability + history** — approved assessment rejects edits; a second assessment on the same risk is a new row (Risk 1—N); revision creates a new row with copied responses and leaves the rejected original untouched; template PATCH never changes a snapshot's `template_version`. Pinned by Tasks 5–7.
6. **Seed idempotency** — `seed_assessment_templates` twice yields exactly one template with 7 sections and its questions; unique `(tenant_id, key)` backs it; seeded question types/options match the plan §23 content. Pinned by Task 3.
7. **Audit trail** — created/started/response_updated/submitted/review_started/approved/rejected/revised each write exactly one audit event in the same transaction with `resource_type="assessment"`. Pinned by Task 5.
8. **Submission & approval authorization surface** — without an auth system, the guard rails are tenant isolation + state rules + required rejection reason; reason-less reject → 422/400, never a silent empty reason. Pinned by Tasks 5–6.

---

### Task 1: Architecture inspection, plan + spec commit, green baseline

**Files:**
- Create: `docs/superpowers/plans/2026-11-17-rf02-ai-risk-assessment.md` (this file)
- No production code changes.

**Interfaces:**
- Consumes: repo conventions (documented in this plan's Rulings).
- Produces: committed plan; verified baseline; ledger initialized.

- [x] **Step 1: Verify workspace state**

Run `git status`, `git branch --show-current`. Expected: branch `feature/rf02-ai-risk-assessment`, only this plan file untracked.

- [x] **Step 2: Install dependencies**

Run `uv sync` (with `UV_LINK_MODE=copy`) from `backend/`. Expected: exits 0.

- [x] **Step 3: Run the baseline suite**

Run `uv run pytest -q` from `backend/`. Expected: all existing tests pass (PostgreSQL reachable on localhost:5433). If failures pre-exist, stop and report.

- [x] **Step 4: Commit plan**

Commit this plan file as `docs(risk): RF-02 AI risk assessment plan`. Expected: clean `git status` except future work.

- [x] **Step 5: Initialize ledger**

Run `sdd-workspace` for this plan; create `progress.md` with first line `# SDD ledger — plan: docs/superpowers/plans/2026-11-17-rf02-ai-risk-assessment.md`; write pre-flight rows (shared interfaces: Task 2 models/enums/errors → Task 3 seed → Task 4 schemas → Task 5 service → Task 6 routes → Task 7 e2e; no conflicts found — names fixed by this plan's Interfaces blocks).

---

### Task 2: Assessment domain foundation (enums, errors, models, migration 0016, conftest)

**Files:**
- Create: `backend/app/modules/assessment/__init__.py`
- Create: `backend/app/modules/assessment/enums.py`
- Create: `backend/app/modules/assessment/errors.py`
- Create: `backend/app/modules/assessment/models.py`
- Create: `backend/alembic/versions/0016_ai_risk_assessment.py`
- Modify: `backend/tests/conftest.py` (TRUNCATE list: add `assessment_responses, risk_assessments, assessment_questions, assessment_sections, assessment_templates`)
- Modify: `backend/tests/integration/test_migration.py`
- Test: `backend/tests/unit/test_assessment_models.py` (new)

**Interfaces:**
- Consumes: `app.core.db.Base`; `risks.id` (migration `0015` is current head); `DomainError` from `app.modules.model_inventory.domain.errors`.
- Produces (relied on by later tasks):
  - `AssessmentStatus(StrEnum)`: `DRAFT="draft"`, `IN_PROGRESS="in_progress"`, `SUBMITTED="submitted"`, `UNDER_REVIEW="under_review"`, `APPROVED="approved"`, `REJECTED="rejected"`.
  - `AssessmentType(StrEnum)`: `INITIAL="initial"`, `PRE_DEPLOYMENT="pre_deployment"`, `PERIODIC="periodic"`, `CHANGE="change"`, `INCIDENT_TRIGGERED="incident_triggered"`, `VENDOR="vendor"`, `OTHER="other"`.
  - `QuestionType(StrEnum)`: `TEXT="text"`, `LONG_TEXT="long_text"`, `SINGLE_SELECT="single_select"`, `MULTI_SELECT="multi_select"`, `BOOLEAN="boolean"`, `NUMBER="number"`.
  - Errors (all subclass `DomainError`): `AssessmentError` (ASSESSMENT_ERROR, 400), `AssessmentNotFoundError` (ASSESSMENT_NOT_FOUND, 404), `AssessmentQuestionNotFoundError` (ASSESSMENT_QUESTION_NOT_FOUND, 404), `AssessmentInvalidStateError` (ASSESSMENT_INVALID_STATE, 409), `AssessmentIncompleteError` (ASSESSMENT_INCOMPLETE, 409), `InvalidAssessmentResponseError` (ASSESSMENT_RESPONSE_INVALID, 400), `InvalidAssessmentQueryError` (ASSESSMENT_QUERY_INVALID, 400), `AssessmentTemplateError` (ASSESSMENT_TEMPLATE_ERROR, 400), `AssessmentTemplateNotFoundError` (ASSESSMENT_TEMPLATE_NOT_FOUND, 404), `AssessmentTemplateInactiveError` (ASSESSMENT_TEMPLATE_INACTIVE, 409), `DuplicateAssessmentTemplateError` (ASSESSMENT_TEMPLATE_DUPLICATE, 409), `InvalidAssessmentTemplateQueryError` (ASSESSMENT_TEMPLATE_QUERY_INVALID, 400).
  - `models.AssessmentTemplate` — table `assessment_templates`: `id`, `tenant_id`, `key` (String 100), `name` (String 255), `description` (Text, nullable), `assessment_type` (String 32), `version` (int, default 1, server_default 1), `is_system` (bool, default false), `is_active` (bool, default true), `created_at`, `updated_at`. Constraints: `uq_assessment_templates_tenant_key` UNIQUE(tenant_id, key); check `ck_assessment_templates_type` type IN (7 values); check `ck_assessment_templates_version` version >= 1; index `ix_assessment_templates_tenant_active` (tenant_id, is_active).
  - `models.AssessmentSection` — table `assessment_sections`: `id`, `template_id` (FK assessment_templates.id, RESTRICT), `title` (String 255), `description` (Text, nullable), `display_order` (int), `is_active` (bool, default true), `created_at`, `updated_at`. Index `ix_assessment_sections_template_order` (template_id, display_order).
  - `models.AssessmentQuestion` — table `assessment_questions`: `id`, `section_id` (FK assessment_sections.id, RESTRICT), `key` (String 100), `question_text` (Text), `description` (Text, nullable), `question_type` (String 32), `required` (bool, default false), `display_order` (int), `options` (JSON().with_variant(JSONB,"postgresql"), nullable), `validation_rules` (JSON variant, nullable), `help_text` (Text, nullable), `is_active` (bool, default true), `created_at`, `updated_at`. Constraints: `uq_assessment_questions_section_key` UNIQUE(section_id, key); check `ck_assessment_questions_type` type IN (6 values); index `ix_assessment_questions_section_order` (section_id, display_order).
  - `models.RiskAssessment` — table `risk_assessments`: `id`, `tenant_id`, `risk_id` (FK risks.id, RESTRICT), `template_id` (FK assessment_templates.id, RESTRICT), `template_version` (int, default 1), `assessment_type` (String 32), `status` (String 32, default 'draft'), `assessor` (String 255, nullable), `reviewer` (String 255, nullable), `submitted_at`, `reviewed_at`, `approved_at`, `rejected_at` (DateTime tz, all nullable), `rejection_reason` (Text, nullable), `revision_number` (int, default 1), `original_assessment_id` (FK risk_assessments.id, RESTRICT, nullable), `created_at`, `updated_at`. Checks: `ck_risk_assessments_status` status IN (6 values); `ck_risk_assessments_type` type IN (7 values); `ck_risk_assessments_revision` revision_number >= 1. Indexes: `ix_risk_assessments_tenant_risk` (tenant_id, risk_id), `ix_risk_assessments_tenant_status` (tenant_id, status), `ix_risk_assessments_tenant_template` (tenant_id, template_id), `ix_risk_assessments_tenant_assessor` (tenant_id, assessor), `ix_risk_assessments_tenant_reviewer` (tenant_id, reviewer), `ix_risk_assessments_tenant_created` (tenant_id, created_at), `ix_risk_assessments_tenant_type` (tenant_id, assessment_type), `ix_risk_assessments_original` (original_assessment_id).
  - `models.AssessmentResponse` — table `assessment_responses`: `id`, `assessment_id` (FK risk_assessments.id, RESTRICT), `question_id` (FK assessment_questions.id, RESTRICT), `response_value` (JSON().with_variant(JSONB,"postgresql")), `answered_by` (String 255, nullable), `created_at`, `updated_at`. Constraints: `uq_assessment_responses_assessment_question` UNIQUE(assessment_id, question_id); index on question_id (`ix_assessment_responses_question`).
  - Migration revision `"0016"`, `down_revision="0015"`, all five tables + constraints/indexes, reversible downgrade.
  - conftest TRUNCATE list includes the five new tables (children before parents).

- [x] **Step 1: Write the failing tests**

`backend/tests/unit/test_assessment_models.py`:
- `test_assessment_status_values` — `AssessmentStatus` members equal exactly `{draft, in_progress, submitted, under_review, approved, rejected}`.
- `test_assessment_type_values` — `AssessmentType` equals the 7 spec values (§6).
- `test_question_type_values` — `QuestionType` equals the 6 spec values (§14).
- `test_assessment_table_shape` — `RiskAssessment.__tablename__ == "risk_assessments"`; required columns present (`risk_id`, `template_id`, `template_version`, `assessment_type`, `status`, `assessor`, `reviewer`, `submitted_at`, `approved_at`, `rejection_reason`, `revision_number`, `original_assessment_id`); `AssessmentTemplate.__tablename__ == "assessment_templates"`; `AssessmentSection`, `AssessmentQuestion`, `AssessmentResponse` table names correct.
- `test_assessment_check_constraints` — inspect `RiskAssessment.__table__.constraints` contains the status/type/revision check constraints with expected SQL fragments; `AssessmentQuestion` has the question_type check; `AssessmentResponse` has the `(assessment_id, question_id)` unique constraint.

Extend `backend/tests/integration/test_migration.py` with `test_assessment_tables_exist` — reflects all five tables via `sa.inspect`, asserting columns (`template_version`, `response_value`, `question_type`, `display_order`) and FKs to `risks`, `assessment_templates`, `assessment_sections`, `risk_assessments`, `assessment_questions`.

- [x] **Step 2: Run and watch them fail**

Run `uv run pytest tests/unit/test_assessment_models.py tests/integration/test_migration.py -q`. Expected: FAIL/ERROR — `ModuleNotFoundError: app.modules.assessment` (feature missing, not typo).

- [x] **Step 3: Implement**

Create `__init__.py`, `enums.py`, `errors.py`, `models.py` per Interfaces; write migration `0016_ai_risk_assessment.py` (explicit `op.create_table` style matching `0015`); add the five tables to conftest TRUNCATE.

- [x] **Step 4: Run and watch them pass**

Run `uv run pytest tests/unit/test_assessment_models.py tests/integration/test_migration.py -q`. Expected: PASS. Then run the full suite `uv run pytest -q` — expected all green (conftest rebuilds the test DB from migrations each session).

- [x] **Step 5: Lint + commit**

`uv run ruff check .` clean; `uv run ruff format` on touched files. Commit: `feat(assessment): assessment domain models, enums, errors and migration 0016 (RF-02)`.

---

### Task 3: Idempotent system assessment-template seed

**Files:**
- Create: `backend/app/modules/assessment/seed.py`
- Modify: `backend/app/seed.py` (call `seed_assessment_templates` for the default tenant in `__main__`)
- Modify: `backend/tests/conftest.py` (`client` fixture seeds templates like it seeds risk categories)
- Test: `backend/tests/integration/test_assessment_seed.py` (new)

**Interfaces:**
- Consumes: models from Task 2, `settings.default_tenant_id`.
- Produces:
  - `GENERAL_AI_RISK_ASSESSMENT_KEY = "general-ai-risk-assessment"`.
  - `seed_assessment_templates(db: Session, tenant_id: UUID) -> int` — inserts the system template if `(tenant_id, key)` is absent, with its sections and questions (`is_system=True`, `is_active=True`, `version=1`, `assessment_type="initial"`), flushes, returns 1 when created / 0 when already present. Idempotent by construction and by the unique constraint.
  - Content: template name `General AI Risk Assessment`, description per plan §23, **7 sections** in order: `1. System Purpose & Context`, `2. Data & Privacy`, `3. Stakeholder Impact`, `4. Human Oversight`, `5. Safety & Reliability`, `6. Security & Robustness`, `7. Governance & Accountability`.
  - Questions (keys unique per section; mix of all six types so every type is exercised):
    - `purpose` (TEXT, required): "What is the intended purpose of the AI system?" — help_text present.
    - `data-types` (MULTI_SELECT, required, options: training_data/training, personal_data/Personal data, sensitive_data/Sensitive data, publicly_available/Publicly available, third_party/Third-party data): "What types of data does the system process?"
    - `processes_personal_data` (BOOLEAN, required): "Does the system process personal or sensitive information?"
    - `affected-stakeholders` (LONG_TEXT, required): "Who is affected by the system's outputs?"
    - `influenced-decisions` (LONG_TEXT, optional): "What decisions are influenced by the system?"
    - `human-oversight` (BOOLEAN, required): "Is human oversight available for consequential decisions?"
    - `oversight-mechanism` (TEXT, optional): "Describe the human oversight mechanism."
    - `incorrect-output` (LONG_TEXT, required): "What happens when the system produces an incorrect output?"
    - `security-threats` (MULTI_SELECT, optional, options: adversarial_attacks/Adversarial attacks, data_poisoning/Data poisoning, model_extraction/Model extraction, unauthorized_access/Unauthorized access, other/Other): "What are the main security threats associated with this system?"
    - `impact-severity` (SINGLE_SELECT, required, options: none/None, minor/Minor, moderate/Moderate, significant/Significant, severe/Severe): "What is the potential severity of harm if the system fails?"
    - `misuse-potential` (BOOLEAN, required): "Can the system be misused or abused?"
    - `regulatory-scope` (SINGLE_SELECT, optional, options: not_applicable/Not applicable, uncertain/Uncertain, applicable/Applicable): "Is the system subject to regulatory requirements?"
    - `accountable-owner` (TEXT, required): "Who is accountable for the system's operation?"
    - `governance-review` (NUMBER, optional, validation_rules {"min": 0, "max": 100}): "How many governance reviews has the system undergone in the last 12 months?"
  - Every question: `display_order` sequential within its section; optional `description`/`help_text` where noted; `options` as `[{"value": ..., "label": ...}, ...]` for select questions (plan §15), `None` otherwise; `validation_rules` only on `governance-review`.

- [x] **Step 1: Write the failing tests**

`tests/integration/test_assessment_seed.py`:
- `test_seed_creates_general_template_with_sections_and_questions` — call once: returns 1; template exists with `key`, `is_system=True`, `version=1`, `assessment_type="initial"`; exactly 7 sections ordered by `display_order`; question count matches the content list; every `QuestionType` value appears at least once; select questions carry options with `value`/`label` keys; required flags match content.
- `test_seed_is_idempotent` — call twice: second returns 0; still exactly 1 template / 7 sections / same question count.
- `test_seed_scopes_by_tenant` — seed for a second tenant id: that tenant gets its own template; default tenant unaffected.

- [x] **Step 2: Run and watch them fail**

Run `uv run pytest tests/integration/test_assessment_seed.py -q`. Expected: ERROR — `ModuleNotFoundError: app.modules.assessment.seed`.

- [x] **Step 3: Implement**

`app/modules/assessment/seed.py` with the content constants + `seed_assessment_templates`; wire into `app/seed.py` `__main__` block after `seed_risk_categories` (tenant-scoped, not inside `seed_reference_data`); add template seeding to the conftest `client` fixture.

- [x] **Step 4: Run and watch them pass**

`uv run pytest tests/integration/test_assessment_seed.py -q` → PASS. Full suite `uv run pytest -q` → green.

- [x] **Step 5: Lint + commit**

ruff clean. Commit: `feat(assessment): idempotent General AI Risk Assessment template seed (RF-02)`.

---

### Task 4: Pydantic schemas (templates + assessments + responses)

**Files:**
- Create: `backend/app/modules/assessment/schemas.py`
- Test: `backend/tests/unit/test_assessment_schemas.py` (new)

**Interfaces:**
- Consumes: enums from Task 2.
- Produces (relied on by Tasks 5–7) — all `extra="forbid"`:
  - `AssessmentTemplateRef`: `id`, `name`, `version`, `assessment_type`.
  - `AssessmentSectionResponse`: `id`, `title`, `description`, `display_order`, `is_active`.
  - `AssessmentQuestionResponse`: `id`, `section_id`, `key`, `question_text`, `description`, `question_type`, `required`, `display_order`, `options`, `validation_rules`, `help_text`, `is_active`.
  - `AssessmentQuestionOption` shape validated by `options: list[dict]` entries carrying `value`+`label` (validated in seed, not enforced by schema — options are template data).
  - `AssessmentTemplateCreate` (extra="forbid"): `name` (strip, 1..255, required), `description` optional (max 10000), `assessment_type` default `INITIAL`, `is_active` default True; `key` optional (pattern `^[a-z0-9]+(-[a-z0-9]+)*$`, max 100; auto-derived from name when absent, mirroring RF-01 slugify).
  - `AssessmentTemplateUpdate` (extra="forbid"): `name`, `description`, `assessment_type`, `is_active` all optional; never touches `version`/`key` (Ruling 4).
  - `AssessmentTemplateResponse`: `id`, `tenant_id`, `key`, `name`, `description`, `assessment_type`, `version`, `is_system`, `is_active`, `created_at`, `updated_at` (+ tz validator).
  - `AssessmentTemplateDetailResponse`: template fields + `sections: list[AssessmentSectionResponse]` each carrying nested `questions: list[AssessmentQuestionResponse]`.
  - `AssessmentTemplateListResponse`: `items, page, page_size, total, total_pages`.
  - `AssessmentTemplateFilter` (extra="forbid"): `page`, `page_size` (le 100), `search` (max 255), `is_active`, `is_system`, `assessment_type` optional, `sort_by` whitelist (`name`, `created_at`, `updated_at`), `sort_order` default `asc`.
  - `RiskAssessmentCreate` (extra="forbid"): `risk_id` UUID required, `template_id` UUID required, `assessment_type` default `INITIAL`, `assessor` optional (strip, max 255; blank → None).
  - `AssessmentResponseUpdate` (extra="forbid"): `response` required — `Any` value validated against the question type in the service (Task 5), never null (Ruling 10); `answered_by` optional (strip, max 255).
  - `AssessmentRejectRequest` (extra="forbid"): `reason` required (strip, min 1, max 10000), `reviewer` optional (strip, max 255, blank → None).
  - `AssessmentApproveRequest` (extra="forbid"): `reviewer` optional (strip, max 255, blank → None).
  - `AssessmentRevisionRequest` (extra="forbid"): empty body allowed (`model_config` default) — reserved for future notes; no required fields.
  - `AssessmentAnswerState` (embedded in detail): `value`, `answered_by`, `updated_at` (tz-aware).
  - `RiskAssessmentDetailResponse`: `id`, `tenant_id`, `risk_id`, `risk: AssessmentRiskRef` (`id`, `title`, `asset_id`, `asset: {id,name} | None`), `template: AssessmentTemplateRef`, `template_version`, `assessment_type`, `status`, `assessor`, `reviewer`, `revision_number`, `original_assessment_id`, `submitted_at`, `reviewed_at`, `approved_at`, `rejected_at`, `rejection_reason`, `completion_percentage`, `created_at`, `updated_at`, `sections: list[AssessmentSectionDetail]` (`id`, `title`, `description`, `display_order`, `questions: list[AssessmentQuestionDetail]` — question fields + `response: AssessmentAnswerState | None`).
  - `RiskAssessmentListItem`: `id`, `risk_id`, `risk: {id, title}`, `template: AssessmentTemplateRef`, `template_version`, `assessment_type`, `status`, `assessor`, `reviewer`, `revision_number`, `original_assessment_id`, `completion_percentage`, `submitted_at`, `approved_at`, `created_at`, `updated_at`.
  - `RiskAssessmentListResponse`: `items, page, page_size, total, total_pages`.
  - `RiskAssessmentFilter` (extra="forbid"): `page`, `page_size` (le 100), `search` (max 255), `risk_id`, `asset_id`, `template_id`, `assessment_type`, `status`, `assessor`, `reviewer`, `created_from`, `created_to` (datetime, tz-aware; validator: from ≤ to), `sort_by` whitelist (`created_at`, `updated_at`, `status`), `sort_order` default `desc`.

- [x] **Step 1: Write the failing tests**

`tests/unit/test_assessment_schemas.py`:
- `test_template_create_strips_name_and_autosizes_key` (name " General AI Risk Assessment " → key `general-ai-risk-assessment`)
- `test_template_create_rejects_blank_name`
- `test_template_create_rejects_bad_key` ("Bad Key!" → ValidationError)
- `test_template_create_rejects_extra_fields` (`sections: []` → ValidationError — authoring is out of scope)
- `test_template_update_accepts_partial_payload` and `test_template_update_rejects_extra_fields` (`version: 2` → ValidationError — version is not PATCHable, Ruling 4)
- `test_assessment_create_requires_risk_and_template`
- `test_assessment_create_strips_assessor` (blank → None)
- `test_assessment_create_rejects_unknown_assessment_type` (bogus enum → ValidationError)
- `test_assessment_create_rejects_extra_fields` (`score: 10` → ValidationError — RF-03 boundary)
- `test_response_update_requires_response_value` (missing/None → ValidationError, Ruling 10)
- `test_reject_request_requires_non_blank_reason` (blank → ValidationError, plan §32)
- `test_reject_request_strips_reason_and_reviewer`
- `test_approve_request_accepts_empty_body`
- `test_filter_defaults` (page 1, page_size 25, sort_by updated_at, sort_order desc)
- `test_filter_rejects_inverted_date_range` (created_from > created_to → ValidationError)
- `test_filter_rejects_unknown_sort_field` is a service-level concern — schema only whitelists via literal? (schema keeps `sort_by: str`; whitelist enforced in repository → covered in Task 5)

- [x] **Step 2: Run and watch them fail**

Run `uv run pytest tests/unit/test_assessment_schemas.py -q`. Expected: ERROR — `ModuleNotFoundError: app.modules.assessment.schemas`.

- [x] **Step 3: Implement**

`schemas.py` per Interfaces (slugify helper reuses the RF-01 regex approach; tz-aware timestamp validators mirror `risk/schemas.py`).

- [x] **Step 4: Run and watch them pass**

`uv run pytest tests/unit/test_assessment_schemas.py -q` → PASS. Full suite → green.

- [x] **Step 5: Lint + commit**

ruff clean. Commit: `feat(assessment): assessment and template schemas with validation (RF-02)`.

---

### Task 5: Repository + service layer (state machine, validation, tenant isolation, audit)

**Files:**
- Create: `backend/app/modules/assessment/repository.py`
- Create: `backend/app/modules/assessment/service.py`
- Modify: `backend/app/modules/audit/enums.py` (add the 10 assessment event types, Ruling 14)
- Test: `backend/tests/integration/test_assessment_service.py` (new)
- Test: `backend/tests/integration/test_assessment_template_service.py` (new)

**Interfaces:**
- Consumes: models/enums/errors/schemas (Tasks 2–4), `AuditService.record_event`, `Risk`/`RiskCategory`/`DiscoveredAsset` models, `RiskArchivedError` + `RiskNotFoundError` from `risk.errors` (Ruling 13), repository patterns from `risk/repository.py`.
- Produces (relied on by Task 6):
  - `AssessmentTemplateService(db, tenant_id, request_id=None, actor=None, source="api")`:
    - `create(payload: AssessmentTemplateCreate) -> AssessmentTemplate` — key uniqueness per tenant (existing → `DuplicateAssessmentTemplateError` 409); `is_system=False`; `version=1`; audit `assessment_template.created`; single commit.
    - `get(template_id) -> AssessmentTemplate` — tenant-scoped 404 (cross-tenant indistinguishable).
    - `get_detail(template_id) -> tuple[AssessmentTemplate, list[AssessmentSection], dict[UUID, list[AssessmentQuestion]]]` — sections ordered, questions grouped by section, in at most 3 queries.
    - `list(filters) -> tuple[list, int]` — tenant-scoped, search on name, filters is_active/is_system/assessment_type, sort whitelist (`InvalidAssessmentTemplateQueryError` 400 on unknown sort).
    - `update(template_id, payload: AssessmentTemplateUpdate) -> AssessmentTemplate` — 404 unknown/cross-tenant; applies only non-None fields; never touches `key`/`version`; audit `assessment_template.updated` with changed_fields; single commit.
  - `AssessmentService(db, tenant_id, request_id=None, actor=None, source="api")`:
    - `create(payload: RiskAssessmentCreate) -> RiskAssessment` — validates: risk exists in tenant (else `RiskNotFoundError` 404), risk not archived (else `RiskArchivedError` 409), template exists in tenant (else `AssessmentTemplateNotFoundError` 404), template active (else `AssessmentTemplateInactiveError` 409); snapshots `template.version` into `template_version`; status `draft`, `revision_number=1`; audit `assessment.created` (after_state); single commit.
    - `get(assessment_id) -> RiskAssessment` — tenant-scoped 404.
    - `list(filters) -> tuple[list[RiskAssessment], int]` — filters compose: tenant, risk_id, template_id, assessment_type, status, assessor (exact), reviewer (exact), `asset_id` via joined `risks.asset_id`, created_from/created_to on `created_at`; `search` OR ilike on assessor/reviewer; sort whitelist (`InvalidAssessmentQueryError` 400); pagination + total.
    - `get_detail(assessment_id) -> AssessmentDetailView` (dataclass or tuple) — assessment + risk + asset + template ref + sections/questions (active only? **no — all questions**, so historical rendering is stable) + responses keyed by question_id + derived `completion_percentage`; at most 6 queries (assessment, risk, asset, template, sections, questions, responses).
    - `save_response(assessment_id, question_id, payload: AssessmentResponseUpdate) -> AssessmentResponse` — state guard: status ∈ {draft, in_progress} else `AssessmentInvalidStateError` (message names the current status, e.g. "Cannot edit an approved assessment."); question must belong to the assessment's template sections (else `AssessmentQuestionNotFoundError` 404); validates `payload.response` against the question type per Ruling 9 (else `InvalidAssessmentResponseError`); upsert by `(assessment_id, question_id)`; if status was `draft` → transition to `in_progress`, set `updated_at`, audit `assessment.started` (before/after state) — then audit `assessment.response_updated` with `changed_fields=[question_id key]`... **use `metadata={"question_key": ...}`** and `changed_fields=["responses"]`; single commit.
    - `submit(assessment_id) -> RiskAssessment` — status ∈ {draft, in_progress} else `AssessmentInvalidStateError`; all required questions answered else `AssessmentIncompleteError` (message lists up to 5 missing question keys); sets `status=submitted`, `submitted_at=now`; audit `assessment.submitted`; single commit.
    - `start_review(assessment_id) -> RiskAssessment` — status == `submitted` else `AssessmentInvalidStateError`; sets `status=under_review`; audit `assessment.review_started`; single commit.
    - `approve(assessment_id, payload: AssessmentApproveRequest | None) -> RiskAssessment` — status == `under_review` else 409; sets `status=approved`, `reviewer=payload.reviewer` when supplied (else keep existing), `reviewed_at=now`, `approved_at=now`; audit `assessment.approved`; single commit. Post-approval every mutating method raises 409 (immutability).
    - `reject(assessment_id, payload: AssessmentRejectRequest) -> RiskAssessment` — status == `under_review` else 409; sets `status=rejected`, `rejection_reason=payload.reason`, `reviewer` when supplied, `rejected_at=now`; audit `assessment.rejected`; single commit.
    - `create_revision(assessment_id) -> RiskAssessment` — status == `rejected` else 409; inserts a NEW row (same tenant/risk/template/template_version/assessment_type/assessor, `original_assessment_id`=predecessor.id, `revision_number`=predecessor+1, status `draft`, all timestamps None); copies the predecessor's responses row-for-row (new ids, same question/value/answered_by); audit `assessment.revised` on the NEW assessment with metadata `{"original_assessment_id": ...}`; single commit.
    - `completion_percentage(assessment, questions, responses) -> int` — static/helper per Ruling 8.
    - `resolve_refs(assessments) -> tuple[dict[UUID, Risk], dict[UUID, DiscoveredAsset], dict[UUID, AssessmentTemplate]]` — batch lookup for list responses (at most 3 queries).
  - Audit: `AuditEventType` gains `ASSESSMENT_CREATED/STARTED/RESPONSE_UPDATED/SUBMITTED/REVIEW_STARTED/APPROVED/REJECTED/REVISED` + `ASSESSMENT_TEMPLATE_CREATED/UPDATED` (values `assessment.created` … `assessment_template.updated`; all match `EVENT_TYPE_PATTERN`).

- [x] **Step 1: Write the failing tests**

`tests/integration/test_assessment_template_service.py` (fixture: seed templates):
- `test_create_template_persists_and_derives_key`
- `test_create_duplicate_key_rejected` (409)
- `test_get_template_from_other_tenant_returns_404`
- `test_update_template_changes_name_and_is_active` (+ audit `assessment_template.updated`, changed_fields)
- `test_update_template_cannot_change_version_or_key` (`version=2`/`key=` rejected at schema layer — assert service leaves them untouched when only name changes)
- `test_list_templates_search_and_pagination`
- `test_list_templates_invalid_sort_returns_400`

`tests/integration/test_assessment_service.py` (fixture: seed templates, create discovered asset + risk for default tenant; create second tenant with its own risk/template):
- `test_create_assessment_persists_and_snapshots_template_version` (audit `assessment.created`, resource_type `assessment`)
- `test_create_assessment_with_unknown_risk_returns_404`
- `test_create_assessment_with_cross_tenant_risk_returns_404`
- `test_create_assessment_with_unknown_template_returns_404`
- `test_create_assessment_with_cross_tenant_template_returns_404`
- `test_create_assessment_with_inactive_template_returns_409`
- `test_create_assessment_on_archived_risk_returns_409`
- `test_get_assessment_from_other_tenant_returns_404`
- `test_save_response_moves_draft_to_in_progress` (audit `assessment.started` + `assessment.response_updated`)
- `test_save_response_upserts_and_bumps_updated_at`
- `test_save_response_with_wrong_type_rejected` (string for NUMBER → 400 `ASSESSMENT_RESPONSE_INVALID`; int for BOOLEAN → 400; value not in select options → 400; oversize TEXT → 400)
- `test_save_response_for_foreign_question_returns_404` (question from another template)
- `test_save_response_on_submitted_assessment_returns_409`
- `test_save_response_on_approved_assessment_returns_409` (immutability)
- `test_submit_requires_all_required_questions` (missing → 409 `ASSESSMENT_INCOMPLETE`, message names a missing key)
- `test_submit_sets_submitted_timestamp_and_status` (audit `assessment.submitted`)
- `test_submit_allowed_from_draft_when_complete`
- `test_completion_percentage_counts_required_only` (e.g. 5 of 10 required answered → 50 regardless of optional answers)
- `test_start_review_transitions_submitted_to_under_review` (audit `assessment.review_started`)
- `test_start_review_on_draft_returns_409`
- `test_approve_requires_under_review` (from submitted → 409; from draft → 409)
- `test_approve_sets_reviewer_and_timestamps_and_is_immutable` (audit `assessment.approved`; subsequent save_response → 409; subsequent submit → 409)
- `test_reject_requires_reason` (schema-level blank reason — covered in unit tests; service asserts reason persisted)
- `test_reject_sets_reason_and_timestamp` (audit `assessment.rejected`)
- `test_reject_without_under_review_returns_409`
- `test_create_revision_from_rejected_copies_responses` (new id, status draft, revision_number 2, original_assessment_id set, responses copied; predecessor untouched — still `rejected` with its own timestamps; audit `assessment.revised`)
- `test_create_revision_from_approved_returns_409`
- `test_list_assessments_filters_by_risk_status_type_assessor` (+ asset_id filter via risk join)
- `test_list_assessments_search_covers_assessor_and_reviewer`
- `test_list_assessments_created_range_filter`
- `test_list_assessments_invalid_sort_returns_400`
- `test_multiple_assessments_per_risk_are_preserved` (create 3 on one risk → 3 rows, distinct ids — §8 history)

- [x] **Step 2: Run and watch them fail**

Run `uv run pytest tests/integration/test_assessment_service.py tests/integration/test_assessment_template_service.py -q`. Expected: ERROR — `ModuleNotFoundError: app.modules.assessment.repository` / service.

- [x] **Step 3: Implement**

`repository.py` (tenant-scoped getters, `_conditions` filter builders incl. the risk-join for `asset_id`, `SORT_COLUMNS` whitelists for both entities, count+offset/limit lists, response upsert helper, batch `get_*_by_ids` — mirror `risk/repository.py`), `service.py` (two services + the state machine helpers `_require_status`, `_audit`, `completion_percentage`), extend `audit/enums.py` with the ten event types.

- [x] **Step 4: Run and watch them pass**

`uv run pytest tests/integration/test_assessment_service.py tests/integration/test_assessment_template_service.py -q` → PASS. Full suite → green.

- [x] **Step 5: Lint + commit**

ruff clean. Commit: `feat(assessment): assessment workflow services with state machine, validation and audit (RF-02)`.

---

### Task 6: REST API layer (risk-assessments + assessment-templates) with API tests

**Files:**
- Create: `backend/app/modules/assessment/routes.py`
- Modify: `backend/app/main.py` (include both routers)
- Test: `backend/tests/api/test_risk_assessments_api.py` (new)
- Test: `backend/tests/api/test_assessment_templates_api.py` (new)

**Interfaces:**
- Consumes: `AssessmentService`, `AssessmentTemplateService`, schemas, `get_db`, `settings.default_tenant_id`, `Request.state.request_id` (as `risk/routes.py` does).
- Produces:
  - `GET /api/v1/risk-assessments` — query: page, page_size, search, risk_id, asset_id, template_id, assessment_type, status, assessor, reviewer, created_from, created_to, sort_by, sort_order → `RiskAssessmentListResponse`.
  - `POST /api/v1/risk-assessments` — 201 `RiskAssessmentDetailResponse` (detail shape so the client can render immediately).
  - `GET /api/v1/risk-assessments/{assessment_id}` — 200 detail (§25: metadata + risk + asset + template + sections/questions + responses + completion), 404 envelope.
  - `PATCH /api/v1/risk-assessments/{assessment_id}/responses/{question_id}` — body `AssessmentResponseUpdate` → 200 `RiskAssessmentDetailResponse` (fresh detail incl. updated completion %).
  - `POST /api/v1/risk-assessments/{assessment_id}/submit` — 200 detail.
  - `POST /api/v1/risk-assessments/{assessment_id}/start-review` — 200 detail.
  - `POST /api/v1/risk-assessments/{assessment_id}/approve` — optional body `AssessmentApproveRequest` → 200 detail.
  - `POST /api/v1/risk-assessments/{assessment_id}/reject` — body `AssessmentRejectRequest` → 200 detail.
  - `POST /api/v1/risk-assessments/{assessment_id}/revision` — 201 detail of the NEW assessment.
  - `GET /api/v1/assessment-templates` — filters → `AssessmentTemplateListResponse` (metadata only).
  - `POST /api/v1/assessment-templates` — 201 `AssessmentTemplateResponse`.
  - `GET /api/v1/assessment-templates/{template_id}` — 200 `AssessmentTemplateDetailResponse` (sections + questions).
  - `PATCH /api/v1/assessment-templates/{template_id}` — 200 `AssessmentTemplateResponse`.
  - Errors use the existing envelope: 404 `ASSESSMENT_NOT_FOUND`/`ASSESSMENT_QUESTION_NOT_FOUND`/`ASSESSMENT_TEMPLATE_NOT_FOUND`/`RISK_NOT_FOUND`, 409 state/incomplete/inactive/duplicate, 400 response/query invalid, 422 `VALIDATION_ERROR` for malformed bodies. Unknown query params → 400 (extra="forbid" + `_reject_unknown_query` pattern).

- [x] **Step 1: Write the failing tests**

`tests/api/test_risk_assessments_api.py` (fixtures seed templates + create a risk with asset via API):
- `test_create_assessment_returns_201_with_detail_shape` (sections present, completion_percentage 0, status draft)
- `test_create_assessment_unknown_risk_404_envelope` (code RISK_NOT_FOUND, request_id present)
- `test_create_assessment_unknown_template_404`
- `test_create_assessment_validation_error_on_extra_field` (422 VALIDATION_ERROR, e.g. `likelihood: 3` — RF-03 boundary)
- `test_get_assessment_returns_sections_questions_and_responses`
- `test_get_unknown_assessment_returns_404_envelope` (code ASSESSMENT_NOT_FOUND)
- `test_patch_response_updates_answer_and_completion` (BOOLEAN answer → completion rises; response echoed in detail)
- `test_patch_response_wrong_type_returns_400` (code ASSESSMENT_RESPONSE_INVALID)
- `test_patch_response_unknown_question_404`
- `test_submit_incomplete_returns_409` (code ASSESSMENT_INCOMPLETE)
- `test_submit_complete_then_edit_returns_409` (code ASSESSMENT_INVALID_STATE)
- `test_full_review_journey_via_api` (create → answer all required → submit → start-review → approve → status approved, reviewer recorded, timestamps set; further PATCH → 409)
- `test_reject_without_reason_returns_422` and `test_reject_with_reason_then_revision_201` (revision has revision_number 2 and copied responses; original still rejected)
- `test_list_assessments_pagination_metadata` (3 created, page_size 2 → page 1 has 2, total 3, total_pages 2)
- `test_list_assessments_filters_by_status_and_risk`
- `test_list_assessments_unknown_filter_rejected` (extra query param → 400)
- `test_list_assessments_invalid_sort_returns_400` (code ASSESSMENT_QUERY_INVALID)
- `test_cross_tenant_assessment_get_returns_404` (assessment created directly in DB for tenant B)

`tests/api/test_assessment_templates_api.py`:
- `test_list_templates_includes_seeded_general_template` (key present, is_system true, version 1)
- `test_get_template_detail_returns_sections_with_questions` (7 sections, ordered, questions nested)
- `test_create_custom_template_returns_201` (auto key)
- `test_create_duplicate_template_key_returns_409`
- `test_patch_template_updates_name`
- `test_patch_template_unknown_returns_404`
- `test_patch_template_rejects_version_field` (422 VALIDATION_ERROR)

- [x] **Step 2: Run and watch them fail**

Run `uv run pytest tests/api/test_risk_assessments_api.py tests/api/test_assessment_templates_api.py -q`. Expected: FAIL with 404 NOT_FOUND (routes missing).

- [x] **Step 3: Implement**

`routes.py` with `router = APIRouter(prefix="/api/v1/risk-assessments")` and `templates_router = APIRouter(prefix="/api/v1/assessment-templates")`; service factories passing `settings.default_tenant_id`, `request_id`, `actor=None`; `_build` filter wrappers (ValidationError → domain query error); detail builders that resolve risk/asset/template refs and nest sections/questions/responses without N+1 (service `get_detail`/`resolve_refs`); register both routers in `create_app()`.

- [x] **Step 4: Run and watch them pass**

`uv run pytest tests/api/test_risk_assessments_api.py tests/api/test_assessment_templates_api.py -q` → PASS. Full suite → green.

- [x] **Step 5: Lint + commit**

ruff clean. Commit: `feat(assessment): risk assessment and template REST APIs (RF-02)`.

---

### Task 7: End-to-end verification (RF-01 → RF-02 journey), acceptance sweep, push

**Files:**
- Test: `backend/tests/e2e/test_risk_assessment_acceptance.py` (new)
- Modify: `README.md` (endpoint list + RF-02 mention)
- Modify: this plan (check off completed tasks)

**Interfaces:**
- Consumes: everything from Tasks 2–6.
- Produces: green end-to-end proof; documented endpoints; pushed branch + PR.

- [x] **Step 1: Write the failing/verifying end-to-end test**

`tests/e2e/test_risk_assessment_acceptance.py`:
- `test_full_risk_assessment_journey` (plan §47 Step 12 / §48 DoD):
  1. seed reference data, risk categories, assessment templates;
  2. create a discovered asset + risk (RF-01 path);
  3. create an assessment (type `initial`, assessor recorded);
  4. answer every required question through `save_response` (mixed types) — progress rises to 100, status `in_progress`, `assessment.started` audit exists;
  5. "leave and resume": build a fresh service instance, verify all answers persist and completion is still 100 (§27);
  6. submit → `submitted`, `submitted_at` set; attempt edit → 409;
  7. start-review → `under_review`; approve with reviewer → `approved`, immutable;
  8. create a SECOND assessment on the same risk (type `periodic`) → both exist, first untouched (§8);
  9. create a third, submit, reject with reason → `rejected` + reason; `create_revision` → new DRAFT with `revision_number=3`, copied responses, original untouched (§32/§33);
  10. assert the audit trail for the journey exists in order with `resource_type="assessment"` covering created/started/submitted/review_started/approved/rejected/revised;
  11. assert list filters (`risk_id`, `status`) return the expected subsets.
- `test_cross_tenant_assessment_isolation` — assessment + template + risk created for tenant B are all unreachable from tenant A via service (404s), and tenant B's assessment cannot be submitted/edited from tenant A (§43).

- [x] **Step 2: Run it**

`uv run pytest tests/e2e/test_risk_assessment_acceptance.py -q` → PASS. If RED: debug with systematic-debugging before proceeding.

- [ ] **Step 3: Full acceptance sweep**

- `uv run pytest -q` → entire suite green (no RF-01/Asset Discovery regression).
- `uv run ruff check .` → clean; `uv run ruff format --check .` on touched files.
- `uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head` → migration reversible (or assert via the Task 2 migration test if no dev DB).
- `uv run python -m app.seed` twice → idempotent (one template, 21 categories, no duplicates).
- Verify OpenAPI: assert `/api/v1/risk-assessments` and `/api/v1/assessment-templates` appear in `app.openapi()` paths.

- [x] **Step 4: Update README**

Add assessment endpoints to the README endpoint list (`/api/v1/risk-assessments` + subroutes, `/api/v1/assessment-templates`) and a one-line RF-02 mention.

- [ ] **Step 5: Commit, push, open PR**

Commit `feat(assessment): AI risk assessment workflow acceptance and docs (RF-02)`. Check off tasks in this plan file (`docs: check off RF-02 plan tasks`). Push `feature/rf02-ai-risk-assessment` to `origin`. Open PR against `main` with a summary of RF-02 scope + rulings. Expected: PR created; local suite green.

---

## Definition of Done (plan §48 acceptance mapping)

- Start assessment from a risk with valid type + active template; template version snapshotted (Tasks 5–6 tests).
- Questions render dynamically from template data — API detail payload carries sections/questions/responses (Task 6 detail tests); UI deferred (Ruling 1).
- All six question types work; required validated at submit; answers savable; incomplete assessments resumable (Tasks 5–6).
- Draft editable; submitted not casually edited; approve/reject with required reason; approved immutable (Task 5 state-machine tests).
- Multiple assessments per risk preserved; template changes never mutate snapshots; revisions never overwrite (Tasks 5–7).
- Tenant isolation everywhere; cross-tenant references rejected; unauthorized transitions 409 (Tasks 5–7).
- RF-01 integration: assessments link to risks/assets without duplicating data; Asset Discovery regression-free (Task 7 sweep).
- Authenticated/authorized/audited/tested: audited (Task 5), tested (all tasks), tenant-isolated (all tasks); authentication/authorization roles deferred with the auth system (Ruling 3).
