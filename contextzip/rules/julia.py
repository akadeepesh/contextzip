"""
Julia exclusion rules.
"""

PATTERNS = [
    # Resolved manifest
    "Manifest.toml",
    # Coverage and allocation traces
    "*.jl.cov",
    "*.jl.*.cov",
    "*.jl.mem",
    # Docs and dependency builds
    "docs/build/",
    "docs/site/",
    "deps/deps.jl",
    "deps/build.log",
    "deps/downloads/",
    "deps/usr/",
    "deps/src/",
]
