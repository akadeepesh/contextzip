"""
packager.py — Creates the ZIP archive from the resolved file list.

Responsibilities:
  - Resolve the output path (custom or auto-generated in temp dir)
  - Write files into the ZIP preserving relative paths
  - Return a PackageResult with stats for the CLI to display
"""

from __future__ import annotations

import tempfile
import zipfile
from dataclasses import dataclass
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


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

@dataclass
class PackageResult:
    zip_path: Path
    file_count: int
    uncompressed_bytes: int
    compressed_bytes: int

    @property
    def compression_ratio(self) -> float:
        """0.0–1.0 — how much smaller the zip is vs raw source. Clamped to 0 minimum."""
        if self.uncompressed_bytes == 0:
            return 0.0
        ratio = 1.0 - (self.compressed_bytes / self.uncompressed_bytes)
        return max(0.0, ratio)

    @property
    def compression_pct(self) -> str:
        return f"{self.compression_ratio * 100:.0f}%"

    @property
    def grew(self) -> bool:
        """True when the zip is larger than the source (tiny projects)."""
        return self.compressed_bytes > self.uncompressed_bytes


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_zip(
    included: list[Path],
    project_dir: Path,
    output_path: Path | None,
    console: Console,
) -> PackageResult:
    """
    Write *included* files into a ZIP archive and return a :class:`PackageResult`.

    Parameters
    ----------
    included:
        Absolute paths of files to pack (all must be under *project_dir*).
    project_dir:
        The project root; used to compute relative paths inside the ZIP.
    output_path:
        Explicit output location, or ``None`` to auto-generate one in the
        system temp directory.
    console:
        Rich console for progress display.
    """
    zip_path = output_path or _auto_output_path(project_dir)

    # Ensure parent directory exists (relevant when --output includes sub-dirs)
    zip_path.parent.mkdir(parents=True, exist_ok=True)

    uncompressed = 0
    file_count = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[cyan]{task.description}[/]"),
        BarColumn(),
        TaskProgressColumn(),
        FileSizeColumn(),
        TransferSpeedColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=True,   # clears the bar when done, keeps the terminal clean
    ) as progress:

        total_bytes = sum(p.stat().st_size for p in included if p.is_file())
        task = progress.add_task("Compressing…", total=total_bytes)

        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for abs_path in included:
                if not abs_path.is_file():
                    continue

                rel = abs_path.relative_to(project_dir)
                file_size = abs_path.stat().st_size

                try:
                    zf.write(abs_path, arcname=rel.as_posix())
                    uncompressed += file_size
                    file_count += 1
                except (OSError, PermissionError):
                    # Skip files we can't read (locked, permission denied, etc.)
                    pass

                progress.advance(task, file_size)

    compressed = zip_path.stat().st_size

    return PackageResult(
        zip_path=zip_path,
        file_count=file_count,
        uncompressed_bytes=uncompressed,
        compressed_bytes=compressed,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _auto_output_path(project_dir: Path) -> Path:
    """
    Build a timestamped filename like ``myproject_context_20260514_153042.zip``
    inside the system temp directory.
    """
    project_name = _safe_name(project_dir.name)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{project_name}_context_{timestamp}.zip"
    return Path(tempfile.gettempdir()) / filename


def _safe_name(name: str) -> str:
    """Strip characters that are unsafe in filenames."""
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
    return safe[:48] or "project"  # cap length, ensure non-empty
