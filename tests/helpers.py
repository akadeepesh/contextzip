"""Shared test helpers: throwaway projects in a temp folder."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from contextlib import contextmanager
from pathlib import Path

HAS_GIT = shutil.which("git") is not None


class TempProject(unittest.TestCase):
    """A fresh temp folder per test, with HOME pointed elsewhere so the
    "never above home" rule can't interfere with the test tree."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="cz-test-")
        self.tmp = Path(self._tmp.name).resolve()
        self.root = self.tmp / "proj"
        self.root.mkdir()
        self._env = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE", "CONTEXTZIP_ROOT", "CONTEXTZIP_WORKSPACE_LOCATION")}
        fake_home = self.tmp / "home"
        fake_home.mkdir()
        os.environ["HOME"] = str(fake_home)
        os.environ["USERPROFILE"] = str(fake_home)
        os.environ.pop("CONTEXTZIP_ROOT", None)
        os.environ.pop("CONTEXTZIP_WORKSPACE_LOCATION", None)

    def tearDown(self) -> None:
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    def write(self, rel: str, text: str | bytes, base: Path | None = None) -> Path:
        path = (base or self.root) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_bytes(text.encode("utf-8"))
        return path

    def read(self, rel: str) -> str:
        return (self.root / rel).read_bytes().decode("utf-8")

    def make_zip(self, name: str, files: dict[str, str | bytes]) -> Path:
        path = self.tmp / name
        with zipfile.ZipFile(path, "w") as zf:
            for arc, content in files.items():
                zf.writestr(arc, content.encode("utf-8") if isinstance(content, str) else content)
        return path

    def git(self, *args: str, cwd: Path | None = None) -> None:
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "core.autocrlf=false", *args],
            cwd=cwd or self.root,
            check=True,
            capture_output=True,
        )


def backdate(*paths: Path, seconds: int = 60) -> None:
    """Move files' modification time into the past, so caches that skip just-changed files will keep them."""
    import time

    when = time.time() - seconds
    for path in paths:
        targets = [p for p in path.rglob("*") if p.is_file()] if path.is_dir() else [path]
        for target in targets:
            os.utime(target, (when, when))


@contextmanager
def chdir(path: Path):
    prev = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(prev)
