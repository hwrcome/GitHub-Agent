from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ValidationError
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_db
from app.models import User


password_hash = PasswordHash.recommended()
bearer_scheme = HTTPBearer(auto_error=False)


class TokenClaims(BaseModel):
    sub: UUID
    role: str
    iat: int
    exp: int


class InvalidTokenError(ValueError):
    """Raised when a bearer token cannot be trusted."""


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    try:
        return password_hash.verify(password, encoded)
    except Exception:
        return False


def create_access_token(user_id: UUID, role: str) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    issued_at = int(now.timestamp())
    expires_at = issued_at + settings.jwt_expire_seconds
    payload = {"sub": str(user_id), "role": role, "iat": issued_at, "exp": expires_at}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> TokenClaims:
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        return TokenClaims.model_validate(payload)
    except (jwt.PyJWTError, ValidationError, ValueError) as exc:
        raise InvalidTokenError("invalid access token") from exc


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    try:
        claims = decode_access_token(credentials.credentials)
    except InvalidTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token") from exc
    user = await session.scalar(select(User).where(User.id == claims.sub))
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token")
    return user
