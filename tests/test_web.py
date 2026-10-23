from __future__ import annotations

import io
import json
import os
import zipfile
from unittest import mock

from contextzip import cli, remote, service
from contextzip.remote import FetchedRemote, RemoteError
from contextzip.web import run
from tests.helpers import TempProject


class WebRunTests(TempProject):
    def setUp(self) -> None:
        super().setUp()
        self.write("package.json", '{"name": "demo"}\n')
        self.write("src/index.ts", "export const a = 1;\n")

    def test_returns_the_json_result_and_restores_the_cwd(self):
        before = os.getcwd()
        result = run(["--no-clipboard"], cwd=self.root)
        self.assertEqual(os.getcwd(), before)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["files"]["included_files"], ["package.json", "src/index.ts"])
        self.assertTrue(os.path.isfile(result["zip"]["path"]))

    def test_apply_round_trip(self):
        run(["--no-clipboard"], cwd=self.root)
        reply = self.make_zip("reply.zip", {"index.ts": "export const a = 2;\n"})
        result = run(["apply-zip", str(reply), "--safe-only"], cwd=self.root)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["result"]["written"], ["src/index.ts"])

    def test_bad_arguments_are_a_failed_result_not_an_exception(self):
        result = run(["--no-such-flag"], cwd=self.root)
        self.assertFalse(result["ok"])
        self.assertIn("no-such-flag", result["error"])


def call(environ_query: str, method: str = "GET", client: str = "1.2.3.4") -> tuple[str, dict, bytes]:
    captured: dict = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)

    environ = {"REQUEST_METHOD": method, "QUERY_STRING": environ_query, "REMOTE_ADDR": client, "wsgi.input": io.BytesIO()}
    body = b"".join(service.app(environ, start_response))
    return captured["status"], captured["headers"], body


class ServiceTests(TempProject):
    def setUp(self) -> None:
        super().setUp()
        self._env_patch = mock.patch.dict(os.environ, {remote.CACHE_ENV_VAR: str(self.tmp / "cache")})
        self._env_patch.start()
        (self.tmp / "cache").mkdir()
        self._limiter = service._limiter
        service._limiter = service._RateLimiter(100)

    def tearDown(self) -> None:
        service._limiter = self._limiter
        self._env_patch.stop()
        os.environ.pop(remote.PUBLIC_ONLY_ENV_VAR, None)
        super().tearDown()

    def fake_fetch(self, spec, ref=None):
        repo = self.tmp / "cache" / "demo"
        if not repo.exists():
            self.write("pyproject.toml", "[project]\nname = 'demo'\n", base=repo)
            self.write("demo/core.py", "x = 1\n", base=repo)
            self.write(".env", "TOKEN=secret\n", base=repo)
        self.fetched_public_only = os.environ.get(remote.PUBLIC_ONLY_ENV_VAR)
        return FetchedRemote(spec=spec, sha="c" * 40, ref=ref, project_dir=repo, zip_path=repo.parent / "demo.zip", cached=True)

    def request(self, query: str, **kw):
        with mock.patch.object(cli.remote_mod, "fetch", side_effect=self.fake_fetch):
            return call(query, **kw)

    def test_zip_for_a_repository(self):
        status, headers, body = self.request("repo=github.com/octo/demo")
        self.assertEqual(status, "200 OK")
        self.assertEqual(headers["Content-Type"], "application/zip")
        self.assertEqual(headers["X-Contextzip-Commit"], "c" * 40)
        self.assertIn('filename="demo-ccccccc.zip"', headers["Content-Disposition"])
        self.assertIn("s-maxage=300", headers["Cache-Control"])
        names = zipfile.ZipFile(io.BytesIO(body)).namelist()
        self.assertIn("demo/core.py", names)
        self.assertNotIn(".env", names)
        self.assertEqual(self.fetched_public_only, "1", "the service must only fetch public repos")

    def test_a_full_sha_is_cacheable_forever(self):
        _status, headers, _body = self.request(f"repo=github.com/octo/demo&ref={'c' * 40}")
        self.assertIn("immutable", headers["Cache-Control"])

    def test_json_summary_has_no_server_paths(self):
        status, _headers, body = self.request("repo=gh:octo/demo&format=json")
        self.assertEqual(status, "200 OK")
        data = json.loads(body)
        self.assertEqual(data["repo"], "octo/demo")
        self.assertIn("demo/core.py", data["files"]["included_files"])
        self.assertGreater(data["zip"]["estimated_tokens"], 0)
        self.assertNotIn(str(self.tmp), body.decode())

    def test_bad_requests(self):
        for query, status in (("", "400"), ("repo=codeberg.org/a/b", "400"), ("repo=github.com/a/b&format=tar", "400")):
            with self.subTest(query=query):
                got, _headers, body = call(query)
                self.assertTrue(got.startswith(status), got)
                self.assertFalse(json.loads(body)["ok"])

    def test_fetch_errors_map_to_http_statuses(self):
        for kind, status in (("private", "403"), ("not_found", "404"), ("too_big", "413"), ("rate_limited", "429")):
            with self.subTest(kind=kind):
                error = RemoteError(f"nope ({kind})", kind)
                with mock.patch.object(cli.remote_mod, "fetch", side_effect=error):
                    got, _headers, body = call("repo=github.com/octo/demo")
                self.assertTrue(got.startswith(status), got)
                self.assertEqual(json.loads(body)["error_kind"], kind)

    def test_rate_limit_per_client(self):
        service._limiter = service._RateLimiter(2)
        statuses = [self.request("repo=github.com/octo/demo&format=json")[0] for _ in range(3)]
        self.assertEqual([s[:3] for s in statuses], ["200", "200", "429"])
        self.assertTrue(self.request("repo=github.com/octo/demo&format=json", client="5.6.7.8")[0].startswith("200"))

    def test_preflight(self):
        status, headers, _body = call("", method="OPTIONS")
        self.assertEqual(status, "204 No Content")
        self.assertEqual(headers["Access-Control-Allow-Origin"], "*")
