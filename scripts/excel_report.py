"""Excel帳票とGUI表示の共通セルモデル。数式を評価し、検証済みキャッシュを出力する。"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal as D, ROUND_HALF_UP
from io import BytesIO
import math
import unicodedata
import zipfile
from xml.etree import ElementTree as ET

import fill_jiban_shogen as base

SHEET_NAMES = {"horizontal": "水平地盤ばね", "pressure": "有効抵抗土圧", "shaft": "杭周面ばね", "tip": "杭先端ばね"}
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
    checks: list[tuple[int, int]] = field(default_factory=list)
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
    expected: list[tuple[str, int, int, str, str]] = field(default_factory=list)

    def sheet(self, name): return next(s for s in self.sheets if s.name == name)

    def fit_rows(self):
        for sheet in self.sheets:
            if sheet.layout in ("horizontal", "pressure"): continue  # 見本の行高と改行を保持する。
            for r,row in enumerate(sheet.rows):
                if r in sheet.merges: continue
                lines=1
                for c,cell in enumerate(row):
                    if cell.number_format==NUM and isinstance(cell.value,(int,float,D)):
                        digits=max(0,-D(str(cell.value)).normalize().as_tuple().exponent)
                        cell.number_format='#,##0'+('.'+'0'*min(digits,12) if digits else '')
                    if not isinstance(cell.value,str):continue
                    for line in cell.value.splitlines():
                        units=sum(2 if unicodedata.east_asian_width(ch) in ('F','W','A') else 1 for ch in line)
                        lines=max(lines,math.ceil(units/max(1,sheet.widths[c]-2)))
                    lines=max(lines,len(cell.value.splitlines()))
                sheet.heights[r]=max(sheet.heights.get(r,23),lines*14+4)

    def paginate(self):
        """混在する表の見出しを改ページごとに再掲。セル参照は全シートで再配置する。"""
        maps={}
        for sheet in self.sheets:
            if sheet.layout in ("horizontal", "pressure"):
                maps[sheet.name] = {r:r for r in range(len(sheet.rows))}
                continue
            old_rows,old_heights,old_merges=sheet.rows,sheet.heights,sheet.merges
            sections={r for _,r,_ in sheet.sections}
            new_rows,heights,merges,positions=[],{},{},{}
            repeat_height=sum(old_heights.get(r,23) for r in range(4))
            capacity=505-repeat_height
            used=0
            current=None

            def append(old):
                nr=len(new_rows)
                new_rows.append(list(old_rows[old]))
                heights[nr]=old_heights.get(old,23)
                if old in old_merges:merges[nr]=old_merges[old]

            for r,row in enumerate(old_rows):
                h=old_heights.get(r,23)
                required=h
                if r in sections:
                    required+=sum(old_heights.get(j,23) for j in range(r+1,min(r+3,len(old_rows))))
                if r>=4 and used and used+required>capacity:
                    sheet.page_breaks.append(len(new_rows))
                    used=0
                    if current is not None and r>current+1 and r not in sections and row:
                        for j in (current,current+1):
                            append(j)
                            used+=old_heights.get(j,23)
                positions[r]=len(new_rows)
                append(r)
                if r>=4:used+=h
                if r in sections:current=r
            sheet.rows,sheet.heights,sheet.merges=new_rows,heights,merges
            sheet.sections=[(name,positions[r],c) for name,r,c in sheet.sections]
            sheet.column_merges={(positions[r],c):end for (r,c),end in sheet.column_merges.items()}
            sheet.range_merges={(positions[r],c):(positions[b],e) for (r,c),(b,e) in sheet.range_merges.items()}
            sheet.checks=[(positions[r],c) for r,c in sheet.checks]
            maps[sheet.name]=positions

        def moved(expr):
            if expr.op=='ref':
                name,r,c=expr.args
                return Expr('ref',(name,maps[name][r],c))
            if expr.op=='literal':return expr
            return Expr(expr.op,tuple(moved(arg) for arg in expr.args))

        for sheet in self.sheets:
            for row in sheet.rows:
                for cell in row:
                    if cell.formula:cell.formula=moved(cell.formula)
                    if cell.link:
                        name,r,c=cell.link
                        cell.link=(name,maps[name][r],c)
        self.expected=[(name,maps[name][r],c,value,target) for name,r,c,value,target in self.expected]

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
        cache = {}
        for sheet in self.sheets:
            for r, row in enumerate(sheet.rows):
                for c, cell in enumerate(row):
                    if cell.formula: cell.cached = self.value(sheet.name, r, c, cache=cache)
        if verify:
            for name, r, c, expected, target in self.expected:
                actual = D(str(self.value(name,r,c,cache=cache)))
                if actual != D(expected):
                    formula = self.sheet(name).rows[r][c].formula.text(name)
                    raise base.InputError(f"{target}: Excel再計算値 {actual} と実入力 {expected} が不一致。{name}!{address(r,c)} ={formula}")

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
                if cell.style.startswith(("spring", "pressure")):
                    props.update(font_name="ＭＳ 明朝", font_size=10, align="center")
                if cell.style in ("spring_title", "pressure_title"): props["text_wrap"] = False
                if cell.align: props["align"] = cell.align
                if cell.valign: props["valign"] = {"center":"vcenter"}.get(cell.valign,cell.valign)
                if cell.borders is not None:
                    props.update({edge:1 for code,edge in (("L","left"),("R","right"),("T","top"),("B","bottom")) if code in cell.borders})
                formats[key] = workbook.add_format(props)
            return formats[key]
        error_format = workbook.add_format({"bg_color": "#FDE5E5", "font_color": "#B42318"})
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
            for r,c in sheet.checks:
                ws.conditional_format(r,c,r,c,{"type":"cell", "criteria":"!=", "value":0, "format":error_format})
            if sheet.layout in ("horizontal", "pressure"): ws.set_portrait()
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
            elif sheet.layout == "pressure":
                ws.set_margins(18/25.4,18/25.4,19/25.4,19/25.4)
                ws.repeat_rows(0,1)
                ws.set_zoom(85)
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
        for i,sheet in enumerate(book.sheets,1):
            root = ET.fromstring(archive.read(f"xl/worksheets/sheet{i}.xml"))
            actual_merges = {e.get("ref") for e in root.findall("m:mergeCells/m:mergeCell",ns)}
            expected_merges = {f"{address(r,c)}:{address(b,e)}" for (r,c),(b,e) in sheet.merged_ranges().items()}
            if actual_merges != expected_merges:
                raise base.InputError(f"{sheet.name}: Excel結合範囲の保存照合に失敗。")
            cells = {c.attrib["r"]:c for c in root.findall(".//m:sheetData/m:row/m:c",ns)}
            for r,row in enumerate(sheet.rows):
                for c,cell in enumerate(row):
                    saved = cells.get(address(r,c))
                    value = saved.find("m:v",ns) if saved is not None else None
                    if cell.formula:
                        formula = saved.find("m:f",ns) if saved is not None else None
                        if formula is None or formula.text != cell.formula.text(sheet.name) or value is None or not math.isclose(float(value.text),cell.cached,rel_tol=1e-14,abs_tol=1e-14):
                            raise base.InputError(f"Excel数式の保存照合に失敗: {sheet.name}!{address(r,c)}")
                    elif isinstance(cell.value,(int,float,D)):
                        if value is None or not math.isclose(float(value.text),float(cell.value),rel_tol=1e-14,abs_tol=0):
                            raise base.InputError(f"Excel数値の保存照合に失敗: {sheet.name}!{address(r,c)}")
                    elif cell.value is None and value is not None:
                        raise base.InputError(f"Excel空欄の保存照合に失敗: {sheet.name}!{address(r,c)}")


def numeric(value): return D(str(value)) if value is not None and str(value).strip() else None


def build_horizontal(book, sheet, source, rows, fixed, src, length, links):
    """部材×地層の7列表。計算根拠と固定実入力の照合は入力根拠の末尾へ置く。"""
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

    layers = {}
    source.section("水平ばねの地層",["KG","列","層","SDC行","層上端\n(m)","層下端\n(m)","層厚\n(m)"])
    for block in blocks:
        for row in block:
            for piece in row["pieces"]:
                key = (row["group"],piece["layer"])
                if key in layers: continue
                r = source.add([row["group"],row["column"],piece["layer"],piece["sdc_line"],
                                numeric(piece["layer_top_m"]),numeric(piece["layer_bottom_m"]),None],
                               styles={4:"source",5:"source"},formats={i:LENGTH for i in (4,5,6)})
                source.set_formula(r,6,length(source.ref(r,5)-source.ref(r,4)))
                layers[key] = r
    geometry = {}
    source.section("水平ばねの部材幾何",["KG","列","部材","上端節点","下端節点","上端深さ\n(m)","下端深さ\n(m)","部材長\n(m)"])
    for block in blocks:
        for row in block:
            g = row["geometry"]
            r = source.add([row["group"],row["column"],row["member"],g["top_node"],g["bottom_node"],
                            numeric(g["top_m"]),numeric(g["bottom_m"]),None],
                           styles={5:"source",6:"source"},formats={i:LENGTH for i in (5,6,7)})
            source.set_formula(r,7,length(source.ref(r,6)-source.ref(r,5)))
            geometry[row["id"]] = r
    source.section("水平ばねの照合",["KG","列","部材","方法","主表の値\n(kN/m²)","採用再計算\n(kN/m²)","固定実入力\n(kN/m²)","差"])
    checks = {}
    for block in blocks:
        for row in block:
            ident = row["id"]
            actual = numeric(fixed[ident,2]["after"])
            r = source.add([row["group"],row["column"],Cell(str(row["member"]),style="link"),
                            METHODS[row["method"]],None,None,actual,None],styles={6:"actual"})
            checks[ident] = r
            digits = max(0,-actual.normalize().as_tuple().exponent) if actual is not None else 0
            source.rows[r][5].number_format = '#,##0'+('.'+'0'*digits if digits else '')
            if row["method"] != "skip":
                book.expected.append((source.name,r,5,fixed[ident,2]["after"],ident))
            if actual is not None:
                source.set_formula(r,7,source.ref(r,5)-source.ref(r,6))
                source.checks.append((r,7))

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
        for j,h in enumerate(headers): put(1,c+j,h)
        sheet.column_merges[1,c+3] = c+4
        r, previous_layer = 2, None
        layer_rows, first_rows = [], []
        for row in block:
            ident = row["id"]
            first = r
            gr, cr = geometry[ident], checks[ident]
            pieces = sorted(row["pieces"],key=lambda p:numeric(p["top_m"]))
            first_rows.append(first)
            links[ident] = (sheet.name,first,c+5)
            source.rows[cr][2].link = links[ident]
            for j,p in enumerate(pieces):
                lr = layers[row["group"],p["layer"]]
                layer_start = previous_layer != p["layer"]
                put(r,c,p["layer"] if layer_start else None,borders="LR"+("T" if layer_start else ""))
                put(r,c+1,source.ref(lr,6) if layer_start else None,fmt="#,##0.000",align="right",borders="LR"+("T" if layer_start else ""))
                if layer_start: layer_rows.append(r)
                put(r,c+2,src("horizontal",p["sdc_line"],p["value_field"]))
                edges = ("T" if j==0 else "")+("B" if j==len(pieces)-1 else "")
                put(r,c+3,source.ref(gr,7) if j==0 else None,fmt="#,##0.000",align="right",borders="L"+edges)
                overlap = fn("MAX",0,length(fn("MIN",source.ref(gr,6),source.ref(lr,5))-
                                           fn("MAX",source.ref(gr,5),source.ref(lr,4))))
                put(r,c+4,overlap if len(pieces)>1 else None,fmt="#,##0.000",align="right",borders="R"+edges)
                put(r,c+5,row["member"] if j==0 else None,fmt="0",borders="LR"+edges)
                put(r,c+6,borders="LR"+edges)
                previous_layer = p["layer"]
                r += 1
            member_spans.append((first,r,c,row["member"]))
            method = row["method"]
            if method == "skip":
                # 保留された現在値は空欄もそのまま残す。加重平均の値に見せない。
                actual = source.rows[cr][6].value
                if actual is not None:
                    sheet.set_formula(first,c+6,source.ref(cr,6))
                    source.set_formula(cr,4,sheet.ref(first,c+6))
                    source.set_formula(cr,5,source.ref(cr,6))
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
            source.set_formula(cr,4,sheet.ref(first,c+6))
            source.set_formula(cr,5,fn("ROUND",source.ref(cr,4),0) if method=="length-weighted" else source.ref(cr,4))
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

    source.section("水平ばねの正確な計算記録",["部材ごとの丸め前値・採用条件"])
    for row in rows:
        text = f"KG{row['group']} 部材{row['member']} 丸め前: {row['unrounded_value'] if row['unrounded_value'] is not None else '保留（現在値を保持）'}"
        if row["method"]=="midpoint": text += f"　中央深さ {row['midpoint_m']} mの層を採用（境界ちょうどは下側）。"
        source.note(text)


def build_pressure(book, sheet, source, rows, fixed, src, length, links, decimals):
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

    layers, geometry, intervals = {}, {}, {}
    source.section("土圧の地層",["KG","モデル列","土圧列","層","SDC行","層上端\n(m)","層下端\n(m)",
                                "層厚\n(m)","原上側\n(kN/m)","原下側\n(kN/m)"])
    for block in blocks:
        for row in block:
            for p in row["pieces"]:
                key = (row["group"],p["layer"])
                if key in layers: continue
                r = source.add([row["group"],row["column"],row["pressure_column"],p["layer"],p["sdc_line"],
                                numeric(p["layer_top_m"]),numeric(p["layer_bottom_m"]),None,
                                src("pressure",p["sdc_line"],p["upper_field"]),
                                src("pressure",p["sdc_line"],p["lower_field"])],
                               styles={5:"source",6:"source"},formats={i:LENGTH for i in (5,6,7)})
                source.set_formula(r,7,length(source.ref(r,6)-source.ref(r,5)))
                layers[key] = r
    source.note("原土圧は層全体の上下側の値です。杭が層途中で終わる場合も、補間は元の層上下端深さを用います。")
    source.section("土圧の部材幾何",["KG","モデル列","部材","上端節点","下端節点","上端深さ\n(m)","下端深さ\n(m)","部材長\n(m)"])
    for block in blocks:
        for row in block:
            g = row["geometry"]
            r = source.add([row["group"],row["column"],row["member"],g["top_node"],g["bottom_node"],
                            numeric(g["top_m"]),numeric(g["bottom_m"]),None],
                           styles={5:"source",6:"source"},formats={i:LENGTH for i in (5,6,7)})
            source.set_formula(r,7,length(source.ref(r,6)-source.ref(r,5)))
            geometry[row["id"]] = r
    source.section("土圧の区間計算",["KG","モデル列","部材","層","区間上端\n(m)","区間下端\n(m)","区間長\n(m)",
                                    "補間上端\n(kN/m)","補間下端\n(kN/m)","上端比率","下端比率","台形積分\n(kN)"])
    for block in blocks:
        for row in block:
            gr = geometry[row["id"]]
            for p in row["pieces"]:
                lr = layers[row["group"],p["layer"]]
                r = source.add([row["group"],row["column"],row["member"],p["layer"],*([None]*8)],
                               formats={4:LENGTH,5:LENGTH,6:LENGTH,9:"0.000000",10:"0.000000"})
                source.set_formula(r,4,fn("MAX",source.ref(gr,5),source.ref(lr,5)))
                source.set_formula(r,5,fn("MIN",source.ref(gr,6),source.ref(lr,6)))
                source.set_formula(r,6,fn("MAX",0,length(source.ref(r,5)-source.ref(r,4))))
                for c,depth in ((9,4),(10,5)):
                    source.set_formula(r,c,(source.ref(r,depth)-source.ref(lr,5))/source.ref(lr,7))
                source.set_formula(r,11,(source.ref(r,7)+source.ref(r,8))/2*source.ref(r,6))
                intervals[row["id"],p["layer"]] = r

    member_spans, results = [], {}
    headers = ["層番号","層厚\n(m)","有効抵抗\n土圧力\npe(z)\n(kN/m)","部材長\n(m)",None,
               "深さ\nz\n(m)","有効抵抗\n土圧力\npe(z)\n(kN/m)","部材\n番号","有効抵抗\n土圧力\npe(z)\n(kN/m)"]
    for i,block in enumerate(blocks):
        c = i*9
        first_row = block[0]
        sheet.sections.append((f"KG{first_row['group']} / モデル{first_row['column']}列目 / 土圧SDC{first_row['pressure_column']}列目",0,c))
        title = "有効抵抗土圧" + ("（上下端採用）" if any(row["method"]=="endpoints" for row in block) else "")
        put(0,c,title,align="left",borders="").style = "pressure_title"
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
            gr = geometry[ident]
            put(first,c+3,source.ref(gr,7),fmt="#,##0.000",align="right",borders="LTB")
            put(first,c+7,row["member"],fmt="0")
            merge(first,last,c+3)
            merge(first,last,c+7)
            links[ident] = (sheet.name,first,c+7)
            member_spans.append((first,last+1,c,row["member"]))
            prs = []
            for p in pieces:
                lr = layers[row["group"],p["layer"]]
                pr = intervals[ident,p["layer"]]
                prs.append(pr)
                ls,le = layer_spans[p["layer"]]
                for rr in (r,r+1):
                    edges = "LR"+("T" if rr==ls else "")+("B" if rr==le else "")
                    put(rr,c,p["layer"] if rr==ls else None,fmt="0",valign="top",borders=edges)
                    put(rr,c+1,source.ref(lr,7) if rr==ls else None,fmt="#,##0.000",align="right",valign="top",borders=edges)
                    raw = source.ref(lr,8) if rr==ls else source.ref(lr,9) if rr==le else None
                    put(rr,c+2,raw,fmt="0.0",valign="top" if rr==ls else "bottom",borders=edges)
                    top = rr==r
                    put(rr,c+5,source.ref(pr,4 if top else 5),fmt="#,##0.000",align="right",
                        valign="top" if top else "bottom",borders="LR"+("T" if top else "B"))
                    # 層途中から始まる区間でも、層全体の上端aからの距離で補間する。
                    value = sheet.ref(ls,c+2)+(sheet.ref(le,c+2)-sheet.ref(ls,c+2))*(sheet.ref(rr,c+5)-source.ref(lr,5))/sheet.ref(ls,c+1)
                    put(rr,c+6,value,valign="top" if top else "bottom",borders="LR"+("T" if top else "B"))
                    put(rr,c+8,valign="top" if rr==first else "bottom",borders="LR"+("T" if rr==first else "")+("B" if rr==last else ""))
                source.set_formula(pr,7,sheet.ref(r,c+6))
                source.set_formula(pr,8,sheet.ref(r+1,c+6))
                if len(pieces)>1:
                    put(r,c+4,source.ref(pr,6),fmt="#,##0.000",align="right")
                    merge(r,r+1,c+4)
                else:
                    put(r,c+4,fmt="#,##0.000",borders="RT")
                    put(r+1,c+4,fmt="#,##0.000",borders="RB")
                r += 2
            if row["method"]=="integral-average":
                upper = fn("SUM",*[source.ref(pr,11) for pr in prs])/sheet.ref(first,c+3)
                lower = sheet.ref(first,c+8)
            else:
                upper,lower = sheet.ref(first,c+6),sheet.ref(last,c+6)
            sheet.set_formula(first,c+8,upper)
            sheet.set_formula(last,c+8,lower)
            results[ident] = (first,last,c+8)
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

    source.section("土圧の採用照合",["KG","モデル列","土圧列","部材明細","端","方法","主表丸め前\n(kN/m)",
                                    "採用再計算\n(kN/m)","固定実入力\n(kN/m)","差\n(kN/m)","Decimal丸め前文字列"])
    for row in rows:
        ident = row["id"]
        first,last,c = results[ident]
        for index,(side,rr,field) in enumerate((("上端",first,3),("下端",last,4))):
            r = source.add([row["group"],row["column"],row["pressure_column"],
                            Cell(str(row["member"]),style="link",link=links[ident]),side,METHODS[row["method"]],
                            sheet.ref(rr,c),None,numeric(fixed[ident,field]["after"]),None,row["unrounded"][index]],
                           styles={8:"actual"},formats={7:"0"+("."+"0"*decimals if decimals else ""),8:pressure_format})
            source.set_formula(r,7,fn("ROUND",source.ref(r,6),decimals))
            source.set_formula(r,9,source.ref(r,7)-source.ref(r,8))
            source.checks.append((r,9))
            book.expected.append((source.name,r,7,fixed[ident,field]["after"],ident))


def build(report):
    record, config = report["calculation"], report["configuration"]
    summary = Sheet("変換結果")
    sheets = {op:Sheet(SHEET_NAMES[op]) for op in SHEET_NAMES if op in report["details"]}
    source_sheet = Sheet("入力根拠")
    book = ReportBook([summary,*sheets.values(),source_sheet])
    state = "モデル保存と同一実行" if report.get("mode") == "saved" else "計算確認・モデル未保存"
    for sheet in book.sheets:
        if sheet.name in (SHEET_NAMES["horizontal"],SHEET_NAMES["pressure"]): continue
        sheet.note(sheet.name, "title")
        sheet.note(state + "　" + report.get("created_at", ""))
        sheet.note("青文字：原値　黄色：変換時の実入力（固定）　数式：再計算値。条件変更時はアプリから再実行してください。")
        sheet.add([])
    source_sheet.section("実行・ファイル情報", ["項目", "値"])
    metadata = {"実行ID":report["run_id"], "アプリ":report["application"]+" "+report["version"],
                "帳票スキーマ":record["schema_version"], "対象モデル":report["sources"][1]["path"],
                "状態":state, "モデル出力":report.get("output") or "未保存",
                "出力予定SHA-256":report["output_sha256"], "JSON":report.get("report_path") or "未保存",
                "Excel":report.get("excel_path") or "未保存", "バックアップ":report.get("backup") or "なし",
                "杭対応":" ".join(f"{g}:{c}" for g,c in config["groups"].items()),
                "土圧方向":config["push_direction"], "周面方式":config.get("shaft_profile") or "未選択",
                "水平境界":config["horizontal_cross_layer"], "土圧境界":config["pressure_cross_layer"],
                "土圧小数桁":config["pressure_decimals"], "周面K小数桁":config["shaft_k_decimals"],
                "周面F小数桁":config["shaft_force_decimals"], "丸め":"ROUND_HALF_UP（先端・水平単一層は原値）"}
    for label,value in metadata.items(): source_sheet.note(f"{label}: {value}")
    for source in report["sources"]:
        source_sheet.note(f"入力: {source['path']}")
        source_sheet.note(f"SHA-256: {source['sha256']}")
    source_sheet.section("採用したSDC原値", ["処理", "SDC行", "CSV欄", "項目", "単位", "原値", "正確な十進文字列"])
    source_refs = {}
    for value in record["source_values"]:
        r=source_sheet.add([SHEET_NAMES[value["operation"]],value["line"],value["field"],value["label"],value["unit"],numeric(value["value"]),value["value"]],styles={5:"source"})
        source_refs[(value["operation"],value["line"],value["field"])] = source_sheet.ref(r,5)
    source_sheet.widths = [17,9,9,24,11,15,26,11,11,11,11,11]

    def src(op,line,field): return source_refs[(op,line,field)]

    fixed = {(f["id"],f["field"]):f for f in record["fields"]}
    # 座標差は入力が持つ十進桁まで正確。中点用に1桁追加し、二進の引算ノイズだけを除く。
    length_values = [D(str(v)) for t in record["targets"] for k,v in t["geometry"].items() if k.endswith("_m")]
    for detail in report["details"].values():
        length_values += [D(str(v)) for k,v in detail.get("conditions",{}).items() if k.endswith("_m")]
        length_values += [D(str(v)) for row in detail.get("members",detail.get("nodes",[])) for p in row.get("pieces",[]) for k,v in p.items() if k.endswith("_m")]
    length_digits = max([3]+[-v.as_tuple().exponent for v in length_values]) + 1
    def length(expr): return fn("ROUND",expr,length_digits)
    source_sheet.note(f"座標演算の保持小数桁: {length_digits}（入力座標の最大精度＋中点の1桁）。積分・補間の結果は途中で丸めません。")
    detail_links = {}
    for op,sheet in sheets.items():
        if op in ("horizontal","pressure"): continue
        detail=report["details"][op]
        rows=detail.get("members",detail.get("nodes",[]))
        sheet.widths = [8,8,10,17,15,15,15,15,15,15,15,15]
        if op=="shaft":
            sheet.note("既存画面方式：押込みK1/Fyを正負へ配置。長さ積分後に丸め、平均の除算・1.2補正・本数の追加乗算はしません。")
            sheet.section("節点別の結果",["KG","列","節点","有効長\n(m)","K丸め前\n(kN/m)","K再計算\n(kN/m)","K実入力\n(kN/m)","K差","F丸め前\n(kN)","F再計算\n(kN)","F実入力\n(kN)","F差"])
        else:
            sheet.note("SDCの短期K1/K2・押込みFy/Fuを転記。負側制限値F1−/F2−は空欄のまま転記します。")
            sheet.section("杭先端の結果",["KG","列","先端節点","K1再計算\n(kN/m)","K1実入力\n(kN/m)","K2再計算\n(kN/m)","K2実入力\n(kN/m)","Fy再計算\n(kN)","Fy実入力\n(kN)","Fu再計算\n(kN)","Fu実入力\n(kN)"])
        summaries={}
        for row in rows:
            ident=row["id"]
            def actual(field): return Cell(numeric(fixed[ident,field]["after"]),style="actual")
            if op=="shaft": values=[row["group"],row["column"],row["node"],None,None,None,actual(4),None,None,None,actual(5),None]
            else: values=[row["group"],row["column"],row["node"],None,actual(4),None,actual(7),None,actual(5),None,actual(8)]
            r=sheet.add(values)
            summaries[ident]=r
            detail_links[ident]=(sheet.name,r,0)
            checks = [(5,6,7,4),(9,10,11,5)] if op=="shaft" else [(3,4,None,4),(5,6,None,7),(7,8,None,5),(9,10,None,8)]
            for calc,actual_col,delta,f in checks:
                if row.get("method") != "skip": book.expected.append((sheet.name,r,calc,fixed[ident,f]["after"],ident))
                digits=max(0,-numeric(fixed[ident,f]["after"]).normalize().as_tuple().exponent) if fixed[ident,f]["after"] is not None else 0
                sheet.rows[r][calc].number_format='#,##0'+('.'+'0'*digits if digits else '')
                if delta is not None:
                    sheet.set_formula(r,delta,sheet.ref(r,calc)-sheet.ref(r,actual_col))
                    sheet.checks.append((r,delta))
        geometries={}
        if op=="shaft":
            cond=detail["conditions"]
            sheet.section("抵抗を考慮する範囲",["杭長 (m)","1/β (m)","根入れ (m)","有効下端 (m)"])
            cr=sheet.add([cond["pile_length_m"],cond["exclusion_m"],cond["embedment_m"],None],styles={0:"source",1:"source",2:"source"},formats={i:LENGTH for i in range(4)})
            sheet.set_formula(cr,3,length(sheet.ref(cr,0)-sheet.ref(cr,2)))
            sheet.section("節点の負担区間（隣接座標の中点）",["KG","列","節点","前節点","次節点","前深さ\n(m)","節点深さ\n(m)","次深さ\n(m)","負担上端\n(m)","負担下端\n(m)","負担長\n(m)","除外長\n(m)"])
            for row in rows:
                g=row["geometry"]
                r=sheet.add([row["group"],row["column"],row["node"],g["previous_node"],g["next_node"],g["previous_depth_m"],g["depth_m"],g["next_depth_m"],None,None,None,None],styles={5:"source",6:"source",7:"source"},formats={i:LENGTH for i in range(5,12)})
                sheet.set_formula(r,8,length((sheet.ref(r,5)+sheet.ref(r,6))/2))
                sheet.set_formula(r,9,length((sheet.ref(r,6)+sheet.ref(r,7))/2))
                sheet.set_formula(r,10,length(sheet.ref(r,9)-sheet.ref(r,8)))
                sheet.set_formula(r,11,length(sheet.ref(r,10)-sheet.ref(summaries[row["id"]],3)))
                geometries[row["id"]]=r
        else:
            sheet.section("杭頭・最深節点と杭長",["KG","列","杭頭節点","先端節点","x座標\n(m)","杭頭y\n(m)","先端y\n(m)","座標差\n(m)","SDC杭長\n(m)"])
            for row in rows:
                g=row["geometry"]
                r=sheet.add([row["group"],row["column"],g["head_node"],row["node"],row["x_m"],g["origin_y_m"],row["y_m"],None,row["pile_length_m"]],styles={i:"source" for i in (4,5,6,8)},formats={i:LENGTH for i in range(4,9)})
                sheet.set_formula(r,7,length(sheet.ref(r,6)-sheet.ref(r,5)))
                sr=summaries[row["id"]]
                for c,key,line in ((3,"k1_kN_per_m","spring_line"),(5,"k2_kN_per_m","spring_line"),(7,"fy_kN","force_line"),(9,"fu_kN","force_line")):
                    sheet.set_formula(sr,c,src(op,row[line],row["source_fields"][key]))
        if op=="shaft":
            sheet.section("地層と有効な重なり",["KG","節点","層","層上端\n(m)","層下端\n(m)","全層厚\n(m)","有効上端\n(m)","有効下端\n(m)","重なり上端\n(m)","重なり下端\n(m)","有効長\n(m)"])
        piece_rows={}
        for row in rows:
            if op=="tip": break
            gr=geometries[row["id"]]
            prs=[]
            for p in row["pieces"]:
                number=row.get("member",row.get("node"))
                r=sheet.add([row["group"],number,p["layer"],numeric(p["layer_top_m"]),numeric(p["layer_bottom_m"]),None,None,None,None,None,None],styles={3:"source",4:"source"},formats={i:LENGTH for i in range(3,11)})
                sheet.set_formula(r,5,length(sheet.ref(r,4)-sheet.ref(r,3)))
                sheet.set_formula(r,6,fn("MAX",sheet.ref(r,3),sheet.ref(cr,1)))
                sheet.set_formula(r,7,fn("MIN",sheet.ref(r,4),sheet.ref(cr,3)))
                sheet.set_formula(r,8,fn("MAX",sheet.ref(gr,8),sheet.ref(r,6)))
                sheet.set_formula(r,9,fn("MIN",sheet.ref(gr,9),sheet.ref(r,7)))
                sheet.set_formula(r,10,fn("MAX",0,length(sheet.ref(r,9)-sheet.ref(r,8))))
                prs.append(r)
            piece_rows[row["id"]]=prs
        if op=="shaft":
            sheet.section("周面K1・Fyの区間積分",["KG","節点","層","KのSDC行","FのSDC行","有効長\n(m)","原値K1\n(kN/m²)","原値Fy\n(kN/m)","K1×長さ\n(kN/m)","Fy×長さ\n(kN)"])
        for row in rows:
            if op=="tip": break
            sr=summaries[row["id"]]
            gr=geometries[row["id"]]
            prs=piece_rows[row["id"]]
            calc_rows=[]
            for p,pr in zip(row["pieces"],prs):
                number=row["node"]
                r=sheet.add([row["group"],number,p["layer"],p["spring_line"],p["force_line"],sheet.ref(pr,10),src(op,p["spring_line"],p["spring_field"]),src(op,p["force_line"],p["force_field"]),None,None],styles={6:"source",7:"source"},formats={5:LENGTH})
                sheet.set_formula(r,8,sheet.ref(r,5)*sheet.ref(r,6))
                sheet.set_formula(r,9,sheet.ref(r,5)*sheet.ref(r,7))
                calc_rows.append(r)
            sheet.set_formula(sr,3,fn("SUM",*[sheet.ref(r,10) for r in prs]))
            sheet.set_formula(sr,4,fn("SUM",*[sheet.ref(r,8) for r in calc_rows]))
            sheet.set_formula(sr,8,fn("SUM",*[sheet.ref(r,9) for r in calc_rows]))
            sheet.set_formula(sr,5,fn("ROUND",sheet.ref(sr,4),config["shaft_k_decimals"]))
            sheet.set_formula(sr,9,fn("ROUND",sheet.ref(sr,8),config["shaft_force_decimals"]))
        if op=="shaft":
            sheet.section("周面工程の除外節点",["KG","列","節点","深さ (m)","負担上端 (m)","負担下端 (m)"])
            for row in detail["excluded"]:
                sheet.add([row["group"],row["column"],row["node"],row["depth_m"],row["top_m"],row["bottom_m"]],formats={i:LENGTH for i in range(3,6)})
                sheet.note(row["reason"])
        if op in ("shaft","tip"):
            for title,offsets in (("ばね実入力 (kN/m)",[4,7,10,11,12,13]),("制限値実入力 (kN)",[5,6,8,9])):
                labels=[fixed[rows[0]["id"],i]["label"] for i in offsets]
                sheet.section(title,["KG","列","節点","支点項目",*labels])
                for row in rows:
                    sheet.add([row["group"],row["column"],row["node"],row["output"]["item"],
                               *[numeric(fixed[row["id"],i]["after"]) for i in offsets]],styles={i:"actual" for i in range(4,4+len(offsets))})
        sheet.section("Python Decimalの丸め前文字列",["KG","対象","項目","正確な計算記録"])
        for row in rows:
            originals = [("K",row["raw_k1_kN_per_m"]),("F",row["raw_fy_kN"])] if op=="shaft" else [(k,row[k]) for k in ("k1_kN_per_m","k2_kN_per_m","fy_kN","fu_kN")]
            for label,value in originals: sheet.note(f"KG{row['group']} {row.get('member',row.get('node'))} {label}: {value if value is not None else '保留'}")

    source_sheet.section("SDC見出しと原行（文字列保存）",["原行"])
    for row in record["sdc_context"]: source_sheet.note(f"SDC {row['line']}行: {row['raw']}")
    source_sheet.section("NDU幾何の出典",["原行"])
    for row in record["geometry"]: source_sheet.note(f"NDU {row['line']}行: {row['raw']}")
    source_sheet.section("支点数・ケース行の構造変更",["項目"])
    source_sheet.note("構造変更あり" if record["structure"]["changed"] else "構造変更なし")
    if record["structure"]["changed"]:
        for label in ("before","after"):
            for raw in record["structure"][label]: source_sheet.note(f"{label}: {raw}")
    if "horizontal" in sheets:
        build_horizontal(book,sheets["horizontal"],source_sheet,report["details"]["horizontal"]["members"],
                         fixed,src,length,detail_links)
    if "pressure" in sheets:
        build_pressure(book,sheets["pressure"],source_sheet,report["details"]["pressure"]["members"],
                       fixed,src,length,detail_links,config["pressure_decimals"])

    summary.note("モデル: "+report["sources"][1]["path"])
    summary.note(f"計算対象 {len(record['targets'])}件　変更 {record['counts']['変更']}件　追加 {record['counts']['追加']}件　同値 {record['counts']['同値']}件　保留 {record['counts']['保留']}件")
    if "shaft" in report["details"]: summary.note(f"周面の抵抗0による除外: {len(report['details']['shaft']['zero_resistance_nodes'])}節点")
    member_targets={}
    for target in record['targets']:
        if target['kind']=='部材': member_targets.setdefault(target['number'],target)
    if member_targets:
        pressure_link = "horizontal" in sheets and "pressure" in sheets
        summary.section("部材の実入力",["計算明細","KG","列","部材","上端節点","下端節点","水平ばね\n(kN/m²)","上端土圧\n(kN/m)","下端土圧\n(kN/m)","NDU行"]+(["土圧明細"] if pressure_link else []))
        for number,target in member_targets.items():
            member_fields={f['field']:f for f in record['fields'] if f['kind']=='部材' and f['number']==number}
            geo=target['geometry']
            summary.add([Cell("部材"+str(number),style='link',link=detail_links[target['id']]),target['group'],target['column'],number,geo['top_node'],geo['bottom_node'],
                         *[numeric(member_fields[i]['after']) if i in member_fields else '未計算' for i in (2,3,4)],target['line']]+
                        ([Cell("土圧"+str(number),style="link",link=detail_links[f"pressure:KG{target['group']}:{number}"])] if pressure_link else []),styles={6:'actual',7:'actual',8:'actual'})
    node_targets=[t for t in record['targets'] if t['kind']=='節点']
    if node_targets:
        for title,offsets in (("節点ばねの実入力 (kN/m)",[4,7,10,11,12,13]),("節点制限値の実入力 (kN)",[5,6,8,9])):
            labels=[fixed[node_targets[0]['id'],i]['label'] for i in offsets]
            summary.section(title,["計算明細","KG","列","節点","支点項目","方向",*labels])
            for t in node_targets:
                summary.add([Cell(SHEET_NAMES[t['operation']],style='link',link=detail_links[t['id']]),t['group'],t['column'],t['number'],t['item'],'Y',
                             *[numeric(fixed[t['id'],i]['after']) for i in offsets]],styles={i:'actual' for i in range(6,6+len(offsets))})
    summary.widths=[18,7,7,10,11,10,15,15,15,15,15,15]
    summary.section("変更前後（空欄と0を区別）",["処理","KG","対象","番号","入力欄","変更前","実入力","差","処理結果","空欄の変更"])
    for f in record["fields"]:
        r=summary.add([SHEET_NAMES[f["operation"]],f["group"],f["kind"],f["number"],f["label"],numeric(f["before"]),numeric(f["after"]),None,f["field_status"],f["transition"]],styles={6:"actual"})
        if f["difference"] is not None: summary.set_formula(r,7,summary.ref(r,6)-summary.ref(r,5))
    book.recalculate()
    book.fit_rows()
    book.paginate()
    book.recalculate()
    return book
