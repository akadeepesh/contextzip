"""
service.py — A small web service that packages public GitHub repositories.

It's the server side of contextzipcli.com's "paste a GitHub URL" (and of
contextzipcli.com/github.com/user/repo). A WSGI app with no dependencies
beyond contextzip itself; on Vercel the whole function is:

    # api/pack.py                        requirements.txt: contextzip
    from contextzip.service import app

Requests (any path; only the query string matters):

    GET ?repo=github.com/user/repo[&ref=main]             the ZIP
    GET ?repo=…&format=json                               what's in it, as JSON

The repo can be anything the CLI accepts, including /tree/<branch>/<folder>
links. The ZIP is made exactly as `contextzip --from REPO` makes it: stack
detection, exclusions, secrets left out or redacted, CONTEXTZIP.md added.

Safety, since it runs for anyone:
  - Only public repositories, checked with GitHub on every request, so a
    server token (GITHUB_TOKEN, for the higher rate limit) can never expose
    the private repositories of the account it belongs to. Prefer a token
    that can only read public repositories anyway.
  - Downloads are capped (CONTEXTZIP_REMOTE_MAX_MB, default 100 here).
  - A per-client rate limit (CONTEXTZIP_SERVICE_RATE requests a minute,
    default 20), per server instance.
  - Responses never include server paths.

The cache lives in the temp folder (CONTEXTZIP_CACHE_DIR to change it).
Responses for a full commit SHA are cacheable forever; branch responses
for five minutes. X-Contextzip-Commit says which commit was packaged.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from collections import defaultdict, deque
from urllib.parse import parse_qs

from contextzip import remote

_STATUS = {
    "invalid": "400 Bad Request",
    "not_found": "404 Not Found",
    "private": "403 Forbidden",
    "too_big": "413 Content Too Large",
    "rate_limited": "429 Too Many Requests",
    "auth": "502 Bad Gateway",
    "unavailable": "502 Bad Gateway",
}
_SHA_RE = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)
_CORS = [
    ("Access-Control-Allow-Origin", "*"),
    ("Access-Control-Expose-Headers", "Content-Disposition, X-Contextzip-Commit"),
]


def _configure() -> None:
    """Settings a public service must have, unless the deployment overrides them."""
    os.environ[remote.PUBLIC_ONLY_ENV_VAR] = "1"  # never overridable
    os.environ.setdefault(remote.MAX_MB_ENV_VAR, "100")
    os.environ.setdefault(remote.CACHE_ENV_VAR, os.path.join(tempfile.gettempdir(), "contextzip-cache"))
    os.environ.setdefault("CONTEXTZIP_WORKSPACE_LOCATION", "git-root")


class _RateLimiter:
    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self.hits: dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, client: str) -> bool:
        if self.per_minute <= 0:
            return True
        now = time.monotonic()
        with self.lock:
            hits = self.hits[client]
            while hits and now - hits[0] > 60:
                hits.popleft()
            if len(hits) >= self.per_minute:
                return False
            hits.append(now)
            return True


def _rate_from_env() -> int:
    try:
        return int(os.environ.get("CONTEXTZIP_SERVICE_RATE", "20"))
    except ValueError:
        return 20


_limiter = _RateLimiter(_rate_from_env())


def _client(environ: dict) -> str:
    forwarded = environ.get("HTTP_X_FORWARDED_FOR", "")
    return forwarded.split(",")[0].strip() or environ.get("REMOTE_ADDR", "unknown")


def _json(start_response, status: str, payload: dict, extra_headers=()):
    body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    headers = [("Content-Type", "application/json"), ("Content-Length", str(len(body))), ("Cache-Control", "no-store"), *_CORS, *extra_headers]
    start_response(status, headers)
    return [body]


def _error(start_response, status: str, message: str, kind: str):
    return _json(start_response, status, {"ok": False, "error": message, "error_kind": kind})


def summary(result: dict) -> dict:
    """The parts of a --json result that are safe and useful to send back."""
    files = result.get("files") or {}
    zip_info = result.get("zip") or {}
    detection = result.get("detection") or {}
    rem = result.get("remote") or {}
    return {
        "ok": True,
        "repo": rem.get("repo"),
        "commit": rem.get("sha"),
        "ref": rem.get("ref"),
        "folder": rem.get("subdir"),
        "detected": detection.get("ecosystems", []),
        "files": {
            "included": files.get("included"),
            "excluded": files.get("excluded"),
            "included_bytes": files.get("included_bytes"),
            "included_files": files.get("included_files", []),
        },
        "zip": {
            "files": zip_info.get("files"),
            "compressed_bytes": zip_info.get("compressed_bytes"),
            "estimated_tokens": zip_info.get("estimated_tokens"),
            "redacted": [r.get("path") for r in zip_info.get("redacted", [])],
        },
        "warnings": result.get("warnings", []),
    }


def app(environ: dict, start_response):
    """The WSGI application."""
    _configure()
    method = environ.get("REQUEST_METHOD", "GET").upper()
    if method == "OPTIONS":
        start_response("204 No Content", [*_CORS, ("Access-Control-Allow-Methods", "GET, OPTIONS"), ("Access-Control-Max-Age", "86400")])
        return [b""]
    if method not in ("GET", "HEAD"):
        return _error(start_response, "405 Method Not Allowed", "Use GET", "invalid")

    query = parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=False)
    repo = (query.get("repo") or [""])[0].strip()
    ref = (query.get("ref") or [""])[0].strip() or None
    fmt = (query.get("format") or ["zip"])[0].strip().lower()
    if not repo:
        return _error(start_response, "400 Bad Request", "Add ?repo=github.com/user/repo", "invalid")
    if fmt not in ("zip", "json"):
        return _error(start_response, "400 Bad Request", "format must be zip or json", "invalid")
    try:
        remote.parse_target(repo)
    except remote.RemoteError as exc:
        return _error(start_response, _STATUS.get(exc.kind, "400 Bad Request"), str(exc), exc.kind)
    if not _limiter.allow(_client(environ)):
        return _error(start_response, "429 Too Many Requests", "Too many requests. Wait a minute and try again.", "rate_limited")

    from contextzip.web import run

    args = ["--from", repo, "--no-clipboard"]
    if ref:
        args += ["--ref", ref]
    result = run(args, cwd=os.environ[remote.CACHE_ENV_VAR] if os.path.isdir(os.environ[remote.CACHE_ENV_VAR]) else tempfile.gettempdir())
    if not result.get("ok"):
        kind = result.get("error_kind") or ("not_found" if "Nothing to compress" in (result.get("error") or "") else "unavailable")
        return _error(start_response, _STATUS.get(kind, "502 Bad Gateway"), result.get("error") or "Packaging failed", kind)

    info = summary(result)
    sha = info["commit"] or ""
    immutable = bool(ref and _SHA_RE.match(ref))
    cache = "public, max-age=31536000, immutable" if immutable else "public, max-age=60, s-maxage=300"
    headers = [("Cache-Control", cache), ("X-Contextzip-Commit", sha), *_CORS]

    if fmt == "json":
        body = json.dumps(info, ensure_ascii=True).encode("utf-8")
        start_response("200 OK", [("Content-Type", "application/json"), ("Content-Length", str(len(body))), *headers])
        return [body]

    zip_path = (result.get("zip") or {}).get("path")
    try:
        with open(zip_path, "rb") as fh:
            data = fh.read()
    except (OSError, TypeError):
        return _error(start_response, "500 Internal Server Error", "The ZIP couldn't be read back", "unavailable")
    name = re.sub(r"[^A-Za-z0-9._-]", "-", f"{(info['repo'] or 'repo').split('/')[-1]}-{sha[:7]}.zip")
    start_response(
        "200 OK",
        [
            ("Content-Type", "application/zip"),
            ("Content-Length", str(len(data))),
            ("Content-Disposition", f'attachment; filename="{name}"'),
            *headers,
        ],
    )
    return [b"" if method == "HEAD" else data]
