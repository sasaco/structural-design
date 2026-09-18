"""SDC参照方向と有効抵抗土圧力の区分選択を検証する。"""

from decimal import Decimal as D
from pathlib import Path
import sys
import time
import tkinter as tk
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import fill_jiban_shogen as horizontal
import fill_jiban_pressure as pressure
import fill_suppot_info as shaft
import fill_pile_tip_suppot_info as tip
import portable_converter as converter
import sdc_columns
from sdc_converter_app import App


RIGHT = ROOT / "test/今町橋りょう4P(右).sdc"
LEFT = ROOT / "test/今町橋りょう4P(左).sdc"
R2 = ROOT / "test/R2ラーメンばね1.sdc"
NDU = ROOT / "test/今町橋りょう4P(C方向･右押し→).ndu"


class SectionAndParserTests(unittest.TestCase):
    def test_direction_range_and_invalid_choices(self):
        lines = RIGHT.read_bytes().decode("cp932").splitlines()
        self.assertEqual(sdc_columns.direction_range(lines, "longitudinal"), (22, 159))
        self.assertEqual(sdc_columns.direction_range(lines, "transverse"), (159, len(lines)))
        for value in ("", "axis", "TRANSVERSE"):
            with self.subTest(value=value), self.assertRaises(horizontal.InputError):
                sdc_columns.direction_range(lines, value)
        with self.assertRaises(horizontal.InputError):
            sdc_columns.pressure_case_heading("other")

    def test_actual_right_longitudinal_all_parsers_and_pressure_cases(self):
        raw = RIGHT.read_bytes()
        h = horizontal.parse_sdc(raw, "longitudinal")
        non_response = pressure.parse_pressure_sdc(raw, "longitudinal", "non-response")
        response = pressure.parse_pressure_sdc(raw, "longitudinal", "response")
        s = shaft.parse_sdc(raw, "longitudinal")
        t = tip.parse_sdc(raw, "longitudinal")
        self.assertEqual(h[0].source_line, 39)
        self.assertEqual(h[0].values, dict(enumerate(map(D, (39208,19604,39208,19604,39208)), 1)))
        self.assertEqual(non_response[0].source_line, 52)
        self.assertEqual(non_response[0].values[2], (D("508.5"), D("581.4")))
        self.assertEqual(response[0].source_line, 64)
        self.assertEqual(response[0].values[2], (D("813.5"), D("930.2")))
        self.assertEqual((s.length, s.exclusion, set(s.layers[0].values)), (D(31), D("4.945"), set(range(1,6))))
        self.assertEqual((s.layers[0].spring_line, s.layers[0].force_line), (77, 109))
        self.assertEqual((t.spring_line, t.force_line, set(t.values)), (140, 158, set(range(1,6))))
        self.assertEqual(t.values[2], tip.TipValues(D(109024), D(29473), D("2389.2"), D("5574.8")))

    def test_beta_header_accepts_digit_one_and_fullwidth_letter_l(self):
        original = RIGHT.read_bytes()
        digit = "杭列数,奥行き本数,,1/β(m)".encode("cp932")
        letter = "杭列数,奥行き本数,,ｌ/β(m)".encode("cp932")
        self.assertIn(digit, original)
        for heading, raw in (("1", original), ("ｌ", original.replace(digit, letter))):
            with self.subTest(heading=heading):
                for direction in sdc_columns.DIRECTIONS:
                    horizontal.parse_sdc(raw, direction)
                    pressure.parse_pressure_sdc(raw, direction, "non-response")
                    pressure.parse_pressure_sdc(raw, direction, "response")
                    shaft.parse_sdc(raw, direction)
                    tip.parse_sdc(raw, direction)

    def test_actual_r2_fullwidth_letter_l_beta_header(self):
        raw = R2.read_bytes()
        self.assertIn("杭列数,奥行き本数,,ｌ/β(m)".encode("cp932"), raw)
        for direction, expected_columns in (("longitudinal", 7), ("transverse", 2)):
            with self.subTest(direction=direction):
                shaft_profile = shaft.parse_sdc(raw, direction)
                tip_profile = tip.parse_sdc(raw, direction)
                self.assertEqual(set(shaft_profile.layers[0].values), set(range(1, expected_columns + 1)))
                self.assertEqual(set(tip_profile.values), set(range(1, expected_columns + 1)))

    def test_actual_left_longitudinal_keeps_shared_parity_sources(self):
        raw = LEFT.read_bytes()
        h = horizontal.parse_sdc(raw, "longitudinal")
        p = pressure.parse_pressure_sdc(raw, "longitudinal", "response")
        s = shaft.parse_sdc(raw, "longitudinal")
        t = tip.parse_sdc(raw, "longitudinal")
        self.assertEqual(h[0].values, {1:D(58740), 2:D(29370), 3:D(58740)})
        self.assertEqual([h[0].sources[c].field for c in (1,2,3)], [7,8,7])
        self.assertEqual(p[0].source_line, 57)
        self.assertEqual(p[0].values[3], (D("1252.2"), D("1845.6")))
        self.assertEqual([r.field for r in s.layers[0].sources[3]], [8,4])
        self.assertEqual([r.field for r in t.sources[3].values()], [3,5,1,3])

    def test_selected_direction_does_not_fall_through_to_other_direction(self):
        raw = RIGHT.read_bytes().replace("b）水平地盤ばね値".encode("cp932"),
                                         "b）対象外".encode("cp932"), 1)
        with self.assertRaisesRegex(horizontal.InputError, "橋軸方向"):
            horizontal.parse_sdc(raw, "longitudinal")
        self.assertEqual(horizontal.parse_sdc(raw, "transverse")[0].source_line, 174)


class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.originals = {path:path.read_bytes() for path in (RIGHT, LEFT, NDU)}

    @classmethod
    def tearDownClass(cls):
        assert all(path.read_bytes() == raw for path, raw in cls.originals.items())

    def request(self, **kwargs):
        values = dict(groups=("4:1","5:2","6:3"), push_direction="direct",
                      shaft_profile="existing-screen", sdc_direction="longitudinal",
                      pressure_case="response")
        values.update(kwargs)
        return converter.Request(RIGHT, NDU, **values)

    def test_defaults_are_current_transverse_non_response_and_byte_identical(self):
        plan = converter.prepare(converter.Request(RIGHT, NDU, shaft_profile="existing-screen"))
        self.assertEqual(plan.data, self.originals[NDU])
        self.assertEqual(plan.report["configuration"]["sdc_direction"], "transverse")
        self.assertEqual(plan.report["configuration"]["pressure_case"], "non-response")
        self.assertEqual(plan.report["configuration"]["sdc_direction_label"], "（２）直角方向")
        self.assertEqual(plan.report["configuration"]["pressure_case_label"], "・応答変位法以外の場合")

    def test_longitudinal_response_runs_all_operations_and_records_selection(self):
        plan = converter.prepare(self.request())
        ndu = horizontal.parse_ndu(plan.data)
        self.assertEqual(len(plan.rows), 207)
        self.assertEqual(ndu.fields("JibanShogenInfo98", 7)[1:4], ["39208","2033.9","2286.6"])
        self.assertEqual(ndu.fields("JibanShogenInfo123", 7)[1:4], ["19604","813.5","914.6"])
        config = plan.report["configuration"]
        self.assertEqual((config["sdc_direction_label"], config["pressure_case_label"]),
                         ("（１）橋軸方向", "・応答変位法の場合"))
        sources = plan.report["calculation"]["source_values"]
        self.assertTrue(any(row["operation"] == "pressure" and row["line"] == 64 for row in sources))
        self.assertFalse(any(row["operation"] == "pressure" and row["line"] == 52 for row in sources))
        self.assertEqual(plan.workbook.sheet("水平地盤ばね").rows[0][1].value, "（１）橋軸方向")
        self.assertEqual(plan.workbook.sheet("有効抵抗土圧").rows[0][1].value,
                         "（１）橋軸方向・応答変位法の場合")
        self.assertEqual(plan.workbook.metadata["SDCConverter.PressureCase"], "・応答変位法の場合")
        plan.workbook.to_xlsx()

    def test_pressure_case_only_changes_pressure_operation(self):
        response = converter.prepare(self.request())
        non_response = converter.prepare(self.request(pressure_case="non-response"))
        for operation in ("horizontal", "shaft", "tip"):
            self.assertEqual(response.report["details"][operation], non_response.report["details"][operation])
        a, b = map(horizontal.parse_ndu, (response.data, non_response.data))
        self.assertEqual(a.fields("JibanShogenInfo123",7)[1], b.fields("JibanShogenInfo123",7)[1])
        self.assertNotEqual(a.fields("JibanShogenInfo123",7)[2:4], b.fields("JibanShogenInfo123",7)[2:4])

    def test_invalid_request_choices_fail_before_conversion(self):
        for changes in ({"sdc_direction":"other"}, {"pressure_case":"other"}):
            with self.subTest(changes=changes), self.assertRaises(converter.InputError):
                converter.prepare(self.request(**changes))


class GuiTests(unittest.TestCase):
    def test_defaults_states_and_direction_reload_columns(self):
        root = tk.Tk(); root.withdraw(); self.addCleanup(root.destroy)
        app = App(root)
        self.assertEqual(app.sdc_direction.get(), "transverse")
        self.assertEqual(app.pressure_case.get(), "non-response")
        app.operations["pressure"].set(False); app.apply_states()
        self.assertTrue(all(button.instate(["disabled"]) for button in app.pressure_case_buttons))
        app.operations["pressure"].set(True); app.apply_states()
        self.assertTrue(all(button.instate(["!disabled"]) for button in app.pressure_case_buttons))
        app.paths["sdc"].set(str(RIGHT)); app.paths["ndu"].set(str(NDU))
        self.wait_ready(root, app)
        self.assertEqual(app.group_selector.catalog.columns, (1,2,3))
        for group in (4,5,6): app.group_selector.rows[group].check.invoke()
        self.assertTrue(app.groups.get())
        app.sdc_direction.set("longitudinal")
        self.assertFalse(app.groups.get())
        self.wait_ready(root, app)
        self.assertEqual(app.group_selector.catalog.columns, (1,2,3,4,5))

    def wait_ready(self, root, app):
        deadline = time.monotonic() + 10
        while not app.group_selector.ready and time.monotonic() < deadline:
            root.update(); time.sleep(.01)
        self.assertTrue(app.group_selector.ready, app.group_selector.message.get())


if __name__ == "__main__":
    unittest.main()
