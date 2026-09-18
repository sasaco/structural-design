"""補助シート削除B方式の構成、原値共有、内部照合、保存時検証。"""

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal as D
from io import BytesIO
from itertools import combinations
import json
from pathlib import Path
import sys
import tempfile
import unittest
from xml.etree import ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import portable_converter as app
from tests.fixture_paths import IMACHO_RIGHT_NDU, IMACHO_RIGHT_SDC
import excel_report as report
from excel_test_helpers import source_cell

NS = {'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


class RemoveSupportSheetsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.request = app.Request(IMACHO_RIGHT_SDC, IMACHO_RIGHT_NDU,
                                  shaft_profile='existing-screen')
        cls.plan = app.prepare(cls.request)

    def test_all_operation_combinations_have_only_ordered_main_sheets(self):
        operations = tuple(app.OPERATIONS)
        for size in range(1,5):
            for selected in combinations(operations,size):
                with self.subTest(operations=selected):
                    # 呼出側の指定順に依存せず、最初の残存主表を開く。
                    plan = app.prepare(replace(self.request,operations=selected[::-1]))
                    names = []
                    for op in selected:
                        names.append(report.SHEET_NAMES[op])
                        if op=='shaft': names.append(report.SHAFT_FORCE_SHEET)
                    self.assertEqual([s.name for s in plan.workbook.sheets],names)
                    with zipfile.ZipFile(BytesIO(plan.workbook.to_xlsx())) as z:
                        wb = ET.fromstring(z.read('xl/workbook.xml'))
                        self.assertEqual([s.get('name') for s in wb.findall('m:sheets/m:sheet',NS)],names)
                        self.assertEqual(wb.find('m:bookViews/m:workbookView',NS).get('activeTab','0'),'0')

    def test_same_source_column_is_shared_across_kg_but_other_columns_are_independent(self):
        # 帳票ビルダーの共有機能を、独立した正規の変換記録を組み合わせて検証する。
        # 同じSDC列を直接指定する変換経路はtest_column_assignmentでも検証する。
        second = app.prepare(replace(self.request,groups=('5:1',)))
        record = deepcopy(self.plan.report)
        for op,detail in record['details'].items():
            for key in ('members','nodes','excluded'):
                if key in detail:
                    detail[key] = [r for r in detail[key] if r['group']!=5]+second.report['details'][op][key]
        for key in ('targets','fields'):
            record['calculation'][key] = [r for r in record['calculation'][key] if r['group']!=5]+second.report['calculation'][key]
        for op in app.OPERATIONS:
            with self.subTest(operation=op):
                book = report.build(record)
                key = next(k for k in book.sources if k[0]==op)
                anchor = source_cell(book,*key)
                self.assertIsNone(anchor.formula)
                anchor.value += 1000
                book.recalculate(verify=False)
                changed = [c.target for c in book.expected if D(str(c.value(book)))!=D(c.expected)]
                self.assertTrue(any(t.startswith(op+':KG4:') for t in changed),changed)
                self.assertTrue(any(t.startswith(op+':KG5:') for t in changed),changed)
                self.assertFalse(any(':KG6:' in t for t in changed),changed)
                self.assertTrue(all(t.startswith(op+':') for t in changed),changed)
        book = report.build(self.plan.report)
        first,third = source_cell(book,'tip',275,4),source_cell(book,'tip',275,6)
        self.assertEqual(first.value,third.value)
        self.assertIsNot(first,third)
        first.value += 1000
        book.recalculate(verify=False)
        self.assertEqual(third.value,D('327072'))

    def test_all_adoption_and_geometry_checks_remain_internal(self):
        book = self.plan.workbook
        self.assertEqual((len(book.expected),len(book.geometry_checks)),(348,153))
        self.assertEqual(len(book.sources),105)
        self.assertEqual(len(self.plan.report['calculation']['source_values']),117)
        self.assertEqual(len(self.plan.report['calculation']['fields']),846)
        self.assertEqual(sum(f['after'] is None for f in self.plan.report['calculation']['fields']),6)
        for sheet in book.sheets:
            self.assertFalse(any(cell.style=='actual' for row in sheet.rows for cell in row))
            for row in sheet.rows:
                for cell in row:
                    if cell.formula:
                        self.assertNotIn('!',cell.formula.text(sheet.name))
        for name,r,c in book.sources.values():
            self.assertIsNone(book.sheet(name).rows[r][c].formula)

    def test_zero_resistance_geometry_corruption_is_detected(self):
        book = report.build(self.plan.report)
        sheet = book.sheet('杭周面ばね')
        self.assertEqual(book.value(sheet.name,2,6),0)
        sheet.rows[2][3].value += D('0.125')
        book.recalculate(verify=False)
        self.assertEqual(book.value(sheet.name,2,6),0)
        with self.assertRaisesRegex(app.InputError,'shaft:KG4:.*負担全幅'):
            book.recalculate()

    def test_tip_geometry_is_checked_independently_of_adopted_values(self):
        record = deepcopy(self.plan.report)
        record['details']['tip']['nodes'][0]['pile_length_m'] = '32'
        with self.assertRaisesRegex(app.InputError,'tip:KG4:122:杭長'):
            report.build(record)

    def test_missing_cross_sheet_out_of_bounds_and_circular_references_are_rejected(self):
        for ref,message in [(('入力根拠',0,0),'主表外'),
                            (('有効抵抗土圧',2,2),'主表外'),
                            (('水平地盤ばね',10000,2),'参照先セル'),
                            (('水平地盤ばね',2,6),'循環参照')]:
            with self.subTest(ref=ref):
                book = report.build(self.plan.report)
                book.sheets[0].rows[2][6].formula = report.Expr('ref',ref)
                with self.assertRaisesRegex(app.InputError,message): book.recalculate()
        book = report.build(self.plan.report)
        book.sheets[0].rows[0][0].link = ('入力根拠',0,0)
        with self.assertRaisesRegex(app.InputError,'参照先シート'): book.recalculate()

    def test_saved_sheet_view_print_link_and_raw_cell_corruption_are_rejected(self):
        book = self.plan.workbook
        data = book.to_xlsx()
        cases = [
            ('xl/workbook.xml','m:sheets/m:sheet',lambda e:e.set('name','変換結果'),'シート構成'),
            ('xl/workbook.xml','m:bookViews/m:workbookView',lambda e:e.set('activeTab','1'),'初期表示'),
            ('xl/workbook.xml','m:definedNames/m:definedName',lambda e:setattr(e,'text','A1'),'印刷範囲'),
            ('xl/worksheets/sheet1.xml','m:pageSetup',lambda e:e.set('scale','75'),'印刷設定'),
            ('xl/worksheets/sheet1.xml','m:colBreaks/m:brk',lambda e:e.set('id','8'),'改ページ'),
            ('xl/worksheets/sheet1.xml','.',lambda e:ET.SubElement(ET.SubElement(e,'{'+NS['m']+'}hyperlinks'),'{'+NS['m']+'}hyperlink',ref='A1',location="'入力根拠'!A1"),'内部リンク'),
            ('xl/worksheets/sheet1.xml',"m:sheetData/m:row/m:c[@r='C3']",lambda e:ET.SubElement(e,'{'+NS['m']+'}f'),'数値'),
            ('xl/worksheets/sheet1.xml',"m:sheetData/m:row/m:c[@r='C3']",lambda e:e.clear(),'数値'),
        ]
        for path,selector,change,message in cases:
            with self.subTest(message=message,selector=selector):
                output = BytesIO()
                with zipfile.ZipFile(BytesIO(data)) as source,zipfile.ZipFile(output,'w') as target:
                    for entry in source.infolist():
                        body = source.read(entry.filename)
                        if entry.filename==path:
                            root = ET.fromstring(body)
                            change(root if selector=='.' else root.find(selector,NS))
                            body = ET.tostring(root)
                        target.writestr(entry,body)
                with self.assertRaisesRegex(app.InputError,message): report.verify_xlsx(output.getvalue(),book)

    def test_preview_and_saved_identity_remain_in_document_properties(self):
        with tempfile.TemporaryDirectory() as folder:
            saved = app.save(self.plan,Path(folder)/'結果.ndu')
            saved_record = json.loads(saved.report.read_text(encoding='utf8'))
            for mode,book in [('preview',self.plan.workbook),('saved',saved.workbook)]:
                with zipfile.ZipFile(BytesIO(book.to_xlsx())) as archive:
                    props = {e.get('name'):list(e)[0].text for e in ET.fromstring(archive.read('docProps/custom.xml'))}
                self.assertEqual(props['SDCConverter.Mode'],mode)
                self.assertEqual(props['SDCConverter.RunId'],(saved_record if mode=='saved' else self.plan.report)['run_id'])
                self.assertEqual(props['SDCConverter.OutputSHA256'],app.digest(self.plan.data))
                self.assertEqual(props['SDCConverter.Status'],'モデル保存と同一実行' if mode=='saved' else '計算確認・モデル未保存')


if __name__=='__main__': unittest.main()
