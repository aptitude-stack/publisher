"""Console entrypoint for the Aptitude Publisher MCP server."""

from __future__ import annotations

from publisher.interfaces.mcp.server import create_server


def main() -> None:
    """Run the local stdio MCP server."""

    from publisher.app.cli import _load_local_env_defaults

    _load_local_env_defaults()
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
