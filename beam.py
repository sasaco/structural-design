"""等分布荷重を受ける対称 I 形単純梁の線形弾性計算。

内部単位は N, mm。1 kN/m = 1 N/mm。
断面はフィレットを除いた矩形の組合せ。基準への適合判定は扱わない。
"""

from dataclasses import dataclass, fields
from math import isfinite


@dataclass(frozen=True)
class BeamInput:
    span_mm: float = 6000.0
    height_mm: float = 300.0
    width_mm: float = 150.0
    web_mm: float = 6.5
    flange_mm: float = 9.0
    young_n_mm2: float = 205000.0
    unit_weight_kn_m3: float = 78.5
    dead_load_kn_m: float = 5.0  # 梁自重を含まない
    live_load_kn_m: float = 3.0
    bending_limit_n_mm2: float = 156.7  # サンプル用の仮定値
    shear_limit_n_mm2: float = 90.0  # サンプル用の仮定値
    deflection_divisor: float = 300.0  # サンプル用 L/300

    def __post_init__(self) -> None:
        nonnegative = {"unit_weight_kn_m3", "dead_load_kn_m", "live_load_kn_m"}
        for field in fields(self):
            value = getattr(self, field.name)
            if not isfinite(value):
                raise ValueError(f"{field.name} must be finite")
            if value < 0 or (value == 0 and field.name not in nonnegative):
                raise ValueError(f"{field.name} has an invalid sign or is zero")
        if self.web_mm >= self.width_mm:
            raise ValueError("web_mm must be smaller than width_mm")
        if 2 * self.flange_mm >= self.height_mm:
            raise ValueError("2 * flange_mm must be smaller than height_mm")


@dataclass(frozen=True)
class BeamResult:
    area_mm2: float
    inertia_mm4: float
    modulus_mm3: float
    first_moment_mm3: float
    self_weight_kn_m: float
    total_load_n_mm: float
    reaction_n: float
    moment_n_mm: float
    shear_n: float
    bending_n_mm2: float
    shear_n_mm2: float
    deflection_mm: float
    deflection_limit_mm: float
    bending_ratio: float
    shear_ratio: float
    deflection_ratio: float

    @property
    def passed(self) -> bool:
        """設定した３項目の比較結果。部材全体の安全判定ではない。"""
        return max(self.bending_ratio, self.shear_ratio, self.deflection_ratio) <= 1


def calculate(p: BeamInput) -> BeamResult:
    h, b, tw, tf = p.height_mm, p.width_mm, p.web_mm, p.flange_mm
    hw = h - 2 * tf
    area = 2 * b * tf + hw * tw
    inertia = (b * h**3 - (b - tw) * hw**3) / 12
    modulus = inertia / (h / 2)
    # 中立軸より上側の断面一次モーメント: 上フランジ + ウェブ上半分
    first_moment = b * tf * (h / 2 - tf / 2) + (tw * hw / 2) * (hw / 4)
    self_weight = area * 1e-6 * p.unit_weight_kn_m3
    w = p.dead_load_kn_m + p.live_load_kn_m + self_weight
    length = p.span_mm
    reaction = w * length / 2
    moment = w * length**2 / 8
    bending = moment / modulus
    shear = reaction * first_moment / (inertia * tw)
    deflection = 5 * w * length**4 / (384 * p.young_n_mm2 * inertia)
    deflection_limit = length / p.deflection_divisor
    return BeamResult(
        area, inertia, modulus, first_moment, self_weight, w,
        reaction, moment, reaction, bending, shear, deflection, deflection_limit,
        bending / p.bending_limit_n_mm2,
        shear / p.shear_limit_n_mm2,
        deflection / deflection_limit,
    )


def response_at(p: BeamInput, x_mm: float) -> tuple[float, float, float]:
    """左端から x の V [N], M [N mm], 下向きたわみ [mm]。"""
    if not isfinite(x_mm) or not 0 <= x_mm <= p.span_mm:
        raise ValueError("x_mm must be finite and within the span")
    r = calculate(p)
    x, length, w = x_mm, p.span_mm, r.total_load_n_mm
    shear = w * (length / 2 - x)
    moment = w * x * (length - x) / 2
    displacement = w * x * (length**3 - 2 * length * x**2 + x**3) / (
        24 * p.young_n_mm2 * r.inertia_mm4
    )
    return shear, moment, displacement
