"""
redact.py — Best-effort in-content secret redaction.

Implements the `limits.redact_secrets` project setting (see project_config.py),
which until now was persisted and editable in the config UI but never actually
enforced by the packaging logic — see the 0.4.0 changelog's "known
limitation."

This is deliberately a *second line of defense*, not a replacement for the
secrets/credentials exclusion list (rules/base.py's SECRET_PATTERNS). That
list keeps whole files that are almost certainly secrets (private keys,
credential files, .env) out of the archive by default. This module instead
handles the narrower case those file-level rules can't catch: an ordinary
source or config file that's fine to include, but happens to have a
secret-shaped value hardcoded somewhere inside it (e.g. an AWS key pasted
into a settings.py for local testing).

Secret files the user deliberately includes
───────────────────────────────────────────
A project's `always_include` can bring a SECRET_PATTERNS file back on
purpose — most often a `.env`, so the AI tool can see which variables the
app expects. Those files are never written as-is (see redact_sensitive_file,
called unconditionally by packager.py, whatever `limits.redact_secrets`
says):
  - `.env` / `.env.*` files keep every key, comment and blank line, but
    every value becomes "[REDACTED]" — not just the secret-shaped ones,
    because a dotenv value is a secret far too often to guess which aren't.
  - Any other secret file gets the same pattern scan as above.
  - A secret file that isn't UTF-8 text (a .p12, a .jks keystore) can't be
    redacted, so packager.py skips it instead of zipping it raw.

Spring Boot config files
────────────────────────
application.properties / application.yml (and profile and bootstrap
variants) are included like any project file but always redacted too, by
key name and in URLs, whatever `limits.redact_secrets` says. See
redact_spring_config.

Design choices, deliberately conservative to match the rest of the project:
  - A curated list of high-precision patterns (known key/token formats,
    private-key blocks, JWTs, and a narrow generic key=value assignment
    form) rather than an entropy-based scan — same "curated list, not a
    heuristic" approach as the file-level secrets baseline in packager.py.
  - Only the matched *value* is replaced with "[REDACTED...]", not the
    whole line — a redacted file should stay useful context for whatever
    AI tool receives the archive.
  - Obvious placeholders ("changeme", "xxxxxxxx", "<your-key-here>") are
    left alone rather than redacted, so example/template files don't get
    needlessly mangled.

Performance: the opt-in scan only ever runs on files the caller has already
confirmed are text and within `limits.max_file_size_mb` (see packager.py,
which reuses the binary/large-file classification `resolve_files()` already
computes) — never on binaries or oversized files. Deliberately included
secret files are the one exception: they're always scanned, whatever their
size. A handful of compiled regexes over a
small text file costs low-single-digit milliseconds; across an entire
project it's negligible next to the I/O of zipping in the first place.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePath

from contextzip.filters import make_spec
from contextzip.rules.base import SECRET_PATTERNS

REDACTED = "[REDACTED]"


@dataclass(frozen=True)
class _SecretPattern:
    name: str
    regex: re.Pattern[str]
    # Which regex group holds the actual secret value to redact.
    # 0 means "redact the whole match" (used for freestanding key formats
    # with no surrounding key=value context to preserve).
    value_group: int = 0


_PLACEHOLDER_VALUES = {
    "changeme",
    "change_me",
    "your_api_key_here",
    "your-api-key-here",
    "your_key_here",
    "example",
    "placeholder",
    "insert_key_here",
    "todo",
    "fixme",
    "xxx",
    "n/a",
    "none",
    "null",
    "test",
    "fake",
    "dummy",
}


def _looks_like_placeholder(value: str) -> bool:
    v = value.strip().strip("'\"").lower()
    if not v:
        return True
    if v in _PLACEHOLDER_VALUES:
        return True
    if set(v) <= {"x"}:  # xxxxxxxx, XXXXXXXX
        return True
    if v.startswith("<") and v.endswith(">"):  # <your-key-here>
        return True
    if v.startswith("$") or v.startswith("{{") or v.startswith("%"):
        # env-var interpolation / templating placeholders, e.g. ${API_KEY},
        # {{ secrets.API_KEY }}, %API_KEY% — a reference, not a live secret.
        return True
    return False


# Freestanding, self-identifying formats — redact the whole match, no
# surrounding key=value context needed.
_PATTERNS: list[_SecretPattern] = [
    _SecretPattern("AWS Access Key ID", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    _SecretPattern("Google API Key", re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b")),
    _SecretPattern("GitHub Token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    _SecretPattern("Slack Token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,48}\b")),
    _SecretPattern("Stripe Key", re.compile(r"\bsk_(?:live|test)_[0-9A-Za-z]{24,}\b")),
    _SecretPattern("Anthropic API Key", re.compile(r"\bsk-ant-[A-Za-z0-9\-_]{20,}\b")),
    _SecretPattern("OpenAI API Key", re.compile(r"\bsk-proj-[A-Za-z0-9\-_]{20,}\b")),
    _SecretPattern(
        "JWT",
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    ),
    _SecretPattern(
        "Private Key Block",
        re.compile(
            r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"
            r"[\s\S]+?"
            r"-----END (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"
        ),
    ),
    # AWS secret access keys have no distinguishing prefix, so this one
    # needs the "aws_secret_access_key = ..." context to be high-precision.
    _SecretPattern(
        "AWS Secret Access Key",
        re.compile(
            r"(?i)aws_secret_access_key\s*[:=]\s*['\"]?([A-Za-z0-9/+=]{40})['\"]?"
        ),
        value_group=1,
    ),
    # Narrow generic assignment: fires when a key/secret/token/password-shaped
    # word appears in the assigned name (standalone or as part of a
    # compound identifier like DB_PASSWORD, STRIPE_SECRET_KEY), assigned a
    # quoted value of at least 10 chars — conservative on purpose to keep
    # false positives low, though a false positive here only means an
    # unnecessary "[REDACTED]" in the zip copy, not any change to the
    # user's actual file.
    _SecretPattern(
        "Generic Secret Assignment",
        re.compile(
            r"(?i)\b[A-Za-z0-9_]*?"
            r"(?:api[_-]?key|secret[_-]?key|access[_-]?token|"
            r"auth[_-]?token|client[_-]?secret|password|passwd)"
            r"[A-Za-z0-9_]*\s*[:=]\s*['\"]([^'\"\s]{10,})['\"]"
        ),
        value_group=1,
    ),
]


def redact_secrets(text: str) -> tuple[str, list[str]]:
    """
    Scan *text* for secret-shaped values and replace each one with
    "[REDACTED]", leaving everything else — including the surrounding
    key name, quotes, and punctuation — untouched.

    Returns (possibly-modified text, list of pattern names that matched at
    least once — e.g. ["AWS Access Key ID", "Generic Secret Assignment"]).
    An empty list means nothing matched and *text* is returned unchanged
    (the same string object, so callers can cheaply check `if matched:`
    to skip rewriting a file that didn't need it).
    """
    matched_names: list[str] = []

    for pattern in _PATTERNS:

        def _sub(m: re.Match[str], pattern: _SecretPattern = pattern) -> str:
            value = m.group(pattern.value_group)
            if _looks_like_placeholder(value):
                return m.group(0)
            matched_names.append(pattern.name)
            if pattern.value_group == 0:
                return "[REDACTED]"
            full = m.group(0)
            g_start, g_end = m.span(pattern.value_group)
            rel_start = g_start - m.start()
            rel_end = g_end - m.start()
            return full[:rel_start] + "[REDACTED]" + full[rel_end:]

        text = pattern.regex.sub(_sub, text)

    return text, matched_names


# ---------------------------------------------------------------------------
# Secret files the user deliberately included (see module docstring)
# ---------------------------------------------------------------------------

_SECRET_SPEC = make_spec(SECRET_PATTERNS)

# KEY=value, optionally prefixed with `export` — the dotenv assignment form.
_ENV_ASSIGNMENT = re.compile(r"^(\s*(?:export\s+)?[A-Za-z_][A-Za-z0-9_.\-]*\s*=[ \t]*)(.*)$")

ENV_VALUE = ".env value"


def is_sensitive_path(rel: PurePath | str) -> bool:
    """True if *rel* (relative to the project root) matches SECRET_PATTERNS."""
    rel_str = PurePath(rel).as_posix()
    if _SECRET_SPEC.match_file(rel_str):
        return True
    parts = PurePath(rel_str).parts[:-1]
    return any(
        _SECRET_SPEC.match_file("/".join(parts[: i + 1]) + "/") for i in range(len(parts))
    )


def is_env_file(rel: PurePath | str) -> bool:
    """True for `.env` and `.env.*` files (`.env.local`, `.env.production`, ...)."""
    name = PurePath(rel).name
    return name == ".env" or name.startswith(".env.")


def _split_line_ending(line: str) -> tuple[str, str]:
    body = line.rstrip("\r\n")
    return body, line[len(body):]


def _closing_quote(value: str, quote: str, start: int) -> int:
    """Index of the closing *quote* in *value* at or after *start*, or -1."""
    i = start
    while i < len(value):
        if value[i] == "\\" and quote == '"':
            i += 2
            continue
        if value[i] == quote:
            return i
        i += 1
    return -1


def _safe_tail(tail: str) -> str:
    """Keep what follows a closing quote only if it's blank or a `#comment`."""
    if not tail.strip() or re.match(r"\s+#", tail):
        return tail
    return " " + REDACTED


def redact_env(text: str) -> tuple[str, int]:
    """
    Replace every value in a dotenv file with "[REDACTED]".

    Keys, `export` prefixes, quotes, inline comments, blank lines and line
    endings are kept, so the file still tells the reader which variables
    exist. Quoted values that span several lines collapse to one redacted
    line. Any other non-comment line (something that isn't KEY=value) is
    redacted whole, since it might be a pasted secret.

    Returns (redacted text, number of values redacted).
    """
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    count = 0
    i = 0

    while i < len(lines):
        body, ending = _split_line_ending(lines[i])
        i += 1
        stripped = body.strip()

        if not stripped or stripped.startswith("#"):
            out.append(body + ending)
            continue

        m = _ENV_ASSIGNMENT.match(body)
        if not m:
            out.append(REDACTED + ending)
            count += 1
            continue

        prefix, value = m.group(1), m.group(2)
        if not value.strip():
            out.append(body + ending)
            continue

        count += 1
        quote = value[0]
        if quote not in "\"'`":
            # Unquoted: the value runs up to an inline ` #comment`.
            comment = re.search(r"\s+#.*$", value)
            tail = comment.group(0) if comment else ""
            out.append(prefix + REDACTED + tail + ending)
            continue

        close = _closing_quote(value, quote, 1)
        if close != -1:
            out.append(prefix + quote + REDACTED + quote + _safe_tail(value[close + 1:]) + ending)
            continue

        # Multi-line quoted value: swallow lines until the closing quote.
        tail = None
        while i < len(lines):
            next_body, ending = _split_line_ending(lines[i])
            i += 1
            close = _closing_quote(next_body, quote, 0)
            if close != -1:
                tail = next_body[close + 1:]
                break
        if tail is None:
            # Never closed — everything after the opening quote is the value.
            out.append(prefix + REDACTED + ending)
        else:
            out.append(prefix + quote + REDACTED + quote + _safe_tail(tail) + ending)

    return "".join(out), count


def redact_sensitive_file(rel: PurePath | str, text: str) -> tuple[str, list[str]]:
    """
    Redact a SECRET_PATTERNS file the user deliberately included.

    `.env` files get every value redacted (see redact_env); any other secret
    file gets the regular pattern scan. Returns the same shape as
    redact_secrets(), with one ENV_VALUE entry per redacted dotenv value.
    """
    names: list[str] = []
    if is_env_file(rel):
        text, count = redact_env(text)
        names.extend([ENV_VALUE] * count)
    text, matched = redact_secrets(text)
    names.extend(matched)
    return text, names


# ---------------------------------------------------------------------------
# Spring Boot config files: always redacted (see redact_spring_config)
# ---------------------------------------------------------------------------

# application.properties, application-prod.yml, bootstrap.yaml, ...
_SPRING_CONFIG_NAME = re.compile(
    r"^(?:application|bootstrap)(?:-[\w.-]+)?\.(?:properties|ya?ml)$", re.IGNORECASE
)

SPRING_VALUE = "Spring config value"

# Words that make a key's value a secret, matched against the key's last
# segment with '-' and '_' removed (key-store-password -> keystorepassword).
_SPRING_SECRET_WORDS = (
    "password", "passwd", "pwd", "passphrase", "secret", "token", "credential",
    "apikey", "privatekey", "accesskey", "secretkey", "signingkey", "encryptionkey",
    "masterkey", "clientkey", "authkey",
)
# ...unless the segment ends like a setting *about* the secret, not the
# secret itself: token-uri, token-validity-seconds, secret-file, ...
_SPRING_NOT_SECRET_ENDINGS = (
    "uri", "url", "endpoint", "path", "header", "name", "expiration", "expiry",
    "validity", "ttl", "seconds", "millis", "timeout", "length", "size", "enabled",
    "required", "type", "algorithm", "prefix", "location", "file", "format", "mode",
)

# user:password@ in a URL, e.g. jdbc:postgresql://app:hunter2@db/app
_URL_CREDENTIALS = re.compile(r"(\b[A-Za-z][\w+.\-]*://[^:/@\s'\"]+:)([^@/\s'\"]+)(@)")
# ?password=... in a URL or connection string
_URL_PASSWORD_PARAM = re.compile(r"(?i)([?&;](?:password|passwd|pwd)=)([^&;\s'\"]+)")

_YAML_KEY_LINE = re.compile(
    r"^(\s*)(-\s+)?([\w.\-\[\]/@$]+|\"[^\"]*\"|'[^']*')(\s*:)(?=\s|$)(\s*)(.*)$"
)
_YAML_BLOCK_SCALAR = re.compile(r"^[|>][-+0-9]*$")
_PROPERTIES_LINE = re.compile(r"^(\s*)((?:\\.|[^=:\s\\])+)(\s*[=:]\s*|\s+)(.*)$")


def is_spring_config(rel: PurePath | str) -> bool:
    """True for Spring Boot config files: application[-profile].properties/.yml/.yaml, bootstrap*."""
    return bool(_SPRING_CONFIG_NAME.match(PurePath(rel).name))


def _is_secret_key(key: str) -> bool:
    segments = [s for s in re.split(r"[.\[\]]", key.strip("'\"")) if s]
    last = segments[-1] if segments else ""
    norm = re.sub(r"[-_]", "", last).lower()
    if norm == "key":
        return True
    if not any(w in norm for w in _SPRING_SECRET_WORDS):
        return False
    return not norm.endswith(_SPRING_NOT_SECRET_ENDINGS)


def _redact_secret_value(value: str) -> tuple[str, bool]:
    """
    A secret key's value -> "[REDACTED]". A ${ENV_VAR} reference is kept,
    since it points at a secret rather than holding one, but the fallback in
    ${DB_PASSWORD:hunter2} is redacted. Placeholders like "changeme" are kept.
    """
    v = value.strip()
    if not v:
        return value, False
    m = re.fullmatch(r"\$\{([^}:]+):(.*)\}", v, re.DOTALL)
    if m:
        default = m.group(2)
        if not default.strip() or _looks_like_placeholder(default):
            return value, False
        return "${" + m.group(1) + ":" + REDACTED + "}", True
    if _looks_like_placeholder(v):
        return value, False
    return REDACTED, True


def _scrub_url_credentials(text: str) -> tuple[str, int]:
    """Redact passwords embedded in URLs / connection strings anywhere in *text*."""
    count = 0

    def _sub(m: re.Match[str]) -> str:
        nonlocal count
        if _looks_like_placeholder(m.group(2)):
            return m.group(0)
        count += 1
        return m.group(1) + REDACTED + (m.group(3) if m.re.groups >= 3 else "")

    text = _URL_CREDENTIALS.sub(_sub, text)
    text = _URL_PASSWORD_PARAM.sub(_sub, text)
    return text, count


def _continues(line: str) -> bool:
    """A .properties line continues onto the next when it ends in an odd number of backslashes."""
    trailing = len(line) - len(line.rstrip("\\"))
    return trailing % 2 == 1


def _redact_properties(text: str) -> tuple[str, int]:
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    count = 0
    i = 0
    while i < len(lines):
        body, ending = _split_line_ending(lines[i])
        i += 1
        stripped = body.lstrip()
        if not stripped or stripped[0] in "#!":
            out.append(body + ending)
            continue
        m = _PROPERTIES_LINE.match(body)
        if not m:
            out.append(body + ending)
            continue
        prefix, key, value = m.group(1) + m.group(2) + m.group(3), m.group(2), m.group(4)

        # Gather continuation lines into one logical value.
        raw_lines = [body + ending]
        logical = value
        last = body
        while _continues(last) and i < len(lines):
            next_body, ending = _split_line_ending(lines[i])
            i += 1
            raw_lines.append(next_body + ending)
            logical = logical[:-1] + next_body.lstrip()
            last = next_body

        if _is_secret_key(key):
            new_value, changed = _redact_secret_value(logical)
            if changed:
                out.append(prefix + new_value + ending)
                count += 1
                continue
        scrubbed, n = _scrub_url_credentials("".join(raw_lines))
        out.append(scrubbed)
        count += n
    return "".join(out), count


def _redact_yaml(text: str) -> tuple[str, int]:
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    count = 0
    i = 0
    while i < len(lines):
        body, ending = _split_line_ending(lines[i])
        i += 1
        m = None if body.lstrip().startswith("#") else _YAML_KEY_LINE.match(body)
        if m is None:
            scrubbed, n = _scrub_url_credentials(body)
            out.append(scrubbed + ending)
            count += n
            continue
        indent, dash, key, colon, space, rest = m.groups()
        head = indent + (dash or "") + key + colon + space
        key_indent = len(indent) + len(dash or "")

        if not _is_secret_key(key) or not rest or rest.startswith("#"):
            scrubbed, n = _scrub_url_credentials(body)
            out.append(scrubbed + ending)
            count += n
            continue

        if rest[0] in "'\"":
            quote = rest[0]
            close = _closing_quote(rest, quote, 1)
            inner = rest[1:close] if close != -1 else rest[1:]
            tail = rest[close + 1:] if close != -1 else ""
            new_inner, changed = _redact_secret_value(inner)
            if changed:
                out.append(head + quote + new_inner + quote + _safe_tail(tail) + ending)
                count += 1
            else:
                out.append(body + ending)
            continue

        comment = re.search(r"\s+#.*$", rest)
        value = rest[: comment.start()] if comment else rest
        tail = comment.group(0) if comment else ""

        if _YAML_BLOCK_SCALAR.match(value.strip()):
            # A multi-line value (often a PEM key): drop its lines, keep one marker.
            while i < len(lines):
                next_body, _next_ending = _split_line_ending(lines[i])
                next_indent = len(next_body) - len(next_body.lstrip())
                if next_body.strip() and next_indent <= key_indent:
                    break
                i += 1
            out.append(head + REDACTED + tail + ending)
            count += 1
            continue

        new_value, changed = _redact_secret_value(value)
        if changed:
            out.append(head + new_value + tail + ending)
            count += 1
        else:
            out.append(body + ending)
    return "".join(out), count


def redact_spring_config(rel: PurePath | str, text: str) -> tuple[str, list[str]]:
    """
    Redact a Spring Boot config file (see is_spring_config).

    application.properties / application.yml are ordinary project files
    that an AI tool needs to see, so they're included, but they routinely
    hold live database passwords, JWT signing secrets and API keys as plain,
    unquoted values that redact_secrets() doesn't catch. So, whatever
    `limits.redact_secrets` says:

      - The value of every key whose last segment names a secret
        (password, secret, token, credentials, api-key, private-key, key,
        ...) becomes "[REDACTED]". ${ENV_VAR} references are kept, but a
        fallback such as ${DB_PASSWORD:hunter2} is redacted.
      - A password embedded in any URL (jdbc:postgresql://app:pw@db/app,
        ?password=pw) is redacted, whatever the key.
      - Then the regular redact_secrets() scan runs over the result.

    Keys, comments, structure and every other value are kept. Returns the
    same shape as redact_secrets(), with one SPRING_VALUE entry per value.
    """
    if PurePath(rel).name.lower().endswith(".properties"):
        text, count = _redact_properties(text)
    else:
        text, count = _redact_yaml(text)
    names = [SPRING_VALUE] * count
    text, matched = redact_secrets(text)
    names.extend(matched)
    return text, names
