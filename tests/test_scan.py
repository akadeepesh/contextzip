"""
resolve_files walks the tree once and never matches patterns inside a folder
that's already excluded. These tests hold that fast walk to the answer a
plain file-by-file check gives.
"""

from __future__ import annotations

import os
import time
import unittest
from pathlib import Path

from contextzip.filters import (
    ResolveResult,
    _any_parent_excluded,
    _explicit_include_override,
    _matches_any_prefix,
    build_force_include_spec,
    build_spec,
    resolve_files,
)
from tests.helpers import TempProject


def reference_resolve(project_dir: Path, spec, include_only=None, force_include=None) -> ResolveResult:
    """The obvious implementation: visit every file, check every parent folder."""
    result = ResolveResult()
    for path in sorted(project_dir.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        rel = path.relative_to(project_dir)
        rel_str = rel.as_posix()
        forced = force_include is not None and (
            force_include.match_file(rel_str) or _any_parent_excluded(rel, force_include)
        )
        if not forced:
            excluded = _any_parent_excluded(rel, spec) or spec.match_file(rel_str)
            if excluded and include_only and _explicit_include_override(rel, include_only, spec):
                result.explicitly_included.append(path)
                excluded = False
            if excluded:
                result.excluded.append(path)
                continue
        if include_only and not _matches_any_prefix(rel_str, include_only):
            result.excluded.append(path)
            continue
        result.included.append(path)
    return result


def can_symlink(where: Path) -> bool:
    probe = where / "probe-link"
    try:
        probe.symlink_to(where)
        probe.unlink()
        return True
    except (OSError, NotImplementedError):
        return False


class WalkMatchesReferenceTests(TempProject):
    def setUp(self):
        super().setUp()
        for rel in [
            "README.md",
            "package.json",
            "src/app.ts",
            "src/App.tsx",
            "src/b.ts",
            "src/b/c.ts",
            "src/.env",
            "src/nested/deep/file.py",
            "src/nested/deep/debug.log",
            "node_modules/once/index.js",
            "node_modules/once/package.json",
            "node_modules/.bin/tool",
            "packages/web/node_modules/x/y.js",
            "packages/web/src/page.tsx",
            "dist/bundle.js",
            "dist/node_modules/dep/a.js",
            "docs/guide.md",
            "docs/build/out.html",
            "build/out.txt",
            ".env",
            "Zed.txt",
            "apple.txt",
            ".git/config",
        ]:
            self.write(rel, "x")
        self.write(".gitignore", "build/\n*.tmp\n!docs/build/\n")

    def check(self, *, include_only=None, force=None, gitignore=True):
        spec = build_spec(["base", "node"], None, self.root / ".gitignore" if gitignore else None)
        force_spec = build_force_include_spec(force)
        got = resolve_files(self.root, spec, include_only=include_only, force_include=force_spec)
        want = reference_resolve(self.root, spec, include_only=include_only, force_include=force_spec)
        for name in ("included", "excluded", "explicitly_included"):
            self.assertEqual(getattr(got, name), getattr(want, name), name)
        return got

    def test_plain(self):
        got = self.check()
        rels = [p.relative_to(self.root).as_posix() for p in got.included]
        self.assertIn("src/app.ts", rels)
        self.assertFalse([r for r in rels if "node_modules" in r or r.endswith(".env")])

    def test_without_gitignore(self):
        self.check(gitignore=False)

    def test_include_only(self):
        self.check(include_only=["src"])
        self.check(include_only=["./src/", "docs"])
        self.check(include_only=["src/b"])

    def test_include_brings_back_excluded_paths(self):
        got = self.check(include_only=["dist", ".env", "node_modules/once"])
        rels = {p.relative_to(self.root).as_posix() for p in got.included}
        self.assertEqual(rels, {"dist/bundle.js", ".env", "node_modules/once/index.js", "node_modules/once/package.json"})

    def test_force_include(self):
        self.check(force=["dist/"])
        self.check(force=["*.log", ".env"])
        self.check(force=["node_modules/once/"], include_only=["node_modules", "src"])

    def test_results_are_in_path_order(self):
        got = self.check()
        self.assertEqual(got.included, sorted(got.included))
        self.assertEqual(got.excluded, sorted(got.excluded))

    def test_sizes_are_recorded_for_included_files(self):
        self.write("src/big.txt", "y" * 5000)
        got = self.check()
        self.assertEqual(set(got.sizes), set(got.included))
        self.assertEqual(got.sizes[self.root / "src" / "big.txt"], 5000)
        self.assertEqual(got.size_of(self.root / "src" / "big.txt"), 5000)
        self.assertEqual(got.size_of(self.root / "gone.txt"), 0)

    def test_large_and_binary_files_are_flagged(self):
        self.write("src/blob.dat", b"\x00\x01\x02" * 10)
        self.write("src/huge.txt", "z" * 300)
        spec = build_spec(["base"], None, None)
        got = resolve_files(self.root, spec, large_file_warn_bytes=200)
        self.assertEqual([p.name for p in got.binary_files], ["blob.dat"])
        self.assertEqual([(p.name, n) for p, n in got.large_files], [("huge.txt", 300)])


class SymlinkTests(TempProject):
    def setUp(self):
        super().setUp()
        if not can_symlink(self.tmp):
            self.skipTest("this machine can't create symlinks")
        self.write("src/real.py", "x")
        self.write("node_modules/dep/index.js", "x")
        self.outside = self.write("elsewhere/secret.txt", "x", base=self.tmp)
        self.spec = build_spec(["base", "node"], None, None)

    def rels(self, paths):
        out = []
        for p in paths:
            try:
                out.append(p.relative_to(self.root).as_posix())
            except ValueError:
                out.append(str(p))
        return out

    def test_link_to_a_file_in_the_project_is_matched_at_its_real_path(self):
        (self.root / "alias.py").symlink_to(self.root / "src" / "real.py")
        (self.root / "dep.js").symlink_to(self.root / "node_modules" / "dep" / "index.js")
        got = resolve_files(self.root, self.spec)
        self.assertEqual(self.rels(got.included), ["src/real.py", "src/real.py"])
        self.assertIn("node_modules/dep/index.js", self.rels(got.excluded))

    def test_dangling_link_is_reported_as_skipped(self):
        (self.root / "broken").symlink_to(self.root / "nope.txt")
        got = resolve_files(self.root, self.spec)
        self.assertEqual([(p.name, why) for p, why in got.skipped], [("broken", "dangling symlink")])
        self.assertEqual(self.rels(got.included), ["src/real.py"])

    def test_linked_folders_are_not_followed(self):
        (self.root / "loop").symlink_to(self.root, target_is_directory=True)
        (self.root / "out").symlink_to(self.outside.parent, target_is_directory=True)
        got = resolve_files(self.root, self.spec)
        self.assertEqual(self.rels(got.included), ["src/real.py"])
        self.assertEqual(got.skipped, [])

    def test_link_to_a_file_outside_the_project_is_matched_where_the_link_sits(self):
        (self.root / "src" / "linked.txt").symlink_to(self.outside)
        (self.root / "node_modules" / "linked.txt").symlink_to(self.outside)
        got = resolve_files(self.root, self.spec)
        self.assertEqual(got.included.count(self.outside.resolve()), 1)
        self.assertEqual(got.excluded.count(self.outside.resolve()), 1)


class ScanSpeedTests(TempProject):
    """A dependency folder with thousands of files must not slow the scan down."""

    FILES = 12_000

    def test_big_dependency_tree_is_scanned_quickly(self):
        self.write("src/app.py", "x")
        deps = self.root / "node_modules"
        for d in range(120):
            folder = deps / f"pkg{d}" / "lib" / "inner"
            folder.mkdir(parents=True)
            for f in range(self.FILES // 120):
                (folder / f"m{f}.js").write_bytes(b"x")
        self.write(".gitignore", "\n".join(f"ignored{i}/\n*.ext{i}" for i in range(150)))
        spec = build_spec(["base", "node", "python"], None, self.root / ".gitignore")

        matched: list[str] = []
        original = spec.match_file
        spec.match_file = lambda path, *a, **k: matched.append(path) or original(path, *a, **k)  # type: ignore[method-assign]

        start = time.perf_counter()
        got = resolve_files(self.root, spec)
        elapsed = time.perf_counter() - start

        self.assertEqual(len(got.excluded), self.FILES)
        self.assertEqual([p.name for p in got.included], [".gitignore", "app.py"])
        # The point of the walk: nothing under node_modules/ is ever matched.
        self.assertEqual([m for m in matched if m.startswith("node_modules/") and m != "node_modules/"], [])
        self.assertLess(len(matched), 10)
        # Generous: this takes about 0.1 s on a laptop and took several seconds before.
        self.assertLess(elapsed, 5.0, f"scan took {elapsed:.2f}s")


if __name__ == "__main__":
    unittest.main()
