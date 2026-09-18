"""土圧の見本配置、独立計算、縦結合・印刷とGUIの回帰検証。"""

from dataclasses import replace
from decimal import Decimal as D, ROUND_HALF_UP
from fractions import Fraction as F
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import tkinter as tk
import unittest
from xml.etree import ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import portable_converter as app
from tests.fixture_paths import IMACHO_RIGHT_NDU, IMACHO_RIGHT_SDC
import excel_report as report
from excel_test_helpers import sheet_xml, source_cell, print_names
from excel_preview import ExcelPreview


class PressureReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.request = app.Request(IMACHO_RIGHT_SDC, IMACHO_RIGHT_NDU,
                                  operations=('pressure',))
        cls.plan = app.prepare(cls.request)

    def synthetic(self,lengths,thicknesses,values=None,**options):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        sdc,ndu = Path(folder.name)/'入力.sdc',Path(folder.name)/'入力.ndu'
        n = len(lengths)
        lines = ['（２）直角方向','c）有効抵抗土圧力','・応答変位法以外の場合',
                 '層番,層厚(m),地震時：有効抵抗土圧力(kN/m)',
                 ',,'+',,'.join(f'{c+1}列目' for c in range(n)),',,'+','.join(['上側','下側']*n)]
        for i,h in enumerate(thicknesses):
            pairs = values[i] if values is not None else [(100*(i+1)+c,100*(i+1)+c+10) for c in range(n)]
            lines.append(','.join(map(str,[i+1,h,*[v for pair in pairs for v in pair]])))
        sdc.write_bytes(('\r\n'.join(lines)+'\r\n\r\n').encode('cp932'))
        lines,groups = ['DataName=土圧帳票テスト'],[]
        for i,spans in enumerate(lengths):
            g,first,head = i+10,100+i*1000,10000+i*1000
            groups.append(f'{g}:{i+1}')
            lines.append(f'KGInfo{g}=21,{first},{first+len(spans)-1}')
            depth = D('13.041')
            lines.append(f'JointXY{head}={i*5},{depth},{head}')
            for j,span in enumerate(spans):
                depth += D(str(span))
                a,b,m = head+j*3,head+(j+1)*3,first+j
                lines += [f'JointXY{b}={i*5},{depth},{b}',f'ElementInfo{m}=3,0,0,0,{a},{b},75,0',
                          f'JibanShogenInfo{m}= ,100, , , , , ']
        ndu.write_bytes(('\r\n'.join(lines)+'\r\n').encode('cp932'))
        return app.prepare(app.Request(sdc,ndu,operations=('pressure',),groups=tuple(groups),**options))

    def assert_independent(self,plan):
        book,sheet = plan.workbook,plan.workbook.sheet('有効抵抗土圧')
        rows = {row['member']:row for row in plan.report['details']['pressure']['members']}
        found = set()
        for c in range(0,len(sheet.widths),9):
            for r,cells in enumerate(sheet.rows):
                member = cells[c+7].value
                if not isinstance(member,int):continue
                row = rows[member]
                ps = []
                for p in row['pieces']:
                    a,b,u,v = [F(str(p[key])) for key in ('layer_top_m','layer_bottom_m','top_m','bottom_m')]
                    upper,lower = F(str(p['layer_upper'])),F(str(p['layer_lower']))
                    at = lambda z:upper+(lower-upper)*(z-a)/(b-a)
                    ps.append((at(u),at(v),v-u))
                if row['method']=='integral-average':
                    exact = sum((u+v)/2*l for u,v,l in ps)/sum(l for u,v,l in ps)
                    expected = (exact,exact)
                else:expected = (ps[0][0],ps[-1][1])
                last = r+2*len(ps)-1
                for rr,value,actual in zip((r,last),expected,row['calculated']):
                    self.assertAlmostEqual(book.value(sheet.name,rr,c+8),float(value),places=8)
                    rounded = (D(value.numerator)/D(value.denominator)).quantize(
                        D(1).scaleb(-plan.report['configuration']['pressure_decimals']),rounding=ROUND_HALF_UP)
                    self.assertEqual(rounded,D(actual))
                found.add(member)
        self.assertEqual(found,set(rows))
        book.recalculate()

    def test_reference_layout_and_all_endpoints(self):
        sheet = self.plan.workbook.sheet('有効抵抗土圧')
        self.assertEqual((len(sheet.rows),len(sheet.widths)),(64,27))
        self.assertEqual(len(sheet.merged_ranges()),180)
        self.assertEqual(sheet.column_merges,{(1,3):4,(1,12):13,(1,21):22})
        self.assertEqual(sheet.range_merges[4,3],(7,3))
        self.assertEqual(sheet.range_merges[20,7],(25,7))
        self.assertEqual(sheet.freeze,(0,0))
        self.assertEqual(sheet.vertical_breaks,[9,18])
        for c in (0,9,18):
            self.assertEqual(sheet.rows[0][c].value,'有効抵抗土圧')
            self.assertEqual([r for r in range(2,62) if sheet.rows[r][c].value is not None],[2,6,10,16,22,24,42])
            self.assertEqual(sheet.rows[62][c].value,'Σ')
            for j in (1,3):self.assertEqual(self.plan.workbook.value(sheet.name,62,c+j),31)
            for j in (2,6,8):
                self.assertIn('土圧力\npe(z)\n(kN/m)',sheet.rows[1][c+j].value)
                self.assertFalse(any(cc==c+j for rr,cc in sheet.range_merges))
        self.assertEqual(sheet.rows[3][8].display(),'1307.5')
        self.assertEqual(sheet.rows[3][8].cached,1307.46)
        self.assertEqual(sheet.rows[2][5].display(),'0.000')
        self.assertIsNone(sheet.rows[5][8].formula)
        self.assertIsNone(sheet.rows[2][4].formula)
        self.assert_independent(self.plan)

    def test_saved_merges_values_styles_and_printing(self):
        ns = {'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        with zipfile.ZipFile(BytesIO(self.plan.workbook.to_xlsx())) as z:
            root = sheet_xml(z,'有効抵抗土圧')
            self.assertEqual(root.find('m:dimension',ns).get('ref'),'A1:AA64')
            merges = {e.get('ref') for e in root.findall('m:mergeCells/m:mergeCell',ns)}
            self.assertTrue({'D3:D4','D5:D8','H21:H26','E21:E22','V2:W2'}<=merges)
            self.assertEqual(len(merges),180)
            self.assertEqual(root.find('m:pageSetup',ns).get('orientation'),'portrait')
            self.assertEqual(root.find('m:pageSetup',ns).get('pageOrder','downThenOver'),'downThenOver')
            cells = {e.get('r'):e for e in root.findall('m:sheetData/m:row/m:c',ns)}
            self.assertIsNone(cells['D3'].find('m:f',ns))
            self.assertEqual(float(cells['D3'].findtext('m:v',namespaces=ns)),1.3)
            self.assertIsNone(cells['D4'].find('m:v',ns))
            styles = ET.fromstring(z.read('xl/styles.xml'))
            xfs = list(styles.find('m:cellXfs',ns))
            self.assertEqual(xfs[int(cells['G3'].get('s'))].find('m:alignment',ns).get('vertical'),'top')
            self.assertEqual(xfs[int(cells['G4'].get('s'))].find('m:alignment',ns).get('vertical','bottom'),'bottom')
            names = list(print_names(z,'有効抵抗土圧').values())
            self.assertTrue(any('$A$1:$AA$63' in n for n in names))
            self.assertTrue(any('$1:$2' in n for n in names))

    def test_source_edit_isolated_by_column_and_endpoint_and_fixed_values(self):
        book = report.build(self.plan.report)
        original_fixed = list(book.expected)
        baseline = {(r,c):book.sheet('有効抵抗土圧').rows[r][c].cached for r,c in [(2,8),(3,8),(2,17),(2,26)]}
        cell = source_cell(book,'pressure',187,7)
        cell.value += 100
        book.recalculate(verify=False)
        sheet = book.sheet('有効抵抗土圧')
        self.assertAlmostEqual(sheet.rows[2][8].cached,baseline[2,8]+100)
        self.assertAlmostEqual(sheet.rows[3][8].cached,baseline[3,8]+100*(1-1.3/1.5))
        self.assertEqual(sheet.rows[2][17].cached,baseline[2,17])
        self.assertEqual(sheet.rows[2][26].cached,baseline[2,26])
        self.assertEqual(original_fixed,book.expected)
        with self.assertRaisesRegex(app.InputError,'pressure:KG4:98'):book.recalculate()

    def test_selected_groups_and_direction_use_total_sdc_columns(self):
        for groups,direction,members,values in [
            (('5:2',),'right',[123],[1005.1]),
            (('6:3','4:1'),'right',[98,148],[1162.9,2907.3]),
            (('4:1',),'left',[98],[2907.3])]:
            plan = app.prepare(replace(self.request,groups=groups,push_direction=direction))
            sheet = plan.workbook.sheet('有効抵抗土圧')
            self.assertEqual(len(sheet.widths),9*len(groups))
            self.assertEqual([sheet.rows[2][c+7].value for c in range(0,len(sheet.widths),9)],members)
            self.assertEqual([sheet.rows[2][c+8].cached for c in range(0,len(sheet.widths),9)],values)
            self.assert_independent(plan)

    def test_four_blocks_different_splits_and_partial_layer(self):
        plan = self.synthetic([[1],['0.1','0.9'],['0.5','0.5'],['0.3','0.7']],['0.25']*4)
        sheet = plan.workbook.sheet('有効抵抗土圧')
        self.assertEqual(len(sheet.widths),36)
        self.assertEqual(sheet.vertical_breaks,[9,18,27])
        self.assert_independent(plan)
        plan.workbook.to_xlsx()
        partial = self.synthetic([['0.5','0.5']],[2],[[(100,300)]])
        sheet = partial.workbook.sheet('有効抵抗土圧')
        self.assertEqual([sheet.rows[r][8].cached for r in (2,3,4,5)],[100,150,150,200])
        self.assertEqual(partial.workbook.value(sheet.name,6,1),2)
        self.assertEqual(partial.workbook.value(sheet.name,6,3),1)

    def test_nondefault_policies_rounding_zero_and_boundary(self):
        for decimals in (0,1,3,6):
            plan = self.synthetic([[2]],[1,1],[[('10.05','10.05')],[('20.05','20.05')]],pressure_decimals=decimals)
            self.assert_independent(plan)
            plan.workbook.to_xlsx()
        plan = self.synthetic([[2]],[1,1],[[(10,20)],[(100,200)]],pressure_cross_layer='endpoints')
        sheet = plan.workbook.sheet('有効抵抗土圧')
        self.assertEqual((sheet.rows[2][8].cached,sheet.rows[5][8].cached),(10,200))
        self.assertIn('上下端採用',sheet.rows[0][0].value)
        with self.assertRaisesRegex(app.InputError,'層境界'):
            self.synthetic([[2]],[1,1],pressure_cross_layer='error')
        plan = self.synthetic([[1,1]],[1,1],[[(0,0)],[(100,200)]],pressure_cross_layer='error')
        sheet = plan.workbook.sheet('有効抵抗土圧')
        self.assertEqual([sheet.rows[r][8].cached for r in (2,3,4,5)],[0,0,100,200])
        self.assertEqual(sheet.rows[2][8].display(),'0.0')
        self.assertEqual(len(sheet.rows),8)
        plan.workbook.to_xlsx()

    def test_long_members_split_merges_without_double_counting(self):
        for spans in ([[1]*70],[[70]],[[70],[1]*70]):
            plan = self.synthetic(spans,[1]*70)
            sheet = plan.workbook.sheet('有効抵抗土圧')
            self.assertGreater(len(sheet.page_breaks),0)
            for br in sheet.page_breaks:
                self.assertEqual(br%2,0)
                self.assertFalse(any(a<br<=b for (a,c),(b,e) in sheet.range_merges.items()))
            for c in range(0,len(sheet.widths),9):
                self.assertEqual(plan.workbook.value(sheet.name,sheet.print_last_row,c+3),70)
            if len(spans[0])==1:
                for r in sheet.page_breaks:self.assertEqual(sheet.rows[r][7].value,'100\n（続き）')
            self.assert_independent(plan)
            plan.workbook.to_xlsx()

    def test_merge_validation_and_zero_formula_anchor(self):
        sheet = report.Sheet('結合検証',widths=[10,10],layout='pressure')
        sheet.add([report.Cell(formula=report.expression(0)),None])
        sheet.add([None,None])
        sheet.range_merges[0,0]=(1,0)
        book = report.ReportBook([sheet])
        book.to_xlsx()
        sheet.rows[1][0].value=5
        with self.assertRaisesRegex(app.InputError,'覆われる'):book.to_xlsx()
        sheet.rows[1][0].value=None
        sheet.range_merges[1,0]=(1,1)
        with self.assertRaisesRegex(app.InputError,'重複'):book.to_xlsx()

    def test_selected_sheets_and_no_pressure_dependencies_when_unselected(self):
        both = app.prepare(replace(self.request,operations=('horizontal','pressure')))
        self.assertEqual([s.name for s in both.workbook.sheets],['水平地盤ばね','有効抵抗土圧'])
        self.assertEqual([s.name for s in self.plan.workbook.sheets],['有効抵抗土圧'])
        none = app.prepare(replace(self.request,operations=('horizontal',)))
        self.assertNotIn('有効抵抗土圧',[s.name for s in none.workbook.sheets])
        self.assertFalse(any(check.target.startswith('pressure:') for check in none.workbook.expected))

    def test_gui_rectangle_selection_offscreen_anchor_and_right_block(self):
        root = tk.Tk()
        root.withdraw()
        root.geometry('1250x800')
        self.addCleanup(root.destroy)
        preview = ExcelPreview(root)
        preview.pack(fill='both',expand=True)
        preview.show(self.plan.workbook)
        preview.sheet_name.set('有効抵抗土圧')
        preview.select_sheet()
        root.attributes('-alpha',0)
        root.deiconify()
        root.update()
        preview.section_name.set('KG6 / モデル3列目 / 土圧SDC1列目')
        preview.go_section()
        root.update_idletasks()
        def event(r,c):
            return SimpleNamespace(x=(preview.xs[c]+preview.xs[c+1])/2-preview.canvas.canvasx(0),
                                   y=(preview.ys[r]+preview.ys[r+1])/2-preview.canvas.canvasy(0))
        preview.select_cell(event(6,25))
        self.assertEqual(preview.cell_name.get(),'Z5')
        self.assertEqual(preview.formula.get(),'149')
        preview.select_cell(event(3,26))
        self.assertEqual(preview.cell_name.get(),'AA4')
        self.assertIn('Y4',preview.formula.get())
        preview.scroll_to(22,18)
        root.update_idletasks()
        labels = [preview.canvas.itemcget(i,'text') for i in preview.canvas.find_all() if preview.canvas.type(i)=='text']
        self.assertIn('154',labels)  # Z21が画面外でもZ21:Z26は可視。
        self.assertIn('AA',labels)
        self.assertEqual(tuple(preview.sheet_box['values']),('有効抵抗土圧',))
        self.assertNotIn('リンク',preview.formula.get())


if __name__=='__main__':unittest.main()
