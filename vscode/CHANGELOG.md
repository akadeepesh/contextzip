# Changelog

## [0.1.3] — 2026-11-17

- New description: made for free ChatGPT, Claude and Gemini plans, no paid plan or coding agent needed.
- With contextzip 0.8 and later, the "Packed" message shows the exact token count and whether the ZIP fits Claude, ChatGPT and Gemini ("Fits Claude ≈23% · ChatGPT ≈36%"), and becomes a warning when it's too big for one. Older CLIs show the same message as before.
- Bundles the contextzip 0.9.1 CLI (was 0.5.6): 62 detected stacks including Java, Kotlin and Spring Boot, a much faster scan, exact token counts, and secrets in Spring Boot config files redacted.

## [0.1.2] — 2026-10-24

- Bundles the contextzip 0.5.6 CLI: `.gitignore` files in subfolders are respected (monorepos).

## [0.1.1] — 2026-10-24

- The extension has an icon on the Marketplace and in the Extensions view.

## [0.1.0] — 2026-10-24

First release.

- Package the project, selected files and folders, git changes, the files for a task, or a GitHub repository, with the ZIP on the clipboard.
- Apply an AI's ZIP: a plan of every file in the **AI Changes** view, safe changes in one step, conflicts in the three-way merge editor, in the browser or in the terminal.
- Notices AI replies that land in Downloads and offers to apply them.
- Bundles the contextzip 0.5.5 CLI on Windows x64, macOS and Linux x64/arm64.
