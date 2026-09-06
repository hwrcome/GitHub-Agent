from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class DocumentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1, max_length=2_000_000)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("title", "content")
    @classmethod
    def reject_whitespace_only(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("metadata")
    @classmethod
    def limit_metadata_size(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > 16_384:
            raise ValueError("metadata is too large")
        return value


class DocumentSubmission(BaseModel):
    document_id: UUID
    task_id: UUID
    status: str
