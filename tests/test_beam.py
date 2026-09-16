from dataclasses import replace

import pytest

from beam import BeamInput, calculate, response_at


def test_section_against_parallel_axis_theorem():
    p = BeamInput()
    r = calculate(p)
    hw = p.height_mm - 2 * p.flange_mm
    flange_area = p.width_mm * p.flange_mm
    offset = (p.height_mm - p.flange_mm) / 2
    independent_i = (
        2 * (p.width_mm * p.flange_mm**3 / 12 + flange_area * offset**2)
        + p.web_mm * hw**3 / 12
    )
    assert r.area_mm2 == pytest.approx(4533)
    assert r.inertia_mm4 == pytest.approx(independent_i)
    assert r.first_moment_mm3 == pytest.approx(261038.25)
    assert r.self_weight_kn_m == pytest.approx(0.3558405)


def test_equilibrium_boundaries_and_symmetry():
    p = BeamInput()
    r = calculate(p)
    left, middle, right = [response_at(p, x) for x in (0, p.span_mm / 2, p.span_mm)]
    assert 2 * r.reaction_n == pytest.approx(r.total_load_n_mm * p.span_mm)
    assert left[1:] == pytest.approx((0, 0))
    assert right[1:] == pytest.approx((0, 0))
    assert left[0] == pytest.approx(-right[0])
    assert middle == pytest.approx((0, r.moment_n_mm, r.deflection_mm))
    v1, m1, d1 = response_at(p, 1200)
    v2, m2, d2 = response_at(p, p.span_mm - 1200)
    assert (v1, m1, d1) == pytest.approx((-v2, m2, d2))


def test_deflection_satisfies_beam_equation():
    p = BeamInput()
    r = calculate(p)
    x, step = 2100.0, 1.0
    _, moment, displacement = response_at(p, x)
    curvature = (
        response_at(p, x - step)[2] - 2 * displacement + response_at(p, x + step)[2]
    ) / step**2
    assert curvature == pytest.approx(-moment / (p.young_n_mm2 * r.inertia_mm4), rel=1e-6)


def test_load_and_span_scaling():
    p = BeamInput(unit_weight_kn_m3=0)
    r = calculate(p)
    doubled = calculate(replace(p, dead_load_kn_m=10, live_load_kn_m=6))
    longer = calculate(replace(p, span_mm=12000))
    assert doubled.bending_n_mm2 == pytest.approx(2 * r.bending_n_mm2)
    assert doubled.shear_n_mm2 == pytest.approx(2 * r.shear_n_mm2)
    assert doubled.deflection_mm == pytest.approx(2 * r.deflection_mm)
    assert longer.reaction_n == pytest.approx(2 * r.reaction_n)
    assert longer.moment_n_mm == pytest.approx(4 * r.moment_n_mm)
    assert longer.deflection_mm == pytest.approx(16 * r.deflection_mm)


def test_zero_load_and_over_limit():
    unloaded = calculate(BeamInput(unit_weight_kn_m3=0, dead_load_kn_m=0, live_load_kn_m=0))
    assert (unloaded.reaction_n, unloaded.moment_n_mm, unloaded.deflection_mm) == (0, 0, 0)
    assert unloaded.passed
    assert calculate(BeamInput()).passed
    assert not calculate(BeamInput(live_load_kn_m=100)).passed


@pytest.mark.parametrize("changes", [
    {"span_mm": 0}, {"young_n_mm2": -1}, {"web_mm": 150},
    {"flange_mm": 150}, {"dead_load_kn_m": -1},
    {"height_mm": float("nan")}, {"live_load_kn_m": float("inf")},
    {"bending_limit_n_mm2": 0}, {"deflection_divisor": 0},
])
def test_invalid_inputs(changes):
    with pytest.raises(ValueError):
        BeamInput(**changes)


@pytest.mark.parametrize("x", [-1, 6001, float("nan")])
def test_invalid_position(x):
    with pytest.raises(ValueError):
        response_at(BeamInput(), x)
