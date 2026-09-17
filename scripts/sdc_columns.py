"""SDCの実杭列と、集約されている原CSV欄の対応。計算モジュールには依存しない。"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re


class InputError(ValueError):
    """入力ファイルの不足・不整合。書き込み前に報告する。"""


PARITY = ("奇数列", "偶数列")
PRESSURE_PARITY = ("1列目", "2列目", "3列目以降奇数列", "4列目以降偶数列")


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


def pile_count(lines: list[str], begin: int, end: int) -> int | None:
    """方向内の配置表を読む。番号形式の部分入力には配置表なしも許す。"""
    hits = [i for i in range(begin, end) if lines[i].strip() == "杭列数,奥行き本数,,1/β(m)"]
    if not hits:
        return None
    if len(hits) != 1:
        raise InputError("SDCの杭配置条件が重複しています。")
    i = hits[0]
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
