"""
ai/heuristic.py — Keyword-based file relevance scorer.

Used as a fallback when the Gemini API is unavailable (rate limit, network
error, no key). Scores each file purely from the prompt text and file path —
no API calls, no dependencies beyond stdlib.

This is intentionally simple and transparent. It will never be as accurate
as Gemini, but it is always available and produces reasonable results for
common cases like "update the login page" or "fix the toast component".

Scoring model
─────────────
Each file receives a score based on:

  1. Token overlap  — how many meaningful words from the prompt appear in
                      the file's path components (stem + parent dirs).
                      Longer matches score higher.

  2. Directory bias — source-like directories (src/, app/, lib/, components/,
                      utils/, routes/, pages/, server/, api/) score higher.
                      Test and documentation directories score lower.

  3. Extension bias — code files score higher than config, lock, or data files.

Files with a score of zero are excluded entirely. The top-N results are
returned, where N is capped at MAX_FILES.
"""

from __future__ import annotations

import re
from pathlib import Path

# Never return more files than this regardless of score
MAX_FILES = 8

# Words that carry no signal — filtered out before scoring
_STOPWORDS = frozenset({
    "i", "a", "an", "the", "to", "in", "on", "at", "of", "for",
    "and", "or", "but", "is", "it", "be", "do", "my", "this",
    "that", "with", "from", "want", "need", "make", "update",
    "change", "fix", "add", "get", "use", "set", "new", "old",
    "file", "code", "function", "method", "class", "variable",
})

# Directory names that suggest source code worth including
_SOURCE_DIRS = frozenset({
    "src", "app", "lib", "libs", "components", "component",
    "utils", "util", "helpers", "helper", "routes", "route",
    "pages", "page", "server", "api", "services", "service",
    "hooks", "hook", "store", "stores", "context", "contexts",
    "middleware", "handlers", "handler", "controllers", "controller",
    "views", "view", "models", "model", "core", "common", "shared",
})

# Directory names that suggest low relevance for most coding tasks
_NOISE_DIRS = frozenset({
    "test", "tests", "__tests__", "spec", "specs",
    "docs", "doc", "documentation", "examples", "example",
    "fixtures", "mocks", "mock", "stubs", "stub",
    "scripts", "bin", "dist", "build", "out", "coverage",
})

# Extensions that suggest runnable/editable source code
_CODE_EXTENSIONS = frozenset({
    ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".rb",
    ".java", ".kt", ".swift", ".cs", ".cpp", ".c", ".h",
    ".vue", ".svelte", ".astro",
})

# Extensions that are config-like (lower relevance unless prompt mentions them)
_CONFIG_EXTENSIONS = frozenset({
    ".json", ".toml", ".yaml", ".yml", ".ini", ".cfg", ".env",
    ".md", ".txt", ".lock", ".sum",
})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def select_files(
    *,
    prompt: str,
    file_tree: list[tuple[str, int]],
) -> list[str]:
    """
    Score and rank *file_tree* entries by relevance to *prompt*.

    Parameters
    ----------
    prompt:
        The user's natural-language task description.
    file_tree:
        Candidate files as (relative_posix_path, size_bytes) tuples.
        Standard exclusions must already have been applied by the caller.

    Returns
    -------
    list[str]
        Relative POSIX paths of the top-scoring files, best first.
        Files scoring zero are excluded. Result is capped at MAX_FILES.
    """
    tokens = _tokenize(prompt)
    if not tokens:
        # Degenerate prompt — return nothing rather than random files
        return []

    scored: list[tuple[float, str]] = []

    for rel_path, size_bytes in file_tree:
        score = _score(rel_path, tokens, size_bytes)
        if score > 0:
            scored.append((score, rel_path))

    # Sort descending by score, then alphabetically for determinism on ties
    scored.sort(key=lambda x: (-x[0], x[1]))

    return [path for _, path in scored[:MAX_FILES]]


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _score(rel_path: str, tokens: set[str], size_bytes: int) -> float:
    """Compute a relevance score for a single file path."""
    path = Path(rel_path)
    stem = path.stem.lower()
    suffix = path.suffix.lower()
    parts = [p.lower() for p in path.parts]
    dirs = parts[:-1]  # everything except the filename itself

    score = 0.0

    # ── 1. Token overlap ─────────────────────────────────────────────────────
    # Split the stem by common separators (camelCase, kebab-case, snake_case)
    stem_words = set(_split_identifier(stem))
    dir_words: set[str] = set()
    for d in dirs:
        dir_words.update(_split_identifier(d))

    # Direct hits in filename stem score highest
    stem_hits = tokens & stem_words
    score += len(stem_hits) * 3.0

    # Partial substring matches in stem (e.g. "toast" in "useToast")
    for token in tokens:
        if len(token) >= 4 and token in stem and token not in stem_hits:
            score += 1.0

    # Hits in parent directory names score lower
    dir_hits = tokens & dir_words
    score += len(dir_hits) * 1.5

    # No token overlap at all → zero score, file is irrelevant
    if score == 0.0:
        return 0.0

    # ── 2. Directory bias ────────────────────────────────────────────────────
    dir_set = set(dirs)
    if dir_set & _SOURCE_DIRS:
        score *= 1.4
    if dir_set & _NOISE_DIRS:
        score *= 0.4

    # ── 3. Extension bias ────────────────────────────────────────────────────
    if suffix in _CODE_EXTENSIONS:
        score *= 1.2
    elif suffix in _CONFIG_EXTENSIONS:
        score *= 0.7

    # ── 4. Penalise very large files ─────────────────────────────────────────
    # Files over 100 KB are less likely to be what you want to change
    if size_bytes > 100_000:
        score *= 0.6

    return score


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> set[str]:
    """
    Extract meaningful lowercase tokens from *text*.

    - Splits on whitespace and punctuation
    - Removes stopwords
    - Keeps only tokens of 3+ characters
    """
    raw = re.findall(r"[a-zA-Z]+", text.lower())
    return {w for w in raw if len(w) >= 3 and w not in _STOPWORDS}


def _split_identifier(name: str) -> list[str]:
    """
    Split a file/directory name into component words.

    Handles: kebab-case, snake_case, camelCase, PascalCase.

    Examples:
        "LoginPage"   → ["login", "page"]
        "use-toast"   → ["use", "toast"]
        "auth_utils"  → ["auth", "utils"]
        "apiClient"   → ["api", "client"]
    """
    # Insert space before uppercase letters following lowercase (camelCase)
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
    # Split on non-alphanumeric separators
    parts = re.split(r"[^a-zA-Z0-9]+", spaced)
    return [p.lower() for p in parts if p]
