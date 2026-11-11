"""Release 0.9 step 2: the apply plan, one shape for errors, no raw tracebacks."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from click.testing import CliRunner
from rich.console import Console

from contextzip import cli, cli_display, jsonout
from contextzip.applier import ApplyEntry, ApplyPlan, FileStatus
from tests.helpers import TempProject
from tests.test_summary import _Env


def _console(width: int = 100) -> tuple[Console, io.StringIO]:
    out = io.StringIO()
    return Console(file=out, width=width, color_system=None, legacy_windows=False), out


def _entry(path: str, status: FileStatus, note: str = "", **kw) -> ApplyEntry:
    return ApplyEntry(rel_path=path, status=status, size=1, extracted_path=Path(path), source_name=kw.pop("source_name", path), note=note, **kw)


class ApplyPlanDisplayTests(TempProject):
    def plan(self, entries, **kw) -> ApplyPlan:
        values = dict(zip_path=self.root / "reply.zip", manifest_path=self.root / "m.json", entries=entries, snapshot="14-k3f9a2")
        values.update(kw)
        return ApplyPlan(**values)

    def render(self, plan, *, verbose: bool = False, fancy: str = "1", width: int = 100) -> str:
        con, out = _console(width)
        with _Env(CONTEXTZIP_FANCY=fancy):
            cli_display.print_apply_plan(plan, self.root, verbose=verbose, con=con)
        text = out.getvalue()
        for line in text.splitlines():
            self.assertLessEqual(len(line), width, line)
        return text

    def sample(self) -> ApplyPlan:
        return self.plan(
            [
                *[_entry(f"src/m{i}.ts", FileStatus.MODIFIED) for i in range(6)],
                _entry("src/new1.ts", FileStatus.NEW),
                _entry("src/new2.ts", FileStatus.NEW),
                _entry("src/db/schema.ts", FileStatus.MERGED, "your edits and the AI's merged cleanly"),
                _entry("src/api/client.ts", FileStatus.PARTIAL, 'contains "// rest unchanged"'),
                _entry("src/auth/login.ts", FileStatus.CONFLICT, "1 place where you and the AI changed the same lines"),
                _entry("src/lib/util.ts", FileStatus.MOVED, moved_from="src/util.ts"),
                *[_entry(f"same{i}.ts", FileStatus.UNCHANGED) for i in range(12)],
            ]
        )

    def test_header_and_counts(self):
        text = self.render(self.sample())
        lines = text.splitlines()
        self.assertEqual(lines[0], "contextzip apply · reply.zip   compared with snapshot #14")
        self.assertIn("  Will write  10   6 modified · 2 new · 1 merged · 1 moved", text)
        self.assertIn("  Needs you    2   1 conflict · 1 partial", text)
        self.assertIn("  Unchanged   12", text)
        self.assertLess(text.index("Will write"), text.index("Needs you"))
        self.assertLess(text.index("Needs you"), text.index("Unchanged"))

    def test_a_zero_count_line_is_left_out(self):
        text = self.render(self.plan([_entry("a.ts", FileStatus.MODIFIED)]))
        self.assertIn("Will write", text)
        self.assertNotIn("Needs you", text)
        self.assertNotIn("Unchanged", text)

    def test_rows_risky_first(self):
        text = self.render(self.sample())
        rows = [line.strip() for line in text.splitlines() if line.strip() and line.strip()[0] in "!…≈→"]
        self.assertTrue(rows[0].startswith("! src/auth/login.ts"))
        self.assertIn("conflict", rows[0])
        self.assertTrue(rows[1].startswith("… src/api/client.ts"))
        self.assertTrue(rows[2].startswith("≈ src/db/schema.ts"))
        self.assertTrue(rows[3].startswith("→ src/lib/util.ts"))
        self.assertIn("from src/util.ts", rows[3])
        self.assertNotIn("src/m0.ts", text)  # plain modified files aren't listed
        # columns line up
        self.assertEqual(len({row.index("  ", 2) for row in rows[:2]}), 1)

    def test_row_cap_and_more_line(self):
        entries = [_entry(f"c/{i:02}.ts", FileStatus.CONFLICT, "both changed") for i in range(20)]
        text = self.render(self.plan(entries))
        self.assertEqual(sum(1 for line in text.splitlines() if line.strip().startswith("! c/")), 14)
        self.assertIn("… and 6 more, see the report or use --verbose", text)

    def test_verbose_lists_every_file(self):
        text = self.render(self.sample(), verbose=True)
        for i in range(6):
            self.assertIn(f"src/m{i}.ts", text)
        self.assertIn("same11.ts", text)
        self.assertNotIn("more, see the report", text)

    def test_notes_follow_the_table(self):
        plan = self.plan([_entry("a.ts", FileStatus.MODIFIED)], manifest_path=None, snapshot=None, ignored=["x/.DS_Store"])
        text = self.render(plan)
        self.assertEqual(text.splitlines()[0], "contextzip apply · reply.zip")
        self.assertIn("! No snapshot of what you sent was found — files that already exist need a look", text)
        self.assertIn("Ignored 1 entry (contextzip's own files, OS junk)", text)
        self.assertLess(text.index("Will write"), text.index("No snapshot"))

    def test_long_paths_are_cut_from_the_left(self):
        deep = "packages/" + "very-long-folder/" * 5 + "file.ts"
        text = self.render(self.plan([_entry(deep, FileStatus.CONFLICT, "x")]), width=70)
        row = next(line for line in text.splitlines() if "file.ts" in line)
        self.assertIn("…", row)
        self.assertIn("file.ts", row)

    def test_plain_path_unchanged(self):
        text = self.render(self.sample(), fancy="0")
        self.assertIn("✓ Resolved", text)
        self.assertIn("✓ Compared with snapshot #14 (k3f9a2)", text)
        self.assertIn("✓ Classified 24 files  · 6 modified, 1 merged, 1 moved, 2 new, 12 unchanged", text)
        self.assertIn("! 1 conflict, 1 partial — needs your call", text)
        self.assertNotIn("Will write", text)


class ApplyResultDisplayTests(TempProject):
    def render(self, result, fancy: str = "1") -> str:
        con, out = _console()
        with _Env(CONTEXTZIP_FANCY=fancy):
            cli_display.print_apply_result(result, con=con)
        return out.getvalue()

    def result(self, **kw):
        values = dict(written=[f"f{i}" for i in range(9)], skipped=[], applied_zip_path=self.root / "a.zip", zip_kept=False, removed=[], not_removed=[])
        values.update(kw)
        return SimpleNamespace(**values)

    def test_nothing_left(self):
        self.assertIn("✓ Wrote 9 files. Nothing left to decide.", self.render(self.result()))

    def test_files_left_to_decide(self):
        text = self.render(self.result(skipped=["a", "b"], zip_kept=True))
        self.assertIn("✓ Wrote 9 files.   2 need your call: run contextzip apply --review", text)

    def test_one_file(self):
        text = self.render(self.result(written=["a"], skipped=["b"]))
        self.assertIn("✓ Wrote 1 file.   1 needs your call", text)

    def test_plain_unchanged(self):
        text = self.render(self.result(), fancy="0")
        self.assertIn("✓ Applied 9 files", text)
        self.assertIn("✓ Archived zip to", text)


class FailTests(TempProject):
    def test_text(self):
        con, out = _console()
        cli_display.fail("Something broke [x].", why="Because.", fix="contextzip include PATH", con=con)
        self.assertEqual(out.getvalue().splitlines(), ["✗ Something broke [x].", "  Because.", "  Try: contextzip include PATH"])

    def test_text_without_why_or_fix(self):
        con, out = _console()
        cli_display.fail("Only this.", con=con)
        self.assertEqual(out.getvalue().splitlines(), ["✗ Only this."])

    def test_json(self):
        with mock.patch("sys.stdout", io.StringIO()) as stdout:
            try:
                with jsonout.session("package", True):
                    cli_display.fail("Broke.", why="Because.", fix="do this")
                    raise SystemExit(1)
            except SystemExit:
                pass
        data = json.loads(stdout.getvalue())
        self.assertEqual(data["error"], "Broke.")
        self.assertEqual(data["log"][-1], {"level": "error", "text": "Broke.", "detail": "Because. Try: do this"})


class ConvertedErrorTests(TempProject):
    def setUp(self) -> None:
        super().setUp()
        self._cwd = os.getcwd()
        os.chdir(self.root)

    def tearDown(self) -> None:
        os.chdir(self._cwd)
        super().tearDown()

    def invoke(self, *args: str, **env: str):
        with _Env(CONTEXTZIP_FANCY="0", **env):
            return CliRunner().invoke(cli.main, list(args), catch_exceptions=False)

    def test_nothing_to_compress_has_a_fix(self):
        self.write("debug.log", "x\n")
        result = self.invoke("--no-clipboard")
        self.assertIn("✗ Nothing to compress in this one — all files were excluded.", result.stdout)
        self.assertIn("Try: contextzip include PATH", result.stdout)
        self.assertEqual(result.exit_code, 0)  # unchanged: it reports and returns

    def test_an_empty_folder_says_so(self):
        result = self.invoke("--no-clipboard")
        self.assertIn("✗ No files found to package.", result.stdout)
        self.assertIn("Try: cd into your project folder", result.stdout)
        self.assertEqual(result.exit_code, 0)

    def test_an_unreadable_zip(self):
        self.write("a.py", "print(1)\n")
        self.write("bad.zip", "not a zip")
        result = self.invoke("apply-zip", "bad.zip")
        self.assertEqual(result.exit_code, 1)
        self.assertIn("✗ Not a valid zip file", result.stdout)
        self.assertIn("Try: contextzip apply PATH/TO/reply.zip", result.stdout)

    def test_a_missing_zip(self):
        self.write("a.py", "print(1)\n")
        result = self.invoke("apply-zip", "nope.zip")
        self.assertEqual(result.exit_code, 1)
        self.assertIn("✗ No such file", result.stdout)
        self.assertIn("Try: contextzip apply PATH/TO/reply.zip", result.stdout)

    def test_ref_without_a_repository(self):
        self.write("a.py", "print(1)\n")
        result = self.invoke("-n", "--ref", "main")
        self.assertEqual(result.exit_code, 1)
        self.assertIn("✗ --ref picks a branch of a remote repository", result.stdout)
        self.assertIn("Try: contextzip --from github.com/user/repo --ref BRANCH", result.stdout)

    def test_git_error_has_the_git_message(self):
        from contextzip.git import GitError, GitErrorKind

        self.write("a.py", "print(1)\n")
        with mock.patch.object(cli, "get_changed_files", return_value=GitError(GitErrorKind.NOT_A_REPO, "fatal: not a git repository")):
            result = self.invoke("--git-changes", "-n")
        self.assertEqual(result.exit_code, 1)
        self.assertIn("✗ Git error: couldn't list the changed files.", result.stdout)
        self.assertIn("fatal: not a git repository", result.stdout)

    def test_zip_failure_has_the_reason(self):
        self.write("a.py", "print(1)\n")
        with mock.patch.object(cli, "create_zip", side_effect=OSError("disk full")):
            result = self.invoke("--no-clipboard")
        self.assertEqual(result.exit_code, 1)
        self.assertIn("✗ Failed to create ZIP.", result.stdout)
        self.assertIn("disk full", result.stdout)

    def test_gemini_key_under_json(self):
        self.write("a.py", "print(1)\n")
        with mock.patch.object(cli, "get_api_key", return_value=None), mock.patch.object(cli, "diagnose_api_key", return_value=None):
            result = self.invoke("-p", "fix it", "--no-clipboard", "--json")
        self.assertEqual(result.exit_code, 1)
        data = json.loads(result.stdout)
        self.assertEqual(data["error"], "No Gemini API key set.")
        self.assertIn("Try: contextzip config --reset-key", data["log"][-1]["detail"])


class UnexpectedErrorTests(TempProject):
    def setUp(self) -> None:
        super().setUp()
        self._cwd = os.getcwd()
        os.chdir(self.root)
        self.write("a.py", "print(1)\n")

    def tearDown(self) -> None:
        os.chdir(self._cwd)
        super().tearDown()

    def test_a_bug_gets_a_message(self):
        with _Env(CONTEXTZIP_DEBUG=None, CONTEXTZIP_FANCY="0"), mock.patch.object(cli, "_list_snapshots", side_effect=RuntimeError("boom")):
            result = CliRunner().invoke(cli.main, ["snapshots"], catch_exceptions=False)
        self.assertEqual(result.exit_code, 1)
        self.assertIn("✗ contextzip hit an unexpected error: RuntimeError: boom", result.stdout)
        self.assertIn("This is a bug. Please report it: https://github.com/akadeepesh/contextzip/issues/new/choose", " ".join(result.stdout.split()))
        self.assertIn("CONTEXTZIP_DEBUG=1", result.stdout)
        self.assertNotIn("Traceback", result.output)

    def test_debug_shows_the_traceback(self):
        with _Env(CONTEXTZIP_DEBUG="1"), mock.patch.object(cli, "_list_snapshots", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                CliRunner().invoke(cli.main, ["snapshots"], catch_exceptions=False)

    def test_json_still_prints_only_the_object(self):
        with _Env(CONTEXTZIP_DEBUG=None), mock.patch.object(cli, "_list_snapshots", side_effect=RuntimeError("boom")):
            result = CliRunner().invoke(cli.main, ["snapshots", "--json"], catch_exceptions=False)
        self.assertEqual(result.exit_code, 1)
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        self.assertEqual(len(lines), 1)
        data = json.loads(lines[0])
        self.assertFalse(data["ok"])
        self.assertIn("RuntimeError: boom", data["error"])

    def test_click_errors_pass_through(self):
        result = CliRunner().invoke(cli.main, ["snapshots", "--nope"])
        self.assertEqual(result.exit_code, 2)
        self.assertIn("No such option", result.output)
        self.assertNotIn("unexpected error", result.output)

    def test_keyboard_interrupt_passes_through(self):
        with mock.patch.object(cli, "_list_snapshots", side_effect=KeyboardInterrupt):
            result = CliRunner().invoke(cli.main, ["snapshots"])
        self.assertNotIn("unexpected error", result.output)
        self.assertNotEqual(result.exit_code, 0)


class ApplyCliTests(TempProject):
    """The whole apply-zip run in both shapes."""

    def setUp(self) -> None:
        super().setUp()
        self._cwd = os.getcwd()
        os.chdir(self.root)
        self.write("pyproject.toml", "[project]\nname = 'demo'\n")
        self.write("app/main.py", "print('hello')\n")
        self.write("app/util.py", "".join(f"x{i} = {i}\n" for i in range(40)))
        with _Env(CONTEXTZIP_FANCY="0"):
            CliRunner().invoke(cli.main, ["--no-clipboard"], catch_exceptions=False)
        partial = "".join(f"x{i} = {i}\n" for i in range(3)) + "# ... rest unchanged\n"
        self.reply = self.make_zip("reply.zip", {"app/main.py": "print('hi')\n", "app/util.py": partial})

    def tearDown(self) -> None:
        os.chdir(self._cwd)
        super().tearDown()

    def test_fancy_dry_run(self):
        with _Env(CONTEXTZIP_FANCY="1"):
            result = CliRunner().invoke(cli.main, ["apply-zip", str(self.reply), "--dry-run"], catch_exceptions=False)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("contextzip apply · reply.zip   compared with snapshot #1", result.stdout)
        self.assertIn("Will write", result.stdout)
        self.assertIn("Needs you", result.stdout)
        self.assertIn("app/util.py", result.stdout)

    def test_plain_flag(self):
        with _Env(CONTEXTZIP_FANCY="1"):
            result = CliRunner().invoke(cli.main, ["apply-zip", str(self.reply), "--dry-run", "--plain"], catch_exceptions=False)
        self.assertIn("✓ Resolved", result.stdout)
        self.assertNotIn("Will write", result.stdout)

    def test_fancy_safe_only(self):
        with _Env(CONTEXTZIP_FANCY="1"):
            result = CliRunner().invoke(cli.main, ["apply-zip", str(self.reply), "--safe-only"], catch_exceptions=False)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("✓ Wrote 1 file.   1 needs your call: run contextzip apply --review", result.stdout)
        self.assertEqual(self.read("app/main.py"), "print('hi')\n")
