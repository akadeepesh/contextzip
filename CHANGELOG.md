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

## [0.3.0] — 2026-05-24

### Added

* `--prompt` / `-p` flag for AI-powered file selection — describe your task in
  plain English and contextzip automatically selects the minimum relevant files
  (e.g. `contextzip --prompt "Change toast color on failed login"`)
* Gemini 2.5 Flash Lite integration via a thin `httpx` client — no Google SDK
  required; a single lightweight POST call to the Gemini API
* First-run onboarding flow for users without an API key:
  * Displays a guided panel with a link to [Google AI Studio](https://aistudio.google.com/apikey) (free, no credit card)
  * Optionally opens the browser automatically
  * Saves the key persistently on confirmation
* Persistent API key storage in the platform-appropriate config directory:
  * `~/.config/contextzip/config.json` on Linux / macOS
  * `%APPDATA%\contextzip\config.json` on Windows
  * `GEMINI_API_KEY` environment variable always takes precedence
* `contextzip config` subcommand for key management:
  * `contextzip config` — show current key status and source
  * `contextzip config --reset-key` — clear stored key and re-run onboarding
  * `contextzip config --show-key-path` — print config file location
* `prompt.txt` is written as the first entry in every AI-generated ZIP,
  containing the task description, detected framework, and selected file list —
  so any AI tool receiving the archive immediately knows the context
* Keyword-based heuristic file scorer as a rate-limit fallback:
  * Activates only on confirmed HTTP 429 from Gemini
  * Scores files by token overlap with the prompt, directory semantics, and
    extension type
  * User is warned clearly that heuristic was used and Gemini results are
    preferred
* `--prompt --dry-run` combination — preview AI-selected files without
  creating a ZIP
* API key validation on load — keys not starting with `AIza` are treated as
  unconfigured and trigger onboarding rather than a cryptic API error
* `diagnose_api_key()` surfaces a human-readable explanation when a key exists
  but fails validation, before any API call is attempted

### Changed

* `create_zip()` in `packager.py` now accepts an optional `prompt_txt`
  parameter; when provided, `prompt.txt` is written as the first ZIP entry
* xdg-open on Linux now runs with stdout and stderr suppressed to prevent
  KDE / DBus / MIME warnings from appearing over the API key prompt

### Developer Notes

* Introduced `contextzip.config` module for cross-platform config path
  resolution and key read / write / delete operations
* Introduced `contextzip.ai` package with three modules:
  * `gemini.py` — Gemini API client with typed exception hierarchy
    (`GeminiError`, `GeminiRateLimitError`, `GeminiUnavailable`)
  * `heuristic.py` — keyword + directory scoring fallback scorer
  * `selector.py` — project map builder, AI orchestration, `prompt.txt`
    generation; returns `(paths, prompt_txt, method)` so callers know
    which selection path was taken
* Added `httpx` as a required runtime dependency

## [0.3.1] — 2026-05-25

### Added

* `contextzip watch -- <command>` subcommand — wraps any dev server or build
  process, buffers its output, detects errors automatically, and packages a
  debug-ready ZIP in a single keypress
  * Supports `npm run dev`, `python manage.py runserver`, and any process
    that writes errors to stdout/stderr
  * On error detection, renders an inline prompt beneath the error output:
    `[D] package debug context   [S] skip`
  * Pressing `D` writes `.contextzip/debug-context.zip` immediately; the
    wrapped process continues running uninterrupted
  * On `Ctrl+C`, if no errors were packaged during the session, a final
    prompt offers to capture the full session output as a fallback
* `debug-context.zip` — flat ZIP structure produced by `watch`:
  * `prompt.txt` — auto-generated from detected framework, error type, and
    referenced files; no `--prompt` flag required from the user
  * `terminal-error.txt` — cleaned, noise-stripped error block and stack trace
  * `source-files.zip` — inner ZIP containing source files extracted from
    stack frames, with relative paths preserved
* `contextzip/error_parser.py` — full terminal output processing pipeline:
  * `strip_ansi()` — removes all CSI, OSC, and Fe escape sequences and bare
    carriage returns from raw process output
  * `detect_error_block()` — backward scan through the rolling buffer to find
    the most recent error; walks back from terminal exception lines to capture
    the full block including the `Traceback` header
  * `extract_paths()` — runs per-framework path regexes on the error block,
    resolves relative and bare filenames, and filters out stdlib, venv, and
    `node_modules` paths
  * `strip_noise()` — removes known noise lines (request logs, startup
    banners, HMR chatter) and collapses blank line runs
  * `build_prompt_txt()` — generates structured, AI-ready `prompt.txt` content
  * `process_buffer()` — convenience function running the full pipeline in a
    single call
* `contextzip/watcher.py` — process management and packaging:
  * Spawns child via `subprocess.Popen` with piped stdout/stderr; two daemon
    threads drain pipes concurrently into a `deque(maxlen=2000)` rolling buffer
  * Detection thread polls the buffer every 300ms using `detect_error_block()`
  * Error deduplication: MD5 of the first three non-blank lines of an error
    block; same signature within a session suppresses repeat prompts
  * Single-keypress D/S prompt via `termios`/`tty` raw mode on Unix and
    `msvcrt` on Windows, with an `input()` fallback for unusual environments
  * Child terminated cleanly on `Ctrl+C`: SIGTERM → 3-second grace period →
    SIGKILL
* `contextzip/rules/errors/python.py` — error detection pattern sets for
  Python, Django, and FastAPI:
  * 18 error start patterns covering tracebacks, syntax errors, Django/DRF
    exceptions, uvicorn/gunicorn ERROR logs, and pytest failures
  * 4 stack frame path patterns
  * 21 noise patterns for Django startup, request logs, pytest collection
    output, and pip install chatter
* `contextzip/rules/errors/node.py` — error detection pattern sets for
  Node.js, Next.js, and React:
  * 33 error start patterns covering JS runtime errors, Next.js error symbols
    (`✗`, `×`, `⨯`), webpack/bundler errors, npm errors, and unhandled
    promise rejections
  * 7 stack frame and import path patterns
  * 32 noise patterns for Next.js/Vite/CRA startup, HMR chatter, and request
    logs

### Changed

* `cli.py` refactored from a single 1,100-line file into four focused modules
  with no behaviour changes:
  * `cli.py` — Click group, four subcommands, and `_run()`; routing only
  * `cli_display.py` — all Rich rendering helpers (`print_detection`,
    `print_scan_summary`, `print_file_warnings`, `print_package_result`, etc.)
  * `cli_ai.py` — AI selection orchestration and `normalize_pattern`
  * `cli_onboard.py` — Gemini API key onboarding flow and browser launch
* `pyproject.toml` updated to include `contextzip.rules.errors` in package
  discovery

### Developer Notes

* `contextzip/rules/errors/` follows the same pattern as `contextzip/rules/`:
  one file per ecosystem, each exporting `ERROR_START_PATTERNS`,
  `PATH_PATTERNS`, and `NOISE_PATTERNS` as lists of compiled `re.Pattern`
  objects — adding support for a new framework means adding one file
* `_load_patterns()` in `error_parser.py` maps ecosystem names from
  `DetectionResult.ecosystems` to rule modules; unrecognised ecosystems fall
  back to loading both Python and Node patterns as a best-effort default
* `cli_display.py` functions accept an optional `con: Console` parameter
  (defaulting to the module-level console) making every display function
  independently testable without monkey-patching
* No new runtime dependencies introduced — `watch` uses only stdlib
  (`subprocess`, `threading`, `queue`, `termios`/`msvcrt`, `zipfile`)

## [0.3.2] — 2026-05-25

### Added

* **Python API** — contextzip is now usable as a library, not just a CLI tool:
  * `get_git_changes(path?)` — returns a `FileCollection` of git-modified,
    added, and untracked files; applies base safety rules (no secrets, no
    binaries) automatically
  * `get_files(path?, include?, exclude?, use_gitignore?)` — returns a
    `FileCollection` of all project files after applying ecosystem rules,
    `.gitignore`, and any extra exclusion patterns
  * `create_zip(collection, output?)` — writes a `FileCollection` to a ZIP
    archive and returns a `PackageResult`; output path is caller-controlled
  * `detect_ecosystem(path?)` — returns a `DetectionResult` with ecosystem
    names, display string, and confidence level
  * `FileCollection` — unified return type for `get_git_changes` and
    `get_files`; supports iteration, `len()`, and `bool()` directly; `.files`
    is always a plain `list[pathlib.Path]` usable without zipping
  * `PackageResult` re-exported from the top-level package
  * Exception hierarchy: `ContextzipError` (base), `NotARepositoryError`,
    `GitNotFoundError`, `GitCommandError`, `NoFilesError`
  * All API functions default `path` to `Path.cwd()`, matching CLI behaviour

* `contextzip/api.py` — new module implementing the public API; no Click, no
  Rich output, no `SystemExit`; raises typed exceptions on failure
* `contextzip/packager.py` — added `create_zip_silent()` and
  `_workspace_output_path_silent()`: same ZIP logic as the CLI path but
  without a `Console` dependency, used internally by the API

### Changed

* `contextzip/__init__.py` — now exports the full public API
  (`get_git_changes`, `get_files`, `create_zip`, `detect_ecosystem`,
  `FileCollection`, `PackageResult`, and all exception types) so
  `from contextzip import ...` works without knowing internal module paths

### Fixed

* `filters.py` — symlink guard inner `try` block repeated the identical
  `relative_to` call that already failed in the outer block; the fallback now
  correctly uses the pre-resolve (`original_path`) to compute the relative
  path for symlinks that point outside the project tree
* `watcher.py` — `_drain_output_queue` silently consumed sentinel values
  (`raw=None`) with `continue`, making them invisible to `_check_reader_done`;
  sentinels are now put back into the queue so done flags are set correctly
* `watcher.py` — `_read_key_windows` had no timeout and would spin forever in
  non-interactive terminals; now uses a 60-second `monotonic` deadline
  matching the existing Unix `select` timeout
* `packager.py` — removed unused `_safe_name` helper
* `ai/gemini.py` — corrected stale docstring (`gemini-2.0-flash-lite` →
  `gemini-2.5-flash-lite`) to match the actual `DEFAULT_MODEL` constant

## [0.3.3] — 2026-05-29

### Fixed

* `contextzip.ai` and all subpackages were missing from the published wheel
  due to an incomplete `include` allowlist in `pyproject.toml` — replaced
  with auto-discovery so all current and future subpackages are captured
  automatically

## [0.3.4] — 2026-06-21

### Added

* `contextzip eod` — builds a paste-ready end-of-day prompt from today's
  Claude/ChatGPT conversation plus whatever code changed, copied straight
  to the clipboard. Does no summarizing itself — that's left to whichever
  AI tool the prompt gets pasted into
* `contextzip handoff` — same pipeline, aimed at continuing a project in a
  fresh chat after hitting a usage limit, rather than at an end-of-day report
* Per-branch commit checkpoints (`.contextzip/markers/eod.json`,
  `.contextzip/markers/handoff.json`) so every run only reports what's new
  since *that command* last ran on *that branch* — no manual bookkeeping,
  no date-based guessing
* Three-case code-change resolution per file, checked in priority order:
  1. **Not pushed** — diff against the upstream branch (covers committed-
     but-unpushed commits and uncommitted edits in one comparison) + the
     complete current file
  2. **Diverged from Claude** — diff against the version Claude last
     produced, matched by filename against a local artifacts folder + the
     complete current codebase file
  3. **Since last run** — diff against the stored marker commit, or the
     branch's merge-base with the default branch on a branch never tracked
     before + the complete current file

  Brand-new untracked files skip the diff (no baseline exists) and are
  included as full content only
* Best-effort fetching of Claude's artifact files for case 2, using the
  user's own Claude.ai session key (`contextzip config --set-session-key`).
  Explicitly degrades gracefully — a missing key, an expired cookie, or the
  endpoint changing shape just skips case 2 with a warning, never fails the
  whole `eod`/`handoff` run
* `contextzip config --set-session-key` / `--reset-session-key` — manage
  the Claude session key alongside the existing Gemini key; default
  `contextzip config` now shows both keys' status
* Text-clipboard support (`clipboard.handle_text()`) — `eod`/`handoff`
  produce a prompt to paste, not a file to upload, so the existing
  file-clipboard tiers gained a text-mode sibling (`pbcopy` / `xclip`+`xsel`
  / `clip.exe`)
* `exports/` added to the base exclusion ruleset (next to `.contextzip/`) —
  conversation exports and fetched Claude artifacts are contextzip's own
  working files, never code to package or report on

### Changed

* `packager._ensure_gitignore()` generalised to accept an arbitrary entry
  instead of being hardcoded to `.contextzip/`, so `exports/` can register
  itself in `.gitignore` the same way on first use

### Developer Notes

* New modules: `markers.py` (per-branch checkpoint storage), `code_changes.py`
  (the three-case decision tree), `claude_export.py` (locating the latest
  export, inline-vs-attach sizing), `claude_artifacts.py` (best-effort
  artifact fetching, rewritten on `httpx` for consistency with `ai/gemini.py`
  rather than introducing a second HTTP stack), `brief.py` (orchestrates
  `eod`/`handoff` end to end)
* `git.py` gained branch-aware primitives: `get_current_branch()`,
  `get_default_branch()` (origin/HEAD detection with fallback chain),
  `get_merge_base()`, `is_ancestor()`, `get_upstream_branch()`,
  `diff_against()` (working-tree-aware — captures committed-since-baseline
  and uncommitted changes in one diff), `changed_files_against()`
* A stored marker that's no longer an ancestor of `HEAD` (e.g. after a
  rebase) is detected via `is_ancestor()` and silently recomputed from the
  branch's merge-base, with a warning surfaced to the user rather than
  producing a nonsense diff
* `claude_artifacts.py` matches Claude's flat artifact filenames against the
  codebase by basename only, since artifacts carry no knowledge of the
  repo's directory structure; ambiguous matches (same filename in multiple
  directories) pick the most recently modified candidate and surface a
  warning rather than guessing silently
