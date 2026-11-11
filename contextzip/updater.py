"""
updater.py — Self-update support for `contextzip update` / `cz update`.

contextzip cuts frequent releases, so re-running
`pip install --upgrade contextzip` by hand every time gets old fast.
This module:

  1. Asks PyPI's JSON API for the latest published version.
  2. Compares it against the version actually running (contextzip.__version__).
  3. If newer, works out how contextzip was installed (pip, pipx, uv tool,
     a uv-made venv) and tries each installer that could upgrade it, in
     order, until one actually lands the new version.

Never raises on network failure — a broken connection or PyPI hiccup
should degrade to "couldn't check right now", not crash the CLI.
"""

from __future__ import annotations

import importlib.util
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

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
# Install-method detection lives in install_info.py, which --version uses
# without loading this module; the names stay importable from here.
# ---------------------------------------------------------------------------

from contextzip.install_info import (  # noqa: E402,F401
    NPM_PACKAGE,
    RELEASES_URL,
    BinaryInstall,
    PythonInstall,
    _read_pyvenv_cfg,
    binary_install,
    current_install,
    install_method,
)


@dataclass(frozen=True)
class UpdateStep:
    method: str
    cmd: tuple[str, ...]

    @property
    def display(self) -> str:
        return subprocess.list2cmdline(self.cmd) if os.name == "nt" else shlex.join(self.cmd)


def update_plan(install: PythonInstall | None = None, which=shutil.which) -> list[UpdateStep]:
    """
    Every way we could upgrade this install, most likely to work first.

    Each step targets the *running* interpreter — never a bare "pip" off
    PATH, which could silently upgrade a different Python. pip-only
    steps are left out when this interpreter has no pip (uv-made venvs).
    """
    install = install or current_install()
    exe = install.executable
    pip = UpdateStep("pip", (exe, "-m", "pip", "install", "--upgrade", "contextzip"))
    uv_pip = UpdateStep("uv pip", ("uv", "pip", "install", "--python", exe, "--upgrade", "contextzip"))
    has_uv = which("uv") is not None

    steps: list[UpdateStep] = []
    if install.via_pipx:
        if which("pipx"):
            steps.append(UpdateStep("pipx", ("pipx", "upgrade", "contextzip")))
        steps.append(pip)
        if has_uv:
            steps.append(uv_pip)
    elif install.via_uv_tool:
        if has_uv:
            steps += [UpdateStep("uv tool", ("uv", "tool", "upgrade", "contextzip")), uv_pip]
        steps.append(pip)
    elif install.made_by_uv:
        if has_uv:
            steps.append(uv_pip)
        steps.append(pip)
    else:
        steps.append(pip)
        if has_uv:
            steps.append(uv_pip)

    if not install.has_pip:
        steps = [s for s in steps if s.method != "pip"]
    return steps


def manual_update_command(install: PythonInstall | None = None, which=shutil.which) -> str:
    """The single command we'd tell someone to run by hand."""
    binary = binary_install()
    if binary is not None:
        return binary.update_command
    install = install or current_install()
    plan = update_plan(install, which)
    if plan:
        return plan[0].display
    # No pip and no uv on PATH: getting uv is the shortest way back.
    return f"install uv (https://docs.astral.sh/uv/), then: uv pip install --python {install.executable} --upgrade contextzip"


@dataclass
class Attempt:
    step: UpdateStep
    ok: bool
    output: str


@dataclass
class UpdateResult:
    ok: bool
    method: str
    output: str
    attempts: list[Attempt] = field(default_factory=list)
    hint: str | None = None


_PERMISSION_MARKERS = ("permission denied", "[errno 13]", "[winerror 5]", "access is denied")


def _run(cmd: tuple[str, ...], timeout: float) -> tuple[bool, str]:
    try:
        proc = subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 — missing exe, timeout, etc.
        return False, f"{type(exc).__name__}: {exc}"
    return proc.returncode == 0, ((proc.stdout or "") + (proc.stderr or "")).strip()


def _installed_version(executable: str) -> str | None:
    """The contextzip version *executable* now sees, or None if we can't tell."""
    ok, out = _run(
        (executable, "-c", "from importlib.metadata import version; print(version('contextzip'))"),
        timeout=30,
    )
    return out.splitlines()[-1].strip() if ok and out else None


def _hint_for(attempts: list[Attempt], install: PythonInstall, has_uv: bool) -> str | None:
    text = "\n".join(a.output for a in attempts).lower()
    if "externally-managed-environment" in text or "externally managed" in text:
        return (
            "This Python is managed by your OS and refuses pip installs. Install contextzip "
            "as an isolated tool instead: `uv tool install contextzip` or `pipx install contextzip`."
        )
    if any(m in text for m in _PERMISSION_MARKERS):
        return "The install folder isn't writable. Re-run from an elevated (administrator / sudo) shell."
    if not install.has_pip and not has_uv:
        return (
            "This Python has no pip (common in venvs made by uv). Install uv from "
            "https://docs.astral.sh/uv/ or run `python -m ensurepip`, then try again."
        )
    if "still on" in text:
        return "PyPI can take a few minutes to serve a fresh release everywhere — try again shortly."
    return None


def run_update(target: str | None = None, timeout: float = 120, install: PythonInstall | None = None,
               which=shutil.which) -> UpdateResult:
    """
    Upgrade contextzip, trying each step of `update_plan` until one works.

    A step only counts as success if the running interpreter actually sees
    the new version afterwards (when *target* is given) — pip can exit 0
    after upgrading nothing, and `uv tool upgrade` exits 0 when a version
    constraint holds it back. Outside a venv, a pip permission error is
    retried once with `--user`.

    A standalone build (see `binary_install`) is never updated here; the
    result carries the command to run instead.
    """
    binary = binary_install()
    if binary is not None:
        return UpdateResult(ok=False, method=binary.manager, output=binary.update_command)

    install = install or current_install()
    steps = update_plan(install, which)
    attempts: list[Attempt] = []
    i = 0
    while i < len(steps):
        step = steps[i]
        i += 1
        ok, output = _run(step.cmd, timeout)
        if ok and target:
            now = _installed_version(install.executable)
            if now is not None and is_newer(target, now):
                ok = False
                output = f"{output}\n(finished, but contextzip is still on {now})".strip()
        attempts.append(Attempt(step, ok, output))
        if ok:
            return UpdateResult(ok=True, method=step.method, output=output, attempts=attempts)
        if (
            step.method == "pip"
            and not install.in_venv
            and "--user" not in step.cmd
            and any(m in output.lower() for m in _PERMISSION_MARKERS)
        ):
            steps.insert(i, UpdateStep("pip --user", step.cmd + ("--user",)))

    last = attempts[-1] if attempts else None
    return UpdateResult(
        ok=False,
        method=last.step.method if last else "none",
        output=last.output if last else "No pip or uv available to run the update.",
        attempts=attempts,
        hint=_hint_for(attempts, install, which("uv") is not None),
    )
