"""
picker_model.py — everything `contextzip pick` shows, without the screen.

The tree of the project's files, which of them are ticked, why a file is
left out by default, token and size totals, suggestions, filtering, and
the config patterns that reproduce a selection. picker.py draws it with
Textual; this module never imports Textual, so all of it is unit-tested.

How the default selection is decided is shared with the config UI
(filters.classify_with_rules): the stack's rules, the project's .gitignore
files and the config's always_exclude, with always_include beating them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from contextzip.filters import (
    _any_parent_excluded,
    _clean_include_path,
    _explicit_include_override_str,
    _under_any,
    build_spec,
    classify_with_rules,
    make_spec,
    normalize_pattern,
    scan_all_files,
)

LARGE_FILE_BYTES = 1024 * 1024
"""A file this big or bigger is marked "large"."""

ESTIMATE_ABOVE_BYTES = 4 * 1024 * 1024
"""Files bigger than this get a token estimate instead of a count, so the picker never stalls on a huge data file."""

HIDDEN_REASONS = ("rule", "gitignore")
"""Files left out for these reasons stay out when their folder is ticked, and are hidden until `e`."""

REASON_TEXT = {
    "rule": "left out by the rules",
    "gitignore": "ignored by .gitignore",
    "config": "left out by the project config",
}


@dataclass(eq=False)
class Node:
    path: str
    """POSIX path relative to the project root; "" for the root."""
    name: str
    is_dir: bool
    size: int = 0
    """Bytes: a file's size, or everything under a folder."""
    tokens: int = 0
    """A file's tokens (0 until counted); a folder's: the sum of its ticked files."""
    state: str = "out"
    """in, out or partial."""
    children: list[Node] = field(default_factory=list)
    large: bool = False
    reason: str | None = None
    """Why a file is out by default: "gitignore", "rule", "config", or None."""
    depth: int = 0
    parent: Node | None = field(default=None, repr=False)
    files_in: int = 0
    """Folders: ticked files under it."""
    files_total: int = 0
    """Folders: files under it that a tick of the folder covers, plus any ticked ones."""

    @property
    def hidden_by_default(self) -> bool:
        """Out by rule or .gitignore, and not ticked: hidden until `e` shows them."""
        if self.is_dir:
            return self.files_total == 0
        return self.reason in HIDDEN_REASONS and self.state != "in"


def _escape(rel: str) -> str:
    """A path as a literal gitignore pattern: no wildcards, and no backslashes (normalize_pattern turns them into slashes)."""
    return "".join({"[": "[[]", "]": "[]]", "*": "[*]", "?": "[?]"}.get(ch, ch) for ch in rel)


def literal_pattern(rel: str, is_dir: bool) -> str:
    """"/src/app/" or "/README.md": anchored to the project root, matching only this path."""
    return "/" + _escape(rel) + ("/" if is_dir else "")


class PickerModel:
    def __init__(self, project_dir: Path, detection, project_cfg, *, include_only: list[str] | None = None):
        """
        Scan *project_dir* once and decide the starting selection: what a
        normal run would package (*include_only* None), nothing ([]), or
        what `contextzip include PATH…` would package.
        """
        self.project_dir = Path(project_dir)
        self.detection = detection
        self.project_cfg = project_cfg
        self._pruned: list[str] = []
        scanned = scan_all_files(self.project_dir, self._pruned)
        gitignore = self.project_dir / ".gitignore"
        rules = list(detection.rule_modules)
        config_exclude = [normalize_pattern(p) for p in project_cfg.always_exclude]
        config_include = [normalize_pattern(p) for p in project_cfg.always_include]

        # What a run would package, and what the rules alone (no config) would.
        default = classify_with_rules(
            self.project_dir, scanned, rules,
            always_include=config_include, always_exclude=config_exclude, gitignore_path=gitignore,
        )
        self._base = classify_with_rules(self.project_dir, scanned, rules, gitignore_path=gitignore)

        # Each layer on its own, to say why a file is out.
        layers = [
            ("config", make_spec(config_exclude) if config_exclude else None),
            ("gitignore", build_spec([], gitignore_path=gitignore)),
            ("rule", build_spec(rules)),
        ]

        self.root = Node(path="", name=self.project_dir.name, is_dir=True)
        self.nodes: dict[str, Node] = {"": self.root}
        self._abs: dict[str, Path] = {}
        self._base_in: dict[str, bool] = {}
        files: list[Node] = []
        for abs_path, size in scanned:
            try:
                rel_path = abs_path.relative_to(self.project_dir)
            except ValueError:
                continue
            rel = rel_path.as_posix()
            if rel in self.nodes:
                continue  # a symlink and its target: keep one
            included = default.get(abs_path, False)
            reason = None
            if not included:
                for name, spec in layers:
                    if spec is not None and (spec.match_file(rel) or _any_parent_excluded(rel_path, spec)):
                        reason = name
                        break
                reason = reason or "rule"
            node = Node(path=rel, name=rel_path.name, is_dir=False, size=size, large=size >= LARGE_FILE_BYTES, reason=reason)
            self._attach(node)
            self._abs[rel] = abs_path
            self._base_in[rel] = self._base.get(abs_path, False)
            files.append(node)
        self._sort(self.root)

        if include_only is None:
            selected = {f.path for f in files if default.get(self._abs[f.path], False)}
        else:
            named = [_clean_include_path(p) for p in include_only]
            combined = build_spec(rules, extra_exclude=config_exclude or None, gitignore_path=gitignore)
            selected = {
                f.path for f in files
                if named and (
                    (default.get(self._abs[f.path], False) and _under_any(f.path, named))
                    or _explicit_include_override_str(f.path, named, combined)
                )
            }
        self._selected: set[str] = selected
        self._explicit: set[str] = set()
        self._applied: dict[str, dict] = {}
        self._estimated: set[str] = set()
        self.tokens_counted = False
        self.tokens_exact = True
        self._recompute()

    # ── Building the tree ───────────────────────────────────────────────

    def _attach(self, node: Node) -> None:
        parts = node.path.split("/")
        parent = self.root
        for i in range(len(parts) - 1):
            path = "/".join(parts[: i + 1])
            child = self.nodes.get(path)
            if child is None:
                child = Node(path=path, name=parts[i], is_dir=True, depth=i + 1, parent=parent)
                self.nodes[path] = child
                parent.children.append(child)
            parent = child
        node.parent = parent
        node.depth = len(parts)
        parent.children.append(node)
        self.nodes[node.path] = node

    def _sort(self, node: Node) -> None:
        node.children.sort(key=lambda n: (not n.is_dir, n.name.lower(), n.name))
        for child in node.children:
            if child.is_dir:
                self._sort(child)

    def _files_under(self, node: Node) -> Iterable[Node]:
        if not node.is_dir:
            yield node
            return
        stack = [node]
        while stack:
            current = stack.pop()
            for child in current.children:
                if child.is_dir:
                    stack.append(child)
                else:
                    yield child

    def _covered(self, f: Node) -> bool:
        """Whether ticking f's folder brings f in."""
        return f.reason not in HIDDEN_REASONS or f.path in self._explicit

    def _recompute(self, node: Node | None = None) -> None:
        """Folder states, counts, sizes and tokens, from the files up."""
        node = node or self.root
        size = tokens = files_in = files_total = 0
        for child in node.children:
            if child.is_dir:
                self._recompute(child)
                size += child.size
                tokens += child.tokens
                files_in += child.files_in
                files_total += child.files_total
            else:
                ticked = child.path in self._selected
                child.state = "in" if ticked else "out"
                size += child.size
                if ticked:
                    tokens += child.tokens
                    files_in += 1
                if ticked or self._covered(child):
                    files_total += 1
        node.size, node.tokens, node.files_in, node.files_total = size, tokens, files_in, files_total
        if files_in == 0:
            node.state = "out"
        elif files_in >= files_total:
            node.state = "in"
        else:
            node.state = "partial"

    def _node(self, path: str) -> Node:
        node = self.nodes.get(path.strip("/"))
        if node is None:
            raise KeyError(f"not in the picker's tree: {path}")
        return node

    # ── Ticking ─────────────────────────────────────────────────────────

    def toggle(self, path: str) -> None:
        """
        A file flips. A folder that is "in" goes out entirely; otherwise
        everything under it comes in except files left out by the rules or
        .gitignore that you haven't ticked yourself. A folder holding only
        such files brings them all in: ticking it is asking for them.
        """
        node = self._node(path)
        if not node.is_dir:
            if node.path in self._selected:
                self._selected.discard(node.path)
                self._explicit.discard(node.path)
            else:
                self._selected.add(node.path)
                self._explicit.add(node.path)
        elif node.state == "in":
            self._selected.difference_update(f.path for f in self._files_under(node))
        else:
            self._tick_folder(node)
        self._recompute()

    def _tick_folder(self, node: Node) -> None:
        under = list(self._files_under(node))
        covered = [f for f in under if self._covered(f)]
        if not covered:
            covered = under
            self._explicit.update(f.path for f in under)
        self._selected.update(f.path for f in covered)

    def set_all(self, paths: Iterable[str], on: bool) -> None:
        """Tick (or untick) every one of *paths*; a folder follows toggle's rule for what comes in."""
        for path in paths:
            node = self._node(path)
            if on:
                if node.is_dir:
                    self._tick_folder(node)
                else:
                    self._selected.add(node.path)
                    self._explicit.add(node.path)
            else:
                gone = [f.path for f in self._files_under(node)]
                self._selected.difference_update(gone)
        self._recompute()

    def is_selected(self, path: str) -> bool:
        return path in self._selected

    # ── Results ─────────────────────────────────────────────────────────

    def included(self) -> list[Path]:
        """The ticked files, absolute, in path order."""
        return [self._abs[rel] for rel in sorted(self._selected, key=lambda r: (r.lower(), r))]

    def totals(self) -> tuple[int, int, int]:
        """(files, bytes, tokens) of what's ticked."""
        files = [self.nodes[rel] for rel in self._selected]
        return len(files), sum(f.size for f in files), sum(f.tokens for f in files)

    def file_count(self) -> int:
        """Every file in the tree."""
        return len(self._abs)

    # ── Suggestions ─────────────────────────────────────────────────────

    def suggestions(self) -> list[dict]:
        """
        The config UI's suggestions for what's ticked now (webui.suggestions),
        plus the ones already applied, each with "on": True once applied.
        Largest first.
        """
        from contextzip.webui.suggestions import build_suggestions

        current = build_suggestions([(rel, self.nodes[rel].size) for rel in sorted(self._selected)])
        out = [dict(s, on=False) for s in current if s["id"] not in self._applied]
        out += [dict(entry["suggestion"], on=True) for entry in self._applied.values()]
        out.sort(key=lambda s: (-s["bytes"], s["label"]))
        return out

    def apply_suggestion(self, suggestion_id: str, on: bool) -> None:
        """On: untick what the suggestion covers. Off: tick those files again."""
        if not on:
            entry = self._applied.pop(suggestion_id, None)
            if entry is not None:
                self._selected.update(rel for rel in entry["removed"] if rel in self.nodes)
                self._recompute()
            return
        if suggestion_id in self._applied:
            return
        found = next((s for s in self.suggestions() if s["id"] == suggestion_id), None)
        if found is None:
            raise KeyError(f"no such suggestion: {suggestion_id}")
        if found.get("paths"):
            covered = {rel for rel in found["paths"] if rel in self._selected}
        else:
            ext = suggestion_id.split(":", 1)[1]
            covered = {rel for rel in self._selected if rel.lower().endswith(ext)}
        self._selected.difference_update(covered)
        found = {k: v for k, v in found.items() if k != "on"}
        self._applied[suggestion_id] = {"suggestion": found, "removed": covered}
        self._recompute()

    # ── Saving as the project's default ─────────────────────────────────

    def _has_pruned_under(self, folder: str) -> bool:
        prefix = folder + "/"
        return any(p.startswith(prefix) for p in self._pruned)

    def as_patterns(self) -> tuple[list[str], list[str]]:
        """
        (always_include, always_exclude) that reproduce the current
        selection on top of the rules and .gitignore: a folder that is out
        entirely becomes "/dir/" in always_exclude, a folder the rules
        leave out but that is in entirely becomes "/dir/" in always_include,
        and single files are listed only where they differ from what the
        rules give them. Every pattern is anchored and literal.
        """
        include: list[str] = []
        exclude: list[str] = []

        def walk(node: Node) -> None:
            for child in node.children:
                if not child.is_dir:
                    wanted = child.path in self._selected
                    if wanted != self._base_in[child.path]:
                        (include if wanted else exclude).append(literal_pattern(child.path, False))
                    continue
                under = list(self._files_under(child))
                wanted = [f.path in self._selected for f in under]
                base = [self._base_in[f.path] for f in under]
                if wanted == base:
                    continue
                if not any(wanted):
                    exclude.append(literal_pattern(child.path, True))
                    continue
                if all(wanted) and not self._has_pruned_under(child.path):
                    include.append(literal_pattern(child.path, True))
                    continue
                walk(child)

        walk(self.root)
        return include, exclude

    # ── Filtering and rows ──────────────────────────────────────────────

    def filter(self, query: str) -> list[str]:
        """
        Paths whose path contains *query* (case-insensitive), plus their
        folders, so every match stays reachable. In tree order; an empty
        query keeps everything.
        """
        q = query.strip().lower()
        ordered = self._ordered()
        if not q:
            return [n.path for n in ordered]
        keep: set[str] = set()
        for node in ordered:
            if q in node.path.lower():
                keep.add(node.path)
                parent = node.parent
                while parent is not None and parent.path:
                    keep.add(parent.path)
                    parent = parent.parent
        return [n.path for n in ordered if n.path in keep]

    def _ordered(self) -> list[Node]:
        out: list[Node] = []

        def walk(node: Node) -> None:
            for child in node.children:
                out.append(child)
                if child.is_dir:
                    walk(child)

        walk(self.root)
        return out

    def visible_rows(self, expanded: set[str], *, query: str = "", show_hidden: bool = False) -> list[Node]:
        """
        The rows to draw: children of open folders, in tree order. With a
        filter every folder on the way to a match is open. Files left out
        by the rules or .gitignore only appear with *show_hidden*.
        """
        keep = set(self.filter(query)) if query.strip() else None
        rows: list[Node] = []

        def walk(node: Node) -> None:
            for child in node.children:
                if keep is not None and child.path not in keep:
                    continue
                if not show_hidden and child.hidden_by_default:
                    continue
                rows.append(child)
                if child.is_dir and (keep is not None or child.path in expanded):
                    walk(child)

        walk(self.root)
        return rows

    # ── Tokens ──────────────────────────────────────────────────────────

    def compute_tokens(self) -> tuple[dict[str, int], bool]:
        """
        Count every scanned text file (not only the ticked ones, so ticking
        never has to wait), using the workspace's token cache when there is
        one. Files over ESTIMATE_ABOVE_BYTES get an estimate. Safe to run
        in a worker thread: it doesn't touch the tree.
        """
        from contextzip import tokens as tokens_mod
        from contextzip.filters import _is_binary
        from contextzip.packager import _workspace_dir

        cache_dir = None
        try:
            workspace, _is_git = _workspace_dir(self.project_dir)
            if workspace.is_dir():
                cache_dir = workspace / "cache"
        except (OSError, ValueError):
            cache_dir = None

        counted: dict[str, int] = {}
        exact_files: list[Path] = []
        estimated: dict[str, int] = {}
        for rel, abs_path in self._abs.items():
            size = self.nodes[rel].size
            if size > ESTIMATE_ABOVE_BYTES:
                estimated[rel] = tokens_mod._estimate_for(size, abs_path.suffix)
            elif _is_binary(abs_path):
                counted[rel] = 0
            else:
                exact_files.append(abs_path)
        report = tokens_mod.count_files(exact_files, self.project_dir, cache_dir=cache_dir)
        counted.update(report.by_file)
        counted.update({rel: -n - 1 for rel, n in estimated.items()})  # negative marks an estimate
        return counted, report.exact

    def apply_tokens(self, counts: dict[str, int], exact: bool) -> None:
        """Put compute_tokens' result into the tree (on the UI thread)."""
        self._estimated = set()
        for rel, n in counts.items():
            node = self.nodes.get(rel)
            if node is None:
                continue
            if n < 0:
                node.tokens = -n - 1
                self._estimated.add(rel)
            else:
                node.tokens = n
        self.tokens_counted = True
        self.tokens_exact = exact
        self._recompute()

    def count_tokens(self, progress: Callable[[int, int], None] | None = None) -> None:
        """Count every scanned file's tokens and update the totals."""
        total = len(self._abs)
        if progress is not None:
            progress(0, total)
        counts, exact = self.compute_tokens()
        self.apply_tokens(counts, exact)
        if progress is not None:
            progress(total, total)

    def totals_exact(self) -> bool:
        """Whether the ticked total is an exact count (no estimates in it)."""
        return self.tokens_exact and not (self._estimated & self._selected)

    def token_report(self):
        """A TokenReport of what's ticked, for the Fits line."""
        from contextzip.tokens import METHOD_ESTIMATE, METHOD_EXACT, TokenReport

        by_file = {rel: self.nodes[rel].tokens for rel in sorted(self._selected)}
        method = METHOD_EXACT if self.totals_exact() else METHOD_ESTIMATE
        return TokenReport(total=sum(by_file.values()), method=method, by_file=by_file)


__all__ = ["Node", "PickerModel", "literal_pattern", "LARGE_FILE_BYTES", "REASON_TEXT"]
