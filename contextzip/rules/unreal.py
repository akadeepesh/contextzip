"""
Unreal Engine exclusion rules.
"""

PATTERNS = [
    # Build output and caches
    "Binaries/",
    "DerivedDataCache/",
    "Intermediate/",
    "Saved/",
    "Build/",
    # Binary assets
    "*.uasset",
    "*.umap",
    # Project files the engine regenerates
    "*.sln",
    "*.xcodeproj/",
    ".vs/",
    "*.VC.db",
    "*.opensdf",
    "*.sdf",
    "*.suo",
]
