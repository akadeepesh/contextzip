from __future__ import annotations

import json
import os
from unittest import mock

from click.testing import CliRunner

from contextzip import cli
from contextzip.remote import FetchedRemote, RemoteSpec
from contextzip.tokens import estimate_tokens, format_tokens
from tests.helpers import TempProject


class CliTestCase(TempProject):
    def setUp(self) -> None:
        super().setUp()
        self._cwd = os.getcwd()
        os.chdir(self.root)
        self.write("package.json", '{"name": "demo"}\n')
        self.write("src/index.ts", "export const a = 1;\n")
        self.write("node_modules/dep/index.js", "module.exports = 1;\n")

    def tearDown(self) -> None:
        os.chdir(self._cwd)
        super().tearDown()

    def run_json(self, *args: str) -> tuple[int, dict]:
        result = CliRunner().invoke(cli.main, [*args, "--json"], catch_exceptions=False)
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        self.assertEqual(len(lines), 1, f"expected one JSON line, got: {result.stdout!r}")
        return result.exit_code, json.loads(lines[0])


class PackageJsonTests(CliTestCase):
    def test_package_prints_one_object(self):
        code, data = self.run_json("--no-clipboard")
        self.assertEqual(code, 0)
        self.assertTrue(data["ok"])
        self.assertEqual(data["command"], "package")
        self.assertIn("Node.js", data["detection"]["ecosystems"])
        self.assertEqual(data["files"]["included_files"], ["package.json", "src/index.ts"])
        self.assertTrue(os.path.isfile(data["zip"]["path"]))
        self.assertEqual(data["zip"]["files"], 2)
        self.assertGreater(data["zip"]["estimated_tokens"], 0)
        self.assertIsNone(data["clipboard"])
        self.assertTrue(any(entry["level"] == "ok" for entry in data["log"]))

    def test_dry_run_writes_no_zip(self):
        code, data = self.run_json("-n")
        self.assertEqual(code, 0)
        self.assertTrue(data["dry_run"])
        self.assertIsNone(data["zip"])

    def test_nothing_to_package_is_a_failure(self):
        code, data = self.run_json("include", "missing/")
        self.assertNotEqual(code, 0)
        self.assertFalse(data["ok"])
        self.assertIn("Nothing to compress", data["error"])

    def test_ref_without_a_repository_is_an_error(self):
        code, data = self.run_json("-n", "--ref", "main")
        self.assertNotEqual(code, 0)
        self.assertIn("--from", data["error"])


class RemoteCliTests(CliTestCase):
    """The GitHub fetch itself is covered in test_remote; here it's faked."""

    def fake_fetch(self, spec, ref=None):
        repo = self.tmp / "cache" / "demo"
        if not repo.exists():
            self.write("README.md", "# demo\n", base=repo)
            self.write("lib/core.py", "x = 1\n", base=repo)
            self.write("pyproject.toml", "[project]\nname = 'demo'\n", base=repo)
        self.fetched_with = (spec.slug, ref)
        return FetchedRemote(spec=spec, sha="c" * 40, ref=ref, project_dir=repo, zip_path=repo.parent / "demo.zip", cached=True)

    def invoke(self, *args: str) -> tuple[int, dict]:
        with mock.patch.object(cli.remote_mod, "fetch", side_effect=self.fake_fetch):
            return self.run_json(*args)

    def test_repository_shortcut_packages_the_fetched_tree(self):
        code, data = self.invoke("gh:octo/demo", "--no-clipboard")
        self.assertEqual(code, 0, data)
        self.assertEqual(data["project"]["kind"], "remote")
        self.assertEqual(data["remote"]["repo"], "octo/demo")
        self.assertEqual(data["zip"]["path"], str(self.tmp / "cache" / "demo.zip"))
        self.assertIn("lib/core.py", data["files"]["included_files"])

    def test_shortcut_after_a_flag_and_before_a_subcommand(self):
        code, data = self.invoke("-n", "github.com/octo/demo")
        self.assertEqual((code, data["remote"]["repo"]), (0, "octo/demo"))
        code, data = self.invoke("https://github.com/octo/demo", "include", "lib/", "-n")
        self.assertEqual(code, 0, data)
        self.assertEqual(data["files"]["included_files"], ["lib/core.py"])

    def test_ref_is_passed_through(self):
        self.invoke("--from", "gh:octo/demo", "--ref", "v1.2", "-n")
        self.assertEqual(self.fetched_with, ("octo/demo", "v1.2"))

    def test_git_changes_needs_a_local_checkout(self):
        code, data = self.invoke("gh:octo/demo", "--git-changes")
        self.assertNotEqual(code, 0)
        self.assertIn("local checkout", data["error"])


class ApplyJsonTests(CliTestCase):
    def test_conflict_writes_nothing_and_lists_the_plan(self):
        code, data = self.run_json("--no-clipboard")
        self.assertEqual(code, 0, data)
        self.write("src/index.ts", "export const a = 2;\n")  # your edit…
        reply = self.make_zip("reply.zip", {"index.ts": "export const a = 3;\n"})  # …and the AI's, flattened

        code, data = self.run_json("apply-zip", str(reply))
        self.assertNotEqual(code, 0)
        self.assertTrue(data["plan"]["needs_decision"])
        self.assertEqual(
            [(f["source"], f["path"], f["status"]) for f in data["plan"]["files"]],
            [("index.ts", "src/index.ts", "conflict")],
        )
        self.assertIsNone(data["result"])
        self.assertEqual(self.read("src/index.ts"), "export const a = 2;\n")

    def test_safe_changes_are_applied(self):
        self.run_json("--no-clipboard")
        reply = self.make_zip("reply.zip", {"src/index.ts": "export const a = 3;\n", "src/new.ts": "export {};\n"})
        code, data = self.run_json("apply-zip", str(reply), "--safe-only")
        self.assertEqual(code, 0, data)
        self.assertEqual(sorted(data["result"]["written"]), ["src/index.ts", "src/new.ts"])
        self.assertEqual(self.read("src/new.ts"), "export {};\n")

    def test_editor_is_refused(self):
        reply = self.make_zip("reply.zip", {"src/index.ts": "x"})
        code, data = self.run_json("apply-zip", str(reply), "--editor")
        self.assertNotEqual(code, 0)
        self.assertIn("--editor", data["error"])

    def test_snapshots(self):
        self.run_json("--no-clipboard")
        code, data = self.run_json("snapshots")
        self.assertEqual(code, 0)
        self.assertEqual([s["id"] for s in data["snapshots"]], [1])


class TokenTests(TempProject):
    def test_estimate_and_format(self):
        self.assertEqual(estimate_tokens(0), 0)
        self.assertEqual(estimate_tokens(9), 3)
        self.assertEqual(format_tokens(850), "≈ 850 tokens")
        self.assertEqual(format_tokens(4_249), "≈ 4.2k tokens")
        self.assertEqual(format_tokens(46_400), "≈ 46k tokens")
        self.assertEqual(format_tokens(999_700), "≈ 1.0M tokens")
