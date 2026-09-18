"""Repository-owned liquefaction SDC dialect and parser boundaries."""

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

from tests.fixture_paths import (
    LIQUEFACTION_L1_LONGITUDINAL_NDU,
    LIQUEFACTION_L1_SDC,
    LIQUEFACTION_SDCS,
    R2_SDC,
)


class LiquefactionParserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = LIQUEFACTION_L1_SDC.read_bytes()

    def test_horizontal_rejects_known_liquefaction_header_dialect_explicitly(self):
        for direction, line in (("longitudinal", 40), ("transverse", 237)):
            with self.subTest(direction=direction), self.assertRaisesRegex(
                    horizontal.InputError, rf"SDC {line}行の水平ばね表"):
                horizontal.parse_sdc(self.raw, direction)

    def test_pressure_detects_liquefaction_for_both_directions_and_cases(self):
        expected = {
            "longitudinal": (7, {"non-response": 63, "response": 84}, ("1109.0", "1435.2")),
            "transverse": (2, {"non-response": 260, "response": 281}, ("3881.6", "5023.3")),
        }
        for direction, (columns, source_lines, first_pair) in expected.items():
            for pressure_case in ("non-response", "response"):
                with self.subTest(direction=direction, pressure_case=pressure_case):
                    layers = pressure.parse_pressure_sdc(self.raw, direction, pressure_case)
                    self.assertEqual(len(layers), 16)
                    self.assertEqual(layers[0].condition, "liquefaction")
                    self.assertEqual(len(layers[0].values), columns)
                    self.assertEqual(layers[0].source_line, source_lines[pressure_case])
                    self.assertEqual(tuple(map(str, layers[0].values[1])), first_pair)

    def test_shaft_and_tip_detect_liquefaction_for_both_directions(self):
        expected = {
            "longitudinal": (7, "4.694", (106, 156), (205, 223), "403462"),
            "transverse": (2, "4.822", (303, 353), (402, 420), "1412118"),
        }
        for direction, (columns, exclusion, shaft_lines, tip_lines, k1) in expected.items():
            with self.subTest(direction=direction):
                shaft_profile = shaft.parse_sdc(self.raw, direction)
                tip_profile = tip.parse_sdc(self.raw, direction)
                self.assertEqual(shaft_profile.condition, "liquefaction")
                self.assertEqual(str(shaft_profile.length), "31")
                self.assertEqual(str(shaft_profile.exclusion), exclusion)
                self.assertEqual(len(shaft_profile.layers), 16)
                self.assertEqual(len(shaft_profile.layers[0].values), columns)
                self.assertEqual(
                    (shaft_profile.layers[0].spring_line, shaft_profile.layers[0].force_line),
                    shaft_lines,
                )
                self.assertEqual(tip_profile.condition, "liquefaction")
                self.assertEqual(len(tip_profile.values), columns)
                self.assertEqual((tip_profile.spring_line, tip_profile.force_line), tip_lines)
                self.assertEqual(str(tip_profile.values[1].k1), k1)

    def test_all_four_liquefaction_cases_parse_supported_operations(self):
        for path in LIQUEFACTION_SDCS:
            raw = path.read_bytes()
            for direction, columns in (("longitudinal", 7), ("transverse", 2)):
                with self.subTest(path=path.name, direction=direction):
                    self.assertEqual(
                        pressure.parse_pressure_sdc(raw, direction, "non-response")[0].condition,
                        "liquefaction",
                    )
                    self.assertEqual(len(shaft.parse_sdc(raw, direction).layers[0].values), columns)
                    self.assertEqual(len(tip.parse_sdc(raw, direction).values), columns)

    def test_seismic_r2_remains_supported(self):
        raw = R2_SDC.read_bytes()
        for direction, columns in (("longitudinal", 7), ("transverse", 2)):
            with self.subTest(direction=direction):
                self.assertEqual(horizontal.parse_sdc(raw, direction)[0].condition, "seismic")
                self.assertEqual(
                    len(pressure.parse_pressure_sdc(raw, direction, "non-response")[0].values),
                    columns,
                )

    def test_candidate_catalog_reports_parser_error_without_guessing_columns(self):
        ndu = LIQUEFACTION_L1_LONGITUDINAL_NDU.read_bytes()
        catalog = kg_candidates.inspect_candidates(ndu, self.raw, sdc_direction="longitudinal")
        self.assertIn("水平ばね表", catalog.error)
        self.assertEqual(catalog.columns, ())
        self.assertIsNone(catalog.condition)
        self.assertTrue(all(candidate.reason == "SDC未確認" for candidate in catalog.candidates))


class LiquefactionIntegrationBoundaryTests(unittest.TestCase):
    def test_portable_prepare_rejects_known_dialect_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "must-not-exist.ndu"
            request = converter.Request(
                LIQUEFACTION_L1_SDC,
                LIQUEFACTION_L1_LONGITUDINAL_NDU,
                groups=("1:1",),
                push_direction="direct",
                shaft_profile="existing-screen",
                sdc_direction="longitudinal",
            )
            with self.assertRaisesRegex(horizontal.InputError, "水平ばね表"):
                converter.prepare(request)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
