"""
report.py — writes the full plain-text report file for a packaging run.

The terminal now only ever prints one line per step (see cli_display.py).
Everything that used to fill the screen — the excluded-directory
breakdown, the full included/excluded file list, per-file warning
detail — is written here instead, once per run, alongside the ZIP it
describes. `contextzip -v` still prints the same detail inline for
people who want it in-terminal without opening a file.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

from contextzip.filters import summarise_exclusions
from contextzip.cli_display import human_size
from contextzip.applier import inbox_dir


def report_path_for(zip_path: Path) -> Path:
    """The report path that sits next to a given zip: codebase.zip -> codebase.report.txt."""
    return zip_path.with_name(zip_path.stem + ".report.txt")


def write_scan_report(
    *,
    zip_path: Path,
    project_dir: Path,
    detection,
    resolved,
    mode: str,
    ai_prompt: str | None = None,
    ai_selected: list[Path] | None = None,
    large_file_warn_bytes: int,
    redacted: list[tuple[Path, list[str]]] | None = None,
) -> Path:
    """
    Write the full scan/package report next to *zip_path*.

    Returns the report path. Never raises — a failure to write the report
    should never fail the actual packaging run, so callers may wrap this
    in a try/except and just skip mentioning it on failure.
    """
    lines: list[str] = []
    w = lines.append

    w("contextzip report")
    w(f"generated : {_dt.datetime.now().isoformat(timespec='seconds')}")
    w(f"project   : {project_dir}")
    w(f"mode      : {mode}")
    w("")

    w("Detection")
    if detection.is_unknown:
        w("  ecosystem : unknown — base rules only")
    else:
        for name in detection.ecosystems:
            src = detection.sources.get(name)
            loc = f" ({src}/)" if src and src != "." else ""
            w(f"  ecosystem : {name}{loc}")
        w(f"  confidence: {detection.confidence}")
    w(f"  rules     : {', '.join(detection.rule_modules)}")
    w("")

    total = len(resolved.included) + len(resolved.excluded)
    included_size = sum(p.stat().st_size for p in resolved.included if p.exists())
    w("Files")
    if mode != "git-changes":
        w(f"  scanned  : {total + len(resolved.skipped)}")
    w(f"  included : {len(resolved.included)} ({human_size(included_size)})")
    if mode != "git-changes":
        w(f"  excluded : {len(resolved.excluded)}")
    if resolved.skipped:
        w(f"  skipped  : {len(resolved.skipped)}")
    w("")

    if mode != "git-changes" and resolved.excluded:
        w("Excluded directories / files")
        buckets = summarise_exclusions(resolved.excluded, project_dir)
        for label, count in buckets.items():
            w(f"  {label:<30} {count} file{'s' if count != 1 else ''}")
        w("")

    w("Included files")
    for p in sorted(resolved.included):
        try:
            rel = p.relative_to(project_dir).as_posix()
            size = human_size(p.stat().st_size) if p.exists() else "?"
        except ValueError:
            rel, size = str(p), "?"
        w(f"  {rel:<60} {size}")
    w("")

    if ai_prompt is not None:
        w("AI selection")
        w(f'  prompt   : "{ai_prompt}"')
        w(f"  selected : {len(ai_selected or [])} file(s)")
        w("")

    if resolved.large_files:
        w(f"Large files (>= {human_size(large_file_warn_bytes)})")
        for p, size in resolved.large_files:
            rel = p.relative_to(project_dir).as_posix()
            w(f"  {rel:<60} {human_size(size)}")
        w("")

    if resolved.binary_files:
        w("Binary files (AI tools may not read these)")
        for p in resolved.binary_files:
            rel = p.relative_to(project_dir).as_posix()
            w(f"  {rel}")
        w("")

    if redacted:
        w("Redacted secrets (limits.redact_secrets)")
        for rel, names in redacted:
            w(f"  {rel.as_posix():<60} {', '.join(names)}")
        w("")

    if resolved.skipped:
        w("Skipped files (unreadable or dangling symlink)")
        for p, reason in resolved.skipped:
            w(f"  {p.name} — {reason}")
        w("")

    report_path = report_path_for(zip_path)
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        return None
    return report_path


def write_apply_report(
    *,
    project_dir: Path,
    plan,
    result=None,
) -> Path | None:
    """
    Write the full apply-zip report (every file + status) into
    .contextzip/inbox/ — always there, regardless of where the zip that
    was applied actually lives. Returns the report path, or None on
    failure.

    `apply-zip <path>` accepts an explicit path anywhere on disk (not
    just the inbox), and that path is meant to be left alone afterwards
    rather than moved — see `archive_applied_zip`. The report used to
    follow the same "leave it where the zip is" rule, which meant
    pointing apply-zip at a zip sitting in the project root (or anywhere
    else outside .contextzip/inbox/) dropped a stray
    `<name>.apply-report.txt` right there, cluttering the repo as an
    untracked file. The report itself doesn't need to travel with the
    zip the way an archived copy would, so it always goes to the one
    place these reports are expected to live.
    """
    lines: list[str] = []
    w = lines.append

    w("contextzip apply-zip report")
    w(f"generated : {_dt.datetime.now().isoformat(timespec='seconds')}")
    w(f"project   : {project_dir}")
    w(f"zip       : {plan.zip_path}")
    w(f"manifest  : {plan.manifest_path if plan.has_manifest else 'none found'}")
    w("")

    w("Files")
    for e in plan.entries:
        w(f"  {e.rel_path:<60} {e.status.value}")
    w("")

    if result is not None:
        w("Result")
        w(f"  written : {len(result.written)}")
        w(f"  archived: {result.applied_zip_path}")
        w("")

    report_path = inbox_dir(project_dir) / (plan.zip_path.stem + ".apply-report.txt")
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        return None
    return report_path
