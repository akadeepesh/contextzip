"""Release 0.9 step 4: completions, init and doctor; the first-run prompt is now a hint."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

import click
from click.testing import CliRunner

from contextzip import cli, completions, doctor, pathsetup
from tests.helpers import TempProject
from tests.test_summary import _Env


def _invoke(*args: str, **kwargs):
    return CliRunner().invoke(cli.main, list(args), prog_name="cz", catch_exceptions=False, **kwargs)


class MarkedBlockTests(TempProject):
    def test_creates_the_file_and_its_folder(self):
        path = self.tmp / "a" / "b" / "rc"
        self.assertTrue(pathsetup.write_marked_block(path, "# >>> x", "# <<< x", "echo hi"))
        self.assertEqual(path.read_text(encoding="utf-8"), "# >>> x\necho hi\n# <<< x\n")

    def test_appends_after_existing_content_and_replaces_on_the_second_run(self):
        path = self.tmp / "rc"
        path.write_text("export A=1\n# after\n", encoding="utf-8")
        pathsetup.write_marked_block(path, "# >>> x", "# <<< x", "one")
        pathsetup.write_marked_block(path, "# >>> x", "# <<< x", "two")
        text = path.read_text(encoding="utf-8")
        self.assertEqual(text.count("# >>> x"), 1)
        self.assertIn("two", text)
        self.assertNotIn("one", text)
        self.assertTrue(text.startswith("export A=1\n# after\n"))

    def test_an_unchanged_block_is_not_rewritten(self):
        path = self.tmp / "rc"
        pathsetup.write_marked_block(path, "# >>> x", "# <<< x", "same")
        self.assertFalse(pathsetup.write_marked_block(path, "# >>> x", "# <<< x", "same"))

    def test_block_in_the_middle_keeps_what_follows(self):
        path = self.tmp / "rc"
        path.write_bytes(b"a\r\n# >>> x\r\nold\r\n# <<< x\r\nb\r\n")
        pathsetup.write_marked_block(path, "# >>> x", "# <<< x", "new")
        self.assertEqual(path.read_bytes(), b"a\r\n# >>> x\r\nnew\r\n# <<< x\r\nb\r\n")  # CRLF kept

    def test_a_missing_end_marker_replaces_to_the_end(self):
        path = self.tmp / "rc"
        path.write_text("a\n# >>> x\nbroken\n", encoding="utf-8")
        pathsetup.write_marked_block(path, "# >>> x", "# <<< x", "new")
        self.assertEqual(path.read_text(encoding="utf-8"), "a\n# >>> x\nnew\n# <<< x\n")


class DetectShellTests(TempProject):
    def test_posix(self):
        self.assertEqual(completions.detect_shell({"SHELL": "/bin/zsh"}, "posix"), "zsh")
        self.assertEqual(completions.detect_shell({"SHELL": "/usr/bin/fish"}, "posix"), "fish")
        self.assertEqual(completions.detect_shell({"SHELL": "/usr/bin/pwsh"}, "posix"), "powershell")
        self.assertIsNone(completions.detect_shell({"SHELL": "/bin/tcsh"}, "posix"))
        self.assertIsNone(completions.detect_shell({}, "posix"))

    def test_windows(self):
        self.assertEqual(completions.detect_shell({}, "nt"), "powershell")
        self.assertEqual(completions.detect_shell({"SHELL": "/usr/bin/bash"}, "nt"), "bash")  # Git Bash


class CompletionScriptTests(TempProject):
    def test_each_shell_covers_both_names(self):
        for shell, needles in {
            "bash": ("_CZ_COMPLETE", "_CONTEXTZIP_COMPLETE"),
            "zsh": ("_CZ_COMPLETE", "_CONTEXTZIP_COMPLETE", "compinit"),
            "fish": ("--command cz", "--command contextzip"),
            "powershell": ("'contextzip', 'cz'", "Register-ArgumentCompleter", "completions --complete"),
        }.items():
            result = _invoke("completions", shell)
            self.assertEqual(result.exit_code, 0, result.output)
            for needle in needles:
                self.assertIn(needle, result.stdout, f"{shell}: {needle}")
            self.assertNotIn("✓", result.stdout)  # the script and nothing else

    def test_detected_shell_is_used(self):
        with mock.patch.object(completions, "detect_shell", return_value="fish"):
            result = _invoke("completions")
        self.assertIn("--command cz", result.stdout)

    def test_unknown_shell(self):
        with mock.patch.object(completions, "detect_shell", return_value=None):
            result = _invoke("completions")
        self.assertEqual(result.exit_code, 1)
        self.assertIn("Couldn't tell which shell you use.", result.stdout)
        self.assertIn("zsh, fish, powershell", result.stdout)
        result = _invoke("completions", "tcsh")
        self.assertEqual(result.exit_code, 1)
        self.assertIn("bash, zsh, fish and powershell", result.stdout)

    def test_hidden_complete_mode(self):
        result = _invoke("completions", "--complete", "--")
        names = result.stdout.split()
        for name in ("apply", "include", "doctor", "completions"):
            self.assertIn(name, names)
        self.assertNotIn("apply-zip", names)
        result = _invoke("completions", "--complete", "--", "--")
        options = result.stdout.split()
        self.assertIn("--dry-run", options)
        self.assertIn("--json", options)
        result = _invoke("completions", "--complete", "--", "include", "--dr")
        self.assertEqual(result.stdout.split(), ["--dry-run"])
        result = _invoke("completions", "--complete", "--new", "--", "apply")
        self.assertEqual(result.stdout.split(), [])  # a path comes next: left to the shell

    def test_complete_is_hidden_from_help(self):
        result = _invoke("completions", "--help")
        self.assertNotIn("--complete", result.stdout)
        self.assertIn("--install", result.stdout)


class InstallCompletionTests(TempProject):
    def home(self) -> Path:
        return Path(os.environ["HOME"])

    def test_bash_install_twice_is_one_block(self):
        with _Env(SHELL="/bin/bash"), mock.patch("sys.platform", "linux"):
            first = _invoke("completions", "--install")
            second = _invoke("completions", "--install")
        self.assertEqual(first.exit_code, 0, first.output)
        self.assertEqual(second.exit_code, 0, second.output)
        rc = self.home() / ".bashrc"
        text = rc.read_text(encoding="utf-8")
        self.assertEqual(text.count(completions.START), 1)
        self.assertEqual(text.count(completions.END), 1)
        self.assertIn("_CZ_COMPLETE", text)
        out = " ".join(first.stdout.split())
        self.assertIn("✓ Completions installed for bash · ~", out)
        self.assertIn("Open a new terminal to use them.", out)

    def test_zsh_respects_zdotdir(self):
        zdot = self.tmp / "zdot"
        with _Env(SHELL="/bin/zsh", ZDOTDIR=str(zdot)):
            _invoke("completions", "--install")
        self.assertIn(completions.START, (zdot / ".zshrc").read_text(encoding="utf-8"))

    def test_fish_gets_its_own_files(self):
        with _Env(SHELL="/usr/bin/fish", XDG_CONFIG_HOME=None):
            _invoke("completions", "--install")
            _invoke("completions", "--install")
        folder = self.home() / ".config" / "fish" / "completions"
        for prog in ("cz", "contextzip"):
            text = (folder / f"{prog}.fish").read_text(encoding="utf-8")
            self.assertEqual(text.count(completions.START), 1)
            self.assertIn(f"--command {prog}", text)

    def test_powershell_profiles_and_policy_hint(self):
        profile = self.home() / "Documents" / "PowerShell" / "profile.ps1"
        with mock.patch.object(completions, "powershell_profiles", return_value=[(profile, "Restricted")]):
            first = _invoke("completions", "powershell", "--install")
            _invoke("completions", "powershell", "--install")
        text = profile.read_text(encoding="utf-8")
        self.assertEqual(text.count(completions.START), 1)
        self.assertIn("Register-ArgumentCompleter", text)
        self.assertIn("Set-ExecutionPolicy -Scope CurrentUser RemoteSigned", " ".join(first.stdout.split()))

    def test_policy_check(self):
        self.assertTrue(completions.blocks_policy("Restricted"))
        self.assertTrue(completions.blocks_policy("AllSigned"))
        self.assertFalse(completions.blocks_policy("RemoteSigned"))
        self.assertFalse(completions.blocks_policy(None))


class InitTests(TempProject):
    def setUp(self) -> None:
        super().setUp()
        self._cwd = os.getcwd()
        os.chdir(self.root)
        self.write("pyproject.toml", "[project]\nname = 'demo'\n")
        self.write("app/main.py", "print('hi')\n")

    def tearDown(self) -> None:
        os.chdir(self._cwd)
        super().tearDown()

    @property
    def config(self) -> Path:
        return self.root / ".contextzip" / "config.json"

    def test_fresh_project_is_set_up(self):
        result = _invoke("init")  # not interactive: writes without asking
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(json.loads(self.config.read_text(encoding="utf-8")), {"always_include": [], "always_exclude": []})
        self.assertTrue((self.root / ".contextzip" / ".gitignore").is_file())
        out = result.stdout
        self.assertIn("Saved", out)
        self.assertRegex(out, r"cz\s+package it")
        self.assertRegex(out, r"cz pick\s+choose files by hand")
        self.assertRegex(out, r"cz apply\s+write the AI's reply back")
        self.assertIn("Scanned", out)  # the dry scan's summary

    def test_fancy_summary_has_no_dry_run_footer(self):
        with _Env(CONTEXTZIP_FANCY="1"):
            result = _invoke("init")
        self.assertIn("contextzip · proj", result.stdout)
        self.assertNotIn("Run it again without --dry-run", result.stdout)

    def test_configured_project_is_left_alone(self):
        self.write(".contextzip/config.json", '{"always_exclude": ["docs/"]}\n')
        before = self.config.read_bytes()
        result = _invoke("init")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertIn("This project is already set up", result.stdout)
        self.assertIn("cz pick", result.stdout)
        self.assertIn("cz config --ui", result.stdout)
        self.assertNotIn("Scanned", result.stdout)

    def test_yes_never_prompts(self):
        with mock.patch.object(cli, "_interactive", return_value=True), mock.patch.object(click, "confirm", side_effect=AssertionError("asked")):
            result = _invoke("init", "--yes")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertTrue(self.config.is_file())

    def test_interactive_no_saves_nothing(self):
        with mock.patch.object(cli, "_interactive", return_value=True), mock.patch.object(click, "confirm", return_value=False) as confirm:
            result = _invoke("init")
        self.assertEqual(result.exit_code, 0)
        self.assertFalse(self.config.exists())
        self.assertIn("Save these defaults for this project?", confirm.call_args[0][0])

    def test_json(self):
        result = _invoke("init", "--json")
        data = json.loads(result.stdout)
        self.assertTrue(data["ok"])
        self.assertEqual(data["command"], "init")
        self.assertTrue(data["created"])
        self.assertEqual(Path(data["config"]), self.config)
        self.assertIn("files", data)


class FirstRunTests(TempProject):
    def setUp(self) -> None:
        super().setUp()
        self._cwd = os.getcwd()
        os.chdir(self.root)
        self.write("app.py", "print(1)\n")

    def tearDown(self) -> None:
        os.chdir(self._cwd)
        super().tearDown()

    def test_hint_once_and_no_prompt(self):
        with mock.patch.object(cli, "_interactive", return_value=True), mock.patch.object(click, "confirm", side_effect=AssertionError("asked")):
            first = _invoke("--no-clipboard")
            second = _invoke("--no-clipboard")
        self.assertEqual(first.exit_code, 0, first.output)
        hint = "First run here. cz init sets defaults, cz pick chooses files by hand."
        self.assertIn(hint, " ".join(first.stdout.split()))
        self.assertNotIn("First run here", second.stdout)

    def test_no_hint_with_a_config_or_under_json(self):
        result = _invoke("--no-clipboard", "--json")
        self.assertNotIn("First run here", result.stdout)

    def test_the_old_prompt_is_gone(self):
        self.assertFalse(hasattr(cli, "_maybe_offer_config_ui"))
        from contextzip import config

        self.assertFalse(hasattr(config, "save_config_ui_dismissed"))

    def test_old_config_files_with_the_dismissed_key_still_load(self):
        from contextzip import config

        config._CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        saved = config._CONFIG_FILE.read_text(encoding="utf-8") if config._CONFIG_FILE.exists() else None
        try:
            config._CONFIG_FILE.write_text('{"config_ui_prompt_dismissed": true}', encoding="utf-8")
            self.assertIsNone(config.get_workspace_location())
            result = _invoke("--no-clipboard")
            self.assertEqual(result.exit_code, 0, result.output)
        finally:
            if saved is None:
                config._CONFIG_FILE.unlink()
            else:
                config._CONFIG_FILE.write_text(saved, encoding="utf-8")


class DoctorTests(TempProject):
    NAMES = ["Version", "PATH", "Python", "Clipboard", "Tokenizer", "git", "Project", "Gemini key", "Terminal"]

    def setUp(self) -> None:
        super().setUp()
        self._cwd = os.getcwd()
        os.chdir(self.root)
        self.write("app.py", "print(1)\n")
        from contextzip import clipboard, updater

        boom = AssertionError("must not be called")
        self._patches = [
            mock.patch.object(clipboard, "handle", side_effect=boom),
            mock.patch.object(clipboard, "_handle", side_effect=boom),
            mock.patch.object(updater, "check_latest_version", side_effect=boom),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()
        os.chdir(self._cwd)
        super().tearDown()

    def test_all_nine_checks(self):
        result = _invoke("doctor")
        self.assertEqual(result.exit_code, 0, result.output)
        lines = result.stdout.splitlines()
        for name in self.NAMES:
            self.assertTrue(any(line[2:].startswith(name) for line in lines if line[:1] in "✓!"), name)
        self.assertRegex(result.stdout, r"(✓ Everything looks fine\.|! \d+ things? to look at\.)")

    def test_a_crashing_check_is_reported(self):
        with mock.patch.object(doctor, "check_git", side_effect=RuntimeError("kaboom")):
            result = _invoke("doctor")
        self.assertEqual(result.exit_code, 0)
        self.assertIn("! git", result.stdout)
        self.assertIn("could not check (RuntimeError: kaboom)", result.stdout)
        self.assertIn("Terminal", result.stdout)  # the checks after it still ran

    def test_strict(self):
        bad = doctor.Check("git", False, "git not found", "install git")
        with mock.patch.object(doctor, "check_git", return_value=bad):
            relaxed = _invoke("doctor")
            strict = _invoke("doctor", "--strict")
        self.assertEqual(relaxed.exit_code, 0)
        self.assertEqual(strict.exit_code, 1)
        self.assertIn("Try: install git", strict.stdout)
        self.assertRegex(strict.stdout, r"! \d+ things? to look at\.")

    def test_strict_passes_when_all_is_fine(self):
        fine = [doctor.Check(n, True, "fine") for n in self.NAMES]
        with mock.patch.object(doctor, "run_checks", return_value=fine):
            result = _invoke("doctor", "--strict")
        self.assertEqual(result.exit_code, 0)
        self.assertIn("✓ Everything looks fine.", result.stdout)

    def test_json_shape(self):
        result = _invoke("doctor", "--json")
        data = json.loads(result.stdout)
        self.assertEqual([c["name"] for c in data["checks"]], self.NAMES)
        for check in data["checks"]:
            self.assertEqual(set(check), {"name", "ok", "detail", "fix"})
            self.assertIsInstance(check["ok"], bool)

    def test_gemini_key_is_never_printed(self):
        with _Env(GEMINI_API_KEY="AIzaSySECRETSECRETSECRETSECRET1234567"):
            result = _invoke("doctor", "--json")
        self.assertNotIn("SECRETSECRET", result.stdout)
        gemini = next(c for c in json.loads(result.stdout)["checks"] if c["name"] == "Gemini key")
        self.assertIn("set", gemini["detail"])

    def test_online_asks_pypi(self):
        from contextzip import updater

        check = updater.VersionCheck(current="0.1.0", latest="9.9.9")
        with mock.patch.object(updater, "check_latest_version", return_value=check):
            result = _invoke("doctor", "--online", "--json")
        version = json.loads(result.stdout)["checks"][0]
        self.assertFalse(version["ok"])
        self.assertIn("9.9.9 is available", version["detail"])

    def test_old_python_warns_about_mcp(self):
        with mock.patch("sys.version_info", (3, 9, 18)):
            check = doctor.check_python()
        self.assertFalse(check.ok)
        self.assertIn("MCP server needs 3.10", check.detail)

    def test_missing_git(self):
        with mock.patch("shutil.which", return_value=None):
            check = doctor.check_git()
        self.assertFalse(check.ok)
        self.assertIn("only --git-changes needs it", check.detail)

    def test_headless_linux_clipboard(self):
        with mock.patch("platform.system", return_value="Linux"), _Env(DISPLAY=None, WAYLAND_DISPLAY=None):
            check = doctor.check_clipboard()
        self.assertTrue(check.ok)
        self.assertIn("prints the ZIP's path", check.detail)
