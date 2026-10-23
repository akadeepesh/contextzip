from __future__ import annotations

import asyncio
import importlib.util
import unittest
from unittest import mock

from contextzip import mcp_server
from tests.helpers import TempProject

HAS_MCP = importlib.util.find_spec("mcp") is not None


class ToolTests(TempProject):
    def setUp(self) -> None:
        super().setUp()
        self.write("package.json", '{"name": "demo"}\n')
        self.write("src/index.ts", "export const a = 1;\n")
        self.write(".env", "TOKEN=secret\n")

    def test_package_project_returns_the_json_result_without_the_log(self):
        result = mcp_server.package_project(str(self.root))
        self.assertTrue(result["ok"], result)
        self.assertNotIn("log", result)
        self.assertEqual(result["files"]["included_files"], ["package.json", "src/index.ts"])
        self.assertIsNone(result["clipboard"], "no clipboard unless asked")

    def test_filters_become_cli_flags(self):
        with mock.patch.object(mcp_server, "run", return_value={"ok": True}) as run:
            mcp_server.package_project("/p", include=["src/"], exclude=["*.log"], git_changes=True, dry_run=True)
        args = run.call_args[0][0]
        self.assertEqual(args, ["-i", "src/", "-e", "*.log", "--git-changes", "--dry-run", "--no-clipboard"])

    def test_preview_then_apply_safe_changes(self):
        mcp_server.package_project(str(self.root))
        reply = self.make_zip("reply.zip", {"index.ts": "export const a = 2;\n"})
        preview = mcp_server.preview_apply(str(reply), str(self.root))
        self.assertEqual([(f["path"], f["status"]) for f in preview["plan"]["files"]], [("src/index.ts", "modified")])
        self.assertEqual(self.read("src/index.ts"), "export const a = 1;\n", "a preview writes nothing")
        applied = mcp_server.apply_changes(str(reply), str(self.root))
        self.assertEqual(applied["result"]["written"], ["src/index.ts"])

    def test_cautious_defaults(self):
        with mock.patch.object(mcp_server, "run", return_value={"ok": True}) as run:
            mcp_server.apply_changes("r.zip", "/p")
            self.assertIn("--safe-only", run.call_args[0][0])
            mcp_server.open_pull_request("r.zip")
            self.assertIn("--dry-run", run.call_args[0][0])
            self.assertNotIn("--yes", run.call_args[0][0])
        self.assertFalse(mcp_server.apply_changes("r.zip", "/p", mode="everything")["ok"])


@unittest.skipUnless(HAS_MCP, 'needs pip install "contextzip[mcp]" (Python 3.10+)')
class ServerTests(TempProject):
    def test_every_tool_is_registered_with_annotations(self):
        server = mcp_server.build_server()
        tools = {t.name: t for t in asyncio.run(server.list_tools())}
        self.assertEqual(
            set(tools),
            {"package_project", "package_repository", "preview_apply", "apply_changes", "open_pull_request", "list_snapshots"},
        )
        self.assertTrue(tools["preview_apply"].annotations.read_only_hint)
        self.assertTrue(tools["apply_changes"].annotations.destructive_hint)
        self.assertIn("path", tools["package_project"].input_schema["properties"])
