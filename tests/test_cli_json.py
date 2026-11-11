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


class StacksCommandTests(CliTestCase):
    def test_lists_every_stack(self):
        from contextzip import stacks

        code, data = self.run_json("stacks")
        self.assertEqual(code, 0)
        self.assertEqual([s["name"] for s in data["stacks"]], [s.name for s in stacks.STACKS])
        laravel = next(s for s in data["stacks"] if s["slug"] == "laravel")
        self.assertEqual(laravel["manifests"], ["composer.json"])
        self.assertIn("vendor/", laravel["excludes"])

    def test_one_stack_by_name_or_slug(self):
        for name in ("Laravel", "laravel", "c#/.net", "csharp-net"):
            code, data = self.run_json("stacks", name)
            self.assertEqual(code, 0, name)
            self.assertIn(data["stack"]["name"], ("Laravel", "C#/.NET"))

    def test_unknown_stack_fails_with_a_pointer(self):
        code, data = self.run_json("stacks", "cobol")
        self.assertNotEqual(code, 0)
        self.assertIn("cobol", data["error"])

    def test_text_output_lists_names_in_full(self):
        result = CliRunner().invoke(cli.main, ["stacks"], catch_exceptions=False)
        self.assertEqual(result.exit_code, 0)
        for name in ("React Native", "Unreal Engine", "Spring Boot"):
            self.assertIn(name, result.stdout)


class DetectionOutputTests(CliTestCase):
    def test_file_type_detection_is_reported(self):
        (self.root / "package.json").unlink()
        for name in ("a.py", "b.py", "c.py"):
            self.write(name, "print(1)\n")
        code, data = self.run_json("--dry-run")
        self.assertEqual(code, 0)
        self.assertEqual(data["detection"]["by_file_type"], ["Python"])
        self.assertEqual(data["detection"]["confidence"], "low")
        self.assertIn("python", data["detection"]["rules"])

        result = CliRunner().invoke(cli.main, ["--dry-run"], catch_exceptions=False)
        self.assertIn("from file types", result.stdout)

    def test_marker_detection_has_no_file_type_guess(self):
        code, data = self.run_json("--dry-run")
        self.assertEqual(data["detection"]["by_file_type"], [])


class PrintableStreamTests(TempProject):
    """A check mark must never crash a run on a stream that can't encode it."""

    def run_with_stdout(self, encoding: str):
        import io
        import sys

        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding=encoding)
        with mock.patch.object(sys, "stdout", stream), mock.patch.object(sys, "stderr", stream):
            cli._printable_streams()
            stream.write("✓ ok → ≈")
            stream.flush()
        return raw.getvalue()

    def test_legacy_code_page_no_longer_raises(self):
        out = self.run_with_stdout("cp1252")
        if os.name == "nt":
            self.assertEqual(out.decode("utf-8"), "✓ ok → ≈")
        else:
            self.assertIn(b" ok ", out)

    def test_utf8_stream_is_left_alone(self):
        self.assertEqual(self.run_with_stdout("utf-8").decode("utf-8"), "✓ ok → ≈")


class TokenTests(TempProject):
    def test_estimate_and_format(self):
        self.assertEqual(estimate_tokens(0), 0)
        self.assertEqual(estimate_tokens(9), 3)
        self.assertEqual(format_tokens(850), "≈ 850 tokens")
        self.assertEqual(format_tokens(4_249), "≈ 4.2k tokens")
        self.assertEqual(format_tokens(46_400), "≈ 46k tokens")
        self.assertEqual(format_tokens(999_700), "≈ 1.0M tokens")


class TokenOutputTests(CliTestCase):
    """Release 0.8: the real token count in every result."""

    def setUp(self) -> None:
        super().setUp()
        self.write("src/util.ts", "export function add(a: number, b: number) { return a + b; }\n")
        self.write("README.md", "# demo\n\nA small project.\n")

    def expected_method(self) -> str:
        from contextzip.tokens import METHOD_ESTIMATE, METHOD_EXACT, tokenizer_available

        return METHOD_EXACT if tokenizer_available() else METHOD_ESTIMATE

    def assert_tokens_block(self, block: dict, estimated: int, *, verbose: bool) -> None:
        self.assertEqual(block["total"], estimated)
        self.assertEqual(block["method"], self.expected_method())
        self.assertEqual(block["exact"], block["method"] == "o200k_base")
        self.assertEqual(block["by_dir"][0]["path"], "src/")
        self.assertEqual(sum(d["tokens"] for d in block["by_dir"]), block["total"])
        self.assertLessEqual(len(block["top_files"]), 10)
        self.assertEqual(
            {f["path"] for f in block["top_files"]}, {"package.json", "src/index.ts", "src/util.ts", "README.md"}
        )
        if verbose:
            self.assertEqual(sum(block["by_file"].values()), block["total"])
            self.assertEqual(sorted(block["by_file"]), ["README.md", "package.json", "src/index.ts", "src/util.ts"])
        else:
            self.assertNotIn("by_file", block)

    def test_package_json_has_tokens_in_files_and_zip(self):
        code, data = self.run_json("--no-clipboard")
        self.assertEqual(code, 0)
        self.assert_tokens_block(data["files"]["tokens"], data["files"]["estimated_tokens"], verbose=False)
        self.assert_tokens_block(data["zip"]["tokens"], data["zip"]["estimated_tokens"], verbose=False)
        self.assertEqual(data["zip"]["tokens"]["total"], data["files"]["tokens"]["total"])

    def test_by_file_only_with_verbose(self):
        code, data = self.run_json("--no-clipboard", "--verbose")
        self.assertEqual(code, 0)
        self.assert_tokens_block(data["files"]["tokens"], data["files"]["estimated_tokens"], verbose=True)
        self.assert_tokens_block(data["zip"]["tokens"], data["zip"]["estimated_tokens"], verbose=True)

    def test_dry_run_has_tokens(self):
        code, data = self.run_json("--dry-run")
        self.assertEqual(code, 0)
        self.assert_tokens_block(data["files"]["tokens"], data["files"]["estimated_tokens"], verbose=False)

    def test_dry_run_never_creates_the_workspace(self):
        code, _data = self.run_json("--dry-run")
        self.assertEqual(code, 0)
        result = CliRunner().invoke(cli.main, ["--dry-run"], catch_exceptions=False)
        self.assertEqual(result.exit_code, 0)
        self.assertFalse((self.root / ".contextzip").exists())

    def test_dry_run_uses_an_existing_workspace_cache(self):
        (self.root / ".contextzip").mkdir()
        code, _data = self.run_json("--dry-run")
        self.assertEqual(code, 0)
        self.assertTrue((self.root / ".contextzip" / "cache" / "tokens.json").is_file())

    def test_output_flag_never_creates_the_cache(self):
        out = self.tmp / "out" / "x.zip"
        code, data = self.run_json("--no-clipboard", "-o", str(out))
        self.assertEqual(code, 0)
        self.assertIn("tokens", data["zip"])
        self.assertFalse((self.root / ".contextzip" / "cache").exists())

    def test_package_run_caches_counts(self):
        code, _data = self.run_json("--no-clipboard")
        self.assertEqual(code, 0)
        cached = json.loads((self.root / ".contextzip" / "cache" / "tokens.json").read_text(encoding="utf-8"))
        self.assertEqual(set(cached["files"]), {"package.json", "src/index.ts", "src/util.ts", "README.md"})

    def test_report_and_guide_carry_the_count(self):
        import zipfile

        code, data = self.run_json("--no-clipboard")
        self.assertEqual(code, 0)
        block = data["zip"]["tokens"]
        label = format_tokens(block["total"], exact=block["exact"])

        with open(data["report"], encoding="utf-8") as f:
            report = f.read()
        self.assertIn(f"tokens   : {label}, counted with {block['method']}", report)
        self.assertIn(f"Tokens ({block['method']})", report)
        self.assertRegex(report, r"\n  src/\s+[\d,]+\s+\d+%\n")
        self.assertRegex(report, r"\n  src/util\.ts\s+\d+ B\s+[\d,]+\n")

        with zipfile.ZipFile(data["zip"]["path"]) as zf:
            guide = zf.read("CONTEXTZIP.md").decode("utf-8")
        self.assertIn(f"Size: 4 files, {label} ({block['method']}).", guide)

    def test_terminal_line_shows_the_count(self):
        from contextzip.tokens import tokenizer_available

        result = CliRunner().invoke(cli.main, ["--dry-run"], catch_exceptions=False)
        self.assertEqual(result.exit_code, 0)
        scan_line = next(line for line in result.stdout.splitlines() if "Scanned" in line)
        self.assertRegex(scan_line, r"\d tokens")
        if tokenizer_available():
            self.assertNotIn("≈", scan_line)

    def test_verbose_lists_most_tokens(self):
        result = CliRunner().invoke(cli.main, ["--dry-run", "--verbose"], catch_exceptions=False)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Most tokens: src/util.ts", " ".join(result.stdout.split()))

    def test_without_the_tokenizer_it_says_estimate(self):
        from contextzip import tokens

        with mock.patch.object(tokens, "tokenizer_available", return_value=False):
            code, data = self.run_json("--no-clipboard")
            result = CliRunner().invoke(cli.main, ["--dry-run"], catch_exceptions=False)
        self.assertEqual(code, 0)
        self.assertEqual(data["files"]["tokens"]["method"], "estimate")
        self.assertFalse(data["zip"]["tokens"]["exact"])
        self.assertIn("≈", result.stdout)

    def test_bracketed_paths_print_safely(self):
        self.write("app/[id]/page.tsx", "export default function Page() { return null; }\n" * 40)
        self.write("app/[/b]/x.ts", "export const b = 2;\n")
        result = CliRunner().invoke(cli.main, ["--dry-run", "--verbose"], catch_exceptions=False)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("app/[id]/page.tsx", result.stdout)
