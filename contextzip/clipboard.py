"""
clipboard.py — Tiered clipboard strategy for copying a ZIP file.

Tier 1  — Copy the actual file object to clipboard (paste directly into
           browser upload zones like Claude, ChatGPT, etc.)
           macOS  : osascript + Finder NSPasteboard trick
           Linux  : xclip (if installed)
           Windows: not possible via CLI — skip to Tier 2

Tier 2  — Open the containing folder with the file highlighted/selected,
           so the user can copy it themselves with one Ctrl+C.
           macOS  : open -R <file>
           Linux  : xdg-open <folder>   (can't pre-select a file on Linux)
           Windows: explorer /select,"<file>"   ← selects the file in Explorer

Tier 3  — Print the path clearly and tell the user to copy it manually.
           Always available, never fails.

The module returns a ClipboardResult describing which tier succeeded.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


class Tier(Enum):
    FILE_ON_CLIPBOARD = 1
    FOLDER_OPENED = 2
    PATH_ONLY = 3


@dataclass
class ClipboardResult:
    tier: Tier
    message: str
    success: bool = True


def handle(zip_path: Path) -> ClipboardResult:
    """
    Attempt to put *zip_path* on the clipboard using the best available tier.
    Always returns a :class:`ClipboardResult` — never raises.
    """
    system = platform.system()

    # ── Tier 1: real file-object on clipboard ───────────────────────────────
    if system == "Darwin":
        result = _tier1_macos(zip_path)
        if result:
            return result

    elif system == "Linux":
        result = _tier1_linux(zip_path)
        if result:
            return result

    # Windows Tier 1: not possible — fall straight through to Tier 2

    # ── Tier 2: open folder with file selected ──────────────────────────────
    if system == "Darwin":
        result = _tier2_macos(zip_path)
    elif system == "Linux":
        result = _tier2_linux(zip_path)
    else:
        result = _tier2_windows(zip_path)

    if result:
        return result

    # ── Tier 3: path only ───────────────────────────────────────────────────
    return _tier3(zip_path)


# ---------------------------------------------------------------------------
# Tier 1 — file object on clipboard
# ---------------------------------------------------------------------------


def _tier1_macos(zip_path: Path) -> ClipboardResult | None:
    """
    Use osascript to ask Finder to copy the file to the clipboard.
    This puts a real file object on the NSPasteboard — identical to
    selecting the file in Finder and pressing Cmd+C.
    Browsers read this as a File object on paste.
    """
    script = (
        f'tell application "Finder" to set the clipboard to '
        f'(POSIX file "{zip_path.as_posix()}")'
    )
    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=8,
        )
        if proc.returncode == 0:
            return ClipboardResult(
                tier=Tier.FILE_ON_CLIPBOARD,
                message="📋 ZIP copied to clipboard — just paste into Claude / ChatGPT!",
            )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return None


def _tier1_linux(zip_path: Path) -> ClipboardResult | None:
    """
    Use xclip to write the file bytes with MIME type application/zip.
    Works in most GTK/Qt browser upload dialogs when pasting.
    Requires xclip to be installed.
    """
    if not shutil.which("xclip"):
        return None
    try:
        with zip_path.open("rb") as fh:
            proc = subprocess.run(
                ["xclip", "-selection", "clipboard", "-t", "application/zip", "-i"],
                stdin=fh,
                capture_output=True,
                timeout=10,
            )
        if proc.returncode == 0:
            return ClipboardResult(
                tier=Tier.FILE_ON_CLIPBOARD,
                message="📋 ZIP copied to clipboard — paste into your AI tool!",
            )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError, PermissionError):
        pass
    return None


# ---------------------------------------------------------------------------
# Tier 2 — open folder / highlight file
# ---------------------------------------------------------------------------


def _tier2_macos(zip_path: Path) -> ClipboardResult | None:
    """open -R reveals and selects the file in Finder."""
    try:
        proc = subprocess.run(
            ["open", "-R", str(zip_path)],
            capture_output=True,
            timeout=6,
        )
        if proc.returncode == 0:
            return ClipboardResult(
                tier=Tier.FOLDER_OPENED,
                message=(
                    "📂 Opened Finder with your ZIP selected.\n"
                    "   Press [bold]Cmd+C[/] then paste into your AI tool."
                ),
            )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return None


def _tier2_linux(zip_path: Path) -> ClipboardResult | None:
    """xdg-open opens the parent folder (can't pre-select on Linux)."""
    if not shutil.which("xdg-open"):
        return None
    try:
        subprocess.Popen(
            ["xdg-open", str(zip_path.parent)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return ClipboardResult(
            tier=Tier.FOLDER_OPENED,
            message=(
                f"📂 Opened folder containing your ZIP.\n"
                f"   File: [cyan]{zip_path.name}[/]"
            ),
        )
    except (FileNotFoundError, OSError):
        pass
    return None


def _tier2_windows(zip_path: Path) -> ClipboardResult | None:
    """
    explorer /select,"<path>" opens Explorer with the file highlighted.
    The user can then press Ctrl+C and paste it directly into a browser.
    """
    try:
        # Use the Windows path format with backslashes
        win_path = str(zip_path).replace("/", "\\")
        subprocess.run(
            ["explorer", f"/select,{win_path}"],
            # explorer always exits 1 even on success — don't check returncode
            capture_output=True,
            timeout=8,
        )
        # explorer.exe opens asynchronously; a quick return (any code) means it launched
        return ClipboardResult(
            tier=Tier.FOLDER_OPENED,
            message=(
                "📂 Opened Explorer with your ZIP selected.\n"
                "   Press [bold]Ctrl+C[/] then paste into Claude / ChatGPT!"
            ),
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return None


def _tier3(zip_path: Path) -> ClipboardResult:
    return ClipboardResult(
        tier=Tier.PATH_ONLY,
        message=(f"📄 Copy this path and open it manually:\n   [cyan]{zip_path}[/]"),
    )
