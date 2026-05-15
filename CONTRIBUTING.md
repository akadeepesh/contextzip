# Contributing to contextzip

Thanks for taking the time to contribute. This document covers how to get set up, what kinds of contributions are most useful, and how to submit them.

---

## What we're looking for

- **New framework rule sets** — if your stack isn't detected or the exclusions aren't right, a new `rules/` file is the most useful contribution
- **Bug fixes** — especially edge cases around symlinks, path handling, and platform-specific clipboard behaviour
- **Platform improvements** — clipboard integration on Linux/Windows can always be improved
- **Documentation fixes** — typos, unclear instructions, outdated examples

Please **open an issue before starting a large PR** so we can discuss the approach first. Small fixes (typos, obvious bugs) can go straight to a PR.

---

## Setting up locally

```bash
git clone https://github.com/akadeepesh/contextzip
cd contextzip

python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -e .
```

Verify it works:

```bash
contextzip --version
contextzip --dry-run   # run from any project directory
```

---

## Adding a new framework

1. Create `contextzip/rules/yourlang.py`:

```python
"""
YourLang exclusion rules.
"""

PATTERNS = [
    "build/",
    "*.compiled",
    ".cache/",
]
```

2. Register it in `contextzip/filters.py` under `_RULE_REGISTRY`:

```python
_RULE_REGISTRY: dict[str, str] = {
    ...
    "yourlang": "contextzip.rules.yourlang",
}
```

3. Add a detection rule in `contextzip/detector.py`:

```python
_Rule(
    name="YourLang",
    module="yourlang",
    check=lambda p: _file_exists(p, "yourlang.config"),
    weight=3,
),
```

4. Test it:

```bash
mkdir /tmp/test-yourlang && touch /tmp/test-yourlang/yourlang.config
cd /tmp/test-yourlang && contextzip --dry-run --verbose
```

---

## Submitting a PR

1. Fork the repo and create a branch: `git checkout -b fix/your-description`
2. Make your changes
3. Test manually — run `contextzip --dry-run --verbose` on a real project
4. Commit with a clear message: `git commit -m "add: Elixir/Mix rule set"`
5. Push and open a PR against `main`

PR titles should follow the format:
- `add: <thing>` — new feature or rule set
- `fix: <thing>` — bug fix
- `docs: <thing>` — documentation only
- `chore: <thing>` — maintenance, deps, tooling

---

## Code style

- Follow the existing patterns in the codebase — each module has a clear single responsibility
- Type hints on all public functions
- Docstrings on all modules and public functions
- Keep rule files simple — just a `PATTERNS` list with comments explaining non-obvious exclusions
