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
    message: str


@dataclass
class GitChanges:
    """Successful result from :func:`get_changed_files`."""

    files: list[Path] = field(default_factory=list)
    staged: list[str] = field(default_factory=list)
    unstaged: list[str] = field(default_factory=list)
    untracked: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    submodules: list[str] = field(default_factory=list)

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
        Path(root_proc.stdout.strip()).resolve()
        if root_proc.returncode == 0
        else project_dir
    )

    # ── Status of the repo itself, then every repo nested inside it ──────────
    # An embedded repo (e.g. the `git init` create-next-app runs inside
    # frontend/) or a submodule shows up in the outer repo's status as one
    # opaque entry, so the changes inside it would otherwise be lost.
    from contextzip.root import nested_git_roots

    result = GitChanges()
    nested = [r for r in nested_git_roots(repo_root) if _is_within(r, project_dir) or _is_within(project_dir, r)]
    for repo in [repo_root, *nested]:
        status = _run_status(repo)
        if isinstance(status, GitError):
            if repo == repo_root:
                return status
            # A broken nested repo shouldn't sink the whole run — report it
            # the way submodules always have been: listed, not packaged.
            result.submodules.append(_display_rel(repo, project_dir))
            continue
        _parse_porcelain_z(status, repo, project_dir, result, nested_repos=set(nested))

    return result


def _run_status(repo: Path) -> str | GitError:
    """`git status --porcelain -z` for one repository."""
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain", "-z", "-u", "--no-renames"],
            cwd=repo,
            capture_output=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return GitError(
            kind=GitErrorKind.COMMAND_ERROR,
            message="git status timed out — repository may be very large.",
        )
    except OSError as exc:
        return GitError(kind=GitErrorKind.COMMAND_ERROR, message=f"Could not run git: {exc}")

    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        return GitError(
            kind=GitErrorKind.COMMAND_ERROR,
            message=f"git status failed: {stderr or 'unknown error'}",
        )
    # -z output is raw bytes in the filesystem encoding; git never quotes or
    # escapes paths in this mode, so names with spaces or non-ASCII
    # characters come through exactly.
    return proc.stdout.decode("utf-8", errors="surrogateescape")


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _display_rel(abs_path: Path, project_dir: Path) -> str:
    try:
        return abs_path.relative_to(project_dir).as_posix()
    except ValueError:
        return abs_path.as_posix()


def _parse_porcelain_z(
    output: str,
    repo_root: Path,
    project_dir: Path,
    result: GitChanges,
    *,
    nested_repos: set[Path] | None = None,
) -> GitChanges:
    """
    Parse ``git status --porcelain -z`` output for *repo_root* into *result*.

    Each NUL-terminated record is ``XY path`` (X = staged, Y = unstaged),
    with *path* relative to *repo_root*. With ``--no-renames`` there is no
    second "from" path. Paths are reported relative to *project_dir*.
    """
    nested_repos = nested_repos or set()

    for record in output.split("\0"):
        if len(record) < 4:
            continue

        x, y = record[0], record[1]
        rel_in_repo = record[3:]

        if x == "!" and y == "!":
            continue

        abs_path = (repo_root / rel_in_repo).resolve()
        rel_path = _display_rel(abs_path, project_dir)

        # A nested repo we're already scanning on its own — skip its
        # opaque entry here so its files aren't double-counted or lost.
        if abs_path in nested_repos:
            continue

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
        if not _is_within(abs_path, project_dir):
            continue

        # A file can be reported twice: by the outer repo and by a nested
        # repo that tracks the same path. Count it once.
        if abs_path in result.files:
            continue
        result.files.append(abs_path)

        # ── Categorise for display ────────────────────────────────────────────
        if x == "?" and y == "?":
            result.untracked.append(rel_path)
        elif x != " " and x != "?":
            result.staged.append(rel_path)
        else:
            result.unstaged.append(rel_path)

    return result


def _parse_porcelain(
    output: str,
    repo_root: Path,
    project_dir: Path,
) -> GitChanges:
    """
    Parse classic newline-separated ``git status --porcelain`` output.

    Kept for callers that already have text output; contextzip itself now
    uses the NUL-separated form (see _parse_porcelain_z), which never
    quotes or escapes unusual file names.
    """
    records = []
    for line in output.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip()
        if path.startswith('"') and path.endswith('"'):
            path = path[1:-1]
        records.append(line[:3] + path)
    return _parse_porcelain_z("\0".join(records), repo_root.resolve(), project_dir, GitChanges())


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
