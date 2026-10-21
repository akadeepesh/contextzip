"""
pathsetup.py — Make `cz` / `contextzip` runnable from any terminal.

pip puts the two commands in the interpreter's Scripts (Windows) or bin
folder. That folder is often not on PATH — the Microsoft Store and
Python install manager builds, `pip install --user`, many Linux distros —
and pip's "not on PATH" warning scrolls by unread, so the first `cz`
fails with "not recognized". `python -m contextzip` always works, so
`python -m contextzip setup-path` is the way back in:

  - Windows: the folder is appended to the *user* PATH in the registry
    (HKCU\\Environment), the same value the "Environment Variables" dialog
    edits. No admin rights needed. Store-packaged Pythons can't change it
    from inside their sandbox, so they get a command to paste instead.
  - macOS / Linux: one line is appended to the shell's startup file.

pipx and uv tool installs have their own command for this (`pipx
ensurepath`, `uv tool update-shell`), and a virtual environment puts its
commands on PATH when activated, so those cases are pointed elsewhere.
"""

from __future__ import annotations

import os
import sys
import sysconfig
from dataclasses import dataclass
from pathlib import Path

from contextzip import updater

MARKER = "# added by contextzip setup-path"


@dataclass
class PathStatus:
    directory: Path | None  # folder holding the cz command, None if not found
    on_path: bool  # in this terminal's PATH
    manager: str | None = None  # "pipx" / "uv" when a tool manager owns the install
    in_venv: bool = False


def _script_name() -> str:
    return "cz.exe" if os.name == "nt" else "cz"


def _user_scheme() -> str:
    get = getattr(sysconfig, "get_preferred_scheme", None)  # 3.10+
    if get is not None:
        return get("user")
    if sys.platform == "darwin" and getattr(sys, "_framework", None):
        return "osx_framework_user"
    return f"{os.name}_user"


def _candidate_dirs() -> list[Path]:
    dirs: list[Path] = []
    # The install record lists exactly where pip wrote the script.
    try:
        from importlib.metadata import distribution

        for f in distribution("contextzip").files or []:
            if f.name.lower() == _script_name():
                dirs.append(Path(f.locate()).resolve().parent)
    except Exception:  # noqa: BLE001 — metadata is a hint, never required
        pass
    for scheme in (None, _user_scheme()):
        try:
            p = sysconfig.get_path("scripts") if scheme is None else sysconfig.get_path("scripts", scheme)
        except KeyError:
            continue
        if p:
            dirs.append(Path(p))
    seen: set[str] = set()
    unique = []
    for d in dirs:
        if _norm(d) not in seen:
            seen.add(_norm(d))
            unique.append(d)
    return unique


def scripts_dir() -> Path | None:
    """The folder holding this install's `cz` command, or None."""
    for d in _candidate_dirs():
        if (d / _script_name()).is_file():
            return d
    return None


def _norm(p) -> str:
    return os.path.normcase(os.path.normpath(os.path.expandvars(str(p).strip().strip('"'))))


def in_path_value(directory: Path, value: str, sep: str = os.pathsep) -> bool:
    """True if *directory* is one of the entries of a PATH-style *value*."""
    target = _norm(directory)
    return any(_norm(e) == target for e in value.split(sep) if e.strip())


def status() -> PathStatus:
    manager = "pipx" if updater._installed_via_pipx() else "uv" if updater._installed_via_uv_tool() else None
    directory = scripts_dir()
    return PathStatus(
        directory=directory,
        on_path=directory is not None and in_path_value(directory, os.environ.get("PATH", "")),
        manager=manager,
        in_venv=manager is None and sys.prefix != sys.base_prefix,
    )


def is_store_python() -> bool:
    """A Microsoft Store (MSIX) Python, whose registry writes stay in its sandbox."""
    exe = sys.executable.lower()
    return "\\windowsapps\\" in exe or "pythonsoftwarefoundation.python" in exe


def powershell_command(directory: Path) -> str:
    """A one-liner that adds *directory* to the user PATH, for pasting."""
    d = str(directory).replace("'", "''")
    return (
        "[Environment]::SetEnvironmentVariable('Path', "
        f"[Environment]::GetEnvironmentVariable('Path', 'User') + ';{d}', 'User')"
    )


# ---------------------------------------------------------------------------
# Where the change goes
# ---------------------------------------------------------------------------


def shell_rc(home: Path | None = None, shell: str | None = None) -> Path:
    """The startup file of the user's shell (macOS / Linux)."""
    home = home or Path.home()
    name = Path(shell if shell is not None else os.environ.get("SHELL", "")).name
    if name == "zsh":
        return Path(os.environ.get("ZDOTDIR") or home) / ".zshrc"
    if name == "bash":
        # macOS Terminal opens login shells, which read .bash_profile, not .bashrc.
        return home / (".bash_profile" if sys.platform == "darwin" else ".bashrc")
    if name == "fish":
        return home / ".config" / "fish" / "config.fish"
    return home / ".profile"


def target_label() -> str:
    """Where add_to_path() will write, in words."""
    if os.name == "nt":
        return "your user PATH"
    return str(shell_rc()).replace(str(Path.home()), "~", 1)


def add_to_path(directory: Path) -> bool:
    """
    Add *directory* to the user's PATH for new terminals.
    Returns False if it was already there. Raises OSError on failure.
    """
    if os.name == "nt":
        return _add_windows(directory)
    return add_to_rc(directory, shell_rc())


def add_to_rc(directory: Path, rc: Path) -> bool:
    existing = rc.read_text(encoding="utf-8", errors="replace") if rc.exists() else ""
    if str(directory) in existing:
        return False
    if rc.name == "config.fish":
        line = f'fish_add_path "{directory}"'
    else:
        line = f'export PATH="{directory}:$PATH"'
    rc.parent.mkdir(parents=True, exist_ok=True)
    prefix = "" if not existing or existing.endswith("\n") else "\n"
    with rc.open("a", encoding="utf-8") as fh:
        fh.write(f"{prefix}\n{MARKER}\n{line}\n")
    return True


def with_entry(current: str, directory: Path) -> str | None:
    """*current* (a Windows PATH value) with *directory* appended, or None if present."""
    if in_path_value(directory, current, ";"):
        return None
    entries = [e for e in current.split(";") if e.strip()]
    entries.append(str(directory))
    return ";".join(entries)


def _add_windows(directory: Path) -> bool:
    import ctypes
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
        try:
            current, kind = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            current, kind = "", winreg.REG_EXPAND_SZ
        new = with_entry(str(current), directory)
        if new is None:
            return False
        if kind not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
            kind = winreg.REG_EXPAND_SZ
        winreg.SetValueEx(key, "Path", 0, kind, new)

    # Tell Explorer the environment changed, so terminals opened from now on see it.
    result = ctypes.c_ulong()
    ctypes.windll.user32.SendMessageTimeoutW(
        0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000, ctypes.byref(result)
    )  # HWND_BROADCAST, WM_SETTINGCHANGE, SMTO_ABORTIFHUNG
    return True
