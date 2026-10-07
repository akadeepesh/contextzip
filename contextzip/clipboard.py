"""
clipboard.py — put the generated ZIP on the clipboard as a real *file*, so it
can be pasted straight into an upload box (Claude, ChatGPT, Gemini, ...).

"A file on the clipboard" is not the file's bytes. Every OS expects a
*reference* to the file in a specific clipboard format, which is what Explorer /
Finder / Nautilus put there when you press Ctrl+C on a file:

    Windows : CF_HDROP (a DROPFILES block listing the absolute path)
    macOS   : a file URL on the NSPasteboard
    Linux   : the ``text/uri-list`` target containing ``file:///...``
              (Chromium reads this target when you paste into a web page)

Tier 1 — file reference on the clipboard (paste directly into the browser)
         Windows: ctypes → CF_HDROP, read back to verify; PowerShell
                  ``Set-Clipboard -LiteralPath`` as a second attempt
         macOS  : osascript / Finder
         Linux  : built-in X11 clipboard owner (pure Python, nothing to
                  install; also works on Wayland desktops via XWayland),
                  or wl-copy / xclip if present — all read back to verify
Tier 2 — open the containing folder (file selected where the OS allows it)
Tier 3 — print the path

``handle()`` never raises and only reports Tier 1 when the clipboard was
actually set (and, where possible, read back).
"""

from __future__ import annotations

import os
import platform
import select
import shutil
import struct
import subprocess
import sys
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

URI_LIST_MIME = "text/uri-list"
_CF_HDROP = 15


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
    hint: str | None = None  # why Tier 1 failed / how to fix it, when known


def handle(zip_path: Path) -> ClipboardResult:
    """
    Attempt to put *zip_path* on the clipboard using the best available tier.
    Always returns a :class:`ClipboardResult` — never raises.
    """
    try:
        return _handle(Path(zip_path))
    except Exception:  # pragma: no cover - last-resort safety net
        return _tier3(Path(zip_path))


def _handle(zip_path: Path) -> ClipboardResult:
    system = platform.system()
    hint: str | None = None

    if system == "Windows":
        result = _tier1_windows(zip_path)
        if result:
            return result
        hint = "Couldn't access the Windows clipboard — copy the selected file with Ctrl+C."
        tier2 = _tier2_windows

    elif system == "Darwin":
        result = _tier1_macos(zip_path)
        if result:
            return result
        tier2 = _tier2_macos

    else:  # Linux / BSD
        if not _linux_has_gui():
            return _tier3(zip_path)  # SSH / headless: nothing to open or paste into
        result, hint = _tier1_linux(zip_path)
        if result:
            return result
        tier2 = _tier2_linux

    result = tier2(zip_path)
    if result:
        result.hint = hint
        return result
    return _tier3(zip_path)


# ---------------------------------------------------------------------------
# Tier 1 — Windows
# ---------------------------------------------------------------------------


def _build_dropfiles(paths: list[str]) -> bytes:
    """
    Build a CF_HDROP payload: a DROPFILES header followed by UTF-16 paths,
    each NUL-terminated, with one extra NUL ending the list.

        struct DROPFILES { DWORD pFiles; POINT pt; BOOL fNC; BOOL fWide; }
    """
    header = struct.pack("<IiiII", 20, 0, 0, 0, 1)  # pFiles=20, pt=(0,0), fNC=0, fWide=1
    body = "".join(p + "\0" for p in paths) + "\0"
    return header + body.encode("utf-16-le")


def _tier1_windows(zip_path: Path) -> ClipboardResult | None:
    path = os.path.abspath(str(zip_path))
    for attempt in (_win_ctypes_copy, _win_powershell_copy):
        try:
            if attempt(path):
                return ClipboardResult(
                    tier=Tier.FILE_ON_CLIPBOARD,
                    message="📋 ZIP copied to clipboard — paste into Claude / ChatGPT!",
                )
        except Exception:
            continue
    return None


def _win_ctypes_copy(path: str) -> bool:
    """
    Set CF_HDROP directly through the Win32 API (no subprocess, ~1 ms), then
    read it back with DragQueryFileW to confirm the clipboard really holds it.
    """
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)

    # Explicit prototypes: without them 64-bit handles get truncated to 32 bits.
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.restype = wintypes.HGLOBAL
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.argtypes = []
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
    user32.RegisterClipboardFormatW.restype = wintypes.UINT
    shell32.DragQueryFileW.argtypes = [
        wintypes.HANDLE,
        wintypes.UINT,
        wintypes.LPWSTR,
        wintypes.UINT,
    ]
    shell32.DragQueryFileW.restype = wintypes.UINT

    def alloc(data: bytes):
        handle_ = kernel32.GlobalAlloc(0x0042, len(data))  # GMEM_MOVEABLE | GMEM_ZEROINIT
        if not handle_:
            return None
        ptr = kernel32.GlobalLock(handle_)
        if not ptr:
            kernel32.GlobalFree(handle_)
            return None
        ctypes.memmove(ptr, data, len(data))
        kernel32.GlobalUnlock(handle_)
        return handle_

    # Clipboard managers / history briefly lock the clipboard — retry.
    for _ in range(20):
        if user32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        return False

    try:
        if not user32.EmptyClipboard():
            return False

        mem = alloc(_build_dropfiles([path]))
        if not mem:
            return False
        if not user32.SetClipboardData(_CF_HDROP, mem):
            kernel32.GlobalFree(mem)  # the clipboard only owns it on success
            return False

        # Tell Explorer this is a copy, not a move (best effort).
        effect_fmt = user32.RegisterClipboardFormatW("Preferred DropEffect")
        if effect_fmt:
            effect = alloc(struct.pack("<I", 1))  # DROPEFFECT_COPY
            if effect and not user32.SetClipboardData(effect_fmt, effect):
                kernel32.GlobalFree(effect)

        # Verify: read the file list back out of the clipboard.
        hdrop = user32.GetClipboardData(_CF_HDROP)
        if not hdrop:
            return False
        if shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0) != 1:
            return False
        length = shell32.DragQueryFileW(hdrop, 0, None, 0)
        buf = ctypes.create_unicode_buffer(length + 1)
        shell32.DragQueryFileW(hdrop, 0, buf, length + 1)
        return os.path.normcase(os.path.normpath(buf.value)) == os.path.normcase(
            os.path.normpath(path)
        )
    finally:
        user32.CloseClipboard()


def _win_powershell_copy(path: str) -> bool:
    """Second attempt: ``Set-Clipboard -LiteralPath`` (PowerShell 5.0+ / pwsh)."""
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        return False
    # The path travels in an environment variable, so quotes, spaces, `$`,
    # brackets and non-ASCII characters in it can't break the command line.
    env = dict(os.environ, CZ_CLIP_PATH=path)
    cmd = [
        exe,
        "-NoProfile",
        "-NonInteractive",
        "-STA",
        "-Command",
        "$ErrorActionPreference='Stop'; Set-Clipboard -LiteralPath $env:CZ_CLIP_PATH",
    ]
    try:
        proc = subprocess.run(
            cmd,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


# ---------------------------------------------------------------------------
# Tier 1 — macOS
# ---------------------------------------------------------------------------


def _tier1_macos(zip_path: Path) -> ClipboardResult | None:
    """
    Ask Finder to put the file on the pasteboard — identical to selecting it in
    Finder and pressing Cmd+C. Browsers read this as a File object on paste.
    """
    posix = str(zip_path).replace("\\", "\\\\").replace('"', '\\"')
    script = f'tell application "Finder" to set the clipboard to (POSIX file "{posix}")'
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


# ---------------------------------------------------------------------------
# Tier 1 — Linux
# ---------------------------------------------------------------------------
#
# Writing the ZIP's *bytes* to the clipboard (what this module used to do) gives
# the browser an "application/zip" blob it ignores. What browsers read on paste
# is ``text/uri-list``: a ``file:///`` URL pointing at the file.
#
# X11 (and Wayland desktops through XWayland) is handled by contextzip's own
# small selection owner in _x11_clip.py, so nothing needs to be installed.
# wl-copy / xclip are used when present: wl-copy is the native Wayland route,
# xclip is a fallback if the built-in owner can't reach the X server.


def _linux_has_gui() -> bool:
    return bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"))


def _tier1_linux(zip_path: Path) -> tuple[ClipboardResult | None, str | None]:
    """Returns (result, hint). *hint* explains a failure and how to fix it."""
    uri_payload = (zip_path.resolve().as_uri() + "\r\n").encode("utf-8")
    attempts: list[tuple[str, object]] = []

    if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-copy"):
        read = (
            ["wl-paste", "--no-newline", "--type", URI_LIST_MIME]
            if shutil.which("wl-paste")
            else None
        )
        attempts.append(
            (
                "wl-copy",
                lambda: _run_clipboard_tool(["wl-copy", "--type", URI_LIST_MIME], read, uri_payload),
            )
        )
    if os.environ.get("DISPLAY"):
        attempts.append(("built-in X11", lambda: _x11_builtin_copy(zip_path, uri_payload)))
        if shutil.which("xclip"):
            attempts.append(
                (
                    "xclip",
                    lambda: _run_clipboard_tool(
                        ["xclip", "-selection", "clipboard", "-t", URI_LIST_MIME, "-i"],
                        ["xclip", "-selection", "clipboard", "-t", URI_LIST_MIME, "-o"],
                        uri_payload,
                    ),
                )
            )

    for _name, attempt in attempts:
        try:
            ok = attempt()  # type: ignore[operator]
        except Exception:
            ok = False
        if ok:
            return (
                ClipboardResult(
                    tier=Tier.FILE_ON_CLIPBOARD,
                    message="📋 ZIP copied to clipboard — paste into your AI tool!",
                ),
                None,
            )
    # Only reachable when the X server couldn't be used (e.g. a Wayland-only
    # session without XWayland) — then a native tool is the way forward.
    return None, _install_hint()


def _x11_builtin_copy(zip_path: Path, expected: bytes) -> bool:
    """
    Start the background owner (``_x11_clip.py serve``), wait for it to report
    that it owns the clipboard, then confirm through the X server that the
    clipboard really serves our file. The owner keeps running after this CLI
    exits and stops itself when another app copies something.
    """
    if not sys.executable:
        return False
    from contextzip import _x11_clip  # lazy: Linux-only code path

    try:
        proc = subprocess.Popen(
            [sys.executable, _x11_clip.__file__, "serve", str(zip_path.resolve())],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,  # only used for the one-line "ready" handshake
            stderr=subprocess.DEVNULL,
            start_new_session=True,  # outlive this process and its terminal
        )
    except OSError:
        return False

    try:
        assert proc.stdout is not None
        readable, _, _ = select.select([proc.stdout], [], [], 4.0)
        if not readable or proc.stdout.readline().strip() != b"ready":
            proc.kill()
            return False
        proc.stdout.close()
        for _ in range(20):
            if _x11_clip.read_selection(URI_LIST_MIME, timeout=1.0) == expected:
                return True
            time.sleep(0.05)
    except (OSError, ValueError):
        pass
    proc.kill()
    return False


def _run_clipboard_tool(
    write_cmd: list[str], read_cmd: list[str] | None, payload: bytes
) -> bool:
    """
    Feed *payload* to a clipboard tool and confirm by reading it back.

    xclip / wl-copy fork a background process that keeps serving the selection.
    That child inherits our pipes, so stdout/stderr must go to DEVNULL (not
    capture_output) or reading would block until the clipboard changes, and it
    gets its own session so it outlives this CLI and its terminal.
    """
    try:
        proc = subprocess.Popen(
            write_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        return False
    try:
        assert proc.stdin is not None
        proc.stdin.write(payload)
        proc.stdin.close()
    except OSError:
        return False
    try:
        rc: int | None = proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        rc = None  # still running in the foreground = still owns the selection
    if rc not in (None, 0):
        return False
    if read_cmd is None:
        return True

    for _ in range(8):  # the selection owner may need a moment to register
        try:
            out = subprocess.run(
                read_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=3
            ).stdout
        except (OSError, subprocess.TimeoutExpired):
            out = b""
        if out.strip() == payload.strip():
            return True
        time.sleep(0.1)
    return False


def _install_hint() -> str:
    """Tell the user which package gives cz a way to set the clipboard."""
    pkg = "wl-clipboard" if os.environ.get("WAYLAND_DISPLAY") else "xclip"
    ids: set[str] = set()
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            key, _, value = line.partition("=")
            if key in ("ID", "ID_LIKE"):
                ids.update(value.strip().strip('"').split())
    except OSError:
        pass
    if ids & {"debian", "ubuntu"}:
        cmd = f"sudo apt install {pkg}"
    elif ids & {"fedora", "rhel", "centos"}:
        cmd = f"sudo dnf install {pkg}"
    elif ids & {"arch", "manjaro"}:
        cmd = f"sudo pacman -S {pkg}"
    elif ids & {"opensuse", "suse"}:
        cmd = f"sudo zypper install {pkg}"
    elif "alpine" in ids:
        cmd = f"sudo apk add {pkg}"
    else:
        return f"Install '{pkg}' with your package manager so cz can copy the file."
    return f"Install '{pkg}' once so cz can copy the file: {cmd}"


# ---------------------------------------------------------------------------
# Tier 2 — open folder / highlight file
# ---------------------------------------------------------------------------


def _tier2_macos(zip_path: Path) -> ClipboardResult | None:
    """open -R reveals and selects the file in Finder."""
    try:
        proc = subprocess.run(["open", "-R", str(zip_path)], capture_output=True, timeout=6)
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
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return ClipboardResult(
            tier=Tier.FOLDER_OPENED,
            message=(
                f"📂 Opened folder containing your ZIP.\n   File: [cyan]{zip_path.name}[/]"
            ),
        )
    except (FileNotFoundError, OSError):
        pass
    return None


def _tier2_windows(zip_path: Path) -> ClipboardResult | None:
    """explorer /select,"<path>" opens Explorer with the file highlighted."""
    try:
        win_path = os.path.abspath(str(zip_path))
        # A single command-line string: Explorer needs the quotes around the
        # path itself, which a list argument would put in the wrong place.
        subprocess.run(
            f'explorer /select,"{win_path}"',
            # explorer.exe exits 1 even on success — don't check the return code
            capture_output=True,
            timeout=8,
        )
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


# ---------------------------------------------------------------------------
# Tier 3 — path only
# ---------------------------------------------------------------------------


def _tier3(zip_path: Path) -> ClipboardResult:
    return ClipboardResult(
        tier=Tier.PATH_ONLY,
        message=f"📄 Copy this path and open it manually:\n   [cyan]{zip_path}[/]",
    )
