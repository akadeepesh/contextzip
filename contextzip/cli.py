"""
cli.py — contextzip entry point.

Phase 1 + 2: scaffold, detection, dry-run, and rich output.
Packaging (Phase 3) and clipboard (Phase 4) are stubbed with clear TODOs.
"""

from __future__ import annotations

import os
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box
from rich.text import Text

from contextzip.detector import detect
from contextzip.filters import build_spec, resolve_files, summarise_exclusions

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
    help="Skip clipboard copy after creating the ZIP.",
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

    # ── TODO Phase 3: Create ZIP ─────────────────────────────────────────────
    console.print()
    console.print(
        Panel.fit(
            "[bold yellow]⚙  Phase 3 coming next[/]\n"
            "[dim]ZIP creation will be wired in the next phase.[/]",
            border_style="yellow",
            padding=(0, 2),
        )
    )

    # ── TODO Phase 4: Clipboard ──────────────────────────────────────────────
    if not no_clipboard:
        pass  # clipboard logic arrives in Phase 4


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def _print_detection(detection) -> None:
    """Render a detection summary panel."""
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
        parts = []
        for name in detection.ecosystems:
            colour = colours.get(name, "white")
            parts.append(f"[{colour}]{name}[/]")
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
    """Render file inclusion/exclusion summary."""

    total = len(included) + len(excluded)
    included_size = sum(p.stat().st_size for p in included if p.exists())

    # Summary table
    table = Table(box=box.ROUNDED, show_header=False, padding=(0, 2))
    table.add_column(style="dim")
    table.add_column()
    table.add_row("Files scanned", str(total))
    table.add_row(
        "To be included",
        f"[green]{len(included)}[/]  [dim]({_human_size(included_size)})[/]",
    )
    table.add_row("Excluded", f"[red]{len(excluded)}[/]")
    console.print(table)

    # Verbose: show every included file
    if verbose and included:
        console.print()
        console.print("[bold]Included files:[/]")
        for p in included:
            rel = p.relative_to(project_dir).as_posix()
            console.print(f"  [green]✓[/] {rel}")

    # Always show top excluded buckets
    if excluded:
        console.print()
        buckets = summarise_exclusions(excluded, project_dir)
        console.print("[bold]Top excluded directories / files:[/]")
        for label, count in list(buckets.items())[:8]:
            console.print(f"  [red]✗[/] [dim]{label}[/]  [dim]({count} file{'s' if count != 1 else ''})[/]")


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
