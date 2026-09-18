"""有効抵抗土圧力を層内で線形補間し、NDUの第3・第4フィールドを計算する。

元NDUは変更せず、--output に指定した別ファイルへ出力する。
両端とも小数第1位に四捨五入する。層をまたぐ部材は分布の積分平均を上下端に入力する。
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import re
import sys

import fill_jiban_shogen as base
import sdc_columns


@dataclass(frozen=True)
class PressureLayer:
    number: int
    top: Decimal
    bottom: Decimal
    values: dict[int, tuple[Decimal, Decimal]]
    source_line: int
    sources: dict[int, tuple[sdc_columns.SourceColumn, sdc_columns.SourceColumn]] = field(default_factory=dict)

    def at(self, column: int, depth: Decimal) -> Decimal:
        if not self.top <= depth <= self.bottom:
            raise base.InputError(f"第{self.number}層の外側へは補外しません: {depth}m")
        if column not in self.values:
            raise base.InputError(f"土圧表に{column}列目がありません。")
        upper, lower = self.values[column]
        return upper + (lower - upper) * (depth - self.top) / (self.bottom - self.top)


@dataclass(frozen=True)
class Piece:
    layer: PressureLayer
    top: Decimal
    bottom: Decimal
    upper: Decimal
    lower: Decimal


def parse_pressure_sdc(raw: bytes, sdc_direction: str = sdc_columns.DEFAULT_DIRECTION,
                       pressure_case: str = sdc_columns.DEFAULT_PRESSURE_CASE) -> list[PressureLayer]:
    lines = [line.strip() for line in raw.decode("cp932").splitlines()]
    direction, end = sdc_columns.direction_range(lines, sdc_direction)
    direction_label = sdc_columns.DIRECTIONS[sdc_direction]
    case_heading = sdc_columns.pressure_case_heading(pressure_case)
    try:
        section = lines.index("c）有効抵抗土圧力", direction + 1, end)
        section_end = next((i for i in range(section + 1, end)
                            if re.match(r"[a-z]）", lines[i])), end)
        case = lines.index(case_heading, section + 1, section_end)
    except ValueError as exc:
        raise base.InputError(f"{direction_label}の有効抵抗土圧力『{case_heading}』の表がありません。") from exc
    if case + 3 >= section_end or lines[case + 1] != "層番,層厚(m),地震時：有効抵抗土圧力(kN/m)":
        raise base.InputError("有効抵抗土圧力の層厚・単位の見出しを確認してください。")
    header = [field.strip() for field in lines[case + 2].split(",")]
    subheader = [field.strip() for field in lines[case + 3].split(",")]
    # 実SDCでは最後の杭列ラベルの後の空欄（末尾カンマ）が省略される。
    if len(header) + 1 == len(subheader):
        header.append("")
    if len(header) != len(subheader) or len(header) < 4 or len(header) % 2:
        raise base.InputError("土圧表の杭列・上側/下側の列数が一致しません。")
    for i in range(2, len(header), 2):
        if header[i + 1] or subheader[i:i + 2] != ["上側", "下側"]:
            raise base.InputError("土圧表は杭列ごとの上側・下側の組が必要です。")
    layout = sdc_columns.resolve(header[2::2], sdc_columns.pile_count(lines, direction+1, end),
                                 pressure=True, context=f"SDC {case+3}行の土圧表")
    columns = {col: 2+2*index for col, index in layout.indices.items()}
    sources = {col: tuple(sdc_columns.SourceColumn(index+offset+1, header[index]+label, layout.kind)
                         for offset, label in enumerate((" 上側", " 下側")))
               for col, index in columns.items()}
    depth = base.ZERO
    layers = []
    for i in range(case + 4, section_end):
        if not lines[i]:
            break
        fields = [field.strip() for field in lines[i].split(",")]
        if len(fields) != len(header):
            raise base.InputError(f"SDC {i + 1}行目: 土圧表の列数が見出しと一致しません。")
        number = base.integer(fields[0], f"SDC {i + 1}行目の層番")
        thickness = base.number(fields[1], f"第{number}層の層厚")
        if number != len(layers) + 1 or thickness <= 0:
            raise base.InputError("層番は1から連続、層厚は正の値としてください。")
        values = {column: (base.number(fields[index], f"第{number}層の上側土圧"),
                           base.number(fields[index + 1], f"第{number}層の下側土圧"))
                  for column, index in columns.items()}
        raw_values = [base.number(v, f"SDC {i+1}行の土圧") for v in fields[2:]]
        if any(value < 0 for value in raw_values):
            raise base.InputError(f"第{number}層に負の土圧があります。")
        layers.append(PressureLayer(number, depth, depth + thickness, values, i + 1, sources))
        depth += thickness
    if not layers:
        raise base.InputError("土圧表に層データがありません。")
    return layers


def collect_pieces(member: base.Member, column: int, layers: list[PressureLayer]) -> list[Piece]:
    pieces = []
    for layer in layers:
        top = max(member.top, layer.top)
        bottom = min(member.bottom, layer.bottom)
        if bottom > top:
            pieces.append(Piece(layer, top, bottom, layer.at(column, top), layer.at(column, bottom)))
    if sum((p.bottom - p.top for p in pieces), base.ZERO) != member.bottom - member.top:
        raise base.InputError(f"部材{member.number}の全区間をSDCの土圧層が覆っていません。")
    return pieces


def calculate_pair(member: base.Member, pieces: list[Piece], policy: str) -> tuple[Decimal, Decimal]:
    if not pieces:
        raise base.InputError(f"部材{member.number}に対応する層がありません。")
    if len(pieces) == 1 or policy == "endpoints":
        return pieces[0].upper, pieces[-1].lower
    if policy == "integral-average":
        area = sum(((p.upper + p.lower) / 2 * (p.bottom - p.top) for p in pieces), base.ZERO)
        mean = area / (member.bottom - member.top)
        return mean, mean
    raise base.InputError(
        f"部材{member.number}が層境界をまたぎます。--cross-layer integral-average または endpoints を指定してください。"
    )


def render_pressure(ndu: base.Ndu, updates: dict[int, tuple[Decimal, Decimal]]) -> bytes:
    lines = list(ndu.lines)
    for member, pair in updates.items():
        key = f"JibanShogenInfo{member}"
        ndu.fields(key, 7)
        index = ndu.records[key][0]
        prefix, body = lines[index].split(b"=", 1)
        fields = body.split(b",")
        for field, value in zip((2, 3), pair):
            old = fields[field]
            token = base.format_number(value).encode("ascii")
            if old.strip():
                token = old[:len(old) - len(old.lstrip())] + token + old[len(old.rstrip()):]
            fields[field] = token
        lines[index] = prefix + b"=" + b",".join(fields)
    return b"".join(lines)


def make_plan(ndu: base.Ndu, layers: list[PressureLayer], groups: dict[int, int], direction: str,
              decimals: int, policy: str, reference: base.Ndu | None = None) -> tuple[dict, dict]:
    if direction not in ("direct", "right", "left"):
        raise base.InputError("土圧の列指定は direct / right / left を指定してください。")
    members = base.collect_members(ndu, groups)
    column_count = len(layers[0].values)
    quantum = Decimal(1).scaleb(-decimals)
    updates = {}
    rows = []
    for member in members:
        if member.column > column_count:
            raise base.InputError(f"モデル列{member.column}がSDCの列数{column_count}を超えています。")
        # GUIのdirectでは手入力・自動設定したSDC列を再反転しない。
        # right/leftは既存CLIのモデル列指定との互換用。
        column = column_count + 1 - member.column if direction == "right" else member.column
        pieces = collect_pieces(member, column, layers)
        raw = calculate_pair(member, pieces, policy)
        pair = tuple(value.quantize(quantum, rounding=ROUND_HALF_UP) for value in raw)
        updates[member.number] = pair
        key = f"JibanShogenInfo{member.number}"
        row = {
            "member": member.number, "group": member.group, "model_column": member.column,
            "pressure_column": column, "top_m": str(member.top), "bottom_m": str(member.bottom),
            "pieces": [{"layer": p.layer.number, "sdc_line": p.layer.source_line,
                        "top_m": str(p.top), "bottom_m": str(p.bottom),
                        "upper_pressure": str(p.upper), "lower_pressure": str(p.lower)} for p in pieces],
            "method": "linear-endpoints" if len(pieces) == 1 else policy,
            "unrounded": list(map(str, raw)), "calculated": list(map(base.format_number, pair)),
            "current": ndu.fields(key, 7)[2:4],
        }
        if reference is not None:
            values = reference.fields(key, 7)[2:4]
            row["reference"] = values
            if all(values):
                delta = [value - base.number(old, f"照合用{key}") for value, old in zip(pair, values)]
                row["difference"] = list(map(base.format_number, delta))
                row["matches_reference"] = all(value == 0 for value in delta)
            else:
                row["difference"] = None
                row["matches_reference"] = None
        rows.append(row)
    summary = {"members": len(rows), "crossing_members": sum(len(row["pieces"]) > 1 for row in rows)}
    if reference is not None:
        summary.update({"reference_matching_pairs": sum(row["matches_reference"] is True for row in rows),
                        "reference_differing_pairs": sum(row["matches_reference"] is False for row in rows),
                        "reference_missing_pairs": sum(row["matches_reference"] is None for row in rows)})
    return updates, {"summary": summary, "members": rows}


def save_new(path: Path, data: bytes) -> None:
    if path.exists():
        if path.read_bytes() == data:
            return
        raise base.InputError(f"既存ファイルは上書きしません。別の出力名を指定してください: {path}")
    with path.open("xb") as stream:
        stream.write(data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdc", type=Path, default=base.DEFAULT_SDC)
    parser.add_argument("--ndu", type=Path, default=base.DEFAULT_NDU)
    parser.add_argument("--sdc-direction", choices=sdc_columns.DIRECTIONS, default=sdc_columns.DEFAULT_DIRECTION,
                        help="SDC参照方向（longitudinal=橋軸、transverse=直角。既定: transverse）")
    parser.add_argument("--pressure-case", choices=sdc_columns.PRESSURE_CASES,
                        default=sdc_columns.DEFAULT_PRESSURE_CASE,
                        help="土圧区分（non-response=応答変位法以外、response=応答変位法。既定: non-response）")
    parser.add_argument("--groups", nargs="+", default=["4:1", "5:2", "6:3"], metavar="KG:モデル列")
    parser.add_argument("--push-direction", choices=["right", "left", "direct"], default="right",
                        help="direct=指定SDC列をそのまま使用。right/left=従来のモデル列指定（既定:right）")
    parser.add_argument("--decimals", type=int, choices=range(7), default=1, help="最終値の小数桁数（既定: 1）")
    parser.add_argument("--cross-layer", choices=["error", "integral-average", "endpoints"], default="integral-average",
                        help="層境界の処理（既定: integral-average、分布の積分平均を上下端に入力）")
    parser.add_argument("--reference", type=Path, help="既存値と照合するNDU。入力値の算出には使わない")
    parser.add_argument("--output", type=Path, help="計算値を入力したNDUの新規出力先。省略時は表示のみ")
    parser.add_argument("--report", type=Path, help="計算根拠・照合差分のJSON出力先")
    parser.add_argument("--excel-report", type=Path, help="計算過程の.xlsx（モデル保存省略時は確認帳票）")
    args = parser.parse_args(argv)
    if args.excel_report:
        from excel_cli import run
        return run("pressure", args)
    try:
        inputs = [p.resolve() for p in (args.sdc, args.ndu, args.reference) if p is not None]
        outputs = [p.resolve() for p in (args.output, args.report) if p is not None]
        if len(set(outputs)) != len(outputs) or any(path in inputs for path in outputs):
            raise base.InputError("出力先は入力・照合ファイルと別々のパスを指定してください。")
        sdc_raw = args.sdc.read_bytes()
        ndu = base.parse_ndu(args.ndu.read_bytes())
        reference = base.parse_ndu(args.reference.read_bytes()) if args.reference else None
        groups = base.parse_groups(args.groups)
        updates, report = make_plan(ndu, parse_pressure_sdc(sdc_raw, args.sdc_direction, args.pressure_case), groups, args.push_direction,
                                    args.decimals, args.cross_layer, reference)
        report["configuration"] = {"push_direction": args.push_direction, "groups": groups,
                                   "sdc_direction": args.sdc_direction,
                                   "sdc_direction_label": sdc_columns.DIRECTIONS[args.sdc_direction],
                                   "pressure_case": args.pressure_case,
                                   "pressure_case_label": sdc_columns.PRESSURE_CASES[args.pressure_case],
                                   "decimals": args.decimals, "rounding": "ROUND_HALF_UP", "cross_layer": args.cross_layer}
        report["sources"] = {"sdc": {"path": str(args.sdc.resolve()), "sha256": hashlib.sha256(sdc_raw).hexdigest()},
                             "ndu": {"path": str(args.ndu.resolve()), "sha256": hashlib.sha256(ndu.raw).hexdigest()}}
        if reference is not None:
            report["sources"]["reference"] = {"path": str(args.reference.resolve()), "sha256": hashlib.sha256(reference.raw).hexdigest()}
        result = render_pressure(ndu, updates)
        report["output_sha256"] = hashlib.sha256(result).hexdigest()
        report_bytes = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        pending = [(args.output, result), (args.report, report_bytes)]
        for path, data in pending:
            if path is not None and path.exists() and path.read_bytes() != data:
                raise base.InputError(f"既存ファイルは上書きしません。別の出力名を指定してください: {path}")
        for row in report["members"]:
            print(f"部材{row['member']} 土圧{row['pressure_column']}列目 "
                  f"深さ{row['top_m']}～{row['bottom_m']}m: "
                  f"上端{row['calculated'][0]} / 下端{row['calculated'][1]} ({row['method']})")
        print(json.dumps(report["summary"], ensure_ascii=False))
        for path, data in pending:
            if path is not None:
                save_new(path, data)
                print(f"出力: {path}")
        return 0
    except (base.InputError, OSError, UnicodeError) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
