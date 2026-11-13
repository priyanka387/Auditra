"""Deterministic static detection rules for repository discovery.

Rules are regular expressions evaluated over text that has already been
read from disk. Nothing here imports, resolves or executes anything from
the scanned repository, and no LLM is involved: the same content always
produces the same observations.

Each observation carries the id of the rule that produced it so results
stay explainable. Rules require meaningful contextual signals (imports,
usage, provider configuration keys, environment-variable references,
dependency-manifest entries) rather than bare word occurrences, so prose
mentions of e.g. "openai" in a README do not become observations.
"""

import re
from dataclasses import dataclass, field
from uuid import UUID

from app.modules.asset_discovery.connectors.base import (
    AGENT_REFERENCE,
    FRAMEWORK_REFERENCE,
    MODEL_REFERENCE,
    PROVIDER_REFERENCE,
    WORKFLOW_REFERENCE,
    DiscoveryObservation,
)

MAX_MODEL_ID_LENGTH = 128
MAX_LINE_LENGTH = 10_000

# provider id -> detection context. Values are regex fragments.
_PROVIDERS: dict[str, dict[str, tuple[str, ...]]] = {
    "openai": {
        "py_modules": ("openai",),
        "py_extra": (),
        "packages": ("openai",),
        "slugs": ("openai",),
        "env_vars": ("OPENAI_API_KEY",),
    },
    "anthropic": {
        "py_modules": ("anthropic",),
        "py_extra": (),
        "packages": ("anthropic",),
        "slugs": ("anthropic",),
        "env_vars": ("ANTHROPIC_API_KEY",),
    },
    "google": {
        # explicit modules only: bare `google.*` imports are far too common
        # outside AI code to be a meaningful signal
        "py_modules": (r"google\.(?:generativeai|genai)",),
        "py_extra": (r"from\s+google\s+import\s+(?P<name>genai|generativeai)",),
        "packages": (
            r"@google/generative-ai",
            r"@google/genai",
            "google-generativeai",
            "google-genai",
        ),
        "slugs": ("gemini", "google"),
        "env_vars": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    },
    "huggingface": {
        "py_modules": ("huggingface_hub",),
        "py_extra": (),
        "packages": ("huggingface_hub", "huggingface-hub", "huggingface"),
        "slugs": ("huggingface", "huggingface_hub"),
        "env_vars": ("HF_TOKEN", "HUGGINGFACE_API_KEY"),
    },
    "ollama": {
        "py_modules": ("ollama",),
        "py_extra": (),
        "packages": ("ollama",),
        "slugs": ("ollama",),
        "env_vars": ("OLLAMA_HOST",),
    },
}

# framework id -> detection context. Values are regex fragments.
_FRAMEWORKS: dict[str, dict[str, tuple[str, ...]]] = {
    "langchain": {
        "py_modules": (r"langchain(?:[_.][a-z0-9]+)*",),
        # @langchain/langgraph belongs to langgraph, not langchain
        "packages": (
            r"@langchain/(?!(?:langgraph)(?:[-_][a-z0-9]+)?\b)[a-z0-9-]+",
            r"langchain(?:[-_][a-z0-9]+)*",
        ),
        "env_vars": (),
    },
    "langgraph": {
        "py_modules": (r"langgraph(?:[_.][a-z0-9]+)*",),
        "packages": (r"@langchain/langgraph(?:[-_][a-z0-9]+)*", r"langgraph(?:[-_][a-z0-9]+)*"),
        "env_vars": (),
    },
    "llama_index": {
        "py_modules": (r"llama_index(?:[_.][a-z0-9]+)*",),
        "packages": (r"llamaindex", r"llama[-_]index(?:[-_][a-z0-9]+)*"),
        "env_vars": (),
    },
    "vllm": {
        "py_modules": ("vllm",),
        "packages": ("vllm",),
        "env_vars": (),
    },
}

_MANIFEST_FILENAMES = frozenset({"pyproject.toml", "package.json", "Pipfile", "setup.cfg"})


def is_manifest_file(file_path: str) -> bool:
    """True for dependency manifests, where any package token is a declaration."""
    name = file_path.rsplit("/", 1)[-1]
    return name in _MANIFEST_FILENAMES or (
        name.startswith("requirements") and name.endswith(".txt")
    )


@dataclass(frozen=True)
class _Rule:
    """One deterministic detection rule."""

    rule_id: str
    observation_type: str
    pattern: re.Pattern[str]
    provider: str | None = None
    name: str | None = None  # static identifier; otherwise the "name" group
    metadata: dict[str, str] = field(default_factory=dict)
    meta_group: str | None = None  # capture group recorded under this metadata key
    manifest_only: bool = False


def _provider_rules() -> list[_Rule]:
    rules: list[_Rule] = []
    for provider, spec in _PROVIDERS.items():
        py_modules = "|".join(spec["py_modules"])
        packages = "|".join(spec["packages"])
        slugs = "|".join(spec["slugs"])
        env_vars = "|".join(spec["env_vars"])
        rules.extend(
            [
                _Rule(
                    f"provider.{provider}.import",
                    PROVIDER_REFERENCE,
                    re.compile(rf"\b(?:import|from)\s+(?P<name>{py_modules})\b"),
                    provider=provider,
                ),
                _Rule(
                    f"provider.{provider}.usage",
                    PROVIDER_REFERENCE,
                    re.compile(rf"\b(?P<name>{py_modules})\.[A-Za-z_]"),
                    provider=provider,
                ),
                _Rule(
                    f"provider.{provider}.from_string",
                    PROVIDER_REFERENCE,
                    re.compile(rf"\bfrom\s+[\"'](?P<name>{packages})[\"']"),
                    provider=provider,
                ),
                _Rule(
                    f"provider.{provider}.require",
                    PROVIDER_REFERENCE,
                    re.compile(rf"\brequire\s*\(\s*[\"'](?P<name>{packages})[\"']"),
                    provider=provider,
                ),
                _Rule(
                    f"provider.{provider}.config",
                    PROVIDER_REFERENCE,
                    re.compile(
                        r"\b(?:provider_name|provider_id|provider|api_provider|llm_provider"
                        rf"|model_provider)\s*[:=]\s*[\"']?(?P<name>{slugs})[\"']?"
                        r"(?![A-Za-z0-9_.-])"
                    ),
                    provider=provider,
                ),
                _Rule(
                    f"provider.{provider}.env",
                    PROVIDER_REFERENCE,
                    re.compile(rf"\b(?P<env_var>{env_vars})\b"),
                    provider=provider,
                    name=provider,
                    meta_group="env_var",
                ),
                _Rule(
                    f"provider.{provider}.manifest",
                    PROVIDER_REFERENCE,
                    re.compile(rf"(?<![A-Za-z0-9_.-])(?P<name>{packages})(?![A-Za-z0-9_.-])"),
                    provider=provider,
                    manifest_only=True,
                ),
            ]
        )
        for extra in spec["py_extra"]:
            rules.append(
                _Rule(
                    f"provider.{provider}.import_from",
                    PROVIDER_REFERENCE,
                    re.compile(rf"\b(?:{extra})\b"),
                    provider=provider,
                )
            )
    return rules


def _framework_rules() -> list[_Rule]:
    rules: list[_Rule] = []
    for framework, spec in _FRAMEWORKS.items():
        metadata = {"framework": framework}
        py_modules = "|".join(spec["py_modules"])
        packages = "|".join(spec["packages"])
        rules.extend(
            [
                _Rule(
                    f"framework.{framework}.import",
                    FRAMEWORK_REFERENCE,
                    re.compile(rf"\b(?:import|from)\s+(?P<name>{py_modules})\b"),
                    metadata=metadata,
                ),
                _Rule(
                    f"framework.{framework}.usage",
                    FRAMEWORK_REFERENCE,
                    re.compile(rf"\b(?P<name>{py_modules})\.[A-Za-z_]"),
                    metadata=metadata,
                ),
                _Rule(
                    f"framework.{framework}.from_string",
                    FRAMEWORK_REFERENCE,
                    re.compile(rf"\bfrom\s+[\"'](?P<name>{packages})[\"']"),
                    metadata=metadata,
                ),
                _Rule(
                    f"framework.{framework}.require",
                    FRAMEWORK_REFERENCE,
                    re.compile(rf"\brequire\s*\(\s*[\"'](?P<name>{packages})[\"']"),
                    metadata=metadata,
                ),
                _Rule(
                    f"framework.{framework}.manifest",
                    FRAMEWORK_REFERENCE,
                    re.compile(rf"(?<![A-Za-z0-9_.-])(?P<name>{packages})(?![A-Za-z0-9_.-])"),
                    metadata=metadata,
                    manifest_only=True,
                ),
            ]
        )
        env_vars = spec["env_vars"]
        if env_vars:
            rules.append(
                _Rule(
                    f"framework.{framework}.env",
                    FRAMEWORK_REFERENCE,
                    re.compile(rf"\b(?P<env_var>{'|'.join(env_vars)})\b"),
                    metadata=metadata,
                    name=framework,
                    meta_group="env_var",
                )
            )
    return rules


_RULES: tuple[_Rule, ...] = (
    *_provider_rules(),
    *_framework_rules(),
    # conservative agent/workflow signals tied to recognizable framework APIs
    _Rule(
        "agent.langchain.create_react_agent",
        AGENT_REFERENCE,
        re.compile(r"\b(?P<name>create_react_agent)\s*\("),
        metadata={"framework": "langchain"},
    ),
    _Rule(
        "workflow.langgraph.state_graph",
        WORKFLOW_REFERENCE,
        re.compile(r"\b(?P<name>StateGraph)\s*\("),
        metadata={"framework": "langgraph"},
    ),
)

_MODEL_KEY = r"(?P<key>model_name|model_id|modelname|modelid|default_model|defaultmodel|model)"
_MODEL_ASSIGNMENT_RE = re.compile(
    rf"\b{_MODEL_KEY}[\"']?\s*(?:={{1,2}}|:)\s*"
    rf"(?:"
    rf"\"(?P<dq>[^\"\s]{{1,{MAX_MODEL_ID_LENGTH}}})\""
    rf"|'(?P<sq>[^'\s]{{1,{MAX_MODEL_ID_LENGTH}}})'"
    rf"|(?P<uq>[A-Za-z0-9][A-Za-z0-9._:/-]{{0,{MAX_MODEL_ID_LENGTH - 1}}})"
    rf")",
    re.IGNORECASE,
)


def _model_value(line: str, match: re.Match[str]) -> str | None:
    """Validate a model-assignment value; None when it is not a model id.

    A meaningful model reference must look like an identifier (letters and
    digits, no whitespace) and must not be a bare call such as
    ``model = get_model()`` — this keeps arbitrary ``model`` mentions from
    becoming observations.
    """
    value = match.group("dq") or match.group("sq") or match.group("uq") or ""
    if not any(char.isalpha() for char in value):
        return None
    if not any(char.isdigit() for char in value):
        return None
    if line[match.end() :].lstrip().startswith("("):
        return None
    return value


def detect(
    source_id: UUID,
    run_id: UUID,
    file_path: str,
    text: str,
    *,
    limit: int | None = None,
) -> list[DiscoveryObservation]:
    """Run every applicable rule over ``text`` and return observations.

    ``file_path`` is the source-relative posix path used for location
    evidence. ``limit`` caps the number of observations returned.
    """
    if limit is not None and limit <= 0:
        return []
    manifest = is_manifest_file(file_path)
    rules = [rule for rule in _RULES if not rule.manifest_only or manifest]
    observations: list[DiscoveryObservation] = []
    seen: set[tuple[str, str, int]] = set()

    def _append(observation: DiscoveryObservation) -> bool:
        key = (observation.detection_rule, observation.name, observation.line_number or 0)
        if key in seen:
            return limit is None or len(observations) < limit
        seen.add(key)
        observations.append(observation)
        return limit is None or len(observations) < limit

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line[:MAX_LINE_LENGTH]
        for rule in rules:
            for match in rule.pattern.finditer(line):
                name = rule.name or match.groupdict().get("name")
                if not name:
                    continue
                metadata = dict(rule.metadata)
                if rule.meta_group is not None:
                    metadata[rule.meta_group] = match.group(rule.meta_group)
                if not _append(
                    DiscoveryObservation(
                        source_id=source_id,
                        run_id=run_id,
                        observation_type=rule.observation_type,
                        name=name,
                        provider=rule.provider,
                        file_path=file_path,
                        line_number=line_number,
                        detection_rule=rule.rule_id,
                        metadata=metadata,
                    )
                ):
                    return observations
        for match in _MODEL_ASSIGNMENT_RE.finditer(line):
            value = _model_value(line, match)
            if value is None:
                continue
            if not _append(
                DiscoveryObservation(
                    source_id=source_id,
                    run_id=run_id,
                    observation_type=MODEL_REFERENCE,
                    name=value,
                    provider=None,
                    file_path=file_path,
                    line_number=line_number,
                    detection_rule="model.assignment",
                    metadata={"key": match.group("key")},
                )
            ):
                return observations
    return observations
