<div align="center">

# contextzip

**Package exactly the right parts of your codebase for any AI tool, in one command.**

[![PyPI](https://img.shields.io/pypi/v/contextzip?color=blue)](https://pypi.org/project/contextzip/)
[![npm](https://img.shields.io/npm/v/@contextzip/cli?color=blue&label=npm)](https://www.npmjs.com/package/@contextzip/cli)
[![VS Code](https://img.shields.io/visual-studio-marketplace/v/contextzip.contextzip?label=VS%20Code&color=blue)](https://marketplace.visualstudio.com/items?itemName=contextzip.contextzip)
[![Python](https://img.shields.io/pypi/pyversions/contextzip)](https://pypi.org/project/contextzip/)
[![PyPI Downloads](https://static.pepy.tech/personalized-badge/contextzip?period=total&units=INTERNATIONAL_SYSTEM&left_color=BLACK&right_color=GREEN&left_text=downloads)](https://pepy.tech/projects/contextzip)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](https://github.com/akadeepesh/contextzip/blob/main/LICENSE)

[Website](https://www.contextzipcli.com) ·
[Documentation](https://www.contextzipcli.com/docs/getting-started) ·
[CLI reference](https://github.com/akadeepesh/contextzip/blob/main/CLI.md) ·
[Changelog](https://github.com/akadeepesh/contextzip/blob/main/CHANGELOG.md) ·
[Report a bug](https://github.com/akadeepesh/contextzip/issues/new/choose)

</div>

https://github.com/user-attachments/assets/91913ad6-6161-430d-9075-b0fa76498021

<div align="center">

**[▶ Watch the 72-second walkthrough on YouTube](https://youtu.be/9FnYfyB3qd8)**: package a project with `cz`, paste it into Claude, and write the reply back with `cz apply-zip`.

</div>

---

## Why contextzip?

Every AI coding session starts the same way. You hunt down the relevant files, skip `node_modules` and build output by hand, zip it all, find the zip and upload it. Next session, you do it again.

contextzip does all of that for you. Run it from your project root and it:

1. **Detects your stack** (Node.js, Next.js, Python, Django, FastAPI, Rust, Go, Ruby, monorepos included).
2. **Excludes the noise**: dependencies, build output, caches, binaries, logs, and anything in your `.gitignore`.
3. **Keeps secrets out**: `.env` files, private keys, cloud credentials and Terraform state are left out by default. If you include your `.env` on purpose, its values are redacted.
4. **Puts the ZIP on your clipboard** as a real file, so you can paste it straight into Claude, ChatGPT, Gemini or any other upload box.

When the AI sends changed files back, `contextzip apply-zip` writes them into your project safely, and warns you before touching anything you've edited since.

It works where you do: in the terminal, on a GitHub, GitLab or Bitbucket link without cloning, [in your browser](https://www.contextzipcli.com) with nothing to install, in [VS Code](https://marketplace.visualstudio.com/items?itemName=contextzip.contextzip), and for AI agents over MCP.

## Table of contents

- [Installation](#installation)
  - ["`cz` is not recognized"](#cz-is-not-recognized--command-not-found)
- [Quick start](#quick-start)
- [Features](#features)
  - [Smart packaging](#smart-packaging)
  - [Package only what changed](#package-only-what-changed)
  - [Package a GitHub, GitLab or Bitbucket repo](#package-a-github-gitlab-or-bitbucket-repo)
  - [In your browser](#in-your-browser)
  - [VS Code extension](#vs-code-extension)
  - [MCP server for AI agents](#mcp-server-for-ai-agents)
  - [AI-powered file selection](#ai-powered-file-selection)
  - [Applying AI changes back](#applying-ai-changes-back)
  - [Terminal error watcher](#terminal-error-watcher)
  - [Visual config UI](#visual-config-ui)
- [Command reference](#command-reference)
- [Configuration](#configuration)
- [What is always excluded](#what-is-always-excluded)
- [Where files go](#where-files-go)
- [Python API](#python-api)
- [Privacy & security](#privacy--security)
- [Platform support](#platform-support)
- [Contributing](#contributing)
- [License](#license)

## Installation

**With Python** (3.9 or newer):

```bash
pip install contextzip
```

Installing with [pipx](https://pipx.pypa.io/) or [uv](https://docs.astral.sh/uv/) keeps it isolated from your projects:

```bash
pipx install contextzip
# or
uv tool install contextzip
```

**Without Python.** These install a standalone build of the same tool:

```bash
npm install -g @contextzip/cli                 # Node.js 16+, any OS
brew install akadeepesh/tap/contextzip         # macOS / Linux (Homebrew)
scoop bucket add contextzip https://github.com/akadeepesh/scoop-bucket
scoop install contextzip                       # Windows (Scoop)
```

Or download an archive for your platform from the [latest release](https://github.com/akadeepesh/contextzip/releases/latest), unpack it, and put the `contextzip` folder on your PATH. Builds are made for Windows x64, macOS (Apple Silicon and Intel) and Linux x64/arm64 (glibc 2.35 or newer).

**In VS Code**, install [contextzip from the Marketplace](https://marketplace.visualstudio.com/items?itemName=contextzip.contextzip) (or run `code --install-extension contextzip.contextzip`). It bundles the CLI, so there's nothing else to install. See [VS Code extension](#vs-code-extension).

**For AI agents:** `pip install "contextzip[mcp]"` and register `contextzip mcp` as an MCP server (Python 3.10+). See [CLI.md](https://github.com/akadeepesh/contextzip/blob/main/CLI.md#contextzip-mcp).

**No install at all:** drop a folder or paste a repo link on [contextzipcli.com](https://www.contextzipcli.com).

**Just trying the CLI?** Nothing stays installed with these:

```bash
npx @contextzip/cli          # Node.js
uvx contextzip               # uv
pipx run contextzip          # pipx
```

Every method installs two commands that do the same thing: `contextzip` and the shorter `cz`.

To update later, run `contextzip update`. It works out whether you installed with pip, pipx or uv and upgrades the right one. A standalone build tells you the npm, Homebrew or Scoop command to run instead.

### "`cz` is not recognized" / "command not found"

pip installed contextzip, but put the `cz` command in a folder your terminal doesn't search. This is common with Python from the Microsoft Store or the Python install manager on Windows, and with `pip install --user` on macOS and Linux. Look for pip's `WARNING: The scripts cz.exe and contextzip.exe are installed in '…' which is not on PATH.`

Python itself always works, so run contextzip through it and let it fix PATH for you:

```bash
python -m contextzip setup-path     # macOS / Linux: python3 -m contextzip setup-path
```

On Windows this adds the folder to your user PATH (no admin needed). On macOS and Linux it adds one line to your shell's startup file. Open a new terminal and `cz` works. Until then, `python -m contextzip` does everything `cz` does.

If you installed with pipx, run `pipx ensurepath`. With uv, run `uv tool update-shell`.

## Quick start

```bash
cd ~/projects/my-app
contextzip
```

You don't need any flags. contextzip detects your framework, excludes the noise, writes `codebase.zip` into `.contextzip/output/codebase/` and copies it to your clipboard. Paste it into your AI tool.

Some other common runs:

```bash
contextzip --dry-run --verbose              # preview every included/excluded file, write nothing
contextzip --git-changes                    # only what you've changed
contextzip --prompt "fix the login redirect bug"   # let AI pick the relevant files
contextzip include src/ app/                # only these folders
contextzip include src/ .env                # .env too, values redacted
contextzip --here                           # only the folder you're in (monorepos)
contextzip github.com/user/repo             # a GitHub/GitLab/Bitbucket repo, without cloning it
contextzip exclude tests/ '*.snap'          # everything except these
contextzip apply-zip                        # write the AI's changes back
```

## Features

### Smart packaging

contextzip scans your project and recognises its stack from marker files such as `package.json`, `next.config.*`, `pyproject.toml`, `manage.py`, `Cargo.toml`, `go.mod` and `Gemfile`. It looks a couple of levels deep, so a monorepo with a Next.js frontend and a FastAPI backend gets **both** rule sets applied.

| Ecosystem | Examples of what's excluded |
|---|---|
| **Node.js / Next.js** | `node_modules/`, `.next/`, `dist/`, `build/`, `coverage/`, lockfiles, `*.min.js` |
| **Python / Django / FastAPI** | `__pycache__/`, `.venv/`, `*.pyc`, `.pytest_cache/`, `*.egg-info/` |
| **Rust** | `target/`, `Cargo.lock`, `*.rlib` |
| **Go** | `vendor/`, `bin/`, `go.sum` |
| **Ruby / Rails** | `vendor/bundle/`, `tmp/`, `log/`, `storage/`, built gems |

On top of the stack rules, contextzip also:

- respects your `.gitignore` files, including ones in subfolders like `apps/web/.gitignore` (turn this off with `--no-gitignore`),
- flags large files (1 MB or more by default) and binary files before packaging,
- skips dangling symlinks and unreadable files, and tells you why,
- writes a plain-text report next to every ZIP listing what went in and what was left out,
- estimates the token count of what it packed (`≈ 46k tokens`), so you know whether it'll fit before you upload.

### Package only what changed

```bash
contextzip --git-changes
```

This packages only the files git reports as **staged, modified or untracked**. It's ideal for code review or "here's what I changed, what's wrong?" prompts. Deleted files, submodules and monorepo roots are handled for you, and the secret-exclusion rules still apply to files git tracks.

### Package a GitHub, GitLab or Bitbucket repo

```bash
contextzip github.com/pallets/click
contextzip https://github.com/pallets/click/tree/main/src     # one branch, one folder
contextzip gh:you/private-repo --ref dev
contextzip gitlab.com/group/project                           # or gl:group/project
contextzip bitbucket.org/team/repo                            # or bb:team/repo
```

No clone and no git needed: contextzip downloads one commit through the host's API into a local cache and packages it like any other project, with the same stack detection, exclusions and redaction. `include` and `exclude` work as usual, and a link to a branch's folder packages just that folder.

- **Private repos:** set `GITHUB_TOKEN` (or sign in with `gh auth login`), `GITLAB_TOKEN` or `BITBUCKET_TOKEN`. A token is only ever sent to its own host's API.
- **Send the reply back as a pull request:** for a GitHub repo, `contextzip apply-zip reply.zip --pr` commits the AI's changes on top of the commit you packaged and opens a pull request, forking first if you can't push. No clone needed.
- **Cache:** a commit is downloaded once and reused. Override the location with `CONTEXTZIP_CACHE_DIR`; downloads over 250 MB are refused unless you raise `CONTEXTZIP_REMOTE_MAX_MB`.

### In your browser

[contextzipcli.com](https://www.contextzipcli.com) runs contextzip in your browser, with the same rules as the CLI:

- **Drop a folder** (or choose one) and it's packaged right in the page. Your files never leave your machine.
- **Paste a GitHub, GitLab or Bitbucket link** to package a public repository. Shortcut: put `contextzipcli.com/` in front of a repo URL, as in `contextzipcli.com/github.com/user/repo`.
- **Apply the AI's reply** to your folder (Chrome and Edge): pick the folder and the ZIP, review the plan, and the safe changes are written for you.

### VS Code extension

[contextzip for VS Code](https://marketplace.visualstudio.com/items?itemName=contextzip.contextzip) bundles the CLI, so it works without Python.

- **Package for AI** from the Explorer's right-click menu, the status bar or the command palette: the whole project, selected files and folders, git changes, the files for a task, or a GitHub repo. The ZIP goes on your clipboard.
- **Apply the reply:** when an AI's ZIP lands in your Downloads, the extension offers to apply it. Every file's status shows in the **AI Changes** view, safe changes apply in one step, and conflicts open in VS Code's three-way merge editor.

### MCP server for AI agents

```bash
pip install "contextzip[mcp]"      # Python 3.10+
```

Then register it in your MCP client:

```json
{"mcpServers": {"contextzip": {"command": "contextzip", "args": ["mcp"]}}}
```

An agent can then package a project or a repository, preview and apply a reply, list snapshots and open a pull request. Each tool returns the same JSON as `--json`. See [CLI.md](https://github.com/akadeepesh/contextzip/blob/main/CLI.md#contextzip-mcp) for every tool.

### AI-powered file selection

```bash
contextzip --prompt "add rate limiting to the login endpoint"
```

Describe your task in plain language and [Gemini](https://ai.google.dev/) picks only the files that matter. The ZIP also includes a `prompt.txt` describing the task, so the AI tool you paste it into knows what you want.

- **Setup:** you need a free Gemini API key. contextzip walks you through it the first time you use `--prompt`, or you can set the `GEMINI_API_KEY` environment variable.
- **What is sent:** only the list of file **paths and sizes** that already passed the exclusion rules, plus your prompt. File contents never leave your machine.
- **Fallback:** if Gemini is rate-limited, a local keyword-based selector takes over, so `--prompt` keeps working. Other errors (a bad key, no network) stop with a clear message.
- **Model:** `gemini-2.5-flash-lite` by default.

### Applying AI changes back

```bash
contextzip apply-zip              # picks up a ZIP from .contextzip/inbox/
contextzip apply-zip fix.zip      # or point at one directly
contextzip apply-zip --dry-run    # preview without writing anything
```

AI tools return ZIPs in every shape: wrapped in an extra folder, relative to a subfolder, flattened, with Windows separators. `apply-zip` matches each file to its real place in your project anyway, and new files follow their siblings.

Every ZIP contextzip makes gets a **local snapshot** (a hash of each file, never uploaded). `apply-zip` picks the snapshot that best matches the returned ZIP and compares three versions of each file: what you sent, what's on disk now, and what came back.

| Status | Meaning | Default |
|---|---|---|
| **New** / **Modified** | New file, or only the AI changed it | Written |
| **Merged** | You both changed it, in different places | Written (three-way merge) |
| **Unchanged** | Already matches — including changes you already applied | Skipped |
| **Conflict** | You both changed the same or neighbouring lines | Needs your call |
| **Partial** | The AI returned a cut-down file (`// ... rest unchanged`) | Needs your call |
| **Moved** | The AI moved or renamed it | Written at the new path; the old file is kept unless you say otherwise |
| **Drifted** / **Deleted** / **Untracked** / **Ambiguous** / **Delete requested** | Can't be applied safely without you | Needs your call |

When something needs a decision you choose: apply the safe changes now, **review side by side in your browser** (`--review`), **resolve in VS Code's merge editor** (`--editor`), or apply everything. Line endings never count as a change, redacted secrets are never overwritten with placeholders, and **files are only removed with your explicit OK** — never one you've changed since.

### Snapshots and CONTEXTZIP.md

Every package is kept locally as a numbered **snapshot** (`#14`), so a reply can be merged against exactly what you sent, even after you've packaged again. Each ZIP also carries a short **`CONTEXTZIP.md`** for the AI: the snapshot ID, and how to reply so the answer applies cleanly (complete files, same paths, secrets left as `[REDACTED]`, moves and deletions listed). When the reply echoes it back, `apply-zip` knows the exact snapshot. `contextzip snapshots` lists them; turn the file off with `"zip_guide": false`.

### Terminal error watcher

```bash
contextzip watch -- npm run dev
contextzip watch -- python manage.py runserver
contextzip watch -- python -m pytest --tb=short
```

contextzip runs your command and watches its output. When a stack trace appears (Python, Django and Node.js/Next.js errors are recognised), it asks whether to package debug context. Press `D` and you get `debug-context.zip` containing:

- `prompt.txt`: an auto-generated description of the problem
- `terminal-error.txt`: the error output with ANSI codes and noise stripped out
- `source-files.zip`: the source files named in the stack trace

If you press Ctrl+C before packaging anything, you get one last chance to capture the whole session.

### Visual config UI

```bash
contextzip config --ui
```

This opens a local browser tab where you can tick files and folders to always include or exclude, with a live preview of what the ZIP will contain. It saves to `.contextzip/config.json`. The server binds to `127.0.0.1` only, needs a random one-time session token and shuts itself down when you're done.

## Command reference

| Command | What it does |
|---|---|
| `contextzip` | Detect the stack, exclude noise and package the project |
| `contextzip github.com/user/repo` | Package a GitHub, GitLab or Bitbucket repository without cloning it |
| `contextzip include PATH…` | Package **only** these paths |
| `contextzip exclude PATTERN…` | Package everything **except** these patterns |
| `contextzip --prompt "task"` | Let Gemini pick only the files relevant to a task |
| `contextzip --git-changes` | Package only modified, staged and untracked files |
| `contextzip apply-zip [ZIP]` | Write an AI-returned ZIP back into your project, safely |
| `contextzip watch -- COMMAND` | Run a command and package debug context when an error appears |
| `contextzip apply-zip ZIP --pr` | Send an AI's reply to a GitHub repo back as a pull request |
| `contextzip snapshots` | List what you've sent, so a reply can be merged against it |
| `contextzip mcp` | Run the MCP server for AI agents |
| `contextzip config …` | Gemini key, workspace location and the visual config UI |
| `contextzip update` | Update to the latest release (or print the npm, Homebrew or Scoop command for a standalone build) |
| `contextzip setup-path` | Put `cz` on your PATH when pip didn't |

**Run flags** work on `contextzip`, `include` and `exclude`:

| Flag | Short | Description |
|---|---|---|
| `--include PATH` | `-i` | Only include files under these paths. Naming an excluded path (like `.env`) brings it back. Repeatable. |
| `--exclude PATTERN` | `-e` | Extra exclusion patterns (gitignore syntax). Repeatable. |
| `--prompt TEXT` | `-p` | Let Gemini select files relevant to this task. |
| `--git-changes` | | Only include files git reports as changed. |
| `--dry-run` | `-n` | Show what would be included without creating the ZIP. |
| `--output FILE` | `-o` | Write the ZIP to this exact path. |
| `--no-clipboard` | | Don't copy the ZIP to the clipboard. |
| `--no-gitignore` | | Ignore `.gitignore` and use only the built-in rules. |
| `--verbose` | `-v` | List every included and excluded file. |
| `--from REPO` | | Package a remote repository instead of the local project (or just put the repo first: `contextzip github.com/user/repo`). |
| `--ref REF` | | Branch, tag or commit for `--from`. |
| `--json` | | Print one JSON object instead of terminal output, for editors and scripts. Also on `apply-zip` and `snapshots`. |

Every command supports `-h` / `--help`, and `contextzip --version` prints the installed version.

For every flag of every subcommand (`apply-zip`, `snapshots`, `watch`, `mcp`, `config`, `update`), see **[CLI.md](https://github.com/akadeepesh/contextzip/blob/main/CLI.md)** or the [online CLI reference](https://www.contextzipcli.com/docs/cli).

## Configuration

contextzip has two config files, and both are optional.

### Project config: `.contextzip/config.json`

This holds per-project settings. Edit it by hand or with `contextzip config --ui`. Every key is optional:

```jsonc
{
  "always_include": ["docs/architecture.md"],  // force-include, even if a rule or .gitignore excludes it
  "always_exclude": ["*.snap", "fixtures/"],   // a standing --exclude on every run
  "workspace_location": "git-root",            // "git-root" | "cwd" | "/some/path"
  "ai": {
    "enabled": true,
    "max_files": 10,                           // cap on files the AI may select
    "prompt_template": "We use pytest, not unittest."  // prepended to every prompt.txt
  },
  "limits": {
    "max_file_size_mb": 1,                     // threshold for "large file" warnings
    "redact_secrets": false                    // scan included files and mask secret-looking values
  },
  "applied_zip_retention": 1,                  // past apply-zip archives to keep
  "cleanup": { "enabled": true, "keep_recent": 1 },
  "snapshots": { "keep": 10, "max_age_days": 14 },  // history apply-zip merges against
  "zip_guide": true,                           // add CONTEXTZIP.md to every ZIP
  "webui": { "auto_open": true, "port": null }
}
```

`.contextzip/` is fully gitignored by default. If your team wants to share these settings, commit the file explicitly with `git add -f .contextzip/config.json`.

### Personal config

Your Gemini API key and personal defaults live outside the project, so they're never committed:

| OS | Location |
|---|---|
| Linux / macOS | `~/.config/contextzip/config.json` (or `$XDG_CONFIG_HOME/contextzip/`) |
| Windows | `%APPDATA%\contextzip\config.json` |

```bash
contextzip config                          # show the current configuration
contextzip config --reset-key              # clear the Gemini key and set it up again
contextzip config --set-workspace cwd      # put .contextzip/ in the current directory
contextzip config --show-key-path          # print the config file location
```

The full option-by-option guide is at [contextzipcli.com/docs/configuration](https://www.contextzipcli.com/docs/configuration).

## What is always excluded

These patterns apply to **every** project, whatever its stack or `.gitignore` says, and in `--git-changes` mode too:

- **Secrets & credentials:** `.env`, `.env.*`, `*.pem`, `*.key`, `*.p12`, `*.pfx`, `*.jks`, `*.keystore`, `*.ppk`, SSH private keys (`id_rsa`, `id_ed25519`, …), `.npmrc`, `.netrc`, `.pypirc`, `.pgpass`, Docker auth config, `.aws/credentials`, service-account and `*credentials*.json` files, kubeconfigs
- **Infrastructure state:** `*.tfstate`, `.terraform/`
- **Version control & editors:** `.git/`, `.svn/`, `.hg/`, `.vscode/`, `.idea/`, swap files, `.DS_Store`
- **Logs, caches & temp files:** `*.log`, `logs/`, `.cache/`, `tmp/`, `*.tmp`, `*.bak`
- **Binaries & media:** executables, shared libraries, images, video, audio and archives
- **Project boilerplate:** `CHANGELOG`, `LICENSE`, `CONTRIBUTING`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, GitHub issue/PR templates

To bring one back, name it explicitly with `contextzip include .env`, list it under `always_include` in the project config, or tick it in `contextzip config --ui`. `--exclude` and `.gitignore` can't bring them back, and naming a folder like `src/` doesn't pull in the secret files inside it.

**Secret files you include on purpose are always redacted.** If you include your `.env` so the AI can see which variables your app expects, every key and comment is kept but every value becomes `[REDACTED]`:

```bash
# .env on disk                          # .env in the ZIP
DATABASE_URL=postgres://u:pw@db/app     DATABASE_URL=[REDACTED]
export API_KEY="sk-live-…"  # prod      export API_KEY="[REDACTED]"  # prod
```

Other secret files (private keys, credential files) get a secret-pattern scan. Binary ones like `.p12` can't be redacted, so they're left out. All of this happens whatever your other settings say.

For extra safety, set `limits.redact_secrets: true` to also mask API keys, tokens, JWTs and private-key blocks hardcoded inside ordinary files that get included. Every run's report lists what was redacted.

> **Tip:** before sharing a sensitive project, run `contextzip --dry-run --verbose` to see exactly what will go into the ZIP.

## Where files go

contextzip keeps everything in a `.contextzip/` folder at your git root (change this with `contextzip config --set-workspace`):

```text
.contextzip/
├── output/
│   ├── codebase/      codebase.zip + manifest + report   (contextzip)
│   ├── git-changes/   changes.zip + manifest + report    (--git-changes)
│   ├── prompt/        vibe.zip + manifest + report       (--prompt)
│   └── watch/         debug-context.zip                  (contextzip watch)
├── snapshots/         numbered copies of what you sent   (contextzip snapshots)
├── inbox/             drop AI-returned ZIPs here for apply-zip
└── config.json        project config (optional)
```

Old outputs are cleaned up automatically: `output/` keeps the latest ZIP per mode (`cleanup`), and `snapshots/` keeps the newest 10 or anything under 14 days old (`snapshots`) — never one a reply in the inbox is waiting on.

## Python API

contextzip is also a library. The functions behave like the CLI but return plain Python objects and raise exceptions instead of printing.

```python
from contextzip import get_files, get_git_changes, create_zip, apply_zip, detect_ecosystem, fetch_remote

# All project files, filtered by .gitignore and the built-in rules
collection = get_files(include=["src/"], exclude=["*.log"])
for path in collection.files:          # plain pathlib.Path objects
    print(path)

# Only git-changed files, zipped
changes = get_git_changes()
pkg = create_zip(changes, output="/tmp/changes.zip")
print(f"Packed {pkg.file_count} files → {pkg.zip_path}")

# What stack is this?
print(detect_ecosystem("."))

# A remote repository, without cloning it
repo = fetch_remote("github.com/pallets/click")
pkg = create_zip(get_files(repo.project_dir), output=repo.zip_path)
```

| Function | Returns |
|---|---|
| `get_files(path=None, *, include=None, exclude=None, …)` | `FileCollection` of all project files after filtering |
| `get_git_changes(path=None, *, use_project_config=True)` | `FileCollection` of staged, modified and untracked files |
| `create_zip(collection, output=…)` | `PackageResult` with the ZIP path, file count and sizes |
| `apply_zip(zip_path=None, …)` | `ApplyResult` describing what was written |
| `detect_ecosystem(path=None)` | Detected ecosystem information |
| `fetch_remote(target, ref=None)` | `FetchedRemote`: a GitHub, GitLab or Bitbucket repo unpacked into the cache (`project_dir`, `sha`, `zip_path`) |
| `web.run(args, cwd=None)` | Any CLI command's `--json` result as a dict, without printing or prompting (used in the browser via Pyodide, and on servers) |

All exceptions inherit from `ContextzipError`: `NotARepositoryError`, `GitNotFoundError`, `GitCommandError`, `NoFilesError`, `ZipNotFoundError` and `RemoteFetchError`.

For full signatures, see [contextzipcli.com/cli#python-api](https://www.contextzipcli.com/docs/cli#python-api).

## Privacy & security

- **Local by default.** Packaging, `apply-zip`, `watch` and the config UI never make network requests.
- **Network use is opt-in and limited to three features:**
  - `--prompt` sends file paths, sizes and your prompt to the Gemini API. File contents are never sent.
  - Packaging a remote repo (`contextzip github.com/user/repo`) downloads it from that host, and `apply-zip --pr` opens a pull request on GitHub. A token, if any, is sent only to its own host's API.
  - `contextzip update` asks PyPI for the latest version number.
- **No telemetry.** Nothing is collected or phoned home.
- **Manifests stay local.** `apply-zip` manifests are written next to the ZIP and never inside it.
- **Secrets are excluded or redacted.** See [What is always excluded](#what-is-always-excluded).

Found a vulnerability? Please follow [SECURITY.md](https://github.com/akadeepesh/contextzip/blob/main/SECURITY.md) and report it privately rather than in a public issue.

## Platform support

| | Windows | macOS | Linux |
|---|---|---|---|
| Packaging, `apply-zip`, config | ✅ | ✅ | ✅ |
| ZIP copied to clipboard as a file | ✅ native (`CF_HDROP`) | ✅ via Finder | ✅ built-in X11 owner (works under XWayland), or `wl-copy` / `xclip` |
| `contextzip watch` | ✅ (best in Windows Terminal / VS Code) | ✅ | ✅ |
| Standalone build (no Python: npm, Homebrew, Scoop, download) | ✅ x64 | ✅ Apple Silicon and Intel | ✅ x64 and arm64, glibc 2.35+ |

If the clipboard can't be used, contextzip opens the output folder with the ZIP selected and tells you why. Use `--no-clipboard` to skip this step entirely.

## Contributing

Contributions are welcome, especially **new framework rule sets** and edge-case fixes. Not up for writing code? [Request a feature](https://www.contextzipcli.com/request-feature) or [request your language](https://www.contextzipcli.com/request-language). Both turn into GitHub issues ranked by 👍. To set up a local copy:

```bash
git clone https://github.com/akadeepesh/contextzip
cd contextzip
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
contextzip --version
```

Adding a new language takes three small steps: a `PATTERNS` list in `contextzip/rules/`, one registry entry and one detection rule. [CONTRIBUTING.md](https://github.com/akadeepesh/contextzip/blob/main/CONTRIBUTING.md) walks through it, along with PR conventions. Please open an issue before starting a large PR.

## License

[MIT](https://github.com/akadeepesh/contextzip/blob/main/LICENSE) © Deepesh

---

<p align="center"><sub>
  Built by <a href="https://akadeepesh.com?ref=github">Deepesh Kumar</a>
</sub></p>
