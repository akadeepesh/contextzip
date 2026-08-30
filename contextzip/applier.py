"""
applier.py — Apply an AI-returned ZIP back into the project.

This is the other half of the round trip contextzip already does well:
`contextzip` packages your project *out* to an AI tool; `apply-zip` brings
the AI's response back *in*, safely.

Design
──────
Every ZIP `packager.py` creates gets a sidecar manifest written next to it
on disk (never inside the ZIP — see packager.py's Phase 8 notes). That
manifest records a hash of each included file at zip-time. When you run
`contextzip apply-zip`, the incoming ZIP is diffed against that manifest
to classify every file:

    NEW        — wasn't part of the original zip; no local baseline for it
    MODIFIED   — was sent, content changed, and the local copy hasn't
                 moved since — a clean, expected edit
    UNCHANGED  — identical to what's already on disk; nothing to do
    DRIFTED    — the local file changed since the zip was made (you edited
                 it yourself, or it was deleted locally) — applying blindly
                 here could clobber your own work
    UNTRACKED  — present in the returned zip but wasn't part of the
                 original manifest, and already exists locally under that
                 path — no baseline to compare against

DRIFTED and UNTRACKED (and the "no manifest at all" case, which naturally
produces UNTRACKED for every pre-existing path) are what the CLI treats as
"risky" and prompts about. NEW and MODIFIED apply silently.

v1 scope: only adds and modifies files. Deletions are never inferred or
performed — a file that used to be in the manifest but is absent from the
returned zip is simply left alone.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

_MANIFEST_SUFFIX = ".manifest.json"
_INBOX_DIRNAME = "inbox"
_APPLIED_DIRNAME = "applied"
_BACKUPS_DIRNAME = "backups"
_OUTPUT_DIRNAME = "output"  # must match packager._OUTPUT_DIRNAME

# Files contextzip itself writes into outgoing ZIPs that should never be
# written back into the project even if an AI tool echoes them back.
_IGNORED_ZIP_ENTRIES = {"prompt.txt"}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ApplyError(Exception):
    """Base class for all apply-zip errors."""


class NoZipFoundError(ApplyError):
    """Raised when no zip could be resolved from the inbox or an explicit path."""


class MultipleZipsFoundError(ApplyError):
    """Raised when the inbox has more than one zip and none was specified."""

    def __init__(self, candidates: list[Path]):
        self.candidates = candidates
        names = ", ".join(p.name for p in candidates)
        super().__init__(
            f"Multiple zips found in the inbox: {names}. "
            "Specify which one: contextzip apply-zip <path>"
        )


class UnsafeZipEntryError(ApplyError):
    """Raised when a zip entry would resolve outside the project tree."""


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


class FileStatus(str, Enum):
    NEW = "new"
    MODIFIED = "modified"
    UNCHANGED = "unchanged"
    DRIFTED = "drifted"
    UNTRACKED = "untracked"


@dataclass
class ApplyEntry:
    rel_path: str
    status: FileStatus
    size: int
    extracted_path: Path


@dataclass
class ApplyPlan:
    zip_path: Path
    manifest_path: Path | None
    entries: list[ApplyEntry] = field(default_factory=list)
    extraction_dir: Path = None  # type: ignore[assignment]
    wrapper_stripped: str | None = None
    wrapper_note: str | None = None
    structure_warning: str | None = None

    @property
    def has_manifest(self) -> bool:
        return self.manifest_path is not None

    @property
    def writable_entries(self) -> list[ApplyEntry]:
        """Entries that would actually be written if applied (excludes unchanged)."""
        return [e for e in self.entries if e.status != FileStatus.UNCHANGED]

    @property
    def risky_entries(self) -> list[ApplyEntry]:
        return [
            e
            for e in self.entries
            if e.status in (FileStatus.DRIFTED, FileStatus.UNTRACKED)
        ]

    @property
    def is_risky(self) -> bool:
        """True if anything here warrants a confirmation prompt before writing."""
        return (
            bool(self.risky_entries)
            or not self.has_manifest
            or bool(self.structure_warning)
        )


@dataclass
class ApplyResult:
    written: list[str]
    backup_dir: Path | None
    applied_zip_path: Path


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


# ---------------------------------------------------------------------------
# Manifest (write side — called from packager.py right after zipping)
# ---------------------------------------------------------------------------


def manifest_path_for_zip(zip_path: Path) -> Path:
    """The sidecar manifest path for *zip_path*, e.g. codebase.zip -> codebase.manifest.json."""
    return zip_path.parent / f"{zip_path.stem}{_MANIFEST_SUFFIX}"


def write_manifest(zip_path: Path, project_dir: Path, included: list[Path]) -> Path:
    """
    Write the local sidecar manifest for a ZIP that was just created.

    Never written into the ZIP itself — see the module docstring and
    packager.py's Phase 8 notes for why that separation matters.
    """
    files: dict[str, dict] = {}
    for abs_path in included:
        if not abs_path.is_file():
            continue
        try:
            rel = abs_path.relative_to(project_dir).as_posix()
        except ValueError:
            continue
        try:
            files[rel] = {
                "hash": _hash_file(abs_path),
                "size": abs_path.stat().st_size,
            }
        except OSError:
            continue

    manifest = {
        "contextzip_manifest": True,
        "version": 1,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "zip_filename": zip_path.name,
        "files": files,
    }

    manifest_path = manifest_path_for_zip(zip_path)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest_path


def load_manifest(path: Path) -> dict:
    """Load and validate a manifest file. Never raises — returns {} if unusable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict) or not data.get("contextzip_manifest"):
        return {}
    return data


# ---------------------------------------------------------------------------
# Workspace locations
# ---------------------------------------------------------------------------


def _workspace_dir(project_dir: Path) -> Path:
    # Lazy import: packager.py imports this module too (to write manifests),
    # so importing it back at module load time would be circular.
    from contextzip.packager import _workspace_dir as _pkg_workspace_dir

    workspace, _is_git_repo = _pkg_workspace_dir(project_dir)
    return workspace


def inbox_dir(project_dir: Path) -> Path:
    return _workspace_dir(project_dir) / _INBOX_DIRNAME


def output_dir(project_dir: Path) -> Path:
    return _workspace_dir(project_dir) / _OUTPUT_DIRNAME


# ---------------------------------------------------------------------------
# Finding the zip / manifest to apply
# ---------------------------------------------------------------------------


def find_zip_to_apply(
    project_dir: Path,
    explicit_path: str | Path | None = None,
) -> Path:
    """
    Resolve which zip `apply-zip` should use.

    An explicit path always wins. Otherwise, scans .contextzip/inbox/ for
    *.zip files: exactly one -> use it; none -> NoZipFoundError; more than
    one -> MultipleZipsFoundError listing every candidate.
    """
    if explicit_path is not None:
        path = Path(explicit_path).expanduser().resolve()
        if not path.is_file():
            raise NoZipFoundError(f"No such file: {path}")
        return path

    inbox = inbox_dir(project_dir)
    if not inbox.is_dir():
        raise NoZipFoundError(
            f"No zip found. Drop the AI-returned zip into {inbox} "
            "(created the first time you run contextzip), or pass a path "
            "directly: contextzip apply-zip <path>"
        )

    candidates = sorted(p for p in inbox.glob("*.zip") if p.is_file())
    if not candidates:
        raise NoZipFoundError(
            f"No zip found in {inbox}. Drop the AI-returned zip there, "
            "or pass a path directly: contextzip apply-zip <path>"
        )
    if len(candidates) > 1:
        raise MultipleZipsFoundError(candidates)
    return candidates[0]


def find_latest_manifest(
    project_dir: Path,
    explicit_manifest: str | Path | None = None,
) -> Path | None:
    """
    Resolve which manifest to diff against.

    An explicit path always wins (if it exists). Otherwise picks the most
    recently modified *.manifest.json anywhere under .contextzip/output/
    — including its per-mode subfolders (output/codebase/, output/git-changes/,
    output/prompt/) — since in the normal one-round-trip-at-a-time flow,
    that's the zip you most recently generated regardless of which mode
    produced it, which is almost always the right baseline. Returns None
    if nothing is found (apply-zip still works, just more conservatively).
    """
    if explicit_manifest is not None:
        path = Path(explicit_manifest).expanduser().resolve()
        return path if path.is_file() else None

    out_dir = output_dir(project_dir)
    if not out_dir.is_dir():
        return None

    candidates = [p for p in out_dir.rglob(f"*{_MANIFEST_SUFFIX}") if p.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


# ---------------------------------------------------------------------------
# Safe extraction
# ---------------------------------------------------------------------------


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _safe_extract(zip_path: Path, dest: Path, strip_prefix: str | None = None) -> None:
    """
    Extract *zip_path* into *dest*, refusing to write anything if any entry
    would resolve outside *dest* (zip-slip protection). Validated in a full
    first pass before any file is written, so a malicious entry never
    causes a partial extraction.

    If *strip_prefix* is given (see `_detect_common_wrapper`), that leading
    path component is removed from every entry before it's resolved against
    *dest* — e.g. `codebase/contextzip/config.py` extracts to
    `contextzip/config.py` instead of recreating a `codebase/` folder.
    """
    dest = dest.resolve()
    strip = f"{strip_prefix}/" if strip_prefix else None
    with zipfile.ZipFile(zip_path) as zf:
        targets: dict[str, Path] = {}
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename
            if strip:
                if not name.startswith(strip):
                    # Shouldn't happen given how strip_prefix is detected
                    # (every entry shares it), but never silently misplace
                    # a file if it somehow doesn't.
                    continue
                name = name[len(strip):]
                if not name:
                    continue
            target = (dest / name).resolve()
            if not _is_within(target, dest):
                raise UnsafeZipEntryError(
                    f"Refusing to apply: entry '{info.filename}' resolves "
                    "outside the project tree."
                )
            targets[info.filename] = target

        for info in zf.infolist():
            if info.is_dir():
                continue
            target = targets.get(info.filename)
            if target is None:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out)


# ---------------------------------------------------------------------------
# Wrapper-folder detection ((1) in the apply-zip structure fix)
# ---------------------------------------------------------------------------


def _detect_common_wrapper(names: list[str]) -> str | None:
    """
    If every entry in *names* is nested one level under the exact same
    single top-level directory, return that directory's name — otherwise
    None. This is the shape produced by `zip -r out.zip myfolder`, GitHub's
    "Download ZIP" (`repo-branch/...`), and similar: an incidental wrapper
    around the real, root-relative paths, rather than an intentional part
    of the project's structure.

    Deliberately conservative: a single root-level file mixed in with the
    rest (no wrapper actually applies), or more than one top-level
    directory, both return None rather than guessing.
    """
    if not names:
        return None
    tops: set[str] = set()
    for n in names:
        if "/" not in n:
            return None
        tops.add(n.split("/", 1)[0])
    if len(tops) != 1:
        return None
    candidate = next(iter(tops))
    return candidate or None


def _match_rate(names: list[str], manifest_files: dict) -> float:
    """Fraction of *names* that appear as a path in *manifest_files*."""
    if not names:
        return 0.0
    known = sum(1 for n in names if n in manifest_files)
    return known / len(names)


# ---------------------------------------------------------------------------
# Building the plan
# ---------------------------------------------------------------------------


def build_plan(
    zip_path: Path,
    project_dir: Path,
    manifest_path: Path | None,
) -> ApplyPlan:
    """
    Extract *zip_path* to a temp directory and classify every file against
    *manifest_path* (may be None). Caller is responsible for eventually
    calling `execute_plan` or `discard_plan` to clean up the temp dir.

    Before extracting, checks whether every entry in the zip shares a
    single wrapping top-level directory (e.g. `codebase/contextzip/...`)
    that isn't actually part of the project — see `_detect_common_wrapper`.
    If so, and stripping it would line paths up with the manifest better
    than leaving them alone, it's stripped automatically. Separately, if a
    manifest exists and almost none of the resulting paths match it,
    `structure_warning` is set so the caller can make sure the person
    actually looks before applying — a zip whose files come out looking
    all-new is exactly what a silently-mismatched structure produces.
    """
    manifest = load_manifest(manifest_path) if manifest_path else {}
    manifest_files: dict = manifest.get("files", {}) if manifest else {}

    with zipfile.ZipFile(zip_path) as zf:
        raw_names = sorted(i.filename for i in zf.infolist() if not i.is_dir())

    strip_prefix: str | None = None
    wrapper_note: str | None = None
    candidate = _detect_common_wrapper(raw_names)
    if candidate and not (project_dir / candidate).is_dir():
        if manifest_files:
            unstripped_rate = _match_rate(raw_names, manifest_files)
            stripped_names = [n[len(candidate) + 1:] for n in raw_names]
            stripped_rate = _match_rate(stripped_names, manifest_files)
            # Only strip when it clearly helps — meaningfully better match
            # against paths we know this project actually has, not just a
            # coincidental improvement on a tiny zip.
            if stripped_rate > unstripped_rate and stripped_rate >= 0.5:
                strip_prefix = candidate
                wrapper_note = (
                    f"Removed wrapping folder '{candidate}/' present in every "
                    f"zip entry — {stripped_rate:.0%} of paths matched the "
                    f"project manifest after stripping vs {unstripped_rate:.0%} "
                    "before."
                )
        else:
            # No manifest to confirm against, so this is a softer call —
            # still strip (matches how `tar` and GitHub's own zip downloads
            # behave), but say so plainly since it's not manifest-verified.
            strip_prefix = candidate
            wrapper_note = (
                f"Removed wrapping folder '{candidate}/' present in every zip "
                "entry (no manifest available to confirm — double-check the "
                "result before trusting it)."
            )

    extraction_dir = Path(tempfile.mkdtemp(prefix="contextzip-apply-"))
    _safe_extract(zip_path, extraction_dir, strip_prefix=strip_prefix)

    entries: list[ApplyEntry] = []
    for extracted in sorted(extraction_dir.rglob("*")):
        if not extracted.is_file():
            continue

        rel = extracted.relative_to(extraction_dir).as_posix()
        if rel in _IGNORED_ZIP_ENTRIES:
            continue

        size = extracted.stat().st_size
        live_path = project_dir / rel
        manifest_entry = manifest_files.get(rel)

        if manifest_entry is None:
            # No baseline for this path at all — either genuinely new, or
            # (if it already exists locally) something we can't safely
            # compare, so we don't assume it's a clean overwrite.
            status = FileStatus.NEW if not live_path.is_file() else FileStatus.UNTRACKED
        elif not live_path.is_file():
            # Was part of the original manifest but no longer exists locally
            # (e.g. you deleted it after zipping). Applying blindly would
            # resurrect a file you removed on purpose, so this counts as
            # drift rather than a plain new/modified write.
            status = FileStatus.DRIFTED
        else:
            original_hash = manifest_entry.get("hash")
            live_hash = _hash_file(live_path)
            new_hash = _hash_file(extracted)
            if live_hash != original_hash:
                status = FileStatus.DRIFTED
            elif new_hash != original_hash:
                status = FileStatus.MODIFIED
            else:
                status = FileStatus.UNCHANGED

        entries.append(
            ApplyEntry(rel_path=rel, status=status, size=size, extracted_path=extracted)
        )

    structure_warning: str | None = None
    if manifest_files and len(entries) >= 3:
        known = sum(1 for e in entries if e.rel_path in manifest_files)
        rate = known / len(entries)
        if rate < 0.1:
            structure_warning = (
                f"Only {known} of {len(entries)} files in this zip match paths "
                "from the project manifest, even after checking for a wrapping "
                "folder. That usually means the zip's internal structure "
                "doesn't line up with this project — the wrong zip, or one "
                "built with an unexpected layout. Applying it as-is will "
                "likely create a pile of unrelated new files rather than "
                "update the ones you meant to change. Double-check the zip "
                "before proceeding."
            )

    return ApplyPlan(
        zip_path=zip_path,
        manifest_path=manifest_path,
        entries=entries,
        extraction_dir=extraction_dir,
        wrapper_stripped=strip_prefix,
        wrapper_note=wrapper_note,
        structure_warning=structure_warning,
    )


def discard_plan(plan: ApplyPlan) -> None:
    """Clean up the temp extraction dir without writing anything (dry-run / declined)."""
    shutil.rmtree(plan.extraction_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Executing the plan
# ---------------------------------------------------------------------------


def _backup_entries(
    entries: list[ApplyEntry], project_dir: Path, workspace_root: Path
) -> Path | None:
    """
    Copy the current, pre-apply version of every existing file about to be
    touched into .contextzip/backups/<timestamp>/, preserving relative
    paths. Files that don't exist yet locally (new files) have nothing to
    back up. Returns the backup directory, or None if nothing needed one.
    """
    to_backup = [e for e in entries if (project_dir / e.rel_path).is_file()]
    if not to_backup:
        return None

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_dir = workspace_root / _BACKUPS_DIRNAME / stamp
    for entry in to_backup:
        src = project_dir / entry.rel_path
        dst = backup_dir / entry.rel_path
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return backup_dir


def _move_zip_to_applied(
    zip_path: Path, project_dir: Path, workspace_root: Path, retain: int = 1
) -> Path:
    """
    Move a consumed inbox zip into .contextzip/inbox/applied/, timestamped,
    so it can't be accidentally re-applied. Zips passed by an explicit path
    outside the inbox are left where the user put them rather than being
    moved unexpectedly.

    Keeps at most *retain* applied zips (default 1 — only the most recent).
    Filenames are timestamp-prefixed, so a lexicographic sort is also a
    chronological one; the oldest are pruned first, before this run's zip
    is moved in, so the folder never holds more than *retain* at once.
    Typically a project's `applied_zip_retention` preference
    (.contextzip/config.json) — raise it to keep a longer audit trail.
    """
    if zip_path.parent.resolve() != inbox_dir(project_dir).resolve():
        return zip_path

    applied_dir = workspace_root / _INBOX_DIRNAME / _APPLIED_DIRNAME
    applied_dir.mkdir(parents=True, exist_ok=True)

    retain = max(1, retain)
    existing = sorted(applied_dir.glob("*.zip"))
    # Keep room for the zip we're about to add: prune down to retain - 1
    # existing entries before moving the new one in.
    overflow = len(existing) - (retain - 1)
    for old in existing[: max(0, overflow)]:
        try:
            old.unlink()
        except OSError:
            pass  # best-effort — a leftover old zip isn't worth failing the apply over

    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = applied_dir / f"{stamp}-{zip_path.name}"
    try:
        shutil.move(str(zip_path), str(dest))
    except OSError:
        return zip_path
    return dest


def execute_plan(plan: ApplyPlan, project_dir: Path, retain: int = 1) -> ApplyResult:
    """
    Write every non-unchanged entry from *plan* into the project.

    Backs up whatever's about to be overwritten first. Assumes the caller
    has already decided to proceed — dry-run and confirmation prompts are
    handled upstream (see cli.py's `apply-zip` command).

    *retain* caps how many applied zips are kept in
    .contextzip/inbox/applied/ — typically a project's
    `applied_zip_retention` preference (.contextzip/config.json).
    """
    workspace_root = _workspace_dir(project_dir)
    to_write = plan.writable_entries

    backup_dir = _backup_entries(to_write, project_dir, workspace_root)

    written: list[str] = []
    for entry in to_write:
        dest = project_dir / entry.rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(entry.extracted_path, dest)
        written.append(entry.rel_path)

    applied_zip_path = _move_zip_to_applied(
        plan.zip_path, project_dir, workspace_root, retain=retain
    )
    shutil.rmtree(plan.extraction_dir, ignore_errors=True)

    return ApplyResult(
        written=written, backup_dir=backup_dir, applied_zip_path=applied_zip_path
    )
