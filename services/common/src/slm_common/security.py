from __future__ import annotations

import hashlib
import re
import secrets
import string
import time
import uuid
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from slm_common.config import BaseServiceSettings, get_settings
from slm_common.errors import ApiError

_hasher = PasswordHasher()  # argon2id with library defaults
_ALNUM = string.ascii_letters + string.digits

API_KEY_CACHE_PREFIX = "slm:apikey:"  # Redis key = prefix + sha256 hex; auth deletes on revoke
API_KEY_PREFIX_LEN = 8
API_KEY_SECRET_LEN = 40
API_KEY_RE = re.compile(rf"^slm_[A-Za-z0-9]{{{API_KEY_PREFIX_LEN}}}_[A-Za-z0-9]{{{API_KEY_SECRET_LEN}}}$")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    """True when `password` matches the stored argon2id hash (False for SSO-only users)."""
    if not password_hash:
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def _signing_key(settings: BaseServiceSettings) -> str:
    if settings.jwt_algorithm.startswith("RS"):
        if settings.jwt_private_key is None:
            raise RuntimeError("SLM_JWT_PRIVATE_KEY is required to issue RS256 tokens")
        return settings.jwt_private_key.get_secret_value()
    return settings.jwt_secret.get_secret_value()


def _verification_key(settings: BaseServiceSettings) -> str:
    if settings.jwt_algorithm.startswith("RS"):
        if settings.jwt_public_key is None:
            raise RuntimeError("SLM_JWT_PUBLIC_KEY is required to verify RS256 tokens")
        return settings.jwt_public_key
    return settings.jwt_secret.get_secret_value()


def create_access_token(
    *,
    user_id: uuid.UUID | str,
    org_id: uuid.UUID | str | None,
    role: str,
    settings: BaseServiceSettings | None = None,
    ttl_seconds: int | None = None,
    now: float | None = None,
) -> str:
    """Issue a signed access JWT with claims sub, org, role, exp, iat, jti (and iss)."""
    settings = settings or get_settings()
    issued = int(now if now is not None else time.time())
    claims: dict[str, Any] = {
        "sub": str(user_id),
        "org": str(org_id) if org_id else None,
        "role": role,
        "iat": issued,
        "exp": issued + (ttl_seconds if ttl_seconds is not None else settings.jwt_access_ttl_seconds),
        "jti": uuid.uuid4().hex,
        "iss": settings.jwt_issuer,
    }
    return jwt.encode(claims, _signing_key(settings), algorithm=settings.jwt_algorithm)


def decode_token(token: str, settings: BaseServiceSettings | None = None) -> dict[str, Any]:
    """Verify signature, expiry and issuer; raises a 401 ApiError on any failure."""
    settings = settings or get_settings()
    try:
        return jwt.decode(
            token,
            _verification_key(settings),
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise ApiError.unauthorized("Token has expired", "token_expired") from exc
    except jwt.PyJWTError as exc:
        raise ApiError.unauthorized("Invalid token", "invalid_token") from exc


def hash_token(token: str) -> bytes:
    """sha256 of an opaque token (refresh tokens)."""
    return hashlib.sha256(token.encode()).digest()


def generate_refresh_token() -> tuple[str, bytes]:
    token = secrets.token_urlsafe(48)
    return token, hash_token(token)


def hash_api_key(plaintext: str) -> bytes:
    return hashlib.sha256(plaintext.encode()).digest()


def generate_api_key() -> tuple[str, str, bytes]:
    """Return (plaintext, prefix, sha256) for `slm_<8 char prefix>_<40 char secret>`.

    The alphabet is alphanumeric so the `_` separators are unambiguous.
    """
    prefix = "".join(secrets.choice(_ALNUM) for _ in range(API_KEY_PREFIX_LEN))
    secret = "".join(secrets.choice(_ALNUM) for _ in range(API_KEY_SECRET_LEN))
    plaintext = f"slm_{prefix}_{secret}"
    return plaintext, prefix, hash_api_key(plaintext)


def looks_like_api_key(value: str) -> bool:
    return API_KEY_RE.match(value) is not None
