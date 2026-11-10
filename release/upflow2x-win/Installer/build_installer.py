"""
Compile the Upflow2x GUI installer into a standalone executable.

Usage:
    pip install pyinstaller
    python Installer/build_installer.py

Produces:
    dist/Upflow2xInstaller.exe  (Windows)
    dist/Upflow2xInstaller-mac.zip  (macOS, universal app)

Each installer includes the plugin files. It can be run on its own.
"""

from __future__ import annotations

import importlib.util

import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from Installer.build_release import PAYLOAD, _copy
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from Installer.build_release import PAYLOAD, _copy


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    script = repo_root / "Installer" / "gui_installer.py"

    if not script.exists():
        print(f"Missing installer script: {script}")
        return 1

    # An availability check, not a use, so ask the import system directly
    # rather than importing a name we never touch.
    if importlib.util.find_spec("PyInstaller") is None:
        print("PyInstaller is not installed.")
        print("Run:  pip install pyinstaller")
        return 1

    icon_arg: list[str] = []
    icon = repo_root / "Installer" / "icon.ico"
    if icon.exists():
        icon_arg = ["--icon", str(icon)]

    name = "Upflow2xInstaller"

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onedir" if sys.platform == "darwin" else "--onefile",
        "--windowed",
        "--noconfirm",
        "--paths", str(repo_root),
        "--name", name,
        "--distpath", str(repo_root / "dist"),
        "--workpath", str(repo_root / "build"),
        "--specpath", str(repo_root / "build"),
        *icon_arg,
    ]

    if sys.platform == "darwin":
        cmd += ["--target-arch", "universal2"]

    with tempfile.TemporaryDirectory(prefix="upflow2x-setup-") as staging:
        payload = Path(staging) / "plugin"
        for item in PAYLOAD:
            _copy(repo_root / item, payload / item)
        cmd += ["--add-data", f"{payload}:plugin", str(script)]
        print(f"Building {name}...")
        result = subprocess.run(cmd)

    if result.returncode == 0:
        output = repo_root / "dist" / (name + ".exe" if sys.platform.startswith("win") else name)
        if sys.platform == "darwin":
            output = repo_root / "dist" / "Upflow2xInstaller-mac.zip"
            result = subprocess.run([
                "ditto", "-c", "-k", "--sequesterRsrc", "--keepParent",
                str(repo_root / "dist" / f"{name}.app"), str(output),
            ])
            if result.returncode:
                return result.returncode
        print(f"\nBuild complete: {output}")
        print(f"Size: {output.stat().st_size / 1024 / 1024:.1f} MB")
    else:
        print("\nBuild failed. Check the output above for errors.")

    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
