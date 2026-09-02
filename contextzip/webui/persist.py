"""
webui/persist.py — Writes the local config UI's choices to
.contextzip/config.json.

Reuses packager._ensure_workspace_gitignore so a UI-driven save produces
exactly the same on-disk workspace layout (entire .contextzip/ ignored,
config.json included) that a normal contextzip run would create.
"""

from __future__ import annotations

import json
from pathlib import Path

from contextzip.project_config import project_config_path


def save_config_from_ui(
    project_dir: Path,
    *,
    always_include: list[str],
    always_exclude: list[str],
    workspace_location: str | None = None,
    scan_depth: int | None = None,
    ai: dict | None = None,
    limits: dict | None = None,
    applied_zip_retention: int | None = None,
    webui: dict | None = None,
) -> Path:
    """
    Merge the config UI's choices into the project's
    .contextzip/config.json and ensure the workspace directory and its
    .gitignore exist.

    *always_include*/*always_exclude* are always written (the UI's Files
    tab always has a value for both — an empty list clears it). Every
    other parameter is optional and left untouched when None, so a caller
    that only cares about one tab's worth of settings (or an older UI
    build) can't accidentally wipe fields it doesn't know about.
    *ai*, *limits*, and *webui* are merged key-by-key into whatever's
    already there rather than replacing the whole nested object, for the
    same reason.

    Always writes to the new-format location — a legacy .contextzip.json,
    if present, is left untouched and simply superseded going forward,
    same as any other write path in contextzip. Returns the path written.
    """
    from contextzip.packager import _ensure_workspace_gitignore

    path = project_config_path(project_dir)
    workspace = path.parent

    existing: dict = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                existing = data
        except (json.JSONDecodeError, OSError):
            existing = {}

    existing["always_include"] = always_include
    existing["always_exclude"] = always_exclude

    if workspace_location is not None:
        existing["workspace_location"] = workspace_location
    if scan_depth is not None:
        existing["scan_depth"] = scan_depth
    if applied_zip_retention is not None:
        existing["applied_zip_retention"] = applied_zip_retention

    for key, incoming in (("ai", ai), ("limits", limits), ("webui", webui)):
        if incoming is None:
            continue
        merged = dict(existing.get(key) or {})
        merged.update(incoming)
        existing[key] = merged

    workspace.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")

    try:
        _ensure_workspace_gitignore(workspace)
    except OSError:
        pass

    return path
