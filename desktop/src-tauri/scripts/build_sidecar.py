from __future__ import annotations

import os
import shutil
import ssl
import subprocess
import sys
from pathlib import Path


def main() -> None:
    if ssl.OPENSSL_VERSION_INFO[:2] == (3, 5):
        raise SystemExit(
            "Refusing to package the Provider-capable sidecar with "
            f"{ssl.OPENSSL_VERSION}. Use the official Python 3.14 runtime "
            "(OpenSSL 3.0.x), for example `py -3.14`, then rebuild."
        )
    tauri_root = Path(__file__).resolve().parents[1]
    repository = tauri_root.parents[1]
    source = repository / "src"
    entry = Path(__file__).with_name("sidecar_entry.py")
    output = tauri_root / "resources" / "sidecar"
    work = tauri_root / "target" / "pyinstaller-sidecar"
    output.mkdir(parents=True, exist_ok=True)
    for artifact in output.iterdir():
        if artifact.name == "README.txt":
            continue
        if artifact.is_dir():
            shutil.rmtree(artifact)
        else:
            artifact.unlink()
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--name",
        "aipic-to-model-sidecar",
        "--paths",
        str(source),
        "--collect-data",
        "aipic_to_model",
        "--collect-data",
        "aipic_to_model.agent.providers",
        "--collect-all",
        "onnxruntime",
        "--distpath",
        str(output),
        "--workpath",
        str(work / "work"),
        "--specpath",
        str(work / "spec"),
        str(entry),
    ]
    environment = dict(os.environ)
    existing_python_path = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        f"{source}{os.pathsep}{existing_python_path}" if existing_python_path else str(source)
    )
    completed = subprocess.run(command, cwd=repository, env=environment, check=False)
    if completed.returncode:
        raise SystemExit(
            "Sidecar packaging failed. Install the dev dependencies with "
            "`python -m pip install -e . --group dev` and retry."
        )


if __name__ == "__main__":
    main()
