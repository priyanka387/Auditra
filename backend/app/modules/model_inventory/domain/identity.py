def build_canonical_key(provider_slug: str, native_model_id: str) -> str:
    slug = provider_slug.strip()
    native_id = native_model_id.strip()
    if not slug or not native_id:
        raise ValueError("provider_slug and native_model_id must be non-empty")
    return f"{slug.lower()}|{native_id}"
