"""
cli.py — contextzip entry point.  Phase 1–4 complete.
"""

from __future__ import annotations

import os
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from contextzip.detector import detect
from contextzip.filters import build_spec, resolve_files, summarise_exclusions
from contextzip.packager import create_zip
from contextzip.clipboard import handle as clipboard_handle, Tier

console = Console()


# ---------------------------------------------------------------------------
# CLI definition
# ---------------------------------------------------------------------------

@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--include", "-i",
    multiple=True,
    metavar="PATH",
    help="Only include files under these paths (relative to project root). "
         "Can be repeated: --include src --include app",
)
@click.option(
    "--exclude", "-e",
    multiple=True,
    metavar="PATTERN",
    help="Extra exclusion patterns on top of auto-rules (gitignore syntax). "
         "Can be repeated: --exclude '*.log' --exclude temp.js",
)
@click.option(
    "--dry-run", "-n",
    is_flag=True,
    default=False,
    help="Show what would be included without creating the ZIP.",
)
@click.option(
    "--output", "-o",
    default=None,
    metavar="FILE",
    help="Path for the output ZIP file. "
         "Defaults to <project_name>_context_<timestamp>.zip in the system temp dir.",
)
@click.option(
    "--no-clipboard",
    is_flag=True,
    default=False,
    help="Skip clipboard / folder-open step after creating the ZIP.",
)
@click.option(
    "--verbose", "-v",
    is_flag=True,
    default=False,
    help="Show every included and excluded file.",
)
@click.version_option(version="0.1.0", prog_name="contextzip")
def main(
    include: tuple[str, ...],
    exclude: tuple[str, ...],
    dry_run: bool,
    output: str | None,
    no_clipboard: bool,
    verbose: bool,
) -> None:
    """
    \b
    contextzip — package your codebase for AI tools.

    Run from your project root to produce a smart, lightweight ZIP
    ready to paste directly into Claude, ChatGPT, or any AI interface.
    """

    project_dir = Path(os.getcwd()).resolve()

    # ── Header ──────────────────────────────────────────────────────────────
    console.print()
    console.print(
        Panel.fit(
            f"[bold cyan]contextzip[/] [dim]v0.1.0[/]\n"
            f"[dim]Project:[/] [white]{project_dir}[/]",
            border_style="cyan",
            padding=(0, 2),
        )
    )
    console.print()

    # ── Detection ───────────────────────────────────────────────────────────
    with console.status("[cyan]Detecting project ecosystem…[/]", spinner="dots"):
        detection = detect(project_dir)

    _print_detection(detection)

    # ── Build exclusion spec ────────────────────────────────────────────────
    with console.status("[cyan]Building exclusion rules…[/]", spinner="dots"):
        spec = build_spec(
            rule_modules=detection.rule_modules,
            extra_exclude=list(exclude) if exclude else None,
        )

    # ── Resolve files ────────────────────────────────────────────────────────
    with console.status("[cyan]Scanning project files…[/]", spinner="dots"):
        included, excluded = resolve_files(
            project_dir=project_dir,
            spec=spec,
            include_only=list(include) if include else None,
        )

    # ── Results ─────────────────────────────────────────────────────────────
    _print_results(included, excluded, project_dir, verbose)

    # ── Dry run — stop here ──────────────────────────────────────────────────
    if dry_run:
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

    if not included:
        console.print(
            "\n[red]Nothing to package.[/] All files were excluded — "
            "try [cyan]--include[/] to override."
        )
        return

    # ── Phase 3: Create ZIP ──────────────────────────────────────────────────
    console.print()
    output_path = Path(output).resolve() if output else None

    try:
        result = create_zip(
            included=included,
            project_dir=project_dir,
            output_path=output_path,
            console=console,
        )
    except Exception as exc:
        console.print(f"\n[red]Failed to create ZIP:[/] {exc}")
        raise SystemExit(1)

    _print_package_result(result)

    # ── Phase 4: Clipboard / folder-open ────────────────────────────────────
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
            "Next.js": "bright_blue",
            "Node.js": "green",
            "Python":  "yellow",
            "Django":  "green",
            "FastAPI": "cyan",
            "Rust":    "red",
            "Go":      "cyan",
            "Ruby":    "red",
        }
        parts = [
            f"[{colours.get(name, 'white')}]{name}[/]"
            for name in detection.ecosystems
        ]
        ecosystem_line = " [dim]+[/] ".join(parts)

    confidence_colour = {"high": "green", "medium": "yellow", "low": "dim"}.get(
        detection.confidence, "dim"
    )
    rules_str = ", ".join(detection.rule_modules)

    console.print(
        Panel(
            f"  [dim]Ecosystem :[/]   {ecosystem_line}\n"
            f"  [dim]Confidence:[/]   [{confidence_colour}]{detection.confidence}[/]\n"
            f"  [dim]Rules     :[/]   [dim]{rules_str}[/]",
            title="[bold]Detection[/]",
            border_style="blue",
            padding=(0, 1),
        )
    )
    console.print()


def _print_results(
    included: list[Path],
    excluded: list[Path],
    project_dir: Path,
    verbose: bool,
) -> None:
    total = len(included) + len(excluded)
    included_size = sum(p.stat().st_size for p in included if p.exists())

    table = Table(box=box.ROUNDED, show_header=False, padding=(0, 2))
    table.add_column(style="dim")
    table.add_column()
    table.add_row("Files scanned",   str(total))
    table.add_row(
        "To be included",
        f"[green]{len(included)}[/]  [dim]({_human_size(included_size)})[/]",
    )
    table.add_row("Excluded",        f"[red]{len(excluded)}[/]")
    console.print(table)

    if verbose and included:
        console.print()
        console.print("[bold]Included files:[/]")
        for p in included:
            console.print(f"  [green]✓[/] {p.relative_to(project_dir).as_posix()}")

    if excluded:
        console.print()
        buckets = summarise_exclusions(excluded, project_dir)
        console.print("[bold]Top excluded directories / files:[/]")
        for label, count in list(buckets.items())[:8]:
            console.print(
                f"  [red]✗[/] [dim]{label}[/]  "
                f"[dim]({count} file{'s' if count != 1 else ''})[/]"
            )


def _print_package_result(result) -> None:
    ratio_colour = "green" if result.compression_ratio >= 0.3 else "yellow"

    if result.grew:
        size_detail = "[dim](ZIP overhead on tiny project)[/]"
    else:
        size_detail = f"[{ratio_colour}](↓ {result.compression_pct} smaller)[/]"

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
            border_style="green",
            padding=(0, 1),
        )
    )


def _print_clipboard_result(cb) -> None:
    """Render clipboard outcome — colour and border vary by tier."""
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
