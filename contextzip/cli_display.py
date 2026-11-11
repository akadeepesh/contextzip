"""
cli_display.py — terminal output for contextzip.

Design: every command prints a short, scannable log — one line per step,
a checkmark, a dim detail. No boxed panels for routine runs; even the
final "saved to" line is just another line. The only thing that visually
differs from a plain checkmark line is a warning (`!`) or an error
(`✗`), which is exactly the point: quiet by default, and something
actually stands out when it matters.

Full detail that used to be printed here (excluded-directory
breakdowns, per-file listings) is now written to a report file by
report.py and only echoed inline when --verbose is passed.

Two shapes of output, chosen once per run by fancy(): in a terminal, a
package run ends with a short designed summary (print_package_summary);
piped, in CI, with --plain or under --json, every step stays one plain
line, which is what scripts and logs want.

Imported by cli.py and watcher.py; nothing here imports from either
(no circular deps).
"""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from pathlib import Path

from rich.console import Console
from rich.markup import escape

from contextzip import jsonout
from contextzip.clipboard import Tier
from contextzip.tokens import estimate_tokens, format_tokens

console = Console()


# ---------------------------------------------------------------------------
# Plain or fancy
# ---------------------------------------------------------------------------

_plain_forced = False

# Characters the designed output needs. A stream that can't encode them
# (an old code page that _printable_streams couldn't switch) gets the plain
# lines, which only need what that function already guarantees.
_FANCY_CHARS = "✓█░→·…≈"


@contextmanager
def plain_output(enabled: bool):
    """`--plain`: one line per step for the rest of the command, even in a terminal."""
    global _plain_forced
    previous = _plain_forced
    _plain_forced = previous or enabled
    try:
        yield
    finally:
        _plain_forced = previous


def fancy() -> bool:
    """
    True when output should be the designed blocks: stdout is a terminal,
    not --json and not --plain. Piped, redirected and CI output keeps one
    plain line per step. CONTEXTZIP_FANCY=1 / 0 forces the choice (tests).
    """
    if _plain_forced or jsonout.active() is not None:
        return False
    forced = os.environ.get("CONTEXTZIP_FANCY", "").strip()
    if forced == "1":
        return True
    if forced == "0":
        return False
    stream = sys.stdout
    try:
        if stream is None or not stream.isatty():
            return False
    except (AttributeError, ValueError, OSError):
        return False
    try:
        _FANCY_CHARS.encode(getattr(stream, "encoding", None) or "ascii")
    except (UnicodeEncodeError, LookupError):
        return False
    return True


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


def fail(what: str, *, why: str | None = None, fix: str | None = None, con: Console = console) -> None:
    """
    A fatal problem, in one shape everywhere:

        ✗ <what>
          <why>
          Try: <fix>

    Under --json, *what* is the error and *why* / *fix* its detail. Plain
    text, never markup. It doesn't exit: callers still raise SystemExit.
    """
    detail = " ".join(bit for bit in (why, f"Try: {fix}" if fix else None) if bit) or None
    if _collected("error", what, detail):
        return
    con.print(f"[red]✗[/] {escape(what)}", highlight=False)
    if why:
        con.print(f"  [dim]{escape(why)}[/]", highlight=False)
    if fix:
        con.print(f"  [dim]Try:[/] {escape(fix)}", highlight=False)


def prog_name() -> str:
    """The name the user typed, cz or contextzip, for the commands contextzip suggests."""
    import click

    ctx = click.get_current_context(silent=True)
    name = ctx.find_root().info_name if ctx is not None else None
    stem = os.path.splitext(os.path.basename(name or ""))[0].lower()
    return "cz" if stem == "cz" else "contextzip"


def status(text: str, *, con: Console = console):
    """
    A spinner while something runs. Under --json it's never seen, so it's
    skipped: its refresh thread is also something Pyodide can't start.
    """
    if jsonout.active() is not None:
        import contextlib

        return contextlib.nullcontext()
    return con.status(text, spinner="dots")


def literal(text: str) -> str:
    """
    *text* made safe to pass to ok/warn/err/info when it holds file paths:
    a path like "app/[id]/page.tsx" must not be read as Rich markup.
    Unchanged under --json, which collects plain text.
    """
    if jsonout.active() is not None:
        return text
    return escape(text)


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


def _ecosystem_text(detection) -> str:
    """"Next.js + FastAPI (backend/)": the detected stacks, with the folder each came from."""
    parts = []
    for name in detection.ecosystems:
        src = detection.sources.get(name)
        if src and src != ".":
            parts.append(f"{name} ({src}/)")
        else:
            parts.append(name)
    return " + ".join(parts)


def print_detection(detection, *, con: Console = console) -> None:
    """One line: what was detected and how confident contextzip is."""
    if detection.is_unknown:
        ok("No known stack detected", "universal rules only", con=con)
        info(
            "Don't see your language? Request it: https://www.contextzipcli.com/request-language",
            con=con,
        )
        return

    ecosystem_str = _ecosystem_text(detection)
    if getattr(detection, "by_file_type", None):
        ok(f"Detected {ecosystem_str}", "from file types, no project file found", con=con)
        return
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
            con.print(f"  [{colour}]·[/] [dim]{escape(str(rel))}[/] [dim]({category})[/]")


# ---------------------------------------------------------------------------
# File scan summary
# ---------------------------------------------------------------------------


def scan_label_and_detail(resolved, git_mode: bool = False) -> tuple[str, str | None]:
    """Build the (label, detail) pair used by print_scan_and_pack for the standard scan."""
    total = len(resolved.included) + len(resolved.excluded)
    included_size = sum(resolved.size_of(p) for p in resolved.included)

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
    tokens=None,
    con: Console = console,
) -> None:
    """
    Scan-only line, used when no packaging happens (dry runs). *tokens*, a
    TokenReport for the included files, replaces the byte-based estimate.
    """
    label, detail = scan_label_and_detail(resolved, git_mode=git_mode)
    if tokens is not None:
        token_text = tokens.label()
    else:
        binary = set(resolved.binary_files)
        text_bytes = sum(resolved.size_of(p) for p in resolved.included if p not in binary)
        token_text = format_tokens(estimate_tokens(text_bytes))
    ok(label, f"{detail}, {token_text}" if detail else token_text, con=con)

    if resolved.skipped and not verbose:
        info(f"{len(resolved.skipped)} file(s) skipped — see report", con=con)

    if verbose and resolved.included:
        _print_included_files(resolved, project_dir, con=con)
        if tokens is not None:
            print_most_tokens(tokens, con=con)


def _print_included_files(resolved, project_dir: Path, *, con: Console = console) -> None:
    """For --verbose dry runs: every included file and its size."""
    for p in sorted(resolved.included):
        size_str = human_size(resolved.size_of(p))
        rel = p.relative_to(project_dir).as_posix()
        con.print(f"  [green]·[/] {escape(rel)}  [dim]{size_str}[/]")


def print_most_tokens(tokens, *, n: int = 5, con: Console = console) -> None:
    """For --verbose: the files with the most tokens, on one line."""
    top = [(rel, count) for rel, count in tokens.top_files(n) if count > 0]
    if not top:
        return
    prefix = "" if tokens.exact else "≈ "
    info(literal("Most tokens: " + " · ".join(f"{rel} {prefix}{count:,}" for rel, count in top)), con=con)


def _share_text(share: float) -> str:
    if share < 0.01:
        return "<1%"
    return f"{round(share * 100)}%"


def print_fit(fit_rows: list[dict], *, con: Console = console) -> None:
    """
    One line saying whether the ZIP fits each model's context window:
    "Fits  Claude ≈23%  ·  ChatGPT 36%  ·  Gemini 5%". Estimated shares get
    "≈". When any target is over, the line is a warning plus what to do.
    """
    if not fit_rows:
        return
    parts = []
    too_big = []
    for row in fit_rows:
        text = f"{row['name']} {'' if row['exact'] else '≈'}{_share_text(row['share'])}"
        if not row["fits"]:
            text += ", too big"
            too_big.append(row["name"])
        parts.append(text)
    line = literal("Fits  " + "  ·  ".join(parts))
    if not too_big:
        info(line, con=con)
        return
    warn(line, con=con)
    names = too_big[0] if len(too_big) == 1 else ", ".join(too_big[:-1]) + " and " + too_big[-1]
    info(
        literal(f"Too big for {names}. Narrow it with contextzip include PATH, or see the largest folders with --verbose."),
        con=con,
    )


_BAR_CELLS = 24
_MAX_FOLDER_ROWS = 30


def print_token_breakdown(tokens, *, depth: int = 1, top: int = 15, con: Console = console) -> None:
    """
    `contextzip tokens`: the total, then folders with a bar for their share
    and the largest files. Bars only on a terminal; piped output gets the
    same rows without them.
    """
    n = len(tokens.by_file)
    ok(f"{n} file{'' if n == 1 else 's'}", f"{tokens.label()} ({tokens.method})", con=con)
    if jsonout.active() is not None or not tokens.by_file:
        return

    folders = tokens.by_dir(depth)
    files = tokens.top_files(top)
    shown = folders[:_MAX_FOLDER_ROWS]
    width = min(48, max([16, *(len(path) for path, _ in shown), *(len(path) for path, _ in files)]))
    bars = con.is_terminal
    total = tokens.total

    con.print()
    for path, count in shown:
        share = count / total if total else 0.0
        cells = round(share * _BAR_CELLS)
        bar = f"{'█' * cells}{'░' * (_BAR_CELLS - cells)}  " if bars else ""
        con.print(f"  {escape(f'{path:<{width}}')} {count:>10,}  {bar}{_share_text(share):>4}", highlight=False)
    if len(folders) > len(shown):
        more = len(folders) - len(shown)
        con.print(f"  [dim]and {more} more folder{'' if more == 1 else 's'} (all of them are in --json)[/]")

    if files:
        con.print()
        con.print("  Largest files")
        for path, count in files:
            con.print(f"  {escape(f'{path:<{width}}')} {count:>10,}", highlight=False)
    con.print()


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
    tokens=None,
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
    if tokens is None:
        tokens = getattr(result, "tokens", None)
    token_text = tokens.label() if tokens is not None else format_tokens(result.estimated_tokens)
    pack_detail = (
        f"{human_size(result.uncompressed_bytes)} → "
        f"{human_size(result.compressed_bytes)}, {size_detail}, "
        f"{token_text}"
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
# The designed package summary (fancy() only)
# ---------------------------------------------------------------------------

_SUMMARY_BAR_CELLS = 20
_SUMMARY_FOLDER_ROWS = 4
_SUMMARY_PATH_MAX = 28
_SUMMARY_PATH_MIN = 8
_SUMMARY_BARS_MIN_WIDTH = 60
_SUMMARY_LABEL = 10  # "Left out" plus two spaces: where the Fits and Left out values start


def _cells(text: str) -> int:
    from rich.cells import cell_len

    return cell_len(text)


def _left_truncate(text: str, width: int) -> str:
    """"…components/forms/": the end of a path is the part worth keeping."""
    if _cells(text) <= width:
        return text
    while text and _cells(text) + 1 > width:
        text = text[1:]
    return "…" + text


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _cells(text))


def _print_fitted(con: Console, *variants: str) -> None:
    """
    Print the first markup variant that fits on one line of the console;
    when none does, crop the last one with "…". A designed block never wraps.
    """
    from rich.text import Text

    width = max(con.width, 1)
    text = None
    for markup in variants:
        text = Text.from_markup(markup)
        if text.cell_len <= width:
            con.print(text, no_wrap=True, overflow="ignore")
            return
    if text is not None:
        text.truncate(width, overflow="ellipsis")
        con.print(text, no_wrap=True, overflow="ellipsis")


def _summary_header(name: str, detection) -> tuple[str, str]:
    """The header line, wide and narrow."""
    if detection is None or detection.is_unknown:
        stacks = "no known stack, universal rules"
    else:
        stacks = _ecosystem_text(detection)
        if getattr(detection, "by_file_type", None):
            stacks += " (from file types)"
    title = f"contextzip [dim]·[/] [bold]{escape(name)}[/]"
    return f"{title}   {escape(stacks)}", f"{title}  {escape(stacks)}"


def _summary_folder_rows(tokens, width: int) -> list[str]:
    """
    Up to four rows of the biggest folders (two levels deep): path, tokens,
    a 20-cell bar and the share. The fourth row sums the rest when there
    are more. Empty when there's nothing worth splitting up.
    """
    total = tokens.total
    groups = tokens.by_dir(2)
    if total <= 0 or len(groups) < 2:
        return []
    if len(groups) > _SUMMARY_FOLDER_ROWS:
        rest = groups[_SUMMARY_FOLDER_ROWS - 1:]
        rows = [*groups[: _SUMMARY_FOLDER_ROWS - 1], (f"{len(rest)} more folders", sum(n for _p, n in rest))]
    else:
        rows = groups

    count_w = max(len(f"{n:,}") for _p, n in rows)
    path_w = min(_SUMMARY_PATH_MAX, max(_cells(p) for p, _n in rows))
    bar_w = 2 + _SUMMARY_BAR_CELLS
    pct_w = 4
    fixed = 2 + 2 + count_w + 2 + pct_w  # indent, the gaps, counts and the share
    bars = width >= _SUMMARY_BARS_MIN_WIDTH and fixed + path_w + bar_w <= width
    if not bars and fixed + path_w > width:
        path_w = max(_SUMMARY_PATH_MIN, width - fixed)

    lines = []
    for path, count in rows:
        share = count / total
        label = escape(_pad(_left_truncate(path, path_w), path_w))
        pct = f"[dim]{_share_text(share):>{pct_w}}[/]"
        if bars:
            filled = round(share * _SUMMARY_BAR_CELLS)
            if count > 0 and filled == 0:
                filled = 1
            bar = "█" * filled + "░" * (_SUMMARY_BAR_CELLS - filled)
            lines.append(f"  {label}  {count:>{count_w},}  {bar}  {pct}")
        else:
            lines.append(f"  {label}  {count:>{count_w},}  {pct}")
    return lines


def _summary_fits(fit_rows: list[dict]) -> tuple[list[str], list[str]]:
    """The Fits line's variants (wide, then compact), and the targets it's too big for."""
    parts = []
    too_big = []
    for row in fit_rows:
        text = escape(f"{row['name']} {'' if row['exact'] else '≈'}{_share_text(row['share'])}")
        if not row["fits"]:
            text = f"[yellow]{text}, too big[/]"
            too_big.append(row["name"])
        parts.append(text)
    label = f"  [dim]{_pad('Fits', _SUMMARY_LABEL)}[/]"
    return [
        label + "  [dim]·[/]  ".join(parts),
        label + " [dim]·[/] ".join(parts),
        "  [dim]Fits[/]  " + " [dim]·[/] ".join(parts),
    ], too_big


def _summary_left_out(excluded: list[Path], project_dir: Path) -> list[str]:
    """
    "Left out  36,176 files: node_modules/ (35,120), .next/ (904), 3 secret files",
    then shorter variants with fewer groups, for narrow terminals.
    """
    from contextzip.filters import summarise_exclusions

    n = len(excluded)
    groups = []
    for name, count in list(summarise_exclusions(excluded, project_dir).items())[:3]:
        slash = "/" if (project_dir / name).is_dir() else ""
        groups.append(f"{name}{slash} ({count:,})")
    from contextzip.redact import count_sensitive_paths

    rels = []
    for p in excluded:
        try:
            rels.append(p.relative_to(project_dir).as_posix())
        except ValueError:
            continue
    secrets = count_sensitive_paths(rels)
    tail = [f"{secrets} secret file{'' if secrets == 1 else 's'}"] if secrets else []
    head = f"  [dim]{_pad('Left out', _SUMMARY_LABEL)}[/]{n:,} file{'' if n == 1 else 's'}"
    variants = []
    for keep in range(len(groups), -1, -1):
        bits = groups[:keep] + tail
        variants.append(head + (": " + escape(", ".join(bits)) if bits else ""))
    return variants


def _display_path(path: Path) -> str:
    """*path* relative to the current folder when it's inside it, otherwise absolute."""
    try:
        return str(Path(path).resolve().relative_to(Path.cwd().resolve()))
    except (ValueError, OSError):
        return str(path)


def print_package_summary(
    *,
    name: str,
    detection,
    resolved,
    project_dir: Path,
    tokens,
    fit_rows: list[dict] | None = None,
    result=None,
    git_mode: bool = False,
    verbose: bool = False,
    warnings=None,
    clipboard=None,
    hint: str | None = None,
    report_path=None,
    footer: str | None = "Nothing was written. Run it again without --dry-run to make the ZIP.",
    con: Console = console,
) -> None:
    """
    The designed end of a package run, for a terminal (see fancy()):

        contextzip · my-app   Next.js + FastAPI (backend/)

          44 files   186 KB → 63 KB zip   46,210 tokens

          src/app/           18,200  ████████████░░░░░░░░  39%
          …

          Fits      Claude ≈23%  ·  ChatGPT 36%  ·  Gemini ≈5%
          Left out  36,176 files: node_modules/ (35,120), .next/ (904), 3 secret files

        ✓ On your clipboard. Paste it into Claude or ChatGPT.   snapshot #14
          When the AI replies, run: cz apply

    *result* is the PackageResult, or None for a dry run. *warnings* is a
    callable that prints the run's `!` lines; they go between the block and
    the final lines. *clipboard* is the ClipboardResult, or None after
    --no-clipboard. *report_path* is printed only when given. *footer* is
    a dry run's last line (None for none).
    """
    if jsonout.active() is not None:
        return
    from rich.text import Text

    width = con.width
    dry_run = result is None

    _print_fitted(con, *_summary_header(name, detection))
    con.print()

    # ── Stats ─────────────────────────────────────────────────────────────
    files = result.file_count if result is not None else len(resolved.included)
    count = f"{files:,} file{'' if files == 1 else 's'}"
    token_text = f"[bold]{escape(tokens.label())}[/]"
    if dry_run:
        size = human_size(sum(resolved.size_of(p) for p in resolved.included))
        _print_fitted(
            con,
            f"  {count}   {size}   [dim]dry run[/]   {token_text}",
            f"  {count}  {size}  [dim]dry run[/]  {token_text}",
        )
    else:
        sizes = f"{human_size(result.uncompressed_bytes)} → {human_size(result.compressed_bytes)} zip"
        _print_fitted(con, f"  {count}   {sizes}   {token_text}", f"  {count}  {sizes}  {token_text}")

    # ── Folders ───────────────────────────────────────────────────────────
    rows = _summary_folder_rows(tokens, width)
    if rows:
        con.print()
        for row in rows:
            _print_fitted(con, row)

    # ── Fits and Left out ─────────────────────────────────────────────────
    fits, too_big = _summary_fits(fit_rows) if fit_rows else (None, [])
    left_out = _summary_left_out(resolved.excluded, project_dir) if not git_mode and resolved.excluded else None
    if fits or left_out:
        con.print()
        if fits:
            _print_fitted(con, *fits)
        if left_out:
            _print_fitted(con, *left_out)
    con.print()

    # ── Verbose detail and warnings ───────────────────────────────────────
    if verbose:
        if dry_run and resolved.included:
            _print_included_files(resolved, project_dir, con=con)
        print_most_tokens(tokens, con=con)
    if too_big:
        names = too_big[0] if len(too_big) == 1 else ", ".join(too_big[:-1]) + " and " + too_big[-1]
        warn(
            literal(f"Too big for {names}. Narrow it with contextzip include PATH, or see the largest folders with --verbose."),
            con=con,
        )
    if warnings is not None:
        warnings()

    # ── Final lines ───────────────────────────────────────────────────────
    if dry_run:
        if footer:
            info(footer, con=con)
        return

    snapshot = getattr(result, "snapshot", None)
    snap = f"   [dim]snapshot #{escape(snapshot.partition('-')[0])}[/]" if snapshot else ""
    if clipboard is not None and clipboard.tier == Tier.FILE_ON_CLIPBOARD:
        _print_fitted(
            con,
            f"[green]✓[/] On your clipboard. Paste it into Claude or ChatGPT.{snap}",
            f"[green]✓[/] On your clipboard.{snap}",
            "[green]✓[/] On your clipboard.",
        )
    else:
        saved = f"[green]✓[/] Saved to {escape(_display_path(result.zip_path))}"
        if Text.from_markup(saved + snap).cell_len <= width:
            saved += snap
        con.print(saved, highlight=False)  # a long path may wrap; it's never cut short
        if clipboard is not None:
            print_clipboard_result(clipboard, con=con)
    if hint:
        info(hint, con=con)
    if report_path:
        info(literal(f"Full report: {report_path}"), con=con)


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
            con.print(f"  [cyan]·[/] {escape(rel)}  [dim]{size}[/]")


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

    if fancy():
        _print_apply_plan_fancy(plan, project_dir, verbose=verbose, con=con)
        return

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


_APPLY_WRITE_ORDER = ("modified", "new", "merged", "moved")
_APPLY_MAX_ROWS = 14


def _apply_rows(plan, verbose: bool) -> list:
    """The files worth a line: risky first, then merged, moved and matched by name. Every file with --verbose."""
    rank = {status: i for i, status in enumerate(_RISKY_ORDER)}
    risky = sorted(plan.risky_entries, key=lambda e: rank.get(e.status.value, len(rank)))
    risky_ids = {id(e) for e in risky}
    if verbose:
        return risky + [e for e in plan.entries if id(e) not in risky_ids]
    notable = [
        e for e in plan.entries
        if id(e) not in risky_ids and (e.status.value in ("merged", "moved") or (e.moved and e.how in ("suffix", "content")))
    ]
    return risky + notable


def _apply_note(e) -> str:
    bits = []
    if e.status.value == "moved" and e.moved_from:
        bits.append(f"from {e.moved_from}")
    elif e.moved and e.how in ("suffix", "content"):
        bits.append(f"matched {e.source_name} by {'content' if e.how == 'content' else 'name'}")
    if e.note:
        bits.append(e.note)
    return "; ".join(bits)


def _print_apply_plan_fancy(plan, project_dir: Path, *, verbose: bool, con: Console) -> None:
    """
    `apply-zip` in a terminal:

        contextzip apply · reply.zip   compared with snapshot #14

          Will write   9   6 modified · 2 new · 1 merged
          Needs you    2   1 conflict · 1 partial
          Unchanged   12

          ! src/auth/login.ts        conflict   3 places where you and the AI changed …
          ≈ src/db/schema.ts         merged     your edits and the AI's merged cleanly
    """
    from collections import Counter

    width = con.width
    title = f"contextzip apply [dim]·[/] [bold]{escape(plan.zip_path.name)}[/]"
    snapshot = getattr(plan, "snapshot", None)
    if plan.has_manifest:
        against = f"snapshot #{snapshot.partition('-')[0]}" if snapshot else "what you sent"
        _print_fitted(con, f"{title}   [dim]compared with[/] {escape(against)}", f"{title}  {escape(against)}", title)
    else:
        _print_fitted(con, title)
    con.print()

    counts = Counter(e.status.value for e in plan.entries)
    lines = []
    write = sum(counts.get(k, 0) for k in _APPLY_WRITE_ORDER)
    if write:
        lines.append(("Will write", write, [f"{counts[k]} {k}" for k in _APPLY_WRITE_ORDER if counts.get(k)]))
    needs = sum(counts.get(k, 0) for k in _RISKY_ORDER)
    if needs:
        lines.append(("Needs you", needs, [f"{counts[k]} {k}" for k in _RISKY_ORDER if counts.get(k)]))
    if counts.get("unchanged"):
        lines.append(("Unchanged", counts["unchanged"], []))
    number_w = max([3, *(len(str(n)) for _l, n, _p in lines)])
    for label, n, parts in lines:
        head = f"  [dim]{label:<10}[/] {n:>{number_w}}"
        if parts:
            _print_fitted(con, f"{head}   " + " [dim]·[/] ".join(parts), head)
        else:
            _print_fitted(con, head)

    rows = _apply_rows(plan, verbose)
    shown = rows if verbose else rows[:_APPLY_MAX_ROWS]
    if shown:
        con.print()
        status_w = max(len(e.status.value) for e in shown)
        longest = max(_cells(e.display_path) for e in shown)
        # Room for the note matters more than long paths: cut paths from the left past that.
        path_w = max(12, min(longest, 48, width - (2 + 2 + 2 + status_w + 3) - 20))
        for e in shown:
            colour, sym = _APPLY_STATUS_STYLE[e.status.value]
            path = escape(_pad(_left_truncate(e.display_path, path_w), path_w))
            status = f"{e.status.value:<{status_w}}"
            note = _apply_note(e)
            row = f"  [{colour}]{sym}[/] {path}  [{colour}]{status}[/]"
            _print_fitted(con, f"{row}   [dim]{escape(note)}[/]" if note else row)
        hidden = len(rows) - len(shown)
        if hidden > 0:
            info(f"… and {hidden} more, see the report or use --verbose", con=con)

    notes = []
    if plan.has_manifest and plan.manifest_note:
        notes.append(("info", plan.manifest_note))
    if not plan.has_manifest:
        notes.append(("warn", "No snapshot of what you sent was found — files that already exist need a look"))
    if plan.rule is not None:
        notes.append(("info", f"Paths in the zip were remapped: {plan.rule.describe()}"))
    if plan.structure_warning:
        notes.append(("warn", plan.structure_warning))
    if plan.ignored:
        n = len(plan.ignored)
        notes.append(("info", f"Ignored {n} entr{'ies' if n != 1 else 'y'} (contextzip's own files, OS junk)"))
    if notes:
        con.print()
        for level, text in notes:
            (warn if level == "warn" else info)(literal(text), con=con)
    con.print()


def print_apply_result(result, *, con: Console = console) -> None:
    """What was written, skipped, and where the zip went."""
    if fancy():
        n = len(result.written)
        wrote = f"Wrote {n} file{'' if n == 1 else 's'}."
        left = len(result.skipped)
        if left:
            need = "needs" if left == 1 else "need"
            con.print(f"[green]✓[/] {wrote}   {left} {need} your call: run {prog_name()} apply --review", highlight=False)
        else:
            con.print(f"[green]✓[/] {wrote} Nothing left to decide.", highlight=False)
        removed = getattr(result, "removed", [])
        if removed:
            ok(f"Removed {len(removed)} old file{'s' if len(removed) != 1 else ''}", literal(", ".join(removed[:5])), con=con)
        for path, reason in getattr(result, "not_removed", [])[:5]:
            warn(literal(f"Kept {path} — {reason}"), con=con)
        return

    ok(f"Applied {len(result.written)} file{'s' if len(result.written) != 1 else ''}", con=con)
    removed = getattr(result, "removed", [])
    if removed:
        ok(f"Removed {len(removed)} old file{'s' if len(removed) != 1 else ''}", ", ".join(removed[:5]), con=con)
    for path, reason in getattr(result, "not_removed", [])[:5]:
        warn(f"Kept {path} — {reason}", con=con)
    if result.skipped:
        warn(f"Skipped {len(result.skipped)} for now: {', '.join(result.skipped[:5])}{' …' if len(result.skipped) > 5 else ''}", con=con)
    if result.zip_kept:
        info("The zip stays in the inbox — run contextzip apply again once you've decided on the rest.", con=con)
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
    """
    *n* bytes, readable: "512 B", "186 KB", "1.4 MB", "14 MB", "2.1 GB".
    One decimal from MB up while the number is below 10, none otherwise.
    """
    size = float(max(n, 0))
    units = ("B", "KB", "MB", "GB", "TB")
    for i, unit in enumerate(units):
        last = i == len(units) - 1
        if i >= 2 and round(size, 1) < 10:
            return f"{size:.1f} {unit}"
        if last or round(size) < 1024:
            return f"{size:.0f} {unit}"
        size /= 1024
    return f"{size:.0f} TB"  # pragma: no cover - the loop always returns
