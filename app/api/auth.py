from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_access_token, hash_password, verify_password
from app.config import get_settings
from app.db import get_db
from app.models import User
from app.repositories.users import get_user_by_email
from app.schemas.auth import LoginRequest, LoginResponse, RegisterRequest, UserPublic


router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserPublic, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, session: AsyncSession = Depends(get_db)) -> User:
    if await get_user_by_email(session, payload.email) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="USER_EXISTS")
    user = User(email=payload.email, password_hash=hash_password(payload.password), role="user")
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="USER_EXISTS") from exc
    await session.refresh(user)
    return user


@router.post("/login", response_model=LoginResponse)
async def login(payload: LoginRequest, session: AsyncSession = Depends(get_db)) -> LoginResponse:
    user = await get_user_by_email(session, payload.email)
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="INVALID_CREDENTIALS")
    settings = get_settings()
    return LoginResponse(
        access_token=create_access_token(user.id, user.role),
        expires_in=settings.jwt_expire_seconds,
        user=UserPublic.model_validate(user),
    )
