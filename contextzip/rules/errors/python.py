"""
rules/errors/python.py — Error detection patterns for Python, Django, and FastAPI.

Three pattern sets:
  ERROR_START_PATTERNS  — regex list; any match on a line signals an error block start
  PATH_PATTERNS         — regex list; extracts file paths from error text
  NOISE_PATTERNS        — regex list; lines to strip before saving terminal-error.txt
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Error start detection
# ---------------------------------------------------------------------------
# Any line matching one of these triggers error block accumulation.
# Ordered roughly from most specific to most general.

ERROR_START_PATTERNS: list[re.Pattern] = [
    # Standard Python traceback header
    re.compile(r"^Traceback \(most recent call last\):"),
    # Syntax errors (may appear without a Traceback header)
    re.compile(r"^\s*SyntaxError\s*:"),
    re.compile(r"^\s*IndentationError\s*:"),
    re.compile(r"^\s*TabError\s*:"),
    # Common exception types at the start of a line (end of a traceback block)
    # Listed here so we catch them even if we missed the Traceback header
    re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)*Error\s*:"),
    re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)*Exception\s*:"),
    re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)*Warning\s*:"),
    # Django-specific
    re.compile(r"^django\.core\.exceptions\."),
    re.compile(r"^django\.db\.utils\."),
    re.compile(r"^\[ERROR\]"),  # gunicorn/uvicorn error prefix
    re.compile(r"^ERROR:"),  # logging.ERROR output
    # FastAPI / uvicorn / starlette
    re.compile(r"ERROR:\s+Exception in ASGI"),
    re.compile(r"ERROR:\s+Traceback"),
    # pytest failures
    re.compile(r"^FAILED "),
    re.compile(r"^E\s+[A-Za-z].*Error\s*:"),  # pytest error lines (E    TypeError: ...)
    re.compile(r"^={3,}\s+FAILURES\s+={3,}"),
    re.compile(r"^_{3,}\s+.*\s+_{3,}$"),  # pytest section divider
    # SystemExit / KeyboardInterrupt surfaced as errors
    re.compile(r"^SystemExit\s*:"),
]

# ---------------------------------------------------------------------------
# File path extraction
# ---------------------------------------------------------------------------
# Applied to the accumulated error block text only (not the full buffer).
# Each pattern must have exactly one capture group that yields the file path.

PATH_PATTERNS: list[re.Pattern] = [
    # Standard Python traceback frame:  File "/path/to/file.py", line 42, in func
    re.compile(r'^\s*File "([^"]+\.py)"'),
    # pytest short form:  path/to/test_foo.py:42:
    re.compile(r'^([^\s"]+\.py):\d+'),
    # Django template errors:  Template: path/to/template.html
    re.compile(r"^\s*Template:\s+([^\s,]+\.(?:html|htm|djhtml))"),
    # import errors sometimes show module file paths
    re.compile(r"^\s*\(from ([^)]+\.py)\)"),
]

# ---------------------------------------------------------------------------
# Noise line patterns
# ---------------------------------------------------------------------------
# Lines matching these are stripped from terminal-error.txt.
# Applied AFTER error block extraction so we only clean what we keep.

NOISE_PATTERNS: list[re.Pattern] = [
    # Django dev server startup chatter
    re.compile(r"^Watching for file changes with"),
    re.compile(r"^Performing system checks"),
    re.compile(r"^System check identified"),
    re.compile(r"^Django version"),
    re.compile(r"^Starting development server at"),
    re.compile(r"^Quit the server with"),
    re.compile(
        r"^February|January|March|April|May|June|July|August|September|October|November|December"
    ),
    # Django request logs:  "GET /path HTTP/1.1" 200 1234
    re.compile(r'^"(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+\S+\s+HTTP/\d'),
    # uvicorn / gunicorn startup
    re.compile(r"^INFO:\s+Started"),
    re.compile(r"^INFO:\s+Uvicorn running"),
    re.compile(r"^INFO:\s+Waiting for"),
    re.compile(r"^\[INFO\]\s+Booting"),
    re.compile(r"^\[INFO\]\s+Listening"),
    re.compile(r"^\[INFO\]\s+Worker"),
    # pip install noise (sometimes shown in dev server output)
    re.compile(r"^Requirement already satisfied"),
    re.compile(r"^Installing collected packages"),
    re.compile(r"^Successfully installed"),
    # pytest collection chatter
    re.compile(r"^collecting \.\.\."),
    re.compile(r"^collected \d+ item"),
    re.compile(r"^platform "),
    re.compile(r"^cacheprovider"),
    # Blank / whitespace-only lines at the very start or between blocks
    # (handled in parser, not here — kept for reference)
]
