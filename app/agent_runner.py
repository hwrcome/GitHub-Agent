from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.models import SearchRequest
from app.schemas.agent import SearchInput, SearchRunResult


ProgressCallback = Callable[[str], None]


class AgentError(RuntimeError):
    pass


class PermanentAgentError(AgentError):
    pass


class TransientAgentError(AgentError):
    pass


async def _load_search_input(task_id: UUID) -> SearchInput:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            request = await session.get(SearchRequest, task_id)
            if request is None:
                raise PermanentAgentError(f"Search request {task_id} does not exist")
            return SearchInput(query=request.query, config=request.config)
    finally:
        await engine.dispose()


def load_search_input(task_id: UUID) -> SearchInput:
    return asyncio.run(_load_search_input(task_id))


def _emit(progress_callback: ProgressCallback | None, stage: str) -> None:
    if progress_callback is not None:
        progress_callback(stage)


def run_mock_search(
    search_input: SearchInput,
    progress_callback: ProgressCallback | None = None,
) -> SearchRunResult:
    repositories = [
        {
            "name": "vllm-project/vllm",
            "url": "https://github.com/vllm-project/vllm",
            "description": "High-throughput LLM inference engine",
            "score": 0.96,
        },
        {
            "name": "ggerganov/llama.cpp",
            "url": "https://github.com/ggerganov/llama.cpp",
            "description": "Portable inference with quantization support",
            "score": 0.93,
        },
        {
            "name": "huggingface/text-generation-inference",
            "url": "https://github.com/huggingface/text-generation-inference",
            "description": "Production toolkit for text generation",
            "score": 0.89,
        },
    ]
    for stage in ("QUERY_ANALYZED", "REPOS_FETCHED", "RERANKING", "REPORT_GENERATING"):
        _emit(progress_callback, stage)
    result = SearchRunResult(
        final_results=f"# Mock recommendations\n\nQuery: {search_input.query}",
        repositories=repositories,
        filtered_candidates=repositories,
        search_history=[search_input.query],
        metadata={"mode": "mock", "config": search_input.config},
    )
    _emit(progress_callback, "DONE")
    return result


def _normalize_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item if isinstance(item, dict) else {"value": str(item)} for item in value]


def run_real_search(
    task_id: UUID,
    search_input: SearchInput,
    progress_callback: ProgressCallback | None = None,
) -> SearchRunResult:
    from agent_new import graph

    _emit(progress_callback, "QUERY_ANALYZED")
    raw = graph.invoke(
        {"user_query": search_input.query},
        config={"configurable": {"thread_id": str(task_id), **search_input.config}},
    )
    if not isinstance(raw, dict):
        raise PermanentAgentError("Agent returned an unsupported result")
    _emit(progress_callback, "DONE")
    return SearchRunResult(
        final_results=str(raw.get("final_results", "")),
        repositories=_normalize_items(raw.get("repositories")),
        filtered_candidates=_normalize_items(raw.get("filtered_candidates")),
        search_history=[str(item) for item in raw.get("search_history", [])],
        metadata={"mode": "real"},
    )


def run_search(
    task_id: UUID,
    *,
    mode: Literal["mock", "real"],
    progress_callback: ProgressCallback | None = None,
) -> SearchRunResult:
    search_input = load_search_input(task_id)
    if mode == "mock":
        return run_mock_search(search_input, progress_callback)
    if mode == "real":
        return run_real_search(task_id, search_input, progress_callback)
    raise PermanentAgentError(f"Unsupported agent mode: {mode}")
