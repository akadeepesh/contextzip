"""
cli.py — contextzip entry point.  Phases 1–5 complete.

Command surface
───────────────
Main command (unchanged, fully backwards-compatible):
  contextzip [OPTIONS]
    -i / --include PATH      only include files under these paths (repeatable)
    -e / --exclude PATTERN   extra exclusion patterns (repeatable)
    -n / --dry-run           preview without creating ZIP
    -o / --output FILE       custom output path
    --no-clipboard           skip clipboard / folder-open step
    --no-gitignore           ignore project .gitignore
    --git-changes            only package files changed in git
    -v / --verbose           show every file decision

Subcommands (new — all modifier flags available on each):
  contextzip exclude PATTERN… [OPTIONS]
  contextzip include PATH…    [OPTIONS]

Both subcommands accept the same modifier flags as the main command so that
the flags naturally follow the verb, matching the git / docker / cargo UX:
  contextzip exclude CHANGELOG.md --dry-run --verbose
  contextzip include src/ app/    --output ~/Desktop/out.zip
"""

from __future__ import annotations

import os
import webbrowser
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from contextzip import __version__
from contextzip.config import (
    get_api_key,
    save_api_key,
    delete_api_key,
    config_path,
    diagnose_api_key,
)
from contextzip.detector import detect
from contextzip.filters import (
    build_spec,
    resolve_files,
    resolve_files_from_git,
    summarise_exclusions,
    LARGE_FILE_WARN_BYTES,
)
from contextzip.git import get_changed_files, GitError, GitChanges
from contextzip.packager import create_zip
from contextzip.clipboard import handle as clipboard_handle, Tier

console = Console()


# ---------------------------------------------------------------------------
# Shared modifier-flag decorator
# ---------------------------------------------------------------------------
# Defined once so the main command and every subcommand declare exactly the
# same flags without duplicating help strings.


def _modifier_options(f):
    """
    Attach all run-modifier flags to a command.

    Applied to both the main command and every subcommand so that flags
    always follow the verb — matching the git / docker / cargo convention:
      contextzip exclude CHANGELOG.md --dry-run --verbose
    """
    decorators = [
        click.option(
            "--prompt",
            "-p",
            default=None,
            metavar="TEXT",
            help=(
                "Describe your task in natural language. contextzip uses Gemini AI "
                "to select only the files relevant to that task. "
                "Requires a free Gemini API key (you will be guided on first use)."
            ),
        ),
        click.option(
            "--dry-run",
            "-n",
            is_flag=True,
            default=False,
            help="Show what would be included without creating the ZIP.",
        ),
        click.option(
            "--output",
            "-o",
            default=None,
            metavar="FILE",
            help="Output ZIP path. Bypasses .contextzip/ workspace — writes directly to FILE.",
        ),
        click.option(
            "--no-clipboard",
            is_flag=True,
            default=False,
            help="Skip clipboard / folder-open step after creating the ZIP.",
        ),
        click.option(
            "--no-gitignore",
            is_flag=True,
            default=False,
            help="Ignore the project's .gitignore file (use only built-in rules).",
        ),
        click.option(
            "--git-changes",
            is_flag=True,
            default=False,
            help=(
                "Only include files that git reports as modified, added, or untracked. "
                "Requires the project to be inside a git repository."
            ),
        ),
        click.option(
            "--verbose",
            "-v",
            is_flag=True,
            default=False,
            help="Show every included and excluded file.",
        ),
    ]
    for dec in reversed(decorators):
        f = dec(f)
    return f


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------


@click.group(
    invoke_without_command=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.option(
    "--include",
    "-i",
    multiple=True,
    metavar="PATH",
    help=(
        "Only include files under these paths (relative to project root). "
        "Repeatable: --include src --include app  |  or use: contextzip include src app"
    ),
)
@click.option(
    "--exclude",
    "-e",
    multiple=True,
    metavar="PATTERN",
    help=(
        "Extra exclusion patterns on top of auto-rules (gitignore syntax). "
        "Repeatable: -e '*.log' -e CHANGELOG.md  |  or use: contextzip exclude CHANGELOG.md *.log"
    ),
)
@_modifier_options
@click.version_option(version=__version__, prog_name="contextzip")
@click.pass_context
def main(
    ctx: click.Context,
    include: tuple[str, ...],
    exclude: tuple[str, ...],
    prompt: str | None,
    dry_run: bool,
    output: str | None,
    no_clipboard: bool,
    no_gitignore: bool,
    git_changes: bool,
    verbose: bool,
) -> None:
    """
    \b
    contextzip — package your codebase for AI tools.

    Run from your project root to produce a smart, lightweight ZIP
    ready to paste directly into Claude, ChatGPT, or any AI interface.

    \b
    SUBCOMMANDS
      contextzip exclude CHANGELOG.md LICENSE .github/
      contextzip include src/ app/

    Both subcommands accept the same flags as the main command:
      contextzip exclude CHANGELOG.md --dry-run --verbose
    """
    # A subcommand was invoked — let it handle everything.
    if ctx.invoked_subcommand is not None:
        return

    _run(
        extra_exclude=list(exclude),
        include_only=list(include),
        prompt=prompt,
        dry_run=dry_run,
        output=output,
        no_clipboard=no_clipboard,
        no_gitignore=no_gitignore,
        git_changes=git_changes,
        verbose=verbose,
    )


# ---------------------------------------------------------------------------
# Subcommand: exclude
# ---------------------------------------------------------------------------


@main.command("exclude")
@click.argument("patterns", nargs=-1, required=True, metavar="PATTERN…")
@_modifier_options
def cmd_exclude(
    patterns: tuple[str, ...],
    prompt: str | None,
    dry_run: bool,
    output: str | None,
    no_clipboard: bool,
    no_gitignore: bool,
    git_changes: bool,
    verbose: bool,
) -> None:
    """
    Exclude specific files or patterns and package everything else.

    \b
    EXAMPLES
      contextzip exclude CHANGELOG.md CONTRIBUTING.md LICENSE
      contextzip exclude .github/ tests/ '*.log'
      contextzip exclude CHANGELOG.md --dry-run --verbose
      contextzip exclude .github/ CHANGELOG.md --output ~/Desktop/out.zip

    Patterns follow gitignore syntax. Folders are matched with or without
    a trailing slash: both '.github' and '.github/' work.
    """
    _run(
        extra_exclude=list(patterns),
        include_only=None,
        prompt=prompt,
        dry_run=dry_run,
        output=output,
        no_clipboard=no_clipboard,
        no_gitignore=no_gitignore,
        git_changes=git_changes,
        verbose=verbose,
    )


# ---------------------------------------------------------------------------
# Subcommand: include
# ---------------------------------------------------------------------------


@main.command("include")
@click.argument("paths", nargs=-1, required=True, metavar="PATH…")
@_modifier_options
def cmd_include(
    paths: tuple[str, ...],
    prompt: str | None,
    dry_run: bool,
    output: str | None,
    no_clipboard: bool,
    no_gitignore: bool,
    git_changes: bool,
    verbose: bool,
) -> None:
    """
    Package only the specified paths and skip everything else.

    \b
    EXAMPLES
      contextzip include src/ app/
      contextzip include src/ app/ --dry-run
      contextzip include src/ --output ~/Desktop/out.zip --verbose

    Paths are matched as exact prefixes at directory boundaries:
    'src' matches 'src/index.ts' but not 'src2/index.ts'.
    """
    _run(
        extra_exclude=None,
        include_only=list(paths),
        prompt=prompt,
        dry_run=dry_run,
        output=output,
        no_clipboard=no_clipboard,
        no_gitignore=no_gitignore,
        git_changes=git_changes,
        verbose=verbose,
    )


# ---------------------------------------------------------------------------
# Core execution logic (shared by main command + all subcommands)
# ---------------------------------------------------------------------------


def _run(
    *,
    extra_exclude: list[str] | None,
    include_only: list[str] | None,
    prompt: str | None,
    dry_run: bool,
    output: str | None,
    no_clipboard: bool,
    no_gitignore: bool,
    git_changes: bool,
    verbose: bool,
) -> None:
    """
    All actual work lives here.  The main command and every subcommand
    delegate to this function after collecting their arguments/flags,
    keeping the CLI surface thin and the logic testable in isolation.
    """
    project_dir = Path(os.getcwd()).resolve()

    # ── Header ───────────────────────────────────────────────────────────────
    console.print()
    console.print(
        Panel.fit(
            f"[bold cyan]contextzip[/] [dim]v{__version__}[/]\n"
            f"[dim]Project:[/] [white]{project_dir}[/]",
            border_style="cyan",
            padding=(0, 2),
        )
    )
    console.print()

    # ── Detection ────────────────────────────────────────────────────────────
    with console.status("[cyan]Detecting project ecosystem…[/]", spinner="dots"):
        detection = detect(project_dir)

    _print_detection(detection)

    # ── Git-changes mode ─────────────────────────────────────────────────────
    if git_changes:
        with console.status("[cyan]Querying git for changed files…[/]", spinner="dots"):
            git_result = get_changed_files(project_dir)

        if isinstance(git_result, GitError):
            console.print(
                Panel.fit(
                    f"[red]Git error:[/] {git_result.message}",
                    border_style="red",
                    padding=(0, 2),
                )
            )
            raise SystemExit(1)

        _print_git_summary(git_result, project_dir, verbose)

        if git_result.is_empty:
            console.print(
                "\n[yellow]Nothing to package.[/] "
                "No modified, added, or untracked files found — working tree is clean."
            )
            return

        with console.status("[cyan]Checking git-changed files…[/]", spinner="dots"):
            resolved = resolve_files_from_git(
                git_files=git_result.files,
                project_dir=project_dir,
            )

    else:
        # ── Build exclusion spec ─────────────────────────────────────────────
        gitignore_path = None if no_gitignore else (project_dir / ".gitignore")
        used_gitignore = (
            not no_gitignore and gitignore_path is not None and gitignore_path.is_file()
        )

        normalized_exclude = (
            [_normalize_pattern(p) for p in extra_exclude] if extra_exclude else []
        )

        with console.status("[cyan]Building exclusion rules…[/]", spinner="dots"):
            spec = build_spec(
                rule_modules=detection.rule_modules,
                extra_exclude=normalized_exclude if normalized_exclude else None,
                gitignore_path=gitignore_path,
            )

        if used_gitignore:
            console.print("[dim]↳ .gitignore patterns applied[/]")
            console.print()

        # ── Resolve files ─────────────────────────────────────────────────────
        with console.status("[cyan]Scanning project files…[/]", spinner="dots"):
            resolved = resolve_files(
                project_dir=project_dir,
                spec=spec,
                include_only=include_only if include_only else None,
            )

    # ── File scan summary ────────────────────────────────────────────────────
    _print_scan_summary(resolved, project_dir, verbose, git_mode=git_changes)

    # ── Warnings: large files ────────────────────────────────────────────────
    if resolved.large_files:
        console.print()
        console.print(
            f"  [yellow]⚠[/]  [bold]{len(resolved.large_files)} large file"
            f"{'s' if len(resolved.large_files) != 1 else ''}[/] "
            f"[dim](≥ {_human_size(LARGE_FILE_WARN_BYTES)}) will be included:[/]"
        )
        for p, size in resolved.large_files[:5]:
            rel = p.relative_to(project_dir).as_posix()
            console.print(
                f"    [yellow]·[/] [dim]{rel}[/]  [yellow]{_human_size(size)}[/]"
            )
        if len(resolved.large_files) > 5:
            console.print(f"    [dim]… and {len(resolved.large_files) - 5} more[/]")
        console.print(
            "  [dim]  Use [cyan]-e PATTERN[/] or [cyan]contextzip exclude PATTERN[/] "
            "to drop them if unneeded.[/]"
        )

    # ── Warnings: binary files ───────────────────────────────────────────────
    if resolved.binary_files:
        console.print()
        console.print(
            f"  [yellow]⚠[/]  [bold]{len(resolved.binary_files)} binary file"
            f"{'s' if len(resolved.binary_files) != 1 else ''}[/] "
            f"[dim]detected — AI tools may not read them:[/]"
        )
        for p in resolved.binary_files[:3]:
            rel = p.relative_to(project_dir).as_posix()
            console.print(f"    [yellow]·[/] [dim]{rel}[/]")
        if len(resolved.binary_files) > 3:
            console.print(f"    [dim]… and {len(resolved.binary_files) - 3} more[/]")

    # ── Warnings: skipped files (symlinks, unreadable) ───────────────────────
    if resolved.skipped:
        console.print()
        console.print(
            f"  [red]⚠[/]  [bold]{len(resolved.skipped)} file"
            f"{'s' if len(resolved.skipped) != 1 else ''}[/] "
            f"[dim]skipped (unreadable or dangling symlink):[/]"
        )
        for p, reason in resolved.skipped[:3]:
            console.print(f"    [red]·[/] [dim]{p.name}[/] — {reason}")

    # ── Dry run ──────────────────────────────────────────────────────────────
    if dry_run:
        # If --prompt is set, still run AI selection so the user can preview
        # which files would be chosen — but don't create the ZIP.
        if prompt:
            diagnosis = diagnose_api_key()
            if diagnosis:
                console.print(f"\n  [yellow]⚠[/]  {diagnosis}\n")
            api_key = get_api_key()
            if not api_key:
                api_key = _onboard_api_key()
                if not api_key:
                    raise SystemExit(0)
            _run_ai_selection_preview(
                resolved=resolved,
                project_dir=project_dir,
                prompt=prompt,
                ecosystem=detection.display_name,
                api_key=api_key,
            )
        else:
            console.print()
            console.print(
                Panel.fit(
                    "[yellow]Dry run — no ZIP created.[/]\n"
                    "[dim]Remove --dry-run to produce the archive.[/]",
                    border_style="yellow",
                    padding=(0, 2),
                )
            )
        return

    if not resolved.included:
        console.print(
            "\n[red]Nothing to package.[/] All files were excluded — "
            "try [cyan]contextzip include PATH[/] or [cyan]-i PATH[/] to override."
        )
        return

    # ── AI-powered file selection (--prompt mode) ────────────────────────────
    prompt_txt: str | None = None

    if prompt:
        diagnosis = diagnose_api_key()
        if diagnosis:
            console.print(f"\n  [yellow]⚠[/]  {diagnosis}\n")
        api_key = get_api_key()
        if not api_key:
            api_key = _onboard_api_key()
            if not api_key:
                raise SystemExit(0)

        selected_paths, prompt_txt = _run_ai_selection(
            resolved=resolved,
            project_dir=project_dir,
            prompt=prompt,
            ecosystem=detection.display_name,
            api_key=api_key,
        )

        if not selected_paths:
            console.print(
                "\n[red]AI selection returned no files.[/] "
                "Try a more specific prompt, or run without [cyan]--prompt[/] "
                "to package the full project."
            )
            raise SystemExit(1)

        # Replace the resolved file list with only the AI-selected files
        resolved.included = selected_paths

    # ── Create ZIP ───────────────────────────────────────────────────────────
    console.print()
    output_path = Path(output).resolve() if output else None

    try:
        result = create_zip(
            resolve_result=resolved,
            project_dir=project_dir,
            output_path=output_path,
            console=console,
            git_changes=git_changes,
            prompt_txt=prompt_txt,
        )
    except Exception as exc:
        console.print(f"\n[red]Failed to create ZIP:[/] {exc}")
        raise SystemExit(1)

    _print_package_result(result)

    # ── Skipped during ZIP write ─────────────────────────────────────────────
    if result.skipped_in_zip:
        console.print()
        console.print(
            f"  [red]⚠[/]  [bold]{len(result.skipped_in_zip)} file"
            f"{'s' if len(result.skipped_in_zip) != 1 else ''}[/] "
            f"[dim]could not be written to ZIP:[/]"
        )
        for p, reason in result.skipped_in_zip[:3]:
            console.print(f"    [red]·[/] [dim]{p.name}[/] — {reason}")

    # ── Clipboard ────────────────────────────────────────────────────────────
    if not no_clipboard:
        console.print()
        with console.status("[cyan]Preparing clipboard…[/]", spinner="dots"):
            cb = clipboard_handle(result.zip_path)
        _print_clipboard_result(cb)


# ---------------------------------------------------------------------------
# API key onboarding
# ---------------------------------------------------------------------------

_GEMINI_KEY_URL = "https://aistudio.google.com/apikey"


def _open_browser_silent(url: str) -> None:
    """
    Open *url* in the default browser, suppressing all stderr output.

    On Linux, xdg-open frequently emits KDE/DBus/MIME warnings to stderr
    that are completely unrelated to contextzip and appear right on top of
    the API key prompt, confusing users. We redirect both stdout and stderr
    to /dev/null to keep the terminal clean.
    """
    import subprocess

    try:
        subprocess.Popen(
            ["xdg-open", url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    except (FileNotFoundError, OSError):
        pass
    # macOS / Windows fallback
    try:
        webbrowser.open(url)
    except Exception:
        pass


def _onboard_api_key() -> str | None:
    """
    Guide the user through getting and saving a Gemini API key.

    Returns the key string if successfully configured, or None if the
    user declined or provided nothing.
    """
    console.print()
    console.print(
        Panel(
            "  [bold]--prompt[/] requires a free Gemini API key.\n\n"
            "  contextzip uses [bold cyan]Google AI Studio[/] — "
            "no credit card needed.\n\n"
            f"  [dim]Get your free key at:[/]\n"
            f"  [bold cyan link={_GEMINI_KEY_URL}]{_GEMINI_KEY_URL}[/]",
            title="[bold yellow]Gemini API Key Required[/]",
            border_style="yellow",
            padding=(0, 2),
        )
    )
    console.print()

    open_browser = click.confirm(
        "  Open Google AI Studio in your browser now?",
        default=True,
    )
    if open_browser:
        _open_browser_silent(_GEMINI_KEY_URL)
        console.print("  [dim]Browser opened. Generate a key, then come back here.[/]")

    console.print()
    console.print("  [dim]─────────────────────────────────────────────[/]")
    console.print("  Once you have your key, paste it below and press [bold]Enter[/].")
    console.print("  [dim]─────────────────────────────────────────────[/]")
    console.print()
    key = click.prompt(
        "  Paste your API key",
        hide_input=True,
        prompt_suffix=" › ",
    ).strip()

    if not key:
        console.print("\n  [red]No key provided. Exiting.[/]")
        return None

    # Basic sanity check — Gemini keys start with "AIza"
    if not key.startswith("AIza"):
        console.print(
            "\n  [yellow]⚠[/]  That doesn't look like a Gemini key "
            "(expected it to start with [cyan]AIza[/]).\n"
            "  [dim]Double-check you copied the full key from AI Studio.[/]"
        )
        if not click.confirm("\n  Save it anyway?", default=False):
            return None

    try:
        save_api_key(key)
        console.print(
            f"\n  [green]✓[/]  Key saved to "
            f"[dim]{config_path()}[/]\n"
            f"  [dim]You won't be asked again. "
            f"Run [cyan]contextzip config --reset-key[/] to change it.[/]"
        )
    except OSError as exc:
        console.print(
            f"\n  [yellow]⚠[/]  Could not save key to disk ([dim]{exc}[/]).\n"
            f"  [dim]Set [cyan]GEMINI_API_KEY[/] as an environment variable "
            f"to avoid this prompt next time.[/]"
        )

    console.print()
    return key


# ---------------------------------------------------------------------------
# AI selection helpers
# ---------------------------------------------------------------------------


def _run_ai_selection(
    *,
    resolved,
    project_dir: Path,
    prompt: str,
    ecosystem: str,
    api_key: str,
) -> tuple[list[Path], str]:
    """
    Call the AI selector and return (selected_paths, prompt_txt).
    Handles display of progress, heuristic fallback warning, and errors.
    Raises SystemExit on hard failure.
    """
    from contextzip.ai.selector import ai_select, USED_HEURISTIC
    from contextzip.ai.gemini import GeminiError

    console.print()
    with console.status(
        "[cyan]Asking Gemini to select relevant files…[/]", spinner="dots"
    ):
        try:
            selected_paths, prompt_txt, method = ai_select(
                resolved=resolved,
                project_dir=project_dir,
                prompt=prompt,
                ecosystem=ecosystem,
                api_key=api_key,
            )
        except GeminiError as exc:
            console.print(
                Panel.fit(
                    f"[red]Gemini error:[/] {exc}",
                    border_style="red",
                    padding=(0, 2),
                )
            )
            raise SystemExit(1)

    if method == USED_HEURISTIC:
        console.print(
            "  [yellow]⚠[/]  [dim]Gemini rate limited — used keyword heuristic instead. "
            "Results may be less precise. Try again in a moment for AI selection.[/]"
        )

    _print_ai_selection(selected_paths, project_dir, prompt)
    return selected_paths, prompt_txt


def _run_ai_selection_preview(
    *,
    resolved,
    project_dir: Path,
    prompt: str,
    ecosystem: str,
    api_key: str,
) -> None:
    """
    Run AI selection and show the result without creating a ZIP.
    Used when --dry-run and --prompt are combined.
    """
    from contextzip.ai.selector import ai_select, USED_HEURISTIC
    from contextzip.ai.gemini import GeminiError

    console.print()
    with console.status(
        "[cyan]Asking Gemini to select relevant files…[/]", spinner="dots"
    ):
        try:
            selected_paths, _, method = ai_select(
                resolved=resolved,
                project_dir=project_dir,
                prompt=prompt,
                ecosystem=ecosystem,
                api_key=api_key,
            )
        except GeminiError as exc:
            console.print(
                Panel.fit(
                    f"[red]Gemini error:[/] {exc}",
                    border_style="red",
                    padding=(0, 2),
                )
            )
            raise SystemExit(1)

    if method == USED_HEURISTIC:
        console.print(
            "  [yellow]⚠[/]  [dim]Gemini rate limited — used keyword heuristic instead. "
            "Results may be less precise. Try again in a moment for AI selection.[/]"
        )

    _print_ai_selection(selected_paths, project_dir, prompt)
    console.print()
    console.print(
        Panel.fit(
            "[yellow]Dry run — no ZIP created.[/]\n"
            "[dim]Remove --dry-run to package these files.[/]",
            border_style="yellow",
            padding=(0, 2),
        )
    )


# ---------------------------------------------------------------------------
# Subcommand: config
# ---------------------------------------------------------------------------


@main.command("config")
@click.option(
    "--reset-key",
    is_flag=True,
    default=False,
    help="Clear the stored Gemini API key and re-run the setup prompt.",
)
@click.option(
    "--show-key-path",
    is_flag=True,
    default=False,
    help="Print the path to the config file and exit.",
)
def cmd_config(reset_key: bool, show_key_path: bool) -> None:
    """
    Manage contextzip configuration.

    \b
    EXAMPLES
      contextzip config --reset-key       # clear stored API key, re-onboard
      contextzip config --show-key-path   # print config file location
    """
    if show_key_path:
        console.print(f"\n  [dim]Config file:[/] [cyan]{config_path()}[/]\n")
        return

    if reset_key:
        removed = delete_api_key()
        if removed:
            console.print(
                f"\n  [green]✓[/]  API key removed from [dim]{config_path()}[/]"
            )
        else:
            console.print("\n  [dim]No API key was stored.[/]")

        console.print()
        new_key = _onboard_api_key()
        if not new_key:
            console.print(
                "  [dim]You can set [cyan]GEMINI_API_KEY[/] as an environment "
                "variable instead.[/]\n"
            )
        return

    # Default: show current config status
    key = get_api_key()
    from_env = bool(os.environ.get("GEMINI_API_KEY", "").strip())

    if key:
        masked = key[:8] + "…" + key[-4:] if len(key) > 12 else "****"
        source = (
            "[dim](from environment variable)[/]"
            if from_env
            else f"[dim]({config_path()})[/]"
        )
        console.print(
            Panel(
                f"  [dim]Gemini API key:[/]  [green]{masked}[/]  {source}\n\n"
                "  [dim]Run [cyan]contextzip config --reset-key[/] to change it.[/]",
                title="[bold]contextzip config[/]",
                border_style="cyan",
                padding=(0, 2),
            )
        )
    else:
        console.print(
            Panel(
                "  No Gemini API key configured.\n\n"
                '  Run [cyan]contextzip --prompt "your task"[/] to set one up,\n'
                "  or [cyan]contextzip config --reset-key[/] to go through setup now.",
                title="[bold]contextzip config[/]",
                border_style="yellow",
                padding=(0, 2),
            )
        )
    console.print()


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def _normalize_pattern(p: str) -> str:
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
    # 1. Strip leading ./ or .\
    if p.startswith("./") or p.startswith(".\\"):
        p = p[2:]
    # 2. Normalise path separators
    p = p.replace("\\", "/")
    # 3. folder/* → folder/
    if p.endswith("/*"):
        p = p[:-1]
    return p


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


def _print_detection(detection) -> None:
    if detection.is_unknown:
        ecosystem_line = "[yellow]Unknown[/] — applying base rules only"
    else:
        colours = {
            "Next.js": "bright_blue",
            "Node.js": "green",
            "Python": "yellow",
            "Django": "green",
            "FastAPI": "cyan",
            "Rust": "red",
            "Go": "cyan",
            "Ruby": "red",
        }
        parts = [f"[{colours.get(n, 'white')}]{n}[/]" for n in detection.ecosystems]
        ecosystem_line = " [dim]+[/] ".join(parts)

    conf_colour = {"high": "green", "medium": "yellow", "low": "dim"}.get(
        detection.confidence, "dim"
    )
    console.print(
        Panel(
            f"  [dim]Ecosystem :[/]   {ecosystem_line}\n"
            f"  [dim]Confidence:[/]   [{conf_colour}]{detection.confidence}[/]\n"
            f"  [dim]Rules     :[/]   [dim]{', '.join(detection.rule_modules)}[/]",
            title="[bold]Detection[/]",
            border_style="blue",
            padding=(0, 1),
        )
    )
    console.print()


def _print_git_summary(changes: GitChanges, project_dir: Path, verbose: bool) -> None:
    """Print a panel summarising the git-changed files."""
    table = Table(box=box.ROUNDED, show_header=False, padding=(0, 2))
    table.add_column(style="dim")
    table.add_column()
    table.add_row("Staged", f"[green]{len(changes.staged)}[/]")
    table.add_row("Unstaged", f"[yellow]{len(changes.unstaged)}[/]")
    table.add_row("Untracked", f"[cyan]{len(changes.untracked)}[/]")
    if changes.deleted:
        table.add_row("Deleted (skipped)", f"[dim]{len(changes.deleted)}[/]")
    if changes.submodules:
        table.add_row("Submodules (skipped)", f"[dim]{len(changes.submodules)}[/]")
    table.add_row("To be included", f"[bold green]{len(changes.files)}[/]")

    console.print(
        Panel(
            table,
            title="[bold]Git Changes[/]",
            border_style="magenta",
            padding=(0, 1),
        )
    )
    console.print()

    if verbose and changes.files:
        console.print("[bold]Git-changed files:[/]")
        for category, paths, colour in (
            ("Staged", changes.staged, "green"),
            ("Unstaged", changes.unstaged, "yellow"),
            ("Untracked", changes.untracked, "cyan"),
        ):
            for rel in paths:
                console.print(f"  [{colour}]✓[/] [dim]{rel}[/]  [dim]({category})[/]")
        console.print()


def _print_scan_summary(
    resolved,
    project_dir: Path,
    verbose: bool,
    *,
    git_mode: bool = False,
) -> None:
    total = len(resolved.included) + len(resolved.excluded)
    included_size = sum(p.stat().st_size for p in resolved.included if p.exists())

    table = Table(box=box.ROUNDED, show_header=False, padding=(0, 2))
    table.add_column(style="dim")
    table.add_column()

    if not git_mode:
        table.add_row("Files scanned", str(total + len(resolved.skipped)))
    table.add_row(
        "To be included",
        f"[green]{len(resolved.included)}[/]  [dim]({_human_size(included_size)})[/]",
    )
    if not git_mode:
        table.add_row("Excluded", f"[red]{len(resolved.excluded)}[/]")
    if resolved.skipped:
        table.add_row("Skipped", f"[yellow]{len(resolved.skipped)}[/]")
    console.print(table)

    if verbose and resolved.included:
        console.print()
        console.print("[bold]Included files:[/]")
        for p in resolved.included:
            size_str = _human_size(p.stat().st_size) if p.exists() else "?"
            rel = p.relative_to(project_dir).as_posix()
            console.print(f"  [green]✓[/] {rel}  [dim]{size_str}[/]")

    if not git_mode and resolved.excluded:
        console.print()
        buckets = summarise_exclusions(resolved.excluded, project_dir)
        console.print("[bold]Top excluded directories / files:[/]")
        for label, count in list(buckets.items())[:8]:
            console.print(
                f"  [red]✗[/] [dim]{label}[/]  "
                f"[dim]({count} file{'s' if count != 1 else ''})[/]"
            )


def _print_package_result(result) -> None:
    ratio_colour = "green" if result.compression_ratio >= 0.3 else "yellow"
    size_detail = (
        "[dim](ZIP overhead on tiny project)[/]"
        if result.grew
        else f"[{ratio_colour}](↓ {result.compression_pct} smaller)[/]"
    )

    table = Table(box=box.ROUNDED, show_header=False, padding=(0, 2))
    table.add_column(style="dim", min_width=18)
    table.add_column()
    table.add_row("Files packed", f"[green]{result.file_count}[/]")
    table.add_row("Original size", _human_size(result.uncompressed_bytes))
    table.add_row(
        "Compressed size",
        f"[bold]{_human_size(result.compressed_bytes)}[/]  {size_detail}",
    )
    table.add_row("Saved to", f"[cyan]{result.zip_path}[/]")

    console.print(
        Panel(
            table,
            title="[bold green]✓ ZIP created[/]",
            border_style="green",
            padding=(0, 1),
        )
    )


def _print_ai_selection(
    selected_paths: list[Path],
    project_dir: Path,
    prompt: str,
) -> None:
    """Display a panel showing the files Gemini selected."""
    table = Table(box=box.ROUNDED, show_header=False, padding=(0, 2))
    table.add_column(style="cyan")
    table.add_column(style="dim")

    for p in selected_paths:
        try:
            rel = p.relative_to(project_dir).as_posix()
            size = _human_size(p.stat().st_size)
        except (ValueError, OSError):
            rel = str(p)
            size = "?"
        table.add_row(rel, size)

    console.print(
        Panel(
            table,
            title=f'[bold cyan]AI Selected — [dim]"{prompt}"[/][/]',
            border_style="cyan",
            padding=(0, 1),
        )
    )
    console.print(
        f"  [dim]↳ {len(selected_paths)} file"
        f"{'s' if len(selected_paths) != 1 else ''} selected by Gemini "
        f"· prompt.txt will be included in the ZIP[/]"
    )


def _print_clipboard_result(cb) -> None:
    tier_style = {
        Tier.FILE_ON_CLIPBOARD: ("green", "✓ Ready to paste"),
        Tier.FOLDER_OPENED: ("yellow", "✓ Folder opened"),
        Tier.PATH_ONLY: ("dim", "↳ Manual copy needed"),
    }
    border, title = tier_style.get(cb.tier, ("dim", "Clipboard"))
    console.print(
        Panel.fit(
            cb.message,
            title=f"[bold {border}]{title}[/]",
            border_style=border,
            padding=(0, 2),
        )
    )


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
