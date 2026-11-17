# Auditra

AI governance backend. **Model Registration V1** — register, update, query, and archive
models under a stable canonical identity. **Model Versioning V1** — version those models
with immutable identity, lifecycle transitions, and optimistic concurrency.
**Model Deployment V1** — record where each version is deployed and how it is reached.
**Model Usage V1** — persist immutable model-call usage events (tokens, latency, errors,
inventory correlation) with LangChain/LangGraph telemetry callbacks and usage statistics.
**Audit V1** — append-only, same-transaction audit event trail for model, deployment, and
endpoint changes with a read-only query API and sanitized before/after state.
Specs: `docs/features/model-inventory/spec.md`, `docs/features/model-inventory/versioning-spec.md`,
`docs/features/model-inventory/model-deployment.md`,
`docs/features/model-inventory/usage-spec.md`, `docs/features/model-inventory/model-usage.md`,
`docs/features/model-inventory/audit.md`.

## AI Model Inventory status

```text
  [x] Model Registration
  [x] Model Versioning
  [x] Model Deployment
  [x] Agent Association
  [x] Model Usage
  [x] Model Discovery
  [x] Audit
```

## Prerequisites

- Docker (with Compose)
- [uv](https://docs.astral.sh/uv/)

## Quickstart

Run from the repository root, then stay in `backend/`:

```powershell
# 1. Start PostgreSQL (container maps host port 5433 — the host machine's own PostgreSQL 18 occupies 5432)
docker compose up -d

cd backend

# 2. Install dependencies
uv sync

# 3. Configure (pydantic resolves env_file=".env" against the working directory, so it belongs in backend/)
Copy-Item ..\.env.example .\.env

# 4. Create schema and seed reference data (13 providers, 15 model types; safe to re-run)
uv run alembic upgrade head
uv run python -m app.seed

# 5. Run the API — http://127.0.0.1:8000
uv run uvicorn app.main:app --reload

# 6. Test suite (recreates its own auditra_test database automatically)
uv run pytest -v
```

Endpoints: `/health`, `/api/v1/models`, `/api/v1/models/{model_id}`,
`/api/v1/models/{model_id}/versions`, `/api/v1/models/{model_id}/versions/{version_id}`,
`/api/v1/models/{model_id}/versions/{version_id}/lifecycle`,
`/api/v1/model-providers`, `/api/v1/model-types`,
`/api/v1/model-versions/{model_version_id}/deployments`, `/api/v1/deployments`,
`/api/v1/deployments/{deployment_id}`, `/api/v1/deployments/{deployment_id}/transition`,
`/api/v1/deployments/{deployment_id}/endpoints`,
`/api/v1/deployment-endpoints/{endpoint_id}`,
`/api/v1/applications`, `/api/v1/applications/{application_id}/agents`,
`/api/v1/agents/{agent_id}/models`,
`/api/v1/model-usage/events`, `/api/v1/model-usage/events/{event_id}`,
`/api/v1/model-usage/stats`, `/api/v1/audit/events`,
`/api/v1/audit/events/{event_id}`, `/api/v1/risks`,
`/api/v1/risks/{risk_id}`, `/api/v1/risk-categories`,
`/api/v1/risk-categories/{category_id}`, `/api/v1/risk-assessments`,
`/api/v1/risk-assessments/{assessment_id}`,
`/api/v1/risk-assessments/{assessment_id}/responses/{question_id}`,
`/api/v1/risk-assessments/{assessment_id}/submit`,
`/api/v1/risk-assessments/{assessment_id}/start-review`,
`/api/v1/risk-assessments/{assessment_id}/approve`,
`/api/v1/risk-assessments/{assessment_id}/reject`,
`/api/v1/risk-assessments/{assessment_id}/revision`,
`/api/v1/assessment-templates`, `/api/v1/assessment-templates/{template_id}`
(OpenAPI at `/docs`).

Risk & Compliance Management: RF-01 ships the risk register and its
per-tenant taxonomy — risks are created, reviewed, searched and soft-archived
against the 21 seeded system categories and discovered AI assets, with every
mutation recorded in the audit trail.

RF-02 adds the AI risk assessment workflow: answer the seeded General AI
Risk Assessment questionnaire against a risk, submit it, run the strict
review state machine (draft → in_progress → submitted → under_review →
approved | rejected), fork rejected assessments into revisions that carry
the answers forward, and browse completion percentages with list filters —
all tenant-isolated and fully audited. A custom template catalog
(create/patch) complements the seeded system template.

Lint: `uv run ruff check .` and `uv run ruff format .` (from `backend/`).
