"""
remote.py — Package a GitHub, GitLab or Bitbucket repository without cloning it.

`contextzip github.com/user/repo` (or `gh:user/repo`, gitlab.com/…,
bitbucket.org/…) asks the host's API which commit to use, downloads that
commit's tarball and unpacks it into a local cache. From there it's an ordinary project: detection, exclusion
rules and redaction all apply unchanged. No git needed.

    <cache>/<host>/<owner>/<repo>/<sha12>/
      <repo>/          the unpacked tree, which becomes the project root
      <repo>.zip       where the CLI writes the ZIP (+ its report)
      .complete        written last; an entry without it is re-fetched

A commit never changes, so a cached entry is reused as-is: the only network
call on a cache hit is resolving the branch to a commit, and none at all
when you pass a full commit SHA.

Private repositories work with a token: GITHUB_TOKEN or GH_TOKEN (or the
one `gh auth token` prints), GITLAB_TOKEN, or BITBUCKET_TOKEN. A token is
only sent to its own host's API; when the download is a redirect to a
signed URL elsewhere, that's fetched without it.
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

# Short forms: gh:owner/repo, gl:group/project, bb:workspace/repo.
_SHORTHANDS = {"gh": "github.com", "gl": "gitlab.com", "bb": "bitbucket.org"}
# Hosts recognised only to say they aren't supported yet.
_OTHER_HOSTS = ("codeberg.org",)


class RemoteError(Exception):
    """
    A repository couldn't be fetched. The message is shown as-is; *kind*
    says why, for callers that map it to something (an HTTP status):
    invalid, not_found, private, too_big, rate_limited, auth or unavailable.
    """

    def __init__(self, message: str, kind: str = "unavailable"):
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class RemoteSpec:
    """A repository named on the command line, before anything is fetched."""

    owner: str
    """The user, organisation or workspace; on GitLab it can include
    subgroups ("group/subgroup")."""
    repo: str
    tree: tuple[str, ...] = ()
    """Path segments after /tree/, /blob/ or /src/ in a URL: a ref, then maybe
    a path. Which segments are the ref is only known once the host is asked,
    because branch names can contain slashes (see `fetch`)."""
    host: str = HOST

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def host_name(self) -> str:
        return _provider(self.host).name


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
    if t.startswith(("git@", "http://", "https://")) or any(t.startswith(f"{s}:") for s in _SHORTHANDS):
        return True
    t = t.removeprefix("www.")
    return t.startswith(tuple(f"{h}/" for h in (*_SHORTHANDS.values(), *_OTHER_HOSTS)))


def parse_target(text: str) -> RemoteSpec:
    """
    Parse a repository reference. Accepts, for GitHub:

        gh:owner/repo
        github.com/owner/repo           (with or without https://, www.)
        https://github.com/owner/repo.git
        https://github.com/owner/repo/tree/<ref>/<path>   (or /blob/)
        git@github.com:owner/repo.git

    and the same shapes for GitLab (gl:, gitlab.com/group[/subgroup]/project,
    …/-/tree/<ref>/<path>) and Bitbucket (bb:, bitbucket.org/workspace/repo,
    …/src/<ref>/<path>). Raises RemoteError with a message for anything else.
    """
    raw = text.strip()
    prefix, colon, after = raw.partition(":")
    if colon and prefix.lower() in _SHORTHANDS:
        host, rest = _SHORTHANDS[prefix.lower()], after
    elif raw.lower().startswith("git@"):
        host, _, rest = raw[4:].partition(":")
    else:
        rest = re.sub(r"^https?://", "", raw, flags=re.IGNORECASE)
        if rest.lower().startswith("www."):
            rest = rest[4:]
        host, _, rest = rest.partition("/")
    host = host.lower()
    if host not in _PROVIDERS:
        raise RemoteError(
            f"GitHub, GitLab and Bitbucket repositories are supported, not {host or 'that address'}",
            "invalid",
        )

    rest = re.split(r"[?#]", rest, maxsplit=1)[0]
    segments = [s for s in rest.split("/") if s]
    spec = _provider(host).split(segments, text)
    for name in (*spec.owner.split("/"), spec.repo):
        if not _NAME_RE.match(name) or name in (".", ".."):
            raise RemoteError(f"{name!r} isn't a valid {_provider(host).name} owner or repository name", "invalid")
    return spec


def _strip_git(repo: str) -> str:
    return repo[:-4] if repo.lower().endswith(".git") else repo


# ---------------------------------------------------------------------------
# Hosts
# ---------------------------------------------------------------------------


class _Provider:
    """What differs between hosts: URLs, auth, and how they answer."""

    name = ""
    domain = ""
    api = ""

    def split(self, segments: list[str], text: str) -> RemoteSpec:
        raise NotImplementedError

    def token(self) -> str | None:
        raise NotImplementedError

    def auth_headers(self, token: str | None) -> dict:
        return {"Authorization": f"Bearer {token}"} if token else {}

    def repo_url(self, spec: RemoteSpec) -> str:
        raise NotImplementedError

    def commit_url(self, spec: RemoteSpec, ref: str) -> str:
        raise NotImplementedError

    def parse_commit(self, resp) -> str | None:
        raise NotImplementedError

    def default_ref(self, client, headers: dict, spec: RemoteSpec, *, has_token: bool) -> str:
        """The ref that means "the default branch" in commit_url."""
        return "HEAD"

    def is_private(self, info: dict) -> bool:
        raise NotImplementedError

    def archive_url(self, spec: RemoteSpec, sha: str) -> str:
        raise NotImplementedError

    def token_hint(self) -> str:
        raise NotImplementedError

    def not_found_codes(self) -> tuple[int, ...]:
        return (404,)


class _GitHub(_Provider):
    name, domain, api = "GitHub", "github.com", "https://api.github.com"

    def split(self, segments, text):
        if len(segments) < 2:
            raise RemoteError(f"Expected a repository like github.com/owner/repo, got {text!r}", "invalid")
        owner, repo, extra = segments[0], _strip_git(segments[1]), segments[2:]
        if not extra:
            return RemoteSpec(owner, repo)
        if extra[0] in ("tree", "blob") and len(extra) > 1:
            return RemoteSpec(owner, repo, tuple(extra[1:]))
        raise RemoteError(
            f"Can't package github.com/{owner}/{repo}/{'/'.join(extra)} — "
            "use the repository URL, or a /tree/<branch>/<folder> link",
            "invalid",
        )

    def token(self):
        return github_token()

    def auth_headers(self, token):
        base = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        return {**base, **({"Authorization": f"Bearer {token}"} if token else {})}

    def repo_url(self, spec):
        return f"{self.api}/repos/{spec.owner}/{spec.repo}"

    def commit_url(self, spec, ref):
        return f"{self.repo_url(spec)}/commits/{quote(ref, safe='')}"

    def commit_headers(self) -> dict:
        return {"Accept": "application/vnd.github.sha"}

    def parse_commit(self, resp):
        return resp.text.strip().lower()

    def is_private(self, info):
        return bool(info.get("private", True))

    def archive_url(self, spec, sha):
        return f"{self.repo_url(spec)}/tarball/{sha}"

    def token_hint(self):
        return "set GITHUB_TOKEN or sign in with `gh auth login`"

    def not_found_codes(self):
        # 404: no such repo/ref · 409: empty repository · 422: no such commit
        return (404, 409, 422)


class _GitLab(_Provider):
    name, domain, api = "GitLab", "gitlab.com", "https://gitlab.com/api/v4"

    def split(self, segments, text):
        path, extra = segments, []
        if "-" in segments:
            i = segments.index("-")
            path, extra = segments[:i], segments[i + 1:]
        if len(path) < 2:
            raise RemoteError(f"Expected a project like gitlab.com/group/project, got {text!r}", "invalid")
        owner, repo = "/".join(path[:-1]), _strip_git(path[-1])
        if not extra:
            return RemoteSpec(owner, repo, host=self.domain)
        if extra[0] in ("tree", "blob") and len(extra) > 1:
            return RemoteSpec(owner, repo, tuple(extra[1:]), host=self.domain)
        raise RemoteError(
            f"Can't package gitlab.com/{owner}/{repo}/-/{'/'.join(extra)} — "
            "use the project URL, or a /-/tree/<branch>/<folder> link",
            "invalid",
        )

    def token(self):
        return os.environ.get("GITLAB_TOKEN", "").strip() or None

    def auth_headers(self, token):
        return {"PRIVATE-TOKEN": token} if token else {}

    def repo_url(self, spec):
        return f"{self.api}/projects/{quote(spec.slug, safe='')}"

    def commit_url(self, spec, ref):
        return f"{self.repo_url(spec)}/repository/commits/{quote(ref, safe='')}"

    def parse_commit(self, resp):
        return str(resp.json().get("id", "")).lower()

    def is_private(self, info):
        return info.get("visibility", "private") != "public"

    def archive_url(self, spec, sha):
        return f"{self.repo_url(spec)}/repository/archive.tar.gz?sha={sha}"

    def token_hint(self):
        return "set GITLAB_TOKEN to a token with read_repository"


class _Bitbucket(_Provider):
    name, domain, api = "Bitbucket", "bitbucket.org", "https://api.bitbucket.org/2.0"

    def split(self, segments, text):
        if len(segments) < 2:
            raise RemoteError(f"Expected a repository like bitbucket.org/workspace/repo, got {text!r}", "invalid")
        owner, repo, extra = segments[0], _strip_git(segments[1]), segments[2:]
        if not extra:
            return RemoteSpec(owner, repo, host=self.domain)
        if extra[0] == "src" and len(extra) > 1:
            return RemoteSpec(owner, repo, tuple(extra[1:]), host=self.domain)
        raise RemoteError(
            f"Can't package bitbucket.org/{owner}/{repo}/{'/'.join(extra)} — "
            "use the repository URL, or a /src/<branch>/<folder> link",
            "invalid",
        )

    def token(self):
        return os.environ.get("BITBUCKET_TOKEN", "").strip() or None

    def repo_url(self, spec):
        return f"{self.api}/repositories/{spec.owner}/{spec.repo}"

    def commit_url(self, spec, ref):
        return f"{self.repo_url(spec)}/commit/{quote(ref, safe='')}"

    def parse_commit(self, resp):
        return str(resp.json().get("hash", "")).lower()

    def default_ref(self, client, headers, spec, *, has_token):
        resp = _get(client, self.repo_url(spec), headers, self)
        if resp.status_code == 404:
            _raise_not_found(client, headers, spec, None, has_token=has_token)
        _raise_for_status(resp, spec, has_token=has_token)
        try:
            return str(resp.json()["mainbranch"]["name"])
        except (ValueError, KeyError, TypeError):
            raise RemoteError(f"{spec.slug} has no main branch yet", "not_found") from None

    def is_private(self, info):
        return bool(info.get("is_private", True))

    def archive_url(self, spec, sha):
        return f"https://bitbucket.org/{spec.owner}/{spec.repo}/get/{sha}.tar.gz"

    def token_hint(self):
        return "set BITBUCKET_TOKEN to a repository or workspace access token"


_PROVIDERS: dict[str, _Provider] = {p.domain: p for p in (_GitHub(), _GitLab(), _Bitbucket())}


def _provider(host: str) -> _Provider:
    return _PROVIDERS[host]


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
    return root / spec.host / Path(*spec.owner.lower().split("/")) / spec.repo.lower() / sha[:12]


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
    be combined with a /tree/<ref>/ URL. *token* overrides the host's usual
    token (GITHUB_TOKEN / gh, GITLAB_TOKEN, BITBUCKET_TOKEN); *use_gh_token*
    False uses none unless given. *public_only* refuses private
    repositories (default: the CONTEXTZIP_REMOTE_PUBLIC_ONLY env var); a
    server fetching for other people must set it. *transport* is an httpx
    transport, for tests. Raises RemoteError with a user-facing message.
    """
    import httpx

    spec = parse_target(target) if isinstance(target, str) else target
    provider = _provider(spec.host)
    if ref and spec.tree:
        raise RemoteError("Give the branch either in the URL or with --ref, not both", "invalid")

    root = cache_dir or cache_root()
    if public_only is None:
        public_only = os.environ.get(PUBLIC_ONLY_ENV_VAR, "").strip().lower() in ("1", "true", "yes")
    if token is None and use_gh_token:
        token = provider.token()
    limit = max_bytes or max_download_bytes()

    # A full SHA that's already cached needs no network at all (public-only
    # mode always asks the host: visibility can change).
    if ref and _SHA_RE.match(ref.lower()) and not spec.tree and not public_only:
        hit = _cached(spec, ref.lower(), ref, None, root)
        if hit is not None:
            return hit

    headers = {"User-Agent": _user_agent()}
    api_headers = {**headers, **provider.auth_headers(token)}

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

    marker = {"host": spec.host, "repo": spec.slug, "sha": sha, "ref": ref, "fetched": int(time.time()), "skipped": skipped}
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
    provider = _provider(spec.host)
    if not spec.tree:
        lookup = ref or provider.default_ref(client, api_headers, spec, has_token=has_token)
        sha = _commit_sha(client, api_headers, spec, lookup, has_token=has_token)
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
    provider = _provider(spec.host)
    resp = _get(client, provider.repo_url(spec), api_headers, provider)
    if resp.status_code == 404:
        raise RemoteError(f"Repository {spec.slug} not found (only public repositories can be packaged here)", "not_found")
    _raise_for_status(resp, spec, has_token=has_token)
    try:
        private = provider.is_private(resp.json())
    except (ValueError, AttributeError):
        private = True
    if private:
        raise RemoteError(
            f"{spec.slug} is private. Only public repositories can be packaged here; "
            f"for a private one, use the contextzip CLI with your own {provider.name} token.",
            "private",
        )


def _commit_sha(client, api_headers: dict, spec: RemoteSpec, ref: str, *, has_token: bool) -> str | None:
    provider = _provider(spec.host)
    extra = provider.commit_headers() if isinstance(provider, _GitHub) else {}
    resp = _get(client, provider.commit_url(spec, ref), {**api_headers, **extra}, provider)
    if resp.status_code == 200:
        try:
            sha = provider.parse_commit(resp)
        except (ValueError, AttributeError):
            sha = ""
        if _SHA_RE.match(sha):
            return sha
        raise RemoteError(f"{provider.name} returned an unexpected answer for {spec.slug}@{ref}")
    if resp.status_code in provider.not_found_codes():
        return None
    _raise_for_status(resp, spec, has_token=has_token)
    return None


def _raise_not_found(client, api_headers: dict, spec: RemoteSpec, ref: str | None, *, has_token: bool) -> None:
    provider = _provider(spec.host)
    resp = _get(client, provider.repo_url(spec), api_headers, provider)
    if resp.status_code in (401, 403, 404) and not has_token:
        raise RemoteError(f"Repository {spec.slug} not found. If it's private, {provider.token_hint()}.", "not_found")
    if resp.status_code == 404:
        raise RemoteError(f"Repository {spec.slug} not found. Check the name, and that your token can read it.", "not_found")
    _raise_for_status(resp, spec, has_token=has_token)
    if ref:
        raise RemoteError(f"No branch, tag or commit {ref!r} in {spec.slug}", "not_found")
    raise RemoteError(f"{spec.slug} has no commits yet", "not_found")


def _get(client, url: str, headers: dict, provider: _Provider | None = None):
    import httpx

    try:
        return client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise RemoteError(f"Couldn't reach {(provider or _PROVIDERS[HOST]).name}: {exc}") from None


def _raise_for_status(resp, spec: RemoteSpec, *, has_token: bool) -> None:
    code = resp.status_code
    if code < 400:
        return
    provider = _provider(spec.host)
    if code == 401:
        raise RemoteError(f"{provider.name} rejected the token (401). To use another, {provider.token_hint()}.", "auth")
    limited = code == 429 or (code == 403 and resp.headers.get("x-ratelimit-remaining") == "0")
    if limited:
        reset = resp.headers.get("x-ratelimit-reset", "") or resp.headers.get("ratelimit-reset", "")
        when = ""
        if reset.isdigit():
            minutes = max(1, round((int(reset) - time.time()) / 60))
            when = f" Try again in about {minutes} minute{'s' if minutes != 1 else ''}."
        hint = "" if has_token else f" For a higher limit, {provider.token_hint()}."
        raise RemoteError(f"{provider.name}'s API rate limit was reached.{when}{hint}", "rate_limited")
    if code == 403:
        raise RemoteError(f"{provider.name} refused access to {spec.slug} (403).", "auth")
    raise RemoteError(f"{provider.name} returned {code} for {spec.slug}")


def _download(client, api_headers: dict, spec: RemoteSpec, sha: str, dest: Path, limit: int) -> int:
    """Download the tarball for *sha* to *dest*. Returns its size in bytes."""
    import httpx

    provider = _provider(spec.host)
    url = provider.archive_url(spec, sha)
    has_token = any(k in api_headers for k in ("Authorization", "PRIVATE-TOKEN"))
    try:
        with client.stream("GET", url, headers=api_headers, follow_redirects=False) as resp:
            if resp.status_code == 200:
                return _write_limited(resp, spec, dest, limit)
            resp.read()
            if resp.status_code not in (301, 302, 303, 307, 308):
                _raise_for_status(resp, spec, has_token=has_token)
                raise RemoteError(f"{provider.name} returned {resp.status_code} for the {spec.slug} download")
            location = resp.headers.get("location")
            if not location:
                raise RemoteError(f"{provider.name} sent a download redirect without a location")
            target = resp.url.join(location)
        # The redirect target is a signed URL, usually on another host: the
        # token is only ever sent to the API that issued it.
        with client.stream("GET", target, follow_redirects=True) as stream:
            if stream.status_code != 200:
                raise RemoteError(f"Downloading {spec.slug} failed ({stream.status_code})")
            return _write_limited(stream, spec, dest, limit)
    except httpx.HTTPError as exc:
        raise RemoteError(f"Downloading {spec.slug} failed: {exc}") from None


def _write_limited(stream, spec: RemoteSpec, dest: Path, limit: int) -> int:
    declared = stream.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise RemoteError(_too_big(spec, limit), "too_big")
    written = 0
    with dest.open("wb") as fh:
        for chunk in stream.iter_bytes():
            written += len(chunk)
            if written > limit:
                raise RemoteError(_too_big(spec, limit), "too_big")
            fh.write(chunk)
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
