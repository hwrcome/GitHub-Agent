from typing import Any

from pydantic import BaseModel, Field


class SearchInput(BaseModel):
    query: str
    config: dict[str, Any] = Field(default_factory=dict)


class SearchRunResult(BaseModel):
    final_results: str
    repositories: list[dict[str, Any]] = Field(default_factory=list)
    filtered_candidates: list[dict[str, Any]] = Field(default_factory=list)
    search_history: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
