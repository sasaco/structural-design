"""Run Quarto with this uv environment's Python, including on Windows."""

import os
from pathlib import Path
import shutil
import subprocess
import sys


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    local = root / ".tools" / "quarto-1.10.18" / "bin" / "quarto.exe"
    executable = str(local) if local.is_file() else shutil.which("quarto")
    if executable is None:
        raise SystemExit("Quarto が見つかりません。uv run python scripts/setup.py を実行してください。")
    env = os.environ.copy()
    env["QUARTO_PYTHON"] = sys.executable
    env["PYTHONUTF8"] = "1"
    args = sys.argv[1:] or ["render", "--to", "all"]
    return subprocess.call([executable, *args], cwd=root, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
