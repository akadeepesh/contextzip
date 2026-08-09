"""contextzip — intelligent codebase packager for AI tools."""

__version__ = "0.3.5"

from contextzip.api import (
    FileCollection,
    ContextzipError,
    NotARepositoryError,
    GitNotFoundError,
    GitCommandError,
    NoFilesError,
    get_git_changes,
    get_files,
    create_zip,
    detect_ecosystem,
)
from contextzip.packager import PackageResult

__all__ = [
    # Functions
    "get_git_changes",
    "get_files",
    "create_zip",
    "detect_ecosystem",
    # Data types
    "FileCollection",
    "PackageResult",
    # Exceptions
    "ContextzipError",
    "NotARepositoryError",
    "GitNotFoundError",
    "GitCommandError",
    "NoFilesError",
]
