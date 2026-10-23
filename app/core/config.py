from functools import lru_cache

from pydantic import Field, SecretStr
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
    portfolio_model_path: str = "models/portfolio-anomaly.joblib"
    portfolio_demo_model_path: str = "models/portfolio-anomaly-demo.joblib"
    portfolio_review_threshold: int = Field(default=80, ge=1, le=100)
    portfolio_min_peers: int = Field(default=5, ge=3)
    portfolio_patient_monthly_threshold: int = Field(default=12, ge=1)
    visual_analysis_enabled: bool = False
    gemini_api_key: SecretStr = SecretStr("")
    gemini_model: str = Field(default="gemini-3.1-flash-lite", pattern=r"^gemini-[a-zA-Z0-9_.-]+$")
    visual_analysis_timeout_seconds: int = Field(default=35, ge=5, le=90)

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
