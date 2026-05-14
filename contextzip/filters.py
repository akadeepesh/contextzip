"""
filters.py — Loads exclusion patterns from rule modules and resolves
which files in a project directory should be included or excluded.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pathspec


# ---------------------------------------------------------------------------
# Rule registry — maps module key → importable module path
# ---------------------------------------------------------------------------

_RULE_REGISTRY: dict[str, str] = {
    "base":   "contextzip.rules.base",
    "node":   "contextzip.rules.node",
    "python": "contextzip.rules.python",
    "rust":   "contextzip.rules.rust",
    "go":     "contextzip.rules.go",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_spec(
    rule_modules: list[str],
    extra_exclude: list[str] | None = None,
) -> pathspec.PathSpec:
    """
    Combine patterns from the given *rule_modules* (plus optional
    *extra_exclude* patterns) into a single :class:`pathspec.PathSpec`.

    The spec can then be used to test whether a path should be excluded.
    """
    patterns: list[str] = []

    for key in rule_modules:
        module_path = _RULE_REGISTRY.get(key)
        if module_path is None:
            continue
        try:
            mod = importlib.import_module(module_path)
            patterns.extend(getattr(mod, "PATTERNS", []))
        except ImportError:
            pass

    if extra_exclude:
        patterns.extend(extra_exclude)

    return pathspec.PathSpec.from_lines("gitwildmatch", patterns)


def resolve_files(
    project_dir: Path,
    spec: pathspec.PathSpec,
    include_only: list[str] | None = None,
) -> tuple[list[Path], list[Path]]:
    """
    Walk *project_dir* and split every file into included / excluded lists.

    Parameters
    ----------
    project_dir:
        Root of the project to scan.
    spec:
        The exclusion spec built by :func:`build_spec`.
    include_only:
        If provided, only files whose relative path starts with one of
        these prefixes will be considered (after exclusions are applied).

    Returns
    -------
    (included, excluded)
        Both lists contain absolute :class:`Path` objects.
    """
    included: list[Path] = []
    excluded: list[Path] = []

    for abs_path in sorted(project_dir.rglob("*")):
        if not abs_path.is_file():
            continue

        rel = abs_path.relative_to(project_dir)
        rel_str = rel.as_posix()

        # Check directory-level exclusion — match any path component
        if _any_parent_excluded(rel, spec):
            excluded.append(abs_path)
            continue

        # Check file-level exclusion
        if spec.match_file(rel_str):
            excluded.append(abs_path)
            continue

        # Apply --include filter if set
        if include_only:
            if not any(rel_str.startswith(prefix.rstrip("/")) for prefix in include_only):
                excluded.append(abs_path)
                continue

        included.append(abs_path)

    return included, excluded


def summarise_exclusions(excluded: list[Path], project_dir: Path) -> dict[str, int]:
    """
    Group excluded files by their top-level directory / filename for display.
    Returns a dict of { label: count } sorted by count descending.
    """
    buckets: dict[str, int] = {}
    for p in excluded:
        rel = p.relative_to(project_dir)
        top = rel.parts[0] if rel.parts else str(rel)
        buckets[top] = buckets.get(top, 0) + 1
    return dict(sorted(buckets.items(), key=lambda x: x[1], reverse=True))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _any_parent_excluded(rel: Path, spec: pathspec.PathSpec) -> bool:
    """
    Return True if any *directory* component of *rel* is matched by *spec*.
    This catches patterns like ``node_modules/`` that should exclude the
    entire subtree, not just the top-level directory entry.
    """
    parts = rel.parts[:-1]  # all directory components, no filename
    for i in range(len(parts)):
        dir_path = "/".join(parts[: i + 1]) + "/"
        if spec.match_file(dir_path):
            return True
    return False
