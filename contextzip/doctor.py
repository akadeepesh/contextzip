"""
doctor.py — `contextzip doctor`: check that everything works on this machine.

Each check returns a Check: fine or not, one line of detail, and what to do
when it isn't fine. A check that crashes is reported as "could not check",
never raised, so one broken corner can't hide the rest. Nothing here puts
anything on the clipboard, and nothing touches the network unless the
caller asks for the online version check.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    fix: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def run_checks(*, online: bool = False, cwd: Path | None = None) -> list[Check]:
    """Every check, in order. Never raises."""
    checks: list[tuple[str, Callable[[], Check]]] = [
        ("Version", lambda: check_version(online=online)),
        ("PATH", check_path),
        ("Python", check_python),
        ("Clipboard", check_clipboard),
        ("Tokenizer", check_tokenizer),
        ("git", check_git),
        ("Project", lambda: check_project(cwd or Path.cwd())),
        ("Gemini key", check_gemini_key),
        ("Terminal", check_terminal),
    ]
    results = []
    for name, check in checks:
        try:
            results.append(check())
        except Exception as exc:  # noqa: BLE001 — a broken check is reported, not raised
            results.append(Check(name, False, f"could not check ({type(exc).__name__}: {exc})"))
    return results


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------


def check_version(*, online: bool = False) -> Check:
    from contextzip import __version__, updater

    detail = f"contextzip {__version__}, installed with {updater.install_method()}"
    if not online:
        return Check("Version", True, detail)
    result = updater.check_latest_version()
    if result.latest is None:
        return Check("Version", False, f"{detail}; couldn't reach PyPI ({result.error})", "check your connection and try again")
    if result.update_available:
        binary = updater.binary_install()
        fix = binary.update_command if binary is not None else "contextzip update"
        return Check("Version", False, f"{detail}; {result.latest} is available", fix)
    return Check("Version", True, f"{detail}, the latest")


def check_path() -> Check:
    from contextzip import pathsetup, updater

    found = [name for name in ("cz", "contextzip") if shutil.which(name)]
    binary = updater.binary_install()
    if binary is not None:
        if found:
            return Check("PATH", True, f"{' and '.join(found)} found (standalone build, {binary.manager})")
        return Check("PATH", False, "this standalone build isn't on PATH", f"add {Path(sys.executable).parent} to your PATH")
    st = pathsetup.status()
    if len(found) == 2 or st.on_path:
        return Check("PATH", True, "cz and contextzip are on PATH" + (f" ({st.directory})" if st.directory else ""))
    if st.manager == "pipx":
        return Check("PATH", False, "cz isn't on PATH", "pipx ensurepath")
    if st.manager == "uv":
        return Check("PATH", False, "cz isn't on PATH", "uv tool update-shell")
    if st.in_venv:
        return Check("PATH", True, "installed in a virtual environment: cz is on PATH while it's activated")
    if st.directory is None:
        return Check("PATH", False, "couldn't find the folder cz was installed into", "python -m contextzip setup-path")
    return Check("PATH", False, f"cz is in {st.directory}, which isn't on PATH", "contextzip setup-path")


def check_python() -> Check:
    import platform

    version = platform.python_version()
    where = sys.executable or "unknown"
    if sys.version_info < (3, 10):
        return Check(
            "Python",
            False,
            f"Python {version} ({where}); the MCP server needs 3.10 or newer",
            "install Python 3.10 or newer to use contextzip mcp; everything else works",
        )
    return Check("Python", True, f"Python {version} ({where})")


def check_clipboard() -> Check:
    """Which way the ZIP would reach the clipboard here. Looks only; never copies anything."""
    import platform

    system = platform.system()
    if system == "Windows":
        return Check("Clipboard", True, "the Windows clipboard, built in")
    if system == "Darwin":
        if shutil.which("osascript"):
            return Check("Clipboard", True, "Finder, through osascript")
        return Check("Clipboard", False, "osascript isn't available, so contextzip opens the ZIP's folder instead", "run contextzip from a normal macOS user session")

    from contextzip import clipboard

    wayland = bool(os.environ.get("WAYLAND_DISPLAY"))
    x11 = bool(os.environ.get("DISPLAY"))
    if not (wayland or x11):
        return Check("Clipboard", True, "no desktop session (SSH or headless): contextzip prints the ZIP's path instead")
    if wayland and shutil.which("wl-copy"):
        return Check("Clipboard", True, "wl-copy (Wayland)")
    if x11:
        extra = ", with xclip as a fallback" if shutil.which("xclip") else ""
        return Check("Clipboard", True, f"contextzip's built-in X11 clipboard{extra}")
    return Check("Clipboard", False, "this Wayland session has no X server and no wl-copy", clipboard._install_hint())


def check_tokenizer() -> Check:
    from contextzip import tokens

    if tokens.tokenizer_available():
        return Check("Tokenizer", True, "o200k_base, exact counts, offline")
    if not tokens._vocab_path().is_file():
        why = "its vocabulary file is missing"
    else:
        try:
            import tiktoken  # noqa: F401

            why = "it couldn't be loaded"
        except Exception as exc:  # noqa: BLE001
            why = f"tiktoken can't be imported ({type(exc).__name__}: {exc})"
    return Check("Tokenizer", False, f"estimates only: {why}", "pip install --force-reinstall contextzip")


def check_git() -> Check:
    git = shutil.which("git")
    if git is None:
        return Check("git", False, "git not found; only --git-changes needs it", "install git to use --git-changes")
    try:
        out = subprocess.run([git, "--version"], capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Check("git", False, f"git is at {git} but didn't run ({exc})", "check your git install")
    return Check("git", True, out or git)


def check_project(cwd: Path) -> Check:
    from contextzip.cli_display import human_size
    from contextzip.detector import detect
    from contextzip.packager import _workspace_dir
    from contextzip.project_config import project_config_path
    from contextzip.root import find_project_root
    from contextzip.snapshots import list_snapshots

    root = find_project_root(cwd).root
    detection = detect(root)
    stacks = "no known stack" if detection.is_unknown else detection.display_name
    config = project_config_path(root)
    bits = [str(root), stacks, "config: " + (".contextzip/config.json" if config.is_file() else "none, defaults (contextzip init)")]
    try:
        workspace, _git = _workspace_dir(root)
    except (OSError, ValueError):
        workspace = root / ".contextzip"
    if workspace.is_dir():
        size = 0
        for path in workspace.rglob("*"):
            try:
                if path.is_file():
                    size += path.stat().st_size
            except OSError:
                continue
        count = len(list_snapshots(root))
        bits.append(f".contextzip/ {human_size(size)}, {count} snapshot{'' if count == 1 else 's'}")
    else:
        bits.append("nothing packaged here yet")
    return Check("Project", True, " · ".join(bits))


def check_gemini_key() -> Check:
    from contextzip.config import diagnose_api_key, get_api_key

    problem = diagnose_api_key()
    if problem:
        return Check("Gemini key", False, problem, "contextzip config --reset-key")
    if get_api_key():
        source = "GEMINI_API_KEY" if os.environ.get("GEMINI_API_KEY", "").strip() else "saved in your contextzip config"
        return Check("Gemini key", True, f"set ({source})")
    return Check("Gemini key", True, "not set; only needed for --prompt")


def check_terminal() -> Check:
    stream = sys.stdout
    encoding = getattr(stream, "encoding", None) or "unknown"
    try:
        tty = bool(stream.isatty())
    except (AttributeError, ValueError):
        tty = False
    width = shutil.get_terminal_size(fallback=(80, 24)).columns
    no_color = bool(os.environ.get("NO_COLOR"))
    detail = f"{encoding}, {'a terminal' if tty else 'not a terminal (piped)'}, {width} columns" + (", NO_COLOR set" if no_color else "")
    try:
        "✓→≈█".encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return Check("Terminal", False, f"{detail}; it can't show contextzip's symbols", "use a UTF-8 terminal, or set PYTHONIOENCODING=utf-8")
    return Check("Terminal", True, detail)
