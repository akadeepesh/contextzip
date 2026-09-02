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
  - Deterministic output names: codebase.zip (default), changes.zip
    (--git-changes), or vibe.zip (--prompt) — see Phase 9 below for the
    per-mode subfolder each now lives in.
  - --output flag bypasses workspace logic entirely (user owns the path)

Phase 7 changes:
  - Workspace restructured: generated ZIPs now live under .contextzip/output/
    instead of directly in .contextzip/, leaving room for config.json
    alongside them.
  - Ignoring is now handled by a self-contained .contextzip/.gitignore
    (ignore everything, no exceptions) instead of a blanket ".contextzip/"
    entry in the project's top-level .gitignore — this keeps working even
    when the workspace is relocated outside the default git-root anchor
    (see project_config.py / _resolve_workspace_location). config.json is
    local by default like everything else in the workspace; sharing it
    with a team is a deliberate, manual `git add -f`, never automatic.

Phase 8 changes:
  - Every ZIP now gets a sidecar manifest written next to it (e.g.
    codebase.zip -> codebase.manifest.json), never inside the ZIP itself.
    It records a hash of each included file at zip-time, and is what
    `contextzip apply-zip` (applier.py) later diffs an AI-returned ZIP
    against. Keeping it out of the archive means it's never uploaded and
    never visible to whatever AI tool the ZIP is pasted into — nothing
    for a model (or a teammate) to notice or ask about.

Phase 9 changes:
  - .contextzip/output/ is no longer a single flat folder shared by every
    run mode. Each mode now gets its own subfolder — output/codebase/,
    output/git-changes/, output/prompt/ (and watcher.py's own
    output/watch/) — named after the flag that produced it, so it's
    obvious at a glance which zip came from `contextzip`,
    `contextzip --git-changes`, or `contextzip --prompt` without having
    to remember which run you just did. See `output_subdir_for_mode` /
    `zip_filename_for_mode`, which are the single source of truth other
    modules (watcher.py, cleanup.py) build on so the mapping never drifts.

Phase 10 changes:
  - Implements `limits.redact_secrets`, previously persisted by config.py /
    the config UI but never actually enforced (flagged as a known
    limitation as of 0.4.0). Text files that are already going into the
    archive get scanned for secret-shaped values (API keys, tokens,
    private-key blocks, etc. — see redact.py) and the matched values are
    replaced with "[REDACTED]" before writing. Binary and oversized files
    are never scanned — see `_should_scan_for_secrets`.
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
from contextzip.redact import redact_secrets

# Subdirectory of the workspace where generated ZIPs are written.
_OUTPUT_DIRNAME = "output"

# Per-mode output subfolder + zip filename. "standard" is the plain
# `contextzip` run with no --git-changes / --prompt. Unknown modes (there
# shouldn't be any — this is the exhaustive list of run modes the CLI
# produces) fall back to "standard" rather than raising, so a caller that
# forgets to pass a mode still gets a sane, working path.
_MODE_DIRNAMES: dict[str, str] = {
    "standard": "codebase",
    "git-changes": "git-changes",
    "prompt": "prompt",
    "watch": "watch",
}
_MODE_ZIP_FILENAMES: dict[str, str] = {
    "standard": "codebase.zip",
    "git-changes": "changes.zip",
    "prompt": "vibe.zip",
}
_DEFAULT_MODE = "standard"

# Every mode that produces a zip+manifest+report set via create_zip /
# create_zip_silent (excludes "watch", which writes its own debug-context.zip
# directly — see watcher.py). Used by cleanup.py to enumerate mode folders
# without hardcoding the list a second time.
ZIP_MODES: tuple[str, ...] = ("standard", "git-changes", "prompt")


def output_subdir_for_mode(workspace: Path, mode: str) -> Path:
    """
    The .contextzip/output/<mode-folder>/ directory for *mode*.

    *mode* is one of "standard", "git-changes", "prompt", or "watch".
    Unrecognized values fall back to the "standard" folder rather than
    raising, so this never becomes a hard crash if a new mode is added
    upstream before this mapping is updated.
    """
    dirname = _MODE_DIRNAMES.get(mode, _MODE_DIRNAMES[_DEFAULT_MODE])
    return workspace / _OUTPUT_DIRNAME / dirname


def zip_filename_for_mode(mode: str) -> str:
    """The deterministic zip filename written for *mode* (see module docstring)."""
    return _MODE_ZIP_FILENAMES.get(mode, _MODE_ZIP_FILENAMES[_DEFAULT_MODE])

# Contents of .contextzip/.gitignore — ignore absolutely everything in the
# workspace, no exceptions. .contextzip/ is a local, per-machine scratch
# space; nothing in it is ever pushed by default. Anyone who genuinely
# wants to share something from it (e.g. config.json, for team-wide
# settings) can still do so explicitly with `git add -f`, but contextzip
# itself never carves out an exception — see project_config.py.
_WORKSPACE_GITIGNORE_CONTENTS = (
    "# contextzip workspace\n"
    "# Everything here is a local, per-machine artifact. Nothing in this\n"
    "# directory is ever pushed by default. To share something from it\n"
    "# (e.g. config.json) with your team anyway, use `git add -f`.\n"
    "*\n"
)

# A previous version of contextzip added this block to the project's
# top-level .gitignore. Now that .contextzip/.gitignore handles ignoring
# on its own (and works correctly regardless of where the workspace is
# relocated), we clean up that older entry the first time we touch a
# project — see
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
    # Files where at least one secret-shaped value was redacted before
    # writing — (path relative to project root, pattern names matched).
    # Always empty unless limits.redact_secrets is enabled.
    redacted: list[tuple[Path, list[str]]] = field(default_factory=list)

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
# Shared per-file write logic (redaction happens here, in exactly one place)
# ---------------------------------------------------------------------------


def _write_member(
    zf: zipfile.ZipFile,
    abs_path: Path,
    rel: Path,
    redact_enabled: bool,
    binary_paths: frozenset[Path],
    large_paths: frozenset[Path],
) -> list[str]:
    """
    Write one file into *zf*.

    When *redact_enabled* is True and *abs_path* isn't already known to be
    binary or oversized (per resolve_files()'s existing classification —
    never re-detected here), reads it as UTF-8 text and redacts any
    secret-shaped values before writing. Binary/oversized files, or text
    that fails to decode as UTF-8, are written unmodified via the same
    fast zf.write() path used when redaction is off entirely.

    Returns the list of pattern names redacted (empty if none matched, or
    if this file wasn't eligible for scanning). May raise OSError, exactly
    as the old plain zf.write() call did — callers already handle that.
    """
    if not redact_enabled or abs_path in binary_paths or abs_path in large_paths:
        zf.write(abs_path, arcname=rel.as_posix())
        return []

    original_bytes = abs_path.read_bytes()
    try:
        text = original_bytes.decode("utf-8")
    except UnicodeDecodeError:
        zf.writestr(rel.as_posix(), original_bytes)
        return []

    redacted_text, matched = redact_secrets(text)
    zf.writestr(rel.as_posix(), redacted_text.encode("utf-8") if matched else original_bytes)
    return matched


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_zip_silent(
    resolve_result: ResolveResult,
    project_dir: Path,
    output_path: Path | None,
    mode: str = _DEFAULT_MODE,
    prompt_txt: str | None = None,
    redact_secrets_enabled: bool = False,
) -> PackageResult:
    """
    Write the included files from *resolve_result* into a ZIP archive
    without any console/progress output. Intended for programmatic use.

    Identical to :func:`create_zip` except it produces no Rich output —
    safe to call in scripts, background threads, or anywhere a TTY isn't
    available.

    *mode* selects which .contextzip/output/<mode>/ subfolder and
    deterministic filename to use — see `output_subdir_for_mode` /
    `zip_filename_for_mode`. Ignored when *output_path* is given.

    *redact_secrets_enabled* mirrors the project's `limits.redact_secrets`
    setting — see redact.py.
    """
    if output_path is not None:
        zip_path = output_path
    else:
        zip_path = _workspace_output_path_silent(project_dir, mode)

    zip_path.parent.mkdir(parents=True, exist_ok=True)

    included: list[Path] = resolve_result.included
    binary_paths = frozenset(resolve_result.binary_files)
    large_paths = frozenset(p for p, _ in resolve_result.large_files)
    skipped_in_zip: list[tuple[Path, str]] = []
    redacted: list[tuple[Path, list[str]]] = []
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
                matched = _write_member(
                    zf, abs_path, rel, redact_secrets_enabled, binary_paths, large_paths
                )
                if matched:
                    redacted.append((rel, matched))
                uncompressed += file_size
                file_count += 1
            except PermissionError:
                skipped_in_zip.append((abs_path, "permission denied"))
            except OSError as e:
                skipped_in_zip.append((abs_path, str(e)))

    compressed = zip_path.stat().st_size

    _write_manifest_sidecar(zip_path, project_dir, included)

    return PackageResult(
        zip_path=zip_path,
        file_count=file_count,
        uncompressed_bytes=uncompressed,
        compressed_bytes=compressed,
        skipped_in_zip=skipped_in_zip,
        redacted=redacted,
    )


def create_zip(
    resolve_result: ResolveResult,
    project_dir: Path,
    output_path: Path | None,
    console: Console,
    mode: str = _DEFAULT_MODE,
    prompt_txt: str | None = None,
    redact_secrets_enabled: bool = False,
) -> PackageResult:
    """
    Write the included files from *resolve_result* into a ZIP archive.

    If *output_path* is given (via --output) it is used as-is and the
    .contextzip/ workspace logic is skipped entirely.

    Otherwise the archive is written to .contextzip/output/<mode>/ inside
    the workspace (created automatically) at the git root, or the CWD if
    no git repository is detected. *mode* is one of "standard",
    "git-changes", or "prompt" — see `output_subdir_for_mode` /
    `zip_filename_for_mode` for the folder/filename each maps to.

    If *prompt_txt* is provided (set when --prompt is used), a ``prompt.txt``
    file is written as the first entry in the ZIP. Any AI tool that receives
    the ZIP will immediately see the task description and selected file list.

    *redact_secrets_enabled* mirrors the project's `limits.redact_secrets`
    setting — see redact.py. Only ever scans files already known (via
    *resolve_result*) to be text and within `limits.max_file_size_mb`;
    binary/oversized files are always written unmodified.

    Returns a :class:`PackageResult` with compression stats and any
    files that had to be skipped during writing (e.g. permission denied).
    """
    if output_path is not None:
        # User specified --output: honour it exactly, no workspace logic.
        zip_path = output_path
    else:
        zip_path = _workspace_output_path(project_dir, mode, console)

    zip_path.parent.mkdir(parents=True, exist_ok=True)

    included: list[Path] = resolve_result.included
    binary_paths = frozenset(resolve_result.binary_files)
    large_paths = frozenset(p for p, _ in resolve_result.large_files)
    skipped_in_zip: list[tuple[Path, str]] = []
    redacted: list[tuple[Path, list[str]]] = []
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
                    matched = _write_member(
                        zf, abs_path, rel, redact_secrets_enabled, binary_paths, large_paths
                    )
                    if matched:
                        redacted.append((rel, matched))
                    uncompressed += file_size
                    file_count += 1
                except PermissionError:
                    skipped_in_zip.append((abs_path, "permission denied"))
                except OSError as e:
                    skipped_in_zip.append((abs_path, str(e)))
                finally:
                    progress.advance(task, file_size)

    compressed = zip_path.stat().st_size

    _write_manifest_sidecar(zip_path, project_dir, included)

    return PackageResult(
        zip_path=zip_path,
        file_count=file_count,
        uncompressed_bytes=uncompressed,
        compressed_bytes=compressed,
        skipped_in_zip=skipped_in_zip,
        redacted=redacted,
    )


# ---------------------------------------------------------------------------
# Manifest sidecar (local-only — never written into the ZIP itself)
# ---------------------------------------------------------------------------


def _write_manifest_sidecar(
    zip_path: Path,
    project_dir: Path,
    included: list[Path],
) -> None:
    """
    Write the local manifest for the ZIP just created at *zip_path*.

    This lives next to the archive on disk (e.g. codebase.zip ->
    codebase.manifest.json) and is never added as a ZIP entry — it must
    never travel with the archive when it's uploaded to an AI tool.
    `contextzip apply-zip` reads it later, from this same local location,
    to classify an AI-returned ZIP's files as new / modified / unchanged
    without needing any extra metadata inside the returned ZIP.

    Best-effort: failures here (e.g. read-only filesystem) never affect
    the ZIP creation itself, since apply-zip degrades gracefully when no
    manifest is found.
    """
    # Lazy import: applier.py resolves the workspace dir via this module,
    # so importing it at module load time would be circular.
    from contextzip.applier import write_manifest

    try:
        write_manifest(zip_path=zip_path, project_dir=project_dir, included=included)
    except OSError:
        pass


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
    ignore the entire workspace, no exceptions — .contextzip/ is a local,
    per-machine scratch space and nothing in it is tracked by default,
    including config.json and this .gitignore file itself.

    This is self-contained: it works no matter where *workspace* ends up
    living (git-root default, cwd, or a custom relocated path), since a
    nested .gitignore applies to its own directory regardless of where
    that directory sits in the tree — unlike a single blanket entry in a
    distant top-level .gitignore, which can't be relied on to reach a
    relocated workspace.

    Idempotent — leaves an existing, already-correct file untouched, and
    only rewrites files that don't yet match (e.g. hand-edited or from an
    older contextzip version that carved out an exception for config.json).
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
    is redundant now that the nested .gitignore ignores the whole workspace
    on its own — leaving the old root-level entry around is harmless but
    unnecessary clutter, and it would have stopped git from ever descending
    into .contextzip/ to notice a force-added file (`git add -f`) if the
    entry ignores the directory itself rather than its contents. Only ever
    removes the exact block contextzip itself wrote — never touches
    unrelated .gitignore content, and is a no-op if the block isn't present
    (e.g. it was already removed, or never added).
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
      2. Project config (.contextzip/config.json at git root — local by
         default like the rest of the workspace, force-added with
         `git add -f` if a team wants to share it; falls back to the
         deprecated .contextzip.json if that's all a project has)
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
    mode: str,
) -> Path:
    """
    Determine the output ZIP path inside the .contextzip/ workspace,
    without printing any warnings (for programmatic/API use).

    Falls back to the system temp directory if the workspace cannot be created.
    """
    workspace, is_git_repo = _workspace_dir(project_dir)
    output_dir = output_subdir_for_mode(workspace, mode)
    filename = zip_filename_for_mode(mode)

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
    mode: str,
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
    output_dir = output_subdir_for_mode(workspace, mode)
    filename = zip_filename_for_mode(mode)

    # Attempt to create the workspace's output directory
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        # Graceful fallback: warn and use temp dir
        console.print(
            f"[yellow]![/] Could not create .contextzip/ workspace ({exc}) — "
            "falling back to temp directory."
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
