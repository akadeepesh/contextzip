"""
webui/server.py — Local-only HTTP server powering `contextzip config --ui`.

Design goals, in order:
  1. Nothing about the user's project ever leaves their machine. The
     server binds to 127.0.0.1 only (never 0.0.0.0), and makes zero
     outbound network calls of its own — no telemetry, no "phone home".
     The only thing that touches the network is the browser talking to
     this same-machine server.
  2. Every request must present a random, single-use session token — the
     same approach Jupyter Notebook uses — so another local process or
     browser tab can't read or reconfigure someone else's project just by
     guessing a port number.
  3. It shuts itself down without being asked twice: on a successful save,
     if the tab is abandoned for a while, or after a hard time ceiling —
     so a forgotten `--ui` run doesn't linger as an open localhost port.

The file tree is scanned from disk exactly once at startup
(filters.scan_all_files); every subsequent checkbox toggle in the browser
re-classifies that same in-memory list (filters.classify_scanned_files)
rather than re-walking the filesystem, which is what keeps the live
preview feeling instant even on larger projects.
"""

from __future__ import annotations

import hmac
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from rich.console import Console

from contextzip.cli_onboard import open_browser_silent
from contextzip.cli_display import ok, warn, err, info
from contextzip.filters import (
    build_spec,
    build_force_include_spec,
    scan_all_files,
    classify_scanned_files,
)
from contextzip.project_config import load_project_config, project_config_path
from contextzip.webui import suggestions as _suggestions
from contextzip.webui.assets import INDEX_HTML, TOKEN_ERROR_HTML
from contextzip.webui.persist import save_config_from_ui

# If the tab sits idle (no requests at all) this long, assume it was
# abandoned and shut the server down rather than leaving a port open.
_IDLE_TIMEOUT_SECONDS = 15 * 60

# Absolute ceiling regardless of activity — a safety net, not the normal
# exit path (saving, or the idle timeout above, both fire first in
# practice).
_HARD_TIMEOUT_SECONDS = 60 * 60


class _ConfigUIState:
    """Shared, mutable state for one `--ui` session."""

    def __init__(self, project_dir: Path, detection, project_cfg):
        self.project_dir = project_dir
        self.detection = detection
        self.token = secrets.token_urlsafe(24)
        self.lock = threading.Lock()
        self.last_activity = time.monotonic()
        self.saved = False
        self.should_stop = threading.Event()
        self.gitignore_path = project_dir / ".gitignore"

        # Scanned once — every /api/preview call reclassifies this same
        # list in memory instead of touching the filesystem again.
        self.all_files = scan_all_files(project_dir)

        # Seed the working state from whatever's already configured, so
        # re-opening the UI on a project that already has a config.json
        # shows what's actually in effect, not a blank slate.
        self.always_include: list[str] = list(project_cfg.always_include)
        self.always_exclude: list[str] = list(project_cfg.always_exclude)
        self.workspace_location: str = project_cfg.workspace_location or "git-root"
        self.scan_depth: int = (
            project_cfg.scan_depth if project_cfg.scan_depth is not None else 2
        )
        self.ai: dict = {
            "enabled": project_cfg.ai.enabled,
            "provider": project_cfg.ai.provider,
            "max_files": project_cfg.ai.max_files,
            "prompt_template": project_cfg.ai.prompt_template,
        }
        self.limits: dict = {
            "max_file_size_mb": project_cfg.limits.max_file_size_mb,
            "redact_secrets": project_cfg.limits.redact_secrets,
        }
        self.applied_zip_retention: int = project_cfg.applied_zip_retention
        self.webui: dict = {
            "auto_open": project_cfg.webui.auto_open,
            "port": project_cfg.webui.port,
        }

    def touch(self) -> None:
        self.last_activity = time.monotonic()

    def classify(self, always_include: list[str], always_exclude: list[str]):
        spec = build_spec(
            rule_modules=self.detection.rule_modules,
            extra_exclude=always_exclude or None,
            gitignore_path=self.gitignore_path,
        )
        force_include = build_force_include_spec(always_include or None)
        return classify_scanned_files(
            self.project_dir, self.all_files, spec, force_include
        )


def _make_handler(state: _ConfigUIState):
    """Build a BaseHTTPRequestHandler subclass bound to *state*."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "contextzip-config-ui"

        def log_message(self, format, *args):  # noqa: A002 — stdlib signature
            pass  # keep the terminal clean; no per-request access log spam

        # ---- helpers -----------------------------------------------------

        def _token_from_request(self) -> str | None:
            qs = parse_qs(urlparse(self.path).query)
            token = qs.get("token", [None])[0]
            if token:
                return token
            return self.headers.get("X-Contextzip-Token")

        def _authorized(self) -> bool:
            token = self._token_from_request()
            if not token:
                return False
            return hmac.compare_digest(token, state.token)

        def _send_json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, status: int, html: str) -> None:
            body = html.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _read_json_body(self) -> dict:
            try:
                length = int(self.headers.get("Content-Length", 0) or 0)
            except ValueError:
                length = 0
            if length <= 0 or length > 5_000_000:
                return {}
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw)
                return data if isinstance(data, dict) else {}
            except json.JSONDecodeError:
                return {}

        # ---- tree + stats building -----------------------------------

        def _rel(self, abs_path: Path) -> str | None:
            """Return the project-relative POSIX path, or None if abs_path
            somehow isn't under project_dir (defensive — scan_all_files
            should already guarantee this, but a single bad entry here
            must never take down the whole request)."""
            try:
                return abs_path.relative_to(state.project_dir).as_posix()
            except ValueError:
                return None

        def _build_payload(
            self, always_include: list[str], always_exclude: list[str]
        ) -> dict:
            classified = state.classify(always_include, always_exclude)

            root: dict = {"name": "", "path": "", "type": "dir", "children": {}}
            included_count = 0
            excluded_count = 0
            included_bytes = 0
            included_for_suggestions: list[tuple[str, int]] = []

            for abs_path, size in state.all_files:
                rel = self._rel(abs_path)
                if rel is None:
                    continue  # see _rel's docstring — should never happen, skip if it does

                is_included = classified.get(abs_path, True)
                parts = rel.split("/")
                node = root
                acc = ""
                for i, part in enumerate(parts):
                    acc = part if not acc else f"{acc}/{part}"
                    is_last = i == len(parts) - 1
                    children = node["children"]
                    if part not in children:
                        children[part] = {
                            "name": part,
                            "path": acc,
                            "type": "file" if is_last else "dir",
                            "children": None if is_last else {},
                            "size": size if is_last else 0,
                            "included": is_included if is_last else None,
                        }
                    node = children[part]

                if is_included:
                    included_count += 1
                    included_bytes += size
                    included_for_suggestions.append((rel, size))
                else:
                    excluded_count += 1

            def rollup(node: dict) -> tuple[int, int]:
                if node["type"] == "file":
                    return (1 if node["included"] else 0, 1)
                included = total = 0
                size_sum = 0
                for child in node["children"].values():
                    ci, ct = rollup(child)
                    included += ci
                    total += ct
                    size_sum += child["size"]
                node["size"] = size_sum
                if total == 0:
                    node["included"] = True
                elif included == total:
                    node["included"] = True
                elif included == 0:
                    node["included"] = False
                else:
                    node["included"] = "partial"
                return included, total

            rollup(root)

            def to_list(node: dict):
                if node["children"] is None:
                    return node
                ordered = sorted(
                    node["children"].values(),
                    key=lambda n: (n["type"] != "dir", n["name"].lower()),
                )
                node["children"] = [to_list(c) for c in ordered]
                return node

            tree = to_list(root)["children"]
            suggestions = _suggestions.build_suggestions(included_for_suggestions)

            return {
                "tree": tree,
                "stats": {
                    "includedCount": included_count,
                    "excludedCount": excluded_count,
                    "includedBytes": included_bytes,
                    "totalCount": included_count + excluded_count,
                },
                "suggestions": suggestions,
            }

        # ---- routes --------------------------------------------------

        def do_GET(self):  # noqa: N802 — stdlib method name
            try:
                self._dispatch_get()
            except Exception as exc:  # noqa: BLE001 — last-resort safety net
                # Whatever this is, the client must get a real response —
                # a bare exception here otherwise drops the connection
                # with no body at all, which just looks like "Failed to
                # fetch" in the browser with zero diagnostic value. Still
                # print it so it's visible in the terminal for debugging.
                import traceback

                traceback.print_exc()
                try:
                    self._send_json(500, {"error": f"{type(exc).__name__}: {exc}"})
                except Exception:
                    pass

        def _dispatch_get(self):
            state.touch()
            path = urlparse(self.path).path

            if path == "/":
                if not self._authorized():
                    self._send_html(403, TOKEN_ERROR_HTML)
                    return
                self._send_html(200, INDEX_HTML.replace("__TOKEN__", state.token))
                return

            if path == "/api/state":
                if not self._authorized():
                    self._send_json(403, {"error": "invalid token"})
                    return
                payload = self._build_payload(
                    state.always_include, state.always_exclude
                )
                payload["project"] = {
                    "name": state.project_dir.name or str(state.project_dir),
                    "path": str(state.project_dir),
                    "ecosystem": state.detection.display_name,
                }
                payload["config"] = {
                    "always_include": state.always_include,
                    "always_exclude": state.always_exclude,
                    "workspace_location": state.workspace_location,
                    "scan_depth": state.scan_depth,
                    "ai": state.ai,
                    "limits": state.limits,
                    "applied_zip_retention": state.applied_zip_retention,
                    "webui": state.webui,
                }
                payload["configPath"] = str(project_config_path(state.project_dir))
                self._send_json(200, payload)
                return

            if path == "/api/shutdown":
                if not self._authorized():
                    self._send_json(403, {"error": "invalid token"})
                    return
                self._send_json(200, {"ok": True})
                state.should_stop.set()
                return

            self._send_json(404, {"error": "not found"})

        def do_POST(self):  # noqa: N802
            try:
                self._dispatch_post()
            except Exception as exc:  # noqa: BLE001 — last-resort safety net
                import traceback

                traceback.print_exc()
                try:
                    self._send_json(500, {"error": f"{type(exc).__name__}: {exc}"})
                except Exception:
                    pass

        def _dispatch_post(self):
            state.touch()
            path = urlparse(self.path).path

            if path == "/api/preview":
                if not self._authorized():
                    self._send_json(403, {"error": "invalid token"})
                    return
                body = self._read_json_body()
                always_include = [
                    p for p in body.get("always_include", []) if isinstance(p, str)
                ]
                always_exclude = [
                    p for p in body.get("always_exclude", []) if isinstance(p, str)
                ]
                payload = self._build_payload(always_include, always_exclude)
                self._send_json(200, payload)
                return

            if path == "/api/save":
                if not self._authorized():
                    self._send_json(403, {"error": "invalid token"})
                    return
                body = self._read_json_body()
                always_include = [
                    p.strip()
                    for p in body.get("always_include", [])
                    if isinstance(p, str) and p.strip()
                ]
                always_exclude = [
                    p.strip()
                    for p in body.get("always_exclude", [])
                    if isinstance(p, str) and p.strip()
                ]

                workspace_location = body.get("workspace_location")
                if (
                    not isinstance(workspace_location, str)
                    or not workspace_location.strip()
                ):
                    workspace_location = None

                scan_depth = body.get("scan_depth")
                if (
                    not isinstance(scan_depth, int)
                    or isinstance(scan_depth, bool)
                    or scan_depth < 0
                ):
                    scan_depth = None

                ai_in = body.get("ai") if isinstance(body.get("ai"), dict) else {}
                ai = {}
                if isinstance(ai_in.get("enabled"), bool):
                    ai["enabled"] = ai_in["enabled"]
                if isinstance(ai_in.get("provider"), str) and ai_in["provider"].strip():
                    ai["provider"] = ai_in["provider"].strip()
                mf = ai_in.get("max_files")
                if isinstance(mf, int) and not isinstance(mf, bool) and mf >= 1:
                    ai["max_files"] = mf
                if isinstance(ai_in.get("prompt_template"), str):
                    ai["prompt_template"] = ai_in["prompt_template"].strip()

                limits_in = (
                    body.get("limits") if isinstance(body.get("limits"), dict) else {}
                )
                limits = {}
                mfs = limits_in.get("max_file_size_mb")
                if isinstance(mfs, (int, float)) and not isinstance(mfs, bool) and mfs > 0:
                    limits["max_file_size_mb"] = mfs
                if isinstance(limits_in.get("redact_secrets"), bool):
                    limits["redact_secrets"] = limits_in["redact_secrets"]

                applied_zip_retention = body.get("applied_zip_retention")
                if (
                    not isinstance(applied_zip_retention, int)
                    or isinstance(applied_zip_retention, bool)
                    or applied_zip_retention < 1
                ):
                    applied_zip_retention = None

                webui_in = (
                    body.get("webui") if isinstance(body.get("webui"), dict) else {}
                )
                webui = {}
                if isinstance(webui_in.get("auto_open"), bool):
                    webui["auto_open"] = webui_in["auto_open"]
                port = webui_in.get("port")
                if port is None:
                    webui["port"] = None
                elif isinstance(port, int) and not isinstance(port, bool) and 1024 <= port <= 65535:
                    webui["port"] = port

                with state.lock:
                    try:
                        saved_path = save_config_from_ui(
                            state.project_dir,
                            always_include=always_include,
                            always_exclude=always_exclude,
                            workspace_location=workspace_location,
                            scan_depth=scan_depth,
                            ai=ai or None,
                            limits=limits or None,
                            applied_zip_retention=applied_zip_retention,
                            webui=webui or None,
                        )
                    except OSError as exc:
                        self._send_json(500, {"error": str(exc)})
                        return
                    state.always_include = always_include
                    state.always_exclude = always_exclude
                    if workspace_location is not None:
                        state.workspace_location = workspace_location
                    if scan_depth is not None:
                        state.scan_depth = scan_depth
                    state.ai.update(ai)
                    state.limits.update(limits)
                    if applied_zip_retention is not None:
                        state.applied_zip_retention = applied_zip_retention
                    state.webui.update(webui)
                    state.saved = True
                self._send_json(200, {"ok": True, "path": str(saved_path)})
                return

            self._send_json(404, {"error": "not found"})

    return Handler


def launch_config_ui(project_dir: Path, detection, *, con: Console = None) -> bool:
    """
    Start the local config UI, open the browser, and block until the user
    finishes — either by saving, by the tab going idle long enough to
    assume it was abandoned, or by Ctrl+C in the terminal.

    Returns True if a config was saved during this session.
    """
    con = con or Console()
    project_cfg = load_project_config(project_dir)
    state = _ConfigUIState(project_dir, detection, project_cfg)
    handler_cls = _make_handler(state)

    # A configured fixed port (project_cfg.webui.port) is tried first —
    # useful behind strict local-port firewall rules — but never blocks
    # startup: if it's taken, fall straight back to a random free port
    # rather than failing the whole command over one occupied port.
    preferred_port = project_cfg.webui.port
    httpd = None
    if preferred_port is not None:
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", preferred_port), handler_cls)
        except OSError:
            warn(f"Configured port {preferred_port} is in use — picking a free one instead", con=con)
            httpd = None

    if httpd is None:
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        except OSError as exc:
            err(f"Could not start the local config UI: {exc}", con=con)
            return False

    port = httpd.server_address[1]
    url = f"http://127.0.0.1:{port}/?token={state.token}"

    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()

    ok("Config UI running", url, con=con)
    info("Bound to 127.0.0.1 only · Ctrl+C to stop", con=con)

    if project_cfg.webui.auto_open:
        open_browser_silent(url)
    else:
        info("Auto-open is off — open the link above manually.", con=con)

    start = time.monotonic()
    try:
        with con.status(
            "[cyan]Waiting for you to finish in the browser…[/]", spinner="dots"
        ):
            while not state.should_stop.is_set() and not state.saved:
                now = time.monotonic()
                if now - state.last_activity > _IDLE_TIMEOUT_SECONDS:
                    break
                if now - start > _HARD_TIMEOUT_SECONDS:
                    break
                time.sleep(0.25)
    except KeyboardInterrupt:
        info("Stopped.", con=con)
    finally:
        httpd.shutdown()
        httpd.server_close()

    if state.saved:
        ok("Saved to", str(project_config_path(project_dir)), con=con)
    else:
        info("Config UI closed without saving.", con=con)

    return state.saved
