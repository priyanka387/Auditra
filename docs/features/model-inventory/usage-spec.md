# AUDITRA — AI Model Inventory
## Sub-Feature 5: Model Usage

**Status:** Implementation Specification  
**Phase:** Phase 1E — AI Model Inventory  
**Primary focus:** Backend only  
**Execution target:** OpenCode coding agent  
**Backend stack:** Python, FastAPI, Pydantic, SQLAlchemy 2.x, PostgreSQL, Redis (optional/asynchronous delivery path), LangChain, LangGraph  
**Frontend:** Explicitly out of scope for this phase

---

## 1. Purpose

Implement the **Model Usage** sub-feature of Auditra's **AI Model Inventory**.

Model Usage records how registered AI models are actually used by applications, agents, and deployments. It provides a durable usage-event ledger plus queryable operational statistics.

The implementation must support:

- Immutable model usage events
- Request/response execution metadata
- Token and latency measurements when available
- Success/failure tracking
- Model/version/deployment/application/agent correlation
- LangChain integration
- LangGraph integration
- Batch event ingestion
- Basic usage statistics
- Idempotent ingestion
- Privacy-aware telemetry defaults
- Extensible provider-specific metadata
- Automated unit, integration, API, and end-to-end tests

The implementation must **not** depend on a frontend.

---

# 2. Position in Auditra

The AI Model Inventory is structured as follows:

```text
AUDITRA
│
└── AI Model Inventory
    │
    ├── 1. Model Registration
    │   ├── Model CRUD
    │   ├── Provider
    │   ├── Model Type
    │   ├── Metadata
    │   └── Ownership
    │
    ├── 2. Model Versioning
    │   ├── Versions
    │   ├── Version metadata
    │   └── Lifecycle
    │
    ├── 3. Model Deployment
    │   ├── Deployment
    │   ├── Environment
    │   └── Endpoint
    │
    ├── 4. Application / Agent Association
    │   ├── Application
    │   ├── Agent
    │   └── Model ↔ Agent relationship
    │
    ├── 5. Model Usage          <-- THIS SPEC
    │   ├── Usage events
    │   ├── Request metadata
    │   └── Basic statistics
    │
    ├── 6. Model Discovery
    │   ├── Manual
    │   ├── LangChain
    │   ├── LangGraph
    │   └── Future connectors
    │
    └── 7. Audit
        ├── Created
        ├── Updated
        ├── Registered
        └── Deployment changes
```

Model Usage consumes identity/context established by the previous sub-features. It must not duplicate model, version, deployment, application, or agent master data.

---

# 3. Problem Statement

Organizations can register an AI model and know where it is deployed, but registration alone does not answer:

- Is the model actually being used?
- Which application or agent is using it?
- Which model version is receiving traffic?
- Which deployment/environment is serving the traffic?
- How many requests are executed?
- What is the success/error rate?
- How many tokens are consumed?
- What is the latency profile?
- Which requests are failing?
- What provider/model configuration produced the usage?
- Can usage be traced back to a specific execution or agent run?

Without usage telemetry, the inventory becomes a static registry rather than an operational governance system.

Model Usage turns the registry into an evidence-producing system.

---

# 4. Goals

## 4.1 Functional Goals

1. Record every supported model invocation as an immutable usage event.
2. Correlate events to Auditra inventory entities whenever those entities are known.
3. Accept events synchronously through an internal REST API.
4. Provide a batch ingestion API for SDK/callback producers.
5. Provide a reusable Python telemetry client/service that can be used by LangChain and LangGraph integrations.
6. Capture provider-neutral request metadata.
7. Capture provider-reported token/cost data when available.
8. Track execution duration and result status.
9. Support idempotent event ingestion.
10. Provide basic aggregate statistics over configurable time ranges.
11. Make telemetry failures non-fatal to the application/model invocation path.
12. Keep sensitive prompt/response content excluded by default.

## 4.2 Non-Goals

Do **not** implement in this phase:

- Frontend/UI/dashboard
- Advanced analytics or BI
- Real-time streaming dashboard
- Cost-pricing catalog management
- Prompt evaluation
- Quality evaluation
- Safety evaluation
- Policy evaluation
- PII detection/classification engine
- Full distributed tracing backend
- LangChain/LangGraph automatic discovery/registration
- Model discovery/connectors as a separate feature
- Audit-log implementation for registry changes
- Retention/deletion scheduler beyond persistence hooks/configuration
- Billing/invoice functionality

These may consume Model Usage later.

---

# 5. Architectural Principle

Use the following separation:

```text
Application / Agent / LangChain / LangGraph
                    │
                    │ usage telemetry
                    ▼
          Auditra Usage Collector
                    │
          normalize + validate
                    │
                    ▼
             Usage Event Service
                    │
        ┌───────────┴───────────┐
        ▼                       ▼
  PostgreSQL                Redis (optional)
 immutable events           async buffer
        │
        ▼
 Statistics / Query Service
```

The telemetry path must be **failure-isolated**:

```text
Model invocation succeeds
        │
        ├── telemetry succeeds → event persisted
        │
        └── telemetry fails    → model result still returned
```

Telemetry must not turn a successful model invocation into a failed application request.

---

# 6. Existing System Dependencies

The implementation assumes the following entities are already implemented by previous inventory phases or are available as integration points:

- `models`
- `model_versions`
- `deployments`
- `applications`
- `agents`
- Application ↔ Agent association
- Model ↔ Agent association

The exact table names may differ in the existing codebase. Reuse the existing naming and ORM conventions instead of introducing parallel master tables.

## 6.1 Dependency Rules

`model_id` is required.

The following fields are optional because usage can sometimes arrive before the complete inventory context is known:

- `model_version_id`
- `deployment_id`
- `application_id`
- `agent_id`

When these IDs are supplied:

- They must refer to existing records.
- Relationships must be logically compatible.
- Incompatible relationships must return a validation/domain error.

Example:

```text
usage.model_id = GPT-4o
usage.agent_id = ResearchAgent
usage.application_id = ResearchApp
usage.deployment_id = ProductionEndpoint
usage.model_version_id = GPT-4o@2026-09
```

The event is still valid if only `model_id` is known.

---

# 7. Canonical Usage Event

A usage event represents **one logical model execution**.

Do not create one row for every streaming token.

For a streaming invocation, emit one final usage event containing aggregated metrics such as:

- request start timestamp
- request end timestamp
- duration
- total tokens
- input tokens
- output tokens
- status
- error information, when applicable

Future streaming telemetry can be introduced separately.

---

# 8. Usage Event Lifecycle

```text
START
  │
  ▼
RUNNING
  │
  ├───────────────┐
  │               │
  ▼               ▼
SUCCEEDED       FAILED
  │               │
  └───────┬───────┘
          ▼
      PERSISTED
```

The database record should store the **final state**, not a mutable workflow state machine.

Recommended status enum:

```text
success
error
cancelled
unknown
```

`unknown` exists for producers that cannot determine final outcome.

---

# 9. Usage Event Data Model

## 9.1 Required Fields

| Field | Type | Required | Description |
|---|---|---:|---|
| `id` | UUID | Yes | Auditra event ID |
| `event_id` | UUID/string | Yes | External idempotency identifier |
| `model_id` | UUID | Yes | Registered model |
| `status` | enum | Yes | success/error/cancelled/unknown |
| `started_at` | timestamptz | Yes | Invocation start time |
| `completed_at` | timestamptz | No | Invocation completion time |
| `duration_ms` | integer/bigint | No | Total execution latency |
| `source` | enum/string | Yes | Event producer |
| `created_at` | timestamptz | Yes | Auditra persistence time |

## 9.2 Inventory Correlation Fields

| Field | Type | Required | Description |
|---|---|---:|---|
| `model_version_id` | UUID | No | Version used |
| `deployment_id` | UUID | No | Deployment used |
| `application_id` | UUID | No | Application invoking model |
| `agent_id` | UUID | No | Agent invoking model |

## 9.3 Request Metadata

Store metadata required to understand the invocation without storing sensitive payloads by default.

Recommended fields:

| Field | Type | Required | Description |
|---|---|---:|---|
| `request_id` | string/UUID | No | Producer/request correlation ID |
| `trace_id` | string | No | Distributed trace correlation ID |
| `parent_run_id` | string | No | Parent chain/agent run ID |
| `provider_request_id` | string | No | Provider request identifier |
| `environment` | string | No | dev/staging/prod/etc. |
| `region` | string | No | Execution/provider region |
| `host` | string | No | Optional producer host identifier |
| `runtime` | string | No | Optional runtime description |
| `framework` | string | No | langchain/langgraph/custom/etc. |
| `framework_version` | string | No | Producer framework version |
| `sdk_version` | string | No | Auditra SDK/collector version |
| `operation_name` | string | No | Logical operation name |
| `tags` | JSON | No | Non-sensitive searchable metadata |
| `metadata` | JSONB | No | Extensible provider-neutral metadata |

Do not use arbitrary JSON for fields that need querying/indexing frequently. Promote frequently queried fields to first-class columns.

---

# 10. Model Metrics

## 10.1 Token Metrics

All fields are nullable because not every model/provider exposes usage information.

```text
input_tokens
output_tokens
total_tokens
cached_input_tokens
reasoning_tokens
```

Rules:

1. `total_tokens` should be provider-reported when available.
2. If provider reports input/output but not total, Auditra may calculate:

```text
input_tokens + output_tokens
```

3. Calculated values must not overwrite provider-reported values.
4. Store whether a metric was `provider_reported` or `calculated`.

Recommended implementation:

```text
token_usage_source = provider_reported | calculated | unavailable
```

## 10.2 Cost Metrics

Optional in Phase 1E.

Store values only when the producer/provider supplies a trustworthy value.

```text
estimated_cost
cost_currency
cost_source
```

Do not introduce a model pricing catalog as part of this phase.

Possible `cost_source` values:

```text
provider_reported
local_estimate
unavailable
```

`local_estimate` should not be implemented unless an explicit pricing configuration already exists in the repository.

## 10.3 Latency

Store:

```text
duration_ms
queue_ms                # optional future/use when available
provider_latency_ms    # optional provider-reported value
first_token_latency_ms # optional future streaming metric
```

Only `duration_ms` is required for Phase 1E.

---

# 11. Error Information

When a model invocation fails, store structured error information.

Recommended fields:

```text
error_type
error_code
error_message
retry_count
provider_status_code
```

Privacy requirement:

- Do not blindly persist exception objects.
- Do not persist stack traces by default.
- Do not persist secrets/API keys.
- Error messages must pass through a sanitization function.

Recommended maximum lengths:

```text
error_type      <= 255 chars
error_code      <= 100 chars
error_message   <= 2000 chars
```

If the provider error contains a request prompt or other sensitive payload, sanitize it before persistence.

---

# 12. Prompt/Response Privacy Model

This is a governance platform. The default must be privacy-preserving.

## 12.1 Default

Do **not** store:

- raw prompt
- raw completion
- conversation transcript
- documents retrieved by RAG
- images/audio/video payloads
- secrets
- authentication credentials

## 12.2 Optional Future Capability

Support configurable capture modes later:

```text
none
hash_only
redacted
full
```

Phase 1E should create an abstraction boundary for payload capture but may leave the actual full-content storage feature disabled.

Recommended future fields:

```text
input_content_hash
output_content_hash
input_content_size
output_content_size
```

Do not make these required for MVP.

---

# 13. Source / Producer Model

Each usage event must indicate how it entered Auditra.

Recommended enum:

```text
api
sdk
langchain
langgraph
internal
connector
manual
```

For this phase, `langchain` and `langgraph` must be supported as integration values.

`manual` is allowed for tests and operational backfilling.

---

# 14. Database Design

## 14.1 Primary Table

Recommended table:

```text
model_usage_events
```

Suggested SQLAlchemy model concept:

```python
class ModelUsageEvent(Base):
    __tablename__ = "model_usage_events"

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)

    model_id: Mapped[UUID] = mapped_column(ForeignKey("models.id"), nullable=False)
    model_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("model_versions.id"), nullable=True
    )
    deployment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("deployments.id"), nullable=True
    )
    application_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("applications.id"), nullable=True
    )
    agent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agents.id"), nullable=True
    )

    status: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)

    input_tokens: Mapped[int | None] = mapped_column(BigInteger)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger)
    total_tokens: Mapped[int | None] = mapped_column(BigInteger)
    cached_input_tokens: Mapped[int | None] = mapped_column(BigInteger)
    reasoning_tokens: Mapped[int | None] = mapped_column(BigInteger)
    token_usage_source: Mapped[str] = mapped_column(String(32), nullable=False)

    estimated_cost: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    cost_currency: Mapped[str | None] = mapped_column(String(8))
    cost_source: Mapped[str] = mapped_column(String(32), nullable=False)

    request_id: Mapped[str | None] = mapped_column(String(128))
    trace_id: Mapped[str | None] = mapped_column(String(128))
    parent_run_id: Mapped[str | None] = mapped_column(String(128))
    provider_request_id: Mapped[str | None] = mapped_column(String(255))

    environment: Mapped[str | None] = mapped_column(String(64))
    region: Mapped[str | None] = mapped_column(String(128))
    framework: Mapped[str | None] = mapped_column(String(64))
    framework_version: Mapped[str | None] = mapped_column(String(64))
    sdk_version: Mapped[str | None] = mapped_column(String(64))
    operation_name: Mapped[str | None] = mapped_column(String(255))

    error_type: Mapped[str | None] = mapped_column(String(255))
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int | None] = mapped_column(Integer)
    provider_status_code: Mapped[int | None] = mapped_column(Integer)

    tags: Mapped[dict | None] = mapped_column(JSONB)
    metadata: Mapped[dict | None] = mapped_column(JSONB)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

Adapt exact typing/imports to the existing repository conventions.

## 14.2 UUID Strategy

Use UUIDs for Auditra-owned entity IDs.

`event_id` is a producer-supplied idempotency identifier and may be UUID/string because external systems may use another identifier format.

---

# 15. Database Constraints

At minimum enforce:

```text
model_id                  NOT NULL
status                    NOT NULL
source                    NOT NULL
started_at                NOT NULL
created_at                NOT NULL
event_id                   UNIQUE
```

Recommended checks:

```text
input_tokens >= 0
output_tokens >= 0
total_tokens >= 0
cached_input_tokens >= 0
reasoning_tokens >= 0
duration_ms >= 0
retry_count >= 0
estimated_cost >= 0
```

Application-level validation should provide human-readable errors even if database constraints are also used.

---

# 16. Index Strategy

Usage is read primarily by model, time, application, agent, deployment, and status.

Create indexes for:

```text
(model_id, started_at DESC)
(model_version_id, started_at DESC)
(deployment_id, started_at DESC)
(application_id, started_at DESC)
(agent_id, started_at DESC)
(status, started_at DESC)
(source, started_at DESC)
(started_at DESC)
```

Do not create indexes on every JSONB property.

A GIN index on `metadata` or `tags` should only be added if an actual query requirement exists in this phase.

---

# 17. Retention / Partitioning Consideration

Do not implement PostgreSQL partitioning unless the existing repository already uses it or tests prove it is necessary for Phase 1E.

However, design the table so future time-based partitioning is possible:

- `started_at` is mandatory
- queries use bounded time ranges
- statistics queries must always support a time window

The implementation should avoid unbounded full-table scans.

---

# 18. Pydantic Schemas

Create separate schemas for:

```text
UsageEventCreate
UsageEventResponse
UsageEventBatchCreate
UsageEventBatchResponse
UsageEventFilter
UsageStatsQuery
UsageStatsResponse
UsageTimeBucket
```

Do not expose ORM models directly through FastAPI routes.

## 18.1 UsageEventCreate

Suggested shape:

```python
class UsageEventCreate(BaseModel):
    event_id: str
    model_id: UUID
    model_version_id: UUID | None = None
    deployment_id: UUID | None = None
    application_id: UUID | None = None
    agent_id: UUID | None = None

    status: UsageStatus = UsageStatus.SUCCESS
    source: UsageSource

    started_at: datetime
    completed_at: datetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)

    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)

    token_usage_source: TokenUsageSource = TokenUsageSource.UNAVAILABLE

    estimated_cost: Decimal | None = Field(default=None, ge=0)
    cost_currency: str | None = None
    cost_source: CostSource = CostSource.UNAVAILABLE

    request_id: str | None = None
    trace_id: str | None = None
    parent_run_id: str | None = None
    provider_request_id: str | None = None

    environment: str | None = None
    region: str | None = None
    framework: str | None = None
    framework_version: str | None = None
    sdk_version: str | None = None
    operation_name: str | None = None

    error_type: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    retry_count: int | None = Field(default=None, ge=0)
    provider_status_code: int | None = Field(default=None, ge=100, le=599)

    tags: dict[str, str] | None = None
    metadata: dict[str, Any] | None = None
```

The implementation may add stricter constraints based on the repository's current conventions.

---

# 19. Validation Rules

## 19.1 Timestamp Rules

- `started_at` must be timezone-aware.
- `completed_at`, when present, must not be earlier than `started_at`.
- If `duration_ms` is supplied, it should be non-negative.
- If both timestamps are present, service logic should calculate duration when duration is absent.
- Do not silently replace explicitly supplied duration unless the design clearly defines it as derived.

## 19.2 Status/Error Rules

If `status == success`:

- error fields should be absent/null.

If `status == error`:

- `error_type` or `error_message` should normally be present.

If producer lacks an error detail, allow the event but store only the status.

## 19.3 Token Rules

If `total_tokens` is null and both `input_tokens` and `output_tokens` are available:

```text
total_tokens = input_tokens + output_tokens
```

Set:

```text
token_usage_source = calculated
```

Only perform this derivation when `token_usage_source` is not explicitly `provider_reported`.

## 19.4 Relationship Rules

When a version is provided:

```text
model_version.model_id == usage.model_id
```

When deployment is provided:

```text
deployment.model_id == usage.model_id
```

When agent is provided and the existing association feature exposes relationship validation:

```text
agent is associated with model
```

When application is provided with an agent:

```text
agent belongs to application
```

Use the repository's actual association model instead of duplicating these relationships.

---

# 20. Idempotency

Usage producers may retry delivery. Duplicate events must not inflate statistics.

Use `event_id` as the idempotency key.

Behavior:

```text
First POST event_id=abc
    -> 201 Created

Second POST event_id=abc with same logical payload
    -> return existing event / idempotent success

Second POST event_id=abc with materially different payload
    -> 409 Conflict or domain-specific idempotency error
```

Preferred implementation:

1. Unique DB constraint on `event_id`.
2. Service-level duplicate lookup.
3. Compare a deterministic payload fingerprint/hash for conflict detection.

Do not rely only on application-side duplicate lookup because concurrent requests can race.

---

# 21. REST API

Base prefix:

```text
/api/v1/model-usage
```

Adapt to the project's existing versioning/prefix conventions.

## 21.1 Create Single Usage Event

```http
POST /api/v1/model-usage/events
```

Request:

```json
{
  "event_id": "6b6e6fc8-0f0f-45d0-bd11-58f7b1a7e7c4",
  "model_id": "9f4c3d4d-3a7c-4ef8-8d10-08f7b4310a1f",
  "model_version_id": "ca0c7a5b-6ea0-4a8a-96d0-6a1a3f3f9b12",
  "deployment_id": "f01c0d9d-9ad1-46df-b07e-2d9b1b6a43f2",
  "application_id": "c58f5e19-6df7-4e0d-b7fa-0c5f0b43f5b7",
  "agent_id": "d7c2f1c7-5dc3-4e6f-842f-0ad72b7e8a6e",
  "status": "success",
  "source": "langgraph",
  "started_at": "2026-10-05T10:00:00Z",
  "completed_at": "2026-10-05T10:00:01.842Z",
  "duration_ms": 1842,
  "input_tokens": 843,
  "output_tokens": 392,
  "total_tokens": 1235,
  "token_usage_source": "provider_reported",
  "request_id": "req_012345",
  "trace_id": "trace_123",
  "parent_run_id": "run_987",
  "provider_request_id": "provider_req_456",
  "environment": "production",
  "region": "ap-south-1",
  "framework": "langgraph",
  "framework_version": "<detected-version>",
  "sdk_version": "<auditra-sdk-version>",
  "operation_name": "customer_support_agent",
  "tags": {
    "team": "support",
    "channel": "api"
  }
}
```

Expected response:

```http
201 Created
```

Return the canonical persisted event.

## 21.2 Idempotent Duplicate

For an already-ingested `event_id`:

```http
200 OK
```

or `201 Created` according to the chosen API convention, but the behavior must be documented and consistent.

Prefer:

```text
201 on new event
200 on existing event
```

## 21.3 Batch Ingestion

```http
POST /api/v1/model-usage/events/batch
```

Requirements:

- Accept multiple events.
- Maximum batch size configurable; default recommended: 100.
- Validate each event.
- Preserve per-event success/failure information.
- Do not lose valid events because another event in the batch is invalid unless the API explicitly chooses atomic semantics.

Recommended response:

```json
{
  "accepted": 8,
  "duplicates": 1,
  "rejected": 1,
  "results": [
    {
      "event_id": "abc",
      "status": "accepted",
      "event_id_persisted": "..."
    },
    {
      "event_id": "def",
      "status": "duplicate"
    },
    {
      "event_id": "ghi",
      "status": "rejected",
      "error": "model_id does not exist"
    }
  ]
}
```

## 21.4 Get Event

```http
GET /api/v1/model-usage/events/{event_id}
```

Return a single event.

## 21.5 List Events

```http
GET /api/v1/model-usage/events
```

Supported filters:

```text
model_id
model_version_id
deployment_id
application_id
agent_id
status
source
environment
started_from
started_to
request_id
trace_id
operation_name
page
page_size
sort
```

Pagination must be bounded.

Recommended default:

```text
page_size = 50
max_page_size = 200
```

The implementation may use cursor pagination if that matches the existing API framework. Do not introduce a second pagination paradigm unnecessarily.

## 21.6 Model Usage Statistics

```http
GET /api/v1/model-usage/stats
```

Filters:

```text
model_id
model_version_id
deployment_id
application_id
agent_id
environment
started_from
started_to
granularity
```

Supported `granularity` for Phase 1E:

```text
total
hour
 day
```

Daily granularity is sufficient if hourly aggregation becomes too complex; however the service should be structured to support both.

Example response:

```json
{
  "period": {
    "from": "2026-10-01T00:00:00Z",
    "to": "2026-10-05T23:59:59Z"
  },
  "filters": {
    "model_id": "..."
  },
  "totals": {
    "requests": 18234,
    "successful_requests": 17643,
    "failed_requests": 591,
    "error_rate": 0.0324,
    "input_tokens": 17324889,
    "output_tokens": 8248194,
    "total_tokens": 25573083,
    "avg_latency_ms": 1214.37,
    "min_latency_ms": 281,
    "max_latency_ms": 4921
  },
  "buckets": []
}
```

P95/P99 latency can be added if the database/query design supports it cleanly. It is not mandatory for initial MVP acceptance unless the repository already exposes percentile utilities.

---

# 22. Statistics Definition

Basic statistics must have deterministic definitions.

## 22.1 Requests

```text
requests = COUNT(events)
```

## 22.2 Successful Requests

```text
successful_requests = COUNT(status = success)
```

## 22.3 Failed Requests

```text
failed_requests = COUNT(status = error)
```

`cancelled` may be reported separately.

## 22.4 Error Rate

```text
error_rate = failed_requests / requests
```

Return `0` when requests are zero.

## 22.5 Token Totals

```text
input_tokens  = SUM(input_tokens)
output_tokens = SUM(output_tokens)
total_tokens  = SUM(total_tokens)
```

Null values should be ignored by aggregation.

## 22.6 Average Latency

```text
avg_latency_ms = AVG(duration_ms)
```

Do not treat missing latency as zero.

## 22.7 Time Bucket

Bucket by UTC unless the API explicitly adds timezone support later.

---

# 23. Service Layer

Create a dedicated service, for example:

```text
ModelUsageService
```

Responsibilities:

- Validate event relationships.
- Normalize provider telemetry.
- Derive duration where appropriate.
- Derive total token count when allowed.
- Sanitize errors/metadata.
- Enforce idempotency semantics.
- Persist immutable events.
- Query usage events.
- Calculate statistics.
- Keep API routes thin.

The service must not contain FastAPI-specific request/response concerns.

Suggested interface:

```python
class ModelUsageService:
    async def record_event(self, event: UsageEventCreate) -> UsageEventResponse: ...

    async def record_batch(
        self,
        events: list[UsageEventCreate],
    ) -> UsageEventBatchResponse: ...

    async def get_event(self, event_id: str) -> UsageEventResponse | None: ...

    async def list_events(
        self,
        filters: UsageEventFilter,
    ) -> PaginatedResult[UsageEventResponse]: ...

    async def get_stats(
        self,
        query: UsageStatsQuery,
    ) -> UsageStatsResponse: ...
```

Use the project's existing repository/service naming pattern where available.

---

# 24. Repository Layer

Create a repository abstraction, for example:

```text
ModelUsageRepository
```

Responsibilities:

- `insert_event`
- `get_by_event_id`
- `get_by_id`
- `list_events`
- `aggregate_stats`

Do not put HTTP or Pydantic concerns inside the repository.

The repository must use parameterized SQLAlchemy expressions and avoid raw SQL unless required for PostgreSQL-specific aggregation.

---

# 25. Transaction Semantics

For single-event ingestion:

```text
validate
  ↓
insert
  ↓
commit
  ↓
return
```

For a batch:

Prefer one transaction for the whole batch only if the response semantics are atomic.

For the recommended partial-success behavior, process each event safely and ensure a single bad event does not cause valid events to disappear.

Use the repository's existing transaction/session pattern.

The application should use SQLAlchemy's async PostgreSQL support if the surrounding Auditra backend is async. SQLAlchemy documents native asyncio support through `AsyncSession`/async-compatible dialects. citeturn460576search4turn460576search12

---

# 26. FastAPI Integration

Create routes under the project's existing router organization.

Recommended structure:

```text
app/
├── api/
│   └── v1/
│       └── model_usage.py
├── schemas/
│   └── model_usage.py
├── services/
│   └── model_usage_service.py
├── repositories/
│   └── model_usage_repository.py
├── models/
│   └── model_usage.py
├── integrations/
│   └── usage/
│       ├── base.py
│       ├── langchain.py
│       └── langgraph.py
└── tests/
    ├── unit/
    ├── integration/
    └── e2e/
```

Reuse the existing project structure if it differs.

Do not create a parallel application architecture only for this feature.

FastAPI middleware may be used later for generic HTTP correlation metadata, but request middleware alone is not sufficient to capture model-level token usage; model execution telemetry must come from the model/framework integration. FastAPI middleware runs around HTTP request/response processing, while background tasks are appropriate only for lightweight post-response work. citeturn460576search11turn460576search0

---

# 27. Telemetry Client / Collector

Create an internal abstraction that all framework integrations use.

Example:

```python
class UsageTelemetryCollector(Protocol):
    async def emit(self, event: UsageEventCreate) -> None: ...
```

Recommended implementation layers:

```text
AuditraUsageCollector
        │
        ▼
UsageEventNormalizer
        │
        ▼
UsageEventTransport
        │
        ├── direct repository/service (server-side)
        └── HTTP batch endpoint (external SDK)
```

For the initial monorepo POC, the collector may call the internal service directly.

The abstraction must make future external SDK transport possible.

---

# 28. LangChain Integration

## 28.1 Requirement

Provide a LangChain callback integration that observes model execution and emits one normalized Auditra usage event per logical model execution.

Current LangChain Python exposes callback hooks for model start/end/error and supports callbacks through `RunnableConfig`; metadata is propagated to child runnables and callbacks. The implementation should use these public callback/config mechanisms rather than private/internal APIs. citeturn441750search0turn441750search1turn441750search4

## 28.2 Callback Responsibilities

At model start:

Capture:

```text
run_id
parent_run_id
serialized/model identity when available
start timestamp
metadata
tags
```

At model end:

Capture:

```text
end timestamp
response usage metadata when available
provider response/request ID when available
```

Then normalize into `UsageEventCreate`.

At model error:

Capture:

```text
error type
sanitized error message
end timestamp
status=error
```

LangChain's callback API explicitly exposes model start/end/error lifecycle hooks; use the appropriate chat-model hook for chat models and LLM hook for non-chat models. citeturn441750search1turn441750search4turn441750search7

## 28.3 Configuration Context

The integration must support metadata such as:

```python
config = {
    "tags": ["auditra", "production"],
    "metadata": {
        "auditra_model_id": "<uuid>",
        "auditra_model_version_id": "<uuid>",
        "auditra_deployment_id": "<uuid>",
        "auditra_application_id": "<uuid>",
        "auditra_agent_id": "<uuid>",
        "auditra_operation": "support_agent",
    },
}
```

LangChain's current `RunnableConfig` supports tags, metadata, callbacks, run names, and run IDs; metadata values are JSON-serializable and may flow to child operations. citeturn441750search0turn441750search9

## 28.4 Do Not Depend on Provider-Specific Shapes

Token metadata differs across providers.

Create a provider-neutral extraction function:

```python
def extract_token_usage(response: Any) -> TokenUsage | None:
    ...
```

Supported sources should include common provider metadata patterns where present, but the extractor must fail safely when usage data is unavailable.

Do not make provider-specific assumptions part of the database contract.

---

# 29. LangGraph Integration

## 29.1 Requirement

Provide a LangGraph-compatible integration that records model calls executed inside a graph/agent.

The implementation should rely on LangChain's public callback/config propagation mechanisms where possible because LangGraph model execution commonly occurs through LangChain runnables/models.

LangChain's current callback stack supports chain, agent, tool, retriever, and model lifecycle events, and callback metadata can propagate through nested runnables. citeturn441750search0turn441750search1

## 29.2 Graph-Level Correlation

The following metadata must be supported when supplied by the graph/application:

```text
agent_id
application_id
model_id
model_version_id
deployment_id
trace_id
parent_run_id
operation_name
```

Example:

```python
result = graph.invoke(
    input_state,
    config={
        "tags": ["auditra", "agent"],
        "metadata": {
            "auditra_model_id": model_id,
            "auditra_agent_id": agent_id,
            "auditra_application_id": application_id,
            "auditra_operation": "research_agent",
        },
    },
)
```

The integration must not require Auditra-specific graph node implementations just to capture model usage.

---

# 30. Usage Context Resolution

Telemetry producers may know only some inventory identifiers.

Resolve context in this priority order:

```text
1. Explicit Auditra metadata
2. Explicit invocation arguments
3. Registered mapping/context object
4. Provider/model identity lookup
5. Unknown/unresolved
```

Do not create a model record automatically as part of Model Usage. Automatic discovery belongs to sub-feature 6.

Example:

```text
LangChain model = ChatOpenAI(model="gpt-4o")

metadata includes auditra_model_id
        │
        ▼
use explicit model_id
```

If no Auditra model identity is available, the collector may:

- reject the event with a clear configuration error in strict mode, or
- accept it into a future/unresolved ingestion path.

For Phase 1E, prefer **strict application integration tests** that demonstrate a registered model ID is explicitly passed through metadata.

---

# 31. Telemetry Failure Policy

Telemetry must not break inference by default.

Recommended modes:

```text
BEST_EFFORT (default)
STRICT (testing/debugging)
```

## 31.1 BEST_EFFORT

```python
try:
    await collector.emit(event)
except Exception:
    logger.exception("Auditra usage telemetry failed")
```

The original model result/error must remain unchanged.

## 31.2 STRICT

Telemetry failure propagates to the caller.

Use only for integration tests, local debugging, or explicitly configured environments.

---

# 32. Asynchronous Delivery

Direct synchronous persistence is acceptable for the initial POC.

The design should support a future asynchronous path:

```text
LangChain/LangGraph
      │
      ▼
collector.emit()
      │
      ▼
Redis queue
      │
      ▼
worker
      │
      ▼
PostgreSQL
```

Redis must **not** become a mandatory dependency for the MVP if PostgreSQL persistence is already functional.

FastAPI `BackgroundTasks` may be used only for lightweight same-process work. For durable queues or heavier workloads, the framework documentation recommends a dedicated queue/job system; Redis is already part of the Auditra target architecture and may serve this future role. citeturn460576search0

---

# 33. Concurrency

The implementation must be safe for concurrent telemetry ingestion.

Test scenarios:

- Same `event_id` submitted simultaneously.
- Different events for same model simultaneously.
- Batch ingestion concurrent with single-event ingestion.
- Statistics query while events are being inserted.

Unique DB constraints must be the final safeguard against duplicate event rows.

---

# 34. Query Performance Requirements

Basic statistics queries must always use bounded time ranges.

Example:

```text
started_at >= :from
AND started_at < :to
```

Avoid:

```text
SELECT COUNT(*) FROM model_usage_events;
```

for dashboard/statistics paths unless the endpoint is explicitly a global unbounded administrative query.

Tests should create enough events to demonstrate that the expected indexes are selected for the common filter paths where practical.

---

# 35. Pagination

Event listing must never return an unbounded number of rows.

Default:

```text
page_size = 50
```

Maximum:

```text
page_size = 200
```

Sort:

```text
started_at DESC, id DESC
```

Use a deterministic secondary sort to avoid duplicate/missing rows across pages when timestamps are equal.

---

# 36. API Error Model

Follow Auditra's existing API error structure.

At minimum support:

```text
400 Bad Request
401 Unauthorized (if authentication already exists)
403 Forbidden (if authorization already exists)
404 Not Found
409 Conflict
422 Validation Error
500 Internal Server Error
```

Usage event validation errors should be explicit.

Examples:

```text
Model not found
Model version does not belong to model
Deployment does not belong to model
Duplicate event_id with conflicting payload
Invalid timestamp range
Invalid token count
```

Do not expose database stack traces.

---

# 37. Logging

Log operational telemetry about the telemetry pipeline, not model payloads.

Good:

```text
usage event persisted
usage event duplicate
usage event rejected
usage telemetry failed
```

Bad:

```text
full prompt
full model completion
API key
authorization header
raw provider credential
```

Structured logs should include:

```text
event_id
model_id
request_id
trace_id
status
source
duration_ms
```

when available.

---

# 38. Security Requirements

1. Never store API credentials in usage events.
2. Never log authorization headers.
3. Sanitize provider exceptions before persistence.
4. Limit metadata size.
5. Limit error message size.
6. Reject unexpectedly large batch payloads.
7. Validate UUIDs before database queries.
8. Avoid allowing arbitrary metadata to overwrite first-class fields.
9. Keep prompt/response content disabled by default.
10. Do not persist secrets accidentally copied into metadata.

Future versions should implement tenant isolation and field-level policy enforcement if the broader Auditra authorization architecture requires it.

---

# 39. Metadata Limits

Suggested Phase 1E limits:

```text
batch_size <= 100
metadata serialized size <= 32 KB per event
tags <= 50 keys
string metadata value <= 512 chars
error_message <= 2000 chars
operation_name <= 255 chars
```

These are defaults, not hard requirements if the existing repository has centralized limits. Reuse project-wide request size configuration when available.

---

# 40. Migration

Create a versioned Alembic migration for:

```text
model_usage_events
```

Migration must include:

- table
- foreign keys to existing inventory tables
- unique constraint/index for `event_id`
- required query indexes
- database check constraints where supported

Migration must be reversible.

Do not modify previous inventory migrations unnecessarily.

---

# 41. API Dependency Injection

Routes should obtain dependencies through the project's existing dependency mechanism.

Recommended dependency chain:

```text
FastAPI Route
    │
    ├── DB session
    ├── ModelUsageRepository
    └── ModelUsageService
```

Do not instantiate repository/service/database connections directly inside route functions.

---

# 42. Suggested Module Breakdown

```text
model_usage/
├── __init__.py
├── enums.py
├── domain.py
├── schemas.py
├── repository.py
├── service.py
├── normalizer.py
├── sanitization.py
├── statistics.py
├── dependencies.py
├── routes.py
│
└── integrations/
    ├── __init__.py
    ├── base.py
    ├── langchain.py
    └── langgraph.py
```

Exact directory placement must follow existing Auditra conventions.

---

# 43. Domain Objects

Prefer explicit domain enums.

Example:

```python
class UsageStatus(str, Enum):
    SUCCESS = "success"
    ERROR = "error"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class UsageSource(str, Enum):
    API = "api"
    SDK = "sdk"
    LANGCHAIN = "langchain"
    LANGGRAPH = "langgraph"
    INTERNAL = "internal"
    CONNECTOR = "connector"
    MANUAL = "manual"


class TokenUsageSource(str, Enum):
    PROVIDER_REPORTED = "provider_reported"
    CALCULATED = "calculated"
    UNAVAILABLE = "unavailable"


class CostSource(str, Enum):
    PROVIDER_REPORTED = "provider_reported"
    LOCAL_ESTIMATE = "local_estimate"
    UNAVAILABLE = "unavailable"
```

Do not use raw strings throughout the codebase when an enum is already appropriate.

---

# 44. Normalization Layer

Provider/framework integrations must produce a common normalized structure.

Example:

```python
@dataclass
class NormalizedUsage:
    event_id: str
    model_id: UUID
    model_version_id: UUID | None
    deployment_id: UUID | None
    application_id: UUID | None
    agent_id: UUID | None

    status: UsageStatus
    source: UsageSource

    started_at: datetime
    completed_at: datetime | None
    duration_ms: int | None

    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None

    request_id: str | None
    trace_id: str | None
    parent_run_id: str | None

    provider_request_id: str | None
    framework: str | None
    framework_version: str | None

    error_type: str | None
    error_code: str | None
    error_message: str | None

    tags: dict[str, str]
    metadata: dict[str, Any]
```

The normalized object should be converted to the Pydantic/domain schema before persistence.

---

# 45. Fingerprint for Idempotency Conflict Detection

Create a deterministic canonical fingerprint from meaningful event fields.

Do not include `created_at`.

Recommended conceptual input:

```text
event_id
model_id
model_version_id
deployment_id
application_id
agent_id
status
started_at
completed_at
duration_ms
input_tokens
output_tokens
total_tokens
request_id
trace_id
provider_request_id
source
```

The exact fingerprint algorithm may be SHA-256 over canonical JSON.

Store the fingerprint in the database if useful.

Recommended field:

```text
payload_hash VARCHAR(64) NOT NULL
```

This is optional if the existing project already has an equivalent idempotency strategy.

---

# 46. Statistics Query Implementation

Prefer one well-structured aggregation query for total statistics rather than N separate queries for:

- request count
- success count
- error count
- token sums
- average latency

Example conceptual SQL:

```sql
SELECT
    COUNT(*) AS requests,
    COUNT(*) FILTER (WHERE status = 'success') AS successful_requests,
    COUNT(*) FILTER (WHERE status = 'error') AS failed_requests,
    COALESCE(SUM(input_tokens), 0) AS input_tokens,
    COALESCE(SUM(output_tokens), 0) AS output_tokens,
    COALESCE(SUM(total_tokens), 0) AS total_tokens,
    AVG(duration_ms) AS avg_latency_ms,
    MIN(duration_ms) AS min_latency_ms,
    MAX(duration_ms) AS max_latency_ms
FROM model_usage_events
WHERE started_at >= :from_ts
  AND started_at < :to_ts;
```

Use SQLAlchemy expressions where practical.

---

# 47. Time-Series Statistics

For daily statistics:

```text
DATE_TRUNC('day', started_at AT TIME ZONE 'UTC')
```

For hourly statistics:

```text
DATE_TRUNC('hour', started_at AT TIME ZONE 'UTC')
```

Only implement granularities that are supported by the API contract.

Do not create a separate aggregate table in Phase 1E unless measured performance requires it.

---

# 48. Example Event: Successful LangChain Call

```python
from langchain_openai import ChatOpenAI

model = ChatOpenAI(model="<configured-model>")

config = {
    "tags": ["auditra", "production"],
    "metadata": {
        "auditra_model_id": str(model_id),
        "auditra_model_version_id": str(version_id),
        "auditra_deployment_id": str(deployment_id),
        "auditra_application_id": str(application_id),
        "auditra_agent_id": str(agent_id),
        "auditra_operation": "support_agent",
    },
    "callbacks": [auditra_callback],
}

result = model.invoke(messages, config=config)
```

The callback should transform the model lifecycle into a single Auditra event.

Do not require application developers to manually construct a usage event after every model invocation when the callback integration is enabled.

---

# 49. Example Event: LangGraph Agent

```text
Application
    │
    ▼
LangGraph
    │
    ├── planner node
    │      │
    │      └── Model A → usage event #1
    │
    ├── tool node
    │
    └── response node
           │
           └── Model A → usage event #2
```

The statistics layer must distinguish:

```text
2 model requests
1 agent invocation
```

This is important: **a Model Usage event represents model usage, not an entire agent run**.

Agent-level aggregate statistics may be introduced later.

---

# 50. Duplicate Model Call Semantics

If an agent retries a model invocation:

```text
Attempt 1 → event A
Attempt 2 → event B
```

Both should be stored if they are genuinely separate provider executions.

Only repeated delivery of the **same event** with the **same event ID** is deduplicated.

Do not deduplicate based on:

- same model
- same prompt
- same timestamp
- same agent

unless an explicit idempotency ID is provided.

---

# 51. Error and Retry Semantics

Example:

```text
Model request
   │
   ├── provider timeout
   │
   ├── retry
   │
   └── success
```

Recommended MVP semantics:

```text
attempt 1 → error event
attempt 2 → success event
```

Alternatively, a producer may represent a final logical request with `retry_count=1`.

Do not automatically synthesize separate error events unless the integration actually observes separate provider executions.

---

# 52. Relationship Consistency

Model Usage must never mutate inventory master data automatically.

For example, when a usage event references an unknown `model_id`:

```text
DO NOT create model
DO NOT create version
DO NOT create deployment
```

Return a validation/domain error.

Discovery and registration are separate features.

---

# 53. Observability of Auditra Itself

The Model Usage implementation should emit internal logs/metrics for:

```text
usage_events_received_total
usage_events_persisted_total
usage_events_duplicate_total
usage_events_rejected_total
usage_telemetry_failures_total
usage_batch_size
usage_persistence_latency_ms
```

These are internal platform metrics and are separate from AI model usage events.

Do not insert platform metrics into `model_usage_events`.

---

# 54. Testing Strategy

Testing is mandatory.

## 54.1 Unit Tests

Test:

- schema validation
- enum validation
- duration derivation
- total-token derivation
- error sanitization
- metadata size validation
- idempotency fingerprint
- statistics calculation helpers
- provider metadata normalization

## 54.2 Repository Tests

Test:

- insert
- get
- list
- filtering
- pagination
- aggregation
- unique `event_id`
- foreign-key validation

## 54.3 API Tests

Test:

```text
POST single success
POST single failure
POST duplicate same payload
POST duplicate conflicting payload
POST batch
GET event
GET list
GET stats
```

## 54.4 Integration Tests

Run against a real PostgreSQL instance/container.

Do not use SQLite as the only integration database because PostgreSQL-specific types/constraints/query behavior are relevant.

## 54.5 LangChain Tests

Use a deterministic fake/mock model integration where possible.

Test:

1. model start callback fires.
2. model end callback creates success event.
3. model error callback creates error event.
4. metadata contains Auditra identifiers.
5. token usage extraction works when provider metadata exists.
6. missing token metadata does not fail the model call.
7. telemetry failure does not fail the model call in best-effort mode.
8. strict telemetry mode propagates telemetry errors.

## 54.6 LangGraph Tests

Create a small deterministic graph/agent containing at least one model call.

Verify:

```text
graph invocation
     ↓
model callback
     ↓
Auditra usage event
     ↓
PostgreSQL
```

Also verify that parent/trace metadata is preserved where the framework provides it.

---

# 55. End-to-End Acceptance Test

The implementation is not complete until this scenario passes:

```text
1. Create/register a model through existing Model Registration API.
2. Create or select its version through Model Versioning.
3. Create/select a deployment through Model Deployment.
4. Create/select application and agent through Association.
5. Execute a real LangChain model call.
6. Pass Auditra inventory IDs through LangChain config metadata.
7. Callback emits usage event.
8. Usage event is persisted in PostgreSQL.
9. GET /model-usage/events returns the event.
10. GET /model-usage/stats includes the request.
11. Execute a LangGraph agent using the same model.
12. A second usage event is persisted.
13. Statistics show the expected request count and token/latency totals.
14. Repeat delivery of the same event ID.
15. Statistics do not double-count the duplicate.
```

This is the primary proof that Model Usage is integrated correctly with the rest of AI Inventory.

---

# 56. Test Data

Use deterministic fixture IDs and timestamps.

Example model fixture:

```text
provider = openai
model_name = gpt-4o-mini
model_type = chat
```

Example usage fixtures:

```text
success event:
input_tokens=100
output_tokens=50
total_tokens=150
duration_ms=900

error event:
status=error
error_type=TimeoutError
duration_ms=2000
```

Do not depend on paid external APIs for CI.

Live provider calls may exist as optional local integration tests guarded by environment variables.

---

# 57. Mock Provider / Fake Model

Create a tiny deterministic fake model or test double to simulate:

```text
success with token metadata
success without token metadata
failure
slow response
```

The fake provider should be sufficient to validate the collector without network access.

---

# 58. Configuration

Recommended settings:

```text
AUDITRA_USAGE_ENABLED=true
AUDITRA_USAGE_MODE=best_effort
AUDITRA_USAGE_BATCH_SIZE=100
AUDITRA_USAGE_METADATA_MAX_BYTES=32768
AUDITRA_USAGE_STORE_PAYLOADS=false
AUDITRA_USAGE_DEFAULT_PAGE_SIZE=50
AUDITRA_USAGE_MAX_PAGE_SIZE=200
```

Use the project's existing settings/config mechanism.

Do not add duplicated environment parsing logic.

---

# 59. Feature Flag Behavior

If `AUDITRA_USAGE_ENABLED=false`:

- REST ingestion endpoints may remain available for administrative usage ingestion, or be disabled according to global project conventions.
- Framework callbacks should become no-op collectors.
- Model execution must not be affected.

The safest default for application integrations is a no-op collector when disabled.

---

# 60. Audit Integration

Full Auditra Audit is explicitly a later sub-feature.

Do not build the complete audit subsystem here.

However, usage events themselves must contain immutable timestamps and source context so future audit and compliance functionality can reference them.

Do not add `updated_at` to the usage event unless an explicit business need exists; events are intended to be immutable.

---

# 61. Immutability

After insertion, a usage event should not be updated through the public API.

Do not implement:

```http
PUT /model-usage/events/{id}
PATCH /model-usage/events/{id}
```

in Phase 1E.

If administrative correction is required later, implement an explicit correction/event-reconciliation mechanism rather than mutable telemetry rows.

---

# 62. Deletion

Do not expose public delete endpoints for usage events in Phase 1E.

Deletion/retention must be addressed by future data-retention policy implementation.

Database migration rollback is not the same as runtime event deletion.

---

# 63. Data Ownership

Model Usage owns:

```text
model_usage_events
usage ingestion
usage normalization
usage statistics
```

Model Usage does not own:

```text
models
model_versions
deployments
applications
agents
provider credentials
pricing catalog
prompt evaluation
```

---

# 64. API Documentation

FastAPI OpenAPI documentation should expose:

- request schemas
- response schemas
- enums
- filter query parameters
- examples
- error responses

Document that prompt/completion payloads are not stored by default.

---

# 65. README / Developer Documentation

Add/update documentation covering:

1. What Model Usage is.
2. Event lifecycle.
3. API endpoints.
4. Event JSON example.
5. LangChain integration example.
6. LangGraph integration example.
7. Best-effort vs strict telemetry.
8. Privacy defaults.
9. Running tests.
10. Local PostgreSQL setup.

Example developer workflow:

```text
register model
    ↓
register version
    ↓
register deployment
    ↓
associate agent/application
    ↓
run LangChain/LangGraph
    ↓
inspect usage events
    ↓
query stats
```

---

# 66. Recommended CLI/Test Commands

Adapt commands to the repository's actual package manager and scripts.

Examples:

```bash
pytest tests/unit/model_usage -q
pytest tests/integration/model_usage -q
pytest tests/e2e/model_usage -q
pytest -q
```

Migration example:

```bash
alembic upgrade head
```

API health check/example:

```bash
curl http://localhost:8000/health
```

Do not hard-code commands if the repository uses `uv`, Poetry, Make, Taskfile, or another project-specific runner. Discover and follow the existing project convention first.

---

# 67. Implementation Order

OpenCode should implement this feature in the following order.

## Phase 1 — Repository Reconnaissance

Before writing code:

1. Inspect the repository structure.
2. Locate FastAPI app entrypoint.
3. Locate existing database/session setup.
4. Locate SQLAlchemy base/model conventions.
5. Locate Alembic configuration.
6. Locate Pydantic schema conventions.
7. Locate API router conventions.
8. Locate service/repository patterns.
9. Locate authentication/authorization abstractions.
10. Locate existing model/version/deployment/application/agent entities.
11. Locate existing test infrastructure.
12. Locate existing LangChain/LangGraph dependencies.

Do not overwrite established architecture.

## Phase 2 — Domain + Schema

Implement:

- enums
- domain event structure
- Pydantic schemas
- validation rules
- normalization helpers
- sanitization helpers

## Phase 3 — Database

Implement:

- SQLAlchemy model
- migration
- constraints
- indexes

## Phase 4 — Repository

Implement:

- insert
- idempotent lookup
- get
- list
- statistics aggregation

## Phase 5 — Service

Implement:

- event validation
- normalization
- idempotency
- persistence
- list/get
- statistics

## Phase 6 — API

Implement:

- single ingestion
- batch ingestion
- get event
- list events
- statistics

## Phase 7 — LangChain

Implement callback integration and end-to-end test.

## Phase 8 — LangGraph

Implement graph-compatible context propagation and end-to-end test.

## Phase 9 — Hardening

Add:

- logging
- limits
- error handling
- telemetry failure isolation
- concurrency tests
- documentation

## Phase 10 — Full Validation

Run unit + integration + e2e tests.

Then:

```text
format
lint
type check
unit tests
integration tests
e2e tests
migration test
full test suite
```

## Phase 11 — Git Commit

Only after all tests pass:

```text
git status
git diff
git add <files>
git commit -m "feat(ai-inventory): add model usage tracking"
```

Do not push secrets, provider API keys, `.env` files, local DB data, or generated caches.

Follow the repository's actual branch/push policy.

---

# 68. Acceptance Criteria

The feature is complete when all criteria below are satisfied.

## AC-01 Event Persistence

A valid usage event can be persisted to PostgreSQL.

## AC-02 Model Correlation

Every event references an existing `model_id`.

## AC-03 Optional Inventory Context

Events may reference version/deployment/application/agent and invalid relationships are rejected.

## AC-04 Immutability

No public update/delete API exists for usage events.

## AC-05 Idempotency

Repeated delivery of the same `event_id` does not create duplicate rows or inflate statistics.

## AC-06 Conflict Detection

Same `event_id` with materially different data is detected as an idempotency conflict.

## AC-07 Batch Ingestion

Batch events can be ingested with per-event outcomes and bounded batch size.

## AC-08 Querying

Events can be filtered by model and time at minimum, with the additional supported dimensions documented above.

## AC-09 Statistics

The API returns request count, success count, failure count, error rate, token totals, and latency statistics for a bounded time range.

## AC-10 Privacy

Raw prompts and completions are not persisted by default.

## AC-11 LangChain

A real LangChain model invocation produces a usage event when configured with the Auditra callback/context.

## AC-12 LangGraph

A real LangGraph agent model invocation produces a usage event.

## AC-13 Failure Isolation

Telemetry failure does not fail the application/model invocation in best-effort mode.

## AC-14 PostgreSQL Integration

Integration tests run against PostgreSQL, not only SQLite.

## AC-15 Documentation

API and integration usage are documented.

## AC-16 Existing Architecture

Implementation follows existing Auditra architecture instead of creating conflicting parallel patterns.

---

# 69. Definition of Done

```text
[ ] Repository structure inspected
[ ] Existing model/version/deployment/application/agent entities reused
[ ] Domain enums implemented
[ ] Pydantic schemas implemented
[ ] SQLAlchemy model implemented
[ ] Alembic migration created
[ ] DB constraints created
[ ] Query indexes created
[ ] Repository implemented
[ ] Service implemented
[ ] Single event API implemented
[ ] Batch API implemented
[ ] Event lookup API implemented
[ ] Event list API implemented
[ ] Statistics API implemented
[ ] Idempotency implemented
[ ] Error sanitization implemented
[ ] Metadata limits implemented
[ ] LangChain callback implemented
[ ] LangGraph integration implemented
[ ] Best-effort telemetry implemented
[ ] Strict telemetry mode implemented/tested
[ ] Unit tests pass
[ ] Repository/integration tests pass
[ ] API tests pass
[ ] LangChain integration tests pass
[ ] LangGraph integration tests pass
[ ] End-to-end scenario passes
[ ] OpenAPI docs verified
[ ] Developer documentation updated
[ ] Lint passes
[ ] Type checks pass
[ ] Full test suite passes
[ ] Git diff reviewed
[ ] Git commit created
```

---

# 70. Explicit OpenCode Instructions

OpenCode must treat this file as an **implementation specification**, not a request for architectural brainstorming.

Before implementation:

1. Inspect the existing Auditra repository.
2. Compare current architecture with this specification.
3. Reuse existing patterns whenever they satisfy the requirements.
4. Do not rewrite unrelated modules.
5. Do not introduce frontend code.
6. Do not implement Model Discovery.
7. Do not implement the full Audit subsystem.
8. Do not implement pricing management.
9. Do not add hidden/background behavior that cannot be tested.
10. Keep external provider dependencies optional in tests.

During implementation:

- Keep routes thin.
- Keep domain logic in services.
- Keep persistence logic in repositories.
- Keep provider/framework normalization in integration adapters.
- Prefer public LangChain/LangGraph APIs.
- Keep usage events immutable.
- Make telemetry failure-safe.
- Avoid raw prompt/completion persistence.
- Use PostgreSQL as the integration-test database.
- Follow existing lint/type/test conventions.

After implementation:

1. Run the smallest relevant tests first.
2. Fix failures before adding unrelated enhancements.
3. Run the complete Model Usage test suite.
4. Run the complete Auditra backend test suite.
5. Review the database migration.
6. Review API OpenAPI output.
7. Review Git diff.
8. Commit only when tests pass.

Do not mark the feature complete merely because the server starts.

The feature is complete only when the **real LangChain/LangGraph → Auditra Usage Event → PostgreSQL → Usage Statistics** path has been demonstrated.

---

# 71. Future Extension Points

The design should leave clear extension points for:

```text
Model Usage
│
├── Cost management
├── Token/cost pricing catalog
├── Prompt/response capture with policy controls
├── PII redaction
├── Distributed tracing
├── OpenTelemetry integration
├── Streaming token events
├── Real-time usage dashboards
├── Usage anomaly detection
├── Budget/quotas
├── Rate limits
├── Model performance comparison
├── Agent-level usage
├── Model discovery
├── Governance policies
└── Audit/compliance reports
```

Do not implement these now unless required by the repository's existing foundations.

---

# 72. Reference Implementation Principle

The preferred implementation is:

```text
                 ┌──────────────────────────┐
                 │ LangChain / LangGraph    │
                 │ model execution          │
                 └────────────┬─────────────┘
                              │
                         callbacks
                              │
                              ▼
                 ┌──────────────────────────┐
                 │ Auditra Usage Callback   │
                 │ / Collector              │
                 └────────────┬─────────────┘
                              │
                        normalized event
                              │
                              ▼
                 ┌──────────────────────────┐
                 │ Model Usage Service       │
                 │ validation + idempotency │
                 └────────────┬─────────────┘
                              │
                              ▼
                 ┌──────────────────────────┐
                 │ PostgreSQL                │
                 │ model_usage_events       │
                 └────────────┬─────────────┘
                              │
                    aggregate / filtered query
                              │
                              ▼
                 ┌──────────────────────────┐
                 │ Usage Statistics API      │
                 └──────────────────────────┘
```

This is the core architecture to implement in Phase 1E.

---

# 73. Final Implementation Target

At the end of this sub-feature, Auditra must be able to answer programmatically:

```text
Which model was used?
Which version was used?
Which deployment served it?
Which application invoked it?
Which agent invoked it?
When did the invocation happen?
How long did it take?
Did it succeed or fail?
How many input/output/total tokens were consumed?
What provider request ID or execution ID is associated?
What environment/operation produced the usage?
How many model requests occurred in a time range?
What was the error rate?
What was the average/min/max latency?
```

And it must be able to demonstrate the answer through:

```text
FastAPI
   ↓
PostgreSQL
   ↓
real LangChain invocation
   ↓
real LangGraph invocation
   ↓
usage events
   ↓
statistics
```

That is the completion target for **AI Model Inventory → 5. Model Usage**.

---

# 74. Reference Documentation

Implementation should prefer current official documentation for the installed versions of the dependencies.

- FastAPI middleware and background tasks documentation
- SQLAlchemy 2.x asyncio and PostgreSQL documentation
- LangChain Python callback reference (`BaseCallbackHandler`, `RunnableConfig`)
- LangGraph/LangChain agent and observability documentation

Do not copy private/internal APIs from framework source code when a public API exists.

