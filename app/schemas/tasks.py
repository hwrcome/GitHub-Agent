from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class TaskCreated(BaseModel):
    task_id: UUID
    status: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TaskError(BaseModel):
    code: str
    message: str


class TaskView(BaseModel):
    task_id: UUID
    task_type: str
    status: str
    progress: str
    retry_count: int
    result: dict[str, Any] | None = None
    error: TaskError | None = None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None
