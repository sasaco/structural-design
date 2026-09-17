"""Windows x64上でテスト→onedirビルド→展開検証→ZIPを作成する。"""

from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile

from portable_converter import APP_NAME, VERSION

ROOT = Path(__file__).resolve().parents[1]


def run(command, **kwargs):
    subprocess.run([str(value) for value in command], check=True, **kwargs)


def main():
    if sys.platform != "win32" or struct.calcsize("P") != 8 or platform.machine().upper() not in ("AMD64", "X86_64"):
        raise SystemExit("Build on Windows with x64 Python.")
    # 先に既存の変換テストも実行し、失敗したら配布物を作らない。
    run([sys.executable, "-B", "-X", "utf8", "-m", "unittest", "discover", "-s", ROOT / "tests", "-q"], cwd=ROOT)
    (ROOT / "build").mkdir(exist_ok=True)
    (ROOT / "dist").mkdir(exist_ok=True)
    session = Path(tempfile.mkdtemp(prefix="portable-", dir=ROOT / "build"))
    run([sys.executable, "-B", "-m", "PyInstaller", "--onedir", "--windowed", "--noupx",
         "--name", APP_NAME, "--distpath", session / "dist", "--workpath", session / "work",
         "--specpath", session / "spec", "--paths", ROOT / "scripts", ROOT / "scripts/sdc_converter_app.py"], cwd=ROOT)
    bundle = session / "dist" / APP_NAME
    # 案件データ・社内仕様書をコピーしない。利用案内とランタイムのライセンスだけを追加する。
    shutil.copyfile(ROOT / "docs/SDCConverter-README.txt", bundle / "はじめに.txt")
    licenses = bundle / "licenses"
    licenses.mkdir()
    shutil.copytree(ROOT / "docs/licenses", licenses / "third-party")
    shutil.copyfile(Path(sys.base_prefix) / "LICENSE.txt", licenses / "Python-LICENSE.txt")
    # Python standaloneのライセンス集があれば全て保存する。
    standalone_licenses = Path(sys.base_prefix) / "licenses"
    if standalone_licenses.is_dir():
        shutil.copytree(standalone_licenses, licenses / "python-runtime")
    for source in (Path(sys.base_prefix) / "tcl").rglob("license.terms"):
        target = licenses / source.relative_to(Path(sys.base_prefix))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    pyinstaller = importlib.metadata.distribution("pyinstaller")
    for item in pyinstaller.files or []:
        if "license" in str(item).lower() and str(item).endswith(".txt"):
            shutil.copyfile(pyinstaller.locate_file(item), licenses / ("PyInstaller-" + Path(item).name))
    xlsxwriter = importlib.metadata.distribution("XlsxWriter")
    for item in xlsxwriter.files or []:
        if "license" in str(item).lower() and str(item).endswith(".txt"):
            shutil.copyfile(xlsxwriter.locate_file(item), licenses / ("XlsxWriter-" + Path(item).name))
    source_files = [ROOT / "scripts" / name for name in (
        "sdc_converter_app.py", "model_preview.py", "kg_candidates.py", "kg_selection.py", "excel_preview.py", "excel_report.py", "excel_cli.py",
        "calculation_record.py", "sdc_columns.py", "portable_converter.py", "portable_smoke.py", "fill_jiban_shogen.py",
        "fill_jiban_pressure.py", "fill_suppot_info.py", "fill_pile_tip_suppot_info.py", "build_portable.py")]
    source_files += [ROOT / "requirements.txt", ROOT / "requirements-build.txt", ROOT / "docs/SDCConverter-README.txt"]
    source_files += sorted(path for path in (ROOT / "docs/licenses").rglob("*") if path.is_file())
    info = {"application": APP_NAME, "version": VERSION, "platform": "win-x64",
            "built_at": datetime.now(timezone.utc).isoformat(), "python": sys.version,
            "runtime_dependencies": {"XlsxWriter": importlib.metadata.version("XlsxWriter")},
            "build_dependencies": {name: importlib.metadata.version(name) for name in (
                "pyinstaller", "pyinstaller-hooks-contrib", "altgraph", "packaging", "pefile", "pywin32-ctypes", "setuptools")},
            "source_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                              for path in source_files}}
    (bundle / "BUILD-INFO.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    # 一旦ZIPにしてから、日本語・空白入りパスへ展開。PATHにPythonがなくても起動・変換できるかを確認。
    name = f"{APP_NAME}-v{VERSION}-win-x64.zip"
    archive_path = session / name
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(bundle.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(bundle.parent))
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.testzip() is None
        if any(Path(name).suffix.lower() in (".sdc", ".ndu", ".xlsx") for name in archive.namelist()):
            raise RuntimeError("Project input data must not be bundled")
        extracted = session / "配布 検証"
        archive.extractall(extracted)
    environment = dict(os.environ)
    for key in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "TCL_LIBRARY", "TK_LIBRARY"):
        environment.pop(key, None)
    environment["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
    smoke_report = session / "packaged-smoke.json"
    run([extracted / APP_NAME / f"{APP_NAME}.exe", "--self-test", smoke_report,
         "--sdc", ROOT / "snap/今町橋りょう4P(右).sdc",
         "--ndu", ROOT / "snap/今町橋りょう4P(C方向･右押し→).ndu"],
        cwd=extracted, env=environment, timeout=90)
    result = json.loads(smoke_report.read_text(encoding="utf-8"))
    if not result.get("ok") or not result.get("frozen"):
        raise RuntimeError(f"Packaged test failed; see {smoke_report}")
    left_smoke = session / "packaged-smoke-left.json"
    run([extracted / APP_NAME / f"{APP_NAME}.exe", "--self-test", left_smoke,
         "--sdc", ROOT / "snap/今町橋りょう4P(左).sdc",
         "--ndu", ROOT / "snap/今町橋りょう4P(C方向･右押し→).ndu",
         "--self-test-groups", "1", "2", "3"],
        cwd=extracted, env=environment, timeout=90)
    left_result = json.loads(left_smoke.read_text(encoding="utf-8"))
    if not left_result.get("ok") or not left_result.get("frozen"):
        raise RuntimeError(f"Packaged left SDC test failed; see {left_smoke}")
    # テスト済みのZIPだけを公開する。既存成果物は成功時に置換する。
    destination = ROOT / "dist" / name
    staging = ROOT / "dist" / (name + ".tmp")
    shutil.copyfile(archive_path, staging)
    os.replace(staging, destination)
    checksum = hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix(".zip.sha256").write_text(f"{checksum}  {name}\n", encoding="ascii")
    shutil.copyfile(smoke_report, ROOT / "dist" / "packaged-smoke.json")
    shutil.copyfile(left_smoke, ROOT / "dist" / "packaged-smoke-left.json")
    print(f"ZIP: {destination}")
    print(f"SHA256: {checksum}")
    print(f"Verified application: {extracted / APP_NAME / (APP_NAME + '.exe')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
