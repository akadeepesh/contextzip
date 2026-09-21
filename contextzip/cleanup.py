"""
cleanup.py — Automatic workspace housekeeping for contextzip.

.contextzip/ accumulates disposable artifacts over time: a zip+manifest+report
set every run, and (until pruned) old archived applied-zips and loose apply
reports. None of it is precious — every zip is trivially reproducible by just
re-running contextzip. So cleanup here is deliberately brutal rather than
cautious: it runs automatically after every successful command (see
`_auto_cleanup` in cli.py), with no prompt and no separate command to
remember, and keeps only the most recent `cleanup.keep_recent` item(s) in
each category — everything else is deleted immediately, every time.

Nothing here ever touches the project — only disposable metadata under
.contextzip/ that costs nothing to regenerate.

Never touched, under any settings:
  - .contextzip/config.json (project preferences)
  - .contextzip/inbox/*.zip (zips waiting to be applied — never assumed
    stale just because they've been sitting there; only inbox/applied/,
    the archive of zips already consumed, is ever pruned)
  - anything outside .contextzip/

Kept only-the-most-recent-N, everything older deleted immediately:
  - zip/manifest/report sets under .contextzip/output/<mode>/, per mode
    folder
  - archived zips under .contextzip/inbox/applied/ (this is also governed
    by `applied_zip_retention`, already enforced on every apply-zip run —
    scanning it here too just catches the case where zips piled up before
    that setting existed, or the setting was since lowered)
  - loose `*.apply-report.txt` files sitting directly in .contextzip/inbox/
    (as opposed to inbox/applied/) — these are written next to wherever
    apply-zip resolved its zip from, decoupled from the zip's own
    lifecycle (a zip gets archived into applied/ or left where the user
    put it; its report always lands in inbox/ itself), so nothing else
    here would ever catch them piling up run after run

Removed outright, no matter how recent:
  - .contextzip/backups/ in its entirety. apply-zip no longer creates
    per-run backups there at all — this only ever catches a leftover
    folder from a project that used an older contextzip version, and it's
    dead weight the moment that version is gone.

Scanning only ever touches .contextzip/ directory listings and file mtimes —
no hashing, no reading file contents — so this stays fast even called on
every command, on a large project.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path


_OUTPUT_DIRNAME = "output"
_LEGACY_BACKUPS_DIRNAME = "backups"
_INBOX_DIRNAME = "inbox"
_APPLIED_DIRNAME = "applied"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class CleanupItem:
    path: Path
    kind: str  # "zip-set" | "applied-zip" | "apply-report" | "legacy-backups"
    size_bytes: int


@dataclass
class CleanupPlan:
    workspace: Path
    items: list[CleanupItem] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return sum(i.size_bytes for i in self.items)

    @property
    def is_empty(self) -> bool:
        return not self.items


@dataclass
class CleanupResult:
    removed: list[Path] = field(default_factory=list)
    failed: list[tuple[Path, str]] = field(default_factory=list)
    freed_bytes: int = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _size_of(path: Path) -> int:
    """Size of a file, or total size of everything under a directory."""
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    if not path.is_dir():
        return 0
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


def _zip_set_files(zip_path: Path) -> list[Path]:
    """The zip and any sidecar manifest/report files that sit next to it."""
    files = [zip_path]
    for suffix in (".manifest.json", ".report.txt", ".apply-report.txt"):
        sidecar = zip_path.with_name(zip_path.stem + suffix)
        if sidecar.is_file():
            files.append(sidecar)
    return files


def _newest_first(paths: list[Path]) -> list[Path]:
    return sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True)


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------


def scan(
    project_dir: Path,
    *,
    keep_recent: int = 1,
    applied_zip_retention: int = 1,
) -> CleanupPlan:
    """
    Build a plan of everything beyond the most recent `keep_recent` item(s)
    per category. Read-only — never deletes anything itself; pass the
    result to `execute()` to actually do so.
    """
    from contextzip.packager import _workspace_dir

    workspace, _is_git_repo = _workspace_dir(project_dir)
    items: list[CleanupItem] = []
    keep = max(0, keep_recent)

    # ── output/ — per-mode subfolders, plus any flat legacy files left
    #    behind by a contextzip version predating the per-mode split ──────
    output_root = workspace / _OUTPUT_DIRNAME
    if output_root.is_dir():
        for mode_dir in sorted(p for p in output_root.iterdir() if p.is_dir()):
            zips = _newest_first([p for p in mode_dir.glob("*.zip") if p.is_file()])
            for zp in zips[keep:]:
                for f in _zip_set_files(zp):
                    items.append(CleanupItem(path=f, kind="zip-set", size_bytes=_size_of(f)))

        legacy_zips = _newest_first([p for p in output_root.glob("*.zip") if p.is_file()])
        for zp in legacy_zips[keep:]:
            for f in _zip_set_files(zp):
                items.append(CleanupItem(path=f, kind="zip-set", size_bytes=_size_of(f)))

    # ── backups/ — the feature that created these is gone entirely; wipe
    #    the whole folder outright rather than pruning it, so upgrading
    #    from an older contextzip version doesn't leave it behind forever ──
    backups_root = workspace / _LEGACY_BACKUPS_DIRNAME
    if backups_root.is_dir():
        items.append(
            CleanupItem(path=backups_root, kind="legacy-backups", size_bytes=_size_of(backups_root))
        )

    # ── inbox/applied/ ───────────────────────────────────────────────────
    applied_root = workspace / _INBOX_DIRNAME / _APPLIED_DIRNAME
    if applied_root.is_dir():
        applied_zips = _newest_first([p for p in applied_root.glob("*.zip") if p.is_file()])
        retain = max(1, applied_zip_retention)
        for zp in applied_zips[retain:]:
            items.append(CleanupItem(path=zp, kind="applied-zip", size_bytes=_size_of(zp)))

    # ── inbox/*.apply-report.txt ─────────────────────────────────────────
    # Loose report files written directly in the inbox root (see
    # report.py's write_apply_report) — decoupled from any single zip's
    # own path, so nothing above ever catches these. Every apply-zip run
    # writes one, so left alone they'd accumulate forever.
    inbox_root = workspace / _INBOX_DIRNAME
    if inbox_root.is_dir():
        reports = _newest_first(
            [p for p in inbox_root.glob("*.apply-report.txt") if p.is_file()]
        )
        for rp in reports[keep:]:
            items.append(CleanupItem(path=rp, kind="apply-report", size_bytes=_size_of(rp)))

    return CleanupPlan(workspace=workspace, items=items)


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------


def execute(plan: CleanupPlan) -> CleanupResult:
    """
    Delete everything in *plan*, immediately, no confirmation. Best-effort:
    one failure doesn't stop the rest, and every failure is reported rather
    than silently swallowed.
    """
    result = CleanupResult()
    for item in plan.items:
        try:
            if item.path.is_dir():
                shutil.rmtree(item.path)
            elif item.path.is_file():
                item.path.unlink()
            else:
                continue  # already gone — nothing to do, not a failure
            result.removed.append(item.path)
            result.freed_bytes += item.size_bytes
        except OSError as exc:
            result.failed.append((item.path, str(exc)))
    return result
