"""
error_parser.py — Processes raw terminal output into structured debug context.

Responsibilities:
  1. Strip ANSI escape codes from raw output
  2. Detect error blocks using per-framework patterns
  3. Extract file paths referenced in the error block
  4. Strip noise lines to produce a clean terminal-error.txt
  5. Build the auto-generated prompt.txt content

Pipeline (called by watcher.py):
    raw_buffer (bytes/str)
        → strip_ansi()
        → detect_error_block()     returns (error_text, error_type) | None
        → extract_paths()          returns list[Path] (validated against project)
        → strip_noise()            returns cleaned error text
        → build_prompt_txt()       returns prompt.txt content
"""

from __future__ import annotations

import re
from pathlib import Path

# ---------------------------------------------------------------------------
# ANSI escape code stripping
# ---------------------------------------------------------------------------

# Covers:
#   CSI sequences   \x1b[ ... m  (colours, bold, etc.)
#   OSC sequences   \x1b] ... \x07 or ST  (terminal title etc.)
#   Single-char     \x1b[A-Z]    (cursor movement etc.)
#   Hyperlinks      \x1b]8;; ... \x1b\\
_ANSI_RE = re.compile(
    r"\x1b(?:"
    r"\[[0-9;?]*[A-Za-z]"  # CSI sequence
    r"|\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC sequence
    r"|[A-Z@-Z\\^_`]"  # Fe escape sequences
    r"|\([AB012]"  # character set designation
    r")"
)

# Also strip bare carriage returns used by some progress spinners
_CR_RE = re.compile(r"\r(?!\n)")


def strip_ansi(text: str) -> str:
    """Remove all ANSI escape codes and bare carriage returns from *text*."""
    text = _ANSI_RE.sub("", text)
    text = _CR_RE.sub("", text)
    return text


# ---------------------------------------------------------------------------
# Framework pattern loading
# ---------------------------------------------------------------------------


def _load_patterns(ecosystems: list[str]) -> dict:
    """
    Load ERROR_START_PATTERNS, PATH_PATTERNS, NOISE_PATTERNS for the
    detected ecosystems. Always loads base patterns; adds framework-specific
    ones on top.

    Returns a dict with keys: error_start, path, noise — each a list of
    compiled re.Pattern objects.
    """
    from contextzip.rules.errors import python as _py_rules
    from contextzip.rules.errors import node as _node_rules

    error_start: list[re.Pattern] = []
    path: list[re.Pattern] = []
    noise: list[re.Pattern] = []

    # Map ecosystem names (from detector.py) to rule modules
    _ECOSYSTEM_MAP = {
        "Python": _py_rules,
        "Django": _py_rules,
        "FastAPI": _py_rules,
        "Node.js": _node_rules,
        "Next.js": _node_rules,
        "React": _node_rules,
    }

    loaded_modules: set[int] = set()  # avoid loading same module twice

    for eco in ecosystems:
        mod = _ECOSYSTEM_MAP.get(eco)
        if mod is None or id(mod) in loaded_modules:
            continue
        loaded_modules.add(id(mod))
        error_start.extend(getattr(mod, "ERROR_START_PATTERNS", []))
        path.extend(getattr(mod, "PATH_PATTERNS", []))
        noise.extend(getattr(mod, "NOISE_PATTERNS", []))

    # If no ecosystem matched, load both as a best-effort fallback
    if not loaded_modules:
        for mod in (_py_rules, _node_rules):
            error_start.extend(getattr(mod, "ERROR_START_PATTERNS", []))
            path.extend(getattr(mod, "PATH_PATTERNS", []))
            noise.extend(getattr(mod, "NOISE_PATTERNS", []))

    return {"error_start": error_start, "path": path, "noise": noise}


# ---------------------------------------------------------------------------
# Error block detection
# ---------------------------------------------------------------------------

# How many lines of "quiet" output after an error start before we consider
# the block closed. Conservative — better to over-capture than under-capture.
_ERROR_TAIL_LINES = 30

# Generic error signals used as a last-resort fallback (framework-agnostic).
_GENERIC_ERROR_PATTERNS: list[re.Pattern] = [
    re.compile(r"\bError\b.*:"),
    re.compile(r"\berror\b.*:", re.IGNORECASE),
    re.compile(r"\bfailed\b", re.IGNORECASE),
    re.compile(r"\bException\b.*:"),
    re.compile(r"\bpanic\b.*:", re.IGNORECASE),  # Go / Rust
    re.compile(r"Traceback"),
    re.compile(r"stack trace", re.IGNORECASE),
]


def detect_error_block(
    clean_lines: list[str],
    ecosystems: list[str],
) -> tuple[str, str] | None:
    """
    Scan *clean_lines* (ANSI-stripped) for the last error block.

    Searches from the END of the buffer backwards so that for long-running
    dev servers we always find the most recent error, not the first one
    from startup.

    Returns
    -------
    (error_block_text, error_type_label) if an error was found, else None.

    error_type_label is a short human-readable string like "TypeError" or
    "Django exception" used in the auto-generated prompt.txt.
    """
    patterns = _load_patterns(ecosystems)
    start_patterns = patterns["error_start"]

    # Search from the bottom up for the last error start line.
    # Two-pass strategy:
    #   Pass 1 — look for an explicit framework error start pattern.
    #            Bare stack frame lines ("    at ...") are skipped as start
    #            candidates since they are part of a block, not its beginning.
    #   Pass 2 — if pass 1 finds nothing, fall back to generic patterns.
    start_idx: int | None = None
    error_type = "Runtime error"

    # Patterns that indicate we're mid-block, not at a start
    _STACK_FRAME_RE = re.compile(
        r"^\s+at\s+"  # JS:   at functionName (file:line)
        r"|^\s+File \""  # Py:     File "path", line N
        r"|^\s+from\s+\S"  # Rust: from crate::module
    )

    for i in range(len(clean_lines) - 1, -1, -1):
        line = clean_lines[i]
        # Skip bare stack frame lines — they are part of a block, not a start
        if _STACK_FRAME_RE.match(line):
            continue
        matched = _match_any(line, start_patterns)
        if matched:
            start_idx = i
            error_type = _extract_error_type(line, ecosystems)

            if re.match(
                r"^[A-Za-z][A-Za-z0-9_.]*(?:Error|Exception|Warning)\s*:", line
            ):
                # Walk further back to find the Traceback / block header
                for j in range(i - 1, max(i - 60, -1), -1):
                    if re.match(
                        r"^Traceback \(most recent call last\):", clean_lines[j]
                    ):
                        start_idx = j
                        break
                    candidate = clean_lines[j]
                    if (
                        candidate.strip()
                        and not candidate.startswith(" ")
                        and not candidate.startswith("\t")
                        and not re.match(r"^\s*File ", candidate)
                        and not re.match(r"Traceback", candidate)
                    ):
                        break
            break

    # Fallback: generic patterns
    if start_idx is None:
        for i in range(len(clean_lines) - 1, -1, -1):
            line = clean_lines[i]
            if _match_any(line, _GENERIC_ERROR_PATTERNS):
                start_idx = i
                error_type = "Error (generic)"
                break

    if start_idx is None:
        return None

    # Take from start_idx to end of buffer (user triggered capture = error is done)
    block_lines = clean_lines[start_idx:]

    # Remove leading/trailing blank lines
    while block_lines and not block_lines[0].strip():
        block_lines = block_lines[1:]
    while block_lines and not block_lines[-1].strip():
        block_lines = block_lines[:-1]

    if not block_lines:
        return None

    return "\n".join(block_lines), error_type


def _match_any(line: str, patterns: list[re.Pattern]) -> bool:
    return any(p.search(line) for p in patterns)


def _extract_error_type(line: str, ecosystems: list[str]) -> str:
    """
    Try to extract a short error type label from the triggering line.
    Falls back to a framework-aware generic label.
    """
    # Python exception names: "SomeError: message"
    m = re.match(
        r"^(?:\s*(?:Uncaught\s+)?)([A-Za-z][A-Za-z0-9_.]*(?:Error|Exception|Warning))\s*:",
        line,
    )
    if m:
        return m.group(1)

    # Django / DRF prefixed:  django.core.exceptions.ValidationError
    m = re.match(r"^(django\.[^\s:]+|rest_framework\.[^\s:]+)", line)
    if m:
        parts = m.group(1).rsplit(".", 1)
        return parts[-1] if parts else m.group(1)

    # Next.js / webpack:  "Failed to compile" / "Module not found"
    if re.search(r"Failed to compile", line, re.IGNORECASE):
        return "Compilation error"
    if re.search(r"Module not found", line, re.IGNORECASE):
        return "Module not found"
    if re.search(r"npm ERR!", line):
        return "npm error"

    # Generic fallback per ecosystem
    if any(e in ecosystems for e in ("Python", "Django", "FastAPI")):
        return "Python exception"
    if any(e in ecosystems for e in ("Node.js", "Next.js", "React")):
        return "JavaScript error"
    return "Runtime error"


# ---------------------------------------------------------------------------
# Noise stripping
# ---------------------------------------------------------------------------


def strip_noise(error_block: str, ecosystems: list[str]) -> str:
    """
    Remove known noise lines from *error_block*, returning the cleaned text.

    Preserves blank lines that are structurally part of the error (e.g.
    between the traceback and the exception type). Collapses runs of more
    than two consecutive blank lines into one.
    """
    patterns = _load_patterns(ecosystems)
    noise_patterns = patterns["noise"]

    cleaned_lines: list[str] = []
    for line in error_block.splitlines():
        if _match_any(line, noise_patterns):
            continue
        cleaned_lines.append(line)

    # Collapse runs of blank lines
    result: list[str] = []
    blank_run = 0
    for line in cleaned_lines:
        if not line.strip():
            blank_run += 1
            if blank_run <= 1:
                result.append(line)
        else:
            blank_run = 0
            result.append(line)

    return "\n".join(result).strip()


# ---------------------------------------------------------------------------
# File path extraction
# ---------------------------------------------------------------------------

# Directories that are never useful as AI context (stdlib, venv, node_modules)
_PATH_BLOCKLIST: list[re.Pattern] = [
    re.compile(r"/usr/lib/python"),
    re.compile(r"/usr/local/lib/python"),
    re.compile(r"site-packages"),
    re.compile(r"dist-packages"),
    re.compile(r"node_modules"),
    re.compile(r"\.venv"),
    re.compile(r"/venv/"),
    re.compile(r"<frozen "),
    re.compile(r"<string>"),
    re.compile(r"<unknown>"),
    re.compile(r"^internal/"),
    re.compile(r"^node:"),
]


def extract_paths(
    error_block: str,
    project_dir: Path,
    ecosystems: list[str],
) -> list[Path]:
    """
    Extract source file paths referenced in *error_block*.

    Returns absolute Path objects, deduplicated, all confirmed to:
      - Exist on disk
      - Be located under project_dir (no stdlib / venv / node_modules)
    """
    patterns = _load_patterns(ecosystems)
    path_patterns = patterns["path"]

    candidates: list[str] = []

    for line in error_block.splitlines():
        for pattern in path_patterns:
            m = pattern.search(line)
            if m:
                # All our patterns have exactly one capture group
                raw = m.group(1).strip().strip("'\"")
                if raw:
                    candidates.append(raw)

    resolved: list[Path] = []
    seen: set[Path] = set()

    for raw in candidates:
        path = _resolve_path(raw, project_dir)
        if path is None:
            continue
        if path in seen:
            continue
        if _is_blocklisted(str(path)):
            continue
        try:
            path.relative_to(project_dir)  # must be under project_dir
        except ValueError:
            continue
        if not path.is_file():
            continue
        seen.add(path)
        resolved.append(path)

    return resolved


def _resolve_path(raw: str, project_dir: Path) -> Path | None:
    """
    Turn a raw path string from a stack trace into an absolute Path.

    Handles:
      - Absolute paths as-is
      - Relative paths (./foo, ../foo) resolved against project_dir
      - Bare filenames searched recursively in project_dir (last resort)
    """
    raw = raw.strip()
    if not raw:
        return None

    p = Path(raw)

    if p.is_absolute():
        return p.resolve()

    # Relative path
    if raw.startswith("./") or raw.startswith("../"):
        return (project_dir / p).resolve()

    # Bare filename — try to find it in the project
    # (e.g. Go/Rust sometimes emit just "main.rs")
    matches = list(project_dir.rglob(raw))
    if len(matches) == 1:
        return matches[0].resolve()

    return None


def _is_blocklisted(path_str: str) -> bool:
    return any(p.search(path_str) for p in _PATH_BLOCKLIST)


# ---------------------------------------------------------------------------
# prompt.txt generation
# ---------------------------------------------------------------------------


def build_prompt_txt(
    error_type: str,
    ecosystem: str,
    error_block: str,
    referenced_files: list[Path],
    project_dir: Path,
) -> str:
    """
    Build the auto-generated prompt.txt content to include in debug-context.zip.

    Structured so that any AI tool (Claude, ChatGPT, Gemini) immediately
    understands the task without any additional explanation from the user.
    """
    lines: list[str] = [
        f"Framework: {ecosystem}",
        f"Error type: {error_type}",
        "",
        "Task: Debug and fix the runtime error below. "
        "Relevant source files are included in source-files.zip within this archive.",
        "",
        "--- Error ---",
        error_block.strip(),
    ]

    if referenced_files:
        lines.append("")
        lines.append("--- Files included in source-files.zip ---")
        for f in referenced_files:
            try:
                rel = f.relative_to(project_dir).as_posix()
            except ValueError:
                rel = str(f)
            lines.append(f"  - {rel}")

    lines.append("")  # trailing newline

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Full pipeline convenience function
# ---------------------------------------------------------------------------


def process_buffer(
    raw_buffer: str,
    project_dir: Path,
    ecosystems: list[str],
    ecosystem_display: str,
) -> tuple[str, str, list[Path]] | None:
    """
    Run the full pipeline on *raw_buffer*.

    Returns
    -------
    (prompt_txt, terminal_error_txt, referenced_paths)
        if an error block was found and processed, else None.

    Parameters
    ----------
    raw_buffer:
        Raw accumulated output from the child process (may contain ANSI).
    project_dir:
        Absolute path to the project root.
    ecosystems:
        List of detected ecosystem names from DetectionResult.ecosystems.
    ecosystem_display:
        Human-readable display string, e.g. "Next.js + Node.js".
    """
    # 1. Strip ANSI
    clean_text = strip_ansi(raw_buffer)
    clean_lines = clean_text.splitlines()

    # Cap to last 2000 lines to avoid processing enormous buffers
    if len(clean_lines) > 2000:
        clean_lines = clean_lines[-2000:]

    # 2. Detect error block
    result = detect_error_block(clean_lines, ecosystems)
    if result is None:
        return None

    error_block_raw, error_type = result

    # 3. Strip noise from the error block
    terminal_error_txt = strip_noise(error_block_raw, ecosystems)

    # 4. Extract file paths
    referenced_paths = extract_paths(error_block_raw, project_dir, ecosystems)

    # 5. Build prompt.txt
    prompt_txt = build_prompt_txt(
        error_type=error_type,
        ecosystem=ecosystem_display,
        error_block=terminal_error_txt,
        referenced_files=referenced_paths,
        project_dir=project_dir,
    )

    return prompt_txt, terminal_error_txt, referenced_paths
