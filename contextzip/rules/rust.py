"""
Rust (Cargo) exclusion rules.
"""

PATTERNS = [
    "target/",
    "Cargo.lock",       # exclude for libraries; keep for binaries — we exclude for AI context
    "*.rlib",
    "*.rmeta",
]
