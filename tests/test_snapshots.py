from __future__ import annotations

import os
import time
import zipfile

from contextzip import snapshots
from contextzip.api import create_zip, get_files
from tests.helpers import TempProject


class SnapshotTests(TempProject):
    def setUp(self):
        super().setUp()
        (self.root / ".git").mkdir()
        self.write("src/app.py", "print(1)\n")

    def package(self, **kw):
        return create_zip(get_files(self.root, use_gitignore=False), **kw)

    def test_every_package_is_a_numbered_snapshot(self):
        a, b = self.package(), self.package()
        self.assertEqual((a.snapshot_id, b.snapshot_id), (1, 2))
        self.assertRegex(a.snapshot, r"^1-[a-z0-9]{6}$")
        snaps = snapshots.list_snapshots(self.root)
        self.assertEqual([s.id for s in snaps], [2, 1])
        self.assertTrue(all(s.is_complete for s in snaps))

    def test_zip_carries_the_guide_with_the_snapshot(self):
        pkg = self.package()
        with zipfile.ZipFile(pkg.zip_path) as zf:
            text = zf.read("CONTEXTZIP.md").decode()
        self.assertIn(f"Snapshot: {pkg.snapshot}", text)
        self.assertNotIn("CONTEXTZIP.md", snapshots.find(self.root, pkg.snapshot).load()["files"])

    def test_find(self):
        pkg = self.package()
        token = pkg.snapshot.split("-")[1]
        for ref in ("1", "#1", pkg.snapshot, token):
            self.assertEqual(snapshots.find(self.root, ref).id, 1, ref)
        self.assertIsNone(snapshots.find(self.root, "1-zzzzzz"))  # another clone's #1

    def test_output_flag_still_snapshots(self):
        pkg = self.package(output=self.tmp / "out" / "custom.zip")
        self.assertEqual(pkg.snapshot_id, 1)
        self.assertTrue(snapshots.find(self.root, 1).zip_path.is_file())

    def test_pruning_keeps_recent_and_young(self):
        for _ in range(4):
            self.package()
        self.assertEqual(snapshots.prune_candidates(self.root, keep=2, max_age_days=14), [])
        old = time.time() - 30 * 86400
        for snap in snapshots.list_snapshots(self.root):
            os.utime(snap.manifest_path, (old, old))
        pruned = sorted(p.name for p in snapshots.prune_candidates(self.root, keep=2, max_age_days=14))
        self.assertEqual(pruned, ["0001", "0002"])

    def test_pruning_never_removes_a_snapshot_the_inbox_waits_on(self):
        first = self.package()
        for _ in range(3):
            self.package()
        old = time.time() - 30 * 86400
        for snap in snapshots.list_snapshots(self.root):
            os.utime(snap.manifest_path, (old, old))
        inbox = self.root / ".contextzip" / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(inbox / "reply.zip", "w") as zf:
            zf.writestr("CONTEXTZIP.md", f"Snapshot: {first.snapshot}\n")
            zf.writestr("src/app.py", "print(2)\n")
        protected = snapshots.protected_by_inbox(self.root)
        pruned = {p.name for p in snapshots.prune_candidates(self.root, keep=1, max_age_days=1, protected=protected)}
        self.assertNotIn("0001", pruned)
        self.assertEqual(pruned, {"0002", "0003"})

    def test_guide_can_be_turned_off(self):
        from contextzip.filters import ResolveResult
        from contextzip.packager import create_zip_silent

        pkg = create_zip_silent(ResolveResult(included=[self.root / "src/app.py"]), self.root, None, guide=False)
        with zipfile.ZipFile(pkg.zip_path) as zf:
            self.assertNotIn("CONTEXTZIP.md", zf.namelist())
        self.assertIsNotNone(pkg.snapshot)  # the snapshot is still kept locally
