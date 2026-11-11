"""
remote_names.py — tell a repository reference from a path or a command.

Split out of remote.py, with no imports, so `contextzip`'s argument
parser can recognise `contextzip github.com/user/repo` without loading
the code that downloads repositories.
"""

from __future__ import annotations

_SHORTHANDS = {"gh": "github.com", "gl": "gitlab.com", "bb": "bitbucket.org"}
_OTHER_HOSTS = ("codeberg.org",)


def looks_like_remote(text: str) -> bool:
    """True if *text* is meant as a repository URL rather than a local path or command."""
    t = text.strip().lower()
    if t.startswith(("git@", "http://", "https://")) or any(t.startswith(f"{s}:") for s in _SHORTHANDS):
        return True
    if t.startswith("www."):
        t = t[len("www."):]
    return t.startswith(tuple(f"{h}/" for h in (*_SHORTHANDS.values(), *_OTHER_HOSTS)))
