"""
brief.py — Orchestrates `contextzip eod` and `contextzip handoff`.

Both commands share one pipeline:
  1. Locate the latest conversation export
  2. Best-effort fetch Claude's artifact files if missing (for case 2)
  3. Resolve code changes (the three-case decision tree in code_changes.py)
  4. Render the prompt — conversation inlined or attached depending on size,
     code changes attached as a zip when there are any
  5. Write the prompt text (+ zip) to .contextzip/, advance the marker

`eod` and `handoff` differ only in the fixed instruction text and which
marker they read/write — everything else is identical, so this module
takes a `kind` parameter rather than having two near-duplicate pipelines.

contextzip does no AI calls here. This produces a prompt for a human to
paste into Claude or ChatGPT; the actual summarizing/continuing is the
receiving chat's job, not contextzip's.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from contextzip.claude_artifacts import fetch_artifacts
from contextzip.claude_export import (
    DEFAULT_EXPORTS_DIR,
    ConversationExport,
    NoExportFoundError,
    artifacts_dir_for,
    find_latest_export,
    should_inline,
)
from contextzip.code_changes import CodeChangesResult, resolve_code_changes
from contextzip.config import get_session_key
from contextzip.git import GitError
from contextzip.markers import save_marker
from contextzip.packager import _ensure_gitignore, _workspace_dir

EOD_INSTRUCTION = "Create the work log table."

HANDOFF_INTRO = (
    "I've hit my usage limit in a previous chat and need to continue this "
    "project in a new one."
)
HANDOFF_REVIEW_INLINE = "Here's the conversation so far — please review it and continue from where we left off."
HANDOFF_REVIEW_ATTACHED = (
    "I've attached the prior conversation export ({filename}) — please "
    "review it and continue from where we left off."
)


class BriefError(Exception):
    """Raised for hard failures — no export found, etc."""


@dataclass
class BriefResult:
    kind: str  # "eod" | "handoff"
    prompt_text: str
    prompt_path: Path
    changes_zip_path: Path | None
    export: ConversationExport
    conversation_inlined: bool
    code_changes: CodeChangesResult | None
    fetch_warnings: list[str] = field(default_factory=list)


def run_brief(
    *,
    kind: str,
    project_dir: Path,
    exports_dir: Path | None = None,
    auto_fetch_artifacts: bool = True,
    dry_run: bool = False,
) -> BriefResult:
    if exports_dir is None:
        exports_dir = project_dir / DEFAULT_EXPORTS_DIR

    _ensure_exports_ignored(exports_dir, project_dir)

    try:
        export = find_latest_export(exports_dir)
    except NoExportFoundError as exc:
        raise BriefError(str(exc))

    fetch_warnings: list[str] = []
    claude_dir = artifacts_dir_for(export, exports_dir)

    if auto_fetch_artifacts and not _has_any_file(claude_dir):
        session_key = get_session_key()
        if not session_key:
            fetch_warnings.append(
                "No Claude session key configured — skipping the diverged-from-Claude "
                "check (case 2). Run `contextzip config --set-session-key` to enable it."
            )
        else:
            _saved, errors = fetch_artifacts(export.text, claude_dir, session_key)
            fetch_warnings.extend(errors)

    code_changes = resolve_code_changes(
        project_dir=project_dir,
        kind=kind,
        claude_artifacts_dir=claude_dir if _has_any_file(claude_dir) else None,
    )
    if isinstance(code_changes, GitError):
        fetch_warnings.append(
            f"Code changes skipped — {code_changes.message.splitlines()[0]}"
        )
        code_changes = None
    elif code_changes.warnings:
        fetch_warnings.extend(code_changes.warnings)

    inline = should_inline(export)

    changes_zip_path: Path | None = None
    if code_changes and not code_changes.is_empty:
        changes_zip_path = _build_changes_zip(code_changes, project_dir, kind)

    prompt_text = _render_prompt(
        kind=kind,
        export=export,
        inline=inline,
        changes_zip_path=changes_zip_path,
    )

    prompt_path = _write_prompt(prompt_text, project_dir, kind)

    if not dry_run:
        from contextzip.git import get_commit_hash, get_current_branch

        branch = get_current_branch(project_dir) or "HEAD"
        head = get_commit_hash("HEAD", project_dir)
        if head:
            save_marker(kind, branch, head, project_dir)

    return BriefResult(
        kind=kind,
        prompt_text=prompt_text,
        prompt_path=prompt_path,
        changes_zip_path=changes_zip_path,
        export=export,
        conversation_inlined=inline,
        code_changes=code_changes,
        fetch_warnings=fetch_warnings,
    )


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------


def _render_prompt(
    *,
    kind: str,
    export: ConversationExport,
    inline: bool,
    changes_zip_path: Path | None,
) -> str:
    if kind == "eod":
        return _render_eod(export, inline, changes_zip_path)
    return _render_handoff(export, inline, changes_zip_path)


def _render_eod(
    export: ConversationExport, inline: bool, changes_zip_path: Path | None
) -> str:
    parts: list[str] = []

    if inline:
        parts.append(f"Conversation:\n{export.text}")
    else:
        parts.append(f"Conversation attached: {export.path.name}")

    if changes_zip_path is not None:
        parts.append(f"Code changes attached: {changes_zip_path.name}")

    parts.append(EOD_INSTRUCTION)

    return "\n\n".join(parts)


def _render_handoff(
    export: ConversationExport, inline: bool, changes_zip_path: Path | None
) -> str:
    parts: list[str] = [HANDOFF_INTRO]

    if inline:
        parts.append(f"{HANDOFF_REVIEW_INLINE}\n\n{export.text}")
    else:
        parts.append(HANDOFF_REVIEW_ATTACHED.format(filename=export.path.name))

    if changes_zip_path is not None:
        parts.append(
            f"I've also attached the code changes since our last session "
            f"({changes_zip_path.name}) so you have the current state without "
            f"needing the full codebase."
        )

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Output writing
# ---------------------------------------------------------------------------


def _write_prompt(prompt_text: str, project_dir: Path, kind: str) -> Path:
    workspace, is_git_repo = _workspace_dir(project_dir)
    workspace.mkdir(parents=True, exist_ok=True)

    if is_git_repo:
        try:
            _ensure_gitignore(workspace.parent)
        except OSError:
            pass

    # eod is once-a-day (overwritable, like codebase.zip); handoff can fire
    # multiple times in a day (you can hit the limit more than once), so it
    # gets a timestamp instead of just a date.
    if kind == "eod":
        stamp = datetime.now().strftime("%Y-%m-%d")
    else:
        stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")

    path = workspace / f"{kind}-{stamp}.txt"
    path.write_text(prompt_text, encoding="utf-8")
    return path


def _build_changes_zip(
    code_changes: CodeChangesResult, project_dir: Path, kind: str
) -> Path:
    workspace, _ = _workspace_dir(project_dir)
    workspace.mkdir(parents=True, exist_ok=True)
    zip_path = workspace / f"{kind}-changes.zip"

    with zipfile.ZipFile(
        zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
    ) as zf:
        for f in code_changes.files:
            if f.content is not None:
                zf.writestr(f"full/{f.rel_path}", f.content.encode("utf-8"))
            if f.diff is not None:
                zf.writestr(f"diffs/{f.rel_path}.patch", f.diff.encode("utf-8"))

    return zip_path


def _has_any_file(directory: Path) -> bool:
    return directory.is_dir() and any(p.is_file() for p in directory.rglob("*"))


def _ensure_exports_ignored(exports_dir: Path, project_dir: Path) -> None:
    """
    Register exports/ in .gitignore the first time it's used, same treatment
    as .contextzip/. Conversation exports and fetched Claude artifacts are
    personal working files, not something to accidentally commit — though
    a team that *wants* to share them can simply remove the line.
    """
    from contextzip.packager import _find_git_root

    git_root = _find_git_root(project_dir)
    if git_root is None:
        return

    try:
        rel = exports_dir.resolve().relative_to(git_root)
    except ValueError:
        return  # exports_dir lives outside the repo — nothing to register

    entry = rel.as_posix().rstrip("/") + "/"
    try:
        _ensure_gitignore(git_root, entry)
    except OSError:
        pass
