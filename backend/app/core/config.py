from uuid import UUID

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://auditra:auditra@localhost:5433/auditra"
    default_tenant_id: UUID = UUID("11111111-1111-1111-1111-111111111111")
    usage_enabled: bool = True
    usage_mode: str = "best_effort"
    usage_batch_size: int = 100
    usage_default_page_size: int = 50
    usage_max_page_size: int = 200
    # Asset Lifecycle (Sub-feature 8): how many completed discovery runs
    # of a source may miss an asset before it becomes STALE / DISAPPEARED
    discovery_lifecycle_stale_after_missed_runs: int = 1
    discovery_lifecycle_disappeared_after_missed_runs: int = 3
    model_config = SettingsConfigDict(env_prefix="AUDITRA_", env_file=".env")


settings = Settings()
