"""Ver.5.2.1共有値SDCの解析、変換、既存NDUとの対応を検証する。"""

from dataclasses import replace
from decimal import Decimal as D
import gc
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
from tests.fixture_paths import (
    SAPPORO_LONGITUDINAL_NDU,
    SAPPORO_SDC,
    SAPPORO_TRANSVERSE_NDU,
    SHINAGAWA_LONGITUDINAL_NDU,
    SHINAGAWA_SDC,
    SHINAGAWA_TRANSVERSE_NDU,
)
from kg_candidates import assign_columns, inspect_candidates
from kg_selection import KGSelection


SAPPORO = SAPPORO_SDC
SHINAGAWA = SHINAGAWA_SDC

# name, SDC, direction, NDU, KG:SDC列, expected preview rows
CASES = (
    ("sapporo-longitudinal", SAPPORO, "longitudinal",
     SAPPORO_LONGITUDINAL_NDU, ("1:2", "2:1"), 66),
    ("sapporo-transverse", SAPPORO, "transverse",
     SAPPORO_TRANSVERSE_NDU, ("1:3", "2:2", "3:1"), 99),
    ("shinagawa-longitudinal", SHINAGAWA, "longitudinal",
     SHINAGAWA_LONGITUDINAL_NDU, ("1:2", "2:1"), 138),
    ("shinagawa-transverse", SHINAGAWA, "transverse",
     SHINAGAWA_TRANSVERSE_NDU, ("1:2", "2:1"), 138),
)


def request(case, **changes):
    _, sdc, direction, ndu, groups, _ = case
    base = converter.Request(
        sdc, ndu, groups=groups, push_direction="direct",
        shaft_profile="existing-screen", require_matching_lengths=True,
        sdc_direction=direction,
    )
    return replace(base, **changes)


def replace_line(raw, predicate, replacement, occurrence=1):
    lines = raw.decode("cp932").splitlines()
    matches = [index for index, line in enumerate(lines) if predicate(line)]
    if len(matches) < occurrence:
        raise AssertionError("変更対象のSDC行が見つかりません。")
    index = matches[occurrence - 1]
    lines[index] = replacement(lines[index]) if callable(replacement) else replacement
    return ("\r\n".join(lines) + "\r\n").encode("cp932")


class ParserTests(unittest.TestCase):
    def test_actual_shared_values_columns_and_sources(self):
        expected = {
            (SAPPORO, "longitudinal"): (7, 2, D(181422)),
            (SAPPORO, "transverse"): (7, 3, D(107049)),
            (SHINAGAWA, "longitudinal"): (6, 2, D(26325)),
            (SHINAGAWA, "transverse"): (6, 2, D(26325)),
        }
        for (path, direction), (layer_count, column_count, first_value) in expected.items():
            with self.subTest(path=path.name, direction=direction):
                raw = path.read_bytes()
                h = horizontal.parse_sdc(raw, direction)
                p = pressure.parse_pressure_sdc(raw, direction)
                s = shaft.parse_sdc(raw, direction)
                t = tip.parse_sdc(raw, direction)
                columns = set(range(1, column_count + 1))
                self.assertEqual((len(h), len(p), len(s.layers)),
                                 (layer_count, layer_count, layer_count))
                self.assertEqual(set(h[0].values), columns)
                self.assertEqual(set(p[0].values), columns)
                self.assertEqual(set(s.layers[0].values), columns)
                self.assertEqual(set(t.values), columns)
                self.assertEqual(set(h[0].values.values()), {first_value})
                self.assertEqual({v.field for v in h[0].sources.values()}, {5})
                self.assertEqual({v.interpretation for v in h[0].sources.values()},
                                 {sdc_columns.LEGACY_SHARED_INTERPRETATION})
                self.assertEqual({pair[0].field for pair in p[0].sources.values()},
                                 set(range(3, 2 * column_count + 2, 2)))
                self.assertEqual({v[0].field for v in s.layers[0].sources.values()}, {6})
                self.assertEqual({v[1].field for v in s.layers[0].sources.values()}, {4})
                self.assertEqual(t.interpretation,
                                 "tip-v5.2.1-shared-omitted-headings")
                for values in t.sources.values():
                    self.assertEqual([source.field for source in values.values()],
                                     [2, 3, 1, 2])

    def test_sapporo_three_column_pressure_uses_tail_category(self):
        p = pressure.parse_pressure_sdc(SAPPORO.read_bytes(), "transverse")
        self.assertEqual([v.field for v in p[0].sources[3]], [7, 8])
        self.assertEqual(p[0].values[3], (D("1052.8"), D("1440.7")))

    def test_both_pressure_cases_parse_for_both_directions(self):
        for path in (SAPPORO, SHINAGAWA):
            for direction in ("longitudinal", "transverse"):
                for pressure_case in ("non-response", "response"):
                    with self.subTest(path=path.name, direction=direction,
                                      pressure_case=pressure_case):
                        layers = pressure.parse_pressure_sdc(
                            path.read_bytes(), direction, pressure_case
                        )
                        self.assertTrue(layers)
                        self.assertTrue(all(layer.condition == "seismic" for layer in layers))

    def test_legacy_signatures_are_strict(self):
        raw = SHINAGAWA.read_bytes()
        cases = []
        cases.append((horizontal.parse_sdc,
                      replace_line(raw, lambda line: line == "Ver.5.2.1", "Ver.5.2.2")))
        cases.append((horizontal.parse_sdc,
                      replace_line(raw, lambda line: line == ",,長期,短期(線形解析),短期(非線形解析)",
                                   lambda line: line + ",余分")))
        cases.append((pressure.parse_pressure_sdc,
                      replace_line(raw, lambda line: line.startswith("1, 0.270,") and len(line.split(",")) == 8,
                                   lambda line: line.rsplit(",", 1)[0])))
        cases.append((shaft.parse_sdc,
                      replace_line(raw, lambda line: "⊿l(m)" in line,
                                   lambda line: line.replace("⊿l(m)", "変位"))))
        cases.append((tip.parse_sdc,
                      replace_line(raw, lambda line: [part.strip() for part in line.split(",")] ==
                                   ["56276", "112552", "61116"],
                                   lambda line: line + ",1")))
        for parser, edited in cases:
            with self.subTest(parser=parser.__module__), self.assertRaises(horizontal.InputError):
                parser(edited, "longitudinal")

    def test_only_two_or_three_piles_are_accepted(self):
        raw = SHINAGAWA.read_bytes()
        for count in (1, 4):
            edited = replace_line(
                raw,
                lambda line: line.startswith("2,") and len(line.split(",")) == 4,
                lambda line, count=count: str(count) + line[1:],
            )
            for parser in (horizontal.parse_sdc, pressure.parse_pressure_sdc,
                           shaft.parse_sdc, tip.parse_sdc):
                with self.subTest(count=count, parser=parser.__module__), \
                        self.assertRaises(horizontal.InputError):
                    parser(edited, "longitudinal")

    def test_version_header_must_be_unique_and_present(self):
        raw = SHINAGAWA.read_bytes()
        missing = replace_line(raw, lambda line: line == "【バージョン情報】", "【版情報】")
        lines = raw.decode("cp932").splitlines()
        duplicate = ("\r\n".join(["【バージョン情報】", "Ver.5.2.1"] + lines) +
                     "\r\n").encode("cp932")
        for edited in (missing, duplicate):
            for parser in (horizontal.parse_sdc, pressure.parse_pressure_sdc,
                           shaft.parse_sdc, tip.parse_sdc):
                with self.subTest(parser=parser.__module__), \
                        self.assertRaises(horizontal.InputError):
                    parser(edited, "longitudinal")

    def test_unused_third_pressure_category_for_two_piles_must_be_zero(self):
        raw = SHINAGAWA.read_bytes()
        edited = replace_line(
            raw,
            lambda line: line.startswith("1, 0.270,") and len(line.split(",")) == 8,
            lambda line: ",".join(line.split(",")[:-1] + ["1"]),
        )
        with self.assertRaises(horizontal.InputError):
            pressure.parse_pressure_sdc(edited, "longitudinal")


class IntegrationTests(unittest.TestCase):
    def test_all_four_real_pairs_prepare_excel_and_leave_inputs_unchanged(self):
        for case in CASES:
            name, sdc, _, ndu, _, expected_rows = case
            with self.subTest(case=name):
                originals = (sdc.read_bytes(), ndu.read_bytes())
                plan = converter.prepare(request(case))
                self.assertEqual(len(plan.rows), expected_rows)
                self.assertEqual(set(plan.report["details"]), set(converter.OPERATIONS))
                self.assertEqual(plan.report["version"], converter.VERSION)
                self.assertTrue(all(
                    row["interpretation"] == "tip-v5.2.1-shared-omitted-headings"
                    for row in plan.report["details"]["tip"]["nodes"]
                ))
                self.assertEqual(plan.workbook.to_xlsx()[:2], b"PK")
                self.assertEqual((sdc.read_bytes(), ndu.read_bytes()), originals)

    def test_candidate_columns_and_right_push_mapping(self):
        for case in CASES:
            name, sdc, direction, ndu, groups, _ = case
            with self.subTest(case=name):
                catalog = inspect_candidates(ndu.read_bytes(), sdc.read_bytes(),
                                             sdc_direction=direction)
                expected_columns = tuple(range(1, len(groups) + 1))
                self.assertEqual((catalog.error, catalog.columns), ("", expected_columns))
                self.assertEqual(assign_columns(catalog, list(expected_columns), "right"),
                                 {group: column for group, column in
                                  zip(expected_columns, reversed(expected_columns))})

    def test_horizontal_values_match_all_four_existing_ndu_files(self):
        for case in CASES:
            name, _, _, ndu_path, groups, _ = case
            with self.subTest(case=name):
                before = horizontal.parse_ndu(ndu_path.read_bytes())
                plan = converter.prepare(request(case, operations=("horizontal",)))
                after = horizontal.parse_ndu(plan.data)
                members = horizontal.collect_members(before, horizontal.parse_groups(list(groups)))
                self.assertTrue(members)
                for member in members:
                    key = f"JibanShogenInfo{member.number}"
                    self.assertEqual(after.fields(key, 7)[1], before.fields(key, 7)[1])

    def test_shinagawa_pressure_matches_existing_ndu_files(self):
        for case in CASES[2:]:
            name, _, _, ndu_path, groups, _ = case
            with self.subTest(case=name):
                before = horizontal.parse_ndu(ndu_path.read_bytes())
                plan = converter.prepare(request(case, operations=("pressure",)))
                after = horizontal.parse_ndu(plan.data)
                members = horizontal.collect_members(before, horizontal.parse_groups(list(groups)))
                for member in members:
                    key = f"JibanShogenInfo{member.number}"
                    self.assertEqual(after.fields(key, 7)[2:4], before.fields(key, 7)[2:4])

    def test_existing_ndu_tip_positive_fields_match_all_four_pairs(self):
        positive = (0, 1, 3, 4, 6)
        for case in CASES:
            name, _, _, ndu_path, _, _ = case
            with self.subTest(case=name):
                before = converter.support_values(ndu_path.read_bytes())
                plan = converter.prepare(request(case, operations=("tip",)))
                after = converter.support_values(plan.data)
                for row in plan.report["details"]["tip"]["nodes"]:
                    node = row["node"]
                    self.assertIn(node, before)
                    self.assertEqual([after[node][i] for i in positive],
                                     [before[node][i] for i in positive])

    def test_calculation_sources_are_original_csv_fields_and_shared_once(self):
        for case in CASES:
            name, sdc, _, _, _, _ = case
            with self.subTest(case=name):
                plan = converter.prepare(request(case))
                lines = sdc.read_bytes().decode("cp932").splitlines()
                sources = plan.report["calculation"]["source_values"]
                for value in sources:
                    self.assertEqual(D(value["value"]),
                                     D(lines[value["line"] - 1].split(",")[value["field"] - 1]))
                horizontal_sources = [v for v in sources if v["operation"] == "horizontal"]
                shaft_sources = [v for v in sources if v["operation"] == "shaft"]
                self.assertEqual(len(horizontal_sources), len(horizontal.parse_sdc(
                    sdc.read_bytes(), case[2])))
                self.assertEqual(len(shaft_sources), 2 * len(shaft.parse_sdc(
                    sdc.read_bytes(), case[2]).layers))

    def test_each_operation_can_run_independently(self):
        case = CASES[0]
        for operation in converter.OPERATIONS:
            with self.subTest(operation=operation):
                plan = converter.prepare(request(case, operations=(operation,)))
                self.assertEqual(set(plan.report["details"]), {operation})
                self.assertEqual(plan.workbook.to_xlsx()[:2], b"PK")


class GuiTests(unittest.TestCase):
    def test_all_four_pairs_load_and_right_push_selects_expected_columns(self):
        gc.collect()
        root = tk.Tk()
        root.withdraw()
        try:
            ndu_var = tk.StringVar(root)
            sdc_var = tk.StringVar(root)
            groups_var = tk.StringVar(root)
            direction_var = tk.StringVar(root)
            selector = KGSelection(root, ndu_var, sdc_var, groups_var, direction_var)
            selector.pack()
            for case in CASES:
                name, sdc, direction, ndu, groups, _ = case
                with self.subTest(case=name):
                    direction_var.set(direction)
                    sdc_var.set(str(sdc))
                    ndu_var.set(str(ndu))
                    deadline = time.monotonic() + 5
                    while not selector.ready and time.monotonic() < deadline:
                        root.update()
                        time.sleep(0.01)
                    self.assertTrue(selector.ready, selector.message.get())
                    for group in map(int, (value.split(":")[0] for value in groups)):
                        selector.rows[group].check.invoke()
                    selector.direction_buttons["right"].invoke()
                    self.assertEqual(selector.selection(), groups)
        finally:
            root.destroy()
        gc.collect()


if __name__ == "__main__":
    unittest.main()
