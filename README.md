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
4. Create a compressed ZIP in your system temp directory
5. Open your file manager with the ZIP selected and ready to copy

---

## Usage

```
contextzip [OPTIONS]
```

| Option | Description |
|---|---|
| `-i`, `--include PATH` | Only include files under this path. Repeatable. |
| `-e`, `--exclude PATTERN` | Extra exclusion patterns (gitignore syntax). Repeatable. |
| `exclude` | Subcommand: exclude specific files/patterns. `contextzip exclude CHANGELOG.md LICENSE .github/` |
| `include` | Subcommand: include only specific paths. `contextzip include src/ app/` |
| `--git-changes` | Only include files reported by git as modified, staged, or untracked. |
| `-n`, `--dry-run` | Preview what would be included, no ZIP created. |
| `-o`, `--output FILE` | Custom output path for the ZIP file. |
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

**Save ZIP to a specific path:**
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
`.git/`, `.env`, `.env.*`, `*.log`, `logs/`, `.cache/`, `tmp/`, editor files (`.vscode/`, `.idea/`), OS files (`.DS_Store`, `Thumbs.db`), common binary formats (images, audio, video, archives)

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
│   ├── cli.py             # Click entry point, all flags, subcommands (exclude, include), rich output
│   ├── detector.py        # framework/language detection engine
│   ├── filters.py         # pathspec-based file filtering + ResolveResult
│   ├── git.py             # git status parsing + changed-file detection
│   ├── packager.py        # ZIP creation, compression stats, PackageResult
│   ├── clipboard.py       # tiered clipboard strategy (all platforms)
│   └── rules/
│       ├── base.py        # universal exclusions
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

No other runtime dependencies. Clipboard and folder-open use only stdlib (`subprocess`, `shutil`, `platform`).

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
