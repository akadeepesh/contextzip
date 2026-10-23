"""
pullrequest.py — Apply an AI's reply to a GitHub repository as a pull request.

The other half of `contextzip github.com/user/repo`: once the AI answers,

    contextzip apply-zip reply.zip --pr

finds the cached copy of the repository the reply answers (by the snapshot
its CONTEXTZIP.md names, or `--from REPO` and the closest match by
content), works out what to change exactly as apply-zip does, and opens a
pull request. No clone and no git: the commit is built with GitHub's API —
a blob per file, a tree on top of the commit that was packaged, a commit, a
branch — so the pull request shows precisely the AI's changes against what
it was given. If you can't push to the repository, it's forked first.

Only safe changes go in (new, modified and moved files). Files the AI cut
short or that can't be matched are listed in the pull request instead of
committed, and removals still need --remove-moved / --allow-delete.
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from contextzip import remote
from contextzip.remote import RemoteError, RemoteSpec

# Tests replace this with an httpx.MockTransport.
_transport = None
_FORK_WAIT_SECONDS = 60


@dataclass
class CachedRepo:
    """A repository packaged with `contextzip <url>`, as it sits in the cache."""

    spec: RemoteSpec
    sha: str
    ref: str | None
    project_dir: Path
    snapshot: object | None = None
    """The snapshots.Snapshot the reply answers, when its CONTEXTZIP.md names one."""


@dataclass
class Change:
    path: str
    content: bytes | None
    """None deletes the file."""


@dataclass
class PullRequest:
    url: str
    number: int
    branch: str
    repo: str
    """Where the branch was pushed: the repository itself, or your fork."""
    forked: bool = False
    files: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Finding what the reply answers
# ---------------------------------------------------------------------------


def _cached_entries(root: Path) -> list[tuple[dict, Path]]:
    """(marker, project_dir) for every complete cached commit, newest first."""
    found = []
    for marker in root.glob("*/**/.complete"):
        try:
            data = json.loads(marker.read_text(encoding="utf-8"))
            mtime = marker.stat().st_mtime
        except (OSError, ValueError):
            continue
        repo = str(data.get("repo", "")).rsplit("/", 1)[-1]
        project_dir = marker.parent / repo
        if repo and project_dir.is_dir():
            found.append((mtime, data, project_dir))
    found.sort(key=lambda t: t[0], reverse=True)
    return [(data, project_dir) for _mtime, data, project_dir in found]


def find_cached_repo(zip_path: Path, repo: str | None = None, *, cache_dir: Path | None = None) -> CachedRepo:
    """
    The cached repository *zip_path* answers. With *repo*, only that
    repository is considered, and without a snapshot named in the reply its
    most recently used commit is taken.
    """
    from contextzip import snapshots
    from contextzip.guide import read_guide_from_zip

    wanted = remote.parse_target(repo) if repo else None
    guide = read_guide_from_zip(zip_path)
    label = guide.snapshot if guide else None
    if not label and not wanted:
        # Guessing here could open a pull request on the wrong repository.
        raise RemoteError(
            "The reply doesn't name a snapshot (no CONTEXTZIP.md in it). Say which repository it's for with --from.",
            "invalid",
        )
    root = cache_dir or remote.cache_root()

    for data, project_dir in _cached_entries(root):
        host = data.get("host", remote.HOST)
        if wanted and (host != wanted.host or str(data.get("repo", "")).lower() != wanted.slug.lower()):
            continue
        snap = snapshots.find(project_dir, label) if label else None
        if label and snap is None:
            continue
        owner, _, name = str(data["repo"]).rpartition("/")
        return CachedRepo(
            spec=RemoteSpec(owner, name, host=host),
            sha=str(data["sha"]),
            ref=data.get("ref"),
            project_dir=project_dir,
            snapshot=snap,
        )

    if label:
        where = f" of {wanted.slug}" if wanted else ""
        raise RemoteError(
            f"No packaged repository{where} has snapshot {label}. Package it with "
            "`contextzip github.com/user/repo` on this machine first, then apply the reply.",
            "not_found",
        )
    raise RemoteError(f"{wanted.slug} hasn't been packaged on this machine yet.", "not_found")


def changes_from_plan(plan, *, remove_moved: bool = False, allow_delete: bool = False) -> tuple[list[Change], list[tuple[str, str]]]:
    """(what to commit, [(path, why it's left out)]) for an apply plan."""
    from contextzip.applier import STATUS_REASONS, FileStatus

    changes: list[Change] = []
    left_out: list[tuple[str, str]] = []
    for e in plan.entries:
        if e.status == FileStatus.UNCHANGED:
            continue
        if e.is_safe and e.content is not None:
            changes.append(Change(e.rel_path, e.content))
            if e.status == FileStatus.MOVED and e.moved_from:
                if remove_moved and e.removable:
                    changes.append(Change(e.moved_from, None))
                else:
                    left_out.append((e.moved_from, "old copy of a moved file, kept (use --remove-moved)"))
        elif e.status == FileStatus.DELETE_REQUESTED:
            if allow_delete and e.removable:
                changes.append(Change(e.rel_path, None))
            else:
                left_out.append((e.rel_path, "the AI asked to delete it (use --allow-delete)"))
        else:
            left_out.append((e.display_path, STATUS_REASONS.get(e.status, e.status.value)))
    return changes, left_out


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------


class _GitHub:
    def __init__(self, token: str):
        import httpx

        self.client = httpx.Client(
            base_url=remote.API_URL,
            timeout=60.0,
            transport=_transport,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Authorization": f"Bearer {token}",
                "User-Agent": remote._user_agent(),
            },
        )

    def call(self, method: str, path: str, spec: RemoteSpec, *, ok=(200, 201), **kwargs):
        import httpx

        try:
            resp = self.client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise RemoteError(f"Couldn't reach GitHub: {exc}") from None
        if resp.status_code not in ok:
            remote._raise_for_status(resp, spec, has_token=True)
            detail = ""
            try:
                detail = resp.json().get("message", "")
            except ValueError:
                pass
            raise RemoteError(f"GitHub returned {resp.status_code} for {method} {path}{f': {detail}' if detail else ''}")
        return resp

    def close(self) -> None:
        self.client.close()


def open_pull_request(
    spec: RemoteSpec,
    base_sha: str,
    base_branch: str | None,
    changes: list[Change],
    *,
    title: str,
    body: str,
    branch: str,
    token: str,
    message: str | None = None,
) -> PullRequest:
    """Commit *changes* on top of *base_sha* in a new branch and open a pull request."""
    if spec.host != remote.HOST:
        raise RemoteError(f"Pull requests are only supported on GitHub for now, not {spec.host_name}.", "invalid")
    if not changes:
        raise RemoteError("Nothing to commit: the reply has no changes that can be applied safely.", "invalid")

    gh = _GitHub(token)
    try:
        info = gh.call("GET", f"/repos/{spec.slug}", spec).json()
        base = info.get("default_branch") or "main"
        if base_branch:
            # It was packaged from a branch: target that one. A tag or a commit
            # isn't a branch, so those go to the default branch.
            from urllib.parse import quote

            if gh.client.get(f"/repos/{spec.slug}/branches/{quote(base_branch, safe='')}").status_code == 200:
                base = base_branch
        can_push = bool((info.get("permissions") or {}).get("push"))
        target = spec.slug
        if not can_push:
            target = _fork(gh, spec, base_sha)

        base_tree = gh.call("GET", f"/repos/{target}/git/commits/{base_sha}", spec).json()["tree"]["sha"]
        modes = _modes(gh, spec, target, base_tree, [c.path for c in changes])
        entries = []
        for change in changes:
            if change.content is None:
                entries.append({"path": change.path, "mode": modes.get(change.path, "100644"), "type": "blob", "sha": None})
                continue
            blob = gh.call(
                "POST",
                f"/repos/{target}/git/blobs",
                spec,
                json={"content": base64.b64encode(change.content).decode("ascii"), "encoding": "base64"},
            ).json()["sha"]
            entries.append({"path": change.path, "mode": modes.get(change.path, "100644"), "type": "blob", "sha": blob})
        tree = gh.call("POST", f"/repos/{target}/git/trees", spec, json={"base_tree": base_tree, "tree": entries}).json()["sha"]
        commit = gh.call(
            "POST",
            f"/repos/{target}/git/commits",
            spec,
            json={"message": message or title, "tree": tree, "parents": [base_sha]},
        ).json()["sha"]
        branch = _create_branch(gh, spec, target, branch, commit)
        head = branch if target == spec.slug else f"{target.split('/')[0]}:{branch}"
        pr = gh.call(
            "POST",
            f"/repos/{spec.slug}/pulls",
            spec,
            json={"title": title, "head": head, "base": base, "body": body, "maintainer_can_modify": True},
        ).json()
        return PullRequest(
            url=pr["html_url"],
            number=pr["number"],
            branch=branch,
            repo=target,
            forked=target != spec.slug,
            files=[c.path for c in changes],
        )
    finally:
        gh.close()


def _fork(gh: _GitHub, spec: RemoteSpec, base_sha: str) -> str:
    """Fork *spec* (or reuse your existing fork) and wait until it has *base_sha*."""
    fork = gh.call("POST", f"/repos/{spec.slug}/forks", spec, ok=(200, 202), json={"default_branch_only": False}).json()
    name = fork["full_name"]
    deadline = time.monotonic() + _FORK_WAIT_SECONDS
    while True:
        resp = gh.client.get(f"/repos/{name}/git/commits/{base_sha}")
        if resp.status_code == 200:
            return name
        if time.monotonic() > deadline:
            raise RemoteError(f"Your fork {name} isn't ready yet. Try again in a minute.", "unavailable")
        time.sleep(2)


def _modes(gh: _GitHub, spec: RemoteSpec, target: str, tree_sha: str, paths: list[str]) -> dict[str, str]:
    """Existing files keep their mode (an executable script stays executable)."""
    try:
        tree = gh.call("GET", f"/repos/{target}/git/trees/{tree_sha}", spec, params={"recursive": "1"}).json()
    except RemoteError:
        return {}
    wanted = set(paths)
    return {t["path"]: t["mode"] for t in tree.get("tree", []) if t.get("path") in wanted and t.get("type") == "blob"}


def _create_branch(gh: _GitHub, spec: RemoteSpec, target: str, branch: str, commit: str) -> str:
    for attempt in range(1, 20):
        name = branch if attempt == 1 else f"{branch}-{attempt}"
        resp = gh.client.post(f"/repos/{target}/git/refs", json={"ref": f"refs/heads/{name}", "sha": commit})
        if resp.status_code == 201:
            return name
        if resp.status_code != 422:  # 422: that branch already exists
            remote._raise_for_status(resp, spec, has_token=True)
            raise RemoteError(f"GitHub returned {resp.status_code} creating branch {name}")
    raise RemoteError(f"Couldn't find a free branch name like {branch}")


def pr_text(repo: CachedRepo, plan, changes: list[Change], left_out: list[tuple[str, str]]) -> tuple[str, str]:
    """(title, body) for the pull request."""
    written = [c for c in changes if c.content is not None]
    removed = [c for c in changes if c.content is None]
    n = len(written)
    title = f"Apply AI changes to {n} file{'s' if n != 1 else ''}"
    lines = [
        f"These changes came back from an AI chat as a ZIP and were applied with `contextzip apply-zip --pr`, "
        f"against `{repo.spec.slug}@{repo.sha[:7]}`, the commit that was packaged"
        + (f" (snapshot {plan.snapshot})." if getattr(plan, "snapshot", None) else "."),
        "",
        "**Changed**",
        *[f"- `{c.path}`" for c in written],
    ]
    if removed:
        lines += ["", "**Removed**", *[f"- `{c.path}`" for c in removed]]
    if left_out:
        lines += ["", "**Not included** (review these yourself)", *[f"- `{p}`: {why}" for p, why in left_out]]
    lines += ["", "Review the diff before merging: it's exactly what the AI returned, nothing was run or tested."]
    return title, "\n".join(lines)
