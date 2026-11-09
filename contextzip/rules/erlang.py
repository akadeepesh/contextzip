"""
Erlang (rebar3, erlang.mk) exclusion rules.
"""

PATTERNS = [
    # Build output, dependencies and lockfile
    "_build/",
    "_checkouts/",
    "ebin/",
    "deps/",
    "*.beam",
    "*.plt",
    "erl_crash.dump",
    "rebar.lock",
    ".rebar3/",
    ".rebar/",
]
