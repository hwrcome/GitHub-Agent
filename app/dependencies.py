from fastapi import Depends

from app.auth import get_current_user
from app.errors import ApiError
from app.models import User


async def require_user(user: User = Depends(get_current_user)) -> User:
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise ApiError(403, "FORBIDDEN", "Admin role required")
    return user
