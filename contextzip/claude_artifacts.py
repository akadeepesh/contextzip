"""
claude_artifacts.py — Fetches Claude.ai artifact files referenced in a
conversation export.

This talks to an undocumented Claude.ai endpoint using the user's own
session cookie (contextzip.config.get_session_key()). It is explicitly
best-effort: a missing key, an expired cookie, or the endpoint changing
shape should degrade gracefully — code_changes.py simply skips case 2
(diverged-from-Claude) for affected files — rather than aborting the whole
eod/handoff run. Nothing here should ever raise out to the caller for a
single failed download; only total inability to attempt the fetch at all
(httpx missing) is surfaced as an immediate empty result with an error.

Known fragility, by design, not oversight: this is reverse-engineered from
the claude.ai web app's own network calls, not a published API. It may stop
working without notice, and depending on Cloudflare's bot-management state
it may also simply fail unpredictably from a non-browser client regardless
of how correct the request looks.
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

try:
    import httpx

    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False


DOWNLOAD_LINK_RE = re.compile(
    r"\[([^\]]+)\]\((https://claude\.ai/api/organizations/[^)\s]+"
    r"/wiggle/download-file\?path=[^)\s]+)\)"
)

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
}


@dataclass
class ArtifactLink:
    label: str
    url: str
    filename: str


def extract_links(export_text: str) -> list[ArtifactLink]:
    """Pull every unique download-file link out of an export's raw text."""
    seen: set[str] = set()
    links: list[ArtifactLink] = []

    for label, url in DOWNLOAD_LINK_RE.findall(export_text):
        if url in seen:
            continue
        seen.add(url)

        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed.query)
        raw_path = query.get("path", [""])[0]
        decoded = urllib.parse.unquote(raw_path)
        filename = Path(decoded).name or Path(label).name or "artifact"

        links.append(ArtifactLink(label=label, url=url, filename=filename))

    return links


def fetch_artifacts(
    export_text: str,
    dest_dir: Path,
    session_key: str,
) -> tuple[list[Path], list[str]]:
    """
    Download every artifact link found in *export_text* into *dest_dir*.

    Returns (saved_paths, errors). Never raises for an individual download
    failure — each failure becomes a string in *errors* so callers (brief.py)
    can surface them as warnings and continue with whatever did succeed.
    """
    if not _HTTPX_AVAILABLE:
        return [], ["httpx is not installed — cannot fetch Claude artifacts."]

    links = extract_links(export_text)
    if not links:
        return [], []

    dest_dir.mkdir(parents=True, exist_ok=True)

    saved: list[Path] = []
    errors: list[str] = []

    session_key = session_key.strip().strip('"').strip("'")
    headers_base = dict(_BROWSER_HEADERS)
    headers_base["Cookie"] = f"sessionKey={session_key}"

    with httpx.Client(timeout=30, follow_redirects=True) as client:
        for link in links:
            headers = dict(headers_base)
            headers["Referer"] = _conversation_referer(link.url)
            headers["Origin"] = "https://claude.ai"

            try:
                resp = client.get(link.url, headers=headers)
            except httpx.RequestError as exc:
                errors.append(f"{link.filename}: network error ({exc})")
                continue

            if resp.status_code in (401, 403):
                errors.append(
                    f"{link.filename}: auth failed ({resp.status_code}) — session key "
                    "may be missing/expired, or this request was blocked before it "
                    "even reached Claude's app (Cloudflare bot protection)."
                )
                continue
            if resp.status_code == 404:
                errors.append(
                    f"{link.filename}: not found (404) — the file or conversation "
                    "session may have expired."
                )
                continue
            if resp.status_code != 200:
                errors.append(f"{link.filename}: HTTP {resp.status_code}")
                continue

            dest = dest_dir / link.filename
            try:
                dest.write_bytes(resp.content)
                saved.append(dest)
            except OSError as exc:
                errors.append(f"{link.filename}: could not write file ({exc})")

    return saved, errors


def _conversation_referer(url: str) -> str:
    match = re.search(r"/conversations/([0-9a-fA-F-]+)/wiggle", url)
    if match:
        return f"https://claude.ai/chat/{match.group(1)}"
    return "https://claude.ai/"
