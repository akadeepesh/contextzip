"""
Ruby / Rails / Bundler exclusion rules.
"""

PATTERNS = [
    # Bundler
    "vendor/bundle/",
    ".bundle/",
    "Gemfile.lock",

    # Ruby version managers
    ".ruby-version",
    ".ruby-gemset",
    ".rvmrc",

    # Built gems
    "*.gem",
    "pkg/",

    # Rails-specific
    "tmp/",           # runtime temp (also in base, fine to repeat)
    "log/",           # request logs (also in base)
    "public/assets/", # precompiled assets (sprockets / propshaft)
    "public/packs/",  # webpacker output
    "storage/",       # ActiveStorage blobs
    ".byebug_history",

    # Test coverage
    "coverage/",

    # Documentation
    "doc/",
    "rdoc/",

    # Spring (Rails app preloader)
    ".spring/",

    # Compiled C extensions
    "*.bundle",
    "*.so",           # also in base, explicit here for clarity
]