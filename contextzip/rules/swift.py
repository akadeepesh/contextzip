"""
Swift / Xcode / SwiftPM / CocoaPods exclusion rules.
"""

PATTERNS = [
    # Build output
    ".build/",
    "DerivedData/",
    "*.xcarchive/",
    "*.ipa",
    "*.dSYM/",
    "*.dSYM.zip",
    "*.hmap",
    # Dependencies
    "Pods/",
    "Carthage/Build/",
    "Carthage/Checkouts/",
    ".swiftpm/",
    # Lockfiles
    "Package.resolved",
    "Podfile.lock",
    "Cartfile.resolved",
    # Xcode's own files: per-user state, and the project file it rewrites itself
    "xcuserdata/",
    "*.xcuserstate",
    "*.xcscmblueprint",
    "*.pbxproj",
    "*.xcassets/",
    # Signing
    "*.mobileprovision",
    "*.cer",
    # fastlane output
    "fastlane/report.xml",
    "fastlane/Preview.html",
    "fastlane/screenshots/",
    "fastlane/test_output/",
]
