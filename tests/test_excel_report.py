"""帳票全欄・数式・再計算・原本保護・CLI非既定値・GUIシート表示の受入検証。"""

from contextlib import redirect_stdout, redirect_stderr
from dataclasses import replace
from decimal import Decimal as D
from fractions import Fraction
from io import BytesIO, StringIO
import json
from pathlib import Path
import sys
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import portable_converter as app
import excel_report as report
from sdc_converter_app import App
from test_fill_pile_tip_suppot_info import sdc_bytes
from test_fill_suppot_info import ndu_bytes


class WorkbookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.request=app.Request(ROOT/'snap/今町橋りょう4P(右).sdc',ROOT/'snap/今町橋りょう4P(C方向･右押し→).ndu',shaft_profile='existing-screen')
        cls.plan=app.prepare(cls.request)

    def test_all_expected_targets_pieces_and_fields(self):
        record=self.plan.report['calculation']
        self.assertEqual(len(record['targets']),207)
        self.assertEqual(len(record['fields']),846)
        self.assertEqual([s.name for s in self.plan.workbook.sheets],['変換結果','水平地盤ばね','有効抵抗土圧','杭周面ばね','杭周面の支持力','杭先端ばね','入力根拠'])
        details=self.plan.report['details']
        for op,count in [('horizontal',90),('pressure',90),('shaft',72)]:
            self.assertEqual(sum(len(r['pieces']) for r in details[op].get('members',details[op].get('nodes',[]))),count)
        self.assertEqual(len(details['shaft']['zero_resistance_nodes']),12)
        self.assertEqual(len(details['shaft']['excluded']),15)
        raw=dict(line.split(b'=',1) for line in self.plan.data.splitlines() if b'=' in line)
        for f in record['fields']:
            actual=raw[f['key'].encode()].split(b',')[f['field']-1].strip().decode('ascii') or None
            self.assertEqual(f['after'],actual)
        self.assertEqual(sum(f['after'] is None for f in record['fields']),6)

    def test_independent_fraction_shaft_integration(self):
        for node in self.plan.report['details']['shaft']['nodes']:
            for field,key in [('raw_k1_kN_per_m','k1_kN_per_m2'),('raw_fy_kN','fy_kN_per_m')]:
                exact=sum(Fraction(str(p[key]))*(Fraction(str(p['bottom_m']))-Fraction(str(p['top_m']))) for p in node['pieces'])
                self.assertEqual(exact,Fraction(str(node[field])))
        node=next(n for n in self.plan.report['details']['shaft']['nodes'] if n['node']==113)
        self.assertEqual(node['field4_to_13'][1],'672.8')

    def test_saved_formula_caches_and_real_blank_cells(self):
        data=self.plan.workbook.to_xlsx()
        report.verify_xlsx(data,self.plan.workbook)
        ns={'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        with zipfile.ZipFile(BytesIO(data)) as archive:
            names=archive.namelist()
            self.assertFalse(any('externalLinks' in n or 'vbaProject' in n for n in names))
            styles=ET.fromstring(archive.read('xl/styles.xml'))
            normal=styles.find('m:fonts/m:font',ns)
            self.assertEqual(normal.find('m:name',ns).attrib['val'],'Calibri')
            self.assertEqual(normal.find('m:scheme',ns).attrib['val'],'none')
            for i in range(1,8):
                root=ET.fromstring(archive.read(f'xl/worksheets/sheet{i}.xml'))
                self.assertEqual(root.find('m:pageSetup',ns).attrib['orientation'],'portrait' if i in (2,3,4,5) else 'landscape')
                self.assertLessEqual(int(root.find('m:pageSetup',ns).attrib.get('scale','100')),100)
                if i in (2,3,4,5):
                    self.assertEqual([int(e.get('id')) for e in root.findall('m:colBreaks/m:brk',ns)],[9,18] if i==3 else [7,14])
                    self.assertIsNone(root.find('m:sheetViews/m:sheetView/m:pane',ns))
                else:
                    self.assertIsNotNone(root.find('m:rowBreaks',ns))
                    self.assertIsNotNone(root.find('m:sheetViews/m:sheetView/m:pane',ns))
                self.assertFalse(root.findall('.//m:c[@t="e"]',ns))
            workbook=ET.fromstring(archive.read('xl/workbook.xml'))
            self.assertEqual(len(workbook.findall('.//m:definedName[@name="_xlnm.Print_Area"]',ns)),7)

    def test_change_one_source_column_recalculates_only_that_pile_and_keeps_snapshot(self):
        book=report.build(self.plan.report)
        source=book.sheet('入力根拠')
        r=next(r for r,row in enumerate(source.rows) if len(row)==7 and row[0].value=='水平地盤ばね' and row[1].value==174 and row[2].value==9)
        source.rows[r][5].value+=100
        book.recalculate(verify=False)
        for target,delta in [('horizontal:KG4:98',100),('horizontal:KG6:148',0)]:
            name,rr,c,expected,_=next(x for x in book.expected if x[-1]==target)
            self.assertEqual(book.value(name,rr,c),float(expected)+delta)
            self.assertEqual(book.sheet(name).rows[rr][c+1].value,D(expected))
        with self.assertRaisesRegex(app.InputError,'KG4:98'):book.recalculate()

    def test_selected_operations_direction_and_digits(self):
        plan=app.prepare(replace(self.request,operations=('shaft','tip'),groups=('5:2',),shaft_k_decimals=3,shaft_force_decimals=4))
        self.assertEqual(len(plan.workbook.sheets),5)
        self.assertEqual(len(plan.report['calculation']['targets']),21)
        left=app.prepare(replace(self.request,operations=('pressure',),push_direction='left',pressure_cross_layer='endpoints',pressure_decimals=3))
        self.assertTrue(all(r['pressure_column']==r['column'] for r in left.report['details']['pressure']['members']))
        self.assertEqual(left.report['details']['pressure']['members'][1]['method'],'endpoints')
        for method in ('midpoint','skip'):
            plan=app.prepare(replace(self.request,operations=('horizontal',),horizontal_cross_layer=method))
            row=plan.report['details']['horizontal']['members'][1]
            self.assertEqual(row['method'],method)
            if method=='skip':self.assertEqual(row['output']['status'],'保留')

    def test_gui_sheets_formula_bar_and_invalidated_preview(self):
        root=tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        gui=App(root)
        gui.excel_preview.show(self.plan.workbook)
        root.update_idletasks()
        self.assertEqual(len(gui.excel_preview.sheet_box['values']),7)
        for sheet in self.plan.workbook.sheets:
            gui.excel_preview.sheet_name.set(sheet.name)
            gui.excel_preview.select_sheet()
            root.update_idletasks()
            self.assertTrue(gui.excel_preview.canvas.find_all())
            self.assertTrue(gui.excel_preview.section_box['values'])
        gui.groups.set('5:2')
        self.assertIsNone(gui.excel_preview.book)
        self.assertEqual(str(gui.open_excel_button['state']),'disabled')

    def test_all_cli_reports_honor_nondefault_options_and_unchanged_write(self):
        with tempfile.TemporaryDirectory(prefix='CLI Excel ') as directory:
            folder=Path(directory)
            sdc,ndu=folder/'入力.sdc',folder/'入力.ndu'
            sdc.write_bytes(self.request.sdc.read_bytes())
            ndu.write_bytes(self.plan.data)
            original=ndu.read_bytes()
            cases=[(app.base.main,'horizontal',['--write']),
                   (app.pressure.main,'pressure',['--cross-layer','endpoints','--push-direction','left','--decimals','3']),
                   (app.support.main,'shaft',['--profile','existing-screen','--k-decimals','3','--force-decimals','4']),
                   (app.tip.main,'tip',[])]
            with redirect_stdout(StringIO()),redirect_stderr(StringIO()):
                for main,name,extra in cases:
                    args=['--sdc',str(sdc),'--ndu',str(ndu),'--excel-report',str(folder/(name+'.xlsx')),*extra]
                    if name!='horizontal':args += ['--report',str(folder/(name+'.json'))]
                    self.assertEqual(main(args),0,name)
                    self.assertTrue((folder/(name+'.xlsx')).is_file())
                self.assertFalse(list(folder.glob('*.bak')))
            self.assertEqual(ndu.read_bytes(),original)
            shaft=json.loads((folder/'shaft.json').read_text(encoding='utf8'))
            self.assertEqual(shaft['configuration']['shaft_force_decimals'],4)
            self.assertEqual(shaft['mode'],'preview')
            self.assertIsNone(shaft['output'])
            pressure=json.loads((folder/'pressure.json').read_text(encoding='utf8'))
            self.assertEqual(pressure['members'][1]['method'],'endpoints')


class ExcelSaveTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='帳票 検証 ')
        self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name)
        self.sdc,self.ndu=self.folder/'入力.sdc',self.folder/'入力.ndu'
        self.sdc.write_bytes(sdc_bytes())
        self.ndu.write_bytes(ndu_bytes(empty=True))
        self.plan=app.prepare(app.Request(self.sdc,self.ndu,operations=('tip',),groups=('4:1',)))

    def test_excel_on_off_model_bytes_and_json_links(self):
        before=self.ndu.read_bytes()
        one=app.save(self.plan,self.folder/'あり.ndu')
        two=app.save(self.plan,self.folder/'なし.ndu',excel=False)
        self.assertEqual(one.output.read_bytes(),two.output.read_bytes())
        self.assertIsNone(two.excel)
        self.assertEqual(before,self.ndu.read_bytes())
        data=json.loads(one.report.read_text(encoding='utf8'))
        self.assertEqual(data['excel_sha256'],app.digest(one.excel.read_bytes()))
        self.assertEqual(data['excel_path'],str(one.excel))
        self.assertIn(data['run_id'],one.excel.name)

    def test_excel_generation_failure_never_changes_original(self):
        before=self.ndu.read_bytes()
        with patch.object(report.ReportBook,'to_xlsx',side_effect=app.InputError('formula mismatch')):
            with self.assertRaises(app.InputError):app.save(self.plan,self.ndu,overwrite=True)
        self.assertEqual(before,self.ndu.read_bytes())
        self.assertEqual(len(list(self.folder.iterdir())),2)

    def test_publication_and_locked_model_failure_clean_up_both_reports(self):
        original=app.publish_new
        for suffix in ('.xlsx','.json','.ndu'):
            with self.subTest(suffix=suffix):
                def fail(temp,dest):
                    if dest.suffix==suffix:raise PermissionError('locked')
                    original(temp,dest)
                with patch.object(app,'publish_new',side_effect=fail),self.assertRaises(PermissionError):
                    app.save(self.plan,self.folder/'出力.ndu')
                self.assertEqual(len(list(self.folder.iterdir())),2)

    def test_foreign_file_replacing_published_excel_is_not_deleted(self):
        original=app.publish_new
        def race(temp,dest):
            if dest.suffix=='.json':
                next(self.folder.glob('*.xlsx')).write_bytes(b'other process')
                raise OSError('report failure')
            original(temp,dest)
        with patch.object(app,'publish_new',side_effect=race),self.assertRaises(OSError):
            app.save(self.plan,self.folder/'出力.ndu')
        self.assertEqual(next(self.folder.glob('*.xlsx')).read_bytes(),b'other process')

    def test_tip_only_missing_other_tables_and_nonconsecutive_nodes(self):
        self.assertEqual([s.name for s in self.plan.workbook.sheets],['変換結果','杭先端ばね','入力根拠'])
        row=self.plan.report['details']['tip']['nodes'][0]
        self.assertEqual(row['node'],405)
        self.assertEqual(row['k1_kN_per_m'],D('100.125'))
        self.assertEqual(len(self.plan.report['calculation']['fields']),10)

    def test_cli_excel_only_collision_and_atomic_failure(self):
        excel=self.folder/'確認.xlsx'
        argv=['--sdc',str(self.sdc),'--ndu',str(self.ndu),'--groups','4:1','--excel-report',str(excel)]
        with redirect_stdout(StringIO()),redirect_stderr(StringIO()):
            self.assertEqual(app.tip.main(argv),0)
            self.assertEqual(app.tip.main(argv),1)
            self.assertEqual(app.tip.main([*argv[:-1],str(self.ndu)]),1)
            output=self.folder/'CLI.ndu'
            other=self.folder/'失敗.xlsx'
            old_publish=app.publish_new
            def fail(temp,dest):
                if dest==output:raise OSError('locked')
                old_publish(temp,dest)
            with patch.object(app,'publish_new',side_effect=fail):
                self.assertEqual(app.tip.main([*argv[:-1],str(other),'--output',str(output)]),1)
            self.assertFalse(other.exists())
            self.assertFalse(output.exists())


if __name__=='__main__':unittest.main()
