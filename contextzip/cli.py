"""
cli.py — contextzip entry point and command routing.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

import click
from rich.console import Console
from rich.markup import escape

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
    make_spec,
    resolve_files,
    resolve_files_from_git,
)
from contextzip.git import get_changed_files, GitError
from contextzip.packager import create_zip, create_zip_silent
from contextzip.clipboard import handle as clipboard_handle
from contextzip.project_config import (
    load_project_config,
    has_legacy_project_config,
    is_known_ai_provider,
)

from contextzip.cli_display import (
    status,
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
    print_most_tokens,
    print_fit,
    literal,
    print_token_breakdown,
    print_package_summary,
    plain_output,
    fancy,
    fail,
    prog_name,
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
from contextzip import models as models_mod
from contextzip import tokens as tokens_mod
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


def _budget_option(_ctx, _param, value: str | None) -> int | None:
    """--max-tokens: "150k" -> 150000 (see tokens.parse_budget)."""
    return None if value is None else tokens_mod.parse_budget(value)


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
                "Package a GitHub, GitLab or Bitbucket repository instead of the local "
                "project, without cloning it: github.com/user/repo, gitlab.com/group/project, "
                "bitbucket.org/team/repo, a link to a branch's folder, or gh:/gl:/bb:user/repo. "
                "Shortcut: contextzip github.com/user/repo. Private repos use GITHUB_TOKEN "
                "(or your gh login), GITLAB_TOKEN or BITBUCKET_TOKEN."
            ),
        ),
        click.option(
            "--ref",
            default=None,
            metavar="REF",
            help="Branch, tag or commit to package with --from (default: the repo's default branch).",
        ),
        click.option(
            "--max-tokens",
            "max_tokens",
            default=None,
            metavar="N",
            callback=_budget_option,
            help=(
                "A token budget: 150000, 150k or 1.5m. Over it, nothing is written "
                "(a dry run just reports it). Overrides tokens.max in the project config."
            ),
        ),
        click.option(
            "--trim",
            is_flag=True,
            default=False,
            help=(
                "With a budget: leave out the least important files until the ZIP fits. "
                "Generated files go first, then data, tests and docs. Never root files or files you named."
            ),
        ),
        click.option(
            "--for",
            "fit_for",
            multiple=True,
            metavar="MODEL",
            help="Show whether the ZIP fits this model: claude, chatgpt or gemini. Repeatable. Default: all.",
        ),
        click.option(
            "--plain",
            is_flag=True,
            default=False,
            help="One line per step, no bars or colour blocks. This is also what you get when the output is piped.",
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

    def main(self, *args, **kwargs):
        _printable_streams()
        try:
            try:
                return super().main(*args, **kwargs)
            except OSError:
                # `cz -v | head`: the reader went away. Click exits quietly on
                # EPIPE; Windows reports the same thing as EINVAL instead.
                import sys

                if os.name != "nt" or not _stdout_closed():
                    raise
                try:
                    sys.stdout = open(os.devnull, "w")  # nothing left to flush at exit
                except OSError:
                    pass
                sys.exit(1)
        except (click.exceptions.ClickException, click.exceptions.Abort, click.exceptions.Exit):
            raise  # only reaches here with standalone_mode=False; click's to handle
        except Exception as exc:  # noqa: BLE001 — a bug gets a message, not a traceback
            if os.environ.get("CONTEXTZIP_DEBUG", "").strip():
                raise
            _report_unexpected(exc)
            raise SystemExit(1)


ISSUES_URL = "https://github.com/akadeepesh/contextzip/issues/new/choose"


def _report_unexpected(exc: BaseException) -> None:
    """What a crash prints instead of a traceback (CONTEXTZIP_DEBUG=1 shows the traceback)."""
    try:
        message = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
        err(literal(f"contextzip hit an unexpected error: {message}"))
        info(f"This is a bug. Please report it: {ISSUES_URL}")
        info("Run again with CONTEXTZIP_DEBUG=1 to see the full traceback.")
    except Exception:  # noqa: BLE001 — never let the report itself crash
        import sys

        sys.stderr.write(f"contextzip hit an unexpected error: {exc!r}\n")


def _stdout_closed() -> bool:
    import sys

    try:
        sys.stdout.write("")
        sys.stdout.flush()
    except (OSError, ValueError):
        return True
    return False


def _printable_streams() -> None:
    """
    Make sure "✓", "→" and "≈" can be printed. On Windows before Python
    3.15, output that isn't a real console (Git Bash, a pipe, a CI log) is
    encoded with the ANSI code page, which has no check mark, so the first
    line of output raised UnicodeEncodeError. There the stream switches to
    UTF-8; anywhere else a character the terminal can't show becomes "?".
    """
    import sys

    for stream in (sys.stdout, sys.stderr):
        encoding = getattr(stream, "encoding", None)
        if not encoding:
            continue
        try:
            "✓→≈…·↓".encode(encoding)
            continue
        except (UnicodeEncodeError, LookupError):
            pass
        try:
            if os.name == "nt":
                stream.reconfigure(encoding="utf-8", errors="replace")
            else:
                stream.reconfigure(errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


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
    fit_for: tuple[str, ...],
    max_tokens: int | None,
    trim: bool,
    plain: bool,
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
        fit_for=list(fit_for),
        max_tokens=max_tokens,
        trim=trim,
        plain=plain,
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
    fit_for: tuple[str, ...],
    max_tokens: int | None,
    trim: bool,
    plain: bool,
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
        fit_for=list(fit_for),
        max_tokens=max_tokens,
        trim=trim,
        plain=plain,
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
    fit_for: tuple[str, ...],
    max_tokens: int | None,
    trim: bool,
    plain: bool,
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
        fit_for=list(fit_for),
        max_tokens=max_tokens,
        trim=trim,
        plain=plain,
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
@click.option(
    "--plain",
    is_flag=True,
    default=False,
    help="One line per step, no table. This is also what you get when the output is piped.",
)
@click.option(
    "--pr",
    "as_pr",
    is_flag=True,
    default=False,
    help=(
        "The reply is for a GitHub repository you packaged with `contextzip github.com/user/repo`: "
        "open a pull request with its changes instead of writing files here. No clone needed; "
        "forks the repository if you can't push to it. Needs GITHUB_TOKEN or your gh login."
    ),
)
@click.option(
    "--from",
    "pr_repo",
    default=None,
    metavar="REPO",
    help="With --pr: the repository the reply is for, when its CONTEXTZIP.md doesn't say.",
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
    plain: bool,
    as_pr: bool,
    pr_repo: str | None,
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
      contextzip apply-zip reply.zip --pr   # a packaged GitHub repo: open a pull request

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
    if pr_repo and not as_pr:
        raise click.UsageError("--from goes with --pr: apply-zip --pr --from github.com/user/repo")
    with _json_session("apply-zip", json_output), plain_output(plain):
        if as_pr:
            _apply_as_pull_request(
                zip_path,
                repo=pr_repo,
                dry_run=dry_run,
                verbose=verbose,
                yes=yes,
                remove_moved=remove_moved,
                allow_delete=allow_delete,
            )
            return
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


def _apply_as_pull_request(
    zip_path: str | None,
    *,
    repo: str | None,
    dry_run: bool,
    verbose: bool,
    yes: bool,
    remove_moved: bool,
    allow_delete: bool,
) -> None:
    """apply-zip --pr: the reply's changes as a pull request (see pullrequest.py)."""
    from contextzip import pullrequest as pr_mod

    console.print()
    if not zip_path:
        err("Give the reply's ZIP: contextzip apply-zip reply.zip --pr")
        raise SystemExit(1)
    zip_file = Path(zip_path).expanduser().resolve()
    if not zip_file.is_file():
        err(f"No such file: {zip_path}")
        raise SystemExit(1)

    try:
        cached = pr_mod.find_cached_repo(zip_file, repo)
    except remote_mod.RemoteError as exc:
        jsonout.put("error_kind", exc.kind)
        err(str(exc))
        raise SystemExit(1)
    how = "named in the reply's CONTEXTZIP.md" if cached.snapshot is not None else "the most recently packaged commit"
    ok(f"Reply for {cached.spec.slug}@{cached.sha[:7]}", how)
    jsonout.put("repository", {"host": cached.spec.host, "repo": cached.spec.slug, "sha": cached.sha, "ref": cached.ref})

    try:
        with status("[cyan]Matching files and comparing versions…[/]"):
            plan = build_plan(zip_file, cached.project_dir, cached.snapshot.manifest_path if cached.snapshot else None)
        if cached.snapshot is not None:
            plan.snapshot = plan.snapshot or cached.snapshot.label
            plan.matched_by_guide = True
    except ApplyError as exc:
        fail(str(exc), fix=f"{prog_name()} apply-zip PATH/TO/reply.zip --pr")
        raise SystemExit(1)

    try:
        print_apply_plan(plan, cached.project_dir, verbose=verbose)
        jsonout.put("plan", jsonout.plan_data(plan))
        jsonout.put("pull_request", None)
        changes, left_out = pr_mod.changes_from_plan(plan, remove_moved=remove_moved, allow_delete=allow_delete)
        for path, why in left_out:
            warn(f"Not included: {path} ({why})")
        if not changes:
            info("Nothing to put in a pull request: the reply has no changes that can be applied safely.")
            return
        title, body = pr_mod.pr_text(cached, plan, changes, left_out)
        n = len(changes)
        summary = {"title": title, "files": [c.path for c in changes], "left_out": [p for p, _ in left_out]}
        if dry_run:
            jsonout.put("pull_request", {"dry_run": True, **summary})
            info(f"Dry run — would open a pull request on {cached.spec.slug} with {n} change{'s' if n != 1 else ''}.")
            return
        if cached.spec.host != remote_mod.HOST:
            err(f"Pull requests are only supported on GitHub for now, not {cached.spec.host_name}.")
            raise SystemExit(1)
        token = remote_mod.github_token()
        if not token:
            err("Opening a pull request needs a GitHub token: set GITHUB_TOKEN, or sign in with `gh auth login`.")
            raise SystemExit(1)
        if not yes:
            if not _interactive():
                err("Opening a pull request publishes these changes. Add --yes to confirm.")
                raise SystemExit(1)
            if not click.confirm(f"  Open a pull request on {cached.spec.slug} with {n} change{'s' if n != 1 else ''}?", default=True):
                info("Cancelled — nothing was sent to GitHub.")
                return

        branch = f"contextzip/{plan.snapshot or cached.sha[:7]}"
        try:
            with status(f"[cyan]Opening a pull request on {cached.spec.slug}…[/]"):
                pr = pr_mod.open_pull_request(
                    cached.spec, cached.sha, cached.ref, changes, title=title, body=body, branch=branch, token=token
                )
        except remote_mod.RemoteError as exc:
            jsonout.put("error_kind", exc.kind)
            err(str(exc))
            raise SystemExit(1)
        ok(f"Opened pull request #{pr.number}", pr.url)
        if pr.forked:
            info(f"From your fork {pr.repo}, since you can't push to {cached.spec.slug}.")
        jsonout.put(
            "pull_request",
            {**summary, "url": pr.url, "number": pr.number, "branch": pr.branch, "pushed_to": pr.repo, "forked": pr.forked},
        )
    finally:
        discard_plan(plan)


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
        message = str(exc)
        # "No zip found…" already says how to pass one.
        fail(message, fix=None if "apply-zip <path>" in message else f"{prog_name()} apply-zip PATH/TO/reply.zip")
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
        with status("[cyan]Matching files and comparing versions…[/]"):
            plan = build_plan(resolved_zip, project_dir, explicit_manifest)
    except ApplyError as exc:
        fail(str(exc), fix=f"{prog_name()} apply-zip PATH/TO/reply.zip")
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

    with status("[cyan]Detecting project ecosystem…[/]"):
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
        with status("[cyan]Detecting project ecosystem…[/]"):
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
        state = data.get("status", "pending")
        mode = {"standard": "codebase", "git-changes": "changes", "prompt": "prompt"}.get(data.get("mode", ""), data.get("mode", "?"))
        age = _ago(now - snaps.created_ts(s))
        session = jsonout.active()
        if session is not None:
            session.data["snapshots"].append(
                {"id": s.id, "token": s.token, "label": s.label, "mode": mode, "files": files,
                 "status": state, "created": snaps.created_ts(s)}
            )
        colour = "green" if state == "applied" else "dim"
        console.print(
            f"  [bold]#{s.id:<4}[/] [dim]{s.token}[/]  {mode:<9} {files:>5} files  {age:>12}   [{colour}]{state}[/]"
        )
    console.print()


@main.command("tokens")
@click.argument("paths", nargs=-1, metavar="[PATH…]")
@click.option("--top", default=15, show_default=True, type=click.IntRange(min=1), help="How many of the largest files to list.")
@click.option("--depth", default=1, show_default=True, type=click.IntRange(min=1), help="Group folders this many levels deep.")
@click.option(
    "--for",
    "fit_for",
    multiple=True,
    metavar="MODEL",
    help="Show whether it fits this model: claude, chatgpt or gemini. Repeatable. Default: all.",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    default=False,
    help="Print one JSON object instead of terminal output, for editors and scripts.",
)
def cmd_tokens(paths: tuple[str, ...], top: int, depth: int, fit_for: tuple[str, ...], json_output: bool) -> None:
    """
    Show where the tokens are: per folder, the largest files, and whether
    it fits each model. Writes nothing.

    \b
    EXAMPLES
      contextzip tokens
      contextzip tokens src/ --depth 2 --top 5
      contextzip tokens --for claude --json

    PATH arguments scope it exactly like contextzip include PATH.
    """
    with _json_session("tokens", json_output):
        _tokens(list(paths), top=top, depth=depth, fit_for=list(fit_for))


def _tokens(paths: list[str], *, top: int, depth: int, fit_for: list[str]) -> None:
    console.print()
    scan = _scan(extra_exclude=None, include_only=paths or None, fit_for=fit_for, quiet=True)
    if scan is None:  # only --git-changes returns None, and it isn't an option here
        return
    if not scan.resolved.included:
        err("Nothing to count — all files were excluded.")
        info("Check the paths, or try contextzip tokens without them.")
        raise SystemExit(1)

    report = _count_tokens(scan.resolved, scan.project_dir, create_cache=False)
    fit_rows = jsonout.fit_data(report, scan.fit_targets)
    jsonout.put("tokens", jsonout.tokens_data(report, depth=depth, top=top, max_dirs=None))
    jsonout.put("fit", fit_rows)

    print_token_breakdown(report, depth=depth, top=top)
    if scan.project_cfg.tokens.show_fit:
        print_fit(fit_rows)


@main.command("stacks")
@click.argument("name", required=False, metavar="[STACK]")
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    default=False,
    help="Print one JSON object instead of terminal output, for editors and scripts.",
)
def cmd_stacks(name: str | None, json_output: bool) -> None:
    """
    List the languages and frameworks contextzip recognises.

    \b
    For each one: what marks a project as that stack, and how many
    patterns it adds to what's left out. Name a stack to see every
    pattern:

    \b
      contextzip stacks
      contextzip stacks laravel
    """
    with _json_session("stacks", json_output):
        _list_stacks(name)


def _list_stacks(name: str | None) -> None:
    from contextzip import stacks as stacks_mod

    described = [stacks_mod.describe(s) for s in stacks_mod.STACKS]
    console.print()

    if name:
        wanted = name.strip().lower()
        match = [d for d in described if wanted in (d["name"].lower(), d["slug"])]
        if not match:
            close = [d["name"] for d in described if wanted in d["name"].lower() or wanted in d["slug"]]
            err(f"contextzip doesn't have a stack called {name!r}.")
            if close:
                info(f"Did you mean: {', '.join(close)}?")
            info("Run contextzip stacks to see them all, or request one: https://www.contextzipcli.com/request-language")
            raise SystemExit(1)
        d = match[0]
        jsonout.put("stack", d)
        ok(d["name"], _stack_signs(d) or None)
        info(f"Leaves out {len(d['excludes'])} patterns, on top of the universal rules:")
        for pattern in d["excludes"]:
            console.print(f"    [dim]{escape(pattern)}[/]")
        console.print()
        return

    jsonout.put("stacks", described)
    ok(f"{len(described)} stacks", "run contextzip stacks NAME to see what one leaves out")
    from rich.table import Table

    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold", no_wrap=True, min_width=max(len(d["name"]) for d in described) + 2)
    table.add_column(style="dim", no_wrap=True, overflow="ellipsis")
    for d in described:
        table.add_row("  " + d["name"], escape(_stack_signs(d, limit=3)))
    console.print(table)
    console.print()
    info("Don't see yours? Request it: https://www.contextzipcli.com/request-language")
    console.print()


def _stack_signs(described: dict, *, limit: int | None = None) -> str:
    """What gives a stack away, as one line: 'artisan, or laravel/framework in composer.json'."""
    # next.config.js, next.config.ts, next.config.mjs  ->  next.config.*
    signs: list[str] = []
    stems: dict[str, int] = {}
    for marker in described["markers"]:
        stem = marker.rsplit(".", 1)[0] if "." in marker.lstrip(".*") else marker
        stems[stem] = stems.get(stem, 0) + 1
    for marker in described["markers"]:
        stem = marker.rsplit(".", 1)[0] if "." in marker.lstrip(".*") else marker
        label = f"{stem}.*" if stems[stem] > 1 else marker
        if label not in signs:
            signs.append(label)
    if limit is not None and len(signs) > limit:
        signs = [*signs[:limit], f"+{len(signs) - limit} more"]
    if described["dependencies"]:
        deps = described["dependencies"]
        if limit is not None and len(deps) > 2:
            deps = [*deps[:2], "…"]
        where = " or ".join(described["manifests"][:2]) or "its manifest"
        signs.append(f"{' or '.join(deps)} in {where}")
    if not signs:
        signs = [f"{ext} files" for ext in described["extensions"]]
    return ", ".join(signs)


def _ago(seconds: float) -> str:
    seconds = max(0, int(seconds))
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= size:
            n = seconds // size
            return f"{n} {unit}{'s' if n != 1 else ''} ago"
    return "just now"


@main.command("mcp")
def cmd_mcp() -> None:
    """
    Run contextzip as an MCP server (stdio), for AI agents and editors.

    
    Needs the optional extra, on Python 3.10 or newer:
      pip install "contextzip[mcp]"

    
    Then register it in your MCP client, for example:
      {"mcpServers": {"contextzip": {"command": "contextzip", "args": ["mcp"]}}}

    
    Tools: package_project, package_repository, preview_apply,
    apply_changes (safe changes by default), open_pull_request (a dry run
    by default) and list_snapshots. Each returns the same JSON as --json.
    """
    import sys

    try:
        from contextzip.mcp_server import serve

        import mcp.server.mcpserver  # noqa: F401  (fail here, with a clear message, if it's missing)
    except ImportError:
        console_err = Console(stderr=True)
        if sys.version_info < (3, 10):
            console_err.print("[red]✗[/] The MCP server needs Python 3.10 or newer (this is " + sys.version.split()[0] + ").")
        else:
            console_err.print('[red]✗[/] The MCP server is an optional extra. Install it with: pip install "contextzip[mcp]"')
        raise SystemExit(1)
    serve()


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
    binary = updater_mod.binary_install()
    if binary is not None:
        import sys

        ok("This is a standalone build of contextzip", f"installed with {binary.manager}")
        if binary.manager == "a download":
            info(f"To run it from any terminal, add this folder to your PATH: {Path(sys.executable).parent}")
        else:
            info(f"{binary.manager} puts contextzip on your PATH. If a new terminal still can't find it, reinstall it.")
        console.print()
        return
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
        fail(f"Couldn't update {target}.", why=str(exc), fix="python -m contextzip works without PATH")
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
      install came from pip, pipx, uv tool or a uv-made venv, tries the
      matching installer first and falls back to the others (pip ↔ uv,
      pip --user) until the new version actually lands — always upgrading
      the interpreter that's currently running, never a possibly-different
      "pip" found on PATH.

      A standalone build (from npm, Homebrew or Scoop) is updated by the
      tool that installed it; this prints the command to run.
    """
    binary = updater_mod.binary_install()
    manual = binary.update_command if binary else updater_mod.manual_update_command()

    console.print()
    with status("[cyan]Checking for updates…[/]"):
        check = updater_mod.check_latest_version()

    if check.latest is None:
        err(f"Could not check for updates: {check.error}")
        info(f"You're on {check.current}. Try again later, or update manually:")
        info(f"  {manual}")
        console.print()
        raise SystemExit(1)

    if not check.update_available:
        ok(f"You're up to date — contextzip {check.current}")
        console.print()
        return

    info(f"Update available: {check.current} → {check.latest}")

    if binary is not None:
        info(f"This contextzip was installed with {binary.manager}. To update, run:")
        console.print(f"    {binary.update_command}", highlight=False, soft_wrap=True)
        console.print()
        return

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

    with status(f"[cyan]Updating to {check.latest}…[/]"):
        result = updater_mod.run_update(target=check.latest)

    console.print()
    if result.ok:
        ok(f"Updated to contextzip {check.latest}", f"via {result.method}")
        if len(result.attempts) > 1:
            tried = ", ".join(a.step.method for a in result.attempts[:-1])
            info(f"({tried} didn't work here, so contextzip fell back to {result.method}.)")
        info("Restart any running contextzip/watch sessions to pick it up.")
        console.print()
        return

    if not result.attempts:
        err("Couldn't find pip or uv for this Python to run the update with")
    else:
        err("Update failed — tried " + ", then ".join(a.step.method for a in result.attempts))
        for attempt in result.attempts:
            console.print(f"  [bold]{attempt.step.method}[/] [dim]$ {attempt.step.display}[/]", highlight=False, soft_wrap=True)
            tail = attempt.output.splitlines()[-4:]
            for line in tail:
                console.print(f"    [dim]{escape(line)}[/]", highlight=False, soft_wrap=True)
    if result.hint:
        info(result.hint)
    info("To update by hand, run:")
    console.print(f"    {manual}", highlight=False, soft_wrap=True)
    console.print()
    raise SystemExit(1)


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


def _run(*, json_output: bool = False, plain: bool = False, **kwargs) -> None:
    """
    All packaging work lives in _package. The main command and every
    subcommand delegate here after collecting their arguments/flags,
    keeping the CLI surface thin and the logic testable in isolation.
    """
    with _json_session("package", json_output), plain_output(plain):
        _package(**kwargs)



def _fetch_remote(target: str, ref: str | None):
    """Fetch a GitHub repository into the cache (see remote.py), or exit with why not."""
    from contextzip.cli_display import human_size

    try:
        spec = remote_mod.parse_target(target)
        with status(f"[cyan]Fetching {spec.slug} from {spec.host_name}…[/]"):
            fetched = remote_mod.fetch(spec, ref)
    except remote_mod.RemoteError as exc:
        jsonout.put("error_kind", exc.kind)
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
        fail(
            "No Gemini API key set.",
            why="Set GEMINI_API_KEY, or run contextzip --prompt once in a terminal to set one up.",
            fix="contextzip config --reset-key",
        )
        raise SystemExit(1)
    api_key = onboard_api_key()
    if not api_key:
        raise SystemExit(0)
    return api_key


def _count_tokens(resolved, project_dir: Path, *, create_cache: bool):
    """
    Count the tokens in *resolved*'s included files (see tokens.py).

    Counts are cached in the project's .contextzip/cache/ — but a run that
    writes nothing there (a dry run, --output, a remote repository) only
    uses the cache when the workspace already exists; it never creates
    .contextzip/ just for the cache.
    """
    from contextzip.packager import _workspace_dir

    cache_dir = None
    try:
        workspace, _is_git = _workspace_dir(project_dir)
        if create_cache or workspace.is_dir():
            cache_dir = workspace / "cache"
    except (OSError, ValueError):
        cache_dir = None  # no cache is slower, never wrong
    with status("[cyan]Counting tokens…[/]"):
        return tokens_mod.count_files(
            resolved.included, project_dir, binary=resolved.binary_files, cache_dir=cache_dir
        )


def _protected_from_trim(scan: _Scan) -> set[str]:
    """Files --trim must keep: the ones the user named, and the config's always_include."""
    from contextzip.filters import _any_parent_excluded

    project_dir = scan.project_dir
    named = {p.strip("/") for p in scan.include_only or []}
    protected: set[str] = set()
    for p in getattr(scan.resolved, "explicitly_included", []):
        try:
            protected.add(p.relative_to(project_dir).as_posix())
        except ValueError:
            pass
    for p in scan.resolved.included:
        try:
            rel = p.relative_to(project_dir)
        except ValueError:
            continue
        rel_str = rel.as_posix()
        if rel_str in named:
            protected.add(rel_str)
        elif scan.force_include is not None and (
            scan.force_include.match_file(rel_str) or _any_parent_excluded(rel, scan.force_include)
        ):
            protected.add(rel_str)
    return protected


def _trim_if_asked(scan: _Scan, report, *, trim: bool):
    """
    With --trim and a report over budget, leave files out of scan.resolved
    until it fits (see tokens.trim_to_budget). Returns (report, trimmed,
    trim_failed): the report for what's left, [(path, tokens)] in drop
    order, and whether --trim couldn't get under the budget.
    """
    budget = scan.budget
    if not trim or budget is None or report.total <= budget:
        return report, [], False
    drop = tokens_mod.trim_to_budget(report, budget, protected=_protected_from_trim(scan))
    if not drop:
        return report, [], True

    resolved, project_dir = scan.resolved, scan.project_dir
    dropped = set(drop)

    def rel(p: Path) -> str:
        try:
            return p.relative_to(project_dir).as_posix()
        except ValueError:
            return p.name

    gone = [p for p in resolved.included if rel(p) in dropped]
    gone_set = set(gone)
    resolved.included = [p for p in resolved.included if p not in gone_set]
    resolved.excluded = [*resolved.excluded, *gone]
    resolved.large_files = [(p, n) for p, n in resolved.large_files if p not in gone_set]
    resolved.explicitly_included = [p for p in resolved.explicitly_included if p not in gone_set]
    trimmed = [(r, report.by_file[r]) for r in drop]
    return report.subset(rel(p) for p in resolved.included), trimmed, False


def _put_trimmed(scan: _Scan, report, trimmed, *, verbose: bool, git_mode: bool) -> None:
    """Refresh the JSON result after --trim changed what goes in."""
    jsonout.put("trimmed", [{"path": rel, "tokens": n} for rel, n in trimmed])
    jsonout.put(
        "files",
        jsonout.files_data(scan.resolved, scan.project_dir, verbose=verbose, git_mode=git_mode, tokens=report),
    )
    jsonout.put("fit", jsonout.fit_data(report, scan.fit_targets))


def _print_trimmed(trimmed, budget: int, *, verbose: bool, dry_run: bool) -> None:
    count = len(trimmed)
    total = sum(n for _rel, n in trimmed)
    files = "file" if count == 1 else "files"
    where = "" if dry_run else " — see report"
    warn(f"Trimmed {count} {files} ({total:,} tokens) to fit the {budget:,} budget{where}")
    if verbose:
        for rel, n in trimmed:
            info(literal(f"  trimmed {rel}  {n:,}"))


def _print_over_budget(report, budget: int, *, dry_run: bool, trim_failed: bool) -> None:
    """
    Say how far over the budget the files are, and where the tokens are.
    Exits 1 unless it's a dry run, which only reports it.
    """
    message = f"{report.label()} is over the {budget:,} budget by {report.total - budget:,}."
    if dry_run:
        warn(message)
    else:
        err(message)
    rows = [*report.by_dir(1)[:5], *report.top_files(5)]
    width = max([16, *(len(path) for path, _n in rows)])
    for path, n in rows:
        info(literal(f"  {path:<{width}} {n:>10,}"))
    if trim_failed:
        info("--trim could not get under the budget without dropping files you asked for.")
    else:
        info("Leave some out with contextzip exclude PATH, or add --trim to drop the least important files.")
    if not dry_run:
        raise SystemExit(1)


def _narrow_tokens(report, resolved, project_dir: Path):
    """The token report for resolved.included after --prompt narrowed it to the AI's selection."""
    keys = []
    for p in resolved.included:
        try:
            keys.append(p.relative_to(project_dir).as_posix())
        except ValueError:
            keys.append(p.name)
    if all(k in report.by_file for k in keys):
        return report.subset(keys)
    # The selection named a file the scan didn't count: count it fresh.
    return tokens_mod.count_files(resolved.included, project_dir, binary=resolved.binary_files)


@dataclass
class _Scan:
    """What _scan found: where the project is, its settings, and the files to package."""

    project_root: ProjectRoot
    fetched: object | None
    project_cfg: object
    detection: object
    resolved: object
    scan_label: str
    scan_detail: str | None
    include_only: list[str] | None
    force_include: object | None
    fit_targets: list
    budget: int | None

    @property
    def project_dir(self) -> Path:
        return self.project_root.root


def _scan(
    *,
    extra_exclude: list[str] | None,
    include_only: list[str] | None,
    prompt: str | None = None,
    output: str | None = None,
    no_gitignore: bool = False,
    git_changes: bool = False,
    verbose: bool = False,
    here: bool = False,
    remote: str | None = None,
    ref: str | None = None,
    fit_for: list[str] | None = None,
    max_tokens: int | None = None,
    trim: bool = False,
    quiet: bool = False,
    show_detection: bool = True,
) -> _Scan | None:
    """
    Find the project (or fetch the remote one), load its config, detect the
    stack and resolve which files go in: everything `contextzip` does before
    counting tokens. Shared by the package commands and `contextzip tokens`.

    *quiet* skips the progress lines (root notice, detection, git details)
    and the first-run config UI offer; *show_detection* False skips only the
    detection line, which the designed summary shows in its header. Returns None when --git-changes finds
    nothing to package. Exits on a bad flag or a failed fetch.
    """
    # ── Where to work: this project, or a GitHub repository ─────────────────
    fetched = None
    if remote:
        if git_changes:
            fail(
                "--git-changes needs a local checkout — it can't be combined with a remote repository.",
                fix="clone it, then run contextzip --git-changes in the clone",
            )
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
            fail(
                "--ref picks a branch of a remote repository — use it with --from github.com/user/repo.",
                fix="contextzip --from github.com/user/repo --ref BRANCH",
            )
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
        if not quiet:
            print_root_notice(project_root, here=here, packaging=True)

    # ── Project config (.contextzip/config.json) ────────────────────────────
    project_cfg = load_project_config(project_dir)
    if has_legacy_project_config(project_dir):
        warn(
            "Found the deprecated .contextzip.json — move it to .contextzip/config.json"
        )

    # Checked before any scanning, so a mistake in a flag fails fast.
    try:
        fit_targets = models_mod.resolve(fit_for or None, project_cfg.tokens.windows)
    except ValueError as exc:
        err(str(exc))
        raise SystemExit(1)
    budget = max_tokens if max_tokens is not None else project_cfg.tokens.max
    if trim and budget is None:
        err("--trim needs a budget: add --max-tokens N.")
        raise SystemExit(1)

    # ── Detection ────────────────────────────────────────────────────────────
    with status("[cyan]Detecting project ecosystem…[/]"):
        detection = detect(project_dir)

    if not quiet and show_detection:
        print_detection(detection)
    jsonout.put("detection", jsonout.detection_data(detection))

    # ── First-run offer: visual config UI ────────────────────────────────────
    if project_cfg.source_path is None and fetched is None and not quiet:
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
    # What was typed on the command line outranks the project config, so an
    # --exclude also beats the config's always_include.
    command_exclude = make_spec(normalized_exclude) if normalized_exclude else None
    normalized_exclude += [normalize_pattern(p) for p in project_cfg.always_exclude]
    force_include = build_force_include_spec(
        [normalize_pattern(p) for p in project_cfg.always_include]
    )

    # ── Git-changes mode ─────────────────────────────────────────────────────
    if git_changes:
        with status("[cyan]Querying git for changed files…[/]"):
            git_result = get_changed_files(project_dir)

        if isinstance(git_result, GitError):
            fail("Git error: couldn't list the changed files.", why=git_result.message)
            raise SystemExit(1)

        if not quiet:
            print_git_deleted_and_submodules(git_result)

        if git_result.is_empty:
            info("Nothing to compress in this one — working tree is clean.")
            return None

        scan_label, scan_detail = git_scan_label_and_detail(git_result)
        if verbose and not quiet:
            print_git_verbose_files(git_result)

        with status("[cyan]Checking git-changed files…[/]"):
            resolved = resolve_files_from_git(
                git_files=git_result.files,
                project_dir=project_dir,
                large_file_warn_bytes=int(
                    project_cfg.limits.max_file_size_mb * 1024 * 1024
                ),
                extra_exclude=normalized_exclude if normalized_exclude else None,
                include_only=include_only if include_only else None,
                force_include=force_include,
                command_exclude=command_exclude,
            )

    else:
        # ── Build exclusion spec ─────────────────────────────────────────────
        gitignore_path = None if no_gitignore else (project_dir / ".gitignore")

        with status("[cyan]Building exclusion rules…[/]"):
            spec = build_spec(
                rule_modules=detection.rule_modules,
                extra_exclude=normalized_exclude if normalized_exclude else None,
                gitignore_path=gitignore_path,
            )

        # ── Resolve files ─────────────────────────────────────────────────────
        with status("[cyan]Scanning project files…[/]"):
            resolved = resolve_files(
                project_dir=project_dir,
                spec=spec,
                include_only=include_only if include_only else None,
                force_include=force_include,
                large_file_warn_bytes=int(
                    project_cfg.limits.max_file_size_mb * 1024 * 1024
                ),
                command_exclude=command_exclude,
            )

        # Snapshot the scan summary now — --prompt reassigns resolved.included
        # below, and the merged "Scanned & Packed" line still needs to report
        # the original, pre-selection counts.
        scan_label, scan_detail = scan_label_and_detail(resolved, git_mode=False)

    return _Scan(
        project_root=project_root,
        fetched=fetched,
        project_cfg=project_cfg,
        detection=detection,
        resolved=resolved,
        scan_label=scan_label,
        scan_detail=scan_detail,
        include_only=include_only,
        force_include=force_include,
        fit_targets=fit_targets,
        budget=budget,
    )


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
    fit_for: list[str] | None = None,
    max_tokens: int | None = None,
    trim: bool = False,
) -> None:
    console.print()
    use_fancy = fancy()
    run_mode = "git-changes" if git_changes else ("prompt" if prompt else "standard")
    jsonout.put("mode", run_mode)
    jsonout.put("dry_run", dry_run)
    jsonout.put("zip", None)

    scan = _scan(
        extra_exclude=extra_exclude,
        include_only=include_only,
        prompt=prompt,
        output=output,
        no_gitignore=no_gitignore,
        git_changes=git_changes,
        verbose=verbose,
        here=here,
        remote=remote,
        ref=ref,
        fit_for=fit_for,
        max_tokens=max_tokens,
        trim=trim,
        show_detection=not use_fancy,
    )
    if scan is None:
        return
    project_root, project_dir, fetched = scan.project_root, scan.project_dir, scan.fetched
    project_cfg, detection, resolved = scan.project_cfg, scan.detection, scan.resolved
    scan_label, scan_detail = scan.scan_label, scan.scan_detail
    fit_targets = scan.fit_targets

    large_file_warn_bytes = int(project_cfg.limits.max_file_size_mb * 1024 * 1024)
    summary_name = fetched.label if fetched is not None else project_dir.name

    # ── Tokens ───────────────────────────────────────────────────────────────
    # Counted once, for every file the scan included. --prompt narrows the
    # report to the AI's selection below without counting again.
    token_report = _count_tokens(
        resolved,
        project_dir,
        create_cache=not dry_run and not output and fetched is None,
    )
    jsonout.put(
        "files",
        jsonout.files_data(resolved, project_dir, verbose=verbose, git_mode=git_changes, tokens=token_report),
    )
    jsonout.put("fit", jsonout.fit_data(token_report, fit_targets))

    # ── Token budget ─────────────────────────────────────────────────────────
    # --prompt is checked after the AI has picked the files, below.
    trimmed: list[tuple[str, int]] = []
    over_budget = False
    trim_failed = False
    if scan.budget is not None and not prompt:
        token_report, trimmed, trim_failed = _trim_if_asked(scan, token_report, trim=trim)
        over_budget = token_report.total > scan.budget
        if trimmed:
            if not git_changes:
                scan_label, scan_detail = scan_label_and_detail(resolved, git_mode=False)
            _put_trimmed(scan, token_report, trimmed, verbose=verbose, git_mode=git_changes)
        jsonout.put("over_budget", over_budget)

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
        elif use_fancy:

            def dry_run_warnings() -> None:
                if trimmed:
                    _print_trimmed(trimmed, scan.budget, verbose=verbose, dry_run=True)
                if over_budget:
                    _print_over_budget(token_report, scan.budget, dry_run=True, trim_failed=trim_failed)
                print_file_warnings(resolved, project_dir, large_file_warn_bytes=large_file_warn_bytes)

            print_package_summary(
                name=summary_name,
                detection=detection,
                resolved=resolved,
                project_dir=project_dir,
                tokens=token_report,
                fit_rows=jsonout.fit_data(token_report, fit_targets) if project_cfg.tokens.show_fit else None,
                git_mode=git_changes,
                verbose=verbose,
                warnings=dry_run_warnings,
            )
        else:
            print_scan_summary(resolved, project_dir, verbose, git_mode=git_changes, tokens=token_report)
            if project_cfg.tokens.show_fit:
                print_fit(jsonout.fit_data(token_report, fit_targets))
            if trimmed:
                _print_trimmed(trimmed, scan.budget, verbose=verbose, dry_run=True)
            if over_budget:
                _print_over_budget(token_report, scan.budget, dry_run=True, trim_failed=trim_failed)
            print_file_warnings(
                resolved, project_dir, large_file_warn_bytes=large_file_warn_bytes
            )
            info("Dry run — no ZIP created. Remove --dry-run to produce the archive.")
        return

    if not resolved.included:
        if not resolved.excluded and not resolved.skipped:
            fail(
                "No files found to package.",
                why=f"There are no files in {project_dir}.",
                fix="cd into your project folder, then run contextzip again",
            )
        else:
            fail("Nothing to compress in this one — all files were excluded.", fix="contextzip include PATH")
        return

    if over_budget:
        ok(scan_label, f"{scan_detail}, {token_report.label()}" if scan_detail else token_report.label())
        _print_over_budget(token_report, scan.budget, dry_run=False, trim_failed=trim_failed)

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
        token_report = _narrow_tokens(token_report, resolved, project_dir)
        jsonout.put("fit", jsonout.fit_data(token_report, fit_targets))
        if scan.budget is not None:
            token_report, trimmed, trim_failed = _trim_if_asked(scan, token_report, trim=trim)
            over_budget = token_report.total > scan.budget
            jsonout.put("over_budget", over_budget)
            if trimmed:
                jsonout.put("trimmed", [{"path": rel, "tokens": n} for rel, n in trimmed])
                jsonout.put("fit", jsonout.fit_data(token_report, fit_targets))
            if over_budget:
                _print_over_budget(token_report, scan.budget, dry_run=False, trim_failed=trim_failed)
        jsonout.put(
            "ai",
            {"prompt": prompt, "selected": [p.relative_to(project_dir).as_posix() for p in selected_paths]},
        )

    # ── Create ZIP ───────────────────────────────────────────────────────────
    if output:
        output_path = Path(output).resolve()
    else:
        output_path = fetched.zip_path if fetched is not None else None

    zip_options = dict(
        resolve_result=resolved,
        project_dir=project_dir,
        output_path=output_path,
        mode=run_mode,
        prompt_txt=prompt_txt,
        redact_secrets_enabled=project_cfg.limits.redact_secrets,
        ecosystem=detection.display_name,
        guide=project_cfg.zip_guide,
        tokens=token_report,
    )
    try:
        if jsonout.active() is not None:
            result = create_zip_silent(**zip_options)  # no progress bar to draw
        else:
            result = create_zip(console=console, **zip_options)
    except Exception as exc:
        fail("Failed to create ZIP.", why=str(exc))
        raise SystemExit(1)

    if use_fancy:
        _finish_fancy(
            scan,
            result,
            token_report,
            name=summary_name,
            run_mode=run_mode,
            prompt=prompt,
            trimmed=trimmed,
            verbose=verbose,
            no_clipboard=no_clipboard,
        )
        return

    print_scan_and_pack(scan_label, scan_detail, result, tokens=token_report)
    if verbose:
        print_most_tokens(token_report)
    if project_cfg.tokens.show_fit:
        print_fit(jsonout.fit_data(token_report, fit_targets))
    if trimmed:
        _print_trimmed(trimmed, scan.budget, verbose=verbose, dry_run=False)
    print_file_warnings(
        resolved, project_dir, large_file_warn_bytes=large_file_warn_bytes
    )
    print_zip_write_warnings(result)
    print_redaction_summary(result)
    print_package_result(result)
    jsonout.put("zip", jsonout.package_data(result, project_dir, verbose=verbose))

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
        tokens=token_report,
        trimmed=trimmed,
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


def _finish_fancy(
    scan: _Scan,
    result,
    token_report,
    *,
    name: str,
    run_mode: str,
    prompt: str | None,
    trimmed: list[tuple[str, int]],
    verbose: bool,
    no_clipboard: bool,
) -> None:
    """
    The end of a package run in a terminal: the same report, clipboard and
    cleanup as the plain path, shown as one designed summary.
    """
    project_dir, resolved, project_cfg = scan.project_dir, scan.resolved, scan.project_cfg
    large_file_warn_bytes = int(project_cfg.limits.max_file_size_mb * 1024 * 1024)

    report_path = write_scan_report(
        zip_path=result.zip_path,
        project_dir=project_dir,
        detection=scan.detection,
        resolved=resolved,
        mode=run_mode,
        ai_prompt=prompt,
        ai_selected=resolved.included if prompt else None,
        large_file_warn_bytes=large_file_warn_bytes,
        redacted=result.redacted,
        skipped_in_zip=result.skipped_in_zip,
        tokens=token_report,
        trimmed=trimmed,
    )

    cb = None
    if not no_clipboard:
        with status("[cyan]Copying to the clipboard…[/]"):
            cb = clipboard_handle(result.zip_path)

    def package_warnings() -> None:
        if trimmed:
            _print_trimmed(trimmed, scan.budget, verbose=verbose, dry_run=False)
        print_file_warnings(resolved, project_dir, large_file_warn_bytes=large_file_warn_bytes)
        print_zip_write_warnings(result)
        print_redaction_summary(result)

    # The report is mentioned only when something points to it.
    see_report = verbose or bool(trimmed or result.skipped_in_zip or result.redacted)
    print_package_summary(
        name=name,
        detection=scan.detection,
        resolved=resolved,
        project_dir=project_dir,
        tokens=token_report,
        fit_rows=jsonout.fit_data(token_report, scan.fit_targets) if project_cfg.tokens.show_fit else None,
        result=result,
        git_mode=run_mode == "git-changes",
        verbose=verbose,
        warnings=package_warnings,
        clipboard=cb,
        hint=None if scan.fetched is not None else f"When the AI replies, run: {prog_name()} apply-zip",
        report_path=report_path if see_report else None,
    )
    if scan.fetched is not None:
        info("This is a copy of the repo at that commit. To apply the AI's changes, package from a clone and run contextzip apply-zip there.")

    _auto_cleanup(project_dir, project_cfg)
