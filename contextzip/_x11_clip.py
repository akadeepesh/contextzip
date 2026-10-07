"""
_x11_clip.py — a tiny X11 CLIPBOARD owner written against the raw X protocol.

Why this exists: on Linux, "a file on the clipboard" means owning the X
CLIPBOARD selection and answering requests for ``text/uri-list`` (what
browsers read when you paste) with a ``file:///…`` URL. Normally that needs
``xclip`` — a system package that ``pip install`` can't provide. The protocol
subset needed is small, so contextzip speaks it itself: standard library only,
no dependencies.

It offers the same targets a file manager offers when you press Ctrl+C on a
file, so it works for browsers (text/uri-list), GNOME-family file managers
(x-special/gnome-copied-files) and KDE (application/x-kde-cutselection).

Runs two ways:
  * imported:   ``read_selection()`` reads the clipboard back (verification)
  * as a script ``python _x11_clip.py serve <abs-path>`` — the background
    process that keeps owning the selection until another app copies
    something (SelectionClear) or the X connection closes. It is launched by
    path, so this file must stay importable without the contextzip package.
"""

from __future__ import annotations

import os
import socket
import struct
import sys
from pathlib import Path

URI_LIST = "text/uri-list"
GNOME_COPIED = "x-special/gnome-copied-files"
KDE_CUT = "application/x-kde-cutselection"

_ATOM = 4  # predefined atom: ATOM
_SELECTION_CLEAR = 29
_SELECTION_REQUEST = 30
_SELECTION_NOTIFY = 31


class X11Error(Exception):
    pass


def _pad(n: int) -> int:
    return (4 - n % 4) % 4


def payloads_for(path: str) -> dict[str, bytes]:
    """Clipboard targets (and their data) for one file, as a file manager sets them."""
    uri = Path(path).as_uri()
    return {
        URI_LIST: (uri + "\r\n").encode(),
        GNOME_COPIED: ("copy\n" + uri).encode(),
        KDE_CUT: b"0",
    }


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------


def _parse_display(display: str) -> tuple[str, int]:
    host, sep, rest = display.rpartition(":")
    if not sep or not rest:
        raise X11Error(f"unusable DISPLAY {display!r}")
    try:
        return host, int(rest.split(".")[0])
    except ValueError:
        raise X11Error(f"unusable DISPLAY {display!r}") from None


def _open_socket(host: str, num: int, timeout: float | None) -> socket.socket:
    if host in ("", "unix"):
        # Xorg/XWayland listen on a filesystem socket and (on Linux) an abstract one.
        for addr in (f"/tmp/.X11-unix/X{num}", f"\0/tmp/.X11-unix/X{num}"):
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            try:
                sock.connect(addr)
                return sock
            except OSError:
                sock.close()
        raise X11Error(f"cannot connect to X display :{num}")
    try:
        return socket.create_connection((host, 6000 + num), timeout)
    except OSError as exc:
        raise X11Error(f"cannot connect to X display {host}:{num}") from exc


def _read_xauthority() -> list[tuple[int, bytes, bytes, bytes, bytes]]:
    path = os.environ.get("XAUTHORITY") or os.path.join(os.path.expanduser("~"), ".Xauthority")
    try:
        data = Path(path).read_bytes()
    except OSError:
        return []
    entries, i = [], 0
    try:
        while i + 2 <= len(data):
            (family,) = struct.unpack(">H", data[i : i + 2])
            i += 2
            fields = []
            for _ in range(4):  # address, display number, auth name, auth data
                (n,) = struct.unpack(">H", data[i : i + 2])
                i += 2
                fields.append(data[i : i + n])
                i += n
            entries.append((family, *fields))
    except struct.error:
        pass
    return entries


def _auth_for(host: str, num: int) -> tuple[bytes, bytes]:
    """MIT-MAGIC-COOKIE-1 for this display from ~/.Xauthority ($XAUTHORITY), if any."""
    cookies = [
        e
        for e in _read_xauthority()
        if e[3] == b"MIT-MAGIC-COOKIE-1" and e[2] in (b"", str(num).encode())
    ]
    local = host in ("", "unix")
    me = socket.gethostname().encode()
    for family, addr, _number, name, value in cookies:
        if local and (family == 65535 or (family == 256 and addr == me)):
            return name, value
    if cookies:  # hostname changed (containers, renamed machines): number match is enough
        return cookies[0][3], cookies[0][4]
    return b"", b""  # open display (xhost +local:, Xvfb -ac)


class Connection:
    """Just enough X11 client for selection handling. Little-endian, core protocol only."""

    def __init__(self, display: str | None = None, timeout: float | None = 5.0):
        display = display if display is not None else os.environ.get("DISPLAY", "")
        host, num = _parse_display(display)
        self.sock = _open_socket(host, num, timeout)
        self.sock.settimeout(timeout)
        self.seq = 0
        self.events: list[tuple[int, bytes]] = []
        self._handshake(*_auth_for(host, num))
        self._next_id = 0

    # -- wire helpers -------------------------------------------------------

    def _recv(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise X11Error("X connection closed")
            buf += chunk
        return buf

    def _handshake(self, auth_name: bytes, auth_data: bytes) -> None:
        req = struct.pack("<BxHHHH2x", 0x6C, 11, 0, len(auth_name), len(auth_data))
        req += auth_name + b"\0" * _pad(len(auth_name))
        req += auth_data + b"\0" * _pad(len(auth_data))
        self.sock.sendall(req)
        status, reason_len, _maj, _min, words = struct.unpack("<BBHHH", self._recv(8))
        body = self._recv(words * 4)
        if status != 1:
            reason = body[:reason_len].decode("latin-1", "replace") or "authorization failed"
            raise X11Error(f"X server refused connection: {reason}")
        self.id_base, self.id_mask = struct.unpack("<II", body[4:12])
        (vendor_len,) = struct.unpack("<H", body[16:18])
        n_formats = body[21]
        off = 32 + vendor_len + _pad(vendor_len) + n_formats * 8
        (self.root,) = struct.unpack("<I", body[off : off + 4])

    def _request(self, opcode: int, data_byte: int, body: bytes) -> None:
        assert len(body) % 4 == 0
        self.sock.sendall(struct.pack("<BBH", opcode, data_byte, 1 + len(body) // 4) + body)
        self.seq = (self.seq + 1) & 0xFFFF

    def _read_packet(self) -> tuple[int, bytes]:
        head = self._recv(32)
        code = head[0] & 0x7F
        if code == 1:  # reply: may carry extra data
            (extra,) = struct.unpack("<I", head[4:8])
            head += self._recv(extra * 4)
        return code, head

    def _reply(self) -> bytes:
        while True:
            code, pkt = self._read_packet()
            if code == 0:
                raise X11Error(f"X error {pkt[1]} (request {struct.unpack('<H', pkt[2:4])[0]})")
            if code == 1:
                return pkt
            self.events.append((code, pkt))

    def next_event(self) -> tuple[int, bytes]:
        """Next event; asynchronous errors are ignored."""
        while True:
            if self.events:
                return self.events.pop(0)
            code, pkt = self._read_packet()
            if code not in (0, 1):
                return code, pkt

    # -- requests -----------------------------------------------------------

    def intern(self, name: str) -> int:
        raw = name.encode()
        self._request(16, 0, struct.pack("<HH", len(raw), 0) + raw + b"\0" * _pad(len(raw)))
        return struct.unpack("<I", self._reply()[8:12])[0]

    def create_window(self) -> int:
        """A 1x1 InputOnly window: selections need a window to be owned from."""
        step = self.id_mask & -self.id_mask
        wid = self.id_base | (step * (self._next_id + 1))
        self._next_id += 1
        # wid, parent, x, y, w, h, border, class=InputOnly, visual=CopyFromParent, mask=0
        self._request(1, 0, struct.pack("<IIhhHHHHII", wid, self.root, 0, 0, 1, 1, 0, 2, 0, 0))
        return wid

    def set_selection_owner(self, window: int, selection: int) -> None:
        self._request(22, 0, struct.pack("<III", window, selection, 0))  # CurrentTime

    def get_selection_owner(self, selection: int) -> int:
        self._request(23, 0, struct.pack("<I", selection))
        return struct.unpack("<I", self._reply()[8:12])[0]

    def change_property(self, window: int, prop: int, typ: int, fmt: int, data: bytes) -> None:
        units = len(data) // (fmt // 8)
        body = struct.pack("<IIIB3xI", window, prop, typ, fmt, units)
        self._request(18, 0, body + data + b"\0" * _pad(len(data)))  # mode Replace

    def send_selection_notify(
        self, requestor: int, selection: int, target: int, prop: int, time: int
    ) -> None:
        event = struct.pack("<BBHIIIII", _SELECTION_NOTIFY, 0, 0, time, requestor, selection, target, prop)
        self._request(25, 0, struct.pack("<II", requestor, 0) + event + b"\0" * 8)

    def convert_selection(self, requestor: int, selection: int, target: int, prop: int) -> None:
        self._request(24, 0, struct.pack("<IIIII", requestor, selection, target, prop, 0))

    def get_property(self, window: int, prop: int) -> tuple[int, bytes]:
        self._request(20, 1, struct.pack("<IIIII", window, prop, 0, 0, 0x1FFFFF))  # delete=True
        pkt = self._reply()
        fmt = pkt[1]
        (typ,) = struct.unpack("<I", pkt[8:12])
        (units,) = struct.unpack("<I", pkt[16:20])
        return typ, pkt[32 : 32 + units * (fmt // 8 if fmt else 0)]

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Reading (used to verify the copy, and by the tests)
# ---------------------------------------------------------------------------


def read_selection(
    target: str = URI_LIST, *, display: str | None = None, timeout: float = 2.0
) -> bytes | None:
    """Ask the current CLIPBOARD owner for *target*. None if nobody offers it."""
    conn = Connection(display, timeout=timeout)
    try:
        win = conn.create_window()
        clipboard, dest = conn.intern("CLIPBOARD"), conn.intern("CZ_CLIPBOARD_READ")
        conn.convert_selection(win, clipboard, conn.intern(target), dest)
        while True:
            code, pkt = conn.next_event()
            if code == _SELECTION_NOTIFY:
                (prop,) = struct.unpack("<I", pkt[20:24])
                if not prop:
                    return None
                return conn.get_property(win, prop)[1]
    except (OSError, X11Error):
        return None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Owning (the background process)
# ---------------------------------------------------------------------------


def serve(path: str, display: str | None = None, on_ready=None) -> None:
    """
    Own the CLIPBOARD selection for *path* until something else takes it.
    *on_ready* is called once ownership is confirmed (the launcher waits for it).
    """
    conn = Connection(display, timeout=5.0)
    win = conn.create_window()
    clipboard, targets = conn.intern("CLIPBOARD"), conn.intern("TARGETS")
    offered = {conn.intern(name): data for name, data in payloads_for(path).items()}

    conn.set_selection_owner(win, clipboard)
    if conn.get_selection_owner(clipboard) != win:
        raise X11Error("could not become CLIPBOARD owner")
    conn.sock.settimeout(None)  # from here on: block until the server says something
    if on_ready:
        on_ready()

    while True:
        code, pkt = conn.next_event()
        if code == _SELECTION_CLEAR:
            return  # someone else copied something — our job is done
        if code != _SELECTION_REQUEST:
            continue
        time, _owner, requestor, selection, target, prop = struct.unpack("<IIIIII", pkt[4:28])
        reply_prop = prop or target  # obsolete clients send no property
        if selection == clipboard and target == targets:
            atoms = [targets, *offered]
            conn.change_property(requestor, reply_prop, _ATOM, 32, struct.pack(f"<{len(atoms)}I", *atoms))
        elif selection == clipboard and target in offered:
            conn.change_property(requestor, reply_prop, target, 8, offered[target])
        else:
            reply_prop = 0  # refuse (TIMESTAMP, MULTIPLE, other selections, …)
        conn.send_selection_notify(requestor, selection, target, reply_prop, time)


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[1] == "serve":
        def ready() -> None:
            sys.stdout.write("ready\n")
            sys.stdout.flush()

        try:
            serve(argv[2], on_ready=ready)
        except (OSError, X11Error):
            return 1
        return 0
    sys.stderr.write("usage: _x11_clip.py serve <absolute-path>\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
