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
    "base":   "contextzip.rules.base",
    "node":   "contextzip.rules.node",
    "python": "contextzip.rules.python",
    "rust":   "contextzip.rules.rust",
    "go":     "contextzip.rules.go",
    "ruby":   "contextzip.rules.ruby",
}

# Files larger than this trigger a warning (but are still included)
LARGE_FILE_WARN_BYTES = 1 * 1024 * 1024   # 1 MB

# Peek this many bytes to detect binary files
_BINARY_PEEK = 512


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

@dataclass
class ResolveResult:
    included:     list[Path] = field(default_factory=list)
    excluded:     list[Path] = field(default_factory=list)
    skipped:      list[tuple[Path, str]] = field(default_factory=list)  # (path, reason)
    large_files:  list[tuple[Path, int]] = field(default_factory=list)  # (path, bytes)
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
            lines = gitignore_path.read_text(encoding="utf-8", errors="ignore").splitlines()
            # Strip comments and blank lines; pathspec handles the rest
            patterns.extend(l for l in lines if l.strip() and not l.startswith("#"))
        except OSError:
            pass

    if extra_exclude:
        patterns.extend(extra_exclude)

    return pathspec.PathSpec.from_lines("gitwildmatch", patterns)


def resolve_files(
    project_dir: Path,
    spec: pathspec.PathSpec,
    include_only: list[str] | None = None,
) -> ResolveResult:
    """
    Walk *project_dir* and classify every file.

    Returns a :class:`ResolveResult` with included/excluded/skipped/large/binary lists.
    """
    result = ResolveResult()

    for abs_path in sorted(project_dir.rglob("*")):

        # ── Resolve symlinks safely ──────────────────────────────────────────
        if abs_path.is_symlink():
            try:
                real = abs_path.resolve(strict=True)
                if not real.is_file():
                    continue   # symlink to a dir or missing — skip silently
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
            # Symlink pointed outside the project tree — include with original rel
            try:
                rel = abs_path.relative_to(project_dir)
            except ValueError:
                result.skipped.append((abs_path, "outside project tree"))
                continue

        rel_str = rel.as_posix()

        # ── Check directory-level exclusion ──────────────────────────────────
        if _any_parent_excluded(rel, spec):
            result.excluded.append(abs_path)
            continue

        # ── Check file-level exclusion ───────────────────────────────────────
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
        if file_size >= LARGE_FILE_WARN_BYTES:
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
        if file_size >= LARGE_FILE_WARN_BYTES:
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