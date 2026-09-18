# NDU supported subset

## Byte-preserving model

NDU はファイル全体を単一の文字コードで往復変換しない。次の方法を基本とする。

1. `raw.splitlines(keepends=True)` で行と改行を保持する。
2. 対象の ASCII キーだけを照合する。
3. `=` の右辺を必要な範囲だけ分割する。
4. 対象フィールドだけを ASCII 数値として置換する。
5. 対象外の行、バイト、空白、改行をそのまま残す。

NDU 全体が CP932 でデコードできるとは限らない。全体デコードに成功することを前提にしない。

## Geometry and group records

以下は1始まりのフィールド番号である。

| Record | Used fields | Meaning in this application |
|---|---:|---|
| `KGInfoN` | 2, 3 | 対象グループに含まれる開始部材番号、終了部材番号。両端を含む |
| `ElementInfoN` | 5, 6 | 部材の始点節点番号、終点節点番号 |
| `JointXYN` | 1, 2 | x 座標、y 座標 |

`KGInfo` の第1フィールドなど、アプリが使わないフィールドの意味は `unknown` とし、推定して変更しない。

## JibanShogenInfo

対象レコードは少なくとも7フィールドを必要とする。

| Field | Meaning used here | Operation |
|---:|---|---|
| 2 | 水平地盤ばね | 水平地盤ばね変換 |
| 3 | 地盤反力度・上端 | 地盤反力度変換 |
| 4 | 地盤反力度・下端 | 地盤反力度変換 |

各処理は自分の対象フィールドだけを更新し、他フィールドを保持する。

## SuppotInfo

`SuppotInfoN` は対象処理では厳密に13フィールドとして扱う。

| Field | Meaning used here |
|---:|---|
| 1 | 開始節点。空欄なら単一節点支持 |
| 2 | 終了節点、または単一節点の節点番号 |
| 3 | 方向。杭の対象支持では `2` |
| 4 | K1 正側 |
| 5 | F1 正側 |
| 6 | F1 負側 |
| 7 | K2 正側 |
| 8 | F2 正側 |
| 9 | F2 負側 |
| 10 | K3 正側 |
| 11 | K1 負側 |
| 12 | K2 負側 |
| 13 | K3 負側 |

開始節点が入った範囲支持が対象節点と重なる場合は自動統合しない。方向2の同一節点支持が複数ある場合も中止する。

## Support counts and case rows

支持を追加するときは、次の不変条件を同時に満たす。

```text
SuppotNum
  = SuppotRow
  = SuppotInfo の件数
  = Suppot_ChokuKisoCaseNo の件数
  = N
```

- `SuppotInfo1` … `SuppotInfoN` は1から連続する。
- `Suppot_ChokuKisoCaseNo1` … `Suppot_ChokuKisoCaseNoN` も1から連続する。
- ケース行の末尾番号は節点番号ではなく、支持項目番号に対応する。
- 欠落ケース行は `=0` で補う。
- 既存の範囲内にある非ゼロ値は保持する。
- N を超える余剰ケース行は、値がゼロの場合だけ削除できる。非ゼロなら孤立データとして中止する。
- 現行処理は `ShitenCaseNum=0` の単一支持ケースだけに対応する。

## What is not specified

この資料は NDU の全レコード、全フィールド、単位系、バージョン差を定義しない。上記以外のキーやフィールドは、実装・fixture・独立した実例が揃うまで `unknown` とする。
