"""
detector.py — Analyses a project directory and identifies the ecosystem(s) present.

Detection is additive: base rules always apply, and each detected framework
stacks its own rules on top. A monorepo can match multiple ecosystems at once.
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

    ecosystems: list[str] = field(default_factory=list)   # e.g. ["Node.js", "Next.js"]
    rule_modules: list[str] = field(default_factory=list)  # e.g. ["base", "node"]
    confidence: str = "low"                                # "low" | "medium" | "high"

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
    name: str              # Human-readable ecosystem name  e.g. "Next.js"
    module: str            # Rule module key               e.g. "node"
    check: Callable[[Path], bool]
    weight: int = 1        # Higher = stronger signal


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
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect(project_dir: Path) -> DetectionResult:
    """
    Analyse *project_dir* and return a :class:`DetectionResult`.

    Always includes "base" in rule_modules.
    """
    result = DetectionResult(rule_modules=["base"])

    seen_modules: set[str] = {"base"}
    seen_names: set[str] = set()
    total_weight = 0

    for rule in _RULES:
        try:
            matched = rule.check(project_dir)
        except Exception:
            matched = False

        if matched:
            if rule.name not in seen_names:
                result.ecosystems.append(rule.name)
                seen_names.add(rule.name)
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