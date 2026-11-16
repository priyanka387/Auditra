
# Auditra — Feature 3: Risk & Compliance Management

## 1. Feature Overview

### Feature Name
**Risk & Compliance Management**

### Feature Number
**Feature 3**

### Purpose

Risk & Compliance Management is the core governance layer that transforms the AI inventory created by **Asset Discovery** into actionable governance information.

It should allow an organization to:

- identify AI-related risks
- assess and score those risks
- prioritize risks based on severity and likelihood
- define mitigation plans
- assign owners
- track remediation
- identify applicable regulatory and compliance requirements
- map requirements to organizational controls
- collect evidence
- identify compliance gaps
- track findings and remediation
- monitor compliance status over time
- generate audit-ready reports

The feature should treat risk and compliance as **continuous lifecycle processes**, rather than one-time assessments.

---

# 2. Relationship With Asset Discovery

Asset Discovery is the upstream source of AI-system information.

The relationship should be:

```text
Asset Discovery
      │
      ▼
AI Asset / System Inventory
      │
      ├──────────────┐
      ▼              ▼
Risk Management   Compliance Management
      │              │
      ▼              ▼
Risk Assessment   Requirement Mapping
      │              │
      ▼              ▼
Mitigation        Controls
      │              │
      └──────┬───────┘
             ▼
        Evidence / Findings
             │
             ▼
        Remediation
             │
             ▼
      Compliance Status
             │
             ▼
     Governance Reporting
```

An AI system discovered by Asset Discovery should therefore become a first-class object that can have:

```text
AI System
 ├── Risks
 ├── Assessments
 ├── Controls
 ├── Requirements
 ├── Evidence
 ├── Findings
 ├── Remediation Actions
 └── Compliance Status
```

---

# 3. Design Principles

### 3.1 Framework-neutral

Do not hard-code Auditra around a single regulation.

The architecture should support:

```text
Framework
   └── Requirements
          └── Controls
                 └── Evidence
```

This allows Auditra to support frameworks such as:

- NIST AI RMF
- ISO/IEC 42001
- EU AI Act
- internal corporate policies
- future AI regulations
- industry-specific requirements

ISO/IEC 42001 is specifically intended as an AI management system that organizations can use to establish, maintain, and continually improve AI governance and risk processes.

### 3.2 Risk ≠ Finding ≠ Control

These entities must remain separate.

```text
Risk
    = something that could negatively happen

Control
    = mechanism designed to prevent/detect/reduce the risk

Finding
    = observed failure, weakness, or gap

Remediation Action
    = action taken to resolve the finding/risk
```

### 3.3 Everything should be traceable

Every important governance decision should be traceable.

Example:

```text
AI System
   ↓
Risk
   ↓
Control
   ↓
Requirement
   ↓
Evidence
   ↓
Finding
   ↓
Remediation
   ↓
Verification
```

### 3.4 Risk management is continuous

Risk should not disappear after an assessment.

A system can change because of:

- new models
- new datasets
- new vendors
- new deployment environments
- new use cases
- model updates
- regulatory changes
- new incidents

NIST describes AI risk management as a lifecycle process and recommends monitoring risks as contexts, methods, and expectations evolve.

---

# 4. Subfeatures

Feature 3 should be divided into **9 subfeatures**.

## Subfeature 1 — Risk Register & Risk Taxonomy

### Purpose

Create the central repository for all risks identified across Auditra.

### Capabilities

Users should be able to:

- create risks
- edit risks
- archive risks
- categorize risks
- link risks to AI systems/assets
- link risks to business processes
- assign risk owners
- assign responsible teams
- define risk descriptions
- record risk sources
- record affected stakeholders

### Initial AI Risk Categories

Auditra should provide a default taxonomy containing categories such as:

```text
Safety
Security
Privacy
Fairness / Bias
Reliability
Accuracy
Robustness
Explainability
Transparency
Accountability
Data Quality
Data Governance
Legal / Regulatory
Third-Party / Vendor
Operational
Reputational
Financial
Human Oversight
Misuse / Abuse
Model Risk
```

The taxonomy must remain configurable.

### Risk Entity

Conceptually:

```text
Risk
- id
- name
- description
- category
- source
- asset_id
- owner_id
- status
- inherent_likelihood
- inherent_impact
- inherent_score
- residual_likelihood
- residual_impact
- residual_score
- created_at
- updated_at
```

### Output

A centralized **Risk Register**.

---

# Subfeature 2 — AI Risk Assessment

### Purpose

Allow organizations to formally assess the risks associated with an AI system.

### Assessment Types

Support:

```text
Initial Assessment
Periodic Assessment
Pre-Deployment Assessment
Post-Deployment Assessment
Change Assessment
Incident-Triggered Assessment
Vendor Assessment
```

### Assessment Questions

The assessment engine should support configurable questionnaires.

Examples:

```text
What is the intended purpose?

Who is affected?

What decisions are influenced?

What data is processed?

Does the system process sensitive information?

What happens if the model produces an incorrect result?

Is human oversight available?

Can the system impact safety?

Can the model be misused?

Is the system subject to regulatory requirements?
```

### Assessment Lifecycle

```text
Draft
  ↓
In Progress
  ↓
Submitted
  ↓
Under Review
  ↓
Approved / Rejected
```

### Important Design Decision

Do not hard-code assessment questions into the backend.

Use configurable:

```text
Assessment Template
     ↓
Assessment Questions
     ↓
Assessment Response
     ↓
Assessment Result
```

This allows different risk assessment templates for different organizations.

---

# Subfeature 3 — Risk Scoring & Prioritization

### Purpose

Convert assessment results into a consistent risk score.

### Initial Model

Use:

```text
Risk Score = Likelihood × Impact
```

For example:

```text
Likelihood: 1–5
Impact:     1–5

Score:
1–4      Low
5–9      Medium
10–16    High
17–25    Critical
```

This is an initial implementation only.

The scoring engine must eventually support configurable matrices.

### Risk Factors

Potential factors:

```text
Likelihood
Impact
Exposure
Affected Users
Regulatory Exposure
Financial Impact
Safety Impact
Privacy Impact
Reputational Impact
Detectability
Control Effectiveness
```

### Risk Views

Provide:

- risk register
- risk heat map
- highest-risk assets
- highest-risk categories
- risk trend
- unresolved critical risks

### Important Concept

Track both:

```text
Inherent Risk
```

and

```text
Residual Risk
```

Example:

```text
Inherent Risk
     ↓
Controls / Mitigation
     ↓
Residual Risk
```

---

# Subfeature 4 — Risk Treatment & Mitigation

### Purpose

Allow organizations to decide what to do with identified risks.

Risk treatment should support the standard response strategies:

```text
Mitigate
Accept
Transfer
Avoid
```

NIST's MANAGE function similarly emphasizes prioritizing treatment and documenting responses to high-priority AI risks.

### Mitigation Plan

Each risk can have:

```text
Risk
 ↓
Treatment Plan
 ↓
Mitigation Actions
 ↓
Assigned Owner
 ↓
Due Date
 ↓
Verification
```

### Example

```text
Risk:
Model produces biased candidate rankings

Treatment:
Mitigate

Actions:
1. Add fairness evaluation
2. Review training dataset
3. Introduce human approval
4. Run periodic bias monitoring

Owner:
AI Governance Team

Due Date:
...

Status:
In Progress
```

### Statuses

```text
Open
In Progress
Blocked
Pending Verification
Resolved
Accepted
Closed
```

---

# Subfeature 5 — Compliance Framework & Requirement Management

### Purpose

Create the compliance knowledge layer of Auditra.

Instead of building separate code paths for every regulation, introduce a generic framework model.

### Structure

```text
Framework
   ↓
Domain / Category
   ↓
Requirement
   ↓
Control
```

Example:

```text
NIST AI RMF
   ↓
Manage
   ↓
Risk Treatment
   ↓
Auditra Control
```

Another:

```text
ISO/IEC 42001
   ↓
AI Risk Management
   ↓
Risk Assessment Requirement
   ↓
Auditra Control
```

### Framework Entity

```text
Framework
- id
- name
- version
- jurisdiction
- description
- authority
- status
```

### Requirement Entity

```text
Requirement
- id
- framework_id
- identifier
- title
- description
- category
- applicability
```

### Important Architecture Decision

Frameworks should be **versioned**.

For example:

```text
Framework
  ├── Version 1
  ├── Version 2
  └── Version 3
```

This prevents regulatory updates from destroying historical compliance records.

---

# Subfeature 6 — Control Management & Requirement Mapping

### Purpose

Connect compliance requirements to the actual controls implemented by an organization.

### Control Model

```text
Requirement
       ↓
Control
       ↓
Implementation
       ↓
Evidence
```

Example:

```text
Requirement:
Perform AI risk assessments

Control:
AI systems undergo documented risk assessment before deployment.

Implementation:
Risk Assessment Workflow

Evidence:
Assessment Report
```

### Control Types

```text
Preventive
Detective
Corrective
Technical
Administrative
Organizational
Human
Automated
```

### Control Lifecycle

```text
Draft
Implemented
Partially Implemented
Not Implemented
Not Applicable
Retired
```

### Mapping

One control may satisfy many requirements.

```text
Requirement A ─┐
Requirement B ─┼──> Control X
Requirement C ─┘
```

This is important for avoiding duplicate compliance work.

---

# Subfeature 7 — Evidence & Compliance Assessment

### Purpose

Provide proof that controls and requirements are actually being satisfied.

Evidence can include:

```text
Documents
Policies
Risk Assessments
Model Cards
Evaluation Reports
Audit Logs
System Logs
Screenshots
Test Results
Approvals
Certificates
Tickets
Training Records
Vendor Documents
```

### Evidence Model

```text
Evidence
- id
- type
- title
- description
- source
- file/location reference
- control_id
- requirement_id
- asset_id
- owner
- collected_at
- valid_from
- valid_until
- status
```

### Evidence Lifecycle

```text
Requested
 ↓
Collected
 ↓
Under Review
 ↓
Approved / Rejected
 ↓
Expired
```

### Evidence Expiration

Evidence should support expiration.

Example:

```text
Evidence valid until:
2027-06-30
```

Auditra can then identify upcoming evidence renewals.

---

# Subfeature 8 — Findings, Compliance Gaps & Remediation

### Purpose

Turn assessments and evidence reviews into actionable findings.

A finding may represent:

```text
Requirement not satisfied
Control failure
Missing evidence
Expired evidence
Risk above threshold
Policy violation
Assessment failure
```

### Finding Lifecycle

```text
Open
 ↓
Assigned
 ↓
In Progress
 ↓
Pending Verification
 ↓
Resolved
 ↓
Closed
```

### Finding Structure

```text
Finding
- id
- title
- description
- severity
- source
- asset_id
- requirement_id
- control_id
- risk_id
- owner
- due_date
- status
- remediation_plan
```

### Example

```text
Finding:
No documented human oversight process

Requirement:
Human Oversight

Control:
Human approval required for high-impact decisions

Evidence:
Not found

Severity:
High

Remediation:
Implement approval workflow
```

### Critical Relationship

A finding should be able to automatically generate:

```text
Finding
   ↓
Risk
   ↓
Remediation Action
```

---

# Subfeature 9 — Compliance Monitoring, Dashboards & Reporting

### Purpose

Provide executives, governance teams, auditors, and technical teams with a unified view of organizational AI risk and compliance.

### Dashboard Areas

#### Risk Overview

```text
Total Risks
Critical Risks
High Risks
Open Risks
Accepted Risks
Overdue Risks
```

#### Compliance Overview

```text
Frameworks
Applicable Requirements
Compliant Requirements
Partially Compliant
Non-Compliant
Not Assessed
```

#### Control Overview

```text
Implemented
Partially Implemented
Not Implemented
Failed
```

#### Evidence Overview

```text
Valid
Expiring Soon
Expired
Missing
Pending Review
```

#### Remediation Overview

```text
Open
Overdue
In Progress
Resolved
```

### Compliance Score

Auditra can initially calculate:

```text
Compliance %
=
Satisfied Requirements
/
Applicable Requirements
× 100
```

But do not represent this as an absolute "legal compliance" guarantee.

Instead expose:

```text
Auditra Compliance Status
```

or

```text
Control Coverage
```

This avoids suggesting that a percentage automatically means an organization is legally compliant.

### Reporting

Generate:

```text
Risk Register Report
Risk Assessment Report
Compliance Status Report
Control Effectiveness Report
Evidence Report
Open Findings Report
Remediation Report
Framework Compliance Report
Audit Readiness Report
```

---

# 5. Core Data Model

The first implementation should establish the following entities.

```text
AIAsset
   │
   ├── Risk
   │     └── RiskAssessment
   │            └── RiskTreatment
   │
   ├── Requirement
   │     └── Control
   │            └── Evidence
   │
   ├── Finding
   │     └── RemediationAction
   │
   └── ComplianceAssessment
```

Supporting entities:

```text
Framework
FrameworkVersion
Requirement
Control
Risk
RiskAssessment
RiskTreatment
Evidence
Finding
RemediationAction
AssessmentTemplate
AssessmentQuestion
AssessmentResponse
```

---

# 6. Important Relationships

The backend should support relationships such as:

```text
AIAsset 1 ─── N Risk

Risk 1 ─── N RiskAssessment

Risk 1 ─── N RiskTreatment

Framework 1 ─── N Requirement

Requirement N ─── N Control

Control 1 ─── N Evidence

Requirement 1 ─── N Evidence

AIAsset 1 ─── N Evidence

Risk 1 ─── N Finding

Control 1 ─── N Finding

Requirement 1 ─── N Finding

Finding 1 ─── N RemediationAction
```

The many-to-many relationship between **Requirements and Controls** is particularly important.

---

# 7. API Structure

The API should follow the modular-monolith architecture already planned for Auditra.

Suggested REST structure:

```text
/api/v1/risks
/api/v1/risk-assessments
/api/v1/risk-treatments

/api/v1/frameworks
/api/v1/requirements
/api/v1/controls

/api/v1/evidence

/api/v1/findings
/api/v1/remediation-actions

/api/v1/compliance-assessments
/api/v1/compliance/reports
```

Example:

```http
GET    /api/v1/risks
POST   /api/v1/risks
GET    /api/v1/risks/{risk_id}
PATCH  /api/v1/risks/{risk_id}
DELETE /api/v1/risks/{risk_id}
```

And:

```http
GET  /api/v1/frameworks
POST /api/v1/frameworks/{framework_id}/requirements
GET  /api/v1/requirements/{requirement_id}/controls
```

---

# 8. Permission Model

Risk and compliance information can be sensitive.

Initial roles should include:

```text
Admin
Governance Manager
Risk Manager
Compliance Manager
Asset Owner
Control Owner
Auditor
Viewer
```

Example:

```text
Risk Manager
→ create/update risks

Compliance Manager
→ manage frameworks and controls

Control Owner
→ provide evidence

Auditor
→ review evidence/findings

Viewer
→ read-only access
```

---

# 9. Audit Trail

Every important governance operation should generate an audit event.

Example:

```text
User changed Risk severity
User approved Assessment
User rejected Evidence
User changed Control status
User closed Finding
User accepted Residual Risk
```

Audit event:

```text
AuditEvent
- id
- actor_id
- action
- entity_type
- entity_id
- before_value
- after_value
- timestamp
- metadata
```

This is important because the Risk & Compliance module itself must be auditable.

NIST's measurement guidance explicitly discusses maintaining histories, audit logs, and information needed to evaluate AI risks and organizational accountability.

---

# 10. Recommended Implementation Sequence

Do NOT implement all nine subfeatures simultaneously.

Implement them in this order:

```text
1. Risk Register & Risk Taxonomy
          ↓
2. AI Risk Assessment
          ↓
3. Risk Scoring & Prioritization
          ↓
4. Risk Treatment & Mitigation
          ↓
5. Compliance Framework & Requirement Management
          ↓
6. Control Management & Requirement Mapping
          ↓
7. Evidence & Compliance Assessment
          ↓
8. Findings & Remediation
          ↓
9. Monitoring, Dashboards & Reporting
```

This creates a natural dependency chain.

---

# 11. MVP Boundary

For the first working Auditra version, the minimum complete vertical slice should be:

```text
AI Asset
   ↓
Risk
   ↓
Assessment
   ↓
Risk Score
   ↓
Control
   ↓
Evidence
   ↓
Finding
   ↓
Remediation
```

Once this works end-to-end, compliance frameworks and advanced reporting can be expanded.

---

# 12. What Should NOT Be Built in This Feature

To prevent scope explosion, do not initially build:

```text
Automated legal advice
Automatic legal interpretation
Full GRC replacement
Enterprise SIEM
Full vulnerability management
Full privacy management platform
External certification
Automated regulatory filing
Complex AI red-teaming engine
Full ML monitoring platform
```

Auditra should remain an **AI Governance platform**, not attempt to become every enterprise security/compliance product.

---

# 13. Future Automation Layer

Once the foundational nine subfeatures work, automation can be added.

Examples:

```text
New AI asset discovered
        ↓
Automatically create risk assessment

New regulation requirement
        ↓
Identify affected AI assets

Control changed
        ↓
Re-evaluate affected requirements

Evidence expires
        ↓
Create compliance task

Critical risk detected
        ↓
Create finding

Risk score increases
        ↓
Notify risk owner

New model version deployed
        ↓
Trigger reassessment
```

This is where Auditra can eventually become more than a static governance database.

---

# 14. Future AI-Powered Capabilities

A later AI layer could provide:

### Risk Copilot

```text
Analyze asset
→ identify likely risks
→ explain reasoning
→ propose severity
→ suggest controls
```

### Compliance Copilot

```text
Analyze requirement
→ identify relevant controls
→ determine evidence needed
→ detect gaps
```

### Evidence Intelligence

```text
Upload document
→ extract compliance evidence
→ identify applicable controls
→ detect missing information
```

### Continuous Risk Intelligence

```text
Asset changes
      ↓
Risk changes
      ↓
Compliance impact
      ↓
Automatic reassessment
```

These should remain future layers rather than being prerequisites for the initial implementation.

---

# 15. Recommended Database Modules

Keep the database organized by domain.

```text
risk/
    risks
    risk_assessments
    risk_treatments

compliance/
    frameworks
    framework_versions
    requirements
    controls
    requirement_controls
    compliance_assessments

evidence/
    evidence
    evidence_reviews

findings/
    findings
    remediation_actions

governance/
    audit_events
    assignments
    approvals
```

This fits Auditra's modular-monolith approach and leaves room for later extraction into services without forcing microservices prematurely.

---

# 16. Definition of Done for Feature 3

Feature 3 should be considered complete when an organization can perform this entire workflow:

```text
1. Discover an AI asset
          ↓
2. Create/identify risks
          ↓
3. Perform risk assessment
          ↓
4. Calculate inherent risk
          ↓
5. Define controls
          ↓
6. Map controls to compliance requirements
          ↓
7. Collect evidence
          ↓
8. Determine compliance status
          ↓
9. Create findings for gaps
          ↓
10. Assign remediation
          ↓
11. Verify remediation
          ↓
12. Recalculate residual risk
          ↓
13. View organizational risk/compliance dashboard
          ↓
14. Export audit-ready reports
```

The important end state is therefore:

**Asset → Risk → Control → Requirement → Evidence → Finding → Remediation → Residual Risk → Compliance Status**

That chain should become one of the defining architectural flows of Auditra.

---

# 17. Recommended Subfeature Names for Implementation

Use these exact names in the project so the work stays organized:

```text
RF-01 — Risk Register & Taxonomy
RF-02 — AI Risk Assessment
RF-03 — Risk Scoring & Prioritization
RF-04 — Risk Treatment & Mitigation
RF-05 — Compliance Framework & Requirements
RF-06 — Control Management & Requirement Mapping
RF-07 — Evidence & Compliance Assessment
RF-08 — Findings & Remediation
RF-09 — Compliance Monitoring & Reporting
```

Each `RF-*` subfeature should be implemented as a separate development milestone, with its own specification, API work, database changes, tests, and UI changes.

For the actual implementation, **RF-01 should be developed first**, because every subsequent risk-management workflow depends on the Risk entity and taxonomy.