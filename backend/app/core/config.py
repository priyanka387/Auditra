from uuid import UUID

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://auditra:auditra@localhost:5433/auditra"
    default_tenant_id: UUID = UUID("11111111-1111-1111-1111-111111111111")
    model_config = SettingsConfigDict(env_prefix="AUDITRA_", env_file=".env")


settings = Settings()
