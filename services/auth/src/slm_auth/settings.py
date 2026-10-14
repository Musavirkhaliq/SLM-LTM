from __future__ import annotations

from functools import lru_cache

from pydantic import BaseModel, SecretStr
from pydantic_settings import SettingsConfigDict

from slm_common.config import BaseServiceSettings

_DEFAULTS: dict[str, dict[str, object]] = {
    "google": {
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "userinfo_url": "https://openidconnect.googleapis.com/v1/userinfo",
        "scopes": ["openid", "email", "profile"],
    },
    "azure_ad": {
        "authorize_url": "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize",
        "token_url": "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        "userinfo_url": "https://graph.microsoft.com/oidc/userinfo",
        "scopes": ["openid", "email", "profile"],
    },
    "github": {
        "authorize_url": "https://github.com/login/oauth/authorize",
        "token_url": "https://github.com/login/oauth/access_token",
        "userinfo_url": "https://api.github.com/user",
        "scopes": ["read:user", "user:email"],
    },
}

PROVIDERS = tuple(_DEFAULTS)


class OAuthProviderSettings(BaseModel):
    """Per-provider overrides. Endpoints default to the public ones for each provider."""

    client_id: str | None = None
    client_secret: SecretStr | None = None
    authorize_url: str | None = None
    token_url: str | None = None
    userinfo_url: str | None = None
    emails_url: str | None = None  # GitHub only: verified-email lookup
    scopes: list[str] | None = None
    tenant: str = "common"  # Azure AD tenant id or domain


class ResolvedProvider(BaseModel):
    name: str
    client_id: str
    client_secret: str
    authorize_url: str
    token_url: str
    userinfo_url: str
    emails_url: str | None
    scopes: list[str]


class AuthSettings(BaseServiceSettings):
    """Env: `SLM_OAUTH__GOOGLE__CLIENT_ID=...` (double underscore nests)."""

    model_config = SettingsConfigDict(
        env_prefix="SLM_", env_file=".env", extra="ignore", env_nested_delimiter="__"
    )

    public_base_url: str = "http://localhost:8000"
    oauth: dict[str, OAuthProviderSettings] = {}
    oauth_success_redirect: str | None = None  # if set, callback redirects with tokens in the fragment
    oauth_state_ttl_seconds: int = 600
    api_key_default_grace_seconds: int = 24 * 3600
    api_key_max_grace_seconds: int = 30 * 24 * 3600
    password_min_length: int = 10

    def provider(self, name: str) -> ResolvedProvider | None:
        defaults = _DEFAULTS.get(name)
        cfg = self.oauth.get(name)
        if defaults is None or cfg is None or not cfg.client_id or cfg.client_secret is None:
            return None

        def pick(field: str) -> str:
            value = getattr(cfg, field) or defaults[field]
            return str(value).format(tenant=cfg.tenant)

        return ResolvedProvider(
            name=name,
            client_id=cfg.client_id,
            client_secret=cfg.client_secret.get_secret_value(),
            authorize_url=pick("authorize_url"),
            token_url=pick("token_url"),
            userinfo_url=pick("userinfo_url"),
            emails_url=cfg.emails_url
            or ("https://api.github.com/user/emails" if name == "github" else None),
            scopes=list(cfg.scopes or defaults["scopes"]),  # type: ignore[arg-type]
        )

    @property
    def redirect_base(self) -> str:
        return self.public_base_url.rstrip("/") + "/api/v1/auth/oauth"


@lru_cache
def get_auth_settings() -> AuthSettings:
    return AuthSettings()
