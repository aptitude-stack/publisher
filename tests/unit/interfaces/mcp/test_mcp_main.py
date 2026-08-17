from __future__ import annotations

from pathlib import Path


def test_main_runs_stdio_server(monkeypatch) -> None:
    from publisher.interfaces.mcp import main

    calls: list[str] = []

    class FakeServer:
        def run(self, *, transport: str) -> None:
            calls.append(transport)

    monkeypatch.setattr(main, "create_server", lambda: FakeServer())

    main.main()

    assert calls == ["stdio"]


def test_pyproject_exposes_direct_mcp_entrypoint() -> None:
    pyproject = (Path(__file__).resolve().parents[4] / "pyproject.toml").read_text(
        encoding="utf-8"
    )

    assert 'aptitude-publisher-mcp = "publisher.interfaces.mcp.main:main"' in pyproject
