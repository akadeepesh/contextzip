"""
workspaces.py — Packages a monorepo declares, at any depth.

Stack detection only looks a couple of folders deep (scan_depth), which
misses layouts like packages/*/apps/web. But monorepo tools already list
their packages, so read those lists instead of guessing:

    package.json      "workspaces": [...] or {"packages": [...]}   npm, yarn, bun
    pnpm-workspace.yaml   packages: - 'apps/*'                     pnpm
    lerna.json        "packages": [...]                             lerna
    Cargo.toml        [workspace] members = [...]                   cargo
    go.work           use ./a  /  use ( ./a ./b )                   go
    pom.xml           <modules><module>a</module></modules>         maven
    settings.gradle   include 'a', ':b:c'  /  include(":a")         gradle

Parsed without YAML or TOML libraries (Python 3.9 has neither built in) —
only the simple, list-of-globs shapes these files use in practice.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_MAX_DIRS = 500
_SKIP = {"node_modules", ".git", "dist", "build", "target", "vendor", ".venv", "venv", ".next"}


def _from_package_json(folder: Path) -> list[str]:
    try:
        data = json.loads((folder / "package.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return []
    ws = data.get("workspaces") if isinstance(data, dict) else None
    if isinstance(ws, dict):
        ws = ws.get("packages")
    return [w for w in ws if isinstance(w, str)] if isinstance(ws, list) else []


def _from_lerna(folder: Path) -> list[str]:
    try:
        data = json.loads((folder / "lerna.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return []
    pkgs = data.get("packages") if isinstance(data, dict) else None
    return [p for p in pkgs if isinstance(p, str)] if isinstance(pkgs, list) else []


def _from_pnpm(folder: Path) -> list[str]:
    try:
        text = (folder / "pnpm-workspace.yaml").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    out: list[str] = []
    in_packages = False
    for line in text.splitlines():
        if re.match(r"^packages\s*:", line):
            in_packages = True
            continue
        if in_packages:
            if line and not line[0].isspace() and not line.lstrip().startswith("-"):
                break  # next top-level key
            m = re.match(r"^\s*-\s*['\"]?([^'\"#]+?)['\"]?\s*(?:#.*)?$", line)
            if m:
                out.append(m.group(1).strip())
    return out


def _from_cargo(folder: Path) -> list[str]:
    try:
        text = (folder / "Cargo.toml").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    section = re.search(r"^\[workspace\]\s*$(.*?)(?=^\[|\Z)", text, re.MULTILINE | re.DOTALL)
    if not section:
        return []
    members = re.search(r"members\s*=\s*\[(.*?)\]", section.group(1), re.DOTALL)
    return re.findall(r"['\"]([^'\"]+)['\"]", members.group(1)) if members else []


def _from_go_work(folder: Path) -> list[str]:
    try:
        text = (folder / "go.work").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    out: list[str] = []
    for block in re.findall(r"^use\s*\((.*?)\)", text, re.MULTILINE | re.DOTALL):
        out += [line.split("//")[0].strip() for line in block.splitlines()]
    out += re.findall(r"^use\s+([^\s(]\S*)", text, re.MULTILINE)
    return [o for o in out if o]


def _from_maven(folder: Path) -> list[str]:
    try:
        text = (folder / "pom.xml").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    out: list[str] = []
    for block in re.findall(r"<modules>(.*?)</modules>", text, re.DOTALL):
        out += [m.strip() for m in re.findall(r"<module>([^<]+)</module>", block)]
    return [o for o in out if o]


def _from_gradle_settings(folder: Path) -> list[str]:
    text = ""
    for name in ("settings.gradle", "settings.gradle.kts"):
        try:
            text = (folder / name).read_text(encoding="utf-8")
            break
        except (OSError, UnicodeDecodeError):
            continue
    if not text:
        return []
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"//[^\n]*", "", text)
    out: list[str] = []
    # include 'app', ':libs:core'   /   include(":app", ":lib")   /   includeBuild("tools")
    for kind, args in re.findall(r"\b(include|includeBuild)\b\s*\(?((?:\s*['\"][^'\"]+['\"]\s*,?)+)", text):
        for name in re.findall(r"['\"]([^'\"]+)['\"]", args):
            # Gradle project paths use ':' as the separator; includeBuild takes a folder.
            out.append(name if kind == "includeBuild" else name.strip(":").replace(":", "/"))
    return [o for o in out if o]


_READERS = (
    _from_package_json,
    _from_pnpm,
    _from_lerna,
    _from_cargo,
    _from_go_work,
    _from_maven,
    _from_gradle_settings,
)


def _expand(folder: Path, pattern: str, root: Path) -> list[Path]:
    pattern = pattern.strip().replace("\\", "/")
    if not pattern or pattern.startswith("!"):
        return []
    while pattern.startswith("./"):
        pattern = pattern[2:]
    pattern = pattern.rstrip("/")
    # "**" would walk into dependency folders; two levels covers real layouts.
    variants = [pattern.replace("**", "*"), pattern.replace("**", "*/*")] if "**" in pattern else [pattern]
    out: list[Path] = []
    for variant in variants:
        try:
            matches = folder.glob(variant) if any(c in variant for c in "*?[") else [folder / variant]
            for m in matches:
                if not m.is_dir() or m.resolve() == root or any(part in _SKIP for part in m.relative_to(root).parts):
                    continue
                out.append(m.resolve())
        except (OSError, ValueError, NotImplementedError):
            continue
    return out


def declared_package_dirs(root: Path, folders: list[Path] | None = None) -> list[Path]:
    """
    Every package folder a workspace file in *folders* (default: just
    *root*) declares, resolved, inside *root*, deduplicated.
    """
    root = root.resolve()
    seen: set[Path] = set()
    out: list[Path] = []
    for folder in folders or [root]:
        for reader in _READERS:
            for pattern in reader(folder):
                for d in _expand(folder, pattern, root):
                    try:
                        d.relative_to(root)
                    except ValueError:
                        continue
                    if d not in seen:
                        seen.add(d)
                        out.append(d)
                        if len(out) >= _MAX_DIRS:
                            return out
    return out
