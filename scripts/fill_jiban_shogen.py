"""番号列・奇数偶数列・Ver.5.2.1共有値形式のSDCから、NDUの水平地盤ばね値を入力する。

標準ライブラリのみを使用。CP932の入力を読み、変更箇所以外のバイトを保持する。
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import os
from pathlib import Path
import re
import sys
import tempfile

import sdc_columns as columns
from sdc_columns import InputError


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SDC = ROOT / "test/今町橋りょう4P(右).sdc"
DEFAULT_NDU = ROOT / "test/今町橋りょう4P(C方向･右押し→).ndu"
ZERO = Decimal(0)


@dataclass(frozen=True)
class Layer:
    number: int
    top: Decimal
    bottom: Decimal
    values: dict[int, Decimal]
    source_line: int
    sources: dict[int, columns.SourceColumn] = field(default_factory=dict)
    condition: str = "seismic"


@dataclass(frozen=True)
class Member:
    number: int
    group: int
    column: int
    top: Decimal
    bottom: Decimal


@dataclass(frozen=True)
class Overlap:
    layer: Layer
    length: Decimal


@dataclass
class Ndu:
    raw: bytes
    lines: list[bytes]
    records: dict[str, tuple[int, list[str]]]

    def fields(self, key: str, minimum: int) -> list[str]:
        if key not in self.records:
            raise InputError(f"NDUに {key} がありません。")
        fields = self.records[key][1]
        if len(fields) < minimum:
            raise InputError(f"{key}: フィールドが {minimum} 個以上必要です。")
        return fields


def number(text: str, label: str) -> Decimal:
    try:
        result = Decimal(text.strip())
    except InvalidOperation as exc:
        raise InputError(f"{label}: 数値ではありません: {text!r}") from exc
    if not result.is_finite():
        raise InputError(f"{label}: 有限の数値が必要です。")
    return result


def integer(text: str, label: str) -> int:
    result = number(text, label)
    if result != result.to_integral_value() or result <= 0:
        raise InputError(f"{label}: 正の整数が必要です。")
    return int(result)


def parse_sdc(raw: bytes, sdc_direction: str = columns.DEFAULT_DIRECTION) -> list[Layer]:
    """選択方向の水平地盤ばね表を、行番号ではなく見出しで特定する。"""
    lines = raw.decode("cp932").splitlines()
    section, end = columns.direction_range(lines, sdc_direction)
    direction_label = columns.DIRECTIONS[sdc_direction]
    table_titles = {
        "seismic": ("b）水平地盤ばね値",),
        "liquefaction": ("b）設計水平地盤ばね値",),
    }
    hits = [(i, condition) for i in range(section + 1, end)
            for condition, titles in table_titles.items() if lines[i].strip() in titles]
    if len(hits) != 1:
        raise InputError(f"SDCの{direction_label}に既知の水平地盤ばね表が1個必要です。")
    table, condition = hits[0]
    if table + 2 >= end or not lines[table + 1].startswith("層番,層厚(m),"):
        raise InputError("水平地盤ばね表の層番・層厚(m)の見出しを確認してください。")
    header = [field.strip() for field in lines[table + 2].split(",")]
    count = columns.pile_count(lines, section + 1, end)
    legacy_header = ["", "", "長期", "短期(線形解析)", "短期(非線形解析)"]
    if header == legacy_header:
        if condition != "seismic":
            raise InputError("Ver.5.2.1共有値形式は地震時の水平ばね表だけに対応しています。")
        columns.require_legacy_shared_version(lines, f"SDC {table+3}行の水平ばね表")
        layout = columns.shared_layout(count, header[4], context=f"SDC {table+3}行の水平ばね表")
        positions = [4]
        indices = {col: 4 for col in layout.indices}
        sources = layout.sources(4, interpretation=columns.LEGACY_SHARED_INTERPRETATION)
    else:
        prefix = {"seismic": "短期(非線形)-", "liquefaction": "液状化時-"}[condition]
        positions = [i for i, label in enumerate(header) if label.startswith(prefix)]
        layout = columns.resolve([header[i][len(prefix):] for i in positions], count,
                                 context=f"SDC {table+3}行の水平ばね表")
        indices = {col: positions[index] for col, index in layout.indices.items()}
        sources = {col: columns.SourceColumn(index+1, header[index], layout.kind)
                   for col, index in indices.items()}
    layers: list[Layer] = []
    depth = ZERO
    for i in range(table + 3, end):
        if not lines[i].strip():
            break
        fields = [field.strip() for field in lines[i].split(",")]
        if len(fields) != len(header):
            raise InputError(f"SDC {i + 1}行目: 見出しとデータの列数が一致しません。")
        layer_number = integer(fields[0], f"SDC {i + 1}行目の層番")
        if layer_number != len(layers) + 1:
            raise InputError(f"SDC {i + 1}行目: 層番は1から順番に並べてください。")
        thickness = number(fields[1], f"第{layer_number}層の層厚")
        if thickness <= 0:
            raise InputError(f"第{layer_number}層の層厚は正の値が必要です。")
        values = {column: number(fields[index], f"第{layer_number}層・{column}列目")
                  for column, index in indices.items()}
        raw_values = [number(fields[index], f"第{layer_number}層・{header[index]}") for index in positions]
        if any(value < 0 for value in raw_values):
            raise InputError(f"第{layer_number}層: 負のばね値があります。")
        layers.append(Layer(layer_number, depth, depth + thickness, values, i + 1, sources, condition))
        depth += thickness
    if not layers:
        raise InputError("水平地盤ばね表に層データがありません。")
    return layers


def parse_ndu(raw: bytes) -> Ndu:
    lines = raw.splitlines(keepends=True)
    records: dict[str, tuple[int, list[str]]] = {}
    for i, line in enumerate(lines):
        match = re.match(rb"((?:KGInfo|ElementInfo|JointXY|JibanShogenInfo)\d+)=(.*)", line.rstrip(b"\r\n"))
        if not match:
            continue
        key = match[1].decode("ascii")
        if key in records:
            raise InputError(f"NDUのキー {key} が重複しています。")
        records[key] = (i, [field.strip() for field in match[2].decode("cp932").split(",")])
    return Ndu(raw, lines, records)


def collect_members(ndu: Ndu, groups: dict[int, int]) -> list[Member]:
    """鉛直杭の各グループ上端を深さ0とする。yは下向きに増加する。"""
    members: list[Member] = []
    used: set[int] = set()
    for group, column in groups.items():
        kg = ndu.fields(f"KGInfo{group}", 3)
        start = integer(kg[1], f"KGInfo{group}の開始部材")
        finish = integer(kg[2], f"KGInfo{group}の終了部材")
        if start > finish:
            raise InputError(f"KGInfo{group}: 開始・終了部材の順序が逆です。")
        spans: list[tuple[Decimal, Decimal, int]] = []
        x_positions: set[Decimal] = set()
        for member in range(start, finish + 1):
            if member in used:
                raise InputError(f"部材{member}が複数のKGInfoに含まれています。")
            used.add(member)
            element = ndu.fields(f"ElementInfo{member}", 6)
            joints = [integer(field, f"部材{member}の節点番号") for field in element[4:6]]
            xy = [[number(v, f"節点{joint}の座標") for v in ndu.fields(f"JointXY{joint}", 2)[:2]]
                  for joint in joints]
            if xy[0][0] != xy[1][0]:
                raise InputError(f"部材{member}は鉛直ではありません。このスクリプトは鉛直杭用です。")
            top, bottom = sorted([xy[0][1], xy[1][1]])
            if top == bottom:
                raise InputError(f"部材{member}の長さが0です。")
            ndu.fields(f"JibanShogenInfo{member}", 7)
            x_positions.add(xy[0][0])
            spans.append((top, bottom, member))
        if len(x_positions) != 1:
            raise InputError(f"KGInfo{group}内の杭のx座標が一致しません。")
        spans.sort()
        origin = spans[0][0]
        previous_bottom = origin
        for top, bottom, member in spans:
            if top != previous_bottom:
                raise InputError(f"KGInfo{group}: 部材{member}の上端に隙間または重なりがあります。")
            members.append(Member(member, group, column, top - origin, bottom - origin))
            previous_bottom = bottom
    return members


def find_overlaps(member: Member, layers: list[Layer]) -> list[Overlap]:
    overlaps: list[Overlap] = []
    for layer in layers:
        if member.column not in layer.values:
            raise InputError(f"SDCに{columns.CONDITIONS[layer.condition]}-{member.column}列目がありません。")
        length = min(member.bottom, layer.bottom) - max(member.top, layer.top)
        if length > 0:
            overlaps.append(Overlap(layer, length))
    if sum((item.length for item in overlaps), ZERO) != member.bottom - member.top:
        raise InputError(f"部材{member.number}の深さ{member.top}～{member.bottom}mをSDCの層が覆っていません。")
    return overlaps


def format_number(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def render_ndu(ndu: Ndu, updates: dict[int, Decimal]) -> bytes:
    """JibanShogenInfoの第2フィールドだけを置換し、その他はバイト単位で保持。"""
    lines = list(ndu.lines)
    for member, value in updates.items():
        key = f"JibanShogenInfo{member}"
        ndu.fields(key, 7)
        index = ndu.records[key][0]
        prefix, body = lines[index].split(b"=", 1)
        fields = body.split(b",")
        original = fields[1]
        token = format_number(value).encode("ascii")
        if original.strip():
            leading = original[:len(original) - len(original.lstrip())]
            trailing = original[len(original.rstrip()):]
            fields[1] = leading + token + trailing
        else:
            fields[1] = token
        lines[index] = prefix + b"=" + b",".join(fields)
    return b"".join(lines)


def select_value(member: Member, overlaps: list[Overlap], policy: str) -> Decimal | None:
    """境界をまたぐ場合は、利用者が指定した規則だけを適用する。"""
    if len(overlaps) == 1:
        return overlaps[0].layer.values[member.column]
    if policy == "skip":
        return None
    if policy == "length-weighted":
        total = sum((o.length * o.layer.values[member.column] for o in overlaps), ZERO)
        return (total / (member.bottom - member.top)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    if policy == "midpoint":
        midpoint = (member.top + member.bottom) / 2
        # 中央が境界と一致した場合は下側の層を選ぶ。
        return next(o.layer.values[member.column] for o in overlaps if o.layer.top <= midpoint < o.layer.bottom)
    detail = ", ".join(f"第{o.layer.number}層 {o.length}m" for o in overlaps)
    raise InputError(
        f"部材{member.number}が層境界をまたぎます（{detail}）。"
        "--cross-layer で length-weighted / midpoint / skip のいずれかを指定してください。"
    )


def parse_groups(items: list[str]) -> dict[int, int]:
    groups: dict[int, int] = {}
    for item in items:
        match = re.fullmatch(r"([1-9]\d*):([1-9]\d*)", item)
        if not match:
            raise InputError(f"杭グループは KG番号:SDC杭列番号 の形式で指定してください: {item}")
        group, column = map(int, match.groups())
        if group in groups:
            raise InputError("KG番号が重複しています。")
        groups[group] = column
    return groups


def write_with_backup(path: Path, expected: bytes, result: bytes) -> Path:
    """元ファイルを再確認し、バックアップを新規作成してから一括置換する。"""
    if path.read_bytes() != expected:
        raise InputError("読み込み後にNDUが変更されました。再実行してください。")
    backup = path.with_name(path.name + ".bak")
    # 既存バックアップを上書きしない。
    with backup.open("xb") as stream:
        stream.write(expected)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
            temp_path = Path(stream.name)
            stream.write(result)
            stream.flush()
            os.fsync(stream.fileno())
        if path.read_bytes() != expected:
            raise InputError("保存前にNDUが変更されました。書き込みを中止しました。")
        os.replace(temp_path, path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()
    return backup


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sdc", type=Path, default=DEFAULT_SDC, help="参照SDC（既定: test内の右基礎）")
    parser.add_argument("--ndu", type=Path, default=DEFAULT_NDU, help="入力先NDU（既定: test内）")
    parser.add_argument("--sdc-direction", choices=columns.DIRECTIONS, default=columns.DEFAULT_DIRECTION,
                        help="SDC参照方向（longitudinal=橋軸、transverse=直角。既定: transverse）")
    parser.add_argument("--groups", nargs="+", default=["4:1", "5:2", "6:3"], metavar="KG:列", help="KG番号:SDC杭列番号（既定: 4:1 5:2 6:3）")
    parser.add_argument("--cross-layer", choices=["error", "length-weighted", "midpoint", "skip"], default="length-weighted",
                        help="層境界: length-weighted=長さ加重平均・整数四捨五入、error=中止、midpoint=中央の層、skip=保留（既定: length-weighted）")
    parser.add_argument("--write", action="store_true", help="NDUを更新する（省略時は結果表示のみ）。元データを.ndu.bakへ保存")
    parser.add_argument("--excel-report", type=Path, help="計算過程の.xlsx（モデル保存省略時は確認帳票）")
    args = parser.parse_args(argv)
    if args.excel_report:
        from excel_cli import run
        return run("horizontal", args)
    try:
        groups = parse_groups(args.groups)
        layers = parse_sdc(args.sdc.read_bytes(), args.sdc_direction)
        ndu = parse_ndu(args.ndu.read_bytes())
        members = collect_members(ndu, groups)
        rows = []
        updates: dict[int, Decimal] = {}
        for member in members:
            overlaps = find_overlaps(member, layers)
            value = select_value(member, overlaps, args.cross_layer)
            previous = ndu.fields(f"JibanShogenInfo{member.number}", 7)[1]
            if value is not None:
                updates[member.number] = value
            rows.append((member, overlaps, previous, value))
        result = render_ndu(ndu, updates)
        changed = sum(a != b for a, b in zip(ndu.lines, result.splitlines(keepends=True)))
        print(f"SDC: {args.sdc}")
        print(f"NDU: {args.ndu}")
        print(f"参照: {columns.DIRECTIONS[args.sdc_direction]} / {columns.CONDITIONS[layers[0].condition]}、境界処理: {args.cross_layer}")
        print("対象: " + ", ".join(f"KGInfo{group}→{column}列目" for group, column in groups.items()))
        for member, overlaps, previous, value in rows:
            sources = ", ".join(f"SDC {o.layer.source_line}行:層{o.layer.number}×{format_number(o.length)}m" for o in overlaps)
            target = "保留" if value is None else format_number(value)
            print(f"部材{member.number} 深さ{member.top:.3f}～{member.bottom:.3f}m: {previous or '(空欄)'} → {target} [{sources}]")
        skipped = len(members) - len(updates)
        print(f"対象{len(members)}部材 / 計算{len(updates)} / 保留{skipped} / 変更{changed}")
        if not args.write:
            print("確認表示のみ。--write を付けるとNDUを更新します。")
        elif result == ndu.raw:
            print("変更はありません。NDUは既に同じ値です。")
        else:
            backup = write_with_backup(args.ndu, ndu.raw, result)
            print(f"更新しました。バックアップ: {backup}")
        return 0
    except (InputError, OSError, UnicodeError) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
