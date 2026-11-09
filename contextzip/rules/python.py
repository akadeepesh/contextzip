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
    # More environment and build tooling
    ".eggs/",
    ".nox/",
    ".pytype/",
    ".pyre/",
    "__pypackages__/",
    ".pdm-build/",
    ".pdm-python",
    "pdm.lock",
    "Pipfile.lock",
    # Notebooks and experiment tracking
    ".ipynb_checkpoints/",
    "wandb/",
    "mlruns/",
    "lightning_logs/",
    # Model weights and array dumps
    "*.pt",
    "*.pth",
    "*.ckpt",
    "*.safetensors",
    "*.onnx",
    "*.h5",
    "*.hdf5",
    "*.npy",
    "*.npz",
    "*.pkl",
    "*.pickle",
    "*.parquet",
    "*.feather",
]
