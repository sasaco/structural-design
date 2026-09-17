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
        report["checks"].append("tkinter-ui")
        if args.sdc or args.ndu:
            if not (args.sdc and args.ndu):
                raise ValueError("変換検証には --sdc と --ndu の両方が必要です。")
            originals = {p: p.read_bytes() for p in (args.sdc, args.ndu) if p}
            with tempfile.TemporaryDirectory(prefix="SDCConverter-日本語 ") as folder:
                destination = Path(folder)
                request = converter.Request(args.sdc, args.ndu, shaft_profile="existing-screen")
                plan = converter.prepare(request)
                output = destination / "変換 結果.ndu"
                saved = converter.save(plan, output)
                assert output.read_bytes() == plan.data
                audit = json.loads(saved.report.read_text(encoding="utf-8"))
                assert audit["output_sha256"] == converter.digest(output.read_bytes())
                rerun = converter.Request(args.sdc, output, operations=request.operations,
                                          shaft_profile="existing-screen")
                repeated = converter.prepare(rerun)
                assert repeated.data == plan.data
                replaced = converter.save(repeated, output, overwrite=True)
                assert replaced.backup.read_bytes() == plan.data
                report["checks"].append({"format": output.suffix, "rows": len(plan.rows),
                                         "sha256": converter.digest(plan.data), "backup": True})
                # GUIの実ボタンと同じ実行経路（ワーカー→イベントキュー→Tk表示）も通す。
                app.auto_open.set(False)
                app.paths["sdc"].set(str(args.sdc))
                app.paths["ndu"].set(str(args.ndu))
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
                for group in (4, 5, 6):
                    selector.rows[group].check.invoke()
                assert selector.selection() == converter.DEFAULT_GROUPS
                expected = set(range(98, 122)) | set(range(123, 147)) | set(range(148, 172))
                assert view.selected == expected
                assert len(view.canvas.find_withtag("selected")) == 72
                for group in (4, 6):
                    selector.rows[group].check.invoke()
                assert view.selected == set(range(123, 147))
                assert view.canvas.itemcget("element:123", "fill") == "#dc2626"
                assert view.canvas.itemcget("element:98", "fill") == "#475569"
                selector.rows[5].check.invoke()
                assert not view.selected and not view.canvas.find_withtag("selected")
                for row in selector.rows.values():
                    row.column.set("")
                for group in (4, 5, 6):
                    selector.rows[group].check.invoke()
                assert view.selected == expected
                report["checks"].append({"model-preview": "live-kg-highlighting", "elements": len(view.model.elements),
                                         "selected": len(view.selected)})
                # 短いKG候補はEXEでも無効。処理ロック解除後にも復活しない。
                shorter = destination / "杭長不一致.ndu"
                shorter.write_bytes(args.ndu.read_bytes().replace(b"KGInfo4=21,98,121", b"KGInfo4=21,98,120"))
                app.paths["ndu"].set(str(shorter))
                assert not app.groups.get() and not selector.ready
                deadline = time.monotonic() + 15
                while not selector.ready and time.monotonic() < deadline:
                    root.update()
                    time.sleep(0.01)
                assert selector.ready, selector.message.get()
                row = selector.rows[4]
                assert not row.candidate.enabled
                app.apply_states()
                row.check.invoke()
                assert row.check.instate(["disabled"]) and not row.selected.get()
                assert "長さ不一致" in row.candidate.reason
                report["checks"].append("kg-checkbox-length-mismatch-disabled")
                app.paths["ndu"].set(str(args.ndu))
                deadline = time.monotonic() + 15
                while not selector.ready and time.monotonic() < deadline:
                    root.update()
                    time.sleep(0.01)
                assert selector.ready, selector.message.get()
                for group in (4, 5, 6):
                    selector.rows[group].check.invoke()
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
                    assert len(app.excel_preview.book.sheets) == 6
                    assert app.excel_preview.sheet.name == "変換結果"
                    assert len(app.excel_preview.canvas.find_all()) > 0
                    assert "入力値" not in app.excel_preview.sheet_box["values"]
                assert app.last_saved.output.is_file()
                assert app.last_saved.excel.is_file()
                assert app.last_saved.excel.read_bytes()[:2] == b"PK"
                assert str(app.open_excel_button["state"]) == "normal"
                assert app.last_saved.output.read_bytes() == converter.prepare(
                    converter.Request(args.sdc, args.ndu, shaft_profile="existing-screen")).data
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
