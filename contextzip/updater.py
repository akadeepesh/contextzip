"""
updater.py — Self-update support for `contextzip update` / `cz update`.

contextzip cuts frequent releases, so re-running
`pip install --upgrade contextzip` by hand every time gets old fast.
This module:

  1. Asks PyPI's JSON API for the latest published version.
  2. Compares it against the version actually running (contextzip.__version__).
  3. If newer, re-invokes pip (or pipx, if that's how contextzip was
     installed) to upgrade in place.

Never raises on network failure — a broken connection or PyPI hiccup
should degrade to "couldn't check right now", not crash the CLI.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass

from contextzip import __version__ as installed_version

PYPI_URL = "https://pypi.org/pypi/contextzip/json"
_TIMEOUT_SECS = 5


# ---------------------------------------------------------------------------
# Version comparison (no `packaging` dependency)
# ---------------------------------------------------------------------------


def _parse_version(v: str) -> tuple[int, ...]:
    """
    Turn "1.2.3" into (1, 2, 3) for comparison. Ignores any pre-release /
    build suffix after the numeric core (e.g. "1.2.3rc1" -> (1, 2, 3)) —
    good enough for "is there a newer release" without pulling in the
    `packaging` library just for this.
    """
    core = v.strip().lstrip("v")
    parts: list[int] = []
    for chunk in core.split("."):
        digits = ""
        for ch in chunk:
            if ch.isdigit():
                digits += ch
            else:
                break
        parts.append(int(digits) if digits else 0)
    return tuple(parts) if parts else (0,)


def is_newer(latest: str, current: str) -> bool:
    """True if *latest* is a strictly newer version than *current*."""
    a, b = _parse_version(latest), _parse_version(current)
    length = max(len(a), len(b))
    a = a + (0,) * (length - len(a))
    b = b + (0,) * (length - len(b))
    return a > b


# ---------------------------------------------------------------------------
# PyPI lookup
# ---------------------------------------------------------------------------


@dataclass
class VersionCheck:
    current: str
    latest: str | None  # None if the lookup failed
    error: str | None = None

    @property
    def update_available(self) -> bool:
        return bool(self.latest) and is_newer(self.latest, self.current)


def check_latest_version(timeout: float = _TIMEOUT_SECS) -> VersionCheck:
    """
    Query PyPI for the latest published contextzip version.

    Uses httpx (already a hard dependency) but never lets a network
    problem escape as an exception — callers always get a VersionCheck
    back, with `.error` set on failure.
    """
    try:
        import httpx

        resp = httpx.get(PYPI_URL, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        latest = data.get("info", {}).get("version")
        if not latest:
            return VersionCheck(
                current=installed_version, latest=None, error="Malformed PyPI response"
            )
        return VersionCheck(current=installed_version, latest=latest)
    except Exception as exc:  # noqa: BLE001 — any network/parsing failure
        return VersionCheck(
            current=installed_version, latest=None, error=f"{type(exc).__name__}: {exc}"
        )


# ---------------------------------------------------------------------------
# Install-method detection
# ---------------------------------------------------------------------------


def _installed_via_pipx() -> bool:
    """
    Best-effort guess at whether this contextzip was installed with pipx
    rather than plain pip — pipx installs live under a `pipx/venvs/...`
    path, which shows up in the interpreter's own executable path.
    """
    exe = sys.executable.replace("\\", "/").lower()
    return "pipx" in exe


def _installed_via_uv_tool() -> bool:
    exe = sys.executable.replace("\\", "/").lower()
    return "/uv/tools/" in exe or "\\uv\\tools\\" in sys.executable.lower()


@dataclass
class UpdateResult:
    ok: bool
    method: str
    output: str


def run_update(timeout: float = 120) -> UpdateResult:
    """
    Upgrade the installed contextzip package to the latest PyPI release.

    Picks the tool that matches how contextzip was installed:
      - pipx, if this interpreter lives inside a pipx venv and the pipx
        executable is on PATH
      - uv tool, if this interpreter lives inside a uv tool install
      - otherwise `python -m pip install --upgrade contextzip`, using
        the *currently running* interpreter's pip — never a bare "pip"
        off PATH, which could silently target the wrong Python.
    """
    if _installed_via_pipx() and shutil.which("pipx"):
        cmd = ["pipx", "upgrade", "contextzip"]
        method = "pipx"
    elif _installed_via_uv_tool() and shutil.which("uv"):
        cmd = ["uv", "tool", "upgrade", "contextzip"]
        method = "uv tool"
    else:
        cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "contextzip"]
        method = "pip"

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        return UpdateResult(ok=proc.returncode == 0, method=method, output=output.strip())
    except Exception as exc:  # noqa: BLE001
        return UpdateResult(ok=False, method=method, output=f"{type(exc).__name__}: {exc}")
