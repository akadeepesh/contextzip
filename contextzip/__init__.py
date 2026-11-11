"""contextzip — intelligent codebase packager for AI tools."""

__version__ = "0.9.0"

__all__ = [
    # Functions
    "get_git_changes",
    "get_files",
    "create_zip",
    "apply_zip",
    "detect_ecosystem",
    "fetch_remote",
    # Data types
    "FileCollection",
    "PackageResult",
    "ApplyResult",
    # Exceptions
    "ContextzipError",
    "NotARepositoryError",
    "GitNotFoundError",
    "GitCommandError",
    "NoFilesError",
    "ZipNotFoundError",
    "RemoteFetchError",
]

# The public API is imported on first use (PEP 562), so `import contextzip`
# and the CLI's quick commands (`cz --version`, `cz --help`) don't load the
# packager, the applier and everything they import.
_LAZY = {
    "PackageResult": "contextzip.packager",
    "ApplyResult": "contextzip.applier",
}


def __getattr__(name: str):
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(__import__(_LAZY.get(name, "contextzip.api"), fromlist=[name]), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
