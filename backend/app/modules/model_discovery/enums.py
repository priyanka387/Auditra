from enum import Enum


class DiscoverySourceType(str, Enum):
    MANUAL = "manual"
    LANGCHAIN = "langchain"
    LANGGRAPH = "langgraph"


class DiscoveryStatus(str, Enum):
    DISCOVERED = "DISCOVERED"
    MATCHED = "MATCHED"
    UNRESOLVED = "UNRESOLVED"
    REGISTERED = "REGISTERED"
    IGNORED = "IGNORED"
    FAILED = "FAILED"
