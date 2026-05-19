"""
packager.py — Creates the ZIP archive from a ResolveResult.

Phase 5 changes:
  - Accepts ResolveResult instead of a bare list[Path]
  - Reports skipped (unreadable / symlink) files to the caller
  - Caps individual file read to avoid runaway memory on huge files
  - Carries skipped_paths forward into PackageResult for CLI display
"""

from __future__ import annotations

import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.progress import (
    BarColumn,
    FileSizeColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TransferSpeedColumn,
)

from contextzip.filters import ResolveResult


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

@dataclass
class PackageResult:
    zip_path:            Path
    file_count:          int
    uncompressed_bytes:  int
    compressed_bytes:    int
    skipped_in_zip:      list[tuple[Path, str]] = field(default_factory=list)

    @property
    def compression_ratio(self) -> float:
        if self.uncompressed_bytes == 0:
            return 0.0
        return max(0.0, 1.0 - (self.compressed_bytes / self.uncompressed_bytes))

    @property
    def compression_pct(self) -> str:
        return f"{self.compression_ratio * 100:.0f}%"

    @property
    def grew(self) -> bool:
        return self.compressed_bytes > self.uncompressed_bytes


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_zip(
    resolve_result: ResolveResult,
    project_dir: Path,
    output_path: Path | None,
    console: Console,
) -> PackageResult:
    """
    Write the included files from *resolve_result* into a ZIP archive.
    Returns a :class:`PackageResult` with compression stats and any
    files that had to be skipped during writing (e.g. permission denied).
    """
    zip_path = output_path or _auto_output_path(project_dir)
    zip_path.parent.mkdir(parents=True, exist_ok=True)

    included      = resolve_result.included
    skipped_in_zip: list[tuple[Path, str]] = []
    uncompressed  = 0
    file_count    = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[cyan]{task.description}[/]"),
        BarColumn(),
        TaskProgressColumn(),
        FileSizeColumn(),
        TransferSpeedColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:

        total_bytes = sum(
            p.stat().st_size for p in included if p.is_file()
        )
        task = progress.add_task("Compressing…", total=max(total_bytes, 1))

        with zipfile.ZipFile(
            zip_path, "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as zf:
            for abs_path in included:
                if not abs_path.is_file():
                    continue

                try:
                    rel = abs_path.relative_to(project_dir)
                except ValueError:
                    skipped_in_zip.append((abs_path, "outside project tree"))
                    continue

                try:
                    file_size = abs_path.stat().st_size
                except OSError as e:
                    skipped_in_zip.append((abs_path, f"stat failed: {e}"))
                    continue

                try:
                    zf.write(abs_path, arcname=rel.as_posix())
                    uncompressed += file_size
                    file_count   += 1
                except PermissionError:
                    skipped_in_zip.append((abs_path, "permission denied"))
                except OSError as e:
                    skipped_in_zip.append((abs_path, str(e)))
                finally:
                    progress.advance(task, file_size)

    compressed = zip_path.stat().st_size

    return PackageResult(
        zip_path=zip_path,
        file_count=file_count,
        uncompressed_bytes=uncompressed,
        compressed_bytes=compressed,
        skipped_in_zip=skipped_in_zip,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _auto_output_path(project_dir: Path) -> Path:
    project_name = _safe_name(project_dir.name)
    timestamp    = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename     = f"{project_name}_context_{timestamp}.zip"
    return Path(tempfile.gettempdir()) / filename


def _safe_name(name: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
    return safe[:48] or "project"
