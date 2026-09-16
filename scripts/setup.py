"""Install the checksum-verified official portable Quarto for Windows x64."""

import hashlib
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
from urllib.request import urlopen
from zipfile import ZipFile

VERSION = "1.10.18"
SHA256 = "4e824652ff0da3f646868277582ed59c0872d1456e35350b7d7cdc4243ee18c2"
URL = f"https://github.com/quarto-dev/quarto-cli/releases/download/v{VERSION}/quarto-{VERSION}-win.zip"


def main() -> None:
    if platform.system() != "Windows" or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise SystemExit("このセットアップは Windows x64 用です。他の OS では公式 Quarto を導入してください。")
    tools = Path(__file__).resolve().parents[1] / ".tools"
    installation = tools / f"quarto-{VERSION}"
    executable = installation / "bin" / "quarto.exe"
    if executable.is_file():
        installed_version = subprocess.check_output([str(executable), "--version"], text=True).strip()
        if installed_version != VERSION:
            raise SystemExit(f"Unexpected Quarto version in {installation}: {installed_version}")
        print(f"Quarto {installed_version} is already installed.", flush=True)
        return
    tools.mkdir(exist_ok=True)
    print(f"Downloading Quarto {VERSION} (Windows x64)...", flush=True)
    # TemporaryDirectory removes only the temporary directory it creates.
    with tempfile.TemporaryDirectory(prefix="download-", dir=tools) as temporary:
        archive = Path(temporary) / "quarto.zip"
        with urlopen(URL, timeout=120) as response, archive.open("wb") as output:
            shutil.copyfileobj(response, output)
        with archive.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != SHA256:
            raise SystemExit(f"Quarto SHA-256 mismatch: {actual}")
        print("SHA-256 verified. Extracting...", flush=True)
        with ZipFile(archive) as zipped:
            for member in zipped.infolist():
                destination = (installation / member.filename).resolve()
                if not destination.is_relative_to(installation.resolve()):
                    raise SystemExit("Invalid archive path")
            zipped.extractall(installation)
    if not executable.is_file():
        raise SystemExit(f"Expected executable missing: {executable}")
    subprocess.run([str(executable), "--version"], check=True)
    print("Setup complete. Run: uv run python scripts/quarto.py", flush=True)


if __name__ == "__main__":
    main()
