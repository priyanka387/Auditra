def build_canonical_key(provider_slug: str, native_model_id: str) -> str:
    slug = provider_slug.strip()
    native_id = native_model_id.strip()
    if not slug or not native_id:
        raise ValueError("provider_slug and native_model_id must be non-empty")
    return f"{slug.lower()}|{native_id}"


VERSION_IDENTITY_TYPES = {"native", "revision", "release", "checkpoint", "label", "opaque"}


def build_canonical_version_key(
    identity_type: str, version_label: str, native_version_id: str | None
) -> str:
    itype = (identity_type or "").strip().lower()
    if itype not in VERSION_IDENTITY_TYPES:
        raise ValueError(f"identity_type must be one of {sorted(VERSION_IDENTITY_TYPES)}")
    label = (version_label or "").strip()
    if not label:
        raise ValueError("version_label must be non-empty")
    native = (native_version_id or "").strip()
    return f"{itype}:{native or label}"
