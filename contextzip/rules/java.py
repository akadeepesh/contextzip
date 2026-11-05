"""
Java / Kotlin (Maven, Gradle, Spring Boot) exclusion rules.

Kept: pom.xml, build.gradle(.kts), settings.gradle(.kts),
gradle.properties, src/**, application.properties / application.yml.
"""

PATTERNS = [
    # Build output
    "target/",  # Maven
    "build/",  # Gradle
    "out/",  # IntelliJ
    ".gradle/",  # Gradle's per-project cache
    ".kotlin/",  # Kotlin compiler session data
    "dependency-reduced-pom.xml",  # written by maven-shade-plugin
    # Compiled classes and packaged archives
    "*.class",
    "*.jar",
    "*.war",
    "*.ear",
    "*.nar",
    # Build wrappers: generated boilerplate that tells an AI nothing about the project
    "mvnw",
    "mvnw.cmd",
    "gradlew",
    "gradlew.bat",
    "**/.mvn/wrapper/",
    "**/gradle/wrapper/",
    # Lockfiles
    "gradle.lockfile",
    # Eclipse / Spring Tools / VS Code Java
    ".settings/",
    ".classpath",
    ".project",
    ".factorypath",
    ".apt_generated/",
    ".apt_generated_tests/",
    ".sts4-cache/",
    ".springBeans",
    # IntelliJ module files (.idea/ is already in base)
    "*.iml",
    "*.ipr",
    "*.iws",
    # JVM crash dumps
    "hs_err_pid*",
    "replay_pid*",
    # Spring Initializr's generic "getting started" links
    "HELP.md",
]
