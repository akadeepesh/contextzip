"""
Godot exclusion rules.
"""

PATTERNS = [
    # Editor cache and imported assets
    ".godot/",
    ".import/",
    "*.import",
    "*.uid",
    "*.translation",
    # C# build output
    ".mono/",
    "data_*/",
    "mono_crash.*.json",
    # Export presets can hold keystore passwords
    "export_presets.cfg",
]
