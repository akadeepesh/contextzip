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

---

## [0.2.1] — 2026-05-16

### Fixed
- `--exclude` / `-e` patterns containing Windows-style paths (e.g. `.\CHANGELOG.md`) were silently ignored due to backslash and leading `.\` — patterns are now normalized to forward-slash posix paths before matching
- Files passed to `-e` were not being excluded from the ZIP despite appearing in the exclusion list

### Improved
- `-e` now accepts multiple space-separated patterns in a single flag invocation: `-e file1 file2 folder/` in addition to the existing repeatable form `-e file1 -e file2`
- Folder exclusion via `-e folder/*` is now supported and normalized to `folder/` automatically

## [0.2.2] - 2026-05-16

### Added
- `contextzip exclude PATTERN…` subcommand — exclude multiple files and folders
  in a single invocation without repeating `-e` (e.g. `contextzip exclude CHANGELOG.md LICENSE .github/`)
- `contextzip include PATH…` subcommand — symmetric counterpart to `exclude`;
  packages only the specified paths (e.g. `contextzip include src/ app/`)
- All modifier flags (`--dry-run`, `--verbose`, `--output`, `--no-clipboard`,
  `--git-changes`, `--no-gitignore`) work on both subcommands

### Changed
- `-e` / `--exclude` flag on the main command restored and kept for full
  backwards compatibility alongside the new subcommand
- Warning messages for large files now mention both `-e PATTERN` and
  `contextzip exclude PATTERN` as remediation options
- `_normalize_pattern` promoted to a named module-level function (testable,
  documented); behaviour is unchanged

### Fixed
- Removed the previous positional-argument approach to exclusions on the main
  command, which was ambiguous and undiscoverable

---

## [0.2.3] — 2026-05-19

### Added
- Dedicated Ruby ecosystem exclusion rules via new `contextzip.rules.ruby` module
- Automatic exclusion of common Ruby / Rails artifacts:
  - Bundler directories and lockfiles
  - Rails logs, temp files, assets, storage, and Spring cache
  - Built gems and compiled Ruby extensions
  - Coverage and generated documentation directories
- Additional package discovery keywords for AI tooling and code packaging:
  - `claude`
  - `chatgpt`
  - `code-packaging`

### Changed
- Ruby project detection now uses the dedicated Ruby rule module instead of the generic base rules
- Git-based packaging now enforces base exclusion safety rules even for tracked files
- Repository governance and metadata files are now excluded by default:
  - `CHANGELOG*`
  - `LICENSE*`
  - `CONTRIBUTING*`
  - `SECURITY.md`
  - `.github` issue / PR templates
- `.gitignore` is no longer excluded automatically from packaged context archives

### Fixed
- Prevented git-tracked sensitive files (e.g. `.env`, secrets, binaries) from being included in `--git-changes` mode
- Improved ZIP packaging resilience when file stat operations fail during archive creation
- Fixed progress tracking during ZIP generation to avoid repeated filesystem stat calls
- Fixed Ruby framework detection routing so Ruby projects receive ecosystem-specific exclusion behaviour

---

## [0.2.4] — 2026-05-20

### Added

* Persistent `.contextzip/` workspace directory for generated context packages
* Automatic output naming based on packaging mode:

  * `codebase.zip` for standard packaging
  * `changes.zip` for `--git-changes`
* Automatic `.contextzip/` exclusion from packaged archives and file scanning
* Automatic `.gitignore` integration:

  * `.contextzip/` is appended when a git repository is detected
  * missing entries are added only once
* Repository-root-aware workspace placement:

  * `.contextzip/` is created at the git repository root when `.git/` is detected
  * falls back to the current working directory outside git repositories

### Changed

* ZIP outputs are now written to the persistent `.contextzip/` workspace by default instead of temporary system directories
* Generated archives now overwrite previous outputs with the same logical name instead of creating temporary randomized filenames
* `--output` now bypasses all `.contextzip/` workspace logic and writes directly to the user-specified destination

### Improved

* Generated context packages are now easier to rediscover, reuse, and inspect across AI-assisted development workflows
* Workspace output structure now provides a foundation for future features such as:

  * timestamped package history
  * metadata indexing
  * incremental packaging
  * AI debug sessions
  * package caching

### Fixed

* Prevented `.contextzip/` contents from recursively packaging previous generated archives
* Improved fallback behaviour when workspace creation fails due to filesystem permissions or read-only directories