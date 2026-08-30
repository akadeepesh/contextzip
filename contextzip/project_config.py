"""
project_config.py — Project-level, team-shareable contextzip settings.

Loaded from `.contextzip/config.json`, anchored at the project's git root
(or the project directory itself outside a git repo) — the same anchor
`.contextzip/` itself resolves to by default. Unlike config.py's personal,
per-machine store (~/.config/contextzip/config.json, holds API keys, never
committed), this file is meant to be committed to the repo so every
contributor gets the same project defaults automatically — no per-machine
setup required. `.contextzip/` is gitignored by default (see packager.py's
_ensure_workspace_gitignore), but config.json is deliberately carved out of
that ignore rule so it stays trackable.

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
        redact_secrets   — reserved for a future best-effort scrub of
                            secret-shaped values (API keys, tokens) inside
                            otherwise-included files, on top of the
                            always-excluded credential file patterns.
                            Currently persisted but not yet enforced.

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
      grows over time (a zip+manifest+report set per run, a timestamped
      folder under backups/ per risky apply-zip). See cleanup.py — after
      every successful contextzip command, the workspace is pruned down
      to only the most recent `keep_recent` item(s) per category, no
      confirmation, no separate command needed. This is deliberately
      brutal rather than cautious: every zip is trivially reproducible
      by re-running contextzip, so there's little reason to let old ones
      pile up.
        enabled       — if true (default), auto-cleanup runs after every
                        successful command. Set to false to keep
                        everything contextzip has ever generated.
        keep_recent   — how many most-recent item(s) are kept per
                        category before the rest are deleted: zip+manifest
                        +report sets per mode folder (output/codebase/,
                        output/git-changes/, output/prompt/), timestamped
                        folders under backups/, and archived zips under
                        inbox/applied/. Default 1 — keep only the latest
                        of each, delete everything older immediately.

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

    # Diagnostics — not part of the schema, set by load_project_config so
    # callers (the CLI) can decide whether to surface a deprecation notice.
    source_path: Path | None = None
    is_legacy: bool = False


def _anchor_dir(project_dir: Path) -> Path:
    """Git root if inside a repo, otherwise *project_dir* itself."""
    from contextzip.packager import _find_git_root

    git_root = _find_git_root(project_dir)
    return git_root if git_root is not None else project_dir


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


def is_known_ai_provider(provider: str) -> bool:
    """True if *provider* is a currently-supported AI provider."""
    return provider in _VALID_AI_PROVIDERS
