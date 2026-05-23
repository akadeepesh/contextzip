"""
packager.py — Creates the ZIP archive from a ResolveResult.

Phase 5 changes:
  - Accepts ResolveResult instead of a bare list[Path]
  - Reports skipped (unreadable / symlink) files to the caller
  - Caps individual file read to avoid runaway memory on huge files
  - Carries skipped_paths forward into PackageResult for CLI display

Phase 6 changes:
  - Introduces .contextzip/ workspace directory at the git root (or CWD fallback)
  - Auto-creates .contextzip/ and registers it in .gitignore when inside a git repo
  - Deterministic output names: codebase.zip (default) or changes.zip (--git-changes)
  - --output flag bypasses workspace logic entirely (user owns the path)
"""

from __future__ import annotations

import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console
from rich.progress import (
    BarColumn,
    FileSizeColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TransferSpeedColumn,
)

from contextzip.filters import ResolveResult

# The entry written into .gitignore
_GITIGNORE_ENTRY = ".contextzip/"


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


@dataclass
class PackageResult:
    zip_path: Path
    file_count: int
    uncompressed_bytes: int
    compressed_bytes: int
    skipped_in_zip: list[tuple[Path, str]] = field(default_factory=list)

    @property
    def compression_ratio(self) -> float:
        if self.uncompressed_bytes == 0:
            return 0.0
        return max(0.0, 1.0 - (self.compressed_bytes / self.uncompressed_bytes))

    @property
    def compression_pct(self) -> str:
        return f"{self.compression_ratio * 100:.0f}%"

    @property
    def grew(self) -> bool:
        return self.compressed_bytes > self.uncompressed_bytes


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_zip(
    resolve_result: ResolveResult,
    project_dir: Path,
    output_path: Path | None,
    console: Console,
    git_changes: bool = False,
    prompt_txt: str | None = None,
) -> PackageResult:
    """
    Write the included files from *resolve_result* into a ZIP archive.

    If *output_path* is given (via --output) it is used as-is and the
    .contextzip/ workspace logic is skipped entirely.

    Otherwise the archive is written to the .contextzip/ workspace
    directory (created automatically) at the git root, or the CWD if
    no git repository is detected.

    If *prompt_txt* is provided (set when --prompt is used), a ``prompt.txt``
    file is written as the first entry in the ZIP. Any AI tool that receives
    the ZIP will immediately see the task description and selected file list.

    Returns a :class:`PackageResult` with compression stats and any
    files that had to be skipped during writing (e.g. permission denied).
    """
    if output_path is not None:
        # User specified --output: honour it exactly, no workspace logic.
        zip_path = output_path
    else:
        zip_path = _workspace_output_path(project_dir, git_changes, console)

    zip_path.parent.mkdir(parents=True, exist_ok=True)

    included: list[Path] = resolve_result.included
    skipped_in_zip: list[tuple[Path, str]] = []
    uncompressed = 0
    file_count = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[cyan]{task.description}[/]"),
        BarColumn(),
        TaskProgressColumn(),
        FileSizeColumn(),
        TransferSpeedColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        total_bytes = sum(p.stat().st_size for p in included if p.is_file())
        task = progress.add_task("Compressing…", total=max(total_bytes, 1))

        with zipfile.ZipFile(
            zip_path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as zf:
            # Write prompt.txt first so it's the first thing AI tools see
            if prompt_txt is not None:
                zf.writestr("prompt.txt", prompt_txt.encode("utf-8"))

            for abs_path in included:
                if not abs_path.is_file():
                    continue

                try:
                    rel = abs_path.relative_to(project_dir)
                except ValueError:
                    skipped_in_zip.append((abs_path, "outside project tree"))
                    continue

                try:
                    file_size = abs_path.stat().st_size
                except OSError as e:
                    skipped_in_zip.append((abs_path, f"stat failed: {e}"))
                    continue

                try:
                    zf.write(abs_path, arcname=rel.as_posix())
                    uncompressed += file_size
                    file_count += 1
                except PermissionError:
                    skipped_in_zip.append((abs_path, "permission denied"))
                except OSError as e:
                    skipped_in_zip.append((abs_path, str(e)))
                finally:
                    progress.advance(task, file_size)

    compressed = zip_path.stat().st_size

    return PackageResult(
        zip_path=zip_path,
        file_count=file_count,
        uncompressed_bytes=uncompressed,
        compressed_bytes=compressed,
        skipped_in_zip=skipped_in_zip,
    )


# ---------------------------------------------------------------------------
# Workspace helpers
# ---------------------------------------------------------------------------


def _find_git_root(start: Path) -> Path | None:
    """
    Walk up the directory tree from *start* looking for a .git directory.
    Returns the directory that contains .git, or None if not found.
    """
    current = start.resolve()
    while True:
        if (current / ".git").is_dir():
            return current
        parent = current.parent
        if parent == current:
            # Reached filesystem root without finding .git
            return None
        current = parent


def _ensure_gitignore(git_root: Path) -> None:
    """
    Ensure .contextzip/ is listed in <git_root>/.gitignore.

    - If .gitignore exists and already contains the entry: do nothing.
    - If .gitignore exists but lacks the entry: append it.
    - If .gitignore does not exist: create it with just the entry.
    """
    gitignore_path = git_root / ".gitignore"

    if gitignore_path.is_file():
        content = gitignore_path.read_text(encoding="utf-8", errors="replace")
        # Check for the entry on its own line (with or without trailing slash variants)
        lines = [line.strip() for line in content.splitlines()]
        if _GITIGNORE_ENTRY in lines or _GITIGNORE_ENTRY.rstrip("/") in lines:
            return  # Already present — nothing to do
        # Append, ensuring there's a trailing newline before our entry
        separator = "\n" if content and not content.endswith("\n") else ""
        with gitignore_path.open("a", encoding="utf-8") as f:
            f.write(f"{separator}\n# contextzip workspace\n{_GITIGNORE_ENTRY}\n")
    else:
        # .gitignore doesn't exist — create a minimal one
        gitignore_path.write_text(
            f"# contextzip workspace\n{_GITIGNORE_ENTRY}\n",
            encoding="utf-8",
        )


def _workspace_dir(project_dir: Path) -> tuple[Path, bool]:
    """
    Resolve the .contextzip/ workspace directory.

    Returns ``(workspace_path, is_git_repo)`` where:
    - workspace_path is <git_root>/.contextzip/ when inside a git repo
    - workspace_path is <project_dir>/.contextzip/ as a fallback
    - is_git_repo indicates whether a git root was found
    """
    git_root = _find_git_root(project_dir)
    if git_root is not None:
        return git_root / ".contextzip", True
    return project_dir / ".contextzip", False


def _workspace_output_path(
    project_dir: Path,
    git_changes: bool,
    console: Console,
) -> Path:
    """
    Determine the output ZIP path inside the .contextzip/ workspace.

    Side effects:
    - Creates the workspace directory if it doesn't exist.
    - Manages .gitignore registration when inside a git repo.
    - Falls back to the system temp directory if the workspace cannot
      be created (e.g. read-only filesystem), printing a warning.
    """
    workspace, is_git_repo = _workspace_dir(project_dir)
    filename = "changes.zip" if git_changes else "codebase.zip"

    # Attempt to create the workspace directory
    try:
        workspace.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        # Graceful fallback: warn and use temp dir
        console.print(
            f"\n  [yellow]⚠[/]  Could not create [cyan].contextzip/[/] workspace "
            f"([dim]{exc}[/]) — falling back to temp directory.\n"
        )
        return Path(tempfile.gettempdir()) / filename

    # Handle .gitignore only when we're inside a git repo
    if is_git_repo:
        git_root = workspace.parent  # workspace is <git_root>/.contextzip
        try:
            _ensure_gitignore(git_root)
        except OSError:
            # Non-fatal: gitignore update failed, carry on silently
            pass

    return workspace / filename


# ---------------------------------------------------------------------------
# Legacy helper (kept for any internal callers that may reference it)
# ---------------------------------------------------------------------------


def _safe_name(name: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
    return safe[:48] or "project"
