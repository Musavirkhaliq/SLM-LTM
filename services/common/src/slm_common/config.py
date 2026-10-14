from __future__ import annotations

from functools import lru_cache

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_JWT_SECRET = "dev-only-secret-change-me-0123456789"


class BaseServiceSettings(BaseSettings):
    """Settings every service shares. Services subclass this and add their own fields."""

    model_config = SettingsConfigDict(env_prefix="SLM_", env_file=".env", extra="ignore")

    env: str = "dev"
    log_level: str = "INFO"
    database_url: str = "postgresql://slm_app:slm_app@localhost:5432/slm"
    redis_url: str | None = None
    otlp_endpoint: str | None = None

    jwt_algorithm: str = "HS256"
    jwt_secret: SecretStr = SecretStr(DEV_JWT_SECRET)  # HS256 signing key
    jwt_private_key: SecretStr | None = None  # RS256: only the auth service needs it
    jwt_public_key: str | None = None  # RS256: every service verifying tokens
    jwt_issuer: str = "slm-studio"
    jwt_access_ttl_seconds: int = 15 * 60
    jwt_refresh_ttl_seconds: int = 30 * 24 * 3600

    @model_validator(mode="after")
    def _reject_insecure_prod(self) -> BaseServiceSettings:
        if (
            self.env == "prod"
            and self.jwt_algorithm == "HS256"
            and self.jwt_secret.get_secret_value() == DEV_JWT_SECRET
        ):
            raise ValueError("SLM_JWT_SECRET must be set when SLM_ENV=prod")
        return self


@lru_cache
def get_settings() -> BaseServiceSettings:
    return BaseServiceSettings()
