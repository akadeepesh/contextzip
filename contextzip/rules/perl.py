"""
Perl exclusion rules.
"""

PATTERNS = [
    # Build output and local libraries
    "blib/",
    "_build/",
    "local/",
    "cover_db/",
    "nytprof/",
    "nytprof.out",
    "pm_to_blib",
    "MYMETA.*",
    "Makefile.old",
    "*.bs",
    "cpanfile.snapshot",
]
