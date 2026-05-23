"""
Python, Django, FastAPI, and general Python project exclusion rules.
"""

PATTERNS = [
    "__pycache__/",
    "*.pyc",
    "*.pyo",
    "*.pyd",
    ".Python",
    ".venv/",
    "venv/",
    "env/",
    ".env/",
    "ENV/",
    "*.egg-info/",
    "*.egg",
    "dist/",
    "build/",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    ".hypothesis/",
    "htmlcov/",
    "coverage.xml",
    ".coverage",
    "*.sqlite3",
    "*.db",
    "site/",  # mkdocs build output
    ".tox/",
    "poetry.lock",
    "uv.lock",
    "migrations/",  # Django migrations (auto-generated)
    "staticfiles/",  # Django collected static
    "media/",  # Django uploaded media
]
