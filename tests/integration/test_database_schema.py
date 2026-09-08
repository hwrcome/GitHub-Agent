import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models import Task, User


@pytest.mark.integration
async def test_user_email_is_unique(db_session):
    db_session.add(User(email="same@example.com", password_hash="x", role="user"))
    await db_session.commit()
    db_session.add(User(email="same@example.com", password_hash="y", role="user"))

    with pytest.raises(IntegrityError):
        await db_session.commit()


@pytest.mark.integration
async def test_transaction_rolls_back_task_and_request(db_session, user):
    with pytest.raises(RuntimeError, match="rollback"):
        async with db_session.begin():
            db_session.add(
                Task(
                    user_id=user.id,
                    task_type="SEARCH",
                    status="PENDING",
                    progress="QUEUED",
                )
            )
            raise RuntimeError("rollback")

    result = await db_session.execute(select(func.count(Task.id)))
    assert result.scalar_one() == 0
