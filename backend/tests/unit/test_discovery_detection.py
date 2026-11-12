"""Unit tests for deterministic static detection rules."""

from uuid import uuid4

from app.modules.asset_discovery.connectors.detection import detect, is_manifest_file

SOURCE_ID = uuid4()
RUN_ID = uuid4()


def _detect(text: str, file_path: str = "app/main.py", limit: int | None = None):
    return detect(SOURCE_ID, RUN_ID, file_path, text, limit=limit)


def _rules(observations):
    return {observation.detection_rule for observation in observations}


def _one(observations, rule_id):
    matched = [obs for obs in observations if obs.detection_rule == rule_id]
    assert len(matched) == 1, f"expected exactly one {rule_id}: {matched}"
    return matched[0]


# --- provider detection ----------------------------------------------------


def test_python_provider_import_detected():
    observation = _one(_detect("import openai\n"), "provider.openai.import")
    assert observation.observation_type == "provider_reference"
    assert observation.provider == "openai"
    assert observation.name == "openai"
    assert observation.file_path == "app/main.py"
    assert observation.line_number == 1
    assert observation.source_id == SOURCE_ID
    assert observation.run_id == RUN_ID


def test_python_from_import_detected():
    observation = _one(_detect("from anthropic import Anthropic\n"), "provider.anthropic.import")
    assert observation.provider == "anthropic"


def test_provider_module_usage_detected():
    observation = _one(_detect("client = openai.OpenAI()\n"), "provider.openai.usage")
    assert observation.provider == "openai"


def test_provider_config_key_detected():
    observation = _one(_detect('provider: "anthropic"\n'), "provider.anthropic.config")
    assert observation.provider == "anthropic"
    assert observation.name == "anthropic"


def test_provider_config_key_unquoted_yaml_detected():
    observation = _detect("provider: gemini\nmodel_provider: google\n", "config.yaml")
    config_hits = [obs for obs in observation if obs.detection_rule == "provider.google.config"]
    assert [obs.name for obs in config_hits] == ["gemini", "google"]


def test_javascript_provider_import_string_detected():
    observation = _one(_detect('import OpenAI from "openai";\n'), "provider.openai.from_string")
    assert observation.provider == "openai"


def test_provider_require_detected():
    observation = _one(
        _detect('const { Anthropic } = require("anthropic");\n'), "provider.anthropic.require"
    )
    assert observation.provider == "anthropic"


def test_provider_env_var_detected_without_value():
    line = "OPENAI_API_KEY=sk-super-secret-value\n"
    observation = _one(_detect(line), "provider.openai.env")
    assert observation.name == "openai"
    assert observation.metadata == {"env_var": "OPENAI_API_KEY"}
    # the secret value must never be captured
    assert "sk-super-secret-value" not in str(observation.metadata)


def test_google_generativeai_import_detected():
    observation = _one(_detect("import google.generativeai as genai\n"), "provider.google.import")
    assert observation.provider == "google"
    assert observation.name == "google.generativeai"


def test_google_new_sdk_from_import_detected():
    observation = _one(_detect("from google import genai\n"), "provider.google.import_from")
    assert observation.provider == "google"
    assert observation.name == "genai"


def test_generic_google_import_is_not_detected():
    # `from google import protobuf` is not an AI signal
    assert _detect("from google import protobuf\n") == []


def test_huggingface_hub_import_detected():
    observation = _one(
        _detect("from huggingface_hub import hf_hub_download\n"), "provider.huggingface.import"
    )
    assert observation.provider == "huggingface"


def test_huggingface_env_detected():
    observation = _one(_detect("export HF_TOKEN=hf_xxx\n"), "provider.huggingface.env")
    assert observation.metadata == {"env_var": "HF_TOKEN"}
    assert "hf_xxx" not in str(observation.metadata)


def test_ollama_import_and_usage_detected():
    observations = _detect("import ollama\nreply = ollama.chat(model='llama3')\n")
    assert _rules(observations) == {
        "provider.ollama.import",
        "provider.ollama.usage",
        "model.assignment",
    }


# --- framework detection ---------------------------------------------------


def test_langchain_family_import_detected():
    observation = _one(
        _detect("from langchain_openai import ChatOpenAI\n"), "framework.langchain.import"
    )
    assert observation.observation_type == "framework_reference"
    assert observation.provider is None
    assert observation.name == "langchain_openai"
    assert observation.metadata == {"framework": "langchain"}


def test_langgraph_import_detected():
    observation = _one(
        _detect("from langgraph.graph import StateGraph\n"), "framework.langgraph.import"
    )
    assert observation.metadata == {"framework": "langgraph"}


def test_llamaindex_import_detected():
    observation = _one(
        _detect("from llama_index.core import VectorStoreIndex\n"),
        "framework.llama_index.import",
    )
    assert observation.metadata == {"framework": "llama_index"}


def test_vllm_import_detected():
    observation = _one(_detect("from vllm import LLM\n"), "framework.vllm.import")
    assert observation.metadata == {"framework": "vllm"}


def test_langchain_js_package_detected():
    observation = _one(
        _detect('import { ChatOpenAI } from "@langchain/openai";\n'),
        "framework.langchain.from_string",
    )
    assert observation.name == "@langchain/openai"


def test_langgraph_js_package_belongsto_langgraph():
    # @langchain/langgraph must not be reported as a langchain signal
    observations = _detect('import { StateGraph } from "@langchain/langgraph";\n')
    assert _rules(observations) == {"framework.langgraph.from_string"}


# --- agent / workflow signals ----------------------------------------------


def test_langgraph_state_graph_call_detected_as_workflow():
    observation = _one(
        _detect("graph = StateGraph(StateAnnotation)\n"), "workflow.langgraph.state_graph"
    )
    assert observation.observation_type == "workflow_reference"
    assert observation.name == "StateGraph"
    assert observation.metadata == {"framework": "langgraph"}


def test_create_react_agent_call_detected_as_agent():
    observation = _one(
        _detect("agent = create_react_agent(model, tools)\n"),
        "agent.langchain.create_react_agent",
    )
    assert observation.observation_type == "agent_reference"
    assert observation.metadata == {"framework": "langchain"}


def test_import_without_call_is_not_a_workflow_signal():
    rules = _rules(_detect("from langgraph.graph import StateGraph\n"))
    assert "workflow.langgraph.state_graph" not in rules
    assert "framework.langgraph.import" in rules


# --- model reference detection ---------------------------------------------


def test_model_assignment_double_quoted_detected():
    observation = _one(_detect('model = "gpt-4o"\n'), "model.assignment")
    assert observation.observation_type == "model_reference"
    assert observation.name == "gpt-4o"
    assert observation.provider is None
    assert observation.metadata == {"key": "model"}


def test_model_name_assignment_detected():
    observation = _one(_detect("model_name = 'claude-3-5-sonnet'\n"), "model.assignment")
    assert observation.name == "claude-3-5-sonnet"
    assert observation.metadata == {"key": "model_name"}


def test_model_id_env_assignment_detected():
    observation = _one(_detect("MODEL_NAME=gpt-4o-mini\n"), "model.assignment")
    assert observation.name == "gpt-4o-mini"


def test_yaml_unquoted_model_detected():
    observation = _one(_detect("model: gemini-1.5-pro\n", "config.yaml"), "model.assignment")
    assert observation.name == "gemini-1.5-pro"


def test_dict_model_entry_detected():
    observation = _one(_detect('{"model": "llama-3.1-70b"}\n', "agent.json"), "model.assignment")
    assert observation.name == "llama-3.1-70b"


def test_model_comparison_detected():
    observation = _one(_detect('if model == "gpt-4o":\n'), "model.assignment")
    assert observation.name == "gpt-4o"


def test_model_default_assignment_detected():
    observation = _one(_detect("default_model: qwen2.5-72b\n", "cfg.toml"), "model.assignment")
    assert observation.metadata == {"key": "default_model"}


def test_bare_variable_model_assignment_is_ignored():
    assert _detect("model = get_model()\n") == []


def test_model_with_non_identifier_value_is_ignored():
    assert _detect('model = "the quick brown fox"\n') == []


def test_model_with_letter_only_value_is_ignored():
    assert _detect('model = "string"\n') == []


def test_data_model_key_is_not_a_model_signal():
    assert _detect('data_model = "gpt-4o"\n') == []


def test_models_plural_key_is_not_a_model_signal():
    assert _detect('models = ["gpt-4o"]\n') == []


def test_bare_word_model_in_prose_is_ignored():
    text = "We use openai for chat. Langchain is our framework of choice.\n"
    assert _detect(text) == []


# --- dependency manifest detection ------------------------------------------


def test_requirements_manifest_entries_detected():
    text = "langchain>=0.1.0\nopenai==1.40.0\n"
    observations = _detect(text, "requirements.txt")
    assert _rules(observations) == {
        "framework.langchain.manifest",
        "provider.openai.manifest",
    }


def test_pyproject_manifest_entry_detected():
    observations = _detect('dependencies = ["anthropic>=0.30"]\n', "pyproject.toml")
    assert "provider.anthropic.manifest" in _rules(observations)


def test_package_json_manifest_entry_detected():
    observations = _detect('  "langgraph": "^0.2.0"\n', "package.json")
    assert "framework.langgraph.manifest" in _rules(observations)


def test_version_spec_in_plain_python_is_not_a_signal():
    # the same text outside a manifest must not fire manifest rules
    assert _detect("langchain>=0.1.0\n", "app.py") == []


def test_is_manifest_file():
    assert is_manifest_file("requirements.txt")
    assert is_manifest_file("backend/requirements-dev.txt")
    assert is_manifest_file("pyproject.toml")
    assert is_manifest_file("services/api/package.json")
    assert not is_manifest_file("requirements_helper.py")
    assert not is_manifest_file("app/main.py")


# --- determinism and limits --------------------------------------------------


def test_detection_is_deterministic():
    text = 'import openai\nmodel = "gpt-4o"\nfrom langchain.chains import LLMChain\n'
    first = _detect(text)
    second = _detect(text)
    assert first == second


def test_detection_stops_at_limit():
    text = "import openai\nimport anthropic\nimport ollama\n"
    observations = _detect(text, limit=2)
    assert len(observations) == 2


def test_limit_zero_returns_nothing():
    assert _detect("import openai\n", limit=0) == []


def test_line_numbers_are_reported():
    text = 'first line\n\nmodel = "gpt-4o"\n'
    observation = _one(_detect(text), "model.assignment")
    assert observation.line_number == 3


def test_duplicate_hits_on_same_line_collapse():
    observations = _detect("a = openai.OpenAI(); b = openai.OpenAI()\n")
    assert len(observations) == 1


def test_empty_text_returns_nothing():
    assert _detect("") == []
