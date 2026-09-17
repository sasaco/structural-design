"""先端2表の原値・内部照合・可変件数・改ページとGUIを検証する。"""

from dataclasses import replace
from decimal import Decimal as D
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
from excel_test_helpers import sheet_xml, source_cell, print_names
from excel_preview import ExcelPreview
from test_fill_pile_tip_suppot_info import sdc_bytes
from test_fill_suppot_info import ndu_bytes


def synthetic_request(folder, count, length='7.25'):
    """異なる4原値・非連番KG・最深節点が最大番号でない独立した実入力。"""
    n=count
    cols=[f'{c+1}列目' for c in range(n)]
    lines=['杭長,突出長,根入れ深さ,その他',f'{length},0,0.5,0','（２）直角方向',
           '杭列数,奥行き本数,,1/β(m)',',奇数列,偶数列',f'{n},3,2,0.5','f）杭先端の地盤ばね値']
    def table(title,labels,values):
        lines.extend([title,','.join(v if j==0 else '' for v in labels for j in range(n)),
                      ','.join(cols*len(labels)),','.join(map(str,values)),''])
    table('杭先端の鉛直ばね値(kN/m)',['長期','短期(第1勾配)','短期(第2勾配)'],
          [1]*n+[D('1000.12567')+i for i in range(n)]+[D('50.375')+i for i in range(n)])
    for title in ('杭先端の水平ばね値(kN/m)','杭先端の回転ばね値(kN/m)'):
        table(title,['長期','短期'],[0]*(2*n))
    lines.append('g）杭先端の支持力')
    table('地震時：杭先端の鉛直地盤支持力(kN)',['押し込み側(降伏点)','押し込み側(終局点)'],
          [D('7.125')+i for i in range(n)]+[D('17.375')+i for i in range(n)])
    sdc,ndu=Path(folder)/'先端.sdc',Path(folder)/'先端.ndu'
    sdc.write_bytes(('\r\n'.join(lines)+'\r\n').encode('cp932'))
    lines=['DataName=先端帳票検証','SuppotNum=0','SuppotRow=0','ShitenCaseNum=0','G_intCHOKU_KISO_Link_Num=0']
    groups=[]
    for i in range(n):
        group,member,head,tip=10+3*i,100+7*i,10000+10*i,10001+10*i
        # head番号をtipより大きくする。部材の両端も逆向き。
        head+=5
        groups.append(f'{group}:{i+1}')
        lines.extend([f'KGInfo{group}=21,{member},{member}',f'JointXY{head}={i*5},13.041,{head}',
                      f'JointXY{tip}={i*5},{D("13.041")+D(length)},{tip}',
                      f'ElementInfo{member}=3,0,0,0,{tip},{head},0,0',f'JibanShogenInfo{member}= ,0,0,0, , , '])
    ndu.write_bytes(('\r\n'.join(lines)+'\r\n').encode('cp932'))
    return app.Request(sdc,ndu,operations=('tip',),groups=tuple(groups))


class TipReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.request=app.Request(ROOT/'test/今町橋りょう4P(右).sdc',ROOT/'test/今町橋りょう4P(C方向･右押し→).ndu',operations=('tip',))
        cls.plan=app.prepare(cls.request)

    def test_reference_cells_sources_and_all_ten_fields(self):
        book=self.plan.workbook; sheet=book.sheet('杭先端ばね')
        self.assertEqual((len(sheet.rows),len(sheet.widths)),(14,3))
        self.assertEqual(sheet.merged_ranges(),{(1,0):(2,0),(1,1):(1,2),(9,0):(10,0),(9,1):(9,2)})
        self.assertEqual([s[0] for s in sheet.sections],['杭先端のばね定数','先端支持力'])
        self.assertEqual(sheet.freeze,(0,0));self.assertFalse(sheet.page_breaks)
        self.assertEqual(self.plan.data,self.request.ndu.read_bytes())
        expected=[(122,327072,88418,'7167.5','16724.3'),(147,218048,58945,'4778.4','11149.5'),(172,327072,88418,'7167.5','16724.3')]
        raw=dict(line.split(b'=',1) for line in self.plan.data.splitlines() if b'=' in line)
        for i,(node,k1,k2,fy,fu) in enumerate(expected):
            for r,vals in ((3+i,(k1,k2)),(11+i,(fy,fu))):
                self.assertEqual(sheet.rows[r][0].value,node)
                for c,v in enumerate(vals,1):
                    self.assertEqual(D(str(book.value(sheet.name,r,c))),D(str(v)))
                    self.assertIsNone(sheet.rows[r][c].formula)
                    source_key=('tip',275 if r<8 else 293,(3*c+i+1) if r<8 else (3*(c-1)+i+1))
                    self.assertEqual(book.sources[source_key],(sheet.name,r,c))
            tokens=raw[f'SuppotInfo{61+i}'.encode()].split(b',')[3:]
            values=[D(t.decode().strip()) if t.strip() else None for t in tokens]
            self.assertEqual(values,list(map(lambda v:D(str(v)) if v is not None else None,[k1,fy,None,k2,fu,None,k2,k1,k2,k2])))
        self.assertEqual(len(book.expected),12)
        self.assertEqual(len(book.geometry_checks),3)
        self.assertTrue(all(D(str(check.value(book)))==D(check.expected) for check in (*book.expected,*book.geometry_checks)))
        self.assertEqual(sheet.rows[3][1].display(),'327072')
        self.assertEqual(sheet.rows[11][1].display(),'7167.5')

    def test_four_sources_recalculate_independently_and_fixed_blanks_stay(self):
        for line,field,cell in [(275,4,(3,1)),(275,7,(3,2)),(293,1,(11,1)),(293,4,(11,2))]:
            book=report.build(self.plan.report)
            before={check.expr.args:check.value(book) for check in book.expected}
            fixed=[check.expected for check in book.expected]
            record_before=[f['after'] for f in self.plan.report['calculation']['fields']]
            source_cell(book,'tip',line,field).value+=D('0.125')
            book.recalculate(verify=False)
            for (s,r,c),v in before.items():
                self.assertEqual(book.value(s,r,c)-v,0.125 if (r,c)==cell else 0)
            self.assertEqual(sum(D(str(check.value(book)))!=D(check.expected) for check in book.expected),1)
            self.assertEqual(fixed,[check.expected for check in book.expected])
            self.assertEqual(record_before,[f['after'] for f in self.plan.report['calculation']['fields']])
            self.assertEqual(record_before.count(None),6)
            with self.assertRaisesRegex(app.InputError,'tip:KG4:122'):book.recalculate()

    def test_selection_order_other_column_and_omission(self):
        for groups,nodes in [(('5:2',),[147]),(('6:3','4:1'),[172,122]),(('5:1',),[147])]:
            p=app.prepare(replace(self.request,groups=groups));s=p.workbook.sheet('杭先端ばね');n=len(nodes)
            self.assertEqual(len(s.rows),2*n+8)
            self.assertEqual([row[0].value for row in s.rows if isinstance(row[0].value,int)],nodes*2)
            self.assertEqual([x.name for x in p.workbook.sheets],['杭先端ばね'])
            if groups==('5:1',):self.assertEqual(p.workbook.value(s.name,3,1),327072)
        p=app.prepare(replace(self.request,operations=('shaft',),shaft_profile='existing-screen'))
        self.assertNotIn('杭先端ばね',[s.name for s in p.workbook.sheets])
        self.assertFalse(any(check.target.startswith('tip:') for check in p.workbook.expected))

    def test_nonconsecutive_nodes_decimal_precision_and_add_update(self):
        with tempfile.TemporaryDirectory() as folder:
            for empty in (True,False):
                sdc,ndu=Path(folder)/'入力.sdc',Path(folder)/'入力.ndu'
                sdc.write_bytes(sdc_bytes());ndu.write_bytes(ndu_bytes(empty=empty))
                p=app.prepare(app.Request(sdc,ndu,operations=('tip',),groups=('4:1',)))
                sheet=p.workbook.sheet('杭先端ばね')
                self.assertEqual(sheet.rows[3][0].value,405)
                self.assertEqual([sheet.rows[r][c].display() for r,c in ((3,1),(3,2),(9,1),(9,2))],['100.125','20.375','7.125','17.375'])
                self.assertEqual(p.report['details']['tip']['nodes'][0]['output']['status'],'追加' if empty else '変更')
                report.verify_xlsx(p.workbook.to_xlsx(),p.workbook)
            p=app.prepare(synthetic_request(folder,4))
            s=p.workbook.sheet('杭先端ばね')
            self.assertEqual(s.rows[3][1].display(),'1000.12567')
            self.assertEqual(len(s.rows),16)
            self.assertTrue(all(D(row['pile_length_m'])==D('7.25') for row in p.report['details']['tip']['nodes']))

    def test_pagination_boundary_and_long_tables_preserve_every_node_and_adoption(self):
        with tempfile.TemporaryDirectory() as folder:
            for count,pages in [(14,1),(15,2),(35,2),(36,3),(70,4)]:
                p=app.prepare(synthetic_request(folder,count));book=p.workbook;s=book.sheet('杭先端ばね')
                self.assertEqual(len(s.page_breaks)+1,pages)
                expected=[10001+10*i for i in range(count)]
                self.assertEqual([row[0].value for row in s.rows if isinstance(row[0].value,int)],expected*2)
                cuts=[0,*s.page_breaks,len(s.rows)]
                for a,b in zip(cuts,cuts[1:]):
                    self.assertEqual(s.rows[a][0].style,'tip_title')
                    self.assertIsInstance(s.rows[a+3][0].value,int)
                    self.assertLessEqual(sum(s.heights[r] for r in range(a,b)),730)
                    for (r,c),(bottom,right) in s.merged_ranges().items():
                        if a<=r<b:self.assertLess(bottom,b)
                self.assertEqual(len(book.expected),4*count)
                for check in book.expected:
                    name,r,c=check.expr.args
                    self.assertEqual(s.rows[r][0].value,int(check.target.split(':')[2]))
                    self.assertEqual(book.value(name,r,c),float(check.expected))
                book.to_xlsx()

    def test_xlsx_print_merges_blanks_and_direct_values(self):
        book=self.plan.workbook;ns={'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        with zipfile.ZipFile(BytesIO(book.to_xlsx())) as archive:
            root=sheet_xml(archive,'杭先端ばね')
            self.assertEqual(root.find('m:dimension',ns).get('ref'),'A1:C14')
            self.assertEqual(root.find('m:pageSetup',ns).get('orientation'),'portrait')
            self.assertEqual(root.find('m:pageSetup',ns).get('scale','100'),'100')
            self.assertIsNone(root.find('m:rowBreaks',ns));self.assertIsNone(root.find('m:colBreaks',ns))
            self.assertIsNone(root.find('m:sheetViews/m:sheetView/m:pane',ns))
            self.assertEqual({x.get('ref') for x in root.findall('m:mergeCells/m:mergeCell',ns)},{'A2:A3','B2:C2','A10:A11','B10:C10'})
            self.assertNotIn('_xlnm.Print_Titles',print_names(archive,'杭先端ばね'))
            self.assertFalse(root.findall('.//m:f',ns))
            self.assertFalse(root.findall('m:hyperlinks/m:hyperlink',ns))
            cells={cell.get('r'):cell for cell in root.findall('.//m:sheetData/m:row/m:c',ns)}
            for r in (4,5,6,12,13,14):
                for c in ('B','C'):self.assertIsNotNone(cells[f'{c}{r}'].find('m:v',ns))
            for address in ('A3','A7','B7','C7','A8','B8','C8','A11'):
                cell=cells.get(address)
                self.assertTrue(cell is None or (cell.find('m:v',ns) is None and cell.find('m:f',ns) is None))

    def test_gui_merge_selection_values_titles_and_force_section(self):
        root=tk.Tk();root.withdraw();root.geometry('1000x850');self.addCleanup(root.destroy)
        preview=ExcelPreview(root);preview.pack(fill='both',expand=True);preview.show(self.plan.workbook)
        root.attributes('-alpha',0);root.deiconify();root.update()
        preview.sheet_name.set('杭先端ばね');preview.select_sheet();root.update()
        def event(r,c):
            return SimpleNamespace(x=(preview.xs[c]+preview.xs[c+1])/2-preview.canvas.canvasx(0),
                                   y=(preview.ys[r]+preview.ys[r+1])/2-preview.canvas.canvasy(0))
        preview.select_cell(event(2,0));self.assertEqual(preview.cell_name.get(),'A2')
        preview.select_cell(event(1,2));self.assertEqual(preview.cell_name.get(),'B2')
        preview.select_cell(event(3,1));self.assertFalse(preview.formula.get().startswith('='))
        self.assertIn('327072',preview.formula.get())
        titles=[preview.canvas.itemcget(i,'text') for i in preview.canvas.find_all() if preview.canvas.type(i)=='text']
        self.assertIn('杭先端のばね定数',titles);self.assertIn('先端支持力',titles)
        preview.section_name.set('先端支持力');preview.go_section();root.update()
        self.assertEqual(preview.sheet.sections[1],('先端支持力',8,0))
        self.assertEqual(tuple(preview.sheet_box['values']),('杭先端ばね',))
        preview.select_cell(event(11,1))
        self.assertEqual(preview.selected,(11,1))
        self.assertEqual(preview.formula.get(),'7167.5')


if __name__=='__main__':unittest.main()
