# contextzip for VS Code

**Package the right files of your project for Claude, ChatGPT or any AI chat. Then apply the AI's changes back, without losing your own edits.**

Web chats are great at whole-project changes, but getting a project in and the answer back out is tedious. This extension does both ends:

- **Package for AI.** One click detects your stack, leaves out dependencies, build output and secrets, and puts a lean ZIP on your clipboard as a file. Paste it into the chat.
- **Apply the reply.** Download the ZIP the AI sends back and the extension notices it, matches every file to its real folder (even when the AI flattened the paths), merges around edits you've made since, and lets you resolve anything uncertain in VS Code's merge editor.

## Commands

| Command | What it does |
|---|---|
| **contextzip: Package Project for AI** | The whole project. Also the `cz` button in the status bar. |
| **Package for AI (only these)** | Right-click files or folders in the Explorer. |
| **contextzip: Package Git Changes for AI** | Only staged, modified and untracked files. |
| **contextzip: Package Files for a Task…** | Describe the task; Gemini picks the files from their paths and sizes (contents never leave your machine). Needs a free Gemini API key. |
| **contextzip: Package a GitHub Repository…** | `github.com/user/repo` or a `/tree/<branch>/<folder>` link, without cloning. |
| **contextzip: Apply AI Changes from a ZIP…** | Pick the AI's ZIP, or right-click a `.zip` in the Explorer. |

## Applying a reply

When the AI's ZIP lands in your Downloads folder and it answers something you packaged in this workspace, you're asked whether to apply it. The **AI Changes** view in the Explorer then shows what each file will do:

- **Safe changes** (new, modified, merged, moved) apply in one step.
- **Needs a decision** (conflicts, cut-down files, files you changed in the meantime): resolve them in the three-way merge editor, review them side by side in your browser, or apply everything with the AI's version winning.

Nothing is written until you choose, and files are only ever deleted with your explicit OK.

## Settings

| Setting | Default | |
|---|---|---|
| `contextzip.copyToClipboard` | `true` | Put the ZIP on the clipboard after packaging. |
| `contextzip.watchDownloads` | `true` | Offer to apply AI replies that arrive in Downloads. Only the ZIP's file list and its `CONTEXTZIP.md` are read. |
| `contextzip.downloadsFolder` | | Watch a different folder. |
| `contextzip.path` | | Use a specific `contextzip` executable. |

## Requirements

None: the extension ships with the contextzip CLI for Windows x64, macOS (Apple Silicon and Intel) and Linux x64/arm64. On other platforms it uses `contextzip` from your PATH (`pip install contextzip`).

Works in VS Code 1.90+, Cursor, Windsurf and VSCodium. In a remote window (SSH, WSL, containers) the ZIP is made on the remote machine, so copy it from there.

## Privacy

Everything runs on your machine. The only network use is the optional task-based file picker (paths and sizes to Gemini) and fetching a GitHub repository when you ask for one. No telemetry.

[Documentation](https://www.contextzipcli.com) · [Source](https://github.com/akadeepesh/contextzip) · MIT licensed
