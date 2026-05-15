# Changelog

All notable changes to contextzip are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
This project uses [Semantic Versioning](https://semver.org/).

---

## [0.1.1] — 2026-05-15

### Fixed
- LICENSE link in README now points to GitHub instead of a relative path that broke on PyPI
- License metadata updated to SPDX string format (`"MIT"`) to comply with setuptools ≥ 77

---

## [0.1.2] — 2026-05-15

### Added
- Smart framework and language detection (Next.js, Node.js, Python, Django, FastAPI, Rust, Go, Ruby)
- Automatic exclusion rules per detected ecosystem
- `.gitignore` patterns respected automatically (`--no-gitignore` to bypass)
- `--include` flag for scoping to specific directories (exact path-boundary matching)
- `--exclude` flag for additional gitignore-style exclusion patterns
- `--dry-run` flag to preview without creating any files
- `--verbose` flag showing every file with its size
- `--output` flag for custom ZIP destination
- `--no-clipboard` flag to skip clipboard/folder-open step
- Tiered clipboard strategy: file-on-clipboard (macOS/Linux), Explorer with file selected (Windows), path fallback
- Warnings for large files (≥ 1 MB), binary files, and skipped files (dangling symlinks, permission errors)
- Rich terminal output with panels, progress bars, and colour-coded detection results
- Compression stats (ratio, size before/after) shown after ZIP creation

---

## [0.2.0] — 2026-05-15

### Added
- `--git-changes` mode for packaging only files reported as changed by git
- Automatic detection of:
  - staged files
  - unstaged modifications
  - untracked files
- Dedicated git-aware packaging pipeline optimised for AI/code review workflows
- Rich git change summary panel with staged/unstaged/untracked breakdown
- Verbose git mode output showing categorized changed files
- Graceful handling for:
  - repositories with no changes
  - deleted files
  - submodules
  - repositories outside the selected project tree
  - missing git installations
  - non-git directories
- Support for monorepo-aware repository root resolution
- Binary and large-file checks now also apply in git-changes mode

### Improved
- File resolution architecture refactored to support multiple packaging strategies cleanly
- Internal file scanning pipeline separated from git-based file selection logic
- CLI output consistency improved between standard scan mode and git-changes mode

### Developer Notes
- Introduced new `contextzip.git` module for git integration and porcelain parsing
- Added `resolve_files_from_git()` pipeline for pre-selected file resolution
- Added structured `GitChanges` and `GitError` models for cleaner error handling and extensibility