from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal as D
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import fill_pile_tip_suppot_info as app
import fill_jiban_shogen as base
import fill_suppot_info as support
from test_fill_suppot_info import ndu_bytes
from tests.fixture_paths import IMACHO_RIGHT_NDU, IMACHO_RIGHT_SDC


def sdc_bytes():
    return (
        "杭長,突出長,根入れ深さ,その他\r\n3,0,0.5,0\r\n"
        "（１）橋軸方向\r\nf）杭先端の地盤ばね値\r\n他方向は参照しない\r\n"
        "g）杭先端の支持力\r\n（２）直角方向\r\n"
        "杭列数,奥行き本数,,1/β(m)\r\n,奇数列,偶数列\r\n2,3,2,0.5\r\n"
        "f）杭先端の地盤ばね値\r\n杭先端の鉛直鉛直ばね値(kN/m)\r\n"
        "長期,,短期(第1勾配),,短期(第2勾配)\r\n1列目,2列目,1列目,2列目,1列目,2列目\r\n"
        "50,60,100.125,120,20.375,30\r\n\r\n"
        "杭先端の水平ばね値(kN/m)\r\n長期,,短期\r\n1列目,2列目,1列目,2列目\r\n"
        "0,0,0,0\r\n\r\n"
        "杭先端の回転ばね値(kN/m)\r\n長期,,短期\r\n1列目,2列目,1列目,2列目\r\n"
        "0,0,0,0\r\n\r\n"
        "g）杭先端の支持力\r\n地震時：杭先端の鉛直地盤支持力(kN)\r\n"
        "押し込み側(降伏点),,押し込み側(終局点)\r\n1列目,2列目,1列目,2列目\r\n"
        "7.125,8,17.375,18\r\n\r\n"
    ).encode("cp932")


def plan(raw=None, sdc=None, groups=None):
    return app.make_plan(base.parse_ndu(raw if raw is not None else ndu_bytes()),
                         app.parse_sdc(sdc if sdc is not None else sdc_bytes()), groups or {4: 1})


class ParserTests(unittest.TestCase):
    def test_selects_short_term_in_transverse_direction_without_rounding(self):
        profile = app.parse_sdc(sdc_bytes())
        self.assertEqual(profile.length, D(3))
        self.assertEqual(profile.values[1], app.TipValues(D("100.125"), D("20.375"), D("7.125"), D("17.375")))
        self.assertEqual(profile.values[2], app.TipValues(D(120), D(30), D(8), D(18)))

    def test_exact_field_mapping_blanks_and_no_length_or_count_factor(self):
        updates, rows = plan()
        self.assertEqual(updates, {405: ["100.125", "7.125", " ", "20.375", "17.375", " ",
                                         "20.375", "100.125", "20.375", "20.375"]})
        self.assertEqual(rows[0]["node"], 405)  # 最大節点番号702ではない
        self.assertEqual(rows[0]["y_m"], D(13))

    def test_column_selection_and_reversed_element_ends(self):
        raw = ndu_bytes().replace(b"702,405,0,0", b"405,702,0,0")
        updates, _ = plan(raw, groups={4: 2})
        self.assertEqual(updates[405], ["120", "8", " ", "30", "18", " ", "30", "120", "30", "30"])

    def test_header_mismatch_units_and_column_order_rejected(self):
        changes = [("（２）直角方向", "対象外"), ("kN/m)", "kN/m2)"),
                   ("押し込み側(降伏点)", "押し込み側(終局点)"),
                   ("長期,,短期(第1勾配)", "長期,短期(第1勾配),"),
                   ("1列目,2列目", "2列目,1列目"), ("1列目", "奇数列")]
        for old, new in changes:
            with self.subTest(old=old), self.assertRaises(app.InputError):
                app.parse_sdc(sdc_bytes().replace(old.encode("cp932"), new.encode("cp932")))

    def test_missing_duplicate_or_truncated_tables_rejected(self):
        for raw in (sdc_bytes() + "（２）直角方向\r\n".encode("cp932"),
                    sdc_bytes().replace(b"50,60,100.125,120,20.375,30", b"50,60,100.125,120,20.375"),
                    sdc_bytes().replace(b"7.125,8,17.375,18\r\n", b""),
                    sdc_bytes().replace(b"7.125,8,17.375,18\r\n", b"7.125,8,17.375,18\r\n1,2,3,4\r\n"),
                    sdc_bytes().split("g）杭先端の支持力".encode("cp932"))[-1]):
            with self.subTest(raw=raw[-50:]), self.assertRaises(app.InputError):
                app.parse_sdc(raw)

    def test_invalid_values_or_unsupported_nonvertical_springs_rejected(self):
        for old, new in [(b"100.125", b"NaN"), (b"100.125", b"-1"), (b"20.375", b"0"),
                         (b"17.375", b"7"), (b"0,0,0,0", b"0,0,1,0"),
                         (b"3,0,0.5,0", b"3,1,0.5,0")]:
            with self.subTest(new=new), self.assertRaises(app.InputError):
                app.parse_sdc(sdc_bytes().replace(old, new))

    def test_geometry_length_column_and_disconnected_nodes_rejected(self):
        for raw in (ndu_bytes().replace(b"405=3,13,", b"405=3,13.1,"),
                    ndu_bytes().replace(b"405=3,13,", b"405=4,13,"),
                    ndu_bytes().replace(b"203,702,0,0", b"204,702,0,0") + b"JointXY204=3,11,204\r\n"):
            with self.subTest(raw=raw[-40:]), self.assertRaises(app.InputError):
                plan(raw)
        with self.assertRaises(app.InputError):
            plan(groups={4: 3})


class RenderTests(unittest.TestCase):
    def test_ndu_changes_only_tip_and_clears_negative_limits(self):
        raw = ndu_bytes(existing=True)
        updates, _ = plan(raw)
        result, summary = support.render_ndu(raw, updates, set())
        self.assertEqual(summary, {"updated": 1, "added": 0, "support_count": 3})
        before, after = raw.splitlines(keepends=True), result.splitlines(keepends=True)
        changed = [(a, b) for a, b in zip(before, after) if a != b]
        self.assertEqual(len(changed), 1)
        self.assertTrue(changed[0][0].startswith(b"SuppotInfo2= ,405,2,"))
        self.assertEqual(changed[0][1].split(b"=", 1)[1].strip().split(b",")[3:],
                         [v.encode() for v in updates[405]])
        self.assertEqual(app.compare_ndu(result, updates)[0]["status"], "match")
        self.assertEqual(support.render_ndu(result, updates, set())[0], result)

    def test_ndu_adds_tip_and_count_preserving_existing_supports(self):
        raw = ndu_bytes(empty=True)
        updates, _ = plan(raw)
        result, summary = support.render_ndu(raw, updates, set())
        self.assertEqual(summary, {"updated": 0, "added": 1, "support_count": 1})
        added = b"SuppotInfo1= ,405,2," + ",".join(updates[405]).encode() + b"\r\n"
        expected = raw.replace(b"SuppotNum=0", b"SuppotNum=1").replace(b"SuppotRow=0", b"SuppotRow=1")
        expected = expected.replace(b"G_intCHOKU_KISO_Link_Num=0\r\n",
                                    b"G_intCHOKU_KISO_Link_Num=0\r\nSuppot_ChokuKisoCaseNo1=0\r\n")
        self.assertEqual(result, expected.replace(b"ShitenCaseNum=0\r\n", b"ShitenCaseNum=0\r\n" + added))

    def test_zero_and_blank_are_not_equal(self):
        updates, _ = plan()
        raw, _ = support.render_ndu(ndu_bytes(), updates, set())
        raw = raw.replace(b",7.125, ,", b",7.125,0,")
        diff = app.compare_ndu(raw, updates)[0]
        self.assertEqual(diff["status"], "different")
        self.assertEqual(diff["entries"][0]["differences"], [{"field": 6, "actual": "0", "expected": ""}])

    def test_range_and_duplicate_y_supports_rejected(self):
        updates, _ = plan()
        raw = ndu_bytes().replace(b"SuppotInfo2= ,405,2", b"SuppotInfo2=400,405,2")
        with self.assertRaises(app.InputError):
            support.render_ndu(raw, updates, set())
        raw = ndu_bytes().replace(b"SuppotInfo1= ,203,1", b"SuppotInfo1= ,405,2")
        with self.assertRaises(app.InputError):
            support.render_ndu(raw, updates, set())


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sdc, self.ndu = [self.root / ("input" + ext) for ext in (".sdc", ".ndu")]
        for path, data in [(self.sdc, sdc_bytes()), (self.ndu, ndu_bytes())]:
            path.write_bytes(data)
        self.args = ["--sdc", str(self.sdc), "--ndu", str(self.ndu), "--groups", "4:1"]

    def run_cli(self, *args):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return app.main(self.args + list(args))

    def test_dry_run_and_separate_ndu_report_are_repeatable(self):
        self.assertEqual(self.run_cli(), 0)
        self.assertEqual(len(list(self.root.iterdir())), 2)
        output, report = self.root / "out.ndu", self.root / "report.json"
        args = ["--output", str(output), "--report", str(report)]
        self.assertEqual(self.run_cli(*args), 0)
        self.assertEqual(self.run_cli(*args), 0)
        self.assertEqual(self.ndu.read_bytes(), ndu_bytes())
        self.assertEqual(self.sdc.read_bytes(), sdc_bytes())
        self.assertEqual(json.loads(report.read_text(encoding="utf8"))["nodes"][0]["node"], 405)

    def test_existing_tip_output_repairs_missing_cases_without_adding_supports(self):
        raw = b"".join(l for l in ndu_bytes().splitlines(keepends=True)
                       if not l.startswith(b"Suppot_ChokuKisoCaseNo"))
        self.ndu.write_bytes(raw)
        output, report = self.root / "out.ndu", self.root / "report.json"
        self.assertEqual(self.run_cli("--output", str(output), "--report", str(report)), 0)
        result = output.read_bytes()
        self.assertIn(b"Suppot_ChokuKisoCaseNo1=0\r\nSuppot_ChokuKisoCaseNo2=0\r\n", result)
        self.assertEqual(json.loads(report.read_text(encoding="utf8"))["summary"],
                         {"updated": 1, "added": 0, "support_count": 2})
        self.assertEqual(self.ndu.read_bytes(), raw)

    def test_output_and_report_conflicts_rejected_before_writes(self):
        output, report = self.root / "out.ndu", self.root / "report.json"
        report.write_bytes(b"existing report")
        self.assertEqual(self.run_cli("--output", str(output), "--report", str(report)), 1)
        self.assertFalse(output.exists())
        self.assertEqual(report.read_bytes(), b"existing report")
        self.assertEqual(self.run_cli("--output", str(self.ndu)), 1)
        self.assertEqual(self.run_cli("--output", str(self.root / "out.txt")), 1)
        self.assertEqual(self.ndu.read_bytes(), ndu_bytes())

    def test_hardlink_output_rejected(self):
        alias = self.root / "alias.ndu"
        alias.hardlink_to(self.ndu)
        self.assertEqual(self.run_cli("--output", str(alias)), 1)
        self.assertEqual(self.ndu.read_bytes(), ndu_bytes())

    def test_changed_input_before_save_rejected(self):
        original = app.make_plan
        def mutate(*args, **kwargs):
            result = original(*args, **kwargs)
            self.sdc.write_bytes(sdc_bytes() + b"\r\n")
            return result
        output = self.root / "out.ndu"
        with patch.object(app, "make_plan", side_effect=mutate):
            self.assertEqual(self.run_cli("--output", str(output)), 1)
        self.assertFalse(output.exists())


class ActualFileTests(unittest.TestCase):
    def test_snap_ndu_exact_byte_match_all_three_tips(self):
        sdc = IMACHO_RIGHT_SDC.read_bytes()
        ndu = IMACHO_RIGHT_NDU.read_bytes()
        updates, rows = plan(ndu, sdc, {4: 1, 5: 2, 6: 3})
        self.assertEqual([r["node"] for r in rows], [122, 147, 172])
        self.assertEqual(updates[122], ["327072", "7167.5", " ", "88418", "16724.3", " ",
                                        "88418", "327072", "88418", "88418"])
        self.assertEqual(updates[147], ["218048", "4778.4", " ", "58945", "11149.5", " ",
                                        "58945", "218048", "58945", "58945"])
        self.assertEqual(updates[172], updates[122])
        self.assertEqual(support.render_ndu(ndu, updates, set())[0], ndu)
        self.assertEqual([r["status"] for r in app.compare_ndu(ndu, updates)], ["match"] * 3)


if __name__ == "__main__":
    unittest.main()
