"""入力スナップショットと既存計算の根拠を、JSON/Excel共通の記録にする。"""

from __future__ import annotations

from decimal import Decimal as D
import re

import fill_jiban_shogen as base
import fill_suppot_info as support

SCHEMA_VERSION = 1


def token(value):
    return str(value).strip() or None if value is not None else None


def equal(a, b):
    return a == b if a is None or b is None else D(a) == D(b)


def geometry(ndu, groups):
    members, nodes = {}, {}
    for member in base.collect_members(ndu, groups):
        ids = [int(v) for v in ndu.fields(f"ElementInfo{member.number}", 6)[4:6]]
        ids.sort(key=lambda n: D(ndu.fields(f"JointXY{n}", 2)[1]))
        top, bottom = ids
        x, y = map(D, ndu.fields(f"JointXY{top}", 2)[:2])
        origin = y - member.top
        members[member.number] = dict(group=member.group, column=member.column, member=member.number,
                                     top_node=top, bottom_node=bottom, x_m=x, origin_y_m=origin,
                                     top_m=member.top, bottom_m=member.bottom)
        for node in ids:
            xx, yy = map(D, ndu.fields(f"JointXY{node}", 2)[:2])
            nodes[node] = dict(group=member.group, column=member.column, node=node,
                               x_m=xx, y_m=yy, depth_m=yy-origin, origin_y_m=origin)
    for group in groups:
        ordered = sorted((n for n in nodes.values() if n["group"] == group), key=lambda n: n["depth_m"])
        for i, row in enumerate(ordered):
            before, after = ordered[max(0, i-1)], ordered[min(len(ordered)-1, i+1)]
            row.update(previous_node=before["node"] if i else None,
                       next_node=after["node"] if i+1 < len(ordered) else None,
                       previous_depth_m=before["depth_m"], next_depth_m=after["depth_m"],
                       top_m=(before["depth_m"]+row["depth_m"])/2,
                       bottom_m=(after["depth_m"]+row["depth_m"])/2,
                       head_node=ordered[0]["node"], tip_node=ordered[-1]["node"],
                       pile_length_m=ordered[-1]["depth_m"])
    return members, nodes


def supports(raw):
    result = {}
    for key, (line, body) in support.ndu_supports(raw)[2].items():
        fields = [token(v.decode("ascii")) for v in body.split(b",")]
        if fields[0] is None and fields[2] == "2":
            result[int(fields[1])] = dict(key=key, line=line+1, fields=fields,
                                         item=int(key.removeprefix("SuppotInfo")))
    return result


def complete(report, sdc_raw, original, result, profiles):
    """profilesは実際の計算で使ったパーサの戻り値。ディスクを読み直さない。"""
    groups = report["configuration"]["groups"]
    ndu, output = base.parse_ndu(original), base.parse_ndu(result)
    members, nodes = geometry(ndu, groups)
    old_supports = supports(original) if set(profiles) & {"shaft", "tip"} else {}
    new_supports = supports(result) if old_supports or set(profiles) & {"shaft", "tip"} else {}
    fields, targets, sources = [], [], []
    lines = sdc_raw.decode("cp932").splitlines()
    used_lines = set()

    def source(op, line, column, label, value, unit):
        used_lines.add(line)
        sources.append(dict(operation=op, line=line, field=column, label=label,
                            value=str(value), unit=unit, raw=lines[line-1]))

    for op, profile in profiles.items():
        detail = report["details"][op]
        rows = detail.get("members", detail.get("nodes", []))
        if op in ("horizontal", "pressure"):
            layer_map = {l.source_line: l for l in profile}
        elif op == "shaft":
            layer_map = {l.number: l for l in profile.layers}
            detail["conditions"] = dict(pile_length_m=profile.length, exclusion_m=profile.exclusion,
                                        embedment_m=profile.embedment)
            # 主表は除外区間も含む全層を示す。有効支点のpiecesだけでは先頭層が欠ける。
            ncols = len(profile.layers[0].values)
            detail["layers"] = []
            for layer in profile.layers:
                columns = []
                for col in sorted(set(groups.values())):
                    kv, fv = layer.values[col]
                    kfield, ffield = 3+2*ncols+col, 3+col
                    columns.append(dict(column=col, k1_kN_per_m2=kv, fy_kN_per_m=fv,
                                        spring_field=kfield, force_field=ffield))
                    source(op, layer.spring_line, kfield, "押込み 短期第1勾配 K1", kv, "kN/m²")
                    source(op, layer.force_line, ffield, "押込み 降伏点 Fy", fv, "kN/m")
                detail["layers"].append(dict(number=layer.number, top_m=layer.top, bottom_m=layer.bottom,
                    active_top_m=layer.active_top, active_bottom_m=layer.active_bottom,
                    spring_thickness_m=D(lines[layer.spring_line-1].split(",")[1].strip()),
                    spring_line=layer.spring_line, force_line=layer.force_line, columns=columns))
            detail["excluded"] = [dict(nodes[n], reason="上端・先端除外または抵抗0のため支点を作成しない")
                                  for n in detail["zero_resistance_nodes"]]
            detail["excluded"] += [dict(nodes[n], reason="杭先端ばねを入力" if "tip" in profiles else "周面工程では保持")
                                   for n in detail["unchanged_tip_nodes"]]
        for row in rows:
            number = row.get("member", row.get("node"))
            geo = members[number] if "member" in row else nodes[number]
            row["geometry"] = geo
            ident = f"{op}:KG{row['group']}:{number}"
            row["id"] = ident
            row["column"] = row.get("column", row.get("model_column"))
            if op in ("horizontal", "pressure"):
                col = row.get("pressure_column", row["column"])
                for piece in row["pieces"]:
                    layer = layer_map[piece["sdc_line"]]
                    piece.update(layer=layer.number, layer_top_m=layer.top, layer_bottom_m=layer.bottom,
                                 top_m=max(D(geo["top_m"]), layer.top), bottom_m=min(D(geo["bottom_m"]), layer.bottom))
                    if op == "horizontal":
                        # 同じパーサが選択した見出しからCSV欄の番号を取得する。
                        header = next(l for l in reversed(lines[:layer.source_line-1]) if "短期(非線形)-" in l)
                        index = [f.strip() for f in header.split(",")].index(f"短期(非線形)-{col}列目") + 1
                        piece["value_field"] = index
                        source(op, layer.source_line, index, f"{col}列目 短期(非線形)", layer.values[col], "kN/m²")
                    else:
                        upper, lower = layer.values[col]
                        piece.update(layer_upper=upper, layer_lower=lower)
                        # 土圧パーサと同じ杭列見出しを使う（列の並びを仮定しない）。
                        header = next(l for l in reversed(lines[:layer.source_line-1]) if "1列目" in l)
                        index = [f.strip() for f in header.split(",")].index(f"{col}列目") + 1
                        piece.update(upper_field=index, lower_field=index+1)
                        source(op, layer.source_line, index, f"{col}列目 上側", upper, "kN/m")
                        source(op, layer.source_line, index+1, f"{col}列目 下側", lower, "kN/m")
                key = f"JibanShogenInfo{number}"
                offsets = [2] if op == "horizontal" else [3, 4]
                expected = [row["value"]] if op == "horizontal" else row["calculated"]
                after = [token(output.fields(key, 7)[i-1]) for i in offsets]
                before = [token(ndu.fields(key, 7)[i-1]) for i in offsets]
                labels = ["水平ばね"] if op == "horizontal" else ["上端土圧", "下端土圧"]
                units = ["kN/m²"] if op == "horizontal" else ["kN/m", "kN/m"]
                line, item = output.records[key][0]+1, None
                added = False
            else:
                if op == "shaft":
                    ncols = len(profile.layers[0].values)
                    for piece in row["pieces"]:
                        layer = layer_map[piece["layer"]]
                        piece.update(layer_top_m=layer.top, layer_bottom_m=layer.bottom,
                                     active_top_m=layer.active_top, active_bottom_m=layer.active_bottom,
                                     spring_field=3+2*ncols+row["column"], force_field=3+row["column"])
                        source(op, layer.spring_line, 3+2*ncols+row["column"], "押込み 短期第1勾配 K1", piece["k1_kN_per_m2"], "kN/m²")
                        source(op, layer.force_line, 3+row["column"], "押込み 降伏点 Fy", piece["fy_kN_per_m"], "kN/m")
                else:
                    ncols, col = len(profile.values), row["column"]
                    row["source_fields"] = dict(k1_kN_per_m=ncols+col, k2_kN_per_m=2*ncols+col, fy_kN=col, fu_kN=ncols+col)
                    for line_no, index, label, value, unit in (
                        (profile.spring_line, ncols+col, "短期K1", row["k1_kN_per_m"], "kN/m"),
                        (profile.spring_line, 2*ncols+col, "短期K2", row["k2_kN_per_m"], "kN/m"),
                        (profile.force_line, col, "押込みFy", row["fy_kN"], "kN"),
                        (profile.force_line, ncols+col, "押込みFu", row["fu_kN"], "kN")):
                        source(op, line_no, index, label, value, unit)
                final = new_supports[number]
                key, line, item = final["key"], final["line"], final["item"]
                offsets, labels = list(range(4, 14)), list(support.FIELD_NAMES[3:])
                units = ["kN/m" if label.startswith("K") else "kN" for label in labels]
                expected = row["field4_to_13"]
                after = final["fields"][3:]
                added = number not in old_supports
                before = old_supports[number]["fields"][3:] if not added else [None]*10
            if any(not equal(a, token(e)) for a, e in zip(after, expected)):
                raise base.InputError(f"{ident}: 計算値と最終NDUの {key} が一致しません。")
            state = "追加" if added else "同値" if all(equal(a,b) for a,b in zip(after,before)) else "変更"
            if row.get("method") == "skip":
                state = "保留"
            target = dict(id=ident, operation=op, group=row["group"], column=row["column"], number=number,
                          kind="部材" if "member" in row else "節点", key=key, line=line, item=item,
                          status=state, geometry=geo)
            targets.append(target)
            row["output"] = target
            for i, label, unit, a, b in zip(offsets, labels, units, after, before):
                field_state = "追加" if added else "同値" if equal(a,b) else "変更"
                delta = str(D(a)-D(b)) if a is not None and b is not None else None
                transition = "追加" if added else "空欄→数値" if b is None and a is not None else "数値→空欄" if a is None and b is not None else ""
                fields.append(dict(target, field=i, label=label, unit=unit, before=b, after=a, difference=delta,
                                   field_status=field_state, transition=transition))
    # 採用値は重複を除去し、原行・表見出し・杭条件は別の文字列一覧で保存する。
    source_map = {(r["operation"], r["line"], r["field"]): r for r in sources}
    context_lines = set()
    for line in used_lines:
        i = line-2
        while i >= 0 and lines[i].strip():
            if not re.match(r"\s*\d+\s*,", lines[i]):
                context_lines.add(i+1)
            i -= 1
    for i, line in enumerate(lines):
        if line.startswith("杭長,突出長,根入れ深さ,"):
            context_lines.update((i+1, i+2))
        if line.strip() == "（２）直角方向":
            direction = i
    if "shaft" in profiles or "tip" in profiles:
        for i in range(direction, len(lines)):
            if lines[i].strip() == "杭列数,奥行き本数,,1/β(m)":
                context_lines.update((i+1, i+2, i+3))
                break
    ndu_keys = {f"KGInfo{g}" for g in groups} | {f"ElementInfo{m}" for m in members} | {f"JointXY{n}" for n in nodes}
    structural_prefixes = (b"SuppotNum=", b"SuppotRow=", b"Suppot_ChokuKisoCaseNo")
    before_structure = [l.decode("cp932") for l in original.splitlines() if l.startswith(structural_prefixes)]
    after_structure = [l.decode("cp932") for l in result.splitlines() if l.startswith(structural_prefixes)]
    return dict(schema_version=SCHEMA_VERSION, targets=targets, fields=fields,
                counts={state: sum(t["status"] == state for t in targets) for state in ("追加", "変更", "同値", "保留")},
                source_values=list(source_map.values()),
                sdc_context=[dict(line=i, raw=lines[i-1]) for i in sorted(context_lines | used_lines)],
                geometry=[dict(key=k, line=ndu.records[k][0]+1, raw=ndu.lines[ndu.records[k][0]].decode("cp932").rstrip())
                          for k in sorted(ndu_keys, key=lambda k: ndu.records[k][0])],
                structure=dict(changed=before_structure != after_structure, before=before_structure, after=after_structure))
