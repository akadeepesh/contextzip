"""setup-path: finding the cz folder and adding it to PATH without duplicates."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from contextzip import pathsetup


class InPathValueTests(unittest.TestCase):
    def test_matches_ignoring_trailing_slash_and_quotes(self) -> None:
        d = Path(tempfile.gettempdir()) / "py" / "Scripts"
        value = os.pathsep.join(["/usr/bin", f'"{d}{os.sep}"'])
        self.assertTrue(pathsetup.in_path_value(d, value))

    def test_missing_dir(self) -> None:
        self.assertFalse(pathsetup.in_path_value(Path("/nowhere/bin"), os.pathsep.join(["/usr/bin", ""])))


class WithEntryTests(unittest.TestCase):
    def test_appends_and_drops_empty_entries(self) -> None:
        self.assertEqual(pathsetup.with_entry(r"C:\a;;C:\b;", Path(r"C:\c")), ";".join([r"C:\a", r"C:\b", str(Path(r"C:\c"))]))

    def test_already_present(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows path semantics")
        self.assertIsNone(pathsetup.with_entry(r"C:\a;c:\PY\Scripts\\", Path(r"C:\py\Scripts")))


class ShellRcTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self._zdotdir = os.environ.pop("ZDOTDIR", None)

    def tearDown(self) -> None:
        if self._zdotdir is not None:
            os.environ["ZDOTDIR"] = self._zdotdir
        self._tmp.cleanup()

    def test_picks_file_per_shell(self) -> None:
        self.assertEqual(pathsetup.shell_rc(self.home, "/bin/zsh"), self.home / ".zshrc")
        self.assertEqual(pathsetup.shell_rc(self.home, "/usr/bin/fish"), self.home / ".config" / "fish" / "config.fish")
        self.assertEqual(pathsetup.shell_rc(self.home, "/bin/sh"), self.home / ".profile")
        bash = ".bash_profile" if sys.platform == "darwin" else ".bashrc"
        self.assertEqual(pathsetup.shell_rc(self.home, "/bin/bash"), self.home / bash)

    def test_appends_once(self) -> None:
        rc = self.home / ".zshrc"
        rc.write_text("alias ll='ls -l'", encoding="utf-8")
        d = Path("/home/me/.local/bin")
        self.assertTrue(pathsetup.add_to_rc(d, rc))
        self.assertFalse(pathsetup.add_to_rc(d, rc))
        text = rc.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("alias ll='ls -l'\n"))
        self.assertEqual(text.count(pathsetup.MARKER), 1)
        self.assertIn(f'export PATH="{d}:$PATH"', text)

    def test_fish_syntax_and_new_file(self) -> None:
        rc = self.home / ".config" / "fish" / "config.fish"
        self.assertTrue(pathsetup.add_to_rc(Path("/opt/bin"), rc))
        self.assertIn(f'fish_add_path "{Path("/opt/bin")}"', rc.read_text(encoding="utf-8"))


class PowerShellCommandTests(unittest.TestCase):
    def test_escapes_quotes(self) -> None:
        cmd = pathsetup.powershell_command(Path("C:/Users/o'neil/Scripts"))
        self.assertIn("o''neil", cmd)
        self.assertIn("'User')", cmd)


class EntryPointTests(unittest.TestCase):
    def test_python_dash_m_runs(self) -> None:
        out = subprocess.run(
            [sys.executable, "-m", "contextzip", "--version"], capture_output=True, text=True, timeout=60
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("contextzip", out.stdout)

    def test_status_never_raises(self) -> None:
        st = pathsetup.status()
        if st.directory is not None:
            self.assertTrue(st.directory.is_dir())


if __name__ == "__main__":
    unittest.main()
