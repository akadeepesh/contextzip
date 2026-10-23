"""
root.py — Find the project root that every contextzip command anchors on.

contextzip should behave the same no matter which folder of a project you
run it from — like git does. Running `cz` inside `frontend/` of a monorepo
must not create `frontend/.contextzip/`, must not write manifests with
paths relative to `frontend/`, and must not lose track of `backend/`.

So every command first resolves one project root and works from there:
packaging, manifests, apply-zip, the config UI and watch all use it. The
folder you ran the command from is kept separately (`ProjectRoot.cwd`) for
the few things that genuinely need it — `--here`, and the directory
`watch` starts your dev server in.

How the root is chosen, first match wins:

  1. CONTEXTZIP_ROOT environment variable — an explicit override for CI,
     scripts, or unusual layouts.
  2. The OUTERMOST ancestor (not above your home folder) that is a git
     repository — `.git` as a folder *or* a file, so worktrees and
     submodules count — or that holds a `.contextzip/config.json`.
     Outermost, not nearest, on purpose: scaffolding tools such as
     create-next-app and create-vite run `git init` inside the folder they
     generate, which leaves a stray `frontend/.git` inside the real
     monorepo. The nearest-repo rule picked that up and anchored
     everything on `frontend/`; the outermost rule anchors on the monorepo
     the person actually works in.
  3. The outermost ancestor (again, below home) with an existing
     `.contextzip/` workspace — so non-git projects still find their
     workspace from a subfolder after the first run.
  4. The current folder.

Your home folder and anything above it are never chosen. A dotfiles repo
in ~ would otherwise make every project under it part of one giant "repo".
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT_ENV_VAR = "CONTEXTZIP_ROOT"

_WORKSPACE_DIRNAME = ".contextzip"


@dataclass(frozen=True)
class ProjectRoot:
    """Where a contextzip command should anchor, and where it was run from."""

    root: Path
    """The project root every path, manifest and workspace is relative to."""

    cwd: Path
    """The folder the command was run from."""

    kind: str
    """How the root was found: "env", "git", "config", "workspace" or "cwd"."""

    suggested_root: Path | None = None
    """A parent folder that looks like the real root of a monorepo without
    git at the top (see `_suggest_parent`). Only ever suggested, never used
    until the person agrees and it gets a .contextzip/config.json."""

    submodule: Path | None = None
    """The git submodule the command was run inside, if any."""

    @property
    def is_git(self) -> bool:
        return self.kind == "git"

    @property
    def subdir(self) -> str:
        """cwd relative to root as a POSIX path, "" when they're the same."""
        try:
            rel = self.cwd.relative_to(self.root).as_posix()
        except ValueError:
            return ""
        return "" if rel == "." else rel

    @property
    def in_subdir(self) -> bool:
        return bool(self.subdir)


def _home() -> Path | None:
    try:
        return Path.home().resolve()
    except (RuntimeError, OSError):
        return None


def _ancestors(start: Path) -> list[Path]:
    """start and its parents, nearest first, stopping before home and above."""
    home = _home()
    blocked: set[Path] = set()
    if home is not None:
        blocked.add(home)
        blocked.update(home.parents)

    out: list[Path] = []
    for p in [start, *start.parents]:
        if p in blocked:
            break
        out.append(p)
    return out


def is_git_root(path: Path) -> bool:
    """True if *path* has a .git folder or a .git file (worktree/submodule)."""
    marker = path / ".git"
    try:
        return marker.is_dir() or marker.is_file()
    except OSError:  # a folder we may not look inside, e.g. /tmp/systemd-private-*
        return False


def find_git_root(start: Path) -> Path | None:
    """
    The outermost git repository containing *start* (not above home), or
    None. See the module docstring for why outermost rather than nearest.
    """
    start = start.resolve()
    found: Path | None = None
    for p in _ancestors(start):
        if is_git_root(p):
            found = p
    return found


def find_project_root(start: Path | None = None) -> ProjectRoot:
    """Resolve the project root for a command run from *start* (default: cwd)."""
    cwd = (start or Path.cwd()).resolve()

    env = os.environ.get(ROOT_ENV_VAR, "").strip()
    if env:
        root = Path(env).expanduser().resolve()
        if root.is_dir():
            return ProjectRoot(root=root, cwd=cwd, kind="env")

    ancestors = _ancestors(cwd)

    submodule = _enclosing_submodule(cwd)

    best: tuple[Path, str] | None = None
    for p in ancestors:
        if is_git_root(p):
            best = (p, "git")
        elif (p / _WORKSPACE_DIRNAME / "config.json").is_file():
            best = (p, "config")
    if best is not None:
        root, kind = best
        suggested = _suggest_parent(root) if kind == "git" else None
        return ProjectRoot(root=root, cwd=cwd, kind=kind, suggested_root=suggested, submodule=submodule)

    workspace_root: Path | None = None
    for p in ancestors:
        if (p / _WORKSPACE_DIRNAME).is_dir():
            workspace_root = p
    if workspace_root is not None:
        return ProjectRoot(root=workspace_root, cwd=cwd, kind="workspace")

    return ProjectRoot(root=cwd, cwd=cwd, kind="cwd", suggested_root=_suggest_parent(cwd))


# Files that mark a folder as a project of its own.
_PROJECT_MARKERS = (
    "package.json", "pyproject.toml", "requirements.txt", "setup.py", "Pipfile",
    "Cargo.toml", "go.mod", "Gemfile", "manage.py", "pom.xml", "build.gradle", "composer.json",
)
# Folders that usually hold many unrelated projects, not one monorepo.
_GENERIC_PARENTS = {
    "code", "projects", "project", "dev", "src", "repos", "repositories", "git", "github",
    "workspace", "workspaces", "work", "sites", "documents", "desktop", "downloads", "sandbox",
}


def _is_project(folder: Path) -> bool:
    try:
        return is_git_root(folder) or any((folder / m).is_file() for m in _PROJECT_MARKERS)
    except OSError:
        return False


def _suggest_parent(root: Path) -> Path | None:
    """
    The parent of *root* when it looks like one project split into a few
    sibling repos — shop/frontend/.git + shop/backend/.git with nothing at
    shop/ itself. Deliberately cautious, because a wrong guess would merge
    unrelated projects:
      - the parent must be below home and have no .git or workspace,
      - it must hold 2–5 project folders (more looks like a projects folder),
      - its name mustn't be a generic container like "code" or "projects",
      - and the person mustn't have declined it before.
    """
    parent = root.parent
    if parent == root or parent not in _ancestors(root):
        return None
    if is_git_root(parent) or (parent / _WORKSPACE_DIRNAME).exists():
        return None
    if parent.name.lower() in _GENERIC_PARENTS:
        return None
    try:
        children = [c for c in parent.iterdir() if c.is_dir() and not c.name.startswith(".")]
    except OSError:
        return None
    if len(children) > 40:
        return None
    projects = [c for c in children if _is_project(c)]
    if not 2 <= len(projects) <= 5:
        return None
    try:
        from contextzip.config import get_declined_roots

        if str(parent) in get_declined_roots():
            return None
    except Exception:
        pass
    return parent


def _enclosing_submodule(cwd: Path) -> Path | None:
    """
    The registered git submodule *cwd* is inside, if any: a folder whose
    .git is a file pointing into a parent repo's .git/modules/.
    """
    for p in _ancestors(cwd):
        marker = p / ".git"
        if marker.is_file():
            try:
                text = marker.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return None
            gitdir = text.strip().partition("gitdir:")[2].strip().replace("\\", "/")
            return p if "/modules/" in gitdir or gitdir.startswith("modules/") else None
        if marker.is_dir():
            return None
    return None


def nested_git_roots(root: Path, max_depth: int = 3) -> list[Path]:
    """
    Git repositories nested inside *root* (embedded repos and submodules),
    nearest first, skipping heavy folders. Used by --git-changes, because
    the outer repo's `git status` reports an embedded repo as one opaque
    entry and never lists the changes inside it.
    """
    from contextzip.filters import _ALWAYS_PRUNED_DIRNAMES  # lazy: avoids an import cycle

    found: list[Path] = []
    root = root.resolve()

    def walk(directory: Path, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            children = sorted(p for p in directory.iterdir() if p.is_dir())
        except OSError:
            return
        for child in children:
            name = child.name
            if name == ".git" or name in _ALWAYS_PRUNED_DIRNAMES or name == _WORKSPACE_DIRNAME:
                continue
            if child.is_symlink():
                continue
            if is_git_root(child):
                found.append(child)
            walk(child, depth + 1)

    walk(root, 1)
    return found


ROOT_KIND_LABELS = {
    "env": "set by CONTEXTZIP_ROOT",
    "git": "git repository",
    "config": "has .contextzip/config.json",
    "workspace": "has a .contextzip/ workspace",
    "cwd": "current folder — no repo or workspace found above it",
}

_GLOB_CHARS = set("*?[")


def to_root_relative(items: list[str], project_root: ProjectRoot, *, patterns: bool = False) -> list[str]:
    """
    Translate paths typed on the command line from "relative to where I am"
    to "relative to the project root", the way `git add` treats paths.

    Run from the root, nothing changes. Run from `frontend/`:
      - `src/` becomes `frontend/src/`
      - `../backend/api` becomes `backend/api`
      - an absolute path inside the project becomes root-relative
      - `.` means the whole folder you're in
      - with patterns=True (exclude patterns), a bare glob such as `*.log`
        is left alone, since gitignore syntax already matches it anywhere
    A path that exists only under the root (not under the current folder) is
    kept as typed, so root-relative paths keep working from a subfolder.
    A path that resolves to the project root itself is dropped: it means
    "everything", which is what no filter already does.
    """
    out: list[str] = []
    for raw in items:
        item = raw.replace("\\", "/")
        while item.startswith("./"):
            item = item[2:]
        bare = item.rstrip("/")
        trailing = "/" if item.endswith("/") and bare else ""
        if not bare:
            bare = "."

        is_abs = Path(bare).is_absolute()
        if not project_root.in_subdir and not is_abs and not bare.startswith(".."):
            if bare != ".":
                out.append(item)
            continue
        if patterns and "/" not in bare and any(c in _GLOB_CHARS for c in bare):
            out.append(item)
            continue

        if is_abs:
            target = Path(bare)
        else:
            from_cwd = project_root.cwd / bare
            from_root = project_root.root / bare
            use_cwd = bare.startswith("..") or bare == "." or from_cwd.exists() or not from_root.exists()
            target = from_cwd if use_cwd else from_root

        try:
            rel = target.resolve().relative_to(project_root.root).as_posix()
        except ValueError:
            out.append(item)  # outside the project: leave it, it simply matches nothing
            continue
        if rel != ".":
            out.append(rel + trailing)
    return out
