"""
Scala (sbt, Mill, scala-cli) exclusion rules.
"""

PATTERNS = [
    # Build output
    "target/",
    "project/target/",
    "project/project/",
    "out/",
    "*.class",
    "*.jar",
    # Build servers and editors
    ".bsp/",
    ".metals/",
    ".bloop/",
    ".scala-build/",
    "metals.sbt",
    ".ammonite/",
    ".worksheet/",
]
