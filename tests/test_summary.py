"""Release 0.9 step 1: the designed package summary, and the plain log kept for pipes."""

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
from contextzip.cli_display import human_size, print_package_summary
from contextzip.clipboard import ClipboardResult, Tier
from contextzip.filters import ResolveResult
from contextzip.tokens import METHOD_ESTIMATE, METHOD_EXACT, TokenReport
from tests.helpers import TempProject, backdate


def _console(width: int = 80) -> tuple[Console, io.StringIO]:
    out = io.StringIO()
    return Console(file=out, width=width, color_system=None, legacy_windows=False), out


def _detection(*names: str, sources: dict | None = None, by_file_type: bool = False):
    return SimpleNamespace(
        is_unknown=not names,
        ecosystems=list(names),
        sources=sources or {},
        by_file_type=list(names) if by_file_type else [],
        confidence="high",
    )


class _Env:
    """Set environment variables for one test."""

    def __init__(self, **values: str | None):
        self.values = values
        self.saved: dict[str, str | None] = {}

    def __enter__(self):
        for key, value in self.values.items():
            self.saved[key] = os.environ.get(key)
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        return self

    def __exit__(self, *exc):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class HumanSizeTests(TempProject):
    def test_formats(self):
        self.assertEqual(human_size(0), "0 B")
        self.assertEqual(human_size(512), "512 B")
        self.assertEqual(human_size(186 * 1024), "186 KB")
        self.assertEqual(human_size(int(1.44 * 1024 * 1024)), "1.4 MB")
        self.assertEqual(human_size(1024 * 1024), "1.0 MB")
        self.assertEqual(human_size(14 * 1024 * 1024), "14 MB")
        self.assertEqual(human_size(int(2.1 * 1024 ** 3)), "2.1 GB")
        self.assertEqual(human_size(120 * 1024 ** 3), "120 GB")

    def test_never_shows_1024_of_a_unit(self):
        self.assertEqual(human_size(1023), "1023 B")
        self.assertEqual(human_size(int(1023.8 * 1024)), "1.0 MB")
        self.assertEqual(human_size(int(9.97 * 1024 * 1024)), "10 MB")


class FancyChoiceTests(TempProject):
    def test_forced_on_and_off(self):
        with _Env(CONTEXTZIP_FANCY="1"):
            self.assertTrue(cli_display.fancy())
        with _Env(CONTEXTZIP_FANCY="0"):
            self.assertFalse(cli_display.fancy())

    def test_not_a_terminal_is_plain(self):
        with _Env(CONTEXTZIP_FANCY=None), mock.patch("sys.stdout", io.StringIO()):
            self.assertFalse(cli_display.fancy())

    def test_plain_beats_the_override(self):
        with _Env(CONTEXTZIP_FANCY="1"), cli_display.plain_output(True):
            self.assertFalse(cli_display.fancy())
        with _Env(CONTEXTZIP_FANCY="1"):
            self.assertTrue(cli_display.fancy())  # restored afterwards

    def test_json_is_never_fancy(self):
        with _Env(CONTEXTZIP_FANCY="1"), jsonout.session("package", True):
            self.assertFalse(cli_display.fancy())

    def test_a_stream_without_block_characters_is_plain(self):
        class Tty(io.StringIO):
            encoding = "ascii"

            def isatty(self):
                return True

        with _Env(CONTEXTZIP_FANCY=None), mock.patch("sys.stdout", Tty()):
            self.assertFalse(cli_display.fancy())


class SummaryBlockTests(TempProject):
    def resolved(self, included: dict[str, int], excluded: list[str] = ()) -> ResolveResult:
        r = ResolveResult()
        for rel, size in included.items():
            p = self.root / rel
            r.included.append(p)
            r.sizes[p] = size
        r.excluded = [self.root / rel for rel in excluded]
        return r

    def render(self, *, by_file: dict[str, int], width: int = 80, exact: bool = True, **kwargs) -> str:
        con, out = _console(width)
        tokens = TokenReport(
            total=sum(by_file.values()), method=METHOD_EXACT if exact else METHOD_ESTIMATE, by_file=by_file
        )
        kwargs.setdefault("resolved", self.resolved({rel: 100 for rel in by_file}))
        kwargs.setdefault("detection", _detection("Next.js", "FastAPI", sources={"FastAPI": "backend"}))
        print_package_summary(name="my-app", project_dir=self.root, tokens=tokens, con=con, **kwargs)
        text = out.getvalue()
        for line in text.splitlines():
            self.assertLessEqual(len(line), width, f"line wider than {width}: {line!r}")
        return text

    def result(self, **kw):
        values = dict(
            file_count=44,
            uncompressed_bytes=186 * 1024,
            compressed_bytes=63 * 1024,
            snapshot="14-k3f9a2",
            zip_path=self.root / ".contextzip" / "output" / "codebase.zip",
            skipped_in_zip=[],
            redacted=[],
        )
        values.update(kw)
        return SimpleNamespace(**values)

    def test_header_and_stats(self):
        text = self.render(by_file={"src/a.ts": 46_210}, result=self.result())
        lines = text.splitlines()
        self.assertEqual(lines[0], "contextzip · my-app   Next.js + FastAPI (backend/)")
        self.assertIn("  44 files   186 KB → 63 KB zip   46,210 tokens", text)

    def test_header_for_file_types_and_unknown(self):
        text = self.render(by_file={"a.py": 10}, detection=_detection("Python", by_file_type=True))
        self.assertIn("Python (from file types)", text.splitlines()[0])
        text = self.render(by_file={"a.py": 10}, detection=_detection())
        self.assertIn("no known stack, universal rules", text.splitlines()[0])

    def test_dry_run_stats_and_ending(self):
        resolved = self.resolved({"src/a.ts": 2048, "src/b.ts": 1024})
        text = self.render(by_file={"src/a.ts": 300, "src/b.ts": 100}, resolved=resolved)
        self.assertIn("  2 files   3 KB   dry run   400 tokens", text)
        self.assertIn("Nothing was written. Run it again without --dry-run to make the ZIP.", text)
        self.assertNotIn("✓", text)

    def test_estimated_tokens_say_so(self):
        text = self.render(by_file={"src/a.ts": 46_400}, exact=False)
        self.assertIn("≈ 46k tokens", text)

    def test_folder_rows_order_and_more(self):
        by_file = {
            "src/app/page.ts": 18_200,
            "src/components/b.ts": 12_100,
            "backend/api/x.py": 9_800,
            "docs/a.md": 3_000,
            "scripts/b.sh": 2_000,
            "tools/c.py": 1_110,
        }
        text = self.render(by_file=by_file)
        rows = [line for line in text.splitlines() if "█" in line or "░" in line]
        self.assertEqual(len(rows), 4)
        self.assertTrue(rows[0].lstrip().startswith("src/app/"))
        self.assertTrue(rows[1].lstrip().startswith("src/components/"))
        self.assertTrue(rows[2].lstrip().startswith("backend/api/"))
        self.assertTrue(rows[3].lstrip().startswith("3 more folders"))
        self.assertIn("6,110", rows[3])
        self.assertIn("18,200  ████████", rows[0])
        self.assertTrue(rows[0].endswith("39%"))
        # columns line up: every bar starts in the same place
        self.assertEqual(len({row.index("█") for row in rows}), 1)

    def test_four_folders_need_no_more_row(self):
        by_file = {"a/x": 10, "b/x": 10, "c/x": 10, "d/x": 10}
        text = self.render(by_file=by_file)
        self.assertNotIn("more folders", text)
        self.assertEqual(sum(1 for line in text.splitlines() if "█" in line), 4)

    def test_one_folder_has_no_rows(self):
        text = self.render(by_file={"src/a.ts": 10, "src/b.ts": 20})
        self.assertNotIn("█", text)

    def test_a_tiny_share_gets_one_cell(self):
        text = self.render(by_file={"big/a": 100_000, "small/b": 1})
        small = next(line for line in text.splitlines() if "small/" in line)
        self.assertIn("█" + "░" * 19, small)
        self.assertIn("<1%", small)

    def test_long_paths_are_cut_from_the_left(self):
        long = "packages/very-long-package-name/src/"
        by_file = {long + "a.ts": 100, "lib/b.ts": 50}
        text = self.render(by_file=by_file)
        row = next(line for line in text.splitlines() if "…" in line)
        label = row.strip().split("  ")[0]
        self.assertEqual(len(label), 28)
        self.assertTrue(label.startswith("…"))
        self.assertTrue(label.endswith("-package-name/"))

    def test_narrow_terminal_drops_the_bars(self):
        by_file = {"src/app/a": 100, "src/lib/b": 50}
        text = self.render(by_file=by_file, width=50, result=self.result(), clipboard=None)
        self.assertNotIn("█", text)
        self.assertNotIn("░", text)
        row = next(line for line in text.splitlines() if "src/app/" in line)
        self.assertTrue(row.rstrip().endswith("67%"))

    def test_fits_line(self):
        fit = [
            {"name": "Claude", "share": 0.23, "exact": False, "fits": True},
            {"name": "ChatGPT", "share": 0.36, "exact": True, "fits": True},
        ]
        text = self.render(by_file={"a.ts": 10}, fit_rows=fit)
        self.assertIn("  Fits      Claude ≈23%  ·  ChatGPT 36%", text)

    def test_too_big_is_a_warning(self):
        fit = [{"name": "ChatGPT", "share": 1.4, "exact": True, "fits": False}]
        text = self.render(by_file={"a.ts": 10}, fit_rows=fit)
        self.assertIn("ChatGPT 140%, too big", text)
        self.assertIn("! Too big for ChatGPT.", text)

    def test_left_out_with_secrets(self):
        (self.root / "node_modules" / "x").mkdir(parents=True)
        (self.root / ".next").mkdir()
        excluded = [f"node_modules/x/{i}.js" for i in range(5)] + [".next/a", ".next/b", ".env", "conf/.env.local", "id_rsa"]
        resolved = self.resolved({"src/a.ts": 10}, excluded)
        text = self.render(by_file={"src/a.ts": 10}, resolved=resolved)
        self.assertIn("  Left out  10 files: node_modules/ (5), .next/ (2), .env (1), 3 secret files", text)

    def test_left_out_is_skipped_for_git_changes_and_when_nothing_is_left_out(self):
        resolved = self.resolved({"src/a.ts": 10}, ["x.log"])
        self.assertNotIn("Left out", self.render(by_file={"src/a.ts": 10}, resolved=resolved, git_mode=True))
        self.assertNotIn("Left out", self.render(by_file={"src/a.ts": 10}))

    def test_left_out_drops_groups_when_narrow(self):
        for name in ("aaaaaaaaaaaa", "bbbbbbbbbbbb", "cccccccccccc"):
            (self.root / name).mkdir()
        excluded = [f"{n}/f{i}" for n in ("aaaaaaaaaaaa", "bbbbbbbbbbbb", "cccccccccccc") for i in range(3)]
        resolved = self.resolved({"src/a.ts": 10}, excluded)
        text = self.render(by_file={"src/a.ts": 10}, resolved=resolved, width=50)
        line = next(line for line in text.splitlines() if "Left out" in line)
        self.assertIn("aaaaaaaaaaaa/ (3)", line)
        self.assertNotIn("cccccccccccc", line)

    def test_on_the_clipboard(self):
        cb = ClipboardResult(Tier.FILE_ON_CLIPBOARD, "copied")
        text = self.render(by_file={"a.ts": 10}, result=self.result(), clipboard=cb, hint="When the AI replies, run: cz apply")
        self.assertIn("✓ On your clipboard. Paste it into Claude or ChatGPT.   snapshot #14", text)
        self.assertTrue(text.rstrip().endswith("When the AI replies, run: cz apply"))
        self.assertNotIn("Full report", text)
        self.assertNotIn("Saved to", text)

    def test_clipboard_failure_keeps_its_messages(self):
        cb = ClipboardResult(Tier.FOLDER_OPENED, "opened", hint="Install wl-clipboard")
        text = self.render(by_file={"a.ts": 10}, result=self.result(), clipboard=cb)
        self.assertIn("✓ Saved to", text)
        self.assertIn("Couldn't copy the ZIP to the clipboard — opened its folder instead", text)
        self.assertIn("Install wl-clipboard", text)

    def test_no_clipboard_saves_a_relative_path(self):
        from tests.helpers import chdir

        with chdir(self.root):
            text = self.render(by_file={"a.ts": 10}, result=self.result())
        rel = os.path.join(".contextzip", "output", "codebase.zip")
        self.assertIn(f"✓ Saved to {rel}   snapshot #14", text)

    def test_outside_the_current_folder_the_path_is_absolute(self):
        from tests.helpers import chdir

        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        with chdir(elsewhere):
            text = self.render(by_file={"a.ts": 10}, result=self.result(), width=400)
        self.assertIn(str(self.root / ".contextzip" / "output" / "codebase.zip"), text)

    def test_report_only_when_asked(self):
        text = self.render(by_file={"a.ts": 10}, result=self.result(), report_path="r.txt")
        self.assertIn("Full report: r.txt", text)

    def test_warnings_sit_between_block_and_final_lines(self):
        def warnings():
            cli_display.warn("2 large files (≥ 1.0 MB) will be included", con=con)

        con, out = _console(80)
        tokens = TokenReport(total=10, method=METHOD_EXACT, by_file={"a.ts": 10})
        print_package_summary(
            name="my-app",
            detection=_detection("Python"),
            resolved=self.resolved({"a.ts": 10}),
            project_dir=self.root,
            tokens=tokens,
            result=self.result(),
            warnings=warnings,
            con=con,
        )
        text = out.getvalue()
        self.assertLess(text.index("10 tokens"), text.index("! 2 large files"))
        self.assertLess(text.index("! 2 large files"), text.index("✓ Saved to"))

    def test_bracketed_names_print_safely(self):
        text = self.render(by_file={"app/[id]/page.tsx": 10, "lib/a.ts": 5})
        self.assertIn("app/[id]/", text)


class SummaryCliTests(TempProject):
    def setUp(self) -> None:
        super().setUp()
        self._cwd = os.getcwd()
        os.chdir(self.root)
        self.write("package.json", '{"name": "demo"}\n')
        self.write("src/index.ts", "export const a = 1;\n" * 20)
        self.write("lib/util.ts", "export const b = 2;\n" * 10)
        self.write("node_modules/dep/index.js", "module.exports = 1;\n")
        self.write(".env", "TOKEN=abc\n")
        backdate(self.root)

    def tearDown(self) -> None:
        os.chdir(self._cwd)
        super().tearDown()

    def invoke(self, *args: str, fancy: str = "1"):
        with _Env(CONTEXTZIP_FANCY=fancy):
            result = CliRunner().invoke(cli.main, list(args), catch_exceptions=False)
        self.assertEqual(result.exit_code, 0, result.output)
        return result.stdout

    def test_fancy_dry_run(self):
        out = self.invoke("--dry-run")
        self.assertIn(f"contextzip · {self.root.name}   Node.js", out)
        self.assertIn("dry run", out)
        self.assertIn("Left out  ", out)
        self.assertIn("1 secret file", out)
        self.assertIn("Nothing was written.", out)
        self.assertNotIn("Detected", out)
        self.assertNotIn("Scanned", out)

    def test_fancy_package_without_clipboard(self):
        out = self.invoke("--no-clipboard")
        self.assertIn("✓ Saved to .contextzip", out)
        self.assertIn("snapshot #1", out)
        self.assertIn("When the AI replies, run: contextzip apply", out)
        self.assertNotIn("Full report", out)
        self.assertTrue((self.root / ".contextzip" / "output" / "codebase" / "codebase.zip").is_file())

    def test_fancy_verbose_shows_the_report(self):
        out = self.invoke("--no-clipboard", "--verbose")
        self.assertIn("Full report:", out)

    def test_fancy_package_on_the_clipboard(self):
        cb = ClipboardResult(Tier.FILE_ON_CLIPBOARD, "copied")
        with mock.patch.object(cli, "clipboard_handle", return_value=cb):
            out = self.invoke()
        self.assertIn("✓ On your clipboard. Paste it into Claude or ChatGPT.   snapshot #1", out)

    def test_plain_flag_wins(self):
        out = self.invoke("--dry-run", "--plain")
        self.assertIn("✓ Detected Node.js", out)
        self.assertIn("✓ Scanned", out)
        self.assertNotIn("contextzip · ", out)

    def test_plain_package_output_is_unchanged(self):
        out = " ".join(self.invoke("--no-clipboard", fancy="0").split())
        self.assertRegex(out, r"✓ Detected Node\.js · \w+ confidence")
        self.assertRegex(out, r"✓ Scanned \d+ files & Packed 3 files · 3 included \(\d+ B\), \d+ excluded, [\d.]+ K?B → [\d.]+ K?B, ")
        self.assertIn("tokens", out)
        self.assertRegex(out, r"✓ Saved to · .+codebase\.zip · snapshot #1 \(\w+\)")
        self.assertIn("Full report:", out)

    def test_plain_dry_run_output_is_unchanged(self):
        out = " ".join(self.invoke("--dry-run", fancy="0").split())
        self.assertRegex(out, r"✓ Detected Node\.js · \w+ confidence")
        self.assertRegex(out, r"✓ Scanned \d+ files · 3 included \(\d+ B\), \d+ excluded, [\d,≈ k]+ tokens")
        self.assertIn("Dry run — no ZIP created. Remove --dry-run to produce the archive.", out)

    def test_json_is_unchanged_by_the_fancy_switch(self):
        def run():
            result = CliRunner().invoke(cli.main, ["--dry-run", "--json"], catch_exceptions=False)
            data = json.loads(result.stdout)
            data.pop("log")
            return data

        with _Env(CONTEXTZIP_FANCY="1"):
            fancy = run()
        with _Env(CONTEXTZIP_FANCY="0"):
            plain = run()
        self.assertEqual(fancy, plain)
        self.assertTrue(fancy["ok"])

    def test_cz_is_named_when_typed(self):
        with _Env(CONTEXTZIP_FANCY="1"):
            result = CliRunner().invoke(cli.main, ["--no-clipboard"], prog_name="cz", catch_exceptions=False)
        self.assertIn("run: cz apply", result.stdout)
