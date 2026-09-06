import importlib
from uuid import uuid4

import pytest

from app.schemas.agent import SearchInput


def test_mock_runner_is_deterministic(monkeypatch):
    from app import agent_runner

    task_id = uuid4()
    search_input = SearchInput(query="python inference", config={"max_results": 20})
    monkeypatch.setattr(agent_runner, "load_search_input", lambda _: search_input)

    first = agent_runner.run_search(task_id, mode="mock")
    second = agent_runner.run_search(task_id, mode="mock")

    assert first == second
    assert first.final_results
    assert len(first.repositories) == 3


def test_mock_runner_emits_stable_progress(monkeypatch):
    from app import agent_runner

    monkeypatch.setattr(
        agent_runner,
        "load_search_input",
        lambda _: SearchInput(query="python inference", config={}),
    )
    progress: list[str] = []
    agent_runner.run_search(uuid4(), mode="mock", progress_callback=progress.append)
    assert progress == [
        "QUERY_ANALYZED",
        "REPOS_FETCHED",
        "RERANKING",
        "REPORT_GENERATING",
        "DONE",
    ]


def test_runner_does_not_prompt_for_credentials_on_import(monkeypatch):
    import getpass

    def fail_prompt(*args, **kwargs):
        raise AssertionError("credential prompt must not run during import")

    monkeypatch.setattr(getpass, "getpass", fail_prompt)
    importlib.import_module("app.agent_runner")


def test_runner_rejects_unknown_mode(monkeypatch):
    from app import agent_runner

    monkeypatch.setattr(
        agent_runner,
        "load_search_input",
        lambda _: SearchInput(query="python inference", config={}),
    )
    with pytest.raises(agent_runner.PermanentAgentError, match="Unsupported"):
        agent_runner.run_search(uuid4(), mode="invalid")
