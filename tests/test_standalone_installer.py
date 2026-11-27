"""A downloaded setup must work without Python or adjacent plugin files."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time
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
    monkeypatch.setattr(gui, "_find_embedded_python", lambda root, run: None)
    monkeypatch.setattr(gui, "_find_system_python", lambda run: pytest.fail("used system Python"))
    app = gui.InstallerApp.__new__(gui.InstallerApp)
    app.source_root = gui._find_repo_root()
    app.repo_root = destination
    app.root = SimpleNamespace(after=lambda delay, callback, *args: callback(*args))
    app.install_btn = SimpleNamespace(configure=lambda **kwargs: None)
    app._log = lambda *args: None
    app._set_progress = lambda *args: None
    app.install_done = False
    app._quit, app._child_lock, app._child, app._job, app._worker = (
        threading.Event(), threading.Lock(), None, None, None)

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
                        lambda root, run: str(gui._embedded_python_exe(root)))
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
    monkeypatch.setattr(gui, "_check_python", lambda path, run: True)
    assert gui._find_embedded_python(
        tmp_path, lambda args: subprocess.CompletedProcess(args, 1)) is None


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
    app._quit, app._child_lock, app._child, app._job, app._worker = (
        threading.Event(), threading.Lock(), None, None, None)
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
    ("  Downloading https://example.org/numpy-2.1.0.whl?token=unknown)",
     "Downloading numpy..."),
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
    """pip as Popen sees it: its lines, then its exit code. With hang it runs
    until killed, with times_out a wait given a timeout runs out, with a gate
    Popen does not return until the gate opens, and with held something pip
    started keeps its output open, after pip has ended, until held is set."""

    def __init__(self, lines=(), code=0, hang=False, times_out=False, gate=None, held=None):
        self.lines, self.code, self.hang = lines, code, hang
        self.times_out, self.gate, self.held = times_out, gate, held
        self.returncode = None
        self.killed = threading.Event()
        self.starting = threading.Event()
        self.args = self.kwargs = None
        self.calls = 0

    def __call__(self, args, **kwargs):
        self.calls += 1
        self.args, self.kwargs = args, kwargs
        self.starting.set()
        if self.gate is not None:
            assert self.gate.wait(5), "Popen was never let return"
        self.stdout = self._read()
        return self

    def _read(self):
        yield from (line + "\n" for line in self.lines)
        if self.hang:
            assert self.killed.wait(5), "setup never gave up on pip"
        self.returncode = self.code
        if self.held is not None:
            self.held.wait(10)

    def communicate(self, timeout=None):
        if self.times_out and timeout is not None and not self.killed.is_set():
            raise subprocess.TimeoutExpired(self.args, timeout)
        if self.hang:
            assert self.killed.wait(5), "setup never gave up on pip"
        if self.held is not None:
            self.held.wait(10)
        self.returncode = self.code
        return "".join(line + "\n" for line in self.lines), ""

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed.set()
        # A process that has ended keeps the exit code it ended with.
        if self.returncode is None:
            self.code = -9

    def wait(self):
        self.returncode = self.code
        return self.code


REAL_START, REAL_END = gui._start, gui._end


@pytest.fixture(autouse=True)
def fakes_have_no_job(monkeypatch):
    """A FakePip has no process to put in a job or a process group, so it
    starts as it is and ends when killed. Real children go the real way."""
    def start(args, **kwargs):
        if isinstance(gui.subprocess.Popen, FakePip):
            return gui.subprocess.Popen(args, **kwargs), None
        return REAL_START(args, **kwargs)

    def end(child, job):
        if isinstance(child, FakePip):
            child.kill()
        else:
            REAL_END(child, job)

    monkeypatch.setattr(gui, "_start", start)
    monkeypatch.setattr(gui, "_end", end)


def in_thread(call, *args):
    """Run call in a thread of its own, as setup's install thread runs, and
    keep what it returned or raised."""
    outcome = []

    def run():
        try:
            outcome.append(call(*args))
        except BaseException as exc:  # noqa: B036 - a quit unwinds as BaseException
            outcome.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, outcome


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
    pip = FakePip(["Collecting numpy"], hang=True)
    monkeypatch.setattr(gui.subprocess, "Popen", pip)
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *args, **kwargs: True)
    before = set(threading.enumerate())
    app = window(installing=True)
    app._pulse = lambda label: None
    worker, outcome = in_thread(app._install_packages, "python")
    try:
        assert pip.starting.wait(5)
        time.sleep(0.2)
        app._close()
        app.root.destroy.assert_called_once()
        assert pip.killed.wait(2), "pip kept running after setup quit"
    finally:
        pip.kill()
        worker.join(5)
    assert not worker.is_alive()
    assert isinstance(outcome[0], gui._Quit), "the install thread went on after the quit"
    assert not any(tag == "err" for _, tag in app.logged), "it reported to a closed window"
    lasting = [thread for thread in set(threading.enumerate()) - before
               if not thread.daemon and thread.is_alive()]
    assert not lasting, "a thread would keep setup's process alive"


def test_quit_before_pip_starts_starts_nothing(monkeypatch):
    monkeypatch.setattr(gui.subprocess, "Popen",
                        lambda *args, **kwargs: pytest.fail("pip started after the quit"))
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *args, **kwargs: True)
    app = window(installing=True)
    app._pulse = lambda label: None
    app._close()
    with pytest.raises(gui._Quit):
        app._install_packages("python")


def test_quit_while_pip_is_starting_ends_it_once_started(monkeypatch):
    """The window closed while Popen had not yet returned, so there was no
    handle to end, and pip ran on with no window. Quit must not wait for
    Popen either, or the window would freeze."""
    import time
    gate = threading.Event()
    pip = FakePip(["Collecting numpy"], hang=True, gate=gate)
    monkeypatch.setattr(gui.subprocess, "Popen", pip)
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *args, **kwargs: True)
    app = window(installing=True)
    app._pulse = lambda label: None
    worker, outcome = in_thread(app._install_packages, "python")
    try:
        assert pip.starting.wait(5)
        began = time.monotonic()
        app._close()
        assert time.monotonic() - began < 1, "quit waited for Popen"
        app.root.destroy.assert_called_once()
        gate.set()
        assert pip.killed.wait(2), "pip kept running after setup quit"
    finally:
        gate.set()
        pip.kill()
        worker.join(5)
    assert isinstance(outcome[0], gui._Quit)


def test_quit_during_the_pip_bootstrap_ends_it_and_starts_nothing_more(setup, tmp_path,
                                                                       monkeypatch):
    """get-pip ran through subprocess.run, which Quit could not reach, and the
    install thread went on to the packages and the launcher."""
    import io
    import zipfile
    app, scripts = setup
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("python.exe", b"")
        zf.writestr("python311._pth", "python311.zip\n.\n#import site\n")
    monkeypatch.setattr(gui, "_download_with_progress", lambda url, callback=None:
                        archive.getvalue() if url == gui.PYTHON_WIN_EMBED_URL else b"# get-pip")
    get_pip = FakePip(hang=True)
    monkeypatch.setattr(gui.subprocess, "Popen", get_pip)
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *args, **kwargs: True)
    app._setup_embedded_python = app._setup_embedded_python_windows
    del app._install_packages
    app._pulse = lambda label: None
    app.root.destroy = mock.Mock()
    app._on_finish = mock.Mock()
    app.installing = True
    worker, outcome = in_thread(app._run_install)
    try:
        assert get_pip.starting.wait(5)
        assert get_pip.args[1].endswith("get-pip.py")
        app._close()
        assert get_pip.killed.wait(2), "get-pip kept running after setup quit"
    finally:
        get_pip.kill()
        worker.join(5)
    assert outcome == [None], outcome
    assert get_pip.calls == 1, "pip install started after the quit"
    app._on_finish.assert_not_called()
    assert not (scripts / "Upflow2x.lua").exists()


def test_a_bootstrap_that_runs_too_long_is_ended(tmp_path, monkeypatch):
    """The 120 second limit subprocess.run gave get-pip still holds."""
    import io
    import zipfile
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("python.exe", b"")
    monkeypatch.setattr(gui, "_download_with_progress", lambda url, callback=None:
                        archive.getvalue() if url == gui.PYTHON_WIN_EMBED_URL else b"# get-pip")
    get_pip = FakePip(times_out=True)
    monkeypatch.setattr(gui.subprocess, "Popen", get_pip)
    app = window(repo_root=tmp_path)
    app._pulse = lambda label: None
    assert app._setup_embedded_python_windows() is None
    assert get_pip.killed.is_set()
    assert any("pip setup error" in msg and tag == "err" for msg, tag in app.logged)
    assert app._child is None


def test_a_quit_install_thread_runs_no_more_checks(setup, monkeypatch):
    """Even the quick Python checks start nothing once setup has quit."""
    app, scripts = setup
    monkeypatch.setattr(gui, "_find_embedded_python",
                        lambda root, run: pytest.fail("checked Python after the quit"))
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *args, **kwargs: True)
    app.root.destroy = mock.Mock()
    app._on_finish = mock.Mock()
    app.installing = True
    app._close()
    app._run_install()
    app._on_finish.assert_not_called()


def test_after_the_window_closes_setup_lets_the_install_thread_finish_quitting():
    """A pip that was starting as setup quit is only ended once Popen returns,
    by the install thread, so Python must not exit before it gets the chance."""
    app = window()
    app._worker = mock.Mock()
    app.run()
    app.root.mainloop.assert_called_once()
    app._worker.join.assert_called_once_with(timeout=gui.QUIT_GRACE)


# --------------------------------------------------------------------------
# real child processes
# --------------------------------------------------------------------------

# Owned stand-ins: pip with a build it starts, or a quick check of a Python.
# Each adds to a count in a file of its own every 50 ms until the test's stop
# file appears or its lifetime ends, so none outlives a test by long whatever
# happens, and nothing ever has to be killed by name or by number.
TREE = r'''
import os
import subprocess
import sys
import time
from pathlib import Path

role, folder, lifetime = sys.argv[1], Path(sys.argv[2]), float(sys.argv[3])
if role == "pip":
    # The build writes to pip's output, as a compiler run from a build
    # backend can, so the pipe setup reads stays open while it runs.
    subprocess.Popen([sys.executable, __file__, "build", str(folder), str(lifetime), *sys.argv[4:]],
                     stdout=sys.stdout, creationflags=0x08000000 if sys.platform == "win32" else 0)
    print("Collecting numpy", flush=True)
elif sys.argv[4:] == ["apart"]:
    # A process group of its own, which ending pip's group does not reach.
    os.setpgid(0, 0)
count, end = 0, time.monotonic() + lifetime
while time.monotonic() < end and not (folder.parent / "stop").exists():
    count += 1
    (folder / role).write_text(str(count))
    time.sleep(0.05)
'''

REAL_FIND_EMBEDDED_PYTHON = gui._find_embedded_python


def count(path: Path) -> str | None:
    try:
        return path.read_text()
    except OSError:
        return None


def beating(path: Path, over: float = 0.4) -> bool:
    """Whether the stand-in that writes path is still running."""
    before = count(path)
    time.sleep(over)
    return count(path) != before


def stopped(path: Path, within: float = 3) -> bool:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if not beating(path, 0.3):
            return True
    return False


@pytest.fixture
def children(tmp_path, monkeypatch):
    """Every child setup starts becomes a stand-in from TREE, in a folder of
    its own, started with the options setup gave it: a pip for a pip install,
    and a quick check for anything else. The nth runs for lifetimes[n]
    seconds where that is set, or else for lifetime. With apart, pip's build
    leaves pip's process group."""
    script = tmp_path / "tree.py"
    script.write_text(TREE, encoding="utf-8")
    real_popen = subprocess.Popen
    started = SimpleNamespace(folders=[], lifetime=20.0, lifetimes=[], apart=False)

    def stand_in(args, **kwargs):
        folder = Path(tempfile.mkdtemp(dir=tmp_path))
        n = len(started.folders)
        started.folders.append(folder)
        role = "pip" if "install" in args else "probe"
        lifetime = started.lifetimes[n] if n < len(started.lifetimes) else started.lifetime
        apart = ["apart"] if started.apart else []
        return real_popen([sys.executable, str(script), role, str(folder), str(lifetime), *apart],
                          **kwargs)

    def wait_for(n, role="build", within=10):
        """The nth child's folder, once its role has started counting."""
        deadline = time.monotonic() + within
        while time.monotonic() < deadline:
            if len(started.folders) > n and count(started.folders[n] / role):
                return started.folders[n]
            time.sleep(0.05)
        pytest.fail(f"child {n} never started its {role}")

    started.wait_for = wait_for
    monkeypatch.setattr(gui.subprocess, "Popen", stand_in)
    yield started
    (tmp_path / "stop").write_text("")
    for folder in started.folders:
        for path in folder.iterdir():
            stopped(path, within=1)


def test_off_windows_a_child_leads_a_session_and_its_group_is_ended(monkeypatch):
    """Its process group is then everything it starts. Run on every system,
    with the calls stood in for, since the tests below only reach Windows'
    job objects on Windows."""
    signalled = []
    fake_os = SimpleNamespace(name="posix", killpg=lambda group, sig: signalled.append((group, sig)))
    monkeypatch.setattr(gui, "os", fake_os)
    monkeypatch.setattr(gui, "signal", SimpleNamespace(SIGKILL=9))
    monkeypatch.setattr(gui, "_NO_WINDOW", {})
    pip = FakePip()
    pip.pid = 4242
    monkeypatch.setattr(gui.subprocess, "Popen", pip)
    child, job = REAL_START(["python", "-m", "pip"], stdout=subprocess.PIPE)
    assert job is None
    assert pip.kwargs == {"start_new_session": True, "stdout": subprocess.PIPE}
    REAL_END(child, job)
    assert signalled == [(child.pid, 9)] and not pip.killed.is_set()

    def gone(group, sig):
        raise ProcessLookupError()

    fake_os.killpg = gone
    REAL_END(child, job)
    assert pip.killed.is_set(), "with no group left to signal, the child itself is ended"


def test_quit_ends_pip_with_the_build_it_started(children, monkeypatch):
    """Ending only pip left its build running, holding pip's output open, so
    the install thread also went on waiting for the rest of it."""
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *args, **kwargs: True)
    app = window(installing=True)
    app._pulse = lambda label: None
    worker, outcome = in_thread(app._install_packages, sys.executable)
    pip = children.wait_for(0)
    app._close()
    worker.join(5)
    assert not worker.is_alive(), "the install thread kept waiting on pip's output"
    assert isinstance(outcome[0], gui._Quit)
    assert stopped(pip / "pip") and stopped(pip / "build"), "pip's build kept running"
    assert app._child is None and app._job is None


def test_a_timed_out_pip_is_ended_with_its_build(children, monkeypatch):
    monkeypatch.setattr(gui, "PIP_TIMEOUT", 2.5)
    app = window()
    app._pulse = lambda label: None
    worker, outcome = in_thread(app._install_packages, sys.executable)
    pip = children.wait_for(0)
    worker.join(8)
    assert not worker.is_alive(), "setup kept waiting on the build's copy of pip's output"
    assert outcome == [False]
    assert ("  Timed out after 10 minutes.", "err") in app.logged
    assert stopped(pip / "pip") and stopped(pip / "build"), "pip's build kept running"


def test_a_check_that_runs_too_long_is_ended_with_what_it_started(children):
    """get-pip and the Python checks run this way, with a time limit."""
    app = window()
    began = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        app._run_child([sys.executable, "-m", "pip", "install", "pip"], timeout=2.5)
    assert time.monotonic() - began < 6, "setup waited for the build's copy of the output"
    pip = children.folders[0]
    assert stopped(pip / "pip") and stopped(pip / "build")
    assert app._child is None


@pytest.mark.parametrize("which", [0, 1], ids=["version check", "pip check"])
def test_quit_during_a_python_check_ends_it_and_starts_no_other(setup, children, monkeypatch,
                                                                which):
    """The checks ran where Quit could not reach them, so the one running went
    on, and once it passed, the next one started."""
    app, scripts = setup
    monkeypatch.setattr(gui, "_find_embedded_python", REAL_FIND_EMBEDDED_PYTHON)
    exe = gui._embedded_python_exe(app.repo_root)
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"private Python")
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *args, **kwargs: True)
    app.root.destroy = mock.Mock()
    app._on_finish = mock.Mock()
    app.installing = True
    # The checks before the one quit during pass at once.
    children.lifetimes = [0.2] * which + [4]
    worker, outcome = in_thread(app._run_install)
    check = children.wait_for(which, "probe")
    app._close()
    # Before the install thread is waited on: left alone, the check ends by
    # itself, 4 seconds in.
    assert stopped(check / "probe", within=1.5), "the check went on after the quit"
    worker.join(8)
    assert outcome == [None], outcome
    assert len(children.folders) == which + 1, "another check started after the quit"
    app._on_finish.assert_not_called()
    assert not (scripts / "Upflow2x.lua").exists()


def test_quitting_one_setup_leaves_another_setups_pip_running(children, monkeypatch):
    """Each child is stopped through its own job or group, never by name."""
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *args, **kwargs: True)
    first, second = window(installing=True), window(installing=True)
    first._pulse = second._pulse = lambda label: None
    first_worker, first_outcome = in_thread(first._install_packages, sys.executable)
    first_pip = children.wait_for(0)
    second_worker, second_outcome = in_thread(second._install_packages, sys.executable)
    second_pip = children.wait_for(1)
    first._close()
    first_worker.join(5)
    assert isinstance(first_outcome[0], gui._Quit)
    assert stopped(first_pip / "pip") and stopped(first_pip / "build")
    assert beating(second_pip / "pip") and beating(second_pip / "build")
    second._close()
    second_worker.join(5)
    assert isinstance(second_outcome[0], gui._Quit)
    assert stopped(second_pip / "build")


def test_a_full_install_with_real_children_finishes_and_leaves_nothing(setup, children,
                                                                      monkeypatch):
    """The checks, and a pip whose build ends on its own, as a normal install."""
    app, scripts = setup
    monkeypatch.setattr(gui, "_find_embedded_python", REAL_FIND_EMBEDDED_PYTHON)
    exe = gui._embedded_python_exe(app.repo_root)
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"private Python")
    app._setup_embedded_python = lambda: pytest.fail("downloaded Python again")
    del app._install_packages
    pulses = []
    app._pulse = pulses.append
    children.lifetime = 0.3
    app._do_install()
    assert app.install_done
    assert "Getting numpy..." in pulses
    conf = dict(line.split("=", 1) for line in
                (scripts / "Eternal2x.conf").read_text(encoding="utf-8").splitlines())
    assert Path(conf["python"]) == exe
    assert app._child is None and app._job is None
    pip = next(folder for folder in children.folders if (folder / "pip").exists())
    assert stopped(pip / "build", within=1)


# --------------------------------------------------------------------------
# output that something pip started keeps open
# --------------------------------------------------------------------------

def test_output_held_open_past_the_deadline_cannot_hold_setup(monkeypatch):
    """Something pip started outside its group or job could keep pip's output
    open after pip was ended, and setup read on, past its own deadline."""
    held = threading.Event()
    pip = FakePip(["Collecting numpy"], hang=True, held=held)
    monkeypatch.setattr(gui.subprocess, "Popen", pip)
    monkeypatch.setattr(gui, "PIP_TIMEOUT", 0.3)
    monkeypatch.setattr(gui, "OUTPUT_GRACE", 0.5, raising=False)
    app = window()
    app._pulse = lambda label: None
    worker, outcome = in_thread(app._install_packages, "python")
    try:
        worker.join(5)
        assert not worker.is_alive(), "setup read on past its deadline"
    finally:
        held.set()
    assert outcome == [False] and pip.killed.is_set()
    assert [msg for msg, tag in app.logged if tag == "err"] == [
        "  Timed out after 10 minutes.", "  Something pip started is still running."]


def test_a_pip_that_finished_is_not_held_by_output_left_open(monkeypatch):
    held = threading.Event()
    monkeypatch.setattr(gui.subprocess, "Popen", FakePip(PIP_OUTPUT, held=held))
    monkeypatch.setattr(gui, "OUTPUT_GRACE", 0.5, raising=False)
    app = window()
    app._pulse = lambda label: None
    worker, outcome = in_thread(app._install_packages, "python")
    try:
        worker.join(5)
        assert not worker.is_alive(), "setup waited on output after pip had finished"
    finally:
        held.set()
    assert outcome == [True]
    assert not any(tag == "err" for _, tag in app.logged)


def test_a_check_past_its_time_limit_is_not_held_by_its_output(monkeypatch):
    """After the limit, setup read the check's output to its end, which
    something it started could put off for good."""
    held = threading.Event()
    check = FakePip(times_out=True, held=held)
    monkeypatch.setattr(gui.subprocess, "Popen", check)
    app = window()
    began = time.monotonic()
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            app._run_child(["python", "--version"], timeout=0.2)
    finally:
        held.set()
    assert time.monotonic() - began < 3, "setup read the check's output to its end"
    assert check.killed.is_set() and app._child is None


@pytest.mark.parametrize("when", ["before the deadline", "as setup ends it"])
def test_a_pip_that_finished_as_its_time_ran_out_counts_as_finished(monkeypatch, when):
    """Only a pip still running at the deadline has timed out. One that has
    exited 0, with its output read or waiting to be, has finished."""
    pip = FakePip(PIP_OUTPUT, hang=when == "as setup ends it")
    if when == "before the deadline":
        pip.returncode = 0
    else:
        # It exits 0 by itself in the moment setup ends it.
        pip.kill = pip.killed.set
    monkeypatch.setattr(gui.subprocess, "Popen", pip)
    monkeypatch.setattr(gui, "PIP_TIMEOUT", 0)
    app = window()
    app._pulse = lambda label: None
    assert app._install_packages("python")
    assert not any(tag == "err" for _, tag in app.logged)


@pytest.mark.skipif(os.name == "nt", reason="process groups are POSIX, Windows uses job objects")
def test_a_build_in_a_group_of_its_own_cannot_hold_setup_past_the_deadline(children,
                                                                           monkeypatch):
    """Ending pip's process group doesn't reach it, so setup says so rather
    than wait on the output it keeps open."""
    monkeypatch.setattr(gui, "PIP_TIMEOUT", 2.5)
    children.apart = True
    app = window()
    app._pulse = lambda label: None
    worker, outcome = in_thread(app._install_packages, sys.executable)
    pip = children.wait_for(0)
    worker.join(10)
    assert not worker.is_alive(), "setup waited on output the build kept open"
    assert outcome == [False]
    assert [msg for msg, tag in app.logged if tag == "err"] == [
        "  Timed out after 10 minutes.", "  Something pip started is still running."]
    assert stopped(pip / "pip")


class Kernel32:
    """Windows' kernel32 as setup calls it, with some calls replaced."""

    def __init__(self, real, **replaced):
        self.real = real
        self.__dict__.update(replaced)

    def __getattr__(self, name):
        return getattr(self.real, name)


@pytest.mark.skipif(os.name != "nt", reason="job objects are Windows only")
def test_pip_is_in_its_job_before_it_can_start_a_build(children, monkeypatch):
    """pip ran before it joined its job, so a build it started meanwhile was
    outside the job, outlived a quit and held setup's read of pip's output."""
    real = gui._kernel32

    def slow_job(*args):
        # Time for a pip already running to start its build.
        time.sleep(1.5)
        return real.CreateJobObjectW(*args)

    monkeypatch.setattr(gui, "_kernel32", Kernel32(real, CreateJobObjectW=slow_job))
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *args, **kwargs: True)
    app = window(installing=True)
    app._pulse = lambda label: None
    worker, outcome = in_thread(app._install_packages, sys.executable)
    pip = children.wait_for(0)
    app._close()
    assert stopped(pip / "build"), "the build was outside the job and outlived the quit"
    worker.join(5)
    assert isinstance(outcome[0], gui._Quit)


@pytest.mark.skipif(os.name != "nt", reason="job objects are Windows only")
def test_where_windows_refuses_a_job_nothing_runs(children, monkeypatch):
    """pip ran outside a job, so its build outlived the timeout and held
    setup's read of pip's output. Now setup says why and runs nothing."""
    import ctypes

    def refuse(job, process):
        ctypes.set_last_error(5)  # ERROR_ACCESS_DENIED
        return 0

    monkeypatch.setattr(gui, "_kernel32",
                        Kernel32(gui._kernel32, AssignProcessToJobObject=refuse))
    monkeypatch.setattr(gui, "PIP_TIMEOUT", 2.5)
    app = window()
    app._pulse = lambda label: None
    worker, outcome = in_thread(app._install_packages, sys.executable)
    worker.join(8)
    assert not worker.is_alive(), "setup was held by a build it could not stop"
    assert outcome == [False]
    time.sleep(0.5)
    assert not count(children.folders[0] / "pip"), "pip ran outside a job"
    errors = [msg for msg, tag in app.logged if tag == "err"]
    assert len(errors) == 1 and errors[0].startswith("  Could not run pip: Windows")
