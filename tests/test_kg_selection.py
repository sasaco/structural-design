"""長さの過不足・欠損・読込競合で無効なKGを選択／変換しないことを検証。"""

from decimal import Decimal
from pathlib import Path
import sys
import tempfile
import time
import tkinter as tk
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import fill_jiban_shogen as base
from kg_candidates import inspect_candidates, validate_groups
from kg_selection import KGSelection
import portable_converter as converter


def sdc_bytes(first="1.1", second="1.9"):
    return ("（２）直角方向\nb）水平地盤ばね値\n層番,層厚(m),ばね値\n"
            ",,短期(非線形)-2列目,短期(非線形)-1列目\n"
            f"1,{first},100,200\n2,{second},300,400\n\n").encode("cp932")


def ndu_bytes():
    # KG番号・先頭フィールド・部材番号・端節点番号はそれぞれ異なる。
    return ("KGInfo2=99,40,41\nKGInfo5=88,50,50\nKGInfo9=77,60,60\n"
            "KGInfo12=66,70,70\nKGInfo15=55,80,80\n"
            "JointXY101=4,10\nJointXY102=4,11.3\nJointXY103=4,13\n"
            "JointXY201=8,20\nJointXY202=8,23\n"
            "JointXY301=12,10\nJointXY302=12,12\n"
            "JointXY401=16,10\nJointXY402=16,14\n"
            "ElementInfo40=0,0,0,0,101,102\nElementInfo41=0,0,0,0,102,103\n"
            "ElementInfo50=0,0,0,0,201,202\nElementInfo60=0,0,0,0,301,302\n"
            "ElementInfo70=0,0,0,0,401,402\nElementInfo80=0,0,0,0,501,502\n"
            "JibanShogenInfo40=,,,,,,\nJibanShogenInfo41=,,,,,,\n"
            "JibanShogenInfo50=,,,,,,\nJibanShogenInfo60=,,,,,,\n"
            "JibanShogenInfo70=,,,,,,\nJibanShogenInfo80=,,,,,,\n").encode("cp932")


class CandidateTests(unittest.TestCase):
    def test_actual_ids_and_member_sum_shorter_and_longer(self):
        catalog = inspect_candidates(ndu_bytes(), sdc_bytes())
        self.assertEqual(catalog.thickness, Decimal("3"))
        self.assertEqual(catalog.columns, (1, 2))
        self.assertEqual([item.group for item in catalog.candidates], [2, 5, 9, 12, 15])
        self.assertEqual([item.group for item in catalog.candidates if item.enabled], [2, 5])
        self.assertEqual([item.length for item in catalog.candidates],
                         [Decimal(3), Decimal(3), Decimal(2), Decimal(4), None])
        self.assertEqual((catalog.candidates[0].start, catalog.candidates[0].end), (40, 41))
        for item in catalog.candidates[2:4]:
            self.assertIn("長さ不一致", item.reason)
        self.assertIn("JointXY501", catalog.candidates[4].reason)
        validate_groups(catalog, {2: 1, 5: 2})
        for groups in ({}, {9: 1}, {12: 1}, {15: 1}, {99: 1}, {2: 3}):
            with self.subTest(groups=groups), self.assertRaises(base.InputError):
                validate_groups(catalog, groups)

    def test_decimal_comparison_rounds_totals_to_three_places_half_up(self):
        raw = ndu_bytes().replace(b"4,10", b"4,0").replace(b"4,11.3", b"4,0.1").replace(b"4,13", b"4,0.3")
        self.assertTrue(inspect_candidates(raw, sdc_bytes("0.1", "0.2")).candidates[0].enabled)
        for length, second_layer, enabled in (
            ("0.3000001", "0.2", True),
            ("0.3004999", "0.2", True),
            ("0.3005", "0.2", False),
            ("0.2995", "0.2", True),
            ("0.2994999", "0.2", False),
            # 両方の合計を丸める。差の許容値判定とは異なる。
            ("0.30049", "0.20051", False),
            ("0.30050", "0.20051", True),
        ):
            with self.subTest(length=length, second_layer=second_layer):
                changed = raw.replace(b"4,0.3", f"4,{length}".encode("ascii"))
                catalog = inspect_candidates(changed, sdc_bytes("0.1", second_layer))
                self.assertEqual(catalog.candidates[0].enabled, enabled)
                self.assertEqual(catalog.candidates[0].length, Decimal(length))
                if not enabled:
                    self.assertIn("0.001 m", catalog.candidates[0].reason)

    def test_rounding_is_applied_after_summing_members_and_layers(self):
        raw = ndu_bytes().replace(b"4,11.3", b"4,11.5004").replace(b"4,13", b"4,13.0008")
        catalog = inspect_candidates(raw, sdc_bytes("1.5004", "1.5004"))
        self.assertEqual(catalog.thickness, Decimal("3.0008"))
        self.assertEqual(catalog.candidates[0].length, Decimal("3.0008"))
        self.assertTrue(catalog.candidates[0].enabled)
        # 層ごと／部材ごとに先に丸めると3.000になり、この不一致を見逃す。
        self.assertFalse(inspect_candidates(raw, sdc_bytes("1.5", "1.5")).candidates[0].enabled)
        self.assertFalse(inspect_candidates(ndu_bytes(), sdc_bytes("1.5004", "1.5004")).candidates[0].enabled)

    def test_missing_or_invalid_sdc_keeps_candidates_disabled(self):
        for raw in (None, b"invalid"):
            with self.subTest(raw=raw):
                catalog = inspect_candidates(ndu_bytes(), raw)
                self.assertEqual(len(catalog.candidates), 5)
                self.assertTrue(catalog.error)
                self.assertFalse(any(item.enabled for item in catalog.candidates))
        with self.assertRaisesRegex(base.InputError, "KGInfo"):
            inspect_candidates(b"", sdc_bytes())

    def test_invalid_geometry_only_disables_affected_candidate(self):
        for original, replacement in ((b"101,102", b"101,103"), (b"JointXY102=4", b"JointXY102=5"),
                                      (b"99,40,41", b"99,41,40")):
            catalog = inspect_candidates(ndu_bytes().replace(original, replacement), sdc_bytes())
            self.assertFalse(catalog.candidates[0].enabled)
            self.assertTrue(catalog.candidates[1].enabled)

    def test_prepare_rechecks_lengths_even_for_horizontal_only(self):
        with tempfile.TemporaryDirectory() as folder:
            ndu = Path(folder) / "短い杭.ndu"
            original = base.DEFAULT_NDU.read_bytes()
            shorter = original.replace(b"KGInfo4=21,98,121", b"KGInfo4=21,98,120")
            self.assertNotEqual(original, shorter)
            ndu.write_bytes(shorter)
            with self.assertRaisesRegex(base.InputError, "KGInfo4.*長さ不一致"):
                converter.prepare(converter.Request(base.DEFAULT_SDC, ndu, operations=("horizontal",),
                                                    groups=("4:1",), require_matching_lengths=True))
            self.assertEqual(ndu.read_bytes(), shorter)


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ndu = Path(self.temp.name) / "入力 モデル.ndu"
        self.sdc = Path(self.temp.name) / "参照 地盤.sdc"
        self.ndu.write_bytes(ndu_bytes())
        self.sdc.write_bytes(sdc_bytes())
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.ndu_path, self.sdc_path, self.groups = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.view = KGSelection(self.root, self.ndu_path, self.sdc_path, self.groups)
        self.view.pack()
        self.sdc_path.set(str(self.sdc))
        self.ndu_path.set(str(self.ndu))
        self.wait_for(lambda: self.view.ready)

    def wait_for(self, condition):
        deadline = time.monotonic() + 5
        while not condition() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.assertTrue(condition(), self.view.message.get())

    def test_checkboxes_emit_mapping_and_disabled_rows_never_select(self):
        with self.assertRaisesRegex(base.InputError, "チェック"):
            self.view.selection()
        for group in (9, 12, 15):
            row = self.view.rows[group]
            row.check.invoke()
            self.assertTrue(row.check.instate(["disabled"]))
            self.assertFalse(row.selected.get())
        for group in (2, 5):
            self.view.rows[group].check.invoke()
        self.assertEqual(self.view.selection(), ("2:1", "5:2"))
        self.view.rows[2].check.invoke()
        self.assertEqual(self.view.selection(), ("5:2",))

    def test_duplicate_columns_are_allowed_and_no_text_entry_is_used(self):
        for group in (2, 5):
            self.view.rows[group].check.invoke()
            self.assertEqual(str(self.view.rows[group].combo["state"]), "readonly")
        self.view.rows[5].column.set("1")
        self.view.rows[5].combo.event_generate("<<ComboboxSelected>>")
        self.assertEqual(self.view.selection(), ("2:1", "5:1"))

    def test_direction_buttons_assign_once_and_allow_manual_edits(self):
        self.assertTrue(all(button.instate(["disabled"]) for button in self.view.direction_buttons.values()))
        for group in (5, 2):
            self.view.rows[group].check.invoke()
        self.view.direction_buttons["right"].invoke()
        self.assertEqual(self.view.selection(), ("2:2", "5:1"))
        self.view.rows[2].column.set("1")
        self.view.rows[2].combo.event_generate("<<ComboboxSelected>>")
        self.assertEqual(self.view.selection(), ("2:1", "5:1"))
        # チェック解除・再選択で方向による再設定や共有列の置換をしない。
        self.view.rows[2].check.invoke()
        self.view.rows[2].check.invoke()
        self.assertEqual(self.view.selection(), ("2:1", "5:1"))
        self.view.direction_buttons["left"].invoke()
        self.assertEqual(self.view.selection(), ("2:1", "5:2"))
        self.view.set_locked(True)
        self.view.direction_buttons["right"].invoke()
        self.assertEqual(self.view.selection(), ("2:1", "5:2"))
        self.assertTrue(all(button.instate(["disabled"]) for button in self.view.direction_buttons.values()))
        self.view.set_locked(False)
        self.ndu_path.set("")
        self.assertTrue(all(button.instate(["disabled"]) for button in self.view.direction_buttons.values()))

    def test_unlock_does_not_reenable_invalid_candidates(self):
        self.view.rows[2].check.invoke()
        self.view.set_locked(True)
        self.assertTrue(all(row.check.instate(["disabled"]) for row in self.view.rows.values()))
        self.view.set_locked(False)
        self.assertFalse(self.view.rows[2].check.instate(["disabled"]))
        self.assertTrue(self.view.rows[9].check.instate(["disabled"]))
        self.assertEqual(self.view.selection(), ("2:1",))

    def test_sdc_change_recalculates_and_clears_selection_immediately(self):
        self.view.rows[2].check.invoke()
        second = self.sdc.with_name("短い地盤.sdc")
        second.write_bytes(sdc_bytes("0.5", "1.5"))
        self.sdc_path.set(str(second))
        self.assertEqual(self.groups.get(), "")
        self.assertFalse(self.view.ready)
        self.wait_for(lambda: self.view.ready)
        self.assertEqual([group for group, row in self.view.rows.items() if row.candidate.enabled], [9])
        self.assertTrue(self.view.rows[2].check.instate(["disabled"]))

    def test_old_async_result_cannot_restore_selection_after_path_change(self):
        old_generation, old_catalog = self.view.generation, self.view.catalog
        self.ndu_path.set(str(self.ndu.with_name("存在しない.ndu")))
        self.view.results.put((old_generation, old_catalog, None))
        self.wait_for(lambda: "読み込めません" in self.view.message.get())
        self.assertFalse(self.view.ready)
        self.assertFalse(self.view.rows)
        self.assertEqual(self.groups.get(), "")

    def test_sdc_missing_then_reload_and_no_matching_candidates(self):
        self.sdc_path.set("")
        self.wait_for(lambda: self.view.catalog is not None)
        self.assertFalse(self.view.ready)
        self.assertTrue(all(row.check.instate(["disabled"]) for row in self.view.rows.values()))
        self.sdc.write_bytes(sdc_bytes("10", "20"))
        self.sdc_path.set(str(self.sdc))
        self.wait_for(lambda: self.view.ready)
        self.assertTrue(all(row.check.instate(["disabled"]) for row in self.view.rows.values()))
        self.assertIn("選択可能 0/5件", self.view.message.get())
        self.sdc.write_bytes(sdc_bytes())
        self.view.reload_button.invoke()
        self.wait_for(lambda: self.view.ready)
        self.assertTrue(self.view.rows[2].candidate.enabled)


if __name__ == "__main__":
    unittest.main()
