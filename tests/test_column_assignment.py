"""SDC列の逆順・共用、自動割当、全4項目の保存時の列対応を検証する。"""

from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import fill_jiban_shogen as base
import fill_jiban_pressure as pressure
import portable_converter as converter
from kg_candidates import Candidate, Catalog, assign_columns, inspect_candidates
from test_fill_jiban_pressure import sdc_bytes, ndu_bytes


class AssignmentTests(unittest.TestCase):
    def test_physical_order_not_kg_number_or_selection_order_and_three_column_cap(self):
        ids = [90, 10, 70, 30, 50, 20]
        catalog = Catalog(tuple(Candidate(g, i, i, D(31), "", D(i*5)) for i,g in enumerate(ids)),
                          D(31), (1, 2, 3, 4, 5, 6))
        for count in (1, 2, 3, 5, 6):
            selected = ids[:count]
            with self.subTest(count=count):
                left = assign_columns(catalog, selected[::-1], "left")
                right = assign_columns(catalog, selected[::-1], "right")
                expected = [1, 2, 3, 3, 3, 3][:count]
                self.assertEqual(list(left), selected)
                self.assertEqual(list(left.values()), expected)
                self.assertEqual(list(right.values()), expected[::-1])

    def test_invalid_or_missing_source_column_does_not_silently_fall_back(self):
        catalog = Catalog(tuple(Candidate(g,g,g,D(31),"",D(g)) for g in (1,2,3)),D(31),(1,2))
        for selected,direction in (([],"right"),([1,2,3],"right"),([9],"left"),([1],"unknown")):
            with self.subTest(selected=selected,direction=direction), self.assertRaises(base.InputError):
                assign_columns(catalog, selected, direction)

    def test_direct_pressure_uses_selected_table_column_without_second_reversal(self):
        ndu = base.parse_ndu(ndu_bytes())
        layers = pressure.parse_pressure_sdc(sdc_bytes())
        for column,expected in ((1,(D(100),D(230))), (2,(D(20),D(46))), (3,(D(10),D(23)))):
            updates,record = pressure.make_plan(ndu,layers,{4:column},"direct",1,"integral-average")
            self.assertEqual(updates[98],expected)
            self.assertEqual(record["members"][0]["pressure_column"],column)


class MappingIntegrationTests(unittest.TestCase):
    def test_reverse_and_shared_columns_convert_all_four_and_serialize_excel(self):
        sdc,ndu = base.DEFAULT_SDC,base.DEFAULT_NDU
        before = {p:p.read_bytes() for p in (sdc,ndu)}
        catalog = inspect_candidates(before[ndu],before[sdc])
        self.assertEqual([c.x for c in catalog.candidates if c.group in (4,5,6)],
                         [D("29.8"),D("33.5"),D("37.2")])
        with tempfile.TemporaryDirectory() as folder:
            for selected in ([4,5,6],[2,3,4,5,6]):
                for direction in ("right","left"):
                    groups = assign_columns(catalog,selected[::-1],direction)
                    with self.subTest(groups=groups):
                        request = converter.Request(sdc,ndu,groups=tuple(f"{g}:{c}" for g,c in groups.items()),
                                                    push_direction="direct",shaft_profile="existing-screen",
                                                    require_matching_lengths=True)
                        plan = converter.prepare(request)
                        self.assertEqual(len(plan.rows),len(selected)*69)
                        combined = base.parse_ndu(plan.data)
                        combined_supports = converter.support_values(plan.data)
                        for group,column in groups.items():
                            single = converter.prepare(replace(request,groups=(f"{group}:{column}",)))
                            single_ndu = base.parse_ndu(single.data)
                            for member in base.collect_members(base.parse_ndu(before[ndu]),{group:column}):
                                key = f"JibanShogenInfo{member.number}"
                                self.assertEqual(combined.fields(key,7),single_ndu.fields(key,7))
                            singles = converter.support_values(single.data)
                            for operation in ("shaft","tip"):
                                for row in single.report["details"][operation]["nodes"]:
                                    self.assertEqual(combined_supports[row["node"]],singles[row["node"]])
                        for row in plan.report["details"]["pressure"]["members"]:
                            self.assertEqual(row["pressure_column"],groups[row["group"]])
                        for operation in ("horizontal","shaft","tip"):
                            detail = plan.report["details"][operation]
                            rows = detail.get("members",detail.get("nodes"))
                            self.assertEqual({row["group"] for row in rows},set(selected))
                            self.assertTrue(all(row["column"]==groups[row["group"]] for row in rows))
                        output = Path(folder)/f"{len(selected)}-{direction}.ndu"
                        saved = converter.save(plan,output)
                        self.assertEqual(output.read_bytes(),plan.data)
                        self.assertEqual(saved.excel.read_bytes()[:2],b"PK")
        self.assertEqual({p:p.read_bytes() for p in before},before)


if __name__ == "__main__":
    unittest.main()
