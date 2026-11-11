from __future__ import annotations

import unittest

from contextzip.guide import find_in_zip, parse, render


class GuideTests(unittest.TestCase):
    def test_template_examples_never_count(self):
        g = parse(render(snapshot="14-k3f9a2", project="shop", ecosystem="Next.js"))
        self.assertEqual(g.snapshot, "14-k3f9a2")
        self.assertEqual((g.moves, g.deletions), ([], []))

    def test_changes_are_parsed(self):
        text = render(snapshot="3-abc123", project="shop") + (
            "moved: src/old.ts -> src/new.ts\n"
            "- moved: `lib/a.py` → `lib/b.py`\n"
            "deleted: src/unused.ts\n"
            "deleted: ./tmp/x.js\n"
        )
        g = parse(text)
        self.assertEqual(g.moves, [("src/old.ts", "src/new.ts"), ("lib/a.py", "lib/b.py")])
        self.assertEqual(g.deletions, ["src/unused.ts", "tmp/x.js"])

    def test_size_line_with_tokens(self):
        from contextzip.tokens import TokenReport

        report = TokenReport(total=46210, method="o200k_base", by_file={"a.py": 46210})
        text = render(snapshot="14-k3f9a2", project="shop", tokens=report, files=44)
        self.assertIn("Snapshot: 14-k3f9a2\nSize: 44 files, 46,210 tokens (o200k_base).\n\n## Replying", text)
        self.assertEqual(parse(text).snapshot, "14-k3f9a2")

        estimate = TokenReport(total=1, method="estimate", by_file={"a.py": 1})
        self.assertIn("Size: 1 file, ≈ 1 tokens (estimate).", render(snapshot="1-a", project="x", tokens=estimate, files=1))

    def test_no_tokens_renders_as_before(self):
        text = render(snapshot="3-abc123", project="shop")
        self.assertNotIn("Size:", text)
        self.assertIn("Snapshot: 3-abc123\n\n## Replying", text)

    def test_old_guide_without_size_line_still_parses(self):
        old = "# CONTEXTZIP.md\n\nSnapshot: 9-zzz111\n\n## Changes\n\ndeleted: a.ts\n"
        g = parse(old)
        self.assertEqual((g.snapshot, g.deletions), ("9-zzz111", ["a.ts"]))

    def test_found_at_any_depth(self):
        self.assertEqual(find_in_zip(["a/b/CONTEXTZIP.md", "x.ts", "wrap/contextzip.md"]), "wrap/contextzip.md")
        self.assertIsNone(find_in_zip(["README.md"]))


if __name__ == "__main__":
    unittest.main()
