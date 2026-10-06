# Model Usage (Feature 5)

Backend-only feature persisting immutable AI model usage events and serving request/token/latency statistics. Auditra records what happened; it is not a metering or billing engine. Prompt and completion payloads are never stored.

## 1. Purpose

Give AI governance a durable, correlated record of every model call: which model (and version/deployment) served it, which application/agent invoked it, how many tokens it consumed, how long it took, and whether it succeeded — queryable for statistics, cost visibility, and future audit.

## 2. Scope

In scope (V1):

- Usage-event ingestion (single, idempotent; batch, partial success) at `POST /api/v1/model-usage/events`.
- Event retrieval by external `event_id` or UUID and filtered listing.
- Aggregated statistics (requests, successes, failures, error rate, tokens, latency) with optional day/month buckets.
- LangChain callback and LangGraph config helper that emit events through a pluggable telemetry collector.
- PostgreSQL persistence via SQLAlchemy 2 + Alembic migration `0005_model_usage`.

Out of scope (V1): prompt/response storage, cost *calculation* from provider pricing tables (cost fields are accepted as facts), billing, Redis/RustFS, authentication/authorization middleware, frontend, model discovery. Spec: `usage-spec.md`.

## 3. Architecture

```text
API (FastAPI, app/modules/model_usage/api → routes.py)
  -> Service (ModelUsageService: validation, normalization, idempotency)
    -> Repository (tenant-scoped query builders + single-query aggregates)
      -> ORM (ModelUsageEvent) -> PostgreSQL (alembic 0005)
  -> Telemetry (ServiceUsageCollector / NoopCollector) <- AuditraUsageCallback (LangChain/LangGraph)
```

- The router parses HTTP input, calls one service method, and maps the ORM row to a response schema.
- Services own every business rule: relationship validation (model/version/deployment/application/agent must exist; agent must have an ACTIVE association with the model), token/status/error consistency, payload fingerprint idempotency, derived fields (`duration_ms`, `total_tokens` → `calculated` token source).
- Repositories are tenant-scoped; statistics use a single `FILTER` query; time buckets use `func.timezone('UTC', date_trunc(...))`.
- Events are immutable: no update or delete endpoints exist; there is no `updated_at` column.

## 4. Event lifecycle

```text
normalize_event -> validate relationships -> payload fingerprint (SHA-256)
  -> lookup by (tenant, event_id)
       |-- exists, same fingerprint  -> return existing, 200 duplicate
       |-- exists, different payload -> 409 EVENT_ID_CONFLICT
       |-- absent                    -> INSERT; unique(event_id) index is the
                                        final safeguard under concurrency (one
                                        row wins, loser resolves to duplicate)
```

Statuses: `success`, `partial_success`, `error`, `timeout`, `cancelled`, `rate_limited`, `unavailable`. `source`: `api`, `langchain`, `langgraph`, `manual`, `sdk`, `connector`, `system`. Token source is `provider_reported` when the provider returned usage, `calculated` when Auditra derived totals, otherwise `unavailable`.

## 5. API specification

All under `/api/v1` (OpenAPI at `/docs`):

```http
POST /api/v1/model-usage/events        # 201 created | 200 duplicate | 409 conflict
POST /api/v1/model-usage/events/batch  # partial success: accepted / duplicates / rejected
GET  /api/v1/model-usage/events/{id}   # by UUID or external event_id
GET  /api/v1/model-usage/events        # filters + page/page_size
GET  /api/v1/model-usage/stats         # totals + optional day/month buckets
```

`GET /api/v1/model-usage/events` supports `model_id`, `model_version_id`, `deployment_id`, `application_id`, `agent_id`, `status`, `source`, `environment`, `operation_name`, `request_id`, `trace_id`, `started_from`/`started_to`, `page`, `page_size` (default 50, max 200). `GET /api/v1/model-usage/stats` accepts the same dimensions plus `granularity` (`total`|`day`|`month`). Unknown/invalid query values return 400 `INVALID_USAGE_QUERY`.

## 6. Event JSON example

```json
{
  "event_id": "run_9f3c2a",
  "model_id": "d1e0f2a3-0000-4000-8000-000000000001",
  "model_version_id": "d1e0f2a3-0000-4000-8000-000000000002",
  "deployment_id": "d1e0f2a3-0000-4000-8000-000000000003",
  "application_id": "d1e0f2a3-0000-4000-8000-000000000004",
  "agent_id": "d1e0f2a3-0000-4000-8000-000000000005",
  "status": "success",
  "source": "langchain",
  "started_at": "2026-10-05T10:00:00Z",
  "completed_at": "2026-10-05T10:00:01Z",
  "duration_ms": 1042,
  "input_tokens": 843,
  "output_tokens": 96,
  "total_tokens": 939,
  "token_usage_source": "provider_reported",
  "request_id": "req-42",
  "trace_id": "trace-e2e-1",
  "environment": "production",
  "operation_name": "support_agent",
  "framework": "langchain",
  "framework_version": "1.6.6",
  "tags": {"team": "support"},
  "metadata": {"auditra_model_id": "d1e0f2a3-0000-4000-8000-000000000001"}
}
```

## 7. LangChain integration

```python
from langchain_core.language_models.fake_chat_models import FakeListChatModel  # any BaseChatModel
from langchain_core.messages import HumanMessage

from app.core.config import settings
from app.modules.model_usage.integrations.langchain import AuditraUsageCallback
from app.modules.model_usage.service import ModelUsageService
from app.modules.model_usage.telemetry import ServiceUsageCollector
from app.core.db import SessionLocal

session = SessionLocal()
collector = ServiceUsageCollector(ModelUsageService(session, settings.default_tenant_id))

model = FakeListChatModel(responses=["hello"])  # swap for a real model
model.invoke(
    [HumanMessage("hi")],
    config={
        "callbacks": [AuditraUsageCallback(collector)],
        "metadata": {
            "auditra_model_id": "<uuid>",
            "auditra_model_version_id": "<uuid>",
            "auditra_deployment_id": "<uuid>",
            "auditra_application_id": "<uuid>",
            "auditra_agent_id": "<uuid>",
            "auditra_operation": "support_agent",
            "auditra_environment": "production",
        },
    },
)
```

The callback handles `on_chat_model_start/end/error` (and the `on_llm_*` equivalents), extracts provider-neutral token usage (`usage_metadata`, `llm_output.token_usage`, or nested generations), records `parent_run_id` for run correlation, and never stores prompts or completions.

## 8. LangGraph integration

```python
from langgraph.graph import StateGraph, START, END

from app.modules.model_usage.integrations.langgraph import langgraph_usage_config

graph = StateGraph(GraphState)
# ... add_node("support", node), add_edge(START, "support"), add_edge("support", END)
compiled = graph.compile()

compiled.invoke(
    {"messages": [HumanMessage("follow up")]},
    config=langgraph_usage_config(
        collector,
        model_id=model_id,
        application_id=application_id,
        agent_id=agent_id,
        operation="support_agent",
        environment="production",
    ),
)
```

`langgraph_usage_config` returns a standard `RunnableConfig` (`tags`, `metadata`, `callbacks`) carrying the `auditra_*` metadata keys; every model call inside the graph emits one event with `source="langgraph"`. Nodes must accept and pass `config` (e.g. `def node(state, config): model.invoke(state["messages"], config=config)`) so the metadata propagates.

## 9. Best-effort vs strict telemetry

`AUDITRA_USAGE_MODE`:

- `best_effort` (default): a telemetry failure (DB down, validation rejection) is logged (`usage telemetry failed`, `usage event rejected`) and the model call proceeds unaffected. Callbacks become no-ops when `AUDITRA_USAGE_ENABLED=false`; model execution is never blocked by Auditra.
- `strict`: the same failure raises into the caller so integrators notice in development/staging.

REST ingestion always persists or returns a domain error; the telemetry policy applies to the callback collector path only.

## 10. Privacy defaults

- Prompts, completions, and raw provider payloads are **never stored** (spec §12; `AUDITRA_USAGE_STORE_PAYLOADS=false` is the permanent V1 default).
- Error messages are sanitized before persistence: `Bearer` tokens, `api_key=` values, and `sk-…` keys are redacted; stack traces stripped; message capped at 2000 chars.
- `metadata` is validated (max 10 KB via the shared `validate_metadata` helper — stricter than the spec's 32 KB; secret-like keys rejected).
- Secrets never appear in structured logs; logs carry operational fields only (`event_id`, `model_id`, `request_id`, `trace_id`, `status`, `source`, `duration_ms`).

## 11. Configuration

| Setting | Default | Meaning |
|---|---|---|
| `AUDITRA_USAGE_ENABLED` | `true` | Framework callbacks no-op when false |
| `AUDITRA_USAGE_MODE` | `best_effort` | Telemetry failure policy (`best_effort`/`strict`) |
| `AUDITRA_USAGE_BATCH_SIZE` | `100` | Max events per batch POST |
| `AUDITRA_USAGE_DEFAULT_PAGE_SIZE` | `50` | List default page size |
| `AUDITRA_USAGE_MAX_PAGE_SIZE` | `200` | List page-size cap |

## 12. Running tests

From `backend/` (the suite recreates its own `auditra_test` database automatically):

```powershell
uv run pytest tests/unit/test_model_usage_schemas.py tests/unit/test_usage_telemetry.py -q
uv run pytest tests/integration/test_model_usage_repository.py tests/integration/test_model_usage_service.py -q
uv run pytest tests/integration/test_langchain_usage.py tests/integration/test_langgraph_usage.py -q
uv run pytest tests/integration/test_usage_concurrency.py -q
uv run pytest tests/api/test_model_usage_api.py tests/e2e/test_model_usage_acceptance.py -q
uv run pytest -q
uv run ruff check . ; uv run ruff format --check .
```

Migration round-trip is covered by `tests/integration/test_migration.py`. Concurrency scenarios (same `event_id`, distinct events, batch vs single, stats during insert) live in `test_usage_concurrency.py`.

## 13. Local PostgreSQL setup

```powershell
# from repository root — maps host port 5433 (host PostgreSQL occupies 5432)
docker compose up -d
cd backend
Copy-Item ..\.env.example .\.env
uv run alembic upgrade head
uv run python -m app.seed
uv run uvicorn app.main:app --reload
```

## 14. Developer workflow

```text
register model           POST /api/v1/models
register version         POST /api/v1/models/{id}/versions
register deployment      POST /api/v1/model-versions/{id}/deployments
associate agent/app      POST /api/v1/applications, /applications/{id}/agents,
                                    /agents/{id}/models
run LangChain/LangGraph  invoke with AuditraUsageCallback / langgraph_usage_config
inspect usage events     GET  /api/v1/model-usage/events?model_id=...
query stats              GET  /api/v1/model-usage/stats?model_id=...&granularity=day
```
