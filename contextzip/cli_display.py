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

from contextzip import jsonout
from contextzip.clipboard import Tier
from contextzip.tokens import estimate_tokens, format_tokens

console = Console()


# ---------------------------------------------------------------------------
# Core log primitives — every command builds its output from these three.
# Under --json they're collected into the result instead (see jsonout.py).
# ---------------------------------------------------------------------------


def _collected(level: str, text: str, detail: str | None = None) -> bool:
    session = jsonout.active()
    if session is None:
        return False
    session.record(level, text, detail)
    return True


def ok(text: str, detail: str | None = None, *, con: Console = console) -> None:
    """A completed step: green check, plain text, optional dim detail."""
    if _collected("ok", text, detail):
        return
    line = f"[green]✓[/] {text}"
    if detail:
        line += f"  [dim]· {detail}[/]"
    con.print(line)


def warn(text: str, *, con: Console = console) -> None:
    """A non-fatal issue worth a glance."""
    if _collected("warn", text):
        return
    con.print(f"[yellow]![/] {text}")


def err(text: str, *, con: Console = console) -> None:
    """A fatal problem — command is about to exit non-zero."""
    if _collected("error", text):
        return
    con.print(f"[red]✗[/] {text}")


def status(text: str, *, con: Console = console):
    """
    A spinner while something runs. Under --json it's never seen, so it's
    skipped: its refresh thread is also something Pyodide can't start.
    """
    if jsonout.active() is not None:
        import contextlib

        return contextlib.nullcontext()
    return con.status(text, spinner="dots")


def info(text: str, *, con: Console = console) -> None:
    """A quiet, dim aside — not a step, just context."""
    if _collected("info", text):
        return
    con.print(f"  [dim]{text}[/]")


# ---------------------------------------------------------------------------
# Ecosystem detection
# ---------------------------------------------------------------------------


def print_root_notice(project_root, *, here: bool = False, packaging: bool = False, con: Console = console) -> None:
    """
    One dim line when run from a subfolder, so it's never a surprise that
    contextzip works from the project root — plus a warning if an older
    contextzip left a stray workspace in this subfolder.
    """
    submodule = getattr(project_root, "submodule", None)
    if submodule is not None and submodule != project_root.root:
        try:
            rel = submodule.relative_to(project_root.root).as_posix()
        except ValueError:
            rel = submodule.name
        info(f"You're in the submodule {rel}/ — working from the whole project; --here covers just the submodule", con=con)
    if not project_root.in_subdir:
        return
    import os

    sub = project_root.subdir
    up = os.path.relpath(project_root.root, project_root.cwd).replace("\\", "/")
    if packaging:
        scope = f"only {sub}/ (--here)" if here else f"the whole project — add --here for just {sub}/"
        info(f"Working from the project root ({up}) · you're in {sub}/ · packaging {scope}", con=con)
    else:
        info(f"Working from the project root ({up}) · you're in {sub}/", con=con)

    stray = project_root.cwd / ".contextzip"
    if stray.is_dir() and stray.resolve() != (project_root.root / ".contextzip").resolve():
        from contextzip.packager import _resolve_workspace_location

        location, _ = _resolve_workspace_location(project_root.root)
        if location != "cwd":
            warn(
                f"Found an old workspace at {sub}/.contextzip/ — contextzip now keeps one "
                f"workspace at {project_root.root.name}/.contextzip/. The old one is safe to delete.",
                con=con,
            )


def print_detection(detection, *, con: Console = console) -> None:
    """One line: what was detected and how confident contextzip is."""
    if detection.is_unknown:
        ok("Detected", "unknown ecosystem, base rules only", con=con)
        info(
            "Don't see your language? Request it: https://www.contextzipcli.com/request-language",
            con=con,
        )
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
    binary = set(resolved.binary_files)
    text_bytes = sum(p.stat().st_size for p in resolved.included if p not in binary and p.exists())
    tokens = format_tokens(estimate_tokens(text_bytes))
    ok(label, f"{detail}, {tokens}" if detail else tokens, con=con)

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


def _is_secret(path: Path, project_dir: Path) -> bool:
    from contextzip.redact import is_sensitive_path

    try:
        return is_sensitive_path(path.relative_to(project_dir))
    except ValueError:
        return False


def print_file_warnings(
    resolved,
    project_dir: Path,
    *,
    large_file_warn_bytes: int = 1024 * 1024,
    con: Console = console,
) -> None:
    """One line per warning category — no file listings inline (see report)."""
    explicit = getattr(resolved, "explicitly_included", None)
    if explicit:
        n = len(explicit)
        secret = sum(1 for p in explicit if _is_secret(p, project_dir))
        detail = (
            f" — {secret} secret file{'s' if secret != 1 else ''}, values will be redacted"
            if secret
            else ""
        )
        warn(
            f"{n} normally excluded file{'s' if n != 1 else ''} included "
            f"because you named {'them' if n != 1 else 'it'}{detail}",
            con=con,
        )

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
    snapshot = getattr(result, "snapshot", None)
    detail = f"{result.zip_path}  · snapshot {snapshot_text(snapshot)}" if snapshot else str(result.zip_path)
    ok("Saved to", detail, con=con)


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
        f"{human_size(result.compressed_bytes)}, {size_detail}, "
        f"{format_tokens(result.estimated_tokens)}"
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


def print_redaction_summary(result, *, con: Console = console) -> None:
    """
    A one-line heads-up when limits.redact_secrets caught something —
    same "surface it, don't hide it" treatment as large-file warnings.
    Silent when nothing was redacted, including when the setting is off.
    """
    if not result.redacted:
        return
    n = len(result.redacted)
    total_hits = sum(len(names) for _, names in result.redacted)
    warn(
        f"Redacted {total_hits} secret-shaped value(s) in {n} file{'s' if n != 1 else ''} "
        "before zipping — see report",
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
    method: str = "gemini",
    con: Console = console,
) -> None:
    """One line: how many files were picked, by what, and for what task."""
    n = len(selected_paths)
    files = f'{n} file{"s" if n != 1 else ""}'
    label = f"Gemini selected {files}" if method == "gemini" else f"Selected {files} by keyword match"
    ok(label, f'for "{prompt}"', con=con)
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
    """One line when the ZIP is on the clipboard; otherwise say why it isn't."""
    if cb.tier == Tier.FILE_ON_CLIPBOARD:
        ok("Ready to paste", con=con)
        return
    if cb.tier == Tier.FOLDER_OPENED:
        warn("Couldn't copy the ZIP to the clipboard — opened its folder instead", con=con)
    else:
        info(cb.message, con=con)
    if cb.hint:
        info(cb.hint, con=con)


# ---------------------------------------------------------------------------
# apply-zip
# ---------------------------------------------------------------------------

_APPLY_STATUS_STYLE = {
    "new": ("green", "+"),
    "modified": ("cyan", "~"),
    "merged": ("cyan", "≈"),
    "unchanged": ("dim", "="),
    "conflict": ("red", "!"),
    "drifted": ("yellow", "!"),
    "deleted": ("yellow", "-"),
    "untracked": ("yellow", "?"),
    "partial": ("yellow", "…"),
    "ambiguous": ("yellow", "?"),
    "moved": ("cyan", "→"),
    "delete-requested": ("yellow", "−"),
}

_SAFE_ORDER = ("modified", "merged", "moved", "new", "unchanged")
_RISKY_ORDER = ("conflict", "partial", "drifted", "deleted", "untracked", "ambiguous", "delete-requested")


def snapshot_text(label: str | None) -> str:
    """"14-k3f9a2" -> "#14 (k3f9a2)"."""
    if not label:
        return ""
    number, _, token = label.partition("-")
    return f"#{number} ({token})" if token else f"#{number}"


def _short(path: Path, project_dir: Path) -> str:
    try:
        return path.resolve().relative_to(project_dir).as_posix()
    except ValueError:
        return str(path)


def print_apply_plan(
    plan,
    project_dir: Path,
    *,
    verbose: bool = False,
    con: Console = console,
) -> None:
    """Where the zip came from, what it was compared with, and what each file will do."""
    from collections import Counter

    ok("Resolved", _short(plan.zip_path, project_dir), con=con)

    if plan.has_manifest:
        snapshot = getattr(plan, "snapshot", None)
        if snapshot:
            how = "named in the reply's CONTEXTZIP.md" if plan.matched_by_guide else "best match by content"
            ok(f"Compared with snapshot {snapshot_text(snapshot)}", how, con=con)
        else:
            ok("Compared with what you sent", _short(plan.manifest_path, project_dir), con=con)
        if plan.manifest_note:
            info(plan.manifest_note, con=con)
    else:
        warn("No snapshot of what you sent was found — files that already exist need a look", con=con)

    if plan.rule is not None:
        info(f"Paths in the zip were remapped: {plan.rule.describe()}", con=con)
    moved = [e for e in plan.entries if e.moved and e.how in ("suffix", "content")]
    if moved:
        info(f"{len(moved)} file{'s' if len(moved) != 1 else ''} matched by name, not exact path — see the list below", con=con)

    if plan.structure_warning:
        warn(plan.structure_warning, con=con)

    counts = Counter(e.status.value for e in plan.entries)
    parts = [f"{counts[k]} {k}" for k in _SAFE_ORDER if counts.get(k)]
    ok(f"Classified {len(plan.entries)} files", ", ".join(parts) or None, con=con)

    risky = [f"{counts[k]} {k}" for k in _RISKY_ORDER if counts.get(k)]
    if risky:
        warn(", ".join(risky) + " — needs your call", con=con)

    def line(e) -> None:
        colour, sym = _APPLY_STATUS_STYLE[e.status.value]
        extra = []
        if e.moved and e.how in ("suffix", "content"):
            extra.append(f"matched {e.source_name} by {'content' if e.how == 'content' else 'name'}")
        if e.note:
            extra.append(e.note)
        tail = f"  [dim]— {'; '.join(extra)}[/]" if extra else ""
        con.print(f"  [{colour}]{sym}[/] {e.display_path}{tail}")

    if verbose:
        for e in plan.entries:
            line(e)
    else:
        shown = plan.risky_entries[:12] + [
            e for e in plan.entries if e.is_safe and (e.moved or e.status.value in ("merged", "moved"))
        ][:8]
        for e in shown:
            line(e)
        hidden = len(plan.risky_entries) - 12
        if hidden > 0:
            info(f"… and {hidden} more — see the report, or use --verbose", con=con)

    if plan.ignored:
        info(f"Ignored {len(plan.ignored)} entr{'ies' if len(plan.ignored) != 1 else 'y'} (contextzip's own files, OS junk)", con=con)


def print_apply_result(result, *, con: Console = console) -> None:
    """What was written, skipped, and where the zip went."""
    ok(f"Applied {len(result.written)} file{'s' if len(result.written) != 1 else ''}", con=con)
    removed = getattr(result, "removed", [])
    if removed:
        ok(f"Removed {len(removed)} old file{'s' if len(removed) != 1 else ''}", ", ".join(removed[:5]), con=con)
    for path, reason in getattr(result, "not_removed", [])[:5]:
        warn(f"Kept {path} — {reason}", con=con)
    if result.skipped:
        warn(f"Skipped {len(result.skipped)} for now: {', '.join(result.skipped[:5])}{' …' if len(result.skipped) > 5 else ''}", con=con)
    if result.zip_kept:
        info("The zip stays in the inbox — run contextzip apply-zip again once you've decided on the rest.", con=con)
    else:
        ok("Archived zip to", str(result.applied_zip_path), con=con)


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


def print_auto_cleanup(result, *, con: Console = console) -> None:
    """
    One quiet, dim line after an automatic cleanup pass — only shown when
    something was actually removed, so a workspace with nothing stale
    produces zero extra output. Failures (rare — a locked file, etc.) get
    a short warning; they never interrupt or roll back the command that
    was actually run.
    """
    if result.removed:
        info(f"Cleaned up {len(result.removed)} old item(s) in .contextzip/ · freed {human_size(result.freed_bytes)}", con=con)

    if result.failed:
        n = len(result.failed)
        warn(f"{n} old file{'s' if n != 1 else ''} in .contextzip/ couldn't be auto-cleaned", con=con)
        for path, reason in result.failed[:5]:
            con.print(f"  [yellow]![/] {path}  [dim]{reason}[/]")


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
