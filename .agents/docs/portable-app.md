# SDC Converter のPortable配布

2026-09-17。既存4種類を、各スクリプトが現在対応するNDU/NDT出力の範囲で統合する。
ユーザー指定により Tkinter + PyInstaller `--onedir --windowed` を採用。

## 利用と対応範囲

配布ZIP: `dist/SDCConverter-v1.0.0-win-x64.zip`。
展開した `SDCConverter/SDCConverter.exe` を実行する。
Python・pip・Visual Studio・Excelは利用者PCに不要。
EXEと `_internal` は同じフォルダーに置く。

詳しい使い方は [利用者向け案内](../../docs/SDCConverter-README.txt)。
この案内をZIP内に `はじめに.txt` として含める。

| 入力項目 | NDU | NDT | 共用するモジュール |
| --- | --- | --- | --- |
| 水平地盤ばね | ○ | 非対応 | `fill_jiban_shogen` |
| 有効抵抗土圧力 | ○ | 非対応 | `fill_jiban_pressure` |
| 杭周面ばね・支持力 | ○ | ○ | `fill_suppot_info` |
| 杭先端ばね・支持力 | ○ | ○ | `fill_pile_tip_suppot_info` |

NDTでは参照NDUも必須。NDTの対象杭の座標・接続を照合する。
保存するのは対象NDUまたはNDTの一方。NDU/NDTの同時同期や未対応のNDTカードは実装しない。

方式は既存仕様の既定値に固定し、GUIでは杭対応・土圧の押す方向・対象項目を選択可能。
GUIには周面の既存画面方式を明記し、共用APIへ `shaft_profile="existing-screen"` を渡す。
先端との合成等の既存制限も継承する。

## ソース構成

- `scripts/sdc_converter_app.py`: GUI、ファイル選択、確認一覧、バックグラウンド実行、関連付けアプリで開く。
- `scripts/portable_converter.py`: `Request` → `prepare()` → `Plan` → `save()`。GUI非依存。
- `scripts/portable_smoke.py`: Python / EXE共用の起動・実データ変換検証。
- `scripts/build_portable.py`: 既存を含むテスト、ビルド、ZIP展開、EXE検証、配布ZIPとSHA-256出力。
- `requirements-build.txt`: 開発PCのビルド依存を固定。アプリ本体はPython標準ライブラリのみ。

元の4つのCLIと計算モジュールは変更せず、関数を直接importする。
同梱EXEで `sys.executable` をPython CLIとして再起動する方式は使わない。
GUI入力にはリポジトリ内の `test` / `snap` の既定パスを流用しない。

## 起動とビルド

Windows x64、Python 3.12 x64 + Tkinterがある開発PCで、リポジトリ直下から実行する。

```powershell
# ソースから起動
.venv\Scripts\python.exe -B scripts\sdc_converter_app.py

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
実案件のSDC/NDU/NDT/Excelや内部仕様書は配布物に含めない。
Python/Tk/PyInstallerと同梱ライブラリのライセンス、バージョンとソースSHA-256を同梱する。
`docs/licenses/SOURCES.txt` に第三者告知の取得元を記録する。

PyInstallerの方式は [公式動作説明](https://pyinstaller.org/en/stable/operating-mode.html) と
[公式CLI説明](https://www.pyinstaller.org/en/stable/usage.html) に従う。

## 保存仕様

通常は別名保存。既存の別名出力は同じ内容でも拒否し、ユーザーに別名を指定させる。
元ファイル更新はGUIの明示チェックで選ぶ。参照SDCと参照用NDUは更新しない。

1. 全項目をメモリ上で計算し、支点数・ケース行・NDT幾何等を検証。
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

`tests/test_portable_converter.py` は項目選択、NDU/NDTの制約、実モデル一括変換、
入力不変、バックアップ、既存出力・ハードリンク・入力変更・出力競合・保存障害を検証する。
既存91件と追加17件、計108件成功。

ビルド時はZIPを日本語・空白入りパスへ展開し、Python等を含まないPATHとリポジトリ外の
作業フォルダーでEXEを起動する。Tkinter、CP932、実モデルNDUの4項目207件、
NDTの2項目63件、再実行の同一性、バックアップ、入力原本不変を検証。
GUIのワーカー・イベントキューを通る「入力値を確認→変換して保存」も検証。
検証レポートは `dist/packaged-smoke.json`。実画面でも起動・日本語表示・実行ボタンの配置を確認する。

Input-JR/JRSNAPでの読込み・解析実行、別のクリーンPCでの実行、EXE署名は未実施。
PythonをPATHから除いた同一PCでの検証を「クリーンPC検証済み」とは扱わない。
