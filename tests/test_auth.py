from uuid import uuid4

import jwt
import pytest

from app.auth import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.config import get_settings


def test_password_hash_is_not_plaintext():
    hashed = hash_password("correct horse battery staple")
    assert hashed != "correct horse battery staple"
    assert verify_password("correct horse battery staple", hashed)
    assert not verify_password("wrong", hashed)


def test_access_token_round_trip_contains_identity_and_role():
    user_id = uuid4()
    token = create_access_token(user_id, "user")
    claims = decode_access_token(token)
    assert claims.sub == user_id
    assert claims.role == "user"
    assert claims.exp > claims.iat


def test_expired_access_token_is_rejected():
    settings = get_settings()
    token = jwt.encode(
        {"sub": str(uuid4()), "role": "user", "iat": 1, "exp": 1},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    with pytest.raises(ValueError):
        decode_access_token(token)
