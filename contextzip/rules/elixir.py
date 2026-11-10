"""
Elixir / Mix / Phoenix exclusion rules.
"""

PATTERNS = [
    # Build output and dependencies
    "_build/",
    "deps/",
    "cover/",
    "doc/",
    ".fetch",
    "*.ez",
    "*.beam",
    "erl_crash.dump",
    # Lockfile
    "mix.lock",
    # Language servers and Dialyzer
    ".elixir_ls/",
    ".lexical/",
    "priv/plts/",
    "*.plt",
    # Phoenix digested assets
    "priv/static/assets/",
    "priv/static/cache_manifest.json",
    "assets/node_modules/",
]
