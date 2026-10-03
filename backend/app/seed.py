from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.modules.model_inventory.models import ModelProvider, ModelType

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
        db.commit()
