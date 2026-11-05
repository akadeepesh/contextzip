"""
detector.py — Analyses a project directory and identifies the ecosystem(s) present.

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
.venv, .git, etc.) — the same names those directories' own rule modules
would exclude from the zip, so we skip them before we even look inside.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


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

    @property
    def display_name(self) -> str:
        if not self.ecosystems:
            return "Unknown"
        return " + ".join(self.ecosystems)

    @property
    def is_unknown(self) -> bool:
        return not self.ecosystems


# ---------------------------------------------------------------------------
# Detection rules — ordered from most specific to least specific
# ---------------------------------------------------------------------------


@dataclass
class _Rule:
    """A single detection rule."""

    name: str  # Human-readable ecosystem name  e.g. "Next.js"
    module: str  # Rule module key               e.g. "node"
    check: Callable[[Path], bool]
    weight: int = 1  # Higher = stronger signal


_RULES: list[_Rule] = [
    # ── Next.js (must come before generic Node.js) ────────────────────────
    _Rule(
        name="Next.js",
        module="node",
        check=lambda p: (
            _file_exists(p, "next.config.js")
            or _file_exists(p, "next.config.ts")
            or _file_exists(p, "next.config.mjs")
            or _dep_present(p, "next")
        ),
        weight=3,
    ),
    # ── Node.js / generic JS+TS ───────────────────────────────────────────
    _Rule(
        name="Node.js",
        module="node",
        check=lambda p: _file_exists(p, "package.json"),
        weight=2,
    ),
    # ── Django (must come before generic Python) ──────────────────────────
    _Rule(
        name="Django",
        module="python",
        check=lambda p: _file_exists(p, "manage.py") or _dep_present_py(p, "django"),
        weight=3,
    ),
    # ── FastAPI ───────────────────────────────────────────────────────────
    _Rule(
        name="FastAPI",
        module="python",
        check=lambda p: _dep_present_py(p, "fastapi"),
        weight=3,
    ),
    # ── Generic Python ────────────────────────────────────────────────────
    _Rule(
        name="Python",
        module="python",
        check=lambda p: (
            _file_exists(p, "requirements.txt")
            or _file_exists(p, "pyproject.toml")
            or _file_exists(p, "setup.py")
            or _file_exists(p, "setup.cfg")
            or _file_exists(p, "Pipfile")
        ),
        weight=2,
    ),
    # ── Rust ──────────────────────────────────────────────────────────────
    _Rule(
        name="Rust",
        module="rust",
        check=lambda p: _file_exists(p, "Cargo.toml"),
        weight=3,
    ),
    # ── Go ────────────────────────────────────────────────────────────────
    _Rule(
        name="Go",
        module="go",
        check=lambda p: _file_exists(p, "go.mod"),
        weight=3,
    ),
    # ── Ruby ─────────────────────────────────────────────────────────────
    _Rule(
        name="Ruby",
        module="ruby",
        check=lambda p: _file_exists(p, "Gemfile"),
        weight=2,
    ),
    # ── Spring Boot (must come before generic Java) ───────────────────────
    _Rule(
        name="Spring Boot",
        module="java",
        check=lambda p: _jvm_build_mentions(p, "spring-boot", "org.springframework.boot"),
        weight=3,
    ),
    # ── Java (Maven / Gradle) ─────────────────────────────────────────────
    _Rule(
        name="Java",
        module="java",
        check=lambda p: any(_file_exists(p, f) for f in _JVM_BUILD_FILES),
        weight=3,
    ),
    # ── Kotlin (on top of Java: same build tools, same rules) ─────────────
    _Rule(
        name="Kotlin",
        module="java",
        check=lambda p: _jvm_build_mentions(p, "kotlin"),
        weight=2,
    ),
]

# Files that mark a Maven or Gradle project.
_JVM_BUILD_FILES = (
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "settings.gradle",
    "settings.gradle.kts",
)


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
}

_MAX_SCAN_DEPTH = 2


def _scan_dirs(project_dir: Path, max_depth: int = _MAX_SCAN_DEPTH) -> list[Path]:
    """
    Return [project_dir] plus a bounded, shallow list of subdirectories worth
    checking for ecosystem markers. Never descends into _SKIP_DIR_NAMES.
    """
    dirs = [project_dir]
    if max_depth <= 0:
        return dirs

    frontier = [project_dir]
    for _ in range(max_depth):
        next_frontier: list[Path] = []
        for d in frontier:
            try:
                children = [c for c in d.iterdir() if c.is_dir()]
            except OSError:
                continue
            for child in children:
                if child.name in _SKIP_DIR_NAMES or child.name.startswith("."):
                    continue
                dirs.append(child)
                next_frontier.append(child)
        frontier = next_frontier

    return dirs


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

    seen_modules: set[str] = {"base"}
    seen_names: set[str] = set()
    total_weight = 0

    candidate_dirs = _scan_dirs(project_dir, max_scan_depth)
    # Packages a monorepo declares (npm/pnpm/yarn workspaces, Cargo, go.work…)
    # are checked whatever their depth — see workspaces.py.
    from contextzip.workspaces import declared_package_dirs

    known = {d.resolve() for d in candidate_dirs}
    for d in declared_package_dirs(project_dir, candidate_dirs):
        if d not in known:
            known.add(d)
            candidate_dirs.append(d)

    for candidate_dir in candidate_dirs:
        for rule in _RULES:
            if rule.name in seen_names:
                continue  # already found this ecosystem elsewhere; don't re-check
            try:
                matched = rule.check(candidate_dir)
            except Exception:
                matched = False

            if matched:
                result.ecosystems.append(rule.name)
                seen_names.add(rule.name)
                try:
                    rel = candidate_dir.relative_to(project_dir)
                    result.sources[rule.name] = str(rel) if str(rel) != "." else "."
                except ValueError:
                    result.sources[rule.name] = "."

                if rule.module not in seen_modules:
                    result.rule_modules.append(rule.module)
                    seen_modules.add(rule.module)
                total_weight += rule.weight

    # Confidence based on cumulative signal weight
    if total_weight >= 4:
        result.confidence = "high"
    elif total_weight >= 2:
        result.confidence = "medium"
    elif total_weight >= 1:
        result.confidence = "low"

    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _file_exists(project_dir: Path, filename: str) -> bool:
    return (project_dir / filename).exists()


def _dep_present(project_dir: Path, dep: str) -> bool:
    """Check if *dep* appears in package.json dependencies."""
    pkg = project_dir / "package.json"
    if not pkg.exists():
        return False
    try:
        import json

        data = json.loads(pkg.read_text(encoding="utf-8", errors="ignore"))
        all_deps = {
            **data.get("dependencies", {}),
            **data.get("devDependencies", {}),
            **data.get("peerDependencies", {}),
        }
        return dep in all_deps
    except Exception:
        return False


def _jvm_build_mentions(project_dir: Path, *needles: str) -> bool:
    """Check if any of *needles* appears in pom.xml or a Gradle build script."""
    for name in ("pom.xml", "build.gradle", "build.gradle.kts"):
        f = project_dir / name
        if not f.exists():
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore").lower()
            if any(n in text for n in needles):
                return True
        except OSError:
            pass
    return False


def _dep_present_py(project_dir: Path, dep: str) -> bool:
    """Check if *dep* appears in requirements.txt or pyproject.toml."""
    req = project_dir / "requirements.txt"
    if req.exists():
        try:
            content = req.read_text(encoding="utf-8", errors="ignore").lower()
            if dep.lower() in content:
                return True
        except Exception:
            pass

    ppt = project_dir / "pyproject.toml"
    if ppt.exists():
        try:
            content = ppt.read_text(encoding="utf-8", errors="ignore").lower()
            if dep.lower() in content:
                return True
        except Exception:
            pass

    return False
