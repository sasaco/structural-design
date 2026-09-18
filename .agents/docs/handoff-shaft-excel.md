# Handoff: 杭周面ばね・杭周面の支持力Excel — 次回は調査と修正プラン作成

作成日: 2026-09-17。リポジトリ: `C:/Users/sasai/Documents/structural-design`（`sasaco/structural-design`）。

## 次セッションの依頼と作業範囲

ユーザーの最新依頼:

> `杭周面ばね`および`杭周面の支持力`の変換を、`test\今町橋りょう4P(C方向･右押し→)_変換済み.ndu.計算過程.見本xlsx.xlsx`のシート`杭周面ばね`、`杭周面の支持力`に示す表へ変更したい。いきなり実装せず、次のセッションで現状を把握し、`.agents\docs`に仕様書（修正プラン）を書くよう引き継ぐ。`$handoff`

**次セッションの成果物は現状調査と修正プラン。コード実装は行わず、仕様書をユーザーへ提示するところまで。**
成果物の推奨名は `.agents/docs/shaft-excel-layout-plan.md`。2シートを一緒に扱い、差分・対応案・受入条件をまとめる。
前回の「仕様書OKです。実装してください」は有効抵抗土圧への承認であり、今回の周面2表への実装承認ではない。

今回のセッションでは、引き継ぎに必要な作業ツリー、既存記録、見本のハッシュとシート名だけを確認した。周面見本のセル・数式・書式・印刷の詳細調査、修正プラン作成、コード変更はまだ行っていない。

## 現在地と読む資料

水平地盤ばねと有効抵抗土圧は、仕様書作成→ユーザー承認→実装→検証まで完了している。現在の生成Excelは全4工程を選ぶと6シートで、周面のKとFは同じ「杭周面ばね」シートにある。「杭周面の支持力」という独立シートは現在の生成側にはない。

| 参照先 | 用途 |
| --- | --- |
| `.agents/docs/excel-report-implementation.md` | 現在の共通モデル・保存・GUI・水平/土圧実装結果 |
| `.agents/docs/effective-pressure-excel-layout-plan.md` | 直前の承認済み仕様と実装結果。調査・修正プランの粒度の参考 |
| `.agents/docs/horizontal-spring-excel-layout-plan.md` | 水平表の仕様・受入条件 |
| `.agents/docs/sdc-excel-calculation-report-spec.md` | 初期Excel全体仕様。古い未実装記述・シート数は現状と区別する |
| `.agents/docs/fill-suppot-info-script.md` | 周面の計算、profile、丸め、NDU適用と制約 |
| `.agents/docs/snap-suppot-info-mapping.md` | 既存周面値とSDCの対応調査 |
| `.agents/docs/suppot-info-editing-rules.md` | 支点・ケース同期、保存時に保護する規則 |
| `scripts/fill_suppot_info.py` | 周面SDC読取、節点の負担区間、K/F積分、NDU適用。ファイル名は `suppot` の綴り |
| `scripts/calculation_record.py` | 原値・幾何・区間・丸め前値・固定実入力 |
| `scripts/excel_report.py` | `SHEET_NAMES`、`build` の `shaft` 分岐、数式AST、結合、照合、リンク、印刷 |
| `scripts/excel_preview.py` | Excelと同じセルモデルを表示するTkプレビュー |
| `scripts/portable_converter.py` / `scripts/excel_cli.py` | 4工程の共通API、GUI/個別CLIからの出力・保存 |
| `tests/test_fill_suppot_info.py` / `tests/test_excel_report.py` / `tests/test_portable_converter.py` | 周面計算、共通帳票、保存・CLIの回帰 |
| `tests/test_horizontal_excel_report.py` / `tests/test_effective_pressure_excel_report.py` | 完了済み2表を保護する回帰 |

直前に生成・検証済みの全6シートExcel:
`outputs/effective-pressure-review-20260917-223857/今町橋りょう4P_計算過程_有効抵抗土圧修正版.xlsx`

この確認用出力は、`tests/data/今町橋りょう4P/今町橋りょう4P(右).sdc` と `tests/data/今町橋りょう4P/今町橋りょう4P(C方向･右押し→).ndu`、全4工程、KG対応 `4:1 5:2 6:3`、右押し、周面 `existing-screen` で作成した。モデル未保存の計算確認用。新しい周面見本ではなく、現在の生成側を確認する資料として使う。

## 見本の特定と鮮度

- 基準はユーザー指定の `test` 内の見本。`snap` 内の同名ファイルへ置き換えない。
- 対象名は正確に **`杭周面ばね`** と **`杭周面の支持力`**。
- 今回確認したシート順: `変換結果`、`水平地盤ばね`、`有効抵抗土圧`、`杭周面ばね`、`杭周面の支持力`、`杭先端ばね`、`入力根拠`（7シート）。
- 現在の見本SHA-256は以下。次セッション開始時に再確認する。

```text
ade06612087842f81a2caea6a6acc325c6f5c3641fa2bcb0f81f515e99084372
```

前回の土圧調査・実装時の `3351add2...` から見本が更新され、6シートから7シートへ変わっている。過去の抽出物・画像・シート番号を今回の見本に流用しない。更新原因を推測せず、現在の実ファイルをシート名で特定して読む。

今回の入力SHA-256:

| ファイル | SHA-256 |
| --- | --- |
| `tests/data/今町橋りょう4P/今町橋りょう4P(右).sdc` | `9b9aed2461b87acb5193c8eac79cf07b6d9824bdbd80e096416e197f324f64a7` |
| `tests/data/今町橋りょう4P/今町橋りょう4P(C方向･右押し→).ndu` | `31e9e85971d07ae1a0b21273f500b0d8d039184ecf9febb6db642fbfabfbd22a` |

## 既存計算と保護する事項

以下は既存仕様からの引き継ぎ。次回はソース・SDC・見本を照合して、見本との一致と差分を仕様書へ記載する。

- 周面は `shaft` という1工程でKとFを計算する。帳票を2シートに分ける場合、工程とシートの1対1対応、シート順、未選択時の出力、GUI選択、結果・補足からのリンク、照合先への影響を調査する。
- `existing-screen` は直角方向・押込み側d表の地震時第1勾配と、e表の降伏点（ρgfy考慮）を使用する。Kを6個のばね定数欄、Fを4個の制限値欄へ配置する既存方式。
- 節点負担区間は隣接節点との中点間。地層境界、1/βによる上端除外、根入れによる下端除外を考慮する。節点番号の連番や部材番号との一致を仮定しない。
- Kは `Σ(d表原値[kN/m²] × 区間長[m]) → kN/m`、Fは `Σ(e表原値[kN/m] × 区間長[m]) → kN`。長さで割る平均や、1.2で割る補正・杭本数の追加乗算は行わない。
- d表最終層の有効厚さとe表の全層厚を区別する。見本で扱う深さ・有効長の意味を個別に確認する。
- 既定丸めは合計後のROUND_HALF_UP、K=整数、F=小数1桁。0～6桁指定を保持し、表示桁と採用精度、途中計算と最終丸めを区別する。
- 抵抗対象外、0抵抗の既存支点、先端周面抵抗と先端ばねの合成が必要な入力など、既存の保持・拒否規則を変更しない。
- `SuppotInfo` と `Suppot_ChokuKisoCaseNo` の対応、既存キー・対象外支点・先端支点・保存順序を保護する。Excelの表変更からNDUの計算値・profile変更を推測しない。
- 原値参照は処理＋SDC行＋CSV欄で独立させる。原値編集で再計算できる式、変換時の固定実入力、差の照合、正確な十進文字列を保持する。Excel編集をNDUへ戻さない。
- 前回土圧の単位はユーザーがSDCを正と確定済み（「土圧力 kN/m」、換算なし）。今回の周面2表もSDCの原値・積分後の単位と見本を比較し、不一致は根拠と対応案を示す。値を見本へ合わせるための係数を推測しない。
- 完了済み水平・土圧と、対象外の杭先端の計算・表示を保護する。共通の矩形結合、縦位置、可変ブロック幅、印刷終端は追加済みで、利用可否を調べる。

## 次回の調査と仕様書の骨子

1. 作業ツリー、適用AGENTS.md、既存資料、入力と見本のハッシュを再確認する。過去の状態を復元せず、現在の未コミット変更を基準にする。
2. 見本2シートを読み取り専用で調査する。使用範囲、列・KGブロック順、層・部材・節点・支点の対応、式と保存値、単位、表示桁、空欄と0、Σ、結合、罫線、幅・高さ、印刷範囲・倍率・改ページ・見出し反復を記録し、代表範囲を目視確認する。
3. 現在の周面帳票・計算記録・SDCのd/e表を追跡する。全対象のK/Fを独立計算と照合し、除外境界、層跨ぎ、短い最終区間、原値参照と丸めの差を明確にする。既存資料の60支点を固定仕様にしない。
4. 2つの主表と「入力根拠」の補足・採用照合の役割を決め、具体的な列配置・行生成・式・原値・固定実入力・内部リンクを計画する。現状6シートから見本同様7シートへ分ける影響を確認する。
5. ExcelとGUIに同じセルモデルを使う前提で、結合・画面外アンカー・セル選択・横移動・数式バー・印刷を検討する。先行実装の水平7列・土圧9列を無条件に周面へ流用しない。
6. 任意KG数、異なる節点分割・層数・有効長、非連番、境界・0・非既定丸め、周面だけ/全工程選択、長い表の改ページについて受入条件を定める。基準NDUの出力バイト一致と他工程の回帰も含める。
7. `.agents/docs/shaft-excel-layout-plan.md` に現状、変更対象、配置・計算式、見本との差分、未確定事項、ファイル別修正案、実装順序、検証計画をまとめる。実装せずユーザーへ提示する。

## 作業ツリーと直前の検証

ブランチ: `njc/tkinter`。HEAD: `17c51bfa76844390443e011a59c7976a54f06059`。
**水平・土圧の実装は未コミットの作業ツリーにある。HEADだけで現状を判断しない。**

引き継ぎ作成開始時の変更:

- 変更済み: `.agents/docs/excel-report-implementation.md`、`scripts/excel_report.py`、`scripts/excel_preview.py`、`tests/test_excel_report.py`、`snap` 側の同名見本xlsx。
- 未追跡: 水平/土圧の修正プラン、`tests/test_horizontal_excel_report.py`、`tests/test_effective_pressure_excel_report.py`、`outputs/`、指定の `test` 見本xlsx。
- `snap` 見本には作業中の外部変更もあった。そのまま保持する。今回の `test` 見本も前回から更新されており、上書き・復元しない。

直前の土圧実装検証では全140テストが成功。Microsoft Excel 16.0で全4,876数式と全348採用照合を確認した。土圧A4縦100%・6ページ、水平A4縦90%・3ページを全ページ目視確認済み。NDU出力予定バイト列は改訂前と一致し、水平・周面・先端の主表は実行日時を除いて改訂前と一致した。
これは直前の実装での検証結果であり、今回の周面見本への適合確認ではない。引き継ぎだけの今回はテストを再実行していない。

```powershell
.venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -q
```

検証用基準・PDF・画像は `build/effective-pressure-implementation/`（無視対象）。前回の確認用Excel・入力・見本を変更しない。配布EXE/ZIPは土圧改訂後には再作成していない。

## 環境・引き継ぎの扱い

- Windows / PowerShell。日本語ファイルは `Get-Content -Encoding UTF8`、Pythonは `.venv\Scripts\python.exe -B -X utf8`。日本語コードをPowerShellからパイプする場合は `$OutputEncoding = [System.Text.UTF8Encoding]::new($false)` を設定する。
- `~$...xlsx` はExcelのロックファイル。ハッシュ収集から除外し、ユーザーのExcelを閉じない。実機検証には独立したExcelインスタンスを使い、参照原本を保存しない。
- 今回の確認範囲では適用AGENTS.mdはなかった。次セッションでは再確認する。`linksee-memory` に今回までの判断・実装・注意点を記録済み。
- 指定の `$handoff` スキルは、利用可能一覧とローカルのCodex/Agents/Claudeスキル・プラグインを探したが見つからなかったため、通常の引き継ぎ文書として本書を作成した。新規Issue投稿は行っていない。
- 本書のバックアップ: `C:/Users/sasai/AppData/Local/Temp/HANDOFF-shaft-excel-20260917.md`。本文は同一。
- 以前のIssue #1と `HANDOFF-effective-pressure-excel-20260917.md` は土圧調査開始時の履歴。今回の次作業の指示は本書を優先する。

次セッションへの開始文:

> `.agents/docs/handoff-shaft-excel.md` を読み、指定見本の「杭周面ばね」「杭周面の支持力」と現行処理を調査してください。実装はせず、`.agents/docs/shaft-excel-layout-plan.md` に修正プランを作成してください。
