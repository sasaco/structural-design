from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal as D, ROUND_HALF_UP
from fractions import Fraction as F
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import fill_suppot_info as app
import fill_jiban_shogen as base


def sdc_bytes():
    return (
        "杭長,突出長,根入れ深さ,その他\r\n3,0,0.5,0\r\n"
        "（１）橋軸方向\r\n別の方向の表\r\n（２）直角方向\r\n"
        "a）杭配置条件および1/β\r\n杭列数,奥行き本数,,1/β(m)\r\n,奇数列,偶数列\r\n1,1,1,0.5\r\n"
        "d）杭周面の鉛直せん断地盤ばね値\r\n【押込み側】\r\n"
        "層番,層厚(m),杭周面の鉛直せん断地盤ばね値(kN/m2)\r\n"
        ",,長期,短期(使用性・安全性),短期(復旧性・地震時-第1勾配),,短期(復旧性・地震時-第2勾配)\r\n"
        ",,1列目,1列目,⊿l(m),1列目,1列目\r\n"
        "1,1.5,1,2,1,10,0.1\r\n2,1,3,4,1,30,0.2\r\n"
        "【引抜き側】\r\nこの方式では使わない表\r\n"
        "e）杭周面の支持力\r\n【押込み側】\r\n"
        "層番,層厚,⊿l(m),地震時：杭周面支持力(kN/m)\r\n"
        ",,,降伏点(ρgfy考慮),終局点(ρgfu考慮)\r\n,,,1列目,1列目\r\n"
        "1,1.5,1,2,3\r\n2,1.5,1,6,7\r\n"
        "【引抜き側】\r\nこの方式では使わない表\r\nf）杭先端の地盤ばね値\r\n"
    ).encode("cp932")


def support(number, node, direction=2, values=None):
    values = values or ["91", "92", "93", "94", "95", "96", "97", "98", "99", "100"]
    return f"SuppotInfo{number}= ,{node},{direction}," + ",".join(values) + "\r\n"


def ndu_bytes(existing=False, empty=False):
    # 節点番号は幾何学的な順序と異なる。部材番号にも一致しない。
    body = (
        "DataName=支点の検証\r\nKGInfo4=21,98,100\r\n"
        "JointXY501=3,10,501\r\nJointXY203=3,11,203\r\nJointXY702=3,12,702\r\nJointXY405=3,13,405\r\n"
        "ElementInfo98=3,0,0,0,501,203,0,0\r\nElementInfo99=3,0,0,0,203,702,0,0\r\n"
        "ElementInfo100=3,0,0,0,702,405,0,0\r\n"
        "JibanShogenInfo98= ,10,20,30, , ,\r\nJibanShogenInfo99= ,40,50,60, , ,\r\n"
        "JibanShogenInfo100= ,70,80,90, , ,\r\n"
    )
    entries = "" if empty else support(1, 203, 1) + support(2, 405)
    if existing:
        entries += support(3, 203).replace(",91,", ", 91 ,")
    total = 0 if empty else 3 if existing else 2
    cases = "".join(f"Suppot_ChokuKisoCaseNo{i}=0\r\n" for i in range(1, total + 1))
    return (body + f"SuppotNum={total}\r\nSuppotRow={total}\r\nSuppotAlf=0,0\r\nShitenCaseNum=0\r\n"
            + entries + "FootRow=0\r\nChokuKisoNum=0\r\nG_intCHOKU_KISO_Link_Num=0\r\n"
            + cases + "Untouched= 末尾の空白 ").encode("cp932")


def plan(raw=None, sdc=None, **kwargs):
    return app.make_plan(base.parse_ndu(raw or ndu_bytes()), app.parse_sdc(sdc or sdc_bytes()), {4: 1}, **kwargs)


class ParseAndCalculationTests(unittest.TestCase):
    def test_headers_and_shortened_last_layer(self):
        p = app.parse_sdc(sdc_bytes())
        self.assertEqual(p.length, D(3))
        self.assertEqual((p.layers[1].top, p.layers[1].bottom, p.layers[1].active_bottom), (D("1.5"), D(3), D("2.5")))
        self.assertEqual(p.layers[0].values[1], (D(10), D(2)))

    def test_bad_headers_and_column_order_rejected(self):
        for old, new in [("（２）直角方向", "他方向"), ("kN/m2", "kN/m3"),
                         ("1列目", "奇数列"), ("降伏点(ρgfy考慮)", "終局点(ρgfu考慮)"),
                         ("⊿l(m),1列目", "1列目,⊿l(m)")]:
            with self.subTest(old=old), self.assertRaises(app.InputError):
                app.parse_sdc(sdc_bytes().replace(old.encode("cp932"), new.encode("cp932")))

    def test_invalid_numbers_and_geology_rejected(self):
        for old, new in [(b"1,1.5,1,2,3", b"1,1.5,1,NaN,3"),
                         (b"1,1.5,1,2,3", b"1,1.5,1,-2,3"),
                         (b"2,1.5,1,6,7", b"3,1.5,1,6,7"),
                         (b"2,1.5,1,6,7", b"2,1.6,1,6,7"),
                         (b"2,1,3,4,1,30", b"2,1,3,4,0.8,30"),
                         (b"3,0,0.5,0", b"3,1,0.5,0"),
                         (b"3,0,0.5,0", b"3,0,3,0")]:
            with self.subTest(new=new), self.assertRaises(app.InputError):
                app.parse_sdc(sdc_bytes().replace(old, new))

    def test_missing_rows_and_duplicate_section_rejected(self):
        for raw in [sdc_bytes().replace(b"2,1,3,4,1,30,0.2\r\n", b""),
                    sdc_bytes() + "（２）直角方向\r\n".encode("cp932")]:
            with self.assertRaises(app.InputError):
                app.parse_sdc(raw)

    def test_nonconsecutive_node_ids_zero_region_and_tip(self):
        updates, zeros, tips, rows = plan()
        self.assertEqual(set(updates), {203, 702})
        self.assertEqual(zeros, {501})
        self.assertEqual(tips, {405})
        self.assertEqual(updates[203], ["10", "2", "2", "10", "2", "2", "10", "10", "10", "10"])
        self.assertEqual(updates[702][0:2], ["30", "6"])
        self.assertEqual(sum(p["length_m"] for r in rows for p in r["pieces"]), D(2))

    def test_layer_crossing_integrates_and_does_not_average(self):
        raw = ndu_bytes().replace(b"203=3,11,", b"203=3,11.4,")
        updates, _, _, rows = plan(raw)
        row = next(r for r in rows if r["node"] == 203)
        # 節点203の負担区間0.7～1.7: 第一層0.8、第二層0.2。
        self.assertEqual(row["raw_k1_kN_per_m"], D(14))
        self.assertEqual(row["raw_fy_kN"], D("2.8"))
        # 最終内部節点は1.7～2.5の0.8m。平均値30ではなく24。
        self.assertEqual(updates[702][:2], ["24", "4.8"])

    def test_initial_exclusion_can_clip_partial_node_interval(self):
        raw = ndu_bytes().replace(b"203=3,11,", b"203=3,11.4,")
        updates, zeros, _, rows = plan(raw)
        self.assertIn(501, updates)
        self.assertFalse(zeros)
        self.assertEqual(updates[501][:2], ["2", "0.4"])

    def test_round_half_up_after_sum(self):
        raw = sdc_bytes().replace(b",10,0.1", b",10.5,0.1").replace(b"1,1.5,1,2,3", b"1,1.5,1,2.05,3")
        updates, _, _, _ = plan(sdc=raw)
        self.assertEqual(updates[203][:2], ["11", "2.1"])

    def test_small_positive_values_not_silently_rounded_to_zero(self):
        with self.assertRaises(app.InputError):
            plan(sdc=sdc_bytes().replace(b",10,0.1", b",0.1,0.1"))

    def test_geometry_mismatch_and_missing_columns_rejected(self):
        for raw in [ndu_bytes().replace(b"405=3,13,", b"405=3,13.1,"),
                    ndu_bytes().replace(b"203=3,11,", b"203=4,11,")]:
            with self.assertRaises(app.InputError):
                plan(raw)
        with self.assertRaises(app.InputError):
            app.make_plan(base.parse_ndu(ndu_bytes()), app.parse_sdc(sdc_bytes()), {4: 2})

    def test_tip_shaft_overlap_is_not_silently_dropped(self):
        raw = sdc_bytes().replace(b"3,0,0.5,0", b"3,0,0.1,0")
        raw = raw.replace(b"2,1,3,4,1,30", b"2,1.4,3,4,1.4,30")
        raw = raw.replace(b"2,1.5,1,6,7", b"2,1.5,1.4,6,7")
        with self.assertRaisesRegex(app.InputError, "先端ばねとの合成"):
            plan(sdc=raw)


class NduWriterTests(unittest.TestCase):
    def setUp(self):
        self.updates, self.zeros, _, _ = plan()

    def test_empty_support_table_adds_rows_counts_and_cases(self):
        raw = ndu_bytes(empty=True)
        result, summary = app.render_ndu(raw, self.updates, self.zeros)
        self.assertEqual(summary, {"updated": 0, "added": 2, "support_count": 2})
        expected = raw.replace(b"SuppotNum=0", b"SuppotNum=2").replace(b"SuppotRow=0", b"SuppotRow=2")
        added = (support(1, 203, values=self.updates[203]) + support(2, 702, values=self.updates[702])).encode()
        expected = expected.replace(b"G_intCHOKU_KISO_Link_Num=0\r\n",
                                    b"G_intCHOKU_KISO_Link_Num=0\r\nSuppot_ChokuKisoCaseNo1=0\r\nSuppot_ChokuKisoCaseNo2=0\r\n")
        self.assertEqual(result, expected.replace(b"ShitenCaseNum=0\r\n", b"ShitenCaseNum=0\r\n" + added))

    def test_existing_update_preserves_other_fields_tip_other_direction_and_bytes(self):
        raw = ndu_bytes(existing=True)
        result, summary = app.render_ndu(raw, self.updates, self.zeros)
        self.assertEqual(summary, {"updated": 1, "added": 1, "support_count": 4})
        self.assertIn(support(1, 203, 1).encode(), result)
        self.assertIn(support(2, 405).encode(), result)
        self.assertIn(b"SuppotInfo3= ,203,2, 10 ,2,2,10,2,2,10,10,10,10\r\n", result)
        self.assertIn(b"Suppot_ChokuKisoCaseNo3=0\r\nSuppot_ChokuKisoCaseNo4=0\r\n", result)
        self.assertTrue(result.endswith("Untouched= 末尾の空白 ".encode("cp932")))
        self.assertEqual(app.render_ndu(result, self.updates, self.zeros)[0], result)

    def test_lf_and_final_no_newline_preserved(self):
        raw = ndu_bytes(empty=True).replace(b"\r\n", b"\n")
        result, _ = app.render_ndu(raw, self.updates, self.zeros)
        self.assertNotIn(b"\r", result)
        self.assertEqual(result.splitlines()[-1], raw.splitlines()[-1])

    def test_duplicate_keys_counts_cases_and_ranges_fail(self):
        raw = ndu_bytes(existing=True)
        variants = [raw.replace(b"SuppotRow=3", b"SuppotRow=4"),
                    raw.replace(b"ShitenCaseNum=0", b"ShitenCaseNum=1"),
                    raw.replace(b"SuppotInfo3= ,203", b"SuppotInfo2= ,203"),
                    raw.replace(b"SuppotInfo3= ,203", b"SuppotInfo3=200,203"),
                    raw.replace(b"SuppotInfo2= ,405", b"SuppotInfo2= ,203"),
                    raw.replace(b"SuppotInfo3= ,203", b"SuppotInfo3= ,501")]
        for variant in variants:
            with self.subTest(variant=variant[-200:]), self.assertRaises(app.InputError):
                app.render_ndu(variant, self.updates, self.zeros)


class SupportCaseTests(unittest.TestCase):
    def test_missing_cases_repaired_even_without_support_additions(self):
        raw = ndu_bytes(existing=True)
        for numbers in ([2], [1, 2, 3]):
            with self.subTest(numbers=numbers):
                incomplete = raw
                for n in numbers:
                    incomplete = incomplete.replace(f"Suppot_ChokuKisoCaseNo{n}=0\r\n".encode(), b"")
                result, summary = app.render_ndu(incomplete, {}, set())
                records = dict(l.split(b"=", 1) for l in result.splitlines() if b"=" in l)
                self.assertEqual(summary, {"updated": 0, "added": 0, "support_count": 3})
                self.assertEqual({k: v for k, v in records.items() if k.startswith(b"Suppot_ChokuKisoCaseNo")},
                                 {f"Suppot_ChokuKisoCaseNo{n}".encode(): b"0" for n in range(1, 4)})
                self.assertEqual(app.render_ndu(result, {}, set())[0], result)

    def test_existing_nonzero_values_and_bytes_survive_append(self):
        raw = ndu_bytes(existing=True).replace(b"Suppot_ChokuKisoCaseNo2=0", b"Suppot_ChokuKisoCaseNo2= 7 ")
        updates, zeros, _, _ = plan()
        result, _ = app.render_ndu(raw, updates, zeros)
        self.assertIn(b"Suppot_ChokuKisoCaseNo2= 7 \r\n", result)
        self.assertIn(b"Suppot_ChokuKisoCaseNo4=0\r\n", result)
        self.assertEqual(app.render_ndu(result, updates, zeros)[0], result)

    def test_surplus_zero_cases_removed_after_count_decrease_including_zero(self):
        raw = ndu_bytes(existing=True)
        # 末尾の支点を削除し件数を直した入力。ケース行の消し忘れを補正する。
        for total in (2, 0):
            with self.subTest(total=total):
                reduced = raw.replace(b"SuppotNum=3", f"SuppotNum={total}".encode()).replace(
                    b"SuppotRow=3", f"SuppotRow={total}".encode())
                reduced = b"".join(l for l in reduced.splitlines(keepends=True)
                                   if not l.startswith(b"SuppotInfo") or int(l.split(b"=", 1)[0][10:]) <= total)
                result, _ = app.render_ndu(reduced, {}, set())
                expected = b"".join(l for l in reduced.splitlines(keepends=True)
                                    if not l.startswith(b"Suppot_ChokuKisoCaseNo")
                                    or int(l.split(b"=", 1)[0].removeprefix(b"Suppot_ChokuKisoCaseNo")) <= total)
                self.assertEqual(result, expected)

    def test_stale_zero_cases_are_replaced_when_supports_are_added(self):
        raw = ndu_bytes(empty=True).replace(b"Untouched=", b"".join(
            f"Suppot_ChokuKisoCaseNo{n}= 0 \r\n".encode() for n in range(1, 4)) + b"Untouched=")
        updates, zeros, _, _ = plan()
        result, _ = app.render_ndu(raw, updates, zeros)
        expected, _ = app.render_ndu(ndu_bytes(empty=True), updates, zeros)
        self.assertEqual(result, expected)

    def test_orphan_nonzero_case_cannot_be_reused_for_a_new_support(self):
        raw = ndu_bytes().replace(b"Untouched=", b"Suppot_ChokuKisoCaseNo3=5\r\nUntouched=")
        updates, zeros, _, _ = plan()
        with self.assertRaisesRegex(app.InputError, "非0"):
            app.render_ndu(raw, updates, zeros)

    def test_invalid_case_keys_values_and_duplicates_rejected(self):
        for line in (b"Suppot_ChokuKisoCaseNo1=0", b"Suppot_ChokuKisoCaseNo0=0",
                     b"Suppot_ChokuKisoCaseNo01=0", b"Suppot_ChokuKisoCaseNoX=0",
                     b"Suppot_ChokuKisoCaseNo3=-1", b"Suppot_ChokuKisoCaseNo3=1.5",
                     b"Suppot_ChokuKisoCaseNo3=", b"Suppot_ChokuKisoCaseNo3=NaN"):
            with self.subTest(line=line), self.assertRaises(app.InputError):
                app.render_ndu(ndu_bytes().replace(b"Untouched=", line + b"\r\nUntouched="), {}, set())

    def test_missing_case_block_without_foundation_anchor_and_final_newline(self):
        raw = ndu_bytes(existing=True)
        raw = raw[:raw.index(b"FootRow=")].rstrip(b"\r\n")
        result, _ = app.render_ndu(raw, {}, set())
        self.assertEqual(result, raw + b"\r\n" + b"".join(
            f"Suppot_ChokuKisoCaseNo{n}=0\r\n".encode() for n in range(1, 4)))


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sdc, self.ndu = [self.root / s for s in ("input.sdc", "input.ndu")]
        for p, raw in [(self.sdc, sdc_bytes()), (self.ndu, ndu_bytes())]:
            p.write_bytes(raw)
        self.output, self.report = self.root / "out.ndu", self.root / "out.json"
        self.args = ["--sdc", str(self.sdc), "--ndu", str(self.ndu), "--groups", "4:1"]

    def invoke(self, extra=()):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return app.main(self.args + list(extra))

    def test_preview_and_explicit_profile_output_roundtrip(self):
        self.assertEqual(self.invoke(), 0)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.invoke(["--output", str(self.output)]), 1)
        self.assertFalse(self.output.exists())
        args = ["--profile", "existing-screen", "--output", str(self.output), "--report", str(self.report)]
        self.assertEqual(self.invoke(args), 0)
        self.assertEqual(self.invoke(args), 0)
        report = json.loads(self.report.read_bytes())
        self.assertEqual(report["configuration"]["capacity_divisor"], "1")
        self.assertEqual(report["nodes"][0]["field4_to_13"][1], "2")
        self.assertEqual(self.ndu.read_bytes(), ndu_bytes())
        self.assertEqual(self.sdc.read_bytes(), sdc_bytes())

    def test_collision_conflict_and_preflight_no_partial_output(self):
        for extra in [["--output", str(self.ndu)], ["--report", str(self.sdc)],
                      ["--output", str(self.output), "--report", str(self.output)]]:
            self.assertEqual(self.invoke(["--profile", "existing-screen"] + extra), 1)
        self.report.write_bytes(b"existing report")
        args = ["--profile", "existing-screen", "--output", str(self.output), "--report", str(self.report)]
        self.assertEqual(self.invoke(args), 1)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.report.read_bytes(), b"existing report")

    def test_invalid_sdc_leaves_destinations_absent(self):
        self.sdc.write_bytes(sdc_bytes().replace(b",10,0.1", b",NaN,0.1"))
        self.assertEqual(self.invoke(["--profile", "existing-screen", "--output", str(self.output), "--report", str(self.report)]), 1)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.report.exists())

    def test_orphan_case_leaves_output_and_report_absent(self):
        raw = ndu_bytes().replace(b"Untouched=", b"Suppot_ChokuKisoCaseNo3=7\r\nUntouched=")
        self.ndu.write_bytes(raw)
        self.assertEqual(self.invoke(["--profile", "existing-screen", "--output", str(self.output),
                                     "--report", str(self.report)]), 1)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.report.exists())
        self.assertEqual(self.ndu.read_bytes(), raw)

    def test_changed_input_is_rejected_before_writing(self):
        original = app.render_ndu
        def render_and_change(*args):
            result = original(*args)
            self.sdc.write_bytes(b"changed externally")
            return result
        with patch.object(app, "render_ndu", side_effect=render_and_change):
            self.assertEqual(self.invoke(["--profile", "existing-screen", "--output", str(self.output)]), 1)
        self.assertFalse(self.output.exists())

    def test_save_new_refuses_conflicting_file_created_after_preflight(self):
        self.output.write_bytes(b"concurrent content")
        with self.assertRaises(app.InputError):
            app.save_new(self.output, b"our content")
        self.assertEqual(self.output.read_bytes(), b"concurrent content")
        app.save_new(self.output, b"concurrent content")


class RealFixtureTests(unittest.TestCase):
    def test_all_60_nodes_against_independent_fraction_integrals(self):
        ndu_path = app.ROOT / "snap/今町橋りょう4P(C方向･右押し→).ndu"
        sdc_path = app.ROOT / "snap/今町橋りょう4P(右).sdc"
        model_raw, sdc_raw = ndu_path.read_bytes(), sdc_path.read_bytes()
        updates, zeros, tips, _ = app.make_plan(base.parse_ndu(model_raw), app.parse_sdc(sdc_raw), {4: 1, 5: 2, 6: 3})
        self.assertEqual((len(updates), len(zeros), len(tips)), (60, 12, 3))
        records = dict(s.split("=", 1) for s in model_raw.decode("cp932").splitlines() if "=" in s)
        lines = sdc_raw.decode("cp932").splitlines()
        edges = [F(0)]
        forces, stiffness = [], []
        for i in range(7):
            f = [F(x.strip()) for x in lines[243 + i].split(",")]
            forces.append(f)
            stiffness.append([F(x.strip()) for x in lines[211 + i].split(",")])
            edges.append(edges[-1] + f[1])
        checked = 0
        for group, col in [(4, 1), (5, 2), (6, 3)]:
            start, end = map(int, records[f"KGInfo{group}"].split(",")[1:3])
            ids = {int(n) for e in range(start, end + 1) for n in records[f"ElementInfo{e}"].split(",")[4:6]}
            nodes = sorted((F(records[f"JointXY{n}"].split(",")[1]), n) for n in ids)
            origin = nodes[0][0]
            for i, (y, node) in enumerate(nodes[:-1]):
                a, b = (nodes[max(i - 1, 0)][0] + y) / 2 - origin, (nodes[i + 1][0] + y) / 2 - origin
                points = sorted({a, b, *(z for z in edges + [F("4.945"), F("29.7")] if a < z < b)})
                k, f = F(0), F(0)
                for u, v in zip(points, points[1:]):
                    midpoint = (u + v) / 2
                    if not F("4.945") < midpoint < F("29.7"):
                        continue
                    j = next(j for j in range(7) if edges[j] < midpoint < edges[j + 1])
                    k += stiffness[j][8 + col] * (v - u)
                    f += forces[j][2 + col] * (v - u)
                if not k:
                    self.assertIn(node, zeros)
                    continue
                expected = [(D(v.numerator) / D(v.denominator)).quantize(unit, rounding=ROUND_HALF_UP)
                            for v, unit in [(k, D(1)), (f, D("0.1"))]]
                self.assertEqual([D(updates[node][j]) for j in [0, 1]], expected)
                checked += 1
        self.assertEqual(checked, 60)
        result, summary = app.render_ndu(model_raw, updates, zeros)
        self.assertEqual(summary, {"updated": 60, "added": 0, "support_count": 126})
        for old, new in zip(model_raw.splitlines(keepends=True), result.splitlines(keepends=True)):
            if old != new:
                self.assertTrue(old.startswith(b"SuppotInfo"))
                self.assertEqual(old.split(b",")[:3], new.split(b",")[:3])
                node = int(old.split(b",")[1])
                self.assertIn(node, updates)


if __name__ == "__main__":
    unittest.main()
