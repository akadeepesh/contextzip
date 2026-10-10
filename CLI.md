# contextzip CLI reference

Every command and flag in one place. `cz` is a short alias for `contextzip` — they are the same program.

> **Keeping this file current:** whenever you add, rename or remove a command or flag in `contextzip/cli.py`, update this file in the same commit and mention it in the `CHANGELOG.md` entry. `contextzip --help` and `contextzip <command> --help` are always the source of truth.

## Contents

- [Quick reference](#quick-reference)
- [Run flags](#run-flags)
- [`contextzip`](#contextzip) — package the project
- [`contextzip include`](#contextzip-include) / [`exclude`](#contextzip-exclude)
- [`contextzip apply-zip`](#contextzip-apply-zip)
- [`contextzip watch`](#contextzip-watch)
- [`contextzip config`](#contextzip-config)
- [`contextzip update`](#contextzip-update)
- [Global flags](#global-flags)
- [Where files go](#where-files-go)

## Quick reference

| Command | What it does |
|---|---|
| `contextzip` | Detect the stack, exclude noise, package the project |
| `contextzip include PATH…` | Package **only** these paths |
| `contextzip exclude PATTERN…` | Package everything **except** these patterns |
| `contextzip --prompt "task"` | Let Gemini pick only the files relevant to a task |
| `contextzip --git-changes` | Package only modified / added / untracked files |
| `contextzip apply-zip [ZIP]` | Write an AI-returned ZIP back into your project, safely |
| `contextzip watch -- COMMAND` | Run a command and package debug context when an error appears |
| `contextzip config …` | Gemini key, workspace location, visual include/exclude UI |
| `contextzip update` | Check PyPI for a newer release and update in place |

## Run flags

These flags work on the main command **and** on `include` and `exclude`. Flags go after the verb, like `git` or `docker`: `contextzip exclude CHANGELOG.md --dry-run --verbose`.

| Flag | Short | Description |
|---|---|---|
| `--include PATH` | `-i` | Only include files under these paths (relative to the project root). Repeatable. Same as `contextzip include`. |
| `--exclude PATTERN` | `-e` | Extra exclusion patterns on top of the automatic rules (gitignore syntax). Repeatable. Same as `contextzip exclude`. |
| `--prompt TEXT` | `-p` | Describe your task in plain language; Gemini selects only the relevant files. Needs a free Gemini API key (you're guided through setup on first use). |
| `--dry-run` | `-n` | Show what would be included without creating the ZIP. |
| `--output FILE` | `-o` | Write the ZIP directly to `FILE`, bypassing the `.contextzip/` workspace. |
| `--no-clipboard` | | Skip the clipboard step after creating the ZIP. |
| `--no-gitignore` | | Ignore the project's `.gitignore` (use only the built-in rules). |
| `--git-changes` | | Only include files git reports as modified, added or untracked. The project must be inside a git repository. |
| `--verbose` | `-v` | Show every included and excluded file. |

`--include` and `--exclude` (and the `include` / `exclude` subcommands) also work together with `--git-changes`, and are combined with `always_include` / `always_exclude` from `.contextzip/config.json`.

## `contextzip`

Run from your project root. contextzip detects your stack, applies smart exclusions and writes a ZIP into `.contextzip/`. When it finishes, the ZIP is placed on your clipboard as a real file, so you can paste it straight into Claude, ChatGPT or any AI tool's upload box. If the clipboard can't be used, it opens the folder instead and tells you why.

```bash
contextzip                                  # package the whole project
contextzip --dry-run --verbose              # preview, listing every file
contextzip --git-changes                    # only what you've changed
contextzip --prompt "fix the login redirect bug"
contextzip -o ~/Desktop/project.zip         # write to an exact path
contextzip --no-clipboard                   # build the ZIP, skip clipboard
```

## `contextzip include`

Package only the specified paths and skip everything else.

```bash
contextzip include PATH…  [run flags]

contextzip include src/ app/
contextzip include src/ app/ --dry-run
```

## `contextzip exclude`

Exclude specific files or patterns and package everything else. Patterns use gitignore syntax; a folder matches with or without a trailing slash (`.github` and `.github/` both work).

```bash
contextzip exclude PATTERN…  [run flags]

contextzip exclude CHANGELOG.md CONTRIBUTING.md LICENSE
contextzip exclude .github/ tests/ '*.log'
contextzip exclude CHANGELOG.md --dry-run --verbose
contextzip exclude .github/ CHANGELOG.md --output ~/Desktop/out.zip
```

Quote patterns that contain `*` so your shell doesn't expand them first.

## `contextzip apply-zip`

Apply an AI-returned ZIP back into your project.

```bash
contextzip apply-zip [ZIP] [--manifest PATH] [--dry-run] [--verbose] [--yes]
```

| Argument / flag | Short | Description |
|---|---|---|
| `ZIP` | | Path to the returned ZIP. Optional: if omitted, contextzip looks in `.contextzip/inbox/`. An explicit path overrides the inbox. |
| `--manifest PATH` | | Manifest to compare against, instead of auto-detecting the most recent one in `.contextzip/output/`. |
| `--dry-run` | `-n` | Preview what would change without writing anything. |
| `--verbose` | `-v` | Show every file and its status, not just the summary. |
| `--yes` | `-y` | Skip the confirmation prompt, even for risky changes. |

```bash
contextzip apply-zip                    # auto-detect from .contextzip/inbox/
contextzip apply-zip fix.zip            # explicit path
contextzip apply-zip --dry-run --verbose
contextzip apply-zip -y                 # no confirmation, even if risky
```

**How it works.** Every ZIP contextzip creates gets a local manifest — a hash of each included file — written next to it in `.contextzip/output/`. It is never inside the ZIP, so it is never uploaded and never visible to the AI tool. `apply-zip` compares the returned ZIP against that manifest and classifies every file as new, modified, unchanged, or needing a closer look (edited locally since zipping, or no baseline at all) before writing anything. It only adds and modifies files — **nothing is ever deleted**.

## `contextzip watch`

Run a command, watch its output and package debug context when an error appears.

```bash
contextzip watch -- COMMAND [ARGS]…

contextzip watch -- npm run dev
contextzip watch -- python manage.py runserver
contextzip watch -- python -m pytest --tb=short
```

The `--` separates contextzip's own arguments from your command. When an error is detected you're prompted: `[D]` package debug context, `[S]` skip. Pressing `D` writes `.contextzip/output/watch/debug-context.zip` containing:

- `prompt.txt` — an auto-generated task description
- `terminal-error.txt` — the cleaned error output
- `source-files.zip` — the source files named in the stack trace (files you've put in `always_exclude` are left out)

On Ctrl+C, if nothing was packaged yet, you're offered one last chance to capture the whole session.

Notes:

- Works best with dev servers that don't read stdin interactively (`npm run dev`, `manage.py runserver`, `cargo watch`, …).
- On Windows, colour passthrough may be limited; Windows Terminal or the VS Code terminal work best.
- No PTY emulation is used. If your process needs a real TTY to behave correctly, run it directly instead.

## `contextzip config`

Manage configuration. With no flags it shows the current configuration status (including whether a Gemini key is stored or comes from the `GEMINI_API_KEY` environment variable).

```bash
contextzip config [--reset-key] [--set-workspace LOCATION] [--reset-workspace]
                  [--show-key-path] [--ui]
```

| Flag | Description |
|---|---|
| `--reset-key` | Clear the stored Gemini API key and run the setup prompt again. |
| `--set-workspace LOCATION` | Set your personal default for where `.contextzip/` lives: `git-root`, `cwd`, or a path. |
| `--reset-workspace` | Clear your personal workspace location override. |
| `--show-key-path` | Print the path of the config file and exit. |
| `--ui` | Open a local browser tab to set include/exclude rules visually and write `.contextzip/config.json`. |

```bash
contextzip config --reset-key               # clear Gemini key, re-onboard
contextzip config --set-workspace cwd       # always use ./.contextzip here
contextzip config --set-workspace git-root  # back to the default
contextzip config --set-workspace ~/zips    # a fixed custom location
contextzip config --reset-workspace         # clear the personal override
contextzip config --show-key-path           # print the config file location
contextzip config --ui                      # visually set include/exclude
```

A workspace location can also be pinned for a whole team by committing `.contextzip/config.json` at the project root, for example `{"workspace_location": "git-root"}`. A project config takes priority over your personal setting.

## `contextzip update`

Check PyPI for a newer release and update in place.

```bash
contextzip update [--check] [--yes]
```

| Flag | Short | Description |
|---|---|---|
| `--check` | | Only report the current and latest version; install nothing. |
| `--yes` | `-y` | Skip the confirmation prompt and update immediately if an update is available. |

```bash
contextzip update            # check, then confirm before updating
contextzip update --yes      # update immediately if one is available
contextzip update --check    # just report current vs latest
cz update                    # same thing, short alias
```

It detects whether contextzip was installed with pip, pipx or uv tool and re-runs the matching one, upgrading the same interpreter that is currently running (never a different `pip` that happens to be first on your `PATH`). The manual equivalent is `pip install --upgrade contextzip`.

## Global flags

| Flag | Short | Description |
|---|---|---|
| `--help` | `-h` | Show help. Works on every command: `contextzip apply-zip -h`. |
| `--version` | | Print the installed version. |

## Where files go

| Path | Contents |
|---|---|
| `.contextzip/output/` | ZIPs contextzip creates, plus the manifests used by `apply-zip` |
| `.contextzip/output/watch/` | Debug packages from `contextzip watch` |
| `.contextzip/inbox/` | Where `apply-zip` looks for a returned ZIP when you don't pass a path |
| `.contextzip/config.json` | Project config: `always_include`, `always_exclude`, `workspace_location`, `ai`, size limits |

Where `.contextzip/` itself lives defaults to the git root; see `contextzip config --set-workspace`.
