"""The local config UI's server: what it tells the page about the tree and the suggestions."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from contextzip.detector import detect
from contextzip.project_config import load_project_config
from contextzip.webui import server
from contextzip.webui.assets import INDEX_HTML
from tests.helpers import TempProject

MB = 1024 * 1024


class ConfigUITests(TempProject):
    def setUp(self) -> None:
        super().setUp()
        self.write("package.json", '{"name": "demo"}')
        self.write("src/app.ts", "x" * 100)
        self.write("src/util.ts", "x" * 50)
        self.write("docs/guide.md", "x" * 10)
        self.write("docs/brand.pdf", "x" * 2000)
        self.write("data/dump.json", "x" * (MB + 5))
        self.write("node_modules/dep/index.js", "x")

        self.state = server._ConfigUIState(self.root, detect(self.root), load_project_config(self.root))
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server._make_handler(self.state))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def call(self, path: str, body: dict | None = None, token: str | None = "") -> dict:
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None)
        req.add_header("X-Contextzip-Token", self.state.token if token == "" else (token or ""))
        if body is not None:
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=10) as res:
            return json.loads(res.read())

    def preview(self, include=(), exclude=()) -> dict:
        return self.call("/api/preview", {"always_include": list(include), "always_exclude": list(exclude)})

    @staticmethod
    def node(tree: list, path: str) -> dict:
        for part in path.split("/"):
            found = next(n for n in tree if n["name"] == part)
            tree = found.get("children") or []
        return found

    def test_requests_need_the_session_token(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.call("/api/state", token="wrong")
        self.assertEqual(caught.exception.code, 403)

    def test_folders_report_how_much_of_them_goes_in(self):
        data = self.call("/api/state")
        src = self.node(data["tree"], "src")
        self.assertEqual((src["files"], src["includedFiles"], src["includedSize"], src["included"]), (2, 2, 150, True))

        data = self.preview(exclude=["src/util.ts"])
        src = self.node(data["tree"], "src")
        self.assertEqual((src["files"], src["includedFiles"], src["includedSize"], src["included"]), (2, 1, 100, "partial"))
        self.assertEqual(self.node(data["tree"], "src/util.ts")["includedSize"], 0)

        data = self.preview(exclude=["src/"])
        src = self.node(data["tree"], "src")
        self.assertEqual((src["includedFiles"], src["includedSize"], src["included"]), (0, 0, False))

    def test_large_files_are_flagged(self):
        data = self.call("/api/state")
        self.assertTrue(self.node(data["tree"], "data/dump.json")["large"])
        self.assertNotIn("large", self.node(data["tree"], "src/app.ts"))

    def test_suggestions_and_what_they_would_save(self):
        data = self.call("/api/state")
        by_id = {s["id"]: s for s in data["suggestions"]}
        self.assertEqual(set(by_id), {"ext:.pdf", "large-files"})
        self.assertEqual(by_id["large-files"]["paths"], ["data/dump.json"])
        self.assertEqual(data["stats"]["suggestedBytes"], 2000 + MB + 5)

    def test_an_applied_suggestion_stays_listed_so_it_can_be_undone(self):
        data = self.preview(exclude=["*.pdf"])
        self.assertEqual({s["id"] for s in data["suggestions"]}, {"ext:.pdf", "large-files"})
        self.assertEqual(data["stats"]["suggestedBytes"], MB + 5)

        data = self.preview(exclude=["*.pdf", "data/dump.json"])
        self.assertEqual(len(data["suggestions"]), 2)
        self.assertEqual(data["stats"]["suggestedBytes"], 0)

    def test_page_has_separate_controls_for_opening_and_including(self):
        # The caret and the checkbox are two buttons; a click on one must not act as the other.
        self.assertIn('caret.addEventListener("click", function (e) {\n        e.stopPropagation();', INDEX_HTML)
        self.assertIn('check.addEventListener("click", function (e) {\n      e.stopPropagation();', INDEX_HTML)
        self.assertIn('id="trim-all"', INDEX_HTML)
        self.assertNotIn("https://", INDEX_HTML.replace("https://www.w3.org", ""))
