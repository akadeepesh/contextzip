"""models.py, the "Fits" line, --for and the tokens config section."""

from __future__ import annotations

import json
import os
import unittest
from unittest import mock

from click.testing import CliRunner

from contextzip import cli, jsonout, models
from contextzip.cli_display import print_fit
from contextzip.models import TARGETS, Target, fit, resolve
from contextzip.project_config import load_project_config
from contextzip.tokens import METHOD_ESTIMATE, METHOD_EXACT, TokenReport
from tests.helpers import TempProject


def _report(total: int, method: str = METHOD_EXACT) -> TokenReport:
    return TokenReport(total=total, method=method, by_file={"a.py": total})


class FitTests(unittest.TestCase):
    def test_scales_and_rounds_up(self):
        target = Target("t", "T", 1000, 1.2, False)
        self.assertEqual(fit(100, target), (120, 0.12))  # no float creep: 100 * 1.2 is 120
        self.assertEqual(fit(101, target)[0], 122)  # 121.2 rounds up
        self.assertEqual(fit(0, target), (0, 0.0))

    def test_share_over_one(self):
        tokens, share = fit(1310, Target("t", "T", 1000, 1.0, True))
        self.assertEqual(tokens, 1310)
        self.assertAlmostEqual(share, 1.31)

    def test_targets_are_sane(self):
        self.assertEqual([t.key for t in TARGETS], ["claude", "chatgpt", "gemini"])
        for t in TARGETS:
            self.assertGreaterEqual(t.window, 1000)
            self.assertGreater(t.factor, 0)


class ResolveTests(unittest.TestCase):
    def test_none_is_all(self):
        self.assertEqual(resolve(None, None), list(TARGETS))

    def test_named_in_order_without_repeats(self):
        self.assertEqual([t.key for t in resolve(["gemini", "Claude", "gemini"], None)], ["gemini", "claude"])

    def test_unknown_key_names_the_valid_ones(self):
        with self.assertRaises(ValueError) as ctx:
            resolve(["nope"], None)
        self.assertIn("claude, chatgpt, gemini", str(ctx.exception))

    def test_override_replaces_the_window_only(self):
        claude = resolve(["claude"], {"claude": 500_000})[0]
        builtin = next(t for t in TARGETS if t.key == "claude")
        self.assertEqual((claude.window, claude.factor, claude.name), (500_000, builtin.factor, "Claude"))

    def test_extra_key_adds_a_target(self):
        targets = resolve(None, {"local": 32_000})
        self.assertEqual(targets[-1], Target("local", "local", 32_000, 1.0, False))
        self.assertEqual(resolve(["local"], {"local": 32_000}), [targets[-1]])
        with self.assertRaises(ValueError) as ctx:
            resolve(["nope"], {"local": 32_000})
        self.assertIn("local", str(ctx.exception))


class FitLineTests(unittest.TestCase):
    def lines(self, report: TokenReport, targets: list[Target]) -> list[dict]:
        session = jsonout.Session("test")
        with mock.patch.object(jsonout, "_active", session):
            print_fit(jsonout.fit_data(report, targets))
        return session.log

    def test_under_one_percent(self):
        log = self.lines(_report(5), [Target("x", "X", 1000, 1.0, True)])
        self.assertEqual(log, [{"level": "info", "text": "Fits  X <1%"}])

    def test_normal_values_and_approximation(self):
        log = self.lines(
            _report(230),
            [Target("claude", "Claude", 1000, 1.0, False), Target("chatgpt", "ChatGPT", 640, 1.0, True)],
        )
        self.assertEqual(log, [{"level": "info", "text": "Fits  Claude ≈23%  ·  ChatGPT 36%"}])

    def test_estimate_method_marks_every_target(self):
        log = self.lines(_report(230, METHOD_ESTIMATE), [Target("chatgpt", "ChatGPT", 1000, 1.0, True)])
        self.assertEqual(log[0]["text"], "Fits  ChatGPT ≈23%")

    def test_over_budget_warns_with_a_hint(self):
        log = self.lines(
            _report(1310),
            [Target("claude", "Claude", 1000, 1.0, True), Target("gemini", "Gemini", 10_000, 1.0, True)],
        )
        self.assertEqual(log[0], {"level": "warn", "text": "Fits  Claude 131%, too big  ·  Gemini 13%"})
        self.assertEqual(
            log[1]["text"],
            "Too big for Claude. Narrow it with contextzip include PATH, or see the largest folders with --verbose.",
        )

    def test_fit_data_shape(self):
        rows = jsonout.fit_data(_report(1310), [Target("claude", "Claude", 1000, 1.0, False)])
        self.assertEqual(
            rows,
            [{"target": "claude", "name": "Claude", "window": 1000, "tokens": 1310, "share": 1.31, "exact": False, "fits": False}],
        )


class FitCliTests(TempProject):
    def setUp(self) -> None:
        super().setUp()
        self._cwd = os.getcwd()
        os.chdir(self.root)
        self.write("package.json", '{"name": "demo"}\n')
        self.write("src/index.ts", "export const a = 1;\n")

    def tearDown(self) -> None:
        os.chdir(self._cwd)
        super().tearDown()

    def run_json(self, *args: str) -> tuple[int, dict]:
        result = CliRunner().invoke(cli.main, [*args, "--json"], catch_exceptions=False)
        return result.exit_code, json.loads(result.stdout.strip().splitlines()[-1])

    def run_text(self, *args: str) -> str:
        result = CliRunner().invoke(cli.main, list(args), catch_exceptions=False)
        self.assertEqual(result.exit_code, 0, result.output)
        return " ".join(result.stdout.split())

    def test_dry_run_shows_every_target(self):
        out = self.run_text("--dry-run")
        self.assertIn("Fits Claude", out)
        self.assertIn("ChatGPT", out)
        self.assertIn("Gemini", out)

    def test_for_picks_targets(self):
        out = self.run_text("--dry-run", "--for", "claude", "--for", "gemini")
        self.assertIn("Fits Claude", out)
        self.assertIn("Gemini", out)
        self.assertNotIn("ChatGPT", out)

        code, data = self.run_json("include", "src", "-n", "--for", "chatgpt")
        self.assertEqual(code, 0)
        self.assertEqual([row["target"] for row in data["fit"]], ["chatgpt"])

    def test_unknown_target_fails_with_the_valid_names(self):
        result = CliRunner().invoke(cli.main, ["--dry-run", "--for", "nope"], catch_exceptions=False)
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("claude, chatgpt, gemini", " ".join(result.stdout.split()))
        code, data = self.run_json("--dry-run", "--for", "nope")
        self.assertNotEqual(code, 0)
        self.assertIn("claude, chatgpt, gemini", data["error"])

    def test_json_fit_on_dry_run_and_package(self):
        for args in (("--dry-run",), ("--no-clipboard",)):
            code, data = self.run_json(*args)
            self.assertEqual(code, 0)
            self.assertEqual([row["target"] for row in data["fit"]], ["claude", "chatgpt", "gemini"])
            row = data["fit"][0]
            self.assertEqual(set(row), {"target", "name", "window", "tokens", "share", "exact", "fits"})
            self.assertTrue(row["fits"])

    def test_config_windows_and_show_fit(self):
        self.write(
            ".contextzip/config.json",
            json.dumps({"tokens": {"windows": {"claude": 1000, "tiny": 1000}, "show_fit": False}}),
        )
        out = self.run_text("--dry-run")
        self.assertNotIn("Fits", out)
        code, data = self.run_json("--dry-run")
        self.assertEqual(code, 0)
        windows = {row["target"]: row["window"] for row in data["fit"]}
        self.assertEqual(windows["claude"], 1000)
        self.assertEqual(windows["tiny"], 1000)

    def test_over_a_window_warns(self):
        self.write(".contextzip/config.json", json.dumps({"tokens": {"windows": {"chatgpt": 1000}}}))
        self.write("src/big.ts", "export const x = 'a b c d e f g h';\n" * 400)
        code, data = self.run_json("--dry-run", "--for", "chatgpt")
        self.assertEqual(code, 0)
        self.assertFalse(data["fit"][0]["fits"])
        self.assertTrue(any("too big" in w for w in data["warnings"]))


class TokensConfigTests(TempProject):
    def config(self, tokens) -> object:
        self.write(".contextzip/config.json", json.dumps({"tokens": tokens}))
        return load_project_config(self.root).tokens

    def test_defaults(self):
        cfg = load_project_config(self.root).tokens
        self.assertEqual((cfg.windows, cfg.show_fit), ({}, True))

    def test_bad_values_fall_back(self):
        cfg = self.config({"windows": {"Claude": 500000, "small": 999, "flag": True, "str": "1000", "": 5000}, "show_fit": "no"})
        self.assertEqual(cfg.windows, {"claude": 500000})
        self.assertTrue(cfg.show_fit)
        self.assertEqual(self.config("nope").windows, {})
        self.assertEqual(self.config({"windows": [1, 2]}).windows, {})

    def test_config_ui_save_keeps_tokens(self):
        from contextzip.webui.persist import save_config_from_ui

        self.write(".contextzip/config.json", json.dumps({"tokens": {"windows": {"claude": 500000}, "show_fit": False}}))
        save_config_from_ui(self.root, always_include=["docs/"], always_exclude=[])
        saved = json.loads((self.root / ".contextzip" / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["tokens"], {"windows": {"claude": 500000}, "show_fit": False})
        self.assertEqual(saved["always_include"], ["docs/"])


if __name__ == "__main__":
    unittest.main()
