# テストと branch coverage

更新日: 2026-09-18

## 環境

実行時依存は `requirements.txt`、テストと coverage の依存は `requirements-test.txt` に分ける。
このリポジトリの `.venv` は `pip` モジュールを含まないため、依存の導入には `uv` を使う。

```powershell
uv pip install --python .venv\Scripts\python.exe -r requirements-test.txt
```

## 全テスト

カレントディレクトリに依存しない fixture パスは `tests/fixture_paths.py` に集約している。
実データの組合せと用途は `tests/data/README.md` を参照する。

```powershell
.venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -q
```

2026-09-18 の移行完了時点では 229 tests が成功する。

## branch coverage

アプリ本体と開発補助スクリプトを含む `scripts` 全体を対象にする。

```powershell
.venv\Scripts\python.exe -m coverage erase
.venv\Scripts\python.exe -m coverage run --branch --source=scripts -m unittest discover -s tests -q
.venv\Scripts\python.exe -m coverage report -m
```

パス移行直後の基準値は 226 tests、全体 81%（3939 statements、1392 branches）、
`scripts/sdc_columns.py` は 95% だった。形式条件、Ver.5.2.1土圧列、杭列数の拒否境界を
3 tests 追加した後は 229 tests、全体 81%、`scripts/sdc_columns.py` は branch を含め 100% である。

全体値には、通常の unit test から直接実行しない配布ビルド、packaged smoke、調査専用スクリプトも
含まれる。これらを数字だけのために import・mock せず、配布検証は各スクリプトの実フローで確認する。

## 配布 smoke

```powershell
.venv-build\Scripts\python.exe -B -X utf8 scripts\build_portable.py
```

build smoke は `tests/data` の右・左 fixture を外部入力として渡すが、EXE と fixture を同梱しない。
`portable_smoke.py` は入力を一時ディレクトリへコピーしてからGUI保存までを実行し、原本へ書き込まない。
2026-09-18 の検証では全テスト、PyInstaller、右・左の packaged self-test が成功した。
