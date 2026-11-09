"""
PHP / Composer exclusion rules.
"""

PATTERNS = [
    # Composer
    "vendor/",
    "composer.lock",
    "*.phar",
    # Tool caches
    ".phpunit.result.cache",
    ".phpunit.cache/",
    ".php-cs-fixer.cache",
    ".phpstan.cache/",
    ".psalm/",
    ".phpdoc/",
    # IDE helper stubs
    "_ide_helper*.php",
    ".phpstorm.meta.php",
]
