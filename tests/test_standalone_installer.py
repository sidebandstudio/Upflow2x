"""A downloaded setup must work without Python or adjacent plugin files."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from Installer import build_installer, gui_installer as gui


@pytest.fixture
def setup(tmp_path, monkeypatch):
    source = tmp_path / "bundle" / "plugin"
    for name in ("Installer/Eternal2xLauncher.lua", "Installer/Eternal2x.lua",
                 "Stages/resolve_smooth.py", "Pipeline/config.py", "VERSION",
                 "requirements.txt"):
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("0.5.1" if name == "VERSION" else "payload", encoding="utf-8")
    destination = tmp_path / "installed"
    scripts = tmp_path / "resolve" / "Utility"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(source.parent), raising=False)
    monkeypatch.setattr(gui, "_resolve_scripts_dir", lambda: scripts)
    monkeypatch.setattr(gui, "remove_old_install", lambda: None)
    monkeypatch.setattr(gui, "_find_embedded_python", lambda root: None)
    monkeypatch.setattr(gui, "_find_system_python", lambda: pytest.fail("used system Python"))
    app = gui.InstallerApp.__new__(gui.InstallerApp)
    app.source_root = gui._find_repo_root()
    app.repo_root = destination
    app.root = SimpleNamespace(after=lambda delay, callback, *args: callback(*args))
    app.install_btn = SimpleNamespace(configure=lambda **kwargs: None)
    app._log = lambda *args: None
    app._set_progress = lambda *args: None
    app.install_done = False

    def download_python():
        path = gui._embedded_python_exe(destination)
        path.parent.mkdir(parents=True)
        path.write_bytes(b"private Python")
        return str(path)

    app._setup_embedded_python = download_python
    app._install_packages = lambda path: True
    return app, scripts


def test_setup_installs_without_system_python_and_survives_deleting_the_download(setup):
    import shutil

    app, scripts = setup
    app._do_install()
    assert app.install_done
    conf = dict(line.split("=", 1) for line in
                (scripts / "Eternal2x.conf").read_text(encoding="utf-8").splitlines())
    assert Path(conf["repo_root"]) == app.repo_root
    assert Path(conf["python"]) == gui._embedded_python_exe(app.repo_root)
    assert (scripts / "Upflow2x.lua").read_text() == "payload"
    shutil.rmtree(app.source_root)
    assert (Path(conf["repo_root"]) / "Installer" / "Eternal2x.lua").exists()
    assert Path(conf["python"]).exists()


def test_failed_dependency_download_keeps_the_existing_plugin_and_launcher(setup):
    app, scripts = setup
    scripts.mkdir(parents=True)
    (scripts / "Upflow2x.lua").write_text("previous launcher")
    app.repo_root.mkdir()
    (app.repo_root / "VERSION").write_text("previous version")
    app._install_packages = lambda path: False
    app._do_install()
    assert not app.install_done
    assert (scripts / "Upflow2x.lua").read_text() == "previous launcher"
    assert (app.repo_root / "VERSION").read_text() == "previous version"


def test_reinstall_reuses_private_python_and_preserves_settings(setup, monkeypatch):
    app, scripts = setup
    app._do_install()
    config = scripts / "Eternal2x.conf"
    config.write_text(config.read_text().replace("threshold=5", "threshold=8"))
    monkeypatch.setattr(gui, "_find_embedded_python",
                        lambda root: str(gui._embedded_python_exe(root)))
    app._setup_embedded_python = lambda: pytest.fail("downloaded Python again")
    app._do_install()
    assert "threshold=8" in config.read_text()


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_install_location_is_per_user_and_independent_of_the_download(tmp_path, monkeypatch, platform):
    """In DaVinci Resolve's own folder, beside Fusion and outside its Scripts
    folder, where Resolve would list every file as a script."""
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    expected = (tmp_path / "roaming" / "Blackmagic Design" / "DaVinci Resolve" / "Support"
                if platform == "win32" else
                tmp_path / "Library" / "Application Support" / "Blackmagic Design"
                / "DaVinci Resolve") / "Upflow2x"
    assert gui._install_dir() == expected
    assert "Scripts" not in expected.parts


def test_an_incomplete_private_python_is_not_reused(tmp_path, monkeypatch):
    path = gui._embedded_python_exe(tmp_path)
    path.parent.mkdir(parents=True)
    path.touch()
    monkeypatch.setattr(gui, "_check_python", lambda path: True)
    monkeypatch.setattr(gui.subprocess, "run", lambda *a, **k:
                        subprocess.CompletedProcess(a[0], 1))
    assert gui._find_embedded_python(tmp_path) is None


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_builder_embeds_the_payload_and_keeps_the_mac_app_executable(tmp_path, monkeypatch, platform):
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(build_installer.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(build_installer, "__file__", str(tmp_path / "Installer" / "build_installer.py"))
    for name in build_installer.PAYLOAD:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if name in ("Pipeline", "Stages", "Installer"):
            path.mkdir(exist_ok=True)
        else:
            path.write_text("payload")
    (tmp_path / "Installer" / "gui_installer.py").write_text("# entry point")
    commands = []

    def run(command):
        commands.append(command)
        (tmp_path / "dist").mkdir(exist_ok=True)
        if "--add-data" in command:
            payload = Path(command[command.index("--add-data") + 1].rsplit(":", 1)[0])
            assert (payload / "Installer" / "gui_installer.py").exists()
            assert (payload / "LICENSE.md").exists()
            assert command[-1] == str(tmp_path / "Installer" / "gui_installer.py")
            output = "Upflow2xInstaller.exe" if platform == "win32" else "Upflow2xInstaller"
            (tmp_path / "dist" / output).write_bytes(b"built")
        else:
            Path(command[-1]).write_bytes(b"Mac app archive")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(build_installer.subprocess, "run", run)
    assert build_installer.main() == 0
    if platform == "darwin":
        assert commands[0][commands[0].index("--target-arch") + 1] == "universal2"
        assert commands[1][:5] == ["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent"]
