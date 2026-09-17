# SDC Converter のPortable配布

2026-09-17。既存4種類を、各スクリプトが現在対応するNDU出力の範囲で統合する。
ユーザー指定により Tkinter + PyInstaller `--onedir --windowed` を採用。

## 利用と対応範囲

配布ZIP: `dist/SDCConverter-v1.3.0-win-x64.zip`。
展開した `SDCConverter/SDCConverter.exe` を実行する。
Python・pip・Visual Studio・Excelは利用者PCに不要。
EXEと `_internal` は同じフォルダーに置く。

詳しい使い方は [利用者向け案内](../../docs/SDCConverter-README.txt)。
この案内をZIP内に `はじめに.txt` として含める。

| 入力項目 | NDU | 共用するモジュール |
| --- | --- | --- |
| 水平地盤ばね | ○ | `fill_jiban_shogen` |
| 有効抵抗土圧力 | ○ | `fill_jiban_pressure` |
| 杭周面ばね・支持力 | ○ | `fill_suppot_info` |
| 杭先端ばね・支持力 | ○ | `fill_pile_tip_suppot_info` |

GUIでは杭対応・対象項目を選択可能。「右押し」「左押し」は杭対応の一括設定ボタン。
GUIには周面の既存画面方式を明記し、共用APIへ `shaft_profile="existing-screen"` を渡す。
先端との合成等の既存制限も継承する。

2026-09-18、番号列形式と奇数列・偶数列形式のSDCに対応。左SDCも4項目・JSON・Excelで使用できる。
実杭列数まで展開し、GUIでは1列目からの番号を指定する。元CSV欄と解釈種別はパーサーが保持する。
詳しくは [左SDC対応計画](left-sdc-support-plan.md) と [実装・検証記録](left-sdc-support-implementation.md)。

## ソース構成

- `scripts/sdc_converter_app.py`: GUI、ファイル選択、Excelプレビュー、バックグラウンド実行、関連付けアプリで開く。
- `scripts/calculation_record.py` / `excel_report.py` / `excel_preview.py`: 共通計算記録、Excel生成、同じセルモデルのプレビュー。
- `scripts/model_preview.py`: NDUのXYモデル図、KG入力に連動する対象部材の赤色表示、拡大・移動。
- `scripts/kg_candidates.py`: KGInfoの列挙、SDC地層厚合計と部材長合計の照合。
- `scripts/kg_selection.py`: KGInfoチェックリスト、SDCモデル列の選択、候補のバックグラウンド再読込。
- `scripts/portable_converter.py`: `Request` → `prepare()` → `Plan` → `save()`。GUI非依存。
- `scripts/sdc_columns.py`: 実杭列数、番号列・奇偶列・土圧区分、原CSV欄の共通処理。
- `scripts/portable_smoke.py`: Python / EXE共用の起動・実データ変換検証。
- `scripts/build_portable.py`: 既存を含むテスト、ビルド、ZIP展開、EXE検証、配布ZIPとSHA-256出力。
- `requirements.txt`: Excel生成用XlsxWriter 3.2.9。`requirements-build.txt` はこれとビルド依存を固定。

4つのCLIの計算モジュールから、関数を直接importする。
同梱EXEで `sys.executable` をPython CLIとして再起動する方式は使わない。
GUI入力にはリポジトリ内の `test` / `snap` の既定パスを流用しない。

## KGInfoの選択と長さの照合

KG番号のテキスト入力を廃止し、入力NDU内の全 `KGInfo` を番号順のチェックリストに表示する。
2026-09-17、ユーザーが対象拡張子は現行の `.ndu` でよいと確認済み。
各行にはKG番号、開始～終了部材、部材長合計、SDCモデル列、選択不可の理由を表示する。
初期状態は全件未選択。チェックするとSDC列を仮設定し、コンボボックスで列を変更できる。
未使用の1～3列を優先し、使い切った場合は利用できる1～3列の最後の列を仮設定する。
列の未選択、KG未選択、同じKGの重複、存在しないSDC列は処理を開始しない。
2026-09-18のユーザー指定により、SDC列と杭x座標の昇順制約、およびSDC列の重複禁止を廃止。
SDCモデル列は全4項目で参照するSDC列を直接指定する。逆順・同一列の共用を許可する。

「右押し」「左押し」は状態を保持する切替ではなく、選択済みKGの列を一括設定するボタン。
選択杭をx座標の昇順（同じxならKG番号順）に並べ、左押しは `min(左からの順位,3)`、
右押しは `min(右からの順位,3)` を割り当てる。5杭ではそれぞれ `1,2,3,3,3` / `3,3,3,2,1`。
KG番号順やチェック順には依存しない。1杭は両方1列、2杭は1・2／2・1。
未選択・読込中・処理中はボタンを無効化。必要なSDC列がない場合は設定せず不足を通知する。
一括設定後の手動変更や同一列の共用を保持し、チェック変更で再割当しない。

GUIは `Request(push_direction="direct")` を渡し、土圧の計算時に列を再反転しない。
既存CLI/APIの `right` / `left` と既定値は互換用に保持する。CLIでGUIと同じ直接列指定を使う場合は
`fill_jiban_pressure.py --groups 4:3 5:2 6:1 --push-direction direct` を指定する。

地層厚は [水平地盤ばねの仕様](fill-jiban-shogen-script.md) と同じSDC直角方向の
`b）水平地盤ばね値` 表から取得し、全層の厚さを合計する。
部材長はKGInfoの開始～終了部材と `ElementInfo` 第5・6フィールドの端節点、
`JointXY` の座標から取得する。既存と同じ鉛直杭の条件で各部材長を合計し、
両方の合計を `Decimal.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)` で
小数第3位（0.001 m）に四捨五入してから一致を判定する。2026-09-17のユーザー追加指定。
各層・各部材の段階では丸めず、合計後に1回だけ丸める。差を許容値と比較する方式ではない。
一覧の合計と不一致時の差も、この比較値を小数3桁で表示する。計算に使う元の層厚・座標は保持する。
短い候補・長い候補の両方を非活性にし、差をm単位で表示する。
欠損部材／節点、非鉛直、部材の隙間・重複等で算出できない候補も非活性にして理由を表示する。
SDC未選択・読込エラー時はNDUの候補を表示したまま全件非活性にする。

SDCまたはNDUのパスを変更すると直ちに選択を解除し、250ms後に再読込する。
同じパスのファイルを外部編集した場合は「再読込」を使用する。
古い読込結果は採用しない。変換処理中はチェック・列変更・再読込を無効にし、
処理終了後も長さ不一致候補は非活性のままにする。
GUIは `Request(require_matching_lengths=True)` を指定し、`prepare()` でも
実際に計算する入力バイト列で再判定する。既存の項目別CLI/APIは必要な表のみで実行できる。

実案件データではKGInfo1～6は全て31.000 m、右SDCも31.000 mなので全6候補が長さ条件を満たす。
長さ一致のみで左右の基礎が同一とは判断しない。対象KGと列は利用者が選択する。

## 入力画面のモデル図

「入力・保存設定とモデル」タブの右側にTk Canvasでモデル図を表示する。NDUを選ぶだけで読み込み、
SDCの選択や「Excelをプレビュー」の実行は不要。KGInfoのチェック・モデル列の選択変更で、
指定した全KGの部材の線・番号を赤、それ以外を灰色で表示する。
`4:1 5:2 6:3` はKGInfo4/5/6の開始〜終了部材範囲を対象とする。
モデル列はSDCの対応列を指定するもので、描画対象の抽出はKG番号による。

参照実装はAggre-SEの `ChildFormElementSetting.vb` / `MyPictureBox.vb`。
NDUの `JointXY` のXY座標と `ElementInfo` の第5・6フィールドの端節点を使用し、
Yは下向き、縦横別縮尺で全体に収める。ホイール・＋／−で拡大縮小、ドラッグで移動、
「全体表示」で復帰し、対象部材番号の表示を切り替えられる。

ファイルパス編集は250ms待ってバックグラウンド読込。ファイル変更時には前の図を消し、
古い読込結果を採用しない。未選択は赤色解除、不正な対応・存在しないKG・欠損した部材範囲は
全ての赤色を解除して図内の説明欄へ表示。読込不良時は図を消して理由を表示する。
図はKG範囲の確認用で、鉛直杭・SDCとの整合などの計算条件は既存の `prepare()` で検証する。

## 開発環境での起動

Windows x64、Python 3.12 x64 + Tkinterがある開発PCで、リポジトリ直下から実行する。
GUIのローカル実行にEXEの再ビルドやPyInstallerのインストールは不要。
アプリ本体はPython標準ライブラリとXlsxWriterを使用する。
初回は `uv pip install --python .venv/Scripts/python.exe -r requirements.txt` で実行依存を入れる。

### VS CodeでF5起動

1. VS Codeの「フォルダーを開く」で、このリポジトリのルート `structural-design` を開く。
2. 拡張機能 **Python** (`ms-python.python`) と **Python Debugger** (`ms-python.debugpy`) をインストールする（導入済みなら不要）。
3. `.venv/Scripts/python.exe` があることを確認する。ない場合は、下記の初回セットアップを実行する。
4. 「実行とデバッグ」（**Ctrl+Shift+D**）で **SDC Converter: GUI** を選び、**F5** を押す。

[`.vscode/launch.json`](../../.vscode/launch.json) にGUI専用の起動構成を用意している。
開いているファイルにかかわらず `scripts/sdc_converter_app.py` を起動する。
Pythonはこのリポジトリの `.venv/Scripts/python.exe`、作業フォルダーはリポジトリ直下に固定。
`-B -X utf8` を指定し、ログはVS Codeの統合ターミナルに表示する。
Pythonファイルにブレークポイントを置けば、GUI操作中の処理をデバッグできる。
終了はGUIの閉じるボタン、または **Shift+F5**。デバッグなしの起動は **Ctrl+F5**。
ソース変更後はGUIを終了して、もう一度起動する。

設定項目の詳細は [VS Code公式のPythonデバッグ手順](https://code.visualstudio.com/docs/python/debugging) を参照。

### 初回セットアップ（`.venv` がない場合のみ）

```powershell
uv venv .venv --python 3.12
```

uvを使用しない場合は、Tkinterを含むPython 3.12をインストールして実行する。

```powershell
py -3.12 -m venv .venv
```

### ターミナルから起動

```powershell
.\.venv\Scripts\python.exe -B -X utf8 scripts\sdc_converter_app.py
```

## EXEの再ビルド

### VS Codeで配布ZIPを作成

初回は下記のコマンドで `.venv-build` の作成とビルド依存のインストールを済ませる。
準備済みなら、「実行とデバッグ」（**Ctrl+Shift+D**）で
**SDC Converter: 配布ZIPを作成** を選び、**F5** を押す。

`.venv-build/Scripts/python.exe` で `scripts/build_portable.py` を実行する。
テスト、EXEビルド、ZIP展開後の検証が順に実行され、ログは統合ターミナルに表示される。
子プロセスのテストやPyInstallerにデバッガーが自動接続しないよう `subProcess: false` を指定する。
成功すると `dist/SDCConverter-v1.3.0-win-x64.zip` と `.zip.sha256`、右の `packaged-smoke.json`、左の `packaged-smoke-left.json` が生成される。
GUIを起動したい場合は、構成を **SDC Converter: GUI** に切り替える。

### 初回セットアップとターミナルからのビルド

```powershell
# 分離したビルド環境（uv使用）
uv venv .venv-build --python .venv\Scripts\python.exe
uv pip install --python .venv-build\Scripts\python.exe -r requirements-build.txt
.venv-build\Scripts\python.exe -B -X utf8 scripts\build_portable.py
```

uvを使用しない場合:

```powershell
py -3.12 -m venv .venv-build
.venv-build\Scripts\python.exe -m pip install -r requirements-build.txt
.venv-build\Scripts\python.exe -B -X utf8 scripts\build_portable.py
```

ビルドは `build/portable-*/` ごとに分離し、既存の作業フォルダーを再帰削除しない。
全テスト成功後、`dist/` の同名ZIPを置換する。ZIPはフォルダー全体を含む。
実案件のSDC/NDU/Excelや内部仕様書は配布物に含めない。
Python/Tk/PyInstallerと同梱ライブラリのライセンス、バージョンとソースSHA-256を同梱する。
`docs/licenses/SOURCES.txt` に第三者告知の取得元を記録する。

PyInstallerの方式は [公式動作説明](https://pyinstaller.org/en/stable/operating-mode.html) と
[公式CLI説明](https://www.pyinstaller.org/en/stable/usage.html) に従う。

## 保存仕様

通常は別名保存。既存の別名出力は同じ内容でも拒否し、ユーザーに別名を指定させる。
元ファイル更新はGUIの明示チェックで選ぶ。参照SDCは更新しない。

1. 全項目をメモリ上で計算し、支点数・ケース行等を検証。
2. 出力先の拡張子・入力との衝突（ハードリンクを含む）を検査。
3. 同じフォルダーの一時ファイルへ保存・flush・fsync・バイト照合。
4. 更新モードでは原本の日時・ランダム接尾辞付きバックアップを新規作成。
5. 計算根拠と原本／出力SHA-256の報告書を保存。
6. 出力先と全入力を再検査して、一時ファイルを完成名へ一括移動。
   更新モードは `os.replace`、新規保存はWindowsの上書きしない `os.rename`。
7. 保存失敗時は一時ファイルと今回の報告書を除去。作成済みバックアップは保持する。

複数項目の途中でエラーになっても部分的な変換結果は保存しない。
外部プロセスによる変更は保存直前にも検知するが、外部編集との排他ロック機能ではない。
入力ファイルは他アプリで編集を終えてから処理する。

## 検証

`tests/test_portable_converter.py` は項目選択、NDUの制約、実モデル一括変換、
入力不変、バックアップ、既存出力・ハードリンク・入力変更・出力競合・保存障害を検証する。
計算・CLI・共通保存処理を含む全テストをビルド前に実行する。
`tests/test_kg_selection.py` は長さ一致／不足／超過、小数第3位への四捨五入と境界値・合計後の丸め、不正な杭、SDC未選択、
候補ゼロ、チェック・列変更、処理後の非活性維持、ファイル切替・再読込・読込競合、変換時の再検証を確認する。
`tests/test_model_preview.py` は実モデルの対象72部材と変換対象の一致、入力編集での赤色・番号の
切替、不正入力での解除、読込エラー、ファイル変更後に古い読込結果を採用しないことを検証する。

ビルド時はZIPを日本語・空白入りパスへ展開し、Python等を含まないPATHとリポジトリ外の
作業フォルダーでEXEを起動する。Tkinter、CP932、実モデルNDUの4項目207件、
再実行の同一性、バックアップ、入力原本不変を検証。
GUIのワーカー・イベントキューを通る「Excelをプレビュー→変換して保存」も検証。
EXEでもモデル図171部材の読込・KG4/5/6のチェックによる72部材の赤色表示・選択解除、
長さ不一致候補のチェック無効化、入力の選び直しを検証。
検証レポートは `dist/packaged-smoke.json`。実画面でも起動・日本語表示・実行ボタンの配置を確認する。

Input-JR/JRSNAPでの読込み・解析実行、別のクリーンPCでの実行、EXE署名は未実施。
PythonをPATHから除いた同一PCでの検証を「クリーンPC検証済み」とは扱わない。

## Excel帳票とプレビュー（1.1.0）

従来の `preview_rows` の履歴一覧を、出力Excelと同じセル・数式・色・シートの読取専用表示へ変更。
「Excelをプレビュー」では保存せず、「変換して保存」でモデル・JSON・既定オンのExcelを作成する。
Excel選択時はその生成・検証・一時保存を完了してからモデルを公開する。
通常の保存失敗では今回の帳票を取り消し、作成済みバックアップを保持する。
同一性が変わった他プロセスの帳票は削除しない。
利用方法・CLI・数式検証は [Excel実装記録](excel-report-implementation.md) を参照。
