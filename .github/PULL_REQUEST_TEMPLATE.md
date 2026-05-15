## What does this PR do?

<!-- A clear one-line summary -->

## Type of change

- [ ] Bug fix
- [ ] New framework / language rule set
- [ ] New feature (flag, behaviour)
- [ ] Documentation update
- [ ] Chore (deps, tooling, refactor)

## Testing

<!-- How did you verify this works? -->

- [ ] Ran `contextzip --dry-run --verbose` on a real project
- [ ] Tested on the affected platform (Windows / macOS / Linux)
- [ ] If adding a new framework: tested against a real project of that type

## If adding a new framework rule set

- [ ] Created `contextzip/rules/yourlang.py` with a `PATTERNS` list
- [ ] Registered it in `filters.py` under `_RULE_REGISTRY`
- [ ] Added detection logic in `detector.py`
- [ ] Verified detection fires correctly (`contextzip --dry-run` shows the framework name)
- [ ] Verified the right directories are excluded

## Checklist

- [ ] My changes follow the existing code style
- [ ] I've added docstrings to any new public functions
- [ ] I've updated `CHANGELOG.md` under an `[Unreleased]` section
