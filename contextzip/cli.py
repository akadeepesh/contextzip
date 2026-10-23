"""
cli.py — contextzip entry point and command routing.
"""

from __future__ import annotations

import os
import time
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
    print_root_notice,
)
from contextzip.report import write_scan_report, write_apply_report
from contextzip.applier import (
    ApplyError,
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
from contextzip import jsonout
from contextzip import updater as updater_mod
from contextzip import remote as remote_mod
from contextzip import root as root_mod
from contextzip.root import ROOT_KIND_LABELS, ProjectRoot, find_project_root, to_root_relative

console = Console()


def _interactive() -> bool:
    """True when there's a person at a terminal to ask. Never under --json."""
    import sys

    return sys.stdin.isatty() and sys.stdout.isatty() and jsonout.active() is None


def _json_session(command: str, enabled: bool):
    """Run a command body under --json when *enabled* (see jsonout.py)."""
    from contextzip import cli_ai, cli_display, cli_onboard

    consoles = (console, cli_display.console, cli_ai.console, cli_onboard.console)
    return jsonout.session(command, enabled, consoles=consoles)


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
        click.option(
            "--here",
            is_flag=True,
            default=False,
            help=(
                "Only package the folder you're in. contextzip always works from "
                "the project root; paths in the ZIP stay relative to it."
            ),
        ),
        click.option(
            "--from",
            "remote",
            default=None,
            metavar="REPO",
            help=(
                "Package a GitHub repository instead of the local project, without "
                "cloning it: github.com/user/repo, a /tree/<branch>/<folder> link, or "
                "gh:user/repo. Shortcut: contextzip github.com/user/repo. Private repos "
                "use GITHUB_TOKEN or your gh login."
            ),
        ),
        click.option(
            "--ref",
            default=None,
            metavar="REF",
            help="Branch, tag or commit to package with --from (default: the repo's default branch).",
        ),
        click.option(
            "--json",
            "json_output",
            is_flag=True,
            default=False,
            help="Print one JSON object instead of terminal output, for editors and scripts. Never prompts.",
        ),
    ]
    for dec in reversed(decorators):
        f = dec(f)
    return f


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------


class _MainGroup(click.Group):
    """
    Lets a repository stand where a command would: `contextzip
    github.com/user/repo -n` runs as `contextzip -n --from github.com/user/repo`,
    and `contextzip gh:user/repo include src/` as `include src/ --from …` —
    moved to the end so it reaches whichever command actually runs.
    """

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        takes_value = {
            opt
            for p in self.params
            if isinstance(p, click.Option) and not p.is_flag and not p.count
            for opt in (*p.opts, *p.secondary_opts)
        }
        for i, arg in enumerate(args):
            if arg in self.commands or arg == "--":
                break
            if i and args[i - 1] in takes_value:
                continue
            if not arg.startswith("-") and remote_mod.looks_like_remote(arg):
                args = [*args[:i], *args[i + 1:], "--from", arg]
                break
        return super().parse_args(ctx, args)


@click.group(
    cls=_MainGroup,
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
    here: bool,
    remote: str | None,
    ref: str | None,
    json_output: bool,
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
        here=here,
        remote=remote,
        ref=ref,
        json_output=json_output,
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
    here: bool,
    remote: str | None,
    ref: str | None,
    json_output: bool,
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
        here=here,
        remote=remote,
        ref=ref,
        json_output=json_output,
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
    here: bool,
    remote: str | None,
    ref: str | None,
    json_output: bool,
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
        here=here,
        remote=remote,
        ref=ref,
        json_output=json_output,
    )


# ---------------------------------------------------------------------------
# Subcommand: apply-zip
# ---------------------------------------------------------------------------


@main.command("apply-zip")
@click.argument("zip_path", required=False, metavar="[ZIP]")
@click.option(
    "--snapshot",
    "snapshot_ref",
    default=None,
    metavar="ID",
    help=(
        "Snapshot this reply answers (e.g. 14). Usually found automatically "
        "from the CONTEXTZIP.md in the reply, or by content."
    ),
)
@click.option("--manifest", "manifest_path", default=None, metavar="PATH", hidden=True)
@click.option(
    "--remove-moved",
    is_flag=True,
    default=False,
    help="When the AI moved or renamed a file, remove the old copy (only if you haven't changed it).",
)
@click.option(
    "--allow-delete",
    is_flag=True,
    default=False,
    help="Delete files the AI asked to delete (only if you haven't changed them).",
)
@click.option(
    "--dry-run",
    "-n",
    is_flag=True,
    default=False,
    help="Show what would change without writing anything.",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="List every file and what will happen to it.",
)
@click.option(
    "--review",
    is_flag=True,
    default=False,
    help="Decide file by file in a local browser page with side-by-side diffs.",
)
@click.option(
    "--editor",
    "use_editor",
    is_flag=True,
    default=False,
    help="Resolve files that need a decision in VS Code (or Cursor, Windsurf, VSCodium).",
)
@click.option(
    "--safe-only",
    is_flag=True,
    default=False,
    help="Apply only the safe changes; leave files that need a decision untouched.",
)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    default=False,
    help="Apply everything without asking. Where you and the AI disagree, the AI's version wins.",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    default=False,
    help=(
        "Print one JSON object instead of terminal output, for editors and scripts. Never prompts: "
        "if files need a decision, nothing is written unless --safe-only, --yes or --review says what to do."
    ),
)
def cmd_apply_zip(
    zip_path: str | None,
    snapshot_ref: str | None,
    manifest_path: str | None,
    remove_moved: bool,
    allow_delete: bool,
    dry_run: bool,
    verbose: bool,
    review: bool,
    use_editor: bool,
    safe_only: bool,
    yes: bool,
    json_output: bool,
) -> None:
    """
    Apply an AI-returned ZIP back into the project.

    \b
    EXAMPLES
      contextzip apply-zip                  # picks up the zip in .contextzip/inbox/
      contextzip apply-zip fix.zip          # or one anywhere on disk
      contextzip apply-zip --dry-run -v     # see exactly what would happen
      contextzip apply-zip --review         # decide file by file in the browser
      contextzip apply-zip --editor         # resolve conflicts in VS Code
      contextzip apply-zip --safe-only      # scripts/CI: only the safe changes

    \b
    HOW IT WORKS
      Each file in the zip is matched to its place in the project — even if
      the AI wrapped it in a folder, made paths relative to a subfolder or
      flattened them. It's then compared with what you sent (a local snapshot
      that never leaves your machine) and with the file on disk now:

    \b
        new, modified        written
        merged               you both changed it in different places — merged
        unchanged            already matches — skipped
        conflict             you both changed the same or neighbouring lines
        drifted / deleted    you changed or deleted it and it can't be merged
        untracked            exists, but wasn't in the zip you sent
        partial              the AI returned a cut-down file ("... rest unchanged")
        ambiguous            can't tell which file it belongs to
        moved                the AI moved/renamed it — new path written, old kept
        delete-requested     the AI asked to delete it in CONTEXTZIP.md

      Conflicts and the groups after them are never written without your
      say-so. Files are only removed with an explicit yes (or --remove-moved
      / --allow-delete), and never one you've changed since the snapshot.
    """
    with _json_session("apply-zip", json_output):
        _apply_zip(
            zip_path,
            snapshot_ref=snapshot_ref,
            manifest_path=manifest_path,
            remove_moved=remove_moved,
            allow_delete=allow_delete,
            dry_run=dry_run,
            verbose=verbose,
            review=review,
            use_editor=use_editor,
            safe_only=safe_only,
            yes=yes,
        )


def _apply_zip(
    zip_path: str | None,
    *,
    snapshot_ref: str | None,
    manifest_path: str | None,
    remove_moved: bool,
    allow_delete: bool,
    dry_run: bool,
    verbose: bool,
    review: bool,
    use_editor: bool,
    safe_only: bool,
    yes: bool,
) -> None:
    if use_editor and jsonout.active() is not None:
        err("--editor needs a terminal to ask in — use --review, --safe-only or --yes with --json.")
        raise SystemExit(1)

    project_root = _resolve_root()
    project_dir = project_root.root
    project_cfg = load_project_config(project_dir)

    console.print()
    print_root_notice(project_root)

    try:
        resolved_zip = find_zip_to_apply(project_dir, zip_path)
    except ApplyError as exc:
        err(str(exc))
        raise SystemExit(1)

    explicit_manifest = None
    if snapshot_ref is not None:
        from contextzip.snapshots import find as find_snapshot

        snap = find_snapshot(project_dir, snapshot_ref)
        if snap is None:
            err(f"No snapshot {snapshot_ref} here — see contextzip snapshots")
            raise SystemExit(1)
        explicit_manifest = snap.manifest_path
    elif manifest_path is not None:
        explicit_manifest = find_latest_manifest(project_dir, manifest_path)
        if explicit_manifest is None:
            err(f"No such manifest: {manifest_path}")
            raise SystemExit(1)

    try:
        with console.status("[cyan]Matching files and comparing versions…[/]", spinner="dots"):
            plan = build_plan(resolved_zip, project_dir, explicit_manifest)
    except ApplyError as exc:
        err(str(exc))
        raise SystemExit(1)

    print_apply_plan(plan, project_dir, verbose=verbose)
    report_path = write_apply_report(project_dir=project_dir, plan=plan)
    jsonout.put("project", {"root": str(project_dir), "kind": project_root.kind})
    jsonout.put("plan", jsonout.plan_data(plan))
    jsonout.put("result", None)
    jsonout.put("report", str(report_path))

    if not plan.writable_entries:
        info("Nothing to apply — every file already matches the project.")
        discard_plan(plan)
        _archive_if_inbox(plan, project_dir, project_cfg)
        return

    if dry_run:
        info("Dry run — no files written. Remove --dry-run to apply these changes.")
        print_report_hint(report_path)
        discard_plan(plan)
        return

    decisions: dict | None = None
    include_risky = False
    mode = None

    if plan.is_risky:
        mode = _apply_mode(plan, review=review, use_editor=use_editor, safe_only=safe_only, yes=yes)
    elif review:
        mode = "review"

    if mode == "quit":
        info("Cancelled — no files written.")
        discard_plan(plan)
        return
    if mode == "all":
        include_risky = True
    elif mode == "review":
        from contextzip.webui.review import review_in_browser

        console.print()
        decisions = review_in_browser(plan, project_dir, con=console)
        if decisions is None:
            info("Review closed without applying — no files written.")
            discard_plan(plan)
            return
    elif mode == "editor":
        decisions = _resolve_in_editor(plan, project_dir)
        if decisions is None:
            info("Nothing applied.")
            discard_plan(plan)
            return

    if mode != "review":
        remove_moved, allow_delete = _removal_consent(plan, remove_moved=remove_moved, allow_delete=allow_delete)

    result = execute_plan(
        plan,
        project_dir,
        retain=project_cfg.applied_zip_retention,
        decisions=decisions,
        include_risky=include_risky,
        remove_moved=remove_moved,
        allow_delete=allow_delete,
    )
    console.print()
    print_apply_result(result)
    report_path = write_apply_report(project_dir=project_dir, plan=plan, result=result)
    print_report_hint(report_path)
    jsonout.put("result", jsonout.apply_result_data(result))
    jsonout.put("report", str(report_path))

    # ── Auto-cleanup ─────────────────────────────────────────────────────────
    _auto_cleanup(project_dir, project_cfg)


def _resolve_root():
    """
    Find the project root (see root.py) — and, the first time a folder looks
    like a monorepo without git at the top, ask whether to use it. A yes
    writes .contextzip/config.json there, which makes it the root from then
    on; a no is remembered so the question never comes back.
    """
    project_root = find_project_root()
    suggested = project_root.suggested_root
    if suggested is None or not _interactive():
        return project_root

    projects = sorted(
        c.name for c in suggested.iterdir()
        if c.is_dir() and not c.name.startswith(".") and root_mod._is_project(c)
    )
    console.print()
    names = ", ".join(f"{n}/" for n in projects)
    info(f"{names} look like parts of one project in {suggested.name}/.")
    use_it = click.confirm(f"  Use {suggested.name}/ as the project root?", default=True)
    if use_it:
        from contextzip.packager import _ensure_workspace_gitignore

        workspace = suggested / ".contextzip"
        workspace.mkdir(parents=True, exist_ok=True)
        config_file = workspace / "config.json"
        if not config_file.exists():
            config_file.write_text("{}\n", encoding="utf-8")
        try:
            _ensure_workspace_gitignore(workspace)
        except OSError:
            pass
        ok(f"Project root set to {suggested}", "saved in .contextzip/config.json")
        return find_project_root()

    from contextzip.config import save_declined_root

    try:
        save_declined_root(str(suggested))
    except OSError:
        pass
    info("Got it — contextzip won't ask about this folder again.")
    return project_root


def _removal_consent(plan, *, remove_moved: bool, allow_delete: bool) -> tuple[bool, bool]:
    """
    Removing files is never implied — not even by --yes. Ask about moved
    files' old copies and requested deletions separately, when there's
    someone to ask and no flag already answered.
    """
    interactive = _interactive()
    moves = plan.moves
    if moves and not remove_moved and interactive:
        console.print()
        console.print(f"  The AI moved {len(moves)} file{'s' if len(moves) != 1 else ''}:")
        for e in moves[:10]:
            console.print(f"    [cyan]→[/] {e.moved_from}  [dim]→[/]  {e.rel_path}")
        remove_moved = click.confirm("  Remove the old file(s)?", default=False)

    deletions = [e for e in plan.deletions if e.removable]
    if deletions and not allow_delete and interactive:
        console.print()
        console.print(f"  The AI asked to delete {len(deletions)} file{'s' if len(deletions) != 1 else ''}:")
        for e in deletions[:10]:
            console.print(f"    [yellow]−[/] {e.rel_path}")
        allow_delete = click.confirm("  Delete them?", default=False)
    return remove_moved, allow_delete


def _archive_if_inbox(plan, project_dir: Path, project_cfg) -> None:
    """A zip with nothing left to apply is done — move it out of the inbox."""
    from contextzip.applier import _move_zip_to_applied, _workspace_dir as _ws

    try:
        dest = _move_zip_to_applied(plan.zip_path, project_dir, _ws(project_dir), retain=project_cfg.applied_zip_retention)
    except OSError:
        return
    if dest != plan.zip_path:
        ok("Archived zip to", str(dest))


def _apply_mode(plan, *, review: bool, use_editor: bool, safe_only: bool, yes: bool) -> str:
    """How to handle files that need a decision: review | editor | safe | all | quit."""
    if review:
        return "review"
    if use_editor:
        return "editor"
    if safe_only:
        return "safe"
    if yes:
        return "all"

    if not _interactive():
        err("Some files need a decision, and there's no one here to ask.")
        info("Re-run with --safe-only to apply just the safe changes, or --yes to apply everything.")
        discard_plan(plan)
        raise SystemExit(1)

    from contextzip.editor import editor_name, find_editor

    safe = len(plan.safe_entries)
    risky = len(plan.risky_entries)
    editor = find_editor()
    them = "them" if risky != 1 else "it"

    console.print()
    console.print("  [bold]What now?[/]")
    if safe:
        console.print(f"    [bold cyan]S[/]  apply the {safe} safe change{'s' if safe != 1 else ''}, leave {them} for later")
    else:
        console.print(f"    [bold cyan]S[/]  leave {them} for later")
    console.print(f"    [bold cyan]R[/]  review {them} in your browser, side by side")
    if editor:
        console.print(f"    [bold cyan]E[/]  resolve {them} in {editor_name(editor)}")
    console.print("    [bold cyan]A[/]  apply everything — the AI's version wins")
    console.print("    [bold cyan]Q[/]  quit without writing")
    keys = ["s", "r", "a", "q"] + (["e"] if editor else [])
    choice = click.prompt("  Choose", type=click.Choice(keys, case_sensitive=False), default="s", show_choices=False)
    return {"s": "safe", "r": "review", "e": "editor", "a": "all", "q": "quit"}[choice.lower()]


def _resolve_in_editor(plan, project_dir: Path) -> dict | None:
    from contextzip.editor import editor_name, find_editor, resolve_in_editor

    editor = find_editor()
    if editor is None:
        warn("No editor found (looked for code, cursor, windsurf, codium) — opening the browser review instead.")
        info("Set CONTEXTZIP_EDITOR to use another editor.")
        from contextzip.webui.review import review_in_browser

        return review_in_browser(plan, project_dir, con=console)

    info(f"Opening {len(plan.risky_entries)} file(s) in {editor_name(editor)} — close each tab when you're done.")

    def ask(entry) -> str:
        take = click.confirm(f"  {entry.display_path}: take the AI's version?", default=False)
        return "theirs" if take else "keep"

    return resolve_in_editor(plan, project_dir, ask)


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
    project_root = _resolve_root()
    project_dir = project_root.root

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

    print_root_notice(project_root)

    exit_code = run_watch(
        command=cmd_list,
        project_dir=project_dir,
        run_dir=project_root.cwd,
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

        project_dir = _resolve_root().root
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

    project_root = _resolve_root()
    cwd = project_root.root
    ok(f"Project root: {cwd}", ROOT_KIND_LABELS.get(project_root.kind, project_root.kind))
    ws_location, ws_source = _resolve_workspace_location(cwd)
    from contextzip.packager import _workspace_dir as _ws_dir

    ok(f"Workspace: {_ws_dir(cwd)[0]}", f"{ws_location} · {ws_source}")

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


# ---------------------------------------------------------------------------
# Subcommand: update
# ---------------------------------------------------------------------------


@main.command("snapshots")
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    default=False,
    help="Print one JSON object instead of terminal output, for editors and scripts.",
)
def cmd_snapshots(json_output: bool) -> None:
    """
    List this project's snapshots.

    \b
    Every package contextzip makes is kept as a numbered snapshot, so
    apply-zip can merge an AI reply against exactly what you sent — even
    after you've packaged again. The CONTEXTZIP.md inside each ZIP names
    its snapshot, so a reply that echoes it back is matched exactly.

    \b
    Snapshots stay on your machine and are pruned automatically: each is
    kept while it's among the newest `snapshots.keep` (default 10), younger
    than `snapshots.max_age_days` (default 14), or still awaited by a reply
    in the inbox.
    """
    with _json_session("snapshots", json_output):
        _list_snapshots()


def _list_snapshots() -> None:
    from contextzip import snapshots as snaps

    project_root = _resolve_root()
    project_dir = project_root.root
    console.print()
    print_root_notice(project_root)

    found = snaps.list_snapshots(project_dir)
    jsonout.put("project", {"root": str(project_dir), "kind": project_root.kind})
    jsonout.put("snapshots", [])
    if not found:
        info("No snapshots yet — run contextzip to make one.")
        console.print()
        return

    cfg = load_project_config(project_dir)
    ok(
        f"{len(found)} snapshot{'s' if len(found) != 1 else ''}",
        f"kept while among the newest {cfg.snapshots.keep} or younger than {cfg.snapshots.max_age_days} days",
    )
    now = time.time()
    for s in found:
        data = s.load()
        files = len(data.get("files", {}))
        status = data.get("status", "pending")
        mode = {"standard": "codebase", "git-changes": "changes", "prompt": "prompt"}.get(data.get("mode", ""), data.get("mode", "?"))
        age = _ago(now - snaps.created_ts(s))
        session = jsonout.active()
        if session is not None:
            session.data["snapshots"].append(
                {"id": s.id, "token": s.token, "label": s.label, "mode": mode, "files": files,
                 "status": status, "created": snaps.created_ts(s)}
            )
        colour = "green" if status == "applied" else "dim"
        console.print(
            f"  [bold]#{s.id:<4}[/] [dim]{s.token}[/]  {mode:<9} {files:>5} files  {age:>12}   [{colour}]{status}[/]"
        )
    console.print()


def _ago(seconds: float) -> str:
    seconds = max(0, int(seconds))
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= size:
            n = seconds // size
            return f"{n} {unit}{'s' if n != 1 else ''} ago"
    return "just now"


@main.command("setup-path")
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    default=False,
    help="Skip the confirmation prompt.",
)
def cmd_setup_path(yes: bool) -> None:
    """
    Put the cz and contextzip commands on your PATH.

    \b
    If your terminal says "cz is not recognized" or "command not found",
    pip installed the commands into a folder your PATH doesn't include —
    common with the Microsoft Store Python and `pip install --user`.
    Run this through Python, which always works:

    \b
      python -m contextzip setup-path      # macOS / Linux: python3 -m …

    \b
    Windows: the folder is added to your user PATH (no admin needed).
    macOS / Linux: one line is added to your shell's startup file.
    Open a new terminal afterwards.
    """
    from contextzip import pathsetup

    console.print()
    st = pathsetup.status()

    if st.manager == "pipx":
        info("contextzip was installed with pipx, which manages PATH itself. Run:")
        console.print("    pipx ensurepath")
        console.print()
        return
    if st.manager == "uv":
        info("contextzip was installed with uv, which manages PATH itself. Run:")
        console.print("    uv tool update-shell")
        console.print()
        return
    if st.in_venv:
        ok("contextzip is installed in a virtual environment")
        info("cz is on PATH whenever that environment is activated.")
        console.print()
        return
    if st.directory is None:
        err("Couldn't find the folder pip installed cz into")
        info("You can always run contextzip as: python -m contextzip")
        console.print()
        raise SystemExit(1)
    if st.on_path:
        ok("cz is already on your PATH", str(st.directory))
        console.print()
        return

    info(f"cz is installed in {st.directory}")
    info("That folder isn't on your PATH, so terminals can't find it.")

    if os.name == "nt" and pathsetup.is_store_python():
        # A Store-packaged Python can't change the real user PATH from its sandbox.
        console.print()
        info("This Python comes from the Microsoft Store, so it can't change PATH itself.")
        info("Paste this into PowerShell, then open a new terminal:")
        console.print()
        console.print(pathsetup.powershell_command(st.directory), soft_wrap=True, highlight=False)
        console.print()
        return

    target = pathsetup.target_label()
    if not yes and not click.confirm(f"  Add it to {target}?", default=True):
        info("Not changed. You can keep using: python -m contextzip")
        console.print()
        return

    try:
        changed = pathsetup.add_to_path(st.directory)
    except OSError as exc:
        err(f"Couldn't update {target}: {exc}")
        console.print()
        raise SystemExit(1)

    if changed:
        ok(f"Added to {target}", str(st.directory))
    else:
        ok(f"Already in {target}", str(st.directory))
    info("Open a new terminal, then run cz.")
    console.print()


@main.command("update")
@click.option(
    "--check",
    "check_only",
    is_flag=True,
    default=False,
    help="Only check for a newer version — don't install anything.",
)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    default=False,
    help="Skip the confirmation prompt and update immediately if available.",
)
def cmd_update(check_only: bool, yes: bool) -> None:
    """
    Check PyPI for a newer contextzip release and update in place.

    \b
    EXAMPLES
      contextzip update              # check, then confirm before updating
      contextzip update --yes        # update immediately if one is available
      contextzip update --check      # only report the current/latest version
      cz update                      # same command, short alias

    \b
    HOW IT WORKS
      contextzip cuts frequent releases, so this beats remembering
      `pip install --upgrade contextzip` by hand. It detects whether this
      install came from pip, pipx, or uv tool and re-invokes the matching
      one, upgrading the same interpreter that's currently running —
      never a possibly-different "pip" found on PATH.
    """
    console.print()
    with console.status("[cyan]Checking for updates…[/]", spinner="dots"):
        check = updater_mod.check_latest_version()

    if check.latest is None:
        err(f"Could not check for updates: {check.error}")
        info(f"You're on {check.current}. Try again later, or update manually:")
        info("  pip install --upgrade contextzip")
        console.print()
        raise SystemExit(1)

    if not check.update_available:
        ok(f"You're up to date — contextzip {check.current}")
        console.print()
        return

    info(f"Update available: {check.current} → {check.latest}")

    if check_only:
        info("Run contextzip update to install it.")
        console.print()
        return

    if not yes:
        proceed = click.confirm(f"  Update to {check.latest} now?", default=True)
        if not proceed:
            info("Not updating.")
            console.print()
            return

    with console.status(f"[cyan]Updating to {check.latest}…[/]", spinner="dots"):
        result = updater_mod.run_update()

    console.print()
    if result.ok:
        ok(f"Updated to contextzip {check.latest}", f"via {result.method}")
        info("Restart any running contextzip/watch sessions to pick it up.")
    else:
        err(f"Update via {result.method} failed")
        if result.output:
            console.print(f"[dim]{result.output}[/]")
        info("You can also update manually: pip install --upgrade contextzip")
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
    if prompt or output:
        return False
    if not _interactive():
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
    set per mode folder, the most recent `cleanup.keep_recent` archived
    applied-zip(s), and the most recent `cleanup.keep_recent` loose
    apply-report(s) in inbox/ — everything else is deleted immediately,
    every run. Also wipes .contextzip/backups/ outright if a leftover one
    still exists from an older contextzip version.

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
            snapshots_keep=project_cfg.snapshots.keep,
            snapshots_max_age_days=project_cfg.snapshots.max_age_days,
        )
        if plan.is_empty:
            return
        result = cleanup_mod.execute(plan)
    except OSError:
        return
    print_auto_cleanup(result)


def _run(*, json_output: bool = False, **kwargs) -> None:
    """
    All packaging work lives in _package. The main command and every
    subcommand delegate here after collecting their arguments/flags,
    keeping the CLI surface thin and the logic testable in isolation.
    """
    with _json_session("package", json_output):
        _package(**kwargs)


def _fetch_remote(target: str, ref: str | None):
    """Fetch a GitHub repository into the cache (see remote.py), or exit with why not."""
    from contextzip.cli_display import human_size

    try:
        spec = remote_mod.parse_target(target)
        with console.status(f"[cyan]Fetching {spec.slug} from GitHub…[/]", spinner="dots"):
            fetched = remote_mod.fetch(spec, ref)
    except remote_mod.RemoteError as exc:
        err(str(exc))
        raise SystemExit(1)

    where = "from cache" if fetched.cached else f"downloaded {human_size(fetched.download_bytes)}"
    ok(f"Fetched {fetched.label}", f"{fetched.ref or 'default branch'} · {where}")
    if fetched.subdir:
        info(f"Only {fetched.subdir}, from the link")
    if fetched.skipped:
        n = len(fetched.skipped)
        info(f"{n} entr{'ies' if n != 1 else 'y'} in the repo couldn't be unpacked here (links, or names this system doesn't allow)")
    jsonout.put("remote", jsonout.remote_data(fetched))
    return fetched


def _gemini_key_or_exit() -> str:
    """The Gemini key, set up interactively on first use — never prompting under --json."""
    diagnosis = diagnose_api_key()
    if diagnosis:
        warn(diagnosis)
    api_key = get_api_key()
    if api_key:
        return api_key
    if jsonout.active() is not None:
        err("No Gemini API key set. Set GEMINI_API_KEY, or run contextzip --prompt once in a terminal to set one up.")
        raise SystemExit(1)
    api_key = onboard_api_key()
    if not api_key:
        raise SystemExit(0)
    return api_key


def _package(
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
    here: bool = False,
    remote: str | None = None,
    ref: str | None = None,
) -> None:
    console.print()
    run_mode = "git-changes" if git_changes else ("prompt" if prompt else "standard")
    jsonout.put("mode", run_mode)
    jsonout.put("dry_run", dry_run)
    jsonout.put("zip", None)

    # ── Where to work: this project, or a GitHub repository ─────────────────
    fetched = None
    if remote:
        if git_changes:
            err("--git-changes needs a local checkout — it can't be combined with a GitHub repository.")
            raise SystemExit(1)
        fetched = _fetch_remote(remote, ref)
        # A /tree/<branch>/<folder> link acts like running from that folder.
        target = fetched.project_dir / fetched.subdir if fetched.subdir else fetched.project_dir
        project_root = ProjectRoot(
            root=fetched.project_dir,
            cwd=target if target.is_dir() else target.parent,
            kind="remote",
        )
    else:
        if ref:
            err("--ref picks a branch of a GitHub repository — use it with --from github.com/user/repo.")
            raise SystemExit(1)
        project_root = _resolve_root()
    project_dir = project_root.root
    jsonout.put("project", {"root": str(project_dir), "kind": project_root.kind})

    # Paths typed on the command line are relative to where you are — like
    # git add — so translate them to the project root before anything else.
    if include_only:
        include_only = to_root_relative(include_only, project_root)
    if extra_exclude:
        extra_exclude = to_root_relative(extra_exclude, project_root, patterns=True)
    if fetched is not None:
        if fetched.subdir and not include_only:
            include_only = [fetched.subdir]
    else:
        if here and project_root.in_subdir:
            include_only = [*(include_only or []), project_root.subdir]
        print_root_notice(project_root, here=here, packaging=True)

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
    jsonout.put("detection", jsonout.detection_data(detection))

    # ── First-run offer: visual config UI ────────────────────────────────────
    if project_cfg.source_path is None and fetched is None:
        saved = _maybe_offer_config_ui(
            project_dir, detection, prompt=prompt, output=output
        )
        if saved:
            project_cfg = load_project_config(project_dir)

    # ── Include/exclude inputs shared by every scan mode ─────────────────────
    # CLI --exclude/-e patterns plus any standing always_exclude patterns from
    # project config — both are additive, persistent behavior comes from
    # config.json so it doesn't need to be re-typed every run. always_include
    # is a standing negation. Built once here so standard and --git-changes
    # runs honour project config identically.
    normalized_exclude = [normalize_pattern(p) for p in extra_exclude or []]
    normalized_exclude += [normalize_pattern(p) for p in project_cfg.always_exclude]
    force_include = build_force_include_spec(
        [normalize_pattern(p) for p in project_cfg.always_include]
    )

    # ── Git-changes mode ─────────────────────────────────────────────────────
    if git_changes:
        with console.status("[cyan]Querying git for changed files…[/]", spinner="dots"):
            git_result = get_changed_files(project_dir)

        if isinstance(git_result, GitError):
            err(f"Git error: {git_result.message}")
            raise SystemExit(1)

        print_git_deleted_and_submodules(git_result)

        if git_result.is_empty:
            info("Nothing to compress in this one — working tree is clean.")
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
                extra_exclude=normalized_exclude if normalized_exclude else None,
                include_only=include_only if include_only else None,
                force_include=force_include,
            )

    else:
        # ── Build exclusion spec ─────────────────────────────────────────────
        gitignore_path = None if no_gitignore else (project_dir / ".gitignore")

        with console.status("[cyan]Building exclusion rules…[/]", spinner="dots"):
            spec = build_spec(
                rule_modules=detection.rule_modules,
                extra_exclude=normalized_exclude if normalized_exclude else None,
                gitignore_path=gitignore_path,
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
    jsonout.put("files", jsonout.files_data(resolved, project_dir, verbose=verbose, git_mode=git_changes))

    # ── Dry run ──────────────────────────────────────────────────────────────
    if dry_run:
        if prompt:
            ok(scan_label, scan_detail)
            print_file_warnings(
                resolved, project_dir, large_file_warn_bytes=large_file_warn_bytes
            )
            _enforce_ai_config(project_cfg.ai)
            api_key = _gemini_key_or_exit()
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
        err("Nothing to compress in this one — all files were excluded.")
        info("Try contextzip include PATH or -i PATH to override.")
        return

    # ── AI-powered file selection (--prompt mode) ────────────────────────────
    prompt_txt: str | None = None

    if prompt:
        _enforce_ai_config(project_cfg.ai)
        api_key = _gemini_key_or_exit()

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
        jsonout.put(
            "ai",
            {"prompt": prompt, "selected": [p.relative_to(project_dir).as_posix() for p in selected_paths]},
        )

    # ── Create ZIP ───────────────────────────────────────────────────────────
    if output:
        output_path = Path(output).resolve()
    else:
        output_path = fetched.zip_path if fetched is not None else None

    try:
        result = create_zip(
            resolve_result=resolved,
            project_dir=project_dir,
            output_path=output_path,
            console=console,
            mode=run_mode,
            prompt_txt=prompt_txt,
            redact_secrets_enabled=project_cfg.limits.redact_secrets,
            ecosystem=detection.display_name,
            guide=project_cfg.zip_guide,
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
    jsonout.put("zip", jsonout.package_data(result, project_dir))

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
        skipped_in_zip=result.skipped_in_zip,
    )
    print_report_hint(report_path)
    jsonout.put("report", str(report_path))

    # ── Clipboard ────────────────────────────────────────────────────────────
    jsonout.put("clipboard", None)
    if not no_clipboard:
        cb = clipboard_handle(result.zip_path)
        print_clipboard_result(cb)
        jsonout.put("clipboard", jsonout.clipboard_data(cb))

    if fetched is not None:
        info("This is a copy of the repo at that commit. To apply the AI's changes, package from a clone and run contextzip apply-zip there.")

    # ── Auto-cleanup ─────────────────────────────────────────────────────────
    _auto_cleanup(project_dir, project_cfg)
