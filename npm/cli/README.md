# @contextzip/cli

**Package exactly the right parts of your codebase for any AI tool, in one command.**

contextzip detects your stack, leaves out dependencies, build output and secrets, and puts a lean ZIP of the right files on your clipboard, ready to paste into Claude, ChatGPT or any upload box. When the AI sends changed files back, `cz apply-zip` writes them into the right folders and merges around edits you've made since.

```bash
npx @contextzip/cli                 # try it without installing
npm install -g @contextzip/cli      # then: cz
```

This package installs a standalone build, so you don't need Python. It's the same tool as `pip install contextzip`.

```bash
cz                                  # package the project, ZIP on your clipboard
cz --git-changes                    # only what you've changed
cz github.com/user/repo             # a GitHub repo, without cloning it
cz apply-zip ~/Downloads/reply.zip  # write the AI's changes back
```

Prebuilt for Windows x64, macOS (Apple Silicon and Intel) and Linux x64/arm64 (glibc 2.35+). On anything else, use `pip install contextzip`.

- Documentation: https://www.contextzipcli.com
- Source and full README: https://github.com/akadeepesh/contextzip
- MIT licensed
