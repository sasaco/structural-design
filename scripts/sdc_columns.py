"""SDCの実杭列と、集約されている原CSV欄の対応。計算モジュールには依存しない。"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re


class InputError(ValueError):
    """入力ファイルの不足・不整合。書き込み前に報告する。"""


PARITY = ("奇数列", "偶数列")
PRESSURE_PARITY = ("1列目", "2列目", "3列目以降奇数列", "4列目以降偶数列")
PRESSURE_TAIL = ("1列目", "2列目", "3列目以降")
LEGACY_SHARED_VERSION = "Ver.5.2.1"
LEGACY_SHARED_INTERPRETATION = "v5.2.1-shared"
PILE_COUNT_HEADERS = frozenset({
    "杭列数,奥行き本数,,1/β(m)",
    "杭列数,奥行き本数,,ｌ/β(m)",
})
DIRECTIONS = {
    "longitudinal": "（１）橋軸方向",
    "transverse": "（２）直角方向",
}
PRESSURE_CASES = {
    "non-response": "・応答変位法以外の場合",
    "response": "・応答変位法の場合",
}
CONDITIONS = {
    "seismic": "地震時",
    "liquefaction": "液状化時",
}
DEFAULT_DIRECTION = "transverse"
DEFAULT_PRESSURE_CASE = "non-response"


def choice_label(choices: dict[str, str], value: str, label: str) -> str:
    try:
        return choices[value]
    except KeyError as exc:
        options = " / ".join(choices)
        raise InputError(f"{label}は {options} から指定してください。") from exc


def direction_range(lines: list[str], direction: str = DEFAULT_DIRECTION) -> tuple[int, int]:
    """選択方向の見出し行と、次のトップレベル方向見出し直前を返す。"""
    heading = choice_label(DIRECTIONS, direction, "SDC参照方向")
    normalized = [line.strip() for line in lines]
    hits = [i for i, line in enumerate(normalized) if line == heading]
    if len(hits) != 1:
        raise InputError(f"SDC見出し『{heading}』が1個必要です。")
    begin = hits[0]
    end = next((i for i in range(begin + 1, len(lines))
                if re.fullmatch(r"[（(][０-９0-9]+[）)].*方向", normalized[i])), len(lines))
    return begin, end


def pressure_case_heading(pressure_case: str = DEFAULT_PRESSURE_CASE) -> str:
    return choice_label(PRESSURE_CASES, pressure_case, "有効抵抗土圧力の区分")


def detect_condition(value, aliases: dict[str, tuple], context: str) -> str:
    """既知の完全一致別名だけからSDCの採用条件を判定する。"""
    hits = [condition for condition, accepted in aliases.items() if value in accepted]
    if len(hits) != 1:
        raise InputError(f"{context}: 対応する条件見出しではありません。")
    return hits[0]


def require_same_condition(conditions, context: str) -> str:
    """関連表の条件が1種類だけであることを保証する。"""
    found = set(conditions)
    if len(found) != 1:
        labels = " / ".join(CONDITIONS.get(value, value) for value in sorted(found))
        raise InputError(f"{context}: 条件が一致しません（{labels}）。")
    return next(iter(found))


@dataclass(frozen=True)
class SourceColumn:
    field: int  # CSV欄番号、1始まり。行番号は各表・層が保持する。
    label: str
    interpretation: str = "numbered"


@dataclass(frozen=True)
class ColumnLayout:
    labels: tuple[str, ...]
    indices: dict[int, int]  # 実杭列番号 → この見出し内の0始まり位置
    kind: str

    def sources(self, offset=0, suffix="", interpretation=None):
        return {col: SourceColumn(offset + index + 1, self.labels[index] + suffix,
                                  interpretation or self.kind)
                for col, index in self.indices.items()}


def sdc_version(lines: list[str]) -> str:
    """バージョン情報を完全一致の見出しから一意に取得する。"""
    normalized = [line.strip() for line in lines]
    hits = [i for i, line in enumerate(normalized) if line == "【バージョン情報】"]
    if len(hits) != 1 or hits[0] + 1 >= len(lines) or not normalized[hits[0] + 1]:
        raise InputError("SDCのバージョン情報が1個必要です。")
    return normalized[hits[0] + 1]


def require_legacy_shared_version(lines: list[str], context: str) -> None:
    version = sdc_version(lines)
    if version != LEGACY_SHARED_VERSION:
        raise InputError(
            f"{context}: 共有値の省略見出しは {LEGACY_SHARED_VERSION} だけに対応しています。"
        )


def shared_layout(count: int | None, label: str, *, context="SDC") -> ColumnLayout:
    """Ver.5.2.1の1個の原欄を、観測済みの実杭列2本または3本へ展開する。"""
    if count not in (2, 3):
        raise InputError(f"{context}: Ver.5.2.1共有値形式の杭列数は2または3が必要です。")
    return ColumnLayout((label,), {column: 0 for column in range(1, count + 1)}, "shared")


def pressure_tail_layout(labels, count: int | None, *, context="SDC") -> ColumnLayout:
    """1列目・2列目・3列目以降を、観測済みの実杭列2本または3本へ展開する。"""
    labels = tuple(labels)
    if labels != PRESSURE_TAIL:
        raise InputError(f"{context}: 土圧の旧共有列見出しが一致しません。")
    if count not in (2, 3):
        raise InputError(f"{context}: Ver.5.2.1土圧形式の杭列数は2または3が必要です。")
    indices = {1: 0, 2: 1}
    if count == 3:
        indices[3] = 2
    return ColumnLayout(labels, indices, "pressure-tail")


def is_pile_count_header(line: str) -> bool:
    """数字1/β・全角英字ｌ/βの杭配置見出しを同じものとして扱う。"""
    return line.strip() in PILE_COUNT_HEADERS


def pile_count_header(lines: list[str], begin: int, end: int) -> int | None:
    """方向内にある杭配置見出しの行番号を返す。"""
    hits = [i for i in range(begin, end) if is_pile_count_header(lines[i])]
    if not hits:
        return None
    if len(hits) != 1:
        raise InputError("SDCの杭配置条件が重複しています。")
    return hits[0]


def pile_count(lines: list[str], begin: int, end: int) -> int | None:
    """方向内の配置表を読む。番号形式の部分入力には配置表なしも許す。"""
    i = pile_count_header(lines, begin, end)
    if i is None:
        return None
    if i + 2 >= end:
        raise InputError(f"SDC {i+1}行: 杭列数がありません。")
    fields = [s.strip() for s in lines[i+2].split(",")]
    if len(fields) != 4:
        raise InputError(f"SDC {i+3}行: 杭配置条件は4欄必要です。")
    try:
        n = Decimal(fields[0])
    except InvalidOperation as exc:
        raise InputError(f"SDC {i+3}行: 杭列数は正の整数が必要です。") from exc
    if not n.is_finite() or n <= 0 or n != n.to_integral_value():
        raise InputError(f"SDC {i+3}行: 杭列数は正の整数が必要です。")
    return int(n)


def resolve(labels, count=None, *, pressure=False, ordered=False, context="SDC") -> ColumnLayout:
    labels = tuple(labels)
    if not labels or len(set(labels)) != len(labels):
        raise InputError(f"{context}: 杭列の見出しが空、または重複しています。")
    expected = PRESSURE_PARITY if pressure else PARITY
    if labels == expected:
        if count is None:
            raise InputError(f"{context}: 奇数列・偶数列形式には杭配置条件の杭列数が必要です。")
        if pressure:
            indices = {col: col-1 if col <= 2 else (2 if col % 2 else 3)
                       for col in range(1, count+1)}
        else:
            indices = {col: (col-1) % 2 for col in range(1, count+1)}
        return ColumnLayout(labels, indices, "pressure-parity" if pressure else "parity")
    indices = {}
    for index, label in enumerate(labels):
        match = re.fullmatch(r"([1-9]\d*)列目", label)
        if not match:
            raise InputError(f"{context}: 杭列見出し『{label}』に対応していません。N列目形式または奇数列・偶数列形式が必要です。")
        indices[int(match[1])] = index
    n = count if count is not None else len(labels)
    if set(indices) != set(range(1, n+1)):
        raise InputError(f"{context}: 杭列番号は1から連続し、杭配置条件の杭列数と一致する必要があります。")
    if ordered and list(indices) != list(range(1, n+1)):
        raise InputError(f"{context}: 杭列は1列目から順に並べてください。")
    return ColumnLayout(labels, indices, "numbered")
