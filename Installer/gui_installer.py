"""
Upflow2x GUI Installer

Run it with `python Installer/gui_installer.py`, or compile it with
Installer/build_installer.py. The compiled app installs its bundled plugin
files and a private Python into the user's application data folder.
That works on both Windows (official embeddable package) and macOS
(python-build-standalone).
"""

from __future__ import annotations

import collections
import io
import locale
import os
import platform
import re
import shutil
import signal
import ssl
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

# The config format lives with the command-line installer so the two cannot
# drift. This runs standalone and from a PyInstaller bundle, where the repo
# root is not on the path, so fall back to importing the sibling module.
try:
    from Installer.install_upflow2x import (
        REQUIREMENTS, _resolve_scripts_dir, _resolve_scripts_root, remove_old_install,
        write_config)
except ImportError:  # pragma: no cover - depends on how this was launched
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from install_upflow2x import (
        REQUIREMENTS, _resolve_scripts_dir, _resolve_scripts_root, remove_old_install,
        write_config)

# ---------------------------------------------------------------------------
# Python download sources
# ---------------------------------------------------------------------------

PYTHON_VERSION = "3.11.9"

# Windows: official embeddable zip from python.org
PYTHON_WIN_EMBED_URL = (
    f"https://www.python.org/ftp/python/{PYTHON_VERSION}/"
    f"python-{PYTHON_VERSION}-embed-amd64.zip"
)

# macOS / Linux: portable builds from python-build-standalone
_PBS_TAG = "20240814"
_PBS_BASE = f"https://github.com/indygreg/python-build-standalone/releases/download/{_PBS_TAG}"
PYTHON_MAC_ARM64_URL = f"{_PBS_BASE}/cpython-{PYTHON_VERSION}+{_PBS_TAG}-aarch64-apple-darwin-install_only.tar.gz"
PYTHON_MAC_X86_URL = f"{_PBS_BASE}/cpython-{PYTHON_VERSION}+{_PBS_TAG}-x86_64-apple-darwin-install_only.tar.gz"
PYTHON_LINUX_URL = f"{_PBS_BASE}/cpython-{PYTHON_VERSION}+{_PBS_TAG}-x86_64-unknown-linux-gnu-install_only.tar.gz"

GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"

# The Mac app is frozen from python.org's Python, whose OpenSSL looks for
# certificates in a folder only that Python's own "Install Certificates" step
# fills. Without them every download failed with CERTIFICATE_VERIFY_FAILED.
# Every Mac ships this file, and the Python downloaded above trusts it too.
MAC_CA_FILE = "/private/etc/ssl/cert.pem"

DEFAULT_UPDATE_URL = (
    "https://raw.githubusercontent.com/"
    "sidebandstudio/Upflow2x/main/update/latest.json"
)

# ---------------------------------------------------------------------------
# UI colors
# ---------------------------------------------------------------------------

BG = "#0b0b0f"
BG_CARD = "#121216"
FG = "#e2e2ea"
FG_DIM = "#6e6e82"
ACCENT = "#7c6fef"
BTN_BG = "#16161e"
SUCCESS = "#4ade80"
ERROR = "#ef5350"
BORDER = "#1e1e26"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# On Windows a windowed app that runs python or pip gets a console window
# flashing up for each run unless it asks for none. The flag is Windows only.
_NO_WINDOW = ({"creationflags": subprocess.CREATE_NO_WINDOW}
              if sys.platform.startswith("win") else {})

# pip builds a package it has no wheel for in processes of its own, a build
# backend and a compiler. Ending pip alone left those running, and any that
# held pip's output kept setup's read of it waiting. So each child setup
# starts goes in a job object of its own on Windows, or a session of its own
# elsewhere, and is ended with everything it started.
if os.name == "nt":
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class _JobLimits(ctypes.Structure):
        """JOBOBJECT_EXTENDED_LIMIT_INFORMATION. Setup sets only flags."""
        _fields_ = [("time_limits", ctypes.c_int64 * 2), ("flags", wintypes.DWORD),
                    ("working_set", ctypes.c_size_t * 2), ("process_limit", wintypes.DWORD),
                    ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                    ("scheduling", wintypes.DWORD), ("io", ctypes.c_uint64 * 6),
                    ("memory", ctypes.c_size_t * 4)]

    # A kernel32 of its own, so these argument types change nothing elsewhere.
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
    _kernel32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int,
                                                  wintypes.LPVOID, wintypes.DWORD)
    _kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    _kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _kernel32.PeekNamedPipe.argtypes = (wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                                        wintypes.LPVOID, wintypes.LPDWORD, wintypes.LPVOID)
    # A child starts suspended until it is in its job, so it can start
    # nothing outside it. NtResumeProcess resumes it whole. It is missing
    # from Windows' published API but has been in ntdll since XP, and psutil
    # resumes processes with it.
    _ntdll = ctypes.WinDLL("ntdll")
    _ntdll.NtResumeProcess.argtypes = (wintypes.HANDLE,)
    _ntdll.NtResumeProcess.restype = ctypes.c_long
    _CREATE_SUSPENDED = 0x4


class _Uncontained(OSError):
    """Windows refused the job object a child would run in, so it was ended
    before it ran. It says nothing about the program the child was."""


def _start(args: list[str], **kwargs) -> tuple[subprocess.Popen, int | None]:
    """Start a child that _end can stop with every process it starts. Returns
    it and, on Windows, its job object. Where Windows refuses the job, the
    child is ended before it has run, and _Uncontained says so."""
    if os.name != "nt":
        return subprocess.Popen(args, start_new_session=True, **kwargs, **_NO_WINDOW), None
    child = subprocess.Popen(args, **kwargs,
                             creationflags=_NO_WINDOW["creationflags"] | _CREATE_SUSPENDED)
    job = _kernel32.CreateJobObjectW(None, None)
    # Closing the last handle to the job ends whatever is left in it, so
    # setup exiting, however it exits, leaves nothing running.
    limits = _JobLimits(flags=0x2000)  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if (job and _kernel32.SetInformationJobObject(job, 9, ctypes.byref(limits),
                                                  ctypes.sizeof(limits))
            and _kernel32.AssignProcessToJobObject(job, child._handle)
            and _ntdll.NtResumeProcess(child._handle) == 0):
        return child, job
    error = ctypes.get_last_error()
    child.kill()
    child.wait()
    _close_job(job)
    raise _Uncontained("Windows would not let setup keep track of the processes it starts "
                       f"(error {error}).")


def _end(child: subprocess.Popen, job: int | None) -> None:
    """Stop child and what it started: its job on Windows, its process group
    elsewhere. Their copies of its output pipe close with them."""
    if job:
        _kernel32.TerminateJobObject(job, 1)
    else:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except OSError:
            # Its session has ended already, or can't be signalled. End the
            # child itself, as before.
            child.kill()


def _close_job(job: int | None) -> None:
    if job:
        _kernel32.CloseHandle(job)


def _read_ready(fd: int) -> bytes | None:
    """What the pipe at fd holds now, without waiting for more: b"" while it
    holds nothing, None once every writer has closed it. On macOS and Linux fd
    has to be non-blocking."""
    if os.name == "nt":
        ready = wintypes.DWORD()
        if not _kernel32.PeekNamedPipe(msvcrt.get_osfhandle(fd), None, 0, None,
                                       ctypes.byref(ready), None):
            return None
        return os.read(fd, min(ready.value, 65536)) if ready.value else b""
    try:
        return os.read(fd, 65536) or None
    except BlockingIOError:
        return b""


def _find_repo_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "plugin"
    return Path(__file__).resolve().parent.parent


def _install_dir() -> Path:
    """DaVinci Resolve's own folder for this user, beside Fusion and not in
    its Scripts folder, which Resolve would list every file of as a script."""
    # Support/Fusion/Scripts on Windows, Fusion/Scripts on a Mac.
    return _resolve_scripts_root().parent.parent / "Upflow2x"


def _copy_plugin(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        if item.is_dir():
            shutil.copytree(item, destination / item.name, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(item, destination / item.name)


def _read_version(repo_root: Path) -> str:
    vf = repo_root / "VERSION"
    if vf.exists():
        return vf.read_text(encoding="utf-8").strip()
    return "?"


# The checks below run Python through `run`, which setup gives them: like
# subprocess.run with the output captured, but in a child Quit can end, and
# none starts after Quit. A check Windows would not run says nothing of the
# Python, so _Uncontained goes on up rather than mark a working Python broken,
# which would have setup replace it.

def _check_python(path: str, run) -> bool:
    try:
        result = run(
            [path, "-c", "import sys; sys.exit(not ((3, 10) <= sys.version_info[:2] < (3, 15)))"])
        return result.returncode == 0
    except _Uncontained:
        raise
    except Exception:
        return False


def _find_system_python(run) -> str | None:
    # Run as a script, the Python running this window is the one the user
    # chose, such as `py Installer\gui_installer.py` with nothing on PATH.
    if (not getattr(sys, "frozen", False) and sys.executable
            and _check_python(sys.executable, run)):
        return sys.executable
    candidates = (
        ["python", "python3"] if sys.platform.startswith("win")
        else ["python3", "python"]
    )
    for name in candidates:
        path = shutil.which(name)
        if path and _check_python(path, run):
            return path
    return None


def _patch_pth(text: str) -> str:
    """Make the embeddable Python able to run the plugin.

    A ._pth file replaces sys.path outright, so without the plugin folder in
    it `python -m Stages.resolve_smooth` fails with No module named 'Stages'.
    Entries are relative to the ._pth file, which sits in <plugin>/python.
    """
    lines = [("import site" if line.strip() == "#import site" else line)
             for line in text.splitlines()]
    stripped = [line.strip() for line in lines]
    if ".." not in stripped:
        lines.append("..")
    if "import site" not in stripped:
        lines.append("import site")
    return "\n".join(lines) + "\n"


def _embedded_python_exe(repo_root: Path) -> Path:
    if sys.platform.startswith("win"):
        return repo_root / "python" / "python.exe"
    return repo_root / "python" / "bin" / "python3"


def _find_embedded_python(repo_root: Path, run) -> str | None:
    exe = _embedded_python_exe(repo_root)
    # Older installers left the ._pth without the plugin folder. Repair it
    # before reusing this Python, or the panel still cannot import Stages.
    for pth in exe.parent.glob("python*._pth"):
        try:
            pth.write_text(_patch_pth(pth.read_text(encoding="utf-8")), encoding="utf-8")
        except OSError:
            pass
    if exe.exists() and _check_python(str(exe), run):
        result = run([str(exe), "-m", "pip", "--version"])
        if result.returncode == 0:
            return str(exe)
    return None


def _python_version_str(python_path: str, run) -> str:
    try:
        r = run([python_path, "--version"])
        return (r.stdout + r.stderr).strip()
    except _Uncontained:
        raise
    except Exception:
        return python_path


def _get_python_download_url() -> str | None:
    if sys.platform.startswith("win"):
        return PYTHON_WIN_EMBED_URL
    if sys.platform == "darwin":
        arch = platform.machine().lower()
        if arch in ("arm64", "aarch64"):
            return PYTHON_MAC_ARM64_URL
        return PYTHON_MAC_X86_URL
    if sys.platform.startswith("linux"):
        return PYTHON_LINUX_URL
    return None


def _download_with_progress(url: str, callback=None) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Upflow2x-Installer/1.0"})
    context = ssl.create_default_context()
    if sys.platform == "darwin" and os.path.isfile(MAC_CA_FILE):
        context.load_verify_locations(MAC_CA_FILE)
    resp = urllib.request.urlopen(req, timeout=120, context=context)
    total = int(resp.headers.get("Content-Length", 0))
    data = io.BytesIO()
    downloaded = 0
    while True:
        chunk = resp.read(64 * 1024)
        if not chunk:
            break
        data.write(chunk)
        downloaded += len(chunk)
        if callback and total > 0:
            callback(downloaded, total)
    return data.getvalue()


# How long pip may take over the packages before setup gives up on it.
PIP_TIMEOUT = 600

# How long setup goes on reading pip's output after pip has ended. Something
# pip started outside its process group can keep the output open for good.
OUTPUT_GRACE = 2

# A line of pip's output longer than this is cut here, so output with no line
# ends can't fill memory.
LINE_LIMIT = 4096

# How long setup waits, once its window has closed, for the install thread to
# end a child process that was still starting when Quit was pressed.
QUIT_GRACE = 5


class _Quit(BaseException):
    """Setup was quit. Not an Exception, so nothing on the way out catches it,
    and the install thread stops without starting anything more."""


def _pip_step(line: str) -> str | None:
    """What a line of pip's output says it is doing, short enough for the
    label under the bar. None for the lines in between."""
    text = line.strip()
    words = text.split()
    if len(words) > 1 and words[0] == "Collecting":
        name = re.split(r"[<>=!~;\[(]", words[1])[0]
        return f"Getting {name}..."
    if len(words) > 1 and words[0] == "Downloading":
        # A wheel's file name, or its whole URL in older pips, then its size.
        name = words[1].rsplit("/", 1)[-1].split("-")[0]
        size = re.search(r"\s(\(\d+(?:\.\d+)?\s+[A-Za-z]+\))$", text)
        return f"Downloading {name}" + (f" {size[1]}" if size else "") + "..."
    if text.startswith("Installing collected packages:"):
        return f"Installing {text.split(':', 1)[1].strip()}..."
    return None


def _safe_extract(tf: tarfile.TarFile, dest: Path) -> None:
    """Unpack a downloaded archive so no member can land outside dest.

    filter="data" refuses paths and links that lead out of dest, and device
    files. A Python too old to have it gets the same checks here, made one
    member at a time, so a link unpacked earlier is followed when checking
    the members after it.
    """
    if hasattr(tarfile, "data_filter"):
        tf.extractall(dest, filter="data")
        return
    root = os.path.realpath(dest)

    def inside(path: str) -> bool:
        try:
            return os.path.commonpath([root, os.path.realpath(path)]) == root
        except ValueError:  # on another drive
            return False

    for member in tf.getmembers():
        target = os.path.join(root, member.name)
        if member.issym():
            points_to = os.path.join(os.path.dirname(target), member.linkname)
        elif member.islnk():
            points_to = os.path.join(root, member.linkname)
        elif member.isfile() or member.isdir():
            points_to = target
        else:
            raise tarfile.TarError(f"{member.name} is not a file or folder.")
        if not (inside(target) and inside(points_to)):
            raise tarfile.TarError(f"{member.name} would land outside {dest}.")
        tf.extract(member, dest)


# ---------------------------------------------------------------------------
# Installer app
# ---------------------------------------------------------------------------

class InstallerApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Install Upflow2x")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)

        w, h = 520, 580
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

        self.source_root = _find_repo_root()
        self.repo_root = _install_dir() if getattr(sys, "frozen", False) else self.source_root
        self.version = _read_version(self.source_root)
        self.installing = False
        self.install_done = False
        self.python_path: str | None = None
        # Quit sets _quit and ends _child, the process the install thread is
        # waiting on, with everything it started, through its job, _job, on
        # Windows. The job is used and closed only under the lock, and
        # neither thread holds it for more than a moment, so Quit never waits
        # on the install thread.
        self._quit = threading.Event()
        self._child_lock = threading.Lock()
        self._child: subprocess.Popen | None = None
        self._job: int | None = None
        self._worker: threading.Thread | None = None

        self._build_ui()

    # ---- UI ---------------------------------------------------------------

    def _build_ui(self):
        font_family = "Segoe UI" if sys.platform.startswith("win") else "Helvetica Neue"

        tk.Label(
            self.root, text="Upflow2x", font=(font_family, 26, "bold"),
            bg=BG, fg=FG,
        ).pack(pady=(24, 0))

        tk.Label(
            self.root,
            text=f"Upscale and smooth anime in DaVinci Resolve  \u00b7  v{self.version}",
            font=(font_family, 10), bg=BG, fg=FG_DIM,
        ).pack(pady=(2, 18))

        log_outer = tk.Frame(self.root, bg=BORDER, padx=1, pady=1)
        log_outer.pack(padx=28, fill="x")
        log_inner = tk.Frame(log_outer, bg=BG_CARD, padx=14, pady=10)
        log_inner.pack(fill="both", expand=True)

        mono = "Consolas" if sys.platform.startswith("win") else "Menlo"
        self.log_text = tk.Text(
            log_inner, height=13, bg=BG_CARD, fg=FG_DIM,
            font=(mono, 10), wrap="word",
            borderwidth=0, highlightthickness=0,
            insertbackground=BG_CARD, cursor="arrow",
            state="disabled", selectbackground="#2b4a73",
        )
        self.log_text.pack(fill="both", expand=True)
        self.log_text.tag_configure("ok", foreground=SUCCESS)
        self.log_text.tag_configure("err", foreground=ERROR)
        self.log_text.tag_configure("accent", foreground=ACCENT)
        self.log_text.tag_configure("dim", foreground=FG_DIM)

        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "Accent.Horizontal.TProgressbar",
            background=ACCENT, troughcolor=BG_CARD,
            bordercolor=BORDER, lightcolor=ACCENT, darkcolor=ACCENT,
        )

        self.progress = ttk.Progressbar(
            self.root, length=464, mode="determinate",
            style="Accent.Horizontal.TProgressbar",
        )
        self.progress.pack(pady=(14, 0), padx=28)

        self.progress_label = tk.Label(
            self.root, text="", font=(font_family, 8), bg=BG, fg=FG_DIM,
        )
        self.progress_label.pack(pady=(4, 0))

        info_frame = tk.Frame(self.root, bg=BG)
        info_frame.pack(fill="x", padx=32, pady=(8, 0))

        tk.Label(
            info_frame,
            text=f"Plugin folder:  {self._short_path(self.repo_root, 58)}",
            font=(font_family, 8), bg=BG, fg=FG_DIM, anchor="w",
        ).pack(fill="x")

        try:
            dest = str(_resolve_scripts_dir())
        except Exception:
            dest = "(could not detect)"
        tk.Label(
            info_frame,
            text=f"Install to:  {self._short_path(Path(dest), 58)}",
            font=(font_family, 8), bg=BG, fg=FG_DIM, anchor="w",
        ).pack(fill="x", pady=(2, 0))

        btn_frame = tk.Frame(self.root, bg=BG)
        btn_frame.pack(pady=(18, 16))

        self.install_btn = tk.Button(
            btn_frame, text="Install", font=(font_family, 13, "bold"),
            bg=ACCENT, fg="#0b0b0f", activebackground="#9b90f5",
            activeforeground="#0b0b0f", borderwidth=0, relief="flat",
            padx=44, pady=10, cursor="hand2",
            command=self._start_install,
        )
        self.install_btn.pack(side="left", padx=8)

        self.close_btn = tk.Button(
            btn_frame, text="Close", font=(font_family, 11),
            bg=BTN_BG, fg=FG, activebackground="#1e1e28",
            activeforeground=FG, borderwidth=0, relief="flat",
            padx=26, pady=10, cursor="hand2",
            command=self._close,
        )
        self.close_btn.pack(side="left", padx=8)

        # Enter presses the button that has focus, Install to begin with, so
        # it never installs with Close focused. invoke does nothing on a
        # disabled button, as Install is while it runs and once it's done.
        # Escape and the window's own close button go through Close's check.
        for button in (self.install_btn, self.close_btn):
            button.bind("<Return>", lambda event, button=button: button.invoke())
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.bind("<Escape>", lambda event: self._close())
        self.install_btn.focus_set()

        self._log("Upflow2x needs:", "accent")
        for line in REQUIREMENTS:
            self._log(f"• {line}", "dim")
        self._log("\nClick Install. Setup handles Python and the plugin for you.", "dim")
        self._log("Keep your internet connection on until setup finishes.\n", "dim")
        # The list comes first, however many lines it wraps to.
        self.log_text.see("1.0")

    # ---- Logging / progress -----------------------------------------------

    @staticmethod
    def _short_path(path: Path, limit: int = 56) -> str:
        s = str(path)
        if len(s) <= limit:
            return s
        return "..." + s[-(limit - 3):]

    def _log(self, msg: str, tag: str | None = None):
        self.log_text.configure(state="normal")
        if tag:
            self.log_text.insert("end", msg + "\n", tag)
        else:
            self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _set_progress(self, value: float, label: str = ""):
        if str(self.progress.cget("mode")) == "indeterminate":
            self.progress.stop()
            self.progress.configure(mode="determinate")
        self.progress["value"] = value
        self.progress_label.configure(text=label)
        self.root.update_idletasks()

    def _pulse(self, label: str):
        """For a step with no measure of how far it has got, such as pip. The
        bar keeps moving, so setup doesn't look stuck, and the label says what
        it is on. The next _set_progress stops it."""
        if str(self.progress.cget("mode")) != "indeterminate":
            self.progress.configure(mode="indeterminate")
            self.progress.start(15)
        self.progress_label.configure(text=label)

    # ---- Install thread management ----------------------------------------

    def _start_install(self):
        if self.installing or self.install_done:
            return
        self.installing = True
        self.install_btn.configure(state="disabled", bg="#555555", text="Installing...")
        self._worker = threading.Thread(target=self._run_install, daemon=True)
        self._worker.start()

    def _run_install(self):
        try:
            self._do_install()
        except _Quit:
            pass
        except Exception as exc:
            if not self._quit.is_set():
                self.root.after(0, self._log, f"\nFatal error: {exc}", "err")
        finally:
            # After a quit the window is gone, with nobody to tell.
            if not self._quit.is_set():
                self.root.after(0, self._on_finish)

    def _on_finish(self):
        self.installing = False
        if not self.install_done:
            # Each step logs what went wrong. Without this the label stayed on
            # the step that failed, as if it were still running.
            self._set_progress(0, "Setup did not finish.")
            self._log("\nSetup did not finish. Sort out the problem in red above, "
                      "then press Try Again.", "err")
            self.install_btn.configure(state="normal", bg=ACCENT, text="Try Again")

    def _close(self):
        """Close the window, asking first while setup runs. Quitting partway
        can leave an update half copied over the version Resolve loads."""
        if self.installing and not messagebox.askyesno(
                "Quit setup?",
                "Setup is still running. If you quit now, Upflow2x may not open in "
                "Resolve until you run setup again.\n\nQuit anyway?",
                icon="warning", default="no", parent=self.root):
            return
        # A child process has no window, so left running it would go on
        # installing unseen. One still starting is ended by the install
        # thread, which sees _quit once Popen returns.
        with self._child_lock:
            self._quit.set()
            if self._child is not None:
                _end(self._child, self._job)
        self.root.destroy()

    def _check_quit(self):
        """Stop the install thread here if setup has been quit."""
        if self._quit.is_set():
            raise _Quit()

    def _launch(self, args: list[str], **kwargs) -> subprocess.Popen:
        """Start a child process that Quit can end. After a quit none starts,
        and one that was starting as Quit was pressed is ended at once."""
        self._check_quit()
        child, job = _start(args, **kwargs)
        with self._child_lock:
            quit_meanwhile = self._quit.is_set()
            if not quit_meanwhile:
                self._child, self._job = child, job
        if quit_meanwhile:
            _end(child, job)
            _close_job(job)
            raise _Quit()
        return child

    def _stop(self, child: subprocess.Popen):
        """End child with everything it started, unless it has been released."""
        with self._child_lock:
            if self._child is child:
                _end(child, self._job)

    def _release(self, child: subprocess.Popen):
        with self._child_lock:
            if self._child is child:
                # On Windows, closing the job ends anything the child left running.
                _close_job(self._job)
                self._child = self._job = None

    def _run_child(self, args: list[str], timeout: float = 10,
                   **kwargs) -> subprocess.CompletedProcess:
        """subprocess.run with the output captured, for a child Quit can end."""
        child = self._launch(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, **kwargs)
        try:
            stdout, stderr = child.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._stop(child)
            # The child only. What it started outside its process group can
            # keep its output open for good.
            child.wait()
            raise
        finally:
            self._release(child)
        self._check_quit()
        return subprocess.CompletedProcess(args, child.returncode, stdout, stderr)

    # ---- Embedded Python: Windows -----------------------------------------

    def _setup_embedded_python_windows(self) -> str | None:
        python_dir = self.repo_root / "python"

        self.root.after(0, self._log, "  Downloading Python for Windows...", "accent")

        def on_progress(downloaded, total):
            pct = downloaded / total * 100
            mb = downloaded / (1024 * 1024)
            mb_t = total / (1024 * 1024)
            self.root.after(0, self._set_progress, 15 + pct * 0.10,
                            f"Downloading Python... {mb:.1f} / {mb_t:.1f} MB")

        try:
            zip_data = _download_with_progress(PYTHON_WIN_EMBED_URL, on_progress)
        except Exception as exc:
            self.root.after(0, self._log, f"  Download failed: {exc}", "err")
            return None

        self.root.after(0, self._log, "  Extracting...")

        if python_dir.exists():
            shutil.rmtree(python_dir)
        python_dir.mkdir(parents=True)

        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            zf.extractall(python_dir)

        for pth in python_dir.glob("python*._pth"):
            pth.write_text(_patch_pth(pth.read_text(encoding="utf-8")),
                           encoding="utf-8")

        python_exe = str(python_dir / "python.exe")
        if not Path(python_exe).exists():
            self.root.after(0, self._log, "  python.exe not found after extraction.", "err")
            return None

        self.root.after(0, self._log, "  Installing pip...", "dim")
        self.root.after(0, self._pulse, "Installing pip...")

        try:
            pip_data = _download_with_progress(GET_PIP_URL)
        except Exception as exc:
            self.root.after(0, self._log, f"  Failed to download pip: {exc}", "err")
            return None

        get_pip_path = python_dir / "get-pip.py"
        get_pip_path.write_bytes(pip_data)

        try:
            # Started like pip itself, so Quit can end it.
            result = self._run_child(
                [python_exe, str(get_pip_path), "--no-warn-script-location"],
                timeout=120, cwd=str(python_dir),
            )
            if result.returncode != 0:
                self.root.after(0, self._log,
                                f"  pip setup failed: {result.stderr.strip()[:200]}", "err")
                return None
        except Exception as exc:
            self.root.after(0, self._log, f"  pip setup error: {exc}", "err")
            return None

        get_pip_path.unlink(missing_ok=True)
        self.root.after(0, self._log, "  Python ready.", "ok")
        return python_exe

    # ---- Embedded Python: macOS / Linux -----------------------------------

    def _setup_standalone_python(self) -> str | None:
        python_dir = self.repo_root / "python"
        url = _get_python_download_url()

        if not url:
            self.root.after(0, self._log,
                            "  No portable Python available for this platform.", "err")
            return None

        os_label = "macOS" if sys.platform == "darwin" else "Linux"
        self.root.after(0, self._log, f"  Downloading Python for {os_label}...", "accent")

        def on_progress(downloaded, total):
            pct = downloaded / total * 100
            mb = downloaded / (1024 * 1024)
            mb_t = total / (1024 * 1024)
            self.root.after(0, self._set_progress, 15 + pct * 0.10,
                            f"Downloading Python... {mb:.1f} / {mb_t:.1f} MB")

        try:
            tar_data = _download_with_progress(url, on_progress)
        except Exception as exc:
            self.root.after(0, self._log, f"  Download failed: {exc}", "err")
            return None

        self.root.after(0, self._log, "  Extracting (this may take a moment)...")
        self.root.after(0, self._set_progress, 26, "Extracting Python...")

        if python_dir.exists():
            shutil.rmtree(python_dir)

        temp_extract = self.repo_root / "_python_extract_tmp"
        if temp_extract.exists():
            shutil.rmtree(temp_extract)
        temp_extract.mkdir(parents=True)

        try:
            with tarfile.open(fileobj=io.BytesIO(tar_data), mode="r:gz") as tf:
                _safe_extract(tf, temp_extract)
        except Exception as exc:
            self.root.after(0, self._log, f"  Extraction failed: {exc}", "err")
            shutil.rmtree(temp_extract, ignore_errors=True)
            return None

        extracted_python = temp_extract / "python"
        if not extracted_python.exists():
            children = [c for c in temp_extract.iterdir() if c.is_dir()]
            if len(children) == 1:
                extracted_python = children[0]

        shutil.move(str(extracted_python), str(python_dir))
        shutil.rmtree(temp_extract, ignore_errors=True)

        python_exe = str(python_dir / "bin" / "python3")
        if not Path(python_exe).exists():
            alt = python_dir / "bin" / "python"
            if alt.exists():
                python_exe = str(alt)
            else:
                self.root.after(0, self._log, "  python3 not found after extraction.", "err")
                return None

        os.chmod(python_exe, 0o755)

        self.root.after(0, self._set_progress, 30, "Verifying Python...")

        self._check_quit()
        if not _check_python(python_exe, self._run_child):
            self.root.after(0, self._log, "  Extracted Python is not working.", "err")
            return None

        self.root.after(0, self._log, "  Python ready.", "ok")
        return python_exe

    # ---- Unified embedded setup -------------------------------------------

    def _setup_embedded_python(self) -> str | None:
        if sys.platform.startswith("win"):
            return self._setup_embedded_python_windows()
        return self._setup_standalone_python()

    # ---- Package installation ---------------------------------------------

    def _install_packages(self, python_path: str) -> bool:
        req_file = self.source_root / "requirements.txt"
        packages = ["-r", str(req_file)] if req_file.exists() else ["numpy", "opencv-python"]
        # Not quiet, so the label follows pip from package to package. A
        # minute or two at one spot on the bar looked like setup had hung.
        pip_cmd = [python_path, "-m", "pip", "install", *packages,
                   "--no-warn-script-location", "--progress-bar", "off"]
        self.root.after(0, self._pulse, "Installing packages...")
        try:
            # Unbuffered, or pip's lines would arrive in blocks.
            proc = self._launch(pip_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                env={**os.environ, "PYTHONUNBUFFERED": "1"})
        except OSError as exc:
            self.root.after(0, self._log, f"  Could not run pip: {exc}", "err")
            return False

        # Only what has arrived is read, here, so setup never waits on output
        # something pip started keeps open. Once it stops reading it closes
        # its end, and whatever still writes to the pipe is refused.
        deadline = time.monotonic() + PIP_TIMEOUT
        tail = collections.deque(maxlen=50)
        partial = b""
        timed_out = drained = False
        ended = None
        try:
            fd = proc.stdout.fileno()
            if os.name != "nt":
                os.set_blocking(fd, False)
            encoding = locale.getpreferredencoding(False)
            while True:
                chunk = b"" if drained else _read_ready(fd)
                if chunk is None:
                    drained = True
                    received, partial = [partial], b""
                else:
                    received = (partial + chunk).split(b"\n")
                    partial = received.pop()
                    if len(partial) > LINE_LIMIT:
                        received.append(partial)
                        partial = b""
                for raw in received:
                    line = raw[:LINE_LIMIT].decode(encoding, "replace").rstrip()
                    if line:
                        tail.append(line)
                        step = _pip_step(line)
                        if step:
                            self.root.after(0, self._pulse, step)
                if not chunk:
                    time.sleep(0.1)
                if proc.poll() is None:
                    # Only a pip still running at the deadline has timed out.
                    if not timed_out and time.monotonic() > deadline:
                        timed_out = True
                        self._stop(proc)
                    continue
                if drained:
                    break
                if ended is None:
                    ended = time.monotonic()
                    # pip failed, and something still holds its output. What
                    # is left in pip's own group or job is setup's to end, now,
                    # or on macOS and Linux it would run on after setup lets
                    # go, through Try Again. A timeout or Quit has ended it.
                    if proc.returncode != 0 and not (timed_out or self._quit.is_set()):
                        self._stop(proc)
                if time.monotonic() - ended > OUTPUT_GRACE:
                    break
        finally:
            proc.stdout.close()
            self._release(proc)
        self._check_quit()
        # A pip that exited 0 as the deadline came has finished.
        if timed_out and proc.returncode != 0:
            self.root.after(0, self._log, "  Timed out after 10 minutes.", "err")
            if not drained:
                self.root.after(0, self._log, "  Something pip started is still running.",
                                "err")
            return False
        if proc.returncode != 0:
            # pip's ERROR lines come last and say what went wrong. The first
            # 200 characters, shown before, could stop at a retry warning.
            lines = [line for line in tail if line.strip()]
            errors = [line for line in lines if line.startswith("ERROR:")] or lines[-3:]
            self.root.after(0, self._log, "  pip could not install the packages:", "err")
            for line in errors[-3:]:
                self.root.after(0, self._log, f"    {line.strip()[:300]}", "err")
            return False
        return True

    # ---- Main install flow ------------------------------------------------

    def _do_install(self):
        # --- Step 1: Verify plugin files ---
        self.root.after(0, self._log, "Checking plugin files...")
        self.root.after(0, self._set_progress, 5, "Checking files...")

        required = [
            self.source_root / "Installer" / "Eternal2xLauncher.lua",
            self.source_root / "Installer" / "Eternal2x.lua",
            self.source_root / "Stages" / "resolve_smooth.py",
            self.source_root / "Pipeline" / "config.py",
        ]
        missing = [str(p) for p in required if not p.exists()]
        if missing:
            for m in missing:
                self.root.after(0, self._log, f"  Missing: {m}", "err")
            self.root.after(0, self._log,
                            "\nPlugin files are incomplete. Re-download the plugin.", "err")
            return

        self.root.after(0, self._set_progress, 10, "")
        self.root.after(0, self._log, "  All files present.", "ok")

        # --- Step 2: Find or install Python ---
        self.root.after(0, self._log, "\nLooking for Python...")
        self.root.after(0, self._set_progress, 12, "Looking for Python...")

        # Each check runs Python, so none starts once setup has been quit.
        self._check_quit()
        python_path = _find_embedded_python(self.repo_root, self._run_child)
        python_source = "embedded"

        self._check_quit()
        if python_path:
            self.root.after(0, self._log,
                            f"  Using bundled Python: "
                            f"{_python_version_str(python_path, self._run_child)}", "ok")
        elif not getattr(sys, "frozen", False):
            python_path = _find_system_python(self._run_child)
            python_source = "system"

        self._check_quit()
        if python_path and python_source == "system":
            self.root.after(0, self._log,
                            f"  Found: {_python_version_str(python_path, self._run_child)}", "ok")

        if not python_path:
            self.root.after(0, self._log,
                            "  Setting up a private Python for the plugin...", "accent")
            python_path = self._setup_embedded_python()
            python_source = "embedded"
            if not python_path:
                return

        self.root.after(0, self._set_progress, 35, "")

        # --- Step 3: Install packages ---
        self.root.after(0, self._log, "\nInstalling Python packages...")
        self.root.after(0, self._log,
                        "  (numpy, opencv-python \u2014 may take 1-2 minutes)", "dim")
        self.root.after(0, self._set_progress, 40,
                        "Installing packages (this takes a bit)...")

        if not self._install_packages(python_path):
            return

        self.root.after(0, self._set_progress, 75, "")
        self.root.after(0, self._log, "  Packages installed.", "ok")

        if self.source_root != self.repo_root:
            self.root.after(0, self._log, "\nInstalling plugin files...")
            _copy_plugin(self.source_root, self.repo_root)

        # --- Step 4: Copy launcher into Resolve ---
        self.root.after(0, self._log, "\nInstalling plugin into DaVinci Resolve...")
        self.root.after(0, self._set_progress, 80, "Copying plugin files...")

        try:
            dest_dir = _resolve_scripts_dir()
        except Exception as exc:
            self.root.after(0, self._log,
                            f"  Could not find Resolve scripts folder: {exc}", "err")
            return

        dest_dir.mkdir(parents=True, exist_ok=True)
        src_launcher = self.repo_root / "Installer" / "Eternal2xLauncher.lua"
        dest_lua = dest_dir / "Upflow2x.lua"
        shutil.copy2(src_launcher, dest_lua)
        # Only once the new launcher is in, so a failed copy keeps the old.
        remove_old_install()

        self.root.after(0, self._set_progress, 90, "Writing config...")
        self.root.after(0, self._log, f"  Launcher: {dest_lua}", "ok")

        # --- Step 5: Write config ---
        conf_path = write_config(dest_dir, self.repo_root, python_path,
                                 update_url=DEFAULT_UPDATE_URL)
        self.root.after(0, self._log, f"  Config:   {conf_path}", "ok")

        # --- Done ---
        self.root.after(0, self._set_progress, 100, "Done!")
        self.root.after(0, self._log, "")
        self.root.after(0, self._log, "\u2714  Installation complete!", "accent")
        self.root.after(0, self._log, "")
        self.root.after(0, self._log, "Restart DaVinci Resolve, then open:", "ok")
        self.root.after(0, self._log, "  Workspace \u2192 Scripts \u2192 Upflow2x", "ok")

        if python_source == "embedded":
            self.root.after(0, self._log, "")
            self.root.after(0, self._log,
                            "Python is set up for Upflow2x. No other setup is needed.", "dim")

        self.install_done = True
        self.root.after(0, lambda: self.install_btn.configure(
            text="\u2713  Installed", state="disabled", bg=SUCCESS, fg="#fff",
        ))

    def run(self):
        self.root.mainloop()
        # The window is gone. A child that was starting as Quit was pressed
        # is ended by the install thread once Popen returns, so Python waits
        # a little for it before exiting, which would halt that thread.
        if self._worker is not None:
            self._worker.join(timeout=QUIT_GRACE)


def main():
    app = InstallerApp()
    if getattr(sys, "frozen", False) and sys.platform.startswith("win"):
        # External Python must load its own DLLs, not the installer's copies.
        import ctypes
        ctypes.windll.kernel32.SetDllDirectoryW(None)
    app.run()


if __name__ == "__main__":
    main()
