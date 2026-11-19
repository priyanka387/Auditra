from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import SessionLocal
from app.modules.assessment.seed import seed_assessment_templates
from app.modules.model_inventory.models import ModelProvider, ModelType
from app.modules.risk.seed import seed_risk_categories
from app.modules.risk_scoring.seed import seed_scoring_framework

PROVIDERS = [
    ("openai", "OpenAI"),
    ("anthropic", "Anthropic"),
    ("google", "Google"),
    ("meta", "Meta"),
    ("mistral", "Mistral"),
    ("cohere", "Cohere"),
    ("huggingface", "Hugging Face"),
    ("aws", "AWS"),
    ("azure", "Azure"),
    ("gcp", "GCP"),
    ("ollama", "Ollama"),
    ("vllm", "vLLM"),
    ("internal", "Internal / Custom"),
]

MODEL_TYPES = [
    ("llm", "LLM"),
    ("generative-ai", "Generative AI"),
    ("embedding", "Embedding Model"),
    ("reranker", "Reranker"),
    ("nlp", "NLP Model"),
    ("computer-vision", "Computer Vision Model"),
    ("multimodal", "Multimodal Model"),
    ("classification", "Classification Model"),
    ("object-detection", "Object Detection Model"),
    ("recommendation", "Recommendation Model"),
    ("forecasting", "Forecasting Model"),
    ("speech", "Speech Model"),
    ("deep-learning", "Deep Learning Model"),
    ("reinforcement-learning", "Reinforcement Learning Model"),
    ("custom-ml", "Custom ML Model"),
]


def seed_reference_data(db: Session) -> None:
    provider_slugs = set(db.scalars(select(ModelProvider.slug)))
    for slug, name in PROVIDERS:
        if slug not in provider_slugs:
            db.add(ModelProvider(slug=slug, name=name))

    type_slugs = set(db.scalars(select(ModelType.slug)))
    for slug, name in MODEL_TYPES:
        if slug not in type_slugs:
            db.add(ModelType(slug=slug, name=name, is_active=True, is_system=True))

    db.flush()


if __name__ == "__main__":
    with SessionLocal() as db:
        seed_reference_data(db)
        # RF-01: per-tenant system risk taxonomy; idempotent, so re-running
        # the seed never duplicates categories
        seed_risk_categories(db, settings.default_tenant_id)
        # RF-02: per-tenant system assessment template; idempotent, so
        # re-running the seed never duplicates the questionnaire
        seed_assessment_templates(db, settings.default_tenant_id)
        # RF-03: per-tenant system scoring framework; idempotent, so
        # re-running the seed never duplicates the matrix
        seed_scoring_framework(db, settings.default_tenant_id)
        db.commit()
