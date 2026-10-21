"""
applier.py — Apply an AI-returned ZIP back into the project.

This is the other half of the round trip: `contextzip` packages your
project *out* to an AI tool; `apply-zip` brings the AI's answer back *in*,
without making you babysit it.

How a returned ZIP is read
──────────────────────────
1. Where does each file belong? AI tools return ZIPs in every shape —
   wrapped in extra folders, relative to a subfolder, flattened, with
   Windows separators. pathmatch.py maps every entry onto the project by its
   longest shared path suffix and generalises the result to new files.

2. Which snapshot is the baseline? Every ZIP contextzip makes gets a
   manifest (a hash of each file it sent) kept locally, never inside the
   ZIP. The manifest that best explains the returned ZIP is chosen — not
   just the newest one — so an unrelated run in between, or a ZIP written
   with -o, doesn't turn every file into "drifted".

3. What happened to each file? Three versions are compared: the original
   you sent (read back from the ZIP you sent), the file on disk now, and
   the AI's version:

    NEW        the file doesn't exist yet                       written
    MODIFIED   only the AI changed it                            written
    MERGED     you both changed it, in different places          written (3-way merge)
    UNCHANGED  it already matches — nothing to do                skipped
    CONFLICT   you both changed the same or neighbouring lines   needs your call
    DRIFTED    you changed it and it can't be merged
               (binary, or the original isn't available)         needs your call
    DELETED    you deleted it after zipping                      needs your call
    UNTRACKED  exists here but wasn't in the ZIP you sent        needs your call
    PARTIAL    the AI returned a cut-down file
               ("// ... rest of the file unchanged")              needs your call
    AMBIGUOUS  can't tell which file it belongs to               needs your call

Line endings never count as a change: comparisons ignore CRLF vs LF, and
text written back keeps the local file's line-ending style and BOM.

Secret files that were redacted on the way out (see redact.py) are always
merged against the redacted copy you sent, so lines the AI didn't touch
keep your real values and a "[REDACTED]" placeholder is never written over
a secret.

Files are only removed with your explicit OK: a file the AI moved (MOVED)
keeps its old copy unless you choose to remove it, and a deletion the AI
asks for in CONTEXTZIP.md (DELETE_REQUESTED) needs its own yes — never
implied by --yes. Either way, a file you've changed since the snapshot is
never removed. A file that's simply missing from the reply is left alone.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from contextzip.merge3 import merge3
from contextzip.pathmatch import Rule, match_paths, normalize_name

_MANIFEST_SUFFIX = ".manifest.json"
_MANIFEST_VERSION = 2
_INBOX_DIRNAME = "inbox"
_APPLIED_DIRNAME = "applied"
_OUTPUT_DIRNAME = "output"  # must match packager._OUTPUT_DIRNAME
_INBOX_README_NAME = "README.txt"
_REDACTED = "[REDACTED]"

_INBOX_README_CONTENTS = """\
Drop your zip here which you want to apply.
============================================

This folder is contextzip's inbox — the one place `apply-zip` looks for
a zip to bring back into your project.

HOW TO USE IT
  1. Take the zip your AI tool (Claude, ChatGPT, etc.) gave you back,
     after you sent it a codebase package contextzip made for you.
  2. Drop that zip file directly into this folder — no need to rename
     it or unzip it first.
  3. Run:  contextzip apply-zip     (short alias:  cz apply-zip)
  4. contextzip finds it automatically, works out where every file
     belongs, compares each one with what you sent and what's on disk
     now, and shows you exactly what will change before writing anything.

WHAT HAPPENS TO THE ZIP AFTERWARDS
  Once applied, contextzip moves it into inbox/applied/ instead of
  deleting it, so there's always a record of exactly what was applied
  and when. If some files still need your decision, the zip stays here
  so you can run apply-zip again after resolving them. Old entries in
  applied/ are pruned automatically over time — see the `cleanup` and
  `applied_zip_retention` settings in .contextzip/config.json.

A FEW THINGS WORTH KNOWING
  - Only .zip files placed directly in this folder are picked up —
    not ones inside a subfolder.
  - If more than one zip ends up here at once, contextzip will ask you
    to say which one: contextzip apply-zip <name>.zip
  - You can also point apply-zip at a zip anywhere else on disk instead
    of using this folder: contextzip apply-zip /path/to/file.zip
  - This whole .contextzip/ folder is local to your machine and is
    never committed to git (see .contextzip/.gitignore) — nothing here
    leaves your computer unless you explicitly share it yourself.
  - This file is just documentation — it's fine to edit it, and it
    won't affect anything if you do. If it ever goes missing, though,
    contextzip will put a fresh copy back the next time it touches this
    folder; it never touches an actual pending zip, only this file.

Full docs: https://www.contextzipcli.com/apply-zip
"""


def ensure_inbox_scaffolding(workspace: Path) -> None:
    """
    Make sure <workspace>/inbox/ exists, with its README.txt explaining
    what the folder is for. Idempotent: an already-correct README is left
    alone, a missing or different one is rewritten, and nothing else in
    the inbox (pending zips, applied/, reports) is ever touched.
    """
    inbox = workspace / _INBOX_DIRNAME
    inbox.mkdir(parents=True, exist_ok=True)

    readme_path = inbox / _INBOX_README_NAME
    if readme_path.is_file():
        try:
            if readme_path.read_text(encoding="utf-8", errors="replace") == _INBOX_README_CONTENTS:
                return
        except OSError:
            return
    try:
        readme_path.write_text(_INBOX_README_CONTENTS, encoding="utf-8")
    except OSError:
        pass


# Files contextzip itself writes into outgoing ZIPs. Never written back
# into the project, even if an AI tool echoes them.
_OWN_ARTIFACTS = {"prompt.txt", "terminal-error.txt", "source-files.zip"}


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
    MERGED = "merged"
    UNCHANGED = "unchanged"
    CONFLICT = "conflict"
    DRIFTED = "drifted"
    DELETED = "deleted"
    UNTRACKED = "untracked"
    PARTIAL = "partial"
    AMBIGUOUS = "ambiguous"
    MOVED = "moved"
    DELETE_REQUESTED = "delete-requested"


SAFE_STATUSES = frozenset({FileStatus.NEW, FileStatus.MODIFIED, FileStatus.MERGED, FileStatus.MOVED})
RISKY_STATUSES = frozenset(
    {
        FileStatus.CONFLICT,
        FileStatus.DRIFTED,
        FileStatus.DELETED,
        FileStatus.UNTRACKED,
        FileStatus.PARTIAL,
        FileStatus.AMBIGUOUS,
        FileStatus.DELETE_REQUESTED,
    }
)

# How similar a new file must be to a sent file (missing from the reply) to
# count as that file, moved.
_MOVE_SIMILARITY = 0.85

STATUS_REASONS = {
    FileStatus.NEW: "new file",
    FileStatus.MODIFIED: "changed by the AI",
    FileStatus.MERGED: "your edits and the AI's merged cleanly",
    FileStatus.UNCHANGED: "already matches",
    FileStatus.CONFLICT: "you and the AI changed the same or neighbouring lines",
    FileStatus.DRIFTED: "you changed it since zipping and it can't be merged",
    FileStatus.DELETED: "you deleted it after zipping",
    FileStatus.UNTRACKED: "exists here but wasn't in the zip you sent",
    FileStatus.PARTIAL: "looks like a partial file — parts were replaced with a placeholder comment",
    FileStatus.AMBIGUOUS: "can't tell which file this belongs to",
    FileStatus.MOVED: "moved or renamed by the AI",
    FileStatus.DELETE_REQUESTED: "the AI asked for this file to be deleted",
}


@dataclass
class ApplyEntry:
    rel_path: str
    """Project-relative target path ("" when ambiguous)."""
    status: FileStatus
    size: int
    extracted_path: Path
    source_name: str = ""
    """The entry's (normalised) name inside the returned zip."""
    how: str = "exact"
    """How the target was found: exact, rule, suffix, content, new, ambiguous."""
    note: str = ""
    candidates: list[str] = field(default_factory=list)
    content: bytes | None = None
    """What applying this entry writes: the AI's version with the local
    line endings, or a merge. None when there's nothing safe to write."""
    incoming: bytes | None = None
    """The AI's version, adapted to local line endings — used when you
    choose "take the AI's version" for a risky entry."""
    conflict_text: str | None = None
    """For conflicts: the merge with git-style markers, for review/editing."""
    base_text: str | None = None
    """The version you sent, when available (for review)."""
    redacted: bool = False
    moved_from: str | None = None
    """For MOVED: the old path. It's only removed with your explicit OK."""
    removable: bool = True
    """For MOVED / DELETE_REQUESTED: False when the old file changed since
    the snapshot, so removing it would lose your work."""

    @property
    def is_safe(self) -> bool:
        return self.status in SAFE_STATUSES

    @property
    def is_risky(self) -> bool:
        return self.status in RISKY_STATUSES

    @property
    def display_path(self) -> str:
        return self.rel_path or self.source_name

    @property
    def moved(self) -> bool:
        """True when the file landed somewhere other than its name in the zip."""
        return bool(self.rel_path) and self.rel_path != self.source_name


@dataclass
class ApplyPlan:
    zip_path: Path
    manifest_path: Path | None
    entries: list[ApplyEntry] = field(default_factory=list)
    extraction_dir: Path = None  # type: ignore[assignment]
    rule: Rule | None = None
    """Path rewrite inferred from the zip's layout, e.g. "my-app/… → …"."""
    manifest_note: str | None = None
    ignored: list[str] = field(default_factory=list)
    """Zip entries ignored: OS junk and contextzip's own artifacts."""
    structure_warning: str | None = None
    snapshot: str | None = None
    """The snapshot this reply was matched to, e.g. "14-k3f9a2"."""
    matched_by_guide: bool = False
    """True when the reply echoed CONTEXTZIP.md and named its snapshot."""

    @property
    def has_manifest(self) -> bool:
        return self.manifest_path is not None

    @property
    def wrapper_note(self) -> str | None:
        """Kept for callers of the old API: describes the path rewrite, if any."""
        return f"Paths remapped {self.rule.describe()}" if self.rule else None

    @property
    def safe_entries(self) -> list[ApplyEntry]:
        return [e for e in self.entries if e.is_safe]

    @property
    def writable_entries(self) -> list[ApplyEntry]:
        """Entries that would change something if applied (everything but unchanged)."""
        return [e for e in self.entries if e.status != FileStatus.UNCHANGED]

    @property
    def risky_entries(self) -> list[ApplyEntry]:
        return [e for e in self.entries if e.is_risky]

    @property
    def moves(self) -> list[ApplyEntry]:
        """Moved files whose old copy could be removed."""
        return [e for e in self.entries if e.status == FileStatus.MOVED and e.moved_from and e.removable]

    @property
    def deletions(self) -> list[ApplyEntry]:
        return [e for e in self.entries if e.status == FileStatus.DELETE_REQUESTED]

    @property
    def is_risky(self) -> bool:
        """True if anything here needs a decision before it can be written."""
        return bool(self.risky_entries) or bool(self.structure_warning)


@dataclass
class ApplyResult:
    written: list[str]
    applied_zip_path: Path
    skipped: list[str] = field(default_factory=list)
    """Entries left alone — risky ones nobody decided on, or ones you skipped."""
    zip_kept: bool = False
    """True when the zip stayed in the inbox because something was skipped."""
    removed: list[str] = field(default_factory=list)
    """Old files removed — only ever with your explicit OK."""
    not_removed: list[tuple[str, str]] = field(default_factory=list)
    """(path, reason) for removals that were asked for but refused."""


# ---------------------------------------------------------------------------
# Content helpers
# ---------------------------------------------------------------------------

_BOM = b"\xef\xbb\xbf"


def _hash_bytes(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def _as_text(data: bytes) -> str | None:
    """Decoded text with line endings normalised to \\n, or None for binary."""
    if b"\x00" in data[:8192]:
        return None
    if data.startswith(_BOM):
        data = data[len(_BOM):]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _text_hash(data: bytes) -> str | None:
    text = _as_text(data)
    return None if text is None else _hash_bytes(text.encode("utf-8"))


def _same(a: bytes, b: bytes) -> bool:
    """Byte-identical, or identical text apart from line endings / BOM."""
    if a == b:
        return True
    ta, tb = _as_text(a), _as_text(b)
    return ta is not None and ta == tb


def _matches_manifest(data: bytes, entry: dict) -> bool:
    if _hash_bytes(data) == entry.get("hash"):
        return True
    text_hash = entry.get("text_hash")
    return bool(text_hash) and _text_hash(data) == text_hash


def _style_of(local: bytes | None) -> tuple[str, bool]:
    """(newline, has_bom) of a local file; defaults to \\n, no BOM."""
    if not local:
        return "\n", False
    crlf = local.count(b"\r\n")
    lf = local.count(b"\n") - crlf
    return ("\r\n" if crlf > lf else "\n"), local.startswith(_BOM)


def _encode_like(text: str, local: bytes | None) -> bytes:
    """Encode normalised *text* with the local file's line endings and BOM."""
    newline, bom = _style_of(local)
    data = text.replace("\n", newline).encode("utf-8")
    return _BOM + data if bom else data


def _adapt(incoming: bytes, local: bytes | None) -> bytes:
    """The AI's file with the local file's line endings — so a one-line fix
    never shows up as a whole-file change in git."""
    if local is None:
        return incoming
    text = _as_text(incoming)
    if text is None or _as_text(local) is None:
        return incoming
    return _encode_like(text, local)


# Placeholder comments AI tools leave instead of code. A returned file that
# contains one (and the original didn't) is probably not the whole file.
_PARTIAL_RE = re.compile(
    r"^\s*(?://|#|/\*+|\*|<!--|--|;)?\s*(?:\.{3}|…)\s*"
    r"(?:\(?\s*)?(?:rest|remaining|existing|unchanged|same|other|previous|original|keep|omitted)",
    re.IGNORECASE,
)
_PARTIAL_RE_2 = re.compile(
    r"^\s*(?://|#|/\*+|\*|<!--|--|;)\s*(?:rest of (?:the )?(?:file|code|component|function|class)"
    r"|(?:existing|remaining|other|previous|original) code"
    r"|(?:everything|code) (?:else|above|below) (?:is |stays |remains )?(?:unchanged|the same)"
    r"|unchanged\b)",
    re.IGNORECASE,
)


def looks_partial(incoming: str, reference: str | None) -> str | None:
    """
    The first placeholder line in *incoming* that isn't in *reference*, or
    None. Catches "// ... rest of the file unchanged", "# existing code",
    "/* ... */ unchanged" and similar.
    """
    ref_lines = set(reference.splitlines()) if reference else set()
    for line in incoming.splitlines():
        stripped = line.strip()
        if not stripped or line in ref_lines:
            continue
        if _PARTIAL_RE.match(stripped) or _PARTIAL_RE_2.match(stripped):
            return stripped
    return None


# ---------------------------------------------------------------------------
# Manifest (write side — called from packager.py right after zipping)
# ---------------------------------------------------------------------------


def manifest_path_for_zip(zip_path: Path) -> Path:
    """The sidecar manifest path for *zip_path*, e.g. codebase.zip -> codebase.manifest.json."""
    return zip_path.parent / f"{zip_path.stem}{_MANIFEST_SUFFIX}"


def build_manifest(
    zip_path: Path,
    project_dir: Path,
    included: list[Path],
    *,
    redacted: list[str] | None = None,
    skipped: set[Path] | None = None,
) -> dict:
    """
    The manifest for a ZIP that was just created: per file, a hash of what
    was on disk (plus a line-ending-insensitive hash for text) so apply-zip
    can tell whether you've changed it since, and which files were redacted
    on the way out. Saved locally by snapshots.py — never put in the ZIP.
    """
    skipped = skipped or set()
    files: dict[str, dict] = {}
    for abs_path in included:
        if abs_path in skipped or not abs_path.is_file():
            continue
        try:
            rel = abs_path.relative_to(project_dir).as_posix()
        except ValueError:
            continue
        try:
            data = abs_path.read_bytes()
        except OSError:
            continue
        entry = {"hash": _hash_bytes(data), "size": len(data)}
        text_hash = _text_hash(data)
        if text_hash:
            entry["text_hash"] = text_hash
        files[rel] = entry

    return {
        "contextzip_manifest": True,
        "version": _MANIFEST_VERSION,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "zip_filename": zip_path.name,
        "zip_path": str(zip_path.resolve()),
        "root": str(project_dir.resolve()),
        "redacted": sorted(redacted or []),
        "files": files,
    }


def write_manifest(
    zip_path: Path,
    project_dir: Path,
    included: list[Path],
    *,
    redacted: list[str] | None = None,
    skipped: set[Path] | None = None,
) -> Path:
    """
    Write a manifest next to *zip_path* (e.g. codebase.manifest.json).

    Kept for API compatibility. contextzip itself now stores manifests in
    snapshots (see snapshots.py), which also keep a copy of the ZIP.
    """
    manifest = build_manifest(zip_path, project_dir, included, redacted=redacted, skipped=skipped)
    manifest_path = manifest_path_for_zip(zip_path)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest_path


def load_manifest(path: Path) -> dict:
    """Load and validate a manifest file. Never raises — returns {} if unusable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    if not isinstance(data, dict) or not data.get("contextzip_manifest"):
        return {}
    if not isinstance(data.get("files"), dict):
        data["files"] = {}
    return data


class _BaseReader:
    """Reads the original content of a file from the ZIP a manifest describes."""

    def __init__(self, manifest_path: Path | None, manifest: dict):
        self._zf: zipfile.ZipFile | None = None
        self._files: dict = manifest.get("files", {})
        self._redacted = set(manifest.get("redacted", []))
        if manifest_path is None:
            return
        candidates = []
        if manifest.get("zip_path"):
            candidates.append(Path(manifest["zip_path"]))
        if manifest.get("zip_filename"):
            candidates.append(manifest_path.parent / manifest["zip_filename"])
        for c in candidates:
            try:
                if c.is_file():
                    self._zf = zipfile.ZipFile(c)
                    break
            except (OSError, zipfile.BadZipFile):
                continue

    def read(self, rel: str) -> bytes | None:
        if self._zf is None or rel not in self._files:
            return None
        try:
            data = self._zf.read(rel)
        except (KeyError, OSError, zipfile.BadZipFile):
            return None
        # A redacted file's copy in the zip differs from its manifest hash by
        # design. Anything else must match, or the zip was replaced since.
        if rel not in self._redacted and not _matches_manifest(data, self._files[rel]):
            return None
        return data

    def close(self) -> None:
        if self._zf is not None:
            self._zf.close()


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


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


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
        ensure_inbox_scaffolding(_workspace_dir(project_dir))
        raise NoZipFoundError(
            f"No zip found. Drop the AI-returned zip into {inbox}, "
            "or pass a path directly: contextzip apply-zip <path>"
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


def list_manifests(project_dir: Path) -> list[Path]:
    """
    Every manifest contextzip knows about for this project, newest first:
    snapshots (see snapshots.py), then manifests written by older versions
    next to their ZIPs in .contextzip/output/.
    """
    from contextzip.snapshots import list_snapshots

    found = [s.manifest_path for s in list_snapshots(project_dir)]
    out_dir = output_dir(project_dir)
    if out_dir.is_dir():
        legacy = [p for p in out_dir.rglob(f"*{_MANIFEST_SUFFIX}") if p.is_file()]
        found += sorted(legacy, key=lambda p: p.stat().st_mtime, reverse=True)
    return found


def find_latest_manifest(
    project_dir: Path,
    explicit_manifest: str | Path | None = None,
) -> Path | None:
    """
    The explicit manifest if given (and it exists), else the newest one.

    Kept for API compatibility. build_plan now picks the manifest that best
    explains the returned zip by itself when none is passed — see
    `_choose_manifest`.
    """
    if explicit_manifest is not None:
        path = Path(explicit_manifest).expanduser().resolve()
        return path if path.is_file() else None
    manifests = list_manifests(project_dir)
    return manifests[0] if manifests else None


# ---------------------------------------------------------------------------
# Reading the returned zip
# ---------------------------------------------------------------------------


def _extract(zip_path: Path, dest: Path) -> tuple[dict[str, Path], list[str]]:
    """
    Extract every file entry of *zip_path* into *dest* under its normalised
    name. Returns ({normalised name: extracted path}, [ignored raw names]).

    Validated in a full first pass before anything is written: an absolute
    path, a drive letter or a `..` that climbs out of the zip refuses the
    whole zip (zip-slip protection), so a malicious entry never causes a
    partial extraction.
    """
    dest = dest.resolve()
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise ApplyError(f"Not a valid zip file: {zip_path} ({exc})") from exc

    with zf:
        plan: dict[str, zipfile.ZipInfo] = {}
        ignored: list[str] = []
        for info in zf.infolist():
            if info.is_dir():
                continue
            raw = info.filename
            name = normalize_name(raw)
            if name is None:
                ignored.append(raw)
                continue
            if re.match(r"^[A-Za-z]:", raw) or raw.replace("\\", "/").startswith("/") or ".." in name.split("/"):
                raise UnsafeZipEntryError(
                    f"Refusing to apply: entry '{raw}' points outside the project."
                )
            target = (dest / name).resolve()
            if not _is_within(target, dest):
                raise UnsafeZipEntryError(
                    f"Refusing to apply: entry '{raw}' resolves outside the project tree."
                )
            plan[name] = info

        extracted: dict[str, Path] = {}
        for name, info in plan.items():
            target = dest / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out)
            extracted[name] = target
    return extracted, ignored


def _project_paths(project_dir: Path) -> set[str]:
    """Every file on disk (dependency/build folders pruned), root-relative."""
    from contextzip.filters import scan_all_files

    out: set[str] = set()
    for abs_path, _size in scan_all_files(project_dir):
        try:
            out.add(abs_path.relative_to(project_dir).as_posix())
        except ValueError:
            continue
    return out


def _choose_manifest(
    candidates: list[Path],
    extracted: dict[str, Path],
    project_files: set[str],
) -> tuple[Path | None, dict, str | None]:
    """
    The manifest that best explains the returned zip.

    Each candidate is scored by how many returned files map onto files it
    sent, and how many of those came back byte-for-byte unchanged (a strong
    sign this is the zip they came from). Ties go to the newest. With no
    signal at all — a zip of only brand-new files — the newest is used.
    """
    loaded = [(p, load_manifest(p)) for p in candidates]
    loaded = [(p, m) for p, m in loaded if m]
    if not loaded:
        return None, {}, None

    names = list(extracted)
    best: tuple[float, int, Path, dict] | None = None
    for index, (path, manifest) in enumerate(loaded):
        files = manifest["files"]
        result = match_paths(names, project_files | set(files), sent=set(files))
        score = 0.0
        for m in result.matches:
            if m.target and m.target in files:
                score += 1
                try:
                    if _matches_manifest(extracted[m.name].read_bytes(), files[m.target]):
                        score += 2
                except OSError:
                    pass
        key = (score, -index)
        if best is None or key > (best[0], best[1]):
            best = (score, -index, path, manifest)

    assert best is not None
    path, manifest = best[2], best[3]
    note = None
    if len(loaded) > 1 and path != loaded[0][0]:
        note = f"Using the snapshot from {path.parent.name}/{path.name} — it matches this zip best"
    return path, manifest, note


# ---------------------------------------------------------------------------
# Building the plan
# ---------------------------------------------------------------------------


def build_plan(
    zip_path: Path,
    project_dir: Path,
    manifest_path: Path | None = None,
    *,
    auto_manifest: bool = True,
) -> ApplyPlan:
    """
    Read *zip_path*, work out where each file belongs and what applying it
    would do. Writes nothing to the project. Call `execute_plan` or
    `discard_plan` afterwards to clean up the temporary extraction.

    *manifest_path* pins the baseline snapshot. Without it (and with
    *auto_manifest*), the manifest that best matches the zip is chosen.
    """
    project_dir = project_dir.resolve()
    extraction_dir = Path(tempfile.mkdtemp(prefix="contextzip-apply-"))
    try:
        extracted, ignored = _extract(zip_path, extraction_dir)
    except Exception:
        shutil.rmtree(extraction_dir, ignore_errors=True)
        raise

    project_files = _project_paths(project_dir)

    # The reply may echo the CONTEXTZIP.md we sent: it names the snapshot it
    # answers and can list moved/deleted files. It's never applied as a file.
    from contextzip.guide import find_in_zip, parse as parse_guide

    guide = None
    guide_name = find_in_zip(list(extracted))
    if guide_name is not None and guide_name.rsplit("/", 1)[-1] not in project_files:
        try:
            guide = parse_guide(extracted[guide_name].read_text(encoding="utf-8", errors="replace"))
        except OSError:
            guide = None
        extracted.pop(guide_name)
        ignored.append(guide_name)

    manifest_note = None
    matched_by_guide = False
    manifest: dict = {}
    if manifest_path is not None:
        manifest = load_manifest(manifest_path)
        if not manifest:
            manifest_path = None
    elif auto_manifest:
        from contextzip.snapshots import find as find_snapshot

        snap = find_snapshot(project_dir, guide.snapshot) if guide and guide.snapshot else None
        if snap is not None:
            manifest_path, manifest = snap.manifest_path, load_manifest(snap.manifest_path)
            matched_by_guide = bool(manifest)
        if not manifest:
            manifest_path, manifest, manifest_note = _choose_manifest(
                list_manifests(project_dir), extracted, project_files
            )
            if guide and guide.snapshot:
                manifest_note = (
                    f"The reply is for snapshot {guide.snapshot}, which isn't here "
                    "(made on another machine, or already pruned) — matched by content instead"
                )

    manifest_files: dict = manifest.get("files", {}) if manifest else {}
    redacted = set(manifest.get("redacted", [])) if manifest else set()
    base_reader = _BaseReader(manifest_path, manifest or {})

    def read_known(rel: str) -> bytes | None:
        base = base_reader.read(rel)
        if base is not None:
            return base
        try:
            return (project_dir / rel).read_bytes()
        except OSError:
            return None

    result = match_paths(
        list(extracted),
        project_files | set(manifest_files),
        sent=set(manifest_files),
        read_entry=lambda n: extracted[n].read_bytes(),
        read_known=read_known,
    )

    # A move the reply declares in CONTEXTZIP.md beats path matching: without
    # this, "lib/a.py" (moved from "a.py") would be matched back onto "a.py"
    # by its file name and look like an edit of the old file.
    if guide and guide.moves:
        declared_dsts = {dst for _src, dst in guide.moves}
        claimed = {m.target for m in result.matches if m.target}
        for m in result.matches:
            hit = next((d for d in declared_dsts if m.name == d or m.name.endswith("/" + d)), None)
            if hit is not None and m.target != hit and (hit not in claimed or hit == m.target):
                claimed.discard(m.target)
                m.target, m.how = hit, "new"
                claimed.add(hit)

    entries: list[ApplyEntry] = []
    for m in result.matches:
        path = extracted[m.name]
        # contextzip's own files (prompt.txt, …) echoed back by the AI
        if m.name.rsplit("/", 1)[-1] in _OWN_ARTIFACTS and (m.target is None or m.target not in project_files):
            ignored.append(m.name)
            continue
        entries.append(
            _classify(
                m.name,
                m.target,
                m.how,
                m.candidates,
                path,
                project_dir,
                manifest_files,
                redacted,
                base_reader,
            )
        )
    _detect_moves(entries, manifest_files, project_dir, base_reader, guide)
    entries.extend(_requested_deletions(guide, entries, project_files, manifest_files, project_dir, result.rule))
    base_reader.close()

    entries.sort(key=lambda e: (e.status not in RISKY_STATUSES, e.display_path))

    structure_warning = None
    if manifest_files and len(entries) >= 3:
        placed = sum(1 for e in entries if e.rel_path and e.rel_path in manifest_files)
        if placed == 0 and all(e.status in (FileStatus.NEW, FileStatus.AMBIGUOUS) for e in entries):
            structure_warning = (
                f"None of the {len(entries)} files in this zip match anything you sent — "
                "is this the right zip? Check the paths below before applying."
            )

    return ApplyPlan(
        zip_path=zip_path,
        manifest_path=manifest_path,
        entries=entries,
        extraction_dir=extraction_dir,
        rule=result.rule,
        manifest_note=manifest_note,
        ignored=ignored + result.skipped,
        structure_warning=structure_warning,
        snapshot=_snapshot_label(manifest) if manifest else None,
        matched_by_guide=matched_by_guide,
    )


def _snapshot_label(manifest: dict) -> str | None:
    if "snapshot_id" in manifest and manifest.get("token"):
        return f"{manifest['snapshot_id']}-{manifest['token']}"
    return None


def _unchanged_since_snapshot(rel: str, manifest_files: dict, project_dir: Path) -> bool:
    entry = manifest_files.get(rel)
    path = project_dir / rel
    if entry is None or not path.is_file():
        return False
    try:
        return _matches_manifest(path.read_bytes(), entry)
    except OSError:
        return False


def _detect_moves(entries, manifest_files: dict, project_dir: Path, base_reader, guide) -> None:
    """
    Mark NEW files that are really a sent file at a new path as MOVED:
    declared in the reply's CONTEXTZIP.md, or near-identical in content to a
    sent file the reply left out. The new file is still a safe write; the
    old one is only removed with your explicit OK (see execute_plan).
    """
    from contextzip.pathmatch import _similarity

    returned = {e.rel_path for e in entries if e.rel_path}
    declared = {dst: src for src, dst in (guide.moves if guide else [])}
    gone = [
        rel for rel in manifest_files
        if rel not in returned and (project_dir / rel).is_file()
    ]

    for e in entries:
        if e.status != FileStatus.NEW or not e.rel_path:
            continue
        src = declared.get(e.rel_path)
        if src is None:
            src = next((s for d, s in declared.items() if e.rel_path.endswith("/" + d) or d.endswith("/" + e.rel_path)), None)
        if src is not None and src not in manifest_files and (project_dir / src).is_file():
            pass  # declared move of a file that wasn't sent — still honour it
        elif src is None:
            try:
                incoming = e.extracted_path.read_bytes()
            except OSError:
                continue
            ext = Path(e.rel_path).suffix
            best: tuple[float, str] | None = None
            for rel in gone:
                if Path(rel).suffix != ext:
                    continue
                size = manifest_files[rel].get("size", 0)
                if size and not (0.5 <= len(incoming) / max(size, 1) <= 2.0):
                    continue
                original = base_reader.read(rel)
                if original is None:
                    try:
                        original = (project_dir / rel).read_bytes()
                    except OSError:
                        continue
                score = _similarity(incoming, original)
                if score >= _MOVE_SIMILARITY and (best is None or score > best[0]):
                    best = (score, rel)
            if best is None:
                continue
            src = best[1]
        if src is None or not (project_dir / src).is_file():
            continue

        e.status = FileStatus.MOVED
        e.moved_from = src
        e.removable = _unchanged_since_snapshot(src, manifest_files, project_dir)
        e.note = (
            f"moved from {src} — the old file stays unless you choose to remove it"
            if e.removable
            else f"moved from {src} — you've changed {src} since, so it won't be removed"
        )
        if src in gone:
            gone.remove(src)


def _requested_deletions(guide, entries, project_files: set[str], manifest_files: dict, project_dir: Path, rule) -> list:
    """Files the reply's CONTEXTZIP.md asks to delete — risky, never automatic."""
    if not guide or not guide.deletions:
        return []
    taken = {e.rel_path for e in entries} | {e.moved_from for e in entries if e.moved_from}
    out = []
    for raw in guide.deletions:
        target = raw if raw in project_files else (rule.apply(raw) if rule and rule.applies(raw) else None)
        if target is None or target not in project_files:
            hits = [p for p in project_files if p.endswith("/" + raw)]
            target = hits[0] if len(hits) == 1 else None
        if target is None or target in taken:
            continue
        taken.add(target)
        removable = _unchanged_since_snapshot(target, manifest_files, project_dir)
        out.append(
            ApplyEntry(
                rel_path=target,
                status=FileStatus.DELETE_REQUESTED,
                size=0,
                extracted_path=Path(),
                source_name=raw,
                how="exact" if target == raw else "rule",
                removable=removable,
                note=(
                    "the AI asked for this file to be deleted"
                    if removable
                    else "the AI asked to delete it, but you've changed it since — it won't be deleted"
                ),
            )
        )
    return out


def _classify(
    name: str,
    target: str | None,
    how: str,
    candidates: list[str],
    extracted: Path,
    project_dir: Path,
    manifest_files: dict,
    redacted: set[str],
    base_reader: _BaseReader,
) -> ApplyEntry:
    theirs = extracted.read_bytes()
    entry = ApplyEntry(
        rel_path=target or "",
        status=FileStatus.AMBIGUOUS,
        size=len(theirs),
        extracted_path=extracted,
        source_name=name,
        how=how,
        candidates=candidates,
        redacted=bool(target and target in redacted),
    )
    if target is None:
        entry.note = (
            f"could be {', '.join(candidates[:4])}" if candidates else "no matching file in the project"
        )
        return entry

    live_path = project_dir / target
    try:
        live = live_path.read_bytes() if live_path.is_file() else None
    except OSError:
        live = None
    baseline = manifest_files.get(target)
    base = base_reader.read(target) if baseline else None
    entry.base_text = _as_text(base) if base is not None else None
    entry.incoming = _adapt(theirs, live)

    theirs_text = _as_text(theirs)
    reference = _as_text(base) if base is not None else (_as_text(live) if live is not None else None)

    # ── Doesn't exist locally ────────────────────────────────────────────────
    if live is None:
        if baseline:
            entry.status = FileStatus.DELETED
            entry.note = "you deleted it after zipping — applying brings it back"
        else:
            entry.status = FileStatus.NEW
            entry.content = theirs
        return entry

    # ── Already the same ─────────────────────────────────────────────────────
    if _same(theirs, live):
        entry.status = FileStatus.UNCHANGED
        if baseline and not _matches_manifest(live, baseline):
            entry.note = "already applied"
        return entry

    # ── Placeholder comments instead of code ─────────────────────────────────
    if theirs_text is not None and reference is not None:
        marker = looks_partial(theirs_text, reference)
        if marker:
            entry.status = FileStatus.PARTIAL
            entry.note = f'contains "{marker[:60]}" — applying it would delete code'
            return entry

    # ── No baseline for a file that exists ───────────────────────────────────
    if not baseline:
        entry.status = FileStatus.UNTRACKED
        entry.note = STATUS_REASONS[FileStatus.UNTRACKED]
        return entry

    live_drifted = not _matches_manifest(live, baseline)

    # ── Only the AI changed it ───────────────────────────────────────────────
    if not live_drifted and not entry.redacted:
        entry.status = FileStatus.MODIFIED
        entry.content = entry.incoming
        return entry

    # ── Both changed (or a redacted file): three-way merge ───────────────────
    live_text = _as_text(live)
    base_text = entry.base_text
    if theirs_text is None or live_text is None or base_text is None:
        entry.status = FileStatus.DRIFTED
        entry.note = (
            "binary file — can't be merged"
            if theirs_text is None or live_text is None
            else "the zip you sent is gone, so there's nothing to merge against"
        )
        return entry

    merged = merge3(base_text.split("\n"), live_text.split("\n"), theirs_text.split("\n"))
    merged_text = "\n".join(merged.lines)
    if merged.clean and merged_text == live_text:
        entry.status = FileStatus.UNCHANGED
        entry.note = "nothing new from the AI"
        return entry
    if not merged.clean:
        entry.status = FileStatus.CONFLICT
        entry.conflict_text = merged_text
        n = len(merged.conflicts)
        entry.note = f"{n} place{'s' if n != 1 else ''} where you and the AI changed the same or neighbouring lines"
        return entry

    if entry.redacted and _REDACTED in merged_text and merged_text.count(_REDACTED) > live_text.count(_REDACTED):
        entry.status = FileStatus.CONFLICT
        entry.conflict_text = merged_text
        entry.note = "the AI's version still has [REDACTED] placeholders where your real values go"
        return entry

    entry.content = _encode_like(merged_text, live)
    if live_drifted:
        entry.status = FileStatus.MERGED
        entry.note = STATUS_REASONS[FileStatus.MERGED]
    else:
        entry.status = FileStatus.MODIFIED
        entry.note = "your secret values were kept" if entry.redacted else ""
    return entry


def discard_plan(plan: ApplyPlan) -> None:
    """Clean up the temp extraction dir without writing anything (dry-run / declined)."""
    shutil.rmtree(plan.extraction_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Executing the plan
# ---------------------------------------------------------------------------


def _move_zip_to_applied(
    zip_path: Path, project_dir: Path, workspace_root: Path, retain: int = 1
) -> Path:
    """
    Move a consumed inbox zip into .contextzip/inbox/applied/, timestamped,
    so it can't be accidentally re-applied. Zips passed by an explicit path
    outside the inbox are left where the user put them.

    Keeps at most *retain* applied zips (default 1 — only the most recent);
    the oldest are pruned first.
    """
    if zip_path.parent.resolve() != inbox_dir(project_dir).resolve():
        return zip_path

    applied_dir = workspace_root / _INBOX_DIRNAME / _APPLIED_DIRNAME
    applied_dir.mkdir(parents=True, exist_ok=True)

    retain = max(1, retain)
    existing = sorted(applied_dir.glob("*.zip"))
    overflow = len(existing) - (retain - 1)
    for old in existing[: max(0, overflow)]:
        try:
            old.unlink()
        except OSError:
            pass

    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = applied_dir / f"{stamp}-{zip_path.name}"
    try:
        shutil.move(str(zip_path), str(dest))
    except OSError:
        return zip_path
    return dest


# A decision for one entry, keyed by the entry's display_path:
#   "apply"  write entry.content (safe entries) or the AI's version (risky)
#   "theirs" write the AI's version, whatever the status
#   "keep"   leave the file as it is on disk
#   "skip"   same as keep
#   {"action": "custom", "content": "..."}       write this text
#   {"action": "apply", "target": "path/x.ts"}   place an ambiguous file here


def execute_plan(
    plan: ApplyPlan,
    project_dir: Path,
    retain: int = 1,
    *,
    decisions: dict | None = None,
    include_risky: bool = False,
    remove_moved: bool = False,
    allow_delete: bool = False,
) -> ApplyResult:
    """
    Write the plan into the project.

    Safe entries (new, modified, merged) are written unless a decision says
    otherwise. Risky entries are only written when *include_risky* is True
    (the AI's version wins) or a decision covers them. Ambiguous entries need
    a decision with a target. Unchanged entries are never touched.

    The zip is archived to .contextzip/inbox/applied/ when everything was
    settled; if something was skipped it stays in the inbox so apply-zip can
    be run again after resolving it.
    """
    decisions = decisions or {}
    workspace_root = _workspace_dir(project_dir)
    root = project_dir.resolve()

    written: list[str] = []
    skipped: list[str] = []
    removed: list[str] = []
    not_removed: list[tuple[str, str]] = []
    manifest_files = (load_manifest(plan.manifest_path).get("files", {}) if plan.manifest_path else {})

    def remove(rel: str) -> None:
        path = (root / rel).resolve()
        if not _is_within(path, root) or not path.is_file():
            not_removed.append((rel, "not found"))
        elif not _unchanged_since_snapshot(rel, manifest_files, root):
            not_removed.append((rel, "changed since the snapshot — kept"))
        else:
            path.unlink()
            removed.append(rel)

    for entry in plan.entries:
        if entry.status == FileStatus.UNCHANGED:
            continue

        decision = decisions.get(entry.display_path)
        action = decision.get("action") if isinstance(decision, dict) else decision

        if entry.status == FileStatus.DELETE_REQUESTED:
            # Deleting needs its own explicit yes — --yes alone never deletes.
            if action == "delete" or (action is None and allow_delete):
                remove(entry.rel_path)
            else:
                skipped.append(entry.display_path)
            continue
        target = entry.rel_path
        if isinstance(decision, dict) and decision.get("target"):
            target = str(decision["target"]).replace("\\", "/").strip("/")

        data: bytes | None
        if action in ("keep", "skip"):
            data = None
        elif action == "custom" and isinstance(decision, dict):
            live_path = root / target
            live = live_path.read_bytes() if live_path.is_file() else None
            text = str(decision.get("content", "")).replace("\r\n", "\n")
            data = _encode_like(text, live)
        elif action == "theirs":
            data = entry.incoming
        elif action == "apply" or (action is None and (entry.is_safe or include_risky)):
            data = entry.content if entry.is_safe else entry.incoming
        else:
            data = None

        if data is None or not target:
            skipped.append(entry.display_path)
            continue

        dest = (root / target).resolve()
        if not _is_within(dest, root) or target.split("/", 1)[0] in (".git", ".contextzip"):
            skipped.append(entry.display_path)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        written.append(target)

        if entry.status == FileStatus.MOVED and entry.moved_from:
            wants = (decision.get("remove_old") if isinstance(decision, dict) else None)
            if wants is True or (wants is None and remove_moved):
                remove(entry.moved_from)

    risky_left = [
        s for s in skipped
        if any(e.display_path == s and e.is_risky and e.status != FileStatus.DELETE_REQUESTED for e in plan.entries)
    ]
    if risky_left:
        applied_zip_path, kept = plan.zip_path, True
    else:
        applied_zip_path = _move_zip_to_applied(plan.zip_path, project_dir, workspace_root, retain=retain)
        kept = False
    shutil.rmtree(plan.extraction_dir, ignore_errors=True)

    if plan.manifest_path is not None and (written or removed) and not kept:
        from contextzip.snapshots import mark_applied

        mark_applied(plan.manifest_path)

    return ApplyResult(
        written=written,
        applied_zip_path=applied_zip_path,
        skipped=skipped,
        zip_kept=kept,
        removed=removed,
        not_removed=not_removed,
    )
