# テストデータ一本化・テスト拡充 引き継ぎ

作成日: 2026-09-18  
リポジトリ: `C:\Users\sasai\Documents\structural-design`  
ブランチ: `sdc-converter`  
作成時 HEAD: `93aece5282674d73ec926ca662432eec92d9a8f8`

## 次セッションへの依頼

次の作業を実装し、検証まで完了してください。

> `test` は廃止・削除済み。テストデータは `tests/data` に一本化する。  
> テストデータを参照するドキュメントと `tests/*.py` の参照先を `tests/data` に修正し、カバレッジが足りない箇所のテストを追加する。

このセッションでは調査と引き継ぎ書作成だけを行っており、移行実装はしていません。

## 最初に確認するもの

1. `tests/data/README.md`
2. `.agents/skills/sdc-ndu-format/SKILL.md`
3. 同 skill から案内される、今回必要な参照資料
   - `references/source-map.md`
   - `references/sdc-format.md`
   - `references/mapping-rules.md`
   - `references/mutation-rules.md`
4. `tests/test_legacy_v521_sdc.py`
5. 本書の「現在確認できている参照箇所」に挙げたテストとスクリプト

SDC/NDU の組み合わせはファイル名や同一フォルダーにあることだけで推測せず、`tests/data/README.md` と format skill の対応規則に従ってください。SDC 方言の判別もファイル名に依存させないでください。

## 現在の状態

- 旧 `test/` は削除済みです。復元しないでください。
- 旧 `tests/testdata/` も作業ツリーからなくなっています。
- 新しい `tests/data/` は作成時点で untracked です。
- `tests/data/` は計 26 ファイルです。
  - `README.md`: 1
  - `.sdc`: 9
  - `.ndu`: 16
- データ群は次の4フォルダーに分かれています。
  - `今町橋りょう4P`
  - `中ノ沢BLR2`
  - `札幌駅P2橋脚`
  - `品川(東タ)道路P6`
- 直前までに Ver.5.2.1 SDC 対応が実装されています。アプリのバージョンは 1.5.0 です。
- ユーザーによる削除・移動、ステージ済み変更、既存実装変更が多数あります。`git reset`、`git checkout --`、一括ステージなどで触らず、現在の変更を保持してください。

### 移動後のテスト基準値

次を実行しました。

```powershell
.venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -q
```

結果:

- 162 tests
- 132 errors
- 6 failures
- 大半は旧 `test/`、`snap/`、`tests/testdata/` を参照したことによる `FileNotFoundError`、または入力が開けないことに起因する GUI の not-ready です。

したがって、まずパス移行を完了し、その後に残る失敗を実装上の回帰として調査してください。この基準値だけを根拠にパーサー処理を変更しないでください。

### カバレッジ環境

- 現在、明示的な coverage 設定や依存は確認できませんでした。
- `.venv\Scripts\python.exe -m coverage --version` は `No module named coverage` で失敗します。

テスト本数をカバレッジの代用にせず、リポジトリの依存管理方針を確認したうえで branch coverage を計測できる環境を用意し、移行後の基準値と追加後の結果を記録してください。根拠なしに目標パーセンテージを先に決める必要はありません。

## 現在確認できている参照箇所

### `tests/*.py` の直接参照

以下に `test/`、`snap/`、または `tests/testdata/` への参照があります。

- `tests/test_excel_report.py`
- `tests/test_tip_excel_report.py`
- `tests/test_remove_support_sheets.py`
- `tests/test_shaft_excel_report.py`
- `tests/test_left_sdc.py`
- `tests/test_horizontal_excel_report.py`
- `tests/test_effective_pressure_excel_report.py`
- `tests/test_portable_converter.py`
- `tests/test_fill_pile_tip_suppot_info.py`
- `tests/test_liquefaction_sdc.py`
- `tests/test_legacy_v521_sdc.py`
- `tests/test_direction_selection.py`
- `tests/test_fill_suppot_info.py`
- `tests/test_model_preview.py`

### 間接参照

テストがスクリプトの既定値を利用しており、その既定値が旧データを指しています。

- `tests/test_column_assignment.py` → `scripts/fill_jiban_shogen.py` の `DEFAULT_SDC` / `DEFAULT_NDU`
- `tests/test_left_sdc.py` → `scripts/fill_jiban_shogen.py` の既定値
- `tests/test_kg_selection.py` → `scripts/fill_jiban_shogen.py` の既定値
- `scripts/fill_suppot_info.py` の `DEFAULT_NDU`
- `scripts/build_portable.py` の smoke test 入力
- `scripts/analyze_suppot_info.py` のサンプル入力

ユーザー指定はドキュメントとテストの修正ですが、テストが間接的に利用する fixture の既定値・smoke 入力も `tests/data` に合わせないと自己完結しません。実行時の一般的なアプリ既定値まで無関係に変えず、「リポジトリ内テストデータを指す開発用既定値」を対象にしてください。

### ドキュメント

少なくとも次のファイルに旧パスがあります。作業開始時に再度 `rg` して完全な一覧を取得してください。

- `.agents/docs/fill-jiban-shogen-script.md`
- `.agents/docs/excel-report-implementation.md`
- `.agents/docs/effective-pressure-excel-layout-plan.md`
- `.agents/docs/fill-pile-tip-suppot-info-script.md`
- `.agents/docs/handoff-tip-excel.md`
- `.agents/docs/handoff-jiban-pressure.md`
- `.agents/docs/handoff-shaft-excel.md`
- `.agents/docs/left-sdc-support-plan.md`
- `.agents/docs/legacy-v521-sdc-support-plan.md`
- `.agents/docs/handoff-remove-support-sheets.md`
- `.agents/docs/excel-remove-support-sheets-plan.md`
- `.agents/docs/horizontal-spring-excel-layout-plan.md`
- `.agents/docs/fill-suppot-info-script.md`
- `.agents/docs/liquefaction-sdc-support-plan.md`
- `.agents/docs/sdc-direction-pressure-case-selection-plan.md`
- `.agents/docs/left-sdc-support-implementation.md`
- `.agents/docs/sdc-excel-calculation-report-spec.md`
- `.agents/docs/snap-jiban-shogen-mapping.md`
- `.agents/docs/snap-jiban-pressure-spec.md`
- `.agents/docs/tip-excel-layout-plan.md`
- `.agents/docs/shaft-excel-layout-plan.md`
- `.agents/docs/snap-pile-tip-suppot-info-mapping.md`
- `.agents/docs/suppot-info-editing-rules.md`
- `.agents/docs/snap-suppot-info-mapping.md`
- `.agents/skills/sdc-ndu-format/references/source-map.md`

`docs/`、ルート README、スクリプト内コメントも検索対象に含めてください。実行可能なコマンド、リンク、「現在の入力」「既定入力」は新パスへ更新します。過去の作業記録に書かれた旧パスは、同一 fixture の移動を指すなら更新して構いませんが、履歴上の事実・当時のハッシュ・当時存在したパスを偽る変更は避け、必要なら「旧パス（当時）」と明記してください。

## 実装方針

### 1. fixture パスを一元化する

`tests/data` のルートと代表的な fixture の対応を、例えば `tests/fixture_paths.py` または同等の小さなヘルパーへ集約してください。各テストに大量の文字列リテラルを再配置すると、次回の移動で同じ問題が再発します。

- リポジトリルートから安定して解決できる `pathlib.Path` を使う。
- テストのカレントディレクトリに依存させない。
- 既存 API が文字列を要求する場所だけ `str(...)` に変換する。
- fixture の SDC/NDU ペアは `tests/data/README.md` の対応表に沿わせる。

### 2. テストを自己完結させる

- 外部の `snap/` に依存するテストを、適切な `tests/data` fixture に移行する。
- 旧 `test/` や `tests/testdata/` を復元・複製して互換レイヤーにしない。
- 存在しない入力を理由に skip/xfail してテストを弱めない。
- fixture 原本には書き込まない。変更テストは `TemporaryDirectory` またはメモリ上のコピーを使う。
- CP932、CRLF、空欄とゼロの区別、未変更バイトの保持を壊さない。

注意: `tests/test_liquefaction_sdc.py` は、現在存在しない旧 fixture `test/ラーメン橋R8　鋼管ソイルセメント杭(液状化)(液状化時用).sdc` を参照しています。単純なパス置換はできません。`tests/data/中ノ沢BLR2` の液状化 SDC のうち意味的に対応するものを選び、方言・行番号・期待値を実データに合わせて見直してください。固有のケースが本当に必要な場合だけ、理由を明文化して fixture 追加を検討してください。

### 3. `tests/data/README.md` を整合させる

README 冒頭には札幌駅・品川を未対応とする古い結論が残る一方、後段には対応済みの説明があります。Ver.5.2.1 対応後の現状と矛盾しないように修正してください。fixture の正式な用途と SDC/NDU の組み合わせを、この README を一次案内として維持します。

### 4. カバレッジを計測して不足分を追加する

パス移行後、まず全テスト成功状態で branch coverage の基準値を取得してください。レポートから未実行分岐を確認し、挙動を固定する価値がある箇所にテストを追加します。単なる行実行や私有実装の追認ではなく、入力と期待結果が明確なテストにしてください。

優先的に確認する候補:

- `scripts/sdc_columns.py` の現行・液状化・Ver.5.2.1 方言分岐と拒否境界
- 応答変位法 / 非応答変位法、線路直角 / 線路方向、直杭 / 右杭 / 左杭の選択
- 2列 / 3列の読込み、1列 / 4列以上の明示的拒否、共通ソースの重複排除
- `KGInfo`、`ElementInfo`、`JointXY`、`JibanShogenInfo`、`SuppotInfo` の候補抽出・対応付け
- `portable_converter.prepare/save` の入力検証、保存失敗、準備前保存などの失敗分岐
- モデルプレビューの再読込み、パス変更、並行処理周辺
- CP932、空欄と数値ゼロ、未変更行・未変更バイト保持
- 配布ビルドの smoke test が `tests/data` だけで実行できること

既存の XLSX バイト完全一致テストには ZIP timestamp による不安定性があり得ます。遭遇した場合は skip せず、ワークブック内容の意味的比較または生成時刻の決定化など、意図を保つ安定した検証へ改善してください。

### 5. ドキュメントと skill 参照を更新する

- コマンド例をコピー実行できる形にする。
- Windows のパス表記は既存文書の流儀に合わせる。
- `.agents/skills/sdc-ndu-format/references/source-map.md` から、外部 `snap` fixture が必須であるという現状と異なる記述を除く。
- SDC/NDU の observed subset という位置づけは維持し、完全なベンダー仕様と断定しない。

## 検索・検証コマンドの例

作業ツリーが dirty である前提で、最初と最後に状態を確認してください。

```powershell
git status --short
rg -n --hidden -g '!\.git/**' '(tests[/\\]testdata|snap[/\\]|(^|[^A-Za-z0-9_])test[/\\])' tests scripts docs .agents README*
rg -n --hidden -g '!\.git/**' 'tests[/\\]data' tests scripts docs .agents README*
.venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -q
git diff --check
```

必要に応じて `tests/testdata`、`test/`、`test\\`、`snap/`、`snap\\` も個別検索し、正規表現による見落としがないことを確認してください。

coverage 導入後のコマンドは採用したツールと設定に合わせ、README または開発者向け文書へ残してください。例:

```powershell
.venv\Scripts\python.exe -m coverage run --branch -m unittest discover -s tests
.venv\Scripts\python.exe -m coverage report -m
```

format skill に変更を加えた場合は skill の検証も実行してください。

```powershell
python C:\Users\sasai\.codex\skills\.system\skill-creator\scripts\quick_validate.py .agents\skills\sdc-ndu-format
```

## 完了条件

- `test/` と `tests/testdata/` を復元せず、すべての現行 fixture 参照が `tests/data` に一本化されている。
- 外部 `snap/` がなくてもテストが自己完結する。
- `tests/data` の26ファイルが意図せず改変・欠落していない。必要な fixture 追加がある場合は理由と対応表を README に記録する。
- `tests/data/README.md` に矛盾がなく、9 SDC と16 NDU の用途・組み合わせが分かる。
- 全 `unittest discover` が skip による隠蔽なしで成功する。
- branch coverage の基準値、追加テスト、追加後の結果を報告できる。
- 新規テストが主要な正常系・境界・失敗分岐を明示的に検証している。
- テストが fixture 原本を書き換えない。
- 開発用既定値と build smoke が `tests/data` で動作する一方、配布物へ fixture 全体を誤同梱しない。
- ドキュメント中の実行用パス・リンクが新配置と一致する。
- `git diff --check` が成功する。
- ユーザーの既存・ステージ済み変更を reset、checkout、上書き、無関係な stage していない。

## 禁止事項

- 旧 `test/`、`tests/testdata/`、外部 `snap/` をコピーして失敗を見えなくしない。
- 欠損 fixture のテストを skip/削除するだけで通さない。
- NDU の値に合わせるためだけに計算ロジックを変更しない。
- SDC/NDU をファイル名だけで対応付けない。
- fixture 原本をテスト出力先にしない。
- dirty worktree に対して `git reset --hard`、`git checkout --`、一括 `git add` を行わない。

## 次セッション開始用プロンプト

```text
.agents/docs/handoff-tests-data-consolidation.md を読み、記載の順序と完了条件に従って実装してください。旧 test/ と tests/testdata/ は復元せず、fixture は tests/data に一本化してください。最初に tests/data/README.md と sdc-ndu-format skill を確認し、既存の dirty worktree とユーザー変更を保持してください。パス移行後に全テストを通し、branch coverage を測定して、意味のある未検証分岐へテストを追加し、最後にテスト・coverage・検索・diff check の結果を報告してください。
```
