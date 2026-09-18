# Variants and unknowns

## Known variants

| Area | Variant | Status |
|---|---|---|
| SDC direction | 橋軸方向 / 直角方向 | `verified` |
| SDC columns | 番号列 / 奇数偶数列 | `verified` |
| SDC columns | Ver.5.2.1共有値（2・3列） | `worktree-verified` |
| Pressure columns | 1列目、2列目、3列目以降奇数、4列目以降偶数 | `verified` |
| Pressure columns | 1列目、2列目、3列目以降 | `worktree-verified`（2・3列） |
| Calculation condition | 地震時 | `verified` |
| Calculation condition | 液状化時 | `verified` (`e9e3509`) |
| NDU output | byte-preserving targeted rewrite | `verified` |
| Historical model extension | `.ndt` | `observed`; current targetではない |

## Known rejection boundaries

現行実装は次を対応外として扱う。

- 傾斜杭または非鉛直な節点列
- 杭長外へ突き出す SDC 区間
- 層や部材の隙間・重複
- 必須列・見出し・参照レコードの欠落
- 複数条件を混在させた変換
- `ShitenCaseNum` がゼロでない複数支持ケース
- 対象節点に重なる範囲支持
- 同じ節点・方向にある複数支持
- 杭先端の負担域に残る正の周面抵抗
- 意味の分からない既存ばねを自動的にゼロ化・削除する処理
- Ver.5.2.1共有値形式で杭列数1または4以上の入力

## Unknowns that must stay explicit

- SDC と NDU のベンダー公式な全体仕様およびバージョン識別方法
- このアプリが参照しない `KGInfo`、`ElementInfo`、`JibanShogenInfo` 等のフィールド意味
- `SuppotInfo` の全方向・全非線形モデルに共通する一般的意味
- 杭先端対応で K3=K2 とする物理的根拠
- 杭先端対応で負側の力を空欄とする物理的根拠
- 旧 CLI の右列反転が必要となる入力世代・適用範囲の完全な境界
- 1件のモデルで観測した番号・座標・値が他モデルにも成り立つか
- Ver.5.2.1の `3列目以降` 土圧を4列以上へ展開するときの列別意味
- 札幌の既存NDUとSDCで一部一致しない土圧値、および既存NDUの周面・先端合成規則

## Adding a new dialect safely

新しい SDC/NDU 方言を追加するときは次の順に行う。

1. 個人情報・案件情報を除いた最小 fixture を作る。
2. 現行パーサーがどこで拒否するかを再現テストにする。
3. 見出し、列数、単位、方向、条件、境界を既知方言と比較する。
4. 明示的な discriminator を決める。値の大小だけで方言を推測しない。
5. 方言固有の parser を追加し、既存方言の回帰テストを残す。
6. 出力の対象外バイト保存テストとエラー系テストを追加する。
7. この reference と `source-map.md` を更新し、確度ラベルを付ける。

実例が1件だけなら、まず `observed` としてモデル固有資料へ記録し、複数例または明示要件が揃ってから一般仕様に昇格する。
