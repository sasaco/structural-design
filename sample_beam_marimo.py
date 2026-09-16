import marimo

__generated_with = "0.23.16"
app = marimo.App(width="medium", app_title="構造計算書 | 鋼製単純梁")


@app.cell(hide_code=True)
def _():
    import marimo as mo
    import matplotlib as mpl
    from matplotlib.figure import Figure
    from matplotlib.patches import Polygon, Rectangle, Circle
    import numpy as np
    from dataclasses import asdict
    from io import BytesIO
    from math import isfinite
    from tabulate import tabulate
    from beam import BeamInput, calculate, response_at

    return (
        BeamInput,
        BytesIO,
        Circle,
        Figure,
        Polygon,
        Rectangle,
        asdict,
        calculate,
        isfinite,
        mo,
        mpl,
        np,
        response_at,
        tabulate,
    )


@app.cell(hide_code=True)
def _(BytesIO, mo, tabulate):
    plot_style = {
        "font.family": ["Meiryo", "DejaVu Sans"],
        "font.size": 10,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.unicode_minus": False,
        "figure.dpi": 160,
        "savefig.bbox": "tight",
        "svg.fonttype": "path",
    }
    ink, teal, orange = "#17354a", "#147b80", "#be6b2c"

    def table(rows, headers):
        return mo.md(tabulate(rows, headers=headers, tablefmt="pipe", disable_numparse=True))

    def equation(text):
        return mo.md("$$\n" + text + "\n$$")

    def status(ratio):
        return "OK" if ratio <= 1.0 else "NG"

    def figure_image(figure, alt):
        # Explicit PNG rendering also works in script/export mode, without a GUI backend.
        buffer = BytesIO()
        figure.savefig(buffer, format="png", dpi=160, bbox_inches="tight")
        return mo.image(buffer.getvalue(), alt=alt, width="100%")

    return equation, figure_image, ink, orange, plot_style, status, table, teal


@app.cell(hide_code=True)
def _(mo):
    mo.Html("""
    <style>
      .markdown { font-family: "Meiryo", "Yu Gothic", sans-serif; line-height: 1.85; }
      .markdown h1, .markdown h2, .markdown h3 { color: #17354a; }
      .markdown h2 { border-bottom: 2px solid #147b80; padding-bottom: .5rem; }
      .markdown table { width: 100%; font-size: .93rem; }
      .markdown thead { border-bottom: 2px solid #147b80; }
      .markdown td, .markdown th { padding: .45rem .7rem; }
      @media print {
        @page { size: A4; margin: 18mm 20mm; }
        .beam-inputs { display: none !important; }
        .markdown { font-size: 10pt; }
        table, img, .katex-display { break-inside: avoid; }
        h2, h3 { break-after: avoid; }
      }
    </style>
    """)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    # 構造計算書

    **鋼製単純梁 / 等分布荷重（サンプル）**
    2026年09月12日 · marimo 版

    元の計算書と同じ入力条件・計算式による計算例です。
    以下の入力を変更し、**「条件を適用・再計算」** を押すと、本文・表・図・判定が更新されます。
    初期表示とコマンドからの HTML 出力には、元の計算書の条件を使用します。
    """)
    return


@app.cell(hide_code=True)
def _(BeamInput):
    defaults = BeamInput(
        span_mm=6000.0,
        height_mm=300.0,
        width_mm=150.0,
        web_mm=6.5,
        flange_mm=9.0,
        young_n_mm2=205000.0,
        unit_weight_kn_m3=78.5,
        dead_load_kn_m=5.0,        # 梁自重を含まない固定荷重
        live_load_kn_m=3.0,
        bending_limit_n_mm2=156.7, # 計算例用の仮定値。法令・規準値ではない。
        shear_limit_n_mm2=90.0,    # 同上
        deflection_divisor=300.0, # たわみ制限 L/300 を例として設定
    )
    return (defaults,)


@app.cell(hide_code=True)
def _(BeamInput, isfinite):
    def validate_inputs(values):
        if values is None:
            return "入力条件を指定してください。"
        if any(value is None or not isfinite(value) for value in values.values()):
            return "全項目に有限の数値を入力してください。"
        try:
            BeamInput(**values)
        except (TypeError, ValueError):
            return (
                "寸法・ヤング係数・制限値・たわみ制限の分母は正数、荷重・単位体積重量は0以上とし、"
                "ウェブ厚 < フランジ幅、2 × フランジ厚 < 梁せい を満たしてください。"
            )
        return None

    return (validate_inputs,)


@app.cell(hide_code=True)
def _(defaults, mo, validate_inputs):
    _specs = [
        ("span_mm", "スパン L [mm]", 1),
        ("height_mm", "梁せい h [mm]", 0.1),
        ("width_mm", "フランジ幅 b [mm]", 0.1),
        ("web_mm", "ウェブ厚 tw [mm]", 0.1),
        ("flange_mm", "フランジ厚 tf [mm]", 0.1),
        ("young_n_mm2", "ヤング係数 E [N/mm²]", 100),
        ("unit_weight_kn_m3", "単位体積重量 γ [kN/m³]", 0.1),
        ("dead_load_kn_m", "固定荷重 wG（梁自重を除く）[kN/m]", 0.1),
        ("live_load_kn_m", "積載荷重 wQ [kN/m]", 0.1),
        ("bending_limit_n_mm2", "曲げ応力の制限値 fb [N/mm²]", 0.1),
        ("shear_limit_n_mm2", "せん断応力の制限値 fs [N/mm²]", 0.1),
        ("deflection_divisor", "たわみ制限 L/n の分母 n", 1),
    ]
    _fields = {
        _name: mo.ui.number(value=getattr(defaults, _name), step=_step, label=_label, full_width=True)
        for _name, _label, _step in _specs
    }
    beam_form = mo.md("""
    ### 入力条件の編集

    | 寸法・材料 | 荷重・制限値 |
    | :-- | :-- |
    | {span_mm} | {unit_weight_kn_m3} |
    | {height_mm} | {dead_load_kn_m} |
    | {width_mm} | {live_load_kn_m} |
    | {web_mm} | {bending_limit_n_mm2} |
    | {flange_mm} | {shear_limit_n_mm2} |
    | {young_n_mm2} | {deflection_divisor} |

    制限値はサンプル用の仮定値です。計算書には最後に適用した条件を表示します。
    """).batch(**_fields).form(
        submit_button_label="条件を適用・再計算",
        validate=validate_inputs,
        clear_on_submit=False,
        show_clear_button=False,
    )
    mo.Html(f'<div class="beam-inputs">{beam_form}</div>')
    return (beam_form,)


@app.cell(hide_code=True)
def _(BeamInput, asdict, beam_form, calculate, defaults, mo, validate_inputs):
    _values = beam_form.value if beam_form.value is not None else asdict(defaults)
    _error = validate_inputs(_values)
    mo.stop(_error is not None, mo.callout(_error or "", kind="danger"))
    p = BeamInput(**_values)
    r = calculate(p)
    section_name = f"H-{p.height_mm:g} × {p.width_mm:g} × {p.web_mm:g} × {p.flange_mm:g}"
    return p, r, section_name


@app.cell(hide_code=True)
def _(mo, section_name):
    mo.md(rf"""
    ## 計算概要・入力条件

    本書は、marimo と Python による構造計算書作成のサンプルである。 等分布荷重を受ける鋼製単純梁について、線形弾性理論に基づく曲げ応力、 ウェブ中立軸のせん断応力、および最大たわみを計算し、設定した制限値と比較する。

    **検討範囲：** 強軸曲げ、微小変形、一定断面の Euler–Bernoulli 梁とする。 断面はフィレットを無視した対称 I 形とし、横座屈、局部座屈、接合部、 支点部の局部応力、振動およびせん断変形は対象外とする。 本書の OK は、以下に仮定した３項目の制限値に対する比較結果を表す。

    部材符号：**B-01**　／　断面呼称：**{section_name}**
    """)
    return


@app.cell(hide_code=True)
def _(mo, p, r, table):
    mo.vstack([
        mo.md('**入力条件（制限値はサンプル用の仮定値）**'),
        table([
            ["スパン", "$L$", f"{p.span_mm:,.0f}", "mm"],
            ["梁せい", "$h$", f"{p.height_mm:g}", "mm"],
            ["フランジ幅", "$b$", f"{p.width_mm:g}", "mm"],
            ["ウェブ厚", "$t_w$", f"{p.web_mm:g}", "mm"],
            ["フランジ厚", "$t_f$", f"{p.flange_mm:g}", "mm"],
            ["ヤング係数", "$E$", f"{p.young_n_mm2:,.0f}", "N/mm²"],
            ["鋼材の単位体積重量", "$\\gamma$", f"{p.unit_weight_kn_m3:g}", "kN/m³"],
            ["固定荷重（梁自重を除く）", "$w_G$", f"{p.dead_load_kn_m:g}", "kN/m"],
            ["積載荷重", "$w_Q$", f"{p.live_load_kn_m:g}", "kN/m"],
            ["曲げ応力の制限値", "$f_b$", f"{p.bending_limit_n_mm2:g}", "N/mm²"],
            ["せん断応力の制限値", "$f_s$", f"{p.shear_limit_n_mm2:g}", "N/mm²"],
            ["たわみの制限値", "$\\delta_a$", f"L/{p.deflection_divisor:g} = {r.deflection_limit_mm:g}", "mm"],
        ], ["項目", "記号", "値", "単位"])
    ])
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    荷重はすべて鉛直下向きの線荷重とし、固定荷重・積載荷重・梁自重を係数 1.0 で加算する。 応力とたわみは同じ荷重ケースで計算する。内部計算は **N・mm** に統一し、 途中の計算値は丸めず、表示時のみ丸める。



    ## 解析モデル・断面性能

    左端をピン支持、右端をローラー支持とする。荷重は全スパンに一様に作用する。
    """)
    return


@app.cell(hide_code=True)
def _(Circle, Figure, Polygon, Rectangle, figure_image, ink, mo, mpl, np, p, plot_style, r, teal):
    with mpl.rc_context(plot_style):
        _fig = Figure(figsize=(8, 2.0))
        _ax, _sec = _fig.subplots(1, 2, gridspec_kw={'width_ratios': [3, 1]})
        _length_m = p.span_mm / 1000
        _ax.plot([0, _length_m], [0, 0], color=ink, lw=4)
        for _position in np.linspace(0, _length_m, 13):
            _ax.annotate('', (_position, 0.04), (_position, 0.7), arrowprops={'arrowstyle': '->', 'color': teal})
        _ax.plot([0, _length_m], [0.7, 0.7], color=teal, lw=1)
        _support_width = _length_m * 0.025
        for _position in (0, _length_m):
            _ax.add_patch(Polygon([(_position, -0.03), (_position - _support_width, -0.26), (_position + _support_width, -0.26)], closed=True, fill=False, edgecolor=ink))
        _ax.plot([-_support_width * 1.3, _support_width * 1.3], [-0.3, -0.3], color=ink)
        for _offset in (-_support_width / 2, _support_width / 2):
            _ax.add_patch(Circle((_length_m + _offset, -0.3), _support_width / 5, fill=False, edgecolor=ink))
        _ax.text(_length_m / 2, 0.83, f'w = {r.total_load_n_mm:.3f} kN/m', ha='center', color=teal)
        _ax.text(0, -0.56, 'A（ピン）', ha='center')
        _ax.text(_length_m, -0.56, 'B（ローラー）', ha='center')
        _ax.text(_length_m / 2, -0.47, f'L = {_length_m:g} m', ha='center')
        _ax.set(xlim=(-_length_m * 0.1, _length_m * 1.12), ylim=(-0.75, 1.15))
        _ax.axis('off')
        _h, _b, _tw, _tf = (p.height_mm, p.width_mm, p.web_mm, p.flange_mm)
        for _x, _y, _width, _height in [(-_b / 2, 0, _b, _tf), (-_tw / 2, _tf, _tw, _h - 2 * _tf), (-_b / 2, _h - _tf, _b, _tf)]:
            _sec.add_patch(Rectangle((_x, _y), _width, _height, facecolor='#d1e7e6', edgecolor=ink))
        _sec.axhline(_h / 2, color=teal, lw=0.8, ls='--')
        _sec.text(0, _h + 12, f'b = {_b:g} mm', ha='center', fontsize=9)
        _sec.text(_b / 2 + 15, _h / 2, f'h = {_h:g} mm', rotation=90, va='center', fontsize=9)
        _sec.set(xlim=(-_b / 2 - 25, _b / 2 + 65), ylim=(-15, _h + 45), aspect='equal')
        _sec.axis('off')
        _fig.tight_layout()
        _plot = figure_image(_fig, "左端ピン・右端ローラーの単純梁、下向き等分布荷重と対称 I 形断面")
    mo.vstack([_plot, mo.md('*単純梁の支持・荷重条件と理想化した I 形断面*')])
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ウェブ内法高さを $h_w = h - 2t_f$ とし、断面積 $A$、強軸の断面二次モーメント $I_x$、 弾性断面係数 $Z_x$ を次式により求める。市販形鋼の規格表に記載された断面性能とは区別する。

    $$
    A = 2bt_f + t_w h_w
    $$

    $$
    I_x = \frac{bh^3-(b-t_w)h_w^3}{12}, \qquad Z_x = \frac{I_x}{h/2}
    $$

    中立軸より上側の断面一次モーメント $Q_0$ は、上フランジとウェブ上半分の和とする。

    $$
    Q_0 = bt_f\left(\frac{h}{2}-\frac{t_f}{2}\right)
          + \frac{t_w h_w}{2}\frac{h_w}{4}
    $$
    """)
    return


@app.cell(hide_code=True)
def _(mo, r, table):
    mo.vstack([
        mo.md('**理想化断面の計算結果**'),
        table([
            ["断面積", "$A$", f"{r.area_mm2:,.1f}", "mm²"],
            ["断面二次モーメント", "$I_x$", f"{r.inertia_mm4:,.0f}", "mm⁴"],
            ["弾性断面係数", "$Z_x$", f"{r.modulus_mm3:,.1f}", "mm³"],
            ["断面一次モーメント", "$Q_0$", f"{r.first_moment_mm3:,.2f}", "mm³"],
        ], ["項目", "記号", "計算値", "単位"])
    ])
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 荷重・反力・断面力

    ### 梁自重と荷重の組合せ

    断面積を mm² から m² に換算し、単位体積重量を乗じて梁自重 $w_s$ を求める。
    """)
    return


@app.cell(hide_code=True)
def _(equation, mo, p, r):
    mo.vstack([
        equation(rf"w_s = A\times 10^{{-6}}\gamma = {r.area_mm2:.1f}\times10^{{-6}}\times {p.unit_weight_kn_m3:g} = {r.self_weight_kn_m:.6f}\;\mathrm{{kN/m}}"),
        equation(rf"w = w_G+w_Q+w_s = {p.dead_load_kn_m:g}+{p.live_load_kn_m:g}+{r.self_weight_kn_m:.6f} = {r.total_load_n_mm:.6f}\;\mathrm{{kN/m}}")
    ])
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    $1\;\mathrm{kN/m}=1\;\mathrm{N/mm}$ なので、$w$ の数値をそのまま N/mm として用いる。

    ### 支点反力

    鉛直方向のつり合いと荷重の対称性より、両端の支点反力は等しい。
    """)
    return


@app.cell(hide_code=True)
def _(equation, mo, p, r):
    mo.vstack([
        equation(rf"R_A = R_B = \frac{{wL}}{{2}} = \frac{{{r.total_load_n_mm:.6f}\times {p.span_mm:g}}}{{2}} = {r.reaction_n/1000:.3f}\;\mathrm{{kN}}")
    ])
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ### 最大曲げモーメント・最大せん断力

    曲げモーメントはスパン中央で最大、せん断力の絶対値は支点の直内側で最大となる。
    """)
    return


@app.cell(hide_code=True)
def _(equation, mo, p, r):
    mo.vstack([
        equation(rf"M_{{\max}} = \frac{{wL^2}}{{8}} = \frac{{{r.total_load_n_mm:.6f}\times {p.span_mm:g}^2}}{{8}} = {r.moment_n_mm/1e6:.3f}\;\mathrm{{kN\,m}}"),
        equation(rf"V_{{\max}} = \frac{{wL}}{{2}} = {r.shear_n/1000:.3f}\;\mathrm{{kN}}")
    ])
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    左端からの距離を $x$ とすると、断面力の分布は次式で表される。 下縁引張となる曲げを正とし、せん断力の符号は以下の式に従う。

    $$
    V(x)=w\left(\frac{L}{2}-x\right), \qquad
    M(x)=\frac{wx(L-x)}{2}
    $$



    ## 応力・たわみの検討

    ### 曲げ応力

    弾性断面係数を用いて縁応力を求める。制限値 $f_b$ はこの例の入力値である。
    """)
    return


@app.cell(hide_code=True)
def _(equation, mo, r):
    mo.vstack([
        equation(rf"\sigma_b = \frac{{M_{{\max}}}}{{Z_x}} = \frac{{{r.moment_n_mm:.1f}}}{{{r.modulus_mm3:.1f}}} = {r.bending_n_mm2:.2f}\;\mathrm{{N/mm^2}}")
    ])
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ### せん断応力

    ウェブの中立軸で $\tau=VQ/(It)$ を用いる。支点部の局部応力は含まない。
    """)
    return


@app.cell(hide_code=True)
def _(equation, mo, p, r):
    mo.vstack([
        equation(rf"\tau_{{\max}} = \frac{{V_{{\max}}Q_0}}{{I_x t_w}} = \frac{{{r.shear_n:.1f}\times {r.first_moment_mm3:.2f}}}{{{r.inertia_mm4:.0f}\times {p.web_mm:g}}} = {r.shear_n_mm2:.2f}\;\mathrm{{N/mm^2}}")
    ])
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ### 最大たわみ

    全荷重による曲げ変形のみを評価する。最大たわみはスパン中央に生じる。
    """)
    return


@app.cell(hide_code=True)
def _(equation, mo, p, r):
    mo.vstack([
        equation(rf"\delta_{{\max}} = \frac{{5wL^4}}{{384EI_x}} = {r.deflection_mm:.2f}\;\mathrm{{mm}}, \qquad \delta_a = \frac{{L}}{{{p.deflection_divisor:g}}} = {r.deflection_limit_mm:.2f}\;\mathrm{{mm}}")
    ])
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ### 検定一覧

    検定比は「計算値 ÷ 制限値」とし、丸め前の値が 1.0 以下なら OK とする。
    """)
    return


@app.cell(hide_code=True)
def _(mo, p, r, status, table):
    mo.vstack([
        mo.md('**設定した制限値に対する比較**'),
        table([
            ["曲げ応力 [N/mm²]", f"{r.bending_n_mm2:.2f}", f"{p.bending_limit_n_mm2:.2f}", f"{r.bending_ratio:.3f}", status(r.bending_ratio)],
            ["せん断応力 [N/mm²]", f"{r.shear_n_mm2:.2f}", f"{p.shear_limit_n_mm2:.2f}", f"{r.shear_ratio:.3f}", status(r.shear_ratio)],
            ["たわみ [mm]", f"{r.deflection_mm:.2f}", f"{r.deflection_limit_mm:.2f}", f"{r.deflection_ratio:.3f}", status(r.deflection_ratio)],
        ], ["検討項目", "計算値", "制限値", "検定比", "判定"])
    ])
    return


@app.cell(hide_code=True)
def _(mo, r):
    mo.md(rf"""
    **比較結果：{'全３項目が設定した制限値以内である。' if r.passed else '設定した制限値を超える項目がある。'}**



    ## 断面力図・変形図
    """)
    return


@app.cell(hide_code=True)
def _(Figure, figure_image, ink, mo, mpl, np, orange, p, plot_style, response_at, teal):
    with mpl.rc_context(plot_style):
        _x = np.linspace(0, p.span_mm, 201)
        _responses = np.array([response_at(p, float(_position)) for _position in _x])
        _fig = Figure(figsize=(7.5, 4.9))
        _axes = _fig.subplots(3, 1, sharex=True)
        for _ax, _values, _label, _color in zip(_axes, [_responses[:, 0] / 1000, _responses[:, 1] / 1000000.0, _responses[:, 2]], ['せん断力 V [kN]', '曲げ M [kN m]', 'たわみ δ [mm]'], [ink, teal, orange]):
            _ax.plot(_x / 1000, _values, color=_color, lw=2)
            _ax.fill_between(_x / 1000, 0, _values, color=_color, alpha=0.12)
            _ax.axhline(0, color='#83939d', lw=0.7)
            _ax.set_ylabel(_label)
            _ax.grid(alpha=0.18)
            _ax.margins(x=0, y=0.25)
            _index = int(np.argmax(np.abs(_values)))
            _ax.annotate(f'{_values[_index]:.2f}', (_x[_index] / 1000, _values[_index]), xytext=(5, 5), textcoords='offset points', color=_color)
        _axes[2].invert_yaxis()
        _axes[2].set_xlabel('左端からの距離 x [m]')
        _fig.tight_layout()
        _plot = figure_image(_fig, "せん断力・曲げモーメント・たわみの分布。たわみは下向きを正とする。")
    mo.vstack([_plot, mo.md('*せん断力・曲げモーメント・たわみの分布（たわみは下向きを正）*')])
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    変形図は次の分布式から生成する。変形の表示倍率は実形状と異なる。

    $$
    \delta(x)=\frac{wx}{24EI_x}\left(L^3-2Lx^2+x^3\right)
    $$

    ## 計算の追跡・適用範囲

    初期入力は `sample_beam_marimo.py` の `defaults = BeamInput(...)` に集約し、画面の入力フォームから変更できる。計算式は Quarto 版と共通の `beam.py` に保持する。 本文中の計算値、検定表、図は同じ計算結果から生成し、条件の適用時と HTML 出力時に再計算する。 `uv.lock` により使用する Python パッケージを固定する。

    本サンプルには、設計規準の選定や法令適合性の確認は含まれない。 実案件では、荷重条件、断面性能、材料定数、制限値および対象外の検討項目を、 当該案件の設計条件に合わせて整備する。
    """)
    return


if __name__ == "__main__":
    app.run()
