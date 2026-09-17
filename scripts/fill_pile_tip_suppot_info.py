"""右SDCの杭先端ばね・支持力をNDUのSuppotInfoへ入力する。

指定snapモデルの配置: K1±=短期第1勾配、K2±=K3±=短期第2勾配、
F1+=降伏、F2+=終局、負側制限値は空欄。長さ換算・周面抵抗の合成なし。
--output省略は確認表示。出力は別名ファイルに限定し、入力原本を保持する。
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from decimal import Decimal as D, DecimalException
import hashlib
import json
from pathlib import Path
import re
import sys

import fill_jiban_shogen as base
import fill_suppot_info as support

InputError = base.InputError


@dataclass(frozen=True)
class TipValues:
    k1: D
    k2: D
    fy: D
    fu: D

    def fields(self) -> list[str]:
        k1, k2, fy, fu = map(base.format_number, (self.k1, self.k2, self.fy, self.fu))
        # NDUの空欄は空白1文字。ゼロで代用しない。
        return [k1, fy, " ", k2, fu, " ", k2, k1, k2, k2]


@dataclass(frozen=True)
class Profile:
    length: D
    values: dict[int, TipValues]
    spring_line: int
    force_line: int


def read_tip_table(lines: list[str], title: str, labels: list[str], n: int,
                   begin: int, end: int) -> tuple[list[D], int]:
    index = support.locate(lines, title, begin, end)
    if index + 3 >= end:
        raise InputError(f"SDC『{title}』の見出し・値が不足しています")
    split = lambda i: [v.strip() for v in lines[i].split(",")]
    width = len(labels) * n
    groups = split(index + 1)
    expected = [label if j == 0 else "" for label in labels for j in range(n)]
    # 上段の結合見出しでは、最後のグループの空ラベルが省略される。
    if not width - n + 1 <= len(groups) <= width or groups + [""] * (width - len(groups)) != expected:
        raise InputError(f"SDC『{title}』の勾配・支持力見出しが不正です")
    if split(index + 2) != [f"{i}列目" for i in range(1, n + 1)] * len(labels):
        raise InputError(f"SDC『{title}』は1列目から順に並ぶN列目形式が必要です")
    fields = split(index + 3)
    if len(fields) != width:
        raise InputError(f"SDC {index + 4}行: データ列数が見出しと一致しません")
    if index + 4 < end and lines[index + 4] and not lines[index + 4].startswith("※"):
        raise InputError(f"SDC『{title}』の値は1行で指定してください")
    return [support.num(v, f"SDC {index + 4}行") for v in fields], index + 4


def parse_sdc(raw: bytes) -> Profile:
    lines = [line.strip() for line in raw.decode("cp932").splitlines()]
    direction = support.locate(lines, "（２）直角方向")
    end = next((i for i in range(direction + 1, len(lines))
                if re.match(r"[（(][３-９3-9][）)]", lines[i])), len(lines))
    spring = support.locate(lines, "f）杭先端の地盤ばね値", direction, end)
    force = support.locate(lines, "g）杭先端の支持力", spring + 1, end)
    force_end = next((i for i in range(force + 1, end) if re.match(r"[a-z]）", lines[i])), end)
    header = support.locate(lines, "杭列数,奥行き本数,,1/β(m)", direction, spring)
    if header + 2 >= spring:
        raise InputError("SDCの杭列数がありません")
    arrangement = [v.strip() for v in lines[header + 2].split(",")]
    if len(arrangement) != 4:
        raise InputError("SDCの杭配置条件が不正です")
    n = base.integer(arrangement[0], "杭列数")
    vertical_titles = [s for s in ("杭先端の鉛直鉛直ばね値(kN/m)", "杭先端の鉛直ばね値(kN/m)")
                       if s in lines[spring + 1:force]]
    if len(vertical_titles) != 1:
        raise InputError("SDCの杭先端鉛直ばね(kN/m)の見出しが1個必要です")
    k, kl = read_tip_table(lines, vertical_titles[0], ["長期", "短期(第1勾配)", "短期(第2勾配)"],
                           n, spring + 1, force)
    for title in ("杭先端の水平ばね値(kN/m)", "杭先端の回転ばね値(kN/m)"):
        other, _ = read_tip_table(lines, title, ["長期", "短期"], n, spring + 1, force)
        if any(other):
            raise InputError("水平・回転の先端ばねが非ゼロのSDCには対応していません")
    f, fl = read_tip_table(lines, "地震時：杭先端の鉛直地盤支持力(kN)",
                           ["押し込み側(降伏点)", "押し込み側(終局点)"], n, force + 1, force_end)
    pile_headers = [i for i, s in enumerate(lines) if s.startswith("杭長,突出長,根入れ深さ,")]
    if len(pile_headers) != 1 or pile_headers[0] + 1 >= len(lines):
        raise InputError("SDCの杭長・突出長が見つかりません")
    pile = lines[pile_headers[0] + 1].split(",")
    if len(pile) < 3:
        raise InputError("SDCの杭条件が不足しています")
    length, protrusion = [support.num(v, "杭条件") for v in pile[:2]]
    if length <= 0 or protrusion != 0:
        raise InputError("正の杭長・突出長0のSDCが必要です")
    values = {}
    for col in range(1, n + 1):
        v = TipValues(k[n + col - 1], k[2 * n + col - 1], f[col - 1], f[n + col - 1])
        if min(v.k1, v.k2, v.fy, v.fu) <= 0 or v.fu < v.fy:
            raise InputError(f"SDC {col}列目: 正のばね値・支持力、終局点≧降伏点が必要です")
        values[col] = v
    return Profile(length, values, kl, fl)


def make_plan(ndu: base.Ndu, profile: Profile, groups: dict[int, int]):
    members = base.collect_members(ndu, groups)
    updates, rows, seen = {}, [], set()
    for group, col in groups.items():
        if col not in profile.values:
            raise InputError(f"SDCに{col}列目がありません")
        pile_members = [m for m in members if m.group == group]
        ids = {base.integer(v, "節点番号") for m in pile_members
               for v in ndu.fields(f"ElementInfo{m.number}", 6)[4:6]}
        nodes = sorted((base.number(ndu.fields(f"JointXY{node}", 2)[1], "y座標"), node) for node in ids)
        if seen & ids:
            raise InputError("杭グループ間で節点が重複しています")
        seen.update(ids)
        if len(nodes) != len(pile_members) + 1 or len({y for y, _ in nodes}) != len(nodes):
            raise InputError("杭の節点接続が一本の連続した鉛直線になっていません")
        if nodes[-1][0] - nodes[0][0] != profile.length:
            raise InputError(f"KGInfo{group}: 杭長とSDCの杭長が一致しません")
        y, node = nodes[-1]
        v = profile.values[col]
        updates[node] = v.fields()
        rows.append({"group": group, "column": col, "node": node,
                     "x_m": base.number(ndu.fields(f"JointXY{node}", 2)[0], "x座標"),
                     "y_m": y, "pile_length_m": profile.length,
                     "spring_line": profile.spring_line, "force_line": profile.force_line,
                     "k1_kN_per_m": v.k1, "k2_kN_per_m": v.k2, "fy_kN": v.fy, "fu_kN": v.fu,
                     "field4_to_13": updates[node]})
    if not updates:
        raise InputError("入力対象の杭先端がありません")
    return updates, rows


def compare_ndu(raw: bytes, updates: dict[int, list[str]]) -> list[dict]:
    """既存Y支点との全10欄比較。空欄と数値0を区別する。"""
    _, _, entries = support.ndu_supports(raw)
    comparisons = []
    for node, values in updates.items():
        hits = []
        for key, (index, body) in entries.items():
            fields = [v.strip().decode("ascii") for v in body.split(b",")]
            if len(fields) != 13:
                raise InputError(f"{key}: 13フィールドが必要です")
            if not fields[0] and base.integer(fields[1], "節点") == node and fields[2] == "2":
                differences = []
                for offset, (actual, expected) in enumerate(zip(fields[3:], values), 4):
                    expected = expected.strip()
                    equal = (actual == expected if not actual or not expected
                             else base.number(actual, key) == base.number(expected, key))
                    if not equal:
                        differences.append({"field": offset, "actual": actual, "expected": expected})
                hits.append({"key": key, "line": index + 1, "differences": differences})
        comparisons.append({"node": node, "status": "missing" if not hits else
                            "match" if len(hits) == 1 and not hits[0]["differences"] else "different", "entries": hits})
    return comparisons


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdc", type=Path, default=base.DEFAULT_SDC)
    parser.add_argument("--ndu", type=Path, default=base.DEFAULT_NDU,
                        help="入力NDU")
    parser.add_argument("--groups", nargs="+", default=["4:1", "5:2", "6:3"], metavar="KG:SDC列")
    parser.add_argument("--output", type=Path, help="別名NDU。省略時は確認表示")
    parser.add_argument("--report", type=Path, help="参照値・配置・NDU比較結果を保存するJSON")
    args = parser.parse_args(argv)
    try:
        suffix = ".ndu"
        if args.output and args.output.suffix.lower() != suffix:
            raise InputError(f"出力拡張子は{suffix}にしてください")
        if args.report and args.report.suffix.lower() != ".json":
            raise InputError("報告書は.jsonで指定してください")
        inputs = [args.sdc, args.ndu]
        outputs = [p for p in (args.output, args.report) if p]
        if any(support.same_path(o, i) for o in outputs for i in inputs) or (
                len(outputs) == 2 and support.same_path(*outputs)):
            raise InputError("入力・出力・報告書は別のパスを指定してください")
        snapshots = {p: p.read_bytes() for p in inputs}
        ndu = base.parse_ndu(snapshots[args.ndu])
        profile, groups = parse_sdc(snapshots[args.sdc]), base.parse_groups(args.groups)
        updates, rows = make_plan(ndu, profile, groups)
        result, summary = support.render_ndu(snapshots[args.ndu], updates, set())
        report = {"configuration": {"profile": "existing-tip", "groups": groups,
                  "direction": "直角方向", "k3": "K2", "negative_limits": "blank",
                  "rounding": "none", "length_or_pile_count_factor": "none", "shaft_resistance": "not-added",
                  "output_format": suffix}, "field_names": list(support.FIELD_NAMES),
                  "sources": {str(p.resolve()): hashlib.sha256(v).hexdigest() for p, v in snapshots.items()},
                  "summary": summary, "nodes": rows,
                  "ndu_comparison": compare_ndu(snapshots[args.ndu], updates),
                  "output_sha256": hashlib.sha256(result).hexdigest()}
        report_bytes = (json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n").encode("utf8")
        pending = [(p, data) for p, data in ((args.output, result), (args.report, report_bytes)) if p]
        for path, data in pending:
            if not path.parent.is_dir() or (path.exists() and path.read_bytes() != data):
                raise InputError(f"出力先の親ディレクトリがないか、異なる既存ファイルがあります: {path}")
        if any(p.read_bytes() != raw for p, raw in snapshots.items()):
            raise InputError("計算中に入力が変更されました。再実行してください")
        print("杭先端: K1±=短期K1、K2±=K3±=短期K2、F1+=Fy、F2+=Fu、負側制限値は空欄")
        for row in rows:
            print(f"KG{row['group']} / SDC{row['column']}列 / 節点{row['node']}: "
                  f"K1={row['k1_kN_per_m']}, K2={row['k2_kN_per_m']} kN/m, "
                  f"Fy={row['fy_kN']}, Fu={row['fu_kN']} kN")
        print(json.dumps(summary, ensure_ascii=False))
        for path, data in pending:
            support.save_new(path, data)
            print(f"出力: {path}")
        return 0
    except (InputError, OSError, UnicodeError, DecimalException) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
