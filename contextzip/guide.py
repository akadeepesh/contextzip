"""
guide.py — CONTEXTZIP.md: the note that travels inside every ZIP.

Two jobs:

1. Tell the AI how to reply so the answer applies cleanly — only changed
   files, complete contents, the same paths, secrets left as [REDACTED],
   and moves/deletions listed explicitly instead of left implicit.
2. Carry the snapshot ID (see snapshots.py). When the reply echoes this
   file back, apply-zip knows exactly which snapshot it answers.

Kept short on purpose: it costs the reader (and the model) a few hundred
tokens, not a page. Turned off per project with `"zip_guide": false`.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

GUIDE_NAME = "CONTEXTZIP.md"
_MAX_GUIDE_BYTES = 64 * 1024

_TEMPLATE = """\
# CONTEXTZIP.md

This ZIP was packaged by contextzip from the project **{project}**{stack}.
Paths are relative to the project root.

Snapshot: {snapshot}
{size}
## Replying with files

If you return files as a ZIP, please make it easy to apply:

1. Include only the files you created or changed.
2. Keep the exact paths used in this ZIP. Don't wrap them in an extra folder.
3. Send complete files. Never replace code with a placeholder such as
   `// ... rest of the file unchanged`.
4. Leave every `[REDACTED]` value exactly as it is. Those are secrets that
   stay on the user's machine; they're restored automatically.
5. Include this CONTEXTZIP.md in your ZIP, unchanged except for the
   Changes section below.

## Changes

If you moved, renamed or want to delete files, list them here, one per
line, using this format:

```
moved: old/path.ts -> new/path.ts
deleted: old/unused.ts
```

"""


def render(
    *,
    snapshot: str,
    project: str,
    ecosystem: str | None = None,
    tokens=None,
    files: int | None = None,
) -> str:
    """
    The guide's text. With *tokens* (a TokenReport) a size line follows the
    snapshot line: "Size: 44 files, 46,210 tokens (o200k_base)."
    """
    stack = f" ({ecosystem})" if ecosystem and ecosystem.lower() != "unknown" else ""
    size = ""
    if tokens is not None:
        count = len(tokens.by_file) if files is None else files
        size = f"Size: {count} file{'' if count == 1 else 's'}, {tokens.label()} ({tokens.method}).\n"
    return _TEMPLATE.format(snapshot=snapshot, project=project or "this project", stack=stack, size=size)


@dataclass
class Guide:
    snapshot: str | None = None
    moves: list[tuple[str, str]] = field(default_factory=list)
    deletions: list[str] = field(default_factory=list)


_SNAPSHOT_RE = re.compile(r"^\s*snapshot:\s*`?(\d+-[a-z0-9]+)`?\s*$", re.IGNORECASE)
_MOVED_RE = re.compile(r"^\s*[-*]?\s*moved:\s*(.+?)\s*(?:->|→|=>)\s*(.+?)\s*$", re.IGNORECASE)
_DELETED_RE = re.compile(r"^\s*[-*]?\s*deleted:\s*(.+?)\s*$", re.IGNORECASE)


def _clean_path(raw: str) -> str:
    p = raw.strip().strip("`'\"").replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p.lstrip("/")


def parse(text: str) -> Guide:
    """
    Read a CONTEXTZIP.md. Lines inside code fences are ignored, so the
    template's own examples never count as real moves or deletions.
    """
    guide = Guide()
    in_fence = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _SNAPSHOT_RE.match(line)
        if m and guide.snapshot is None:
            guide.snapshot = m.group(1).lower()
            continue
        m = _MOVED_RE.match(line)
        if m:
            src, dst = _clean_path(m.group(1)), _clean_path(m.group(2))
            if src and dst and src != dst:
                guide.moves.append((src, dst))
            continue
        m = _DELETED_RE.match(line)
        if m:
            path = _clean_path(m.group(1))
            if path:
                guide.deletions.append(path)
    return guide


def find_in_zip(names: list[str]) -> str | None:
    """The entry that is a CONTEXTZIP.md, at any depth (AI tools may wrap it)."""
    hits = [n for n in names if n.replace("\\", "/").rsplit("/", 1)[-1].lower() == GUIDE_NAME.lower()]
    return min(hits, key=lambda n: n.count("/")) if hits else None


def read_guide_from_zip(zip_path: Path) -> Guide | None:
    """Parse the CONTEXTZIP.md inside *zip_path*, if there is one."""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            name = find_in_zip([i.filename for i in zf.infolist() if not i.is_dir()])
            if name is None:
                return None
            info = zf.getinfo(name)
            if info.file_size > _MAX_GUIDE_BYTES:
                return None
            text = zf.read(name).decode("utf-8", errors="replace")
    except (OSError, zipfile.BadZipFile, KeyError):
        return None
    return parse(text)
