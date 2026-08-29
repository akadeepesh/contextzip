"""
cli_ai.py — AI-powered file selection helpers for the contextzip CLI.

Wraps the ai.selector layer with progress display, heuristic fallback
warnings, error handling, and dry-run preview rendering. All functions
raise SystemExit on hard failure so cli.py's _run() stays clean.

Imported by cli.py; nothing here imports from cli.py (no circular deps).
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console

from contextzip.cli_display import print_ai_selection, err, warn, info

console = Console()


# ---------------------------------------------------------------------------
# Pattern normalisation
# ---------------------------------------------------------------------------


def normalize_pattern(p: str) -> str:
    """
    Canonicalise a user-supplied exclusion pattern.

    Rules applied in order:
      1. Strip a leading ``./`` or ``.\\`` so that ``./CHANGELOG.md``
         and ``CHANGELOG.md`` are treated identically.
      2. Replace every backslash with a forward slash for cross-platform
         consistency (Windows paths entered on the CLI).
      3. Collapse ``folder/*`` → ``folder/`` so that gitignore-style
         directory globs work as expected.
    """
    # 1. Strip leading ./ or .\\
    if p.startswith("./") or p.startswith(".\\"):
        p = p[2:]
    # 2. Normalise path separators
    p = p.replace("\\", "/")
    # 3. folder/* → folder/
    if p.endswith("/*"):
        p = p[:-1]
    return p


# ---------------------------------------------------------------------------
# AI selection (full run — produces ZIP)
# ---------------------------------------------------------------------------


def run_ai_selection(
    *,
    resolved,
    project_dir: Path,
    prompt: str,
    ecosystem: str,
    api_key: str,
    max_files: int | None = None,
    prompt_template: str = "",
    con: Console = console,
) -> tuple[list[Path], str]:
    """
    Call the AI selector and return (selected_paths, prompt_txt).

    *max_files* caps how many files the selector may return — typically a
    project's `ai.max_files` preference (.contextzip/config.json). None
    falls back to each backend's own built-in default.

    *prompt_template*, if non-empty, is a project's `ai.prompt_template`
    preference, prepended to the generated prompt.txt.

    Handles progress display, heuristic-fallback warning, and Gemini
    errors. Raises SystemExit(1) on hard failure.
    """
    from contextzip.ai.selector import ai_select, USED_HEURISTIC
    from contextzip.ai.gemini import GeminiError

    with con.status("[cyan]Asking Gemini to select relevant files…[/]", spinner="dots"):
        try:
            selected_paths, prompt_txt, method = ai_select(
                resolved=resolved,
                project_dir=project_dir,
                prompt=prompt,
                ecosystem=ecosystem,
                api_key=api_key,
                max_files=max_files,
                prompt_template=prompt_template,
            )
        except GeminiError as exc:
            err(f"Gemini error: {exc}")
            raise SystemExit(1)

    if method == USED_HEURISTIC:
        warn("Gemini rate limited — used keyword heuristic instead (less precise)")

    print_ai_selection(selected_paths, project_dir, prompt, con=con)
    return selected_paths, prompt_txt


# ---------------------------------------------------------------------------
# AI selection preview (dry-run — no ZIP)
# ---------------------------------------------------------------------------


def run_ai_selection_preview(
    *,
    resolved,
    project_dir: Path,
    prompt: str,
    ecosystem: str,
    api_key: str,
    max_files: int | None = None,
    con: Console = console,
) -> None:
    """
    Run AI selection and display the result without creating a ZIP.
    Used when --dry-run and --prompt are combined.
    """
    from contextzip.ai.selector import ai_select, USED_HEURISTIC
    from contextzip.ai.gemini import GeminiError

    with con.status("[cyan]Asking Gemini to select relevant files…[/]", spinner="dots"):
        try:
            selected_paths, _, method = ai_select(
                resolved=resolved,
                project_dir=project_dir,
                prompt=prompt,
                ecosystem=ecosystem,
                api_key=api_key,
                max_files=max_files,
            )
        except GeminiError as exc:
            err(f"Gemini error: {exc}")
            raise SystemExit(1)

    if method == USED_HEURISTIC:
        warn("Gemini rate limited — used keyword heuristic instead (less precise)")

    print_ai_selection(selected_paths, project_dir, prompt, con=con)
    info("Dry run — no ZIP created. Remove --dry-run to package these files.")
