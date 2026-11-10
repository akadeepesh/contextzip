"""
Haskell (Cabal, Stack) exclusion rules.
"""

PATTERNS = [
    # Build output
    "dist-newstyle/",
    ".stack-work/",
    "dist/",
    ".hpc/",
    # Compiler artifacts
    "*.hi",
    "*.o",
    "*.dyn_hi",
    "*.dyn_o",
    "*.prof",
    "*.hp",
    "*.eventlog",
    # Local config and lockfile
    "cabal.project.local",
    "cabal.project.local~",
    "stack.yaml.lock",
    ".ghc.environment.*",
]
