"""
cli.py — contextzip entry point.  Phases 1–5 complete.
"""

from __future__ import annotations

import os
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from contextzip import __version__
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
# CLI
# ---------------------------------------------------------------------------

@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--include", "-i",
    multiple=True, metavar="PATH",
    help="Only include files under these paths (relative to project root). "
         "Repeatable: --include src --include app",
)
@click.option(
    "--exclude", "-e",
    multiple=True, metavar="PATTERN",
    help="Extra exclusion patterns on top of auto-rules (gitignore syntax). "
         "Repeatable: --exclude '*.log' --exclude temp.js",
)
@click.option(
    "--dry-run", "-n",
    is_flag=True, default=False,
    help="Show what would be included without creating the ZIP.",
)
@click.option(
    "--output", "-o",
    default=None, metavar="FILE",
    help="Output ZIP path. Defaults to <project>_context_<timestamp>.zip in temp dir.",
)
@click.option(
    "--no-clipboard",
    is_flag=True, default=False,
    help="Skip clipboard / folder-open step after creating the ZIP.",
)
@click.option(
    "--no-gitignore",
    is_flag=True, default=False,
    help="Ignore the project's .gitignore file (use only built-in rules).",
)
@click.option(
    "--git-changes",
    is_flag=True, default=False,
    help="Only include files that git reports as modified, added, or untracked. "
         "Requires the project to be inside a git repository.",
)
@click.option(
    "--verbose", "-v",
    is_flag=True, default=False,
    help="Show every included and excluded file.",
)
@click.version_option(version=__version__, prog_name="contextzip")
def main(
    include: tuple[str, ...],
    exclude: tuple[str, ...],
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
    """

    project_dir = Path(os.getcwd()).resolve()

    # ── Header ───────────────────────────────────────────────────────────────
    console.print()
    console.print(
        Panel.fit(
            f"[bold cyan]contextzip[/] [dim]v{__version__}[/]\n"
            f"[dim]Project:[/] [white]{project_dir}[/]",
            border_style="cyan", padding=(0, 2),
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
                    border_style="red", padding=(0, 2),
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
            not no_gitignore
            and gitignore_path is not None
            and gitignore_path.is_file()
        )

        with console.status("[cyan]Building exclusion rules…[/]", spinner="dots"):
            spec = build_spec(
                rule_modules=detection.rule_modules,
                extra_exclude=list(exclude) if exclude else None,
                gitignore_path=gitignore_path,
            )

        if used_gitignore:
            console.print(f"  [dim]↳ .gitignore patterns applied[/]")
            console.print()

        # ── Resolve files ─────────────────────────────────────────────────────
        with console.status("[cyan]Scanning project files…[/]", spinner="dots"):
            resolved = resolve_files(
                project_dir=project_dir,
                spec=spec,
                include_only=list(include) if include else None,
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
            console.print(f"    [yellow]·[/] [dim]{rel}[/]  [yellow]{_human_size(size)}[/]")
        if len(resolved.large_files) > 5:
            console.print(f"    [dim]… and {len(resolved.large_files) - 5} more[/]")
        console.print(
            f"  [dim]  Use --exclude to drop them if unneeded.[/]"
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
        console.print()
        console.print(
            Panel.fit(
                "[yellow]Dry run — no ZIP created.[/]\n"
                "[dim]Remove --dry-run to produce the archive.[/]",
                border_style="yellow", padding=(0, 2),
            )
        )
        return

    if not resolved.included:
        console.print(
            "\n[red]Nothing to package.[/] All files were excluded — "
            "try [cyan]--include[/] to override."
        )
        return

    # ── Create ZIP ───────────────────────────────────────────────────────────
    console.print()
    output_path = Path(output).resolve() if output else None

    try:
        result = create_zip(
            resolve_result=resolved,
            project_dir=project_dir,
            output_path=output_path,
            console=console,
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
# Display helpers
# ---------------------------------------------------------------------------

def _print_detection(detection) -> None:
    if detection.is_unknown:
        ecosystem_line = "[yellow]Unknown[/] — applying base rules only"
    else:
        colours = {
            "Next.js": "bright_blue", "Node.js": "green",
            "Python":  "yellow",      "Django":  "green",
            "FastAPI": "cyan",         "Rust":    "red",
            "Go":      "cyan",         "Ruby":    "red",
        }
        parts = [
            f"[{colours.get(n, 'white')}]{n}[/]"
            for n in detection.ecosystems
        ]
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
            border_style="blue", padding=(0, 1),
        )
    )
    console.print()


def _print_git_summary(changes: GitChanges, project_dir: Path, verbose: bool) -> None:
    """Print a panel summarising the git-changed files."""
    table = Table(box=box.ROUNDED, show_header=False, padding=(0, 2))
    table.add_column(style="dim")
    table.add_column()
    table.add_row("Staged",    f"[green]{len(changes.staged)}[/]")
    table.add_row("Unstaged",  f"[yellow]{len(changes.unstaged)}[/]")
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
            border_style="magenta", padding=(0, 1),
        )
    )
    console.print()

    if verbose and changes.files:
        console.print("[bold]Git-changed files:[/]")
        for category, paths, colour in (
            ("Staged",    changes.staged,    "green"),
            ("Unstaged",  changes.unstaged,  "yellow"),
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
    total         = len(resolved.included) + len(resolved.excluded)
    included_size = sum(p.stat().st_size for p in resolved.included if p.exists())

    table = Table(box=box.ROUNDED, show_header=False, padding=(0, 2))
    table.add_column(style="dim")
    table.add_column()

    if not git_mode:
        table.add_row("Files scanned",   str(total + len(resolved.skipped)))
    table.add_row(
        "To be included",
        f"[green]{len(resolved.included)}[/]  [dim]({_human_size(included_size)})[/]",
    )
    if not git_mode:
        table.add_row("Excluded",        f"[red]{len(resolved.excluded)}[/]")
    if resolved.skipped:
        table.add_row("Skipped",     f"[yellow]{len(resolved.skipped)}[/]")
    console.print(table)

    if verbose and resolved.included:
        console.print()
        console.print("[bold]Included files:[/]")
        for p in resolved.included:
            size_str = _human_size(p.stat().st_size) if p.exists() else "?"
            rel      = p.relative_to(project_dir).as_posix()
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
    size_detail  = (
        "[dim](ZIP overhead on tiny project)[/]"
        if result.grew
        else f"[{ratio_colour}](↓ {result.compression_pct} smaller)[/]"
    )

    table = Table(box=box.ROUNDED, show_header=False, padding=(0, 2))
    table.add_column(style="dim", min_width=18)
    table.add_column()
    table.add_row("Files packed",    f"[green]{result.file_count}[/]")
    table.add_row("Original size",   _human_size(result.uncompressed_bytes))
    table.add_row(
        "Compressed size",
        f"[bold]{_human_size(result.compressed_bytes)}[/]  {size_detail}",
    )
    table.add_row("Saved to",        f"[cyan]{result.zip_path}[/]")

    console.print(
        Panel(
            table,
            title="[bold green]✓ ZIP created[/]",
            border_style="green", padding=(0, 1),
        )
    )


def _print_clipboard_result(cb) -> None:
    tier_style = {
        Tier.FILE_ON_CLIPBOARD: ("green",  "✓ Ready to paste"),
        Tier.FOLDER_OPENED:     ("yellow", "✓ Folder opened"),
        Tier.PATH_ONLY:         ("dim",    "↳ Manual copy needed"),
    }
    border, title = tier_style.get(cb.tier, ("dim", "Clipboard"))
    console.print(
        Panel.fit(
            cb.message,
            title=f"[bold {border}]{title}[/]",
            border_style=border, padding=(0, 2),
        )
    )


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
