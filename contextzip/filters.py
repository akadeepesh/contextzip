"""
filters.py — Loads exclusion patterns from rule modules and resolves
which files in a project directory should be included or excluded.

Phase 5 hardening:
  - Symlinks are followed safely; dangling symlinks are skipped
  - .gitignore in the project root is respected as extra exclusions
  - --include prefix matching is exact (src/ won't match src2/)
  - Binary-looking files (null bytes) are flagged but still included
  - Unreadable files are skipped with a warning rather than silently dropped

Git mode:
  - resolve_files_from_git() accepts a GitChanges result and builds a
    ResolveResult from only the modified/added/untracked files reported
    by git, while still running all size and binary checks.

Force-include (Phase 7):
  - build_force_include_spec() turns a project config's "always_include"
    patterns into a PathSpec. resolve_files() accepts it as force_include
    and, when a file (or one of its parent directories) matches, treats
    the file as not excluded — a standing negation on top of auto-rules
    and .gitignore. It does not apply in git-changes mode, and it does not
    override an explicit --include/-i for the current run.
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path
from dataclasses import dataclass, field

import pathspec


# ---------------------------------------------------------------------------
# Rule registry
# ---------------------------------------------------------------------------

_RULE_REGISTRY: dict[str, str] = {
    "base": "contextzip.rules.base",
    "node": "contextzip.rules.node",
    "python": "contextzip.rules.python",
    "rust": "contextzip.rules.rust",
    "go": "contextzip.rules.go",
    "ruby": "contextzip.rules.ruby",
}

# Files larger than this trigger a warning (but are still included)
LARGE_FILE_WARN_BYTES = 1 * 1024 * 1024  # 1 MB

# Peek this many bytes to detect binary files
_BINARY_PEEK = 512

# Directory names scan_all_files() prunes during the walk itself, before
# even reading their contents. These are always dependency/build/VCS
# noise that no ecosystem's rule module would ever include, and — for
# .venv/venv especially — often contain symlinks to files well outside
# the project tree (e.g. bin/python -> the system interpreter), which is
# exactly the kind of thing that must never reach classify_scanned_files.
# Kept intentionally small and generic (not ecosystem-specific — that's
# what rules/*.py is for) since this list affects every project's scan.
_ALWAYS_PRUNED_DIRNAMES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        ".next",
        ".nuxt",
        ".cache",
        "target",
        "vendor",
        "site-packages",
        ".contextzip",
    }
)


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


@dataclass
class ResolveResult:
    included: list[Path] = field(default_factory=list)
    excluded: list[Path] = field(default_factory=list)
    skipped: list[tuple[Path, str]] = field(default_factory=list)  # (path, reason)
    large_files: list[tuple[Path, int]] = field(default_factory=list)  # (path, bytes)
    binary_files: list[Path] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_spec(
    rule_modules: list[str],
    extra_exclude: list[str] | None = None,
    gitignore_path: Path | None = None,
) -> pathspec.PathSpec:
    """
    Combine patterns from *rule_modules*, an optional *.gitignore* file,
    and any *extra_exclude* CLI patterns into a single PathSpec.
    """
    patterns: list[str] = []

    for key in rule_modules:
        module_path = _RULE_REGISTRY.get(key)
        if not module_path:
            continue
        try:
            mod = importlib.import_module(module_path)
            patterns.extend(getattr(mod, "PATTERNS", []))
        except ImportError:
            pass

    # Respect the project's own .gitignore if present
    if gitignore_path and gitignore_path.is_file():
        try:
            lines = gitignore_path.read_text(
                encoding="utf-8", errors="ignore"
            ).splitlines()
            # Strip comments and blank lines; pathspec handles the rest
            patterns.extend(
                line for line in lines if line.strip() and not line.startswith("#")
            )
        except OSError:
            pass

    if extra_exclude:
        patterns.extend(extra_exclude)

    return pathspec.PathSpec.from_lines("gitwildmatch", patterns)


def build_force_include_spec(patterns: list[str] | None) -> pathspec.PathSpec | None:
    """
    Build a PathSpec from *patterns* (a project config's "always_include"
    list) for use as resolve_files()'s force_include argument.

    Returns None (rather than an empty PathSpec) when *patterns* is empty,
    so callers can cheaply skip the force-include check entirely.
    """
    if not patterns:
        return None
    return pathspec.PathSpec.from_lines("gitwildmatch", patterns)


def scan_all_files(project_dir: Path) -> list[tuple[Path, int]]:
    """
    Lightweight full-tree walk that returns every regular file under
    *project_dir* once, as (abs_path, size_bytes) tuples, without
    classifying anything.

    Meant for tools that need to classify the same file list against
    several different specs without re-touching the filesystem each time
    (the local config UI — see webui/server.py — scans once at startup,
    then reclassifies purely in memory on every checkbox toggle). Pair
    with classify_scanned_files().

    Two things this does that a plain rglob() wouldn't:

    - Prunes a fixed list of known-huge, always-irrelevant directories
      (.venv, node_modules, .git, __pycache__, build output, etc.) during
      the walk itself, not just at classification time. These can easily
      be tens of thousands of files (a .venv's site-packages, in
      particular) that would never end up in a ZIP anyway — walking into
      them at all makes the scan needlessly slow and floods the UI's tree
      with noise nobody wants to look at.
    - Rejects symlinks whose real target resolves outside project_dir.
      A virtualenv's bin/python is a classic example (symlinks to the
      system interpreter) — without this check, relative_to() calls
      downstream (see webui/server.py's _rel()) raise ValueError and
      crash the request entirely.

    Deliberately simpler than resolve_files()'s walk (no skipped/binary
    bookkeeping) since callers here only need "does this file exist, and
    how big is it" — resolve_files() remains the source of truth for an
    actual packaging run, and still sees everything within the pruned
    dirs is excluded via the normal rules (rules/base.py etc.) whether or
    not the config UI ever looked at them.
    """
    files: list[tuple[Path, int]] = []

    for dirpath, dirnames, filenames in os.walk(project_dir, topdown=True):
        dirnames[:] = [d for d in dirnames if d not in _ALWAYS_PRUNED_DIRNAMES]

        for filename in filenames:
            abs_path = Path(dirpath) / filename

            if abs_path.is_symlink():
                try:
                    real = abs_path.resolve(strict=True)
                except (OSError, RuntimeError):
                    continue  # dangling symlink — skip silently, this is best-effort
                try:
                    real.relative_to(project_dir)
                except ValueError:
                    continue  # resolves outside the project tree — never include
                if not real.is_file():
                    continue
                abs_path = real
            elif not abs_path.is_file():
                continue

            try:
                size = abs_path.stat().st_size
            except OSError:
                continue

            files.append((abs_path, size))

    return files


def classify_scanned_files(
    project_dir: Path,
    files: list[tuple[Path, int]],
    spec: pathspec.PathSpec,
    force_include: pathspec.PathSpec | None = None,
) -> dict[Path, bool]:
    """
    Classify each (abs_path, size) from scan_all_files() against *spec*
    (and optional *force_include*) without touching the filesystem again.

    Mirrors resolve_files()'s force_include-then-spec precedence exactly,
    reusing the same matching primitives, but works off an already-scanned
    file list. Returns {abs_path: is_included}.
    """
    result: dict[Path, bool] = {}
    for abs_path, _size in files:
        try:
            rel = abs_path.relative_to(project_dir)
        except ValueError:
            continue

        rel_str = rel.as_posix()

        forced = force_include is not None and (
            force_include.match_file(rel_str)
            or _any_parent_excluded(rel, force_include)
        )

        if forced:
            result[abs_path] = True
            continue

        excluded = _any_parent_excluded(rel, spec) or spec.match_file(rel_str)
        result[abs_path] = not excluded

    return result


def resolve_files(
    project_dir: Path,
    spec: pathspec.PathSpec,
    include_only: list[str] | None = None,
    force_include: pathspec.PathSpec | None = None,
    large_file_warn_bytes: int = LARGE_FILE_WARN_BYTES,
) -> ResolveResult:
    """
    Walk *project_dir* and classify every file.

    *force_include*, if given, is checked before exclusion: a file (or any
    of its parent directories) matching it is treated as not excluded, even
    if it also matches *spec* (auto-rules / .gitignore / --exclude). This
    implements a project config's "always_include" — a standing negation —
    without touching *spec* itself. It's checked ahead of *include_only* so
    an explicit `contextzip include PATH` for this run still has the final
    say on what actually gets included.

    *large_file_warn_bytes* overrides the module default (1 MB) — typically
    a project's `limits.max_file_size_mb` preference (.contextzip/config.json).

    Returns a :class:`ResolveResult` with included/excluded/skipped/large/binary lists.
    """
    result = ResolveResult()

    for original_path in sorted(project_dir.rglob("*")):
        abs_path = original_path
        # ── Resolve symlinks safely ──────────────────────────────────────────
        if abs_path.is_symlink():
            try:
                real = abs_path.resolve(strict=True)
                if not real.is_file():
                    continue  # symlink to a dir or missing — skip silently
                abs_path = real
            except (OSError, RuntimeError):
                # Dangling symlink or resolution loop
                result.skipped.append((abs_path, "dangling symlink"))
                continue

        if not abs_path.is_file():
            continue

        # ── Guard: file must still be under project_dir after symlink resolve ─
        try:
            rel = abs_path.relative_to(project_dir)
        except ValueError:
            # Symlink pointed outside the project tree — use the original
            # (pre-resolve) path to compute a meaningful relative path
            try:
                rel = original_path.relative_to(project_dir)
            except ValueError:
                result.skipped.append((original_path, "outside project tree"))
                continue

        rel_str = rel.as_posix()

        # ── force_include short-circuits the exclusion checks below ─────────
        forced = force_include is not None and (
            force_include.match_file(rel_str)
            or _any_parent_excluded(rel, force_include)
        )

        if not forced:
            # ── Check directory-level exclusion ──────────────────────────────
            if _any_parent_excluded(rel, spec):
                result.excluded.append(abs_path)
                continue

            # ── Check file-level exclusion ─────────────────────────────────
            if spec.match_file(rel_str):
                result.excluded.append(abs_path)
                continue

        # ── Apply --include filter (exact prefix, not substring) ─────────────
        if include_only:
            if not _matches_any_prefix(rel_str, include_only):
                result.excluded.append(abs_path)
                continue

        # ── Readability check ────────────────────────────────────────────────
        try:
            file_size = abs_path.stat().st_size
        except OSError as e:
            result.skipped.append((abs_path, f"stat failed: {e}"))
            continue

        # ── Large file warning ───────────────────────────────────────────────
        if file_size >= large_file_warn_bytes:
            result.large_files.append((abs_path, file_size))

        # ── Binary file detection ────────────────────────────────────────────
        if _is_binary(abs_path):
            result.binary_files.append(abs_path)
            # Still include — caller can decide; we just flag it

        result.included.append(abs_path)

    return result


def resolve_files_from_git(
    git_files: list[Path],
    project_dir: Path,
    large_file_warn_bytes: int = LARGE_FILE_WARN_BYTES,
) -> ResolveResult:
    """
    Build a :class:`ResolveResult` from a pre-selected list of files reported
    by git (modified, added, untracked).

    Applies the base exclusion spec as a safety floor even though git selected
    the files — a git-tracked ``.env`` or secret key file must still be blocked.
    Size and binary checks are also performed so that the usual warnings appear
    in the CLI output.

    Parameters
    ----------
    git_files:
        Absolute paths of files to include, as returned by
        :attr:`GitChanges.files`.
    project_dir:
        Absolute path to the project root (used for relative-path guards).
    large_file_warn_bytes:
        Overrides the module default (1 MB) — typically a project's
        `limits.max_file_size_mb` preference (.contextzip/config.json).
    """
    # Build the base spec once — this is our safety floor regardless of what
    # git reports as changed. It blocks .env, secrets, binaries, etc.
    base_spec = build_spec(rule_modules=["base"])

    result = ResolveResult()

    for abs_path in sorted(git_files):
        # ── Resolve symlinks safely ──────────────────────────────────────────
        if abs_path.is_symlink():
            try:
                real = abs_path.resolve(strict=True)
                if not real.is_file():
                    continue
                abs_path = real
            except (OSError, RuntimeError):
                result.skipped.append((abs_path, "dangling symlink"))
                continue

        if not abs_path.is_file():
            continue

        # ── Guard: must be under project_dir ────────────────────────────────
        try:
            rel = abs_path.relative_to(project_dir)
        except ValueError:
            result.skipped.append((abs_path, "outside project tree"))
            continue

        # ── Base safety floor — block secrets / binaries even if git-tracked ─
        rel_str = rel.as_posix()
        if _any_parent_excluded(rel, base_spec) or base_spec.match_file(rel_str):
            result.excluded.append(abs_path)
            continue

        # ── Readability / stat check ─────────────────────────────────────────
        try:
            file_size = abs_path.stat().st_size
        except OSError as e:
            result.skipped.append((abs_path, f"stat failed: {e}"))
            continue

        # ── Large file warning ───────────────────────────────────────────────
        if file_size >= large_file_warn_bytes:
            result.large_files.append((abs_path, file_size))

        # ── Binary file detection ────────────────────────────────────────────
        if _is_binary(abs_path):
            result.binary_files.append(abs_path)

        result.included.append(abs_path)

    return result


def summarise_exclusions(excluded: list[Path], project_dir: Path) -> dict[str, int]:
    """Group excluded files by top-level directory / filename, sorted by count."""
    buckets: dict[str, int] = {}
    for p in excluded:
        try:
            rel = p.relative_to(project_dir)
            top = rel.parts[0] if rel.parts else str(rel)
        except ValueError:
            top = p.name
        buckets[top] = buckets.get(top, 0) + 1
    return dict(sorted(buckets.items(), key=lambda x: x[1], reverse=True))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _any_parent_excluded(rel: Path, spec: pathspec.PathSpec) -> bool:
    """True if any directory component of *rel* is matched by *spec*."""
    parts = rel.parts[:-1]
    for i in range(len(parts)):
        dir_path = "/".join(parts[: i + 1]) + "/"
        if spec.match_file(dir_path):
            return True
    return False


def _matches_any_prefix(rel_str: str, prefixes: list[str]) -> bool:
    """
    True if *rel_str* starts with one of *prefixes* at a path boundary.

    ``src`` matches  ``src/index.ts``   ✓
    ``src`` matches  ``src``            ✓  (exact file named "src")
    ``src`` does NOT match ``src2/...`` ✗
    """
    for prefix in prefixes:
        p = prefix.rstrip("/")
        if rel_str == p:
            return True
        if rel_str.startswith(p + "/"):
            return True
    return False


def _is_binary(path: Path) -> bool:
    """Peek at the first few hundred bytes; return True if null bytes are found."""
    try:
        with path.open("rb") as fh:
            chunk = fh.read(_BINARY_PEEK)
        return b"\x00" in chunk
    except OSError:
        return False
