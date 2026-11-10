"""
Universal exclusion rules applied to every project regardless of language.
"""

# Files that hold secrets. Excluded on every run like the rest of PATTERNS,
# but also used by redact.py: if a project's `always_include` deliberately
# brings one of these back, its contents are always redacted before zipping
# (whatever `limits.redact_secrets` says), and a binary one is skipped
# because it can't be redacted.
SECRET_PATTERNS = [
    # Secrets & env
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.pkcs12",
    "*.jks",
    "*.keystore",
    "*.ppk",
    # SSH private keys — no extension, so matched by exact basename.
    # Public counterparts (id_rsa.pub etc.) are intentionally NOT excluded.
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    # Credential files for common CLIs/package managers (auth tokens)
    ".npmrc",
    ".netrc",
    ".pypirc",
    ".pgpass",
    ".dockercfg",
    "docker/config.json",
    ".docker/config.json",
    # Cloud provider credential/config files
    ".aws/credentials",
    ".aws/config",
    "*serviceaccount*.json",
    "*service-account*.json",
    "*credentials*.json",
    "kubeconfig",
    "*.kubeconfig",
    # Infra-as-code state (Terraform state routinely contains plaintext
    # secrets — resource passwords, keys — even for "just infra" resources)
    "*.tfstate",
    "*.tfstate.*",
    ".terraform/",
]

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
    # Secrets & credentials — see SECRET_PATTERNS above
    *SECRET_PATTERNS,
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
    "*.tgz",
    "*.bz2",
    "*.xz",
    "*.7z",
    "*.webp",
    "*.avif",
    "*.bmp",
    "*.tiff",
    "*.mov",
    "*.webm",
    "*.wav",
    "*.woff",
    "*.woff2",
    "*.ttf",
    "*.otf",
    "*.eot",
    # contextzip workspace directory — always excluded regardless of .gitignore state
    ".contextzip/",
    "exports/",
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
