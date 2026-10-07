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


EVENT_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
