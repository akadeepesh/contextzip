"""
install_info.py — how this contextzip was installed.

Used by `contextzip --version`, `doctor`, `update` and `setup-path`. Kept
apart from updater.py, with only standard-library imports, so printing the
version doesn't load the update machinery.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path


# ---------------------------------------------------------------------------
# Install-method detection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PythonInstall:
    """What we know about the interpreter that's running contextzip."""

    executable: str
    prefix: str
    in_venv: bool
    has_pip: bool

    @property
    def _exe(self) -> str:
        return self.executable.replace("\\", "/").lower()

    @property
    def via_pipx(self) -> bool:
        # pipx drops pipx_metadata.json in every venv it manages; the path
        # check covers older pipx versions.
        return (Path(self.prefix) / "pipx_metadata.json").exists() or "/pipx/" in self._exe

    @property
    def via_uv_tool(self) -> bool:
        # `uv tool install` writes uv-receipt.toml into the tool's venv.
        # Paths: ~/.local/share/uv/tools/... (Unix), %APPDATA%\uv\data\tools\... (Windows).
        return (
            (Path(self.prefix) / "uv-receipt.toml").exists()
            or "/uv/tools/" in self._exe
            or "/uv/data/tools/" in self._exe
        )

    @property
    def made_by_uv(self) -> bool:
        """A venv created by `uv venv` / `uv sync` — these have no pip in them."""
        return self.in_venv and "uv" in _read_pyvenv_cfg(self.prefix)


def _read_pyvenv_cfg(prefix: str) -> dict[str, str]:
    try:
        text = (Path(prefix) / "pyvenv.cfg").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    cfg: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            cfg[key.strip().lower()] = value.strip()
    return cfg


def current_install() -> PythonInstall:
    return PythonInstall(
        executable=sys.executable,
        prefix=sys.prefix,
        in_venv=sys.prefix != getattr(sys, "base_prefix", sys.prefix),
        has_pip=importlib.util.find_spec("pip") is not None,
    )


RELEASES_URL = "https://github.com/akadeepesh/contextzip/releases/latest"
NPM_PACKAGE = "@contextzip/cli"


@dataclass(frozen=True)
class BinaryInstall:
    """A standalone (PyInstaller) build, and how its installer updates it."""

    manager: str
    update_command: str


def binary_install(executable: str | None = None, frozen: bool | None = None) -> BinaryInstall | None:
    """
    How this standalone build was installed, or None when running from a
    normal Python install. A standalone build has no pip to call, so
    `update` and `setup-path` point to the tool that installed it instead.
    """
    if frozen is None:
        frozen = bool(getattr(sys, "frozen", False))
    if not frozen:
        return None
    exe = (executable or sys.executable).replace("\\", "/").lower()
    if "/_npx/" in exe:
        return BinaryInstall("npx", f"npx {NPM_PACKAGE}@latest")
    if "/node_modules/" in exe:
        return BinaryInstall("npm", f"npm install -g {NPM_PACKAGE}@latest")
    if "/cellar/" in exe or "/homebrew/" in exe or "/linuxbrew/" in exe:
        return BinaryInstall("Homebrew", "brew upgrade contextzip")
    if "/scoop/apps/" in exe:
        return BinaryInstall("Scoop", "scoop update contextzip")
    return BinaryInstall("a download", f"download the latest build from {RELEASES_URL}")


def install_method(install: PythonInstall | None = None) -> str:
    """How this contextzip was installed, in a word or two: pip, pipx, uv tool, uv, or a standalone build."""
    binary = binary_install()
    if binary is not None:
        return f"standalone build ({binary.manager})"
    install = install or current_install()
    if install.via_pipx:
        return "pipx"
    if install.via_uv_tool:
        return "uv tool"
    if install.made_by_uv:
        return "uv"
    return "pip"
