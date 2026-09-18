# SDC to NDU mapping rules

## Common geometry chain

変換対象は次の参照をたどって決める。

```text
ユーザーが選択した KG と SDC 列
  -> KGInfo の部材範囲
  -> ElementInfo の端点節点
  -> JointXY の座標
  -> SDC の深度区間・値
  -> JibanShogenInfo または SuppotInfo
```

事前に次を検証する。

- 部材範囲が存在し、開始・終了番号が妥当である。
- 各 `ElementInfo` と `JointXY` が存在する。
- 杭が一本の連続した鉛直線を形成し、節点 y 座標が重複しない。
- 杭長と SDC の層厚合計が一致する。
- グループ間で節点を共有していない。
- 選択した SDC 列が表に存在する。
- SDC の深度区間に欠落・重複・逆転がない。

## 1. Horizontal ground spring

対象: `JibanShogenInfo` 第2フィールド。

- 部材が単一層に完全に入る場合、その層の選択列の値を使う。
- 部材が層境界をまたぐ場合、既定は長さ加重平均である。

```text
Kmember = Σ(Klayer × overlap_length) / member_length
```

- 既定値は `ROUND_HALF_UP` で整数に丸める。
- 任意ポリシーとして `error`、`midpoint`、`skip` がある。既定動作と混同しない。
- 既存 NDU のばね値は比較には使えるが、計算値の補正には使わない。

## 2. Ground pressure

対象: `JibanShogenInfo` 第3・第4フィールド。

各 SDC 層の上端値・下端値の間を深度方向に線形補間する。

### Member inside one layer

部材上端と下端の深度で補間した値を、それぞれ上端・下端反力度に使う。

### Member crossing layers

既定の `integral-average` では、各層との交差部分を別々の台形として積分する。

```text
area = Σ((piece_upper + piece_lower) / 2 × piece_length)
mean = area / member_length
upper_pressure = mean
lower_pressure = mean
```

- 既定は両値を `ROUND_HALF_UP` で小数1桁に丸める。
- `endpoints` ポリシーでは全区間の上端・下端値を保持する。既定と混同しない。
- GUI の `direct` 列指定は反転しない。旧 CLI の `right` 指定だけは歴史的互換のため列を反転する。

## 3. Shaft support

対象: 各節点の方向2の `SuppotInfo`。

1. 杭節点を y 座標で並べる。
2. 各節点の負担区間を、隣接節点との中点から中点までとする。杭頭・杭端側は杭端で切る。
3. SDC の有効区間を `1/β` による除外深度と杭先端根入れでクリップする。
4. 負担区間と各層の交差長ごとに、ばね K と抵抗 F を積分する。

```text
Knode = Σ(Klayer [kN/m²] × overlap_length [m])
Fnode = Σ(Flayer [kN/m]  × overlap_length [m])
```

ここでは平均を取らない。既定は K を整数、F を小数1桁へ `ROUND_HALF_UP` する。

計算した K、F の `SuppotInfo` 第4～13フィールドへの対応は次である。

```text
[K, F, F, K, F, F, K, K, K, K]
```

杭先端節点の負担区間に正の周面抵抗が残る場合、黙って捨てずに中止する。杭先端支持と周面支持の境界ルールが明示されるまで自動配賦しない。K=F=0 の節点について既存支持を自動削除しない。

## 4. Pile-tip support

対象: 各杭グループで y 座標が最も深い節点の方向2 `SuppotInfo`。

SDC の K1、K2、Fy、Fu は長さによる換算や丸めをせず、次のように直接対応させる。

```text
SuppotInfo = [blank, node, 2, K1, Fy, blank, K2, Fu, blank, K2, K1, K2, K2]
```

したがって第4～13フィールドは次である。

```text
[K1, Fy, blank, K2, Fu, blank, K2, K1, K2, K2]
```

空欄は数値ゼロに置き換えない。K3 に K2 を入れることや負側の力を空欄にすることの物理的根拠は、現時点では `unknown` であり、確認済みの対応形としてのみ保持する。
