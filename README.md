# contextzip

> Stop copy-pasting files manually. Package exactly the right parts of your codebase and paste it straight into Claude, ChatGPT, or any AI tool in one command.

```
contextzip
```

---

## The problem

Every time you want help from an AI tool, you go through the same ritual:

1. Find the relevant files in your project
2. Skip `node_modules`, `.next`, `__pycache__`, build artifacts, lock files...
3. Select the right ones, zip them, find the zip, upload it
4. Repeat every single session

contextzip eliminates that entirely. Run it from your project root, it detects your stack, applies smart exclusions, produces a lean ZIP, and opens your file manager with the archive already selected. One `Ctrl+C` and you're done.

---

## Features

- **Smart framework detection** : automatically identifies Node.js, Next.js, Python, Django, FastAPI, Rust, Go, Ruby and applies the right exclusion rules for each
- **Respects your `.gitignore`** : patterns from your existing gitignore are honoured automatically
- **Git-aware packaging** : package only modified, staged, unstaged, and untracked files with `--git-changes` — perfect for AI review sessions, incremental debugging, and PR workflows
- **AI-powered file selection** : describe your task in plain English with `--prompt` and Gemini selects the minimum relevant files automatically — no manual hunting required
- **Persistent workspace** : all generated ZIPs land in a `.contextzip/` directory at your project root, not a random temp folder — discoverable, reusable, and git-ignored automatically
- **Warns before it's a problem** : flags large files (≥ 1 MB) and binary files that AI tools can't read, before you waste an upload
- **Handles the messy stuff** : dangling symlinks, unreadable files, and files outside the project tree are all caught and reported, never silently dropped
- **Full CLI control** : `--include`, `--exclude`, `--dry-run`, `--verbose`, `--output`, all composable
- **Cross-platform clipboard integration** : copies the file to clipboard on macOS/Linux; opens Explorer with the ZIP selected on Windows

---

## Installation

**Requires Python 3.9+**

```bash
pip install contextzip
```

Or with [pipx](https://pipx.pypa.io/) (recommended for CLI tools, keeps it isolated):

```bash
pipx install contextzip
```

Verify the install:

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

That's it. contextzip will:

1. Detect your framework (e.g. `Next.js + Node.js`)
2. Apply the appropriate exclusion rules
3. Scan and summarise what will be included
4. Create a compressed ZIP in `.contextzip/` at your project root
5. Open your file manager with the ZIP selected and ready to copy

---

## Usage

```
contextzip [OPTIONS]
```

| Option | Description |
|---|---|
| `-p`, `--prompt TEXT` | Describe your task in plain English. Gemini AI selects only the relevant files. Requires a free API key (guided setup on first use). |
| `-i`, `--include PATH` | Only include files under this path. Repeatable. |
| `-e`, `--exclude PATTERN` | Extra exclusion patterns (gitignore syntax). Repeatable. |
| `exclude` | Subcommand: exclude specific files/patterns. `contextzip exclude CHANGELOG.md LICENSE .github/` |
| `include` | Subcommand: include only specific paths. `contextzip include src/ app/` |
| `config` | Subcommand: manage configuration (API key). `contextzip config --reset-key` |
| `--git-changes` | Only include files reported by git as modified, staged, or untracked. |
| `-n`, `--dry-run` | Preview what would be included, no ZIP created. |
| `-o`, `--output FILE` | Write ZIP to a custom path. Bypasses the `.contextzip/` workspace entirely. |
| `--no-clipboard` | Skip the clipboard / folder-open step. |
| `--no-gitignore` | Ignore the project's `.gitignore` file. |
| `-v`, `--verbose` | Show every included and excluded file with sizes. |
| `-h`, `--help` | Show help and exit. |
| `--version` | Show version and exit. |

---

## Examples

**Preview what would be packaged (no ZIP created):**
```bash
contextzip --dry-run
```

**Only package specific directories:**
```bash
contextzip --include src --include app
```

**Exclude additional patterns beyond the auto-rules:**
```bash
contextzip --exclude "*.log" --exclude "*.sqlite" --exclude "tests/"
```

**Exclude files using the subcommand (space-separated, no repetition):**
```bash
contextzip exclude CHANGELOG.md CONTRIBUTING.md LICENSE .github/
```

**Exclude with flags:**
```bash
contextzip exclude CHANGELOG.md --dry-run --verbose
```

**Include only specific directories using the subcommand:**
```bash
contextzip include src/ app/
```

**Only package files changed in git:**
```bash
contextzip --git-changes
```

**Save ZIP to a specific path (bypasses workspace):**
```bash
contextzip --output ~/Desktop/my-project-context.zip
```

**Full verbose audit, see every file decision:**
```bash
contextzip --dry-run --verbose
```

**Combine include + extra excludes:**
```bash
contextzip --include src --exclude "**/*.test.ts"
```

**Let AI select only the files relevant to your task:**
```bash
contextzip --prompt "Change toast color on failed login"
```

**Preview AI file selection without creating a ZIP:**
```bash
contextzip --prompt "Refactor auth middleware" --dry-run
```

---

## AI-powered file selection

The `--prompt` flag lets you describe a task in plain English. contextzip scans your project, builds a lightweight file map, and asks Gemini to select the minimum set of files needed for that task. The result is a tightly scoped ZIP — only what you'd actually open to make the change.

```bash
contextzip --prompt "Change toast color on failed login"
```

Example selected files:
```
components/ui/toast.tsx
app/login/page.tsx
lib/auth.ts
```

The ZIP also includes a `prompt.txt` that describes the task and lists the selected files. When you drop the ZIP into Claude, ChatGPT, or any other AI tool, it immediately knows what you're trying to do.

### First-time setup

`--prompt` requires a free Gemini API key from [Google AI Studio](https://aistudio.google.com/apikey) — no credit card needed. On first use, contextzip will guide you through getting and saving one:

```
╭─ Gemini API Key Required ──────────────────────────────────╮
│                                                             │
│  --prompt requires a free Gemini API key.                   │
│  contextzip uses Google AI Studio — no credit card needed.  │
│                                                             │
│  Get your free key at:                                      │
│  https://aistudio.google.com/apikey                         │
│                                                             │
╰─────────────────────────────────────────────────────────────╯

  Open Google AI Studio in your browser now? [Y/n]:
```

Your key is saved to `~/.config/contextzip/config.json` (Linux/macOS) or `%APPDATA%\contextzip\config.json` (Windows) and reused automatically on every subsequent run.

You can also set the key as an environment variable to skip the config file entirely:

```bash
export GEMINI_API_KEY=AIza...
```

### Managing your API key

```bash
contextzip config                  # show current key status
contextzip config --reset-key      # clear stored key and re-run setup
contextzip config --show-key-path  # print the config file location
```

### How file selection works

1. contextzip scans the project and applies all standard exclusions (no `node_modules`, no build artifacts, no `.env` files)
2. A lightweight file map (relative paths + file sizes) is sent to Gemini along with your task description and detected framework
3. Gemini returns a ranked list of the most relevant files — typically 2–5, never more than 10
4. Only those files are packaged, plus a `prompt.txt` describing the task

The goal is minimum viable context. Sending fewer, more relevant files gets better AI responses and wastes fewer tokens.

---

## Workspace directory

contextzip stores all generated packages in a `.contextzip/` directory at your project root, keeping your AI context artifacts persistent, discoverable, and out of the way.

### Location

contextzip looks for a `.git/` directory walking up from your current working directory and places `.contextzip/` alongside it — the true project root. If no git repository is found, it falls back to the current working directory.

### Output naming

The output filename is determined by how you invoke contextzip:

| Command | Output |
|---|---|
| `contextzip` | `.contextzip/codebase.zip` |
| `contextzip --git-changes` | `.contextzip/changes.zip` |
| `contextzip --prompt "..."` | `.contextzip/codebase.zip` (AI-scoped) |
| `contextzip --output PATH` | `PATH` (workspace bypassed entirely) |

All other flags (`--include`, `--exclude`, subcommands) do not affect the filename — they refine what goes into `codebase.zip`.

Each run overwrites the previous file of the same name, so `.contextzip/codebase.zip` is always your latest snapshot.

### `.gitignore` management

contextzip automatically keeps `.contextzip/` out of version control:

- If `.gitignore` exists → `.contextzip/` is appended if not already present
- If `.gitignore` does not exist and a `.git/` directory is found → a minimal `.gitignore` is created containing `.contextzip/`
- If no git repository is detected → `.gitignore` is left untouched

The `.contextzip/` directory is also always excluded from packaging itself, regardless of `.gitignore` state.

### Workspace structure

```
project/
├── .contextzip/
│   ├── codebase.zip      ← latest full snapshot
│   └── changes.zip       ← latest git-changes snapshot
├── src/
└── ...
```

---

## Framework detection & auto-exclusions

contextzip detects your stack from config files in the project root and stacks the appropriate rules.

### Detection signals

| File found | Detected as |
|---|---|
| `next.config.js` / `.ts` / `.mjs` or `"next"` in `package.json` | Next.js |
| `package.json` | Node.js |
| `manage.py` or `"django"` in requirements | Django |
| `"fastapi"` in requirements | FastAPI |
| `requirements.txt` / `pyproject.toml` / `setup.py` | Python |
| `Cargo.toml` | Rust |
| `go.mod` | Go |
| `Gemfile` | Ruby |

Detection is **additive**. A monorepo with both `package.json` and `pyproject.toml` will have both rule sets applied.

### What gets excluded

**Always (every project):**
`.git/`, `.env`, `.env.*`, `*.log`, `logs/`, `.cache/`, `tmp/`, `.contextzip/`, editor files (`.vscode/`, `.idea/`), OS files (`.DS_Store`, `Thumbs.db`), common binary formats (images, audio, video, archives)

**Node.js / Next.js:**
`node_modules/`, `.next/`, `.nuxt/`, `dist/`, `build/`, `out/`, `.turbo/`, `package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`, `*.min.js`, `*.d.ts`, `tsconfig.tsbuildinfo`

**Python:**
`__pycache__/`, `*.pyc`, `.venv/`, `venv/`, `*.egg-info/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `htmlcov/`, `*.sqlite3`, `migrations/`, `poetry.lock`, `uv.lock`

**Rust:**
`target/`, `Cargo.lock`, `*.rlib`, `*.rmeta`

**Go:**
`vendor/`, `go.sum`, `bin/`

Plus any patterns from your project's own `.gitignore`.

---

## Clipboard behaviour

contextzip uses a tiered strategy so it never just fails silently:

| Platform | Tier 1 (auto) | Tier 2 (fallback) | Tier 3 (last resort) |
|---|---|---|---|
| **macOS** | File copied to clipboard via Finder, paste directly into browser | `open -R` reveals ZIP in Finder | Path printed to terminal |
| **Linux** | `xclip` copies file bytes with `application/zip` MIME type | `xdg-open` opens containing folder | Path printed to terminal |
| **Windows** | - | `explorer /select,"..."` opens Explorer with ZIP highlighted → `Ctrl+C` | Path printed to terminal |

On **macOS** and **Linux with xclip**, you can paste the ZIP directly into an upload zone (Claude, ChatGPT, etc.), no file picker needed. On **Windows**, Explorer opens with the file already selected so one `Ctrl+C` is all it takes.

---

## Warnings

contextzip surfaces issues before they waste your time:

**Large files (≥ 1 MB)** : listed with sizes and a suggestion to exclude if unneeded. AI context windows have limits; a 5 MB log file helps no one.

**Binary files** : files detected as binary (null bytes in the first 512 bytes) are flagged. Most AI tools can't read binary content; you may want to exclude them.

**Skipped files** : dangling symlinks, permission-denied files, and paths outside the project tree are listed with their specific reason rather than silently dropped.

---

## Project structure

```
contextzip/
├── contextzip/
│   ├── __init__.py        # version string
│   ├── cli.py             # Click entry point, all flags, subcommands (exclude, include, config), rich output
│   ├── config.py          # API key storage (~/.config/contextzip/config.json)
│   ├── detector.py        # framework/language detection engine
│   ├── filters.py         # pathspec-based file filtering + ResolveResult
│   ├── git.py             # git status parsing + changed-file detection
│   ├── packager.py        # ZIP creation, workspace management, PackageResult
│   ├── clipboard.py       # tiered clipboard strategy (all platforms)
│   ├── ai/
│   │   ├── __init__.py
│   │   ├── gemini.py      # Gemini API client (file relevance selection)
│   │   └── selector.py    # project map builder + AI orchestration + prompt.txt
│   └── rules/
│       ├── base.py        # universal exclusions (includes .contextzip/)
│       ├── node.py        # Node.js / Next.js / Vite
│       ├── python.py      # Python / Django / FastAPI
│       ├── rust.py        # Rust / Cargo
│       └── go.py          # Go modules
└── pyproject.toml
```

---

## Adding a new language / framework

1. Create `contextzip/rules/yourlang.py` with a `PATTERNS` list using gitignore syntax:

```python
PATTERNS = [
    "build/",
    "*.compiled",
    ".cache/",
]
```

2. Register it in `filters.py`:

```python
_RULE_REGISTRY: dict[str, str] = {
    ...
    "yourlang": "contextzip.rules.yourlang",
}
```

3. Add a detection rule in `detector.py`:

```python
_Rule(
    name="YourLang",
    module="yourlang",
    check=lambda p: _file_exists(p, "yourlang.config"),
    weight=3,
),
```

That's it. Detection, filtering, and CLI output all pick it up automatically.

---

## Dependencies

| Package | Purpose |
|---|---|
| [`click`](https://click.palletsprojects.com/) | CLI framework |
| [`rich`](https://rich.readthedocs.io/) | Terminal output, panels, progress bars |
| [`pathspec`](https://github.com/cpburnz/python-pathspec) | Gitignore-style pattern matching |
| [`httpx`](https://www.python-httpx.org/) | HTTP client for Gemini API calls (required for `--prompt`) |

`httpx` is only used when `--prompt` is invoked. All other contextzip features work without it, but it must be installed for AI-powered file selection.

---

## Contributing

Contributions are welcome, especially new framework rule sets, edge case fixes, and platform-specific clipboard improvements.

```bash
git clone https://github.com/akadeepesh/contextzip
cd contextzip
pip install -e .
```

Please open an issue before submitting a large PR so we can discuss the approach first.

---

## License

MIT - see [LICENSE](https://github.com/akadeepesh/smart-context-packager/blob/main/LICENSE) for details.