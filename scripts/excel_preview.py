"""Excel出力と同じセル・書式・数式を表示する読取専用Tkプレビュー。Excel不要。"""

from __future__ import annotations

from bisect import bisect_right
from decimal import Decimal
import tkinter as tk
from tkinter import ttk

from excel_report import address


class ExcelPreview(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.book = None
        self.sheet = None
        self.pending = None
        self.selected = None
        self.sheet_name = tk.StringVar()
        self.section_name = tk.StringVar()
        self.zoom = tk.StringVar(value="100%")
        self.cell_name = tk.StringVar(value="セル")
        self.formula = tk.StringVar(value="「Excelをプレビュー」で計算過程を表示します。")
        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", pady=(0,5))
        ttk.Label(toolbar,text="シート").pack(side="left")
        self.sheet_box = ttk.Combobox(toolbar,textvariable=self.sheet_name,state="readonly",width=20)
        self.sheet_box.pack(side="left",padx=(6,14))
        self.sheet_box.bind("<<ComboboxSelected>>",self.select_sheet)
        ttk.Label(toolbar,text="表示位置").pack(side="left")
        self.section_box = ttk.Combobox(toolbar,textvariable=self.section_name,state="readonly",width=33)
        self.section_box.pack(side="left",padx=6)
        self.section_box.bind("<<ComboboxSelected>>",self.go_section)
        zoom = ttk.Combobox(toolbar,textvariable=self.zoom,values=("75%","100%","125%","150%"),state="readonly",width=6)
        zoom.pack(side="right")
        zoom.bind("<<ComboboxSelected>>",self.layout)
        ttk.Label(toolbar,text="倍率").pack(side="right",padx=6)
        formula_bar=ttk.Frame(self)
        formula_bar.pack(fill="x",pady=(0,5))
        ttk.Label(formula_bar,textvariable=self.cell_name,width=13).pack(side="left")
        self.formula_entry=ttk.Entry(formula_bar,textvariable=self.formula,state="readonly")
        self.formula_entry.pack(side="left",fill="x",expand=True)
        grid=ttk.Frame(self)
        grid.pack(fill="both",expand=True)
        grid.rowconfigure(0,weight=1)
        grid.columnconfigure(0,weight=1)
        self.canvas=tk.Canvas(grid,background="white",highlightthickness=1,highlightbackground="#D5DEE8",height=245)
        self.canvas.grid(row=0,column=0,sticky="nsew")
        vertical=ttk.Scrollbar(grid,orient="vertical",command=self.yview)
        vertical.grid(row=0,column=1,sticky="ns")
        horizontal=ttk.Scrollbar(grid,orient="horizontal",command=self.xview)
        horizontal.grid(row=1,column=0,sticky="ew")
        self.canvas.configure(yscrollcommand=vertical.set,xscrollcommand=horizontal.set)
        self.canvas.bind("<Configure>",self.schedule)
        self.canvas.bind("<MouseWheel>",self.wheel)
        self.canvas.bind("<Shift-MouseWheel>",lambda e:self.wheel(e,True))
        self.canvas.bind("<Button-1>",self.select_cell)
        self.canvas.bind("<Double-1>",self.follow_link)
        self.canvas.bind("<Destroy>",self.destroyed)

    def destroyed(self,_event):
        if self.pending:
            self.after_cancel(self.pending)
            self.pending=None

    def clear(self, text="設定が変わりました。「Excelをプレビュー」で更新してください。"):
        self.book=self.sheet=None
        self.sheet_box.configure(values=())
        self.section_box.configure(values=())
        self.sheet_name.set("")
        self.section_name.set("")
        self.cell_name.set("セル")
        self.formula.set(text)
        self.canvas.delete("all")
        self.canvas.configure(scrollregion=(0,0,0,0))

    def show(self,book):
        self.book=book
        names=[s.name for s in book.sheets]
        self.sheet_box.configure(values=names)
        self.sheet_name.set(names[0])
        self.select_sheet()

    def select_sheet(self,_event=None):
        if not self.book: return
        self.sheet=self.book.sheet(self.sheet_name.get())
        self.selected=None
        self.section_box.configure(values=[name for name,_ in self.sheet.sections])
        self.section_name.set(self.sheet.sections[0][0] if self.sheet.sections else "")
        self.formula.set("セルを選択すると数式・値を表示します。青いリンクをダブルクリックすると計算明細へ移動します。")
        self.cell_name.set("セル")
        self.layout()
        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)
        self.schedule()

    def layout(self,_event=None):
        if not self.sheet:return
        self.scale=int(self.zoom.get().strip("%"))/100
        self.xs=[48*self.scale]
        for width in self.sheet.widths:self.xs.append(self.xs[-1]+(width*7+8)*self.scale)
        self.ys=[26*self.scale]
        for r in range(len(self.sheet.rows)):
            self.ys.append(self.ys[-1]+self.sheet.heights.get(r,23)*4/3*self.scale)
        self.canvas.configure(scrollregion=(0,0,self.xs[-1],self.ys[-1]),yscrollincrement=24*self.scale,xscrollincrement=24*self.scale)
        self.schedule()

    def go_section(self,_event=None):
        if not self.sheet:return
        index=next((r for name,r in self.sheet.sections if name==self.section_name.get()),0)
        self.canvas.yview_moveto(max(0,self.ys[index]-26*self.scale)/self.ys[-1])
        self.schedule()

    def yview(self,*args):self.canvas.yview(*args);self.schedule()
    def xview(self,*args):self.canvas.xview(*args);self.schedule()

    def wheel(self,event,horizontal=False):
        if self.sheet:
            if horizontal:self.canvas.xview_scroll(-int(event.delta/120)*3,"units")
            else:self.canvas.yview_scroll(-int(event.delta/120)*3,"units")
            self.schedule()
        return "break"

    def schedule(self,_event=None):
        if self.pending is None:self.pending=self.after_idle(self.draw)

    def draw(self):
        self.pending=None
        self.canvas.delete("all")
        if not self.sheet:return
        cv=self.canvas
        x0,y0=cv.canvasx(0),cv.canvasy(0)
        width,height=cv.winfo_width(),cv.winfo_height()
        first=max(0,bisect_right(self.ys,y0)-1)
        last=min(len(self.sheet.rows),bisect_right(self.ys,y0+height)+1)
        normal=("Yu Gothic UI",max(8,round(10*self.scale)))
        bold=(*normal,"bold")
        for r in range(first,last):
            row=self.sheet.rows[r]
            y1,y2=self.ys[r],self.ys[r+1]
            if r in self.sheet.merges:
                cell=row[0]
                fill="#E6EDF5" if cell.style=="section" else "white"
                cv.create_rectangle(x0+self.xs[0],y1,x0+max(width,self.xs[-1]),y2,fill=fill,outline="")
                font=("Yu Gothic UI",round(15*self.scale),"bold") if cell.style=="title" else bold if cell.style=="section" else normal
                cv.create_text(x0+self.xs[0]+8,y1+5,anchor="nw",text=cell.display(),font=font,fill="#243B53",width=max(width-self.xs[0]-25,200))
            else:
                # スクロール領域を先に描き、識別列A:Cを手前に固定する。
                for c in [*range(3,len(row)),*range(min(3,len(row)))]:
                    cell=row[c]
                    left=self.xs[c]+(x0 if c<3 else 0)
                    right=self.xs[c+1]+(x0 if c<3 else 0)
                    if right<x0 or left>x0+width:continue
                    fill="#243B53" if cell.style=="header" else "#FFF2C6" if cell.style=="actual" else "#F8FAFC" if r%2 else "white"
                    color="white" if cell.style=="header" else "#175CAD" if cell.style in ("source","link") else "#202B3C"
                    if (r,c) in self.sheet.checks and cell.cached != 0:fill,color="#FDE5E5","#B42318"
                    cv.create_rectangle(left,y1,right,y2,fill=fill,outline="#E3E8EF")
                    value=cell.cached if cell.formula else cell.value
                    number=isinstance(value,(float,int,Decimal))
                    text=cell.display()
                    # 一覧ではセル幅で折り返し、完全な文字列は選択時のバーで読む。
                    cv.create_text(right-6 if number else left+6,(y1+y2)/2,anchor="e" if number else "w",text=text,font=bold if cell.style=="header" else normal,fill=color,width=right-left-12)
                    if self.selected==(r,c):cv.create_rectangle(left+1,y1+1,right-1,y2-1,outline="#1976B9",width=2)
            cv.create_rectangle(x0,y1,x0+self.xs[0],y2,fill="#F0F4F8",outline="#D5DEE8")
            cv.create_text(x0+self.xs[0]-6,(y1+y2)/2,text=str(r+1),anchor="e",font=normal,fill="#526174")
        # Excel列記号を上端に固定する。
        for c in [*range(3,12),0,1,2]:
            left=self.xs[c]+(x0 if c<3 else 0)
            right=self.xs[c+1]+(x0 if c<3 else 0)
            cv.create_rectangle(left,y0,right,y0+self.ys[0],fill="#F0F4F8",outline="#D5DEE8")
            cv.create_text((left+right)/2,y0+self.ys[0]/2,text=address(0,c)[:-1],font=normal,fill="#526174")
        cv.create_rectangle(x0,y0,x0+self.xs[0],y0+self.ys[0],fill="#F0F4F8",outline="#D5DEE8")

    def select_cell(self,event):
        if not self.sheet:return
        x,y=self.canvas.canvasx(event.x),self.canvas.canvasy(event.y)
        if event.y<self.ys[0]:return
        c=bisect_right(self.xs,event.x if event.x<self.xs[3] else x)-1
        r=bisect_right(self.ys,y)-1
        if r<0 or r>=len(self.sheet.rows):return
        if r in self.sheet.merges:c=0
        if c<0 or c>=len(self.sheet.rows[r]):return
        self.selected=(r,c)
        cell=self.sheet.rows[r][c]
        self.cell_name.set(address(r,c))
        self.formula.set("="+cell.formula.text(self.sheet.name)+"    値: "+str(cell.cached) if cell.formula else "（空欄）" if cell.value is None else str(cell.value))
        self.schedule()

    def follow_link(self,event):
        self.select_cell(event)
        if not self.selected:return
        r,c=self.selected
        link=self.sheet.rows[r][c].link
        if link:
            name,r,c=link
            self.sheet_name.set(name)
            self.select_sheet()
            self.canvas.yview_moveto(max(0,self.ys[r]-self.ys[0])/self.ys[-1])
            self.selected=(r,c)
            self.schedule()
