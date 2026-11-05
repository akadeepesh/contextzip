"""
rules/errors/java.py — Error detection for Java, Kotlin and Spring Boot.

Covers JVM stack traces (with their "Caused by:" chains), Spring Boot's
"APPLICATION FAILED TO START" report, compile errors from javac, kotlinc,
Maven and Gradle, and Maven / Gradle build and test failures.

Besides the three pattern lists every errors module has, this module
defines four hooks that error_parser calls when a module has them:

  refine_start(lines, idx) -> int | None
      Move a block start up to where the error really begins. Bottom-up
      scanning lands on the *last* "Caused by:" or on Maven's closing
      "[ERROR] -> [Help 1]"; the useful part starts above that.
  error_type(block_lines) -> str | None
      A short label, e.g. "UnsatisfiedDependencyException (caused by
      NoSuchBeanDefinitionException)".
  extract_paths(line) -> list[str]
      Source paths a line names that one regex can't build. A stack frame
      gives the class's package and the file name, so
      `at com.acme.UserService.find(UserService.java:42)` becomes
      com/acme/UserService.java, which is matched against the project's
      files by suffix.
  clean_block(text) -> str
      Collapse long runs of framework frames (Tomcat, Spring, the JDK) so
      the application's own frames stand out.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------

# A fully qualified exception class: java.lang.IllegalStateException,
# org.opentest4j.AssertionFailedError, com.acme.UserNotFoundException.
_EXC = r"(?:[A-Za-z_$][\w$]*\.)+[A-Z][\w$]*(?:Exception|Error|Throwable|Failure)"

_EXC_HEADER_RE = re.compile(rf'^(?:Exception in thread "[^"]*"\s+)?({_EXC})(?::|\s*$)')
_CAUSED_RE = re.compile(rf"^\s*(?:Caused by|Suppressed):\s+({_EXC})")
_FRAME_RE = re.compile(r"^\s+at\s+\S")
_MORE_RE = re.compile(r"^\s+\.\.\.\s+\d+\s+(?:more|common frames omitted)")

_SPRING_FAILED_RE = re.compile(r"^APPLICATION FAILED TO START\s*$")
_STARS_RE = re.compile(r"^\*{10,}\s*$")
# Spring Boot's default log line:  2026-11-05T10:00:00.123+05:30 ERROR 4242 --- [demo] [main] o.s.b.SpringApplication : ...
_SPRING_ERROR_LOG_RE = re.compile(r"^\d{4}-\d\d-\d\d[T ][\d:.,]+\S*\s+ERROR\s+\d+\s+---")
# Logback / log4j pattern:  10:00:00.123 [main] ERROR com.acme.Foo - ...
_LOGBACK_ERROR_RE = re.compile(r"^[\d:.,\-T ]+\s+\[[^\]]+\]\s+ERROR\s+\S+\s+-\s")

# Windows paths in build output sometimes carry a leading slash: /D:/proj/...
_P = r"(?:/(?=[A-Za-z]:))?((?:[A-Za-z]:)?[^:\s\[\]]+\.(?:java|kt|kts|groovy|scala))"
_JAVAC_ERR_RE = re.compile(rf"^{_P}:\d+: error:")
_KOTLIN_ERR_RE = re.compile(rf"^e:\s+(?:file://)?{_P}:\d+")
_MAVEN_COMPILE_ERR_RE = re.compile(rf"^\[ERROR\]\s+{_P}:\[\d+")
_MAVEN_COMPILE_HDR_RE = re.compile(r"^\[ERROR\]\s+COMPILATION ERROR")
_GRADLE_TASK_FAILED_RE = re.compile(r"^> Task \S+ FAILED")
_GRADLE_TEST_FAILED_RE = re.compile(r"^[A-Za-z_$][\w$.]* > .+ FAILED\s*$")

# Build tools' closing summaries. On their own they say little; the real
# error is usually printed just above them.
_MAVEN_ERR_RE = re.compile(r"^\[ERROR\]")
_GRADLE_SUMMARY_RE = re.compile(
    r"^(?:FAILURE: Build failed|\* What went wrong:|BUILD FAILED|Execution failed for task)"
)

# A new build or app run starting — an error above one of these belongs to
# an earlier run.
_RUN_BOUNDARY_RE = re.compile(
    r"^(?:\[INFO\] Scanning for projects|\[INFO\] BUILD SUCCESS|BUILD SUCCESSFUL|"
    r"Starting a Gradle Daemon|> Configure project)"
)

_LOOKBACK = 300

_COMPILE_ERR_RES = (_JAVAC_ERR_RE, _KOTLIN_ERR_RE, _MAVEN_COMPILE_ERR_RE)
_STRONG_RES = (
    _EXC_HEADER_RE,
    _CAUSED_RE,
    _SPRING_FAILED_RE,
    _MAVEN_COMPILE_HDR_RE,
    _GRADLE_TASK_FAILED_RE,
    _GRADLE_TEST_FAILED_RE,
    *_COMPILE_ERR_RES,
)

# Classes that belong to the JDK, Kotlin or common libraries rather than
# the application. Their frames are collapsed and never looked up as files.
_FRAMEWORK_PREFIXES = (
    "java.", "javax.", "jakarta.", "jdk.", "sun.", "com.sun.", "kotlin.", "kotlinx.",
    "org.springframework.", "org.apache.", "org.hibernate.", "org.eclipse.", "org.junit.",
    "junit.", "org.gradle.", "worker.org.gradle.", "org.mockito.", "org.assertj.",
    "org.opentest4j.", "org.slf4j.", "ch.qos.logback.", "com.fasterxml.", "org.aspectj.",
    "io.netty.", "reactor.", "io.undertow.", "com.zaxxer.", "net.bytebuddy.", "io.micrometer.",
    "org.h2.", "com.mysql.", "org.postgresql.", "org.thymeleaf.", "org.yaml.", "feign.",
    "io.grpc.", "com.google.", "org.codehaus.", "groovy.", "scala.",
)

# ---------------------------------------------------------------------------
# Pattern lists (same contract as the other errors modules)
# ---------------------------------------------------------------------------

ERROR_START_PATTERNS: list[re.Pattern] = [
    *_STRONG_RES,
    _SPRING_ERROR_LOG_RE,
    _LOGBACK_ERROR_RE,
    _MAVEN_ERR_RE,
    _GRADLE_SUMMARY_RE,
]

# Each has exactly one capture group yielding a path.
PATH_PATTERNS: list[re.Pattern] = [
    _JAVAC_ERR_RE,
    _KOTLIN_ERR_RE,
    _MAVEN_COMPILE_ERR_RE,
    # Gradle test summary:  org.opentest4j.AssertionFailedError at UserServiceTest.java:23
    re.compile(r"\bat\s+([\w$-]+\.(?:java|kt|groovy|scala)):\d+\s*$"),
]

NOISE_PATTERNS: list[re.Pattern] = [
    # Spring Boot / logback INFO, DEBUG and TRACE lines caught inside a block
    re.compile(r"^\d{4}-\d\d-\d\d[T ][\d:.,]+\S*\s+(?:INFO|DEBUG|TRACE)\s+\d+\s+---"),
    re.compile(r"^[\d:.,\-T ]+\s+\[[^\]]+\]\s+(?:INFO|DEBUG|TRACE)\s+\S+\s+-\s"),
    # Maven chatter
    re.compile(r"^\[INFO\]\s*-*\s*$"),
    re.compile(r"^\[INFO\] (?:Total time|Finished at|Download(?:ing|ed) from)"),
    re.compile(r"^Download(?:ing|ed) from"),
    re.compile(r"^\[ERROR\]\s*$"),
    re.compile(r"^\[ERROR\] (?:-> \[Help 1\]|\[Help 1\]|Re-run Maven|To see the full stack trace|For more information about)"),
    # Gradle chatter
    re.compile(r"^\* Try:"),
    re.compile(r"^> (?:Run with --|Get more help at)"),
    re.compile(r"^\d+ actionable tasks?:"),
    re.compile(r"^Deprecated Gradle features were used"),
    re.compile(r"^You can use '--warning-mode"),
    re.compile(r"^For more on this, please refer to"),
]

# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


def _any(line: str, res) -> bool:
    return any(r.search(line) for r in res)


def _is_trace_line(line: str) -> bool:
    return bool(
        _FRAME_RE.match(line)
        or _MORE_RE.match(line)
        or _CAUSED_RE.match(line)
        or _EXC_HEADER_RE.match(line)
    )


def _is_weak(line: str) -> bool:
    return bool(_MAVEN_ERR_RE.match(line) or _GRADLE_SUMMARY_RE.match(line)) and not _any(
        line, _STRONG_RES
    )


def _trace_top(lines: list[str], idx: int) -> int:
    """Walk up a stack trace (and any "Caused by:" chain) to its first line."""
    top = idx
    while True:
        while top > 0 and _is_trace_line(lines[top - 1]):
            top -= 1
        # An exception message can wrap over a few lines; look past them for
        # the header they belong to — but never past a blank line, a frame
        # or a log line, which would mean an earlier, separate error.
        jumped = False
        for j in range(top - 1, max(top - 8, -1), -1):
            prev = lines[j]
            if _EXC_HEADER_RE.match(prev) or _CAUSED_RE.match(prev):
                top, jumped = j, True
                break
            if (
                not prev.strip()
                or _FRAME_RE.match(prev)
                or _MORE_RE.match(prev)
                or _SPRING_ERROR_LOG_RE.match(prev)
                or _LOGBACK_ERROR_RE.match(prev)
                or prev.startswith("[")
            ):
                break
        if not jumped:
            break
    # Keep the log line that announced the exception.
    if top > 0 and (_SPRING_ERROR_LOG_RE.match(lines[top - 1]) or _LOGBACK_ERROR_RE.match(lines[top - 1])):
        top -= 1
    return top


def _compile_top(lines: list[str], idx: int) -> int:
    """First line of a run of compile errors: the failed task's header, or the earliest error."""
    top = idx
    for j in range(idx - 1, max(idx - _LOOKBACK, -1), -1):
        line = lines[j]
        if _RUN_BOUNDARY_RE.match(line):
            break
        if _GRADLE_TASK_FAILED_RE.match(line) or _MAVEN_COMPILE_HDR_RE.match(line):
            return j
        if _any(line, _COMPILE_ERR_RES):
            top = j
    return top


def refine_start(lines: list[str], idx: int) -> int | None:
    """See module docstring. Returns None when *lines[idx]* isn't JVM output."""
    line = lines[idx]

    if _is_weak(line):
        # Prefer the real error printed above the build tool's summary.
        for j in range(idx - 1, max(idx - _LOOKBACK, -1), -1):
            if _RUN_BOUNDARY_RE.match(lines[j]):
                break
            if _any(lines[j], _STRONG_RES) and not _FRAME_RE.match(lines[j]):
                return refine_start(lines, j)
        # Nothing better: start at the top of the summary itself.
        if _GRADLE_SUMMARY_RE.match(line):
            for j in range(idx, max(idx - 40, -1), -1):
                if lines[j].startswith("FAILURE: Build failed"):
                    return j
            return idx
        top = idx
        while top > 0 and _MAVEN_ERR_RE.match(lines[top - 1]):
            top -= 1
        return top

    if _SPRING_FAILED_RE.match(line):
        top = idx - 1 if idx > 0 and _STARS_RE.match(lines[idx - 1]) else idx
        for j in range(top - 1, max(top - 3, -1), -1):
            if _SPRING_ERROR_LOG_RE.match(lines[j]) or _LOGBACK_ERROR_RE.match(lines[j]):
                return j
        return top

    if _any(line, _COMPILE_ERR_RES) or _MAVEN_COMPILE_HDR_RE.match(line):
        return _compile_top(lines, idx)

    if _CAUSED_RE.match(line) or line.startswith("Exception in thread"):
        return _trace_top(lines, idx)
    if _EXC_HEADER_RE.match(line):
        # Python prints dotted names too (requests.exceptions.ConnectionError);
        # only claim the line when JVM frames follow it.
        for j in range(idx + 1, min(idx + 9, len(lines))):
            if _FRAME_RE.match(lines[j]) or _MORE_RE.match(lines[j]) or _CAUSED_RE.match(lines[j]):
                return _trace_top(lines, idx)
            if not lines[j].strip() or lines[j].lstrip().startswith("File "):
                break
        return None

    if _SPRING_ERROR_LOG_RE.match(line) or _LOGBACK_ERROR_RE.match(line):
        return idx

    if _GRADLE_TASK_FAILED_RE.match(line) or _GRADLE_TEST_FAILED_RE.match(line):
        return idx

    return None


def _short(name: str) -> str:
    return name.rsplit(".", 1)[-1]


def error_type(block_lines: list[str]) -> str | None:
    """See module docstring. Returns None when the block isn't JVM output."""
    top: str | None = None
    root: str | None = None
    for line in block_lines:
        m = _CAUSED_RE.match(line)
        if m:
            if not line[:1].isspace():  # "Suppressed:" is indented; only real causes count
                root = m.group(1)
            continue
        m = _EXC_HEADER_RE.match(line)
        if m and top is None:
            top = m.group(1)
    is_jvm = root is not None or any(
        _FRAME_PARTS_RE.match(l) or l.startswith("Exception in thread") for l in block_lines
    )
    if top and is_jvm:
        if root and _short(root) != _short(top):
            return f"{_short(top)} (caused by {_short(root)})"
        return _short(top)
    if root:
        return _short(root)
    if any(_SPRING_FAILED_RE.match(l) for l in block_lines):
        return "Application failed to start"
    if any(_any(l, _COMPILE_ERR_RES) or _MAVEN_COMPILE_HDR_RE.match(l) for l in block_lines):
        return "Compilation error"
    if any(_GRADLE_TEST_FAILED_RE.match(l) for l in block_lines) or any(
        re.match(r"^\[ERROR\] (?:Tests run:|Failures:)", l) for l in block_lines
    ):
        return "Test failure"
    if any(re.search(r"Failed to execute goal|BUILD FAILURE", l) or _GRADLE_SUMMARY_RE.match(l) for l in block_lines):
        return "Build failure"
    return None


_FRAME_PARTS_RE = re.compile(
    r"^\s*at\s+(?:\S*/)?(?P<cls>[\w$.]+)\.[\w$<>-]+\((?P<file>[\w$-]+\.(?:java|kt|groovy|scala)):\d+\)"
)
_FQN_RE = re.compile(r"\b((?:[a-z_][\w]*\.){2,}[A-Z][\w$]*)")


def _is_framework(cls: str) -> bool:
    return cls.startswith(_FRAMEWORK_PREFIXES)


def _package_dir(cls: str) -> str:
    parts = cls.split(".")[:-1]
    return "/".join(parts) + "/" if parts else ""


def extract_paths(line: str) -> list[str]:
    """See module docstring."""
    m = _FRAME_PARTS_RE.match(line)
    if m:
        cls = m.group("cls")
        if _is_framework(cls):
            return []
        return [_package_dir(cls) + m.group("file")]
    if _FRAME_RE.match(line):
        return []
    # Class names in messages: "Parameter 0 of constructor in com.acme.UserService
    # required a bean of type 'com.acme.UserRepository'". Could be Java or Kotlin.
    out: list[str] = []
    for fqn in _FQN_RE.findall(line):
        fqn = fqn.split("$", 1)[0]
        if _is_framework(fqn):
            continue
        base = fqn.replace(".", "/")
        out += [base + ".java", base + ".kt"]
    return out


def _frame_class(line: str) -> str | None:
    m = re.match(r"^\s*at\s+(?:\S*/)?([\w$.]+)\.", line)
    return m.group(1) if m else None


def clean_block(text: str) -> str:
    """See module docstring."""
    out: list[str] = []
    run: list[str] = []

    def flush() -> None:
        if len(run) > 2:
            indent = run[0][: len(run[0]) - len(run[0].lstrip())]
            out.append(run[0])
            out.append(f"{indent}... {len(run) - 1} framework frames omitted")
        else:
            out.extend(run)
        run.clear()

    for line in text.splitlines():
        cls = _frame_class(line) if _FRAME_RE.match(line) else None
        if cls and _is_framework(cls):
            run.append(line)
            continue
        flush()
        out.append(line)
    flush()
    return "\n".join(out)
