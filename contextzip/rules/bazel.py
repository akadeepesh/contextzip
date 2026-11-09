"""
Bazel exclusion rules.
"""

PATTERNS = [
    # Output symlinks and lockfile
    "bazel-*/",
    "bazel-*",
    "MODULE.bazel.lock",
]
