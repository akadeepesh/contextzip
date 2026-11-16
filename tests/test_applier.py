from __future__ import annotations

import json
import unittest

from contextzip.api import create_zip, get_files
from contextzip.applier import FileStatus, build_plan, discard_plan, execute_plan
from tests.helpers import TempProject

APP = "export function login(user) {\n  validate(user)\n  return session(user)\n}\n\nexport function logout() {\n  clear()\n}\n"


class ApplyZipTests(TempProject):
    def setUp(self):
        super().setUp()
        (self.root / ".git").mkdir()
        self.write("frontend/package.json", '{"name": "fe"}\n')
        self.write("frontend/src/lib/auth.ts", APP)
        self.write("frontend/src/app/page.tsx", "export default function Home() {}\n")
        self.write("backend/api/main.py", "print('hi')\n")

    def package(self, output=None):
        collection = get_files(self.root, use_gitignore=False)
        return create_zip(collection, output=output)

    def plan(self, files):
        z = self.make_zip("returned.zip", files)
        p = build_plan(z, self.root)
        self.addCleanup(discard_plan, p)
        return p

    def statuses(self, plan):
        return {e.display_path: e.status for e in plan.entries}

    # ── statuses ─────────────────────────────────────────────────────────────

    def test_ai_change_is_modified(self):
        self.package()
        new = APP.replace("clear()", "clear()\n  audit()")
        p = self.plan({"frontend/src/lib/auth.ts": new})
        self.assertEqual(self.statuses(p), {"frontend/src/lib/auth.ts": FileStatus.MODIFIED})

    def test_same_file_is_unchanged(self):
        self.package()
        p = self.plan({"frontend/src/lib/auth.ts": APP})
        self.assertEqual(p.entries[0].status, FileStatus.UNCHANGED)

    def test_line_endings_never_count(self):
        self.package()
        p = self.plan({"frontend/src/lib/auth.ts": APP.replace("\n", "\r\n")})
        self.assertEqual(p.entries[0].status, FileStatus.UNCHANGED)

    def test_already_applied_is_not_drift(self):
        self.package()
        new = APP.replace("clear()", "clear()\n  audit()")
        self.write("frontend/src/lib/auth.ts", new)  # you pasted the AI's fix yourself
        p = self.plan({"frontend/src/lib/auth.ts": new})
        self.assertEqual(p.entries[0].status, FileStatus.UNCHANGED)

    def test_edits_in_different_places_merge(self):
        self.package()
        self.write("frontend/src/lib/auth.ts", APP.replace("validate(user)", "validate(user, { strict: true })"))
        p = self.plan({"frontend/src/lib/auth.ts": APP.replace("clear()", "clear()\n  audit()")})
        e = p.entries[0]
        self.assertEqual(e.status, FileStatus.MERGED)
        execute_plan(p, self.root)
        result = self.read("frontend/src/lib/auth.ts")
        self.assertIn("strict: true", result)
        self.assertIn("audit()", result)

    def test_same_lines_conflict_and_are_not_written_by_default(self):
        self.package()
        self.write("frontend/src/lib/auth.ts", APP.replace("clear()", "clearMine()"))
        p = self.plan({"frontend/src/lib/auth.ts": APP.replace("clear()", "clearTheirs()")})
        e = p.entries[0]
        self.assertEqual(e.status, FileStatus.CONFLICT)
        self.assertIn("<<<<<<<", e.conflict_text)
        res = execute_plan(p, self.root)
        self.assertEqual(res.written, [])
        self.assertIn("clearMine", self.read("frontend/src/lib/auth.ts"))
        self.assertTrue(res.zip_kept)

    def test_deleted_locally(self):
        self.package()
        (self.root / "backend/api/main.py").unlink()
        p = self.plan({"backend/api/main.py": "print('changed')\n"})
        self.assertEqual(p.entries[0].status, FileStatus.DELETED)

    def test_partial_files_are_caught(self):
        self.package()
        p = self.plan({"frontend/src/lib/auth.ts": "export function login(user) {\n  // ... rest of the function unchanged\n}\n"})
        self.assertEqual(p.entries[0].status, FileStatus.PARTIAL)

    def test_new_file(self):
        self.package()
        p = self.plan({"frontend/src/lib/rate-limit.ts": "export const limit = 5\n"})
        self.assertEqual(p.entries[0].status, FileStatus.NEW)

    # ── paths ────────────────────────────────────────────────────────────────

    def test_wrapped_and_subfolder_relative_zips_land_correctly(self):
        self.package()
        new = APP.replace("clear()", "clear()\n  audit()")
        p = self.plan({
            "fixed-app/src/lib/auth.ts": new,
            "fixed-app/src/lib/rate-limit.ts": "export const limit = 5\n",
        })
        self.assertEqual(
            self.statuses(p),
            {"frontend/src/lib/auth.ts": FileStatus.MODIFIED, "frontend/src/lib/rate-limit.ts": FileStatus.NEW},
        )
        self.assertIsNotNone(p.rule)

    def test_prompt_txt_is_ignored(self):
        self.package()
        p = self.plan({"prompt.txt": "task", "frontend/src/lib/auth.ts": APP})
        self.assertEqual([e.display_path for e in p.entries], ["frontend/src/lib/auth.ts"])

    # ── baseline selection ───────────────────────────────────────────────────

    def test_best_manifest_wins_over_newest(self):
        self.package()  # full snapshot
        # a later, unrelated git-changes style run touching only main.py
        from contextzip.api import FileCollection
        from contextzip.filters import ResolveResult
        from contextzip.packager import create_zip_silent

        self.write("backend/api/main.py", "print('later')\n")
        rr = ResolveResult(included=[self.root / "backend/api/main.py"])
        create_zip_silent(rr, self.root, None, mode="git-changes")
        _ = FileCollection  # noqa

        new = APP.replace("clear()", "clear()\n  audit()")
        p = self.plan({"frontend/src/lib/auth.ts": new, "frontend/src/app/page.tsx": "export default function Home() {}\n"})
        self.assertEqual(p.manifest_path.parent.name, "0001")  # the full snapshot, not the newer one
        self.assertEqual(self.statuses(p)["frontend/src/lib/auth.ts"], FileStatus.MODIFIED)

    def test_zip_written_with_output_flag_is_still_found(self):
        out = self.tmp / "elsewhere" / "custom.zip"
        out.parent.mkdir()
        self.package(output=out)
        p = self.plan({"frontend/src/lib/auth.ts": APP.replace("clear()", "clearAll()")})
        self.assertTrue(p.has_manifest)
        self.assertEqual(p.entries[0].status, FileStatus.MODIFIED)

    # ── secrets ──────────────────────────────────────────────────────────────

    def test_redacted_env_keeps_real_values(self):
        self.write(".env", "DB_URL=postgres://real\nAPI_KEY=sk-real\n")
        self.write(".contextzip/config.json", json.dumps({"always_include": [".env"]}))
        self.package()
        returned = "DB_URL=[REDACTED]\nAPI_KEY=[REDACTED]\nNEW_FLAG=true\n"
        p = self.plan({".env": returned})
        e = p.entries[0]
        self.assertTrue(e.redacted)
        self.assertEqual(e.status, FileStatus.MODIFIED)
        execute_plan(p, self.root)
        self.assertEqual(self.read(".env"), "DB_URL=postgres://real\nAPI_KEY=sk-real\nNEW_FLAG=true\n")

    def test_untouched_redacted_env_is_unchanged(self):
        self.write(".env", "DB_URL=postgres://real\n")
        self.write(".contextzip/config.json", json.dumps({"always_include": [".env"]}))
        self.package()
        p = self.plan({".env": "DB_URL=[REDACTED]\n"})
        self.assertEqual(p.entries[0].status, FileStatus.UNCHANGED)
        self.assertEqual(self.read(".env"), "DB_URL=postgres://real\n")

    # ── writing ──────────────────────────────────────────────────────────────

    def test_local_line_endings_are_kept(self):
        self.write("frontend/src/lib/auth.ts", APP.replace("\n", "\r\n"))
        self.package()
        p = self.plan({"frontend/src/lib/auth.ts": APP.replace("clear()", "clearAll()")})
        execute_plan(p, self.root)
        data = (self.root / "frontend/src/lib/auth.ts").read_bytes()
        self.assertIn(b"clearAll()\r\n", data)
        self.assertNotIn(b"\n\n", data.replace(b"\r\n", b"X"))

    def test_taking_theirs_keeps_local_line_endings(self):
        self.write("frontend/src/lib/auth.ts", APP.replace("\n", "\r\n"))
        self.package()
        self.write("frontend/src/lib/auth.ts", APP.replace("clear()", "clearMine()").replace("\n", "\r\n"))
        p = self.plan({"frontend/src/lib/auth.ts": APP.replace("clear()", "clearTheirs()")})
        self.assertEqual(p.entries[0].status, FileStatus.CONFLICT)
        execute_plan(p, self.root, decisions={"frontend/src/lib/auth.ts": "theirs"})
        data = (self.root / "frontend/src/lib/auth.ts").read_bytes()
        self.assertIn(b"clearTheirs()\r\n", data)

    def test_yes_applies_risky_entries(self):
        self.package()
        self.write("frontend/src/lib/auth.ts", APP.replace("clear()", "clearMine()"))
        p = self.plan({"frontend/src/lib/auth.ts": APP.replace("clear()", "clearTheirs()")})
        res = execute_plan(p, self.root, include_risky=True)
        self.assertEqual(res.written, ["frontend/src/lib/auth.ts"])
        self.assertFalse(res.zip_kept)

    # ── snapshots, guide, moves ──────────────────────────────────────────────

    def test_reply_to_an_older_snapshot_still_merges_after_repackaging(self):
        first = self.package()
        self.write("frontend/src/lib/auth.ts", APP.replace("validate(user)", "validate(user, { strict: true })"))
        self.package()  # packaged again before the first reply came back
        p = self.plan({
            "CONTEXTZIP.md": f"Snapshot: {first.snapshot}\n",
            "frontend/src/lib/auth.ts": APP.replace("clear()", "clear()\n  audit()"),
        })
        self.assertTrue(p.matched_by_guide)
        self.assertEqual(p.snapshot, first.snapshot)
        self.assertEqual(p.entries[0].status, FileStatus.MERGED)

    def test_unknown_snapshot_falls_back_to_content(self):
        self.package()
        p = self.plan({"CONTEXTZIP.md": "Snapshot: 99-zzzzzz\n", "frontend/src/lib/auth.ts": APP.replace("clear()", "x()")})
        self.assertFalse(p.matched_by_guide)
        self.assertIn("99-zzzzzz", p.manifest_note)
        self.assertEqual(p.entries[0].status, FileStatus.MODIFIED)

    def test_move_detected_by_content(self):
        self.package()
        p = self.plan({"frontend/src/lib/session.ts": APP.replace("clear()", "clearAll()")})
        e = p.entries[0]
        self.assertEqual(e.status, FileStatus.MOVED)
        self.assertEqual(e.moved_from, "frontend/src/lib/auth.ts")
        res = execute_plan(p, self.root)  # no consent: old file stays
        self.assertTrue((self.root / "frontend/src/lib/auth.ts").is_file())
        self.assertEqual(res.removed, [])

    def test_move_removes_old_file_only_with_consent(self):
        pkg = self.package()
        guide = f"Snapshot: {pkg.snapshot}\n\n## Changes\nmoved: frontend/src/lib/auth.ts -> frontend/src/lib/session.ts\n"
        p = self.plan({"CONTEXTZIP.md": guide, "frontend/src/lib/session.ts": "totally rewritten\n"})
        self.assertEqual(p.entries[0].status, FileStatus.MOVED)
        res = execute_plan(p, self.root, remove_moved=True)
        self.assertEqual(res.removed, ["frontend/src/lib/auth.ts"])
        self.assertFalse((self.root / "frontend/src/lib/auth.ts").exists())

    def test_declared_move_beats_name_matching(self):
        pkg = self.package()
        guide = f"Snapshot: {pkg.snapshot}\nmoved: backend/api/main.py -> backend/api/v2/main.py\n"
        p = self.plan({"CONTEXTZIP.md": guide, "backend/api/v2/main.py": "print('v2')\n"})
        e = p.entries[0]
        self.assertEqual((e.rel_path, e.status, e.moved_from), ("backend/api/v2/main.py", FileStatus.MOVED, "backend/api/main.py"))

    def test_old_file_changed_since_is_never_removed(self):
        pkg = self.package()
        self.write("frontend/src/lib/auth.ts", APP + "// my edit\n")
        guide = f"Snapshot: {pkg.snapshot}\nmoved: frontend/src/lib/auth.ts -> frontend/src/lib/session.ts\n"
        p = self.plan({"CONTEXTZIP.md": guide, "frontend/src/lib/session.ts": APP})
        self.assertFalse(p.entries[0].removable)
        res = execute_plan(p, self.root, remove_moved=True)
        self.assertEqual(res.removed, [])
        self.assertTrue((self.root / "frontend/src/lib/auth.ts").is_file())

    def test_requested_deletion_needs_its_own_yes(self):
        pkg = self.package()
        guide = f"Snapshot: {pkg.snapshot}\ndeleted: backend/api/main.py\n"
        p = self.plan({"CONTEXTZIP.md": guide})
        self.assertEqual(self.statuses(p), {"backend/api/main.py": FileStatus.DELETE_REQUESTED})
        execute_plan(p, self.root, include_risky=True)  # --yes alone never deletes
        self.assertTrue((self.root / "backend/api/main.py").is_file())

        p = self.plan({"CONTEXTZIP.md": guide})
        res = execute_plan(p, self.root, allow_delete=True)
        self.assertEqual(res.removed, ["backend/api/main.py"])

    def test_snapshot_marked_applied(self):
        pkg = self.package()
        from contextzip import snapshots

        p = self.plan({"CONTEXTZIP.md": f"Snapshot: {pkg.snapshot}\n", "frontend/src/lib/auth.ts": APP.replace("clear()", "x()")})
        execute_plan(p, self.root)
        self.assertEqual(snapshots.find(self.root, pkg.snapshot).load()["status"], "applied")

    def test_decisions_override(self):
        self.package()
        self.write("frontend/src/lib/auth.ts", APP.replace("clear()", "clearMine()"))
        p = self.plan({"frontend/src/lib/auth.ts": APP.replace("clear()", "clearTheirs()")})
        execute_plan(p, self.root, decisions={"frontend/src/lib/auth.ts": {"action": "custom", "content": "resolved\n"}})
        self.assertEqual(self.read("frontend/src/lib/auth.ts"), "resolved\n")


if __name__ == "__main__":
    unittest.main()
