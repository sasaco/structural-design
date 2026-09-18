"""今町橋4P・右基礎のSuppotInfoをSDC表示値と照合する（NDUは書き換えない）。

節点間の中点で区切った負担区間を仮定する調査用。入力仕様の確定・解析検証ではない。
第1勾配/降伏点の既存欄のみ比較し、その他の欄は元の13フィールドを記録する。
画面で確認した項目名を併記する。各勾配の採用値や正負と押込み/引抜きの対応は別問題。
"""

from __future__ import annotations

import argparse
from decimal import Decimal as D, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import re

import fill_jiban_shogen as base
import sdc_columns

ROOT = Path(__file__).resolve().parents[1]
SDC = ROOT / "snap/今町橋りょう4P(右).sdc"
NDU = ROOT / "snap/今町橋りょう4P(C方向･右押し→).ndu"
GROUPS = {4: 1, 5: 2, 6: 3}
ZERO = D(0)
NAMES = ("k1_kN_per_m", "k2_kN_per_m", "fy_kN", "fu_kN")
SUPPOT_FIELDS = (
    "節点番号・開始", "節点番号・終点", "拘束方向（2=Y）",
    "第1勾配・ばね定数・＋側", "第1勾配・制限値・＋側", "第1勾配・制限値・−側",
    "第2勾配・ばね定数・＋側", "第2勾配・制限値・＋側", "第2勾配・制限値・−側",
    "第3勾配・ばね定数・＋側", "第1勾配・ばね定数・−側",
    "第2勾配・ばね定数・−側", "第3勾配・ばね定数・−側",
)


def table(lines: list[str], start: int, width: int) -> list[dict]:
    rows = []
    for index in range(start, len(lines)):
        if not re.match(r"^\s*\d+\s*,", lines[index]):
            if rows:
                break
            continue
        values = [D(x.strip()) for x in lines[index].split(",")]
        if len(values) != width or values[0] != len(rows) + 1:
            raise ValueError(f"SDC {index + 1}行: 想定した右基礎の表ではありません")
        rows.append({"line": index + 1, "values": values})
    if len(rows) != 7:
        raise ValueError("本調査は今町橋4P右基礎の7層・3列を対象にしています")
    return rows


def parse_layers(raw: bytes) -> tuple[list[dict], D, D]:
    lines = raw.decode("cp932").splitlines()
    direction = lines.index("（２）直角方向")
    spring = lines.index("d）杭周面の鉛直せん断地盤ばね値", direction)
    capacity = lines.index("e）杭周面の支持力", spring)
    beta_header = next(i for i in range(direction, spring) if sdc_columns.is_pile_count_header(lines[i]))
    exclusion = D(lines[beta_header + 2].split(",")[-1].strip())
    pile_header = next(i for i, line in enumerate(lines) if line.startswith("杭長,突出長,根入れ深さ,"))
    pile_data = lines[pile_header + 1].split(",")
    pile_length, embedment = D(pile_data[0].strip()), D(pile_data[2].strip())
    data = {}
    for section, start, width in (("k", spring, 15), ("f", capacity, 9)):
        for side, label in (("compression", "【押込み側】"), ("uplift", "【引抜き側】")):
            data[section, side] = table(lines, lines.index(label, start) + 1, width)
    layers = []
    top = ZERO
    for i in range(7):
        # e表の層厚は幾何学的な全層厚。d表の押込み側最終層は有効長になっている。
        thickness = data["f", "uplift"][i]["values"][1]
        bottom = top + thickness
        layer = {"number": i + 1, "top_m": top, "bottom_m": bottom}
        for side, end in (("compression", pile_length - embedment), ("uplift", pile_length)):
            k, f = data["k", side][i], data["f", side][i]
            active_top, active_bottom = max(top, exclusion), min(bottom, end)
            active_length = max(ZERO, active_bottom - active_top)
            if f["values"][1] != thickness or f["values"][2] != active_length or k["values"][8] != active_length:
                raise ValueError(f"第{i + 1}層 {side}: SDCの⊿lと除外範囲が一致しません")
            layer[side] = {"active_top_m": active_top, "active_bottom_m": active_bottom,
                           "sdc_spring_line": k["line"], "sdc_capacity_line": f["line"],
                           "columns": {col: (k["values"][8 + col], k["values"][11 + col],
                                             f["values"][2 + col], f["values"][5 + col])
                                       for col in (1, 2, 3)}}
        layers.append(layer)
        top = bottom
    if top != pile_length:
        raise ValueError("層厚合計と杭長が一致しません")
    return layers, exclusion, pile_length - embedment


def integrate(a: D, b: D, layers: list[dict], side: str, col: int) -> dict:
    totals = [ZERO] * 4
    pieces = []
    for layer in layers:
        data = layer[side]
        u, v = max(a, data["active_top_m"]), min(b, data["active_bottom_m"])
        if v <= u:
            continue
        values = data["columns"][col]
        for i, value in enumerate(values):
            totals[i] += value * (v - u)
        pieces.append({"layer": layer["number"], "top_m": u, "bottom_m": v, "length_m": v - u,
                       "sdc_spring_line": data["sdc_spring_line"], "sdc_capacity_line": data["sdc_capacity_line"],
                       "k1_kN_per_m2": values[0], "k2_kN_per_m2": values[1],
                       "fy_kN_per_m": values[2], "fu_kN_per_m": values[3]})
    return {"pieces": pieces, "raw": dict(zip(NAMES, totals)),
            "rounded_for_comparison": {name: value.quantize(D(1) if i < 2 else D("0.1"), rounding=ROUND_HALF_UP)
                                       for i, (name, value) in enumerate(zip(NAMES, totals))}}


def analyze(sdc_raw: bytes, ndu_raw: bytes) -> dict:
    layers, excluded_top, compression_end = parse_layers(sdc_raw)
    ndu = base.parse_ndu(ndu_raw)
    members = base.collect_members(ndu, GROUPS)  # 既存の鉛直・連続性チェックを共用
    supports = {}
    for line_number, line in enumerate(ndu_raw.decode("cp932").splitlines(), 1):
        match = re.fullmatch(r"SuppotInfo(\d+)=(.*)", line)
        if not match:
            continue
        fields = [x.strip() for x in match[2].split(",")]
        if len(fields) != 13:
            raise ValueError(f"{line_number}行: SuppotInfoのフィールド数が13ではありません")
        if fields[0]:
            raise ValueError("本調査は開始節点が空欄の単一節点指定を対象にしています")
        node = int(fields[1])
        if fields[2] != "2" or node in supports:
            raise ValueError("本調査で想定しない支点種別または同一節点の複数支点です")
        supports[node] = {"support": int(match[1]), "source_line": line_number, "fields": fields}
    rows = []
    for group, col in GROUPS.items():
        node_ids = {int(n) for member in members if member.group == group
                    for n in ndu.fields(f"ElementInfo{member.number}", 6)[4:6]}
        nodes = sorted((D(ndu.fields(f"JointXY{n}", 2)[1]), n) for n in node_ids)
        origin = nodes[0][0]
        if nodes[-1][0] - origin != layers[-1]["bottom_m"]:
            raise ValueError("NDU杭長とSDC層厚が一致しません")
        for index, (y, node) in enumerate(nodes):
            a = (nodes[max(0, index - 1)][0] + y) / 2 - origin
            b = (y + nodes[min(len(nodes) - 1, index + 1)][0]) / 2 - origin
            row = {"group": group, "column": col, "node": node, "node_depth_m": y - origin,
                   "tributary_top_m": a, "tributary_bottom_m": b,
                   "existing": supports.get(node), "is_tip": index == len(nodes) - 1}
            for side in ("compression", "uplift"):
                row[side] = integrate(a, b, layers, side, col)
            if row["existing"] and not row["is_tip"]:
                old = row["existing"]["fields"]
                rounded = row["compression"]["rounded_for_comparison"]
                hypothetical = (row["compression"]["raw"]["fy_kN"] / D("1.2")).quantize(D("0.1"), rounding=ROUND_HALF_UP)
                row["comparison"] = {"delta_field4_k1": rounded["k1_kN_per_m"] - D(old[3]),
                                     "delta_field5_fy": rounded["fy_kN"] - D(old[4]),
                                     "diagnostic_only_fy_div_1_2": hypothetical,
                                     "diagnostic_only_delta_div_1_2": hypothetical - D(old[4])}
            rows.append(row)
    compared = [r for r in rows if "comparison" in r]
    summary = {"nodes": len(rows), "existing_shaft_supports_compared": len(compared),
               "tip_nodes_excluded_from_comparison": sum(r["is_tip"] for r in rows)}
    for field in ("delta_field4_k1", "delta_field5_fy", "diagnostic_only_delta_div_1_2"):
        summary[field] = {"matches": sum(r["comparison"][field] == 0 for r in compared),
                          "max_absolute_difference": max(abs(r["comparison"][field]) for r in compared)}
    return {"scope": "今町橋4P右基礎KGInfo4/5/6・調査用・NDU変更なし",
            "assumptions": ["節点の負担区間は上下隣接節点との中点間（端点は杭端で打切り）",
                            "地震時の第1/第2勾配・降伏/終局をそれぞれ積分。押込み/引抜きを別計算",
                            "比較表示だけK整数、F小数1位へROUND_HALF_UP。最終入力の丸め規則は未確定",
                            "1.2除算は差異の調査用であり、入力規則ではない",
                            "13フィールドの画面項目は確認済み。第3勾配の採用値・正負と押込み/引抜きの対応は未確定"],
            "field_mapping": {str(i): label for i, label in enumerate(SUPPOT_FIELDS, 1)},
            "field_mapping_evidence": "ユーザー提示: スクリーンショット 2026-09-17 120334.png／地盤ばね(節点)",
            "exclusion_top_m": excluded_top, "compression_end_m": compression_end,
            "sources": {"sdc_sha256": hashlib.sha256(sdc_raw).hexdigest(),
                        "ndu_sha256": hashlib.sha256(ndu_raw).hexdigest()},
            "summary": summary, "nodes": rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, help="新規JSON出力先。既存と同一内容の場合のみ再実行可")
    args = parser.parse_args()
    result = analyze(SDC.read_bytes(), NDU.read_bytes())
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2, default=str))
    if args.report:
        if args.report.suffix.lower() != ".json" or args.report.resolve() in (SDC.resolve(), NDU.resolve()):
            parser.error("出力先は別名の.jsonファイルを指定してください")
        content = (json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n").encode("utf-8")
        if args.report.exists():
            if args.report.read_bytes() != content:
                parser.error("異なる内容の既存報告書は上書きしません")
        else:
            with args.report.open("xb") as stream:
                stream.write(content)
        print(args.report)


if __name__ == "__main__":
    main()
