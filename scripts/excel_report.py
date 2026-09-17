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
            if sheet.layout in ("horizontal", "pressure", "shaft", "tip"): continue  # 見本の行高と改行を保持する。
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
            if sheet.layout in ("horizontal", "pressure", "shaft", "tip"):
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


def build_shaft(book, spring, force, source, detail, fixed, src, length, links, force_links, k_digits, f_digits):
    """全節点の負担幅を地層・除外境界で区切り、同じ配置でKとFを表示する。"""
    geometries = {row["node"]: row["geometry"] for row in detail["nodes"]}
    geometries.update({row["node"]: row for row in detail["excluded"]})
    groups = sorted({(g["column"], g["group"]) for g in geometries.values()})
    cond = detail["conditions"]
    upper = numeric(cond["exclusion_m"])
    lower = numeric(cond["pile_length_m"])-numeric(cond["embedment_m"])
    source.section("周面の抵抗範囲", ["杭長 (m)", "1/β (m)", "根入れ (m)", "有効下端 (m)"])
    cr = source.add([cond["pile_length_m"], cond["exclusion_m"], cond["embedment_m"], None],
                    styles={i:"source" for i in range(3)}, formats={i:LENGTH for i in range(4)})
    source.set_formula(cr,3,length(source.ref(cr,0)-source.ref(cr,2)))
    source.note("周面は押込みK1/Fyの長さ積分。平均除算・1.2補正・本数の追加乗算なし。先端行の0は周面抵抗です。")

    source.section("周面の全地層", ["KG","列","層","KのSDC行","FのSDC行","層上端 (m)","層下端 (m)",
                                    "全層厚 (m)","d表層厚 (m)","有効上端 (m)","有効下端 (m)","有効厚 (m)"])
    layers = {}
    for col,group in groups:
        for layer in detail["layers"]:
            r = source.add([group,col,layer["number"],layer["spring_line"],layer["force_line"],
                            layer["top_m"],layer["bottom_m"],None,layer["spring_thickness_m"],None,None,None],
                           styles={i:"source" for i in (5,6,8)},formats={i:LENGTH for i in range(5,12)})
            source.set_formula(r,7,length(source.ref(r,6)-source.ref(r,5)))
            source.set_formula(r,9,fn("MAX",source.ref(r,5),source.ref(cr,1)))
            source.set_formula(r,10,fn("MIN",source.ref(r,6),source.ref(cr,3)))
            source.set_formula(r,11,fn("MAX",0,length(source.ref(r,10)-source.ref(r,9))))
            layers[group,layer["number"]] = r

    source.section("周面の表示区間", ["KG","列","層","抵抗区分","区間上端 (m)","区間下端 (m)",
                                      "区間厚 (m)","K原値 (kN/m²)","F原値 (kN/m)"])
    segments = {}
    for col,group in groups:
        segments[group] = []
        for layer in detail["layers"]:
            lr = layers[group,layer["number"]]
            top,bottom = numeric(layer["top_m"]),numeric(layer["bottom_m"])
            cuts = {top:source.ref(lr,5),bottom:source.ref(lr,6)}
            for depth,ref in ((upper,source.ref(cr,1)),(lower,source.ref(cr,3))):
                if top<depth<bottom: cuts[depth] = ref
            boundaries = sorted(cuts)
            values = next(v for v in layer["columns"] if v["column"]==col)
            for a,b in zip(boundaries,boundaries[1:]):
                active = a>=upper and b<=lower
                r = source.add([group,col,layer["number"],"有効" if active else "除外",cuts[a],cuts[b],None,
                                src("shaft",layer["spring_line"],values["spring_field"]) if active else 0,
                                src("shaft",layer["force_line"],values["force_field"]) if active else 0],
                               formats={i:LENGTH for i in (4,5,6)})
                source.set_formula(r,6,length(source.ref(r,5)-source.ref(r,4)))
                segments[group].append(dict(layer=layer["number"],top=a,bottom=b,active=active,row=r,
                                             k=numeric(values["k1_kN_per_m2"]),f=numeric(values["fy_kN_per_m"])))

    source.section("周面の全節点幾何", ["KG","列","節点","前節点","次節点","前深さ (m)","節点深さ (m)",
                                      "次深さ (m)","負担上端 (m)","負担下端 (m)","負担全幅 (m)","有効幅 (m)"])
    blocks = []
    for col,group in groups:
        block = []
        for g in sorted((g for g in geometries.values() if g["group"]==group),key=lambda g:numeric(g["depth_m"])):
            r = source.add([group,col,g["node"],g["previous_node"],g["next_node"],g["previous_depth_m"],
                            g["depth_m"],g["next_depth_m"],None,None,None,None],
                           styles={i:"source" for i in (5,6,7)},formats={i:LENGTH for i in range(5,12)})
            source.set_formula(r,8,length((source.ref(r,5)+source.ref(r,6))/2))
            source.set_formula(r,9,length((source.ref(r,6)+source.ref(r,7))/2))
            source.set_formula(r,10,length(source.ref(r,9)-source.ref(r,8)))
            source.set_formula(r,11,fn("MAX",0,length(fn("MIN",source.ref(r,9),source.ref(cr,3))-
                                                     fn("MAX",source.ref(r,8),source.ref(cr,1)))))
            block.append(dict(geometry=g,row=r,pieces=[]))
        blocks.append(block)

    source.section("周面の節点区間内訳", ["KG","節点","層","抵抗区分","重なり上端 (m)","重なり下端 (m)","内訳幅 (m)"])
    for (_,group),block in zip(groups,blocks):
        for node in block:
            g,gr = node["geometry"],node["row"]
            for seg in segments[group]:
                if min(numeric(g["bottom_m"]),seg["bottom"])<=max(numeric(g["top_m"]),seg["top"]): continue
                sr = seg["row"]
                r = source.add([group,g["node"],seg["layer"],"有効" if seg["active"] else "除外",None,None,None],
                               formats={i:LENGTH for i in (4,5,6)})
                source.set_formula(r,4,fn("MAX",source.ref(gr,8),source.ref(sr,4)))
                source.set_formula(r,5,fn("MIN",source.ref(gr,9),source.ref(sr,5)))
                source.set_formula(r,6,fn("MAX",0,length(source.ref(r,5)-source.ref(r,4))))
                node["pieces"].append((seg,r))

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
            for j,h in enumerate(headers): put(1,c+j,h)
            sheet.column_merges[1,c+3] = c+4
            r,previous_layer,previous_segment = 2,None,None
            anchors,node_starts,segment_starts = {},[],[]
            for node in block:
                first,gr,g = r,node["row"],node["geometry"]
                ident = f"shaft:KG{group}:{g['node']}"
                main_refs[kind,g["node"]] = sheet.ref(first,c+6)
                (links if kind=="k" else force_links)[ident] = (sheet.name,first,c+5)
                node_starts.append(first)
                for j,(seg,pr) in enumerate(node["pieces"]):
                    sr = seg["row"]
                    layer_start,segment_start = previous_layer!=seg["layer"],previous_segment!=sr
                    put(r,c,seg["layer"] if layer_start else None,borders="LR"+("T" if layer_start else ""))
                    put(r,c+1,source.ref(sr,6) if segment_start else None,fmt="#,##0.000",align="right",borders="LR"+("T" if segment_start else ""))
                    value_digits = max(0 if kind=="k" else 1,-seg[kind].normalize().as_tuple().exponent)
                    source_format = prefix+("."+"0"*value_digits if value_digits else "")
                    put(r,c+2,source.ref(sr,7 if kind=="k" else 8) if segment_start else None,
                        fmt=source_format,borders="LR"+("T" if segment_start else ""))
                    if segment_start:
                        anchors[sr] = r
                        segment_starts.append(r)
                    edges = ("T" if j==0 else "")+("B" if j==len(node["pieces"])-1 else "")
                    put(r,c+3,source.ref(gr,10) if j==0 else None,fmt="#,##0.000",align="right",borders="L"+edges)
                    put(r,c+4,source.ref(pr,6) if len(node["pieces"])>1 else None,fmt="#,##0.000",align="right",borders="R"+edges)
                    put(r,c+5,g["node"] if j==0 else None,borders="LR"+edges)
                    put(r,c+6,fmt=result_format,borders="LR"+edges)
                    previous_layer,previous_segment = seg["layer"],sr
                    r += 1
                if len(node["pieces"])==1:
                    expr = sheet.ref(anchors[node["pieces"][0][0]["row"]],c+2)*sheet.ref(first,c+3)
                else:
                    expr = fn("SUM",*[sheet.ref(anchors[seg["row"]],c+2)*sheet.ref(first+j,c+4)
                                      for j,(seg,_) in enumerate(node["pieces"])])
                sheet.set_formula(first,c+6,expr)
                if kind=="k": node_spans.append((first,r,c,g["node"]))
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

    source.section("周面の幅の照合", ["KG","節点","負担全幅 (m)","内訳幅合計 (m)","幅差 (m)",
                                    "有効幅 (m)","有効内訳合計 (m)","有効幅差 (m)","周面の扱い"])
    reasons = {row["node"]:row["reason"] for row in detail["excluded"]}
    for block in blocks:
        for node in block:
            g,gr = node["geometry"],node["row"]
            r = source.add([g["group"],g["node"],source.ref(gr,10),None,None,source.ref(gr,11),None,None,
                            reasons.get(g["node"],"周面支点に採用")],formats={i:LENGTH for i in range(2,8)})
            source.set_formula(r,3,length(fn("SUM",*[source.ref(pr,6) for _,pr in node["pieces"]])))
            source.set_formula(r,4,length(source.ref(r,3)-source.ref(r,2)))
            source.set_formula(r,6,length(fn("SUM",0,*[source.ref(pr,6) for seg,pr in node["pieces"] if seg["active"]])))
            source.set_formula(r,7,length(source.ref(r,6)-source.ref(r,5)))
            source.checks.extend(((r,4),(r,7)))

    source.section("周面の採用照合", ["KG","列","節点（ばね明細）","支点項目（支持力明細）","K主表値 (kN/m)",
                                    "K採用再計算","K固定実入力","K差","F主表値 (kN)","F採用再計算","F固定実入力","F差"])
    for row in detail["nodes"]:
        ident,node = row["id"],row["node"]
        r = source.add([row["group"],row["column"],Cell(str(node),style="link",link=links[ident]),
                        Cell(str(row["output"]["item"]),style="link",link=force_links[ident]),main_refs["k",node],None,
                        numeric(fixed[ident,4]["after"]),None,main_refs["f",node],None,numeric(fixed[ident,5]["after"]),None],
                       styles={6:"actual",10:"actual"})
        for c,field,digits in ((5,4,k_digits),(9,5,f_digits)):
            source.set_formula(r,c,fn("ROUND",source.ref(r,c-1),digits))
            source.set_formula(r,c+2,source.ref(r,c)-source.ref(r,c+1))
            source.rows[r][c].number_format = '#,##0'+('.'+'0'*digits if digits else '')
            source.checks.append((r,c+2))
            book.expected.append((source.name,r,c,fixed[ident,field]["after"],ident))
    for title,offsets in (("周面ばね実入力 (kN/m)",[4,7,10,11,12,13]),("周面制限値実入力 (kN)",[5,6,8,9])):
        source.section(title,["KG","列","節点","支点項目",*[fixed[detail["nodes"][0]["id"],i]["label"] for i in offsets]])
        for row in detail["nodes"]:
            source.add([row["group"],row["column"],row["node"],row["output"]["item"],
                        *[numeric(fixed[row["id"],i]["after"]) for i in offsets]],styles={i:"actual" for i in range(4,4+len(offsets))})
    source.section("周面の正確な計算記録", ["節点ごとの丸め前値"])
    for row in detail["nodes"]:
        source.note(f"KG{row['group']} 節点{row['node']} K: {row['raw_k1_kN_per_m']} / F: {row['raw_fy_kN']}")


def build_tip(book, sheet, source, rows, fixed, src, length, links, force_links):
    """先端のK/Fを3列の上下2表にし、幾何・固定実入力・照合を入力根拠へ置く。"""
    sheet.layout, sheet.freeze, sheet.print_scale = "tip", (0,0), 100
    sheet.widths = [9,24.375,24.375]
    line_height, header_height = 18.75, 30.75
    # A4縦の上下余白を除いた高さ。プリンタの端数差を4pt確保する。
    capacity = (297/25.4*72-2*0.75*72)*100/sheet.print_scale-4
    heading_height = line_height*2+header_height
    used = 0
    positions = {}
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
        r = add([cell(title+("（続き）" if continued else ""),style="tip_title",align="left"),cell(),cell()])
        if not continued: sheet.sections.append((title,r,0))
        add([cell("節点番号",style="tip_header",borders="LRTB"),
             cell(unit_title,style="tip_header",borders="LRTB"),cell()],header_height)
        add([cell(),*[cell(v,style="tip_header",borders="LRTB") for v in labels]])
        sheet.range_merges[r+1,0] = (r+2,0)
        sheet.column_merges[r+1,1] = 2

    tables = (("杭先端のばね定数","杭先端の鉛直地盤ばね定数\nKtv(kN/m)",
               ("短期（第１勾配）","短期（第２勾配）"),quantities[:2],links),
              ("先端支持力","杭先端の鉛直地盤支持力\n(kN)",
               ("降伏点","終局点"),quantities[2:],force_links))
    for index,(title,unit_title,labels,items,target_links) in enumerate(tables):
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
            values = [cell(row["node"],align="right",borders="LRTB")]
            for _label,key,_field,line,_unit in items:
                values.append(cell(src("tip",row[line],row["source_fields"][key]),borders="LRTB",fmt=formats[key]))
            r = add(values)
            target_links[ident] = (sheet.name,r,0)
            for c,(_label,key,field,_line,_unit) in enumerate(items,1):
                positions[ident,key] = (r,c)
                book.expected.append((sheet.name,r,c,fixed[ident,field]["after"],ident))
    sheet.print_last_row = len(sheet.rows)-1

    source.section("先端の杭頭・最深節点と杭長",["KG","SDC列","杭頭節点","先端節点","x (m)",
                   "杭頭y (m)","先端y (m)","座標差 (m)","SDC杭長 (m)","杭長差 (m)","支点項目","NDU行"])
    for row in rows:
        g = row["geometry"]
        r = source.add([row["group"],row["column"],g["head_node"],row["node"],row["x_m"],g["origin_y_m"],
                        row["y_m"],None,row["pile_length_m"],None,row["output"]["item"],row["output"]["line"]],
                       styles={i:"source" for i in (4,5,6,8)},formats={i:LENGTH for i in range(4,10)})
        source.set_formula(r,7,length(source.ref(r,6)-source.ref(r,5)))
        source.set_formula(r,9,length(source.ref(r,7)-source.ref(r,8)))
        source.checks.append((r,9))

    source.section("先端の採用照合",["KG","SDC列","先端節点","支点項目","量（主表へ）","単位",
                   "主表値","固定実入力","差","SDC行","CSV欄","NDU欄"])
    for row in rows:
        ident = row["id"]
        for label,key,field,line,unit in quantities:
            rr,cc = positions[ident,key]
            r = source.add([row["group"],row["column"],row["node"],row["output"]["item"],
                            Cell(label,style="link",link=(sheet.name,rr,cc)),unit,sheet.ref(rr,cc),
                            numeric(fixed[ident,field]["after"]),None,row[line],row["source_fields"][key],field],
                           styles={7:"actual"},formats={i:formats[key] for i in (6,7)})
            source.set_formula(r,8,source.ref(r,6)-source.ref(r,7))
            source.checks.append((r,8))
    for title,offsets in (("先端のばね実入力 (kN/m)",[4,7,10,11,12,13]),("先端の制限値実入力 (kN)",[5,6,8,9])):
        source.section(title,["KG","SDC列","節点","支点項目",*[fixed[rows[0]["id"],i]["label"] for i in offsets]])
        for row in rows:
            source.add([row["group"],row["column"],row["node"],row["output"]["item"],
                        *[numeric(fixed[row["id"],i]["after"]) for i in offsets]],styles={i:"actual" for i in range(4,4+len(offsets))})
    source.section("先端の正確な十進記録",["KG","先端節点","量","SDC原値の正確な文字列（追加丸めなし）"])
    for row in rows:
        for label,key,_field,_line,_unit in quantities:
            source.add([row["group"],row["node"],label,str(row[key])])
    source.note("先端はSDCの直角方向・短期K1/K2・地震時押込みFy/Fuをそのまま転記。杭長・本数の乗算、追加丸め、周面抵抗の加算は行いません。")
    source.note("先端支点の第4～13欄は K1,Fy,空欄,K2,Fu,空欄,K2,K1,K2,K2。負側制限値F1−/F2−は空欄です。")


def build(report):
    record, config = report["calculation"], report["configuration"]
    summary = Sheet("変換結果")
    sheets = {op:Sheet(SHEET_NAMES[op]) for op in SHEET_NAMES if op in report["details"]}
    source_sheet = Sheet("入力根拠")
    force_sheet = Sheet(SHAFT_FORCE_SHEET) if "shaft" in sheets else None
    main_sheets = []
    for op,sheet in sheets.items():
        main_sheets.append(sheet)
        if op=="shaft": main_sheets.append(force_sheet)
    book = ReportBook([summary,*main_sheets,source_sheet])
    state = "モデル保存と同一実行" if report.get("mode") == "saved" else "計算確認・モデル未保存"
    for sheet in book.sheets:
        if sheet.name in (*SHEET_NAMES.values(),SHAFT_FORCE_SHEET): continue
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
    force_links = {}
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
    if "shaft" in sheets:
        build_shaft(book,sheets["shaft"],force_sheet,source_sheet,report["details"]["shaft"],
                    fixed,src,length,detail_links,force_links,config["shaft_k_decimals"],config["shaft_force_decimals"])
    if "tip" in sheets:
        build_tip(book,sheets["tip"],source_sheet,report["details"]["tip"]["nodes"],
                  fixed,src,length,detail_links,force_links)

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
                link = force_links.get(t['id'],detail_links[t['id']]) if offsets[0]==5 else detail_links[t['id']]
                summary.add([Cell(link[0],style='link',link=link),t['group'],t['column'],t['number'],t['item'],'Y',
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
