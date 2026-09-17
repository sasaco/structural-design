from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal as D
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import fill_jiban_pressure as app
import fill_jiban_shogen as base


def sdc_bytes():
    return (
        "（１）橋軸方向\r\nc）有効抵抗土圧力\r\n別の表\r\n"
        "（２）直角方向\r\nc）有効抵抗土圧力\r\n・応答変位法以外の場合\r\n"
        "層番,層厚(m),地震時：有効抵抗土圧力(kN/m)\r\n"
        ",,1列目,,2列目,,3列目\r\n,,上側,下側,上側,下側,上側,下側\r\n"
        "1,1.5,100,250,20,50,10,25\r\n2,1.3,1000,2300,200,460,100,230\r\n\r\n"
        "・応答変位法の場合\r\nこの表は使用しない\r\n"
    ).encode("cp932")


def ndu_bytes():
    return (
        "DataName=右基礎の検証\r\nKGInfo4=21,98,99\r\n"
        "JointXY201=29.8,13.041,201\r\nJointXY202=29.8,14.341,202\r\nJointXY203=29.8,15.641,203\r\n"
        "ElementInfo98=3,0,0,0,201,202,75,0\r\nElementInfo99=3,0,0,0,202,203,76,0\r\n"
        "JibanShogenInfo98= ,58812, 77 ,88, , , \r\nJibanShogenInfo99= ,272086, , , , , \r\n"
        "JibanShogenInfo1= ,11,12,13,14,15,16"
    ).encode("cp932")


class PressureTests(unittest.TestCase):
    def setUp(self):
        self.layers = app.parse_pressure_sdc(sdc_bytes())
        self.member = base.Member(98, 4, 1, D(0), D("1.3"))

    def test_heading_selects_non_response_displacement_table(self):
        self.assertEqual(len(self.layers), 2)
        self.assertEqual(self.layers[0].values[3], (D(10), D(25)))
        self.assertEqual(self.layers[1].top, D("1.5"))

    def test_missing_case_and_invalid_subheader_are_rejected(self):
        for raw in [sdc_bytes().replace("・応答変位法以外の場合".encode("cp932"), b"other"),
                    sdc_bytes().replace("上側,下側".encode("cp932"), "下側,上側".encode("cp932"))]:
            with self.assertRaises(base.InputError):
                app.parse_pressure_sdc(raw)

    def test_linear_interpolation_uses_member_end_not_layer_bottom(self):
        pieces = app.collect_pieces(self.member, 3, self.layers)
        self.assertEqual(app.calculate_pair(self.member, pieces, "error"), (D(10), D(23)))

    def test_real_member98_example(self):
        layer = app.PressureLayer(1, D(0), D("1.5"), {3: (D("1162.9"), D("1329.7"))}, 187)
        pieces = app.collect_pieces(self.member, 3, [layer])
        self.assertEqual(app.calculate_pair(self.member, pieces, "error"), (D("1162.9"), D("1307.46")))

    def test_crossing_integrates_separate_linear_segments(self):
        member = base.Member(99, 4, 1, D("1.3"), D("2.6"))
        pieces = app.collect_pieces(member, 3, self.layers)
        expected = D("175.3") / D("1.3")
        self.assertEqual(app.calculate_pair(member, pieces, "integral-average"), (expected, expected))
        self.assertEqual(app.calculate_pair(member, pieces, "endpoints"), (D(23), D(210)))
        with self.assertRaisesRegex(base.InputError, "層境界"):
            app.calculate_pair(member, pieces, "error")

    def test_three_layers_keep_pressure_jumps(self):
        layers = [app.PressureLayer(1, D(0), D(1), {1: (D(1), D(3))}, 1),
                  app.PressureLayer(2, D(1), D(2), {1: (D(10), D(20))}, 2),
                  app.PressureLayer(3, D(2), D(3), {1: (D(100), D(100))}, 3)]
        member = base.Member(1, 4, 1, D(0), D(3))
        self.assertEqual(app.calculate_pair(member, app.collect_pieces(member, 1, layers), "integral-average"), (D(39), D(39)))

    def test_boundary_endpoints_take_values_inside_member(self):
        above = base.Member(1, 4, 1, D(0), D("1.5"))
        below = base.Member(2, 4, 1, D("1.5"), D("2.8"))
        self.assertEqual(app.calculate_pair(above, app.collect_pieces(above, 3, self.layers), "error"), (D(10), D(25)))
        self.assertEqual(app.calculate_pair(below, app.collect_pieces(below, 3, self.layers), "error"), (D(100), D(230)))

    def test_outside_profile_and_extrapolation_are_rejected(self):
        with self.assertRaises(base.InputError):
            app.collect_pieces(base.Member(1, 4, 1, D(0), D(3)), 3, self.layers)
        with self.assertRaises(base.InputError):
            self.layers[0].at(3, D(2))

    def test_push_direction_changes_pressure_column(self):
        ndu = base.parse_ndu(ndu_bytes())
        right, _ = app.make_plan(ndu, self.layers, {4: 1}, "right", 1, "integral-average")
        left, _ = app.make_plan(ndu, self.layers, {4: 1}, "left", 1, "integral-average")
        self.assertEqual(right[98], (D("10.0"), D("23.0")))
        self.assertEqual(left[98], (D("100.0"), D("230.0")))

    def test_rounding_is_half_up_at_both_ends(self):
        layer = app.PressureLayer(1, D(0), D("2.6"), {1: (D("10.05"), D("10.25"))}, 1)
        updates, _ = app.make_plan(base.parse_ndu(ndu_bytes()), [layer], {4: 1}, "right", 1, "error")
        self.assertEqual(updates[98], (D("10.1"), D("10.2")))
        self.assertEqual(updates[99], (D("10.2"), D("10.3")))

    def test_only_pressure_fields_change_in_bytes(self):
        raw = ndu_bytes()
        result = app.render_pressure(base.parse_ndu(raw), {98: (D("1162.9"), D("1307.5")), 99: (D("1989.0"), D("1989.0"))})
        expected = raw.replace(b", 77 ,88,", b", 1162.9 ,1307.5,").replace(b",272086, , ,", b",272086,1989,1989,")
        self.assertEqual(result, expected)


class PressureFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ndu = self.root / "input.ndu"
        self.sdc = self.root / "input.sdc"
        self.output = self.root / "result.ndu"
        self.report = self.root / "report.json"
        self.ndu.write_bytes(ndu_bytes())
        self.sdc.write_bytes(sdc_bytes())
        self.args = ["--ndu", str(self.ndu), "--sdc", str(self.sdc), "--groups", "4:1"]

    def invoke(self, extra):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return app.main(self.args + extra)

    def test_explicit_error_policy_aborts_without_outputs(self):
        self.assertEqual(self.invoke(["--cross-layer", "error", "--output", str(self.output)]), 1)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.ndu.read_bytes(), ndu_bytes())

    def test_preview_and_output_leave_originals_unchanged(self):
        policy = []
        self.assertEqual(self.invoke(policy), 0)
        self.assertFalse(self.output.exists())
        args = policy + ["--output", str(self.output), "--report", str(self.report), "--reference", str(self.ndu)]
        self.assertEqual(self.invoke(args), 0)
        report = json.loads(self.report.read_text(encoding="utf-8"))
        self.assertEqual(report["configuration"]["decimals"], 1)
        self.assertEqual(report["members"][1]["calculated"], ["134.8", "134.8"])
        self.assertEqual(report["summary"]["reference_differing_pairs"], 1)
        self.assertEqual(report["summary"]["reference_missing_pairs"], 1)
        self.assertEqual(self.ndu.read_bytes(), ndu_bytes())
        self.assertEqual(self.sdc.read_bytes(), sdc_bytes())
        self.assertEqual(self.invoke(args), 0)

    def test_existing_destination_is_not_overwritten(self):
        self.output.write_bytes(b"existing")
        self.assertEqual(self.invoke(["--cross-layer", "integral-average", "--output", str(self.output), "--report", str(self.report)]), 1)
        self.assertEqual(self.output.read_bytes(), b"existing")
        self.assertFalse(self.report.exists())

    def test_source_and_report_path_collisions_are_rejected(self):
        for args in [["--output", str(self.ndu)], ["--report", str(self.sdc)],
                     ["--output", str(self.output), "--report", str(self.output)]]:
            self.assertEqual(self.invoke(["--cross-layer", "integral-average"] + args), 1)
        self.assertEqual(self.ndu.read_bytes(), ndu_bytes())
        self.assertEqual(self.sdc.read_bytes(), sdc_bytes())


if __name__ == "__main__":
    unittest.main()
