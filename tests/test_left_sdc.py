"""奇偶SDCの実杭列・出典と、実左基礎の独立期待値・保存・Excelを検証する。"""

from dataclasses import replace
from decimal import Decimal as D
import gc
import json
from pathlib import Path
import re
import sys
import tempfile
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
import excel_report
import sdc_columns
from kg_candidates import inspect_candidates, assign_columns
from kg_selection import KGSelection
from excel_test_helpers import source_cell

LEFT = ROOT / "test/今町橋りょう4P(左).sdc"
RIGHT = horizontal.DEFAULT_SDC
NDU = horizontal.DEFAULT_NDU
PARSERS = (horizontal.parse_sdc, pressure.parse_pressure_sdc, shaft.parse_sdc, tip.parse_sdc)


def edited(changes):
    lines = LEFT.read_bytes().decode("cp932").splitlines()
    for line, value in changes.items():
        lines[line-1] = value
    return ("\r\n".join(lines)+"\r\n").encode("cp932")


def request(**kwargs):
    return replace(converter.Request(LEFT, NDU, groups=("1:1", "2:2", "3:3"),
                                     push_direction="direct", shaft_profile="existing-screen",
                                     require_matching_lengths=True), **kwargs)


class ParserTests(unittest.TestCase):
    def test_actual_layers_columns_values_and_sources(self):
        h, p, s, t = [parse(LEFT.read_bytes()) for parse in PARSERS]
        self.assertEqual(h[0].values, {1:D(50638), 2:D(25319), 3:D(50638)})
        self.assertEqual([l.bottom for l in h], list(map(D, ('3.3','4.6','7.5','10.1','10.7','20.2','31'))))
        self.assertEqual([h[0].sources[c].field for c in (1,2,3)], [7,8,7])
        self.assertEqual(p[0].values, {1:(D('1788.9'),D('2636.6')), 2:(D('447.2'),D('659.2')),
                                     3:(D('715.5'),D('1054.7'))})
        self.assertEqual([r.field for r in p[0].sources[3]], [7,8])
        self.assertEqual((s.length,s.exclusion,s.embedment), (D(31),D('4.891'),D('1.3')))
        self.assertEqual(s.layers[2].active_bottom-s.layers[2].active_top, D('2.609'))
        self.assertEqual(s.layers[-1].bottom, D(31))
        self.assertEqual(s.layers[-1].active_bottom-s.layers[-1].active_top, D('9.5'))
        self.assertEqual(s.layers[2].values[3], (D(30751),D('196.0')))
        self.assertEqual([r.field for r in s.layers[2].sources[3]], [8,4])
        self.assertEqual(t.values[1],tip.TipValues(D(218048),D(58945),D('4778.4'),D('11149.5')))
        self.assertEqual(t.values[2],tip.TipValues(D(109024),D(29473),D('2389.2'),D('5574.8')))
        self.assertEqual(t.values[3],t.values[1])
        self.assertEqual([r.field for r in t.sources[3].values()], [3,5,1,3])
        self.assertEqual(t.interpretation,'tip-parity-omitted-gradient-heading')

    def test_actual_count_one_through_five_not_number_of_csv_categories(self):
        for n in range(1,6):
            with self.subTest(n=n):
                h,p,s,t = [parse(edited({157:f'{n},2,1,4.891'})) for parse in PARSERS]
                expected = set(range(1,n+1))
                for values in (h[0].values,p[0].values,s.layers[0].values,t.values):
                    self.assertEqual(set(values),expected)
                if n>=4:
                    self.assertEqual(h[0].values[4],D(25319))
                    self.assertEqual(p[0].values[4],(D(0),D(0)))
                    self.assertEqual(t.values[4],t.values[2])
                if n==5:
                    self.assertEqual(h[0].values[5],h[0].values[1])
                    self.assertEqual(p[0].values[5],p[0].values[3])
                    self.assertEqual(s.layers[2].values[5],s.layers[2].values[1])
        p=pressure.parse_pressure_sdc(edited({157:'5,2,1,4.891',175:'1,3.300,10,20,30,40,50,60,70,80'}))
        self.assertEqual(p[0].values[4],(D(70),D(80)))
        self.assertEqual(p[0].values[5],(D(50),D(60)))

    def test_explicit_parity_tip_gradients(self):
        t=tip.parse_sdc(edited({261:'長期,,短期(第1勾配),,短期(第2勾配)',
                               262:'奇数列,偶数列,奇数列,偶数列,奇数列,偶数列'}))
        self.assertEqual(t.values,tip.parse_sdc(LEFT.read_bytes()).values)
        self.assertEqual(t.interpretation,'parity')

    def test_missing_duplicate_or_invalid_arrangement_rejected(self):
        cases=[{155:'配置表なし'},{157:'0,2,1,4.891'},{157:'NaN,2,1,4.891'},
               {157:'2.5,2,1,4.891'},{157:'3,2,1'},
               {158:'杭列数,奥行き本数,,1/β(m)'}]
        for changes in cases:
            for parse in PARSERS:
                with self.subTest(changes=changes,parse=parse.__module__),self.assertRaises(horizontal.InputError):
                    parse(edited(changes))

    def test_ambiguous_or_mismatched_column_headings_rejected(self):
        cases=[(horizontal.parse_sdc,161,',,短期(非線形)-奇数列,短期(非線形)-奇数列'),
               (horizontal.parse_sdc,161,',,短期(非線形)-奇数列,短期(非線形)-2列目'),
               (horizontal.parse_sdc,161,',,短期(非線形)-奇数列'),
               (pressure.parse_pressure_sdc,173,',,1列目,,2列目,,3列目以降奇数列,,4列目'),
               (pressure.parse_pressure_sdc,174,',,下側,上側,上側,下側,上側,下側,上側,下側'),
               (shaft.parse_sdc,231,',,,奇数列,偶数列,1列目,2列目'),
               (shaft.parse_sdc,199,',,偶数列,奇数列,奇数列,偶数列,⊿l(m),奇数列,偶数列,奇数列,偶数列'),
               (tip.parse_sdc,280,'奇数列,偶数列,1列目,2列目')]
        for parse,line,value in cases:
            with self.subTest(parse=parse.__module__,line=line),self.assertRaises(horizontal.InputError):
                parse(edited({line:value}))

    def test_tip_omitted_header_exception_is_narrow(self):
        for changes in ({263:'109024,54512,218048,109024'}, {263:'109024,54512,218048,109024,58945'},
                        {263:'109024,54512,218048,109024,58945,29473,1'},
                        {261:'長期,,地震時'}, {262:'奇数列,偶数列'}, {2:'Ver.9.9.9'},
                        {157:'2,2,1,4.891',280:'1列目,2列目,1列目,2列目'}):
            with self.subTest(changes=changes),self.assertRaises(horizontal.InputError):
                tip.parse_sdc(edited(changes))

    def test_invalid_values_in_unused_categories_are_not_ignored(self):
        for bad in ('-1','NaN','Infinity'):
            cases=[(horizontal.parse_sdc,{157:'1,2,1,4.891',162:f'1,3.300,12660,6330,25319,12660,50638,{bad}'}),
                   (pressure.parse_pressure_sdc,{175:f'1,3.300,1788.9,2636.6,447.2,659.2,715.5,1054.7,0,{bad}'}),
                   (shaft.parse_sdc,{200:f'1,3.300,8168,4084,16336,8168,0,0,{bad},0,0'}),
                   (tip.parse_sdc,{157:'1,2,1,4.891',263:f'109024,54512,218048,109024,58945,{bad}'})]
            for parse,changes in cases:
                with self.subTest(bad=bad,parse=parse.__module__),self.assertRaises(horizontal.InputError):
                    parse(edited(changes))
        with self.assertRaises(horizontal.InputError):
            tip.parse_sdc(edited({157:'1,2,1,4.891',268:'0,1,0,0'}))

    def test_numbered_tables_keep_order_and_actual_count_validation(self):
        layout=sdc_columns.resolve(['2列目','1列目'],2)
        self.assertEqual(layout.indices,{2:0,1:1})
        for labels in (['1列目','1列目'],['1列目','3列目'],['1列目','2列目','3列目']):
            with self.assertRaises(horizontal.InputError):sdc_columns.resolve(labels,2)
        for parse in PARSERS:
            raw=RIGHT.read_bytes().replace(b'3, 3, 2, 4.945',b'4, 3, 2, 4.945')
            with self.subTest(parse=parse.__module__),self.assertRaises(horizontal.InputError):parse(raw)


class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.originals={p:p.read_bytes() for p in (LEFT,RIGHT,NDU)}
        cls.plan=converter.prepare(request())

    @classmethod
    def tearDownClass(cls):
        assert all(p.read_bytes()==raw for p,raw in cls.originals.items())

    def test_independent_expected_values_all_four_operations(self):
        p=self.plan
        self.assertEqual(len(p.rows),207)
        ndu=horizontal.parse_ndu(p.data)
        self.assertEqual([ndu.fields(f'JibanShogenInfo{m}',7)[1] for m in (18,43,68,20)],
                         ['50638','25319','50638','150802'])
        supports=converter.support_values(p.data)
        self.assertEqual(supports[21],['29490','188','188','29490','188','188','29490','29490','29490','29490'])
        self.assertEqual(supports[22][:2],['39976','254.8'])
        for node,expected in [(41,['218048','4778.4','','58945','11149.5','','58945','218048','58945','58945']),
                              (66,['109024','2389.2','','29473','5574.8','','29473','109024','29473','29473'])]:
            self.assertEqual(supports[node],expected)
        self.assertEqual(supports[91],supports[41])
        self.assertEqual(len(p.report['details']['shaft']['nodes']),60)
        self.assertEqual(p.report['details']['shaft']['unchanged_tip_nodes'],[41,66,91])
        self.assertEqual(p.report['details']['tip']['interpretation'],'tip-parity-omitted-gradient-heading')

    def test_direction_buttons_direct_and_legacy_pressure_reversal(self):
        cat=inspect_candidates(self.originals[NDU],self.originals[LEFT])
        self.assertEqual((cat.error,cat.thickness,cat.columns),('',D(31),(1,2,3)))
        self.assertEqual(assign_columns(cat,[3,1,2],'right'),{1:3,2:2,3:1})
        p=converter.prepare(request(groups=('1:3','2:2','3:1')))
        ndu=horizontal.parse_ndu(p.data)
        self.assertEqual([ndu.fields(f'JibanShogenInfo{m}',7)[2:4] for m in (18,43,68)],
                         [['715.5','849.1'],['447.2','530.7'],['1788.9','2122.8']])
        legacy=converter.prepare(request(push_direction='right'))
        self.assertEqual(p.data,legacy.data)
        self.assertEqual(self.plan.data,converter.prepare(request(push_direction='left')).data)
        shared=converter.prepare(request(groups=('1:3','2:3','3:3')))
        self.assertTrue(all(row['column']==3 for op in shared.report['details'].values()
                            for row in op.get('nodes',op.get('members',[]))))

    def test_source_records_match_original_fields_including_shared_parity(self):
        lines=self.originals[LEFT].decode('cp932').splitlines()
        sources=self.plan.report['calculation']['source_values']
        for value in sources:
            self.assertEqual(D(value['value']),D(lines[value['line']-1].split(',')[value['field']-1]))
        self.assertEqual(len([s for s in sources if s['operation']=='horizontal']),14)
        self.assertEqual(len([s for s in sources if s['operation']=='shaft']),28)
        self.assertEqual(len([s for s in sources if s['operation']=='tip']),8)
        self.assertTrue(any(s['label']=='短期(非線形)-奇数列' for s in sources))

    def test_excel_source_edits_share_only_actual_csv_fields(self):
        cases=[('horizontal',162,7,{1,3}),('shaft',202,8,{1,3}),('shaft',234,4,{1,3}),
               ('tip',263,3,{1,3}),('tip',263,5,{1,3}),('tip',281,1,{1,3}),('tip',281,3,{1,3}),
               ('pressure',175,3,{1}),('pressure',175,7,{3})]
        for op,line,field,expected_groups in cases:
            with self.subTest(op=op,line=line,field=field):
                book=excel_report.build(self.plan.report)
                fixed=[c.expected for c in book.expected]
                before=[c.value(book) for c in book.expected]
                source_cell(book,op,line,field).value+=D(100)
                book.recalculate(verify=False)
                changed=[c.target for old,c in zip(before,book.expected) if old!=c.value(book)]
                self.assertTrue(changed)
                self.assertTrue(all(t.startswith(op+':') for t in changed))
                self.assertEqual({int(re.search(r'KG(\d+)',t)[1]) for t in changed},expected_groups)
                self.assertEqual([c.expected for c in book.expected],fixed)
                with self.assertRaises(horizontal.InputError):book.recalculate()
        self.assertEqual(converter.prepare(request()).data,self.plan.data)

    def test_save_reload_idempotency_and_other_foundation_untouched(self):
        before=horizontal.parse_ndu(self.originals[NDU])
        after=horizontal.parse_ndu(self.plan.data)
        changed={m.number for m in horizontal.collect_members(before,{1:1,2:2,3:3})}
        def unrelated(raw):
            result=[]
            for line in raw.splitlines(keepends=True):
                if line.startswith((b'SuppotNum=',b'SuppotRow=',b'Suppot_ChokuKisoCaseNo',b'SuppotInfo')):continue
                m=re.match(rb'JibanShogenInfo(\d+)=',line)
                if m and int(m[1]) in changed:continue
                result.append(line)
            return result
        self.assertEqual(unrelated(self.originals[NDU]),unrelated(self.plan.data))
        for key in before.records:
            if key.startswith('JibanShogenInfo') and int(key[15:]) not in changed:
                self.assertEqual(before.fields(key,7),after.fields(key,7))
        old_supports=shaft.ndu_supports(self.originals[NDU])[2]
        new_supports=shaft.ndu_supports(self.plan.data)[2]
        for key,(_,body) in old_supports.items():self.assertEqual(new_supports[key][1],body)
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)/'左変換.ndu'
            saved=converter.save(self.plan,out)
            self.assertEqual(saved.output.read_bytes(),self.plan.data)
            self.assertEqual(saved.excel.read_bytes()[:2],b'PK')
            audit=json.loads(saved.report.read_text(encoding='utf-8'))
            self.assertEqual(audit['details']['tip']['interpretation'],'tip-parity-omitted-gradient-heading')
            self.assertEqual(converter.prepare(request(ndu=out)).data,self.plan.data)
            right=converter.prepare(request(ndu=out,sdc=RIGHT,groups=('4:1','5:2','6:3')))
            left_values=converter.support_values(self.plan.data)
            both_values=converter.support_values(right.data)
            for node in range(17,92):
                if node in left_values:self.assertEqual(left_values[node],both_values[node])
            both=horizontal.parse_ndu(right.data)
            for member in changed:
                self.assertEqual(after.fields(f'JibanShogenInfo{member}',7),both.fields(f'JibanShogenInfo{member}',7))

    def test_existing_left_supports_are_updated_without_renumbering(self):
        original=ROOT/'snap/今町橋りょう4P(C方向･右押し→).ndu'
        p=converter.prepare(request(ndu=original))
        before=shaft.ndu_supports(original.read_bytes())[2]
        after=shaft.ndu_supports(p.data)[2]
        self.assertEqual(set(before),set(after))
        for key in ('SuppotInfo121','SuppotInfo122','SuppotInfo123'):
            self.assertEqual(before[key][1],after[key][1])
        self.assertEqual(shaft.sync_ndu_support_cases(p.data),p.data)

    def test_all_five_sheets_and_saving_with_one_operation(self):
        self.assertEqual(len(self.plan.workbook.sheets),5)
        self.plan.workbook.to_xlsx()
        for op in converter.OPERATIONS:
            with self.subTest(op=op):
                p=converter.prepare(request(operations=(op,)))
                p.workbook.to_xlsx()
                self.assertEqual(set(p.report['details']),{op})


class GuiTests(unittest.TestCase):
    def test_left_candidate_load_selection_and_buttons(self):
        # 他のGUIテストの循環参照を、読込ワーカーではなくTk所有スレッドで回収する。
        gc.collect()
        self.addCleanup(gc.collect)
        root=tk.Tk();root.withdraw();self.addCleanup(root.destroy)
        ndu,sdc,groups=tk.StringVar(root),tk.StringVar(root),tk.StringVar(root)
        selector=KGSelection(root,ndu,sdc,groups);selector.pack()
        sdc.set(str(LEFT));ndu.set(str(NDU))
        deadline=time.monotonic()+5
        while not selector.ready and time.monotonic()<deadline:
            root.update();time.sleep(.01)
        self.assertTrue(selector.ready,selector.message.get())
        self.assertFalse(any(r.selected.get() for r in selector.rows.values()))
        for g in (1,2,3):selector.rows[g].check.invoke()
        self.assertEqual(selector.selection(),('1:1','2:2','3:3'))
        selector.direction_buttons['right'].invoke()
        self.assertEqual(selector.selection(),('1:3','2:2','3:1'))
        selector.direction_buttons['left'].invoke()
        self.assertEqual(selector.selection(),('1:1','2:2','3:3'))
        selector.rows[2].column.set('3');selector.sync_groups()
        self.assertEqual(selector.selection(),('1:1','2:3','3:3'))


if __name__=='__main__':unittest.main()
