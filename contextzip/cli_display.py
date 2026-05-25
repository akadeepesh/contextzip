"""
cli_display.py — Rich rendering helpers for contextzip's CLI output.

All functions receive data objects and a Console instance (or use the
module-level console) and return nothing — they are pure display logic
with no business side-effects.

Imported by cli.py; nothing here imports from cli.py (no circular deps).
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from contextzip.filters import summarise_exclusions, LARGE_FILE_WARN_BYTES
from contextzip.clipboard import Tier

console = Console()


# ---------------------------------------------------------------------------
# Ecosystem detection
# ---------------------------------------------------------------------------


def print_detection(detection, *, con: Console = console) -> None:
    """Render the ecosystem detection panel."""
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
    con.print(
        Panel(
            f"  [dim]Ecosystem :[/]   {ecosystem_line}\n"
            f"  [dim]Confidence:[/]   [{conf_colour}]{detection.confidence}[/]\n"
            f"  [dim]Rules     :[/]   [dim]{', '.join(detection.rule_modules)}[/]",
            title="[bold]Detection[/]",
            border_style="blue",
            padding=(0, 1),
        )
    )
    con.print()


# ---------------------------------------------------------------------------
# Git changes
# ---------------------------------------------------------------------------


def print_git_summary(
    changes, project_dir: Path, verbose: bool, *, con: Console = console
) -> None:
    """Render a panel summarising the git-changed file counts."""
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

    con.print(
        Panel(
            table,
            title="[bold]Git Changes[/]",
            border_style="magenta",
            padding=(0, 1),
        )
    )
    con.print()

    if verbose and changes.files:
        con.print("[bold]Git-changed files:[/]")
        for category, paths, colour in (
            ("Staged", changes.staged, "green"),
            ("Unstaged", changes.unstaged, "yellow"),
            ("Untracked", changes.untracked, "cyan"),
        ):
            for rel in paths:
                con.print(f"  [{colour}]✓[/] [dim]{rel}[/]  [dim]({category})[/]")
        con.print()


# ---------------------------------------------------------------------------
# File scan summary
# ---------------------------------------------------------------------------


def print_scan_summary(
    resolved,
    project_dir: Path,
    verbose: bool,
    *,
    git_mode: bool = False,
    con: Console = console,
) -> None:
    """Render file scan counts and, when verbose, the full file list."""
    total = len(resolved.included) + len(resolved.excluded)
    included_size = sum(p.stat().st_size for p in resolved.included if p.exists())

    table = Table(box=box.ROUNDED, show_header=False, padding=(0, 2))
    table.add_column(style="dim")
    table.add_column()

    if not git_mode:
        table.add_row("Files scanned", str(total + len(resolved.skipped)))
    table.add_row(
        "To be included",
        f"[green]{len(resolved.included)}[/]  [dim]({human_size(included_size)})[/]",
    )
    if not git_mode:
        table.add_row("Excluded", f"[red]{len(resolved.excluded)}[/]")
    if resolved.skipped:
        table.add_row("Skipped", f"[yellow]{len(resolved.skipped)}[/]")
    con.print(table)

    if verbose and resolved.included:
        con.print()
        con.print("[bold]Included files:[/]")
        for p in resolved.included:
            size_str = human_size(p.stat().st_size) if p.exists() else "?"
            rel = p.relative_to(project_dir).as_posix()
            con.print(f"  [green]✓[/] {rel}  [dim]{size_str}[/]")

    if not git_mode and resolved.excluded:
        con.print()
        buckets = summarise_exclusions(resolved.excluded, project_dir)
        con.print("[bold]Top excluded directories / files:[/]")
        for label, count in list(buckets.items())[:8]:
            con.print(
                f"  [red]✗[/] [dim]{label}[/]  "
                f"[dim]({count} file{'s' if count != 1 else ''})[/]"
            )


# ---------------------------------------------------------------------------
# File warnings (large / binary / skipped)
# ---------------------------------------------------------------------------


def print_file_warnings(resolved, project_dir: Path, *, con: Console = console) -> None:
    """Render large-file, binary-file, and skipped-file warnings."""
    if resolved.large_files:
        con.print()
        con.print(
            f"  [yellow]⚠[/]  [bold]{len(resolved.large_files)} large file"
            f"{'s' if len(resolved.large_files) != 1 else ''}[/] "
            f"[dim](≥ {human_size(LARGE_FILE_WARN_BYTES)}) will be included:[/]"
        )
        for p, size in resolved.large_files[:5]:
            rel = p.relative_to(project_dir).as_posix()
            con.print(f"    [yellow]·[/] [dim]{rel}[/]  [yellow]{human_size(size)}[/]")
        if len(resolved.large_files) > 5:
            con.print(f"    [dim]… and {len(resolved.large_files) - 5} more[/]")
        con.print(
            "  [dim]  Use [cyan]-e PATTERN[/] or [cyan]contextzip exclude PATTERN[/] "
            "to drop them if unneeded.[/]"
        )

    if resolved.binary_files:
        con.print()
        con.print(
            f"  [yellow]⚠[/]  [bold]{len(resolved.binary_files)} binary file"
            f"{'s' if len(resolved.binary_files) != 1 else ''}[/] "
            f"[dim]detected — AI tools may not read them:[/]"
        )
        for p in resolved.binary_files[:3]:
            rel = p.relative_to(project_dir).as_posix()
            con.print(f"    [yellow]·[/] [dim]{rel}[/]")
        if len(resolved.binary_files) > 3:
            con.print(f"    [dim]… and {len(resolved.binary_files) - 3} more[/]")

    if resolved.skipped:
        con.print()
        con.print(
            f"  [red]⚠[/]  [bold]{len(resolved.skipped)} file"
            f"{'s' if len(resolved.skipped) != 1 else ''}[/] "
            f"[dim]skipped (unreadable or dangling symlink):[/]"
        )
        for p, reason in resolved.skipped[:3]:
            con.print(f"    [red]·[/] [dim]{p.name}[/] — {reason}")


# ---------------------------------------------------------------------------
# Package result
# ---------------------------------------------------------------------------


def print_package_result(result, *, con: Console = console) -> None:
    """Render the ZIP creation summary panel."""
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
    table.add_row("Original size", human_size(result.uncompressed_bytes))
    table.add_row(
        "Compressed size",
        f"[bold]{human_size(result.compressed_bytes)}[/]  {size_detail}",
    )
    table.add_row("Saved to", f"[cyan]{result.zip_path}[/]")

    con.print(
        Panel(
            table,
            title="[bold green]✓ ZIP created[/]",
            border_style="green",
            padding=(0, 1),
        )
    )


def print_zip_write_warnings(result, *, con: Console = console) -> None:
    """Render warnings for files that could not be written into the ZIP."""
    if result.skipped_in_zip:
        con.print()
        con.print(
            f"  [red]⚠[/]  [bold]{len(result.skipped_in_zip)} file"
            f"{'s' if len(result.skipped_in_zip) != 1 else ''}[/] "
            f"[dim]could not be written to ZIP:[/]"
        )
        for p, reason in result.skipped_in_zip[:3]:
            con.print(f"    [red]·[/] [dim]{p.name}[/] — {reason}")


# ---------------------------------------------------------------------------
# AI selection
# ---------------------------------------------------------------------------


def print_ai_selection(
    selected_paths: list[Path],
    project_dir: Path,
    prompt: str,
    *,
    con: Console = console,
) -> None:
    """Render the panel showing which files Gemini selected."""
    table = Table(box=box.ROUNDED, show_header=False, padding=(0, 2))
    table.add_column(style="cyan")
    table.add_column(style="dim")

    for p in selected_paths:
        try:
            rel = p.relative_to(project_dir).as_posix()
            size = human_size(p.stat().st_size)
        except (ValueError, OSError):
            rel = str(p)
            size = "?"
        table.add_row(rel, size)

    con.print(
        Panel(
            table,
            title=f'[bold cyan]AI Selected — [dim]"{prompt}"[/][/]',
            border_style="cyan",
            padding=(0, 1),
        )
    )
    con.print(
        f"  [dim]↳ {len(selected_paths)} file"
        f"{'s' if len(selected_paths) != 1 else ''} selected by Gemini "
        f"· prompt.txt will be included in the ZIP[/]"
    )


# ---------------------------------------------------------------------------
# Clipboard result
# ---------------------------------------------------------------------------


def print_clipboard_result(cb, *, con: Console = console) -> None:
    """Render the clipboard / folder-open result."""
    tier_style = {
        Tier.FILE_ON_CLIPBOARD: ("green", "✓ Ready to paste"),
        Tier.FOLDER_OPENED: ("yellow", "✓ Folder opened"),
        Tier.PATH_ONLY: ("dim", "↳ Manual copy needed"),
    }
    border, title = tier_style.get(cb.tier, ("dim", "Clipboard"))
    con.print(
        Panel.fit(
            cb.message,
            title=f"[bold {border}]{title}[/]",
            border_style=border,
            padding=(0, 2),
        )
    )


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def human_size(n: int) -> str:
    """Format *n* bytes as a human-readable string (B / KB / MB / GB / TB)."""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
