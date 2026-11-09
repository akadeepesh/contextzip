"""
detector.py — Analyses a project directory and identifies the ecosystem(s) present.

What a stack looks like — its marker files, dependency names and file
types — lives in stacks.py. This module walks the project and matches
folders against that table.

Detection is additive: base rules always apply, and each detected framework
stacks its own rules on top. A monorepo can match multiple ecosystems at once.

Detection isn't limited to the project root. Marker files (package.json,
requirements.txt, etc.) are looked for at the root AND in a shallow scan of
subdirectories, so a layout like:

    root/
      frontend/package.json
      backend/requirements.txt

correctly detects BOTH Node.js and Python and stacks both rule sets — rather
than finding nothing at root and only applying the universal base rules,
which would leave node_modules/ and .venv/ unexcluded except by whatever
.gitignore happens to catch.

The scan is unconditional (not "only if root finds nothing") because a
root-level marker (e.g. shared tooling's package.json) doesn't imply there's
nothing else to find in sibling directories — it only tells you about root.

The scan is bounded and cheap: max depth of 2, and it never descends into
directories that are themselves dependency/build output (node_modules,
.venv, .git, etc.) or that the project's .gitignore leaves out — nothing in
those reaches the ZIP, so nothing in them should decide its rules.

A project with no marker file at all (a folder of scripts) is recognised
by its file types instead, so it still gets its language's rules.
"""

from __future__ import annotations

import fnmatch
import json
import os
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from contextzip import stacks
from contextzip.stacks import STACKS, Stack


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class DetectionResult:
    """Everything the detector knows about a project directory."""

    ecosystems: list[str] = field(default_factory=list)  # e.g. ["Node.js", "Next.js"]
    rule_modules: list[str] = field(default_factory=list)  # e.g. ["base", "node"]
    confidence: str = "low"  # "low" | "medium" | "high"
    # Maps ecosystem name -> relative dir it was found in ("." for root),
    # e.g. {"Node.js": "frontend", "Python": "backend"}. Purely informational,
    # shown in CLI output so a monorepo detection doesn't feel like magic.
    sources: dict[str, str] = field(default_factory=dict)
    # Ecosystems recognised only from the project's file types, because no
    # marker file was found. Also listed in `ecosystems`.
    by_file_type: list[str] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        if not self.ecosystems:
            return "Unknown"
        return " + ".join(self.ecosystems)

    @property
    def is_unknown(self) -> bool:
        return not self.ecosystems


# ---------------------------------------------------------------------------
# Subdirectory scan — bounded, cheap, skips dependency/build dirs
# ---------------------------------------------------------------------------

# Directories we never descend into while scanning for markers: they're
# either dependency trees (huge, and full of nested package.json/pyproject
# files that would produce false positives) or build/VCS output. This
# mirrors the directory names the rule modules themselves exclude.
_SKIP_DIR_NAMES = {
    "node_modules",
    ".git",
    ".svn",
    ".hg",
    ".venv",
    "venv",
    "env",
    "ENV",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    "vendor",
    "target",
    "dist",
    "build",
    "out",
    ".next",
    ".nuxt",
    ".turbo",
    ".vercel",
    ".netlify",
    "coverage",
    ".nyc_output",
    "site-packages",
    ".idea",
    ".vscode",
    ".cache",
    "tmp",
    "temp",
    "Pods",
    "DerivedData",
    "obj",
    "_build",
}

_MAX_SCAN_DEPTH = 2

# A manifest is only skimmed for dependency names; never read a huge one whole.
_MANIFEST_READ_BYTES = 512 * 1024

# File types decide the stack only when they are a real share of the project.
_FILE_TYPE_MIN_FILES = 3
_FILE_TYPE_MIN_SHARE = 0.25


class _Folder:
    """One candidate folder: its entries, listed once, and its manifests, read at most once."""

    def __init__(self, path: Path):
        self.path = path
        self.names: set[str] = set()
        self.files: list[str] = []
        self.subdirs: list[str] = []
        self._text: dict[str, str] = {}
        self._deps: dict[str, set[str]] = {}
        try:
            with os.scandir(path) as it:
                for entry in it:
                    self.names.add(entry.name)
                    try:
                        if entry.is_dir():
                            self.subdirs.append(entry.name)
                        else:
                            self.files.append(entry.name)
                    except OSError:
                        continue
        except OSError:
            pass
        self.subdirs.sort()

    def matching(self, pattern: str) -> list[str]:
        """Entries named by *pattern*: an exact name, or a glob like ``*.csproj``."""
        if any(c in pattern for c in "*?["):
            return sorted(n for n in self.names if fnmatch.fnmatchcase(n, pattern))
        return [pattern] if pattern in self.names else []

    def has(self, marker: str) -> bool:
        if "/" in marker:
            first = marker.split("/", 1)[0]
            return first in self.names and (self.path / marker).exists()
        return bool(self.matching(marker))

    def text(self, name: str) -> str:
        """A manifest's text, lower-cased ("" if it can't be read)."""
        if name not in self._text:
            try:
                with (self.path / name).open("rb") as fh:
                    raw = fh.read(_MANIFEST_READ_BYTES)
                self._text[name] = raw.decode("utf-8", errors="ignore").lower()
            except OSError:
                self._text[name] = ""
        return self._text[name]

    def declared_deps(self, name: str) -> set[str]:
        """Package names a JSON manifest (package.json, composer.json) depends on."""
        if name not in self._deps:
            found: set[str] = set()
            try:
                data = json.loads((self.path / name).read_text(encoding="utf-8", errors="ignore"))
            except (OSError, ValueError):
                data = None
            if isinstance(data, dict):
                for section in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies", "require", "require-dev"):
                    deps = data.get(section)
                    if isinstance(deps, dict):
                        found.update(deps)
            self._deps[name] = found
        return self._deps[name]


def _has_dependency(folder: _Folder, stack: Stack) -> bool:
    for pattern in stacks.manifests_for(stack):
        for name in folder.matching(pattern):
            if stacks.is_json_manifest(name):
                declared = folder.declared_deps(name)
                for dep in stack.deps:
                    if dep.endswith("*"):
                        if any(d.startswith(dep[:-1]) for d in declared):
                            return True
                    elif dep in declared:
                        return True
            else:
                text = folder.text(name)
                if any(dep.lower() in text for dep in stack.deps):
                    return True
    return False


def _matches(folder: _Folder, stack: Stack) -> bool:
    if any(folder.has(m) for m in stack.markers):
        return True
    return bool(stack.deps) and _has_dependency(folder, stack)


def _gitignore_spec(project_dir: Path):
    """The root .gitignore as a spec, or None if there isn't one."""
    from contextzip.filters import _read_gitignore, make_spec

    patterns = _read_gitignore(project_dir / ".gitignore")
    return make_spec(patterns) if patterns else None


def _ignored(rel: str, ignore) -> bool:
    return ignore is not None and ignore.match_file(rel + "/")


def _scan_folders(project_dir: Path, max_depth: int = _MAX_SCAN_DEPTH, ignore=None) -> list[_Folder]:
    """
    The project root plus a bounded, shallow list of subdirectories worth
    checking for ecosystem markers. Never descends into _SKIP_DIR_NAMES,
    hidden folders, or folders the project's .gitignore leaves out.
    """
    root = _Folder(project_dir)
    folders = [root]
    frontier: list[tuple[_Folder, str]] = [(root, "")]
    for _ in range(max(max_depth, 0)):
        next_frontier: list[tuple[_Folder, str]] = []
        for parent, prefix in frontier:
            for name in parent.subdirs:
                if name in _SKIP_DIR_NAMES or name.startswith("."):
                    continue
                rel = prefix + name
                if _ignored(rel, ignore):
                    continue
                child = _Folder(parent.path / name)
                folders.append(child)
                next_frontier.append((child, rel + "/"))
        frontier = next_frontier
    return folders


def _scan_dirs(project_dir: Path, max_depth: int = _MAX_SCAN_DEPTH) -> list[Path]:
    """Paths of the folders :func:`detect` looks at (kept for callers that only need the list)."""
    return [f.path for f in _scan_folders(project_dir, max_depth, _gitignore_spec(project_dir))]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect(project_dir: Path, max_scan_depth: int = _MAX_SCAN_DEPTH) -> DetectionResult:
    """
    Analyse *project_dir* and return a :class:`DetectionResult`.

    Checks project_dir itself plus a shallow, bounded scan of subdirectories
    (see module docstring), so multi-framework monorepos are detected even
    when no marker file sits at the root.

    Always includes "base" in rule_modules.
    """
    result = DetectionResult(rule_modules=["base"])
    ignore = _gitignore_spec(project_dir)
    folders = _scan_folders(project_dir, max_scan_depth, ignore)

    # Packages a monorepo declares (npm/pnpm/yarn workspaces, Cargo, go.work…)
    # are checked whatever their depth — see workspaces.py.
    from contextzip.workspaces import declared_package_dirs

    known = {f.path.resolve() for f in folders}
    for d in declared_package_dirs(project_dir, [f.path for f in folders]):
        if d in known:
            continue
        known.add(d)
        try:
            rel = d.relative_to(project_dir).as_posix()
        except ValueError:
            rel = None
        if rel is not None and any(_ignored("/".join(rel.split("/")[: i + 1]), ignore) for i in range(rel.count("/") + 1)):
            continue
        folders.append(_Folder(d))

    # Every (stack, folder) match, in scan order.
    found: dict[str, list[Path]] = {}
    for folder in folders:
        for stack in STACKS:
            try:
                matched = _matches(folder, stack)
            except Exception:
                matched = False
            if matched:
                found.setdefault(stack.name, []).append(folder.path)

    kept: dict[str, list[Path]] = {}
    for name, dirs in found.items():
        visible = [d for d in dirs if not _hidden(name, d, found)]
        if visible:
            kept[name] = visible

    for folder in folders:
        for stack in STACKS:
            if stack.name in result.ecosystems or folder.path not in kept.get(stack.name, ()):
                continue
            _add(result, stack, _rel(folder.path, project_dir))

    # Each folder a stack was found in gets that stack's own exclusions.
    for name in result.ecosystems:
        stack = stacks.BY_NAME[name]
        if not stack.extra_key:
            continue
        for d in kept[name]:
            rel = _rel(d, project_dir)
            key = stack.extra_key if rel == "." else f"{stack.extra_key}@{rel}"
            if key not in result.rule_modules:
                result.rule_modules.append(key)

    if not result.ecosystems:
        for stack in _by_file_type(folders):
            _add(result, stack, ".")
            result.by_file_type.append(stack.name)

    # Confidence based on cumulative signal weight
    total_weight = sum(stacks.BY_NAME[n].weight for n in result.ecosystems)
    if result.by_file_type:
        result.confidence = "low"
    elif total_weight >= 4:
        result.confidence = "high"
    elif total_weight >= 2:
        result.confidence = "medium"
    elif total_weight >= 1:
        result.confidence = "low"

    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rel(path: Path, project_dir: Path) -> str:
    try:
        rel = path.relative_to(project_dir).as_posix()
    except ValueError:
        return "."
    return rel or "."


def _add(result: DetectionResult, stack: Stack, source: str) -> None:
    result.ecosystems.append(stack.name)
    result.sources[stack.name] = source
    if stack.rules and stack.rules not in result.rule_modules:
        result.rule_modules.append(stack.rules)


def _hidden(name: str, where: Path, found: dict[str, list[Path]]) -> bool:
    """
    True if *name*, found in *where*, goes without saying: a stack that
    hides it was found in the same folder, or *where* is one of that
    stack's platform folders (a Flutter app's ios/ and android/).
    """
    for hider in STACKS:
        if name not in hider.hides:
            continue
        for home in found.get(hider.name, ()):
            if where == home:
                return True
            try:
                inside = where.relative_to(home).parts
            except ValueError:
                continue
            if inside and inside[0] in stacks.PLATFORM_DIRS:
                return True
    return False


def _by_file_type(folders: list[_Folder]) -> list[Stack]:
    """
    Stacks suggested by the project's file types, most files first. Only
    used when no marker file was found anywhere, so a folder of scripts
    still gets its language's rules.
    """
    counts: Counter[str] = Counter()
    for folder in folders:
        for name in folder.files:
            ext = os.path.splitext(name)[1].lower()
            if ext:
                counts[ext] += 1

    per_stack = [(sum(counts[e] for e in s.extensions), s) for s in STACKS if s.extensions]
    total = sum(counts[e] for e in {e for s in STACKS for e in s.extensions})
    if not total:
        return []

    picked: list[Stack] = []
    rule_sets: set[str | None] = set()
    for count, stack in sorted(per_stack, key=lambda pair: -pair[0]):
        if count < _FILE_TYPE_MIN_FILES or count / total < _FILE_TYPE_MIN_SHARE:
            continue
        if stack.rules in rule_sets:
            continue  # Kotlin next to Java: one rule set, one name
        rule_sets.add(stack.rules)
        picked.append(stack)
    return picked
