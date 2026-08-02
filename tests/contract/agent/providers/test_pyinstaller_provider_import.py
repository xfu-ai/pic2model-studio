from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_pyinstaller_imports_every_frozen_provider(tmp_path: Path) -> None:
    """The frozen registry must remain available in a packaged desktop build."""

    script = tmp_path / "provider_import_smoke.py"
    script.write_text(
        "from aipic_to_model.agent.providers.registry import create_frozen_provider_registry\n"
        "registry = create_frozen_provider_registry(lambda _ref: None)\n"
        "assert len(registry.ids()) == 39\n",
        encoding="utf-8",
    )
    dist = tmp_path / "dist"
    source_root = Path(__file__).parents[4] / "src"
    provider_data = source_root / "aipic_to_model" / "agent" / "providers" / "data"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onefile",
            "--paths",
            str(source_root),
            "--collect-data",
            "aipic_to_model.agent.providers",
            "--add-data",
            f"{provider_data}{os.pathsep}aipic_to_model/agent/providers/data",
            "--name",
            "provider-import-smoke",
            "--distpath",
            str(dist),
            "--workpath",
            str(tmp_path / "work"),
            "--specpath",
            str(tmp_path / "spec"),
            str(script),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-1000:]
    executable = dist / (
        "provider-import-smoke.exe" if sys.platform == "win32" else "provider-import-smoke"
    )
    smoke = subprocess.run(
        [str(executable)], capture_output=True, text=True, timeout=30, check=False
    )
    assert smoke.returncode == 0, smoke.stderr[-1000:]
