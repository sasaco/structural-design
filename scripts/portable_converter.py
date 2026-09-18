"""Portableアプリ用の変換・確認・保存API。GUIや作業フォルダーに依存しない。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import tempfile
from uuid import uuid4

import fill_jiban_shogen as base
import fill_jiban_pressure as pressure
import fill_suppot_info as support
import fill_pile_tip_suppot_info as tip
import calculation_record
import excel_report
import sdc_columns
from kg_candidates import inspect_candidates, validate_groups

VERSION = "1.4.0"
APP_NAME = "SDCConverter"
InputError = base.InputError
OPERATIONS = {
    "horizontal": "水平地盤ばね",
    "pressure": "有効抵抗土圧力",
    "shaft": "杭周面ばね・支持力",
    "tip": "杭先端ばね・支持力",
}
DEFAULT_GROUPS = ("4:1", "5:2", "6:3")


@dataclass(frozen=True)
class Request:
    sdc: Path
    ndu: Path
    operations: tuple[str, ...] = tuple(OPERATIONS)
    groups: tuple[str, ...] = DEFAULT_GROUPS
    # GUIはdirect（指定SDC列をそのまま使用）。right/leftは既存CLI/API互換用。
    push_direction: str = "right"
    # 周面方式は呼び出し元で明示する。CLIと同じ制約を保つ。
    shaft_profile: str | None = None
    horizontal_cross_layer: str = "length-weighted"
    pressure_cross_layer: str = "integral-average"
    pressure_decimals: int = 1
    shaft_k_decimals: int = 0
    shaft_force_decimals: int = 1
    # GUIチェックリストの再検証。項目別CLIは従来どおり必要な表だけで実行できる。
    require_matching_lengths: bool = False
    # 末尾へ追加し、既存の位置引数呼出しを維持する。
    sdc_direction: str = sdc_columns.DEFAULT_DIRECTION
    pressure_case: str = sdc_columns.DEFAULT_PRESSURE_CASE


@dataclass(frozen=True)
class PreviewRow:
    operation: str
    target: str
    before: str
    after: str


@dataclass(frozen=True)
class Plan:
    target: Path
    sources: tuple[tuple[Path, bytes], ...]
    data: bytes
    rows: tuple[PreviewRow, ...]
    report: dict
    workbook: excel_report.ReportBook | None = None


@dataclass(frozen=True)
class Saved:
    output: Path
    backup: Path | None
    report: Path
    excel: Path | None = None
    workbook: excel_report.ReportBook | None = None


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def input_path(path: Path, suffix: str) -> Path:
    path = Path(path).expanduser().resolve()
    if path.suffix.lower() != suffix or not path.is_file():
        raise InputError(f"既存の{suffix}ファイルを選択してください: {path}")
    return path


def support_values(raw: bytes) -> dict[int, list[str]]:
    """確認画面用にNDUの現在の支点値を取得する。"""
    _, _, entries = support.ndu_supports(raw)
    values = {}
    for _, body in entries.values():
        fields = [s.strip().decode("ascii") for s in body.split(b",")]
        if not fields[0] and fields[2] == "2":
            values[int(fields[1])] = fields[3:]
    return values


def format_support(values: list[str] | None) -> str:
    if values is None:
        return "（支点を追加）"
    return " / ".join(f"{label}={value.strip() or '空欄'}"
                      for label, value in zip(support.FIELD_NAMES[3:], values))


def prepare(request: Request) -> Plan:
    """既存4モジュールの計算・レンダリング関数を直接使用。ファイルは書かない。"""
    operations = request.operations
    if not operations or len(set(operations)) != len(operations) or set(operations) - OPERATIONS.keys():
        raise InputError("変換する項目を1つ以上選択してください（重複・不明な項目は不可）。")
    if "shaft" in operations and request.shaft_profile != "existing-screen":
        raise InputError("周面ばねは『既存画面方式』を明示してください。")
    if request.push_direction not in ("right", "left", "direct"):
        raise InputError("土圧の列指定は direct / right / left を指定してください。")
    direction_label = sdc_columns.choice_label(sdc_columns.DIRECTIONS, request.sdc_direction, "SDC参照方向")
    pressure_case_label = sdc_columns.choice_label(
        sdc_columns.PRESSURE_CASES, request.pressure_case, "有効抵抗土圧力の区分")
    if request.horizontal_cross_layer not in ("length-weighted", "midpoint", "skip", "error") or request.pressure_cross_layer not in ("integral-average", "endpoints", "error"):
        raise InputError("境界処理の指定が不正です。")
    if any(type(d) is not int or d not in range(7) for d in (request.pressure_decimals, request.shaft_k_decimals, request.shaft_force_decimals)):
        raise InputError("丸め桁数は0～6を指定してください。")
    groups = base.parse_groups(list(request.groups))
    if not groups:
        raise InputError("杭グループを KG番号:列番号 の形式で指定してください。")
    sdc_path, ndu_path = input_path(request.sdc, ".sdc"), input_path(request.ndu, ".ndu")
    paths = [sdc_path, ndu_path]
    if any(support.same_path(a, b) for i, a in enumerate(paths) for b in paths[i + 1:]):
        raise InputError("入力ファイル同士が同じ実体を参照しています。")
    sources = tuple((p, p.read_bytes()) for p in paths)
    sdc_raw, ndu_raw = sources[0][1], sources[1][1]
    # GUIの候補確認後に原本が編集されても、実際に計算するバイト列で再判定する。
    if request.require_matching_lengths:
        validate_groups(inspect_candidates(ndu_raw, sdc_raw, sdc_direction=request.sdc_direction), groups)
    ndu = base.parse_ndu(ndu_raw)
    result = ndu_raw
    rows, details, profiles = [], {}, {}
    # 選択順によらず同じ手順で処理する。保存は全項目が成功した後だけ。
    for operation in OPERATIONS:
        if operation not in operations:
            continue
        if operation == "horizontal":
            layers = base.parse_sdc(sdc_raw, request.sdc_direction)
            profiles[operation] = layers
            updates, evidence = {}, []
            current = base.parse_ndu(result)
            for member in base.collect_members(ndu, groups):
                overlaps = base.find_overlaps(member, layers)
                value = base.select_value(member, overlaps, request.horizontal_cross_layer)
                if value is not None:
                    updates[member.number] = value
                before = current.fields(f"JibanShogenInfo{member.number}", 7)[1]
                actual = base.format_number(value) if value is not None else before
                unrounded = sum((o.length * o.layer.values[member.column] for o in overlaps), Decimal(0)) / (member.bottom-member.top)
                if len(overlaps) == 1 or request.horizontal_cross_layer == "midpoint":
                    unrounded = value
                rows.append(PreviewRow(OPERATIONS[operation], f"部材{member.number}",
                                       before or "空欄", actual or "空欄"))
                evidence.append({"member": member.number, "group": member.group, "column": member.column,
                                 "value": str(value) if value is not None else (before or None),
                                 "method": "single-layer" if len(overlaps) == 1 else request.horizontal_cross_layer,
                                 "unrounded_value": str(unrounded) if value is not None else None,
                                 "midpoint_m": str((member.top+member.bottom)/2), "pieces": [
                                     {"sdc_line": o.layer.source_line, "length_m": str(o.length),
                                      "value": str(o.layer.values[member.column])} for o in overlaps]})
            result = base.render_ndu(current, updates)
            details[operation] = {"members": evidence, "count": len(updates)}
        elif operation == "pressure":
            current = base.parse_ndu(result)
            profiles[operation] = pressure.parse_pressure_sdc(
                sdc_raw, request.sdc_direction, request.pressure_case)
            updates, evidence = pressure.make_plan(current, profiles[operation], groups,
                                                   request.push_direction, request.pressure_decimals, request.pressure_cross_layer)
            for row in evidence["members"]:
                rows.append(PreviewRow(OPERATIONS[operation], f"部材{row['member']}",
                                       " / ".join(v or "空欄" for v in row["current"]),
                                       " / ".join(row["calculated"])))
            result = pressure.render_pressure(current, updates)
            details[operation] = evidence
        else:
            zeros = set()
            if operation == "shaft":
                profiles[operation] = support.parse_sdc(sdc_raw, request.sdc_direction)
                updates, zeros, tips, evidence = support.make_plan(ndu, profiles[operation], groups,
                                                                  request.shaft_k_decimals, request.shaft_force_decimals)
                details[operation] = {"nodes": evidence, "unchanged_tip_nodes": sorted(tips), "zero_resistance_nodes": sorted(zeros)}
            else:
                profiles[operation] = tip.parse_sdc(sdc_raw, request.sdc_direction)
                updates, evidence = tip.make_plan(ndu, profiles[operation], groups)
                details[operation] = {"nodes": evidence}
            rendered, summary = support.render_ndu(result, updates, zeros)
            previous = support_values(result)
            for node, values in updates.items():
                rows.append(PreviewRow(OPERATIONS[operation], f"節点{node}",
                                       format_support(previous.get(node)), format_support(values)))
            result = rendered
            details[operation]["summary"] = summary
    # 支点数・ケース行を再検査し、再同期しても同一になることを確認する。
    base.parse_ndu(result)
    checked = support.sync_ndu_support_cases(result) if set(operations) & {"shaft", "tip"} else result
    if checked != result:
        raise InputError("変換結果の整合性を確認できませんでした。")
    report = {
        "application": APP_NAME, "version": VERSION,
        "schema_version": calculation_record.SCHEMA_VERSION, "run_id": uuid4().hex,
        "created_at": datetime.now().astimezone().isoformat(), "mode": "preview",
        "configuration": {"operations": list(operations), "groups": groups,
                          "sdc_direction": request.sdc_direction, "sdc_direction_label": direction_label,
                          "pressure_case": request.pressure_case,
                          "pressure_case_label": pressure_case_label if "pressure" in operations else None,
                          "push_direction": request.push_direction, "shaft_profile": request.shaft_profile,
                          "horizontal_cross_layer": request.horizontal_cross_layer, "pressure_cross_layer": request.pressure_cross_layer,
                          "pressure_decimals": request.pressure_decimals, "shaft_k_decimals": request.shaft_k_decimals,
                          "shaft_force_decimals": request.shaft_force_decimals},
        "sources": [{"path": str(p), "sha256": digest(data)} for p, data in sources],
        "details": details, "output_sha256": digest(result),
        "preview": [vars(row) for row in rows],
    }
    report["calculation"] = calculation_record.complete(report, sdc_raw, ndu_raw, result, profiles)
    return Plan(ndu_path, sources, result, tuple(rows), report, excel_report.build(report))


def verify_sources(plan: Plan) -> None:
    for path, original in plan.sources:
        if path.read_bytes() != original:
            raise InputError(f"確認後に入力ファイルが変更されました。再度確認してください: {path}")


def validate_output(plan: Plan, output: Path, overwrite: bool) -> None:
    if output.suffix.lower() != plan.target.suffix.lower():
        raise InputError(f"出力先の拡張子は {plan.target.suffix} を指定してください。")
    if not output.parent.is_dir():
        raise InputError("出力先のフォルダーがありません。")
    if overwrite:
        if output != plan.target:
            raise InputError("元ファイル更新では、変換対象そのものを出力先に指定してください。")
    elif any(support.same_path(output, p) for p, _ in plan.sources):
        raise InputError("別名保存では入力ファイルを出力先にできません。")
    for path, _ in plan.sources:
        if path != plan.target and support.same_path(output, path):
            raise InputError("参照ファイルを上書きできません。")
    if not overwrite and output.exists():
        raise InputError(f"出力ファイルが既にあります。別の名前を指定してください: {output}")


def publish_new(temp: Path, output: Path) -> None:
    """Windows renameは既存先を上書きせず、完成したファイルだけを公開する。"""
    if os.name == "nt":
        os.rename(temp, output)
    else:
        os.link(temp, output)
        temp.unlink()


def stage(output: Path, data: bytes) -> Path:
    path = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, prefix=".sdc-", suffix=".tmp", delete=False) as stream:
            path = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if path.read_bytes() != data:
            raise OSError("一時ファイルの保存内容が一致しません。")
        return path
    except BaseException:
        if path is not None:
            path.unlink(missing_ok=True)
        raise


def save(plan: Plan, output: Path, *, overwrite: bool = False, excel: bool = True) -> Saved:
    """全計算終了→一時保存・照合→バックアップ→再確認→一括置換。"""
    output = Path(output).expanduser().resolve()
    validate_output(plan, output, overwrite)
    verify_sources(plan)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "-" + uuid4().hex[:8]
    backup = output.with_name(output.name + f".{stamp}.bak") if overwrite else None
    report_path = output.with_name(output.name + f".{stamp}.report.json")
    excel_path = output.with_name(output.name + f".{stamp}.計算過程.xlsx") if excel else None
    report = dict(plan.report, output=str(output), backup=str(backup) if backup else None, run_id=stamp,
                  saved_at=datetime.now().astimezone().isoformat(), mode="saved",
                  report_path=str(report_path), excel_path=str(excel_path) if excel_path else None)
    workbook = excel_report.build(report)
    excel_data = workbook.to_xlsx() if excel else None
    if excel_data is not None:
        report["excel_sha256"] = digest(excel_data)
    report_data = (json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n").encode("utf-8")
    pending = []
    published = []
    def publish(staged, path, data):
        identity = staged.stat()
        publish_new(staged, path)
        published.append((path, identity.st_dev, identity.st_ino, data))
    try:
        staged = stage(output, plan.data)
        pending.append(staged)
        staged_report = stage(report_path, report_data)
        pending.append(staged_report)
        if excel_path is not None:
            staged_excel = stage(excel_path, excel_data)
            pending.append(staged_excel)
        if backup is not None:
            original = next(data for path, data in plan.sources if path == plan.target)
            staged_backup = stage(backup, original)
            pending.append(staged_backup)
            publish_new(staged_backup, backup)
        # 報告書の保存が失敗しても、変換対象はまだ変更していない。
        if excel_path is not None:
            publish(staged_excel, excel_path, excel_data)
        publish(staged_report, report_path, report_data)
        validate_output(plan, output, overwrite)
        verify_sources(plan)
        if overwrite:
            os.replace(staged, output)
        else:
            publish_new(staged, output)
    except BaseException as exc:
        for path, dev, ino, data in reversed(published):
            try:
                current = path.stat()
                if (current.st_dev, current.st_ino) == (dev, ino) and path.read_bytes() == data:
                    path.unlink()
            except OSError:
                exc.add_note(f"帳票を取り消せませんでした: {path}")
        if backup is not None and backup.exists():
            exc.add_note(f"バックアップは保持しています: {backup}")
        raise
    finally:
        for path in pending:
            path.unlink(missing_ok=True)
    return Saved(output, backup, report_path, excel_path, workbook)
