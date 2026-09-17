"""水平ばねの見本レイアウト、一般入力、原値追跡とGUI横移動の回帰検証。"""

from dataclasses import replace
from decimal import Decimal as D
from fractions import Fraction
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
import excel_report as report
from excel_test_helpers import sheet_xml, print_names
from excel_preview import ExcelPreview


class HorizontalReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.request = app.Request(ROOT/'snap/今町橋りょう4P(右).sdc',
                                  ROOT/'snap/今町橋りょう4P(C方向･右押し→).ndu',
                                  operations=('horizontal',))
        cls.plan = app.prepare(cls.request)

    def synthetic(self, lengths, thicknesses, values=None, policy='length-weighted', current=''):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        sdc,ndu = Path(folder.name)/'入力.sdc',Path(folder.name)/'入力.ndu'
        n = len(lengths)
        lines = ['（２）直角方向','b）水平地盤ばね値','層番,層厚(m),水平方向ばね値(kN/m2)',
                 ',,'+','.join(f'短期(非線形)-{c+1}列目' for c in range(n))]
        for i,thickness in enumerate(thicknesses):
            vals = values[i] if values is not None else [100*(i+1)+c for c in range(n)]
            lines.append(','.join(map(str,[i+1,thickness,*vals])))
        sdc.write_bytes(('\r\n'.join(lines)+'\r\n\r\n').encode('cp932'))
        lines = ['DataName=帳票テスト']
        groups = []
        for i,spans in enumerate(lengths):
            g,first,head = i+10,100+i*1000,10000+i*1000
            groups.append(f'{g}:{i+1}')
            lines.append(f'KGInfo{g}=21,{first},{first+len(spans)-1}')
            depth = D('13.041')
            lines.append(f'JointXY{head}={i*5},{depth},{head}')
            for j,span in enumerate(spans):
                depth += D(str(span))
                # 非連番節点。部材番号から節点を作る実装は検出する。
                a,b,m = head+j*3,head+(j+1)*3,first+j
                lines += [f'JointXY{b}={i*5},{depth},{b}',f'ElementInfo{m}=3,0,0,0,{a},{b},75,0',
                          f'JibanShogenInfo{m}= ,{current},0,0, , , ']
        ndu.write_bytes(('\r\n'.join(lines)+'\r\n').encode('cp932'))
        return app.prepare(app.Request(sdc,ndu,operations=('horizontal',),groups=tuple(groups),horizontal_cross_layer=policy))

    def test_reference_layout_and_all_72_values(self):
        book = self.plan.workbook
        sheet = book.sheet('水平地盤ばね')
        self.assertEqual((len(sheet.rows),len(sheet.widths)),(34,21))
        self.assertEqual(sheet.column_merges,{(1,3):4,(1,10):11,(1,17):18})
        self.assertFalse(sheet.merges)
        self.assertEqual(sheet.vertical_breaks,[7,14])
        self.assertEqual(sheet.page_breaks,[])
        self.assertEqual(sheet.freeze,(0,0))
        for c in (0,7,14):
            self.assertEqual(sheet.rows[0][c].value,'水平地盤ばね')
            self.assertEqual(sheet.rows[32][c].value,'Σ')
            self.assertEqual(book.value(sheet.name,32,c+1),31)
            self.assertEqual(book.value(sheet.name,32,c+3),31)
            self.assertEqual([r for r in range(2,32) if sheet.rows[r][c].value is not None],[2,4,6,9,12,13,22])
        by_member = {row['member']:row for row in self.plan.report['details']['horizontal']['members']}
        found = []
        for c in (0,7,14):
            for r in range(2,32):
                cell = sheet.rows[r][c+5]
                if cell.value is None:continue
                member = by_member[cell.value]
                expected = sum(Fraction(str(p['value']))*Fraction(p['length_m']) for p in member['pieces'])
                expected /= Fraction(str(member['geometry']['bottom_m']))-Fraction(str(member['geometry']['top_m']))
                self.assertAlmostEqual(book.value(sheet.name,r,c+6),float(expected),places=8)
                self.assertNotIn('ROUND',sheet.rows[r][c+6].formula.text(sheet.name))
                self.assertEqual(sheet.rows[r][c+6].display(),format(int(member['value']),','))
                found.append(cell.value)
        self.assertEqual(set(found),set(by_member))
        self.assertEqual(len(found),72)
        self.assertEqual([book.value(sheet.name,r,4) for r in (11,12,13)],[0.5,0.6,0.2])
        self.assertIsNone(sheet.rows[2][4].value)
        self.assertIsNone(sheet.rows[2][4].formula)
        self.assertNotIn('B',sheet.rows[3][5].borders)
        self.assertNotIn('T',sheet.rows[4][5].borders)
        self.assertNotIn('R',sheet.rows[3][3].borders)
        self.assertNotIn('L',sheet.rows[3][4].borders)

    def test_saved_merges_printing_styles_and_numeric_cells(self):
        ns = {'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        with zipfile.ZipFile(BytesIO(self.plan.workbook.to_xlsx())) as z:
            root = sheet_xml(z,'水平地盤ばね')
            self.assertEqual(root.find('m:dimension',ns).get('ref'),'A1:U34')
            self.assertEqual({e.get('ref') for e in root.findall('m:mergeCells/m:mergeCell',ns)}, {'D2:E2','K2:L2','R2:S2'})
            self.assertEqual(root.find('m:pageSetup',ns).get('orientation'),'portrait')
            self.assertEqual(root.find('m:pageSetup',ns).get('scale'),'90')
            cells = {e.get('r'):e for e in root.findall('m:sheetData/m:row/m:c',ns)}
            self.assertEqual(cells['G4'].findtext('m:f',namespaces=ns),'(SUM((C4*E4),(C5*E5))/D4)')
            self.assertIsNone(cells['F3'].get('t'))
            styles = ET.fromstring(z.read('xl/styles.xml'))
            xf = list(styles.find('m:cellXfs',ns))[int(cells['G4'].get('s'))]
            font = list(styles.find('m:fonts',ns))[int(xf.get('fontId'))]
            self.assertEqual(font.find('m:name',ns).get('val'),'ＭＳ 明朝')
            wb = ET.fromstring(z.read('xl/workbook.xml'))
            names = print_names(z,'水平地盤ばね')
            self.assertIn('$A$1:$U$34',names['_xlnm.Print_Area'])
            self.assertIn('$1:$2',names['_xlnm.Print_Titles'])

    def test_selected_blocks_are_compact_and_ordered_by_sdc_column(self):
        for groups,expected in [(('5:2',),[123]),(('6:3','4:1'),[98,148])]:
            plan = app.prepare(replace(self.request,groups=groups))
            sheet = plan.workbook.sheet('水平地盤ばね')
            self.assertEqual(len(sheet.widths),7*len(groups))
            self.assertEqual([sheet.rows[2][c+5].value for c in range(0,len(sheet.widths),7)],expected)
            plan.workbook.to_xlsx()

    def test_four_blocks_different_splits_and_four_layer_average(self):
        plan = self.synthetic([[1],['0.1','0.9'],['0.5','0.5'],['0.3','0.7']],['0.25']*4)
        sheet = plan.workbook.sheet('水平地盤ばね')
        self.assertEqual(len(sheet.widths),28)
        self.assertEqual(sheet.vertical_breaks,[7,14,21])
        self.assertEqual(len(sheet.rows),9)
        self.assertEqual(plan.workbook.value(sheet.name,2,6),250)
        for c in (0,7,14,21):self.assertEqual(plan.workbook.value(sheet.name,7,c+3),1)
        plan.workbook.to_xlsx()

    def test_rounding_and_single_layer_decimal_are_separate_from_display(self):
        plan = self.synthetic([[2]],[1,1],[[100],[101]])
        sheet = plan.workbook.sheet('水平地盤ばね')
        self.assertEqual(sheet.rows[2][6].cached,100.5)
        self.assertEqual(sheet.rows[2][6].display(),'101')
        self.assertEqual(plan.report['details']['horizontal']['members'][0]['value'],'101')
        plan = self.synthetic([[1]],[2],[['100.5']])
        self.assertEqual(plan.report['details']['horizontal']['members'][0]['value'],'100.5')
        self.assertEqual(plan.workbook.expected[0].value(plan.workbook),100.5)
        sheet = plan.workbook.sheet('水平地盤ばね')
        self.assertEqual(plan.workbook.value(sheet.name,3,1),2)  # 層厚と部材長の合計は異なってよい。
        self.assertEqual(plan.workbook.value(sheet.name,3,3),1)
        plan.workbook.to_xlsx()

    def test_midpoint_skip_blank_zero_and_error_policies(self):
        plan = self.synthetic([[2]],[1,1],[[100],[101]],policy='midpoint')
        sheet = plan.workbook.sheet('水平地盤ばね')
        self.assertEqual(sheet.rows[2][6].cached,101)
        self.assertIn('中央の層',sheet.rows[0][0].value)
        for current,expected in [('',None),('0',0),('77.5',77.5)]:
            plan = self.synthetic([[2]],[1,1],policy='skip',current=current)
            sheet = plan.workbook.sheet('水平地盤ばね')
            self.assertEqual(sheet.rows[2][6].value,expected)
            self.assertIn('保留',sheet.rows[0][0].value)
            self.assertFalse(plan.workbook.expected)
            plan.workbook.to_xlsx()
        with self.assertRaisesRegex(app.InputError,'層境界'):
            self.synthetic([[2]],[1,1],policy='error')

    def test_long_tables_keep_members_together_or_label_continuation(self):
        ordinary = self.synthetic([[1]*70],[1]*70)
        sheet = ordinary.workbook.sheet('水平地盤ばね')
        self.assertGreater(len(sheet.page_breaks),0)
        self.assertFalse(any('続き' in str(c.value) for row in sheet.rows for c in row))
        long = self.synthetic([[70]],[1]*70)
        sheet = long.workbook.sheet('水平地盤ばね')
        self.assertEqual(len(sheet.page_breaks),2)
        for r in sheet.page_breaks:self.assertEqual(sheet.rows[r][5].value,'100\n（続き）')
        self.assertEqual(long.workbook.value(sheet.name,72,3),70)
        self.assertEqual(long.workbook.value(sheet.name,2,6),3550)
        long.workbook.to_xlsx()

    def test_gui_scroll_selection_merge_beyond_column_l(self):
        root = tk.Tk()
        root.withdraw()
        root.geometry('1200x800')
        self.addCleanup(root.destroy)
        preview = ExcelPreview(root)
        preview.pack(fill='both',expand=True)
        preview.show(self.plan.workbook)
        preview.sheet_name.set('水平地盤ばね')
        preview.select_sheet()
        preview.canvas.configure(width=1100,height=600)
        root.attributes('-alpha',0)
        root.deiconify()
        root.update()
        preview.section_name.set('KG6 / SDC3列目')
        preview.go_section()
        root.update_idletasks()
        self.assertGreater(preview.canvas.canvasx(0),0)
        labels = [preview.canvas.itemcget(i,'text') for i in preview.canvas.find_all() if preview.canvas.type(i)=='text']
        self.assertIn('U',labels)
        def select(r,c):
            x=(preview.xs[c]+preview.xs[c+1])/2-preview.canvas.canvasx(0)
            y=(preview.ys[r]+preview.ys[r+1])/2-preview.canvas.canvasy(0)
            preview.select_cell(SimpleNamespace(x=x,y=y))
        select(1,18)  # S2を選ぶと結合見出しR2へ。
        self.assertEqual(preview.cell_name.get(),'R2')
        select(3,20)
        self.assertEqual(preview.cell_name.get(),'U4')
        self.assertIn('Q4',preview.formula.get())
        self.assertNotIn('変換結果',preview.sheet_box['values'])
        self.assertNotIn('入力根拠',preview.sheet_box['values'])
        self.assertGreater(preview.canvas.canvasx(0),0)


if __name__=='__main__': unittest.main()
