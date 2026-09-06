from pathlib import Path


def test_real_mode_uses_environment_mcp_paths(monkeypatch):
    monkeypatch.setenv("MCP_SERVER_SCRIPT", "C:/agent/mcp_server.py")
    monkeypatch.setenv("MCP_SERVER_PYTHON", "C:/venv/python.exe")
    from app.agent_runner import build_mcp_command

    assert build_mcp_command() == ["C:/venv/python.exe", "C:/agent/mcp_server.py"]


def test_github_requests_define_timeout():
    source = Path("tools/activity_analysis.py").read_text(encoding="utf-8")
    assert "timeout=" in source


def test_repo_cache_store_has_async_boundary():
    from app.repositories.repo_cache import RepoCacheStore

    assert hasattr(RepoCacheStore, "get")
    assert hasattr(RepoCacheStore, "save")
