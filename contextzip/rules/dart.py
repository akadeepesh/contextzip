"""
Dart / Flutter exclusion rules.
"""

PATTERNS = [
    # Tooling and build output
    ".dart_tool/",
    "build/",
    ".packages",
    ".pub-cache/",
    ".pub/",
    ".fvm/",
    "coverage/",
    "doc/api/",
    # Lockfile
    "pubspec.lock",
    # Generated code
    "*.g.dart",
    "*.freezed.dart",
    "*.gr.dart",
    "*.mocks.dart",
    # Flutter plugin wiring
    ".flutter-plugins",
    ".flutter-plugins-dependencies",
    "GeneratedPluginRegistrant.*",
    "generated_plugin_registrant.*",
    "generated_plugins.cmake",
    "flutter_export_environment.sh",
    "Generated.xcconfig",
    # Platform folders
    "ios/Pods/",
    "ios/.symlinks/",
    "ios/Flutter/ephemeral/",
    "macos/Flutter/ephemeral/",
    "linux/flutter/ephemeral/",
    "windows/flutter/ephemeral/",
    "*.pbxproj",
    "*.xcassets/",
    "android/.gradle/",
    "android/gradlew",
    "android/gradlew.bat",
    "android/gradle/wrapper/",
    "*.iml",
    # Machine paths and signing keys
    "android/local.properties",
    "android/key.properties",
]
