"""SDC/NDUの対応・層境界・変更範囲を、小さな独立データで検証する。"""

from decimal import Decimal as D
from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import fill_jiban_shogen as app


def sdc_bytes() -> bytes:
    return (
        "（１）橋軸方向\r\nb）水平地盤ばね値\r\n無関係な表\r\n\r\n"
        "（２）直角方向\r\nb）水平地盤ばね値\r\n"
        "層番,層厚(m),水平方向ばね値(kN/m2)\r\n"
        ",,長期-1列目,短期(線形)-1列目,短期(非線形)-2列目,短期(非線形)-1列目\r\n"
        "1,1.500,1,2,50,100\r\n"
        "2,1.300,3,4,150,300\r\n\r\n"
    ).encode("cp932")


def ndu_bytes() -> bytes:
    # 部材番号と節点番号が異なる。上端座標は0ではない。
    return (
        "DataName=試験（右）\r\n"
        "KGInfo4=21,98,99\r\n"
        "JointXY201=29.8,13.041,201\r\n"
        "JointXY202=29.8,14.341,202\r\n"
        "JointXY203=29.8,15.641,203\r\n"
        "ElementInfo98=3,0,0,0,201,202,75,0\r\n"
        "ElementInfo99=3,0,0,0,202,203,76,0\r\n"
        "JibanShogenInfo1= ,91,92,93, , , \r\n"
        "JibanShogenInfo98= , 77 ,1163,1307.5, , , \r\n"
        "JibanShogenInfo99= , ,24,25, , , \r\n"
        "Other=末尾の改行なし"
    ).encode("cp932")


class MappingTests(unittest.TestCase):
    def test_sdc_selects_direction_and_column_by_heading(self):
        layers = app.parse_sdc(sdc_bytes())
        self.assertEqual(layers[0].values, {1: D(100), 2: D(50)})
        self.assertEqual((layers[1].top, layers[1].bottom), (D("1.500"), D("2.800")))

    def test_members_use_element_joint_references_and_relative_depth(self):
        members = app.collect_members(app.parse_ndu(ndu_bytes()), {4: 1})
        self.assertEqual(members[0], app.Member(98, 4, 1, D(0), D("1.300")))
        self.assertEqual(members[1].bottom, D("2.600"))

    def test_same_member_length_can_cover_different_layers(self):
        layers = app.parse_sdc(sdc_bytes())
        members = app.collect_members(app.parse_ndu(ndu_bytes()), {4: 1})
        a, b = [app.find_overlaps(member, layers) for member in members]
        self.assertEqual([(o.layer.number, o.length) for o in a], [(1, D("1.3"))])
        self.assertEqual([(o.layer.number, o.length) for o in b], [(1, D("0.2")), (2, D("1.1"))])

    def test_exact_boundary_does_not_include_adjacent_layer(self):
        layers = app.parse_sdc(sdc_bytes())
        above = app.find_overlaps(app.Member(1, 4, 1, D(0), D("1.5")), layers)
        below = app.find_overlaps(app.Member(2, 4, 1, D("1.5"), D("2.8")), layers)
        self.assertEqual([o.layer.number for o in above], [1])
        self.assertEqual([o.layer.number for o in below], [2])

    def test_render_changes_only_second_field_and_preserves_bytes(self):
        original = ndu_bytes()
        ndu = app.parse_ndu(original)
        result = app.render_ndu(ndu, {98: D(58812), 99: D(42)})
        expected = original.replace(b"= , 77 ,1163", b"= , 58812 ,1163").replace(b"= , ,24", b"= ,42,24")
        self.assertEqual(result, expected)
        self.assertEqual(app.render_ndu(app.parse_ndu(result), {98: D(58812), 99: D(42)}), result)

    def test_depth_outside_layers_is_rejected(self):
        with self.assertRaisesRegex(app.InputError, "覆っていません"):
            app.find_overlaps(app.Member(1, 4, 1, D("2.7"), D(3)), app.parse_sdc(sdc_bytes()))

    def test_missing_column_is_rejected(self):
        with self.assertRaisesRegex(app.InputError, "3列目"):
            app.find_overlaps(app.Member(1, 4, 3, D(0), D(1)), app.parse_sdc(sdc_bytes()))

    def test_duplicate_keys_are_rejected(self):
        with self.assertRaisesRegex(app.InputError, "重複"):
            app.parse_ndu(ndu_bytes() + b"\r\nJointXY201=1,2,201\r\n")

    def test_inclined_member_is_rejected(self):
        raw = ndu_bytes().replace(b"JointXY202=29.8", b"JointXY202=30.8")
        with self.assertRaisesRegex(app.InputError, "鉛直ではありません"):
            app.collect_members(app.parse_ndu(raw), {4: 1})

    def test_missing_jiban_is_rejected(self):
        raw = ndu_bytes().replace(b"JibanShogenInfo99=", b"UnusedInfo99=")
        with self.assertRaisesRegex(app.InputError, "JibanShogenInfo99"):
            app.collect_members(app.parse_ndu(raw), {4: 1})

    def test_nonfinite_spring_is_rejected(self):
        with self.assertRaisesRegex(app.InputError, "有限"):
            app.parse_sdc(sdc_bytes().replace(b",50,100", b",50,NaN"))

    def test_cross_layer_policies(self):
        member = app.Member(99, 4, 1, D("1.3"), D("2.6"))
        overlaps = app.find_overlaps(member, app.parse_sdc(sdc_bytes()))
        # (100 * 0.2 + 300 * 1.1) / 1.3 = 269.230769...
        self.assertEqual(app.select_value(member, overlaps, "length-weighted"), D(269))
        self.assertEqual(app.select_value(member, overlaps, "midpoint"), D(300))
        self.assertIsNone(app.select_value(member, overlaps, "skip"))
        with self.assertRaisesRegex(app.InputError, "層境界"):
            app.select_value(member, overlaps, "error")

    def test_weighted_rounds_half_up(self):
        layers = [app.Layer(1, D(0), D(1), {1: D(100)}, 1), app.Layer(2, D(1), D(2), {1: D(101)}, 2)]
        member = app.Member(1, 4, 1, D(0), D(2))
        self.assertEqual(app.select_value(member, app.find_overlaps(member, layers), "length-weighted"), D(101))

    def test_single_layer_copies_exact_value_without_length_scaling(self):
        layers = [app.Layer(1, D(0), D(2), {1: D("100.25")}, 1)]
        member = app.Member(1, 4, 1, D(0), D("1.3"))
        self.assertEqual(app.select_value(member, app.find_overlaps(member, layers), "length-weighted"), D("100.25"))

    def test_midpoint_on_boundary_uses_lower_layer(self):
        member = app.Member(1, 4, 1, D("0.5"), D("2.5"))
        overlaps = app.find_overlaps(member, app.parse_sdc(sdc_bytes()))
        self.assertEqual(app.select_value(member, overlaps, "midpoint"), D(300))

    def test_group_mapping_rejects_duplicates(self):
        for items in [["4:1", "4:2"], ["4=1"]]:
            with self.assertRaises(app.InputError):
                app.parse_groups(items)

    def test_group_mapping_allows_shared_sdc_column(self):
        self.assertEqual(app.parse_groups(["4:3", "5:3", "6:1"]), {4: 3, 5: 3, 6: 1})


class FileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ndu = Path(self.temp.name) / "モデル.ndu"
        self.sdc = Path(self.temp.name) / "右.sdc"
        self.ndu.write_bytes(ndu_bytes())
        self.sdc.write_bytes(sdc_bytes())
        self.args = ["--ndu", str(self.ndu), "--sdc", str(self.sdc), "--groups", "4:1"]

    def invoke(self, extra):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return app.main(self.args + extra)

    def test_explicit_error_policy_aborts_before_any_write(self):
        self.assertEqual(self.invoke(["--cross-layer", "error", "--write"]), 1)
        self.assertEqual(self.ndu.read_bytes(), ndu_bytes())
        self.assertFalse(self.ndu.with_name(self.ndu.name + ".bak").exists())

    def test_preview_does_not_modify_files(self):
        self.assertEqual(self.invoke([]), 0)
        self.assertEqual(self.ndu.read_bytes(), ndu_bytes())

    def test_write_backup_and_repeat_are_exact(self):
        args = ["--write"]
        self.assertEqual(self.invoke(args), 0)
        expected = ndu_bytes().replace(b"= , 77 ,1163", b"= , 100 ,1163").replace(b"= , ,24", b"= ,269,24")
        self.assertEqual(self.ndu.read_bytes(), expected)
        backup = self.ndu.with_name(self.ndu.name + ".bak")
        self.assertEqual(backup.read_bytes(), ndu_bytes())
        self.assertEqual(self.invoke(args), 0)
        self.assertEqual(self.ndu.read_bytes(), expected)
        self.assertEqual(backup.read_bytes(), ndu_bytes())
        self.assertEqual(self.sdc.read_bytes(), sdc_bytes())

    def test_existing_backup_is_not_overwritten(self):
        backup = self.ndu.with_name(self.ndu.name + ".bak")
        backup.write_bytes(b"existing backup")
        self.assertEqual(self.invoke(["--cross-layer", "length-weighted", "--write"]), 1)
        self.assertEqual(backup.read_bytes(), b"existing backup")
        self.assertEqual(self.ndu.read_bytes(), ndu_bytes())

    def test_stale_input_is_not_overwritten(self):
        with self.assertRaisesRegex(app.InputError, "変更されました"):
            app.write_with_backup(self.ndu, b"stale data", b"replacement")
        self.assertEqual(self.ndu.read_bytes(), ndu_bytes())

    def test_skip_preserves_crossing_member(self):
        self.assertEqual(self.invoke(["--cross-layer", "skip", "--write"]), 0)
        self.assertEqual(self.ndu.read_bytes(), ndu_bytes().replace(b"= , 77 ,1163", b"= , 100 ,1163"))

    def test_defaults_update_all_three_right_groups_with_weighted_values(self):
        extra = []
        for group, member, joint, x in [(5, 123, 301, "33.5"), (6, 148, 401, "37.2")]:
            extra.extend([
                f"KGInfo{group}=21,{member},{member + 1}",
                f"JointXY{joint}={x},13.041,{joint}",
                f"JointXY{joint + 1}={x},14.341,{joint + 1}",
                f"JointXY{joint + 2}={x},15.641,{joint + 2}",
                f"ElementInfo{member}=3,0,0,0,{joint},{joint + 1},75,0",
                f"ElementInfo{member + 1}=3,0,0,0,{joint + 1},{joint + 2},76,0",
                f"JibanShogenInfo{member}= , ,24,25, , , ",
                f"JibanShogenInfo{member + 1}= , ,24,25, , , ",
            ])
        self.ndu.write_bytes(ndu_bytes() + ("\r\n" + "\r\n".join(extra)).encode("cp932"))
        sdc = sdc_bytes().decode("cp932").replace(
            "短期(非線形)-1列目\r\n", "短期(非線形)-1列目,短期(非線形)-3列目\r\n"
        ).replace("1,1.500,1,2,50,100", "1,1.500,1,2,50,100,200").replace(
            "2,1.300,3,4,150,300", "2,1.300,3,4,150,300,600"
        )
        self.sdc.write_bytes(sdc.encode("cp932"))
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            code = app.main(["--ndu", str(self.ndu), "--sdc", str(self.sdc), "--write"])
        self.assertEqual(code, 0)
        result = app.parse_ndu(self.ndu.read_bytes())
        for member, expected in {98: "100", 99: "269", 123: "50", 124: "135", 148: "200", 149: "538"}.items():
            self.assertEqual(result.fields(f"JibanShogenInfo{member}", 7)[1], expected)


if __name__ == "__main__":
    unittest.main()
