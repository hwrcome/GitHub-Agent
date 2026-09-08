pytest_plugins = ["tests.api_fixtures"]


def pytest_collection_modifyitems(config, items):
    if "integration" in config.getoption("markexpr", default=""):
        return
    import pytest

    skip_integration = pytest.mark.skip(reason="integration tests require -m integration")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_integration)
