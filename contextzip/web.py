"""
web.py — Run contextzip where there's no terminal.

contextzipcli.com runs the real contextzip in the browser through Pyodide,
so a folder dropped on the page is packaged (and a reply applied) with
exactly the CLI's rules; service.py runs it on a server. Both call `run`,
which is the CLI with --json, returned as a dict instead of printed:

    from contextzip.web import run

    result = run(["--no-clipboard"], cwd="/project")
    result["zip"]["path"]                          # the ZIP it wrote
    run(["apply-zip", "/reply.zip", "--safe-only"], cwd="/project")

The result is the same object `contextzip … --json` prints (see CLI.md,
"JSON output"). Nothing here prompts, and nothing raises for a failed run:
check result["ok"].
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import threading

# The working directory is process-wide, so runs take turns.
_lock = threading.Lock()


def run(args: list[str], cwd: str | os.PathLike | None = None) -> dict:
    """Run `contextzip <args> --json` in *cwd* and return its result."""
    import click

    from contextzip.cli import main

    argv = list(args) if "--json" in args else [*args, "--json"]
    out = io.StringIO()
    with _lock:
        previous = os.getcwd()
        try:
            if cwd is not None:
                os.chdir(cwd)
            with contextlib.redirect_stdout(out):
                try:
                    main.main(args=argv, prog_name="contextzip", standalone_mode=False)
                except SystemExit:
                    pass  # the JSON result is already written; "ok" says how it went
                except click.ClickException as exc:
                    # Bad arguments fail before --json takes effect.
                    return _failure(exc.format_message())
        finally:
            os.chdir(previous)
    return _parse(out.getvalue()) or _failure("contextzip didn't return a result")


def run_json(args: list[str], cwd: str | None = None) -> str:
    """`run`, as a JSON string: the easiest shape to hand back to JavaScript."""
    return json.dumps(run(args, cwd), ensure_ascii=True)


def _parse(stdout: str) -> dict | None:
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                data = json.loads(line)
            except ValueError:
                continue
            if isinstance(data, dict) and "ok" in data:
                return data
    return None


def _failure(message: str) -> dict:
    from contextzip import __version__

    return {"ok": False, "command": None, "version": __version__, "error": message, "warnings": [], "log": []}
