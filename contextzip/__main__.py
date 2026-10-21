"""
`python -m contextzip` — works even when the `cz` command isn't on PATH.

When that's why it's being used, a one-line tip points to `setup-path`.
"""

from __future__ import annotations

import sys


def _path_tip() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "setup-path":
        return
    if not sys.stderr.isatty():
        return
    try:
        from contextzip import pathsetup

        st = pathsetup.status()
        if st.directory is None or st.on_path or st.manager or st.in_venv:
            return
    except Exception:  # noqa: BLE001 — a tip must never break the run
        return
    from rich.console import Console

    Console(stderr=True).print(
        "[dim]Tip: the cz command isn't on your PATH yet — run "
        "[bold]python -m contextzip setup-path[/] to fix that.[/]"
    )


if __name__ == "__main__":
    from contextzip.cli import main

    _path_tip()
    main()
