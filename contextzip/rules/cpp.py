"""
C / C++ (CMake, Meson, Autotools, MSBuild, Conan, vcpkg) exclusion rules.
"""

PATTERNS = [
    # Build folders
    "build/",
    "cmake-build-*/",
    "builddir/",
    "Debug/",
    "Release/",
    "x64/",
    "x86/",
    "out/",
    # CMake
    "CMakeFiles/",
    "CMakeCache.txt",
    "cmake_install.cmake",
    "CMakeUserPresets.json",
    "compile_commands.json",
    "_deps/",
    "Testing/",
    "install_manifest.txt",
    # Meson / Ninja
    "meson-logs/",
    "meson-private/",
    "subprojects/packagecache/",
    ".ninja_deps",
    ".ninja_log",
    # Autotools
    "autom4te.cache/",
    "config.status",
    "config.log",
    ".deps/",
    ".libs/",
    "*.lo",
    "*.la",
    # Objects and libraries
    "*.o",
    "*.obj",
    "*.a",
    "*.lib",
    "*.out",
    "*.gch",
    "*.pch",
    "*.pdb",
    "*.ilk",
    "*.exp",
    # Package managers
    "vcpkg_installed/",
    "conanbuildinfo.*",
    "conaninfo.txt",
    "conan.lock",
    # Editors and language servers
    ".vs/",
    "*.vcxproj.user",
    ".ccls-cache/",
    ".clangd/",
]
