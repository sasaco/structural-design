"""KG対応の誤表示・古いファイルの表示・入力イベント連動を検証。"""

from pathlib import Path
import sys
import tempfile
import time
import tkinter as tk
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import model_preview as preview


def fixture():
    # 部材番号・節点番号を一致させず、対象外の横梁と2本の杭を含む。
    return ("JointXY10=-3,-2\nJointXY20=4,-2\nJointXY30=-3,5\nJointXY40=4,5\n"
            "ElementInfo1=0,0,0,0,10,20\nElementInfo7=0,0,0,0,10,30\n"
            "ElementInfo8=0,0,0,0,20,40\nKGInfo4=99,7,7\nKGInfo5=88,8,8\n").encode("cp932")


class ModelTests(unittest.TestCase):
    def test_geometry_and_kg_use_actual_ids_and_endpoint_fields(self):
        model = preview.parse_model(fixture())
        self.assertEqual(model.joints[10], (-3, -2))
        self.assertEqual(model.elements[7], (10, 30))
        self.assertEqual(preview.selected_elements(model, "4:1 5:2"), ({7, 8}, {4: 1, 5: 2}))
        self.assertEqual(preview.selected_elements(model, "5:1"), ({8}, {5: 1}))
        self.assertEqual(preview.selected_elements(model, ""), (set(), {}))

    def test_invalid_mapping_missing_reversed_and_overlapping_ranges(self):
        model = preview.parse_model(fixture())
        for text in ("4:", "0:1", "4:1 5:1", "4:1 4:2", "999:1"):
            with self.subTest(text=text), self.assertRaises(preview.base.InputError):
                preview.selected_elements(model, text)
        for change in (b"99,7,6", b"99,6,7", b"99,7,8"):
            with self.subTest(change=change), self.assertRaises(preview.base.InputError):
                preview.selected_elements(preview.parse_model(fixture().replace(b"99,7,7", change)), "4:1 5:2")

    def test_missing_joints_duplicate_keys_and_nonfinite_geometry_rejected(self):
        for raw in (b"", fixture().replace(b"10,30", b"10,999"),
                    fixture() + b"JointXY10=0,0\n", fixture().replace(b"-3,-2", b"NaN,-2"),
                    fixture().replace(b"-3,-2", b"1e999,-2")):
            with self.subTest(raw=raw), self.assertRaises(preview.base.InputError):
                preview.parse_model(raw)

    def test_real_ndu_selected_ranges_match_conversion_targets(self):
        model = preview.parse_model(next((ROOT / "snap").glob("*右押し*.ndu")).read_bytes())
        selected, groups = preview.selected_elements(model, "4:1 5:2 6:3")
        expected = set(range(98, 122)) | set(range(123, 147)) | set(range(148, 172))
        self.assertEqual(selected, expected)
        self.assertEqual(selected, {member.number for member in preview.base.collect_members(model.ndu, groups)})


class LivePreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / "入力 モデル.ndu"
        self.source.write_bytes(fixture())
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.path, self.groups = tk.StringVar(), tk.StringVar(value="4:1")
        self.view = preview.ModelPreview(self.root, self.path, self.groups)
        self.view.pack(fill="both", expand=True)
        self.path.set(str(self.source))
        self.wait_for(lambda: self.view.model is not None)

    def wait_for(self, condition):
        deadline = time.monotonic() + 5
        while not condition() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.assertTrue(condition(), self.view.message.get())

    def red_members(self):
        return {int(tag.split(":")[1]) for item in self.view.canvas.find_withtag("selected")
                for tag in self.view.canvas.gettags(item) if tag.startswith("element:")}

    def test_live_changes_clear_stale_red_lines_and_numbers(self):
        self.assertEqual(self.red_members(), {7})
        self.assertEqual(self.view.canvas.itemcget("element:7", "fill"), preview.SELECTED)
        self.groups.set("5:1")
        self.assertEqual(self.red_members(), {8})
        self.assertEqual(self.view.canvas.itemcget("element:7", "fill"), preview.NORMAL)
        for text in ("", "5:", "999:1", "4:1 5:1"):
            self.groups.set(text)
            self.assertEqual(self.red_members(), set())
            self.assertFalse(self.view.canvas.find_withtag("member-label"))
        self.groups.set("4:1 5:2")
        self.assertEqual(self.red_members(), {7, 8})
        self.assertEqual(len(self.view.canvas.find_withtag("member-label")), 2)
        self.view.show_numbers.set(False)
        self.view.draw()
        self.assertFalse(self.view.canvas.find_withtag("member-label"))
        self.assertEqual(self.source.read_bytes(), fixture())

    def test_path_change_clears_diagram_and_ignores_old_async_result(self):
        old_model, old_generation = self.view.model, self.view.generation
        self.path.set(str(self.source.with_name("存在しない.ndu")))
        self.assertIsNone(self.view.model)
        self.assertFalse(self.view.canvas.find_withtag("element"))
        self.view.results.put((old_generation, old_model, None))
        self.wait_for(lambda: "表示できません" in self.view.message.get())
        self.assertIsNone(self.view.model)
        self.path.set(str(self.source))
        self.wait_for(lambda: self.view.model is not None)
        self.assertEqual(self.red_members(), {7})
        self.path.set("")
        self.assertIsNone(self.view.model)


if __name__ == "__main__":
    unittest.main()
