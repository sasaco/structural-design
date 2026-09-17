# Handoff: 「変換結果」「入力根拠」の削除 — 次回は現状調査と修正プラン作成

作成日: 2026-09-18。リポジトリ: `C:/Users/sasai/Documents/structural-design`。

## 次セッションの依頼と作業範囲

ユーザーの最新依頼:

> 今度はシート `変換結果` と `入力根拠` を削除する。いきなり実装せずに次のセッションで、現状を把握して `.agents\docs` に仕様書（修正プラン）を書くように次のセッションに引き継いでください。`$handoff`

**次セッションは現状調査と仕様書（修正プラン）の作成まで。実装を始めず、仕様書をユーザーへ提示して止める。**

- 次回成果物の推奨名: `.agents/docs/excel-remove-support-sheets-plan.md`。
- 今回の最終的な要望は、生成帳票の「変換結果」「入力根拠」の2シート削除。
- 直前の「仕様書OKです。実装してください」は杭先端ばねの見本対応に対する承認であり、今回の2シート削除には適用しない。
- 次回も、コード・テストの実装、既存Excelからのシート削除、配布EXE/ZIPの再作成は行わない。
- 本セッションは引き継ぎ保存のみ。入口となる既存コードと作業状態を確認したが、削除方式の詳細調査・修正プラン作成・実装はまだ行っていない。

過去の [杭先端引き継ぎ](handoff-tip-excel.md) は履歴。杭先端の調査・承認・実装は完了している。**次の作業は本書を優先する。**

## 現在地

全4工程を選択すると、現在のExcelは以下の7シートを生成する。

1. 変換結果
2. 水平地盤ばね
3. 有効抵抗土圧
4. 杭周面ばね
5. 杭周面の支持力
6. 杭先端ばね
7. 入力根拠

周面は `shaft` という1工程からK/Fの2シートを生成する。先端は `tip` 工程で、3列の上段K1/K2・下段Fy/Fuになった。基準3節点ではA1:C14、A4縦100%で1ページ。

今回の削除対象以外の5主表は見本への対応が完了している。全工程では削除後に5シートとなる想定だが、選択工程別・NDU/NDT別の構成も次回調査する。

**「入力根拠」は数式の参照先であり、単なる説明シートではない。** 原値・幾何・固定実入力・採用照合・十進文字列を保持し、主表と相互参照している。「変換結果」には変換対象一覧や主表へのリンクがある。単にシート配列やXLSXから2枚を除く方式で、再計算や検証が成立するとは限らない。

削除後の数式・原値・照合の扱いはまだ未決定。非表示化を削除の代わりとしたり、主表を無断で固定値に置き換えたりせず、影響と選択肢を調査したうえで修正プランに示す。

## 先に読む資料とコードの入口

| 参照先 | 確認する事項 |
| --- | --- |
| [共通実装記録](excel-report-implementation.md) | 帳票・GUI・保存経路と現在の実装結果 |
| [杭先端仕様書](tip-excel-layout-plan.md) | 第10節が実装結果。第1〜9節は承認時の調査記録 |
| [水平表仕様書](horizontal-spring-excel-layout-plan.md) | 原値・幾何・採用照合と主表の関係 |
| [土圧表仕様書](effective-pressure-excel-layout-plan.md) | 再計算・固定実入力と主表の関係 |
| [周面仕様書](shaft-excel-layout-plan.md) | K/Fの2シート・入力根拠・別リンク |
| [初期Excel仕様書](sdc-excel-calculation-report-spec.md) | 初期の目的・保存要件。古いシート数や未実装記述は現状と区別する |
| `scripts/excel_report.py` | `build()`、`ReportBook`、`Expr`、各 `build_*()`、参照・再計算・採用検証・改ページ・XLSX出力 |
| `scripts/calculation_record.py` | Excelと独立した記録、原値・幾何・固定実入力・対象一覧 |
| `scripts/excel_preview.py` | 共通セルモデル、シート選択、初期表示、リンク、数式バー |
| `scripts/portable_converter.py` / `scripts/excel_cli.py` | 共通API、Excel作成経路、個別CLI、保存失敗時の扱い |
| `scripts/sdc_converter_app.py` / `scripts/portable_smoke.py` | GUI・自己診断。現自己診断は初期表示「変換結果」を期待する |
| `tests/test_excel_report.py` / `tests/test_portable_converter.py` | シート構成、再計算、保存、CLIの既存回帰 |
| `tests/test_horizontal_excel_report.py` / `tests/test_effective_pressure_excel_report.py` / `tests/test_shaft_excel_report.py` / `tests/test_tip_excel_report.py` | 5主表と入力根拠・採用値・リンク等の既存検証 |

確認済みの入口: `build()` は「変換結果」と「入力根拠」を作り、5主表の構築へ原値参照を渡す。水平・土圧・周面の採用値検証には「入力根拠」のセルが含まれる。先端の主表数式も同シートの原値を参照する。次回、依存を全件追って確定する。

## 次回の調査と仕様書に含める事項

1. 適用AGENTS.md、本書、実装記録、作業ツリー、最新入力・出力のハッシュを確認する。未コミットの先端実装を現状の基準とし、HEADへ戻さない。
2. 「変換結果」「入力根拠」の役割を、表示内容・数式・参照元/参照先・固定実入力・採用値照合・出典・内部リンクに分けて調査する。実コードと現行Excelの両方で確かめる。
3. 5主表が依存する原値・座標・計算中間値の配置、セル参照AST、再計算、キャッシュ、`expected`、`checks`、ページング時の参照再配置を追跡する。2シートを削除した場合の問題箇所を列挙する。
4. 削除後も必要な計算・検証情報をどこで扱うか、主表の数式と再計算機能をどうするかを比較し、推奨案と理由を示す。帳票からの削除と、内部の計算記録・JSON・検証機能の削除を混同しない。既存の原値編集・差の照合が変わる場合は利用者への影響を明記する。
5. Excel出力とGUIプレビューのシート構成・初期表示・内部リンク・数式バー・保存経路を調査する。全工程、単独工程、工程の組合せ、NDU/NDTで整合する方針を定める。
6. 各主表の数値・単位・精度・空欄・書式・印刷、SDC→NDU/NDTの変換規則、既存入力検証・保存保護への影響を分けて記載する。帳票簡素化から計算係数や丸めの変更へ広げない。
7. ファイル別の修正範囲、実装順序、検証方法、受入条件を具体化する。削除対象2シートが出力に残らないこと、参照切れやリンク切れがないこと、主表採用値・モデル出力の維持、GUI/CLI、Excel再計算、印刷確認を含める。
8. `.agents/docs/excel-remove-support-sheets-plan.md` を作成して提示する。**ここで止める。コード・テスト・既存Excelの変更は、今回の仕様書に対する別途の実装依頼後。**

## 基準資料と直前の検証

最新の検証済み全7シートExcel:

[今町橋りょう4P_計算過程_杭先端修正版.xlsx](../../outputs/tip-review-20260918-005232/今町橋りょう4P_計算過程_杭先端修正版.xlsx)

全4工程、KG対応 `4:1 5:2 6:3`、右押し、周面 `existing-screen` で作成した計算確認用出力。モデルは未保存。このファイルを調査資料として使用し、上書きしない。

| ファイル | 引き継ぎ時のSHA-256 |
| --- | --- |
| 上記の杭先端修正版Excel | `9a5fe3e57eea39438fac4533f2962a76c8b8fb84fd7ef3b97c7d97906a4379fb` |
| `test/今町橋りょう4P(右).sdc` | `9b9aed2461b87acb5193c8eac79cf07b6d9824bdbd80e096416e197f324f64a7` |
| `test/今町橋りょう4P(C方向･右押し→).ndu` | `31e9e85971d07ae1a0b21273f500b0d8d039184ecf9febb6db642fbfabfbd22a` |
| `test/今町橋りょう4P(C方向･右押し→)_変換済み.ndu.計算過程.見本xlsx.xlsx` | `7a406fc87569c07e1b8449084a25ce6cbf317677a713831bfd40526decbf4e3b` |

直前の先端実装では全156テストとGUI自己診断が成功。Excel 16.0で5,563数式を再計算し、348採用値一致・501照合欄0を確認した。先端原値4種の個別編集、基準1ページと長表を含む計11ページを確認済み。NDU予定出力は原本とバイト一致し、他4主表のセルモデルは改修前と一致した。

これは2シート削除前の検証結果。今回の引き継ぎ保存ではテストを再実行していない。検証資料は `build/tip-excel-implementation/`（無視対象）。EXE/ZIPは先端改訂後に再作成していない。

```powershell
.venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -q
```

## 作業ツリーと環境

ブランチ: `njc/tkinter`。HEAD: `f13b0d4b8ba7a01f7be612f252400ce3fe64e920`。

**先端の実装は未コミットの作業ツリーにある。HEADだけで現状を判断しない。** 本引き継ぎ追加前の状態:

- 変更済み: `.agents/docs/excel-report-implementation.md`、`scripts/excel_preview.py`、`scripts/excel_report.py`、`tests/test_excel_report.py`。
- 未追跡: `.agents/docs/tip-excel-layout-plan.md`、`tests/test_tip_excel_report.py`、`outputs/tip-review-20260918-005232/`。
- 今回追加するのは本書と同文バックアップ。既存実装・資料・Excel・入力を変更しない。コミット操作は行わない。

Windows / PowerShell。日本語文書は `Get-Content -Encoding UTF8`、Pythonは `.venv\Scripts\python.exe -B -X utf8`。日本語コードをパイプで渡す場合は `$OutputEncoding = [System.Text.UTF8Encoding]::new($false)` を使う。

XlsxWriterは既存仮想環境で利用可能。読取り用openpyxl等は `C:/Users/sasai/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe` にある。NDU全体の単純CP932 decodeは失敗することがあるため既存パーサーを使う。Excel実機確認は独立インスタンスで行い、ユーザーのExcelを閉じたり原本を書き戻したりしない。

今回確認した祖先・リポジトリ・文書ディレクトリに適用AGENTS.mdはなかった。次回も確認する。`$handoff` スキルは利用可能一覧とCodex/Agents/Claudeのローカル検索で見つからず、通常の引き継ぎ文書として保存した。

同文バックアップ: `C:/Users/sasai/AppData/Local/Temp/HANDOFF-remove-support-sheets-20260918.md`。

次セッションへの開始文:

> `.agents/docs/handoff-remove-support-sheets.md` を読んでください。「変換結果」「入力根拠」の2シート削除に向けて、現行帳票・数式参照・検証・GUI・保存処理を調査し、`.agents/docs/excel-remove-support-sheets-plan.md` に仕様書（修正プラン）を作成してください。実装や既存Excelの変更はせず、仕様書の提示で止めてください。
