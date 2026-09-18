"""Canonical paths for repository-owned SDC/NDU test fixtures."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "tests" / "data"

IMACHO = DATA / "今町橋りょう4P"
IMACHO_RIGHT_SDC = IMACHO / "今町橋りょう4P(右).sdc"
IMACHO_LEFT_SDC = IMACHO / "今町橋りょう4P(左).sdc"
IMACHO_RIGHT_NDU = IMACHO / "今町橋りょう4P(C方向･右押し→).ndu"
IMACHO_LEFT_NDU = IMACHO / "今町橋りょう4P(C方向･左押し←).ndu"

NAKANOSAWA = DATA / "中ノ沢BLR2"
R2_SDC = NAKANOSAWA / "R2ラーメンばね1.sdc"
LIQUEFACTION_L1_SDC = NAKANOSAWA / "R2ラーメンばね1-液状化L1(液状化時用).sdc"
LIQUEFACTION_L1_LONGITUDINAL_NDU = NAKANOSAWA / "CDT_R2(液状化時用)L1.ndu"
LIQUEFACTION_L1_TRANSVERSE_NDU = NAKANOSAWA / "CDT_線路直角_端部電柱無し(液状化L1).ndu"
LIQUEFACTION_SDCS = (
    LIQUEFACTION_L1_SDC,
    NAKANOSAWA / "R2ラーメンばね1-液状化spc1(液状化時用).sdc",
    NAKANOSAWA / "R2ラーメンばね1-液状化spc2(液状化時用).sdc",
    NAKANOSAWA / "R2ラーメンばね1-液状化復旧性(液状化時用).sdc",
)

SAPPORO = DATA / "札幌駅P2橋脚"
SAPPORO_SDC = SAPPORO / "P1_φ1.3_L10.0(fr_載荷試験).sdc"
SAPPORO_LONGITUDINAL_NDU = SAPPORO / "P1_線路.ndu"
SAPPORO_TRANSVERSE_NDU = SAPPORO / "P1_直角.ndu"

SHINAGAWA = DATA / "品川(東タ)道路P6"
SHINAGAWA_SDC = SHINAGAWA / "東タ改良 P6橋脚(No.4).sdc"
SHINAGAWA_LONGITUDINAL_NDU = SHINAGAWA / "線路方向.ndu"
SHINAGAWA_TRANSVERSE_NDU = SHINAGAWA / "直角方向.ndu"
