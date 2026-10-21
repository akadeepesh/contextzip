"""
merge3.py — Line-based three-way merge, used by apply-zip.

When you've edited a file after zipping it *and* the AI changed it too,
apply-zip has three versions:

    base   — the file as it was in the ZIP you sent (read back from that ZIP)
    ours   — the file on disk now, with your edits
    theirs — the file the AI returned

Like git's diff3, every change is located against `base`. A region only one
side touched takes that side's version; a region both sides changed the
same way takes it once; a region both sides changed differently is a
conflict, and nothing is guessed. That turns most "you edited this file
too" cases into clean merges instead of an all-or-nothing choice.

Rules for what counts as overlapping, chosen to be safe for code:
  - Replaced ranges that share a base line conflict.
  - Two insertions at the same point conflict (unless identical).
  - An insertion strictly inside a range the other side replaced conflicts.
  - Changes that merely touch (one ends on the line the other begins)
    merge cleanly — unless one of them is a deletion. A deletion drops
    base lines, so the other side keeping them is not something the
    merge can honour on its own: both sides deleted or kept one of the
    same lines, and that needs a decision rather than a guess.

Works on lines with line endings removed; callers decide how to rejoin
them (see applier.py, which keeps the local file's line-ending style).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher

CONFLICT_START = "<<<<<<< yours (on disk)"
CONFLICT_MID = "======="
CONFLICT_END = ">>>>>>> incoming (from the AI)"


@dataclass
class _Hunk:
    start: int  # base index where the change begins
    end: int  # base index where it ends (exclusive); start == end is an insertion
    lines: list[str]
    side: str  # "ours" or "theirs"


@dataclass
class Conflict:
    base: list[str]
    ours: list[str]
    theirs: list[str]


@dataclass
class MergeResult:
    lines: list[str]
    """Merged lines. Conflicted regions use git-style markers (see marked)."""
    conflicts: list[Conflict] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.conflicts


def _hunks(base: list[str], other: list[str], side: str) -> list[_Hunk]:
    sm = SequenceMatcher(None, base, other, autojunk=False)
    return [
        _Hunk(i1, i2, other[j1:j2], side)
        for tag, i1, i2, j1, j2 in sm.get_opcodes()
        if tag != "equal"
    ]


def _is_deletion(h: _Hunk) -> bool:
    """True if *h* removes base lines without putting anything in their place."""
    return h.start < h.end and not h.lines


def _touches(other: _Hunk, deletion: _Hunk) -> bool:
    """True if *other* sits directly against *deletion* without overlapping it."""
    return other.start == deletion.end or other.end == deletion.start


def _overlaps(a: _Hunk, b: _Hunk) -> bool:
    a_ins, b_ins = a.start == a.end, b.start == b.end
    if a_ins and b_ins:
        return a.start == b.start
    if a_ins or b_ins:
        # An insertion conflicts with a range it lands strictly inside.
        ins, rng = (a, b) if a_ins else (b, a)
        if rng.start < ins.start < rng.end:
            return True
    elif a.start < b.end and b.start < a.end:
        return True
    # A deletion that merely touches the other side's change is still a
    # conflict. The other side's change begins (or ends) where the deleted
    # range begins (or ends), so the other side kept the deleted lines —
    # keeping them and dropping them at the same time is not a merge, it is
    # a guess. Two replacements that merely touch are different: neither
    # side loses anything, so they still merge cleanly.
    if a.side != b.side:
        if _is_deletion(a) and _touches(b, a):
            return True
        if _is_deletion(b) and _touches(a, b):
            return True
    return False


def _apply(base: list[str], start: int, end: int, hunks: list[_Hunk]) -> list[str]:
    """base[start:end] with *hunks* (all from one side, inside the range) applied."""
    out: list[str] = []
    pos = start
    for h in sorted(hunks, key=lambda h: (h.start, h.end)):
        out.extend(base[pos:h.start])
        out.extend(h.lines)
        pos = h.end
    out.extend(base[pos:end])
    return out


def merge3(base: list[str], ours: list[str], theirs: list[str]) -> MergeResult:
    """Three-way merge of line lists. See the module docstring for the rules."""
    if ours == theirs:
        return MergeResult(lines=list(ours))
    if ours == base:
        return MergeResult(lines=list(theirs))
    if theirs == base:
        return MergeResult(lines=list(ours))

    hunks = sorted(
        _hunks(base, ours, "ours") + _hunks(base, theirs, "theirs"),
        key=lambda h: (h.start, h.end),
    )

    # Group hunks that overlap (transitively) into regions.
    groups: list[list[_Hunk]] = []
    for h in hunks:
        if groups and any(_overlaps(h, g) for g in groups[-1]):
            groups[-1].append(h)
        else:
            groups.append([h])

    out: list[str] = []
    conflicts: list[Conflict] = []
    pos = 0
    for group in groups:
        start = min(h.start for h in group)
        end = max(h.end for h in group)
        out.extend(base[pos:start])

        sides = {h.side for h in group}
        if len(sides) == 1:
            out.extend(_apply(base, start, end, group))
        else:
            mine = _apply(base, start, end, [h for h in group if h.side == "ours"])
            incoming = _apply(base, start, end, [h for h in group if h.side == "theirs"])
            if mine == incoming:
                out.extend(mine)
            else:
                conflicts.append(Conflict(base=base[start:end], ours=mine, theirs=incoming))
                out.append(CONFLICT_START)
                out.extend(mine)
                out.append(CONFLICT_MID)
                out.extend(incoming)
                out.append(CONFLICT_END)
        pos = end

    out.extend(base[pos:])
    return MergeResult(lines=out, conflicts=conflicts)


def has_conflict_markers(text: str) -> bool:
    """True if *text* still contains merge3's conflict markers."""
    return CONFLICT_START in text or CONFLICT_END in text
