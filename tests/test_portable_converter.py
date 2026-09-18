"""GUI共用サービスの統合、保存障害、競合、原本保護を検証する。"""

from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import portable_converter as app
from test_fill_pile_tip_suppot_info import sdc_bytes
from test_fill_suppot_info import ndu_bytes
from tests.fixture_paths import IMACHO_RIGHT_NDU, IMACHO_RIGHT_SDC


class PortableTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="変換テスト ")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.sdc, self.ndu = [self.folder / ("入力データ." + ext) for ext in ("sdc", "ndu")]
        self.sdc.write_bytes(sdc_bytes())
        self.ndu.write_bytes(ndu_bytes(empty=True))
        self.request = app.Request(self.sdc, self.ndu, operations=("tip",), groups=("4:1",))
        self.output = self.folder / "保存 結果.ndu"

    def plan(self):
        return app.prepare(self.request)

    def assert_clean(self):
        self.assertFalse(list(self.folder.glob("*.tmp")))
        self.assertFalse(list(self.folder.glob("*.report.json")))

    def test_prepare_does_not_write_and_new_save_preserves_sources(self):
        original = {p: p.read_bytes() for p in (self.sdc, self.ndu)}
        plan = self.plan()
        self.assertEqual(len(list(self.folder.iterdir())), 2)
        self.assertEqual(plan.rows[0].before, "（支点を追加）")
        saved = app.save(plan, self.output)
        self.assertIsNone(saved.backup)
        self.assertEqual(self.output.read_bytes(), plan.data)
        self.assertEqual({p: p.read_bytes() for p in original}, original)
        self.assertEqual(app.support_values(plan.data)[405][0], "100.125")
        report = json.loads(saved.report.read_text(encoding="utf-8"))
        self.assertEqual(report["output_sha256"], app.digest(plan.data))
        self.assertEqual(report["sources"][0]["sha256"], app.digest(original[self.sdc]))
        self.assertIn(b"Suppot_ChokuKisoCaseNo1=0\r\n", plan.data)

    def test_overwrite_keeps_exact_original_in_unique_backups(self):
        original = self.ndu.read_bytes()
        plan = self.plan()
        saved = app.save(plan, self.ndu, overwrite=True)
        self.assertEqual(saved.backup.read_bytes(), original)
        self.assertEqual(self.ndu.read_bytes(), plan.data)
        again = app.save(self.plan(), self.ndu, overwrite=True)
        self.assertNotEqual(saved.backup, again.backup)
        self.assertEqual(again.backup.read_bytes(), plan.data)
        self.assertEqual(saved.backup.read_bytes(), original)

    def test_invalid_operations_and_groups_and_direction(self):
        for changes in ({"operations": ()}, {"operations": ("unknown",)},
                        {"operations": ("tip", "tip")}, {"groups": ()},
                        {"groups": ("4:1", "4:2")}, {"push_direction": "unknown"},
                        {"operations": ("shaft",)}):
            with self.subTest(changes=changes), self.assertRaises(app.InputError):
                app.prepare(replace(self.request, **changes))

    def test_invalid_output_modes_and_suffixes(self):
        plan = self.plan()
        for output, overwrite in ((self.ndu, False), (self.sdc, False), (self.output, True),
                                  (self.folder / "output.txt", False), (self.folder / "absent/out.ndu", False)):
            with self.subTest(output=output, overwrite=overwrite), self.assertRaises(app.InputError):
                app.save(plan, output, overwrite=overwrite)
        self.assert_clean()

    def test_existing_destination_is_never_overwritten(self):
        self.output.write_bytes(b"existing unrelated content")
        with self.assertRaises(app.InputError):
            app.save(self.plan(), self.output)
        self.assertEqual(self.output.read_bytes(), b"existing unrelated content")
        self.assert_clean()

    def test_hardlink_to_input_is_rejected(self):
        os.link(self.ndu, self.output)
        with self.assertRaises(app.InputError):
            app.save(self.plan(), self.output)
        self.assertEqual(self.ndu.read_bytes(), ndu_bytes(empty=True))

    def test_changed_input_after_preview_is_rejected(self):
        plan = self.plan()
        self.sdc.write_bytes(self.sdc.read_bytes() + b"\r\n")
        with self.assertRaisesRegex(app.InputError, "変更"):
            app.save(plan, self.output)
        self.assertFalse(self.output.exists())
        self.assert_clean()

    def test_input_change_during_staging_is_rechecked(self):
        plan = self.plan()
        original_stage = app.stage
        def stage_and_change(path, data):
            staged = original_stage(path, data)
            self.sdc.write_bytes(b"external change")
            return staged
        with patch.object(app, "stage", side_effect=stage_and_change), self.assertRaises(app.InputError):
            app.save(plan, self.output)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.sdc.read_bytes(), b"external change")
        self.assert_clean()

    def test_output_created_by_another_process_is_preserved(self):
        plan = self.plan()
        original_verify = app.verify_sources
        calls = 0
        def racing_verify(p):
            nonlocal calls
            calls += 1
            original_verify(p)
            if calls == 2:
                self.output.write_bytes(b"concurrent writer")
        with patch.object(app, "verify_sources", side_effect=racing_verify), self.assertRaises(FileExistsError):
            app.save(plan, self.output)
        self.assertEqual(self.output.read_bytes(), b"concurrent writer")
        self.assert_clean()

    def test_staging_failure_never_changes_original(self):
        plan = self.plan()
        with patch.object(app.os, "fsync", side_effect=OSError("disk full")), self.assertRaises(OSError):
            app.save(plan, self.ndu, overwrite=True)
        self.assertEqual(self.ndu.read_bytes(), ndu_bytes(empty=True))
        self.assert_clean()
        self.assertFalse(list(self.folder.glob("*.bak")))

    def test_report_failure_never_changes_original(self):
        plan = self.plan()
        original_publish = app.publish_new
        def fail_report(temp, output):
            if output.suffix == ".json":
                raise OSError("report failed")
            original_publish(temp, output)
        with patch.object(app, "publish_new", side_effect=fail_report), self.assertRaises(OSError):
            app.save(plan, self.ndu, overwrite=True)
        self.assertEqual(self.ndu.read_bytes(), ndu_bytes(empty=True))
        self.assert_clean()
        self.assertEqual(len(list(self.folder.glob("*.bak"))), 1)

    def test_locked_target_retains_original_and_backup(self):
        plan = self.plan()
        with patch.object(app.os, "replace", side_effect=PermissionError("locked")), self.assertRaises(PermissionError):
            app.save(plan, self.ndu, overwrite=True)
        self.assertEqual(self.ndu.read_bytes(), ndu_bytes(empty=True))
        self.assertEqual(next(self.folder.glob("*.bak")).read_bytes(), ndu_bytes(empty=True))
        self.assert_clean()

class ProjectIntegrationTests(unittest.TestCase):
    def test_all_four_on_real_model_preserve_unrelated_fields_and_sources(self):
        sdc = IMACHO_RIGHT_SDC
        ndu = IMACHO_RIGHT_NDU
        before = {p: p.read_bytes() for p in (sdc, ndu)}
        plan = app.prepare(app.Request(sdc, ndu, shaft_profile="existing-screen"))
        self.assertEqual(len(plan.rows), 207)
        self.assertEqual(app.base.parse_ndu(plan.data).fields("JibanShogenInfo98", 7)[1:4],
                         ["58812", "1162.9", "1307.5"])
        self.assertEqual(app.support_values(plan.data)[122],
                         ["327072", "7167.5", "", "88418", "16724.3", "", "88418", "327072", "88418", "88418"])
        old = before[ndu].splitlines(keepends=True)
        new = plan.data.splitlines(keepends=True)
        self.assertEqual(len(old), len(new))
        changes = [(a, b) for a, b in zip(old, new) if a != b]
        self.assertTrue(changes)
        for a, b in changes:
            key = a.split(b"=", 1)[0]
            self.assertEqual(key, b.split(b"=", 1)[0])
            self.assertTrue(key.startswith((b"JibanShogenInfo", b"SuppotInfo")))
            if key.startswith(b"JibanShogenInfo"):
                aa, bb = a.split(b",") , b.split(b",")
                self.assertEqual(aa[:1] + aa[4:], bb[:1] + bb[4:])
        self.assertEqual({p: p.read_bytes() for p in before}, before)

    def test_selecting_one_operation_never_runs_the_others(self):
        sdc = IMACHO_RIGHT_SDC
        ndu = IMACHO_RIGHT_NDU
        for operation, count in (("horizontal", 72), ("pressure", 72), ("shaft", 60), ("tip", 3)):
            with self.subTest(operation=operation):
                plan = app.prepare(app.Request(sdc, ndu, operations=(operation,), shaft_profile="existing-screen"))
                self.assertEqual(len(plan.rows), count)
                self.assertEqual(set(plan.report["details"]), {operation})


if __name__ == "__main__":
    unittest.main()
