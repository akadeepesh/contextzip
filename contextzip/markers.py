"""
markers.py — Per-branch commit checkpoints for `eod` and `handoff`.

Each command (eod / handoff) tracks, independently and per git branch, the
commit hash it last ran against. This is what lets every report contain
only what's new since *that specific command* last ran on *that specific
branch*, with no manual bookkeeping:

  - First time running a command on a given branch → no marker yet →
    code_changes.py falls back to the branch's merge-base with the default
    branch, which correctly captures everything done on the branch so far
    (whether that's one commit or several).
  - Every subsequent run on the same branch → diff against the stored
    commit, i.e. "everything since last time I ran this".

Storage: .contextzip/markers/<kind>.json at the project's git root (or the
project directory itself outside a git repo), shaped as:

    {"main": "a1b2c3...", "feature-x": "d4e5f6..."}
"""

from __future__ import annotations

import json
from pathlib import Path

from contextzip.packager import _workspace_dir


def _markers_path(project_dir: Path, kind: str) -> Path:
    workspace, _ = _workspace_dir(project_dir)
    return workspace / "markers" / f"{kind}.json"


def load_marker(kind: str, branch: str, project_dir: Path) -> str | None:
    """Return the stored commit hash for *branch* under *kind*, or None if unset."""
    path = _markers_path(project_dir, kind)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    value = data.get(branch)
    return value if isinstance(value, str) and value else None


def save_marker(kind: str, branch: str, commit: str, project_dir: Path) -> None:
    """Persist *commit* as the new checkpoint for *branch* under *kind*."""
    path = _markers_path(project_dir, kind)
    path.parent.mkdir(parents=True, exist_ok=True)

    data: dict[str, str] = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}

    data[branch] = commit
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
