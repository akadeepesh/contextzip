from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from contextzip.applier import ApplyPlan, _cleanup_extraction_dir, discard_plan
from contextzip.cli import _mask_api_key


class TestExtractionDirConfinement(unittest.TestCase):
    def test_discard_plan_deletes_temp_apply_dir(self) -> None:
        extraction_dir = Path(tempfile.mkdtemp(prefix="contextzip-apply-"))
        marker = extraction_dir / "payload.txt"
        marker.write_text("temp", encoding="utf-8")

        plan = ApplyPlan(
            zip_path=Path("dummy.zip"),
            manifest_path=None,
            extraction_dir=extraction_dir,
        )
        discard_plan(plan)
        self.assertFalse(extraction_dir.exists())

    def test_discard_plan_refuses_directory_outside_temp_root(self) -> None:
        # Create a fake workspace dir outside the mocked temp root
        with tempfile.TemporaryDirectory() as outer:
            outer_path = Path(outer)
            fake_temp = outer_path / "temp_root"
            fake_temp.mkdir()
            outside_dir = outer_path / "contextzip-apply-escape"
            outside_dir.mkdir()
            victim = outside_dir / "important.txt"
            victim.write_text("keep me", encoding="utf-8")

            plan = ApplyPlan(
                zip_path=Path("dummy.zip"),
                manifest_path=None,
                extraction_dir=outside_dir,
            )
            with patch("contextzip.applier.tempfile.gettempdir", return_value=str(fake_temp)):
                discard_plan(plan)

            self.assertTrue(outside_dir.exists())
            self.assertTrue(victim.exists())

    def test_discard_plan_refuses_temp_root_itself_or_non_prefix_dir(self) -> None:
        with tempfile.TemporaryDirectory() as fake_temp:
            fake_temp_path = Path(fake_temp)
            other_dir = fake_temp_path / "unrelated-dir"
            other_dir.mkdir()
            (other_dir / "keep.txt").write_text("keep", encoding="utf-8")

            with patch("contextzip.applier.tempfile.gettempdir", return_value=str(fake_temp_path)):
                _cleanup_extraction_dir(fake_temp_path)
                _cleanup_extraction_dir(other_dir)

            self.assertTrue(fake_temp_path.exists())
            self.assertTrue(other_dir.exists())

    def test_discard_plan_refuses_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory() as outer:
            outer_path = Path(outer)
            fake_temp = outer_path / "temp_root"
            fake_temp.mkdir()
            victim_dir = outer_path / "victim"
            victim_dir.mkdir()
            victim_file = victim_dir / "secret.txt"
            victim_file.write_text("keep", encoding="utf-8")

            link_path = fake_temp / "contextzip-apply-link"
            try:
                os.symlink(victim_dir, link_path, target_is_directory=True)
            except OSError:
                self.skipTest("Symlink creation requires elevated privileges on this OS")

            with patch("contextzip.applier.tempfile.gettempdir", return_value=str(fake_temp)):
                _cleanup_extraction_dir(link_path)

            self.assertTrue(victim_dir.exists())
            self.assertTrue(victim_file.exists())


class TestApiKeyMasking(unittest.TestCase):
    def test_mask_reveals_only_last_four_chars(self) -> None:
        key = "AIzaSyD-1234567890abcdefghijklmnopWXYZ"
        masked = _mask_api_key(key)
        self.assertEqual(masked, "****WXYZ")
        self.assertNotIn("AIza", masked)

    def test_mask_hides_short_or_empty_keys_completely(self) -> None:
        self.assertEqual(_mask_api_key(""), "****")
        self.assertEqual(_mask_api_key("short"), "****")
        self.assertEqual(_mask_api_key("12345678"), "****")


if __name__ == "__main__":
    unittest.main()
