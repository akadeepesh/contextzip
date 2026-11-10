"""
R exclusion rules.
"""

PATTERNS = [
    # Session files
    ".Rproj.user/",
    ".Rhistory",
    ".RData",
    ".Ruserdata",
    # Package libraries and lockfile
    "renv/library/",
    "renv/staging/",
    "renv/local/",
    "renv.lock",
    "packrat/lib*/",
    "packrat/src/",
    # Build and render output
    "*.Rcheck/",
    "*_cache/",
    "rsconnect/",
]
