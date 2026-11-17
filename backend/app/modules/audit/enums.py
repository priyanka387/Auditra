import re
from enum import StrEnum


class AuditEventType(StrEnum):
    MODEL_CREATED = "model.created"
    MODEL_REGISTERED = "model.registered"
    MODEL_UPDATED = "model.updated"
    DEPLOYMENT_CREATED = "deployment.created"
    DEPLOYMENT_UPDATED = "deployment.updated"
    DEPLOYMENT_STATUS_CHANGED = "deployment.status_changed"
    DEPLOYMENT_ARCHIVED = "deployment.archived"
    DEPLOYMENT_ENDPOINT_CREATED = "deployment_endpoint.created"
    DEPLOYMENT_ENDPOINT_UPDATED = "deployment_endpoint.updated"
    DEPLOYMENT_ENDPOINT_ARCHIVED = "deployment_endpoint.archived"
    DISCOVERY_SOURCE_CREATED = "discovery_source.created"
    DISCOVERY_SOURCE_UPDATED = "discovery_source.updated"
    DISCOVERY_SOURCE_ARCHIVED = "discovery_source.archived"
    RISK_CREATED = "risk.created"
    RISK_UPDATED = "risk.updated"
    RISK_ARCHIVED = "risk.archived"
    RISK_CATEGORY_CREATED = "risk_category.created"
    RISK_CATEGORY_UPDATED = "risk_category.updated"
    ASSESSMENT_CREATED = "assessment.created"
    ASSESSMENT_STARTED = "assessment.started"
    ASSESSMENT_RESPONSE_UPDATED = "assessment.response_updated"
    ASSESSMENT_SUBMITTED = "assessment.submitted"
    ASSESSMENT_REVIEW_STARTED = "assessment.review_started"
    ASSESSMENT_APPROVED = "assessment.approved"
    ASSESSMENT_REJECTED = "assessment.rejected"
    ASSESSMENT_REVISED = "assessment.revised"
    ASSESSMENT_TEMPLATE_CREATED = "assessment_template.created"
    ASSESSMENT_TEMPLATE_UPDATED = "assessment_template.updated"


EVENT_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
