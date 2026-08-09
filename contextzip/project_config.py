"""
project_config.py — Project-level, team-shareable contextzip settings.

Loaded from a `.contextzip.json` file at the project's git root (or the
project directory itself outside a git repo) — the same location `.contextzip/`
itself resolves to by default. Unlike config.py's personal, per-machine store
(~/.config/contextzip/config.json, holds API keys, never committed), this file
is meant to be committed to the repo so every contributor gets the same
project defaults automatically — no per-machine setup required.

Currently supports:

    {
      "workspace_location": "git-root" | "cwd" | "<path>",
      "scan_depth": 2
    }

Both keys are optional. Missing or malformed values fall through to the next
tier in the resolution order (see config.py's workspace_location docstring
and packager.py's _resolve_workspace_location).

Example — a monorepo that wants its workspace at the repo root explicitly
(the default anyway, but pinned so it's obvious to contributors), committed
alongside the code:

    # .contextzip.json  (git-tracked)
    { "workspace_location": "git-root" }
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_PROJECT_CONFIG_FILENAME = ".contextzip.json"


@dataclass
class ProjectConfig:
    workspace_location: str | None = None
    scan_depth: int | None = None


def project_config_path(project_dir: Path) -> Path:
    """
    Return where a project-level config file would live for *project_dir* —
    the git root if inside a repo, otherwise project_dir itself. Does not
    imply the file exists.
    """
    from contextzip.packager import _find_git_root

    git_root = _find_git_root(project_dir)
    base = git_root if git_root is not None else project_dir
    return base / _PROJECT_CONFIG_FILENAME


def load_project_config(project_dir: Path) -> ProjectConfig:
    """
    Load `.contextzip.json` for *project_dir*, if present.

    Never raises — a missing, malformed, or unreadable file just yields an
    empty ProjectConfig, so callers can fall through to the next tier
    without special-casing errors.
    """
    path = project_config_path(project_dir)
    if not path.is_file():
        return ProjectConfig()

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ProjectConfig()

    if not isinstance(data, dict):
        return ProjectConfig()

    workspace_location = data.get("workspace_location")
    if not isinstance(workspace_location, str) or not workspace_location.strip():
        workspace_location = None

    scan_depth = data.get("scan_depth")
    if (
        not isinstance(scan_depth, int)
        or isinstance(scan_depth, bool)
        or scan_depth < 0
    ):
        scan_depth = None

    return ProjectConfig(
        workspace_location=workspace_location,
        scan_depth=scan_depth,
    )
