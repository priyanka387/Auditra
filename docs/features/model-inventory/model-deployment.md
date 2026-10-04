# Model Deployment (Feature 1C)

Backend-only feature registering where a model version is deployed and how it is reached. Auditra records deployment state; it is not a deployment engine.

## 1. Purpose

Give AI governance a verified record of every place a model version runs or is exposed: which environment, which target, which runtime, how many replicas, and which endpoint an operator or downstream system should call. This is the inventory layer that later features (usage, discovery, agent association) build on.

## 2. Scope

In scope (V1):

- Deployment records scoped to a model version (create, read, update, archive).
- Deployment lifecycle state machine with guarded transitions.
- Endpoint records per deployment (create, read, update, archive).
- Filtering, search, sorting, pagination on `GET /api/v1/deployments`.
- PostgreSQL persistence via SQLAlchemy 2 + Alembic migration `0003_model_deployment`.

Out of scope (V1): connectors, health checking, environment subsystem, Redis, RustFS, Kubernetes/MLflow orchestration, authentication/authorization middleware, frontend, agent association, model usage, model discovery. Auditra never deploys, scales, or probes anything; deployment actions happen elsewhere and are reflected as metadata and status transitions here.

## 3. Architecture

Layering follows the existing model-inventory module:

```text
API (FastAPI, app/modules/model_inventory/api/deployments.py)
  -> Services (DeploymentService, DeploymentEndpointService)
    -> Repositories (deployment_repository, deployment_endpoint_repository)
      -> ORM (ModelDeployment, DeploymentEndpoint) -> PostgreSQL (alembic 0003)
  -> Domain (lifecycle matrix, errors, events)
  -> Schemas (Pydantic request/response, URL/auth validation)
```

- The router only parses/validates HTTP input, calls one service method, and maps the ORM row to a response schema.
- Services own every business rule: deployability guard, duplicate identity, lifecycle transitions, single-active-primary inference endpoint, archive semantics, event dispatch.
- Repositories are tenant-scoped query builders; `IntegrityError`/`StaleDataError` are mapped to domain errors on commit.
- Domain events (`deployment.created/updated/status_changed/archived`, `deployment_endpoint.created/updated/archived`) are frozen dataclasses dispatched in-process.

## 4. Domain model

- **ModelDeployment** — one deployment of one `model_version_id` in one environment/target/namespace. Immutable identity fields: `model_version_id`, `environment`, `deployment_kind`, `source`. Carries `status`, `status_source`, target fields (`target_type`, `target_name`, `region`, `cluster_name`, `namespace`), serving fields (`runtime`, `serving_framework`, `image_uri`, replicas), JSONB `configuration` and `metadata`, ownership timestamps, optimistic-concurrency `record_version`, and `archived_at`.
- **DeploymentEndpoint** — one reachable address belonging to exactly one deployment (`deployment_id`, FK RESTRICT). Fields: `endpoint_type`, `protocol`, `url`, `route`, `auth_type`, `auth_reference`, `is_primary`, `status`, `health_status`, JSONB `metadata`, `archived_at`, `record_version`.
- **Deployment identity / duplicate prevention** — unique per tenant on (`model_version_id`, `environment`, `target_name`, `namespace`) excluding archived rows, enforced by both `find_duplicate()` and a partial `NULLS NOT DISTINCT` unique index.
- **Deployment kind** — `online_inference, batch, embedded, edge, scheduled, other`.
- **Deployment target** — `target_type` is a free string (`kubernetes`, `vm`, `serverless`, `external`, ...); `target_name` names the concrete target.
- **Deployment runtime** — `runtime` / `serving_framework` free strings (`vllm`, `triton`, `torchserve`, ...), lowercased.

## 5. Deployment lifecycle

Statuses (lowercase): `planned, deploying, active, degraded, failed, stopping, stopped, deprecated`. `planned` is the initial status on create; `deprecated` is terminal (set by archive).

Allowed transitions (`domain/lifecycle.py`):

| From     | To                                              |
|----------|-------------------------------------------------|
| planned  | deploying, active, failed                       |
| deploying| active, degraded, failed                        |
| active   | degraded, stopping, deprecated                  |
| degraded | active, stopping, failed, deprecated            |
| failed   | deploying, stopping, deprecated                 |
| stopping | stopped, failed                                 |
| stopped  | deploying, deprecated                           |
| deprecated | (none)                                        |

Rules:

- Status changes only via `POST /api/v1/deployments/{id}/transition`; `PATCH` never touches `status` (immutable fields are absent from `DeploymentUpdate` and rejected with 422).
- Illegal or same-state transitions return 409 `DEPLOYMENT_INVALID_TRANSITION`.
- `status_source` is the payload `source` at creation (`manual, api, connector, discovery, system`) and `"api"` after any transition.
- Archive (DELETE) sets `status = "deprecated"` and `archived_at = now()`; idempotent, returns 204.

## 6. Environment model

V1 supports exactly: `development, staging, production` — a validated string, not a table or subsystem (spec trade-off: environment enum vs table). Invalid values are rejected with 422 at the schema layer. No environment-scoped cascading behavior exists yet.

## 7. Endpoint model

- `endpoint_type`: `inference` or `health`.
- `protocol`: `http, https, grpc, grpcs`; the URL scheme must equal the protocol.
- `auth_type`: `none, api_key, bearer, basic, mtls, custom, external_secret`. `auth_reference` is a reference URI (`secret://...`, `vault://...`) — must contain `://`, and must be absent when `auth_type = none` (409 `INVALID_AUTH_REFERENCE`). Raw secret material is never accepted or stored.
- **Primary endpoint**: at most one active primary `inference` endpoint per deployment, enforced in the service (`ENDPOINT_DUPLICATE_PRIMARY`) and by a partial unique DB index; marking a non-inference endpoint primary returns 409 `ENDPOINT_INVALID_PRIMARY`.
- URL validation (`schemas/deployment_endpoint.validate_endpoint_url`): length 1–2048, parseable, scheme matches protocol, host present, **no embedded credentials, no fragment**. URLs are validated, never fetched (SSRF boundary).

## 8. Database schema

Migration `alembic/versions/0003_model_deployment.py`:

- `model_deployments` — UUID PK, `tenant_id`, FK `model_version_id -> model_versions.id` (RESTRICT), identity/target/serving columns, JSONB `configuration` + `metadata` (column `metadata_`), `status`, `status_source`, timestamps (`deployed_at`, `last_seen_at`, `archived_at`, `created_at`, `updated_at`), ownership (`created_by`, `updated_by`), integer `record_version` (SQLAlchemy version_id_col).
- `deployment_endpoints` — UUID PK, `tenant_id`, FK `deployment_id -> model_deployments.id` (RESTRICT), endpoint columns, JSONB `metadata`, `archived_at`, `record_version`.
- Indexes: list/filter columns; unique partial index `uq_model_deployments_tenant_version_env_target_ns` on (tenant, model_version_id, environment, target_name, namespace) with `NULLS NOT DISTINCT` + `WHERE archived_at IS NULL`; unique partial index `uq_deployment_endpoints_primary_inference` on (tenant, deployment_id) `WHERE is_primary AND endpoint_type = 'inference' AND archived_at IS NULL`.

## 9. API specification

All under `/api/v1` (OpenAPI at `/docs`):

```http
POST   /api/v1/model-versions/{model_version_id}/deployments   # 201
GET    /api/v1/deployments                                     # filters, search, sort, page
GET    /api/v1/deployments/{deployment_id}
PATCH  /api/v1/deployments/{deployment_id}                     # metadata-only
DELETE /api/v1/deployments/{deployment_id}                     # 204 archive, idempotent
POST   /api/v1/deployments/{deployment_id}/transition
POST   /api/v1/deployments/{deployment_id}/endpoints           # 201
GET    /api/v1/deployments/{deployment_id}/endpoints           # include_archived
GET    /api/v1/deployment-endpoints/{endpoint_id}
PATCH  /api/v1/deployment-endpoints/{endpoint_id}
DELETE /api/v1/deployment-endpoints/{endpoint_id}              # 204 archive, idempotent
```

`GET /api/v1/deployments` supports `page`, `page_size` (≤100), `search`, `model_version_id`, `model_id`, `environment`, `status`, `deployment_kind`, `target_type`, `runtime`, `region`, created/updated/`last_seen` date bounds, `include_archived`, and allowlisted `sort_by` (`created_at, updated_at, name, environment, status, deployed_at, last_seen_at`) with `sort_order` — unknown sort fields return 400 `INVALID_SORT_FIELD`.

Errors use the standard envelope `{"error": {code, message, details, request_id, http_status}}`.

## 10. Validation rules

- Create: `environment`/`deployment_kind`/`source` enum-validated, `desired_replicas`/`observed_replicas` ≥ 0, strings stripped and length-capped, JSONB validated by `validate_metadata` (10 000 bytes, recursive secret-key rejection), `status` not accepted from clients.
- Model version must exist in the tenant (404 `MODEL_VERSION_NOT_FOUND`) and be `DRAFT` or `ACTIVE` (409 `MODEL_VERSION_NOT_DEPLOYABLE`).
- Duplicate identity → 409 `DEPLOYMENT_DUPLICATE`.
- PATCH is metadata-only: `status`, `model_version_id`, `environment`, `deployment_kind`, `source` are immutable → 422.
- Transitions validated against the matrix → 409; archived deployments reject PATCH/transition (409 `DEPLOYMENT_ALREADY_ARCHIVED`).
- Endpoints: URL rules and auth pairing per §7 → 409 `ENDPOINT_INVALID_URL` / `INVALID_AUTH_REFERENCE`; duplicate primary → 409.
- Optimistic concurrency: both tables carry `record_version`; conflicting updates map `StaleDataError` → 409 (`DEPLOYMENT_CONCURRENCY_CONFLICT`).

## 11. Security

- No secrets stored — only reference URIs (`auth_reference`), raw values rejected.
- Tenant scoping on every repository read/write (`tenant_id` from `settings.default_tenant_id`); cross-tenant access returns 404.
- No raw SQL string concatenation: ORM query builders + allowlisted sort columns; JSONB comes from validated dicts.
- No automatic URL fetching anywhere in `app/` (no `requests`/`httpx`/`urlopen`) — URL validation is parse-only (SSRF boundary, spec §69).
- Existing FastAPI `DomainError`/validation handlers produce the error envelope with request ID; unhandled exceptions are logged and return a generic 500.
- AuthN/AuthZ remain the API gateway's responsibility in V1 (spec §64–65).

## 12. Testing

Run from `backend/`: `uv run pytest -q` (211 tests, real PostgreSQL via `auditra_test`, no external infrastructure).

- `tests/unit/test_deployment_lifecycle.py` — transition matrix, deployable states.
- `tests/unit/test_deployment_schemas.py`, `test_endpoint_schemas.py` — Pydantic rules, URL/auth validation.
- `tests/integration/test_deployment_persistence.py` — ORM, migration, indexes, uniqueness.
- `tests/integration/test_deployment_service.py` — service rules end to end (guards, duplicates, primary, archive).
- `tests/api/test_deployments_api.py` — full HTTP contract incl. the spec §126 acceptance scenario, filters/search/sort/pagination, error envelopes, hostile URLs.
- Migration test verifies `0003` applies cleanly on a fresh database.

## 13. Connector extension points

- `source` (`connector`, `discovery`) and `source_reference` let a future connector import deployments without schema changes.
- Domain events (`deployment.created/...`) are the hook a connector or audit pipeline can subscribe to.
- `status_source = "connector"` records machine-observed state separately from user actions.
- External mapping guidance for Kubernetes/MLflow/KServe is metadata-shaped (`configuration`, `target_*`, `runtime`) — a connector fills it in; nothing in V1 calls out.

## 14. Known limitations

- **No connector and no health checking** — `health_status` and `last_health_check_at` are metadata only; Auditra never probes a URL (spec trade-off 6).
- **`target_name`/`namespace` NULL uniqueness** — the unique index uses `NULLS NOT DISTINCT`, so two deployments with the same version+environment and NULL target/namespace are rejected.
- **`model_version_id` is immutable** — a deployment cannot be re-pointed to another version; create a new deployment instead.
- **Single active primary inference endpoint** per deployment (service rule + partial index).
- **No Redis, no RustFS, no frontend, no auth middleware** — PostgreSQL is the only infrastructure.

## 15. Examples

Create (spec §39):

```http
POST /api/v1/model-versions/{model_version_id}/deployments
{
  "name": "fraud-detection-prod",
  "environment": "production",
  "deployment_kind": "online_inference",
  "target_type": "kubernetes",
  "target_name": "fraud-prod-cluster",
  "region": "us-east-1",
  "cluster_name": "fraud-prod",
  "namespace": "ai-serving",
  "runtime": "vllm",
  "serving_framework": "vllm",
  "desired_replicas": 3,
  "configuration": {"tensor_parallel_size": 2, "max_model_len": 32768},
  "metadata": {"deployment_ticket": "DEP-1209"},
  "source": "manual"
}
# -> 201, status "planned", status_source "manual"
```

Add endpoint:

```http
POST /api/v1/deployments/{deployment_id}/endpoints
{
  "name": "primary",
  "endpoint_type": "inference",
  "protocol": "https",
  "url": "https://fraud.example.com/v1/infer",
  "auth_type": "external_secret",
  "auth_reference": "secret://prod/fraud",
  "is_primary": true
}
```

Activate and read inventory:

```http
POST /api/v1/deployments/{deployment_id}/transition
{"status": "active", "reason": "Deployment verified"}
# -> status "active", status_source "api"

GET /api/v1/deployments?environment=production&status=active
# -> {"items": [...], "page": 1, "page_size": 25, "total": 1, "total_pages": 1}

POST /api/v1/deployments/{deployment_id}/transition {"status": "stopped"}
# -> 409 DEPLOYMENT_INVALID_TRANSITION (active -> stopped is not allowed)
```
