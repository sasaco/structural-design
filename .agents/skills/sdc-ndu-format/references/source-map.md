# Source map

## Baseline

現在のコミット基準点は `f8ddd1f` である。液状化時対応、Ver.5.2.1共有値形式、回帰テストはこの基準点までにコミットされているため `verified` とする。以後の仕様変更時はこの節と関連ラベルを更新する。

## Implementation index

| Topic | Primary implementation |
|---|---|
| 共通 SDC 列・方向・条件 | `scripts/sdc_columns.py` |
| 水平地盤ばね | `scripts/fill_jiban_shogen.py` |
| 地盤反力度 | `scripts/fill_jiban_pressure.py` |
| 杭周面支持・NDU支持同期 | `scripts/fill_suppot_info.py` |
| 杭先端支持 | `scripts/fill_pile_tip_suppot_info.py` |
| GUI/一括変換の統合 | `scripts/portable_converter.py` |
| NDU/SDCの候補選択 | `scripts/kg_candidates.py` |
| Excel確認帳票 | `scripts/excel_report.py` |

## Test index

| Topic | Tests |
|---|---|
| 水平地盤ばね | `tests/test_fill_jiban_shogen.py` |
| 地盤反力度 | `tests/test_fill_jiban_pressure.py` |
| 杭周面支持 | `tests/test_fill_suppot_info.py` |
| 杭先端支持 | `tests/test_fill_pile_tip_suppot_info.py` |
| 方向選択 | `tests/test_direction_selection.py` |
| 左 SDC | `tests/test_left_sdc.py` |
| 列割当 | `tests/test_column_assignment.py` |
| 液状化時 | `tests/test_liquefaction_sdc.py` |
| GUI/一括変換 | `tests/test_portable_converter.py` |
| KG候補とプレビュー | `tests/test_kg_selection.py`, `tests/test_model_preview.py` |
| Ver.5.2.1共有値 | `tests/test_legacy_v521_sdc.py` |

## Historical design notes

過去資料は判断の背景を調べるために使い、現在のコード・テストより強い根拠として単独では使わない。

- `.agents/docs/snap-jiban-shogen-mapping.md`
- `.agents/docs/snap-jiban-pressure-spec.md`
- `.agents/docs/snap-suppot-info-mapping.md`
- `.agents/docs/snap-pile-tip-suppot-info-mapping.md`
- `.agents/docs/suppot-info-editing-rules.md`
- `.agents/docs/fill-jiban-shogen-script.md`
- `.agents/docs/handoff-jiban-pressure.md`
- `.agents/docs/fill-suppot-info-script.md`
- `.agents/docs/fill-pile-tip-suppot-info-script.md`
- `.agents/docs/left-sdc-support-plan.md`
- `.agents/docs/left-sdc-support-implementation.md`
- `.agents/docs/portable-app.md`
- `.agents/docs/legacy-v521-sdc-support-plan.md`

## Verification commands

液状化時の合成 fixture だけを確認する例:

```powershell
.venv\Scripts\python.exe -B -X utf8 -m unittest tests.test_liquefaction_sdc -q
```

形式共通部または統合処理を変更した場合:

```powershell
.venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -q
```

Ver.5.2.1共有値の4実ファイル組だけを確認する例:

```powershell
.venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -p test_legacy_v521_sdc.py -q
```

Ver.5.2.1共有値形式は `f8ddd1f` でコミット済みのため `verified` とする。

テスト fixture はすべてリポジトリ内の `tests/data` にある。テストと build smoke は外部 `snap/` や
旧 `test/` に依存しない。対応は `tests/fixture_paths.py` と `tests/data/README.md` を一次案内とする。

Skill 構造の確認:

```powershell
.venv\Scripts\python.exe -X utf8 C:\Users\sasai\.codex\skills\.system\skill-creator\scripts\quick_validate.py .agents\skills\sdc-ndu-format
```
