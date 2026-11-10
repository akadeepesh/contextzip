# Roadmap prompts

Releases 0.8 and 0.9 are written here as prompts you can hand to an AI in a web chat (Claude Sonnet is enough), using contextzip itself for the round trip. Each release is split into steps. One step is one chat.

- [0.8-tokens.md](0.8-tokens.md): real token counts, per-file breakdown, model fit, token budget.
- [0.9-terminal.md](0.9-terminal.md): designed output, better help, completions, `init`, `doctor`, the terminal picker.

## How to run one step

1. Start from a clean tree on a branch: `git checkout -b feat/0.8-tokens` (once per release).
2. Make the ZIP with the command the step gives under **Send**. It names only the files that step needs, which keeps the chat small and the model focused.
3. Open a **new** chat. Paste, in this order: the [ground rules](#ground-rules) below, then the step's **Prompt**, then attach the ZIP.
4. Download the ZIP the AI returns into `.contextzip/inbox/` and run `cz apply-zip --dry-run`, then `cz apply-zip`.
5. Run the step's **Checks**. All of them must pass before you go on.
6. If a check fails, reply in the same chat with the exact command and its full output, and ask for corrected files. Do not edit by hand and move on: the next step assumes this one is exactly right.
7. Commit: one commit per step, message given in the step.

Do the steps in order. A step never depends on a later one.

## Ground rules

Paste this block at the top of every chat, unchanged.

```text
You are working on contextzip, a Python CLI (package `contextzip`, entry points `contextzip` and `cz`)
that packages a codebase into a ZIP for AI chat tools and applies the AI's reply back. I attached a
ZIP with the files you need. Read CONTEXTZIP.md in it first and follow its reply rules.

Hard rules. Breaking any of them makes the reply unusable:

1. Reply with a ZIP of COMPLETE files, at the same paths as in my ZIP. Never a diff, never a file with
   "... rest unchanged". Include only files you changed or created. If you cannot produce a ZIP, give
   each file in full in its own code block, with its path on the line above it.
2. Python 3.9 must work. Every module starts with `from __future__ import annotations`. No `match`
   statements, no `X | Y` unions evaluated at runtime (isinstance, dataclass defaults, typing.get_type_hints),
   no `zip(strict=...)`, no `str.removeprefix` on values that may be bytes, no parenthesised context managers.
3. Add no dependency I did not name in this prompt. The standard library, click, rich, pathspec and httpx
   are available. Import anything heavy inside the function that uses it, not at module top level.
4. Change only what this step asks for. Do not rename, move or reformat existing code. Do not touch
   `contextzip/watcher.py`, `contextzip/error_parser.py` or `contextzip/rules/errors/`.
5. `--json` output is a public contract: you may ADD keys, never remove, rename or change the type of one.
6. Terminal output goes through the helpers in `contextzip/cli_display.py` (`ok`, `warn`, `err`, `info`,
   `status`), so that `--json` collects it. Never call print(). No emoji. The only symbols allowed are
   the ones already used: ✓ ! ✗ · → ≈ ↓ and box-drawing/block characters where this prompt shows them.
7. User-facing text: plain, short sentences. Say what happened and what to do next. No exclamation marks.
8. Tests are `unittest.TestCase` classes run by pytest. Use `tests.helpers.TempProject` for anything that
   needs files on disk (it gives `self.root`, `self.write(rel, text)`). Every new behaviour gets a test.
   Never weaken or delete an existing test to make yours pass. If an existing test must change because
   this prompt changes the behaviour it pins, change only that assertion and say so in your summary.
9. Do not change version numbers. Add user-facing changes to CHANGELOG.md under a `## [Unreleased]`
   heading at the END of the file (create it if missing), in the style of the entries above it.
10. If something in this prompt contradicts the code you see, stop and tell me instead of guessing.

End your reply with a short summary: files changed, anything you were unsure about, and anything this
prompt asked for that you did not do.
```

## After the last step of a release

Bump the version in the five places CONTRIBUTING.md lists (a test enforces it), rename `## [Unreleased]` in CHANGELOG.md to the version and date, run `python -m pytest`, and commit `chore(release): contextzip X.Y.0`.
