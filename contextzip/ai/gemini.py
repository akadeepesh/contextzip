"""
ai/gemini.py — Thin Gemini API client for contextzip's prompt-aware mode.

Makes a single POST to the Gemini generateContent endpoint and returns
a ranked list of file paths relevant to the user's task description.

Design principles:
  - Raw httpx calls only — no Google SDK dependency
  - Strict JSON output from the model — no markdown, no prose
  - Validates every returned path against the real file tree
  - Single responsibility: call API, parse response, validate paths
"""

from __future__ import annotations

import json

try:
    import httpx

    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_API_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models"
    "/{model}:generateContent?key={key}"
)

DEFAULT_MODEL = "gemini-2.5-flash-lite"

# Hard cap: never return more than this many files regardless of model output.
# Minimum context is the goal — the prompt enforces this, but we double-guard.
_MAX_FILES = 12

_TIMEOUT_SECONDS = 30


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class GeminiError(Exception):
    """Raised when the Gemini API call fails for any reason."""


class GeminiUnavailable(GeminiError):
    """Raised when httpx is not installed."""


class GeminiRateLimitError(GeminiError):
    """Raised specifically on HTTP 429 — allows callers to trigger fallback."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def select_files(
    *,
    api_key: str,
    prompt: str,
    file_tree: list[tuple[str, int]],  # [(rel_path, size_bytes), ...]
    ecosystem: str,
    model: str = DEFAULT_MODEL,
) -> list[str]:
    """
    Ask Gemini which files are relevant to *prompt* and return their paths.

    Parameters
    ----------
    api_key:
        Gemini API key from Google AI Studio.
    prompt:
        The user's natural-language task description.
    file_tree:
        All candidate files as (relative_posix_path, size_bytes) tuples.
        These should already have contextzip's standard exclusions applied —
        no node_modules, no build artifacts, no .env files.
    ecosystem:
        Human-readable detected framework string, e.g. "Next.js + TypeScript".
        Gives the model important context for relevance scoring.
    model:
        Gemini model identifier. Defaults to gemini-2.0-flash-lite.

    Returns
    -------
    list[str]
        Relative POSIX paths of the selected files, ordered by relevance
        (most relevant first). Always a strict subset of the input file_tree
        paths — hallucinated or non-existent paths are silently dropped.

    Raises
    ------
    GeminiUnavailable
        If httpx is not installed.
    GeminiError
        On any API or parsing failure.
    """
    if not _HTTPX_AVAILABLE:
        raise GeminiUnavailable(
            "httpx is required for AI-powered selection. "
            "Install it with: pip install httpx"
        )

    system_prompt = _build_system_prompt()
    user_message = _build_user_message(prompt, file_tree, ecosystem)

    url = _API_URL.format(model=model, key=api_key)

    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": f"{system_prompt}\n\n{user_message}"}],
            }
        ],
        "generationConfig": {
            "temperature": 0.0,  # deterministic — this is a ranking task
            "maxOutputTokens": 512,  # a list of paths needs very few tokens
            "responseMimeType": "application/json",
        },
    }

    try:
        response = httpx.post(url, json=payload, timeout=_TIMEOUT_SECONDS)
    except httpx.TimeoutException:
        raise GeminiError("Request timed out after 30 seconds.")
    except httpx.RequestError as exc:
        raise GeminiError(f"Network error: {exc}")

    if response.status_code == 400:
        raise GeminiError("Invalid request — check your API key format.")
    if response.status_code == 401 or response.status_code == 403:
        raise GeminiError(
            "API key rejected. Run [cyan]contextzip config --reset-key[/] to update it."
        )
    if response.status_code == 429:
        raise GeminiRateLimitError(
            "Rate limit reached on the free tier (15 req/min). "
            "Wait a moment and try again, or your new key may still be activating — "
            "Google can take up to 60 seconds after key creation."
        )
    if response.status_code != 200:
        raise GeminiError(
            f"Gemini API returned HTTP {response.status_code}: {response.text[:200]}"
        )

    return _parse_response(response.json(), file_tree)


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


def _build_system_prompt() -> str:
    return """\
You are a precise file relevance assistant for software projects.

Your only job: given a developer's task description and a project file tree,
return the MINIMUM set of files a developer would need to open to complete
that task. Think like a senior engineer doing a surgical code change —
open only what you must touch or read to understand the change.

Rules you must follow:
- Return ONLY a JSON array of file path strings. No explanation, no markdown,
  no extra keys. Example: ["src/auth.ts", "components/Toast.tsx"]
- Be ruthless about exclusion. If a file is not directly relevant to the
  stated task, leave it out. Err heavily on the side of fewer files.
- Prefer files that will be MODIFIED over files that are merely referenced.
- Config files, test files, and documentation should only appear if the
  task explicitly concerns them.
- Never return more than 10 files. For most tasks 2–5 files is correct.
- Order by relevance: most directly relevant file first.\
"""


def _build_user_message(
    prompt: str,
    file_tree: list[tuple[str, int]],
    ecosystem: str,
) -> str:
    tree_lines = "\n".join(f"{path} ({_human_size(size)})" for path, size in file_tree)
    return f"""\
Framework: {ecosystem}

Task: {prompt}

Project files (exclusions already applied):
{tree_lines}

Return only a JSON array of the most relevant file paths for this task.\
"""


# ---------------------------------------------------------------------------
# Response parsing and validation
# ---------------------------------------------------------------------------


def _parse_response(
    data: dict,
    file_tree: list[tuple[str, int]],
) -> list[str]:
    """
    Extract and validate the file list from the Gemini API response.

    - Parses the JSON array from the model's text output
    - Drops any path the model hallucinated (not in the real file tree)
    - Enforces the _MAX_FILES hard cap
    - Warns (via exception) if the model returned mostly invalid paths
    """
    # Navigate the Gemini response structure
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as exc:
        raise GeminiError(f"Unexpected API response structure: {exc}\n{data}")

    # Strip any accidental markdown fences the model might add
    text = text.strip().strip("`").strip()
    if text.startswith("json"):
        text = text[4:].strip()

    try:
        raw_paths: list = json.loads(text)
    except json.JSONDecodeError as exc:
        raise GeminiError(
            f"Model returned non-JSON output: {exc}\nRaw output: {text[:300]}"
        )

    if not isinstance(raw_paths, list):
        raise GeminiError(
            f"Expected a JSON array, got {type(raw_paths).__name__}: {text[:200]}"
        )

    # Build a set of valid paths for O(1) lookup
    valid_paths: set[str] = {path for path, _ in file_tree}

    validated: list[str] = []
    hallucinated = 0

    for item in raw_paths:
        if not isinstance(item, str):
            continue
        # Normalise separators (model may return backslashes on Windows prompts)
        normalised = item.replace("\\", "/").strip()
        if normalised in valid_paths:
            validated.append(normalised)
        else:
            hallucinated += 1

    # Warn if the model was mostly making things up
    total_returned = len(raw_paths)
    if total_returned > 0 and hallucinated / total_returned > 0.5:
        raise GeminiError(
            f"Model returned {hallucinated}/{total_returned} non-existent paths. "
            "This may indicate a model or prompt issue. Try a more specific prompt."
        )

    # Enforce hard cap
    return validated[:_MAX_FILES]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"
