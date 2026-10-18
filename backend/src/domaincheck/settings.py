from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ENV_FILE = BACKEND_ROOT / ".env"


class Settings(BaseSettings):
    """Process configuration.

    Secret values have no usable defaults and are never loaded from legacy
    ``config.ini``. The web importer is a separate, explicit operation.
    """

    model_config = SettingsConfigDict(
        env_file=BACKEND_ENV_FILE,
        env_file_encoding="utf-8",
        env_prefix="DOMAINCHECK_",
        case_sensitive=False,
        extra="ignore",
    )

    environment: Literal["development", "test", "production"] = "development"
    api_prefix: str = "/api/v1"
    database_url: str = "postgresql+asyncpg://domaincheck@localhost/domaincheck"
    redis_url: str = "redis://localhost:6379/0"
    artifact_root: Path = BACKEND_ROOT / ".runtime" / "artifacts"
    artifact_max_file_bytes: int = 10 * 1024 * 1024
    artifact_max_dimension: int = 16_384
    artifact_max_pixels: int = 40_000_000
    default_timezone: str = "Asia/Bangkok"
    allowed_origins: tuple[str, ...] = ("http://127.0.0.1:5173", "http://localhost:5173")
    telegram_issuer: str = "https://oauth.telegram.org"
    telegram_client_id: str | None = None
    telegram_client_secret: SecretStr | None = None
    telegram_redirect_uri: str = "http://127.0.0.1:5173/api/v1/auth/telegram/callback"
    oidc_state_ttl_seconds: int = 300
    form_task_ttl_seconds: int = Field(default=300, ge=60, le=900)
    netcraft_submit_enabled: bool = False
    cloudflare_submit_enabled: bool = True
    cloudflare_runner_url: str = "http://127.0.0.1:8789"
    vt_api_key: SecretStr | None = None
    gsb_api_key: SecretStr | None = None
    browser_capture_enabled: bool = False
    browser_egress_proxy_url: SecretStr | None = None
    mail_connectivity_enabled: bool = False
    mail_egress_endpoints: tuple[str, ...] = ()
    secret_master_key: SecretStr | None = None
    secret_key_version: int = 1
    bootstrap_telegram_subject: str | None = None

    @field_validator("artifact_root", mode="after")
    @classmethod
    def stable_artifact_root(cls, value: Path) -> Path:
        # Relative deployment paths are rooted at the workspace/package parent,
        # independently of API/worker working directories.
        return value.expanduser().resolve() if value.is_absolute() else (BACKEND_ROOT.parent / value).resolve()

    def validate_for_production(self) -> None:
        missing = []
        if not self.telegram_client_id:
            missing.append("DOMAINCHECK_TELEGRAM_CLIENT_ID")
        if not self.telegram_client_secret:
            missing.append("DOMAINCHECK_TELEGRAM_CLIENT_SECRET")
        if not self.secret_master_key:
            missing.append("DOMAINCHECK_SECRET_MASTER_KEY")
        if not self.bootstrap_telegram_subject:
            missing.append("DOMAINCHECK_BOOTSTRAP_TELEGRAM_SUBJECT")
        if missing:
            raise ValueError("Missing required production settings: " + ", ".join(missing))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    if settings.environment == "production":
        settings.validate_for_production()
    return settings

