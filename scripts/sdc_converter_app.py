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


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title(f"SDC Converter  {converter.VERSION}")
        root.geometry(f"1060x{min(860, root.winfo_screenheight() - 100)}")
        root.minsize(920, 720)
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.report_callback_exception = self.callback_error
        self.events = queue.Queue()
        self.busy = False
        self.last_saved = None
        self.mode = tk.StringVar(value="NDU")
        self.paths = {key: tk.StringVar() for key in ("sdc", "ndu", "ndt", "output")}
        self.groups = tk.StringVar(value=" ".join(converter.DEFAULT_GROUPS))
        self.direction = tk.StringVar(value="右押し")
        self.overwrite = tk.BooleanVar(value=False)
        self.auto_open = tk.BooleanVar(value=True)
        self.operations = {key: tk.BooleanVar(value=True) for key in converter.OPERATIONS}
        self.status = tk.StringVar(value="SDCと入力NDUを選択してください。")
        self.controls = []
        self.preview_rows = []
        self.build_ui()
        self.mode_changed()
        for variable in [self.mode, self.groups, self.direction, self.overwrite,
                         *self.paths.values(), *self.operations.values()]:
            variable.trace_add("write", self.invalidate)
        root.after(100, self.poll)

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
        ttk.Label(container, text="SDC → NDU / NDT", style="Title.TLabel").pack(anchor="w")
        ttk.Label(container, text="地盤ばね・土圧・杭支持力の入力", style="Hint.TLabel").pack(anchor="w", pady=(2, 12))
        source = ttk.LabelFrame(container, text="1  入力ファイル", padding=12)
        source.pack(fill="x")
        source.columnconfigure(1, weight=1)
        options = ttk.Frame(source)
        options.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        ttk.Label(options, text="更新する形式：").pack(side="left")
        for mode in ("NDU", "NDT"):
            self.control(ttk.Radiobutton(options, text=mode, variable=self.mode, value=mode,
                                         command=self.mode_changed)).pack(side="left", padx=8)
        for row, key, label in ((1, "sdc", "参照SDC"), (2, "ndu", "入力・参照NDU"), (3, "ndt", "更新対象NDT")):
            ttk.Label(source, text=label, width=16).grid(row=row, column=0, sticky="w", pady=4)
            entry = self.control(ttk.Entry(source, textvariable=self.paths[key]))
            entry.grid(row=row, column=1, sticky="ew", padx=8)
            button = self.control(ttk.Button(source, text="選択…", command=lambda k=key: self.select(k)))
            button.grid(row=row, column=2)
            if key == "ndt":
                self.ndt_controls = (entry, button)
        ttk.Label(source, text="NDTの更新には、杭グループ・座標・部材接続を照合するNDUも必要です。",
                  style="Hint.TLabel").grid(row=4, column=0, columnspan=3, sticky="w", pady=(6, 0))
        settings = ttk.LabelFrame(container, text="2  入力する項目と杭の対応", padding=12)
        settings.pack(fill="x", pady=10)
        self.operation_buttons = {}
        for i, (key, label) in enumerate(converter.OPERATIONS.items()):
            button = self.control(ttk.Checkbutton(settings, text=label, variable=self.operations[key]))
            button.grid(row=0, column=i, sticky="w", padx=(0, 16), pady=(0, 8))
            self.operation_buttons[key] = button
        mapping = ttk.Frame(settings)
        mapping.grid(row=1, column=0, columnspan=4, sticky="w")
        ttk.Label(mapping, text="KG番号:モデル列").pack(side="left")
        self.control(ttk.Entry(mapping, textvariable=self.groups, width=24)).pack(side="left", padx=8)
        ttk.Label(mapping, text="土圧の方向").pack(side="left", padx=(12, 0))
        self.direction_box = self.control(ttk.Combobox(mapping, textvariable=self.direction, state="readonly",
                                                       values=("右押し", "左押し"), width=10))
        self.direction_box.pack(side="left", padx=8)
        ttk.Label(settings, style="Hint.TLabel",
                  text="対応：右基礎SDCの直角方向・短期。既定の杭対応は 4:1 5:2 6:3。\n"
                       "周面は既存画面方式（押込みK1を正負の全勾配、Fyを正負の両制限値に設定）。",
                  justify="left").grid(row=2, column=0, columnspan=4, sticky="w", pady=(8, 0))
        destination = ttk.LabelFrame(container, text="3  保存先", padding=12)
        destination.pack(fill="x")
        destination.columnconfigure(0, weight=1)
        self.output_entry = self.control(ttk.Entry(destination, textvariable=self.paths["output"]))
        self.output_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.output_button = self.control(ttk.Button(destination, text="保存先…", command=self.select_output))
        self.output_button.grid(row=0, column=1)
        choices = ttk.Frame(destination)
        choices.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.control(ttk.Checkbutton(choices, text="元ファイルを更新（日時付きバックアップを作成）",
                                    variable=self.overwrite, command=self.output_changed)).pack(side="left")
        self.control(ttk.Checkbutton(choices, text="完了後、関連付けアプリで開く",
                                    variable=self.auto_open)).pack(side="left", padx=16)
        preview = ttk.LabelFrame(container, text="4  入力値の確認", padding=8)
        preview.columnconfigure(0, weight=1)
        preview.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(preview, columns=("kind", "target", "before", "after"), show="headings", height=5)
        for key, label, width in (("kind", "項目", 150), ("target", "対象", 90),
                                  ("before", "現在値", 330), ("after", "入力値", 330)):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=width, stretch=key in ("before", "after"))
        self.tree.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(preview, orient="vertical", command=self.tree.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(preview, orient="horizontal", command=self.tree.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.tree.bind("<Double-1>", self.row_detail)
        ttk.Label(preview, text="行をダブルクリックすると全フィールドを表示します。", style="Hint.TLabel").grid(
            row=2, column=0, sticky="w", pady=(4, 0))
        actions = ttk.Frame(container)
        # フッターの領域を先に確保し、一覧を残りへ配置する。
        # 小さなウィンドウでも保存ボタンが一覧の下へ隠れない。
        self.status_label = ttk.Label(container, textvariable=self.status, wraplength=960, justify="left")
        self.status_label.pack(side="bottom", fill="x", pady=(10, 0))
        actions.pack(side="bottom", fill="x")
        self.control(ttk.Button(actions, text="入力値を確認", command=lambda: self.run(False))).pack(side="left")
        self.save_button = self.control(ttk.Button(actions, text="変換して保存", command=lambda: self.run(True)))
        self.save_button.pack(side="left", padx=8)
        self.open_button = ttk.Button(actions, text="保存したファイルを開く", command=self.open_result, state="disabled")
        self.open_button.pack(side="left", padx=8)
        self.progress = ttk.Progressbar(actions, mode="indeterminate", length=120)
        self.progress.pack(side="right")
        preview.pack(fill="both", expand=True, pady=10)

    def invalidate(self, *_):
        if not self.busy:
            self.tree.delete(*self.tree.get_children())
            self.preview_rows = []
            self.status.set("設定を変更しました。「入力値を確認」で更新できます。")

    def mode_changed(self):
        for key in ("horizontal", "pressure"):
            self.operations[key].set(self.mode.get() == "NDU")
        self.output_changed()

    def apply_states(self):
        for widget in self.controls:
            widget.configure(state="disabled" if self.busy else "normal")
        if self.busy:
            return
        self.direction_box.configure(state="readonly")
        for widget in self.ndt_controls:
            widget.configure(state="normal" if self.mode.get() == "NDT" else "disabled")
        for key in ("horizontal", "pressure"):
            self.operation_buttons[key].configure(state="normal" if self.mode.get() == "NDU" else "disabled")
        for widget in (self.output_entry, self.output_button):
            widget.configure(state="disabled" if self.overwrite.get() else "normal")

    def target(self):
        return self.paths["ndt" if self.mode.get() == "NDT" else "ndu"].get().strip()

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
            if key == ("ndt" if self.mode.get() == "NDT" else "ndu"):
                self.output_changed()

    def select_output(self):
        suffix = "." + self.mode.get().lower()
        path = filedialog.asksaveasfilename(parent=self.root, title="別名で保存（既存ファイルは上書きしません）",
                                           defaultextension=suffix, confirmoverwrite=False,
                                           initialfile=Path(self.paths["output"].get()).name,
                                           filetypes=[(self.mode.get() + "ファイル", "*" + suffix)])
        if path:
            self.paths["output"].set(path)

    def request(self):
        needed = ("sdc", "ndu", "ndt") if self.mode.get() == "NDT" else ("sdc", "ndu")
        for key in needed:
            if not self.paths[key].get().strip():
                raise converter.InputError(f"{key.upper()}ファイルを選択してください。")
        return converter.Request(
            Path(self.paths["sdc"].get().strip()), Path(self.paths["ndu"].get().strip()),
            Path(self.paths["ndt"].get().strip()) if self.mode.get() == "NDT" else None,
            tuple(key for key, selected in self.operations.items() if selected.get()),
            tuple(self.groups.get().split()), "right" if self.direction.get() == "右押し" else "left",
            shaft_profile="existing-screen",
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
        except converter.InputError as exc:
            messagebox.showwarning("入力を確認してください", str(exc), parent=self.root)
            return
        self.busy = True
        self.last_saved = None
        self.open_button.configure(state="disabled")
        self.apply_states()
        self.progress.start(12)
        self.status.set("計算と入力ファイルの検証を実行しています…")

        def worker():
            try:
                plan = converter.prepare(request)
                saved = converter.save(plan, output, overwrite=overwrite) if write else None
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
                exc, trace = failure
                self.status.set("処理を完了できませんでした。入力・保存先を確認してください。")
                self.show_error(exc, trace)
            else:
                self.tree.delete(*self.tree.get_children())
                self.preview_rows = list(plan.rows)
                for i, row in enumerate(plan.rows):
                    self.tree.insert("", "end", iid=str(i), values=(row.operation, row.target, row.before, row.after))
                if saved:
                    self.last_saved = saved
                    self.open_button.configure(state="normal")
                    self.status.set(f"保存しました：{saved.output}\n報告書：{saved.report.name}" +
                                    (f"\nバックアップ：{saved.backup.name}" if saved.backup else ""))
                    if self.auto_open.get():
                        self.open_result()
                else:
                    self.status.set(f"確認完了：{len(plan.rows)}件。ファイルは変更していません。")
        self.root.after(100, self.poll)

    def row_detail(self, _event=None):
        selection = self.tree.selection()
        if selection:
            row = self.preview_rows[int(selection[0])]
            messagebox.showinfo(f"{row.operation} / {row.target}",
                                "現在値\n" + row.before.replace(" / ", "\n") +
                                "\n\n入力値\n" + row.after.replace(" / ", "\n"), parent=self.root)

    def open_result(self):
        if self.last_saved:
            try:
                os.startfile(self.last_saved.output)
            except OSError as exc:
                messagebox.showwarning("変換・保存は完了しました",
                                       f"関連付けアプリで開けませんでした。\n{self.last_saved.output}\n\n{exc}", parent=self.root)

    def show_error(self, exc, trace):
        if isinstance(exc, (converter.InputError, OSError, UnicodeError, DecimalException)):
            messagebox.showerror("変換エラー", str(exc), parent=self.root)
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
    parser.add_argument("--ndt", type=Path)
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
