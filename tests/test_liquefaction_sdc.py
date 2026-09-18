"""液状化時SDCの条件判定と実データ差分を検証する。"""

from contextlib import redirect_stdout
from decimal import Decimal as D
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import fill_jiban_pressure as pressure
import fill_jiban_shogen as horizontal
import fill_pile_tip_suppot_info as tip
import fill_suppot_info as shaft
import kg_candidates
import portable_converter as converter


LIQUEFACTION = ROOT / "test/ラーメン橋R8　鋼管ソイルセメント杭(液状化)(液状化時用).sdc"
R2 = ROOT / "test/R2ラーメンばね1.sdc"


def replace_once(raw: bytes, old: str, new: str) -> bytes:
    before = old.encode("cp932")
    if before not in raw:
        raise AssertionError(f"置換対象がありません: {old!r}")
    return raw.replace(before, new.encode("cp932"), 1)


def minimal_ndu() -> bytes:
    depths = ("0", "3.397", "13.397", "22.097", "24.397", "25.5")
    lines = ["DataName=液状化回帰試験", "KGInfo1=0,1,5"]
    lines += [f"JointXY{i}=0,{depth},{i}" for i, depth in enumerate(depths, 1)]
    lines += [f"ElementInfo{i}=0,0,0,0,{i},{i+1}" for i in range(1, 6)]
    lines += [f"JibanShogenInfo{i}= ,0,0,0, , , " for i in range(1, 6)]
    lines += ["SuppotNum=0", "SuppotRow=0", "ShitenCaseNum=0",
              "G_intCHOKU_KISO_Link_Num=0", "Untouched=keep"]
    return ("\r\n".join(lines) + "\r\n").encode("cp932")


class LiquefactionParserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = LIQUEFACTION.read_bytes()

    def test_all_ten_parser_paths_detect_liquefaction(self):
        for direction, columns in (("longitudinal", 4), ("transverse", 2)):
            expected_lines = {
                "longitudinal": {"horizontal": 38, "pressure": (49, 59), "shaft": (70, 98), "tip": (125, 143)},
                "transverse": {"horizontal": 158, "pressure": (169, 179), "shaft": (190, 218), "tip": (245, 263)},
            }[direction]
            with self.subTest(direction=direction, operation="horizontal"):
                layers = horizontal.parse_sdc(self.raw, direction)
                self.assertEqual(layers[0].condition, "liquefaction")
                self.assertEqual(len(layers[0].values), columns)
                self.assertEqual(layers[0].values[1], D(0))
                self.assertEqual(layers[1].values[1], D(63452 if columns == 4 else 105915))
                self.assertEqual((layers[0].source_line, layers[0].sources[1].field),
                                 (expected_lines["horizontal"], 11 if columns == 4 else 7))
            for pressure_case in ("non-response", "response"):
                with self.subTest(direction=direction, operation="pressure", pressure_case=pressure_case):
                    layers = pressure.parse_pressure_sdc(self.raw, direction, pressure_case)
                    self.assertEqual(layers[0].condition, "liquefaction")
                    self.assertEqual(len(layers[0].values), columns)
                    expected = D("403.9" if columns == 4 else "807.8")
                    self.assertEqual(layers[1].values[1], (expected, expected))
                    source = layers[0].sources[1]
                    self.assertEqual((layers[0].source_line, source[0].field, source[1].field),
                                     (expected_lines["pressure"][pressure_case == "response"], 3, 4))
            with self.subTest(direction=direction, operation="shaft"):
                profile = shaft.parse_sdc(self.raw, direction)
                self.assertEqual(profile.condition, "liquefaction")
                self.assertEqual(len(profile.layers[0].values), columns)
                self.assertEqual(profile.layers[-1].bottom, D("25.5"))
                self.assertEqual(sum((max(D(0), layer.active_bottom - layer.active_top)
                                      for layer in profile.layers), D(0)),
                                 D("16.804" if columns == 4 else "16.615"))
                layer = profile.layers[0]
                self.assertEqual((layer.spring_line, layer.force_line,
                                  layer.sources[1][0].field, layer.sources[1][1].field),
                                 (*expected_lines["shaft"], 12 if columns == 4 else 8, 4))
            with self.subTest(direction=direction, operation="tip"):
                profile = tip.parse_sdc(self.raw, direction)
                self.assertEqual(profile.condition, "liquefaction")
                expected_k1 = D(16 if columns == 4 else 32)
                self.assertEqual(profile.values[1], tip.TipValues(expected_k1, D(0), D(0), D(0)))
                refs = profile.sources[1]
                self.assertEqual((profile.spring_line, profile.force_line,
                                  refs["k1_kN_per_m"].field, refs["k2_kN_per_m"].field,
                                  refs["fy_kN"].field, refs["fu_kN"].field),
                                 (*expected_lines["tip"],
                                  5 if columns == 4 else 3, 9 if columns == 4 else 5,
                                  1, 5 if columns == 4 else 3))

    def test_transverse_pressure_accepts_only_consistent_zero_padding(self):
        layers = pressure.parse_pressure_sdc(self.raw, "transverse", "non-response")
        self.assertEqual(layers[-1].values[2], (D("9779.4"), D("10201.4")))

        nonzero = replace_once(
            self.raw,
            "1, 3.397, 0.0, 0.0, 0.0, 0.0, 0,  0 ",
            "1, 3.397, 0.0, 0.0, 0.0, 0.0, 1,  0 ",
        )
        with self.assertRaisesRegex(horizontal.InputError, "余剰列|ゼロ"):
            pressure.parse_pressure_sdc(nonzero, "transverse", "non-response")

        odd = replace_once(
            self.raw,
            "1, 3.397, 0.0, 0.0, 0.0, 0.0, 0,  0 ",
            "1, 3.397, 0.0, 0.0, 0.0, 0.0, 0",
        )
        with self.assertRaisesRegex(horizontal.InputError, "余剰列|列数"):
            pressure.parse_pressure_sdc(odd, "transverse", "non-response")

        inconsistent = replace_once(
            self.raw,
            "2, 10.000, 807.8, 807.8, 807.8, 807.8, 0,  0 ",
            "2, 10.000, 807.8, 807.8, 807.8, 807.8",
        )
        with self.assertRaisesRegex(horizontal.InputError, "余剰列|列数"):
            pressure.parse_pressure_sdc(inconsistent, "transverse", "non-response")

    def test_existing_r2_transverse_zero_padding_remains_supported(self):
        layers = pressure.parse_pressure_sdc(R2.read_bytes(), "transverse", "non-response")
        self.assertEqual(layers[0].condition, "seismic")
        self.assertEqual(set(layers[0].values), {1, 2})

    def test_shaft_zero_thickness_is_limited_to_zero_final_spring_row(self):
        nonzero = replace_once(
            self.raw,
            "5, 0.000, 0, 0, 0, 0, 0, 0, 0, 0, 0.000, 0, 0, 0, 0, 0, 0, 0,  0 ",
            "5, 0.000, 1, 0, 0, 0, 0, 0, 0, 0, 0.000, 0, 0, 0, 0, 0, 0, 0,  0 ",
        )
        with self.assertRaisesRegex(horizontal.InputError, "層厚0|ゼロ"):
            shaft.parse_sdc(nonzero, "longitudinal")

        middle = replace_once(
            self.raw,
            "3, 8.700, 51742, 51742, 51742, 51742, 103484, 103484, 103484, 103484, 8.700, 103484, 103484, 103484, 103484, 2901, 2901, 2901, 2901",
            "3, 0.000, 0, 0, 0, 0, 0, 0, 0, 0, 0.000, 0, 0, 0, 0, 0, 0, 0, 0",
        )
        with self.assertRaisesRegex(horizontal.InputError, "最終層|層厚"):
            shaft.parse_sdc(middle, "longitudinal")

    def test_shaft_and_tip_reject_mixed_conditions(self):
        mixed_shaft = replace_once(
            self.raw,
            "層番,層厚,⊿l(m),液状化時：杭周面支持力(kN/m)",
            "層番,層厚,⊿l(m),地震時：杭周面支持力(kN/m)",
        )
        with self.assertRaisesRegex(horizontal.InputError, "条件.*一致"):
            shaft.parse_sdc(mixed_shaft, "longitudinal")

        mixed_tip = replace_once(
            self.raw,
            "液状化時：杭先端の鉛直地盤支持力(kN)",
            "地震時：杭先端の鉛直地盤支持力(kN)",
        )
        with self.assertRaisesRegex(horizontal.InputError, "条件.*一致"):
            tip.parse_sdc(mixed_tip, "longitudinal")

    def test_liquefaction_tip_requires_positive_k1_but_allows_zero_others(self):
        bad_k1 = replace_once(
            self.raw,
            "8, 8, 8, 8, 16, 16, 16, 16, 0, 0, 0, 0",
            "8, 8, 8, 8, 0, 16, 16, 16, 0, 0, 0, 0",
        )
        with self.assertRaisesRegex(horizontal.InputError, "K1|第1勾配"):
            tip.parse_sdc(bad_k1, "longitudinal")

    def test_candidate_catalog_exposes_detected_condition(self):
        ndu = (ROOT / "test/今町橋りょう4P(C方向･右押し→).ndu").read_bytes()
        catalog = kg_candidates.inspect_candidates(ndu, self.raw, sdc_direction="transverse")
        self.assertEqual(catalog.columns, (1, 2))
        self.assertEqual(catalog.condition, "liquefaction")


class LiquefactionIntegrationTests(unittest.TestCase):
    def test_individual_cli_json_records_condition(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sdc = root / "liquefaction.sdc"
            ndu = root / "model.ndu"
            sdc.write_bytes(LIQUEFACTION.read_bytes())
            ndu.write_bytes(minimal_ndu())
            cases = (
                (pressure.main, ("--push-direction", "direct")),
                (shaft.main, ()),
                (tip.main, ()),
            )
            for index, (main, extra) in enumerate(cases):
                report = root / f"report-{index}.json"
                arguments = ("--sdc", str(sdc), "--ndu", str(ndu),
                             "--sdc-direction", "longitudinal", "--groups", "1:1",
                             "--report", str(report), *extra)
                with self.subTest(main=main.__module__), redirect_stdout(StringIO()):
                    self.assertEqual(main(list(arguments)), 0)
                    config = json.loads(report.read_text(encoding="utf8"))["configuration"]
                    self.assertEqual(config["sdc_condition"], "liquefaction")
                    self.assertEqual(config["sdc_condition_label"], "液状化時")

    def test_portable_conversion_records_condition_and_excel_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sdc = root / "liquefaction.sdc"
            ndu = root / "model.ndu"
            sdc.write_bytes(LIQUEFACTION.read_bytes())
            ndu.write_bytes(minimal_ndu())
            plan = converter.prepare(converter.Request(
                sdc,
                ndu,
                groups=("1:1",),
                push_direction="direct",
                shaft_profile="existing-screen",
                sdc_direction="longitudinal",
                pressure_case="non-response",
            ))
            config = plan.report["configuration"]
            self.assertEqual(config["sdc_condition"], "liquefaction")
            self.assertEqual(config["sdc_condition_label"], "液状化時")
            self.assertTrue(all(detail["condition"] == "liquefaction"
                                for detail in plan.report["details"].values()))
            self.assertEqual(plan.workbook.metadata["SDCConverter.SDCCondition"], "液状化時")
            self.assertEqual(plan.workbook.sheet("水平地盤ばね").rows[0][1].value,
                             "（１）橋軸方向・液状化時")
            tip_sheet = plan.workbook.sheet("杭先端ばね")
            self.assertEqual(tip_sheet.rows[2][1].value, "液状化時（第１勾配）")
            self.assertEqual(tip_sheet.rows[2][2].value, "液状化時（第２勾配）")
            parsed = horizontal.parse_ndu(plan.data)
            self.assertEqual(parsed.fields("JibanShogenInfo2", 7)[1], "63452")
            self.assertTrue(any(row["operation"] == "tip" and row["value"] == "0.0"
                                for row in plan.report["calculation"]["source_values"]))
            plan.workbook.to_xlsx()

            saved = converter.save(plan, root / "converted.ndu")
            self.assertEqual(saved.output.read_bytes(), plan.data)
            saved_report = json.loads(saved.report.read_text(encoding="utf8"))
            self.assertEqual(saved_report["configuration"]["sdc_condition"], "liquefaction")
            self.assertEqual(saved.excel.read_bytes(), saved.workbook.to_xlsx())
            rerun = converter.prepare(converter.Request(
                sdc,
                saved.output,
                groups=("1:1",),
                push_direction="direct",
                shaft_profile="existing-screen",
                sdc_direction="longitudinal",
                pressure_case="non-response",
            ))
            self.assertEqual(rerun.data, plan.data)

    def test_portable_conversion_rejects_mixed_operation_conditions(self):
        lines = LIQUEFACTION.read_bytes().decode("cp932").splitlines()
        lines[34] = "b）水平地盤ばね値"
        lines[36] = lines[36].replace("液状化時-", "短期(非線形)-")
        mixed = ("\r\n".join(lines) + "\r\n").encode("cp932")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sdc = root / "mixed.sdc"
            ndu = root / "model.ndu"
            sdc.write_bytes(mixed)
            ndu.write_bytes(minimal_ndu())
            request = converter.Request(
                sdc,
                ndu,
                operations=("horizontal", "tip"),
                groups=("1:1",),
                push_direction="direct",
                sdc_direction="longitudinal",
            )
            with self.assertRaisesRegex(horizontal.InputError, "条件が一致"):
                converter.prepare(request)


if __name__ == "__main__":
    unittest.main()
