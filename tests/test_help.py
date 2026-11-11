"""Release 0.9 step 3: help you can scan, apply as the command name, suggestions for typos."""

from __future__ import annotations

import os
import platform
import re
from unittest import mock

import click
from click.testing import CliRunner

from contextzip import __version__, cli
from tests.helpers import TempProject


def _help(*args: str, prog: str = "cz") -> str:
    result = CliRunner().invoke(cli.main, [*args, "--help"] if args else ["--help"], prog_name=prog, catch_exceptions=False)
    assert result.exit_code == 0, result.output
    return result.stdout


def _rows(text: str, section: str) -> list[str]:
    """The first word of each row under *section*."""
    lines = text.splitlines()
    start = lines.index(section) + 1
    rows = []
    for line in lines[start:]:
        if not line.strip():
            break
        if line.startswith("  ") and not line.startswith("   "):
            rows.append(line.split()[0])
    return rows


class MainHelpTests(TempProject):
    def visible_commands(self) -> list[str]:
        ctx = click.Context(cli.main)
        return [n for n in cli.main.list_commands(ctx) if not cli.main.get_command(ctx, n).hidden]

    def test_every_command_appears_exactly_once(self):
        text = _help()
        listed = [row for section in ("Package", "Apply", "Set up", "More") if section in text.splitlines() for row in _rows(text, section)]
        self.assertEqual(sorted(listed), sorted(self.visible_commands()))
        self.assertEqual(len(listed), len(set(listed)))

    def test_group_order(self):
        text = _help()
        positions = [text.index(f"\n{title}\n") for title in ("Usage", "Package", "Apply", "Set up", "More", "Options")]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(_rows(text, "Apply"), ["apply", "snapshots"])
        self.assertEqual(_rows(text, "Package")[:2], ["include", "exclude"])

    def test_title_usage_and_docs(self):
        text = _help()
        self.assertTrue(text.startswith(f"contextzip {__version__} — send the right files to an AI"))
        self.assertIn("  cz include src/ app/", text)
        self.assertIn("  cz apply ", text)
        self.assertTrue(text.rstrip().endswith("Docs: https://www.contextzipcli.com/docs/cli"))

    def test_usage_uses_the_name_typed(self):
        self.assertIn("  contextzip --git-changes", _help(prog="contextzip"))

    def test_short_help_comes_from_the_command(self):
        text = _help()
        self.assertRegex(text, r"\n  include PATH…\s+Package only these paths\n")
        self.assertRegex(text, r"\n  apply \[ZIP\]\s+Write an AI-returned ZIP back, safely\n")
        self.assertRegex(text, r"\n  watch -- COMMAND\s+Package debug context when a command fails\n")
        for name in self.visible_commands():
            cmd = cli.main.get_command(click.Context(cli.main), name)
            self.assertTrue(cmd.short_help, f"{name} has no short_help")

    def test_descriptions_line_up(self):
        text = _help()
        columns = set()
        for section in ("Package", "Apply", "Set up", "More"):
            lines = text.splitlines()
            start = lines.index(section) + 1
            for line in lines[start:]:
                if not line.strip():
                    break
                if not line.startswith("   "):
                    columns.add(re.search(r"\S {3,}(\S)", line).start(1))
        self.assertEqual(len(columns), 1, columns)

    def test_every_option_in_declared_order(self):
        text = _help()
        options = text[text.index("\nOptions\n"):]
        expected = ["--include", "--exclude", "--prompt", "--dry-run", "--output", "--no-clipboard", "--no-gitignore",
                    "--git-changes", "--verbose", "--here", "--from", "--ref", "--max-tokens", "--trim", "--for",
                    "--plain", "--json", "--version", "--help"]
        positions = [options.index(opt) for opt in expected]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("-n, --dry-run", options)

    def test_long_rows_wrap_under_the_description(self):
        text = cli._render_main_help(click.Context(cli.main, info_name="cz"), cli.main, styled=False, width=50)
        for line in text.splitlines():
            self.assertLessEqual(len(line), 50, line)
        self.assertNotRegex(text, r" +\n")  # no trailing spaces

    def test_dash_h_works_and_piped_help_has_no_escapes(self):
        result = CliRunner().invoke(cli.main, ["-h"], prog_name="cz", catch_exceptions=False)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("\nPackage\n", result.stdout)
        self.assertNotIn("\x1b", result.stdout)

    def test_styled_help_marks_headings(self):
        text = cli._render_main_help(click.Context(cli.main, info_name="cz"), cli.main, styled=True, width=100)
        self.assertIn("[bold]Package[/]", text)
        self.assertIn("[cyan]include PATH…[/]", text)

    def test_get_help_is_the_grouped_text(self):
        ctx = click.Context(cli.main, info_name="cz")
        self.assertIn("\nSet up\n", cli.main.get_help(ctx))

    def test_unregistered_table_entry_is_skipped(self):
        groups = [("Package", ["include", "not-a-command"]), *cli.HELP_GROUPS[1:]]
        with mock.patch.object(cli, "HELP_GROUPS", groups):
            text = _help()
        self.assertNotIn("not-a-command", text)

    def test_command_missing_from_the_table_goes_under_more(self):
        groups = [(title, [n for n in names if n != "stacks"]) for title, names in cli.HELP_GROUPS]
        with mock.patch.object(cli, "HELP_GROUPS", groups):
            text = _help()
        self.assertIn("stacks", _rows(text, "More"))
        self.assertNotIn("stacks", _rows(text, "Set up"))

    def test_more_is_created_when_the_table_has_none(self):
        groups = [(title, names) for title, names in cli.HELP_GROUPS if title != "More"]
        with mock.patch.object(cli, "HELP_GROUPS", groups):
            text = _help()
        self.assertEqual(sorted(_rows(text, "More")), ["mcp", "watch"])


class SubcommandHelpTests(TempProject):
    def test_every_subcommand_has_examples_first(self):
        ctx = click.Context(cli.main)
        for name in cli.main.list_commands(ctx):
            cmd = cli.main.get_command(ctx, name)
            text = _help(name)
            self.assertIn("Examples", text, name)
            body = text.split("Examples", 1)[1]
            examples = [line for line in body.splitlines()[1:6] if line.strip()]
            self.assertGreaterEqual(len(examples), 2, f"{name}: {examples}")
            # The Examples block comes before any other section of the docstring.
            intro = text.split("Examples", 1)[0]
            self.assertNotIn("HOW IT WORKS", intro, name)

    def test_apply_help_keeps_clicks_layout(self):
        text = _help("apply")
        self.assertTrue(text.startswith("Usage: cz apply [OPTIONS] [ZIP]"))
        self.assertIn("apply-zip, still works", text)


class ApplyAliasTests(TempProject):
    def test_same_code_and_hidden(self):
        ctx = click.Context(cli.main)
        apply_cmd = cli.main.get_command(ctx, "apply")
        alias = cli.main.get_command(ctx, "apply-zip")
        self.assertIs(apply_cmd.callback, alias.callback)
        self.assertTrue(alias.hidden)
        self.assertFalse(apply_cmd.hidden)
        self.assertNotIn("apply-zip", _help())

    def test_both_names_run(self):
        self._cwd = os.getcwd()
        os.chdir(self.root)
        try:
            self.write("a.py", "print(1)\n")
            for name in ("apply", "apply-zip"):
                result = CliRunner().invoke(cli.main, [name, "missing.zip", "--json"], catch_exceptions=False)
                self.assertEqual(result.exit_code, 1)
                self.assertIn('"command": "apply-zip"', result.stdout)  # the JSON contract keeps the old name
        finally:
            os.chdir(self._cwd)

    def test_alias_is_not_offered_by_completion(self):
        from click.shell_completion import ShellComplete

        comp = ShellComplete(cli.main, {}, "cz", "_CZ_COMPLETE")
        names = [c.value for c in comp.get_completions([], "app")]
        self.assertEqual(names, ["apply"])


class TypoTests(TempProject):
    def invoke(self, *args: str):
        return CliRunner().invoke(cli.main, list(args), prog_name="cz", catch_exceptions=False)

    def test_close_match(self):
        result = self.invoke("aply")
        self.assertEqual(result.exit_code, 2)
        self.assertIn('✗ contextzip has no command "aply".', result.output)
        self.assertIn("Did you mean: apply?", result.output)
        self.assertIn("Run cz --help to see them all.", result.output)

    def test_no_match(self):
        result = self.invoke("zzzz")
        self.assertEqual(result.exit_code, 2)
        self.assertNotIn("Did you mean", result.output)
        self.assertIn("Run cz --help to see them all.", result.output)

    def test_a_path_points_to_include(self):
        result = self.invoke("src/")
        self.assertEqual(result.exit_code, 2)
        self.assertIn("To package only that path: cz include src/", result.output)

    def test_options_and_commands_still_work(self):
        self.assertEqual(self.invoke("--nope").exit_code, 2)
        self.assertIn("No such option", self.invoke("--nope").output)
        self.assertEqual(self.invoke("stacks", "--json").exit_code, 0)

    def test_library_callers_get_clicks_usage_error(self):
        # contextzip.web.run calls main(standalone_mode=False) and turns UsageError into a result.
        with self.assertRaises(click.UsageError):
            cli.main.main(["aply"], prog_name="cz", standalone_mode=False)

    def test_repository_shortcut_is_not_a_typo(self):
        from contextzip.remote import RemoteSpec

        seen = []

        def fake_fetch(target, ref):
            seen.append(target)
            raise SystemExit(0)

        with mock.patch.object(cli, "_fetch_remote", side_effect=fake_fetch):
            result = self.invoke("github.com/user/repo", "-n")
        self.assertEqual(seen, ["github.com/user/repo"])
        self.assertNotIn("no command", result.output)
        del RemoteSpec


class VersionTests(TempProject):
    def test_version_lines(self):
        result = CliRunner().invoke(cli.main, ["--version"], catch_exceptions=False)
        self.assertEqual(result.exit_code, 0)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], f"contextzip {__version__}")
        self.assertTrue(lines[1].startswith(f"Python {platform.python_version()} · "))
        self.assertRegex(lines[1], r"· (pip|pipx|uv tool|uv|standalone build \(.+\))$")

    def test_install_method(self):
        from contextzip import install_info as updater

        def install(**kw):
            values = dict(executable="/usr/bin/python3", prefix="/nope", in_venv=False, has_pip=True)
            values.update(kw)
            return updater.PythonInstall(**values)

        with mock.patch.object(updater, "binary_install", return_value=None):
            self.assertEqual(updater.install_method(install()), "pip")
            self.assertEqual(updater.install_method(install(executable="/home/u/.local/pipx/venvs/contextzip/bin/python")), "pipx")
            self.assertEqual(updater.install_method(install(executable="/home/u/.local/share/uv/tools/contextzip/bin/python")), "uv tool")
        with mock.patch.object(updater, "binary_install", return_value=updater.BinaryInstall("Homebrew", "brew upgrade contextzip")):
            self.assertEqual(updater.install_method(), "standalone build (Homebrew)")
