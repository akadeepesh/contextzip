# contextzip CLI reference

Every command and flag in one place. `cz` is a short alias for `contextzip` — they are the same program.

> **Keeping this file current:** whenever you add, rename or remove a command or flag in `contextzip/cli.py`, update this file in the same commit and mention it in the `CHANGELOG.md` entry. `contextzip --help` and `contextzip <command> --help` are always the source of truth.

## Contents

- [Quick reference](#quick-reference)
- [Run flags](#run-flags)
- [`contextzip`](#contextzip) — package the project
- [GitHub repositories](#github-repositories) — package a repo without cloning it
- [`contextzip include`](#contextzip-include) / [`exclude`](#contextzip-exclude)
- [`contextzip apply-zip`](#contextzip-apply-zip)
- [`contextzip watch`](#contextzip-watch)
- [`contextzip config`](#contextzip-config)
- [`contextzip update`](#contextzip-update)
- [`contextzip setup-path`](#contextzip-setup-path)
- [Global flags](#global-flags)
- [JSON output](#json-output)
- [Where files go](#where-files-go)

## Quick reference

| Command | What it does |
|---|---|
| `contextzip` | Detect the stack, exclude noise, package the project |
| `contextzip github.com/user/repo` | Package a GitHub repository without cloning it |
| `contextzip include PATH…` | Package **only** these paths |
| `contextzip exclude PATTERN…` | Package everything **except** these patterns |
| `contextzip --prompt "task"` | Let Gemini pick only the files relevant to a task |
| `contextzip --git-changes` | Package only modified / added / untracked files |
| `contextzip apply-zip [ZIP]` | Write an AI-returned ZIP back into your project, safely |
| `contextzip watch -- COMMAND` | Run a command and package debug context when an error appears |
| `contextzip config …` | Gemini key, workspace location, visual include/exclude UI |
| `contextzip update` | Check PyPI for a newer release and update in place |
| `contextzip setup-path` | Put `cz` on your PATH when the terminal can't find it |

## Run flags

These flags work on the main command **and** on `include` and `exclude`. Flags go after the verb, like `git` or `docker`: `contextzip exclude CHANGELOG.md --dry-run --verbose`.

| Flag | Short | Description |
|---|---|---|
| `--include PATH` | `-i` | Only include files under these paths (relative to the project root). Naming a path the rules exclude, like `.env`, brings it back. Repeatable. Same as `contextzip include`. |
| `--exclude PATTERN` | `-e` | Extra exclusion patterns on top of the automatic rules (gitignore syntax). Repeatable. Same as `contextzip exclude`. |
| `--prompt TEXT` | `-p` | Describe your task in plain language; Gemini selects only the relevant files. Needs a free Gemini API key (you're guided through setup on first use). |
| `--dry-run` | `-n` | Show what would be included without creating the ZIP. |
| `--output FILE` | `-o` | Write the ZIP directly to `FILE`, bypassing the `.contextzip/` workspace. |
| `--no-clipboard` | | Skip the clipboard step after creating the ZIP. |
| `--no-gitignore` | | Ignore the project's `.gitignore` (use only the built-in rules). |
| `--git-changes` | | Only include files git reports as modified, added or untracked. The project must be inside a git repository. |
| `--verbose` | `-v` | Show every included and excluded file. |
| `--here` | | Only package the folder you're in. Paths in the ZIP stay relative to the project root. |
| `--from REPO` | | Package a GitHub repository instead of the local project. See [GitHub repositories](#github-repositories). |
| `--ref REF` | | Branch, tag or commit to package with `--from`. Defaults to the repository's default branch. |
| `--json` | | Print one JSON object instead of terminal output. Never prompts. See [JSON output](#json-output). |

`--include` and `--exclude` (and the `include` / `exclude` subcommands) also work together with `--git-changes`, and are combined with `always_include` / `always_exclude` from `.contextzip/config.json`.

## Project root

Every command works from the **project root**, wherever you run it from, like git does. Run `cz` inside `frontend/` of a monorepo and the workspace stays at the root (no `frontend/.contextzip/`), the ZIP's paths are root-relative, and `apply-zip` puts files back in the right place.

The root is, first match wins:

1. `CONTEXTZIP_ROOT`, if set.
2. The **outermost** folder (not above your home folder) that is a git repository (`.git` folder or file) or has a `.contextzip/config.json`. Outermost on purpose: tools like create-next-app run `git init` inside the folder they generate, which leaves a nested `frontend/.git` in your monorepo.
3. The outermost folder with an existing `.contextzip/` workspace.
4. The current folder.

Paths you type are relative to where you are, like `git add`: from `frontend/`, `cz include src/` means `frontend/src/`. Add `--here` to package just the current folder. `contextzip config` shows which root was picked and why. `--git-changes` also reads repositories nested inside the root (an embedded `frontend/.git`, or submodules).

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

The summary line ends with an estimated token count (`≈ 46k tokens`), so you can tell whether the ZIP will fit before you upload it. It's bytes of text divided by four: a rough guide, since every model counts differently.

## GitHub repositories

Package a GitHub repository without cloning it. Put the repository where a command would go, or pass it with `--from`:

```bash
contextzip github.com/pallets/click                          # default branch
contextzip https://github.com/pallets/click/tree/main/src    # one branch, one folder
contextzip gh:pallets/click --ref 8.1.7                      # a tag (or branch, or commit)
contextzip gh:pallets/click include src/ tests/test_basic.py # include/exclude work as usual
contextzip --from git@github.com:you/private-repo.git -n
```

contextzip asks the GitHub API which commit to use, downloads that commit as one archive (no git needed) and unpacks it into a local cache. Then it's an ordinary project: stack detection, exclusion rules and redaction all apply, and the ZIP goes on your clipboard. A `/tree/<branch>/<folder>` link packages just that folder; paths you name with `include` are relative to it.

- **Private repositories** use a token from `GITHUB_TOKEN` or `GH_TOKEN`, or the one `gh auth token` prints if you're signed in to the GitHub CLI. The token is only sent to `api.github.com`. A token also lifts GitHub's limit of 60 API requests an hour.
- **Cache:** each commit is kept in your user cache folder (`%LOCALAPPDATA%\contextzip\Cache\remote` on Windows, `~/Library/Caches/contextzip/remote` on macOS, `~/.cache/contextzip/remote` on Linux; override with `CONTEXTZIP_CACHE_DIR`). The ZIP and its report are written next to the unpacked tree. A cached commit isn't downloaded again, and a full commit SHA that's cached needs no network at all. The 3 most recent commits per repository are kept.
- **Size limit:** downloads over 250 MB are refused; change it with `CONTEXTZIP_REMOTE_MAX_MB`.
- **Not unpacked:** `.git/`, `.contextzip/`, symlinks, and names your system can't store (on Windows, names like `CON` or containing `:`).
- `--git-changes` needs a local checkout, so it can't be combined with a repository. To apply the AI's reply, package from a clone and run `apply-zip` there.

## `contextzip include`

Package only the specified paths and skip everything else.

```bash
contextzip include PATH…  [run flags]

contextzip include src/ app/
contextzip include src/ app/ --dry-run
contextzip include src/ .env            # bring back a file the rules exclude
contextzip include dist/                # or a whole excluded folder
```

Paths match at folder boundaries: `src` matches `src/index.ts` but not `src2/index.ts`. Leading `./` or `.\` is ignored.

**Naming an excluded path brings it back.** If a path you name is itself excluded (by the built-in rules, `.gitignore` or `always_exclude`), it is included anyway, because you asked for it explicitly. Inside a named folder, excluded folders nested deeper are still skipped (`include dist/` leaves out `dist/node_modules/`). Naming an ordinary folder only narrows the run: `include src/` does **not** pull in `src/.env`. Secret files brought back this way, such as `.env`, keep their keys but have every value replaced with `[REDACTED]`. The same applies to `--include` / `-i`.

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
contextzip apply-zip [ZIP] [--manifest PATH] [-n] [-v] [--review | --editor | --safe-only | -y] [--json]
```

| Argument / flag | Short | Description |
|---|---|---|
| `ZIP` | | Path to the returned ZIP. Optional: if omitted, contextzip looks in `.contextzip/inbox/`. An explicit path overrides the inbox. |
| `--manifest PATH` | | Snapshot to compare against, instead of the one that best matches the ZIP. |
| `--dry-run` | `-n` | Show what would change; write nothing. |
| `--verbose` | `-v` | List every file and what will happen to it. |
| `--review` | | Decide file by file in a local browser page with side-by-side diffs. |
| `--editor` | | Resolve files that need a decision in VS Code (or Cursor, Windsurf, VSCodium). |
| `--safe-only` | | Apply only the safe changes; leave the rest untouched. For scripts and CI. |
| `--yes` | `-y` | Apply everything without asking. Where you and the AI disagree, the AI's version wins. Never removes files. |
| `--snapshot ID` | | The snapshot this reply answers (e.g. `14`). Usually found automatically. |
| `--remove-moved` | | Remove the old copy of files the AI moved (only if you haven't changed them). |
| `--allow-delete` | | Delete files the AI asked to delete in `CONTEXTZIP.md` (only if you haven't changed them). |
| `--json` | | Print one JSON object with every file's status. Never prompts: if files need a decision, nothing is written unless `--safe-only`, `--yes` or `--review` says what to do. Can't be combined with `--editor`. |

```bash
contextzip apply-zip                    # from .contextzip/inbox/, asks when something needs a decision
contextzip apply-zip fix.zip            # a ZIP anywhere on disk
contextzip apply-zip --dry-run -v       # see exactly what would happen
contextzip apply-zip --review           # decide in the browser
contextzip apply-zip --editor           # three-way merge in VS Code
contextzip apply-zip --safe-only        # CI: only the safe changes
```

**Where each file goes.** The returned ZIP doesn't need to match your layout. Each file is matched to the project by its longest shared path suffix, so a ZIP wrapped in an extra folder, made relative to a subfolder (`src/...` for `frontend/src/...`), flattened to bare names, or using backslash separators still lands correctly. The layout learned from files that clearly match is applied to brand-new files too. A name that fits several files is settled by content; if it's still unclear the file is marked *ambiguous* and never written on a guess. `__MACOSX/`, `.DS_Store` and contextzip's own `prompt.txt` are ignored.

**What it compares against.** Every ZIP contextzip makes gets a local manifest, never uploaded. `apply-zip` picks the manifest that best matches the returned ZIP, not just the newest, so an unrelated run in between (or `-o`) doesn't confuse it. Each file is then compared three ways: what you sent, what's on disk now, and what came back.

| Status | Meaning | Default |
|---|---|---|
| new | The file doesn't exist yet | written |
| modified | Only the AI changed it | written |
| merged | You both changed it in different places — three-way merged | written |
| unchanged | Already matches, including "you already applied this" | skipped |
| conflict | You both changed the same or neighbouring lines | needs your call |
| drifted | You changed it and it can't be merged (binary, or the original ZIP is gone) | needs your call |
| deleted | You deleted it after zipping | needs your call |
| untracked | Exists here but wasn't in the ZIP you sent | needs your call |
| partial | The AI returned a cut-down file (`// ... rest unchanged`) | needs your call |
| ambiguous | Can't tell which file it belongs to | needs your call |
| moved | The AI moved or renamed it (declared in `CONTEXTZIP.md`, or near-identical content) | new path written; old file kept unless you say so |
| delete-requested | The AI asked to delete it in `CONTEXTZIP.md` | needs its own yes |

**Snapshots.** The snapshot is found from the `CONTEXTZIP.md` the reply echoes back, or, if it didn't, by matching content against your snapshots. Since every snapshot keeps a copy of what was sent, merging works even if you've packaged again since.

Line endings never count as a change, and text is written back with the local file's line endings and BOM. Secret files that were redacted on the way out are merged against the redacted copy, so your real values are kept and a `[REDACTED]` placeholder is never written over them.

When something needs a decision, `apply-zip` asks: apply the safe changes and leave the rest, review in the browser, resolve in your editor, apply everything, or quit. Without a terminal to ask (CI), it writes nothing unless you pass `--safe-only` or `--yes`. If anything is left undecided, the ZIP stays in the inbox so you can run `apply-zip` again. Only adds and modifies files — **nothing is ever deleted**.

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

## `contextzip snapshots`

List this project's snapshots: number, token, mode, file count, age, and whether a reply has been applied.

```bash
contextzip snapshots [--json]
```

Every package is kept as a numbered snapshot in `.contextzip/snapshots/` (local, never uploaded): a copy of the ZIP plus a manifest of what was sent. Each ZIP's `CONTEXTZIP.md` names its snapshot, e.g. `Snapshot: 14-k3f9a2`. Snapshots are kept while they're among the newest `snapshots.keep` (10), younger than `snapshots.max_age_days` (14), or still awaited by a reply in the inbox.

## `CONTEXTZIP.md`

A short file added to every ZIP for the AI that receives it: the project, the snapshot ID, and how to reply so `apply-zip` can put the answer back cleanly — only changed files, complete contents, the same paths, `[REDACTED]` values left alone, and moves or deletions listed under **Changes** (`moved: a -> b`, `deleted: x`). Turn it off per project with `"zip_guide": false` or in `contextzip config --ui`.

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

A standalone build (from npm, Homebrew, Scoop or a downloaded archive) has no pip to call, so `update` prints the command for the tool that installed it instead: `npm install -g @contextzip/cli@latest`, `brew upgrade contextzip` or `scoop update contextzip`. `setup-path` likewise explains that the installer manages PATH.

## `contextzip setup-path`

Put the `cz` and `contextzip` commands on your PATH. Use it when your terminal says `cz` is not recognized or not found after `pip install contextzip`. That happens when pip installs the commands into a folder that isn't on PATH, which is common with the Microsoft Store or install-manager Python on Windows and with `pip install --user`.

```bash
python -m contextzip setup-path [--yes]     # macOS / Linux: python3 -m …
```

| Flag | Short | Description |
|---|---|---|
| `--yes` | `-y` | Skip the confirmation prompt. |

- **Windows:** appends the folder to your user PATH (`HKCU\Environment`), the same setting as the Environment Variables dialog. No admin rights needed. A Microsoft Store (MSIX) Python can't change it from its sandbox, so it prints a PowerShell command to paste instead.
- **macOS / Linux:** appends one `export PATH=…` line (`fish_add_path` for fish) to `~/.zshrc`, `~/.bashrc` (`~/.bash_profile` on macOS), fish's `config.fish` or `~/.profile`.
- **pipx / uv tool / virtualenv installs:** nothing to change; it tells you to run `pipx ensurepath` or `uv tool update-shell`, or to activate the environment.

It never adds the folder twice. Open a new terminal afterwards. `python -m contextzip` works anywhere, and when you use it while `cz` isn't on PATH it prints a one-line tip pointing here.

## Global flags

| Flag | Short | Description |
|---|---|---|
| `--help` | `-h` | Show help. Works on every command: `contextzip apply-zip -h`. |
| `--version` | | Print the installed version. |

## JSON output

Add `--json` to `contextzip`, `include`, `exclude`, `apply-zip` or `snapshots` and contextzip prints exactly one JSON object on stdout instead of terminal output, for editors, scripts and CI. It never prompts, and the exit code matches `ok`: 0 when it's `true`, non-zero when it's `false`.

```bash
contextzip --json --no-clipboard
contextzip --json -n --verbose                 # dry run: what would be included, and what wouldn't
contextzip apply-zip reply.zip --json --dry-run
```

Every object has `ok`, `command`, `version`, `warnings` (strings) and `log` (each step as `{"level", "text", "detail"}`), plus `error` when `ok` is false. The rest depends on the command:

| Command | Fields |
|---|---|
| `contextzip`, `include`, `exclude` | `mode`, `dry_run`, `project` (`root`, `kind`), `remote` (with `--from`), `detection`, `files` (counts, `included_bytes`, `estimated_tokens`, `included_files`, warnings; `excluded_files` with `--verbose`), `ai` (with `--prompt`), `zip` (path, sizes, `estimated_tokens`, `snapshot`, `redacted`; `null` on a dry run), `report`, `clipboard` |
| `apply-zip` | `project`, `plan` (`snapshot`, `remap`, `needs_decision`, `counts`, and `files`: each with `path`, `source`, `status`, `safe`, `reason`, `moved_from`, …), `result` (`written`, `skipped`, `removed`, `zip_kept`, `archived_to`; `null` when nothing was written), `report` |
| `snapshots` | `project`, `snapshots` (`id`, `token`, `label`, `mode`, `files`, `status`, `created`) |

## Where files go

| Path | Contents |
|---|---|
| `.contextzip/output/` | ZIPs contextzip creates, plus the manifests used by `apply-zip` |
| `.contextzip/output/watch/` | Debug packages from `contextzip watch` |
| `.contextzip/inbox/` | Where `apply-zip` looks for a returned ZIP when you don't pass a path |
| `.contextzip/config.json` | Project config: `always_include`, `always_exclude`, `workspace_location`, `ai`, size limits |
| `.contextzip/snapshots/` | Numbered snapshots: a copy of each ZIP you sent and its manifest — what `apply-zip` merges against |
| Your user cache folder | GitHub repositories fetched with `--from`, one folder per commit, with the ZIP next to each (see [GitHub repositories](#github-repositories)) |

Where `.contextzip/` itself lives defaults to the git root; see `contextzip config --set-workspace`.
