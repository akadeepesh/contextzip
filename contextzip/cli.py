"""
cli.py — contextzip entry point and command routing.
"""

from __future__ import annotations

import os
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel

from contextzip import __version__
from contextzip.config import (
    get_api_key,
    delete_api_key,
    config_path,
    diagnose_api_key,
    save_workspace_location,
    delete_workspace_location,
)
from contextzip.detector import detect
from contextzip.filters import (
    build_spec,
    build_force_include_spec,
    resolve_files,
    resolve_files_from_git,
)
from contextzip.git import get_changed_files, GitError
from contextzip.packager import create_zip
from contextzip.clipboard import handle as clipboard_handle
from contextzip.project_config import (
    load_project_config,
    has_legacy_project_config,
    is_known_ai_provider,
)

from contextzip.cli_display import (
    print_detection,
    print_git_summary,
    print_scan_summary,
    print_file_warnings,
    print_package_result,
    print_zip_write_warnings,
    print_clipboard_result,
)
from contextzip.cli_ai import (
    normalize_pattern,
    run_ai_selection,
    run_ai_selection_preview,
)
from contextzip.cli_onboard import onboard_api_key

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
# Subcommand: watch
# ---------------------------------------------------------------------------


@main.command(
    "watch", context_settings={"ignore_unknown_options": True, "allow_extra_args": True}
)
@click.argument("command", nargs=-1, required=True, metavar="-- COMMAND [ARGS]...")
def cmd_watch(command: tuple[str, ...]) -> None:
    """
    Watch a process and auto-package debug context when errors are detected.

    \b
    EXAMPLES
      contextzip watch -- npm run dev
      contextzip watch -- python manage.py runserver
      contextzip watch -- python -m pytest --tb=short

    \b
    HOW IT WORKS
      contextzip starts your process and buffers all output in the background.
      When an error is detected in the output stream, a prompt appears:

        [D] package debug context   [S] skip

      Pressing D immediately writes .contextzip/output/debug-context.zip containing:
        · prompt.txt          auto-generated task description
        · terminal-error.txt  the cleaned error output
        · source-files.zip    source files referenced in the stack trace

      On Ctrl+C (stopping the process), if no errors were packaged yet,
      you'll be offered one final chance to capture the full session output.

    \b
    NOTES
      · Works best with dev servers that don't read stdin interactively
        (npm run dev, manage.py runserver, cargo watch, etc.)
      · On Windows, color output passthrough may be limited — run in
        Windows Terminal or VS Code integrated terminal for best results.
      · PTY emulation is not used; if your process requires a TTY for
        correct behaviour, run it directly and use 'contextzip capture'
        as a companion command in a second terminal.
    """
    project_dir = Path(os.getcwd()).resolve()

    cmd_list = list(command)
    if cmd_list and cmd_list[0] == "--":
        cmd_list = cmd_list[1:]

    if not cmd_list:
        console.print(
            "[red]No command specified.[/] Usage: contextzip watch -- COMMAND [ARGS]"
        )
        raise SystemExit(1)

    console.print()
    console.print(
        Panel.fit(
            f"[bold cyan]contextzip[/] [dim]watch mode[/]\n"
            f"[dim]Project:[/] [white]{project_dir}[/]",
            border_style="cyan",
            padding=(0, 2),
        )
    )
    console.print()

    with console.status("[cyan]Detecting project ecosystem…[/]", spinner="dots"):
        detection = detect(project_dir)

    print_detection(detection)

    if os.name == "nt":
        console.print(
            "[yellow]  ⚠[/]  [dim]Windows detected — color passthrough may be limited. "
            "PTY mode is not used.[/]\n"
        )

    from contextzip.watcher import run_watch

    exit_code = run_watch(
        command=cmd_list,
        project_dir=project_dir,
        ecosystems=detection.ecosystems,
        ecosystem_display=detection.display_name,
        console=console,
    )

    raise SystemExit(exit_code)


@main.command("config")
@click.option(
    "--reset-key",
    is_flag=True,
    default=False,
    help="Clear the stored Gemini API key and re-run the setup prompt.",
)
@click.option(
    "--set-workspace",
    default=None,
    metavar="LOCATION",
    help='Set your personal default for where .contextzip/ lives: "git-root", '
    '"cwd", or a path.',
)
@click.option(
    "--reset-workspace",
    is_flag=True,
    default=False,
    help="Clear your personal workspace location override.",
)
@click.option(
    "--show-key-path",
    is_flag=True,
    default=False,
    help="Print the path to the config file and exit.",
)
def cmd_config(
    reset_key: bool,
    set_workspace: str | None,
    reset_workspace: bool,
    show_key_path: bool,
) -> None:
    """
    Manage contextzip configuration.

    \b
    EXAMPLES
      contextzip config --reset-key                # clear Gemini key, re-onboard
      contextzip config --set-workspace cwd         # always use ./.contextzip here
      contextzip config --set-workspace git-root    # back to the default
      contextzip config --set-workspace ~/zips      # a fixed custom location
      contextzip config --reset-workspace           # clear the personal override
      contextzip config --show-key-path             # print config file location

    \b
    A workspace location can also be pinned for the whole team by committing
    a .contextzip/config.json file at the project root:
      {"workspace_location": "git-root"}
    Project config (if present) takes priority over this personal setting —
    see the README for the full precedence order.
    """
    if show_key_path:
        console.print(f"\n  [dim]Config file:[/] [cyan]{config_path()}[/]\n")
        return

    if set_workspace is not None or reset_workspace:
        if reset_workspace:
            removed = delete_workspace_location()
            if removed:
                console.print(
                    f"\n  [green]✓[/]  Workspace override removed from "
                    f"[dim]{config_path()}[/]"
                )
            else:
                console.print("\n  [dim]No personal workspace override was set.[/]")
            if set_workspace is None:
                return

        if set_workspace is not None:
            save_workspace_location(set_workspace)
            console.print(
                f"\n  [green]✓[/]  Personal workspace location set to "
                f"[cyan]{set_workspace}[/] in [dim]{config_path()}[/]\n"
                f"  [dim]A project-level .contextzip/config.json, if present, still "
                f"takes priority over this.[/]"
            )
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
        new_key = onboard_api_key()
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

    from contextzip.packager import _resolve_workspace_location
    from contextzip.project_config import (
        load_project_config,
        has_legacy_project_config,
        project_config_path,
    )

    cwd = Path(os.getcwd()).resolve()
    ws_location, ws_source = _resolve_workspace_location(cwd)
    console.print(
        Panel(
            f"  [dim]Workspace location:[/]  [green]{ws_location}[/]  [dim]({ws_source})[/]\n\n"
            "  [dim]Run [cyan]contextzip config --set-workspace <location>[/] to set "
            "a personal default,\n"
            "  or commit a [cyan].contextzip/config.json[/] at the project root to "
            "share one with your team.[/]",
            title="[bold]contextzip config[/]",
            border_style="cyan",
            padding=(0, 2),
        )
    )
    console.print()

    project_cfg = load_project_config(cwd)
    if has_legacy_project_config(cwd):
        console.print(
            Panel(
                "  [yellow]Using the deprecated .contextzip.json.[/]\n\n"
                "  [dim]Move its contents into [cyan].contextzip/config.json[/] — "
                "same fields, new home. See the README for the current schema.[/]",
                title="[bold]contextzip config[/]",
                border_style="yellow",
                padding=(0, 2),
            )
        )
    else:
        always_include = ", ".join(project_cfg.always_include) or "[dim]none[/]"
        always_exclude = ", ".join(project_cfg.always_exclude) or "[dim]none[/]"
        ai = project_cfg.ai
        console.print(
            Panel(
                f"  [dim]Project config:[/]  [cyan]{project_config_path(cwd)}[/]"
                f"{' [dim](not created yet — defaults shown)[/]' if not project_cfg.source_path else ''}\n\n"
                f"  [dim]always_include:[/]  {always_include}\n"
                f"  [dim]always_exclude:[/]  {always_exclude}\n"
                f"  [dim]ai:[/]  enabled=[green]{ai.enabled}[/] "
                f"provider=[green]{ai.provider}[/] max_files=[green]{ai.max_files}[/]",
                title="[bold]contextzip config[/]",
                border_style="cyan",
                padding=(0, 2),
            )
        )
    console.print()


# Core execution logic (shared by main command + all subcommands)
# ---------------------------------------------------------------------------


def _enforce_ai_config(ai_cfg) -> None:
    """
    Validate a project's `ai` preferences before honouring --prompt.

    Exits with a clear message (rather than silently ignoring --prompt or
    silently falling back) if AI selection is disabled, or if the
    configured provider isn't one contextzip currently supports.
    """
    if not ai_cfg.enabled:
        console.print(
            Panel.fit(
                "[yellow]AI-powered selection is disabled for this project.[/]\n"
                "[dim]Set [cyan]\"ai\": {\"enabled\": true}[/] in "
                "[cyan].contextzip/config.json[/] to use --prompt here.[/]",
                border_style="yellow",
                padding=(0, 2),
            )
        )
        raise SystemExit(1)

    if not is_known_ai_provider(ai_cfg.provider):
        console.print(
            Panel.fit(
                f"[red]Unsupported AI provider:[/] [cyan]{ai_cfg.provider}[/]\n"
                "[dim]Only [cyan]\"gemini\"[/] is currently supported — update "
                "[cyan]ai.provider[/] in [cyan].contextzip/config.json[/].[/]",
                border_style="red",
                padding=(0, 2),
            )
        )
        raise SystemExit(1)


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
    All packaging work lives here. The main command and every subcommand
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

    # ── Project config (.contextzip/config.json) ────────────────────────────
    project_cfg = load_project_config(project_dir)
    if has_legacy_project_config(project_dir):
        console.print(
            "[yellow]⚠[/]  [dim]Found the deprecated [/dim][cyan].contextzip.json[/cyan]"
            "[dim] — move its settings into [/dim][cyan].contextzip/config.json[/cyan]"
            "[dim] when you get a chance.[/dim]"
        )
        console.print()

    # ── Detection ────────────────────────────────────────────────────────────
    with console.status("[cyan]Detecting project ecosystem…[/]", spinner="dots"):
        detection = detect(project_dir)

    print_detection(detection)

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

        print_git_summary(git_result, project_dir, verbose)

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

        # CLI --exclude/-e patterns plus any standing always_exclude patterns
        # from project config — both are additive, persistent behavior comes
        # from config.json so it doesn't need to be re-typed every run.
        normalized_exclude = [normalize_pattern(p) for p in extra_exclude or []]
        normalized_exclude += [
            normalize_pattern(p) for p in project_cfg.always_exclude
        ]

        with console.status("[cyan]Building exclusion rules…[/]", spinner="dots"):
            spec = build_spec(
                rule_modules=detection.rule_modules,
                extra_exclude=normalized_exclude if normalized_exclude else None,
                gitignore_path=gitignore_path,
            )
            force_include = build_force_include_spec(
                [normalize_pattern(p) for p in project_cfg.always_include]
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
                force_include=force_include,
            )

    # ── File scan summary + warnings ─────────────────────────────────────────
    print_scan_summary(resolved, project_dir, verbose, git_mode=git_changes)
    print_file_warnings(resolved, project_dir)

    # ── Dry run ──────────────────────────────────────────────────────────────
    if dry_run:
        if prompt:
            _enforce_ai_config(project_cfg.ai)
            diagnosis = diagnose_api_key()
            if diagnosis:
                console.print(f"\n  [yellow]⚠[/]  {diagnosis}\n")
            api_key = get_api_key()
            if not api_key:
                api_key = onboard_api_key()
                if not api_key:
                    raise SystemExit(0)
            run_ai_selection_preview(
                resolved=resolved,
                project_dir=project_dir,
                prompt=prompt,
                ecosystem=detection.display_name,
                api_key=api_key,
                max_files=project_cfg.ai.max_files,
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
        _enforce_ai_config(project_cfg.ai)
        diagnosis = diagnose_api_key()
        if diagnosis:
            console.print(f"\n  [yellow]⚠[/]  {diagnosis}\n")
        api_key = get_api_key()
        if not api_key:
            api_key = onboard_api_key()
            if not api_key:
                raise SystemExit(0)

        selected_paths, prompt_txt = run_ai_selection(
            resolved=resolved,
            project_dir=project_dir,
            prompt=prompt,
            ecosystem=detection.display_name,
            api_key=api_key,
            max_files=project_cfg.ai.max_files,
        )

        if not selected_paths:
            console.print(
                "\n[red]AI selection returned no files.[/] "
                "Try a more specific prompt, or run without [cyan]--prompt[/] "
                "to package the full project."
            )
            raise SystemExit(1)

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

    print_package_result(result)
    print_zip_write_warnings(result)

    # ── Clipboard ────────────────────────────────────────────────────────────
    if not no_clipboard:
        console.print()
        with console.status("[cyan]Preparing clipboard…[/]", spinner="dots"):
            cb = clipboard_handle(result.zip_path)
        print_clipboard_result(cb)
