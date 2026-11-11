# Auditra — Asset Discovery

## 1. Purpose

Asset Discovery is the second major platform capability of Auditra, following AI Model Inventory / Model Registration.

The purpose of Asset Discovery is to allow Auditra to identify AI-related assets that exist across an organization's technical environment and establish where those assets were observed.

Asset Discovery answers:

> **"What AI-related assets exist, where were they found, and what evidence do we have that they exist?"**

It does **not** replace canonical inventory systems.

### Core architectural principle

> **Discovery discovers and observes assets. Inventory owns canonical entities. Governance determines how those entities are governed.**

For example:

```text
Repository
    ↓
Asset Discovery
    ↓
"gpt-4o is referenced in application X"
    ↓
Reconciliation
    ↓
Existing Model Inventory record
```

Asset Discovery therefore acts as a discovery layer across Auditra rather than becoming a second model inventory.

---

# 2. Relationship to Existing Auditra Features

Auditra currently contains:

```text
Auditra
│
├── AI / Model Inventory
│   ├── Model Registration          ← IMPLEMENTED
│   ├── Model Versioning
│   ├── Model Deployment
│   ├── Application / Agent Association
│   ├── Model Usage
│   ├── Model Discovery
│   └── Audit
│
└── Asset Discovery                 ← CURRENT FEATURE
```

### Important distinction

**Model Discovery** under Model Inventory and **Asset Discovery** are different capabilities.

### Model Discovery

Discovers models specifically and helps populate/reconcile the Model Inventory.

### Asset Discovery

Discovers a broader set of AI-related assets, including:

- Models
- Model references
- Applications
- Agents
- Workflows
- Datasets
- Tools
- Endpoints
- Deployments
- AI services
- Other AI-related components

Asset Discovery may discover a model reference, but Model Inventory remains responsible for the canonical model record.

---

# 3. Asset Discovery Sub-Feature Map

Asset Discovery consists of the following sub-features:

```text
Asset Discovery
│
├── 1. Discovery Source Management
│
├── 2. Asset Discovery
│
├── 3. Discovered Asset Catalog
│
├── 4. Asset Classification & Metadata
│
├── 5. Asset Reconciliation
│
├── 6. Asset Deduplication
│
├── 7. Discovery Runs & History
│
├── 8. Asset Lifecycle
│
└── 9. Discovery Evidence
```

These are the **product-level sub-features**.

Implementation mechanisms such as connectors, detection rules, normalization and fingerprints are implementation components underneath these capabilities and should not become additional top-level product features.

---

# 4. Sub-Feature 1 — Discovery Source Management

## Objective

Define and manage the locations/systems from which Auditra can discover assets.

A discovery source represents:

> **Where Auditra is allowed and configured to look for assets.**

Examples:

- Git repository
- Local/project directory
- GitHub repository
- GitLab repository
- Kubernetes cluster
- Cloud account
- Model registry
- Container registry
- Future SaaS/API integration

## V1

Implement a generic discovery source abstraction with an initial repository/codebase source.

The design must allow additional source types later without changing the core discovered asset model.

## Requirements

A Discovery Source should have:

- ID
- Name
- Source type
- Configuration
- Status
- Created timestamp
- Updated timestamp
- Ownership/tenant boundary if applicable

Sensitive credentials must **not** be stored as plain configuration data.

## Example

```text
Discovery Source
----------------
name: payments-service
type: repository
location: /workspace/payments
status: active
```

## Non-goals

Do not implement every connector in V1.

The abstraction should support future connectors.

---

# 5. Sub-Feature 2 — Asset Discovery

## Objective

Actually inspect a configured source and identify AI-related assets.

This is the core discovery capability.

The discovery process should:

1. Load a discovery source.
2. Start a discovery run.
3. Invoke the appropriate connector.
4. Inspect the source.
5. Detect potential AI assets.
6. Produce normalized discovery results.
7. Store discovered assets.
8. Record evidence.
9. Reconcile with canonical Auditra entities where applicable.

## Initial V1 Discovery

Start with static repository/codebase discovery.

The scanner may inspect:

- Source code
- Configuration files
- Dependency manifests
- Environment/configuration references
- AI framework usage
- Model/provider references
- Agent/workflow declarations
- Relevant deployment/configuration files

## Important security boundary

Discovery must be **static analysis only** in V1.

It must not:

- Execute arbitrary discovered code
- Execute shell commands from the scanned repository
- Load untrusted application code
- Send discovered secrets to external services
- Automatically deploy anything
- Modify the source being scanned

---

# 6. Sub-Feature 3 — Discovered Asset Catalog

## Objective

Provide a persistent representation of everything Auditra discovers.

A discovered asset represents an observation made by the discovery system.

It is **not necessarily a canonical Auditra entity**.

## Initial Asset Types

Support an extensible asset type system.

Initial types:

```text
MODEL
MODEL_REFERENCE
APPLICATION
AGENT
WORKFLOW
DATASET
TOOL
DEPLOYMENT
ENDPOINT
AI_SERVICE
```

The system must allow additional types later.

## Discovered Asset

Conceptually:

```text
DiscoveredAsset
----------------
id
asset_type
name
normalized_identity
source_id
first_seen_at
last_seen_at
status
metadata
created_at
updated_at
```

The exact schema should be determined during implementation while respecting the architecture defined in this specification.

## Important distinction

```text
Discovered Asset
        ≠
Canonical Inventory Entity
```

A discovered asset may later be reconciled to a canonical entity.

---

# 7. Sub-Feature 4 — Asset Classification & Metadata

## Objective

Determine what a discovered item represents and capture useful normalized metadata.

Discovery may initially find something such as:

```text
openai.ChatCompletion(...)
```

or:

```text
model = "gpt-4o"
```

Classification determines:

```text
asset_type = MODEL_REFERENCE
```

and extracts metadata such as:

- Provider
- Model identifier
- Framework
- File
- Path
- Symbol/configuration location
- Environment
- Source identifier
- Detection method
- Relevant metadata

## Normalization

Different sources may describe the same asset differently.

Example:

```text
"openai:gpt-4o"
"gpt-4o"
"OpenAI GPT-4o"
```

The discovery layer should normalize representations sufficiently to support deterministic identity and reconciliation.

Normalization must remain separate from canonical governance ownership.

---

# 8. Sub-Feature 5 — Asset Reconciliation

## Objective

Connect discovered assets to canonical Auditra entities where a reliable match exists.

Example:

```text
Discovered:
MODEL_REFERENCE
provider = OpenAI
model = gpt-4o

        ↓

Reconciliation

        ↓

Canonical Model Inventory Record
```

## Reconciliation outcomes

The system should support:

```text
MATCHED
UNMATCHED
AMBIGUOUS
IGNORED
```

## Important rule

Do not automatically create canonical inventory entities merely because something was discovered unless the specification explicitly allows that behavior for the particular entity type.

For the initial implementation:

> Discovery should identify candidates and reconcile them to existing canonical entities.

Automatic registration can be introduced later as a controlled workflow.

---

# 9. Sub-Feature 6 — Asset Deduplication

## Objective

Prevent the same real-world asset from appearing as multiple unrelated discovered assets.

Potential identity signals include:

- Source
- Provider
- Asset type
- Canonicalized name
- Repository
- Path
- External identifier
- Model identifier
- Other deterministic identifiers

## Principle

Deduplication should be deterministic wherever possible.

Avoid fuzzy/AI-based deduplication in the first implementation.

Example:

```text
Repository A
  ↓
gpt-4o

Repository B
  ↓
gpt-4o
```

These may represent:

- The same canonical model
- Different references to the same model

The system should preserve the individual observations while allowing them to reconcile to the same canonical entity.

---

# 10. Sub-Feature 7 — Discovery Runs & History

## Objective

Track each execution of discovery.

A Discovery Run represents one execution against a discovery source.

Example:

```text
Discovery Run
-------------
source: payments-service
started_at
completed_at
status: COMPLETED

assets_detected: 18
assets_created: 12
assets_updated: 4
assets_unchanged: 2
errors: 0
```

## Run states

At minimum:

```text
PENDING
RUNNING
COMPLETED
FAILED
PARTIAL
```

## History

Auditra should retain enough information to answer:

- When was this source last scanned?
- What was discovered?
- What changed?
- Did the scan fail?
- When was an asset first seen?
- When was it last seen?

Discovery history must be separate from the current state of the asset.

---

# 11. Sub-Feature 8 — Asset Lifecycle

## Objective

Track the lifecycle of discovered assets as sources change over time.

An asset should not disappear simply because it was absent from one discovery run.

Possible states:

```text
DISCOVERED
ACTIVE
STALE
DISAPPEARED
IGNORED
```

The exact lifecycle transition rules should be implemented deterministically.

Example:

```text
Run 1:
gpt-4o discovered
        ↓
ACTIVE

Run 2:
gpt-4o discovered again
        ↓
ACTIVE

Run 3:
gpt-4o no longer present
        ↓
STALE / DISAPPEARED
```

The distinction between stale and disappeared should be explicitly defined by implementation rules rather than inferred dynamically.

---

# 12. Sub-Feature 9 — Discovery Evidence

## Objective

Preserve evidence explaining why Auditra believes an asset exists.

This is critical for future governance and audit capabilities.

Evidence may include:

- Discovery source
- Repository
- File path
- Line/location information where safely available
- Configuration key
- Detection rule
- Connector
- Timestamp
- Extracted metadata
- Source identifier

Example:

```text
Asset:
gpt-4o

Evidence:
source = payments-service
file = app/llm/client.py
detection = OpenAI model reference
observed_at = 2026-11-12
```

## Important principle

> Every discovered asset should be traceable back to the observation that produced it.

---

# 13. Discovery Architecture

The architecture should follow Auditra's existing modular-monolith design.

```text
API
 │
 ▼
Application / Service Layer
 │
 ▼
Discovery Domain
 │
 ├── Source Management
 ├── Discovery Orchestration
 ├── Classification
 ├── Normalization
 ├── Deduplication
 ├── Reconciliation
 └── Lifecycle
 │
 ▼
Repository Interfaces
 │
 ▼
PostgreSQL
```

External discovery integrations should be isolated behind connector interfaces.

```text
Discovery Engine
      │
      ├── Repository Connector
      ├── GitHub Connector       (future)
      ├── Kubernetes Connector   (future)
      ├── Cloud Connector        (future)
      └── Model Registry         (future)
```

The core domain must not depend directly on a specific provider.

---

# 14. Discovery Connector Abstraction

A connector should conceptually provide:

```text
DiscoveryConnector
    discover(source)
        ↓
    discovery observations
```

Each connector is responsible for understanding its source.

The domain layer is responsible for:

- Classification
- Normalization
- Deduplication
- Reconciliation
- Persistence
- Lifecycle
- Evidence

This prevents provider-specific logic from leaking throughout Auditra.

---

# 15. API Requirements

The implementation should expose APIs for:

### Discovery Sources

```text
POST   /discovery/sources
GET    /discovery/sources
GET    /discovery/sources/{id}
PATCH  /discovery/sources/{id}
DELETE /discovery/sources/{id}
```

### Discovery

```text
POST /discovery/sources/{id}/runs
GET  /discovery/runs
GET  /discovery/runs/{id}
```

### Discovered Assets

```text
GET /discovery/assets
GET /discovery/assets/{id}
```

Filtering should eventually support:

- Asset type
- Source
- Status
- Reconciliation status
- First seen
- Last seen

Exact route names may be adjusted to match the existing Auditra API conventions.

---

# 16. Database Requirements

Use PostgreSQL as the source of truth.

The implementation should introduce appropriate tables for:

- Discovery sources
- Discovery runs
- Discovered assets
- Discovery observations/evidence
- Reconciliation information

Use Alembic migrations.

Database design must support:

- Idempotent discovery
- Historical observations
- Source isolation
- Deterministic identity
- Future connectors
- Future multi-tenancy

Do not introduce Redis merely for discovery state.

---

# 17. Security Requirements

Asset Discovery is potentially processing untrusted repositories and configuration.

Therefore:

### Must NOT

- Execute discovered code
- Execute arbitrary shell commands
- Automatically install dependencies from discovered projects
- Store plaintext credentials
- Send secrets to external APIs
- Modify scanned repositories
- Automatically deploy discovered assets

### Should

- Treat all source content as untrusted input.
- Limit scanner scope.
- Validate paths.
- Avoid path traversal.
- Avoid unsafe deserialization.
- Sanitize metadata before persistence.
- Avoid logging secrets.

---

# 18. Idempotency

Running discovery against the same unchanged source multiple times must not create duplicate assets.

Example:

```text
Run 1 → 20 assets
Run 2 → same source, unchanged

Expected:
20 existing assets updated/observed
0 duplicate assets created
```

This is a core acceptance requirement.

---

# 19. V1 Scope

The first Asset Discovery implementation should focus on one complete vertical slice.

### V1 includes

- Discovery Source Management
- Repository/codebase discovery
- Discovery Run tracking
- Static AI asset detection
- Asset classification
- Metadata normalization
- Discovered Asset persistence
- Deterministic deduplication
- Basic reconciliation with Model Inventory
- Lifecycle tracking
- Discovery evidence
- REST APIs
- PostgreSQL persistence
- Alembic migration
- Unit/integration/API tests

### V1 does NOT include

- Kubernetes discovery
- Cloud discovery
- GitHub organization-wide scanning
- GitLab scanning
- Model registry integrations
- Runtime monitoring
- LLM-powered discovery
- Autonomous remediation
- Policy evaluation
- Risk scoring
- Security scanning
- Cost analysis
- Full frontend
- Distributed workers
- Redis-based orchestration
- Automatic model registration without explicit reconciliation rules

---

# 20. Implementation Strategy

Although this is one master specification, implementation must happen **one sub-feature at a time**.

Recommended sequence:

```text
Phase 1
Discovery Source Management
        ↓
Phase 2
Discovery Runs & History
        ↓
Phase 3
Asset Discovery
        ↓
Phase 4
Discovered Asset Catalog
        ↓
Phase 5
Asset Classification & Metadata
        ↓
Phase 6
Asset Deduplication
        ↓
Phase 7
Asset Reconciliation
        ↓
Phase 8
Asset Lifecycle
        ↓
Phase 9
Discovery Evidence
```

Each phase should be independently tested and committed.

---

# 21. OpenCode Implementation Rule

For each sub-feature, OpenCode should:

1. Read the complete `asset-discovery.md`.
2. Identify the current sub-feature being implemented.
3. Inspect the existing Auditra architecture.
4. Reuse existing Model Inventory patterns where appropriate.
5. Implement only the requested sub-feature.
6. Avoid implementing future sub-features prematurely.
7. Add/update migrations if required.
8. Add unit tests.
9. Add integration/API tests where applicable.
10. Run the relevant test suite.
11. Verify that existing functionality remains intact.
12. Report changed files and test results.
13. Do not modify unrelated architecture.

### Critical instruction

> **The master specification defines the complete Asset Discovery architecture. The implementation request defines which single sub-feature is currently being built.**

OpenCode must not interpret the existence of future sub-features as permission to implement them early.

---

# 22. Definition of Done for Each Sub-Feature

A sub-feature is complete only when:

- Domain behavior is implemented.
- Persistence is implemented where required.
- API behavior is implemented where required.
- Validation is implemented.
- Error handling is implemented.
- Tests are passing.
- Existing Auditra tests remain passing.
- Migration is clean and reversible where applicable.
- No unrelated functionality was introduced.
- The implementation follows the master Asset Discovery architecture.

---

# 23. Testing Strategy

Testing should exist at multiple levels.

### Unit Tests

Test:

- Classification
- Normalization
- Identity generation
- Deduplication
- Reconciliation
- Lifecycle transitions
- Validation

### Integration Tests

Test:

- PostgreSQL persistence
- Discovery runs
- Source management
- Asset persistence
- Reconciliation

### API Tests

Test:

- Source CRUD
- Discovery execution
- Run retrieval
- Asset listing
- Asset filtering
- Error responses

### Regression

Every Asset Discovery phase must ensure the existing Model Inventory functionality remains operational.

---

# 24. Observability

Discovery operations should produce structured logs containing:

- Source ID
- Run ID
- Connector
- Asset counts
- Duration
- Status
- Errors

Avoid logging:

- Secrets
- Tokens
- Credentials
- Full source contents

Metrics can be introduced incrementally.

---

# 25. Future Extensions

The architecture should leave room for:

```text
GitHub Discovery
GitLab Discovery
Kubernetes Discovery
AWS Discovery
GCP Discovery
Azure Discovery
Model Registry Discovery
Container Discovery
Runtime Discovery
LLM-assisted Classification
Scheduled Discovery
Event-driven Discovery
Cross-source Correlation
Policy Integration
Risk Analysis
Automated Governance Workflows
```

These are future capabilities and should not be implemented in the initial vertical slice.

---

# 26. Final Architectural Principles

The implementation must preserve these principles.

### Principle 1 — Discovery ≠ Inventory

Discovery finds assets.

Inventory owns canonical entities.

### Principle 2 — Evidence matters

Every discovered asset should be explainable.

### Principle 3 — Discovery is idempotent

Repeated scans must not create duplicate assets.

### Principle 4 — Sources are extensible

Adding GitHub, Kubernetes or cloud discovery should not require rewriting the domain model.

### Principle 5 — Static-first

The first implementation must favor safe static discovery over executing arbitrary workloads.

### Principle 6 — Canonical ownership

Asset Discovery should not silently become another Model Inventory.

### Principle 7 — History is preserved

The system should know not only what exists now, but what was observed previously.

### Principle 8 — Controlled implementation

Implement one sub-feature at a time while maintaining the master architecture.

---

# 27. Target End State

The completed Asset Discovery capability should allow an Auditra user to answer:

> **What AI assets exist in my environment?**

> **Where were they discovered?**

> **When were they first and last observed?**

> **What type of asset is each one?**

> **What evidence supports the discovery?**

> **Is this asset already represented in Auditra's canonical inventory?**

> **Has the asset changed, disappeared, or become stale?**

This becomes the foundation for future Auditra capabilities such as governance, risk, policy enforcement, runtime monitoring and audit.

---

# 28. Implementation Order for OpenCode

The implementation should be requested in this exact order:

```text
1. Discovery Source Management
2. Discovery Runs & History
3. Asset Discovery
4. Discovered Asset Catalog
5. Asset Classification & Metadata
6. Asset Deduplication
7. Asset Reconciliation
8. Asset Lifecycle
9. Discovery Evidence
```

After each sub-feature:

```text
Implement
   ↓
Test
   ↓
Review
   ↓
Commit
   ↓
Next sub-feature
```

**Do not ask OpenCode to implement the entire feature in one pass.**

The master specification is the architectural contract; each subsequent OpenCode prompt should reference this document and explicitly name **one sub-feature only**.