"""入力ファイルから更新するKGInfoチェックリスト。Tk操作はメインスレッドのみ。"""

from dataclasses import dataclass
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import ttk

import fill_jiban_shogen as base
from kg_candidates import inspect_candidates, round_length, validate_groups


@dataclass
class Row:
    candidate: object
    selected: tk.BooleanVar
    column: tk.StringVar
    check: ttk.Checkbutton
    combo: ttk.Combobox


class KGSelection(ttk.Frame):
    def __init__(self, master, ndu_path, sdc_path, groups):
        super().__init__(master)
        self.ndu_path, self.sdc_path, self.groups = ndu_path, sdc_path, groups
        self.rows = {}
        self.catalog = None
        self.ready = False
        self.locked = False
        self.generation = 0
        self.pending_load = None
        self.results = queue.Queue()
        self.message = tk.StringVar()
        header = ttk.Frame(self)
        header.pack(fill="x")
        ttk.Label(header, text="対象KGInfo / 部材範囲・長さ合計 → SDCモデル列").pack(side="left")
        self.reload_button = ttk.Button(header, text="再読込", command=self.path_changed, width=7)
        self.reload_button.pack(side="right")
        self.canvas = tk.Canvas(self, height=130, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        scrollbar.pack(side="right", fill="y", pady=(4, 0))
        self.canvas.pack(fill="x", pady=(4, 0))
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.list_frame = ttk.Frame(self.canvas)
        self.window = self.canvas.create_window(0, 0, window=self.list_frame, anchor="nw")
        self.list_frame.columnconfigure(3, weight=1)
        self.list_frame.bind("<Configure>", lambda _: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(self.window, width=event.width))
        self.canvas.bind("<MouseWheel>", self.wheel)
        ttk.Label(self, textvariable=self.message, wraplength=565, justify="left").pack(fill="x", pady=(4, 0))
        self.traces = [(path, path.trace_add("write", self.path_changed)) for path in (ndu_path, sdc_path)]
        self.poll_id = self.after(75, self.poll)
        self.path_changed()

    def wheel(self, event):
        self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
        return "break"

    def destroy(self):
        for path, trace in self.traces:
            path.trace_remove("write", trace)
        self.after_cancel(self.poll_id)
        if self.pending_load is not None:
            self.after_cancel(self.pending_load)
        super().destroy()

    def path_changed(self, *_):
        self.generation += 1
        if self.pending_load is not None:
            self.after_cancel(self.pending_load)
            self.pending_load = None
        self.ready = False
        self.catalog = None
        self.groups.set("")
        self.rows.clear()
        for widget in self.list_frame.winfo_children():
            widget.destroy()
        self.canvas.yview_moveto(0)
        self.message.set("KGInfoを読み込み中…" if self.ndu_path.get().strip() else "入力NDUを選択してください。")
        if self.ndu_path.get().strip():
            self.pending_load = self.after(250, self.load)

    def load(self):
        self.pending_load = None
        generation = self.generation
        ndu_text, sdc_text = self.ndu_path.get().strip(), self.sdc_path.get().strip()

        def worker():
            try:
                ndu = Path(ndu_text).expanduser()
                if ndu.suffix.lower() != ".ndu":
                    raise base.InputError("NDUファイルを選択してください。")
                raw = ndu.read_bytes()
                sdc_raw, sdc_error = None, ""
                if sdc_text:
                    try:
                        sdc = Path(sdc_text).expanduser()
                        if sdc.suffix.lower() != ".sdc":
                            raise base.InputError("SDCファイルを選択してください。")
                        sdc_raw = sdc.read_bytes()
                    except (OSError, ValueError) as exc:
                        sdc_error = "SDCを読み込めません：" + str(exc)
                catalog, error = inspect_candidates(raw, sdc_raw, sdc_error), None
            except (OSError, ValueError, ArithmeticError, UnicodeError) as exc:
                catalog, error = None, str(exc)
            self.results.put((generation, catalog, error))

        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        while True:
            try:
                generation, catalog, error = self.results.get_nowait()
            except queue.Empty:
                break
            if generation != self.generation:
                continue
            self.catalog = catalog
            if error:
                self.message.set("KGInfoを読み込めません：" + error)
            else:
                self.render()
        self.poll_id = self.after(75, self.poll)

    def render(self):
        self.ready = not self.catalog.error
        for index, candidate in enumerate(self.catalog.candidates):
            selected, column = tk.BooleanVar(value=False), tk.StringVar()
            check = ttk.Checkbutton(self.list_frame, text=f"KGInfo{candidate.group}", variable=selected,
                                    command=lambda group=candidate.group: self.toggled(group))
            check.grid(row=index, column=0, sticky="w", pady=2)
            span = f"{candidate.start}～{candidate.end}" if candidate.start is not None else "範囲不明"
            length = f"{round_length(candidate.length):.3f} m" if candidate.length is not None else "算出不可"
            ttk.Label(self.list_frame, text=f"部材{span} / {length}").grid(row=index, column=1, sticky="w", padx=6)
            combo = ttk.Combobox(self.list_frame, textvariable=column, values=self.catalog.columns, width=3,
                                 state="disabled")
            combo.grid(row=index, column=2, padx=4)
            combo.bind("<<ComboboxSelected>>", lambda _: self.sync_groups())
            ttk.Label(self.list_frame, text=candidate.reason or "一致", wraplength=210,
                      foreground="#687585" if candidate.enabled else "#8b5e3c").grid(row=index, column=3, sticky="w", padx=4)
            self.rows[candidate.group] = Row(candidate, selected, column, check, combo)
        self.set_locked(self.locked)
        self.update_message()

    def toggled(self, group):
        row = self.rows[group]
        if not row.candidate.enabled or self.locked:
            row.selected.set(False)
        elif row.selected.get():
            used = {other.column.get() for key, other in self.rows.items() if key != group and other.selected.get()}
            if not row.column.get() or row.column.get() in used:
                row.column.set(next((str(col) for col in self.catalog.columns if str(col) not in used), ""))
        self.sync_groups()
        self.set_locked(self.locked)

    def sync_groups(self):
        self.groups.set(" ".join(f"{group}:{row.column.get()}" for group, row in self.rows.items()
                                 if row.selected.get() and row.candidate.enabled))
        self.update_message()

    def update_message(self):
        if self.catalog.error:
            self.message.set(self.catalog.error)
            return
        enabled = sum(item.enabled for item in self.catalog.candidates)
        selected = sum(row.selected.get() for row in self.rows.values())
        self.message.set(f"SDC地層厚合計：{round_length(self.catalog.thickness):.3f} m ／ "
                         f"選択可能 {enabled}/{len(self.rows)}件・選択 {selected}件\n"
                         "合計は小数第3位に四捨五入して比較。対象をチェックし、SDCの列を確認してください。")

    def set_locked(self, locked):
        self.locked = locked
        self.reload_button.configure(state="disabled" if locked else "normal")
        for row in self.rows.values():
            enabled = self.ready and row.candidate.enabled and not locked
            row.check.configure(state="normal" if enabled else "disabled")
            row.combo.configure(state="readonly" if enabled and row.selected.get() else "disabled")

    def selection(self):
        if not self.ready:
            raise base.InputError(self.message.get())
        for row in self.rows.values():
            if row.selected.get() and not row.column.get():
                raise base.InputError(f"KGInfo{row.candidate.group}のSDCモデル列を選択してください。")
        groups = base.parse_groups(self.groups.get().split())
        validate_groups(self.catalog, groups)
        return tuple(f"{group}:{column}" for group, column in groups.items())
