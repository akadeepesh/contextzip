from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from unittest import mock

import httpx
from click.testing import CliRunner

from contextzip import cli, pullrequest
from contextzip.remote import RemoteError, RemoteSpec
from contextzip.web import run
from tests.helpers import TempProject

SHA = "a" * 40


class CachedRepoMixin(TempProject):
    """A repository as `contextzip github.com/octo/demo` leaves it in the cache."""

    def setUp(self) -> None:
        super().setUp()
        self.cache = self.tmp / "cache"
        self._cache_env = mock.patch.dict(os.environ, {"CONTEXTZIP_CACHE_DIR": str(self.cache)})
        self._cache_env.start()
        entry = self.cache / "remote" / "github.com" / "octo" / "demo" / SHA[:12]
        self.project = entry / "demo"
        self.write("pyproject.toml", "[project]\nname = 'demo'\n", base=self.project)
        self.write("demo/core.py", "x = 1\n", base=self.project)
        self.write("demo/old.py", "y = 2\n", base=self.project)
        (entry / ".complete").write_text(
            json.dumps({"host": "github.com", "repo": "octo/demo", "sha": SHA, "ref": "main"}), encoding="utf-8"
        )
        result = run(["--no-clipboard"], cwd=self.project)
        assert result["ok"], result
        self.snapshot = result["zip"]["snapshot"]

    def tearDown(self) -> None:
        self._cache_env.stop()
        super().tearDown()

    def reply(self, files: dict[str, str], guide: bool = True) -> Path:
        if guide:
            files = {"CONTEXTZIP.md": f"Snapshot: {self.snapshot}\n", **files}
        return self.make_zip("reply.zip", files)


class FindAndPlanTests(CachedRepoMixin):
    def test_finds_the_repo_by_the_snapshot_in_the_reply(self):
        found = pullrequest.find_cached_repo(self.reply({"core.py": "x = 2\n"}))
        self.assertEqual((found.spec.slug, found.sha, found.ref), ("octo/demo", SHA, "main"))
        self.assertEqual(found.snapshot.label, self.snapshot)

    def test_unknown_snapshot_or_no_guide_without_repo_is_an_error(self):
        with self.assertRaises(RemoteError):
            pullrequest.find_cached_repo(self.make_zip("r.zip", {"CONTEXTZIP.md": "Snapshot: 99-zzzzzz\n"}))
        with self.assertRaises(RemoteError) as caught:
            pullrequest.find_cached_repo(self.reply({"core.py": "x"}, guide=False))
        self.assertEqual(caught.exception.kind, "invalid")
        found = pullrequest.find_cached_repo(self.reply({"core.py": "x"}, guide=False), "github.com/octo/demo")
        self.assertIsNone(found.snapshot)

    def test_only_safe_changes_are_committed(self):
        from contextzip.applier import build_plan, discard_plan

        reply = self.reply(
            {"core.py": "x = 2\n", "demo/new.py": "z = 3\n", "demo/old.py": "# ... rest of the file unchanged ...\n"}
        )
        found = pullrequest.find_cached_repo(reply)
        plan = build_plan(reply, found.project_dir, found.snapshot.manifest_path)
        try:
            changes, left_out = pullrequest.changes_from_plan(plan)
        finally:
            discard_plan(plan)
        self.assertEqual({c.path: c.content for c in changes}, {"demo/core.py": b"x = 2\n", "demo/new.py": b"z = 3\n"})
        self.assertEqual([path for path, _why in left_out], ["demo/old.py"], "a cut-down file is listed, not committed")


class FakeGitHubApi:
    """The parts of GitHub's REST API that open_pull_request uses."""

    def __init__(self, can_push: bool):
        self.can_push = can_push
        self.calls: list[tuple[str, str, dict]] = []
        self.existing_branches = {"contextzip/taken"}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        body = json.loads(request.content) if request.content else {}
        self.calls.append((method, path, body))
        if method == "GET" and path == "/repos/octo/demo":
            return httpx.Response(200, json={"default_branch": "main", "permissions": {"push": self.can_push}})
        if method == "GET" and path == "/repos/octo/demo/branches/main":
            return httpx.Response(200, json={})
        if method == "POST" and path == "/repos/octo/demo/forks":
            return httpx.Response(202, json={"full_name": "me/demo"})
        if method == "GET" and path.endswith(f"/git/commits/{SHA}"):
            return httpx.Response(200, json={"tree": {"sha": "basetree"}})
        if method == "GET" and "/git/trees/" in path:
            return httpx.Response(200, json={"tree": [{"path": "bin/run.sh", "mode": "100755", "type": "blob"}]})
        if method == "POST" and path.endswith("/git/blobs"):
            return httpx.Response(201, json={"sha": f"blob-{len(self.calls)}"})
        if method == "POST" and path.endswith("/git/trees"):
            return httpx.Response(201, json={"sha": "newtree"})
        if method == "POST" and path.endswith("/git/commits"):
            return httpx.Response(201, json={"sha": "newcommit"})
        if method == "POST" and path.endswith("/git/refs"):
            name = body["ref"].removeprefix("refs/heads/")
            if name in self.existing_branches:
                return httpx.Response(422, json={"message": "Reference already exists"})
            return httpx.Response(201, json={})
        if method == "POST" and path == "/repos/octo/demo/pulls":
            return httpx.Response(201, json={"html_url": "https://github.com/octo/demo/pull/7", "number": 7})
        return httpx.Response(404, json={"message": f"unexpected {method} {path}"})

    def body_of(self, method: str, suffix: str) -> dict:
        return next(b for m, p, b in self.calls if m == method and p.endswith(suffix))


class OpenPullRequestTests(TempProject):
    def open(self, api: FakeGitHubApi, branch="contextzip/3-abc"):
        changes = [
            pullrequest.Change("src/app.py", b"print(2)\n"),
            pullrequest.Change("bin/run.sh", b"#!/bin/sh\necho 2\n"),
            pullrequest.Change("src/old.py", None),
        ]
        with mock.patch.object(pullrequest, "_transport", httpx.MockTransport(api)):
            return pullrequest.open_pull_request(
                RemoteSpec("octo", "demo"), SHA, "main", changes, title="T", body="B", branch=branch, token="tok"
            )

    def test_with_push_access_commits_on_the_packaged_commit(self):
        api = FakeGitHubApi(can_push=True)
        pr = self.open(api)
        self.assertEqual((pr.number, pr.repo, pr.forked, pr.branch), (7, "octo/demo", False, "contextzip/3-abc"))
        tree = api.body_of("POST", "/git/trees")
        self.assertEqual(tree["base_tree"], "basetree")
        entries = {e["path"]: e for e in tree["tree"]}
        self.assertEqual(entries["bin/run.sh"]["mode"], "100755", "an executable stays executable")
        self.assertIsNone(entries["src/old.py"]["sha"], "a deletion")
        self.assertEqual(api.body_of("POST", "/git/commits")["parents"], [SHA])
        blob = api.body_of("POST", "/git/blobs")
        self.assertEqual(base64.b64decode(blob["content"]), b"print(2)\n")
        pull = api.body_of("POST", "/pulls")
        self.assertEqual((pull["head"], pull["base"]), ("contextzip/3-abc", "main"))
        self.assertFalse(any(p.endswith("/forks") for _m, p, _b in api.calls))

    def test_without_push_access_forks_first(self):
        api = FakeGitHubApi(can_push=False)
        pr = self.open(api)
        self.assertTrue(pr.forked)
        self.assertEqual(pr.repo, "me/demo")
        self.assertTrue(any(p == "/repos/me/demo/git/blobs" for _m, p, _b in api.calls))
        self.assertEqual(api.body_of("POST", "/pulls")["head"], "me:contextzip/3-abc")

    def test_a_taken_branch_name_gets_a_suffix(self):
        pr = self.open(FakeGitHubApi(can_push=True), branch="contextzip/taken")
        self.assertEqual(pr.branch, "contextzip/taken-2")

    def test_only_github(self):
        with self.assertRaises(RemoteError):
            pullrequest.open_pull_request(
                RemoteSpec("g", "p", host="gitlab.com"), SHA, None, [pullrequest.Change("a", b"x")],
                title="T", body="B", branch="b", token="t",
            )


class CliPullRequestTests(CachedRepoMixin):
    def invoke(self, *args):
        result = CliRunner().invoke(cli.main, ["apply-zip", *args, "--json"], catch_exceptions=False)
        return result.exit_code, json.loads(result.stdout.strip().splitlines()[-1])

    def test_dry_run_reports_what_would_be_opened(self):
        code, data = self.invoke(str(self.reply({"core.py": "x = 2\n"})), "--pr", "--dry-run")
        self.assertEqual(code, 0, data)
        self.assertEqual(data["repository"]["repo"], "octo/demo")
        self.assertEqual(data["pull_request"]["files"], ["demo/core.py"])
        self.assertTrue(data["pull_request"]["dry_run"])

    def test_json_needs_yes_before_publishing(self):
        with mock.patch.object(pullrequest, "open_pull_request") as opened, mock.patch(
            "contextzip.remote.github_token", return_value="tok"
        ):
            code, data = self.invoke(str(self.reply({"core.py": "x = 2\n"})), "--pr")
        self.assertNotEqual(code, 0)
        self.assertIn("--yes", data["error"])
        opened.assert_not_called()

    def test_yes_opens_it(self):
        pr = pullrequest.PullRequest(url="https://github.com/octo/demo/pull/7", number=7, branch="contextzip/x", repo="octo/demo")
        with mock.patch.object(pullrequest, "open_pull_request", return_value=pr) as opened, mock.patch(
            "contextzip.remote.github_token", return_value="tok"
        ):
            code, data = self.invoke(str(self.reply({"core.py": "x = 2\n"})), "--pr", "--yes")
        self.assertEqual(code, 0, data)
        self.assertEqual(data["pull_request"]["url"], "https://github.com/octo/demo/pull/7")
        args, kwargs = opened.call_args
        self.assertEqual((args[0].slug, args[1], args[2]), ("octo/demo", SHA, "main"))
        self.assertIn("demo/core.py", kwargs["body"])

    def test_from_without_pr_is_a_usage_error(self):
        result = CliRunner().invoke(cli.main, ["apply-zip", "x.zip", "--from", "gh:a/b"])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("--pr", result.output)
