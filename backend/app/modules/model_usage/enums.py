from enum import Enum


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
