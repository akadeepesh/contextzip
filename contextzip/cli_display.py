"""
cli_display.py — minimal, one-line-per-step terminal output for contextzip.

Design: every command prints a short, scannable log — one line per step,
a checkmark, a dim detail. No boxed panels for routine runs; even the
final "saved to" line is just another line. The only thing that visually
differs from a plain checkmark line is a warning (`!`) or an error
(`✗`), which is exactly the point: quiet by default, and something
actually stands out when it matters.

Full detail that used to be printed here (excluded-directory
breakdowns, per-file listings) is now written to a report file by
report.py and only echoed inline when --verbose is passed.

Imported by cli.py and watcher.py; nothing here imports from either
(no circular deps).
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console

from contextzip.clipboard import Tier

console = Console()


# ---------------------------------------------------------------------------
# Core log primitives — every command builds its output from these three.
# ---------------------------------------------------------------------------


def ok(text: str, detail: str | None = None, *, con: Console = console) -> None:
    """A completed step: green check, plain text, optional dim detail."""
    line = f"[green]✓[/] {text}"
    if detail:
        line += f"  [dim]· {detail}[/]"
    con.print(line)


def warn(text: str, *, con: Console = console) -> None:
    """A non-fatal issue worth a glance."""
    con.print(f"[yellow]![/] {text}")


def err(text: str, *, con: Console = console) -> None:
    """A fatal problem — command is about to exit non-zero."""
    con.print(f"[red]✗[/] {text}")


def info(text: str, *, con: Console = console) -> None:
    """A quiet, dim aside — not a step, just context."""
    con.print(f"  [dim]{text}[/]")


# ---------------------------------------------------------------------------
# Ecosystem detection
# ---------------------------------------------------------------------------


def print_detection(detection, *, con: Console = console) -> None:
    """One line: what was detected and how confident contextzip is."""
    if detection.is_unknown:
        ok("Detected", "unknown ecosystem, base rules only", con=con)
        return

    parts = []
    for name in detection.ecosystems:
        src = detection.sources.get(name)
        if src and src != ".":
            parts.append(f"{name} ({src}/)")
        else:
            parts.append(name)
    ecosystem_str = " + ".join(parts)
    ok(f"Detected {ecosystem_str}", f"{detection.confidence} confidence", con=con)


# ---------------------------------------------------------------------------
# Git changes
# ---------------------------------------------------------------------------


def print_git_deleted_and_submodules(changes, *, con: Console = console) -> None:
    """Any deleted files or submodules git reported — shown regardless of packaging outcome."""
    if changes.deleted:
        info(f"{len(changes.deleted)} deleted file(s) skipped", con=con)
    if changes.submodules:
        info(f"{len(changes.submodules)} submodule(s) skipped", con=con)


def git_scan_label_and_detail(changes) -> tuple[str, str | None]:
    """Build the (label, detail) pair used by print_scan_and_pack for git-changes mode."""
    bits = []
    if changes.staged:
        bits.append(f"{len(changes.staged)} staged")
    if changes.unstaged:
        bits.append(f"{len(changes.unstaged)} unstaged")
    if changes.untracked:
        bits.append(f"{len(changes.untracked)} untracked")
    detail = ", ".join(bits) if bits else None
    return f"Found {len(changes.files)} changed files", detail


def print_git_verbose_files(changes, *, con: Console = console) -> None:
    """Per-file listing for -v — kept separate from the summary line."""
    for category, paths, colour in (
        ("staged", changes.staged, "green"),
        ("unstaged", changes.unstaged, "yellow"),
        ("untracked", changes.untracked, "cyan"),
    ):
        for rel in paths:
            con.print(f"  [{colour}]·[/] [dim]{rel}[/] [dim]({category})[/]")


# ---------------------------------------------------------------------------
# File scan summary
# ---------------------------------------------------------------------------


def scan_label_and_detail(resolved, git_mode: bool = False) -> tuple[str, str | None]:
    """Build the (label, detail) pair used by print_scan_and_pack for the standard scan."""
    total = len(resolved.included) + len(resolved.excluded)
    included_size = sum(p.stat().st_size for p in resolved.included if p.exists())

    if git_mode:
        return f"Checked {len(resolved.included)} files", human_size(included_size)

    detail = (
        f"{len(resolved.included)} included ({human_size(included_size)}), "
        f"{len(resolved.excluded)} excluded"
    )
    return f"Scanned {total + len(resolved.skipped)} files", detail


def print_scan_summary(
    resolved,
    project_dir: Path,
    verbose: bool,
    *,
    git_mode: bool = False,
    con: Console = console,
) -> None:
    """Scan-only line, used when no packaging happens (dry runs)."""
    label, detail = scan_label_and_detail(resolved, git_mode=git_mode)
    ok(label, detail, con=con)

    if resolved.skipped and not verbose:
        info(f"{len(resolved.skipped)} file(s) skipped — see report", con=con)

    if verbose and resolved.included:
        for p in sorted(resolved.included):
            size_str = human_size(p.stat().st_size) if p.exists() else "?"
            rel = p.relative_to(project_dir).as_posix()
            con.print(f"  [green]·[/] {rel}  [dim]{size_str}[/]")


# ---------------------------------------------------------------------------
# File warnings (large / binary / skipped)
# ---------------------------------------------------------------------------


def print_file_warnings(
    resolved,
    project_dir: Path,
    *,
    large_file_warn_bytes: int = 1024 * 1024,
    con: Console = console,
) -> None:
    """One line per warning category — no file listings inline (see report)."""
    if resolved.large_files:
        n = len(resolved.large_files)
        warn(
            f"{n} large file{'s' if n != 1 else ''} "
            f"(≥ {human_size(large_file_warn_bytes)}) will be included",
            con=con,
        )

    if resolved.binary_files:
        n = len(resolved.binary_files)
        warn(
            f"{n} binary file{'s' if n != 1 else ''} detected — "
            "AI tools may not read them",
            con=con,
        )

    if resolved.skipped:
        n = len(resolved.skipped)
        warn(
            f"{n} file{'s' if n != 1 else ''} skipped (unreadable or dangling symlink)",
            con=con,
        )


# ---------------------------------------------------------------------------
# Package result
# ---------------------------------------------------------------------------


def print_package_result(result, *, con: Console = console) -> None:
    """Where the ZIP landed. (Scan + pack counts are shown by print_scan_and_pack.)"""
    ok("Saved to", str(result.zip_path), con=con)


def print_scan_and_pack(
    label: str,
    scan_detail: str | None,
    result,
    *,
    con: Console = console,
) -> None:
    """
    One combined line covering both the scan and the packaging outcome,
    e.g. "Scanned 1518 files & Packed 42 files · 42 included (394 KB),
    1476 excluded, 394 KB → 126 KB, ↓68% smaller".
    """
    size_detail = (
        "zip overhead on tiny project"
        if result.grew
        else f"↓{result.compression_pct} smaller"
    )
    pack_detail = (
        f"{human_size(result.uncompressed_bytes)} → "
        f"{human_size(result.compressed_bytes)}, {size_detail}"
    )
    detail = f"{scan_detail}, {pack_detail}" if scan_detail else pack_detail
    ok(f"{label} & Packed {result.file_count} files", detail, con=con)


def print_zip_write_warnings(result, *, con: Console = console) -> None:
    if result.skipped_in_zip:
        n = len(result.skipped_in_zip)
        warn(
            f"{n} file{'s' if n != 1 else ''} could not be written to the ZIP — "
            "see report",
            con=con,
        )


def print_report_hint(report_path, *, con: Console = console) -> None:
    """Dim pointer to the full report, printed once at the end of a run."""
    if report_path:
        info(f"Full report: {report_path}", con=con)


# ---------------------------------------------------------------------------
# AI selection
# ---------------------------------------------------------------------------


def print_ai_selection(
    selected_paths: list[Path],
    project_dir: Path,
    prompt: str,
    *,
    verbose: bool = False,
    con: Console = console,
) -> None:
    """One line: how many files Gemini picked and for what task."""
    n = len(selected_paths)
    ok(f'Gemini selected {n} file{"s" if n != 1 else ""}', f'for "{prompt}"', con=con)
    info("prompt.txt will be included in the ZIP", con=con)

    if verbose:
        for p in sorted(selected_paths):
            try:
                rel = p.relative_to(project_dir).as_posix()
                size = human_size(p.stat().st_size)
            except (ValueError, OSError):
                rel, size = str(p), "?"
            con.print(f"  [cyan]·[/] {rel}  [dim]{size}[/]")


# ---------------------------------------------------------------------------
# Clipboard result
# ---------------------------------------------------------------------------


def print_clipboard_result(cb, *, con: Console = console) -> None:
    """One line reflecting whichever clipboard tier fired — folder-opened is silent."""
    if cb.tier == Tier.FILE_ON_CLIPBOARD:
        ok("Ready to paste", con=con)
    elif cb.tier == Tier.FOLDER_OPENED:
        pass  # opening the folder is a convenience, not worth a log line
    else:
        info(cb.message, con=con)


# ---------------------------------------------------------------------------
# apply-zip
# ---------------------------------------------------------------------------

_APPLY_STATUS_STYLE = {
    "new": ("green", "+"),
    "modified": ("cyan", "~"),
    "unchanged": ("dim", "="),
    "drifted": ("yellow", "!"),
    "untracked": ("yellow", "?"),
}


def print_apply_plan(
    plan,
    project_dir: Path,
    *,
    verbose: bool = False,
    con: Console = console,
) -> None:
    """A handful of one-liners: source zip, manifest, and a status breakdown."""
    ok("Resolved", str(plan.zip_path), con=con)

    if plan.has_manifest:
        ok("Diffed against manifest", str(plan.manifest_path), con=con)
    else:
        warn("No manifest found — every existing path is treated as untracked", con=con)

    if plan.wrapper_note:
        info(plan.wrapper_note, con=con)

    if plan.structure_warning:
        warn(plan.structure_warning, con=con)

    from collections import Counter

    counts = Counter(e.status.value for e in plan.entries)
    parts = []
    if counts.get("new"):
        parts.append(f"{counts['new']} new")
    if counts.get("modified"):
        parts.append(f"{counts['modified']} modified")
    if counts.get("unchanged"):
        parts.append(f"{counts['unchanged']} unchanged")
    ok(
        f"Classified {sum(counts.values())} files",
        ", ".join(parts) or None,
        con=con,
    )

    if counts.get("drifted") or counts.get("untracked"):
        risky_parts = []
        if counts.get("drifted"):
            risky_parts.append(f"{counts['drifted']} drifted")
        if counts.get("untracked"):
            risky_parts.append(f"{counts['untracked']} untracked")
        warn(", ".join(risky_parts) + " — needs a closer look", con=con)

    if verbose:
        for e in plan.entries:
            colour, sym = _APPLY_STATUS_STYLE[e.status.value]
            con.print(f"  [{colour}]{sym}[/] {e.rel_path}  [dim]({e.status.value})[/]")
    elif plan.risky_entries:
        for e in plan.risky_entries[:10]:
            colour, sym = _APPLY_STATUS_STYLE[e.status.value]
            reason = (
                "changed or removed locally since this zip was made"
                if e.status.value == "drifted"
                else "no baseline — wasn't part of the original zip"
            )
            con.print(f"  [{colour}]{sym}[/] {e.rel_path}  [dim]— {reason}[/]")
        if len(plan.risky_entries) > 10:
            info(f"… and {len(plan.risky_entries) - 10} more — see report", con=con)


def print_apply_result(result, *, con: Console = console) -> None:
    """Two or three lines: what was written, backed up, and archived."""
    ok(f"Applied {len(result.written)} files", con=con)
    if result.backup_dir:
        ok("Backed up to", str(result.backup_dir), con=con)
    ok("Archived zip to", str(result.applied_zip_path), con=con)


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
