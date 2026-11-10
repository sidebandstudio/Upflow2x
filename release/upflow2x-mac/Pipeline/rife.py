"""The in-between frames, drawn by a learned network.

Warping pixels along optical flow doubles lines and leaves two faded copies of
a character that moves a long way between two drawings. RIFE is a network
trained to predict the in-between directly, and on the same shots it draws
one solid figure.

This is Practical-RIFE 4.25 by Zhewei Huang (MIT licence,
https://github.com/hzwer/Practical-RIFE), as exported to ONNX by vs-mlrt, run
with ONNX Runtime. Neither ships with the plugin. The first time in-betweens
are drawn, ONNX Runtime is installed with pip and the model is downloaded, and
the model is only used once it matches its published SHA-256.
"""

from __future__ import annotations

import hashlib
import importlib
import os
import subprocess
import sys
import tempfile
import urllib.request
from collections import deque
from concurrent.futures import Future
from pathlib import Path
from typing import Callable, Deque, List, Optional

import cv2
import numpy as np

from Pipeline.interpolate import frames_are_a_cut, frames_are_unrelated
from Pipeline.render import SourceError

# Pinned to one commit of the mirror, so the file behind the URL cannot change.
MODEL_URL = ("https://huggingface.co/notaneimu/onnx-image-models/resolve/"
             "f7bf1c91e94ef516900f68456528fa781e2e7174/rife_v4.25_v2.onnx")
MODEL_SHA256 = "65c57a5e4abb17ad67faf35054291ac53affab0506d509d2e66698f6ecd75584"
MODEL_BYTES = 22748049
MODEL_PATH = Path(__file__).resolve().parent.parent / "models" / "rife_v4.25.onnx"

# The network halves the frame several times, so it works on sizes that are a
# multiple of this. The frame is padded by repeating its edge, then cropped.
_PAD = 64

# Asked for a frame close to a drawing, the network hands back the drawing
# itself: on Hero's walk, every t up to 0.1 came back as the first drawing
# exactly and every t from 0.9 as the second, and the motion stayed uneven to
# about 0.2 and from 0.8. Slowed three times, that held each drawing for about
# six extra frames, a pause at every drawing. Between 0.25 and 0.75 it is even,
# so it is only ever asked for those. A frame nearer a drawing is drawn between
# the drawing and the halfway frame, and so on until it falls inside.
_EVEN_FROM = 0.25
# A millionth of a gap: closer to a drawing than any real frame can be.
_ROUNDING = 1e-6

# The network draws an in-between by moving each drawing's pixels and mixing
# the two. These are the inner values it does that with: how far each pixel
# comes from in each drawing, and how much of each drawing it takes. Applied
# to the drawings at 2x, they draw the in-between at 2x, so the upscaler only
# has to draw the drawings. vs-mlrt's export keeps them inside the model, so
# they are added to its outputs when it loads.
_MOTION = ("/Add_7_output_0", "/Sigmoid_output_0")

ProgressFn = Callable[[str, float], None]


class AIUnavailable(SourceError):
    """The network cannot run here. The message says why and what to do."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _packages() -> List[str]:
    """Which ONNX Runtime to install, best first.

    On Windows the DirectML build runs the model on any DirectX 12 GPU, AMD
    and Intel included: on a Radeon, 0.13 s an in-between at 2048x858 against
    1.4 s on the CPU. Where it will not install, the plain build uses the CPU.
    """
    if sys.platform.startswith("win"):
        return ["onnxruntime-directml", "onnxruntime"]
    return ["onnxruntime"]


# How often a pip run that is still going says so. The panel takes a run that
# has not written its status for three minutes for a dead one, and pip can
# take longer than that on a slow connection.
HEARTBEAT_SECONDS = 15.0


def _pip(cmd: List[str], progress: Optional[ProgressFn]) -> subprocess.CompletedProcess:
    """Run pip, writing the status again every HEARTBEAT_SECONDS until it ends."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    while True:
        try:
            out, err = proc.communicate(timeout=HEARTBEAT_SECONDS)
            return subprocess.CompletedProcess(cmd, proc.returncode, out, err)
        except subprocess.TimeoutExpired:
            if progress:
                try:
                    progress("Installing AI runtime", 0.0)
                except BaseException:
                    # Stopped. Cut short, pip could leave this Python half
                    # installed, so it finishes before the run ends.
                    proc.communicate()
                    raise


def ensure_runtime(progress: Optional[ProgressFn] = None):
    """Import ONNX Runtime, installing it into this Python the first time."""
    try:
        return importlib.import_module("onnxruntime")
    except ImportError:
        pass
    if progress:
        progress("Installing AI runtime", 0.0)
    why = ""
    for package in _packages():
        cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", package]
        done = _pip(cmd, progress)
        if done.returncode != 0:
            lines = [x for x in (done.stderr or done.stdout or "").splitlines() if x.strip()]
            why = lines[-1].strip() if lines else f"pip exited with {done.returncode}"
            continue
        importlib.invalidate_caches()
        try:
            return importlib.import_module("onnxruntime")
        except ImportError as exc:
            # The DirectML build can install and still not load, missing a
            # DLL. It is removed, so the next build does not share its files.
            why = f"{package} installed but will not load: {exc}"
            for name in [m for m in sys.modules if m.split(".")[0] == "onnxruntime"]:
                del sys.modules[name]
            _pip([sys.executable, "-m", "pip", "uninstall", "-y", package], progress)
    raise AIUnavailable(
        f"Could not install ONNX Runtime for the in-betweens ({why}). This is Python "
        f"{sys.version_info[0]}.{sys.version_info[1]}, and it has builds for 3.10 to 3.14, "
        f"or up to 3.13 on an Intel Mac. To install it by hand, run: "
        f"\"{sys.executable}\" -m pip install onnxruntime")


def ensure_model(progress: Optional[ProgressFn] = None, path: Path = MODEL_PATH,
                 url: str = MODEL_URL, sha256: str = MODEL_SHA256) -> Path:
    """The model file, downloaded and checked the first time it is needed.

    Two runs can both find it missing and download at once, so each writes its
    own temporary file. Whichever finishes second finds a good model in place,
    and Windows will not replace a file the other run has open, so it keeps
    that one.
    """
    if path.exists() and _sha256(path) == sha256:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".",
                                    suffix=".part")
    part = Path(name)
    try:
        with os.fdopen(handle, "wb") as f:
            try:
                with urllib.request.urlopen(url, timeout=60) as response:
                    total = int(response.headers.get("Content-Length") or MODEL_BYTES)
                    got = 0
                    for chunk in iter(lambda: response.read(1 << 20), b""):
                        f.write(chunk)
                        got += len(chunk)
                        if progress:
                            progress("Downloading AI model", min(1.0, got / float(total)))
            except OSError as exc:
                raise AIUnavailable(f"Could not download the AI model ({exc}). Check the "
                                    f"connection and try again.") from exc
        if _sha256(part) != sha256:
            raise AIUnavailable("The downloaded AI model did not match its checksum, so it "
                                "was not used. Try again.")
        try:
            os.replace(part, path)
        except OSError:
            if not (path.exists() and _sha256(path) == sha256):
                raise
        return path
    finally:
        part.unlink(missing_ok=True)


def _ready(frame) -> np.ndarray:
    """A frame, waiting for it if it is still being drawn."""
    return frame.result() if isinstance(frame, Future) else frame


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        low, n = n & 0x7F, n >> 7
        out.append(low | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _read_varint(data: bytes, i: int):
    n = shift = 0
    while True:
        b = data[i]
        i += 1
        n |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            return n, i


def _with_outputs(model: bytes, names) -> bytes:
    """An ONNX model with inner values `names` added to its outputs.

    The file is a protobuf ModelProto, and a field written again is added to a
    repeated one, so each name goes on the end of the graph's outputs without
    the onnx package. Its type is left for ONNX Runtime to work out.
    """
    i = 0
    while i < len(model):
        start = i
        key, i = _read_varint(model, i)
        field, wire = key >> 3, key & 7
        if wire == 0:
            _, i = _read_varint(model, i)
        elif wire == 1:
            i += 8
        elif wire == 5:
            i += 4
        elif wire == 2:
            n, j = _read_varint(model, i)
            if field == 7:  # ModelProto.graph
                graph = model[j:j + n]
                for name in names:
                    info = b"\x0a" + _varint(len(name)) + name.encode()  # ValueInfoProto.name
                    graph += b"\x62" + _varint(len(info)) + info         # GraphProto.output
                return model[:start] + b"\x3a" + _varint(len(graph)) + graph + model[j + n:]
            i = j + n
        else:
            break
    raise AIUnavailable("The AI model file is not one Upflow2x can read. Delete it and "
                        "render again to download it fresh.")


# The pixel positions of a frame, for each size drawn at.
_GRIDS: dict = {}


def _warp2x(big0: np.ndarray, big1: np.ndarray, flow: np.ndarray,
            mask: np.ndarray) -> np.ndarray:
    """The in-between at 2x, from the drawings at 2x and the network's motion.

    It moves and mixes the pixels as the network does, with the motion
    stretched to the larger size. On Hero's walk this came within 46 dB of
    upscaling the network's own frame, near drawings too, where an in-between
    is drawn from a halfway frame and was moved twice.
    """
    h, w = big0.shape[:2]
    if (h, w) not in _GRIDS:
        x, y = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
        _GRIDS[(h, w)] = np.dstack([x, y])
    grid = _GRIDS[(h, w)]
    moved = []
    for frame, dx, dy in ((big0, flow[0], flow[1]), (big1, flow[2], flow[3])):
        part = cv2.resize(cv2.merge([dx, dy]), (w, h), interpolation=cv2.INTER_LINEAR)
        moved.append(cv2.remap(frame, cv2.scaleAdd(part, 2.0, grid), None, cv2.INTER_LINEAR,
                               borderMode=cv2.BORDER_REPLICATE))
    weight = cv2.resize(mask, (w, h), interpolation=cv2.INTER_LINEAR)
    return cv2.blendLinear(moved[0], moved[1], weight, 1.0 - weight)


class Rife:
    """One loaded model, reused for every pair of drawings in a render."""

    # The pair being drawn and the halfway frames drawn for it, by where they
    # sit between its drawings. A render asks for a pair's in-betweens a few
    # at a time, so they are kept until the next pair.
    _pair = None
    _known = None
    # The last two frames given to the network and the input made from them.
    # The next in-between of the same two only needs its time changed.
    _input = None

    def __init__(self, progress: Optional[ProgressFn] = None):
        ort = ensure_runtime(progress)
        model = ensure_model(progress)
        self._ort, self._model = ort, _with_outputs(Path(model).read_bytes(), _MOTION)
        # The last few frames given to the network, as it takes them. A pair's
        # drawings and halfway frames come back again and again.
        self._seen: Deque[tuple] = deque(maxlen=4)
        # DirectML runs it on any Windows GPU, AMD included, when the
        # onnxruntime-directml package is the one installed.
        self.on_gpu = "DmlExecutionProvider" in ort.get_available_providers()
        try:
            self.session = self._session()
            # ONNX Runtime can catch a DirectML start-up failure itself and
            # hand back a CPU session, one without the core left for Resolve.
            if self.on_gpu and "DmlExecutionProvider" not in self.session.get_providers():
                raise RuntimeError("DirectML did not start")
        except Exception as exc:  # onnxruntime raises its own exception types
            if not self.on_gpu:
                raise AIUnavailable(f"The AI model would not load ({exc}).") from exc
            # A GPU without DirectX 12 lists DirectML but cannot start it.
            self.on_gpu = False
            try:
                self.session = self._session()
            except Exception as exc2:
                raise AIUnavailable(f"The AI model would not load ({exc2}).") from exc2
        self.input = self.session.get_inputs()[0].name

    def _session(self):
        ort = self._ort
        options = ort.SessionOptions()
        if self.on_gpu:
            options.enable_mem_pattern = False
            options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            providers = ["DmlExecutionProvider", "CPUExecutionProvider"]
        else:
            # One core left over, so Resolve stays responsive while it works.
            options.intra_op_num_threads = max(1, (os.cpu_count() or 2) - 1)
            providers = ["CPUExecutionProvider"]
        return ort.InferenceSession(self._model, options, providers=providers)

    def _infer(self, x: np.ndarray, names: Optional[List[str]] = None) -> list:
        try:
            return self.session.run(names, {self.input: x})
        except Exception:  # onnxruntime raises its own exception types
            if not self.on_gpu:
                raise
            # Resolve holds much of the GPU's memory, and a big frame can need
            # more than is left. The CPU is slower but has room.
            self.on_gpu = False
            self.session = self._session()
            return self.session.run(names, {self.input: x})

    def _planes(self, frame: np.ndarray, ph: int, pw: int) -> np.ndarray:
        """`frame` as the network takes it: padded RGB planes from 0 to 1."""
        for given, planes in self._seen:
            if given is frame:
                return planes
        padded = cv2.copyMakeBorder(frame, 0, ph, 0, pw, cv2.BORDER_REPLICATE)
        planes = np.empty((3,) + padded.shape[:2], np.float32)
        for c in range(3):
            # From OpenCV's BGR from 0 to 255.
            cv2.multiply(cv2.extractChannel(padded, 2 - c), 1.0 / 255.0, dst=planes[c],
                         dtype=cv2.CV_32F)
        self._seen.append((frame, planes))
        return planes

    def _run(self, a: np.ndarray, b: np.ndarray, t: float, frame: bool = True):
        """The network's frame at t, and the motion it drew it with: for each
        pixel, how far it comes from in a and in b (4 x h x w), and how much
        of a it takes (h x w). Without `frame`, only the motion, which spares
        copying the frame back from the GPU."""
        h, w = a.shape[:2]
        ph, pw = -h % _PAD, -w % _PAD
        if self._input is None or self._input[0] is not a or self._input[1] is not b:
            x = np.empty((1, 7, h + ph, w + pw), np.float32)
            x[0, :3], x[0, 3:6] = self._planes(a, ph, pw), self._planes(b, ph, pw)
            self._input = (a, b, x)
        x = self._input[2]
        x[0, 6] = t
        y = self._infer(x, None if frame else list(_MOTION))
        # A frame of another size would be written as it came, and a video
        # writer drops it without a word, so the render would come out short.
        expected = [(1, c, h + ph, w + pw) for c in ((3, 4, 1) if frame else (4, 1))]
        shapes = [getattr(part, "shape", None) for part in y]
        if shapes != expected or not all(np.isfinite(part).all() for part in y):
            raise AIUnavailable(f"The AI model returned a broken frame (shape "
                                f"{shapes[0] if shapes else '?'}, expected {expected[0]}), so "
                                f"the render was stopped.")
        motion = (y[-2][0, :, :h, :w], y[-1][0, 0, :h, :w])
        if not frame:
            return None, motion
        # Rounded and held to 0..255 as it goes back to 8 bits.
        return cv2.merge([cv2.multiply(y[0][0, c, :h, :w], 255.0, dtype=cv2.CV_8U)
                          for c in (2, 1, 0)]), motion

    def interpolate_pair(self, a: np.ndarray, b: np.ndarray, times: List[float],
                         unrelated: Optional[bool] = None,
                         big: Optional[tuple] = None, pool=None) -> list:
        """In-betweens for `times` (each in 0..1) between drawings a and b.

        t == 0 returns `a` untouched and t == 1 returns `b` untouched, so real
        drawings are never resampled. Across a cut the first drawing is held
        until the next one starts, since the network would otherwise morph one
        shot into the other. `unrelated` is the renderer's verdict on whether
        the pair is two shots, made with the pairs around it in view, or False
        where the editor's markers already said. Without it the pair is judged
        on its own.

        With `big`, a and b upscaled 2x, every frame comes back at 2x: the
        drawings as given, and each in-between drawn from them with the
        network's motion. Upscaling the in-betweens one by one instead took
        most of a render's time. With `pool` too, those in-betweens come back
        as Futures drawn there, so the network starts on the next meanwhile.
        """
        if not times:
            return []
        ends = big or (a, b)
        if np.array_equal(a, b):
            return [ends[0].copy() for _ in times]
        if unrelated is None:
            unrelated = frames_are_a_cut(a, b) or frames_are_unrelated(a, b)
        if unrelated:
            return [(ends[0].copy() if t < 1.0 else ends[1].copy()) for t in times]
        out: List[np.ndarray] = []
        for t in times:
            t = float(min(1.0, max(0.0, t)))
            # This close to a drawing is rounding in the time map, not a frame.
            if t <= _ROUNDING:
                out.append(ends[0].copy())
            elif t >= 1.0 - _ROUNDING:
                out.append(ends[1].copy())
            else:
                out.append(self._between(a, b, t, big, pool))
        return out

    def _step(self, lo: tuple, hi: tuple, t: float, pool=None, small: bool = True) -> tuple:
        """The frame at t between two (frame, 2x frame) pairs, as another.
        Without `small`, the 1x frame is left out where there is a 2x one."""
        frame, (flow, mask) = self._run(lo[0], hi[0], t, small or lo[1] is None)
        if lo[1] is None:
            return frame, None
        if pool is None:
            return frame, _warp2x(lo[1], hi[1], flow, mask)
        # Either end may be a halfway frame still being drawn. It went into
        # the pool first, so it is drawn or being drawn by the time this runs.
        big0, big1 = lo[1], hi[1]
        return frame, pool.submit(lambda: _warp2x(_ready(big0), _ready(big1), flow, mask))

    def _between(self, a: np.ndarray, b: np.ndarray, t: float,
                 big: Optional[tuple] = None, pool=None):
        """The frame at t between a and b, asking the network only for 0.25 to 0.75."""
        ends = big or (None, None)
        pair = (a, b, ends[0])
        if self._pair is None or any(x is not y for x, y in zip(self._pair, pair)):
            self._pair, self._known = pair, {0.0: (a, ends[0]), 1.0: (b, ends[1])}
        known = self._known
        which = 0 if big is None else 1
        lo, hi = 0.0, 1.0
        while True:
            local = (t - lo) / (hi - lo)
            # Every t strictly inside lands here: each halving doubles its
            # distance from the nearer end. A gap of 5000 frames slowed three
            # times needs 12 halvings and keeps 23 halfway frames.
            if _EVEN_FROM <= local <= 1.0 - _EVEN_FROM:
                # Only a halfway frame is ever given back to the network.
                return self._step(known[lo], known[hi], local, pool, small=big is None)[which]
            if hi - lo < 2.0 ** -30:
                # Only rounding gets here, where both ends are one picture.
                return _ready((known[lo] if local < 0.5 else known[hi])[which]).copy()
            mid = (lo + hi) / 2.0
            if mid not in known:
                known[mid] = self._step(known[lo], known[hi], 0.5, pool)
            lo, hi = (lo, mid) if local < 0.5 else (mid, hi)
