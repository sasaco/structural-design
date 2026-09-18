# Handoff: 杭先端ばねExcel — 次回は現状調査と修正プラン作成

作成日: 2026-09-18。リポジトリ: `C:/Users/sasai/Documents/structural-design`。

## 次セッションの依頼と作業範囲

ユーザーの最新依頼:

> 同様に `杭先端ばね` の変換を `test\今町橋りょう4P(C方向･右押し→)_変換済み.ndu.計算過程.見本xlsx.xlsx` のシート `杭先端ばね` に示す表に変える。いきなり実装せずに次のセッションで、現状を把握して `.agents\docs` に仕様書（修正プラン）を書くように次のセッションに引き継いでください。`$handoff`

**次セッションは現状調査と修正プラン作成まで。実装を始めず、仕様書をユーザーに提示する。**
成果物の推奨名は `.agents/docs/tip-excel-layout-plan.md`。
直前の「仕様書OKです。実装してください」は周面2表への承認であり、今回の杭先端ばねには適用しない。

本セッションは引き継ぎ文書の保存のみ。既存コード・資料の入口、作業ツリー、指定ファイルのハッシュは確認したが、先端見本のセル・数式・書式・印刷の詳細調査、修正プラン作成、実装は行っていない。

## 現在地と先に読む資料

水平地盤ばね、有効抵抗土圧、杭周面ばね・杭周面の支持力は、仕様書作成→承認→実装→検証まで完了している。
現在は全4工程で7シート: `変換結果`、`水平地盤ばね`、`有効抵抗土圧`、`杭周面ばね`、`杭周面の支持力`、`杭先端ばね`、`入力根拠`。
周面は2シートだが `shaft` という1工程のまま。先端は `tip` 工程でK1/K2とFy/Fuを扱う。

| 参照先 | 用途 |
| --- | --- |
| `.agents/docs/excel-report-implementation.md` | 現在の共通帳票・GUI・保存・各表の実装記録 |
| `.agents/docs/shaft-excel-layout-plan.md` | 直前の仕様書。第11節が実装結果。今回の仕様書の粒度の参考 |
| `.agents/docs/horizontal-spring-excel-layout-plan.md` | 完了済み水平表の仕様 |
| `.agents/docs/effective-pressure-excel-layout-plan.md` | 完了済み土圧表の仕様 |
| `.agents/docs/sdc-excel-calculation-report-spec.md` | 初期Excel仕様。古いシート数・未実装記述と現状を区別する |
| `.agents/docs/fill-pile-tip-suppot-info-script.md` | 先端K1/K2/Fy/Fuの読取・配置・保存・拒否条件 |
| `.agents/docs/snap-pile-tip-suppot-info-mapping.md` | 先端SDC/NDUの対応調査。snapの項目番号をtestへ流用しない |
| `.agents/docs/suppot-info-editing-rules.md` | 支点・ケース同期などの保護規則 |
| `scripts/fill_pile_tip_suppot_info.py` | 先端SDC読取、最深節点の特定、NDUへの転記。ファイル名は `pile_tip_suppot` |
| `scripts/calculation_record.py` | 先端原値・幾何・出典・固定実入力・10欄の記録 |
| `scripts/excel_report.py` | `build` の `tip` 分岐、数式AST、採用照合、内部リンク、印刷 |
| `scripts/excel_preview.py` | Excelと共通のセルモデルによるTkプレビュー |
| `scripts/portable_converter.py` / `scripts/excel_cli.py` | 共通変換API、個別CLI、保存処理 |
| `scripts/sdc_converter_app.py` / `scripts/portable_smoke.py` | GUIと配布確認 |
| `tests/test_fill_pile_tip_suppot_info.py` / `tests/test_excel_report.py` / `tests/test_portable_converter.py` | 先端転記、共通Excel、保存・CLIの回帰 |
| `tests/test_horizontal_excel_report.py` / `tests/test_effective_pressure_excel_report.py` / `tests/test_shaft_excel_report.py` | 完了済み各表の保護 |

現行の先端主表には「杭先端の結果」（K1/K2/Fy/Fuの再計算と実入力）、「杭頭・最深節点と杭長」、「ばね実入力」、「制限値実入力」、「Python Decimalの丸め前文字列」がある。
見本との差分は次回実ファイルを調査して確定する。周面の7列ブロック、縦長配置、92%印刷設定を先端へ無条件に流用しない。

直前の検証済み全7シートExcel:
`outputs/shaft-review-20260917-235254/今町橋りょう4P_計算過程_杭周面修正版.xlsx`

これは `tests/data/今町橋りょう4P/今町橋りょう4P(右).sdc` と `tests/data/今町橋りょう4P/今町橋りょう4P(C方向･右押し→).ndu`、全4工程、KG対応 `4:1 5:2 6:3`、右押し、周面 `existing-screen` で作成した、モデル未保存の確認用出力。先端見本ではなく現行出力の確認資料として使う。

## 入力・見本の特定

- 基準はユーザーが指定した **test内の見本**。snap内の同名ファイルへ置き換えない。
- 対象シート名は正確に **`杭先端ばね`**。番号で決め打ちしない。
- 見本は先行作業中にも更新された。次回は最新のハッシュとシート構成を確認し、過去の抽出物をそのまま使わない。
- 今回の引き継ぎ時点でのSHA-256は以下。

| ファイル | SHA-256 |
| --- | --- |
| `test/今町橋りょう4P(C方向･右押し→)_変換済み.ndu.計算過程.見本xlsx.xlsx` | `7a406fc87569c07e1b8449084a25ce6cbf317677a713831bfd40526decbf4e3b` |
| `tests/data/今町橋りょう4P/今町橋りょう4P(右).sdc` | `9b9aed2461b87acb5193c8eac79cf07b6d9824bdbd80e096416e197f324f64a7` |
| `tests/data/今町橋りょう4P/今町橋りょう4P(C方向･右押し→).ndu` | `31e9e85971d07ae1a0b21273f500b0d8d039184ecf9febb6db642fbfabfbd22a` |

## 既存計算と保護する事項

以下は既存資料・コードからの引き継ぎ。次回、SDC・見本・実際の出力を照合して仕様書へ根拠を記載する。

- K1/K2はSDC f表の鉛直ばね・短期第1/第2勾配。Fy/Fuはg表の地震時・押込み側降伏点/終局点。
- 値は既にkN/m・kN。杭長や負担幅、奥行き本数を掛けず、追加の丸め・補正なしで転記する。見本との不一致から換算係数を推測しない。
- 対象は各KGの最深節点。現在の基準入力は122/147/172だが、節点・項目番号や3本固定を仕様にしない。
- 先端支点の10欄は `K1,Fy,空欄,K2,Fu,空欄,K2,K1,K2,K2`。負側制限値の空欄と0を区別し、周面抵抗との合成は行わない。
- 原値のSDC列はKG番号とは別。処理・SDC行・CSV欄をキーにし、別列の同値原値も独立して参照できること。Excel原値変更時の再計算と、変換時の固定実入力・差の照合を保持する。
- 幾何・杭長照合、追加/更新・支点数・`Suppot_ChokuKisoCaseNo` 同期、既存キー・他支点・ケース値、保存順序を保護する。Excel編集をNDUへ戻す機能は追加しない。
- 不連続な接続、傾斜・突出杭、杭長不一致、非ゼロの水平/回転先端ばね、範囲支点との重複、重複Y支点、複数ケースなどの既存拒否条件を変更しない。
- 水平・土圧・周面2表の数式・原値・固定値・リンク・GUI・印刷を保護する。共通セルモデルを使う変更の影響を調査する。

## 次回の調査と修正プランの骨子

1. 本書、適用AGENTS.md、直前の実装記録、作業ツリー、入力・見本のハッシュを確認する。現在の未コミット変更を基準にし、HEADへ戻さない。
2. 指定見本の「杭先端ばね」を読取り専用で調査する。使用範囲、KG/SDC列/節点/項目の対応、見出し、単位、原値、数式と保存値、丸め、空欄/0、結合、罫線、列幅・行高、印刷範囲・倍率・改ページ・反復見出しを記録し、代表範囲を目視確認する。
3. 現行帳票、計算記録、SDCのf/g表、NDUの10欄を追跡する。全対象のK1/K2/Fy/Fuと空欄を独立に照合し、見本の計算・表示上の差分と不整合を分ける。
4. 見本に合わせる主表の列・行配置、式・書式・出典参照を具体化する。現行の採用照合、杭長・幾何、実入力一覧、正確な十進文字列を主表と「入力根拠」にどう配置するか決める。
5. 変換結果・入力根拠からの内部リンク、GUIのシート選択・結合セル・スクロール・数式バー、保存XLSXとの一致、印刷設定を検討する。見本の印刷設定は実際の出力幅で検証する計画を含める。
6. 任意KG数/選択順、別SDC列、非連番節点、異なる杭長、K1≠K2、Fy≠Fu、0・小数・負側空欄、既存支点更新/追加、tipのみ/全工程/tip未選択について受入条件を定める。転記値を丸めない既存仕様を保護する。
7. ファイル別変更案、実装順序、既存回帰と追加検証、NDUバイト一致、他主表保護、Excel再計算、PDF全ページの目視確認を計画する。不明点は根拠と選択肢を記し、推測で実装しない。
8. `.agents/docs/tip-excel-layout-plan.md` を作成してユーザーへ提示する。**ここで止める。コード・テストの実装、配布EXE/ZIPの再作成は次回作業の範囲外。**

## 作業ツリーと直前の検証

ブランチ: `njc/tkinter`。HEAD: `7acf32876e4cdce3959021e9bb9faa642f492b4f`（`excel カスタム`）。
**周面2表の実装は未コミットの作業ツリーにある。HEADだけで現状を判断しない。**

本引き継ぎ作成前の状態:

- 変更済み: `.agents/docs/excel-report-implementation.md`、`scripts/calculation_record.py`、`scripts/excel_report.py`、`scripts/excel_preview.py`、`scripts/portable_smoke.py`、`tests/test_excel_report.py`。
- 未追跡: `.agents/docs/shaft-excel-layout-plan.md`、`tests/test_shaft_excel_report.py`、`outputs/shaft-review-20260917-235254/`。
- 今回は本書と同文バックアップを追加する。既存のコード・テスト・入力・見本・出力は変更しない。

直前の周面実装では全149テストとGUI自己診断が成功。Excel 16.0で5,536数式を再計算し、348採用値一致・486照合欄0を確認した。周面各3ページ、140層の長表各4ページ、計14ページを目視確認した。NDUは改修前とバイト一致し、水平・土圧・先端の主表を保持している。
これは直前の実装の検証結果で、今回の先端見本への適合確認ではない。引き継ぎ保存だけの今回はテストを再実行していない。

```powershell
.venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -q
```

周面の基準・照合スクリプト・PDF・画像は `build/shaft-excel-implementation/`（無視対象）。再利用する際は最新の入力・コードと一致するか確認する。配布EXE/ZIPは周面改訂後に再作成していない。

## 環境と引き継ぎの扱い

- Windows / PowerShell。日本語文書は `Get-Content -Encoding UTF8`、Pythonは `.venv\Scripts\python.exe -B -X utf8`。日本語コードのパイプ実行時は `$OutputEncoding = [System.Text.UTF8Encoding]::new($false)`。
- 仮想環境にはXlsxWriterがある。読取り用openpyxlは `C:/Users/sasai/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe` で利用できた。依存を変更する前に利用可能環境を確認する。
- NDU全体を単純にCP932 decodeすると失敗する場合がある。既存のバイト単位パーサーを使う。UTF-8 BOMなしの日本語ps1は `Get-Content -Encoding UTF8 -Raw` で読んだScriptBlock経由で実行する。
- Excel実機確認は独立インスタンスと確認用コピーで行い、原本を保存しない。`~$...xlsx` はロックファイルであり、ハッシュ収集から除外する。ユーザーのExcelを閉じない。
- 今回確認した祖先と `.agents/docs` の範囲に適用AGENTS.mdはなかった。次回も確認する。
- `$handoff` スキルは利用可能一覧、Codex/Agentsのスキル・プラグイン、Claude側等のローカル検索で見つからず、通常の引き継ぎ文書として保存した。
- 本書の同文バックアップ: `C:/Users/sasai/AppData/Local/Temp/HANDOFF-tip-excel-20260918.md`。
- 過去の `handoff-shaft-excel.md` は周面調査開始時の履歴。周面は既に承認・実装済みであり、今回の次作業の指示は本書を優先する。

次セッションへの開始文:

> `.agents/docs/handoff-tip-excel.md` を読み、指定見本の「杭先端ばね」と現行処理を調査してください。実装はせず、`.agents/docs/tip-excel-layout-plan.md` に仕様書（修正プラン）を作成してください。
