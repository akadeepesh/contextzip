# contextzip

> Package exactly the right parts of your codebase and paste it into any AI tool — in one command.

```bash
pip install contextzip
```

---

## Why contextzip

Every AI session starts the same way: hunt down the relevant files, manually skip `node_modules`, build artifacts, and lock files, zip them, find the zip, upload it. Then do it all again next session.

contextzip eliminates that entirely. Run it from your project root — it detects your stack, applies smart exclusions, produces a lean ZIP, and opens your file manager with the archive already selected. One `Ctrl+C` and you're done.

---

## Features

- **Smart framework detection** — automatically identifies Node.js, Next.js, Python, Django, FastAPI, Rust, Go, and Ruby, applying the right exclusion rules for each. Detection isn't limited to the project root: a shallow, bounded scan of subdirectories means a monorepo (`frontend/` + `backend/`, etc.) gets every ecosystem it contains detected and excluded correctly, not just whatever sits at the top level
- **Respects `.gitignore`** — your existing ignore patterns are honoured automatically
- **Git-aware packaging** — use `--git-changes` to package only modified, staged, and untracked files; perfect for incremental debugging and PR review sessions
- **AI-powered file selection** — describe your task in plain English with `--prompt` and Gemini selects the minimum relevant files automatically, no manual hunting required
- **Terminal error watcher** — wrap any dev server with `contextzip watch` to auto-detect errors and package a ready-to-upload debug context in one keypress
- **Configurable workspace location** — `.contextzip/` lives at the git root by default, but you can pin it elsewhere per-machine (`contextzip config --set-workspace`) or for the whole team via a committed `.contextzip/config.json`
- **Persistent workspace** — all generated ZIPs land in `.contextzip/output/`, discoverable, reusable, and git-ignored automatically
- **Warns before it's a problem** — flags large (≥ 1 MB) and binary files that AI tools can't read, before you waste an upload
- **Handles edge cases** — dangling symlinks, unreadable files, and paths outside the project tree are caught and reported, never silently dropped
- **Full CLI control** — `--include`, `--exclude`, `--dry-run`, `--output`, all composable

---

## Installation

**Requires Python 3.9+**

```bash
pip install contextzip
```

With [pipx](https://pipx.pypa.io/) (recommended for CLI tools — keeps it isolated):

```bash
pipx install contextzip
```

Verify:

```bash
contextzip --version
```

---

## Quick start

Navigate to any project and run:

```bash
cd ~/projects/my-app
contextzip
```

contextzip will:

1. Detect your framework (e.g. `Next.js + Node.js`)
2. Apply the appropriate exclusion rules
3. Create a compressed ZIP in `.contextzip/output/` at your project root
4. Open your file manager with the ZIP selected and ready to copy

---

## Usage

```
contextzip [OPTIONS]
```

> `cz` is a shorthand alias for `contextzip` — both commands are identical and support every option and subcommand shown below (e.g. `cz --git-changes`, `cz exclude node_modules`).

| Option | Description |
|---|---|
| `-p`, `--prompt TEXT` | Describe your task in plain English — Gemini selects only the relevant files |
| `-i`, `--include PATH` | Only include files under this path (repeatable) |
| `-e`, `--exclude PATTERN` | Add exclusion patterns in gitignore syntax (repeatable) |
| `--git-changes` | Only include files reported by git as modified, staged, or untracked |
| `-n`, `--dry-run` | Preview what would be included without creating a ZIP |
| `-o`, `--output FILE` | Write ZIP to a custom path |
| `-v`, `--verbose` | Show every included and excluded file with sizes |
| `--no-clipboard` | Skip the clipboard / folder-open step |
| `--no-gitignore` | Ignore the project's `.gitignore` |

**Subcommands:** `exclude`, `include`, `watch`, `config` — run `contextzip --help` for full details.

---

## Examples

```bash
# Preview what would be packaged
contextzip --dry-run --verbose

# Package only specific directories
contextzip --include src --include app

# Exclude additional patterns
contextzip --exclude "*.log" --exclude "tests/"

# Package only git-modified files
contextzip --git-changes

# Let AI pick only the files relevant to your task
contextzip --prompt "Change toast color on failed login"

# Preview AI file selection without creating a ZIP
contextzip --prompt "Refactor auth middleware" --dry-run

# Save to a custom path
contextzip --output ~/Desktop/project-context.zip
```

---

## Python API

contextzip is also usable as a library. All CLI capabilities are available as plain Python functions — no Click, no Rich output, no `SystemExit`.

```python
from contextzip import get_git_changes, get_files, create_zip

# Get changed files and use them directly
collection = get_git_changes()
for f in collection.files:          # plain pathlib.Path objects
    upload(f)                       # no zip required

# Or zip them and upload the archive
pkg = create_zip(collection, output="/tmp/changes.zip")
with open(pkg.zip_path, "rb") as f:
    upload_to_s3(f)

# Full project scan with filters
collection = get_files(include=["src/"], exclude=["tests/"])
pkg = create_zip(collection, output="/tmp/upload.zip")
print(f"{pkg.file_count} files, {pkg.compressed_bytes} bytes")
```

| Function | Description |
|---|---|
| `get_git_changes(path?)` | Modified, added, and untracked files from git |
| `get_files(path?, include?, exclude?)` | All project files after exclusion rules |
| `create_zip(collection, output?)` | Write a `FileCollection` to a ZIP archive |
| `detect_ecosystem(path?)` | Detect framework and confidence level |

All functions default `path` to `Path.cwd()`. Errors raise typed exceptions (`NotARepositoryError`, `GitNotFoundError`, `NoFilesError`, etc.) rather than exiting.

---

## AI-powered file selection

The `--prompt` flag lets you describe a task in plain English. contextzip scans your project, builds a lightweight file map, and asks Gemini to return the minimum set of files needed for that task — typically 2–5, never more than 10. The result is a tightly scoped ZIP with only what you'd actually open to make the change.

```bash
contextzip --prompt "Change toast color on failed login"
# → components/ui/toast.tsx, app/login/page.tsx, lib/auth.ts
```

The ZIP also includes a `prompt.txt` describing the task, so when you drop it into Claude, ChatGPT, or any other AI tool, it immediately understands what you're trying to do.

**First-time setup:** `--prompt` requires a free Gemini API key from [Google AI Studio](https://aistudio.google.com/apikey) — no credit card needed. On first use, contextzip guides you through obtaining and saving one. You can also skip the setup entirely with an environment variable:

```bash
export GEMINI_API_KEY=AIza...
```

Manage your key at any time:

```bash
contextzip config               # show current key status
contextzip config --reset-key   # clear and re-run setup
```

---

## Terminal error watcher

The `watch` command wraps your dev server, buffers its output, and packages a debug-ready ZIP the moment you spot an error — no manual file hunting, no copy-pasting stack traces.

```bash
contextzip watch -- npm run dev
contextzip watch -- python manage.py runserver
```

contextzip starts your process normally. You see output exactly as you would without it. In the background, it watches the stream for errors. When one is detected, a prompt appears directly beneath the error output:

```
╭─ contextzip · error detected ─────────────────────╮
│  Press [D] to package debug context  [S] to skip  │
╰───────────────────────────────────────────────────╯
```

Press **D** and contextzip immediately writes `.contextzip/output/debug-context.zip`. Your server keeps running — no restart, no interruption.

**What's in the ZIP:**

| File | Contents |
|---|---|
| `prompt.txt` | Auto-generated: detected framework, error type, and task description — ready to paste into any AI tool |
| `terminal-error.txt` | The cleaned, noise-stripped error block and stack trace |
| `source-files.zip` | Source files referenced in the stack trace, paths preserved |

**On Ctrl+C:** If no errors were packaged during the session, contextzip offers one final prompt to capture the full session output — useful when something looked wrong but didn't match a known error pattern.

**Supported frameworks:** Python, Django, FastAPI, Node.js, Next.js, React. Each has its own error detection patterns and noise filters so the output stays clean across stacks.

> **Note:** `watch` works best with dev servers that don't read stdin interactively (`npm run dev`, `manage.py runserver`, etc.). PTY emulation is not used — on Windows, color passthrough may be limited.

---

## What gets excluded

contextzip stacks exclusion rules based on your detected stack, on top of your `.gitignore`.

**Always excluded:** `.git/`, `.env` files, logs, caches, editor config (`.vscode/`, `.idea/`), OS files (`.DS_Store`, `Thumbs.db`), common binary formats, and contextzip's own `.contextzip/` working folder.

**By framework:**

| Stack | Additional exclusions |
|---|---|
| Node.js / Next.js | `node_modules/`, `.next/`, `dist/`, `build/`, lock files, `*.min.js`, `*.d.ts` |
| Python / Django / FastAPI | `__pycache__/`, `.venv/`, `*.pyc`, `migrations/`, `.pytest_cache/`, lock files |
| Rust | `target/`, `Cargo.lock`, `*.rlib` |
| Go | `vendor/`, `go.sum`, `bin/` |

Detection is additive — a monorepo with both `package.json` and `pyproject.toml` gets both rule sets applied. Marker files don't have to sit at the project root: contextzip also does a shallow, bounded scan of subdirectories (2 levels deep, skipping `node_modules/`, `.venv/`, `.git/`, and other dependency/build dirs it would never want to look inside anyway), so a layout like

```
root/
  frontend/package.json
  backend/requirements.txt
```

detects both Node.js and Python from `root/`, without either marker existing at the top level. Run with default output (not `--dry-run --verbose`) and you'll see which subdirectory each ecosystem came from, e.g. `Next.js (frontend/) + FastAPI (backend/)`.

---

## Workspace location

By default, `.contextzip/` is created at your git root — that's `_find_git_root()` walking up from the current directory until it finds a `.git` folder; outside a repo, it falls back to the current directory. This can be overridden two ways, in order of priority:

1. **`CONTEXTZIP_WORKSPACE_LOCATION` environment variable** — for a one-off override on a single run
2. **A committed `.contextzip/config.json` at the project root** — team-shared, applies to everyone who clones the repo:
   ```json
   { "workspace_location": "git-root" }
   ```
3. **Your personal config** — a per-machine default that doesn't get committed:
   ```bash
   contextzip config --set-workspace cwd          # always use ./.contextzip wherever you run it
   contextzip config --set-workspace git-root      # back to the default
   contextzip config --set-workspace ~/zips        # a fixed custom location, anywhere
   contextzip config --reset-workspace             # clear your personal override
   ```

Accepted values are `"git-root"`, `"cwd"`, or any path (absolute, or relative to the git root). Run `contextzip config` with no flags to see which value is currently active and where it came from.

This matters most for monorepos where you sometimes run contextzip from a subdirectory (`cd frontend && contextzip`) — with the default `git-root` setting, the workspace still lands at the repo root no matter where you ran it from, so you don't end up with scattered `.contextzip/` folders across `frontend/`, `backend/`, etc. Set `workspace_location: "cwd"` in a project's `.contextzip/config.json` instead if you'd rather each subproject keep its own.

---

## Project configuration

Every project gets a `.contextzip/` workspace at the project root:

```
.contextzip/
├── config.json     # project preferences — commit this
├── .gitignore       # keeps output/ untracked, config.json trackable
└── output/          # generated ZIPs — never committed
    ├── codebase.zip
    ├── changes.zip       (--git-changes)
    └── debug-context.zip (contextzip watch)
```

`.contextzip/` is **not** committed to Git by default — `.contextzip/.gitignore` is written automatically the first time you run contextzip in a repo, and it ignores everything in the folder *except* `config.json` and itself. That's deliberate: `output/` is per-machine, disposable scratch space, while `config.json` holds team-shared preferences you'll usually want everyone on the same page about.

`config.json` currently supports:

```json
{
  "workspace_location": "git-root",
  "scan_depth": null,
  "always_include": [],
  "always_exclude": [],
  "ai": {
    "enabled": true,
    "provider": "gemini",
    "max_files": 10
  }
}
```

All keys are optional — start with just the ones you need.

| Key | What it does |
| --- | --- |
| `workspace_location` | Same as `contextzip config --set-workspace`, but team-shared. See [Workspace location](#workspace-location). |
| `scan_depth` | Reserved for a future bounded-depth scan mode. |
| `always_include` | A standing "force include" list (gitwildmatch patterns). Files matching it are packaged even if an auto-rule or `.gitignore` would otherwise exclude them — e.g. `["docs/architecture.md"]` to always pull in a doc that lives in an otherwise-excluded folder. |
| `always_exclude` | A standing exclusion list, applied on every run without retyping `-e`/`--exclude`. |
| `ai.enabled` | Set to `false` to disable `--prompt` entirely for this project; contextzip will refuse with a clear message instead of silently ignoring it. |
| `ai.provider` | Reserved for future AI providers. Only `"gemini"` is currently supported. |
| `ai.max_files` | Caps how many files `--prompt` mode may return, for both the Gemini and keyword-heuristic paths. |

`always_include`/`always_exclude` are additive on top of `--include`/`--exclude` for that run — an explicit `contextzip include PATH` still has the final say over what's actually packaged. Persistent preferences belong in `config.json` rather than an ever-growing list of CLI flags; CLI flags stay for one-off, explicit actions (`--dry-run`, `--prompt "…"`, `--output FILE`, etc.).

A **web-based configuration UI** that generates `config.json` for you is on the roadmap — the schema above is designed to grow additively, so future preferences (including ones set visually) won't require another migration.

### Deprecation: `.contextzip.json`

Earlier versions of contextzip read a `.contextzip.json` file at the project root for `workspace_location`/`scan_depth`. That file is now **deprecated** in favor of `.contextzip/config.json` — contextzip still reads it automatically if `.contextzip/config.json` doesn't exist yet (so nothing breaks), and prints a one-time reminder to migrate. To migrate, just move its contents into the `"workspace_location"`/`"scan_depth"` keys of a new `.contextzip/config.json` and delete the old file.

`~/.config/contextzip/config.json` (your personal, per-machine config — API key, personal workspace override) is unrelated and unaffected by any of this.

---

## Contributing

Contributions are welcome — especially new framework rule sets, edge case fixes, and platform-specific clipboard improvements.

See [CONTRIBUTING.md](https://github.com/akadeepesh/contextzip/blob/main/CONTRIBUTING.md) for local setup, how to add a new framework, and PR guidelines. Please open an issue before starting a large PR so we can align on the approach first.

---

## License

MIT — see [LICENSE](https://github.com/akadeepesh/contextzip/blob/main/LICENSE) for details.
