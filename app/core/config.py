from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Erix Reembolso API"
    database_url: str = "postgresql+psycopg://erix:erix@localhost:5432/erix"
    api_key: str = "dev-only-change-me"
    upload_dir: str = "uploads"
    model_path: str = "models/reimbursement-risk.joblib"
    max_upload_bytes: int = 10 * 1024 * 1024
    risk_review_threshold: float = Field(default=0.35, ge=0, le=1)
    low_ocr_confidence_threshold: float = Field(default=0.65, ge=0, le=1)

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
