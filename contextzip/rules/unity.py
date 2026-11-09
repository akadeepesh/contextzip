"""
Unity exclusion rules. Scenes, prefabs and .meta files are machine-written
YAML that swamps the scripts an AI is asked about, so they stay out.
"""

PATTERNS = [
    # Editor caches and build output
    "Library/",
    "Temp/",
    "Obj/",
    "Build/",
    "Builds/",
    "Logs/",
    "UserSettings/",
    "MemoryCaptures/",
    "Recordings/",
    # Asset metadata and serialized assets
    "*.meta",
    "*.unity",
    "*.prefab",
    "*.asset",
    "*.mat",
    "*.anim",
    "*.controller",
    "*.unitypackage",
    # Project files Unity regenerates
    "*.csproj",
    "*.sln",
    "Packages/packages-lock.json",
]
