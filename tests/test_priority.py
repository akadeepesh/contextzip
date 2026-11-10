"""
Who wins when the sources of include/exclude rules disagree. Highest first:

  1. the command      --exclude / exclude, then --include / include
  2. the project config   always_include, then always_exclude
  3. .gitignore
  4. the built-in rules
"""

from __future__ import annotations

import json
import os

from click.testing import CliRunner

from contextzip import cli, get_files
from tests.helpers import TempProject

GITIGNORE = "ignored/\nsrc/local.ts\n!keep.log\n.env*\n!.env.example\n"


class PriorityTests(TempProject):
    def setUp(self) -> None:
        super().setUp()
        self._cwd = os.getcwd()
        os.chdir(self.root)
        for rel in [
            "package.json",
            "src/app.ts",
            "src/.env",
            "src/local.ts",  # ignored by .gitignore
            "src/gen/out.ts",
            ".env",
            ".env.example",  # the built-in rules drop it, .gitignore brings it back
            "docs/guide.md",
            "dist/bundle.js",  # built-in Node.js rule
            "ignored/note.txt",  # folder ignored by .gitignore
            "keep.log",  # the built-in rules drop *.log, .gitignore brings it back
            "CHANGELOG.md",  # built-in rule
        ]:
            self.write(rel, "A=1\n")
        self.write(".gitignore", GITIGNORE)

    def tearDown(self) -> None:
        os.chdir(self._cwd)
        super().tearDown()

    def config(self, **cfg) -> None:
        self.write(".contextzip/config.json", json.dumps(cfg))

    def packed(self, *args: str) -> set[str]:
        result = CliRunner().invoke(cli.main, [*args, "--dry-run", "--json"], catch_exceptions=False)
        data = json.loads([line for line in result.stdout.splitlines() if line.strip()][0])
        self.assertTrue(data["ok"], data)
        return set(data["files"]["included_files"])

    # ── .gitignore over the built-in rules ────────────────────────────────

    def test_defaults(self):
        got = self.packed()
        self.assertEqual(got, {".gitignore", ".env.example", "docs/guide.md", "keep.log", "package.json", "src/app.ts", "src/gen/out.ts"})

    def test_gitignore_negation_brings_back_what_a_built_in_rule_drops(self):
        got = self.packed()
        self.assertIn("keep.log", got)
        self.assertIn(".env.example", got)
        self.assertNotIn(".env", got)

    # ── the config over .gitignore and the built-in rules ─────────────────

    def test_config_include_beats_gitignore_and_built_in_rules(self):
        self.config(always_include=["ignored/", "CHANGELOG.md", "src/local.ts"])
        got = self.packed()
        self.assertLessEqual({"ignored/note.txt", "CHANGELOG.md", "src/local.ts"}, got)

    def test_config_exclude_applies(self):
        self.config(always_exclude=["src/gen/"])
        self.assertNotIn("src/gen/out.ts", self.packed())

    # ── the command over everything ───────────────────────────────────────

    def test_named_file_is_included_whatever_excludes_it(self):
        self.config(always_exclude=["*.md"])
        got = self.packed("include", ".env", "src/local.ts", "CHANGELOG.md", "docs/guide.md")
        self.assertEqual(got, {".env", "src/local.ts", "CHANGELOG.md", "docs/guide.md"})

    def test_named_folder_is_included_whatever_excludes_it(self):
        self.config(always_exclude=["src/gen/"])
        got = self.packed("include", "ignored", "dist", "src/gen")
        self.assertEqual(got, {"ignored/note.txt", "dist/bundle.js", "src/gen/out.ts"})

    def test_including_a_folder_scopes_the_run_and_keeps_the_rules_inside_it(self):
        self.config(always_exclude=["src/gen/"], always_include=["CHANGELOG.md"])
        got = self.packed("include", "src")
        self.assertEqual(got, {"src/app.ts"})

    def test_command_exclude_beats_config_include(self):
        self.config(always_include=["docs/", "ignored/"])
        self.assertIn("docs/guide.md", self.packed())
        self.assertNotIn("docs/guide.md", self.packed("exclude", "docs/"))
        got = self.packed("-e", "ignored/note.txt")
        self.assertNotIn("ignored/note.txt", got)
        self.assertIn("docs/guide.md", got)

    def test_command_exclude_beats_a_gitignore_negation(self):
        self.assertNotIn("keep.log", self.packed("-e", "keep.log"))

    def test_python_api_follows_the_same_order(self):
        self.config(always_include=["docs/"])
        rels = lambda c: {p.relative_to(self.root).as_posix() for p in c.files}  # noqa: E731
        self.assertIn("docs/guide.md", rels(get_files(self.root)))
        self.assertNotIn("docs/guide.md", rels(get_files(self.root, exclude=["docs/"])))
        self.assertIn(".env", rels(get_files(self.root, include=[".env"])))


class GitChangesPriorityTests(TempProject):
    def test_command_exclude_beats_config_include(self):
        from contextzip.filters import build_force_include_spec, make_spec, resolve_files_from_git

        files = [self.write("docs/guide.md", "x"), self.write("src/app.ts", "x")]
        force = build_force_include_spec(["docs/"])
        kept = resolve_files_from_git(files, self.root, force_include=force)
        self.assertEqual(len(kept.included), 2)
        dropped = resolve_files_from_git(files, self.root, force_include=force, command_exclude=make_spec(["docs/"]))
        self.assertEqual([p.name for p in dropped.included], ["app.ts"])
