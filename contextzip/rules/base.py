"""
Universal exclusion rules applied to every project regardless of language.
"""

PATTERNS = [
    # Version control
    ".git/",
    ".svn/",
    ".hg/",

    # OS artifacts
    ".DS_Store",
    "Thumbs.db",
    "desktop.ini",

    # Editor & IDE
    ".vscode/",
    ".idea/",
    "*.swp",
    "*.swo",
    "*.swn",
    ".vim/",

    # Logs
    "*.log",
    "logs/",
    "log/",

    # Temp & cache
    ".cache/",
    "tmp/",
    "temp/",
    "*.tmp",
    "*.bak",
    "*.orig",

    # Secrets & env
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",

    # Binaries & media (rarely useful for AI context)
    "*.exe",
    "*.dll",
    "*.so",
    "*.dylib",
    "*.bin",
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.svg",
    "*.ico",
    "*.mp4",
    "*.mp3",
    "*.zip",
    "*.tar",
    "*.gz",
    "*.rar",

    # contextzip workspace directory — always excluded regardless of .gitignore state
    ".contextzip/",

    # GitHub / repo governance (not useful as AI context)
    "CHANGELOG.md",
    "CHANGELOG",
    "CONTRIBUTING.md",
    "CONTRIBUTING",
    "LICENSE",
    "LICENSE.md",
    "LICENSE.txt",
    "SECURITY.md",
    "CODE_OF_CONDUCT.md",
    ".github/ISSUE_TEMPLATE/",
    ".github/PULL_REQUEST_TEMPLATE.md",
    ".github/PULL_REQUEST_TEMPLATE/",
]