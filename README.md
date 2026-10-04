# Auditra

AI governance backend. **Model Registration V1** — register, update, query, and archive
models under a stable canonical identity. **Model Versioning V1** — version those models
with immutable identity, lifecycle transitions, and optimistic concurrency.
**Model Deployment V1** — record where each version is deployed and how it is reached.
Specs: `docs/features/model-inventory/spec.md`, `docs/features/model-inventory/versioning-spec.md`,
`docs/features/model-inventory/model-deployment.md`.

## AI Model Inventory status

```text
  [x] Model Registration
  [x] Model Versioning
  [x] Model Deployment
  [x] Agent Association
  [ ] Model Usage
  [ ] Model Discovery
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
`/api/v1/deployment-endpoints/{endpoint_id}` (OpenAPI at `/docs`).

Lint: `uv run ruff check .` and `uv run ruff format .` (from `backend/`).
