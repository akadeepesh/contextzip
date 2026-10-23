"""
remote.py — Package a GitHub repository without cloning it.

`contextzip github.com/user/repo` (or `gh:user/repo`) asks the GitHub API
which commit to use, downloads that commit's tarball and unpacks it into a
local cache. From there it's an ordinary project: detection, exclusion
rules and redaction all apply unchanged. No git needed.

    <cache>/github.com/<owner>/<repo>/<sha12>/
      <repo>/          the unpacked tree, which becomes the project root
      <repo>.zip       where the CLI writes the ZIP (+ its report)
      .complete        written last; an entry without it is re-fetched

A commit never changes, so a cached entry is reused as-is: the only network
call on a cache hit is resolving the branch to a commit, and none at all
when you pass a full commit SHA.

Private repositories work with a token from GITHUB_TOKEN or GH_TOKEN, or
the one `gh auth token` prints. The token is only ever sent to
api.github.com — the download itself is a signed, short-lived redirect
URL that's fetched without it.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from urllib.parse import quote

API_URL = "https://api.github.com"
HOST = "github.com"
CACHE_ENV_VAR = "CONTEXTZIP_CACHE_DIR"
MAX_MB_ENV_VAR = "CONTEXTZIP_REMOTE_MAX_MB"
# Set by a server that fetches on behalf of other people (see service.py):
# with its own token it could otherwise read that account's private repos.
PUBLIC_ONLY_ENV_VAR = "CONTEXTZIP_REMOTE_PUBLIC_ONLY"
DEFAULT_MAX_MB = 250
# The unpacked tree may be much larger than the gzipped download; this caps
# it, so a pathological archive can't fill the disk.
_EXPANSION_LIMIT = 6
# Cached commits kept per repository; older ones are removed after a fetch.
_KEEP_PER_REPO = 3
_COMPLETE_MARKER = ".complete"
_TIMEOUT = 30.0

# Folders never unpacked: git metadata, and a .contextzip/ workspace whose
# config.json could otherwise steer where this run writes its files.
_SKIP_TOP_DIRS = frozenset({".git", ".contextzip"})

_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_WINDOWS_RESERVED = frozenset(
    {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
)
_WINDOWS_BAD_CHARS = set('<>:"|?*')

# Hosts recognised only to say they aren't supported yet.
_OTHER_HOSTS = ("gitlab.com", "bitbucket.org", "codeberg.org")


class RemoteError(Exception):
    """
    A GitHub repository couldn't be fetched. The message is shown as-is;
    *kind* says why, for callers that map it to something (an HTTP status):
    invalid, not_found, private, too_big, rate_limited, auth or unavailable.
    """

    def __init__(self, message: str, kind: str = "unavailable"):
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class RemoteSpec:
    """A repository named on the command line, before anything is fetched."""

    owner: str
    repo: str
    tree: tuple[str, ...] = ()
    """Path segments after /tree/ or /blob/ in a URL: a ref, then maybe a
    path. Which segments are the ref is only known once GitHub is asked,
    because branch names can contain slashes (see `fetch`)."""

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.repo}"


@dataclass
class FetchedRemote:
    """A repository unpacked into the cache, ready to package."""

    spec: RemoteSpec
    sha: str
    ref: str | None
    """The branch, tag or commit asked for; None for the default branch."""
    project_dir: Path
    zip_path: Path
    subdir: str | None = None
    """A folder or file inside the repo the URL pointed at, if any."""
    cached: bool = False
    download_bytes: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)
    """(path, reason) for archive entries that weren't unpacked."""

    @property
    def label(self) -> str:
        return f"{self.spec.slug}@{self.sha[:7]}"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def looks_like_remote(text: str) -> bool:
    """True if *text* is meant as a repository URL rather than a local path or command."""
    t = text.strip().lower()
    if t.startswith(("gh:", "git@", "http://", "https://")):
        return True
    t = t.removeprefix("www.")
    return t.startswith(tuple(f"{h}/" for h in (HOST, *_OTHER_HOSTS)))


def parse_target(text: str) -> RemoteSpec:
    """
    Parse a GitHub repository reference. Accepts:

        gh:owner/repo
        github.com/owner/repo           (with or without https://, www.)
        https://github.com/owner/repo.git
        https://github.com/owner/repo/tree/<ref>/<path>
        https://github.com/owner/repo/blob/<ref>/<file>
        git@github.com:owner/repo.git

    Raises RemoteError with a message for anything else.
    """
    raw = text.strip()
    if raw.lower().startswith("gh:"):
        rest = raw[3:]
    elif raw.lower().startswith("git@"):
        host, _, rest = raw[4:].partition(":")
        if host.lower() != HOST:
            raise RemoteError(_unsupported_host(host), "invalid")
    else:
        rest = re.sub(r"^https?://", "", raw, flags=re.IGNORECASE)
        if rest.lower().startswith("www."):
            rest = rest[4:]
        host, _, rest = rest.partition("/")
        if host.lower() != HOST:
            raise RemoteError(_unsupported_host(host), "invalid")

    rest = re.split(r"[?#]", rest, maxsplit=1)[0]
    segments = [s for s in rest.split("/") if s]
    if len(segments) < 2:
        raise RemoteError(f"Expected a repository like github.com/owner/repo, got {text!r}", "invalid")

    owner, repo = segments[0], segments[1]
    if repo.lower().endswith(".git"):
        repo = repo[:-4]
    for name in (owner, repo):
        if not _NAME_RE.match(name) or name in (".", ".."):
            raise RemoteError(f"{name!r} isn't a valid GitHub owner or repository name", "invalid")

    extra = segments[2:]
    if not extra:
        return RemoteSpec(owner, repo)
    if extra[0] in ("tree", "blob") and len(extra) > 1:
        return RemoteSpec(owner, repo, tuple(extra[1:]))
    raise RemoteError(
        f"Can't package github.com/{owner}/{repo}/{'/'.join(extra)} — "
        "use the repository URL, or a /tree/<branch>/<folder> link",
        "invalid",
    )


def _unsupported_host(host: str) -> str:
    return f"Only GitHub repositories are supported for now, not {host or 'that address'}"


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def cache_root() -> Path:
    """Where fetched repositories are kept (override with CONTEXTZIP_CACHE_DIR)."""
    env = os.environ.get(CACHE_ENV_VAR, "").strip()
    if env:
        return Path(env).expanduser() / "remote"
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA", "").strip()
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        return base / "contextzip" / "Cache" / "remote"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "contextzip" / "remote"
    xdg = os.environ.get("XDG_CACHE_HOME", "").strip()
    return (Path(xdg) if xdg else Path.home() / ".cache") / "contextzip" / "remote"


def _entry_dir(spec: RemoteSpec, sha: str, root: Path) -> Path:
    return root / HOST / spec.owner.lower() / spec.repo.lower() / sha[:12]


def _prune(repo_dir: Path, keep_entry: Path) -> None:
    """Keep the newest few cached commits of one repository."""
    try:
        entries = [p for p in repo_dir.iterdir() if p.is_dir()]
    except OSError:
        return

    def used(p: Path) -> float:
        try:
            return (p / _COMPLETE_MARKER).stat().st_mtime
        except OSError:
            return 0.0

    entries.sort(key=used, reverse=True)
    for old in entries[_KEEP_PER_REPO:]:
        if old != keep_entry:
            shutil.rmtree(old, ignore_errors=True)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def github_token() -> str | None:
    """A GitHub token from the environment, or from the gh CLI if it's signed in."""
    for var in ("GITHUB_TOKEN", "GH_TOKEN"):
        value = os.environ.get(var, "").strip()
        if value:
            return value
    gh = shutil.which("gh")
    if gh is None:
        return None
    try:
        out = subprocess.run([gh, "auth", "token"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    token = out.stdout.strip()
    return token if out.returncode == 0 and token else None


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------


def max_download_bytes() -> int:
    raw = os.environ.get(MAX_MB_ENV_VAR, "").strip()
    try:
        mb = float(raw) if raw else DEFAULT_MAX_MB
    except ValueError:
        mb = DEFAULT_MAX_MB
    return int(max(mb, 1) * 1024 * 1024)


def fetch(
    target: str | RemoteSpec,
    ref: str | None = None,
    *,
    token: str | None = None,
    use_gh_token: bool = True,
    max_bytes: int | None = None,
    public_only: bool | None = None,
    cache_dir: Path | None = None,
    transport=None,
) -> FetchedRemote:
    """
    Resolve *target* to one commit and make sure it's unpacked in the cache.

    *ref* (a branch, tag or commit) overrides the default branch; it can't
    be combined with a /tree/<ref>/ URL. *public_only* refuses private
    repositories (default: the CONTEXTZIP_REMOTE_PUBLIC_ONLY env var); a
    server fetching for other people must set it. *transport* is an httpx
    transport, for tests. Raises RemoteError with a user-facing message.
    """
    import httpx

    spec = parse_target(target) if isinstance(target, str) else target
    if ref and spec.tree:
        raise RemoteError("Give the branch either in the URL (/tree/<branch>) or with --ref, not both", "invalid")

    root = cache_dir or cache_root()
    if public_only is None:
        public_only = os.environ.get(PUBLIC_ONLY_ENV_VAR, "").strip().lower() in ("1", "true", "yes")
    if token is None and use_gh_token:
        token = github_token()
    limit = max_bytes or max_download_bytes()

    # A full SHA that's already cached needs no network at all (public-only
    # mode always asks GitHub: visibility can change).
    if ref and _SHA_RE.match(ref.lower()) and not spec.tree and not public_only:
        hit = _cached(spec, ref.lower(), ref, None, root)
        if hit is not None:
            return hit

    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": _user_agent()}
    api_headers = {**headers, **({"Authorization": f"Bearer {token}"} if token else {})}

    with httpx.Client(timeout=_TIMEOUT, transport=transport, headers=headers) as client:
        if public_only:
            _require_public(client, api_headers, spec, has_token=bool(token))
        sha, ref, subdir = _resolve(client, api_headers, spec, ref, has_token=bool(token))

        hit = _cached(spec, sha, ref, subdir, root)
        if hit is not None:
            return hit

        entry = _entry_dir(spec, sha, root)
        entry.mkdir(parents=True, exist_ok=True)
        archive = entry / f"download-{secrets.token_hex(4)}.tar.gz"
        try:
            size = _download(client, api_headers, spec, sha, archive, limit)
            project_dir = entry / spec.repo
            skipped = _unpack(archive, entry, project_dir, limit * _EXPANSION_LIMIT)
        finally:
            archive.unlink(missing_ok=True)

    marker = {"repo": spec.slug, "sha": sha, "ref": ref, "fetched": int(time.time()), "skipped": skipped}
    (entry / _COMPLETE_MARKER).write_text(json.dumps(marker, indent=2), encoding="utf-8")
    _prune(entry.parent, entry)

    fetched = _fetched(spec, sha, ref, subdir, entry, cached=False)
    fetched.download_bytes = size
    fetched.skipped = [(path, reason) for path, reason in skipped]
    return fetched


def _user_agent() -> str:
    from contextzip import __version__

    return f"contextzip/{__version__}"


def _cached(spec: RemoteSpec, sha: str, ref: str | None, subdir: str | None, root: Path) -> FetchedRemote | None:
    entry = _entry_dir(spec, sha, root)
    marker = entry / _COMPLETE_MARKER
    if not marker.is_file() or not (entry / spec.repo).is_dir():
        return None
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
        if data.get("sha") != sha:
            return None
        os.utime(marker)  # most recently used, for _prune
    except (OSError, ValueError):
        return None
    fetched = _fetched(spec, sha, ref, subdir, entry, cached=True)
    fetched.skipped = [(str(s[0]), str(s[1])) for s in data.get("skipped", []) if len(s) == 2]
    return fetched


def _fetched(spec: RemoteSpec, sha: str, ref: str | None, subdir: str | None, entry: Path, *, cached: bool) -> FetchedRemote:
    project_dir = entry / spec.repo
    if subdir and not (project_dir / subdir).exists():
        raise RemoteError(f"{subdir} doesn't exist in {spec.slug} at {sha[:7]}", "not_found")
    return FetchedRemote(
        spec=spec,
        sha=sha,
        ref=ref,
        project_dir=project_dir,
        zip_path=entry / f"{spec.repo}.zip",
        subdir=subdir,
        cached=cached,
    )


def _resolve(client, api_headers: dict, spec: RemoteSpec, ref: str | None, *, has_token: bool) -> tuple[str, str | None, str | None]:
    """(commit SHA, ref asked for, path inside the repo) for *spec*."""
    if not spec.tree:
        sha = _commit_sha(client, api_headers, spec, ref or "HEAD", has_token=has_token)
        if sha is None:
            _raise_not_found(client, api_headers, spec, ref, has_token=has_token)
        return sha, ref, None

    # /tree/feature/login/src: the ref is "feature" or "feature/login" or …
    # Git can't have both "feature" and "feature/login" as refs, so the
    # shortest prefix that resolves is the only one that can.
    for i in range(1, len(spec.tree) + 1):
        candidate = "/".join(spec.tree[:i])
        sha = _commit_sha(client, api_headers, spec, candidate, has_token=has_token)
        if sha is not None:
            path = "/".join(spec.tree[i:]) or None
            return sha, candidate, path
    _raise_not_found(client, api_headers, spec, "/".join(spec.tree), has_token=has_token)
    raise AssertionError("unreachable")


def _require_public(client, api_headers: dict, spec: RemoteSpec, *, has_token: bool) -> None:
    resp = _get(client, f"{API_URL}/repos/{spec.owner}/{spec.repo}", api_headers)
    if resp.status_code == 404:
        raise RemoteError(f"Repository {spec.slug} not found (only public repositories can be packaged here)", "not_found")
    _raise_for_status(resp, spec, has_token=has_token)
    try:
        private = bool(resp.json().get("private", True))
    except ValueError:
        private = True
    if private:
        raise RemoteError(
            f"{spec.slug} is private. Only public repositories can be packaged here; "
            "for a private one, use the contextzip CLI with your own GitHub token.",
            "private",
        )


def _commit_sha(client, api_headers: dict, spec: RemoteSpec, ref: str, *, has_token: bool) -> str | None:
    url = f"{API_URL}/repos/{spec.owner}/{spec.repo}/commits/{quote(ref, safe='')}"
    resp = _get(client, url, {**api_headers, "Accept": "application/vnd.github.sha"})
    if resp.status_code == 200:
        sha = resp.text.strip().lower()
        if _SHA_RE.match(sha):
            return sha
        raise RemoteError(f"GitHub returned an unexpected answer for {spec.slug}@{ref}")
    if resp.status_code in (404, 409, 422):
        # 404: no such repo/ref · 409: empty repository · 422: no such commit
        return None
    _raise_for_status(resp, spec, has_token=has_token)
    return None


def _raise_not_found(client, api_headers: dict, spec: RemoteSpec, ref: str | None, *, has_token: bool) -> None:
    resp = _get(client, f"{API_URL}/repos/{spec.owner}/{spec.repo}", api_headers)
    if resp.status_code == 404:
        hint = (
            "Check the name, and that your token can read it."
            if has_token
            else "If it's private, set GITHUB_TOKEN or sign in with `gh auth login`."
        )
        raise RemoteError(f"Repository {spec.slug} not found. {hint}", "not_found")
    _raise_for_status(resp, spec, has_token=has_token)
    if ref:
        raise RemoteError(f"No branch, tag or commit {ref!r} in {spec.slug}", "not_found")
    raise RemoteError(f"{spec.slug} has no commits yet", "not_found")


def _get(client, url: str, headers: dict):
    import httpx

    try:
        return client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise RemoteError(f"Couldn't reach GitHub: {exc}") from None


def _raise_for_status(resp, spec: RemoteSpec, *, has_token: bool) -> None:
    code = resp.status_code
    if code < 400:
        return
    if code == 401:
        raise RemoteError("GitHub rejected the token (401). Check GITHUB_TOKEN, or run `gh auth login` again.", "auth")
    if code in (403, 429) and resp.headers.get("x-ratelimit-remaining") == "0":
        reset = resp.headers.get("x-ratelimit-reset", "")
        when = ""
        if reset.isdigit():
            minutes = max(1, round((int(reset) - time.time()) / 60))
            when = f" Try again in about {minutes} minute{'s' if minutes != 1 else ''}."
        hint = "" if has_token else " Set GITHUB_TOKEN or sign in with `gh auth login` for a higher limit."
        raise RemoteError(f"GitHub's API rate limit was reached.{when}{hint}", "rate_limited")
    if code == 403:
        raise RemoteError(f"GitHub refused access to {spec.slug} (403).", "auth")
    raise RemoteError(f"GitHub returned {code} for {spec.slug}")


def _download(client, api_headers: dict, spec: RemoteSpec, sha: str, dest: Path, limit: int) -> int:
    """Download the tarball for *sha* to *dest*. Returns its size in bytes."""
    import httpx

    url = f"{API_URL}/repos/{spec.owner}/{spec.repo}/tarball/{sha}"
    resp = _get(client, url, api_headers)
    if resp.status_code in (301, 302, 303, 307, 308):
        location = resp.headers.get("location")
        if not location:
            raise RemoteError("GitHub sent a download redirect without a location")
        # The redirect target is a signed URL on another host: never send the token there.
        download_headers: dict = {}
    elif resp.status_code == 200:
        location, download_headers = url, api_headers
    else:
        _raise_for_status(resp, spec, has_token="Authorization" in api_headers)
        raise RemoteError(f"GitHub returned {resp.status_code} for the {spec.slug} download")

    written = 0
    try:
        with client.stream("GET", location, headers=download_headers, follow_redirects=True) as stream:
            if stream.status_code != 200:
                raise RemoteError(f"Downloading {spec.slug} failed ({stream.status_code})")
            declared = stream.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > limit:
                raise RemoteError(_too_big(spec, limit), "too_big")
            with dest.open("wb") as fh:
                for chunk in stream.iter_bytes():
                    written += len(chunk)
                    if written > limit:
                        raise RemoteError(_too_big(spec, limit), "too_big")
                    fh.write(chunk)
    except httpx.HTTPError as exc:
        raise RemoteError(f"Downloading {spec.slug} failed: {exc}") from None
    return written


def _too_big(spec: RemoteSpec, limit: int) -> str:
    mb = limit / (1024 * 1024)
    return (
        f"{spec.slug} is larger than the {mb:.0f} MB download limit. "
        f"Raise it with {MAX_MB_ENV_VAR}, or clone it and package locally."
    )


# ---------------------------------------------------------------------------
# Unpacking
# ---------------------------------------------------------------------------


def _unsafe_reason(parts: tuple[str, ...]) -> str | None:
    """Why this path can't be written here, or None if it's fine."""
    for part in parts:
        if part in ("", ".", "..") or "\\" in part or "\x00" in part:
            return "unsafe path"
        if os.name == "nt":
            stem = part.split(".", 1)[0].lower()
            if (
                stem in _WINDOWS_RESERVED
                or any(c in _WINDOWS_BAD_CHARS or ord(c) < 32 for c in part)
                or part.endswith((" ", "."))
            ):
                return "name not allowed on Windows"
    return None


def _unpack(archive: Path, entry: Path, project_dir: Path, max_total: int) -> list[list[str]]:
    """
    Unpack a GitHub tarball into *project_dir*, dropping its top folder
    (owner-repo-sha/). Only regular files and folders are written; links,
    unsafe paths and .git/.contextzip are skipped. Unpacks next to the
    destination first and renames, so a half-written tree is never used.
    Returns [path, reason] pairs for what was skipped.
    """
    staging = entry / f"unpack-{secrets.token_hex(4)}"
    staging.mkdir(parents=True)
    skipped: list[list[str]] = []
    total = 0
    try:
        try:
            tar = tarfile.open(archive, mode="r:gz")
        except (tarfile.TarError, OSError) as exc:
            raise RemoteError(f"The download isn't a valid archive: {exc}") from None
        with tar:
            for member in tar:
                parts = PurePosixPath(member.name).parts[1:]
                if not parts:
                    continue
                rel = "/".join(parts)
                if member.name.startswith("/") or parts[0] in _SKIP_TOP_DIRS:
                    continue
                if member.issym() or member.islnk():
                    skipped.append([rel, "link"])
                    continue
                if not (member.isfile() or member.isdir()):
                    continue
                reason = _unsafe_reason(parts)
                if reason:
                    skipped.append([rel, reason])
                    continue
                dest = staging.joinpath(*parts)
                if member.isdir():
                    dest.mkdir(parents=True, exist_ok=True)
                    continue
                total += member.size
                if total > max_total:
                    raise RemoteError(
                        f"The unpacked repository is larger than {max_total // (1024 * 1024)} MB. "
                        "Clone it and package locally instead.",
                        "too_big",
                    )
                src = tar.extractfile(member)
                if src is None:
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with src, dest.open("wb") as fh:
                        shutil.copyfileobj(src, fh)
                except OSError as exc:
                    skipped.append([rel, f"couldn't write: {exc.strerror or exc}"])
        if project_dir.exists():
            shutil.rmtree(project_dir, ignore_errors=True)
        staging.rename(project_dir)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return skipped
