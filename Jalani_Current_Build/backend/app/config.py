from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    simulator_url: str = "http://localhost:8000"
    database_url: str = "sqlite:///./jalani.db"
    jwt_secret: str = Field(default="demo-only-change-this-secret-before-deploying", min_length=32)
    viewer_user: str = "viewer"
    viewer_password: str = "demo-viewer"
    operator_user: str = "operator"
    operator_password: str = "demo-operator"
    admin_user: str = "admin"
    admin_password: str = "demo-admin"
    app_version: str = "0.1.0"
    git_sha: str = "dev"
    poll_seconds: float = Field(default=1, ge=0.1, le=60)
    max_state_age_seconds: float = Field(default=15, gt=0)
    max_tick_lag: int = Field(default=8, ge=0)
    snapshot_tick_span: int = Field(default=2, ge=0)
    sim_timeout_seconds: float = Field(default=5, gt=0)
    retry_base_seconds: float = Field(default=0.15, ge=0)
    breaker_failures: int = Field(default=5, ge=1)
    breaker_seconds: float = Field(default=10, gt=0)
    monte_carlo_paths: int = Field(default=300, ge=10, le=5000)
    horizon_hours: int = Field(default=12, ge=1, le=72)
    chaos_enabled: bool = False
    background_enabled: bool = True
    stream_enabled: bool = True
    llm_api_key: str = ""
    ml_service_url: str = ""


settings = Settings()
