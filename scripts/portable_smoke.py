"""ビルド済みEXEと通常Pythonの両方から動く配布検証。案件データを内包しない。"""

import json
from pathlib import Path
import sys
import tempfile
import time
import traceback
import tkinter as tk

import portable_converter as converter


def self_test(args, app_class):
    report = {"version": converter.VERSION, "frozen": bool(getattr(sys, "frozen", False)), "checks": []}
    root = None
    try:
        assert "地盤ばね".encode("cp932").decode("cp932") == "地盤ばね"
        report["checks"].append("cp932")
        root = tk.Tk()
        root.withdraw()
        app = app_class(root)
        root.update_idletasks()
        assert len(app.operations) == 4
        assert set(app.paths) == {"sdc", "ndu", "output"}
        assert all(str(button["state"]) == "normal" for button in app.operation_buttons.values())
        assert all(variable.get() for variable in app.operations.values())
        assert app.sdc_direction.get() == "transverse"
        assert app.pressure_case.get() == "non-response"
        report["checks"].append("tkinter-ui")
        if args.sdc or args.ndu:
            if not (args.sdc and args.ndu):
                raise ValueError("変換検証には --sdc と --ndu の両方が必要です。")
            originals = {p: p.read_bytes() for p in (args.sdc, args.ndu) if p}
            with tempfile.TemporaryDirectory(prefix="SDCConverter-日本語 ") as folder:
                destination = Path(folder)
                sdc_input = destination / args.sdc.name
                ndu_input = destination / args.ndu.name
                sdc_input.write_bytes(originals[args.sdc])
                ndu_input.write_bytes(originals[args.ndu])
                selected = tuple(args.self_test_groups)
                groups = tuple(f"{g}:{i}" for i,g in enumerate(selected,1))
                ndu = converter.base.parse_ndu(originals[args.ndu])
                members = {g: {m.number for m in converter.base.collect_members(ndu,{g:1})} for g in selected}
                request = converter.Request(sdc_input, ndu_input, groups=groups, shaft_profile="existing-screen")
                plan = converter.prepare(request)
                alternate = converter.prepare(converter.Request(
                    sdc_input, ndu_input, groups=groups, shaft_profile="existing-screen",
                    sdc_direction="longitudinal", pressure_case="response"))
                assert alternate.report["configuration"]["sdc_direction_label"] == "（１）橋軸方向"
                assert alternate.report["configuration"]["pressure_case_label"] == "・応答変位法の場合"
                assert alternate.workbook.sheet("有効抵抗土圧").rows[0][1].value == "（１）橋軸方向・応答変位法の場合"
                report["checks"].append("longitudinal-response-all-operations")
                output = destination / "変換 結果.ndu"
                saved = converter.save(plan, output)
                assert output.read_bytes() == plan.data
                audit = json.loads(saved.report.read_text(encoding="utf-8"))
                assert audit["output_sha256"] == converter.digest(output.read_bytes())
                rerun = converter.Request(sdc_input, output, operations=request.operations,
                                          groups=groups, shaft_profile="existing-screen")
                repeated = converter.prepare(rerun)
                assert repeated.data == plan.data
                replaced = converter.save(repeated, output, overwrite=True)
                assert replaced.backup.read_bytes() == plan.data
                report["checks"].append({"format": output.suffix, "rows": len(plan.rows),
                                         "sha256": converter.digest(plan.data), "backup": True})
                # GUIの実ボタンと同じ実行経路（ワーカー→イベントキュー→Tk表示）も通す。
                app.auto_open.set(False)
                app.paths["sdc"].set(str(sdc_input))
                app.paths["ndu"].set(str(ndu_input))
                app.paths["output"].set(str(destination / "GUI 保存.ndu"))
                # NDUの全KGを候補表示し、チェックボックス操作で図と変換対象を連動。
                view = app.model_preview
                selector = app.group_selector
                deadline = time.monotonic() + 15
                while (view.model is None or not selector.ready) and time.monotonic() < deadline:
                    root.update()
                    time.sleep(0.01)
                assert view.model is not None, view.message.get()
                assert selector.ready, selector.message.get()
                assert set(selector.rows) == set(range(1, 7))
                assert not view.selected
                assert selector.catalog.columns == tuple(sorted(converter.base.parse_sdc(originals[args.sdc])[0].values))
                app.sdc_direction.set("longitudinal")
                assert not selector.ready and not app.groups.get()
                deadline = time.monotonic() + 15
                while not selector.ready and time.monotonic() < deadline:
                    root.update()
                    time.sleep(0.01)
                assert selector.ready, selector.message.get()
                expected_columns = tuple(sorted(converter.base.parse_sdc(originals[args.sdc], "longitudinal")[0].values))
                assert selector.catalog.columns == expected_columns
                app.sdc_direction.set("transverse")
                deadline = time.monotonic() + 15
                while not selector.ready and time.monotonic() < deadline:
                    root.update()
                    time.sleep(0.01)
                assert selector.ready, selector.message.get()
                for group in selected:
                    selector.rows[group].check.invoke()
                assert selector.selection() == groups
                expected = set.union(*members.values())
                assert view.selected == expected
                assert len(view.canvas.find_withtag("selected")) == len(expected)
                for group in (selected[0], selected[2]):
                    selector.rows[group].check.invoke()
                assert view.selected == members[selected[1]]
                assert view.canvas.itemcget(f"element:{min(members[selected[1]])}", "fill") == "#dc2626"
                assert view.canvas.itemcget(f"element:{min(members[selected[0]])}", "fill") == "#475569"
                selector.rows[selected[1]].check.invoke()
                assert not view.selected and not view.canvas.find_withtag("selected")
                for row in selector.rows.values():
                    row.column.set("")
                for group in selected:
                    selector.rows[group].check.invoke()
                assert view.selected == expected
                report["checks"].append({"model-preview": "live-kg-highlighting", "elements": len(view.model.elements),
                                         "selected": len(view.selected)})
                # 短いKG候補はEXEでも無効。処理ロック解除後にも復活しない。
                shorter = destination / "杭長不一致.ndu"
                shortened = list(ndu.lines)
                index = ndu.records[f"KGInfo{selected[0]}"][0]
                fields = shortened[index].rstrip(b"\r\n").split(b",")
                fields[2] = str(int(fields[2])-1).encode("ascii")
                shortened[index] = b",".join(fields)+b"\r\n"
                shorter.write_bytes(b"".join(shortened))
                app.paths["ndu"].set(str(shorter))
                assert not app.groups.get() and not selector.ready
                deadline = time.monotonic() + 15
                while not selector.ready and time.monotonic() < deadline:
                    root.update()
                    time.sleep(0.01)
                assert selector.ready, selector.message.get()
                row = selector.rows[selected[0]]
                assert not row.candidate.enabled
                app.apply_states()
                row.check.invoke()
                assert row.check.instate(["disabled"]) and not row.selected.get()
                assert "長さ不一致" in row.candidate.reason
                report["checks"].append("kg-checkbox-length-mismatch-disabled")
                app.paths["ndu"].set(str(ndu_input))
                deadline = time.monotonic() + 15
                while not selector.ready and time.monotonic() < deadline:
                    root.update()
                    time.sleep(0.01)
                assert selector.ready, selector.message.get()
                for group in selected:
                    selector.rows[group].check.invoke()
                # 通常ボタンで列を変更し、5杭でもSDC3列目を共用できる。
                extra = [g for g in selector.rows if g not in selected][:2]
                for group in extra:
                    selector.rows[group].check.invoke()
                five = sorted((*selected,*extra),key=lambda g: (selector.rows[g].candidate.x,g))
                def assigned(direction):
                    maximum = max(selector.catalog.columns)
                    mapping = {g:min(i,maximum) for i,g in enumerate(five if direction=="left" else five[::-1],1)}
                    return tuple(f"{g}:{mapping[g]}" for g in sorted(mapping))
                selector.direction_buttons["right"].invoke()
                assert selector.selection() == assigned("right")
                selector.direction_buttons["left"].invoke()
                assert selector.selection() == assigned("left")
                for group in extra:
                    selector.rows[group].check.invoke()
                selector.direction_buttons["right"].invoke()
                assert selector.selection() == tuple(f"{g}:{3-i}" for i,g in enumerate(selected))
                assert app.request().push_direction == "direct"
                expected_plan = converter.prepare(app.request())
                assert all(row["pressure_column"] == row["column"]
                           for row in expected_plan.report["details"]["pressure"]["members"])
                report["checks"].append("direction-buttons-5-piles-shared-column-and-direct-pressure")
                failures = []
                app.show_error = lambda exc, trace: failures.append(trace)
                for write in (False, True):
                    app.run(write)
                    deadline = time.monotonic() + 15
                    while app.busy and time.monotonic() < deadline:
                        root.update()
                        time.sleep(0.01)
                    assert not app.busy, "GUI worker timed out"
                    assert not failures, failures
                    assert len(app.excel_preview.book.sheets) == 5
                    assert "杭周面の支持力" in app.excel_preview.sheet_box["values"]
                    assert app.excel_preview.sheet.name == "水平地盤ばね"
                    assert not {"変換結果", "入力根拠"} & set(app.excel_preview.sheet_box["values"])
                    assert len(app.excel_preview.canvas.find_all()) > 0
                    assert "入力値" not in app.excel_preview.sheet_box["values"]
                assert app.last_saved.output.is_file()
                assert app.last_saved.excel.is_file()
                assert app.last_saved.excel.read_bytes()[:2] == b"PK"
                assert str(app.open_excel_button["state"]) == "normal"
                assert app.last_saved.output.read_bytes() == expected_plan.data
                assert str(app.save_button["state"]) == "normal"
                report["checks"].append("gui-preview-and-save-worker")
            assert all(path.read_bytes() == original for path, original in originals.items())
            report["checks"].append("source-files-unchanged")
        report["ok"] = True
    except Exception:
        report["ok"] = False
        report["error"] = traceback.format_exc()
    finally:
        if root is not None:
            root.destroy()
    args.self_test.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if report["ok"] else 1
