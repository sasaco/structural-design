"""右SDCの杭周面ばね・支持力をNDUのSuppotInfoへ入力する。

既存画面方式: 押込み側K1を正負の全勾配、Fyを正負の両制限値へ設定する。
原本は保持。--output省略は確認表示。出力時は--profile existing-screenを明示する。
杭先端ばねは保持し、周面との合成は行わない。SDC表示値に1.2除算は加えない。
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from decimal import Decimal as D, DecimalException, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import re
import sys

import fill_jiban_shogen as base
import sdc_columns

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_NDU = ROOT / "test/今町橋りょう4P(C方向･右押し→)_土圧入力済み.ndu"
ZERO = D(0)
FIELD_NAMES = ("開始節点", "終点節点", "拘束方向", "K1+", "F1+", "F1-",
               "K2+", "F2+", "F2-", "K3+", "K1-", "K2-", "K3-")
InputError = base.InputError


@dataclass(frozen=True)
class Layer:
    number: int
    top: D
    bottom: D
    active_top: D
    active_bottom: D
    values: dict[int, tuple[D, D]]
    spring_line: int
    force_line: int
    sources: dict[int, tuple[sdc_columns.SourceColumn, sdc_columns.SourceColumn]] = field(default_factory=dict)


@dataclass(frozen=True)
class Profile:
    layers: list[Layer]
    length: D
    exclusion: D
    embedment: D


def num(value: str, label: str) -> D:
    value = base.number(value, label)
    if value < 0:
        raise InputError(f"{label}: 0以上の値が必要です")
    return value


def count(value: str, label: str) -> int:
    result = num(value, label)
    if result != result.to_integral_value():
        raise InputError(f"{label}: 整数が必要です")
    return int(result)


def locate(lines: list[str], text: str, start: int = 0, end: int | None = None) -> int:
    hits = [i for i in range(start, len(lines) if end is None else end) if lines[i] == text]
    if len(hits) != 1:
        raise InputError(f"SDC見出し『{text}』が1個必要です")
    return hits[0]


def read_table(lines: list[str], begin: int, end: int, width: int) -> list[tuple[int, list[D]]]:
    rows = []
    for i in range(begin, end):
        if not lines[i] or lines[i].startswith("※"):
            break
        fields = lines[i].split(",")
        if len(fields) != width:
            raise InputError(f"SDC {i + 1}行: データ列数が見出しと一致しません")
        values = [num(v, f"SDC {i + 1}行") for v in fields]
        if values[0] != len(rows) + 1 or values[1] <= 0:
            raise InputError(f"SDC {i + 1}行: 層番は1から連続、層厚は正の値が必要です")
        rows.append((i + 1, values))
    if not rows:
        raise InputError("SDCに層データがありません")
    return rows


def parse_sdc(raw: bytes) -> Profile:
    """直角方向・押込み表の見出しを検証し、全層厚と有効長を分離する。"""
    lines = [s.strip() for s in raw.decode("cp932").splitlines()]
    direction = locate(lines, "（２）直角方向")
    end = next((i for i in range(direction + 1, len(lines)) if re.match(r"[（(][３-９3-9][）)]", lines[i])), len(lines))
    spring = locate(lines, "d）杭周面の鉛直せん断地盤ばね値", direction, end)
    force = locate(lines, "e）杭周面の支持力", spring + 1, end)
    force_end = next((i for i in range(force + 1, end) if re.match(r"[a-z]）", lines[i])), end)
    kside = locate(lines, "【押込み側】", spring, force)
    kend = locate(lines, "【引抜き側】", kside + 1, force)
    fside = locate(lines, "【押込み側】", force, force_end)
    fend = locate(lines, "【引抜き側】", fside + 1, force_end)
    if kside + 3 >= kend or fside + 3 >= fend:
        raise InputError("SDCの表見出しが不足しています")
    if lines[kside + 1] != "層番,層厚(m),杭周面の鉛直せん断地盤ばね値(kN/m2)":
        raise InputError("SDCのばね表の単位・見出しを確認してください")
    if lines[fside + 1] != "層番,層厚,⊿l(m),地震時：杭周面支持力(kN/m)":
        raise InputError("SDCの支持力表の単位・見出しを確認してください")
    split = lambda i: [s.strip() for s in lines[i].split(",")]
    kh, fh = split(kside + 3), split(fside + 3)
    if (len(fh) - 3) % 2 or len(fh) < 5:
        raise InputError("SDC支持力表の列数が不正です")
    n = (len(fh) - 3) // 2
    cols = fh[3:3+n]
    layout = sdc_columns.resolve(cols, sdc_columns.pile_count(lines, direction+1, end),
                                 ordered=True, context=f"SDC {fside+4}行の周面支持力表")
    if fh != ["", "", ""] + cols * 2 or kh != ["", ""] + cols * 2 + ["⊿l(m)"] + cols * 2:
        raise InputError("SDC周面表: ばね・支持力の杭列見出しが一致しません。")
    k_sources = layout.sources(3+2*n, " 押込み 短期第1勾配 K1")
    f_sources = layout.sources(3, " 押込み 降伏点 Fy")
    sources = {col: (k_sources[col], f_sources[col]) for col in layout.indices}
    if [v for v in split(kside + 2) if v] != ["長期", "短期(使用性・安全性)", "短期(復旧性・地震時-第1勾配)", "短期(復旧性・地震時-第2勾配)"]:
        raise InputError("SDCのばね勾配見出しが不正です")
    if [v for v in split(fside + 2) if v] != ["降伏点(ρgfy考慮)", "終局点(ρgfu考慮)"]:
        raise InputError("SDCの支持力見出しが不正です")
    kr = read_table(lines, kside + 4, kend, len(kh))
    fr = read_table(lines, fside + 4, fend, len(fh))
    if len(kr) != len(fr):
        raise InputError("ばね表と支持力表の層数が一致しません")
    pile_headers = [i for i, s in enumerate(lines) if s.startswith("杭長,突出長,根入れ深さ,")]
    if len(pile_headers) != 1 or pile_headers[0] + 1 >= len(lines):
        raise InputError("SDCの杭長・根入れ深さが見つかりません")
    p = split(pile_headers[0] + 1)
    if len(p) < 3:
        raise InputError("SDCの杭条件が不足しています")
    length, protrusion, embedment = [num(v, "杭条件") for v in p[:3]]
    if length <= 0 or protrusion != 0 or not ZERO <= embedment < length:
        raise InputError("正の杭長・突出長0・杭長未満の根入れ深さが必要です")
    beta_header = locate(lines, "杭列数,奥行き本数,,1/β(m)", direction, spring)
    if beta_header + 2 >= spring:
        raise InputError("1/βの値がありません")
    beta_row = split(beta_header + 2)
    if len(beta_row) != 4 or count(beta_row[0], "杭列数") != len(layout.indices):
        raise InputError("杭列数とSDC表の列数が一致しません")
    exclusion = num(beta_row[-1], "1/β")
    if exclusion >= length - embedment:
        raise InputError("杭周面の有効区間がありません")
    top, layers = ZERO, []
    for (kl, k), (fl, f) in zip(kr, fr):
        bottom = top + f[1]
        u, v = max(top, exclusion), min(bottom, length - embedment)
        active = max(ZERO, v - u)
        if f[2] != active or k[2 + 2 * n] != active:
            raise InputError(f"第{int(f[0])}層: ⊿lと1/β・先端除外範囲が一致しません")
        # d表は先端除外分だけ層厚を短縮する形式を許容。上端除外は層厚から引かない。
        if k[1] not in (f[1], max(ZERO, min(bottom, length - embedment) - top)):
            raise InputError("ばね表と支持力表の層厚が一致しません")
        layers.append(Layer(int(f[0]), top, bottom, u, v,
                            {col: (k[ks.field-1], f[fs.field-1]) for col, (ks, fs) in sources.items()},
                            kl, fl, sources))
        top = bottom
    if top != length:
        raise InputError("SDCの全層厚合計と杭長が一致しません")
    return Profile(layers, length, exclusion, embedment)


def make_plan(ndu: base.Ndu, profile: Profile, groups: dict[int, int], k_digits: int = 0, f_digits: int = 1):
    members = base.collect_members(ndu, groups)
    rows, updates, zeros, tips = [], {}, set(), set()
    seen = set()
    for group, col in groups.items():
        if col not in profile.layers[0].values:
            raise InputError(f"SDCに{col}列目がありません")
        ids = {base.integer(v, "節点番号") for m in members if m.group == group
               for v in ndu.fields(f"ElementInfo{m.number}", 6)[4:6]}
        nodes = sorted((base.number(ndu.fields(f"JointXY{n}", 2)[1], "y座標"), n) for n in ids)
        if seen & ids:
            raise InputError("杭グループ間で節点が重複しています")
        seen.update(ids)
        if len(nodes) != sum(m.group == group for m in members) + 1 or len({y for y, _ in nodes}) != len(nodes):
            raise InputError("杭の節点接続が一本の連続した鉛直線になっていません")
        origin = nodes[0][0]
        if nodes[-1][0] - origin != profile.length:
            raise InputError(f"KGInfo{group}: 杭長とSDC層厚合計が一致しません")
        for index, (y, node) in enumerate(nodes):
            if index == len(nodes) - 1:
                tip_top = (nodes[index - 1][0] + y) / 2 - origin
                if any(min(profile.length, l.active_bottom) > max(tip_top, l.active_top)
                       and any(v > 0 for v in l.values[col]) for l in profile.layers):
                    raise InputError(f"節点{node}: 杭先端節点の負担区間に押込み側の周面抵抗があります。先端ばねとの合成には対応していません")
                tips.add(node)
                continue
            a = (nodes[max(0, index - 1)][0] + y) / 2 - origin
            b = (y + nodes[index + 1][0]) / 2 - origin
            k, f, pieces = ZERO, ZERO, []
            for layer in profile.layers:
                u, v = max(a, layer.active_top), min(b, layer.active_bottom)
                if v <= u:
                    continue
                kv, fv = layer.values[col]
                k += kv * (v - u)
                f += fv * (v - u)
                pieces.append({"layer": layer.number, "top_m": u, "bottom_m": v, "length_m": v - u,
                               "k1_kN_per_m2": kv, "fy_kN_per_m": fv,
                               "spring_line": layer.spring_line, "force_line": layer.force_line})
            if k == 0 and f == 0:
                zeros.add(node)
                continue
            rk = k.quantize(D(1).scaleb(-k_digits), rounding=ROUND_HALF_UP)
            rf = f.quantize(D(1).scaleb(-f_digits), rounding=ROUND_HALF_UP)
            if rk <= 0 or rf <= 0:
                raise InputError(f"節点{node}: 集約または丸め後のばね値・支持力が0です。入力を確認してください")
            ks, fs = base.format_number(rk), base.format_number(rf)
            values = [ks, fs, fs, ks, fs, fs, ks, ks, ks, ks]
            updates[node] = values
            rows.append({"group": group, "column": col, "node": node, "top_m": a, "bottom_m": b,
                         "pieces": pieces, "raw_k1_kN_per_m": k, "raw_fy_kN": f,
                         "field4_to_13": values})
    if not updates:
        raise InputError("入力対象の杭周面ばねがありません")
    return updates, zeros, tips, rows


def replace_token(old: bytes, value: str) -> bytes:
    if not old.strip():
        return value.encode("ascii")
    return old[:len(old) - len(old.lstrip())] + value.encode("ascii") + old[len(old.rstrip()):]


def eol(line: bytes) -> bytes:
    return line[len(line.rstrip(b"\r\n")):]


def ndu_supports(raw: bytes):
    lines = raw.splitlines(keepends=True)
    controls, entries = {}, {}
    for i, line in enumerate(lines):
        match = re.fullmatch(rb"(SuppotNum|SuppotRow|ShitenCaseNum|SuppotInfo\d+)=(.*)", line.rstrip(b"\r\n"))
        if not match:
            continue
        key, body = match[1].decode("ascii"), match[2]
        target = entries if key.startswith("SuppotInfo") else controls
        if key in target:
            raise InputError(f"{key}が重複しています")
        target[key] = (i, body)
    if set(controls) != {"SuppotNum", "SuppotRow", "ShitenCaseNum"}:
        raise InputError("SuppotNum/Row/ShitenCaseNumが必要です")
    if count(controls["ShitenCaseNum"][1].decode("ascii"), "ShitenCaseNum") != 0:
        raise InputError("複数の支点ケースには対応していません")
    if any(count(controls[k][1].decode("ascii"), k) != len(entries) for k in ("SuppotNum", "SuppotRow")):
        raise InputError("SuppotNum/Rowと支点レコード数が一致しません")
    if set(entries) != {f"SuppotInfo{i}" for i in range(1, len(entries) + 1)}:
        raise InputError("SuppotInfoの番号は1から連続している必要があります")
    return lines, controls, entries


def render_ndu(raw: bytes, updates: dict[int, list[str]], zeros: set[int]):
    # 追加前に同期する。旧支点数を超えたケース行を新しい支点へ流用しない。
    raw = sync_ndu_support_cases(raw)
    lines, controls, entries = ndu_supports(raw)
    updated, seen, used = {}, set(), set()
    targets = set(updates) | zeros
    for key, (index, body) in entries.items():
        f = [v.strip().decode("ascii") for v in body.split(b",")]
        if len(f) != 13:
            raise InputError(f"{key}: 13フィールドが必要です")
        node, direction = base.integer(f[1], "終点節点"), base.integer(f[2], "拘束方向")
        if direction != 2:
            continue
        if f[0]:
            start = base.integer(f[0], "開始節点")
            if start > node or any(start <= n <= node for n in targets):
                raise InputError(f"{key}: 対象節点と重なる範囲指定には対応していません")
            continue
        if node in seen:
            raise InputError(f"節点{node}のY支点が重複しています")
        seen.add(node)
        if node in zeros:
            raise InputError(f"節点{node}: 抵抗を考慮しない区間に既存Y支点があります。自動削除しません")
        if node in updates:
            parts = body.split(b",")
            for offset, value in enumerate(updates[node], 3):
                parts[offset] = replace_token(parts[offset], value)
            updated[index] = key.encode("ascii") + b"=" + b",".join(parts) + eol(lines[index])
            used.add(node)
    added = [n for n in updates if n not in used]
    anchor = max((index for index, _ in entries.values()), default=controls["ShitenCaseNum"][0])
    newline = eol(lines[anchor]) or next((eol(l) for l in lines if eol(l)), b"\r\n")
    new_lines = [f"SuppotInfo{len(entries) + i}= ,{node},2,".encode("ascii") + ",".join(updates[node]).encode("ascii") + newline
                 for i, node in enumerate(added, 1)]
    if added:
        for key in ("SuppotNum", "SuppotRow"):
            index, old = controls[key]
            updated[index] = key.encode("ascii") + b"=" + replace_token(old, str(len(entries) + len(added))) + eol(lines[index])
    result = []
    for index, line in enumerate(lines):
        result.append(updated.get(index, line))
        if index == anchor and new_lines:
            if not eol(result[-1]):
                result.append(newline)
            result.extend(new_lines)
    return sync_ndu_support_cases(b"".join(result)), {
        "updated": len(used), "added": len(added), "support_count": len(entries) + len(added)}


def sync_ndu_support_cases(raw: bytes) -> bytes:
    """最終支点番号1～NとSuppot_ChokuKisoCaseNoを同期する（支点自体は削除しない）。

    欠落は0で補い、支点数を超える0の行は削除する。既存値・書式は保持する。
    支点の削除・再採番は、呼出し側でケース行も対にして行う必要がある。
    """
    lines, controls, entries = ndu_supports(raw)
    prefix = b"Suppot_ChokuKisoCaseNo"
    cases, removed = {}, set()
    for index, line in enumerate(lines):
        if not line.startswith(prefix):
            continue
        match = re.fullmatch(rb"Suppot_ChokuKisoCaseNo([1-9][0-9]*)=(.*)", line.rstrip(b"\r\n"))
        if not match:
            raise InputError("Suppot_ChokuKisoCaseNoのキー形式が不正です")
        number = int(match[1])
        if number in cases:
            raise InputError(f"Suppot_ChokuKisoCaseNo{number}が重複しています")
        value = count(match[2].decode("ascii"), f"Suppot_ChokuKisoCaseNo{number}")
        cases[number] = index
        if number > len(entries):
            if value != 0:
                raise InputError(f"Suppot_ChokuKisoCaseNo{number}: 対応する支点がない非0のケース行は自動削除しません")
            removed.add(index)
    missing = [n for n in range(1, len(entries) + 1) if n not in cases]
    if not missing and not removed:
        return raw
    # 実NDUの配置を踏襲。ケース行がない場合は直接基礎リンク数の後へ置く。
    anchors = [i for i, l in enumerate(lines) if l.startswith(b"G_intCHOKU_KISO_Link_Num=")]
    anchor = max(cases.values()) if cases else max(anchors) if anchors else max(
        (i for i, _ in entries.values()), default=controls["ShitenCaseNum"][0])
    newline = eol(lines[anchor]) or next((eol(l) for l in lines if eol(l)), b"\r\n")
    result = []
    for index, line in enumerate(lines):
        if index not in removed:
            result.append(line)
        if index == anchor and missing:
            if result and not eol(result[-1]):
                result.append(newline)
            result.extend(prefix + str(n).encode("ascii") + b"=0" + newline for n in missing)
    return b"".join(result)


def same_path(a: Path, b: Path) -> bool:
    return a.resolve() == b.resolve() or (a.exists() and b.exists() and a.samefile(b))


def save_new(path: Path, content: bytes) -> None:
    """確認後に他プロセスが作成した場合も、その内容を再確認する。"""
    try:
        with path.open("xb") as stream:
            stream.write(content)
    except FileExistsError:
        if path.read_bytes() != content:
            raise InputError(f"異なる既存ファイルは上書きしません: {path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdc", type=Path, default=base.DEFAULT_SDC)
    parser.add_argument("--ndu", type=Path, default=DEFAULT_NDU, help="入力NDU")
    parser.add_argument("--groups", nargs="+", default=["4:1", "5:2", "6:3"], metavar="KG:SDC列")
    parser.add_argument("--profile", choices=["existing-screen"], help="既存画面方式を明示選択（出力には必須）")
    parser.add_argument("--k-decimals", type=int, choices=range(7), default=0)
    parser.add_argument("--force-decimals", type=int, choices=range(7), default=1)
    parser.add_argument("--output", type=Path, help="別名NDU。省略時は確認表示")
    parser.add_argument("--report", type=Path, help="計算根拠と出力フィールドのJSON（確認表示時にも出力可）")
    parser.add_argument("--excel-report", type=Path, help="計算過程の.xlsx（モデル保存省略時は確認帳票）")
    args = parser.parse_args(argv)
    if args.excel_report:
        from excel_cli import run
        return run("shaft", args)
    try:
        if args.output and not args.profile:
            raise InputError("出力時は --profile existing-screen を指定してください（押込みK1/Fyを正負の全勾配/制限値へ設定、先端保持）")
        expected_suffix = ".ndu"
        if args.output and args.output.suffix.lower() != expected_suffix:
            raise InputError(f"出力拡張子は{expected_suffix}にしてください")
        if args.report and args.report.suffix.lower() != ".json":
            raise InputError("報告書は.jsonで指定してください")
        inputs = [args.sdc, args.ndu]
        outputs = [p for p in (args.output, args.report) if p]
        if any(same_path(o, i) for o in outputs for i in inputs) or (len(outputs) == 2 and same_path(*outputs)):
            raise InputError("入力・出力・報告書は別のパスを指定してください")
        snapshots = {p: p.read_bytes() for p in inputs}
        ndu = base.parse_ndu(snapshots[args.ndu])
        profile, groups = parse_sdc(snapshots[args.sdc]), base.parse_groups(args.groups)
        updates, zeros, tips, rows = make_plan(ndu, profile, groups, args.k_decimals, args.force_decimals)
        result, summary = render_ndu(snapshots[args.ndu], updates, zeros)
        report = {"configuration": {"profile": "existing-screen", "profile_explicit": bool(args.profile), "groups": groups,
                  "source_side": "compression", "all_stiffness_fields": "K1", "all_limit_fields": "Fy",
                  "capacity_divisor": "1", "tip": "preserve-not-generated", "k_decimals": args.k_decimals,
                  "force_decimals": args.force_decimals, "rounding": "ROUND_HALF_UP", "output_format": expected_suffix},
                  "field_names": list(FIELD_NAMES), "sources": {str(p.resolve()): hashlib.sha256(v).hexdigest() for p, v in snapshots.items()},
                  "summary": summary, "zero_resistance_nodes": sorted(zeros), "tip_nodes_not_updated": sorted(tips),
                  "nodes": rows, "output_sha256": hashlib.sha256(result).hexdigest()}
        report_bytes = (json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n").encode("utf8")
        pending = [(p, data) for p, data in ((args.output, result), (args.report, report_bytes)) if p]
        # 全出力を事前確認し、別内容の既存ファイルは一切上書きしない。
        for path, data in pending:
            if not path.parent.is_dir() or (path.exists() and path.read_bytes() != data):
                raise InputError(f"出力先の親ディレクトリがないか、異なる既存ファイルがあります: {path}")
        if any(p.read_bytes() != raw for p, raw in snapshots.items()):
            raise InputError("計算中に入力が変更されました。再実行してください")
        print("既存画面方式: 押込み側K1→全勾配±、Fy→両制限値±、1.2除算なし、杭先端支点は保持（新規作成なし）")
        for r in rows:
            print(f"節点{r['node']}: K={r['field4_to_13'][0]} kN/m, F={r['field4_to_13'][1]} kN")
        print(json.dumps(summary, ensure_ascii=False))
        for path, data in pending:
            save_new(path, data)
            print(f"出力: {path}")
        return 0
    except (InputError, OSError, UnicodeError, DecimalException) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
