"""NDUのXYモデル図と、入力されたKG範囲のライブ表示（標準ライブラリのみ）。"""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import ttk

import fill_jiban_shogen as base

NORMAL = "#475569"
SELECTED = "#dc2626"


@dataclass(frozen=True)
class Model:
    ndu: base.Ndu
    joints: dict[int, tuple[float, float]]
    elements: dict[int, tuple[int, int]]


def parse_model(raw: bytes) -> Model:
    ndu = base.parse_ndu(raw)
    joints, elements = {}, {}
    for key in ndu.records:
        if key.startswith("JointXY"):
            values = ndu.fields(key, 2)
            xy = tuple(float(base.number(value, key)) for value in values[:2])
            if not all(math.isfinite(value) for value in xy):
                raise base.InputError(f"{key}: 描画可能な座標範囲を超えています。")
            joints[int(key[7:])] = xy
        elif key.startswith("ElementInfo"):
            values = ndu.fields(key, 6)
            elements[int(key[11:])] = tuple(base.integer(value, key) for value in values[4:6])
    if not joints or not elements:
        raise base.InputError("NDUにモデル図の節点・部材データがありません。")
    for number, ends in elements.items():
        for joint in ends:
            if joint not in joints:
                raise base.InputError(f"部材{number}の節点{joint}がありません。")
    for axis in (0, 1):
        values = [point[axis] for point in joints.values()]
        if not math.isfinite(max(values) - min(values)):
            raise base.InputError("モデル図の座標範囲が大きすぎます。")
    return Model(ndu, joints, elements)


def selected_elements(model: Model, text: str) -> tuple[set[int], dict[int, int]]:
    """計算と同じKG:列の構文・KGInfo範囲を使う。計算条件はprepareで検証。"""
    groups = base.parse_groups(text.split())
    selected = set()
    for group in groups:
        fields = model.ndu.fields(f"KGInfo{group}", 3)
        start, end = (base.integer(value, f"KGInfo{group}の部材範囲") for value in fields[1:3])
        if start > end:
            raise base.InputError(f"KGInfo{group}: 開始・終了部材の順序が逆です。")
        members = {number for number in model.elements if start <= number <= end}
        if len(members) != end - start + 1:
            raise base.InputError(f"KGInfo{group}: 範囲内の部材データが不足しています。")
        if selected & members:
            raise base.InputError("複数のKGInfoに同じ部材が含まれています。")
        selected.update(members)
    return selected, groups


class ModelPreview(ttk.LabelFrame):
    def __init__(self, master, path: tk.StringVar, groups: tk.StringVar):
        super().__init__(master, text="入力NDUのモデル図", padding=8)
        self.path = path
        self.groups = groups
        self.model = None
        self.selected = set()
        self.generation = 0
        self.pending_load = None
        self.results = queue.Queue()
        self.zoom = 1.0
        self.pan = (0.0, 0.0)
        self.drag_origin = None
        self.message = tk.StringVar(value="入力NDUを選択するとモデル図を表示します。")
        self.show_numbers = tk.BooleanVar(value=True)
        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", pady=(0, 6))
        ttk.Button(toolbar, text="全体表示", command=self.fit, width=9).pack(side="left")
        ttk.Button(toolbar, text="＋", command=lambda: self.magnify(1.25), width=3).pack(side="left", padx=(4, 0))
        ttk.Button(toolbar, text="−", command=lambda: self.magnify(0.8), width=3).pack(side="left")
        ttk.Checkbutton(toolbar, text="対象部材番号", variable=self.show_numbers,
                        command=self.draw).pack(side="left", padx=6)
        ttk.Label(self, text="赤：選択したKGの部材   灰：その他", foreground=SELECTED).pack(anchor="w")
        self.message_label = ttk.Label(self, textvariable=self.message, justify="left", wraplength=310)
        self.message_label.pack(side="bottom", fill="x", pady=(6, 0))
        ttk.Label(self, text="XY表示（縦横別縮尺）／ホイールで拡大・ドラッグで移動",
                  wraplength=310, foreground="#4f5d70").pack(side="bottom", fill="x", pady=(6, 0))
        self.canvas = tk.Canvas(self, background="#ffffff", highlightthickness=1,
                                highlightbackground="#d5dce5", width=330, height=340)
        self.canvas.pack(fill="both", expand=True, pady=(6, 0))
        self.canvas.bind("<Configure>", self.resized)
        self.canvas.bind("<MouseWheel>", self.wheel)
        self.canvas.bind("<ButtonPress-1>", self.start_drag)
        self.canvas.bind("<B1-Motion>", self.drag)
        self.path_trace = path.trace_add("write", self.path_changed)
        self.groups_trace = groups.trace_add("write", self.update_selection)
        self.poll_id = self.after(75, self.poll)
        self.path_changed()

    def destroy(self):
        self.path.trace_remove("write", self.path_trace)
        self.groups.trace_remove("write", self.groups_trace)
        self.after_cancel(self.poll_id)
        if self.pending_load is not None:
            self.after_cancel(self.pending_load)
        super().destroy()

    def path_changed(self, *_):
        self.generation += 1
        if self.pending_load is not None:
            self.after_cancel(self.pending_load)
            self.pending_load = None
        self.model = None
        self.selected = set()
        self.message.set("モデル図を読み込み中…" if self.path.get().strip()
                         else "入力NDUを選択するとモデル図を表示します。")
        self.message_label.configure(foreground=NORMAL)
        self.fit()
        if self.path.get().strip():
            self.pending_load = self.after(250, self.load)

    def load(self):
        self.pending_load = None
        generation, text = self.generation, self.path.get().strip()

        def worker():
            try:
                path = Path(text).expanduser()
                if path.suffix.lower() != ".ndu" or not path.is_file():
                    raise base.InputError("既存のNDUファイルを選択してください。")
                result, error = parse_model(path.read_bytes()), None
            except (OSError, ValueError, ArithmeticError, UnicodeError) as exc:
                result, error = None, str(exc)
            self.results.put((generation, result, error))

        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        while True:
            try:
                generation, model, error = self.results.get_nowait()
            except queue.Empty:
                break
            # ファイルを選び直した後に古い読込結果で表示を戻さない。
            if generation != self.generation:
                continue
            self.model = model
            if error:
                self.message.set("モデル図を表示できません：" + error)
                self.message_label.configure(foreground=SELECTED)
                self.draw()
            else:
                self.update_selection()
        self.poll_id = self.after(75, self.poll)

    def update_selection(self, *_):
        self.selected = set()
        if self.model is None:
            return
        try:
            self.selected, groups = selected_elements(self.model, self.groups.get())
            mapping = " / ".join(f"KG{kg} → {column}列" for kg, column in groups.items())
            self.message.set(f"全{len(self.model.elements)}部材 ／ 対象{len(self.selected)}部材\n" +
                             (mapping or "対象のKGInfoをリストでチェックしてください。"))
            self.message_label.configure(foreground=NORMAL)
        except base.InputError as exc:
            self.message.set("杭の対応を確認してください：" + str(exc))
            self.message_label.configure(foreground=SELECTED)
        self.draw()

    def fit(self):
        self.zoom, self.pan = 1.0, (0.0, 0.0)
        self.draw()

    def resized(self, event):
        self.message_label.configure(wraplength=max(event.width - 10, 100))
        self.draw()

    def magnify(self, factor, x=None, y=None):
        new_zoom = min(12.0, max(0.5, self.zoom * factor))
        factor = new_zoom / self.zoom
        cx, cy = self.canvas.winfo_width() / 2, self.canvas.winfo_height() / 2
        dx, dy = (0 if x is None else x - cx), (0 if y is None else y - cy)
        self.pan = (dx - factor * (dx - self.pan[0]), dy - factor * (dy - self.pan[1]))
        self.zoom = new_zoom
        self.draw()

    def wheel(self, event):
        if event.delta:
            self.magnify(1.25 if event.delta > 0 else 0.8, event.x, event.y)
        return "break"

    def start_drag(self, event):
        self.drag_origin = (event.x, event.y, self.pan)

    def drag(self, event):
        if self.drag_origin is not None:
            x, y, pan = self.drag_origin
            self.pan = (pan[0] + event.x - x, pan[1] + event.y - y)
            self.draw()

    def draw(self):
        canvas = self.canvas
        canvas.delete("all")
        width, height = canvas.winfo_width(), canvas.winfo_height()
        if self.model is None:
            canvas.create_text(width / 2, height / 2, text="入力NDUのモデル図", fill="#94a3b8")
            return
        points = self.model.joints
        xmin, xmax = min(p[0] for p in points.values()), max(p[0] for p in points.values())
        ymin, ymax = min(p[1] for p in points.values()), max(p[1] for p in points.values())
        sx = max(width - 90, 1) / (xmax - xmin) if xmax != xmin else 1
        sy = max(height - 64, 1) / (ymax - ymin) if ymax != ymin else 1
        coords = {number: (width / 2 + ((x - xmin) - (xmax - xmin) / 2) * sx * self.zoom + self.pan[0],
                           height / 2 + ((y - ymin) - (ymax - ymin) / 2) * sy * self.zoom + self.pan[1])
                  for number, (x, y) in points.items()}
        # 非対象→対象の順に描画し、重なる場合も赤線を手前へ出す。
        for number, (i, j) in sorted(self.model.elements.items(), key=lambda item: item[0] in self.selected):
            x1, y1 = coords[i]
            x2, y2 = coords[j]
            selected = number in self.selected
            color = SELECTED if selected else NORMAL
            tags = ("element", f"element:{number}", "selected" if selected else "normal")
            if (x1, y1) == (x2, y2):
                canvas.create_oval(x1 - 3, y1 - 3, x1 + 3, y1 + 3, outline=color, width=2, tags=tags)
            else:
                canvas.create_line(x1, y1, x2, y2, fill=color, width=2.5 if selected else 1.2, tags=tags)
        for x, y in coords.values():
            canvas.create_oval(x - 1.8, y - 1.8, x + 1.8, y + 1.8, fill=NORMAL, outline="", tags="joint")
        if self.show_numbers.get():
            for number in sorted(self.selected):
                i, j = self.model.elements[number]
                x1, y1 = coords[i]
                x2, y2 = coords[j]
                canvas.create_text((x1 + x2) / 2 + 5, (y1 + y2) / 2, text=str(number),
                                   fill=SELECTED, anchor="w", font=("Segoe UI", 9), tags="member-label")
