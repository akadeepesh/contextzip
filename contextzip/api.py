"""
api.py — Public Python API for contextzip.

Exposes the same capabilities as the CLI but as plain Python functions:
no Click, no Rich output, no SystemExit. All functions raise exceptions
on failure so callers can handle errors in their own way.

Quickstart
──────────
    from contextzip import get_git_changes, get_files, create_zip

    # Get git-changed files and use them directly (no zip needed)
    collection = get_git_changes()
    for path in collection.files:
        upload(path)                    # plain pathlib.Path objects

    # Or zip them
    pkg = create_zip(collection, output="/tmp/changes.zip")
    with open(pkg.zip_path, "rb") as f:
        upload_to_s3(f)

    # Get all project files (respecting .gitignore and built-in rules)
    collection = get_files()
    for path in collection.files:
        print(path)

    # Narrow it down
    collection = get_files(
        include=["src/", "app/"],
        exclude=["tests/", "*.log"],
    )
    pkg = create_zip(collection, output="/tmp/upload.zip")
    print(f"Packed {pkg.file_count} files → {pkg.zip_path}")
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from contextzip.applier import ApplyResult
from contextzip.detector import DetectionResult, detect
from contextzip.filters import (
    ResolveResult,
    build_spec,
    resolve_files,
    resolve_files_from_git,
)
from contextzip.git import GitError, GitErrorKind, get_changed_files
from contextzip.packager import PackageResult, create_zip_silent


# ---------------------------------------------------------------------------
# Public result type
# ---------------------------------------------------------------------------


@dataclass
class FileCollection:
    """
    A resolved set of files returned by :func:`get_git_changes` or
    :func:`get_files`.

    ``files`` is always a plain list of absolute :class:`pathlib.Path` objects
    — use them directly for uploads, processing, or anything else.
    Zipping is optional: pass this object to :func:`create_zip` if needed.

    Attributes
    ----------
    files:
        Absolute paths of all files in this collection. These are the
        files that would be (or were) included in a ZIP.
    skipped:
        Files that were silently skipped during resolution, as
        ``(path, reason)`` tuples (e.g. dangling symlinks, unreadable files).
    large_files:
        Files exceeding 1 MB, as ``(path, size_in_bytes)`` tuples.
        They are still included in ``files`` — this is informational only.
    binary_files:
        Files that appear to be binary (contain null bytes). Still included
        in ``files`` — informational only.
    project_dir:
        The project root used when resolving paths.
    ecosystem:
        Detected ecosystem string, e.g. ``"Next.js + Node.js"``.
    """

    files: list[Path] = field(default_factory=list)
    skipped: list[tuple[Path, str]] = field(default_factory=list)
    large_files: list[tuple[Path, int]] = field(default_factory=list)
    binary_files: list[Path] = field(default_factory=list)
    project_dir: Path = field(default_factory=Path.cwd)
    ecosystem: str = "Unknown"

    def __len__(self) -> int:
        return len(self.files)

    def __iter__(self):
        return iter(self.files)

    def __bool__(self) -> bool:
        return bool(self.files)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ContextzipError(Exception):
    """Base class for all contextzip API errors."""


class NotARepositoryError(ContextzipError):
    """Raised when the project directory is not inside a git repository."""


class GitNotFoundError(ContextzipError):
    """Raised when git is not installed or not on PATH."""


class GitCommandError(ContextzipError):
    """Raised when a git command fails unexpectedly."""


class NoFilesError(ContextzipError):
    """Raised when file resolution produces an empty result."""


class ZipNotFoundError(ContextzipError):
    """Raised when apply_zip() can't resolve which zip to apply."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_git_changes(
    path: str | Path | None = None,
) -> FileCollection:
    """
    Return the files that git reports as modified, added, or untracked.

    The git root is found automatically by walking up from *path* (or the
    current working directory). Files are filtered through contextzip's base
    safety rules so secrets, binaries, and similar files are always excluded.

    Parameters
    ----------
    path:
        Directory to start from. Defaults to ``Path.cwd()``. The actual
        git root may be a parent of this directory.

    Returns
    -------
    FileCollection
        ``collection.files`` contains absolute :class:`~pathlib.Path` objects
        for every changed file. Iterate over it or pass it to
        :func:`create_zip`.

    Raises
    ------
    GitNotFoundError
        If git is not installed or not on PATH.
    NotARepositoryError
        If *path* is not inside a git repository.
    GitCommandError
        If ``git status`` fails for any other reason.

    Example
    -------
    ::

        from contextzip import get_git_changes

        collection = get_git_changes()
        for f in collection.files:
            print(f)          # plain pathlib.Path — use however you like

        # Only staged files
        for rel in collection._git_changes.staged:
            print(rel)
    """
    project_dir = _resolve_dir(path)
    git_result = get_changed_files(project_dir)

    if isinstance(git_result, GitError):
        _raise_git_error(git_result)

    if git_result.is_empty:
        return FileCollection(project_dir=project_dir)

    resolved = resolve_files_from_git(
        git_files=git_result.files,
        project_dir=project_dir,
    )

    detection = detect(project_dir)

    collection = _resolve_result_to_collection(resolved, project_dir, detection)
    # Stash the raw GitChanges on the collection for callers who want
    # staged/unstaged/untracked breakdowns without accessing internals
    collection._git_changes = git_result  # type: ignore[attr-defined]
    return collection


def get_files(
    path: str | Path | None = None,
    *,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    use_gitignore: bool = True,
) -> FileCollection:
    """
    Return all project files after applying contextzip's standard exclusion rules.

    Parameters
    ----------
    path:
        Project root to scan. Defaults to ``Path.cwd()``.
    include:
        If given, only files under these paths are returned (e.g.
        ``["src/", "app/"]``). Matched as exact path prefixes.
    exclude:
        Extra exclusion patterns on top of the auto-detected rules
        (gitignore syntax, e.g. ``["tests/", "*.log"]``).
    use_gitignore:
        Whether to apply the project's ``.gitignore`` file.
        Defaults to ``True``.

    Returns
    -------
    FileCollection
        ``collection.files`` contains absolute :class:`~pathlib.Path` objects
        for every included file.

    Example
    -------
    ::

        from contextzip import get_files

        # All project files
        collection = get_files()

        # Only src/, excluding tests
        collection = get_files(include=["src/"], exclude=["tests/"])

        for f in collection.files:
            process(f)
    """
    project_dir = _resolve_dir(path)
    detection = detect(project_dir)

    gitignore_path = (project_dir / ".gitignore") if use_gitignore else None

    spec = build_spec(
        rule_modules=detection.rule_modules,
        extra_exclude=exclude or None,
        gitignore_path=gitignore_path,
    )

    resolved = resolve_files(
        project_dir=project_dir,
        spec=spec,
        include_only=include or None,
    )

    return _resolve_result_to_collection(resolved, project_dir, detection)


def create_zip(
    collection: FileCollection,
    output: str | Path | None = None,
) -> PackageResult:
    """
    Write *collection* into a ZIP archive and return the result.

    Parameters
    ----------
    collection:
        A :class:`FileCollection` returned by :func:`get_git_changes` or
        :func:`get_files`.
    output:
        Where to write the ZIP. If omitted, the archive is written to the
        ``.contextzip/`` workspace at the project root (same as the CLI).
        Pass an explicit path to control where it lands — useful when you
        want to write to a temp directory before uploading.

    Returns
    -------
    PackageResult
        Contains ``zip_path`` (a :class:`~pathlib.Path`), ``file_count``,
        ``compressed_bytes``, ``uncompressed_bytes``, and ``skipped_in_zip``.

    Raises
    ------
    NoFilesError
        If *collection* is empty (nothing to zip).
    OSError
        If the ZIP file cannot be written.

    Example
    -------
    ::

        import tempfile
        from contextzip import get_git_changes, create_zip

        collection = get_git_changes()
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            pkg = create_zip(collection, output=tmp.name)

        with open(pkg.zip_path, "rb") as f:
            upload_to_s3(f.read())

        print(f"Packed {pkg.file_count} files, {pkg.compressed_bytes} bytes compressed")
    """
    if not collection.files:
        raise NoFilesError(
            "The FileCollection is empty — nothing to zip. "
            "Check get_git_changes() or get_files() returned files."
        )

    resolve_result = ResolveResult(
        included=collection.files,
        skipped=collection.skipped,
        large_files=collection.large_files,
        binary_files=collection.binary_files,
    )

    output_path = Path(output).resolve() if output is not None else None

    return create_zip_silent(
        resolve_result=resolve_result,
        project_dir=collection.project_dir,
        output_path=output_path,
    )


def apply_zip(
    zip_path: str | Path | None = None,
    *,
    project_dir: str | Path | None = None,
    manifest: str | Path | None = None,
) -> ApplyResult:
    """
    Apply an AI-returned ZIP back into *project_dir* (defaults to cwd).

    Diffs the zip against the local sidecar manifest most recently written
    to ``.contextzip/output/`` (or the one at *manifest*, if given) to
    classify every file as new, modified, unchanged, or one with no safe
    baseline (locally edited since zipping, or never part of the original
    manifest). Only adds and modifies files — nothing is ever deleted.
    Every overwritten file is backed up first, under
    ``.contextzip/backups/<timestamp>/``.

    Unlike the CLI, this applies immediately and does not prompt — check
    ``contextzip.applier.build_plan()`` yourself first if you want to
    inspect risk before writing (see ``ApplyPlan.is_risky`` /
    ``.risky_entries``).

    Parameters
    ----------
    zip_path:
        Path to the returned zip. If omitted, auto-detects from
        ``.contextzip/inbox/`` — exactly one zip must be present there.
    project_dir:
        Project root to apply into. Defaults to ``Path.cwd()``.
    manifest:
        Explicit manifest path to diff against, overriding auto-detection.

    Returns
    -------
    ApplyResult
        ``written`` (list of relative paths written), ``backup_dir``
        (``Path`` or ``None`` if nothing needed backing up), and
        ``applied_zip_path`` (where the consumed zip ended up).

    Raises
    ------
    ZipNotFoundError
        If no zip could be resolved — missing, or multiple candidates in
        the inbox with none specified.

    Example
    -------
    ::

        from contextzip import apply_zip

        result = apply_zip()  # picks up .contextzip/inbox/*.zip
        print(f"Wrote {len(result.written)} files")
        if result.backup_dir:
            print(f"Backup at {result.backup_dir}")
    """
    from contextzip.applier import (
        ApplyError,
        build_plan,
        execute_plan,
        find_latest_manifest,
        find_zip_to_apply,
    )

    pdir = _resolve_dir(project_dir)

    try:
        resolved_zip = find_zip_to_apply(pdir, zip_path)
    except ApplyError as exc:
        raise ZipNotFoundError(str(exc)) from exc

    manifest_path = find_latest_manifest(pdir, manifest)
    plan = build_plan(resolved_zip, pdir, manifest_path)
    return execute_plan(plan, pdir)


def detect_ecosystem(
    path: str | Path | None = None,
) -> DetectionResult:
    """
    Detect the ecosystem(s) present in a project directory.

    Parameters
    ----------
    path:
        Directory to inspect. Defaults to ``Path.cwd()``.

    Returns
    -------
    DetectionResult
        Has ``.ecosystems`` (list of strings like ``["Next.js", "Node.js"]``),
        ``.display_name`` (e.g. ``"Next.js + Node.js"``), and
        ``.confidence`` (``"low"`` / ``"medium"`` / ``"high"``).

    Example
    -------
    ::

        from contextzip import detect_ecosystem

        result = detect_ecosystem("/path/to/project")
        print(result.display_name)   # "Django + Python"
        print(result.confidence)     # "high"
    """
    return detect(_resolve_dir(path))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_dir(path: str | Path | None) -> Path:
    if path is None:
        return Path(os.getcwd()).resolve()
    return Path(path).resolve()


def _resolve_result_to_collection(
    resolved: ResolveResult,
    project_dir: Path,
    detection: DetectionResult,
) -> FileCollection:
    return FileCollection(
        files=resolved.included,
        skipped=resolved.skipped,
        large_files=resolved.large_files,
        binary_files=resolved.binary_files,
        project_dir=project_dir,
        ecosystem=detection.display_name,
    )


def _raise_git_error(error: GitError) -> None:
    if error.kind == GitErrorKind.GIT_NOT_FOUND:
        raise GitNotFoundError(error.message)
    if error.kind == GitErrorKind.NOT_A_REPO:
        raise NotARepositoryError(error.message)
    raise GitCommandError(error.message)
