import pytest


@pytest.fixture(autouse=True)
def isolated_publisher_cache(tmp_path_factory, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path_factory.mktemp("publisher-cache")))
