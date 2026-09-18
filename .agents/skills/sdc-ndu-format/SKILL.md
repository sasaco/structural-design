---
name: sdc-ndu-format
description: Inspect, explain, validate, or modify this repository's supported .sdc and .ndu formats and their mappings, including KGInfo, ElementInfo, JointXY, JibanShogenInfo, SuppotInfo, pile-column selection, and SDC-to-NDU conversion. Use for parser changes, new SDC dialects, model-file edits, conversion reviews, and format troubleshooting. Treat the documents as an observed application-supported subset, not a complete vendor specification.
---

# SDC / NDU Format Knowledge

このリポジトリが実装・検証してきた `.sdc` と `.ndu` の部分仕様、および両者の対応規則を扱う。
ベンダー形式全体の公式仕様ではなく、アプリが安全に読み書きできる範囲の知識として使う。

## Reference routing

作業に必要な資料だけを読む。

- 知識の適用範囲、確度ラベル、根拠の優先順位: [references/scope-and-evidence.md](references/scope-and-evidence.md)
- SDC の対応見出し、列指定、条件別の部分仕様: [references/sdc-supported-subset.md](references/sdc-supported-subset.md)
- NDU の対象レコード、フィールド位置、件数整合: [references/ndu-supported-subset.md](references/ndu-supported-subset.md)
- SDC から NDU への4種類の計算・対応規則: [references/mapping-rules.md](references/mapping-rules.md)
- 書換え時の不変条件、中止条件、検証手順: [references/mutation-invariants.md](references/mutation-invariants.md)
- 既知の方言、未確定事項、新形式追加の手順: [references/variants-and-unknowns.md](references/variants-and-unknowns.md)
- 今町橋4Pモデルで確認した具体例: [references/imacho-4p-example.md](references/imacho-4p-example.md)
- 実装、テスト、過去資料への索引: [references/source-map.md](references/source-map.md)

## Required workflow

1. 依頼内容を、水平地盤ばね・地盤反力度・周面支持・杭先端支持・形式調査のどれかに分ける。
2. `scope-and-evidence.md` と該当する形式資料・対応規則を読む。
3. `source-map.md` から現在の実装とテストを確認する。文書とコードが食い違う場合はコードだけを正解扱いせず、テスト・入力例・履歴も照合する。
4. 対象ファイルの文字コード、見出し、列方言、方向、計算条件、NDU の参照整合を変更前に検証する。
5. 曖昧な入力を推測で補わない。対応外または矛盾があれば、対象、期待、実際を明示して安全に中止する。
6. 変更する場合は対象フィールドだけを更新し、無関係なバイト、改行、空白、レコード順を保存する。
7. 変更後は局所不変条件と関連テストを実行し、必要なら全テストも実行する。

## Hard constraints

- SDC は CP932 として読み、見出しは明示した別名だけを境界付きで認識する。曖昧一致を追加しない。
- NDU は原則としてバイト列を保持し、対象となる ASCII キー行の右辺だけを解釈・更新する。ファイル全体を文字列として再生成しない。
- 部材番号、節点番号、支持項目番号、支持ケース番号を混同しない。
- 空欄と数値 `0` は意味が異なるため、相互変換しない。
- 既存 NDU の値を使って SDC 由来の計算結果を都合よく補正しない。
- 複数の候補、未知の見出し、混在条件、不整合な参照を検出したら自動選択しない。
- 上書きは、依頼で許可され、事前検証を通り、復元可能な方法が取れる場合だけ行う。

## Maintaining this knowledge

新しい形式や例外を追加するときは、実装だけでなく該当 reference、fixture、回帰テストを同じ変更で更新する。
確定していない内容は断定せず、`observed`、`inferred`、`unknown` のいずれかで記録する。

Skill 自体の構造は次で検証する。

```powershell
.venv\Scripts\python.exe -X utf8 C:\Users\sasai\.codex\skills\.system\skill-creator\scripts\quick_validate.py .agents\skills\sdc-ndu-format
```
