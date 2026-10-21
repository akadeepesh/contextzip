from __future__ import annotations

import unittest

from contextzip.merge3 import merge3


def lines(s: str) -> list[str]:
    return s.split("\n")


BASE = lines("a\nb\nc\nd\ne\nf")


class Merge3Tests(unittest.TestCase):
    def test_changes_in_different_places_merge(self):
        ours = lines("A\nb\nc\nd\ne\nf")
        theirs = lines("a\nb\nc\nd\ne\nF")
        r = merge3(BASE, ours, theirs)
        self.assertTrue(r.clean)
        self.assertEqual(r.lines, lines("A\nb\nc\nd\ne\nF"))

    def test_adjacent_changes_merge(self):
        ours = lines("a\nB\nc\nd\ne\nf")
        theirs = lines("a\nb\nC\nd\ne\nf")
        r = merge3(BASE, ours, theirs)
        self.assertTrue(r.clean)
        self.assertEqual(r.lines, lines("a\nB\nC\nd\ne\nf"))

    def test_same_change_on_both_sides_is_clean(self):
        both = lines("a\nb\nX\nd\ne\nf")
        r = merge3(BASE, both, both)
        self.assertTrue(r.clean)
        self.assertEqual(r.lines, both)

    def test_different_changes_to_the_same_line_conflict(self):
        r = merge3(BASE, lines("a\nb\nOURS\nd\ne\nf"), lines("a\nb\nTHEIRS\nd\ne\nf"))
        self.assertFalse(r.clean)
        self.assertEqual(len(r.conflicts), 1)
        self.assertEqual(r.conflicts[0].ours, ["OURS"])
        self.assertEqual(r.conflicts[0].theirs, ["THEIRS"])

    def test_insertions_at_the_same_point_conflict(self):
        r = merge3(BASE, lines("a\nb\nmine\nc\nd\ne\nf"), lines("a\nb\nyours\nc\nd\ne\nf"))
        self.assertFalse(r.clean)

    def test_insertions_in_different_places_merge(self):
        r = merge3(BASE, lines("first\na\nb\nc\nd\ne\nf"), lines("a\nb\nc\nd\ne\nf\nlast"))
        self.assertTrue(r.clean)
        self.assertEqual(r.lines, lines("first\na\nb\nc\nd\ne\nf\nlast"))

    def test_untouched_side_takes_the_other(self):
        theirs = lines("a\nb\nc\nd\ne\nf\ng")
        self.assertEqual(merge3(BASE, BASE, theirs).lines, theirs)
        self.assertEqual(merge3(BASE, theirs, BASE).lines, theirs)

    def test_redacted_lines_keep_real_values(self):
        base = lines("A=[REDACTED]\nB=[REDACTED]\n")
        ours = lines("A=real-a\nB=real-b\n")
        theirs = lines("A=[REDACTED]\nB=[REDACTED]\nC=new\n")
        r = merge3(base, ours, theirs)
        self.assertTrue(r.clean)
        self.assertEqual(r.lines, lines("A=real-a\nB=real-b\nC=new\n"))

    # ── Deletions next to each other ────────────────────────────────────────
    #
    # A deletion removes base lines. When the other side's change merely
    # touches that range, the other side kept the deleted lines — so the two
    # changes cannot both be honoured and the merge has to ask, not guess.

    def test_each_side_deleting_a_different_line_conflicts(self):
        # We deleted "b", the AI deleted "a". Both kept the other's line.
        r = merge3(lines("a\nb"), lines("a"), lines("b"))
        self.assertFalse(r.clean, "two opposite deletions must not merge cleanly")
        self.assertEqual(len(r.conflicts), 1)

    def test_adjacent_deletions_conflict(self):
        r = merge3(BASE, lines("a\nc\nd\ne\nf"), lines("a\nb\nd\ne\nf"))
        self.assertFalse(r.clean, "adjacent deletions on both sides must conflict")
        self.assertEqual(len(r.conflicts), 1)

    def test_deletion_next_to_the_other_sides_replacement_conflicts(self):
        # We deleted "b"; the AI replaced "c". The AI still has "b".
        r = merge3(BASE, lines("a\nc\nd\ne\nf"), lines("a\nb\nD\ne\nf"))
        self.assertFalse(r.clean, "a deletion next to a replacement must conflict")
        self.assertEqual(len(r.conflicts), 1)

    def test_deletion_next_to_the_other_sides_insertion_conflicts(self):
        # We deleted "b"; the AI inserted a line before "b" and kept it.
        r = merge3(BASE, lines("a\nc\nd\ne\nf"), lines("a\nX\nb\nc\nd\ne\nf"))
        self.assertFalse(r.clean, "a deletion next to an insertion must conflict")
        self.assertEqual(len(r.conflicts), 1)

    def test_both_sides_deleting_the_same_line_stays_clean(self):
        r = merge3(BASE, lines("a\nc\nd\ne\nf"), lines("a\nc\nd\ne\nf"))
        self.assertTrue(r.clean)
        self.assertEqual(r.lines, lines("a\nc\nd\ne\nf"))

    def test_adjacent_replacements_still_merge_cleanly(self):
        # Both sides replaced a different line: neither change is lost, so
        # this keeps merging cleanly (see the module's overlap rules).
        r = merge3(BASE, lines("a\nB\nc\nd\ne\nf"), lines("a\nb\nC\nd\ne\nf"))
        self.assertTrue(r.clean)
        self.assertEqual(r.lines, lines("a\nB\nC\nd\ne\nf"))

    def test_deletion_far_from_the_other_sides_change_still_merges(self):
        # We deleted "b", the AI replaced "e": far apart, nothing is lost.
        r = merge3(BASE, lines("a\nc\nd\ne\nf"), lines("a\nb\nc\nd\nE\nf"))
        self.assertTrue(r.clean)
        self.assertEqual(r.lines, lines("a\nc\nd\nE\nf"))


if __name__ == "__main__":
    unittest.main()
