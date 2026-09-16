# 構造計算書 / Quarto・marimo + uv

日本語の `.qmd` に文章・数式・Python の計算をまとめ、HTML と A4 PDF を生成する repo です。
サンプルは等分布荷重を受ける鋼製単純梁の曲げ応力・せん断応力・たわみの計算です。

同じ計算書の **marimo 版** を `sample_beam_marimo.py` に用意しています。
Quarto 版と同じ初期条件・本文・数式・表・図・判定を、共通の `beam.py` で計算します。

## marimo 版を開く（PowerShell）

```powershell
uv sync --locked

# 入力フォーム付きの計算書をブラウザーで開く（終了は Ctrl+C）
uv run marimo run sample_beam_marimo.py

# 文章・数式・Python セルも編集する
uv run marimo edit sample_beam_marimo.py

# 閲覧用の静的 HTML を生成
uv run marimo export html sample_beam_marimo.py -o output/sample-beam-marimo.html --no-include-code -f
```

marimo 版だけを使う場合、以下の Quarto セットアップは不要です。
画面上の寸法・荷重・材料定数・制限値を変更し、**「条件を適用・再計算」** を押すと、
数式中の数値、入力・断面性能・検定の各表、支持・荷重モデル図、断面力図・変形図が更新されます。
不正な寸法や空欄は適用できず、計算書には最後に適用した条件を表示します。

初期条件を保存する場合は `sample_beam_marimo.py` の `defaults = BeamInput(...)` を編集します。
フォームでの変更はそのブラウザーセッション中のみ有効で、ソースには保存されません。
コマンドによる HTML 出力はソースの初期条件で再計算します。
生成した HTML は閲覧用スナップショットで、入力変更による再計算には `marimo run` または `marimo edit` が必要です。
HTML の画面描画には marimo の JavaScript/CSS を CDN から読み込むため、ネット接続が必要です。

```powershell
# marimo のセル依存関係・書式を検査
uv run marimo check --strict sample_beam_marimo.py
```

使い方の詳細は [marimo の公式ドキュメント](https://docs.marimo.io/)、
[フォーム](https://docs.marimo.io/api/inputs/form/)、
[静的 HTML 出力](https://docs.marimo.io/guides/exporting/static_html/) を参照してください。

## セットアップ（PowerShell）

repo のルートで実行します。この PC に導入済みの `uv` を使います。

```powershell
uv sync --locked
uv run python scripts/setup.py
uv run python scripts/quarto.py check jupyter
```

Python 3.12 と依存パッケージは `.venv` に入ります。Quarto 1.10.18 は公式ポータブル版の
ZIP を SHA-256 検証後に `.tools/quarto-1.10.18/` へ展開します。
管理者権限・MSI・PATH の変更は不要です。セットアップは再実行できます。
初回は PyPI と GitHub からダウンロードするためネット接続が必要です（Quarto ZIP は約 148 MB）。
仮想環境の手動 activate やグローバルへの pip install は不要です。

## 計算書を生成・確認

```powershell
# HTML と PDF を両方生成（引数なしの既定動作）
uv run python scripts/quarto.py

# HTML のみ
uv run python scripts/quarto.py render --to html

# PDF のみ（Typst を利用）
uv run python scripts/quarto.py render --to typst

# 編集内容をブラウザーでプレビュー（終了は Ctrl+C）
uv run python scripts/quarto.py preview sample-beam.qmd --to html
```

生成物は `output/sample-beam.html` と `output/sample-beam.pdf` です。
HTML は画像・CSS を埋め込み、数式に MathML を使った単一ファイルです。
現在の Edge / Chrome / Firefox 等で開いてください。
PDF は Quarto 同梱の Typst で作成するため、TeX Live / TinyTeX は不要です。
本文・図の日本語フォントには、この PC の **Meiryo（メイリオ）** を使います。
別 OS では `_quarto.yml` の `mainfont` と `.qmd` の `font.family` を、導入済みの日本語フォントへ変更してください。

`scripts/quarto.py` は `QUARTO_PYTHON` に `uv run` が選んだ Python を指定します。
Windows の Python Launcher や別の Jupyter 環境を誤って使用することを避けるため、上のコマンドを使ってください。

## 入力と文章を変更する

1. `sample-beam.qmd` 冒頭の `p = BeamInput(...)` で寸法・荷重・制限値を変更します。
2. Markdown の見出し・本文と LaTeX 形式の数式を編集します。
3. 再生成すると、本文の数値・表・図・OK/NG 判定が更新されます。

新しい計算書を追加するときは `.qmd` を複製し、`_quarto.yml` の `project.render` にファイル名を追加します。
計算のキャッシュ・freeze は無効にしてあり、外部 Python ファイルの変更も毎回反映されます。

## ファイル構成

| ファイル | 役割 |
| --- | --- |
| `sample-beam.qmd` | 計算条件、本文、数式、表、図 |
| `sample_beam_marimo.py` | 同じ計算書の marimo 版、入力フォームと連動した計算結果 |
| `beam.py` | 単純梁の計算と入力チェック（内部単位 N・mm） |
| `_quarto.yml` | 共通の実行設定・HTML/PDF 書式 |
| `styles.css` | HTML とブラウザー印刷の書式 |
| `scripts/quarto.py` | uv の Python を使って Quarto を起動 |
| `scripts/setup.py` | Windows x64 用 Quarto の取得と SHA-256 検証 |
| `tests/test_beam.py` | つり合い、境界条件、梁の微分方程式、荷重・スパンの比例関係等を検証 |
| `tests/test_marimo.py` | Quarto 版との初期条件・結果の一致、marimo の入力変更と出力を検証 |
| `pyproject.toml` / `uv.lock` | パッケージ定義とバージョン固定 |
| `output/` | 再生成可能な成果物（Git 管理対象外） |

## 検証・依存関係の更新

```powershell
uv run pytest
uv lock --check
```

依存関係を意図して更新するときは `uv lock --upgrade`、続けて `uv sync` を実行し、
テストと HTML/PDF の再生成を確認してください。Quarto 本体は Python の依存関係とは別に固定しています。
更新時は `scripts/setup.py` のバージョン・公式 SHA-256 と `scripts/quarto.py` の探索先を変更してください。

Windows では PyPI の `quarto-cli` の wheel ビルドがキャッシュ内の長いファイルパスで失敗するため、
この repo は公式 ZIP の直接展開を採用しています。別 OS では Quarto を公式手順で導入し、
PATH から `quarto` を呼び出せるようにしてください。

## サンプルのモデル

フィレットを除く対称 I 形断面、強軸曲げ、線形弾性、微小変形を仮定します。
梁自重を断面積から求め、固定荷重（梁自重を除く）・積載荷重と加算します。
`1 kN/m = 1 N/mm` とし、せん断応力はウェブ中立軸の `VQ/(It)` で評価します。
曲げとせん断の制限値、および `L/300` は計算例用の仮定値です。
横座屈・局部座屈・接合部等は対象外で、判定は入力した３項目の比較結果です。
実案件の設計規準・法令適合性を判定するものではありません。

## 公式ドキュメント

- [uv のプロジェクト管理](https://docs.astral.sh/uv/guides/projects/)
- [Quarto 1.10.18 の公式配布物](https://github.com/quarto-dev/quarto-cli/releases/tag/v1.10.18)
- [Quarto と Python](https://quarto.org/docs/computations/python.html)
- [本文中の計算値（inline code）](https://quarto.org/docs/computations/inline-code.html)
- [Typst による PDF 出力と日本語フォント設定](https://quarto.org/docs/output-formats/typst.html)
