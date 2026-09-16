"""Check the notebook's input-to-report path, including parity with Quarto."""
import ast
from dataclasses import asdict
from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from beam import BeamInput, calculate
from sample_beam_marimo import app


@pytest.fixture(scope="module")
def notebook_run():
    return app.run()


def test_defaults_and_outputs_match_quarto(notebook_run):
    source = (Path(__file__).resolve().parents[1] / "sample-beam.qmd").read_text(
        encoding="utf-8"
    )
    setup = re.search(r"```\{python\}\n(.*?)\n```", source, re.S).group(1)
    assignment = next(
        node for node in ast.parse(setup).body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "p" for target in node.targets)
    )
    expected = BeamInput(**{
        keyword.arg: ast.literal_eval(keyword.value)
        for keyword in assignment.value.keywords
    })
    outputs, definitions = notebook_run
    assert definitions["p"] == expected
    assert definitions["r"] == calculate(expected)
    report = "\n".join(getattr(output, "text", "") for output in outputs)
    for heading in (
        "計算概要・入力条件", "解析モデル・断面性能", "荷重・反力・断面力",
        "応力・たわみの検討", "断面力図・変形図", "計算の追跡・適用範囲",
    ):
        assert heading in report
    for value in ("81.36", "14.52", "9.92", "0.519", "0.161", "0.496"):
        assert value in report
    assert report.count("data:image/png;base64,") == 2


@pytest.mark.parametrize("changes, passed", [
    ({"span_mm": 9000.0, "live_load_kn_m": 10.0}, False),
    ({"unit_weight_kn_m3": 0.0, "dead_load_kn_m": 0.0, "live_load_kn_m": 0.0}, True),
])
def test_applied_inputs_recompute_report(changes, passed):
    expected = BeamInput(**changes)
    outputs, definitions = app.run(defs={
        "beam_form": SimpleNamespace(value=asdict(expected)),
    })
    assert definitions["p"] == expected
    assert definitions["r"] == calculate(expected)
    assert definitions["r"].passed is passed
    report = "\n".join(getattr(output, "text", "") for output in outputs)
    assert f"{calculate(expected).deflection_mm:.2f}" in report
    assert ("全３項目が設定した制限値以内である。" if passed else "設定した制限値を超える項目がある。") in report


@pytest.mark.parametrize("changes", [
    {"height_mm": None}, {"web_mm": 150}, {"span_mm": 0}, {"live_load_kn_m": -1},
])
def test_form_rejects_invalid_values(notebook_run, changes):
    _, definitions = notebook_run
    values = asdict(BeamInput()) | changes
    assert definitions["validate_inputs"](values) is not None
