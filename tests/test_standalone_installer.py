"""A downloaded setup must work without Python or adjacent plugin files."""

from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

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


# --------------------------------------------------------------------------
# the setup window
# --------------------------------------------------------------------------

def window(**attrs):
    """The installer with its Tk widgets stood in for, so nothing opens."""
    app = gui.InstallerApp.__new__(gui.InstallerApp)
    app.root = mock.MagicMock()
    app.root.after = lambda delay, callback, *args: callback(*args)
    app.progress = mock.MagicMock()
    app.progress.cget.return_value = "determinate"
    app.progress_label = mock.MagicMock()
    app.install_btn = mock.MagicMock()
    app.source_root = Path("/no/plugin")
    app.installing = app.install_done = False
    app._pip = None
    app.logged = []
    app._log = lambda msg, tag=None: app.logged.append((msg, tag))
    for key, value in attrs.items():
        setattr(app, key, value)
    return app


@pytest.mark.parametrize("line,step", [
    ("Collecting numpy (from -r requirements.txt (line 1))", "Getting numpy..."),
    ("Collecting opencv-python>=4.8", "Getting opencv-python..."),
    ("  Downloading opencv_python-4.10.0.84-cp37-abi3-win_amd64.whl (38.8 MB)",
     "Downloading opencv_python (38.8 MB)..."),
    ("  Downloading https://files.pythonhosted.org/packages/ab/numpy-2.1.0-cp311-cp311-"
     "macosx_14_0_arm64.whl (5.1 MB)", "Downloading numpy (5.1 MB)..."),
    ("Installing collected packages: numpy, opencv-python",
     "Installing numpy, opencv-python..."),
    ("Requirement already satisfied: numpy in c:\\python\\lib\\site-packages", None),
    ("", None),
])
def test_the_label_follows_pip_from_package_to_package(line, step):
    assert gui._pip_step(line) == step


class FakePip:
    """pip as Popen sees it: its lines, then its exit code."""

    def __init__(self, lines, code=0, hang=False):
        self.lines, self.code, self.hang = lines, code, hang
        self.returncode = None
        self.killed = threading.Event()
        self.args = self.kwargs = None

    def __call__(self, args, **kwargs):
        self.args, self.kwargs = args, kwargs
        self.stdout = self._read()
        return self

    def _read(self):
        yield from (line + "\n" for line in self.lines)
        if self.hang:
            assert self.killed.wait(5), "setup never gave up on pip"

    def kill(self):
        self.killed.set()
        self.code = -9

    def wait(self):
        self.returncode = self.code
        return self.code


PIP_OUTPUT = [
    "Collecting numpy (from -r requirements.txt (line 1))",
    "  Downloading numpy-2.1.0-cp311-cp311-win_amd64.whl (12.9 MB)",
    "Collecting opencv-python (from -r requirements.txt (line 2))",
    "  Downloading opencv_python-4.10.0.84-cp37-abi3-win_amd64.whl (38.8 MB)",
    "Installing collected packages: numpy, opencv-python",
    "Successfully installed numpy-2.1.0 opencv-python-4.10.0.84",
]


def test_the_bar_keeps_moving_while_pip_installs(monkeypatch):
    """A minute or two at 40% with no change looked like setup had hung, and
    people closed it."""
    pip = FakePip(PIP_OUTPUT)
    monkeypatch.setattr(gui.subprocess, "Popen", pip)
    app = window()
    pulses = []
    app._pulse = pulses.append
    assert app._install_packages("python")
    assert pulses == ["Installing packages...", "Getting numpy...",
                      "Downloading numpy (12.9 MB)...", "Getting opencv-python...",
                      "Downloading opencv_python (38.8 MB)...",
                      "Installing numpy, opencv-python..."]
    assert "--quiet" not in pip.args and pip.args[-2:] == ["--progress-bar", "off"]
    assert pip.kwargs["env"]["PYTHONUNBUFFERED"] == "1"
    assert pip.kwargs["stderr"] == subprocess.STDOUT


def test_a_failed_pip_shows_its_error_not_its_first_warning(monkeypatch):
    """The first 200 characters of pip's output could be a retry warning, with
    the line saying what failed cut off."""
    monkeypatch.setattr(gui.subprocess, "Popen", FakePip([
        "WARNING: Retrying (Retry(total=4)) after connection broken: '/simple/numpy/'",
        "Collecting numpy (from -r requirements.txt (line 1))",
        "ERROR: Could not find a version that satisfies the requirement opencv-python",
        "ERROR: No matching distribution found for opencv-python",
    ], code=1))
    app = window()
    app._pulse = lambda label: None
    assert not app._install_packages("python")
    errors = [msg.strip() for msg, tag in app.logged if tag == "err"]
    assert errors[-2:] == [
        "ERROR: Could not find a version that satisfies the requirement opencv-python",
        "ERROR: No matching distribution found for opencv-python"]
    assert not any("WARNING" in msg for msg in errors)


def test_a_pip_that_never_finishes_is_given_up_on(monkeypatch):
    pip = FakePip(["Collecting numpy"], hang=True)
    monkeypatch.setattr(gui.subprocess, "Popen", pip)
    monkeypatch.setattr(gui, "PIP_TIMEOUT", 0.05)
    app = window()
    app._pulse = lambda label: None
    assert not app._install_packages("python")
    assert pip.killed.is_set()
    assert ("  Timed out after 10 minutes.", "err") in app.logged


def test_the_pulse_starts_once_and_the_next_step_stops_it():
    app = window()
    app._pulse("Getting numpy...")
    app.progress.configure.assert_called_with(mode="indeterminate")
    app.progress.cget.return_value = "indeterminate"
    app._pulse("Downloading numpy (12.9 MB)...")
    app.progress.start.assert_called_once()
    app.progress_label.configure.assert_called_with(text="Downloading numpy (12.9 MB)...")
    app._set_progress(75, "")
    app.progress.stop.assert_called_once()
    app.progress.configure.assert_called_with(mode="determinate")


def test_a_failed_install_ends_on_try_again():
    """The label stayed on the step that failed, as if it were still running,
    and the button said Install as though nothing had been tried."""
    app = window(installing=True)
    app.progress.cget.return_value = "indeterminate"
    app._on_finish()
    assert not app.installing
    app.progress.stop.assert_called_once()
    app.progress_label.configure.assert_called_with(text="Setup did not finish.")
    app.install_btn.configure.assert_called_with(state="normal", bg=gui.ACCENT, text="Try Again")
    assert "Try Again" in app.logged[-1][0] and app.logged[-1][1] == "err"


def test_a_finished_install_keeps_its_button():
    app = window(installing=True, install_done=True)
    app._on_finish()
    app.install_btn.configure.assert_not_called()


@pytest.mark.parametrize("answer", [False, True])
def test_closing_during_setup_asks_first(monkeypatch, answer):
    """Quitting partway can leave an update half copied over the version
    Resolve loads."""
    asked = []
    monkeypatch.setattr(gui.messagebox, "askyesno",
                        lambda *args, **kwargs: asked.append(kwargs) or answer)
    app = window(installing=True)
    app._close()
    assert asked and asked[0]["default"] == "no"
    assert app.root.destroy.called is answer


def test_closing_when_setup_is_not_running_just_closes(monkeypatch):
    monkeypatch.setattr(gui.messagebox, "askyesno",
                        lambda *args, **kwargs: pytest.fail("asked with nothing running"))
    app = window(install_done=True)
    app._close()
    app.root.destroy.assert_called_once()


def test_enter_presses_the_focused_button_and_escape_asks_first(monkeypatch):
    """Bound on the window, Enter started an install even with Close focused.
    Bound on each button, it only reaches the one with focus, and invoke does
    nothing on a disabled one, as during and after an install."""
    monkeypatch.setattr(gui, "tk", mock.MagicMock())
    monkeypatch.setattr(gui, "ttk", mock.MagicMock())
    gui.tk.Button.side_effect = lambda *args, **kwargs: mock.MagicMock()
    app = gui.InstallerApp.__new__(gui.InstallerApp)
    app.root, app.repo_root, app.version = mock.MagicMock(), Path("/plugin"), "0.5.2"
    app._log = lambda msg, tag=None: None
    app._build_ui()
    app.root.protocol.assert_called_with("WM_DELETE_WINDOW", app._close)
    window_keys = {call.args[0]: call.args[1] for call in app.root.bind.call_args_list}
    assert "<Return>" not in window_keys
    for button in (app.install_btn, app.close_btn):
        keys = {call.args[0]: call.args[1] for call in button.bind.call_args_list}
        keys["<Return>"](None)
        button.invoke.assert_called_once()
    app._close = mock.Mock()
    window_keys["<Escape>"](None)
    app._close.assert_called_once()
    app.install_btn.focus_set.assert_called_once()


@pytest.mark.parametrize("state", ["installing", "install_done"])
def test_install_does_nothing_while_running_or_once_done(monkeypatch, state):
    monkeypatch.setattr(gui.threading, "Thread",
                        lambda *args, **kwargs: pytest.fail("started another install"))
    app = window(**{state: True})
    app._start_install()
    app.install_btn.configure.assert_not_called()


def test_quitting_during_pip_ends_pip_and_leaves_nothing_running(monkeypatch):
    """A confirmed quit left pip installing with no window, and pip's timer,
    not a daemon, kept setup's process alive for up to 10 minutes."""
    import time
    pip = FakePip(["Collecting numpy"], hang=True)
    monkeypatch.setattr(gui.subprocess, "Popen", pip)
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *args, **kwargs: True)
    timers = []
    real_timer = threading.Timer

    def spy(*args, **kwargs):
        timers.append(real_timer(*args, **kwargs))
        return timers[-1]

    monkeypatch.setattr(gui.threading, "Timer", spy)
    app = window(installing=True)
    app._pulse = lambda label: None
    worker = threading.Thread(target=app._install_packages, args=("python",), daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 5
        while not timers and time.monotonic() < deadline:
            time.sleep(0.01)
        app._close()
        app.root.destroy.assert_called_once()
        assert pip.killed.wait(2), "pip kept running after setup quit"
    finally:
        pip.kill()
        worker.join(5)
    assert not worker.is_alive()
    assert timers[0].daemon, "the timer would keep setup's process alive"
    assert not timers[0].is_alive()
