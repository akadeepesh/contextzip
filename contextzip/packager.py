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

Phase 7 changes:
  - Workspace restructured: generated ZIPs now live under .contextzip/output/
    instead of directly in .contextzip/, leaving room for config.json
    alongside them.
  - Ignoring is now handled by a self-contained .contextzip/.gitignore
    (ignore everything except config.json) instead of a blanket
    ".contextzip/" entry in the project's top-level .gitignore — this keeps
    config.json trackable/shareable while output/ stays untracked, and it
    keeps working even when the workspace is relocated outside the default
    git-root anchor (see project_config.py / _resolve_workspace_location).
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

# Subdirectory of the workspace where generated ZIPs are written.
_OUTPUT_DIRNAME = "output"

# Contents of .contextzip/.gitignore — ignore everything in the workspace
# except the team-shareable project config and this file itself.
_WORKSPACE_GITIGNORE_CONTENTS = (
    "# contextzip workspace\n"
    "# Everything here is a local, per-machine artifact except config.json,\n"
    "# which holds project-level contextzip preferences meant to be shared\n"
    "# with your team via Git.\n"
    "*\n"
    "!.gitignore\n"
    "!config.json\n"
)

# A previous version of contextzip added this block to the project's
# top-level .gitignore. Now that .contextzip/.gitignore handles ignoring
# on its own (and does so in a way that keeps config.json trackable), we
# clean up that older entry the first time we touch a project — see
# _migrate_legacy_root_gitignore_entry.
_LEGACY_GITIGNORE_BLOCK = "# contextzip workspace\n.contextzip/\n"


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


def create_zip_silent(
    resolve_result: ResolveResult,
    project_dir: Path,
    output_path: Path | None,
    git_changes: bool = False,
    prompt_txt: str | None = None,
) -> PackageResult:
    """
    Write the included files from *resolve_result* into a ZIP archive
    without any console/progress output. Intended for programmatic use.

    Identical to :func:`create_zip` except it produces no Rich output —
    safe to call in scripts, background threads, or anywhere a TTY isn't
    available.
    """
    if output_path is not None:
        zip_path = output_path
    else:
        zip_path = _workspace_output_path_silent(project_dir, git_changes)

    zip_path.parent.mkdir(parents=True, exist_ok=True)

    included: list[Path] = resolve_result.included
    skipped_in_zip: list[tuple[Path, str]] = []
    uncompressed = 0
    file_count = 0

    with zipfile.ZipFile(
        zip_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
    ) as zf:
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

    compressed = zip_path.stat().st_size

    return PackageResult(
        zip_path=zip_path,
        file_count=file_count,
        uncompressed_bytes=uncompressed,
        compressed_bytes=compressed,
        skipped_in_zip=skipped_in_zip,
    )


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


def _ensure_workspace_gitignore(workspace: Path) -> None:
    """
    Ensure <workspace>/.gitignore exists with the standard contents that
    ignore everything in the workspace except config.json (and the
    .gitignore file itself).

    This is self-contained: it works no matter where *workspace* ends up
    living (git-root default, cwd, or a custom relocated path), since a
    nested .gitignore applies to its own directory regardless of where
    that directory sits in the tree — unlike a single blanket entry in a
    distant top-level .gitignore, which can't be relied on to reach a
    relocated workspace and (if it ignores the whole directory rather than
    its contents) would prevent config.json from ever being trackable.

    Idempotent — leaves an existing, already-correct file untouched, and
    only rewrites files that don't yet match (e.g. hand-edited or from an
    older contextzip version).
    """
    gitignore_path = workspace / ".gitignore"

    if gitignore_path.is_file():
        try:
            current = gitignore_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        if current == _WORKSPACE_GITIGNORE_CONTENTS:
            return

    gitignore_path.write_text(_WORKSPACE_GITIGNORE_CONTENTS, encoding="utf-8")


def _migrate_legacy_root_gitignore_entry(git_root: Path) -> None:
    """
    Remove the older "# contextzip workspace\\n.contextzip/\\n" block that a
    previous version of contextzip added to the project's top-level
    .gitignore, if present.

    That blanket directory-level ignore predates .contextzip/.gitignore and
    would otherwise stop config.json from ever becoming trackable (git does
    not descend into an ignored directory to apply nested un-ignore rules).
    Only ever removes the exact block contextzip itself wrote — never
    touches unrelated .gitignore content, and is a no-op if the block isn't
    present (e.g. it was already removed, or never added).
    """
    gitignore_path = git_root / ".gitignore"
    if not gitignore_path.is_file():
        return

    try:
        content = gitignore_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return

    if _LEGACY_GITIGNORE_BLOCK not in content:
        return

    updated = content.replace(_LEGACY_GITIGNORE_BLOCK, "")
    # Collapse any resulting run of 3+ blank lines left behind by the removal
    while "\n\n\n" in updated:
        updated = updated.replace("\n\n\n", "\n\n")

    try:
        gitignore_path.write_text(updated, encoding="utf-8")
    except OSError:
        pass


def _is_relative_to(path: Path, other: Path) -> bool:
    """
    True if *path* is inside *other*, after resolving both. Path.is_relative_to
    exists from 3.9 onward (our floor), but resolving first avoids false
    negatives from relative components or symlinks.
    """
    try:
        path.resolve().relative_to(other.resolve())
        return True
    except ValueError:
        return False


def _resolve_workspace_location(project_dir: Path) -> tuple[str, str]:
    """
    Determine where the .contextzip/ workspace should live, and where that
    decision came from (for diagnostics — see `contextzip config`).

    Precedence, highest wins:
      1. CONTEXTZIP_WORKSPACE_LOCATION env var
      2. Project config (.contextzip/config.json at git root — team-shared,
         committed; falls back to the deprecated .contextzip.json if that's
         all a project has)
      3. Personal config (~/.config/contextzip/config.json — per-machine)
      4. Built-in default: "git-root"

    Returns (location, source) where location is "git-root", "cwd", or an
    explicit path string, and source is a human-readable label for where it
    came from.
    """
    import os

    env_val = os.environ.get("CONTEXTZIP_WORKSPACE_LOCATION", "").strip()
    if env_val:
        return env_val, "CONTEXTZIP_WORKSPACE_LOCATION env var"

    from contextzip.project_config import load_project_config

    project_cfg = load_project_config(project_dir)
    if project_cfg.workspace_location:
        source_label = (
            "project config (.contextzip.json, deprecated)"
            if project_cfg.is_legacy
            else "project config (.contextzip/config.json)"
        )
        return project_cfg.workspace_location, source_label

    from contextzip.config import get_workspace_location

    personal_val = get_workspace_location()
    if personal_val:
        return personal_val, "personal config"

    return "git-root", "default"


def _workspace_dir(project_dir: Path) -> tuple[Path, bool]:
    """
    Resolve the .contextzip/ workspace directory.

    Returns ``(workspace_path, is_git_repo)`` where:
    - workspace_path depends on the resolved workspace location (see
      _resolve_workspace_location): under the git root by default, under
      project_dir for "cwd", or an explicit path for anything else.
    - is_git_repo indicates whether a git root was found (used to decide
      whether .gitignore registration applies)
    """
    git_root = _find_git_root(project_dir)
    location, _source = _resolve_workspace_location(project_dir)
    anchor = git_root if git_root is not None else project_dir

    if location == "git-root":
        workspace = anchor / ".contextzip"
    elif location == "cwd":
        workspace = project_dir / ".contextzip"
    else:
        custom = Path(location).expanduser()
        base = custom if custom.is_absolute() else (anchor / custom)
        workspace = base / ".contextzip"

    return workspace, git_root is not None


def _workspace_output_path_silent(
    project_dir: Path,
    git_changes: bool,
) -> Path:
    """
    Determine the output ZIP path inside the .contextzip/ workspace,
    without printing any warnings (for programmatic/API use).

    Falls back to the system temp directory if the workspace cannot be created.
    """
    workspace, is_git_repo = _workspace_dir(project_dir)
    output_dir = workspace / _OUTPUT_DIRNAME
    filename = "changes.zip" if git_changes else "codebase.zip"

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return Path(tempfile.gettempdir()) / filename

    if is_git_repo:
        try:
            _ensure_workspace_gitignore(workspace)
            git_root = _find_git_root(project_dir)
            if git_root is not None and _is_relative_to(workspace, git_root):
                _migrate_legacy_root_gitignore_entry(git_root)
        except OSError:
            pass

    return output_dir / filename


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
    output_dir = workspace / _OUTPUT_DIRNAME
    filename = "changes.zip" if git_changes else "codebase.zip"

    # Attempt to create the workspace's output directory
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        # Graceful fallback: warn and use temp dir
        console.print(
            f"\n  [yellow]⚠[/]  Could not create [cyan].contextzip/[/] workspace "
            f"([dim]{exc}[/]) — falling back to temp directory.\n"
        )
        return Path(tempfile.gettempdir()) / filename

    # Handle .gitignore only when we're inside a git repo and the workspace
    # actually lives inside it (a custom absolute path outside the repo has
    # nothing for .gitignore to protect)
    if is_git_repo:
        try:
            _ensure_workspace_gitignore(workspace)
        except OSError:
            # Non-fatal: gitignore update failed, carry on silently
            pass

        git_root = _find_git_root(project_dir)
        if git_root is not None and _is_relative_to(workspace, git_root):
            try:
                _migrate_legacy_root_gitignore_entry(git_root)
            except OSError:
                pass

    return output_dir / filename
