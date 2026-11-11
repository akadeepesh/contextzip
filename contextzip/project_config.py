"""
project_config.py — Project-level contextzip settings.

Loaded from `.contextzip/config.json`, anchored at the project's git root
(or the project directory itself outside a git repo) — the same anchor
`.contextzip/` itself resolves to by default. Unlike config.py's personal,
per-machine store (~/.config/contextzip/config.json, holds API keys, never
committed), this file holds settings a whole team could plausibly want to
share — but contextzip never presumes that on your behalf. `.contextzip/`
is gitignored in full by default (see packager.py's
_ensure_workspace_gitignore), config.json included; a team that wants to
share these settings has to explicitly `git add -f .contextzip/config.json`
to commit it. This is a deliberate design choice, not an oversight — see
"Never presumes what belongs in git" below.

Currently supports:

    {
      "workspace_location": "git-root" | "cwd" | "<path>",
      "scan_depth": 2,
      "always_include": ["docs/architecture.md"],
      "always_exclude": ["*.snap"],
      "ai": {
        "enabled": true,
        "provider": "gemini",
        "max_files": 10,
        "prompt_template": "We use pytest, not unittest."
      },
      "limits": {
        "max_file_size_mb": 1,
        "redact_secrets": false
      },
      "applied_zip_retention": 1,
      "webui": {
        "auto_open": true,
        "port": null
      },
      "cleanup": {
        "enabled": true,
        "keep_recent": 1
      },
      "snapshots": {
        "keep": 10,
        "max_age_days": 14
      },
      "zip_guide": true,
      "tokens": {
        "max": 150000,
        "windows": {"claude": 500000},
        "show_fit": true
      }
    }

All keys are optional. Missing or malformed values fall through to the
next tier in the resolution order (see config.py's workspace_location
docstring and packager.py's _resolve_workspace_location), or to a sensible
built-in default for the newer preference fields.

  workspace_location / scan_depth
      Resolved before config.json's own location is known (the file lives
      *inside* the workspace it can redirect), so these are always read
      from config.json at the default anchor (git root, or the project
      directory outside a git repo) — see packager.py's _workspace_dir.

  always_include / always_exclude
      Persistent, gitwildmatch-style patterns applied on every run without
      needing to repeat --include/--exclude flags. always_exclude behaves
      like a standing --exclude/-e pattern list. always_include behaves
      like a standing negation — it force-includes matching files even if
      an auto-rule or .gitignore would otherwise exclude them. Neither
      overrides an explicit `contextzip include PATH` / --include for the
      current run, which stays the more specific, deliberate choice.

  ai
      Persistent AI-selection preferences.
        enabled          — if false, --prompt is refused with a clear
                            message instead of silently ignored.
        provider         — reserved for future providers; only "gemini"
                            is currently supported.
        max_files        — caps how many files the AI selector may return.
        prompt_template  — prepended to every generated prompt.txt, ahead
                            of the task description — house conventions
                            the AI tool receiving the zip should always
                            see (testing framework, folder conventions,
                            etc.). Empty string means nothing is added.

  limits
      Packaging thresholds.
        max_file_size_mb — files at or above this size are flagged as
                            "large" before packaging (still included,
                            just surfaced) instead of the fixed 1 MB
                            default. Fractional values are allowed.
        redact_secrets   — when true, files that are already going into the
                            archive are scanned for secret-shaped values
                            (API keys, tokens, private-key blocks — see
                            redact.py) and matched values are replaced
                            with "[REDACTED]" before writing. This is a
                            second line of defense on top of, not a
                            replacement for, the always-excluded whole-file
                            credential patterns (§5-equivalent baseline in
                            packager.py) — those still keep obviously-secret
                            files out entirely regardless of this setting.
                            Only ever scans files already known to be text
                            and within max_file_size_mb; binary and
                            oversized files are never scanned. Off by
                            default.

  applied_zip_retention
      How many past `apply-zip` archives to keep in
      `.contextzip/inbox/applied/` before pruning the oldest. Defaults to
      1 (only the most recent). Set higher to keep a longer audit trail.

  webui
      Preferences for `contextzip config --ui`.
        auto_open  — if false, the server still starts and prints the
                     URL, but doesn't try to launch a browser tab itself.
        port       — bind to this fixed port instead of a random free
                     one; useful behind strict local-port firewall rules.
                     null means "pick a random free port" (the default).

  cleanup
      Automatic housekeeping for `.contextzip/`, which otherwise only
      grows over time (a zip+manifest+report set per run, archived zips
      under inbox/applied/, loose apply-report.txt files in inbox/). See
      cleanup.py — after every successful contextzip command, the
      workspace is pruned down to only the most recent `keep_recent`
      item(s) per category, no confirmation, no separate command needed.
      This is deliberately brutal rather than cautious: every zip is
      trivially reproducible by re-running contextzip, so there's little
      reason to let old ones pile up.
        enabled       — if true (default), auto-cleanup runs after every
                        successful command. Set to false to keep
                        everything contextzip has ever generated.
        keep_recent   — how many most-recent item(s) are kept per
                        category before the rest are deleted: zip+manifest
                        +report sets per mode folder (output/codebase/,
                        output/git-changes/, output/prompt/), archived
                        zips under inbox/applied/, and loose
                        apply-report.txt files in inbox/. Default 1 —
                        keep only the latest of each, delete everything
                        older immediately.

  snapshots
      Every package is kept as a numbered snapshot (see snapshots.py) so
      apply-zip can merge an AI reply against exactly what was sent, even
      after you've packaged again. A snapshot is kept while it's among the
      newest `keep`, younger than `max_age_days`, or still waiting on a
      reply in the inbox.

  zip_guide
      Whether every ZIP carries a CONTEXTZIP.md (see guide.py): the
      snapshot ID plus short instructions that make AI replies easy to
      apply. Default true.

  tokens
      Token counts, budget and model fit (see tokens.py and models.py).
        max       — a token budget for every run, like --max-tokens (which
                    overrides it). A run over budget writes nothing, unless
                    --trim is given. Must be at least 1; unset by default.
        windows   — context windows in tokens, by --for name, replacing
                    the built-in ones: {"claude": 500000}. A new name adds
                    a target of your own. Values below 1000 are ignored.
        show_fit  — if false, the "Fits" line isn't printed (--json still
                    has "fit"). Default true.

The schema is intentionally a flat, easily-extended dict so future
preferences can be added without another migration.

Deprecation
-----------
A legacy `.contextzip.json` file (same anchor) is still read as a fallback
when `.contextzip/config.json` doesn't exist, so existing projects keep
working. New projects, and any project that already has
`.contextzip/config.json`, should use the new location — see
`has_legacy_project_config` for surfacing a one-time CLI warning.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

_PROJECT_CONFIG_FILENAME = "config.json"
_PROJECT_CONFIG_DIRNAME = ".contextzip"
_LEGACY_PROJECT_CONFIG_FILENAME = ".contextzip.json"

_VALID_AI_PROVIDERS = frozenset({"gemini"})
_DEFAULT_AI_PROVIDER = "gemini"
_DEFAULT_AI_MAX_FILES = 10
_DEFAULT_PROMPT_TEMPLATE = ""

_DEFAULT_MAX_FILE_SIZE_MB = 1.0
_DEFAULT_REDACT_SECRETS = False

_DEFAULT_APPLIED_ZIP_RETENTION = 1

_DEFAULT_WEBUI_AUTO_OPEN = True
_DEFAULT_WEBUI_PORT = None

_DEFAULT_CLEANUP_ENABLED = True
_DEFAULT_CLEANUP_KEEP_RECENT = 1

_DEFAULT_SNAPSHOTS_KEEP = 10
_DEFAULT_SNAPSHOTS_MAX_AGE_DAYS = 14
_DEFAULT_ZIP_GUIDE = True

_DEFAULT_SHOW_FIT = True
_MIN_TOKEN_WINDOW = 1000


@dataclass
class AIConfig:
    enabled: bool = True
    provider: str = _DEFAULT_AI_PROVIDER
    max_files: int = _DEFAULT_AI_MAX_FILES
    prompt_template: str = _DEFAULT_PROMPT_TEMPLATE


@dataclass
class LimitsConfig:
    max_file_size_mb: float = _DEFAULT_MAX_FILE_SIZE_MB
    redact_secrets: bool = _DEFAULT_REDACT_SECRETS


@dataclass
class WebUIConfig:
    auto_open: bool = _DEFAULT_WEBUI_AUTO_OPEN
    port: int | None = _DEFAULT_WEBUI_PORT


@dataclass
class CleanupConfig:
    enabled: bool = _DEFAULT_CLEANUP_ENABLED
    keep_recent: int = _DEFAULT_CLEANUP_KEEP_RECENT


@dataclass
class SnapshotsConfig:
    """How long versioned snapshots (see snapshots.py) are kept."""

    keep: int = _DEFAULT_SNAPSHOTS_KEEP
    max_age_days: int = _DEFAULT_SNAPSHOTS_MAX_AGE_DAYS


@dataclass
class TokensConfig:
    """Model fit settings (see models.py)."""

    windows: dict[str, int] = field(default_factory=dict)
    show_fit: bool = _DEFAULT_SHOW_FIT
    max: int | None = None


@dataclass
class ProjectConfig:
    workspace_location: str | None = None
    scan_depth: int | None = None
    always_include: list[str] = field(default_factory=list)
    always_exclude: list[str] = field(default_factory=list)
    ai: AIConfig = field(default_factory=AIConfig)
    limits: LimitsConfig = field(default_factory=LimitsConfig)
    applied_zip_retention: int = _DEFAULT_APPLIED_ZIP_RETENTION
    webui: WebUIConfig = field(default_factory=WebUIConfig)
    cleanup: CleanupConfig = field(default_factory=CleanupConfig)
    snapshots: SnapshotsConfig = field(default_factory=SnapshotsConfig)
    zip_guide: bool = _DEFAULT_ZIP_GUIDE
    tokens: TokensConfig = field(default_factory=TokensConfig)

    # Diagnostics — not part of the schema, set by load_project_config so
    # callers (the CLI) can decide whether to surface a deprecation notice.
    source_path: Path | None = None
    is_legacy: bool = False


def _anchor_dir(project_dir: Path) -> Path:
    """The project root for *project_dir* — see root.find_project_root."""
    from contextzip.root import find_project_root

    return find_project_root(project_dir).root


def project_config_path(project_dir: Path) -> Path:
    """
    Return where the project-level config file would live for *project_dir*
    — `.contextzip/config.json` under the git root (or project_dir itself
    outside a git repo). Does not imply the file exists.
    """
    return _anchor_dir(project_dir) / _PROJECT_CONFIG_DIRNAME / _PROJECT_CONFIG_FILENAME


def legacy_project_config_path(project_dir: Path) -> Path:
    """Return where the deprecated `.contextzip.json` file would live."""
    return _anchor_dir(project_dir) / _LEGACY_PROJECT_CONFIG_FILENAME


def has_legacy_project_config(project_dir: Path) -> bool:
    """
    True if a deprecated `.contextzip.json` is present and would actually
    be used (i.e. the new `.contextzip/config.json` hasn't been created yet).
    """
    return (
        not project_config_path(project_dir).is_file()
        and legacy_project_config_path(project_dir).is_file()
    )


def load_project_config(project_dir: Path) -> ProjectConfig:
    """
    Load project config for *project_dir*.

    Prefers `.contextzip/config.json`. Falls back to the deprecated
    `.contextzip.json` if the new file isn't present, so existing projects
    keep working without any action required.

    Never raises — a missing, malformed, or unreadable file just yields an
    empty ProjectConfig, so callers can fall through to the next tier
    without special-casing errors.
    """
    path = project_config_path(project_dir)
    is_legacy = False

    if not path.is_file():
        legacy_path = legacy_project_config_path(project_dir)
        if legacy_path.is_file():
            path = legacy_path
            is_legacy = True
        else:
            return ProjectConfig()

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ProjectConfig()

    if not isinstance(data, dict):
        return ProjectConfig()

    cfg = _parse_config_dict(data)
    cfg.source_path = path
    cfg.is_legacy = is_legacy
    return cfg


def _parse_config_dict(data: dict) -> ProjectConfig:
    workspace_location = data.get("workspace_location")
    if not isinstance(workspace_location, str) or not workspace_location.strip():
        workspace_location = None

    scan_depth = data.get("scan_depth")
    if (
        not isinstance(scan_depth, int)
        or isinstance(scan_depth, bool)
        or scan_depth < 0
    ):
        scan_depth = None

    always_include = _parse_str_list(data.get("always_include"))
    always_exclude = _parse_str_list(data.get("always_exclude"))
    ai_config = _parse_ai_config(data.get("ai"))
    limits_config = _parse_limits_config(data.get("limits"))
    webui_config = _parse_webui_config(data.get("webui"))
    cleanup_config = _parse_cleanup_config(data.get("cleanup"))
    snapshots_config = _parse_snapshots_config(data.get("snapshots"))
    zip_guide = data.get("zip_guide", _DEFAULT_ZIP_GUIDE)
    if not isinstance(zip_guide, bool):
        zip_guide = _DEFAULT_ZIP_GUIDE

    applied_zip_retention = data.get("applied_zip_retention", _DEFAULT_APPLIED_ZIP_RETENTION)
    if (
        not isinstance(applied_zip_retention, int)
        or isinstance(applied_zip_retention, bool)
        or applied_zip_retention < 1
    ):
        applied_zip_retention = _DEFAULT_APPLIED_ZIP_RETENTION

    return ProjectConfig(
        workspace_location=workspace_location,
        scan_depth=scan_depth,
        always_include=always_include,
        always_exclude=always_exclude,
        ai=ai_config,
        limits=limits_config,
        applied_zip_retention=applied_zip_retention,
        webui=webui_config,
        cleanup=cleanup_config,
        snapshots=snapshots_config,
        zip_guide=zip_guide,
        tokens=_parse_tokens_config(data.get("tokens")),
    )


def _parse_str_list(value: object) -> list[str]:
    """Return a clean list[str], dropping anything malformed. Never raises."""
    if not isinstance(value, list):
        return []
    return [v.strip() for v in value if isinstance(v, str) and v.strip()]


def _parse_ai_config(value: object) -> AIConfig:
    if not isinstance(value, dict):
        return AIConfig()

    enabled = value.get("enabled", True)
    if not isinstance(enabled, bool):
        enabled = True

    provider = value.get("provider", _DEFAULT_AI_PROVIDER)
    if not isinstance(provider, str) or not provider.strip():
        provider = _DEFAULT_AI_PROVIDER
    else:
        provider = provider.strip()

    max_files = value.get("max_files", _DEFAULT_AI_MAX_FILES)
    if not isinstance(max_files, int) or isinstance(max_files, bool) or max_files < 1:
        max_files = _DEFAULT_AI_MAX_FILES

    prompt_template = value.get("prompt_template", _DEFAULT_PROMPT_TEMPLATE)
    if not isinstance(prompt_template, str):
        prompt_template = _DEFAULT_PROMPT_TEMPLATE
    else:
        prompt_template = prompt_template.strip()

    return AIConfig(
        enabled=enabled,
        provider=provider,
        max_files=max_files,
        prompt_template=prompt_template,
    )


def _parse_limits_config(value: object) -> LimitsConfig:
    if not isinstance(value, dict):
        return LimitsConfig()

    max_file_size_mb = value.get("max_file_size_mb", _DEFAULT_MAX_FILE_SIZE_MB)
    if (
        not isinstance(max_file_size_mb, (int, float))
        or isinstance(max_file_size_mb, bool)
        or max_file_size_mb <= 0
    ):
        max_file_size_mb = _DEFAULT_MAX_FILE_SIZE_MB

    redact_secrets = value.get("redact_secrets", _DEFAULT_REDACT_SECRETS)
    if not isinstance(redact_secrets, bool):
        redact_secrets = _DEFAULT_REDACT_SECRETS

    return LimitsConfig(
        max_file_size_mb=float(max_file_size_mb), redact_secrets=redact_secrets
    )


def _parse_webui_config(value: object) -> WebUIConfig:
    if not isinstance(value, dict):
        return WebUIConfig()

    auto_open = value.get("auto_open", _DEFAULT_WEBUI_AUTO_OPEN)
    if not isinstance(auto_open, bool):
        auto_open = _DEFAULT_WEBUI_AUTO_OPEN

    port = value.get("port", _DEFAULT_WEBUI_PORT)
    if (
        not isinstance(port, int)
        or isinstance(port, bool)
        or not (1024 <= port <= 65535)
    ):
        port = _DEFAULT_WEBUI_PORT

    return WebUIConfig(auto_open=auto_open, port=port)


def _parse_cleanup_config(value: object) -> CleanupConfig:
    if not isinstance(value, dict):
        return CleanupConfig()

    enabled = value.get("enabled", _DEFAULT_CLEANUP_ENABLED)
    if not isinstance(enabled, bool):
        enabled = _DEFAULT_CLEANUP_ENABLED

    keep_recent = value.get("keep_recent", _DEFAULT_CLEANUP_KEEP_RECENT)
    if (
        not isinstance(keep_recent, int)
        or isinstance(keep_recent, bool)
        or keep_recent < 0
    ):
        keep_recent = _DEFAULT_CLEANUP_KEEP_RECENT

    return CleanupConfig(
        enabled=enabled,
        keep_recent=keep_recent,
    )


def _parse_snapshots_config(value: object) -> SnapshotsConfig:
    if not isinstance(value, dict):
        return SnapshotsConfig()

    def _int(key: str, default: int, minimum: int) -> int:
        v = value.get(key, default)
        if not isinstance(v, int) or isinstance(v, bool) or v < minimum:
            return default
        return v

    return SnapshotsConfig(
        keep=_int("keep", _DEFAULT_SNAPSHOTS_KEEP, 1),
        max_age_days=_int("max_age_days", _DEFAULT_SNAPSHOTS_MAX_AGE_DAYS, 0),
    )


def _parse_tokens_config(value: object) -> TokensConfig:
    if not isinstance(value, dict):
        return TokensConfig()

    windows: dict[str, int] = {}
    raw_windows = value.get("windows")
    if isinstance(raw_windows, dict):
        for key, window in raw_windows.items():
            if (
                isinstance(key, str)
                and key.strip()
                and isinstance(window, int)
                and not isinstance(window, bool)
                and window >= _MIN_TOKEN_WINDOW
            ):
                windows[key.strip().lower()] = window

    show_fit = value.get("show_fit", _DEFAULT_SHOW_FIT)
    if not isinstance(show_fit, bool):
        show_fit = _DEFAULT_SHOW_FIT

    budget = value.get("max")
    if not isinstance(budget, int) or isinstance(budget, bool) or budget < 1:
        budget = None

    return TokensConfig(windows=windows, show_fit=show_fit, max=budget)


def is_known_ai_provider(provider: str) -> bool:
    """True if *provider* is a currently-supported AI provider."""
    return provider in _VALID_AI_PROVIDERS
