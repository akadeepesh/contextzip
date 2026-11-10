"""
Lua / LuaRocks exclusion rules.
"""

PATTERNS = [
    # Installed rocks and bytecode
    "lua_modules/",
    ".luarocks/",
    "luarocks/",
    "*.rock",
    "*.luac",
]
