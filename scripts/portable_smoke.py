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
                    assert len(app.tree.get_children()) == 207
                assert app.last_saved.output.is_file()
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
