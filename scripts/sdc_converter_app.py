"""SDC Converter: Tkinter GUI / PyInstaller windowed entry point."""

from __future__ import annotations

import argparse
from decimal import DecimalException
import os
from pathlib import Path
import queue
import tempfile
import threading
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import portable_converter as converter
from model_preview import ModelPreview
from excel_preview import ExcelPreview
from kg_selection import KGSelection


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title(f"SDC Converter  {converter.VERSION}")
        root.geometry(f"{min(1320, root.winfo_screenwidth() - 80)}x{min(860, root.winfo_screenheight() - 100)}")
        root.minsize(1060, 720)
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.report_callback_exception = self.callback_error
        self.events = queue.Queue()
        self.busy = False
        self.last_saved = None
        self.paths = {key: tk.StringVar() for key in ("sdc", "ndu", "output")}
        self.groups = tk.StringVar()
        self.direction = tk.StringVar(value="右押し")
        self.overwrite = tk.BooleanVar(value=False)
        self.auto_open = tk.BooleanVar(value=True)
        self.excel_output = tk.BooleanVar(value=True)
        self.operations = {key: tk.BooleanVar(value=True) for key in converter.OPERATIONS}
        self.status = tk.StringVar(value="SDCと入力NDUを選択してください。")
        self.controls = []
        self.build_ui()
        self.output_changed()
        for variable in [self.groups, self.direction, self.overwrite, self.excel_output,
                         *self.paths.values(), *self.operations.values()]:
            variable.trace_add("write", self.invalidate)
        self.poll_id = root.after(100, self.poll)
        root.bind("<Destroy>", self.destroyed, add="+")

    def destroyed(self, event):
        if event.widget is self.root:
            self.root.after_cancel(self.poll_id)

    def control(self, widget):
        self.controls.append(widget)
        return widget

    def build_ui(self):
        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Title.TLabel", font=("Yu Gothic UI", 19, "bold"))
        style.configure("Hint.TLabel", foreground="#4f5d70")
        style.configure("Treeview", rowheight=29)
        container = ttk.Frame(self.root, padding=20)
        container.pack(fill="both", expand=True)
        ttk.Label(container, text="SDC → NDU", style="Title.TLabel").pack(anchor="w")
        ttk.Label(container, text="地盤ばね・土圧・杭支持力の入力", style="Hint.TLabel").pack(anchor="w", pady=(2, 12))
        self.notebook = ttk.Notebook(container)
        body = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(body, text="入力・保存設定とモデル")
        body.columnconfigure(0, weight=1, minsize=650)
        body.columnconfigure(1, weight=2, minsize=350)
        body.rowconfigure(0, weight=1)
        form_area = ttk.Frame(body)
        form_area.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        form_canvas = tk.Canvas(form_area, highlightthickness=0, width=630)
        form_scroll = ttk.Scrollbar(form_area, orient="vertical", command=form_canvas.yview)
        form_scroll.pack(side="right", fill="y")
        form_canvas.pack(fill="both", expand=True)
        form_canvas.configure(yscrollcommand=form_scroll.set)
        form = ttk.Frame(form_canvas)
        form_window = form_canvas.create_window(0, 0, window=form, anchor="nw")
        form.bind("<Configure>", lambda _: form_canvas.configure(scrollregion=form_canvas.bbox("all")))
        form_canvas.bind("<Configure>", lambda event: form_canvas.itemconfigure(form_window, width=event.width))
        self.model_preview = ModelPreview(body, self.paths["ndu"], self.groups)
        self.model_preview.grid(row=0, column=1, sticky="nsew")
        source = ttk.LabelFrame(form, text="1  入力ファイル", padding=12)
        source.pack(fill="x")
        source.columnconfigure(1, weight=1)
        for row, key, label in ((0, "sdc", "参照SDC"), (1, "ndu", "入力NDU")):
            ttk.Label(source, text=label, width=16).grid(row=row, column=0, sticky="w", pady=4)
            entry = self.control(ttk.Entry(source, textvariable=self.paths[key]))
            entry.grid(row=row, column=1, sticky="ew", padx=8)
            button = self.control(ttk.Button(source, text="選択…", command=lambda k=key: self.select(k)))
            button.grid(row=row, column=2)
        settings = ttk.LabelFrame(form, text="2  入力する項目と杭の対応", padding=12)
        settings.pack(fill="x", pady=10)
        settings.columnconfigure(0, weight=1)
        settings.columnconfigure(1, weight=1)
        self.operation_buttons = {}
        for i, (key, label) in enumerate(converter.OPERATIONS.items()):
            button = self.control(ttk.Checkbutton(settings, text=label, variable=self.operations[key]))
            button.grid(row=i // 2, column=i % 2, sticky="w", padx=(0, 16), pady=(0, 8))
            self.operation_buttons[key] = button
        self.group_selector = KGSelection(settings, self.paths["ndu"], self.paths["sdc"], self.groups)
        self.group_selector.grid(row=2, column=0, columnspan=2, sticky="ew")
        mapping = ttk.Frame(settings)
        mapping.grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(mapping, text="土圧の方向").pack(side="left")
        self.direction_box = self.control(ttk.Combobox(mapping, textvariable=self.direction, state="readonly",
                                                       values=("右押し", "左押し"), width=10))
        self.direction_box.pack(side="left", padx=8)
        ttk.Label(settings, style="Hint.TLabel",
                  text="対応：右基礎SDCの直角方向・短期。モデル列は左から右へ指定。\n"
                       "周面は既存画面方式（押込みK1を正負の全勾配、Fyを正負の両制限値に設定）。",
                  justify="left", wraplength=570).grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))
        destination = ttk.LabelFrame(form, text="3  保存先", padding=12)
        destination.pack(fill="x")
        destination.columnconfigure(0, weight=1)
        self.output_entry = self.control(ttk.Entry(destination, textvariable=self.paths["output"]))
        self.output_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.output_button = self.control(ttk.Button(destination, text="保存先…", command=self.select_output))
        self.output_button.grid(row=0, column=1)
        choices = ttk.Frame(destination)
        choices.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.control(ttk.Checkbutton(choices, text="元ファイルを更新（日時付きバックアップを作成）",
                                    variable=self.overwrite, command=self.output_changed)).pack(anchor="w")
        self.control(ttk.Checkbutton(choices, text="完了後、関連付けアプリで開く",
                                    variable=self.auto_open)).pack(anchor="w")
        self.control(ttk.Checkbutton(choices, text="計算過程をExcelに保存", variable=self.excel_output)).pack(anchor="w")
        ttk.Label(destination, text="Excelはモデルと同じフォルダーに、モデル名.実行ID.計算過程.xlsx として保存します。",
                  style="Hint.TLabel", wraplength=600).grid(row=2,column=0,columnspan=2,sticky="w",pady=(8,0))
        self.excel_preview = ExcelPreview(self.notebook)
        self.notebook.add(self.excel_preview,text="計算過程Excel")
        actions = ttk.Frame(container)
        # フッターの領域を先に確保し、一覧を残りへ配置する。
        # 小さなウィンドウでも保存ボタンが一覧の下へ隠れない。
        self.status_label = ttk.Label(container, textvariable=self.status, wraplength=960, justify="left")
        self.status_label.pack(side="bottom", fill="x", pady=(10, 0))
        actions.pack(side="bottom", fill="x")
        self.control(ttk.Button(actions, text="Excelをプレビュー", command=lambda: self.run(False))).pack(side="left")
        self.save_button = self.control(ttk.Button(actions, text="変換して保存", command=lambda: self.run(True)))
        self.save_button.pack(side="left", padx=8)
        self.open_button = ttk.Button(actions, text="保存したファイルを開く", command=self.open_result, state="disabled")
        self.open_button.pack(side="left", padx=8)
        self.open_excel_button = ttk.Button(actions,text="計算過程Excelを開く",command=self.open_excel,state="disabled")
        self.open_excel_button.pack(side="left",padx=8)
        self.progress = ttk.Progressbar(actions, mode="indeterminate", length=120)
        self.progress.pack(side="right")
        self.notebook.pack(fill="both",expand=True,pady=(0,10))

    def invalidate(self, *_):
        if not self.busy:
            self.excel_preview.clear()
            self.last_saved = None
            self.open_button.configure(state="disabled")
            self.open_excel_button.configure(state="disabled")
            self.status.set("設定を変更しました。「Excelをプレビュー」で更新できます。")

    def apply_states(self):
        for widget in self.controls:
            widget.configure(state="disabled" if self.busy else "normal")
        self.group_selector.set_locked(self.busy)
        if self.busy:
            return
        self.direction_box.configure(state="readonly")
        for widget in (self.output_entry, self.output_button):
            widget.configure(state="disabled" if self.overwrite.get() else "normal")

    def target(self):
        return self.paths["ndu"].get().strip()

    def output_changed(self):
        target = self.target()
        if target:
            path = Path(target)
            self.paths["output"].set(str(path if self.overwrite.get() else path.with_name(path.stem + "_変換済み" + path.suffix)))
        else:
            self.paths["output"].set("")
        self.apply_states()

    def select(self, key):
        path = filedialog.askopenfilename(parent=self.root, title=f"{key.upper()}ファイルを選択",
                                          filetypes=[(f"{key.upper()}ファイル", f"*.{key}")])
        if path:
            self.paths[key].set(path)
            if key == "ndu":
                self.output_changed()

    def select_output(self):
        suffix = ".ndu"
        path = filedialog.asksaveasfilename(parent=self.root, title="別名で保存（既存ファイルは上書きしません）",
                                           defaultextension=suffix, confirmoverwrite=False,
                                           initialfile=Path(self.paths["output"].get()).name,
                                           filetypes=[("NDUファイル", "*" + suffix)])
        if path:
            self.paths["output"].set(path)

    def request(self):
        for key in ("sdc", "ndu"):
            if not self.paths[key].get().strip():
                raise converter.InputError(f"{key.upper()}ファイルを選択してください。")
        return converter.Request(
            Path(self.paths["sdc"].get().strip()), Path(self.paths["ndu"].get().strip()),
            operations=tuple(key for key, selected in self.operations.items() if selected.get()),
            groups=self.group_selector.selection(),
            push_direction="right" if self.direction.get() == "右押し" else "left",
            shaft_profile="existing-screen",
            require_matching_lengths=True,
        )

    def run(self, write):
        if self.busy:
            return
        try:
            request = self.request()
            overwrite = self.overwrite.get()
            if overwrite or not self.paths["output"].get().strip():
                self.output_changed()
            output = Path(self.paths["output"].get().strip())
            excel = self.excel_output.get()
        except converter.InputError as exc:
            messagebox.showwarning("入力を確認してください", str(exc), parent=self.root)
            return
        self.busy = True
        self.last_saved = None
        self.open_button.configure(state="disabled")
        self.open_excel_button.configure(state="disabled")
        self.excel_preview.clear("Excel帳票を作成しています…")
        self.apply_states()
        self.progress.start(12)
        self.status.set("計算と入力ファイルの検証を実行しています…")

        def worker():
            try:
                plan = converter.prepare(request)
                saved = converter.save(plan, output, overwrite=overwrite, excel=excel) if write else None
                self.events.put((plan, saved, None))
            except Exception as exc:
                self.events.put((None, None, (exc, traceback.format_exc())))

        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        try:
            plan, saved, failure = self.events.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            self.progress.stop()
            self.apply_states()
            if failure:
                self.excel_preview.clear("処理を完了できませんでした。入力・保存先を確認してください。")
                exc, trace = failure
                self.status.set("処理を完了できませんでした。入力・保存先を確認してください。")
                self.show_error(exc, trace)
            else:
                self.excel_preview.show(saved.workbook if saved else plan.workbook)
                self.notebook.select(self.excel_preview)
                if saved:
                    self.last_saved = saved
                    self.open_button.configure(state="normal")
                    self.open_excel_button.configure(state="normal" if saved.excel else "disabled")
                    self.status.set(f"保存しました：{saved.output}\n報告書：{saved.report.name}" +
                                    (f"\nExcel：{saved.excel.name}" if saved.excel else "") +
                                    (f"\nバックアップ：{saved.backup.name}" if saved.backup else ""))
                    if self.auto_open.get():
                        self.open_result()
                else:
                    self.status.set(f"Excelプレビュー：{len(plan.workbook.sheets)}シート・計算対象{len(plan.rows)}件。モデル・帳票は未保存です。")
        self.poll_id = self.root.after(100, self.poll)

    def open_excel(self):
        if self.last_saved and self.last_saved.excel:
            try:
                os.startfile(self.last_saved.excel)
            except OSError as exc:
                messagebox.showwarning("Excelの保存は完了しました", f"関連付けアプリで開けませんでした。\n{self.last_saved.excel}\n\n{exc}", parent=self.root)

    def open_result(self):
        if self.last_saved:
            try:
                os.startfile(self.last_saved.output)
            except OSError as exc:
                messagebox.showwarning("変換・保存は完了しました",
                                       f"関連付けアプリで開けませんでした。\n{self.last_saved.output}\n\n{exc}", parent=self.root)

    def show_error(self, exc, trace):
        if isinstance(exc, (converter.InputError, OSError, UnicodeError, DecimalException)):
            messagebox.showerror("変換エラー", "\n".join([str(exc),*getattr(exc,"__notes__",[])]), parent=self.root)
            return
        log = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / converter.APP_NAME / "error.log"
        try:
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a", encoding="utf-8") as stream:
                stream.write(trace + "\n")
            detail = f"\n詳細：{log}"
        except OSError:
            detail = ""
        messagebox.showerror("予期しないエラー", str(exc) + detail, parent=self.root)

    def callback_error(self, exc_type, exc, tb):
        self.show_error(exc, "".join(traceback.format_exception(exc_type, exc, tb)))

    def close(self):
        if self.busy:
            self.status.set("処理中です。完了後に閉じてください。")
        else:
            self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description="SDC Converter")
    parser.add_argument("--self-test", type=Path, help="配布検証レポートの保存先")
    parser.add_argument("--sdc", type=Path)
    parser.add_argument("--ndu", type=Path)
    args = parser.parse_args()
    if args.self_test:
        from portable_smoke import self_test
        return self_test(args, App)
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
