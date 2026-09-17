"""KGInfoの候補とSDC地層厚・鉛直杭部材長の整合判定。"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

import fill_jiban_shogen as base


def round_length(value: Decimal) -> Decimal:
    """合計値の比較・表示用。小数第4位を四捨五入し、0.001 m単位にする。"""
    return value.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Candidate:
    group: int
    start: int | None
    end: int | None
    length: Decimal | None
    reason: str
    x: Decimal | None = None

    @property
    def enabled(self):
        return not self.reason


@dataclass(frozen=True)
class Catalog:
    candidates: tuple[Candidate, ...]
    thickness: Decimal | None
    columns: tuple[int, ...]
    error: str = ""


def inspect_candidates(ndu_raw: bytes, sdc_raw: bytes | None, sdc_error: str = "") -> Catalog:
    ndu = base.parse_ndu(ndu_raw)
    groups = sorted(int(key[6:]) for key in ndu.records if key.startswith("KGInfo"))
    if not groups:
        raise base.InputError("入力NDUにKGInfoがありません。")
    thickness, columns = None, ()
    error = sdc_error or "参照SDCを選択してください。"
    if sdc_raw is not None:
        try:
            layers = base.parse_sdc(sdc_raw)
            thickness = sum((layer.bottom - layer.top for layer in layers), base.ZERO)
            columns = tuple(sorted(layers[0].values))
            error = ""
        except (ValueError, ArithmeticError, UnicodeError) as exc:
            error = "SDCを読み込めません：" + str(exc)
    candidates = []
    for group in groups:
        start = end = length = x = None
        reason = ""
        try:
            fields = ndu.fields(f"KGInfo{group}", 3)
            start, end = (base.integer(value, f"KGInfo{group}の部材範囲") for value in fields[1:3])
            # 端節点、鉛直性、連続性、欠損も変換本体と同じ条件で確認する。
            members = base.collect_members(ndu, {group: 1})
            length = sum((member.bottom - member.top for member in members), base.ZERO)
            joint = ndu.fields(f"ElementInfo{members[0].number}", 6)[4]
            x = base.number(ndu.fields(f"JointXY{base.integer(joint, '節点番号')}", 2)[0], "杭のx座標")
            if thickness is None:
                reason = "SDC未確認"
            elif round_length(length) != round_length(thickness):
                difference = round_length(length) - round_length(thickness)
                reason = f"長さ不一致（差 {difference:.3f} m）"
        except (ValueError, ArithmeticError, UnicodeError) as exc:
            reason = str(exc)
        candidates.append(Candidate(group, start, end, length, reason, x))
    return Catalog(tuple(candidates), thickness, columns, error)


def validate_groups(catalog: Catalog, groups: dict[int, int]) -> None:
    if catalog.error:
        raise base.InputError(catalog.error)
    if not groups:
        raise base.InputError("対象のKGInfoを1つ以上チェックしてください。")
    candidates = {item.group: item for item in catalog.candidates}
    for group, column in groups.items():
        item = candidates.get(group)
        if item is None:
            raise base.InputError(f"KGInfo{group}が入力NDUにありません。")
        if not item.enabled:
            raise base.InputError(f"KGInfo{group}は選択できません：{item.reason}")
        if column not in catalog.columns:
            raise base.InputError(f"SDCに{column}列目がありません。")


def assign_columns(catalog: Catalog, selected: list[int], direction: str) -> dict[int, int]:
    """選択杭をx座標順に並べ、押す側から1・2・3（以後3）を割り当てる。"""
    if direction not in ("right", "left"):
        raise base.InputError("列の自動設定は right / left を指定してください。")
    # 現在の手入力列ではなく、候補と選択KGの妥当性を確認する。
    validate_groups(catalog, dict.fromkeys(selected, 1))
    candidates = {item.group: item for item in catalog.candidates}
    ordered = sorted(selected, key=lambda group: (candidates[group].x, group))
    count = len(ordered)
    groups = {group: min(count-index if direction == "right" else index+1, 3)
              for index, group in enumerate(ordered)}
    validate_groups(catalog, groups)
    return groups
