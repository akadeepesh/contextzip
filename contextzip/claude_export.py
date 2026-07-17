"""
claude_export.py — Locates and prepares a Claude.ai conversation export for
use in `eod` / `handoff` prompts.

Pure text handling — no AI calls, no network. Finding "the latest export"
and deciding whether it's short enough to paste inline are the only two
responsibilities here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DEFAULT_EXPORTS_DIR = "exports"
DEFAULT_ARTIFACTS_SUBDIR = "artifacts"

# Conversations at or under this length get pasted directly into the
# generated prompt; longer ones are referenced as an attachment instead,
# since a full day's chat could otherwise burn through the next chat's
# context budget before any actual work happens.
INLINE_CHAR_THRESHOLD = 3500


class NoExportFoundError(Exception):
    """Raised when no .md export exists in the exports directory."""


@dataclass
class ConversationExport:
    path: Path
    text: str
    conversation_name: str  # filename stem — used for the artifacts subfolder


def find_latest_export(exports_dir: Path) -> ConversationExport:
    """
    Return the most recently modified .md file in *exports_dir*.

    "Most recently modified" rather than "most recently created" — if you
    re-export the same conversation later in the day, the newer copy wins
    without needing a naming convention.
    """
    if not exports_dir.is_dir():
        raise NoExportFoundError(
            f"Exports folder not found: {exports_dir}. "
            f"Export a conversation and drop the .md file there first."
        )

    candidates = list(exports_dir.glob("*.md"))
    if not candidates:
        raise NoExportFoundError(
            f"No .md export files found in {exports_dir}. "
            f"Export a conversation and drop the .md file there first."
        )

    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    path = candidates[0]
    text = path.read_text(encoding="utf-8", errors="replace")

    return ConversationExport(path=path, text=text, conversation_name=path.stem)


def artifacts_dir_for(export: ConversationExport, exports_dir: Path) -> Path:
    """Where this export's Claude-provided artifact files live (or will be fetched to)."""
    return exports_dir / DEFAULT_ARTIFACTS_SUBDIR / export.conversation_name


def should_inline(
    export: ConversationExport, threshold: int = INLINE_CHAR_THRESHOLD
) -> bool:
    """True if the export is short enough to paste directly into the prompt."""
    return len(export.text) <= threshold
