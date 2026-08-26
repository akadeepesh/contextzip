# contextzip

> Package exactly the right parts of your codebase and paste it into any AI tool — in one command.

```bash
pip install contextzip
```

**📖 Full docs, every flag, every config option: [contextzip.vercel.app](https://contextzip.vercel.app)**

---

## Why contextzip

Every AI session starts the same way: hunt down the relevant files, skip `node_modules` and build artifacts by hand, zip it, find the zip, upload it — then do it all again next session.

contextzip eliminates that. Run it from your project root — it detects your stack, applies smart exclusions, and produces a lean ZIP ready to paste into Claude, ChatGPT, or any AI tool. When the AI hands changes back, `contextzip apply-zip` writes them into your project safely.

## Quick start

```bash
cd ~/projects/my-app
contextzip
```

That's it — no flags required. contextzip detects your framework, excludes the noise, and writes a ZIP to `.contextzip/`.

## The essentials

| Command | What it does |
|---|---|
| `contextzip` | Detect stack, exclude noise, package the project |
| `contextzip --prompt "task"` | Let Gemini pick only the files relevant to a task |
| `contextzip --git-changes` | Package only modified/staged/untracked files |
| `contextzip apply-zip` | Write an AI-returned ZIP back into your project, safely |
| `contextzip watch -- npm run dev` | Auto-package debug context the moment an error appears |
| `contextzip config --ui` | Set include/exclude rules visually in a local browser tab |

Every command supports `--dry-run`, `--verbose`, `--include`, `--exclude`, and more — see the [full CLI reference](https://contextzip.vercel.app/cli).

contextzip respects your `.gitignore`, detects Node.js, Next.js, Python, Django, FastAPI, Rust, Go, and Ruby (including in monorepos), and never packages secrets — SSH keys, cloud credentials, and Terraform state are always excluded. Details: [contextzip.vercel.app/features](https://contextzip.vercel.app/features).

## Also a Python library

```python
from contextzip import get_git_changes, create_zip

collection = get_git_changes()
pkg = create_zip(collection, output="/tmp/changes.zip")
```

Full function reference: [contextzip.vercel.app/cli#python-api](https://contextzip.vercel.app/cli#python-api)

## Learn more

- [Packaging & detection](https://contextzip.vercel.app/features) — frameworks, exclusion rules, safety
- [AI-powered file selection](https://contextzip.vercel.app/ai-selection) — `--prompt`, Gemini setup
- [Terminal error watcher](https://contextzip.vercel.app/watch) — `contextzip watch`
- [Applying changes back](https://contextzip.vercel.app/apply-zip) — `apply-zip`, backups, safety
- [Configuration](https://contextzip.vercel.app/configuration) — personal & project config, visual config UI
- [CLI & Python API reference](https://contextzip.vercel.app/cli)

## Contributing

Contributions are welcome — especially new framework rule sets and edge-case fixes. See [CONTRIBUTING.md](https://github.com/akadeepesh/contextzip/blob/main/CONTRIBUTING.md). Please open an issue before starting a large PR.

## License

MIT — see [LICENSE](https://github.com/akadeepesh/contextzip/blob/main/LICENSE) for details.
