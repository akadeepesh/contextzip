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

- **Smart framework detection** — automatically identifies Node.js, Next.js, Python, Django, FastAPI, Rust, Go, and Ruby, applying the right exclusion rules for each
- **Respects `.gitignore`** — your existing ignore patterns are honoured automatically
- **Git-aware packaging** — use `--git-changes` to package only modified, staged, and untracked files; perfect for incremental debugging and PR review sessions
- **AI-powered file selection** — describe your task in plain English with `--prompt` and Gemini selects the minimum relevant files automatically, no manual hunting required
- **Terminal error watcher** — wrap any dev server with `contextzip watch` to auto-detect errors and package a ready-to-upload debug context in one keypress
- **Persistent workspace** — all generated ZIPs land in `.contextzip/` at your project root, discoverable, reusable, and git-ignored automatically
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
```

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

**Always excluded:** `.git/`, `.env` files, logs, caches, editor config (`.vscode/`, `.idea/`), OS files (`.DS_Store`, `Thumbs.db`), and common binary formats.

**By framework:**

| Stack | Additional exclusions |
|---|---|
| Node.js / Next.js | `node_modules/`, `.next/`, `dist/`, `build/`, lock files, `*.min.js`, `*.d.ts` |
| Python / Django / FastAPI | `__pycache__/`, `.venv/`, `*.pyc`, `migrations/`, `.pytest_cache/`, lock files |
| Rust | `target/`, `Cargo.lock`, `*.rlib` |
| Go | `vendor/`, `go.sum`, `bin/` |

Detection is additive — a monorepo with both `package.json` and `pyproject.toml` gets both rule sets applied.

---

## Contributing

Contributions are welcome — especially new framework rule sets, edge case fixes, and platform-specific clipboard improvements.

See [CONTRIBUTING.md](https://github.com/akadeepesh/contextzip/blob/main/CONTRIBUTING.md) for local setup, how to add a new framework, and PR guidelines. Please open an issue before starting a large PR so we can align on the approach first.

---

## License

MIT — see [LICENSE](https://github.com/akadeepesh/contextzip/blob/main/LICENSE) for details.
