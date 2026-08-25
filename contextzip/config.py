"""
config.py — Persistent configuration storage for contextzip.

Stores user settings (e.g. Gemini API key) in the platform-appropriate
config directory:

  Linux/macOS : ~/.config/contextzip/config.json
  Windows     : C:\\Users\\<user>\\AppData\\Roaming\\contextzip\\config.json

This follows the XDG standard and mirrors what tools like gh, gcloud,
and aws configure use. Never uses temp — config persists across sessions.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


# ---------------------------------------------------------------------------
# Config directory resolution (no platformdirs dependency)
# ---------------------------------------------------------------------------


def _config_dir() -> Path:
    """
    Return the platform-appropriate config directory for contextzip.

    Mirrors what platformdirs.user_config_dir("contextzip") would return,
    but without the extra dependency.
    """
    if os.name == "nt":
        # Windows: %APPDATA%\\contextzip
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        # Linux / macOS: $XDG_CONFIG_HOME/contextzip or ~/.config/contextzip
        xdg = os.environ.get("XDG_CONFIG_HOME", "")
        base = Path(xdg) if xdg else Path.home() / ".config"

    return base / "contextzip"


_CONFIG_FILE = _config_dir() / "config.json"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_api_key() -> str | None:
    """
    Return the stored Gemini API key, or None if not configured.

    Checks in order:
      1. GEMINI_API_KEY environment variable (must look like a real key)
      2. Persisted config file

    A key is considered valid only if it starts with "AIza" — this prevents
    a garbage or placeholder env var from bypassing the onboarding flow and
    causing a cryptic API error downstream.
    """
    env_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if env_key and env_key.startswith("AIza"):
        return env_key

    # Env var present but malformed — don't silently use it, fall through
    # to config file. If config file also has nothing, onboarding triggers.

    try:
        data = _read_config()
        key = data.get("gemini_api_key", "").strip()
        # Same validation as env var — must look like a real Gemini key
        return key if (key and key.startswith("AIza")) else None
    except Exception:
        return None


def save_api_key(key: str) -> None:
    """
    Persist *key* to the config file.

    Creates the config directory if it doesn't exist.
    Raises OSError if the directory or file cannot be written.
    """
    _CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = _read_config()
    data["gemini_api_key"] = key.strip()
    _write_config(data)


def delete_api_key() -> bool:
    """
    Remove the stored API key from config.

    Returns True if a key was present and removed, False if there was
    nothing to remove.
    """
    try:
        data = _read_config()
        if "gemini_api_key" not in data:
            return False
        del data["gemini_api_key"]
        _write_config(data)
        return True
    except Exception:
        return False


def diagnose_api_key() -> str | None:
    """
    Return a human-readable explanation if a key exists but is invalid,
    or None if everything is fine (or nothing is configured at all).

    Used by the CLI to surface "you have a bad key" before onboarding.
    """
    env_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if env_key and not env_key.startswith("AIza"):
        return (
            f"GEMINI_API_KEY is set in your environment but doesn't look like "
            f"a valid Gemini key (got: {env_key[:12]}…). "
            f"Unset it or replace it with a key from https://aistudio.google.com/apikey"
        )
    try:
        data = _read_config()
        key = data.get("gemini_api_key", "").strip()
        if key and not key.startswith("AIza"):
            return (
                f"Saved API key in {_CONFIG_FILE} doesn't look valid. "
                f"Run [cyan]contextzip config --reset-key[/] to replace it."
            )
    except Exception:
        pass
    return None


def config_path() -> Path:
    """Return the path to the config file (may or may not exist yet)."""
    return _CONFIG_FILE


# ---------------------------------------------------------------------------
# Personal workspace location preference
#
# Where the .contextzip/ workspace (output zips + eod/handoff-era markers,
# now just output zips) gets created, when nothing more specific overrides
# it. This is a *personal*, per-machine preference — for a setting the whole
# team should share, use the project-level config instead (project_config.py,
# a .contextzip/config.json file meant to be committed to the repo).
#
# Resolution order (highest wins), enforced by packager.py, not here:
#   CLI flag > CONTEXTZIP_WORKSPACE_LOCATION env var > project config
#   > this personal config > built-in default ("git-root")
#
# Accepted values: "git-root", "cwd", or an explicit path (absolute, or
# relative to the git root / project dir).
# ---------------------------------------------------------------------------


def get_workspace_location() -> str | None:
    """Return the personally configured workspace location, or None if unset."""
    try:
        data = _read_config()
        value = data.get("workspace_location", "").strip()
        return value or None
    except Exception:
        return None


def save_workspace_location(value: str) -> None:
    """Persist *value* ("git-root" | "cwd" | a path) as the personal default."""
    _CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = _read_config()
    data["workspace_location"] = value.strip()
    _write_config(data)


def delete_workspace_location() -> bool:
    """Remove the personal workspace location override, reverting to default/project config."""
    try:
        data = _read_config()
        if "workspace_location" not in data:
            return False
        del data["workspace_location"]
        _write_config(data)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# "Don't offer the local config UI again" preference
#
# Set the first time a user declines the first-run offer to launch
# `contextzip config --ui` (see cli.py's _maybe_offer_config_ui), so the
# prompt genuinely asks only once per machine rather than nagging on every
# run of every config-less project. Running `contextzip config --ui`
# explicitly is never affected by this — it's just the unsolicited offer.
# ---------------------------------------------------------------------------


def get_config_ui_dismissed() -> bool:
    """True if the user has previously declined the first-run config UI offer."""
    try:
        return bool(_read_config().get("config_ui_prompt_dismissed", False))
    except Exception:
        return False


def save_config_ui_dismissed() -> None:
    """Record that the user declined the first-run config UI offer."""
    _CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = _read_config()
    data["config_ui_prompt_dismissed"] = True
    _write_config(data)


def _write_config(data: dict) -> None:
    """
    Write *data* to the config file and lock its permissions down to the
    owner only (0600) on POSIX systems.

    The file may hold a Gemini API key in plaintext, so it shouldn't be
    left group/world-readable — other local accounts on a shared machine
    could otherwise read it straight off disk. Applied on every write
    (not just creation) since some platforms reset perms on rewrite, and
    a pre-existing file from an older contextzip version may still have
    the old default (umask-dependent) permissions.
    """
    _CONFIG_FILE.write_text(
        json.dumps(data, indent=2) + "\n",
        encoding="utf-8",
    )
    if os.name != "nt":
        try:
            os.chmod(_CONFIG_FILE, 0o600)
        except OSError:
            pass  # best-effort — never block a save over a chmod failure


def _read_config() -> dict:
    """Read and parse the config file. Returns {} if missing or malformed."""
    if not _CONFIG_FILE.is_file():
        return {}
    try:
        return json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
