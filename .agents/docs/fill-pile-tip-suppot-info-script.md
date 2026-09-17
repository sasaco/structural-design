# 杭先端の節点ばね入力スクリプト

作成・検証日: 2026-09-17。

[`scripts/fill_pile_tip_suppot_info.py`](../../scripts/fill_pile_tip_suppot_info.py) は、右SDCの杭先端ばね・支持力を、NDUの `SuppotInfo` またはNDTの `SUPPORT` へ入力する。調査の根拠・原ファイルの値・フィールド対応は [杭先端の対応調査](snap-pile-tip-suppot-info-mapping.md) に記載した。

## 入力する内容

既定の対応は `--groups 4:1 5:2 6:3`。KGInfo4・5・6の最深節点（現モデルでは122・147・172）に、直角方向の先端表の1・2・3列目を入力する。

```text
SuppotInfo番号= ,先端節点,2,K1,Fy, ,K2,Fu, ,K2,K1,K2,K2
```

- K1: f表の鉛直ばね・短期第1勾配、K2: 同表の短期第2勾配。
- Fy: g表の地震時・押し込み側降伏点、Fu: 同表の終局点。
- 第3勾配はK2、負側ばねもK1/K2/K2、負側の2つの制限値は空欄。指定snapモデルで確認した配置を使う。
- 値はすでにkN/m・kN。長さ・奥行き本数を掛けず、追加の丸めや補正をせず転記する。
- 既存Y支点があれば第4～13フィールドを更新する。なければ末尾に追加し、支点数を更新する。同じ項目番号の `Suppot_ChokuKisoCaseNo番号=0` も追加する。
- 周面支点・左基礎・他の拘束方向・地盤諸元・土圧等は保持する。周面抵抗と先端支持の合成は行わない。

Python標準ライブラリのみを使い、既存の `fill_jiban_shogen.py` と `fill_suppot_info.py` の読取り・保存処理を共用する。3ファイルを同じ `scripts` フォルダーに置く。

## 確認表示

PowerShell、リポジトリ直下で実行する。引数なしではファイルを変更しない。

```powershell
.venv\Scripts\python.exe -B -X utf8 scripts\fill_pile_tip_suppot_info.py
```

既定入力は `test/今町橋りょう4P(右).sdc` と `test/今町橋りょう4P(C方向･右押し→).ndu`。現在のtest NDUは周面60支点が入力済みで、確認表示では先端3支点の追加後の件数63を示す。旧名の `_土圧入力済み.ndu` は参照しない。

## 別名NDUへ出力

```powershell
.venv\Scripts\python.exe -B -X utf8 scripts\fill_pile_tip_suppot_info.py `
  --output "test\今町橋りょう4P(C方向･右押し→)_杭先端入力済み.ndu" `
  --report ".agents\docs\pile-tip-fill-result.json"
```

現在のtest NDUからは `SuppotInfo61～63` を新規追加する。snap NDUを入力にすれば既存の `SuppotInfo124～126` が対象となる。キー番号はハードコードせず、先端節点と拘束方向で照合する。

## 別名NDTへ出力

NDTにはKGInfoがないため、対象杭のグループと接続を参照するNDUも指定する。NDTとNDUの対象杭の座標・部材接続を検証してから出力する。

```powershell
.venv\Scripts\python.exe -B -X utf8 scripts\fill_pile_tip_suppot_info.py `
  --sdc "snap\今町橋りょう4P(右).sdc" `
  --ndu "snap\今町橋りょう4P(C方向･右押し→).ndu" `
  --ndt "snap\今町橋りょう4P(C方向･右押し→)(Case1_ρm10_αf1_正向).ndt" `
  --output "test\今町橋りょう4P(C方向･右押し→)_杭先端入力済み.ndt"
```

このsnap NDTには既に同じ先端値が入っているため、出力は原本とバイト単位で一致する。NDTの他のばね・土圧等をNDUから同期する処理ではない。

## 保存・検証の範囲

- CP932・改行・対象外のバイトを保持する。NDUは13フィールド、NDTは単一 `SUPPORT` → `CASE-1` の115桁固定幅。
- NDU追加時は `SuppotNum`・`SuppotRow` と `Suppot_ChokuKisoCaseNo` の件数・末尾番号を全支点に合わせる。件数が変わらない更新でも欠落を0で補い、余剰0行を削除する。既存ケース値は保持し、重複・不正値・対応支点のない非0行は拒否する。削除・再採番時も [共通編集ルール](suppot-info-editing-rules.md) に従う。NDT追加時は `DIMENSION` の支点数を更新する。
- 原本と同じ出力先、ハードリンクによる同一ファイル、異なる内容の既存出力は拒否する。同じ内容の出力への再実行は可能。
- 水平・回転先端ばねが非ゼロ、傾斜・突出杭、杭長不一致、不連続な接続、列番号不整合、対象と重なる範囲支点、重複Y支点、複数ケース、NDT固定幅を超える値は拒否する。
- SDCは行番号ではなく見出し・列名・単位を検証する。奇数列・偶数列形式の左SDCには対応しない。
- `--report` は参照ファイルのSHA-256、対象節点、SDCの参照行、K1/K2/Fy/Fu、全10欄の配置、既存NDUとの比較、出力内容のSHA-256を保存する。空欄と0を区別する。`updated` は更新対象となる既存支点数で、値が変わった件数ではない。

## 調査の再実行

次のコマンドはNDU/NDTを書き換えず、調査JSONだけを出力する。

```powershell
.venv\Scripts\python.exe -B -X utf8 scripts\fill_pile_tip_suppot_info.py `
  --sdc "snap\今町橋りょう4P(右).sdc" `
  --ndu "snap\今町橋りょう4P(C方向･右押し→).ndu" `
  --ndt "snap\今町橋りょう4P(C方向･右押し→)(Case1_ρm10_αf1_正向).ndt" `
  --report ".agents\docs\pile-tip-suppot-info-verification.json"
```

## 検証結果

以下は初回実装時の記録。2026-09-17の追加修正で共通保存処理に `Suppot_ChokuKisoCaseNo` の同期を実装した。現在は支点追加行と2つの件数だけでなく、対応するケース行も変更対象となる。[共通編集ルール・回帰検証](suppot-info-editing-rules.md) を参照。

[`tests/test_fill_pile_tip_suppot_info.py`](../../tests/test_fill_pile_tip_suppot_info.py) の18件と既存64件、**計82件成功**。

```powershell
.venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -q
```

現物照合と一時出力で以下を確認した。

1. 右先端3支点の24個の数値欄・6個の空欄を、原SDCのCSV列から別途取り出した期待値と照合して一致。
2. snap NDUとNDTを更新した結果は、対象外も含むファイル全体が原本とバイト単位で一致。
3. 現在のtest NDUの一時コピーでは先端3支点だけを追加し、支点数60→63。追加行と2つの件数変更を除く全バイトは原本と一致。
4. 不連続な節点番号、部材端の逆順、列選択、空欄と0の区別、再実行、保存先衝突、計算中の入力変更、NDT幾何不一致を検証。

[調査JSON](pile-tip-suppot-info-verification.json) では3支点とも `status: match`。NDT出力内容のSHA-256は原NDTと同じ `6a50150ac07051486347407f74c8c5629917a0121fd038071ca7f287d4b218ec`。

原本のsnap/testファイルは未変更。今回作成した恒久ファイルはスクリプト・テスト・文書・調査JSONのみ。Input-JR/JRSNAPでの読込み・解析実行や、空欄の解析上の挙動の確認は行っていない。
