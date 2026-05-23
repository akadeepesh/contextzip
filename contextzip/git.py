"""
git.py — Detects git-modified files in a project directory.

Handles every edge case:
  - Not a git repository
  - Git not installed / not on PATH
  - No changes at all (clean working tree)
  - Deleted files (skipped — can't zip what doesn't exist)
  - Renamed files (new name included, old name skipped)
  - Both staged and unstaged changes collected
  - Untracked files included (new files not yet committed)
  - Submodule entries skipped
  - Files outside the project tree skipped
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


# ---------------------------------------------------------------------------
# Status codes from `git status --porcelain`
# ---------------------------------------------------------------------------
#
# Format is two characters: XY where X = staged, Y = unstaged
#
#   M  = modified
#   A  = added / new file staged
#   D  = deleted
#   R  = renamed   (line format: "R  old -> new")
#   C  = copied
#   U  = updated but unmerged
#   ?  = untracked (both X and Y will be '?')
#   !  = ignored   (both X and Y will be '!')
#
# We include: M, A, R, C, U, ?
# We skip:    D (deleted — file doesn't exist), ! (ignored)

_INCLUDE_CODES = {"M", "A", "R", "C", "U", "?"}


# ---------------------------------------------------------------------------
# Result / error models
# ---------------------------------------------------------------------------


class GitErrorKind(Enum):
    NOT_A_REPO = "not_a_repo"
    GIT_NOT_FOUND = "git_not_found"
    COMMAND_ERROR = "command_error"


@dataclass
class GitError:
    kind: GitErrorKind
    message: str  # human-readable, ready to print


@dataclass
class GitChanges:
    """Successful result from :func:`get_changed_files`."""

    files: list[Path] = field(default_factory=list)  # absolute paths
    staged: list[str] = field(default_factory=list)  # rel paths (display)
    unstaged: list[str] = field(default_factory=list)  # rel paths (display)
    untracked: list[str] = field(default_factory=list)  # rel paths (display)
    deleted: list[str] = field(default_factory=list)  # rel paths (skipped)
    submodules: list[str] = field(default_factory=list)  # rel paths (skipped)

    @property
    def is_empty(self) -> bool:
        return not self.files


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_changed_files(project_dir: Path) -> GitChanges | GitError:
    """
    Run ``git status --porcelain`` in *project_dir* and return a
    :class:`GitChanges` with all modified/added/untracked files,
    or a :class:`GitError` describing what went wrong.
    """

    # ── Guard: git must be on PATH ───────────────────────────────────────────
    if not shutil.which("git"):
        return GitError(
            kind=GitErrorKind.GIT_NOT_FOUND,
            message=(
                "git is not installed or not on your PATH.\n"
                "Install git from https://git-scm.com and try again."
            ),
        )

    # ── Guard: must be inside a git repository ───────────────────────────────
    try:
        check = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=8,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return GitError(
            kind=GitErrorKind.GIT_NOT_FOUND,
            message=f"Could not run git: {e}",
        )

    if check.returncode != 0:
        return GitError(
            kind=GitErrorKind.NOT_A_REPO,
            message=(
                f"'{project_dir}' is not inside a git repository.\n"
                "Run 'git init' first, or use contextzip without --git-changes."
            ),
        )

    # ── Get the repo root (may differ from project_dir in a monorepo) ────────
    root_proc = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=project_dir,
        capture_output=True,
        text=True,
        timeout=8,
    )
    repo_root = (
        Path(root_proc.stdout.strip()) if root_proc.returncode == 0 else project_dir
    )

    # ── Run git status --porcelain ───────────────────────────────────────────
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain", "-u", "--no-renames"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        return GitError(
            kind=GitErrorKind.COMMAND_ERROR,
            message="git status timed out — repository may be very large.",
        )

    if proc.returncode != 0:
        stderr = proc.stderr.strip()
        return GitError(
            kind=GitErrorKind.COMMAND_ERROR,
            message=f"git status failed: {stderr or 'unknown error'}",
        )

    # ── Parse the output ─────────────────────────────────────────────────────
    return _parse_porcelain(proc.stdout, repo_root, project_dir)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def _parse_porcelain(
    output: str,
    repo_root: Path,
    project_dir: Path,
) -> GitChanges:
    """
    Parse ``git status --porcelain`` output into a :class:`GitChanges`.

    Each line is ``XY filename`` where X=staged status, Y=unstaged status.
    With ``--no-renames`` renames appear as D + A pairs, so we handle them
    naturally without needing to parse the "old -> new" arrow format.
    """
    result = GitChanges()

    for raw_line in output.splitlines():
        if not raw_line or len(raw_line) < 4:
            continue

        xy = raw_line[:2]  # e.g. "M ", " M", "??", "A "
        rel_path = raw_line[3:].strip()  # path relative to repo root

        # Strip surrounding quotes git adds for paths with spaces/special chars
        if rel_path.startswith('"') and rel_path.endswith('"'):
            rel_path = rel_path[1:-1]

        x = xy[0]  # staged status
        y = xy[1]  # unstaged status

        # Skip ignored files
        if x == "!" and y == "!":
            continue

        abs_path = (repo_root / rel_path).resolve()

        # ── Submodule detection — git marks them as commits not files ─────────
        if _is_submodule(abs_path):
            result.submodules.append(rel_path)
            continue

        # ── Deleted files — can't zip them ───────────────────────────────────
        if x == "D" or y == "D":
            result.deleted.append(rel_path)
            continue

        # ── Skip if not a regular file (directories, sockets, etc.) ──────────
        if not abs_path.is_file():
            continue

        # ── Skip if outside the project_dir we're operating on ───────────────
        try:
            abs_path.relative_to(project_dir)
        except ValueError:
            continue

        # ── Categorise for display ────────────────────────────────────────────
        if x == "?" and y == "?":
            result.untracked.append(rel_path)
        elif x != " " and x != "?":
            result.staged.append(rel_path)
        else:
            result.unstaged.append(rel_path)

        # Deduplicate — a file can appear staged and unstaged simultaneously
        if abs_path not in result.files:
            result.files.append(abs_path)

    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_submodule(path: Path) -> bool:
    """
    A submodule shows up as a directory in the working tree but as a
    commit object in git. We detect it by checking if the path is a
    directory containing a .git file/dir, which is how git embeds submodules.
    """
    if not path.is_dir():
        return False
    git_marker = path / ".git"
    return git_marker.exists()
