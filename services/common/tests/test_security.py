import time
import uuid

import pytest

from slm_common.config import BaseServiceSettings
from slm_common.errors import ApiError
from slm_common.security import (
    create_access_token,
    decode_token,
    generate_api_key,
    generate_refresh_token,
    hash_api_key,
    hash_password,
    hash_token,
    looks_like_api_key,
    verify_password,
)

SETTINGS = BaseServiceSettings(jwt_secret="unit-test-secret-unit-test-secret-1234")


def test_password_roundtrip_and_argon2id():
    h = hash_password("correct horse")
    assert h.startswith("$argon2id$")
    assert verify_password("correct horse", h)
    assert not verify_password("wrong", h)
    assert not verify_password("anything", None)
    assert not verify_password("anything", "not-a-hash")


def test_access_token_claims_roundtrip():
    uid, oid = uuid.uuid4(), uuid.uuid4()
    token = create_access_token(user_id=uid, org_id=oid, role="org_admin", settings=SETTINGS)
    claims = decode_token(token, SETTINGS)
    assert claims["sub"] == str(uid)
    assert claims["org"] == str(oid)
    assert claims["role"] == "org_admin"
    assert claims["exp"] - claims["iat"] == 900
    assert len(claims["jti"]) == 32


def test_expired_token_rejected():
    token = create_access_token(
        user_id=uuid.uuid4(), org_id=None, role="viewer", settings=SETTINGS, now=time.time() - 3600
    )
    with pytest.raises(ApiError) as e:
        decode_token(token, SETTINGS)
    assert e.value.status_code == 401
    assert e.value.code == "token_expired"


def test_tampered_or_foreign_token_rejected():
    token = create_access_token(user_id=uuid.uuid4(), org_id=None, role="viewer", settings=SETTINGS)
    other = BaseServiceSettings(jwt_secret="a-different-secret-a-different-secret-12")
    for bad in (token[:-2] + "xx", token):
        with pytest.raises(ApiError) as e:
            decode_token(bad, other)
        assert e.value.code == "invalid_token"


def test_rs256_roundtrip():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public = (
        key.public_key()
        .public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        .decode()
    )
    signer = BaseServiceSettings(jwt_algorithm="RS256", jwt_private_key=private, jwt_public_key=public)
    verifier = BaseServiceSettings(jwt_algorithm="RS256", jwt_public_key=public)
    token = create_access_token(user_id=uuid.uuid4(), org_id=None, role="viewer", settings=signer)
    assert decode_token(token, verifier)["role"] == "viewer"


def test_api_key_format_and_hash():
    plaintext, prefix, digest = generate_api_key()
    assert plaintext.startswith(f"slm_{prefix}_")
    assert len(prefix) == 8 and len(plaintext.split("_")[2]) == 40
    assert looks_like_api_key(plaintext)
    assert digest == hash_api_key(plaintext) and len(digest) == 32
    assert generate_api_key()[0] != plaintext
    assert not looks_like_api_key("slm_short_key")


def test_refresh_token_hash_matches():
    token, digest = generate_refresh_token()
    assert hash_token(token) == digest
    assert generate_refresh_token()[0] != token


def test_prod_rejects_default_secret():
    with pytest.raises(ValueError, match="SLM_JWT_SECRET"):
        BaseServiceSettings(env="prod")
