"""Release 0.9 step 6: quick commands import only what they need."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest

import contextzip

# Never imported by `import contextzip`, `cz --version`, `cz --help`, `cz stacks` or completion.
HEAVY = (
    "httpx",
    "contextzip.remote",
    "contextzip.pullrequest",
    "contextzip.applier",
    "contextzip.merge3",
    "contextzip.webui",
    "contextzip.updater",
    "contextzip.cli_ai",
    "contextzip.ai",
    "contextzip.watcher",
    "contextzip.error_parser",
    "contextzip.mcp_server",
    "contextzip.snapshots",
    "contextzip.clipboard",
    "contextzip.report",
    "contextzip.packager",
    "tiktoken",
    "textual",
    "zipfile",
)

RUNS = {
    "import": "import contextzip",
    "--version": "from contextzip.cli import main; main(['--version'])",
    "--help": "from contextzip.cli import main; main(['--help'])",
    "stacks": "from contextzip.cli import main; main(['stacks'])",
    "completion": "from contextzip.cli import main; main(['completions', '--complete', '--', 'ap'])",
}


# Prints every loaded module when the run exits, even through click's SystemExit.
_DUMP = (
    "import atexit, sys\n"
    "atexit.register(lambda: sys.__stdout__.write('\\n@@MODULES@@\\n' + '\\n'.join(sorted(sys.modules)) + '\\n'))\n"
)


def _imported(code: str, env: dict | None = None) -> set[str]:
    """
    Every module Python imported to run *code*: the "| module" lines of
    -X importtime's report on stderr, plus sys.modules when it exits (which
    also catches anything imported through importlib, which importtime
    doesn't report).
    """
    proc = subprocess.run(
        [sys.executable, "-X", "importtime", "-c", _DUMP + code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        env={**os.environ, **(env or {})},
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    modules = set()
    for line in proc.stderr.splitlines():
        if line.startswith("import time:") and "|" in line:
            name = line.rsplit("|", 1)[1].strip()
            if name and name != "imported package":
                modules.add(name)
    _out, _sep, dumped = proc.stdout.partition("@@MODULES@@")
    modules.update(line.strip() for line in dumped.splitlines() if line.strip())
    return modules


def _heavy(modules: set[str]) -> list[str]:
    return sorted(m for m in modules if any(m == h or m.startswith(h + ".") for h in HEAVY))


class StartupImportTests(unittest.TestCase):
    def test_quick_commands_stay_light(self):
        for label, code in RUNS.items():
            with self.subTest(run=label):
                modules = _imported(code)
                self.assertIn("contextzip", modules)  # the parser saw the report
                self.assertEqual(_heavy(modules), [], f"{label} imported heavy modules")

    def test_shell_completion_stays_light(self):
        code = "from contextzip.cli import main; main([], prog_name='cz')"
        env = {"_CZ_COMPLETE": "bash_complete", "COMP_WORDS": "cz ap", "COMP_CWORD": "1"}
        self.assertEqual(_heavy(_imported(code, env)), [])

    def test_packaging_still_loads_what_it_needs(self):
        # The opposite check, so the parser above can't pass by finding nothing.
        modules = _imported("import contextzip; contextzip.PackageResult")
        self.assertIn("contextzip.packager", modules)


class LazyPublicApiTests(unittest.TestCase):
    def test_names_import(self):
        from contextzip import ApplyResult, ContextzipError, PackageResult, get_files

        self.assertTrue(callable(get_files))
        self.assertEqual(PackageResult.__name__, "PackageResult")
        self.assertEqual(ApplyResult.__name__, "ApplyResult")
        self.assertTrue(issubclass(ContextzipError, Exception))

    def test_attribute_access_and_dir(self):
        self.assertIs(contextzip.get_files, __import__("contextzip.api", fromlist=["get_files"]).get_files)
        names = sorted(dir(contextzip))
        for name in contextzip.__all__:
            self.assertIn(name, names)
        self.assertIn("__version__", names)

    def test_unknown_attribute(self):
        with self.assertRaises(AttributeError):
            contextzip.not_a_thing  # noqa: B018
        self.assertFalse(hasattr(contextzip, "also_missing"))
