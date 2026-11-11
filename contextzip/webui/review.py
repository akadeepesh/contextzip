"""
webui/review.py — Local review page for `contextzip apply-zip --review`.

Shows every file in an AI-returned zip with a side-by-side diff and lets
you decide, per file, what happens: keep yours, take the AI's version, or
resolve a conflict by editing the merged text right in the page. Nothing
is written by the page itself — it hands the decisions back to the CLI,
which applies them with execute_plan.

Same safety model as the config UI (see server.py): bound to 127.0.0.1
only, a random single-use token on every request, no outbound requests,
and it shuts itself down when you're done, after 15 idle minutes, or after
an hour regardless.
"""

from __future__ import annotations

import hmac
import json
import secrets
import threading
import time
from difflib import SequenceMatcher
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from rich.console import Console

from contextzip.applier import STATUS_REASONS, ApplyPlan, FileStatus, _as_text
from contextzip.cli_display import err, info, ok
from contextzip.cli_onboard import open_browser_silent
from contextzip.webui.review_assets import REVIEW_HTML

_IDLE_TIMEOUT_SECONDS = 15 * 60
_HARD_TIMEOUT_SECONDS = 60 * 60
_MAX_TEXT = 1_500_000  # bytes; larger files are shown as "too large to preview"
_CONTEXT = 3  # unchanged lines kept around each change before folding


class _ReviewState:
    def __init__(self, plan: ApplyPlan, project_dir: Path):
        self.plan = plan
        self.project_dir = project_dir
        self.token = secrets.token_urlsafe(24)
        self.last_activity = time.monotonic()
        self.done = threading.Event()
        self.decisions: dict | None = None
        self.by_id = {e.display_path: e for e in plan.entries}

    def touch(self) -> None:
        self.last_activity = time.monotonic()


# ---------------------------------------------------------------------------
# Payload building
# ---------------------------------------------------------------------------


def _live_bytes(state: _ReviewState, rel: str) -> bytes | None:
    if not rel:
        return None
    path = state.project_dir / rel
    try:
        return path.read_bytes() if path.is_file() else None
    except OSError:
        return None


def _text(data: bytes | None) -> str | None:
    if data is None:
        return ""
    if len(data) > _MAX_TEXT:
        return None
    return _as_text(data)


def _entry_summary(e) -> dict:
    return {
        "id": e.display_path,
        "path": e.rel_path,
        "source": e.source_name,
        "moved": e.moved,
        "how": e.how,
        "status": e.status.value,
        "reason": STATUS_REASONS.get(e.status, ""),
        "note": e.note,
        "risky": e.is_risky,
        "safe": e.is_safe,
        "candidates": e.candidates,
        "size": e.size,
        "redacted": e.redacted,
        "hasBase": e.base_text is not None,
        "hasConflict": e.conflict_text is not None,
        "movedFrom": e.moved_from,
        "removable": e.removable,
    }


def _spans(a: str, b: str) -> tuple[list[list[int]], list[list[int]]]:
    """Character ranges that differ between two similar lines."""
    sm = SequenceMatcher(None, a, b, autojunk=False)
    left, right = [], []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if i2 > i1:
            left.append([i1, i2])
        if j2 > j1:
            right.append([j1, j2])
    return left, right


def _diff_rows(left: str, right: str) -> list[dict]:
    """Side-by-side rows; unchanged runs longer than 2x context are folded."""
    a, b = left.split("\n"), right.split("\n")
    if a and a[-1] == "":
        a = a[:-1]
    if b and b[-1] == "":
        b = b[:-1]
    rows: list[dict] = []
    ops = SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
    for index, (tag, i1, i2, j1, j2) in enumerate(ops):
        if tag == "equal":
            n = i2 - i1
            head = 0 if index == 0 else _CONTEXT
            tail = 0 if index == len(ops) - 1 else _CONTEXT
            if len(ops) == 1:
                head, tail = n, 0  # identical files: show them as they are
            if n > head + tail + 1:
                keep = list(range(head)) + [None] + list(range(n - tail, n))
            else:
                keep = list(range(n))
            for k in keep:
                if k is None:
                    rows.append({"t": "fold", "n": n - head - tail})
                else:
                    rows.append({"t": "eq", "l": [i1 + k + 1, a[i1 + k]], "r": [j1 + k + 1, b[j1 + k]]})
            continue
        la, lb = a[i1:i2], b[j1:j2]
        for k in range(max(len(la), len(lb))):
            row: dict = {"t": "mod" if k < len(la) and k < len(lb) else ("del" if k < len(la) else "add")}
            if k < len(la):
                row["l"] = [i1 + k + 1, la[k]]
            if k < len(lb):
                row["r"] = [j1 + k + 1, lb[k]]
            if row["t"] == "mod":
                row["ls"], row["rs"] = _spans(la[k], lb[k])
            rows.append(row)
    return rows


def _diff_payload(state: _ReviewState, entry_id: str, left: str, right: str) -> dict:
    e = state.by_id.get(entry_id)
    if e is None:
        return {"error": "unknown file"}
    live = _live_bytes(state, e.rel_path)
    if e.status == FileStatus.DELETE_REQUESTED:
        lt = _text(live)
        if lt is None:
            return {"binary": True}
        return {"rows": _diff_rows(lt, ""), "leftExists": live is not None, "same": False}
    if e.status == FileStatus.MOVED and e.moved_from and left == "yours":
        live = _live_bytes(state, e.moved_from)  # compare with the file it moved from
    sides = {
        "yours": _text(live) if live is not None else "",
        "original": e.base_text if e.base_text is not None else None,
        "incoming": _text(e.incoming if e.incoming is not None else e.extracted_path.read_bytes()),
        # What the file will be after applying: the planned content, or —
        # for a file that's already up to date — exactly what's on disk.
        "proposed": (
            _text(e.content)
            if e.content is not None
            else (_text(live) if e.status == FileStatus.UNCHANGED and live is not None else _text(e.incoming))
        ),
    }
    lt, rt = sides.get(left), sides.get(right)
    if lt is None or rt is None:
        return {"binary": True}
    return {
        "rows": _diff_rows(lt, rt),
        "leftExists": not (left == "yours" and live is None),
        "same": lt == rt,
    }


def _edit_payload(state: _ReviewState, entry_id: str) -> dict:
    e = state.by_id.get(entry_id)
    if e is None:
        return {"error": "unknown file"}
    if e.conflict_text is not None:
        return {"text": e.conflict_text, "kind": "conflict"}
    text = _text(e.incoming)
    return {"text": text, "kind": "incoming"} if text is not None else {"binary": True}


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------


def _make_handler(state: _ReviewState):
    class Handler(BaseHTTPRequestHandler):
        server_version = "contextzip-review"

        def log_message(self, format, *args):  # noqa: A002
            pass

        def _authorized(self) -> bool:
            qs = parse_qs(urlparse(self.path).query)
            token = qs.get("token", [None])[0] or self.headers.get("X-Contextzip-Token")
            return bool(token) and hmac.compare_digest(token, state.token)

        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload: dict) -> None:
            self._send(status, json.dumps(payload).encode("utf-8"), "application/json")

        def _guard(self) -> bool:
            state.touch()
            if not self._authorized():
                self._json(403, {"error": "invalid token"})
                return False
            return True

        def do_GET(self):  # noqa: N802
            try:
                url = urlparse(self.path)
                qs = parse_qs(url.query)
                if url.path == "/":
                    state.touch()
                    if not self._authorized():
                        self._send(403, b"Invalid or missing token. Re-run contextzip apply --review.", "text/plain; charset=utf-8")
                        return
                    html = REVIEW_HTML.replace("__TOKEN__", state.token)
                    self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
                    return
                if not self._guard():
                    return
                if url.path == "/api/plan":
                    plan = state.plan
                    self._json(200, {
                        "project": {"name": state.project_dir.name, "path": str(state.project_dir)},
                        "zip": plan.zip_path.name,
                        "manifest": plan.manifest_path.name if plan.manifest_path else None,
                        "manifestNote": plan.manifest_note,
                        "rule": plan.rule.describe() if plan.rule else None,
                        "warning": plan.structure_warning,
                        "entries": [_entry_summary(e) for e in plan.entries],
                    })
                    return
                if url.path == "/api/diff":
                    self._json(200, _diff_payload(
                        state,
                        qs.get("id", [""])[0],
                        qs.get("left", ["yours"])[0],
                        qs.get("right", ["proposed"])[0],
                    ))
                    return
                if url.path == "/api/edit":
                    self._json(200, _edit_payload(state, qs.get("id", [""])[0]))
                    return
                if url.path == "/api/cancel":
                    self._json(200, {"ok": True})
                    state.done.set()
                    return
                self._json(404, {"error": "not found"})
            except Exception as exc:  # noqa: BLE001 — always answer the browser
                self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

        def do_POST(self):  # noqa: N802
            try:
                if not self._guard():
                    return
                if urlparse(self.path).path != "/api/apply":
                    self._json(404, {"error": "not found"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", 0) or 0)
                except ValueError:
                    length = 0
                if length <= 0 or length > 20_000_000:
                    self._json(400, {"error": "bad request"})
                    return
                body = json.loads(self.rfile.read(length))
                raw = body.get("decisions") if isinstance(body, dict) else None
                if not isinstance(raw, dict):
                    self._json(400, {"error": "bad request"})
                    return
                decisions: dict = {}
                for key, value in raw.items():
                    if key not in state.by_id:
                        continue
                    if isinstance(value, str) and value in ("apply", "theirs", "keep", "skip", "delete"):
                        decisions[key] = value
                    elif isinstance(value, dict) and value.get("action") in ("custom", "apply"):
                        clean = {"action": value["action"]}
                        if value.get("remove_old") is True:
                            clean["remove_old"] = True
                        if isinstance(value.get("content"), str):
                            clean["content"] = value["content"]
                        target = value.get("target")
                        entry = state.by_id[key]
                        if isinstance(target, str) and target in entry.candidates:
                            clean["target"] = target
                        decisions[key] = clean
                state.decisions = decisions
                self._json(200, {"ok": True})
                state.done.set()
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

    return Handler


def review_in_browser(plan: ApplyPlan, project_dir: Path, *, con: Console | None = None) -> dict | None:
    """
    Serve the review page and block until it's submitted or abandoned.
    Returns the decisions, or None if cancelled / timed out / closed.
    """
    con = con or Console()
    state = _ReviewState(plan, project_dir)
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(state))
    except OSError as exc:
        err(f"Could not start the review page: {exc}", con=con)
        return None

    url = f"http://127.0.0.1:{httpd.server_address[1]}/?token={state.token}"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    ok("Review page open", url, con=con)
    info("Bound to 127.0.0.1 only · decide in the browser, then press Apply · Ctrl+C to cancel", con=con)
    open_browser_silent(url)

    start = time.monotonic()
    try:
        with con.status("[cyan]Waiting for your decisions in the browser…[/]", spinner="dots"):
            while not state.done.is_set():
                now = time.monotonic()
                if now - state.last_activity > _IDLE_TIMEOUT_SECONDS or now - start > _HARD_TIMEOUT_SECONDS:
                    break
                time.sleep(0.2)
    except KeyboardInterrupt:
        info("Cancelled.", con=con)
        state.decisions = None
    finally:
        httpd.shutdown()
        httpd.server_close()
    return state.decisions


__all__ = ["review_in_browser", "FileStatus"]
