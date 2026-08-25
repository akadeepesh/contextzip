# contextzip

> Package exactly the right parts of your codebase and paste it into any AI tool — in one command.

```bash
pip install contextzip
```

---

## Why contextzip

Every AI session starts the same way: hunt down the relevant files, manually skip `node_modules`, build artifacts, and lock files, zip them, find the zip, upload it. Then do it all again next session.

contextzip eliminates that entirely. Run it from your project root — it detects your stack, applies smart exclusions, produces a lean ZIP, and opens your file manager with the archive already selected. One `Ctrl+C` and you're done.

And when the AI tool hands you a ZIP back with the changes, `contextzip apply-zip` closes the loop — no manual unzip-and-hope, no losing track of what actually changed.

---

## Features

- **Smart framework detection** — automatically identifies Node.js, Next.js, Python, Django, FastAPI, Rust, Go, and Ruby, applying the right exclusion rules for each. Detection isn't limited to the project root: a shallow, bounded scan of subdirectories means a monorepo (`frontend/` + `backend/`, etc.) gets every ecosystem it contains detected and excluded correctly, not just whatever sits at the top level
- **Respects `.gitignore`** — your existing ignore patterns are honoured automatically
- **Git-aware packaging** — use `--git-changes` to package only modified, staged, and untracked files; perfect for incremental debugging and PR review sessions
- **AI-powered file selection** — describe your task in plain English with `--prompt` and Gemini selects the minimum relevant files automatically, no manual hunting required
- **Apply AI-returned changes back** — `contextzip apply-zip` takes the ZIP an AI tool hands you back and writes it into your project safely: diffed against what was actually sent, backed up before anything is overwritten, and never silently deleting anything
- **Terminal error watcher** — wrap any dev server with `contextzip watch` to auto-detect errors and package a ready-to-upload debug context in one keypress
- **Configurable workspace location** — `.contextzip/` lives at the git root by default, but you can pin it elsewhere per-machine (`contextzip config --set-workspace`) or for the whole team via a committed `.contextzip.json`
- **Persistent workspace** — all generated ZIPs land in `.contextzip/`, discoverable, reusable, and git-ignored automatically
- **Warns before it's a problem** — flags large (≥ 1 MB) and binary files that AI tools can't read, before you waste an upload
- **Never packages secrets** — SSH keys, cloud credentials, keystores, Terraform state, and other credential files are always excluded, on top of your `.gitignore` (see [What gets excluded](#what-gets-excluded))
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
3. Create a compressed ZIP in `.contextzip/` at your project root
4. Open your file manager with the ZIP selected and ready to copy

---

## Usage

```
contextzip [OPTIONS]
```

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

# Apply the ZIP an AI tool handed back
contextzip apply-zip
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

# Apply a ZIP an AI tool returned (auto-detects from .contextzip/inbox/)
result = apply_zip()
print(f"Wrote {len(result.written)} files, backup at {result.backup_dir}")
```

| Function | Description |
|---|---|
| `get_git_changes(path?)` | Modified, added, and untracked files from git |
| `get_files(path?, include?, exclude?)` | All project files after exclusion rules |
| `create_zip(collection, output?)` | Write a `FileCollection` to a ZIP archive |
| `apply_zip(zip_path?, project_dir?, manifest?)` | Apply an AI-returned ZIP back into the project |
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

Saved keys live in your OS user-config directory (e.g. `~/.config/contextzip/config.json` on Linux/macOS), and that file's permissions are locked down to your user only (`0600`) on every save.

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

Press **D** and contextzip immediately writes `.contextzip/debug-context.zip`. Your server keeps running — no restart, no interruption.

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

**Always excluded:** `.git/`, `.env` files, logs, caches, editor config (`.vscode/`, `.idea/`), OS files (`.DS_Store`, `Thumbs.db`), common binary formats, secrets and credential files (see below), and contextzip's own `.contextzip/` working folder.

**Secrets & credentials — always excluded, regardless of framework:**

| Category | Examples |
|---|---|
| SSH private keys | `id_rsa`, `id_dsa`, `id_ecdsa`, `id_ed25519` (public counterparts like `id_rsa.pub` are kept) |
| Keystores & certs | `*.p12`, `*.pfx`, `*.pkcs12`, `*.jks`, `*.keystore`, `*.ppk`, `*.key` |
| CLI / package manager credentials | `.npmrc`, `.netrc`, `.pypirc`, `.pgpass`, `.dockercfg`, Docker `config.json` |
| Cloud provider credentials | `.aws/credentials`, `.aws/config`, `*serviceaccount*.json`, `*credentials*.json`, `kubeconfig` |
| Infra-as-code state | `*.tfstate`, `*.tfstate.*`, `.terraform/` (Terraform state routinely contains plaintext secrets, even for "just infra" resources) |

These patterns are applied on top of your `.gitignore` and can't be re-included, so a stray credential file lying around your project never accidentally ends up in a ZIP you paste into an AI tool.

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
2. **A committed `.contextzip.json` at the project root** — team-shared, applies to everyone who clones the repo:
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

This matters most for monorepos where you sometimes run contextzip from a subdirectory (`cd frontend && contextzip`) — with the default `git-root` setting, the workspace still lands at the repo root no matter where you ran it from, so you don't end up with scattered `.contextzip/` folders across `frontend/`, `backend/`, etc. Set `workspace_location: "cwd"` in a project's `.contextzip.json` instead if you'd rather each subproject keep its own.

**Workspace layout:**

```
.contextzip/
  config.json               # team-shared preferences (committed)
  .gitignore                # ignores everything below except itself + config.json
  output/
    codebase.zip            # what you generate and send out
    codebase.manifest.json  # local-only — never uploaded, used by apply-zip
  inbox/
    <ai-returned>.zip        # drop AI-returned zips here for apply-zip to pick up
    applied/
      <timestamp>-name.zip   # archived after a successful apply-zip
  backups/
    <timestamp>/             # pre-overwrite copies, one folder per apply-zip run
```

---

## Contributing

Contributions are welcome — especially new framework rule sets, edge case fixes, and platform-specific clipboard improvements.

See [CONTRIBUTING.md](https://github.com/akadeepesh/contextzip/blob/main/CONTRIBUTING.md) for local setup, how to add a new framework, and PR guidelines. Please open an issue before starting a large PR so we can align on the approach first.

---

## License

MIT — see [LICENSE](https://github.com/akadeepesh/contextzip/blob/main/LICENSE) for details.
