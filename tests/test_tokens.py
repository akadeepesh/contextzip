"""tokens.py: the real tokenizer, its offline fallback, per-file reports and the count cache."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest import mock

from contextzip import tokens
from contextzip.tokens import (
    METHOD_ESTIMATE,
    METHOD_EXACT,
    TokenReport,
    count_files,
    estimate_tokens,
    format_tokens,
    tokenizer_available,
)
from tests.helpers import TempProject


class _FakeEncoding:
    """Stands in for tiktoken's encoding so exact-mode code paths run without the vocabulary."""

    def encode_ordinary(self, text: str) -> list[int]:
        return list(range(len(text.split())))


def _exact_mode():
    """Patch the tokenizer in as available, backed by _FakeEncoding (one token per word)."""
    return mock.patch.multiple(tokens, tokenizer_available=lambda: True, _encoding=lambda: _FakeEncoding())


class EstimateAndFormatTests(unittest.TestCase):
    def test_estimate_unchanged(self):
        self.assertEqual(estimate_tokens(0), 0)
        self.assertEqual(estimate_tokens(9), 3)
        self.assertEqual(estimate_tokens(-5), 0)
        self.assertEqual(estimate_tokens(850 * 4), 850)

    def test_format_estimate_unchanged(self):
        self.assertEqual(format_tokens(0), "≈ 0 tokens")
        self.assertEqual(format_tokens(9), "≈ 9 tokens")
        self.assertEqual(format_tokens(850), "≈ 850 tokens")
        self.assertEqual(format_tokens(4200), "≈ 4.2k tokens")
        self.assertEqual(format_tokens(46000), "≈ 46k tokens")
        self.assertEqual(format_tokens(1_300_000), "≈ 1.3M tokens")

    def test_format_exact(self):
        self.assertEqual(format_tokens(46210, exact=True), "46,210 tokens")
        self.assertEqual(format_tokens(1, exact=True), "1 token")
        self.assertEqual(format_tokens(0, exact=True), "0 tokens")
        self.assertEqual(format_tokens(1_300_000, exact=True), "1,300,000 tokens")


class TokenReportTests(unittest.TestCase):
    def setUp(self):
        self.report = TokenReport(
            total=100,
            method=METHOD_EXACT,
            by_file={
                "README.md": 5,
                "src/app/main.py": 30,
                "src/app/util.py": 10,
                "src/db.py": 20,
                "tests/test_app.py": 25,
                "setup.py": 10,
            },
        )

    def test_by_dir_depth_1(self):
        self.assertEqual(self.report.by_dir(), [("src/", 60), ("tests/", 25), ("(root)", 15)])

    def test_by_dir_depth_2(self):
        self.assertEqual(
            self.report.by_dir(2),
            [("src/app/", 40), ("tests/", 25), ("src/", 20), ("(root)", 15)],
        )

    def test_by_dir_ties_sorted_by_name(self):
        report = TokenReport(total=20, method=METHOD_EXACT, by_file={"b/x": 10, "a/y": 10})
        self.assertEqual(report.by_dir(), [("a/", 10), ("b/", 10)])

    def test_top_files(self):
        self.assertEqual(
            self.report.top_files(3),
            [("src/app/main.py", 30), ("tests/test_app.py", 25), ("src/db.py", 20)],
        )
        # Ties by name.
        self.assertEqual(self.report.top_files(6)[-3:], [("setup.py", 10), ("src/app/util.py", 10), ("README.md", 5)])
        self.assertEqual(self.report.top_files(0), [])

    def test_exact_and_label(self):
        self.assertTrue(self.report.exact)
        self.assertEqual(self.report.label(), "100 tokens")
        estimate = TokenReport(total=46000, method=METHOD_ESTIMATE, by_file={})
        self.assertFalse(estimate.exact)
        self.assertEqual(estimate.label(), "≈ 46k tokens")


class CountFilesTests(TempProject):
    def test_binary_files_count_zero_and_stay_in_report(self):
        text = self.write("a.txt", "one two three")
        logo = self.write("logo.png", b"\x89PNG" + b"\x00" * 400)
        with _exact_mode():
            report = count_files([text, logo], self.root, binary=[logo])
        self.assertEqual(report.by_file, {"a.txt": 3, "logo.png": 0})
        self.assertEqual(report.total, 3)
        self.assertEqual(report.method, METHOD_EXACT)

    def test_unreadable_path_counts_zero(self):
        real = self.write("a.txt", "one two")
        with _exact_mode():
            report = count_files([self.root / "missing.txt", real], self.root)
        self.assertEqual(report.by_file, {"missing.txt": 0, "a.txt": 2})

    def test_by_file_follows_input_order(self):
        names = [f"d{i % 3}/f{i:02d}.txt" for i in range(40, 0, -1)]  # > 32 files: the thread pool path
        paths = [self.write(n, "w " * (i + 1)) for i, n in enumerate(names)]
        with _exact_mode():
            report = count_files(paths, self.root)
        self.assertEqual(list(report.by_file), names)
        self.assertEqual([report.by_file[n] for n in names], list(range(1, 41)))
        self.assertEqual(report.total, sum(range(1, 41)))

    def test_path_outside_project_keyed_by_name(self):
        outside = self.write("elsewhere/notes.txt", "a b c", base=self.tmp)
        with _exact_mode():
            report = count_files([outside], self.root)
        self.assertEqual(report.by_file, {"notes.txt": 3})

    def test_invalid_utf8_is_replaced_not_fatal(self):
        path = self.write("odd.txt", b"one \xff\xfe two")
        with _exact_mode():
            report = count_files([path], self.root)
        self.assertEqual(report.by_file["odd.txt"], 3)


class FallbackTests(TempProject):
    def test_per_extension_ratios(self):
        files = {
            "data.json": 320,  # 3.2 bytes per token
            "notes.md": 360,  # 3.6
            "app.py": 450,  # 4.5
            "app.ts": 420,  # 4.2
            "Makefile": 400,  # default 4.0
            "upper.JSON": 32,  # case-insensitive suffix
            "short.py": 1,  # rounds up
        }
        paths = [self.write(name, "x" * size) for name, size in files.items()]
        with mock.patch.object(tokens, "tokenizer_available", return_value=False):
            report = count_files(paths, self.root)
        self.assertEqual(report.method, METHOD_ESTIMATE)
        self.assertFalse(report.exact)
        self.assertEqual(
            report.by_file,
            {"data.json": 100, "notes.md": 100, "app.py": 100, "app.ts": 100, "Makefile": 100, "upper.JSON": 10, "short.py": 1},
        )

    def test_count_text_without_tokenizer_uses_four_bytes(self):
        with mock.patch.object(tokens, "tokenizer_available", return_value=False):
            self.assertEqual(tokens.count_text("x" * 9), 3)
            self.assertEqual(tokens.count_text(""), 0)

    def test_failed_load_falls_back_silently(self):
        path = self.write("app.py", "x" * 450)
        with mock.patch.multiple(tokens, tokenizer_available=lambda: True, _encoding=lambda: None):
            report = count_files([path], self.root)
        self.assertEqual(report.method, METHOD_ESTIMATE)
        self.assertEqual(report.total, 100)

    def test_missing_vocabulary_never_calls_tiktoken(self):
        with mock.patch.object(tokens, "_vocab_path", return_value=self.root / "nope"), mock.patch.object(
            tokens, "_available", None
        ), mock.patch.object(tokens, "_loaded", False), mock.patch.object(tokens, "_encoding_obj", None):
            self.assertFalse(tokenizer_available())
            self.assertIsNone(tokens._encoding())

    def test_damaged_vocabulary_never_calls_tiktoken(self):
        bad = self.write("vocab", "not a vocabulary")
        with mock.patch.object(tokens, "_vocab_path", return_value=bad), mock.patch.object(
            tokens, "_available", True
        ), mock.patch.object(tokens, "_loaded", False), mock.patch.object(tokens, "_encoding_obj", None), mock.patch.dict(
            "sys.modules", {"tiktoken": None}
        ):
            # Importing tiktoken would raise here; a hash mismatch must stop before that.
            self.assertIsNone(tokens._encoding())


class CacheTests(TempProject):
    def setUp(self):
        super().setUp()
        self.cache = self.root / ".contextzip" / "cache"
        self.a = self.write("a.txt", "one two three")
        self.b = self.write("b.txt", "four five")

    def _count(self, files=None):
        with _exact_mode(), mock.patch.object(tokens, "count_text", side_effect=lambda t: len(t.split())) as counter:
            report = count_files(files or [self.a, self.b], self.root, cache_dir=self.cache)
        return report, counter.call_count

    def test_second_run_reuses_counts(self):
        first, calls = self._count()
        self.assertEqual(calls, 2)
        self.assertEqual(first.by_file, {"a.txt": 3, "b.txt": 2})
        stored = json.loads((self.cache / "tokens.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["method"], METHOD_EXACT)
        self.assertEqual(set(stored["files"]), {"a.txt", "b.txt"})

        second, calls = self._count()
        self.assertEqual(calls, 0)
        self.assertEqual(second.by_file, first.by_file)

    def test_changed_file_invalidates_only_its_entry(self):
        self._count()
        self.write("b.txt", "four five six seven")
        report, calls = self._count()
        self.assertEqual(calls, 1)
        self.assertEqual(report.by_file, {"a.txt": 3, "b.txt": 4})

    def test_entries_for_files_not_in_run_are_dropped(self):
        self._count()
        self._count([self.a])
        stored = json.loads((self.cache / "tokens.json").read_text(encoding="utf-8"))
        self.assertEqual(set(stored["files"]), {"a.txt"})

    def test_method_change_ignores_cache(self):
        self._count()
        with mock.patch.object(tokens, "tokenizer_available", return_value=False):
            report = count_files([self.a, self.b], self.root, cache_dir=self.cache)
        self.assertEqual(report.method, METHOD_ESTIMATE)
        self.assertEqual(report.by_file, {"a.txt": 4, "b.txt": 3})  # 13 and 9 bytes / 4.0

    def test_corrupt_cache_is_ignored(self):
        for junk in ("{not json", "[]", '{"method": "o200k_base", "files": {"a.txt": [1, 2]}}'):
            self.cache.mkdir(parents=True, exist_ok=True)
            (self.cache / "tokens.json").write_text(junk, encoding="utf-8")
            report, calls = self._count()
            self.assertEqual(calls, 2, junk)
            self.assertEqual(report.by_file, {"a.txt": 3, "b.txt": 2})

    def test_cache_write_failure_is_ignored(self):
        with mock.patch.object(tokens.os, "replace", side_effect=OSError("disk full")):
            report, _ = self._count()
        self.assertEqual(report.total, 5)
        self.assertEqual([p.name for p in self.cache.iterdir()], [])  # temp file cleaned up

    def test_no_cache_dir_writes_nothing(self):
        with _exact_mode():
            count_files([self.a], self.root)
        self.assertFalse((self.root / ".contextzip").exists())


@unittest.skipUnless(tokenizer_available(), "tiktoken or the vendored vocabulary is not installed")
class RealTokenizerTests(unittest.TestCase):
    def test_hello_world(self):
        before = os.environ.get("TIKTOKEN_CACHE_DIR")
        self.assertEqual(tokens.count_text("hello world"), 2)
        self.assertEqual(os.environ.get("TIKTOKEN_CACHE_DIR"), before)

    def test_loading_restores_existing_cache_dir(self):
        with mock.patch.dict(os.environ, {"TIKTOKEN_CACHE_DIR": "somewhere-else"}), mock.patch.object(
            tokens, "_loaded", False
        ), mock.patch.object(tokens, "_encoding_obj", None):
            self.assertIsNotNone(tokens._encoding())
            self.assertEqual(os.environ["TIKTOKEN_CACHE_DIR"], "somewhere-else")

    def test_count_files_is_exact(self):
        report = count_files([Path(tokens.__file__)], Path(tokens.__file__).parent)
        self.assertEqual(report.method, METHOD_EXACT)
        self.assertGreater(report.total, 0)
