"""
ai/selector.py — Orchestrates AI-powered file selection for --prompt mode.

Responsibilities:
  1. Build a lean project map from the already-filtered file list
  2. Call the Gemini client (with heuristic fallback on rate limit)
  3. Return the selected subset as Path objects ready for packaging
  4. Generate the prompt.txt content to include in the ZIP
"""

from __future__ import annotations

from pathlib import Path

from contextzip.ai.gemini import select_files, GeminiRateLimitError
from contextzip.filters import ResolveResult

# Sentinels — tell the CLI which selection path was taken
USED_GEMINI = "gemini"
USED_HEURISTIC = "heuristic"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def ai_select(
    *,
    resolved: ResolveResult,
    project_dir: Path,
    prompt: str,
    ecosystem: str,
    api_key: str,
) -> tuple[list[Path], str, str]:
    """
    Select the minimum relevant files for *prompt* from *resolved.included*.

    Returns
    -------
    (selected_paths, prompt_txt, method)
        selected_paths : list[Path] of absolute paths chosen
        prompt_txt     : string content ready to write as prompt.txt in the ZIP
        method         : USED_GEMINI or USED_HEURISTIC
    """
    from contextzip.ai import heuristic as _heuristic

    file_tree = _build_file_tree(resolved.included, project_dir)
    method = USED_GEMINI

    try:
        selected_rel = select_files(
            api_key=api_key,
            prompt=prompt,
            file_tree=file_tree,
            ecosystem=ecosystem,
        )
    except GeminiRateLimitError:
        # Confirmed HTTP 429 only — fall back to keyword heuristic
        selected_rel = _heuristic.select_files(
            prompt=prompt,
            file_tree=file_tree,
        )
        method = USED_HEURISTIC
    # All other GeminiErrors (bad key, network, unexpected status) propagate
    # up to _run_ai_selection in cli.py which displays the error and exits.

    # Map relative path strings back to absolute Path objects
    rel_to_abs: dict[str, Path] = {
        p.relative_to(project_dir).as_posix(): p for p in resolved.included
    }
    selected_paths = [rel_to_abs[rel] for rel in selected_rel if rel in rel_to_abs]

    prompt_txt = _build_prompt_txt(prompt, selected_rel, ecosystem, method)

    return selected_paths, prompt_txt, method


def build_prompt_only_txt(prompt: str, ecosystem: str) -> str:
    """Build a minimal prompt.txt when no AI selection was performed."""
    return _build_prompt_txt(prompt, [], ecosystem, USED_GEMINI)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_file_tree(
    included: list[Path],
    project_dir: Path,
) -> list[tuple[str, int]]:
    """
    Convert the included file list into (relative_posix_path, size_bytes) tuples.

    Binary files and files that can't be stat'd are silently skipped —
    the model can't reason about them anyway.
    """
    tree: list[tuple[str, int]] = []

    for abs_path in included:
        try:
            rel = abs_path.relative_to(project_dir).as_posix()
            size = abs_path.stat().st_size
        except (ValueError, OSError):
            continue

        if _is_binary(abs_path):
            continue

        tree.append((rel, size))

    return tree


def _build_prompt_txt(
    prompt: str,
    selected_rel: list[str],
    ecosystem: str,
    method: str,
) -> str:
    """
    Build the prompt.txt to include inside the ZIP.

    Any AI tool that receives the ZIP immediately sees the task description,
    the framework, and exactly which files were selected and why.
    """

    selector_label = (
        "contextzip AI (Gemini)"
        if method == USED_GEMINI
        else "contextzip (keyword heuristic — Gemini was rate limited)"
    )

    lines: list[str] = [
        f"Task: {prompt}",
        "",
        f"Framework: {ecosystem}",
        "",
    ]

    if selected_rel:
        lines.append(f"Files selected by {selector_label}:")
        for rel in selected_rel:
            lines.append(f"  - {rel}")
    else:
        lines.append("(No files selected)")

    return "\n".join(lines) + "\n"


def _is_binary(path: Path, peek: int = 512) -> bool:
    """Return True if the file appears to be binary (null bytes in first peek bytes)."""
    try:
        with path.open("rb") as fh:
            return b"\x00" in fh.read(peek)
    except OSError:
        return False
