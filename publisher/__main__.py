"""Module entrypoint for `python -m publisher`."""

from __future__ import annotations

from publisher.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
