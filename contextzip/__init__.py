"""contextzip — intelligent codebase packager for AI tools."""

__version__ = "0.5.2"

from contextzip.api import (
    FileCollection,
    ContextzipError,
    NotARepositoryError,
    GitNotFoundError,
    GitCommandError,
    NoFilesError,
    ZipNotFoundError,
    RemoteFetchError,
    get_git_changes,
    get_files,
    create_zip,
    apply_zip,
    detect_ecosystem,
    fetch_remote,
)
from contextzip.packager import PackageResult
from contextzip.applier import ApplyResult

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
