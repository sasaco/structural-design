"""Excel帳票とGUI表示の共通セルモデル。数式を評価し、検証済みキャッシュを出力する。"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal as D, ROUND_HALF_UP
from io import BytesIO
import math
import posixpath
import zipfile
from xml.etree import ElementTree as ET

import fill_jiban_shogen as base
import sdc_columns

SHEET_NAMES = {"horizontal": "水平地盤ばね", "pressure": "有効抵抗土圧", "shaft": "杭周面ばね", "tip": "杭先端ばね"}
SHAFT_FORCE_SHEET = "杭周面の支持力"
METHODS = {"single-layer": "単一層転記", "length-weighted": "長さ加重平均", "midpoint": "中央の層",
           "skip": "境界跨ぎのため保留", "linear-endpoints": "層内線形補間", "integral-average": "積分平均", "endpoints": "上下端採用"}
NUM = '#,##0.000'
LENGTH = '0.000######'


def address(row, col):
    name = ""
    col += 1
    while col:
        col, rem = divmod(col-1, 26)
        name = chr(65+rem) + name
    return name + str(row+1)


@dataclass(frozen=True)
class Expr:
    """生成する数式の限定AST。式文字列と評価値は必ずこの木から得る。evalは使わない。"""
    op: str
    args: tuple

    def __add__(self, other): return Expr("+", (self, expression(other)))
    def __sub__(self, other): return Expr("-", (self, expression(other)))
    def __mul__(self, other): return Expr("*", (self, expression(other)))
    def __truediv__(self, other): return Expr("/", (self, expression(other)))

    def text(self, sheet):
        if self.op == "ref":
            name, row, col = self.args
            prefix = "" if name == sheet else "'" + name.replace("'", "''") + "'!"
            return prefix + address(row, col)
        if self.op == "literal": return str(self.args[0])
        if self.op in ("+", "-", "*", "/"):
            return f"({self.args[0].text(sheet)}{self.op}{self.args[1].text(sheet)})"
        return self.op + "(" + ",".join(a.text(sheet) for a in self.args) + ")"

    def evaluate(self, book, cache, active):
        if self.op == "ref": return book.value(*self.args, cache=cache, active=active)
        if self.op == "literal": return float(self.args[0])
        values = [arg.evaluate(book, cache, active) for arg in self.args]
        if self.op == "+": return values[0]+values[1]
        if self.op == "-": return values[0]-values[1]
        if self.op == "*": return values[0]*values[1]
        if self.op == "/": return values[0]/values[1]
        if self.op == "SUM": return sum(values)
        if self.op == "MAX": return max(values)
        if self.op == "MIN": return min(values)
        if self.op == "ROUND":
            # Excel互換の正負対称な四捨五入。微小値を足す補正は行わない。
            scale = 10 ** int(values[1])
            return math.copysign(math.floor(abs(values[0])*scale+0.5)/scale, values[0])
        raise base.InputError(f"帳票の未対応数式: {self.op}")


def expression(value):
    return value if isinstance(value, Expr) else Expr("literal", (value,))


def fn(name, *args):
    return Expr(name, tuple(map(expression, args)))


@dataclass(frozen=True)
class Check:
    """主表から求める採用値・幾何と、変換時の独立した記録との照合。セルには出力しない。"""
    expr: Expr
    expected: str
    target: str

    def value(self, book, cache=None):
        return self.expr.evaluate(book, {} if cache is None else cache, set())


@dataclass
class Cell:
    value: object = None
    formula: Expr | None = None
    style: str = "body"
    number_format: str = NUM
    link: tuple[str, int, int] | None = None
    cached: object = None
    align: str | None = None
    borders: str | None = None  # L/R/T/B。Noneは既存帳票の書式、空文字は罫線なし。
    valign: str | None = None

    def display(self):
        value = self.cached if self.formula else self.value
        if value is None: return ""
        if isinstance(value, (float, int, D)):
            decimals = len(self.number_format.split(".", 1)[1]) if "." in self.number_format else 0
            minimum = self.number_format.split(".", 1)[1].count("0") if "." in self.number_format else 0
            rounded = D(str(value)).quantize(D(1).scaleb(-decimals), rounding=ROUND_HALF_UP)
            text = format(rounded, f"{',' if ',' in self.number_format else ''}.{decimals}f")
            if decimals > minimum:
                integer, fraction = text.split(".")
                fraction = fraction.rstrip("0").ljust(minimum, "0")
                text = integer + ("."+fraction if fraction else "")
            return text
        return str(value)


@dataclass
class Sheet:
    name: str
    rows: list[list[Cell]] = field(default_factory=list)
    sections: list[tuple[str, int, int]] = field(default_factory=list)
    merges: dict[int, int] = field(default_factory=dict)
    column_merges: dict[tuple[int, int], int] = field(default_factory=dict)
    heights: dict[int, float] = field(default_factory=dict)
    widths: list[float] = field(default_factory=lambda: [11]*12)
    page_breaks: list[int] = field(default_factory=list)
    vertical_breaks: list[int] = field(default_factory=list)
    freeze: tuple[int, int] = (4, 3)
    layout: str = "detail"
    print_scale: int | None = None
    range_merges: dict[tuple[int, int], tuple[int, int]] = field(default_factory=dict)
    block_width: int = 0
    print_last_row: int | None = None

    def merged_ranges(self):
        return {**{(r,0):(r,c) for r,c in self.merges.items()},
                **{(r,c):(r,end) for (r,c),end in self.column_merges.items()},
                **self.range_merges}

    def validate_merges(self):
        occupied = set()
        ranges = list(self.merges) + list(self.column_merges) + list(self.range_merges)
        merged = self.merged_ranges()
        if len(ranges) != len(merged):
            raise base.InputError(f"{self.name}: 結合セルの開始位置が重複しています。")
        for (r,c),(bottom,right) in merged.items():
            if not (0<=r<=bottom<len(self.rows) and 0<=c<=right<len(self.widths)) or (r,c)==(bottom,right):
                raise base.InputError(f"{self.name}: 結合セル範囲が不正です。")
            for rr in range(r,bottom+1):
                for cc in range(c,right+1):
                    if (rr,cc) in occupied:
                        raise base.InputError(f"{self.name}: 結合セル範囲が重複しています。")
                    occupied.add((rr,cc))
                    if (rr,cc)!=(r,c) and cc<len(self.rows[rr]):
                        cell = self.rows[rr][cc]
                        if cell.value is not None or cell.formula is not None or cell.link is not None:
                            raise base.InputError(f"{self.name}!{address(rr,cc)}: 結合に覆われるセルに値があります。")

    def ref(self, row, col): return Expr("ref", (self.name, row, col))

    def add(self, values, styles=None, formats=None):
        cells = []
        for i, value in enumerate(values):
            cell = value if isinstance(value, Cell) else Cell(formula=value) if isinstance(value, Expr) else Cell(value=value)
            if styles and i in styles: cell.style = styles[i]
            if formats and i in formats: cell.number_format = formats[i]
            cells.append(cell)
        self.rows.append(cells)
        return len(self.rows)-1

    def note(self, text, style="note"):
        row = self.add([Cell(text, style=style)])
        self.merges[row] = len(self.widths)-1
        self.heights[row] = max(24, 16 * (1+len(text)//85))
        return row

    def section(self, title, headers):
        if self.rows: self.add([])
        index = self.note(title, "section")
        self.sections.append((title, index, 0))
        self.add([Cell(h, style="header") for h in headers])
        self.heights[len(self.rows)-1] = 36
        return len(self.rows)

    def set_formula(self, row, col, expr):
        self.rows[row][col].formula = expr


@dataclass
class ReportBook:
    sheets: list[Sheet]
    expected: list[Check] = field(default_factory=list)
    geometry_checks: list[Check] = field(default_factory=list)
    sources: dict[tuple[str, int, int], tuple[str, int, int]] = field(default_factory=dict)
    metadata: dict[str, str] = field(default_factory=dict)

    def sheet(self, name):
        sheet = next((s for s in self.sheets if s.name == name), None)
        if sheet is None: raise base.InputError(f"帳票の参照先シートがありません: {name}")
        return sheet

    def validate_references(self):
        if not self.sheets or len({s.name for s in self.sheets}) != len(self.sheets):
            raise base.InputError("帳票のシート構成が不正です。")

        def cell_at(name, r, c):
            sheet = self.sheet(name)
            if not (0 <= r < len(sheet.rows) and 0 <= c < len(sheet.rows[r])):
                raise base.InputError(f"帳票の参照先セルがありません: {name}!{address(r,c)}")
            return sheet.rows[r][c]

        def check_expr(expr, owner=None):
            if expr.op == "ref":
                name, r, c = expr.args
                if owner is not None and name != owner:
                    raise base.InputError(f"{owner}: 主表外への数式参照があります: {name}!{address(r,c)}")
                cell_at(name, r, c)
            elif expr.op != "literal":
                for arg in expr.args: check_expr(arg, owner)

        for sheet in self.sheets:
            sheet.validate_merges()
            for row in sheet.rows:
                for cell in row:
                    if cell.formula: check_expr(cell.formula, sheet.name)
                    if cell.link: cell_at(*cell.link)
        for check in (*self.expected, *self.geometry_checks): check_expr(check.expr)
        for ref in self.sources.values(): cell_at(*ref)

    def value(self, name, row, col, *, cache=None, active=None):
        cache = {} if cache is None else cache
        active = set() if active is None else active
        key = (name, row, col)
        if key in cache: return cache[key]
        if key in active: raise base.InputError(f"帳票の循環参照: {name}!{address(row,col)}")
        active.add(key)
        try:
            cell = self.sheet(name).rows[row][col]
            result = cell.formula.evaluate(self, cache, active) if cell.formula else float(cell.value)
            if not math.isfinite(result): raise ValueError("有限値ではありません")
            cache[key] = result
            return result
        except (ValueError, TypeError, IndexError, ZeroDivisionError) as exc:
            raise base.InputError(f"帳票数式の評価に失敗: {name}!{address(row,col)}: {exc}") from exc
        finally:
            active.remove(key)

    def recalculate(self, verify=True):
        self.validate_references()
        cache = {}
        for sheet in self.sheets:
            for r, row in enumerate(sheet.rows):
                for c, cell in enumerate(row):
                    if cell.formula: cell.cached = self.value(sheet.name, r, c, cache=cache)
        if verify:
            for check in (*self.expected, *self.geometry_checks):
                actual = D(str(check.value(self, cache)))
                if not actual.is_finite() or actual != D(check.expected):
                    raise base.InputError(f"{check.target}: Excel再計算値 {actual} と固定記録 {check.expected} が不一致。式: ={check.expr.text('')}")

    def to_xlsx(self):
        import xlsxwriter
        self.recalculate()
        output = BytesIO()
        workbook = xlsxwriter.Workbook(output, {
            "in_memory": True, "strings_to_formulas": False, "strings_to_urls": False,
            # 列幅の基準フォント。日本語ExcelのテーマによるMS Pゴシックへの
            # 置換を止める。表示セルの游ゴシックは個別に指定する。
            "default_format_properties": {"font_name": "Calibri", "font_size": 11, "font_scheme": "none"},
        })
        for name, value in self.metadata.items(): workbook.set_custom_property(name, value)
        formats = {}
        def fmt(cell):
            key = (cell.style, cell.number_format, cell.align, cell.borders, cell.valign)
            if key not in formats:
                props = dict(font_name="Yu Gothic", font_size=10, font_color="#202B3C", valign="vcenter", num_format=cell.number_format,text_wrap=True)
                if cell.style == "source": props.update(font_color="#175CAD")
                if cell.style == "actual": props.update(bg_color="#FFF2C6")
                if cell.style == "header": props.update(bg_color="#243B53", font_color="white", bold=True, text_wrap=True, align="center")
                if cell.style == "title": props.update(font_size=16, bold=True)
                if cell.style == "section": props.update(bold=True, bg_color="#E6EDF5")
                if cell.style == "note": props.update(text_wrap=True, font_color="#526174")
                if cell.style == "link": props.update(font_color="#175CAD", underline=True)
                if cell.style.startswith(("spring", "pressure", "shaft", "tip")):
                    props.update(font_name="ＭＳ 明朝", font_size=10, align="center")
                if cell.style in ("spring_title", "pressure_title", "shaft_title"): props["text_wrap"] = False
                if cell.style.startswith("tip"): props["text_wrap"] = cell.style == "tip_header"
                if cell.align: props["align"] = cell.align
                if cell.valign: props["valign"] = {"center":"vcenter"}.get(cell.valign,cell.valign)
                if cell.borders is not None:
                    props.update({edge:1 for code,edge in (("L","left"),("R","right"),("T","top"),("B","bottom")) if code in cell.borders})
                formats[key] = workbook.add_format(props)
            return formats[key]
        for sheet in self.sheets:
            sheet.validate_merges()
            ws = workbook.add_worksheet(sheet.name)
            ws.hide_gridlines(2)
            if any(sheet.freeze): ws.freeze_panes(*sheet.freeze)
            ws.set_default_row(23)
            for c, width in enumerate(sheet.widths): ws.set_column(c,c,width)
            covered_ranges = {(r,c) for (a,b),(d,e) in sheet.range_merges.items()
                              for r in range(a,d+1) for c in range(b,e+1) if (r,c)!=(a,b)}
            for r, row in enumerate(sheet.rows):
                if r in sheet.heights: ws.set_row(r,sheet.heights[r])
                if r in sheet.merges:
                    ws.merge_range(r,0,r,sheet.merges[r], str(row[0].value), fmt(row[0]))
                    continue
                covered = {c for (rr,start),end in sheet.column_merges.items() if rr==r for c in range(start+1,end+1)}
                for c, cell in enumerate(row):
                    if c in covered or (r,c) in covered_ranges: continue
                    style = fmt(cell)
                    if (r,c) in sheet.range_merges:
                        bottom,right = sheet.range_merges[r,c]
                        ws.merge_range(r,c,bottom,right,"",style)
                    if (r,c) in sheet.column_merges:
                        ws.merge_range(r,c,r,sheet.column_merges[r,c],"",style)
                    if cell.formula:
                        ws.write_formula(r,c,"="+cell.formula.text(sheet.name),style,cell.cached)
                    elif cell.link:
                        name, rr, cc = cell.link
                        ws.write_url(r,c,"internal:'"+name.replace("'","''")+"'!"+address(rr,cc),style,str(cell.value))
                    elif isinstance(cell.value,(int,float,D)):
                        ws.write_number(r,c,float(cell.value),style)
                    elif cell.value is None:
                        ws.write_blank(r,c,None,style)
                    else:
                        if len(str(cell.value)) > 32767: raise base.InputError("Excelセルの文字数制限を超えています。")
                        ws.write_string(r,c,str(cell.value),style)
            if sheet.layout in ("horizontal", "pressure", "shaft", "tip"): ws.set_portrait()
            else: ws.set_landscape()
            ws.set_paper(9)
            # FitToPagesはExcelで手動改ページを無効にするため、A4横の有効幅へ
            # 収まる倍率を計算する。高さは明細数に応じて複数ページにする。
            # Excelの印刷では画面の7px/文字より幅が増す場合があるため、
            # フォント・DPIによる丸めを含め8px/文字で余裕を確保する。
            width_pixels=sum(w*8+5 for w in sheet.widths)
            ws.set_print_scale(sheet.print_scale or min(100,int(800*96/72/width_pixels*100)))
            if sheet.layout == "horizontal":
                ws.set_margins(0.7,0.7,0.75,0.75)
                ws.repeat_rows(0,1)
                ws.set_zoom(85)
                ws.print_across()
            elif sheet.layout in ("pressure", "shaft"):
                ws.set_margins(18/25.4,18/25.4,19/25.4,19/25.4)
                ws.repeat_rows(0,1)
                ws.set_zoom(85)
            elif sheet.layout == "tip":
                ws.set_margins(0.7,0.7,0.75,0.75)
                # K表とF表で見出しが異なるため、必要な見出しは行配置で再掲する。
            else:
                ws.set_margins(0.25,0.25,0.35,0.35)
                ws.repeat_rows(0,3)
            ws.set_h_pagebreaks(sheet.page_breaks)
            ws.set_v_pagebreaks(sheet.vertical_breaks)
            ws.print_area(0,0,sheet.print_last_row if sheet.print_last_row is not None else len(sheet.rows)-1,len(sheet.widths)-1)
            ws.set_footer('&L'+sheet.name+'&R&P / &N')
        workbook.close()
        data = output.getvalue()
        verify_xlsx(data, self)
        return data


def verify_xlsx(data, book):
    """公開前に実ファイルの全数式キャッシュ・固定数値・空欄を読戻す。"""
    ns = {"m":"http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(BytesIO(data)) as archive:
        if archive.testzip() is not None: raise base.InputError("Excelファイルが破損しています。")
        if any("externalLinks" in n or "vbaProject" in n for n in archive.namelist()):
            raise base.InputError("Excelに外部参照が含まれています。")
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        saved_sheets = workbook.findall("m:sheets/m:sheet",ns)
        if [s.get("name") for s in saved_sheets] != [s.name for s in book.sheets] or any(s.get("state","visible")!="visible" for s in saved_sheets):
            raise base.InputError("Excelシート構成の保存照合に失敗。")
        view = workbook.find("m:bookViews/m:workbookView",ns)
        if view is not None and view.get("activeTab","0")!="0":
            raise base.InputError("Excelの初期表示シートが不一致。")
        rels = {e.get("Id"):e.get("Target") for e in ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))}
        names = {(int(e.get("localSheetId","-1")),e.get("name")):e.text for e in workbook.findall("m:definedNames/m:definedName",ns)}
        for i,(sheet,saved_sheet) in enumerate(zip(book.sheets,saved_sheets)):
            rid = saved_sheet.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
            target = rels[rid]
            path = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("xl",target))
            root = ET.fromstring(archive.read(path))
            last = sheet.print_last_row if sheet.print_last_row is not None else len(sheet.rows)-1
            end = address(last,len(sheet.widths)-1)
            letters = end.rstrip("0123456789")
            print_area = "'"+sheet.name.replace("'","''")+"'!$A$1:$"+letters+"$"+str(last+1)
            unquoted_area = sheet.name+"!$A$1:$"+letters+"$"+str(last+1)
            if names.get((i,"_xlnm.Print_Area")) not in (print_area,unquoted_area):
                raise base.InputError(f"{sheet.name}: Excel印刷範囲の保存照合に失敗。")
            setup = root.find("m:pageSetup",ns)
            expected_orientation = "portrait" if sheet.layout in ("horizontal","pressure","shaft","tip") else "landscape"
            if setup is None or setup.get("orientation")!=expected_orientation or (sheet.print_scale and int(setup.get("scale","100"))!=sheet.print_scale):
                raise base.InputError(f"{sheet.name}: Excel印刷設定の保存照合に失敗。")
            for tag,expected in (("rowBreaks",sheet.page_breaks),("colBreaks",sheet.vertical_breaks)):
                if [int(e.get("id")) for e in root.findall(f"m:{tag}/m:brk",ns)] != expected:
                    raise base.InputError(f"{sheet.name}: Excel改ページの保存照合に失敗。")
            actual_links = {e.get("ref"):e.get("location") for e in root.findall("m:hyperlinks/m:hyperlink",ns)}
            expected_links = {address(r,c):"'"+cell.link[0].replace("'","''")+"'!"+address(*cell.link[1:])
                              for r,row in enumerate(sheet.rows) for c,cell in enumerate(row) if cell.link}
            if actual_links != expected_links:
                raise base.InputError(f"{sheet.name}: Excel内部リンクの保存照合に失敗。")
            actual_merges = {e.get("ref") for e in root.findall("m:mergeCells/m:mergeCell",ns)}
            expected_merges = {f"{address(r,c)}:{address(b,e)}" for (r,c),(b,e) in sheet.merged_ranges().items()}
            if actual_merges != expected_merges:
                raise base.InputError(f"{sheet.name}: Excel結合範囲の保存照合に失敗。")
            cells = {c.get("r"):c for c in root.findall(".//m:sheetData/m:row/m:c",ns)}
            for r,row in enumerate(sheet.rows):
                for c,cell in enumerate(row):
                    saved = cells.get(address(r,c))
                    value = saved.find("m:v",ns) if saved is not None else None
                    if cell.formula:
                        formula = saved.find("m:f",ns) if saved is not None else None
                        if formula is None or formula.text != cell.formula.text(sheet.name) or value is None or not math.isclose(float(value.text),cell.cached,rel_tol=1e-14,abs_tol=1e-14):
                            raise base.InputError(f"Excel数式の保存照合に失敗: {sheet.name}!{address(r,c)}")
                    elif isinstance(cell.value,(int,float,D)):
                        if saved is None or saved.find("m:f",ns) is not None or value is None or not math.isclose(float(value.text),float(cell.value),rel_tol=1e-14,abs_tol=0):
                            raise base.InputError(f"Excel数値の保存照合に失敗: {sheet.name}!{address(r,c)}")
                    elif cell.value is None and value is not None:
                        raise base.InputError(f"Excel空欄の保存照合に失敗: {sheet.name}!{address(r,c)}")


def numeric(value): return D(str(value)) if value is not None and str(value).strip() else None


def build_horizontal(book, sheet, rows, fixed, src, direction_label):
    """部材×地層の7列表。原値・長さを主表に置き、採用値は内部で照合する。"""
    groups = sorted({(row["column"], row["group"]) for row in rows})
    blocks = [sorted((row for row in rows if row["group"]==group),
                     key=lambda row:numeric(row["geometry"]["top_m"])) for _,group in groups]
    sheet.layout, sheet.freeze, sheet.print_scale = "horizontal", (0,0), 90
    sheet.block_width = 7
    # XlsxWriterは保存時に5px相当を加える。Normal=Calibri 11で見本の幅に合わせる。
    sheet.widths = [8,8,16.16,8.91,8.91,8.91,20]*len(blocks)
    sheet.vertical_breaks = list(range(7,len(sheet.widths),7))
    count = max(sum(len(row["pieces"]) for row in block) for block in blocks)
    total = count+2
    sheet.rows = [[Cell(style="spring", number_format="0", borders="") for _ in sheet.widths]
                  for _ in range(total+2)]
    sheet.heights = {r:21.75 for r in range(total+1)}
    sheet.heights.update({0:24, 1:48.75, total+1:23.1})

    def put(r, c, value=None, *, fmt="#,##0", align="center", borders="LRTB"):
        cell = Cell(formula=value if isinstance(value,Expr) else None,
                    value=None if isinstance(value,Expr) else value, style="spring",
                    number_format=fmt, align=align, borders=borders)
        sheet.rows[r][c] = cell
        return cell

    member_spans = []
    headers = ["層番号","層厚\n(m)","水平地盤反力係数\nKh\n(kN/m²)",
               "部材長\n(m)",None,"部材番号","各部材の水平ばね定数\nKh\n(kN/m²)"]
    for i,block in enumerate(blocks):
        c = i*7
        sheet.sections.append((f"KG{block[0]['group']} / SDC{block[0]['column']}列目",0,c))
        special = {row["method"] for row in block} & {"midpoint","skip"}
        title = "水平地盤ばね"
        if special:
            title += "（"+"・".join(METHODS[m] for m in sorted(special))+"）"
        put(0,c,title,align="left",borders="").style = "spring_title"
        put(0,c+1,direction_label,align="left",borders="").style = "spring_title"
        for j,h in enumerate(headers): put(1,c+j,h)
        sheet.column_merges[1,c+3] = c+4
        r, previous_layer = 2, None
        layer_rows, first_rows = [], []
        for row in block:
            ident = row["id"]
            first = r
            pieces = sorted(row["pieces"],key=lambda p:numeric(p["top_m"]))
            first_rows.append(first)
            for j,p in enumerate(pieces):
                layer_start = previous_layer != p["layer"]
                put(r,c,p["layer"] if layer_start else None,borders="LR"+("T" if layer_start else ""))
                put(r,c+1,numeric(p["layer_bottom_m"])-numeric(p["layer_top_m"]) if layer_start else None,fmt="#,##0.000",align="right",borders="LR"+("T" if layer_start else ""))
                if layer_start: layer_rows.append(r)
                put(r,c+2,src("horizontal",p["sdc_line"],p["value_field"],sheet,r,c+2))
                edges = ("T" if j==0 else "")+("B" if j==len(pieces)-1 else "")
                put(r,c+3,numeric(row["geometry"]["bottom_m"])-numeric(row["geometry"]["top_m"]) if j==0 else None,fmt="#,##0.000",align="right",borders="L"+edges)
                overlap = numeric(p["bottom_m"])-numeric(p["top_m"])
                put(r,c+4,overlap if len(pieces)>1 else None,fmt="#,##0.000",align="right",borders="R"+edges)
                put(r,c+5,row["member"] if j==0 else None,fmt="0",borders="LR"+edges)
                put(r,c+6,borders="LR"+edges)
                previous_layer = p["layer"]
                r += 1
            member_spans.append((first,r,c,row["member"]))
            method = row["method"]
            if method == "skip":
                # 保留された現在値は空欄もそのまま残す。加重平均の値に見せない。
                sheet.rows[first][c+6].value = numeric(fixed[ident,2]["after"])
                continue
            if method == "midpoint":
                midpoint = (numeric(row["geometry"]["top_m"])+numeric(row["geometry"]["bottom_m"]))/2
                chosen = next(j for j,p in enumerate(pieces) if numeric(p["layer_top_m"])<=midpoint<numeric(p["layer_bottom_m"]))
                raw = sheet.ref(first+chosen,c+2)
            elif len(pieces)==1:
                raw = sheet.ref(first,c+2)
            else:
                raw = fn("SUM",*[sheet.ref(rr,c+2)*sheet.ref(rr,c+4) for rr in range(first,r)])/sheet.ref(first,c+3)
            sheet.set_formula(first,c+6,raw)
            adopted = fn("ROUND",sheet.ref(first,c+6),0) if method=="length-weighted" else sheet.ref(first,c+6)
            book.expected.append(Check(adopted,fixed[ident,2]["after"],ident))
        for j in (0,1): sheet.rows[r-1][c+j].borders += "B"
        for j in range(7): put(total,c+j)
        put(total,c,"Σ")
        put(total,c+1,fn("SUM",*[sheet.ref(rr,c+1) for rr in layer_rows]),fmt="#,##0.000",align="right")
        put(total,c+3,fn("SUM",*[sheet.ref(rr,c+3) for rr in first_rows]),fmt="#,##0.000",align="right",borders="LTB")
        put(total,c+4,borders="RTB")

    # ページ高さはA4縦の有効高さに90%印刷の余裕を含め800ptとする。
    # 共通の部材境界があればそこまで戻し、長い内訳は対象を明示して継続する。
    start = 2
    capacity = 800-sheet.heights[0]-sheet.heights[1]
    while sum(sheet.heights.get(r,23) for r in range(start,len(sheet.rows))) > capacity:
        end, used = start, 0
        while end<len(sheet.rows) and used+sheet.heights.get(end,23)<=capacity:
            used += sheet.heights.get(end,23)
            end += 1
        # Σだけを次ページに残さず、最後の明細も一緒に送る。
        if end>=total: end = total-1
        safe = next((r for r in range(end,start,-1)
                     if not any(a<r<b for a,b,_,_ in member_spans)),None)
        end = safe if safe is not None else end
        sheet.page_breaks.append(end)
        for a,b,c,member in member_spans:
            if a<end<b:
                sheet.rows[end][c+5].value = f"{member}\n（続き）"
                sheet.heights[end] = max(sheet.heights[end],30)
        start = end


def build_pressure(book, sheet, rows, fixed, src, decimals, selection_label):
    """土圧の9列表。区間の上下端を2行に分け、部材単位で採用値を示す。"""
    groups = sorted({(row["column"],row["group"]) for row in rows})
    blocks = [sorted((row for row in rows if row["group"]==g),
                     key=lambda row:numeric(row["geometry"]["top_m"])) for _,g in groups]
    sheet.layout, sheet.freeze, sheet.print_scale = "pressure", (0,0), 100
    sheet.block_width = 9
    # Normal=Calibri 11、XlsxWriterの約5px分の列幅補正を除く。
    sheet.widths = [8,8.29,11.79,7,7,8,12.91,5.54,10.91]*len(blocks)
    sheet.vertical_breaks = list(range(9,len(sheet.widths),9))
    total = 2 + max(2*sum(len(row["pieces"]) for row in block) for block in blocks)
    sheet.print_last_row = total
    sheet.rows = [[Cell(style="pressure",number_format="0",borders="") for _ in sheet.widths]
                  for _ in range(total+2)]
    sheet.heights = {r:16.5 for r in range(total+1)}
    sheet.heights.update({0:24,1:48,total+1:21.75})
    pressure_format = "0."+"0"*max(1,decimals)

    def put(r,c,value=None,*,fmt=pressure_format,align="center",valign="center",borders="LRTB"):
        cell = Cell(value=None if isinstance(value,Expr) else value,
                    formula=value if isinstance(value,Expr) else None,style="pressure",
                    number_format=fmt,align=align,valign=valign,borders=borders)
        sheet.rows[r][c] = cell
        return cell

    def merge(top,bottom,c):
        if top<bottom: sheet.range_merges[top,c] = (bottom,c)

    member_spans = []
    headers = ["層番号","層厚\n(m)","有効抵抗\n土圧力\npe(z)\n(kN/m)","部材長\n(m)",None,
               "深さ\nz\n(m)","有効抵抗\n土圧力\npe(z)\n(kN/m)","部材\n番号","有効抵抗\n土圧力\npe(z)\n(kN/m)"]
    for i,block in enumerate(blocks):
        c = i*9
        first_row = block[0]
        sheet.sections.append((f"KG{first_row['group']} / モデル{first_row['column']}列目 / 土圧SDC{first_row['pressure_column']}列目",0,c))
        title = "有効抵抗土圧" + ("（上下端採用）" if any(row["method"]=="endpoints" for row in block) else "")
        put(0,c,title,align="left",borders="").style = "pressure_title"
        put(0,c+1,selection_label,align="left",borders="").style = "pressure_title"
        for j,h in enumerate(headers): put(1,c+j,h)
        sheet.column_merges[1,c+3] = c+4
        # 層の上下原値セルは、部材との重なり配置を先に走査して決定する。
        layer_spans, r = {}, 2
        for row in block:
            for p in sorted(row["pieces"],key=lambda p:numeric(p["top_m"])):
                span = layer_spans.setdefault(p["layer"],[r,r+1])
                span[1] = r+1
                r += 2
        first_rows = []
        r = 2
        for row in block:
            ident = row["id"]
            pieces = sorted(row["pieces"],key=lambda p:numeric(p["top_m"]))
            first,last = r,r+2*len(pieces)-1
            first_rows.append(first)
            put(first,c+3,numeric(row["geometry"]["bottom_m"])-numeric(row["geometry"]["top_m"]),fmt="#,##0.000",align="right",borders="LTB")
            put(first,c+7,row["member"],fmt="0")
            merge(first,last,c+3)
            merge(first,last,c+7)
            member_spans.append((first,last+1,c,row["member"]))
            prs = []
            for p in pieces:
                prs.append((r,r+1))
                ls,le = layer_spans[p["layer"]]
                for rr in (r,r+1):
                    edges = "LR"+("T" if rr==ls else "")+("B" if rr==le else "")
                    put(rr,c,p["layer"] if rr==ls else None,fmt="0",valign="top",borders=edges)
                    put(rr,c+1,numeric(p["layer_bottom_m"])-numeric(p["layer_top_m"]) if rr==ls else None,fmt="#,##0.000",align="right",valign="top",borders=edges)
                    field = p["upper_field"] if rr==ls else p["lower_field"] if rr==le else None
                    raw = src("pressure",p["sdc_line"],field,sheet,rr,c+2) if field is not None else None
                    put(rr,c+2,raw,fmt="0.0",valign="top" if rr==ls else "bottom",borders=edges)
                    top = rr==r
                    put(rr,c+5,numeric(p["top_m"] if top else p["bottom_m"]),fmt="#,##0.000",align="right",
                        valign="top" if top else "bottom",borders="LR"+("T" if top else "B"))
                    # 層途中から始まる区間でも、層全体の上端aからの距離で補間する。
                    value = sheet.ref(ls,c+2)+(sheet.ref(le,c+2)-sheet.ref(ls,c+2))*(sheet.ref(rr,c+5)-numeric(p["layer_top_m"]))/sheet.ref(ls,c+1)
                    put(rr,c+6,value,valign="top" if top else "bottom",borders="LR"+("T" if top else "B"))
                    put(rr,c+8,valign="top" if rr==first else "bottom",borders="LR"+("T" if rr==first else "")+("B" if rr==last else ""))
                if len(pieces)>1:
                    put(r,c+4,numeric(p["bottom_m"])-numeric(p["top_m"]),fmt="#,##0.000",align="right")
                    merge(r,r+1,c+4)
                else:
                    put(r,c+4,fmt="#,##0.000",borders="RT")
                    put(r+1,c+4,fmt="#,##0.000",borders="RB")
                r += 2
            if row["method"]=="integral-average":
                upper = fn("SUM",*[(sheet.ref(a,c+6)+sheet.ref(b,c+6))/2*sheet.ref(a,c+4)
                                    for a,b in prs])/sheet.ref(first,c+3)
                lower = sheet.ref(first,c+8)
            else:
                upper,lower = sheet.ref(first,c+6),sheet.ref(last,c+6)
            sheet.set_formula(first,c+8,upper)
            sheet.set_formula(last,c+8,lower)
            for rr,field,side in ((first,3,"上端"),(last,4,"下端")):
                book.expected.append(Check(fn("ROUND",sheet.ref(rr,c+8),decimals),fixed[ident,field]["after"],ident+":"+side))
        for j in range(9): put(total,c+j)
        put(total,c,"Σ")
        put(total,c+1,fn("SUM",*[sheet.ref(a,c+1) for a,b in layer_spans.values()]),fmt="#,##0.000",align="right")
        put(total,c+3,fn("SUM",*[sheet.ref(a,c+3) for a in first_rows]),fmt="#,##0.000",align="right",borders="LTB")
        put(total,c+4,borders="RTB")

    # A4縦の有効高から見出しを除き、区間2行と部材のまとまりを保つ。
    start,capacity = 2, 734-sheet.heights[0]-sheet.heights[1]
    while sum(sheet.heights[r] for r in range(start,total+1))>capacity:
        end,used = start,0
        while end<total and used+sheet.heights[end]+sheet.heights[end+1]<=capacity:
            used += sheet.heights[end]+sheet.heights[end+1]
            end += 2
        if end>=total: end = total-2  # Σだけを次ページへ送らない。
        safe = next((r for r in range(end,start,-2) if not any(a<r<b for a,b,_,_ in member_spans)),None)
        end = safe if safe is not None else end
        if end<=start: raise base.InputError("土圧帳票の区間が印刷可能な高さを超えています。")
        sheet.page_breaks.append(end)
        for a,b,c,member in member_spans:
            if a<end<b: sheet.heights[end] = max(sheet.heights[end],30)
        start = end
    for a,b,c,member in member_spans:
        cuts = [a,*[r for r in sheet.page_breaks if a<r<b],b]
        if len(cuts)==2: continue
        for col in (c+3,c+7):
            del sheet.range_merges[a,col]
            for first,last in zip(cuts,cuts[1:]):
                if first!=a:
                    put(first,col,f"{member}\n（続き）" if col==c+7 else None,
                        fmt="0" if col==c+7 else "#,##0.000",borders="LRTB" if col==c+7 else "LTB")
                merge(first,last-1,col)


def build_shaft(book, spring, force, detail, fixed, src, length, k_digits, f_digits, direction_label):
    """全節点の負担幅を地層・除外境界で区切り、同じ配置でKとFを表示する。"""
    geometries = {row["node"]: row["geometry"] for row in detail["nodes"]}
    geometries.update({row["node"]: row for row in detail["excluded"]})
    groups = sorted({(g["column"], g["group"]) for g in geometries.values()})
    cond = detail["conditions"]
    upper = numeric(cond["exclusion_m"])
    lower = numeric(cond["pile_length_m"])-numeric(cond["embedment_m"])
    segments = {}
    for col,group in groups:
        segments[group] = []
        for layer in detail["layers"]:
            top,bottom = numeric(layer["top_m"]),numeric(layer["bottom_m"])
            cuts = sorted({top,bottom,*[v for v in (upper,lower) if top<v<bottom]})
            values = next(v for v in layer["columns"] if v["column"]==col)
            for a,b in zip(cuts,cuts[1:]):
                segments[group].append(dict(id=(layer["number"],a,b),layer=layer["number"],top=a,bottom=b,
                    active=a>=upper and b<=lower,k=numeric(values["k1_kN_per_m2"]),f=numeric(values["fy_kN_per_m"]),
                    k_line=layer["spring_line"],f_line=layer["force_line"],
                    k_field=values["spring_field"],f_field=values["force_field"]))
    blocks = []
    for col,group in groups:
        block = []
        for g in sorted((g for g in geometries.values() if g["group"]==group),key=lambda g:numeric(g["depth_m"])):
            pieces = []
            for seg in segments[group]:
                width = min(numeric(g["bottom_m"]),seg["bottom"])-max(numeric(g["top_m"]),seg["top"])
                if width>0: pieces.append((seg,width))
            block.append(dict(geometry=g,pieces=pieces))
        blocks.append(block)

    count = max(sum(len(n["pieces"]) for n in block) for block in blocks)
    total = count+2
    node_spans,segment_spans = [],[]
    main_refs = {}
    for sheet,kind,digits in ((spring,"k",k_digits),(force,"f",f_digits)):
        # Normal=Calibri 11では100/95%で結果列が横へ分離する。Excel実機で92%を確認。
        sheet.layout,sheet.freeze,sheet.print_scale,sheet.block_width = "shaft",(0,0),92,7
        sheet.widths = [8,8,21.03,8.91,8.91,8.91,20]*len(blocks)
        sheet.vertical_breaks = list(range(7,len(sheet.widths),7))
        sheet.rows = [[Cell(style="shaft",number_format="0",borders="") for _ in sheet.widths] for _ in range(total+2)]
        sheet.heights = {r:18 for r in range(total+1)}
        sheet.heights.update({0:24,1:48.75,total+1:21.75})
        sheet.print_last_row = total+1
        def put(r,c,value=None,fmt="0",align="center",borders="LRTB"):
            cell = Cell(formula=value if isinstance(value,Expr) else None,value=None if isinstance(value,Expr) else value,
                        style="shaft",number_format=fmt,align=align,borders=borders)
            sheet.rows[r][c] = cell
            return cell
        prefix = "#,##0" if kind=="k" else "0"
        result_format = prefix + ("."+"0"*digits if digits else "")
        headers = ["層番号","層厚\n(m)","鉛直せん断地盤ばね定数\nKv\n(kN/m²)" if kind=="k" else "周面支持力度\nrfk\n(kN/m)",
                   "分布幅\n(m)",None,"節点番号","各節点の鉛直ばね定数\nKv\n(kN/m)" if kind=="k" else "各節点の周面支持力\nRfk\n(kN)"]
        for index,((col,group),block) in enumerate(zip(groups,blocks)):
            c = 7*index
            sheet.sections.append((f"KG{group} / SDC{col}列目",0,c))
            put(0,c,"鉛直せん断地盤ばね定数" if kind=="k" else "杭周面の支持力",align="left",borders="").style="shaft_title"
            put(0,c+1,direction_label,align="left",borders="").style="shaft_title"
            for j,h in enumerate(headers): put(1,c+j,h)
            sheet.column_merges[1,c+3] = c+4
            r,previous_layer,previous_segment = 2,None,None
            anchors,node_starts,segment_starts = {},[],[]
            for node in block:
                first,g = r,node["geometry"]
                ident = f"shaft:KG{group}:{g['node']}"
                main_refs[kind,g["node"]] = sheet.ref(first,c+6)
                node_starts.append(first)
                for j,(seg,width) in enumerate(node["pieces"]):
                    sr = seg["id"]
                    layer_start,segment_start = previous_layer!=seg["layer"],previous_segment!=sr
                    put(r,c,seg["layer"] if layer_start else None,borders="LR"+("T" if layer_start else ""))
                    put(r,c+1,seg["bottom"]-seg["top"] if segment_start else None,fmt="#,##0.000",align="right",borders="LR"+("T" if segment_start else ""))
                    value_digits = max(0 if kind=="k" else 1,-seg[kind].normalize().as_tuple().exponent)
                    source_format = prefix+("."+"0"*value_digits if value_digits else "")
                    raw = src("shaft",seg[kind+"_line"],seg[kind+"_field"],sheet,r,c+2) if segment_start and seg["active"] else 0 if segment_start else None
                    put(r,c+2,raw,
                        fmt=source_format,borders="LR"+("T" if segment_start else ""))
                    if segment_start:
                        anchors[sr] = r
                        segment_starts.append(r)
                    edges = ("T" if j==0 else "")+("B" if j==len(node["pieces"])-1 else "")
                    put(r,c+3,numeric(g["bottom_m"])-numeric(g["top_m"]) if j==0 else None,fmt="#,##0.000",align="right",borders="L"+edges)
                    put(r,c+4,width if len(node["pieces"])>1 else None,fmt="#,##0.000",align="right",borders="R"+edges)
                    put(r,c+5,g["node"] if j==0 else None,borders="LR"+edges)
                    put(r,c+6,fmt=result_format,borders="LR"+edges)
                    previous_layer,previous_segment = seg["layer"],sr
                    r += 1
                if len(node["pieces"])==1:
                    expr = sheet.ref(anchors[node["pieces"][0][0]["id"]],c+2)*sheet.ref(first,c+3)
                else:
                    expr = fn("SUM",*[sheet.ref(anchors[seg["id"]],c+2)*sheet.ref(first+j,c+4)
                                      for j,(seg,_) in enumerate(node["pieces"])])
                sheet.set_formula(first,c+6,expr)
                if kind=="k":
                    node_spans.append((first,r,c,g["node"]))
                    widths = [sheet.ref(first+j,c+4) if len(node["pieces"])>1 else sheet.ref(first,c+3)
                              for j in range(len(node["pieces"]))]
                    full_width = numeric(g["bottom_m"])-numeric(g["top_m"])
                    active_width = max(D(0),min(numeric(g["bottom_m"]),lower)-max(numeric(g["top_m"]),upper))
                    book.geometry_checks.append(Check(length(fn("SUM",*widths)),str(full_width),ident+":負担全幅"))
                    active_parts = [w for w,(seg,_) in zip(widths,node["pieces"]) if seg["active"]]
                    book.geometry_checks.append(Check(length(fn("SUM",0,*active_parts)),str(active_width),ident+":有効幅"))
            for j in (0,1,2): sheet.rows[r-1][c+j].borders += "B"
            if kind=="k": segment_spans.extend((a,b,c) for a,b in zip(segment_starts,[*segment_starts[1:],r]))
            for j in range(7): put(total,c+j)
            put(total,c,"Σ")
            put(total,c+1,fn("SUM",*[sheet.ref(rr,c+1) for rr in segment_starts]),fmt="#,##0.000",align="right")
            put(total,c+3,fn("SUM",*[sheet.ref(rr,c+3) for rr in node_starts]),fmt="#,##0.000",align="right",borders="LTB")
            put(total,c+4,borders="RTB")

    # 見本の短表は1KG1ページ。長表も区間厚・全幅・結果を重複集計しない。
    start,capacity = 2,730*100/spring.print_scale-24-48.75
    while sum(spring.heights[r] for r in range(start,len(spring.rows)))>capacity:
        end,used = start,0
        while end<len(spring.rows) and used+spring.heights[end]<=capacity:
            used += spring.heights[end]
            end += 1
        if end>=total: end=total-1
        safe = next((r for r in range(end,start,-1) if not any(a<r<b for a,b,_,_ in node_spans)),None)
        end = safe if safe is not None else end
        for sheet in (spring,force):
            sheet.page_breaks.append(end)
            for a,b,c in segment_spans:
                if a<end<b:
                    for offset in (1,2):
                        sheet.rows[end][c+offset].formula = sheet.ref(a,c+offset)
                        sheet.rows[end][c+offset].number_format = sheet.rows[a][c+offset].number_format
                    # 層名の先頭が前ページにあっても追えるようにする。
                    layer_row = next(rr for rr in range(a,1,-1) if sheet.rows[rr][c].value is not None)
                    sheet.rows[end][c].value = sheet.rows[layer_row][c].value
            for a,b,c,node in node_spans:
                if a<end<b:
                    sheet.rows[end][c+5].value = f"{node}\n（続き）"
                    sheet.rows[end][c+3].formula = sheet.ref(a,c+3)
                    sheet.rows[end][c+6].formula = sheet.ref(a,c+6)
                    sheet.heights[end] = 30
        start=end

    for row in detail["nodes"]:
        ident,node = row["id"],row["node"]
        for kind,field,digits in (("k",4,k_digits),("f",5,f_digits)):
            book.expected.append(Check(fn("ROUND",main_refs[kind,node],digits),fixed[ident,field]["after"],ident+":"+kind))


def build_tip(book, sheet, rows, fixed, src, length, direction_label, condition):
    """先端のK/F原値を3列の上下2表へ直接置き、幾何と採用値は内部で照合する。"""
    sheet.layout, sheet.freeze, sheet.print_scale = "tip", (0,0), 100
    sheet.widths = [9,24.375,24.375]
    line_height, header_height = 18.75, 30.75
    # A4縦の上下余白を除いた高さ。プリンタの端数差を4pt確保する。
    capacity = (297/25.4*72-2*0.75*72)*100/sheet.print_scale-4
    heading_height = line_height*2+header_height
    used = 0
    quantities = (("K1","k1_kN_per_m",4,"spring_line","kN/m"),
                  ("K2","k2_kN_per_m",7,"spring_line","kN/m"),
                  ("Fy","fy_kN",5,"force_line","kN"),
                  ("Fu","fu_kN",8,"force_line","kN"))
    formats = {}
    for label,key,_field,_line,_unit in quantities:
        digits = max(max(0,-D(str(row[key])).normalize().as_tuple().exponent) for row in rows)
        minimum = 1 if label in ("Fy","Fu") else 0
        digits = max(minimum,digits)
        formats[key] = "0" + (("."+"0"*minimum+"#"*(digits-minimum)) if digits else "")

    def cell(value=None, *, style="tip", align="center", borders="", fmt="0"):
        return Cell(formula=value,style=style,align=align,borders=borders,number_format=fmt) if isinstance(value,Expr) else Cell(value,style=style,align=align,borders=borders,number_format=fmt)

    def add(values, height=line_height):
        nonlocal used
        r = sheet.add(values)
        sheet.heights[r] = height
        used += height
        return r

    def page():
        nonlocal used
        sheet.page_breaks.append(len(sheet.rows))
        used = 0

    def heading(title, unit_title, labels, continued=False):
        r = add([cell(title+("（続き）" if continued else ""),style="tip_title",align="left"),
                 cell(),cell(direction_label,style="tip_title",align="right")])
        if not continued: sheet.sections.append((title,r,0))
        add([cell("節点番号",style="tip_header",borders="LRTB"),
             cell(unit_title,style="tip_header",borders="LRTB"),cell()],header_height)
        add([cell(),*[cell(v,style="tip_header",borders="LRTB") for v in labels]])
        sheet.range_merges[r+1,0] = (r+2,0)
        sheet.column_merges[r+1,1] = 2

    gradient_label = "液状化時" if condition == "liquefaction" else "短期"
    tables = (("杭先端のばね定数","杭先端の鉛直地盤ばね定数\nKtv(kN/m)",
               (f"{gradient_label}（第１勾配）",f"{gradient_label}（第２勾配）"),quantities[:2]),
              ("先端支持力","杭先端の鉛直地盤支持力\n(kN)",
               ("降伏点","終局点"),quantities[2:]))
    for index,(title,unit_title,labels,items) in enumerate(tables):
        if index:
            table_height = heading_height + len(rows)*line_height
            required = min(table_height,heading_height+line_height) if table_height>capacity else table_height
            if used+2*line_height+required>capacity:
                page()
            else:
                for _ in range(2): add([cell(),cell(),cell()])
        heading(title,unit_title,labels)
        for row in rows:
            if used+line_height>capacity:
                page()
                heading(title,unit_title,labels,continued=True)
            ident = row["id"]
            r = len(sheet.rows)
            values = [cell(row["node"],align="right",borders="LRTB")]
            for _label,key,_field,line,_unit in items:
                values.append(cell(src("tip",row[line],row["source_fields"][key],sheet,r,len(values)),borders="LRTB",fmt=formats[key]))
            r = add(values)
            for c,(_label,key,field,_line,_unit) in enumerate(items,1):
                book.expected.append(Check(sheet.ref(r,c),fixed[ident,field]["after"],ident+":"+_label))
    sheet.print_last_row = len(sheet.rows)-1

    for row in rows:
        g = row["geometry"]
        pile_length = length(expression(numeric(row["y_m"]))-numeric(g["origin_y_m"]))
        book.geometry_checks.append(Check(pile_length,str(row["pile_length_m"]),row["id"]+":杭長"))


def build(report):
    record, config = report["calculation"], report["configuration"]
    condition = config.get("sdc_condition", "seismic")
    condition_label = config.get("sdc_condition_label", sdc_columns.CONDITIONS[condition])
    direction_label = config["sdc_direction_label"] + (f"・{condition_label}" if condition == "liquefaction" else "")
    sheets = {op:Sheet(SHEET_NAMES[op]) for op in SHEET_NAMES if op in report["details"]}
    force_sheet = Sheet(SHAFT_FORCE_SHEET) if "shaft" in sheets else None
    main_sheets = []
    for op,sheet in sheets.items():
        main_sheets.append(sheet)
        if op=="shaft": main_sheets.append(force_sheet)
    book = ReportBook(main_sheets,metadata={
        "SDCConverter.RunId":report["run_id"],
        "SDCConverter.Mode":report.get("mode","preview"),
        "SDCConverter.Status":"モデル保存と同一実行" if report.get("mode")=="saved" else "計算確認・モデル未保存",
        "SDCConverter.OutputSHA256":report["output_sha256"],
        "SDCConverter.SDCDirection":config["sdc_direction_label"],
        "SDCConverter.SDCCondition":condition_label,
        "SDCConverter.PressureCase":config.get("pressure_case_label") or "not-used",
    })
    source_values = {(v["operation"],v["line"],v["field"]):numeric(v["value"]) for v in record["source_values"]}

    def src(op, line, field, sheet, row, col):
        key = (op,line,field)
        if key in book.sources:
            anchor = book.sources[key]
            if anchor[0]!=sheet.name: raise base.InputError(f"原値の所有シートが不一致: {key}")
            return Expr("ref",anchor)
        book.sources[key] = (sheet.name,row,col)
        return source_values[key]

    fixed = {(f["id"],f["field"]):f for f in record["fields"]}
    # 座標照合だけに入力最大精度+中点1桁を使う。補間・積分の途中は丸めない。
    length_values = [D(str(v)) for t in record["targets"] for k,v in t["geometry"].items() if k.endswith("_m")]
    for detail in report["details"].values():
        length_values += [D(str(v)) for k,v in detail.get("conditions",{}).items() if k.endswith("_m")]
        length_values += [D(str(v)) for row in detail.get("members",detail.get("nodes",[])) for p in row.get("pieces",[]) for k,v in p.items() if k.endswith("_m")]
    length_digits = max([3]+[-v.as_tuple().exponent for v in length_values]) + 1
    def length(expr): return fn("ROUND",expr,length_digits)
    if "horizontal" in sheets:
        build_horizontal(book,sheets["horizontal"],report["details"]["horizontal"]["members"],fixed,src,
                         direction_label)
    if "pressure" in sheets:
        build_pressure(book,sheets["pressure"],report["details"]["pressure"]["members"],fixed,src,
                       config["pressure_decimals"],
                       direction_label+"・"+config["pressure_case_label"].removeprefix("・"))
    if "shaft" in sheets:
        build_shaft(book,sheets["shaft"],force_sheet,report["details"]["shaft"],fixed,src,length,
                    config["shaft_k_decimals"],config["shaft_force_decimals"],direction_label)
    if "tip" in sheets:
        build_tip(book,sheets["tip"],report["details"]["tip"]["nodes"],fixed,src,length,
                  direction_label,condition)
    book.recalculate()
    return book
