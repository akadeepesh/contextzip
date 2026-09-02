"""
cli.py — contextzip entry point and command routing.
"""

from __future__ import annotations

import os
from pathlib import Path

import click
from rich.console import Console

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
    ok,
    warn,
    err,
    info,
    print_detection,
    print_git_deleted_and_submodules,
    git_scan_label_and_detail,
    print_git_verbose_files,
    scan_label_and_detail,
    print_scan_summary,
    print_scan_and_pack,
    print_file_warnings,
    print_package_result,
    print_zip_write_warnings,
    print_report_hint,
    print_clipboard_result,
    print_apply_plan,
    print_apply_result,
    print_auto_cleanup,
    print_redaction_summary,
)
from contextzip.report import write_scan_report, write_apply_report
from contextzip.applier import (
    ApplyError,
    MultipleZipsFoundError,
    build_plan,
    discard_plan,
    execute_plan,
    find_latest_manifest,
    find_zip_to_apply,
)
from contextzip.cli_ai import (
    normalize_pattern,
    run_ai_selection,
    run_ai_selection_preview,
)
from contextzip.cli_onboard import onboard_api_key
from contextzip import cleanup as cleanup_mod

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
# Subcommand: apply-zip
# ---------------------------------------------------------------------------


@main.command("apply-zip")
@click.argument("zip_path", required=False, metavar="[ZIP]")
@click.option(
    "--manifest",
    "manifest_path",
    default=None,
    metavar="PATH",
    help=(
        "Manifest to diff against, instead of auto-detecting the most "
        "recently created one in .contextzip/output/."
    ),
)
@click.option(
    "--dry-run",
    "-n",
    is_flag=True,
    default=False,
    help="Preview what would change without writing anything.",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Show every file and its status, not just the summary.",
)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    default=False,
    help="Skip the confirmation prompt, even for risky changes.",
)
def cmd_apply_zip(
    zip_path: str | None,
    manifest_path: str | None,
    dry_run: bool,
    verbose: bool,
    yes: bool,
) -> None:
    """
    Apply an AI-returned ZIP back into the project.

    \b
    EXAMPLES
      contextzip apply-zip                  # auto-detect from .contextzip/inbox/
      contextzip apply-zip fix.zip          # explicit path overrides the inbox
      contextzip apply-zip --dry-run --verbose
      contextzip apply-zip -y               # skip confirmation, even if risky

    \b
    HOW IT WORKS
      Every zip contextzip creates gets a local manifest — a hash of each
      included file — written next to it in .contextzip/output/. Never
      inside the zip itself, so it's never uploaded and never visible to
      whatever AI tool the zip is pasted into.

      apply-zip diffs the returned zip against that manifest to classify
      every file as new, modified, unchanged, or one needing a closer look
      (edited locally since zipping, or with no baseline at all) before
      writing anything. Only adds and modifies files — nothing is ever
      deleted. Every overwritten file is backed up first, under
      .contextzip/backups/<timestamp>/.
    """
    project_dir = Path(os.getcwd()).resolve()
    project_cfg = load_project_config(project_dir)

    console.print()

    try:
        resolved_zip = find_zip_to_apply(project_dir, zip_path)
    except MultipleZipsFoundError as exc:
        err(str(exc))
        raise SystemExit(1)
    except ApplyError as exc:
        err(str(exc))
        raise SystemExit(1)

    manifest = find_latest_manifest(project_dir, manifest_path)

    try:
        plan = build_plan(resolved_zip, project_dir, manifest)
    except ApplyError as exc:
        err(str(exc))
        raise SystemExit(1)

    print_apply_plan(plan, project_dir, verbose=verbose)
    report_path = write_apply_report(project_dir=project_dir, plan=plan)

    if not plan.writable_entries:
        info("Nothing to apply — every file already matches the project.")
        discard_plan(plan)
        return

    if dry_run:
        info("Dry run — no files written. Remove --dry-run to apply these changes.")
        print_report_hint(report_path)
        discard_plan(plan)
        return

    if plan.is_risky and not yes:
        prompt = (
            "  This zip's structure doesn't look right — apply anyway?"
            if plan.structure_warning
            else "  Some files above need a closer look — apply anyway?"
        )
        proceed = click.confirm(prompt, default=False)
        if not proceed:
            info("Cancelled — no files written.")
            discard_plan(plan)
            return

    result = execute_plan(plan, project_dir, retain=project_cfg.applied_zip_retention)
    print_apply_result(result)
    report_path = write_apply_report(project_dir=project_dir, plan=plan, result=result)
    print_report_hint(report_path)

    # ── Auto-cleanup ─────────────────────────────────────────────────────────
    _auto_cleanup(project_dir, project_cfg)


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

      Pressing D immediately writes .contextzip/output/watch/debug-context.zip containing:
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
        err("No command specified. Usage: contextzip watch -- COMMAND [ARGS]")
        raise SystemExit(1)

    console.print()

    with console.status("[cyan]Detecting project ecosystem…[/]", spinner="dots"):
        detection = detect(project_dir)

    print_detection(detection)

    if os.name == "nt":
        warn("Windows detected — color passthrough may be limited (no PTY mode)")

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
@click.option(
    "--ui",
    "launch_ui",
    is_flag=True,
    default=False,
    help="Open a local browser UI to set include/exclude preferences visually "
    "and write .contextzip/config.json.",
)
def cmd_config(
    reset_key: bool,
    set_workspace: str | None,
    reset_workspace: bool,
    show_key_path: bool,
    launch_ui: bool,
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
      contextzip config --ui                        # visually set include/exclude

    \b
    A workspace location can also be pinned for the whole team by committing
    a .contextzip/config.json file at the project root:
      {"workspace_location": "git-root"}
    Project config (if present) takes priority over this personal setting —
    see the README for the full precedence order.
    """
    if launch_ui:
        from contextzip.detector import detect as _detect
        from contextzip.webui.server import launch_config_ui

        project_dir = Path(os.getcwd()).resolve()
        with console.status("[cyan]Detecting project ecosystem…[/]", spinner="dots"):
            detection = _detect(project_dir)
        launch_config_ui(project_dir, detection, con=console)
        return

    if show_key_path:
        console.print()
        info(f"Config file: {config_path()}")
        console.print()
        return

    if set_workspace is not None or reset_workspace:
        console.print()
        if reset_workspace:
            removed = delete_workspace_location()
            if removed:
                ok("Workspace override removed", str(config_path()))
            else:
                info("No personal workspace override was set.")
            if set_workspace is None:
                console.print()
                return

        if set_workspace is not None:
            save_workspace_location(set_workspace)
            ok(f"Workspace location set to {set_workspace}", str(config_path()))
            info(
                "A project-level .contextzip/config.json, if present, still "
                "takes priority over this."
            )
        console.print()
        return

    if reset_key:
        console.print()
        removed = delete_api_key()
        if removed:
            ok("API key removed", str(config_path()))
        else:
            info("No API key was stored.")

        console.print()
        new_key = onboard_api_key()
        if not new_key:
            info("You can set GEMINI_API_KEY as an environment variable instead.")
            console.print()
        return

    # Default: show current config status
    console.print()
    key = get_api_key()
    from_env = bool(os.environ.get("GEMINI_API_KEY", "").strip())

    if key:
        masked = key[:8] + "…" + key[-4:] if len(key) > 12 else "****"
        source = "environment variable" if from_env else str(config_path())
        ok("Gemini API key configured", f"{masked} · {source}")
    else:
        warn("No Gemini API key configured")
        info('Run contextzip --prompt "your task" to set one up.')

    from contextzip.packager import _resolve_workspace_location
    from contextzip.project_config import (
        load_project_config,
        has_legacy_project_config,
        project_config_path,
    )

    cwd = Path(os.getcwd()).resolve()
    ws_location, ws_source = _resolve_workspace_location(cwd)
    ok(f"Workspace: {ws_location}", ws_source)

    project_cfg = load_project_config(cwd)
    if has_legacy_project_config(cwd):
        warn(
            "Using the deprecated .contextzip.json — move it to .contextzip/config.json"
        )
    else:
        always_include = ", ".join(project_cfg.always_include) or "none"
        always_exclude = ", ".join(project_cfg.always_exclude) or "none"
        ai = project_cfg.ai
        cfg_note = (
            " (not created yet — defaults shown)" if not project_cfg.source_path else ""
        )
        ok(f"Project config: {project_config_path(cwd)}{cfg_note}")
        info(f"always_include: {always_include}")
        info(f"always_exclude: {always_exclude}")
        info(
            f"ai: enabled={ai.enabled} provider={ai.provider} max_files={ai.max_files}"
        )
        limits = project_cfg.limits
        info(
            f"limits: max_file_size_mb={limits.max_file_size_mb} "
            f"redact_secrets={limits.redact_secrets}"
        )
        cleanup_cfg = project_cfg.cleanup
        info(
            f"cleanup: enabled={cleanup_cfg.enabled} "
            f"keep_recent={cleanup_cfg.keep_recent}"
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
        err("AI-powered selection is disabled for this project.")
        info('Set "ai": {"enabled": true} in .contextzip/config.json to use --prompt.')
        raise SystemExit(1)

    if not is_known_ai_provider(ai_cfg.provider):
        err(f"Unsupported AI provider: {ai_cfg.provider}")
        info(
            'Only "gemini" is currently supported — update ai.provider in .contextzip/config.json.'
        )
        raise SystemExit(1)


def _maybe_offer_config_ui(
    project_dir: Path,
    detection,
    *,
    prompt: str | None,
    output: str | None,
) -> bool:
    """
    On a project's very first run — no .contextzip/config.json and no
    legacy .contextzip.json at all — offer to open the local config UI
    instead of silently proceeding with bare defaults.

    Deliberately conservative about when to ask:
      - never if --prompt or --output were given (the user is mid-task,
        not exploring — don't interrupt with a browser tab)
      - never outside an interactive terminal, or with CI set (scripted/
        automated runs must never block on a prompt)
      - never again once declined once (persisted personally, not per
        project — see config.get_config_ui_dismissed)

    Returns True if the user saved a config during the offered session
    (the caller should reload project config before continuing).
    """
    import sys

    if prompt or output:
        return False
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    if os.environ.get("CI"):
        return False

    from contextzip.config import get_config_ui_dismissed, save_config_ui_dismissed

    if get_config_ui_dismissed():
        return False

    info("No project config found yet.")
    info("contextzip can open a local browser tab to set include/exclude visually.")
    console.print()

    if not click.confirm("  Set up include/exclude visually now?", default=True):
        save_config_ui_dismissed()
        info("No problem — run contextzip config --ui anytime.")
        console.print()
        return False

    from contextzip.webui.server import launch_config_ui

    return launch_config_ui(project_dir, detection, con=console)


def _auto_cleanup(project_dir: Path, project_cfg) -> None:
    """
    Automatically, silently prune the .contextzip/ workspace after a
    successful command — no confirmation, no dry-run, no separate command
    to remember. Every zip is trivially reproducible by re-running
    contextzip, so this is deliberately brutal rather than cautious: it
    keeps only the `cleanup.keep_recent` most recent zip/manifest/report
    set per mode folder, the most recent `cleanup.keep_recent` backup
    folder(s), and the most recent `cleanup.keep_recent` archived
    applied-zip(s) — everything else is deleted immediately, every run.

    Gated on `cleanup.enabled` (default True) — set to False in
    .contextzip/config.json to turn this off entirely.

    Scanning + deleting only ever touches .contextzip/ metadata (a
    handful of small files), never the project itself, so this stays
    fast even on large projects — it does not rescan or re-read any
    project source file.
    """
    if not project_cfg.cleanup.enabled:
        return
    try:
        plan = cleanup_mod.scan(
            project_dir,
            keep_recent=project_cfg.cleanup.keep_recent,
            applied_zip_retention=project_cfg.applied_zip_retention,
        )
        if plan.is_empty:
            return
        result = cleanup_mod.execute(plan)
    except OSError:
        return
    print_auto_cleanup(result)


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
    console.print()

    # ── Project config (.contextzip/config.json) ────────────────────────────
    project_cfg = load_project_config(project_dir)
    if has_legacy_project_config(project_dir):
        warn(
            "Found the deprecated .contextzip.json — move it to .contextzip/config.json"
        )

    # ── Detection ────────────────────────────────────────────────────────────
    with console.status("[cyan]Detecting project ecosystem…[/]", spinner="dots"):
        detection = detect(project_dir)

    print_detection(detection)

    # ── First-run offer: visual config UI ────────────────────────────────────
    if project_cfg.source_path is None:
        saved = _maybe_offer_config_ui(
            project_dir, detection, prompt=prompt, output=output
        )
        if saved:
            project_cfg = load_project_config(project_dir)

    # ── Git-changes mode ─────────────────────────────────────────────────────
    if git_changes:
        with console.status("[cyan]Querying git for changed files…[/]", spinner="dots"):
            git_result = get_changed_files(project_dir)

        if isinstance(git_result, GitError):
            err(f"Git error: {git_result.message}")
            raise SystemExit(1)

        print_git_deleted_and_submodules(git_result)

        if git_result.is_empty:
            info("Nothing to package — working tree is clean.")
            return

        scan_label, scan_detail = git_scan_label_and_detail(git_result)
        if verbose:
            print_git_verbose_files(git_result)

        with console.status("[cyan]Checking git-changed files…[/]", spinner="dots"):
            resolved = resolve_files_from_git(
                git_files=git_result.files,
                project_dir=project_dir,
                large_file_warn_bytes=int(
                    project_cfg.limits.max_file_size_mb * 1024 * 1024
                ),
            )

    else:
        # ── Build exclusion spec ─────────────────────────────────────────────
        gitignore_path = None if no_gitignore else (project_dir / ".gitignore")

        # CLI --exclude/-e patterns plus any standing always_exclude patterns
        # from project config — both are additive, persistent behavior comes
        # from config.json so it doesn't need to be re-typed every run.
        normalized_exclude = [normalize_pattern(p) for p in extra_exclude or []]
        normalized_exclude += [normalize_pattern(p) for p in project_cfg.always_exclude]

        with console.status("[cyan]Building exclusion rules…[/]", spinner="dots"):
            spec = build_spec(
                rule_modules=detection.rule_modules,
                extra_exclude=normalized_exclude if normalized_exclude else None,
                gitignore_path=gitignore_path,
            )
            force_include = build_force_include_spec(
                [normalize_pattern(p) for p in project_cfg.always_include]
            )

        # ── Resolve files ─────────────────────────────────────────────────────
        with console.status("[cyan]Scanning project files…[/]", spinner="dots"):
            resolved = resolve_files(
                project_dir=project_dir,
                spec=spec,
                include_only=include_only if include_only else None,
                force_include=force_include,
                large_file_warn_bytes=int(
                    project_cfg.limits.max_file_size_mb * 1024 * 1024
                ),
            )

        # Snapshot the scan summary now — --prompt reassigns resolved.included
        # below, and the merged "Scanned & Packed" line still needs to report
        # the original, pre-selection counts.
        scan_label, scan_detail = scan_label_and_detail(resolved, git_mode=False)

    large_file_warn_bytes = int(project_cfg.limits.max_file_size_mb * 1024 * 1024)

    # ── Dry run ──────────────────────────────────────────────────────────────
    if dry_run:
        if prompt:
            ok(scan_label, scan_detail)
            print_file_warnings(
                resolved, project_dir, large_file_warn_bytes=large_file_warn_bytes
            )
            _enforce_ai_config(project_cfg.ai)
            diagnosis = diagnose_api_key()
            if diagnosis:
                warn(diagnosis)
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
            print_scan_summary(resolved, project_dir, verbose, git_mode=git_changes)
            print_file_warnings(
                resolved, project_dir, large_file_warn_bytes=large_file_warn_bytes
            )
            info("Dry run — no ZIP created. Remove --dry-run to produce the archive.")
        return

    if not resolved.included:
        err("Nothing to package — all files were excluded.")
        info("Try contextzip include PATH or -i PATH to override.")
        return

    # ── AI-powered file selection (--prompt mode) ────────────────────────────
    prompt_txt: str | None = None

    if prompt:
        _enforce_ai_config(project_cfg.ai)
        diagnosis = diagnose_api_key()
        if diagnosis:
            warn(diagnosis)
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
            prompt_template=project_cfg.ai.prompt_template,
        )

        if not selected_paths:
            err("AI selection returned no files.")
            info(
                "Try a more specific prompt, or run without --prompt to package the full project."
            )
            raise SystemExit(1)

        resolved.included = selected_paths

    # ── Create ZIP ───────────────────────────────────────────────────────────
    output_path = Path(output).resolve() if output else None
    run_mode = "git-changes" if git_changes else ("prompt" if prompt else "standard")

    try:
        result = create_zip(
            resolve_result=resolved,
            project_dir=project_dir,
            output_path=output_path,
            console=console,
            mode=run_mode,
            prompt_txt=prompt_txt,
            redact_secrets_enabled=project_cfg.limits.redact_secrets,
        )
    except Exception as exc:
        err(f"Failed to create ZIP: {exc}")
        raise SystemExit(1)

    print_scan_and_pack(scan_label, scan_detail, result)
    print_file_warnings(
        resolved, project_dir, large_file_warn_bytes=large_file_warn_bytes
    )
    print_zip_write_warnings(result)
    print_redaction_summary(result)
    print_package_result(result)

    # ── Report ───────────────────────────────────────────────────────────────
    report_path = write_scan_report(
        zip_path=result.zip_path,
        project_dir=project_dir,
        detection=detection,
        resolved=resolved,
        mode=run_mode,
        ai_prompt=prompt,
        ai_selected=resolved.included if prompt else None,
        large_file_warn_bytes=large_file_warn_bytes,
        redacted=result.redacted,
    )
    print_report_hint(report_path)

    # ── Clipboard ────────────────────────────────────────────────────────────
    if not no_clipboard:
        cb = clipboard_handle(result.zip_path)
        print_clipboard_result(cb)

    # ── Auto-cleanup ─────────────────────────────────────────────────────────
    _auto_cleanup(project_dir, project_cfg)
