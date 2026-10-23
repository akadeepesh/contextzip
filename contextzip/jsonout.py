"""
jsonout.py — `--json`: one JSON object on stdout instead of terminal output.

For editors, scripts and anything else that drives contextzip. While a
--json run is active every Rich console is quiet, the ok/warn/err/info
lines from cli_display.py are collected instead of printed, nothing ever
prompts, and the command prints exactly one object when it finishes —
whether it succeeded or not:

    {"ok": true,  "command": "package", "version": "0.5.2", ..., "warnings": [], "log": [...]}
    {"ok": false, "command": "package", "version": "0.5.2", "error": "…", "warnings": [], "log": [...]}

The exit code matches "ok": 0 when it's true, non-zero when it's false.

Imported by cli_display.py, so nothing here imports from the cli modules.
"""

from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Session:
    command: str
    data: dict = field(default_factory=dict)
    log: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def record(self, level: str, text: str, detail: str | None = None) -> None:
        entry = {"level": level, "text": text}
        if detail:
            entry["detail"] = detail
        self.log.append(entry)
        if level == "warn":
            self.warnings.append(text)
        elif level == "error":
            self.errors.append(text)


_active: Session | None = None


def active() -> Session | None:
    """The running --json session, or None for normal terminal output."""
    return _active


def put(key: str, value) -> None:
    """Set a top-level field of the JSON result. No-op outside --json."""
    if _active is not None:
        _active.data[key] = value


@contextmanager
def session(command: str, enabled: bool, consoles=()):
    """
    Run a command body in --json mode when *enabled*: silence *consoles*,
    collect output, and print the result object on the way out — including
    when the body exits through SystemExit or an unexpected exception.
    """
    global _active
    if not enabled:
        yield None
        return

    s = Session(command)
    previous = [(c, c.quiet) for c in consoles]
    for c in consoles:
        c.quiet = True
    _active = s
    code: int = 0
    try:
        yield s
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    except Exception as exc:  # noqa: BLE001 — reported in the JSON instead of a traceback
        s.errors.append(f"{type(exc).__name__}: {exc}")
        code = 1
    finally:
        _active = None
        for c, quiet in previous:
            c.quiet = quiet

    # A few paths report an error and return normally ("nothing to
    # compress"); for a caller reading JSON that's still a failure.
    if code == 0 and s.errors:
        code = 1
    _emit(s, code)
    if code:
        raise SystemExit(code)


def _emit(s: Session, code: int) -> None:
    from contextzip import __version__

    ok = code == 0
    payload: dict = {"ok": ok, "command": s.command, "version": __version__}
    if not ok:
        payload["error"] = s.errors[-1] if s.errors else "failed"
    payload.update(s.data)
    payload["warnings"] = s.warnings
    payload["log"] = s.log
    # ASCII-only, so a Windows console code page can never garble or reject it.
    sys.stdout.write(json.dumps(payload, ensure_ascii=True, default=_default) + "\n")
    sys.stdout.flush()


def _default(value):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    raise TypeError(f"not JSON serialisable: {type(value).__name__}")


# ---------------------------------------------------------------------------
# Serialisers — the shape of each result, defined in one place
# ---------------------------------------------------------------------------


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def detection_data(detection) -> dict:
    return {
        "ecosystems": list(detection.ecosystems),
        "display": detection.display_name,
        "confidence": detection.confidence,
        "rules": list(detection.rule_modules),
        "sources": dict(detection.sources),
    }


def files_data(resolved, project_dir: Path, *, verbose: bool, git_mode: bool) -> dict:
    from contextzip.tokens import estimate_tokens

    binary = set(resolved.binary_files)
    sizes = {p: (p.stat().st_size if p.exists() else 0) for p in resolved.included}
    out: dict = {
        "included": len(resolved.included),
        "included_bytes": sum(sizes.values()),
        "estimated_tokens": estimate_tokens(sum(n for p, n in sizes.items() if p not in binary)),
        "included_files": [_rel(p, project_dir) for p in sorted(resolved.included)],
        "large_files": [{"path": _rel(p, project_dir), "bytes": n} for p, n in resolved.large_files],
        "binary_files": [_rel(p, project_dir) for p in resolved.binary_files],
        "skipped": [{"path": _rel(p, project_dir), "reason": r} for p, r in resolved.skipped],
    }
    if not git_mode:
        out["scanned"] = len(resolved.included) + len(resolved.excluded) + len(resolved.skipped)
        out["excluded"] = len(resolved.excluded)
    if verbose:
        out["excluded_files"] = [_rel(p, project_dir) for p in sorted(resolved.excluded)]
    return out


def package_data(result, project_dir: Path) -> dict:
    return {
        "path": str(result.zip_path),
        "files": result.file_count,
        "bytes": result.uncompressed_bytes,
        "compressed_bytes": result.compressed_bytes,
        "estimated_tokens": result.estimated_tokens,
        "snapshot": result.snapshot,
        "snapshot_id": result.snapshot_id,
        "redacted": [{"path": rel.as_posix(), "patterns": names} for rel, names in result.redacted],
        "skipped": [{"path": _rel(p, project_dir), "reason": r} for p, r in result.skipped_in_zip],
    }


def clipboard_data(cb) -> dict:
    return {
        "copied": cb.tier.name == "FILE_ON_CLIPBOARD",
        "method": cb.tier.name.lower(),
        "message": cb.message,
        "hint": cb.hint,
    }


def remote_data(fetched) -> dict:
    return {
        "repo": fetched.spec.slug,
        "sha": fetched.sha,
        "ref": fetched.ref,
        "subdir": fetched.subdir,
        "cached": fetched.cached,
        "download_bytes": fetched.download_bytes,
        "skipped": [{"path": p, "reason": r} for p, r in fetched.skipped],
    }


def plan_data(plan) -> dict:
    from collections import Counter

    from contextzip.applier import STATUS_REASONS

    counts = Counter(e.status.value for e in plan.entries)
    return {
        "zip": str(plan.zip_path),
        "snapshot": plan.snapshot if plan.has_manifest else None,
        "matched_by_guide": plan.matched_by_guide,
        "snapshot_note": plan.manifest_note,
        "remap": plan.rule.describe() if plan.rule is not None else None,
        "structure_warning": plan.structure_warning,
        "needs_decision": plan.is_risky,
        "counts": dict(counts),
        "files": [
            {
                "path": e.rel_path or None,
                "source": e.source_name,
                "status": e.status.value,
                "safe": e.is_safe,
                "reason": STATUS_REASONS.get(e.status, ""),
                "matched_by": e.how,
                "note": e.note or None,
                "candidates": list(e.candidates),
                "moved_from": e.moved_from,
                "removable": e.removable,
                "redacted": e.redacted,
                "size": e.size,
            }
            for e in plan.entries
        ],
        "ignored": list(plan.ignored),
    }


def apply_result_data(result) -> dict:
    return {
        "written": list(result.written),
        "skipped": list(result.skipped),
        "removed": list(result.removed),
        "not_removed": [{"path": p, "reason": r} for p, r in result.not_removed],
        "zip_kept": result.zip_kept,
        "archived_to": None if result.zip_kept else str(result.applied_zip_path),
    }
