"""
webui/suggestions.py — "You probably want to exclude these" detection for
the local config UI.

Purely a UX nicety: it surfaces common non-code file types that show up
in a project's *currently included* set but aren't already covered by
contextzip's built-in exclusion rules (rules/base.py etc. already handle
images, executables, archives, logs — those never show up here, since a
suggestion is only useful if it's genuinely new information). Nothing in
this module affects actual packaging behavior; it only feeds the UI's
one-click "exclude all" chips.
"""

from __future__ import annotations

from pathlib import Path

# Keep in sync with filters.LARGE_FILE_WARN_BYTES — same threshold contextzip
# already warns about during a normal run, so the number in the UI won't
# surprise anyone who's used the CLI before.
LARGE_FILE_BYTES = 1 * 1024 * 1024

# extension -> friendly label. Deliberately excludes anything already
# covered by rules/base.py (images, exe/dll/so, zip/tar/gz/rar, logs, .env,
# etc.) so every entry here is a genuinely new suggestion, not a restatement
# of what contextzip already strips by default.
_SUGGESTABLE_EXTENSIONS: dict[str, str] = {
    ".pdf": "PDFs",
    ".doc": "Word docs",
    ".docx": "Word docs",
    ".xls": "Excel files",
    ".xlsx": "Excel files",
    ".ppt": "PowerPoint files",
    ".pptx": "PowerPoint files",
    ".csv": "CSV data",
    ".tsv": "TSV data",
    ".db": "Database files",
    ".sqlite": "Database files",
    ".sqlite3": "Database files",
    ".parquet": "Parquet data",
    ".woff": "Web fonts",
    ".woff2": "Web fonts",
    ".ttf": "Fonts",
    ".otf": "Fonts",
    ".eot": "Web fonts",
    ".webp": "Images",
    ".bmp": "Images",
    ".tiff": "Images",
    ".tif": "Images",
    ".heic": "Images",
    ".avif": "Images",
    ".mov": "Videos",
    ".avi": "Videos",
    ".mkv": "Videos",
    ".webm": "Videos",
    ".flv": "Videos",
    ".wav": "Audio",
    ".flac": "Audio",
    ".ogg": "Audio",
    ".m4a": "Audio",
    ".aac": "Audio",
    ".7z": "Archives",
    ".iso": "Archives",
    ".psd": "Design files",
    ".ai": "Design files",
    ".sketch": "Design files",
    ".fig": "Design files",
    ".eps": "Design files",
    ".apk": "Mobile builds",
    ".ipa": "Mobile builds",
    ".aab": "Mobile builds",
    ".wasm": "WASM binaries",
    ".ipynb": "Jupyter notebooks",
}

_MAX_SUGGESTIONS = 10


def build_suggestions(included_files: list[tuple[str, int]]) -> list[dict]:
    """
    *included_files*: (rel_posix_path, size_bytes) pairs for files
    currently classified as included — already-excluded files don't need
    suggesting again.

    Returns suggestion dicts, largest total size first:
      {"id": "ext:.pdf", "label": "PDFs", "pattern": "*.pdf",
       "count": 6, "bytes": 4200000}
    or, for the size-based suggestion (no single glob fits):
      {"id": "large-files", "label": "Large files (>1MB)", "pattern": None,
       "paths": [...], "count": 3, "bytes": 9400000}

    Capped at _MAX_SUGGESTIONS so the UI doesn't get cluttered on a huge
    or unusually mixed-content repo.
    """
    by_ext: dict[str, list[tuple[str, int]]] = {}
    large: list[tuple[str, int]] = []

    for rel_path, size in included_files:
        ext = Path(rel_path).suffix.lower()
        if ext in _SUGGESTABLE_EXTENSIONS:
            by_ext.setdefault(ext, []).append((rel_path, size))
        if size >= LARGE_FILE_BYTES:
            large.append((rel_path, size))

    suggestions: list[dict] = []

    for ext, entries in by_ext.items():
        total_bytes = sum(size for _, size in entries)
        suggestions.append(
            {
                "id": f"ext:{ext}",
                "label": _SUGGESTABLE_EXTENSIONS[ext],
                "detail": f"*{ext}",
                "pattern": f"*{ext}",
                "paths": None,
                "count": len(entries),
                "bytes": total_bytes,
            }
        )

    if large:
        total_bytes = sum(size for _, size in large)
        suggestions.append(
            {
                "id": "large-files",
                "label": "Large files (>1MB)",
                "detail": f"{len(large)} file(s) over 1MB",
                "pattern": None,
                "paths": [path for path, _ in large],
                "count": len(large),
                "bytes": total_bytes,
            }
        )

    suggestions.sort(key=lambda s: -s["bytes"])
    return suggestions[:_MAX_SUGGESTIONS]
