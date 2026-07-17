"""
code_changes.py — Resolves "what code changed" for `eod` / `handoff` using
three baselines, checked in priority order per file:

  1. UNPUSHED          — file differs from the pushed/upstream branch
                          (covers both committed-but-unpushed commits and
                          uncommitted edits, in one comparison)
  2. DIVERGED_FROM_CLAUDE — file is pushed/in sync, but differs from the
                          version Claude last produced (matched by filename
                          against a local folder of fetched/saved artifacts)
  3. SINCE_MARKER       — neither of the above; diff against the eod/handoff
                          marker commit (or the branch's merge-base with the
                          default branch, on a branch never tracked before)

Every file in cases 1-3 gets BOTH a diff (against whichever baseline applied)
and the complete current file content. Brand-new untracked files have no
baseline to diff against at all, so they get content only.

contextzip's base safety rules (no .env, no secrets, no binaries-as-text)
are applied here too — being "relevant to today's report" never overrides
"this shouldn't leave the machine".
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from contextzip.filters import build_spec
from contextzip.git import (
    GitError,
    changed_files_against,
    diff_against,
    get_changed_files,
    get_commit_hash,
    get_current_branch,
    get_default_branch,
    get_merge_base,
    get_upstream_branch,
    is_ancestor,
)
from contextzip.markers import load_marker

_BINARY_PEEK = 512


class ChangeCase(Enum):
    UNPUSHED = "unpushed"
    DIVERGED_FROM_CLAUDE = "diverged_from_claude"
    SINCE_MARKER = "since_marker"
    NEW_FILE = "new_file"


@dataclass
class ChangedFile:
    path: Path  # absolute path in the working tree
    rel_path: str
    case: ChangeCase
    diff: str | None  # unified diff text against the case's baseline; None for NEW_FILE
    content: str | None  # complete current content; None if unreadable/binary
    is_binary: bool = False


@dataclass
class CodeChangesResult:
    files: list[ChangedFile] = field(default_factory=list)
    branch: str = ""
    baseline_commit: str | None = None  # the case-3 baseline actually used
    warnings: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.files


def resolve_code_changes(
    *,
    project_dir: Path,
    kind: str,  # "eod" or "handoff" — which marker to read
    claude_artifacts_dir: Path | None = None,
    default_branch_override: str | None = None,
) -> CodeChangesResult | GitError:
    """
    Build a :class:`CodeChangesResult` for the current working tree.

    Returns a :class:`GitError` (not raised) if the project isn't a usable
    git repository at all — callers decide whether that's fatal or just
    means "skip the code-changes section of the prompt".
    """
    git_changes = get_changed_files(project_dir)
    if isinstance(git_changes, GitError):
        return git_changes

    result = CodeChangesResult()

    branch = get_current_branch(project_dir) or "HEAD"
    result.branch = branch

    # ── Resolve the case-3 baseline (marker, or merge-base on first run) ────
    marker = load_marker(kind, branch, project_dir)
    if marker and is_ancestor(marker, "HEAD", project_dir):
        baseline_commit = marker
    else:
        if marker:
            result.warnings.append(
                f"Stored {kind} marker for '{branch}' is no longer reachable from "
                "HEAD (likely a rebase) — recomputed from the branch's divergence point."
            )
        default_branch = get_default_branch(project_dir, default_branch_override)
        baseline_commit = get_merge_base(branch, default_branch, project_dir)
        if baseline_commit is None:
            # Unrelated histories, or default_branch couldn't be resolved at all —
            # last resort: HEAD itself, which collapses to "just today's
            # uncommitted changes", the same safe behaviour as a brand-new repo.
            baseline_commit = get_commit_hash("HEAD", project_dir)

    result.baseline_commit = baseline_commit

    # ── Resolve case-1 baseline (upstream) once, if one exists ─────────────
    upstream = get_upstream_branch(project_dir)
    unpushed_files: set[str] = set()
    if upstream:
        unpushed_files = set(changed_files_against(upstream, project_dir) or [])

    # ── Universe of tracked files in scope ──────────────────────────────────
    since_baseline = set(changed_files_against(baseline_commit, project_dir) or [])
    dirty_now = set(git_changes.staged) | set(git_changes.unstaged)
    tracked_in_scope = since_baseline | dirty_now

    base_spec = build_spec(rule_modules=["base"])

    ambiguous_reference_warned: set[str] = set()

    for rel_path in sorted(tracked_in_scope):
        if base_spec.match_file(rel_path):
            continue  # .env, secrets, etc. — never included regardless of relevance

        abs_path = project_dir / rel_path
        if not abs_path.is_file():
            continue  # deleted since — nothing to diff or attach

        content, is_binary = _read_text(abs_path)

        if rel_path in unpushed_files:
            diff = diff_against(upstream, project_dir, paths=[rel_path])
            case = ChangeCase.UNPUSHED
        elif claude_artifacts_dir is not None:
            ref_path, extras = _find_claude_reference(rel_path, claude_artifacts_dir)
            if extras and rel_path not in ambiguous_reference_warned:
                ambiguous_reference_warned.add(rel_path)
                result.warnings.append(
                    f"Multiple Claude-provided files named '{Path(rel_path).name}' found — "
                    f"used the most recent ({ref_path}); ignored {len(extras)} other match(es)."
                )
            if ref_path is not None:
                claude_content, claude_is_binary = _read_text(ref_path)
                if not claude_is_binary and not is_binary and claude_content != content:
                    diff = _text_diff(
                        claude_content, content or "", f"claude/{rel_path}", rel_path
                    )
                    case = ChangeCase.DIVERGED_FROM_CLAUDE
                else:
                    diff = diff_against(baseline_commit, project_dir, paths=[rel_path])
                    case = ChangeCase.SINCE_MARKER
            else:
                diff = diff_against(baseline_commit, project_dir, paths=[rel_path])
                case = ChangeCase.SINCE_MARKER
        else:
            diff = diff_against(baseline_commit, project_dir, paths=[rel_path])
            case = ChangeCase.SINCE_MARKER

        result.files.append(
            ChangedFile(
                path=abs_path,
                rel_path=rel_path,
                case=case,
                diff=diff,
                content=content,
                is_binary=is_binary,
            )
        )

    # ── Untracked files — no baseline exists, content only ─────────────────
    for rel_path in sorted(git_changes.untracked):
        if base_spec.match_file(rel_path):
            continue

        abs_path = project_dir / rel_path
        if not abs_path.is_file():
            continue

        content, is_binary = _read_text(abs_path)
        result.files.append(
            ChangedFile(
                path=abs_path,
                rel_path=rel_path,
                case=ChangeCase.NEW_FILE,
                diff=None,
                content=content,
                is_binary=is_binary,
            )
        )

    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_text(path: Path) -> tuple[str | None, bool]:
    """Return (content, is_binary). content is None when binary or unreadable."""
    try:
        with path.open("rb") as fh:
            chunk = fh.read(_BINARY_PEEK)
        if b"\x00" in chunk:
            return None, True
        return path.read_text(encoding="utf-8", errors="replace"), False
    except OSError:
        return None, False


def _text_diff(a_text: str, b_text: str, a_label: str, b_label: str) -> str:
    a_lines = a_text.splitlines(keepends=True)
    b_lines = b_text.splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(a_lines, b_lines, fromfile=a_label, tofile=b_label)
    )


def _find_claude_reference(
    rel_path: str, claude_artifacts_dir: Path
) -> tuple[Path | None, list[Path]]:
    """
    Find a file named like *rel_path*'s basename inside claude_artifacts_dir.

    Claude's artifacts come out flat (no knowledge of the repo's directory
    structure), so matching can only be done by filename. Returns
    (best_match, other_matches) — best_match is the most recently modified
    candidate when more than one file shares the name.
    """
    if not claude_artifacts_dir.is_dir():
        return None, []

    target_name = Path(rel_path).name
    matches = [p for p in claude_artifacts_dir.rglob(target_name) if p.is_file()]

    if not matches:
        return None, []
    if len(matches) == 1:
        return matches[0], []

    matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0], matches[1:]
