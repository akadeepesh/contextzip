"""Release 0.9 step 5: the picker screen, driven through Textual's pilot."""

from __future__ import annotations

import asyncio
import json
import os
import unittest
from unittest import mock

from click.testing import CliRunner

from contextzip import cli, models
from contextzip.detector import detect
from contextzip.picker_model import PickerModel
from contextzip.project_config import load_project_config
from tests.helpers import TempProject

try:
    import textual  # noqa: F401

    HAS_TEXTUAL = True
except ImportError:  # pragma: no cover - textual is a dependency; this guards broken installs
    HAS_TEXTUAL = False


def _project(case: TempProject) -> None:
    case.write("package.json", '{"name": "demo"}\n')
    case.write("src/index.ts", "export const a = 1;\n")
    case.write("src/util.ts", "export const b = 2;\n")
    case.write("lib/one.ts", "export const one = 1;\n")
    case.write("docs/spec.pdf", b"%PDF-1.4 fake")
    case.write(".env", "TOKEN=abc\n")


@unittest.skipUnless(HAS_TEXTUAL, "textual is not installed")
class PickerScreenTests(TempProject):
    def setUp(self) -> None:
        super().setUp()
        _project(self)
        self.model = PickerModel(self.root, detect(self.root), load_project_config(self.root))
        self.saved = []

    def run_app(self, *keys: str, size=(100, 30), save_only: bool = False, check=None):
        from contextzip.picker import build_app

        app = build_app(
            self.model,
            title="demo",
            fit_targets=models.resolve(None, None),
            save_only=save_only,
            save=lambda m: self.saved.append(m.as_patterns()),
        )

        async def drive():
            async with app.run_test(size=size) as pilot:
                await app.workers.wait_for_complete()
                await pilot.pause()
                for key in keys:
                    await pilot.press(key)
                    await pilot.pause()
                if check is not None:
                    check(app)
            return app.return_value

        return asyncio.run(drive()), app

    def rows(self, app) -> list[str]:
        return [n.path for n in app.tree.rows]

    def test_starts_with_folders_open_and_tokens_counted(self):
        def check(app):
            self.assertEqual(self.rows(app)[:2], ["docs", "docs/spec.pdf"])
            self.assertNotIn(".env", self.rows(app))
            self.assertTrue(self.model.tokens_counted)
            header = str(app.query_one("#header").render())
            self.assertIn("contextzip pick", header)
            self.assertIn("Fits", header)

        self.run_app(check=check)

    def test_arrows_move_and_space_ticks(self):
        def check(app):
            self.assertEqual(app.tree.current.path, "docs/spec.pdf")
            self.assertFalse(self.model.is_selected("docs/spec.pdf"))

        self.run_app("down", "space", check=check)

    def test_j_k_move(self):
        def check(app):
            self.assertEqual(app.tree.cursor, 1)

        self.run_app("j", "j", "k", check=check)

    def test_open_and_close(self):
        def check(app):
            self.assertNotIn("docs/spec.pdf", self.rows(app))
            self.assertEqual(app.tree.current.path, "docs")

        self.run_app("left", check=check)  # cursor on docs/, which is open: close it

    def test_left_on_a_file_goes_to_its_folder(self):
        def check(app):
            self.assertEqual(app.tree.current.path, "docs")

        self.run_app("down", "left", check=check)

    def test_right_moves_into_an_open_folder(self):
        def check(app):
            self.assertEqual(app.tree.current.path, "docs/spec.pdf")

        self.run_app("right", check=check)

    def test_filter_and_escape(self):
        def check(app):
            self.assertEqual(self.rows(app), ["src", "src/util.ts"])

        self.run_app("slash", "u", "t", "i", "l", check=check)

        def cleared(app):
            self.assertIn("lib", self.rows(app))
            self.assertIs(app.focused, app.tree)

        self.run_app("slash", "u", "escape", check=cleared)

    def test_all_and_none(self):
        def none(app):
            self.assertEqual(self.model.included(), [])

        self.run_app("n", check=none)

        def everything(app):
            self.assertTrue(self.model.is_selected("src/util.ts"))
            self.assertFalse(self.model.is_selected(".env"))  # hidden, so not "visible"

        self.run_app("n", "a", check=everything)

    def test_suggestions(self):
        def check(app):
            self.assertEqual(type(app.screen).__name__, "SuggestionsScreen")

        self.run_app("s", check=check)

        def applied(app):
            self.assertFalse(self.model.is_selected("docs/spec.pdf"))
            self.assertEqual(type(app.screen).__name__, "Screen")

        self.run_app("s", "space", "escape", check=applied)

    def test_e_shows_ignored_files(self):
        def check(app):
            self.assertIn(".env", self.rows(app))

        self.run_app("e", check=check)

    def test_enter_returns_the_selection(self):
        self.model.toggle("lib")
        result, _app = self.run_app("enter")
        self.assertEqual(result, "pack")
        self.assertNotIn(self.root / "lib" / "one.ts", self.model.included())

    def test_w_saves_and_stays(self):
        def check(app):
            self.assertEqual(app.message, "Saved as this project's default.")
            self.assertIsNone(app.return_value)

        self.run_app("n", "w", check=check)
        self.assertEqual(len(self.saved), 1)

    def test_save_only_enter_saves(self):
        result, _app = self.run_app("enter", save_only=True)
        self.assertEqual(result, "save")
        self.assertEqual(len(self.saved), 1)

    def test_q_and_escape_quit(self):
        self.assertIsNone(self.run_app("q")[0])
        self.assertIsNone(self.run_app("escape")[0])

    def test_mouse_targets(self):
        from contextzip.picker import build_app

        app = build_app(self.model, title="demo", fit_targets=[], save=lambda m: None)

        async def drive():
            async with app.run_test(size=(100, 30)) as pilot:
                await app.workers.wait_for_complete()
                await pilot.pause()
                tree = app.tree
                # Row 0 is docs/ (open). The arrow is at column 1, the box at 4-6.
                await pilot.click("#tree", offset=(5, 0))
                await pilot.pause()
                self.assertFalse(self.model.is_selected("docs/spec.pdf"))
                await pilot.click("#tree", offset=(1, 0))
                await pilot.pause()
                self.assertNotIn("docs/spec.pdf", [n.path for n in tree.rows])
                await pilot.click("#tree", offset=(12, 1))
                await pilot.pause()
                self.assertEqual(tree.cursor, 1)

        asyncio.run(drive())

    def test_narrow_terminal(self):
        def check(app):
            line = "".join(seg.text for seg in app.tree.render_line(0))
            self.assertLessEqual(len(line), app.tree.size.width)
            self.assertIn("[x]", line)

        self.run_app(size=(60, 20), check=check)


class PickCliTests(TempProject):
    def setUp(self) -> None:
        super().setUp()
        self._cwd = os.getcwd()
        os.chdir(self.root)
        _project(self)

    def tearDown(self) -> None:
        os.chdir(self._cwd)
        super().tearDown()

    def test_json_is_refused(self):
        result = CliRunner().invoke(cli.main, ["pick", "--json"], catch_exceptions=False)
        self.assertEqual(result.exit_code, 1)
        data = json.loads(result.stdout)
        self.assertEqual(data["error"], "The picker needs an interactive terminal.")

    def test_non_tty_is_refused(self):
        result = CliRunner().invoke(cli.main, ["pick"], catch_exceptions=False)
        self.assertEqual(result.exit_code, 1)
        self.assertIn("✗ The picker needs an interactive terminal.", result.stdout)
        self.assertIn("Try: contextzip include PATH…   or   contextzip config --ui", result.stdout)

    def test_include_without_paths_is_still_a_usage_error_when_piped(self):
        for command in ("include", "exclude"):
            result = CliRunner().invoke(cli.main, [command])
            self.assertEqual(result.exit_code, 2, command)
            self.assertIn("Missing argument", result.output)

    def fake_picker(self, toggles=(), result="pack"):
        seen = {}

        def run(model, **kwargs):
            seen["model"] = model
            seen["kwargs"] = kwargs
            for path in toggles:
                model.toggle(path)
            return result

        return seen, run

    def test_enter_packages_exactly_the_selection(self):
        seen, run = self.fake_picker(toggles=["lib", ".env"])
        with mock.patch.object(cli, "_interactive", return_value=True), mock.patch("contextzip.picker.run_picker", run):
            result = CliRunner().invoke(cli.main, ["pick", "--no-clipboard"], catch_exceptions=False)
        self.assertEqual(result.exit_code, 0, result.output)
        import zipfile

        zip_path = self.root / ".contextzip" / "output" / "codebase" / "codebase.zip"
        with zipfile.ZipFile(zip_path) as zf:
            names = sorted(n for n in zf.namelist() if n != "CONTEXTZIP.md")
            env = zf.read(".env").decode("utf-8")
        self.assertEqual(names, [".env", "docs/spec.pdf", "package.json", "src/index.ts", "src/util.ts"])
        self.assertIn("[REDACTED]", env)  # a ticked secret is still redacted
        self.assertNotIn("abc", env)
        self.assertIn("normally excluded file included", " ".join(result.stdout.split()))

    def test_pick_paths_start_ticked(self):
        seen, run = self.fake_picker(result=None)
        with mock.patch.object(cli, "_interactive", return_value=True), mock.patch("contextzip.picker.run_picker", run):
            result = CliRunner().invoke(cli.main, ["pick", "src/"], catch_exceptions=False)
        self.assertEqual(result.exit_code, 1)
        self.assertIn("Nothing picked.", result.stdout)
        model = seen["model"]
        self.assertEqual([p.relative_to(self.root).as_posix() for p in model.included()], ["src/index.ts", "src/util.ts"])

    def test_include_without_paths_opens_an_empty_picker(self):
        seen, run = self.fake_picker(toggles=["lib/one.ts"])
        with mock.patch.object(cli, "_interactive", return_value=True), mock.patch("contextzip.picker.run_picker", run):
            result = CliRunner().invoke(cli.main, ["include", "--no-clipboard", "--json"], catch_exceptions=False)
        self.assertEqual(result.exit_code, 2)  # --json never opens the picker
        with mock.patch.object(cli, "_interactive", return_value=True), mock.patch("contextzip.picker.run_picker", run):
            result = CliRunner().invoke(cli.main, ["include", "--no-clipboard"], catch_exceptions=False)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual([p.name for p in seen["model"].included()], ["one.ts"])

    def test_exclude_without_patterns_starts_with_the_rules(self):
        seen, run = self.fake_picker(result=None)
        with mock.patch.object(cli, "_interactive", return_value=True), mock.patch("contextzip.picker.run_picker", run):
            CliRunner().invoke(cli.main, ["exclude"], catch_exceptions=False)
        self.assertIn(self.root / "src" / "util.ts", seen["model"].included())

    def test_save_mode(self):
        seen, run = self.fake_picker(result="save")
        with mock.patch.object(cli, "_interactive", return_value=True), mock.patch("contextzip.picker.run_picker", run):
            result = CliRunner().invoke(cli.main, ["pick", "--save"], catch_exceptions=False)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertTrue(seen["kwargs"]["save_only"])
        self.assertIn("Saved as this project's default", result.stdout)
        self.assertFalse((self.root / ".contextzip" / "output").exists())  # nothing packaged

    def test_save_writes_patterns_and_keeps_other_keys(self):
        from contextzip.picker import save_selection

        self.write(".contextzip/config.json", '{"zip_guide": false, "always_exclude": ["old/"]}')
        model = PickerModel(self.root, detect(self.root), load_project_config(self.root))
        model.toggle("lib")
        save_selection(model)
        data = json.loads((self.root / ".contextzip" / "config.json").read_text(encoding="utf-8"))
        self.assertFalse(data["zip_guide"])
        self.assertEqual(data["always_exclude"], ["/lib/"])
        self.assertEqual(data["always_include"], [])

    def test_missing_textual(self):
        import builtins

        real_import = builtins.__import__

        def no_textual(name, *args, **kwargs):
            if name == "textual" or name.startswith("textual."):
                raise ImportError("No module named 'textual'")
            return real_import(name, *args, **kwargs)

        with mock.patch.object(cli, "_interactive", return_value=True), mock.patch("builtins.__import__", no_textual):
            result = CliRunner().invoke(cli.main, ["pick"], catch_exceptions=False)
        self.assertEqual(result.exit_code, 1)
        self.assertIn('✗ The picker needs the "textual" package.', result.stdout)
        self.assertIn("pip install textual", result.stdout)

    def test_picker_flags_are_listed(self):
        result = CliRunner().invoke(cli.main, ["pick", "--help"])
        for flag in ("--save", "--output", "--no-clipboard", "--max-tokens", "--trim", "--for", "--here", "--verbose", "--plain"):
            self.assertIn(flag, result.stdout)
        self.assertNotIn("--prompt", result.stdout)
        self.assertNotIn("--git-changes", result.stdout)
