from __future__ import annotations

import io
import json
import os
import tarfile
from unittest import mock

import httpx

from contextzip import remote
from contextzip.remote import RemoteError, RemoteSpec, fetch, looks_like_remote, parse_target
from tests.helpers import TempProject

SHA = "a" * 40
OTHER_SHA = "b" * 40


def make_tarball(files: dict[str, bytes], top: str = "octo-demo-aaaaaaa", links: dict[str, str] | None = None) -> bytes:
    """A GitHub-style tarball: everything under one top-level folder."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        root = tarfile.TarInfo(top)
        root.type = tarfile.DIRTYPE
        tar.addfile(root)
        for name, data in files.items():
            info = tarfile.TarInfo(f"{top}/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        for name, target in (links or {}).items():
            info = tarfile.TarInfo(f"{top}/{name}")
            info.type = tarfile.SYMTYPE
            info.linkname = target
            tar.addfile(info)
    return buf.getvalue()


class FakeGitHub:
    """Just enough of api.github.com + its download host for remote.fetch."""

    def __init__(self, tarball: bytes, refs: dict[str, str] | None = None, repo_exists: bool = True, private: bool = False):
        self.tarball = tarball
        self.refs = refs if refs is not None else {"HEAD": SHA, "main": SHA}
        self.repo_exists = repo_exists
        self.private = private
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.raw_path.decode()
        if request.url.host == "codeload.example":
            return httpx.Response(200, content=self.tarball)
        prefix = "/repos/octo/demo"
        if path.startswith(prefix + "/commits/"):
            ref = httpx.URL(path).path[len(prefix + "/commits/"):]
            from urllib.parse import unquote

            sha = self.refs.get(unquote(ref))
            return httpx.Response(200, text=sha) if sha else httpx.Response(422, json={"message": "No commit found"})
        if path.startswith(prefix + "/tarball/"):
            return httpx.Response(302, headers={"location": "https://codeload.example/octo/demo/tar.gz/x"})
        if path == prefix:
            return httpx.Response(200, json={"private": self.private}) if self.repo_exists else httpx.Response(404, json={})
        return httpx.Response(404, json={})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


class ParseTests(TempProject):
    def test_accepted_forms(self):
        for text in (
            "gh:octo/demo",
            "github.com/octo/demo",
            "https://github.com/octo/demo",
            "http://www.github.com/octo/demo/",
            "https://github.com/octo/demo.git",
            "git@github.com:octo/demo.git",
            "https://github.com/octo/demo?tab=readme#top",
        ):
            with self.subTest(text=text):
                self.assertEqual(parse_target(text), RemoteSpec("octo", "demo"))

    def test_tree_and_blob_links_keep_the_rest_for_later(self):
        self.assertEqual(
            parse_target("https://github.com/octo/demo/tree/feature/login/src").tree,
            ("feature", "login", "src"),
        )
        self.assertEqual(parse_target("github.com/octo/demo/blob/main/README.md").tree, ("main", "README.md"))

    def test_rejects_other_hosts_and_pages(self):
        for text in ("gitlab.com/a/b", "https://bitbucket.org/a/b", "github.com/octo", "github.com/octo/demo/pull/3", "gh:../demo"):
            with self.subTest(text=text), self.assertRaises(RemoteError):
                parse_target(text)

    def test_looks_like_remote(self):
        for text in ("gh:a/b", "github.com/a/b", "https://github.com/a/b", "www.github.com/a/b", "gitlab.com/a/b", "git@github.com:a/b"):
            self.assertTrue(looks_like_remote(text), text)
        for text in ("src/", "include", "github", "./github.com/a", "C:\\code"):
            self.assertFalse(looks_like_remote(text), text)


class FetchTests(TempProject):
    def setUp(self) -> None:
        super().setUp()
        self.cache = self.tmp / "cache"
        self._token_env = {k: os.environ.pop(k, None) for k in ("GITHUB_TOKEN", "GH_TOKEN")}

    def tearDown(self) -> None:
        for k, v in self._token_env.items():
            if v is not None:
                os.environ[k] = v
        super().tearDown()

    def fetch(self, gh: FakeGitHub, target="gh:octo/demo", **kw):
        kw.setdefault("use_gh_token", False)
        return fetch(target, cache_dir=self.cache, transport=gh.transport, **kw)

    def test_unpacks_into_the_cache_without_the_top_folder(self):
        gh = FakeGitHub(make_tarball({"README.md": b"hi", "src/app.py": b"print(1)"}))
        got = self.fetch(gh)
        self.assertEqual(got.sha, SHA)
        self.assertFalse(got.cached)
        self.assertEqual(got.project_dir.name, "demo")
        self.assertEqual((got.project_dir / "src" / "app.py").read_bytes(), b"print(1)")
        self.assertEqual(got.zip_path, got.project_dir.parent / "demo.zip")
        self.assertTrue((got.project_dir.parent / ".complete").is_file())

    def test_second_fetch_of_the_same_commit_is_a_cache_hit(self):
        gh = FakeGitHub(make_tarball({"a.txt": b"1"}))
        self.fetch(gh)
        gh.requests.clear()
        got = self.fetch(gh)
        self.assertTrue(got.cached)
        self.assertFalse(any("/tarball/" in r.url.path for r in gh.requests))

    def test_full_sha_already_cached_needs_no_network(self):
        gh = FakeGitHub(make_tarball({"a.txt": b"1"}), refs={"HEAD": SHA, SHA: SHA})
        self.fetch(gh, ref=SHA)
        gh.requests.clear()
        self.assertTrue(self.fetch(gh, ref=SHA).cached)
        self.assertEqual(gh.requests, [])

    def test_tree_link_resolves_a_branch_with_a_slash(self):
        gh = FakeGitHub(make_tarball({"src/app.py": b"x"}), refs={"feature/login": SHA})
        got = self.fetch(gh, "https://github.com/octo/demo/tree/feature/login/src")
        self.assertEqual((got.ref, got.subdir), ("feature/login", "src"))

    def test_missing_folder_in_link_is_an_error(self):
        gh = FakeGitHub(make_tarball({"a.txt": b"1"}))
        with self.assertRaisesRegex(RemoteError, "doesn't exist"):
            self.fetch(gh, "github.com/octo/demo/tree/main/nope")

    def test_ref_in_url_and_flag_together_is_an_error(self):
        with self.assertRaisesRegex(RemoteError, "not both"):
            self.fetch(FakeGitHub(b""), "github.com/octo/demo/tree/main", ref="main")

    def test_unknown_repo_mentions_private_access(self):
        gh = FakeGitHub(b"", refs={}, repo_exists=False)
        with self.assertRaisesRegex(RemoteError, "GITHUB_TOKEN"):
            self.fetch(gh)

    def test_unknown_ref_is_named(self):
        gh = FakeGitHub(b"", refs={"HEAD": SHA})
        with self.assertRaisesRegex(RemoteError, "no-such-branch"):
            self.fetch(gh, ref="no-such-branch")

    def test_rate_limit_message(self):
        def handler(request):
            return httpx.Response(403, headers={"x-ratelimit-remaining": "0"}, json={})

        with self.assertRaisesRegex(RemoteError, "rate limit"):
            fetch("gh:octo/demo", cache_dir=self.cache, transport=httpx.MockTransport(handler), use_gh_token=False)

    def test_token_goes_to_the_api_but_never_to_the_download_host(self):
        gh = FakeGitHub(make_tarball({"a.txt": b"1"}))
        self.fetch(gh, token="secret-token")
        api = [r for r in gh.requests if r.url.host == "api.github.com"]
        download = [r for r in gh.requests if r.url.host == "codeload.example"]
        self.assertTrue(api and all(r.headers.get("authorization") == "Bearer secret-token" for r in api))
        self.assertTrue(download and all("authorization" not in r.headers for r in download))

    def test_download_over_the_limit_is_refused_and_nothing_is_cached(self):
        gh = FakeGitHub(make_tarball({"big.txt": os.urandom(4096)}))
        with self.assertRaisesRegex(RemoteError, "download limit"):
            self.fetch(gh, max_bytes=100)
        entry = self.cache / "github.com" / "octo" / "demo" / SHA[:12]
        self.assertFalse((entry / ".complete").exists())
        self.assertFalse((entry / "demo").exists())

    def test_links_unsafe_paths_and_workspace_are_not_unpacked(self):
        data = make_tarball(
            {
                "ok.txt": b"1",
                "../escape.txt": b"x",
                ".git/config": b"x",
                ".contextzip/config.json": b'{"workspace_location": "/elsewhere"}',
            },
            links={"link": "/etc/passwd"},
        )
        got = self.fetch(FakeGitHub(data))
        names = sorted(p.relative_to(got.project_dir).as_posix() for p in got.project_dir.rglob("*"))
        self.assertEqual(names, ["ok.txt"])
        self.assertFalse((self.cache.parent / "escape.txt").exists())
        reasons = dict(got.skipped)
        self.assertEqual(reasons.get("link"), "link")
        self.assertEqual(reasons.get("../escape.txt"), "unsafe path")

    def test_old_commits_of_a_repo_are_pruned(self):
        tar = make_tarball({"a.txt": b"1"})
        for i in range(5):
            sha = f"{i:x}" * 40
            self.fetch(FakeGitHub(tar, refs={"HEAD": sha}))
        kept = list((self.cache / "github.com" / "octo" / "demo").iterdir())
        self.assertEqual(len(kept), remote._KEEP_PER_REPO)

    def test_marker_records_what_was_fetched(self):
        got = self.fetch(FakeGitHub(make_tarball({"a.txt": b"1"})), ref="main")
        marker = json.loads((got.project_dir.parent / ".complete").read_text(encoding="utf-8"))
        self.assertEqual((marker["repo"], marker["sha"], marker["ref"]), ("octo/demo", SHA, "main"))

    def test_public_only_refuses_a_private_repo(self):
        gh = FakeGitHub(make_tarball({"a.txt": b"1"}), private=True)
        with self.assertRaises(RemoteError) as caught:
            self.fetch(gh, token="server-token", public_only=True)
        self.assertEqual(caught.exception.kind, "private")
        self.assertFalse(any("/tarball/" in r.url.path for r in gh.requests))

    def test_public_only_allows_a_public_repo(self):
        got = self.fetch(FakeGitHub(make_tarball({"a.txt": b"1"})), public_only=True)
        self.assertEqual(got.sha, SHA)

    def test_public_only_comes_from_the_environment(self):
        gh = FakeGitHub(make_tarball({"a.txt": b"1"}), private=True)
        with mock.patch.dict(os.environ, {remote.PUBLIC_ONLY_ENV_VAR: "1"}):
            with self.assertRaises(RemoteError):
                self.fetch(gh)

    def test_errors_say_what_kind_they_are(self):
        with self.assertRaises(RemoteError) as caught:
            parse_target("gitlab.com/a/b")
        self.assertEqual(caught.exception.kind, "invalid")
        with self.assertRaises(RemoteError) as caught:
            self.fetch(FakeGitHub(b"", refs={}, repo_exists=False))
        self.assertEqual(caught.exception.kind, "not_found")
