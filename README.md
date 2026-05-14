# contextzip

> Intelligently package your codebase for AI tools — no noise, just signal.

## Install

```bash
pip install -e .
# or once published:
pip install contextzip
```

## Usage

```bash
# Run from your project root
contextzip

# Preview what would be included (no ZIP created)
contextzip --dry-run

# See every file decision
contextzip --dry-run --verbose

# Only include specific folders
contextzip --include src app routes

# Add extra exclusions on top of auto-rules
contextzip --exclude "*.log" "*.sqlite" temp.js

# Save ZIP to a specific path
contextzip --output ~/Desktop/myproject.zip

# Create ZIP without clipboard copy
contextzip --no-clipboard
```

## What gets auto-excluded

### Always (base rules)
`.git/`, `.env`, `*.log`, editor files, OS artifacts, binaries, media

### Node.js / Next.js
`node_modules/`, `.next/`, `dist/`, `package-lock.json`, `yarn.lock`

### Python
`__pycache__/`, `.venv/`, `.pytest_cache/`, `*.pyc`, `migrations/`

### Rust
`target/`, `Cargo.lock`

### Go
`vendor/`, `go.sum`

## Status

| Phase | Feature | Status |
|-------|---------|--------|
| 1+2 | Scaffold + Detection | ✅ Done |
| 3 | ZIP creation | 🔜 Next |
| 4 | Clipboard integration | 🔜 Planned |
| 5 | Rich output polish | 🔜 Planned |
