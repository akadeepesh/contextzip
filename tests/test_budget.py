"""--max-tokens, --trim and `contextzip tokens`."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

from click.testing import CliRunner

from contextzip import cli, tokens
from tests.helpers import TempProject


class _FakeEncoding:
    """One token per whitespace-separated word, so budgets in tests are easy to reason about."""

    def encode_ordinary(self, text: str) -> list[int]:
        return [0] * len(text.split())


def words(n: int) -> str:
    return "w " * n


class BudgetTestCase(TempProject):
    def setUp(self) -> None:
        super().setUp()
        self._cwd = os.getcwd()
        os.chdir(self.root)
        patcher = mock.patch.multiple(tokens, tokenizer_available=lambda: True, _encoding=lambda: _FakeEncoding())
        patcher.start()
        self.addCleanup(patcher.stop)
        # 1,000 tokens in all, spread over the five trim classes.
        self.write("README.md", words(50))
        self.write("setup.py", words(50))
        self.write("src/app.py", words(400))
        self.write("src/util.py", words(100))
        self.write("docs/guide.md", words(60))
        self.write("tests/test_app.py", words(120))
        self.write("tests/test_small.py", words(30))
        self.write("data/rows.csv", words(80))
        self.write("src/generated/schema.py", words(90))
        self.write("contextzip-dummy/.keep", "")  # empty: never trimmed, never counted

    def tearDown(self) -> None:
        os.chdir(self._cwd)
        super().tearDown()

    def run_json(self, *args: str) -> tuple[int, dict]:
        result = CliRunner().invoke(cli.main, [*args, "--json"], catch_exceptions=False)
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        self.assertEqual(len(lines), 1, result.stdout)
        return result.exit_code, json.loads(lines[0])

    def run_text(self, *args: str) -> tuple[int, str]:
        result = CliRunner().invoke(cli.main, list(args), catch_exceptions=False)
        return result.exit_code, " ".join(result.output.split())

    def zips(self) -> list[Path]:
        out = self.root / ".contextzip" / "output"
        return list(out.rglob("*.zip")) if out.exists() else []


class MaxTokensTests(BudgetTestCase):
    def test_total_is_what_the_tests_assume(self):
        code, data = self.run_json("--dry-run")
        self.assertEqual(code, 0)
        self.assertEqual(data["files"]["tokens"]["total"], 980)

    def test_bad_budget_is_a_usage_error(self):
        code, text = self.run_text("--dry-run", "--max-tokens", "lots")
        self.assertNotEqual(code, 0)
        self.assertIn("150k", text)

    def test_within_budget_packages_normally(self):
        code, data = self.run_json("--no-clipboard", "--max-tokens", "1k")
        self.assertEqual(code, 0)
        self.assertFalse(data["over_budget"])
        self.assertEqual(len(self.zips()), 1)

    def test_over_budget_writes_nothing(self):
        code, data = self.run_json("--no-clipboard", "--max-tokens", "500")
        self.assertEqual(code, 1)
        self.assertFalse(data["ok"])
        self.assertEqual(data["error"], "980 tokens is over the 500 budget by 480.")
        self.assertEqual(data["files"]["tokens"]["total"], 980)
        self.assertIsNone(data["zip"])
        self.assertEqual(self.zips(), [])
        texts = [" ".join(entry["text"].split()) for entry in data["log"]]
        self.assertIn("src/ 590", texts)  # largest folder
        self.assertIn("src/app.py 400", texts)  # largest file
        self.assertIn("src/generated/schema.py 90", texts)
        self.assertTrue(texts[-1].startswith("Leave some out with contextzip exclude PATH"))

    def test_over_budget_text(self):
        code, text = self.run_text("--no-clipboard", "--max-tokens", "500")
        self.assertEqual(code, 1)
        self.assertIn("✗ 980 tokens is over the 500 budget by 480.", text)
        self.assertEqual(self.zips(), [])

    def test_dry_run_over_budget_reports_and_succeeds(self):
        code, data = self.run_json("--dry-run", "--max-tokens", "500")
        self.assertEqual(code, 0)
        self.assertTrue(data["ok"])
        self.assertTrue(data["over_budget"])
        self.assertIn("980 tokens is over the 500 budget by 480.", data["warnings"])

    def test_config_budget_and_flag_override(self):
        self.write(".contextzip/config.json", json.dumps({"tokens": {"max": 500}}))
        code, data = self.run_json("--dry-run")
        self.assertTrue(data["over_budget"])
        code, data = self.run_json("--dry-run", "--max-tokens", "2k")
        self.assertFalse(data["over_budget"])

    def test_trim_needs_a_budget(self):
        code, data = self.run_json("--dry-run", "--trim")
        self.assertEqual(code, 1)
        self.assertEqual(data["error"], "--trim needs a budget: add --max-tokens N.")


class TrimTests(BudgetTestCase):
    def test_trims_in_class_order(self):
        code, data = self.run_json("--no-clipboard", "--max-tokens", "700", "--trim")
        self.assertEqual(code, 0, data.get("error"))
        # generated 90, data 80, then the largest test (120): 980 - 290 = 690.
        self.assertEqual(
            data["trimmed"],
            [
                {"path": "src/generated/schema.py", "tokens": 90},
                {"path": "data/rows.csv", "tokens": 80},
                {"path": "tests/test_app.py", "tokens": 120},
            ],
        )
        self.assertEqual(data["zip"]["tokens"]["total"], 690)
        self.assertEqual(data["files"]["tokens"]["total"], 690)
        self.assertNotIn("data/rows.csv", data["files"]["included_files"])
        self.assertIn("Trimmed 3 files (290 tokens) to fit the 700 budget — see report", data["warnings"])
        with open(data["report"], encoding="utf-8") as f:
            report = f.read()
        self.assertIn("Trimmed to fit (3 files, 290 tokens)", report)
        self.assertRegex(report, r"\n  data/rows\.csv\s+80\n")

    def test_dry_run_trim_previews(self):
        code, data = self.run_json("--dry-run", "--max-tokens", "700", "--trim")
        self.assertEqual(code, 0)
        self.assertFalse(data["over_budget"])
        self.assertEqual(len(data["trimmed"]), 3)
        self.assertEqual(self.zips(), [])

    def test_never_trims_root_named_or_always_included_files(self):
        self.write(".contextzip/config.json", json.dumps({"always_include": ["tests/test_app.py"]}))
        code, data = self.run_json(
            "include", "src", "tests", "README.md", "setup.py", "src/app.py",
            "--no-clipboard", "--max-tokens", "650", "--trim",
        )
        self.assertEqual(code, 0, data.get("error"))
        trimmed = [t["path"] for t in data["trimmed"]]
        for kept in ("README.md", "setup.py", "src/app.py", "tests/test_app.py"):
            self.assertNotIn(kept, trimmed)
        self.assertEqual(trimmed, ["src/generated/schema.py", "tests/test_small.py", "src/util.py"])

    def test_impossible_budget_drops_nothing(self):
        code, data = self.run_json("include", "src/app.py", "README.md", "setup.py", "--no-clipboard", "--max-tokens", "100", "--trim")
        self.assertEqual(code, 1)
        self.assertNotIn("trimmed", data)
        self.assertEqual(data["error"], "500 tokens is over the 100 budget by 400.")
        self.assertEqual(
            data["log"][-1]["text"], "--trim could not get under the budget without dropping files you asked for."
        )
        self.assertEqual(self.zips(), [])

    def test_verbose_lists_each_trimmed_file(self):
        code, text = self.run_text("--dry-run", "--verbose", "--max-tokens", "700", "--trim")
        self.assertEqual(code, 0)
        self.assertIn("! Trimmed 3 files (290 tokens) to fit the 700 budget", text)
        self.assertIn("trimmed data/rows.csv 80", text)


class TokensCommandTests(BudgetTestCase):
    def test_text(self):
        code, text = self.run_text("tokens")
        self.assertEqual(code, 0)
        self.assertIn("✓ 10 files · 980 tokens (o200k_base)", text)
        self.assertIn("src/ 590 60%", text)  # no bars: CliRunner isn't a terminal
        self.assertNotIn("█", text)
        self.assertIn("Largest files src/app.py 400", text)
        self.assertIn("Fits Claude", text)
        self.assertEqual(self.zips(), [])

    def test_bars_on_a_terminal(self):
        from rich.console import Console

        from contextzip import cli_display
        from contextzip.tokens import count_files

        report = count_files([self.root / "src/app.py", self.root / "README.md"], self.root)
        con = Console(force_terminal=True, width=120, record=True, color_system=None)
        cli_display.print_token_breakdown(report, con=con)
        out = con.export_text()
        self.assertRegex(out, r"\n  src/ +400  █{21}░{3}   89%\n")
        self.assertRegex(out, r"\n  \(root\) +50  █{3}░{21}   11%\n")

    def test_json(self):
        code, data = self.run_json("tokens")
        self.assertEqual(code, 0)
        self.assertEqual(data["command"], "tokens")
        self.assertEqual(data["tokens"]["total"], 980)
        self.assertEqual(data["tokens"]["by_dir"][0], {"path": "src/", "tokens": 590})
        self.assertEqual(len(data["tokens"]["top_files"]), 10)  # every file with tokens, fewer than --top
        self.assertEqual([row["target"] for row in data["fit"]], ["claude", "chatgpt", "gemini"])

    def test_depth_top_and_for(self):
        code, data = self.run_json("tokens", "--depth", "2", "--top", "3", "--for", "claude")
        self.assertEqual(code, 0)
        dirs = {d["path"]: d["tokens"] for d in data["tokens"]["by_dir"]}
        self.assertEqual(dirs["src/"], 500)
        self.assertEqual(dirs["src/generated/"], 90)
        self.assertEqual([f["path"] for f in data["tokens"]["top_files"]], ["src/app.py", "tests/test_app.py", "src/util.py"])
        self.assertEqual([row["target"] for row in data["fit"]], ["claude"])

    def test_path_scopes_like_include(self):
        code, data = self.run_json("tokens", "tests")
        self.assertEqual(code, 0)
        self.assertEqual(data["tokens"]["total"], 150)
        self.assertEqual([d["path"] for d in data["tokens"]["by_dir"]], ["tests/"])

    def test_nothing_to_count(self):
        code, data = self.run_json("tokens", "missing/")
        self.assertEqual(code, 1)
        self.assertIn("Nothing to count", data["error"])

    def test_bad_option(self):
        code, _text = self.run_text("tokens", "--top", "0")
        self.assertNotEqual(code, 0)
        code, data = self.run_json("tokens", "--for", "nope")
        self.assertEqual(code, 1)
        self.assertIn("claude, chatgpt, gemini", data["error"])
