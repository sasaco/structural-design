"""周面2主表の全節点、独立積分、除外幅、原値追跡、可変入力とGUIを検証する。"""

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
import excel_report as report
from excel_test_helpers import sheet_xml, source_cell
from excel_preview import ExcelPreview


class ShaftReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.request = app.Request(ROOT/'test/今町橋りょう4P(右).sdc',
            ROOT/'test/今町橋りょう4P(C方向･右押し→).ndu',operations=('shaft',),shaft_profile='existing-screen')
        cls.plan = app.prepare(cls.request)

    def synthetic(self, spans, thicknesses, *, upper='0.5', embed=None, kval='10.5', fval='2.05', kd=0, fd=1):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        sdc,ndu = Path(folder.name)/'入力.sdc',Path(folder.name)/'入力.ndu'
        n,L = len(spans),sum(map(D,map(str,thicknesses)))
        embed = max(D(str(s[-1]))/2 for s in spans) if embed is None else D(str(embed))
        u,v = D(str(upper)),L-embed
        cols = [f'{i+1}列目' for i in range(n)]
        lines = ['杭長,突出長,根入れ深さ,その他',f'{L},0,{embed},0','（２）直角方向',
                 'a）杭配置条件および1/β','杭列数,奥行き本数,,1/β(m)',',奇数列,偶数列',f'{n},1,1,{u}',
                 'd）杭周面の鉛直せん断地盤ばね値','【押込み側】','層番,層厚(m),杭周面の鉛直せん断地盤ばね値(kN/m2)',
                 ',,長期,短期(使用性・安全性),短期(復旧性・地震時-第1勾配),,短期(復旧性・地震時-第2勾配)',
                 ','.join(['','']+cols*2+['⊿l(m)']+cols*2)]
        forces=[]
        top=D(0)
        for i,t in enumerate(thicknesses):
            t=D(str(t)); bottom=top+t
            active=max(D(0),min(bottom,v)-max(top,u))
            ks=[D(str(kval))+c+i for c in range(n)]
            fs=[D(str(fval))+c+i for c in range(n)]
            lines.append(','.join(map(str,[i+1,t,*([1]*n),*([2]*n),active,*ks,*(['0.1']*n)])))
            forces.append(','.join(map(str,[i+1,t,active,*fs,*([9]*n)])))
            top=bottom
        lines += ['【引抜き側】','未使用','e）杭周面の支持力','【押込み側】',
                  '層番,層厚,⊿l(m),地震時：杭周面支持力(kN/m)',',,,降伏点(ρgfy考慮),終局点(ρgfu考慮)',
                  ','.join(['','','']+cols*2),*forces,'【引抜き側】','未使用','f）杭先端の地盤ばね値']
        sdc.write_bytes(('\r\n'.join(lines)+'\r\n').encode('cp932'))
        lines=['DataName=周面検証','SuppotNum=0','SuppotRow=0','ShitenCaseNum=0','G_intCHOKU_KISO_Link_Num=0']
        groups=[]
        for c,lengths in enumerate(spans):
            group,head,first=10+c,10000+1000*c,100+1000*c
            groups.append(f'{group}:{c+1}')
            lines.append(f'KGInfo{group}=21,{first},{first+len(lengths)-1}')
            depth=D('13.041')
            lines.append(f'JointXY{head}={c*5},{depth},{head}')
            for i,span in enumerate(lengths):
                a,b,m=head+i*3,head+(i+1)*3,first+i
                depth+=D(str(span))
                lines += [f'JointXY{b}={c*5},{depth},{b}',f'ElementInfo{m}=3,0,0,0,{a},{b},75,0',f'JibanShogenInfo{m}= ,0,0,0, , , ']
        ndu.write_bytes(('\r\n'.join(lines)+'\r\n').encode('cp932'))
        return app.prepare(app.Request(sdc,ndu,operations=('shaft',),groups=tuple(groups),shaft_profile='existing-screen',
                                       shaft_k_decimals=kd,shaft_force_decimals=fd))

    def test_reference_layout_all_nodes_and_independent_integrals(self):
        book=self.plan.workbook
        self.assertEqual([s.name for s in book.sheets],['杭周面ばね','杭周面の支持力'])
        targets={row['node']:row for row in self.plan.report['details']['shaft']['nodes']}
        for name,key,unit in [('杭周面ばね','k1_kN_per_m2','kN/m²'),('杭周面の支持力','fy_kN_per_m','kN/m')]:
            sheet=book.sheet(name)
            self.assertEqual((len(sheet.rows),len(sheet.widths)),(37,21))
            self.assertEqual(sheet.column_merges,{(1,3):4,(1,10):11,(1,17):18})
            self.assertFalse(sheet.range_merges)
            self.assertFalse(sheet.merges)
            self.assertEqual(sheet.freeze,(0,0))
            self.assertFalse(sheet.page_breaks)
            found=[]
            for c in (0,7,14):
                self.assertIn(unit,sheet.rows[1][c+2].value)
                self.assertEqual(sheet.rows[35][c].value,'Σ')
                self.assertEqual(book.value(name,35,c+1),31)
                self.assertEqual(book.value(name,35,c+3),31)
                self.assertEqual(book.value(name,6,c+4),0.45)  # 見本の0.460を幾何から訂正。
                self.assertEqual(book.value(name,5,c+4)+book.value(name,6,c+4),1.3)
                starts=[r for r in range(2,35) if sheet.rows[r][c+5].value is not None]
                self.assertEqual(len(starts),25)
                for ix,r in enumerate(starts):
                    node=sheet.rows[r][c+5].value
                    found.append(node)
                    target=targets.get(node)
                    exact=sum((F(str(p[key]))*F(str(p['length_m'])) for p in target['pieces']),F(0)) if target else F(0)
                    self.assertAlmostEqual(book.value(name,r,c+6),float(exact),places=8)
                    self.assertNotIn('ROUND',sheet.rows[r][c+6].formula.text(name))
                    end=starts[ix+1] if ix+1<len(starts) else 35
                    parts=[book.value(name,j,c+4) for j in range(r,end) if sheet.rows[j][c+4].value is not None or sheet.rows[j][c+4].formula]
                    if parts:self.assertAlmostEqual(sum(parts),book.value(name,r,c+3),places=10)
            self.assertEqual(len(set(found)),75)
        self.assertEqual(book.sheet('杭周面ばね').rows[8][6].formula.text('杭周面ばね'),'SUM((C7*E9),(C10*E10),(C11*E11))')
        self.assertEqual(book.sheet('杭周面の支持力').rows[11][6].display(),'1835.0')
        self.assertEqual(len(book.expected),120)
        self.assertEqual(len(book.geometry_checks),150)
        for check in (*book.expected,*book.geometry_checks):self.assertEqual(D(str(check.value(book))),D(check.expected))

    def test_complete_layers_and_source_provenance(self):
        record=self.plan.report['calculation']
        values=[v for v in record['source_values'] if v['operation']=='shaft']
        self.assertEqual(len(values),42)
        self.assertEqual({v['line'] for v in values},set(range(212,219))|set(range(244,251)))
        self.assertEqual(len({(v['line'],v['field']) for v in values}),42)
        layers=self.plan.report['details']['shaft']['layers']
        self.assertEqual(len(layers),7)
        self.assertEqual(D(str(layers[-1]['bottom_m']))-D(str(layers[-1]['top_m'])),D('12.6'))
        self.assertEqual(D(str(layers[-1]['spring_thickness_m'])),D('11.3'))
        self.assertEqual(len(record['targets']),60)
        self.assertEqual(len(record['fields']),600)

    def test_sources_are_independent_and_actual_values_are_fixed(self):
        for line,field,name,other,delta in [(214,10,'杭周面ばね','杭周面の支持力',75.5),(246,4,'杭周面の支持力','杭周面ばね',75.5)]:
            book=report.build(self.plan.report)
            before={(s,r,c):book.value(s,r,c) for s in (name,other) for r in (8,12,32) for c in (6,13,20)}
            fixed=list(book.expected)
            source_cell(book,'shaft',line,field).value+=100
            book.recalculate(verify=False)
            self.assertAlmostEqual(book.value(name,8,6)-before[name,8,6],delta,places=8)
            for s,r,c in before:
                if (s,r,c)==(name,8,6):continue
                self.assertEqual(book.value(s,r,c),before[s,r,c])
            self.assertEqual(book.expected,fixed)
            with self.assertRaisesRegex(app.InputError,'shaft:KG4:102'):book.recalculate()

    def test_selection_order_and_shaft_omission(self):
        for groups,expected in [(('5:2',),[123]),(('6:3','4:1'),[98,148])]:
            plan=app.prepare(replace(self.request,groups=groups))
            for name in ('杭周面ばね','杭周面の支持力'):
                sheet=plan.workbook.sheet(name)
                self.assertEqual([sheet.rows[2][c+5].value for c in range(0,len(sheet.widths),7)],expected)
            plan.workbook.to_xlsx()
        plan=app.prepare(replace(self.request,operations=('tip',)))
        self.assertFalse(any(s.layout=='shaft' for s in plan.workbook.sheets))

    def test_four_piles_different_nonconsecutive_nodes_and_layer_crossings(self):
        plan=self.synthetic([[1,1,1,1],[2,2],['0.4','1.6',2],['0.8','0.2',1,2]],['0.25']*16,embed=1)
        self.assertEqual(len(plan.workbook.sheet('杭周面ばね').widths),28)
        self.assertEqual(plan.workbook.sheet('杭周面ばね').vertical_breaks,[7,14,21])
        for name in ('杭周面ばね','杭周面の支持力'):
            sheet=plan.workbook.sheet(name)
            for c in range(0,28,7):
                total=next(r for r,row in enumerate(sheet.rows) if row[c].value=='Σ')
                self.assertEqual(plan.workbook.value(name,total,c+1),4)
                self.assertEqual(plan.workbook.value(name,total,c+3),4)
        self.assertTrue(all(D(str(check.value(plan.workbook)))==D(check.expected) for check in plan.workbook.geometry_checks))
        for row in plan.report['details']['shaft']['nodes']:
            for kind,key in [('k','k1_kN_per_m2'),('f','fy_kN_per_m')]:
                exact=sum((F(str(p[key]))*F(str(p['length_m'])) for p in row['pieces']),F(0))
                raw=D(exact.numerator)/D(exact.denominator)
                expected=raw.quantize(D(1) if kind=='k' else D('0.1'),rounding=ROUND_HALF_UP)
                self.assertEqual(expected,D(row['field4_to_13'][0 if kind=='k' else 1]))
        plan.workbook.to_xlsx()

    def test_rounding_digits_and_decimal_source_display(self):
        for digits in range(7):
            plan=self.synthetic([[1,1,1]],[3],kval='10.5',fval='2.05',kd=digits,fd=digits)
            for name,value in [('杭周面ばね','10.5'),('杭周面の支持力','2.05')]:
                sheet=plan.workbook.sheet(name)
                r=next(r for r,row in enumerate(sheet.rows) if row[5].value==10003)
                self.assertEqual(D(str(plan.workbook.value(name,r,6))),D(value))
                self.assertEqual(sheet.rows[r][6].display(),format(D(value).quantize(D(1).scaleb(-digits),rounding=ROUND_HALF_UP),f'.{digits}f'))
                raw=next(cell for row in sheet.rows for cell in row[2:3] if cell.value==D(value))
                self.assertIn(value,raw.display())
            plan.workbook.to_xlsx()

    def test_long_pile_and_long_single_node_keep_totals_and_label_continuation(self):
        for spans,thicknesses,embed in [([[1]*70],[1]*70,'0.5'),([[140]],[1]*140,70)]:
            plan=self.synthetic(spans,thicknesses,upper=0,embed=embed)
            for name in ('杭周面ばね','杭周面の支持力'):
                sheet=plan.workbook.sheet(name)
                self.assertTrue(sheet.page_breaks)
                total=next(r for r,row in enumerate(sheet.rows) if row[0].value=='Σ')
                self.assertEqual(plan.workbook.value(name,total,1),sum(thicknesses))
                self.assertEqual(plan.workbook.value(name,total,3),sum(thicknesses))
                if len(spans[0])==1:
                    self.assertTrue(any('続き' in str(sheet.rows[r][5].value) for r in sheet.page_breaks))
                    for r in sheet.page_breaks:
                        if isinstance(sheet.rows[r][5].value,int):
                            self.assertFalse(any(row[5].value==sheet.rows[r][5].value for row in sheet.rows[2:r]))
                        elif sheet.rows[r][5].value is not None:
                            self.assertIn('続き',str(sheet.rows[r][5].value))
            plan.workbook.to_xlsx()

    def test_xlsx_layout_print_settings_and_local_formulas(self):
        ns={'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        with zipfile.ZipFile(BytesIO(self.plan.workbook.to_xlsx())) as archive:
            for name in ('杭周面ばね','杭周面の支持力'):
                root=sheet_xml(archive,name)
                self.assertEqual(root.find('m:dimension',ns).get('ref'),'A1:U37')
                self.assertEqual(root.find('m:pageSetup',ns).get('orientation'),'portrait')
                self.assertEqual(root.find('m:pageSetup',ns).get('scale','100'),'92')
                self.assertEqual([int(e.get('id')) for e in root.findall('m:colBreaks/m:brk',ns)],[7,14])
                self.assertEqual({e.get('ref') for e in root.findall('m:mergeCells/m:mergeCell',ns)},{'D2:E2','K2:L2','R2:S2'})
                self.assertFalse(root.findall('m:hyperlinks/m:hyperlink',ns))
                self.assertTrue(all('!' not in f.text for f in root.findall('.//m:f',ns)))

    def test_gui_both_sheets_merge_selection_and_force_navigation(self):
        root=tk.Tk();root.withdraw();root.geometry('1200x800');self.addCleanup(root.destroy)
        preview=ExcelPreview(root);preview.pack(fill='both',expand=True);preview.show(self.plan.workbook)
        root.attributes('-alpha',0);root.deiconify();root.update()
        for name in ('杭周面ばね','杭周面の支持力'):
            preview.sheet_name.set(name);preview.select_sheet()
            preview.section_name.set('KG6 / SDC3列目');preview.go_section();root.update()
            self.assertGreater(preview.canvas.canvasx(0),0)
            def select(r,c):
                preview.select_cell(SimpleNamespace(x=(preview.xs[c]+preview.xs[c+1])/2-preview.canvas.canvasx(0),
                                                    y=(preview.ys[r]+preview.ys[r+1])/2-preview.canvas.canvasy(0)))
            select(1,18);self.assertEqual(preview.cell_name.get(),'R2')
            select(8,20);self.assertEqual(preview.cell_name.get(),'U9');self.assertIn('Q7',preview.formula.get())
        self.assertEqual(tuple(preview.sheet_box['values']),('杭周面ばね','杭周面の支持力'))
        self.assertEqual(preview.sheet.name,'杭周面の支持力')


if __name__=='__main__':unittest.main()
