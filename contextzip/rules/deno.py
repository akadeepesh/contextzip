"""
Deno exclusion rules.
"""

PATTERNS = [
    # Lockfile, vendored modules and output
    "deno.lock",
    "vendor/",
    "node_modules/",
    ".deno/",
    "dist/",
    "_fresh/",
    "coverage/",
    "cov_profile/",
]
