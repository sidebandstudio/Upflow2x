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
    app._step = None

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
    app._step = None
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
    """pip as Popen sees it: its lines through a real OS pipe, then its exit
    code. With hang it runs until killed, with times_out a wait given a
    timeout runs out, with a gate Popen does not return until the gate opens,
    and with held something pip started keeps the pipe's write end, writer,
    open after pip has ended, until held is set. With runs_for it exits by
    itself after that many seconds, and with at_once all its lines are in the
    pipe before setup reads any. exited is the code it is seen to have exited
    with, and with unreaped reaping it gives no return code yet."""

    def __init__(self, lines=(), code=0, hang=False, times_out=False, gate=None, held=None,
                 runs_for=0, at_once=False, unreaped=False):
        self.lines, self.code, self.hang = lines, code, hang
        self.times_out, self.gate, self.held = times_out, gate, held
        self.runs_for, self.at_once, self.unreaped = runs_for, at_once, unreaped
        self.returncode = self.exited = None
        self.killed = threading.Event()
        self.starting = threading.Event()
        self.args = self.kwargs = None
        self.calls = 0
        self.writer = self._stdout = None

    def __call__(self, args, **kwargs):
        self.calls += 1
        self.args, self.kwargs = args, kwargs
        self.starting.set()
        if self.gate is not None:
            assert self.gate.wait(5), "Popen was never let return"
        return self

    @property
    def stdout(self):
        """The pipe's read end, made when setup first reads it, in text mode
        where setup asked Popen for text."""
        if self._stdout is None:
            read_end, self.writer = os.pipe()
            self._stdout = (open(read_end, errors=self.kwargs.get("errors"))
                            if self.kwargs.get("text") else open(read_end, "rb"))
            if self.at_once:
                os.write(self.writer, "".join(line + "\n" for line in self.lines).encode())
                self.lines = ()
            threading.Thread(target=self._write, name="fake pip", daemon=True).start()
        return self._stdout

    def _write(self):
        try:
            try:
                for line in self.lines:
                    os.write(self.writer, (line + "\n").encode())
            except OSError:
                pass  # setup closed its end
            if self.hang:
                assert self.killed.wait(5), "setup never gave up on pip"
            elif self.runs_for:
                self.killed.wait(self.runs_for)
            self.exited = self.code
            if not self.unreaped:
                self.returncode = self.code
            if self.held is not None:
                self.held.wait(10)
        finally:
            os.close(self.writer)

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

    def wait(self, timeout=None):
        self.returncode = self.code
        return self.code


REAL_START, REAL_END = gui._start, gui._end
REAL_EXIT_STATUS = getattr(gui, "_exit_status", None)


@pytest.fixture(autouse=True)
def fakes_have_no_job(monkeypatch):
    """A FakePip has no process to put in a job or a process group, so it
    starts as it is, ends when killed, and has an exit status once it is seen
    to have exited, or has a return code. Real children go the real way."""
    def start(args, **kwargs):
        if isinstance(gui.subprocess.Popen, FakePip):
            return gui.subprocess.Popen(args, **kwargs), None
        return REAL_START(args, **kwargs)

    def end(child, job):
        if isinstance(child, FakePip):
            child.kill()
        else:
            REAL_END(child, job)

    def exit_status(child):
        if isinstance(child, FakePip):
            return child.poll() if child.exited is None else child.exited
        return REAL_EXIT_STATUS(child)

    monkeypatch.setattr(gui, "_start", start)
    monkeypatch.setattr(gui, "_end", end)
    monkeypatch.setattr(gui, "_exit_status", exit_status, raising=False)


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


def showing_steps(app, call, *args):
    """What call returned or raised, run as setup's install thread runs it,
    with the test as the window's thread, showing the step pip is on as
    _show_step does, every 10 ms rather than 100, until call returns."""
    looks = []
    window_after = app.root.after

    def after(delay, callback, *args):
        if callback == app._show_step:
            looks.append(args)
        else:
            window_after(delay, callback, *args)

    app.root.after = after
    app.installing = True
    worker, outcome = in_thread(call, *args)
    app._worker = worker
    app._show_step(worker)
    while worker.is_alive():
        time.sleep(0.01)
        app._show_step(*looks.pop())
    return outcome[0]


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
    pip = FakePip(PIP_OUTPUT, runs_for=0.3)
    monkeypatch.setattr(gui.subprocess, "Popen", pip)
    app = window()
    pulses = []
    app._pulse = pulses.append
    assert showing_steps(app, app._install_packages, "python")
    assert app._step is None
    steps = ["Installing packages...", "Getting numpy...",
             "Downloading numpy (12.9 MB)...", "Getting opencv-python...",
             "Downloading opencv_python (38.8 MB)...",
             "Installing numpy, opencv-python..."]
    # Lines read together show only the last of them, so some may be skipped.
    assert pulses[0] == steps[0] and pulses[-1] == steps[-1]
    assert [step for step in steps if step in pulses] == pulses
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
flags = sys.argv[4:]
if role == "pip":
    # The build writes to pip's output, as a compiler run from a build
    # backend can, so the pipe setup reads stays open while it runs.
    subprocess.Popen([sys.executable, __file__, "build", str(folder), str(lifetime), *flags],
                     stdout=sys.stdout, creationflags=0x08000000 if sys.platform == "win32" else 0)
    print("Collecting numpy", flush=True)
    if "fail" in flags or "succeed" in flags:
        # pip exits once its build is running, and leaves it running.
        end = time.monotonic() + 10
        while not (folder / "build").exists() and time.monotonic() < end:
            time.sleep(0.05)
        sys.exit(1 if "fail" in flags else 0)
elif "apart" in flags:
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
    leaves pip's process group, and with fail or succeed, pip exits 1 or 0
    once its build is running."""
    script = tmp_path / "tree.py"
    script.write_text(TREE, encoding="utf-8")
    real_popen = subprocess.Popen
    started = SimpleNamespace(folders=[], lifetime=20.0, lifetimes=[], apart=False, fail=False,
                              succeed=False)

    def stand_in(args, **kwargs):
        folder = Path(tempfile.mkdtemp(dir=tmp_path))
        n = len(started.folders)
        started.folders.append(folder)
        role = "pip" if "install" in args else "probe"
        lifetime = started.lifetimes[n] if n < len(started.lifetimes) else started.lifetime
        flags = [flag for flag in ("apart", "fail", "succeed") if getattr(started, flag)]
        return real_popen([sys.executable, str(script), role, str(folder), str(lifetime), *flags],
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
    monkeypatch.setattr(gui, "sys", SimpleNamespace(platform="linux"))
    pip = FakePip()
    monkeypatch.setattr(gui.subprocess, "Popen", pip)
    child, job = REAL_START(["python", "-m", "pip"], stdout=subprocess.PIPE)
    assert job is None and child is pip
    assert pip.kwargs == {"start_new_session": True, "stdout": subprocess.PIPE}
    leader = Leader()
    REAL_END(leader, None)
    assert signalled == [(leader.pid, 9)]


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
    showing_steps(app, app._do_install)
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


def writing_is_refused(fd: int) -> bool:
    """Whether writes to the pipe at fd fail, as they do once nothing has its
    read end open. A reader that kept going would take them all, and one that
    stopped while keeping its end open would leave the writer stuck."""
    outcome = []

    def write():
        try:
            for _ in range(64):
                os.write(fd, b"x" * 4096)
            outcome.append("written")
        except OSError:
            outcome.append("refused")

    writer = threading.Thread(target=write, daemon=True)
    writer.start()
    writer.join(2)
    return outcome == ["refused"]


@pytest.mark.parametrize("ending", ["exit 0", "time out", "quit"])
def test_output_left_open_is_let_go_once_setup_stops_reading(monkeypatch, ending):
    """Setup stopped waiting on output something pip started kept open, but a
    thread went on reading it, keeping all of it, for each attempt. Once setup
    has returned, nothing of it reads the pipe or holds its end, so whatever
    keeps writing to it is refused at once, attempt after attempt."""
    monkeypatch.setattr(gui, "OUTPUT_GRACE", 0.3)
    monkeypatch.setattr(gui, "PIP_TIMEOUT", 0.3 if ending == "time out" else 600)
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *args, **kwargs: True)
    before = set(threading.enumerate())
    pips = []
    try:
        for attempt in range(2):
            pip = FakePip(["Collecting numpy"], hang=ending != "exit 0", held=threading.Event())
            pips.append(pip)
            monkeypatch.setattr(gui.subprocess, "Popen", pip)
            app = window(installing=True)
            app._pulse = lambda label: None
            worker, outcome = in_thread(app._install_packages, "python")
            if ending == "quit":
                assert pip.starting.wait(5)
                time.sleep(0.3)
                app._close()
            worker.join(5)
            assert not worker.is_alive()
            expected = {"exit 0": True, "time out": False}.get(ending)
            assert outcome == [expected] if expected is not None else isinstance(outcome[0], gui._Quit)
            assert pip.stdout.closed, "setup kept its end of the pipe open"
            left = [thread for thread in set(threading.enumerate()) - before
                    if thread.is_alive() and thread.name != "fake pip"]
            assert not left, f"attempt {attempt + 1} left {left} reading"
        for pip in pips:
            assert writing_is_refused(pip.writer), "something still takes the old pipe's output"
    finally:
        for pip in pips:
            if not pip.stdout.closed:
                pip.stdout.close()
            pip.held.set()


def test_a_line_with_no_end_is_cut_rather_than_kept_whole(monkeypatch):
    """Output with no line ends would all be kept, as one line."""
    monkeypatch.setattr(gui, "LINE_LIMIT", 16, raising=False)
    monkeypatch.setattr(gui.subprocess, "Popen", FakePip(["ERROR: " + "x" * 5000], code=1))
    app = window()
    app._pulse = lambda label: None
    assert not app._install_packages("python")
    assert [msg for msg, tag in app.logged if tag == "err"][-1] == "    ERROR: xxxxxxxxx"


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


# --------------------------------------------------------------------------
# a check that cannot run, and a pip that failed
# --------------------------------------------------------------------------

REFUSED = "Windows would not let setup keep track of the processes it starts (error 5)."


def test_a_python_setup_cannot_check_is_left_alone_but_a_broken_one_is_replaced(tmp_path):
    """A refused job said nothing about the Python, but the check read it as
    broken, and a frozen setup then replaced it. Only a Python whose check
    ran and failed counts as broken."""
    exe = gui._embedded_python_exe(tmp_path)
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"healthy runtime")

    def refused(args):
        raise gui._Uncontained(REFUSED)

    with pytest.raises(gui._Uncontained):
        gui._find_embedded_python(tmp_path, refused)
    with pytest.raises(gui._Uncontained):
        gui._python_version_str(str(exe), refused)

    def broken(args):
        raise OSError(193, "not a valid Win32 application")

    assert gui._find_embedded_python(tmp_path, broken) is None


@pytest.mark.skipif(os.name != "nt", reason="job objects are Windows only")
def test_a_refused_check_leaves_the_installed_python_and_settings_alone(setup, children,
                                                                        monkeypatch):
    """On a reinstall, Windows refused the check's job. Setup took the
    healthy private Python for broken and deleted it with its packages, then
    failed to set up a new one for the same reason."""
    import ctypes
    app, scripts = setup
    exe = gui._embedded_python_exe(app.repo_root)
    package = exe.parent / "Lib" / "site-packages" / "numpy" / "__init__.py"
    package.parent.mkdir(parents=True)
    exe.write_bytes(b"healthy runtime")
    package.write_text("installed numpy", encoding="utf-8")
    scripts.mkdir(parents=True)
    (scripts / "Eternal2x.conf").write_text("threshold=8\n", encoding="utf-8")
    (scripts / "Upflow2x.lua").write_text("previous launcher", encoding="utf-8")
    monkeypatch.setattr(gui, "_find_embedded_python", REAL_FIND_EMBEDDED_PYTHON)
    del app._setup_embedded_python
    downloads = []
    monkeypatch.setattr(gui, "_download_with_progress",
                        lambda url, callback=None: downloads.append(url) or b"")

    def refuse(job, process):
        ctypes.set_last_error(5)  # ERROR_ACCESS_DENIED
        return 0

    monkeypatch.setattr(gui, "_kernel32",
                        Kernel32(gui._kernel32, AssignProcessToJobObject=refuse))
    logged = []
    app._log = lambda msg, tag=None: logged.append((msg, tag))
    app._on_finish = mock.Mock()
    app._run_install()
    assert not downloads, "setup went on to replace the Python it could not check"
    assert exe.read_bytes() == b"healthy runtime"
    assert package.read_text(encoding="utf-8") == "installed numpy"
    assert (scripts / "Eternal2x.conf").read_text(encoding="utf-8") == "threshold=8\n"
    assert (scripts / "Upflow2x.lua").read_text(encoding="utf-8") == "previous launcher"
    assert not app.install_done
    assert any(REFUSED in msg and tag == "err" for msg, tag in logged), logged
    app._on_finish.assert_called_once()


@pytest.mark.parametrize("code", [1, 0])
def test_a_pip_that_ended_with_its_output_held_is_ended_with_its_group_only_if_it_failed(
        monkeypatch, code):
    """On macOS and Linux setup let go of a failed pip's process group with
    something in it still holding pip's output, which Try Again could not
    stop. A pip that exits 0 is left to finish as before."""
    held = threading.Event()
    pip = FakePip(["Collecting numpy"], code=code, held=held)
    monkeypatch.setattr(gui.subprocess, "Popen", pip)
    monkeypatch.setattr(gui, "OUTPUT_GRACE", 0.3)
    app = window()
    app._pulse = lambda label: None
    worker, outcome = in_thread(app._install_packages, "python")
    try:
        worker.join(5)
        assert not worker.is_alive()
    finally:
        held.set()
    assert outcome == [code == 0]
    assert pip.killed.is_set() is (code != 0)
    assert app._child is None


@pytest.mark.parametrize("code", [0, 1])
def test_how_pip_was_seen_to_exit_decides_before_it_can_be_reaped(monkeypatch, code):
    """A kqueue on macOS showed pip's exit before reaping pip gave an exit
    code. Setup let go of a pip seen to exit 0, with output still to read,
    found no exit code and said pip had failed. A failed pip's group is
    ended once, as the failure is seen, and letting go of pip neither ends
    it again nor waits on it."""
    held = threading.Event()
    pip = FakePip(PIP_OUTPUT, code=code, held=held, unreaped=True)
    monkeypatch.setattr(gui.subprocess, "Popen", pip)
    monkeypatch.setattr(gui, "OUTPUT_GRACE", 0.3)
    ends = []
    monkeypatch.setattr(gui, "_end", lambda child, job: ends.append(child) or child.kill())
    app = window()
    app._pulse = lambda label: None
    worker, outcome = in_thread(app._install_packages, "python")
    try:
        worker.join(5)
        assert not worker.is_alive()
    finally:
        held.set()
    assert outcome == [code == 0]
    assert ends == ([pip] if code else [])
    assert pip.returncode is None, "setup waited to reap pip"
    assert any(tag == "err" for _, tag in app.logged) is bool(code)


@pytest.mark.skipif(os.name == "nt", reason="process groups are POSIX, Windows uses job objects")
@pytest.mark.parametrize("group", ["pip's group", "a group of its own"])
def test_a_failed_pip_leaves_no_build_running_in_its_group(children, monkeypatch, group):
    """pip failed before its deadline with its build still running and
    holding pip's output. Setup let go of pip's process group with the build
    in it, which went on changing packages through Try Again. A build that
    left the group is not setup's to end, and is not waited on."""
    monkeypatch.setattr(gui, "OUTPUT_GRACE", 1)
    children.fail = True
    children.apart = group == "a group of its own"
    app = window()
    app._pulse = lambda label: None
    worker, outcome = in_thread(app._install_packages, sys.executable)
    pip = children.wait_for(0)
    worker.join(8)
    assert not worker.is_alive() and outcome == [False]
    if group == "pip's group":
        assert stopped(pip / "build"), "the build in pip's group went on running"
    else:
        assert beating(pip / "build"), "setup ended a build outside pip's group"


# --------------------------------------------------------------------------
# a burst of progress, and a group named by a reaped process's ID
# --------------------------------------------------------------------------

@pytest.mark.parametrize("pip_runs_for,timeout,finished", [
    (1.0, 0.2, False), (0.05, 0.5, True)], ids=["running at the deadline", "done before it"])
def test_a_burst_of_progress_cannot_put_off_the_deadline(monkeypatch, pip_runs_for, timeout,
                                                         finished):
    """Each line pip printed was shown as it came, each a call into the
    window, and a burst of them ran past the deadline: a pip still running
    then had finished by the time setup looked, and counted as done in time."""
    pip = FakePip(["Collecting x"] * 315, runs_for=pip_runs_for, at_once=True)
    monkeypatch.setattr(gui.subprocess, "Popen", pip)
    monkeypatch.setattr(gui, "PIP_TIMEOUT", timeout)
    app = window()

    def slow_after(delay, callback, *args):
        time.sleep(0.005)  # each call into the window takes a while
        callback(*args)

    app.root.after = slow_after
    pulses = []
    app._pulse = pulses.append
    assert showing_steps(app, app._install_packages, "python") is finished
    assert pip.killed.is_set() is not finished
    assert (("  Timed out after 10 minutes.", "err") in app.logged) is not finished
    assert pulses[-1] == "Getting x..." and len(pulses) < 10


@pytest.mark.parametrize("lifetime,timeout,finished", [(0.8, 0.3, False), (0.1, 5, True)],
                         ids=["running at the deadline", "done before it"])
def test_a_busy_window_cannot_put_off_the_deadline(children, monkeypatch, lifetime, timeout,
                                                   finished):
    """Setup showed the step pip was on with a call into the window, which
    waits until the window's thread has taken it. With the window busy for a
    second, a pip still running at its deadline had finished by the time
    setup looked again, and counted as done in time."""
    monkeypatch.setattr(gui, "PIP_TIMEOUT", timeout)
    children.lifetime = lifetime
    ended = []

    def end(child, job):
        ended.append(time.monotonic())
        REAL_END(child, job)

    monkeypatch.setattr(gui, "_end", end)
    app = window()
    app._pulse = lambda label: None

    def busy(delay, callback, *args):
        if children.folders:  # pip has started
            time.sleep(1.15)
        callback(*args)

    app.root.after = busy
    began = time.monotonic()
    assert app._install_packages(sys.executable) is finished
    if finished:
        assert not ended and time.monotonic() - began < 1.15, "setup waited on the window"
    else:
        assert len(ended) == 1 and ended[0] - began < 1.15, "pip was ended once the window was free"
        assert ("  Timed out after 10 minutes.", "err") in app.logged
    assert app._step is None


def test_the_window_shows_the_latest_step_and_none_once_the_attempt_is_over():
    """The install thread only sets the step pip is on, and the window's own
    thread shows it, so a busy window holds up nothing. Steps set between
    two looks are skipped, and none is shown once the attempt has finished
    or Try Again has started another."""
    looks = []
    app = window()
    app.root.after = lambda delay, callback, *args: looks.append((callback, args))
    app._run_install = lambda: None
    pulses = []
    app._pulse = pulses.append

    def look():
        callback, args = looks.pop(0)
        callback(*args)

    app._start_install()
    app._step = "Getting numpy..."
    app._step = "Getting opencv-python..."
    look()
    look()
    assert pulses == ["Getting opencv-python..."]
    app._step = None  # pip is done
    look()
    app._on_finish()  # it failed
    app._start_install()  # Try Again, before the first attempt's next look
    app._step = "Getting numpy..."
    look()
    look()
    assert pulses == ["Getting opencv-python...", "Getting numpy..."]
    app._on_finish()
    look()
    assert looks == [] and len(pulses) == 2


class Leader:
    """A pip as Popen keeps it on macOS and Linux: the leader of its process
    group, whose ID stays its own until Popen reaps it, which Popen does only
    while holding _waitpid_lock and records in returncode. ended is the code
    it has exited with, still unreaped, if it has."""

    def __init__(self, returncode=None, ended=None):
        self.pid, self.returncode, self.ended = 4242, returncode, ended
        self._waitpid_lock = threading.Lock()

    def poll(self):
        if self.returncode is None and self.ended is not None:
            self.returncode = self.ended
        return self.returncode

    def kill(self):
        pass


@pytest.fixture
def posix(monkeypatch):
    """gui as it runs on macOS and Linux, signalling nothing: what it would
    signal is recorded in signalled."""
    signalled = []
    monkeypatch.setattr(gui, "os", SimpleNamespace(
        name="posix", killpg=lambda group, sig: signalled.append((group, sig))))
    monkeypatch.setattr(gui, "signal", SimpleNamespace(SIGKILL=9))
    return signalled


def test_a_group_is_signalled_only_while_its_leader_is_unreaped(posix):
    """Once Popen has reaped pip, its ID can go to another process, even one
    leading another group, so setup signals nothing by that ID."""
    gui._end(Leader(), None)
    assert posix == [(4242, 9)]
    posix.clear()
    gui._end(Leader(returncode=1), None)
    assert posix == [], "setup signalled the ID of a pip already reaped"


def test_a_quit_while_pip_is_being_reaped_signals_nothing_by_its_old_id(posix):
    """Quit can come as pip is being reaped. Then the ID is only used if pip
    is still unreaped once the reaping has finished."""
    leader = Leader()
    leader._waitpid_lock.acquire()  # Popen is reaping it right now
    ender = threading.Thread(target=gui._end, args=(leader, None), daemon=True)
    ender.start()
    time.sleep(0.2)
    assert ender.is_alive(), "setup signalled while pip was being reaped"
    leader.returncode = 1
    leader._waitpid_lock.release()
    ender.join(2)
    assert not ender.is_alive() and posix == []


def test_setup_lets_go_of_pip_without_signalling_or_waiting_on_it(posix):
    """Whether pip's group is ended is settled while pip is unreaped, from
    its exit status. Letting go of pip then signals nothing, reaps it if it
    has ended, and does not wait on one that hasn't."""
    app = window()
    for leader, reaped in ((Leader(ended=0), 0), (Leader(ended=1), 1), (Leader(), None)):
        app._child, app._job = leader, None
        app._release(leader)
        assert posix == [] and app._child is None
        assert leader.returncode == reaped


def test_on_linux_pips_exit_status_is_read_without_reaping_it(monkeypatch):
    """os.waitid with WNOWAIT reports how pip ended and leaves it unreaped,
    so its ID still names its group when setup decides what to end."""
    calls, state = [], ["running"]
    results = {"running": None, "exited": SimpleNamespace(si_code=1, si_status=3),
               "killed": SimpleNamespace(si_code=2, si_status=9)}
    monkeypatch.setattr(gui, "os", SimpleNamespace(
        name="posix", P_PID=1, WEXITED=4, WNOHANG=1, WNOWAIT=0x1000000, CLD_EXITED=1,
        waitid=lambda idtype, pid, options: calls.append((idtype, pid, options))
        or results[state[0]]))
    monkeypatch.setattr(gui, "sys", SimpleNamespace(platform="linux"))
    leader = Leader()
    assert gui._exit_status(leader) is None
    state[0] = "exited"
    assert gui._exit_status(leader) == 3
    state[0] = "killed"
    assert gui._exit_status(leader) == -9
    assert leader.returncode is None, "setup reaped pip to read its status"
    assert all(pid == 4242 and options & 0x1000000 and options & 1 for _, pid, options in calls)


def test_on_older_macos_pips_exit_status_is_read_without_reaping_it(posix, monkeypatch):
    """Python 3.12 and older have no os.waitid on macOS, so setup calls
    macOS's own waitid, with WNOWAIT. It tells how pip ended, even a pip that
    exited before setup first looked, and leaves pip unreaped, so its ID
    still names its group when setup ends that after a failure. A kqueue
    watch was refused once pip had begun to exit, and then only reaping pip
    told its status."""
    import ctypes
    calls, ended = [], []

    def waitid(idtype, pid, info, options):
        """macOS's waitid: a zombie child's status, left to reap with
        WNOWAIT, and info left as it was while the child runs."""
        calls.append((idtype, pid, options))
        if ended:
            siginfo = info._obj
            siginfo.si_pid, siginfo.si_code, siginfo.si_status = pid, *ended[-1]
        return 0

    monkeypatch.setattr(gui, "_darwin_waitid", waitid, raising=False)
    monkeypatch.setattr(gui, "sys", SimpleNamespace(platform="darwin"))
    leader = Leader()
    assert gui._exit_status(leader) is None
    ended.append((1, 1))  # CLD_EXITED, with exit code 1
    assert gui._exit_status(leader) == 1
    assert leader.returncode is None, "setup reaped pip to read its status"
    gui._end(leader, None)
    assert posix == [(4242, 9)], "the failed pip's group was not ended"
    ended.append((2, 9))  # CLD_KILLED, by SIGKILL
    assert gui._exit_status(leader) == -9
    # P_PID, and WEXITED | WNOHANG | WNOWAIT, as macOS's <sys/wait.h> has them.
    assert calls == [(1, 4242, 0x4 | 0x1 | 0x20)] * 3
    # siginfo_t as macOS's <sys/signal.h> lays it out.
    assert [getattr(gui._SigInfo, name).offset
            for name in ("si_code", "si_pid", "si_status")] == [8, 12, 20]
    assert ctypes.sizeof(gui._SigInfo) == 104


@pytest.mark.skipif(os.name == "nt", reason="reaping is POSIX")
@pytest.mark.parametrize("ending", ["exit 3", "killed"])
def test_a_real_childs_exit_status_is_read_without_reaping_it(ending):
    """With the system's own waitid: os.waitid on Linux and on macOS from
    Python 3.13, macOS's through ctypes before that."""
    code = "import sys; sys.exit(3)" if ending == "exit 3" else "import time; time.sleep(30)"
    child, job = gui._start([sys.executable, "-c", code])
    try:
        if ending == "killed":
            gui._end(child, job)
        deadline = time.monotonic() + 10
        while (status := gui._exit_status(child)) is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert status == (3 if ending == "exit 3" else -9)
        assert child.returncode is None, "reading the status reaped the child"
        assert child.poll() == status, "the child was not left to reap"
    finally:
        gui._end(child, job)
        child.wait()


def test_a_pip_that_succeeded_returns_at_once_with_its_output_held(monkeypatch):
    """A pip that succeeded waited out the time for output left open, as if
    it had failed."""
    held = threading.Event()
    monkeypatch.setattr(gui.subprocess, "Popen", FakePip(PIP_OUTPUT, held=held))
    monkeypatch.setattr(gui, "OUTPUT_GRACE", 5)
    app = window()
    app._pulse = lambda label: None
    began = time.monotonic()
    worker, outcome = in_thread(app._install_packages, "python")
    try:
        worker.join(8)
    finally:
        held.set()
    assert outcome == [True]
    assert time.monotonic() - began < 2, "setup waited on output left open after a success"


def test_a_timed_out_pip_that_will_not_end_is_not_waited_on_for_good(monkeypatch):
    pip = FakePip(["Collecting numpy"], hang=True)
    pip.kill = lambda: None  # ending it does nothing
    monkeypatch.setattr(gui.subprocess, "Popen", pip)
    monkeypatch.setattr(gui, "PIP_TIMEOUT", 0.2)
    monkeypatch.setattr(gui, "OUTPUT_GRACE", 0.3)
    app = window()
    app._pulse = lambda label: None
    worker, outcome = in_thread(app._install_packages, "python")
    try:
        worker.join(4)
        assert not worker.is_alive(), "setup waited for good on a pip that would not end"
    finally:
        pip.killed.set()
    assert outcome == [False]
    assert ("  Timed out after 10 minutes.", "err") in app.logged


def test_a_timed_out_check_that_will_not_end_is_not_waited_on_for_good(monkeypatch):
    check = FakePip(times_out=True)
    check.kill = lambda: None  # ending it does nothing
    waits = []

    def wait(timeout=None):
        waits.append(timeout)
        if timeout is None:
            time.sleep(10)  # it never ends
        raise subprocess.TimeoutExpired("python", timeout)

    check.wait = wait
    monkeypatch.setattr(gui.subprocess, "Popen", check)
    app = window()
    began = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        app._run_child(["python", "--version"], timeout=0.2)
    assert time.monotonic() - began < 5, "setup waited for good on a check that would not end"
    assert waits and None not in waits


@pytest.mark.skipif(os.name == "nt", reason="Windows ends what pip started by closing its job")
def test_a_pip_that_succeeds_returns_at_once_and_leaves_what_it_started(children, monkeypatch):
    """Setup ended a successful pip's group, and so what pip had started, only
    because it had seen pip succeed, and waited out the time for output left
    open first. It now returns once pip has, and leaves that running."""
    monkeypatch.setattr(gui, "OUTPUT_GRACE", 5)
    children.succeed = True
    app = window()
    app._pulse = lambda label: None
    worker, outcome = in_thread(app._install_packages, sys.executable)
    pip = children.wait_for(0)
    began = time.monotonic()
    worker.join(8)
    assert outcome == [True]
    assert time.monotonic() - began < 2, "setup waited on output left open after a success"
    assert beating(pip / "build"), "setup ended what a successful pip started"
