"""Stages/play_render_chime.py, which the panel starts once a render is on the
timeline when Render chime is on.

Nothing here plays a sound. Windows' winsound and the Mac's afplay are stood
in for, so the tests run the same on any machine and never reach a device.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from Stages import play_render_chime as chime

# The sample agreed for 0.5.2.
CHIME_SHA256 = "51729ac6771eb6b6e78532ca754aeaea3533eed057cd77791a2d2dff302a99a4"


@pytest.fixture
def spaced(tmp_path) -> Path:
    """The chime in a folder like Resolve's, spaces and an apostrophe included."""
    folder = tmp_path / "Application Support" / "Ali's Upflow2x" / "Stages"
    folder.mkdir(parents=True)
    return Path(shutil.copy2(chime.CHIME, folder / chime.CHIME.name))


@pytest.fixture
def winsound(monkeypatch):
    calls = []
    fake = SimpleNamespace(SND_FILENAME=0x20000, SND_NODEFAULT=0x2, SND_ASYNC=0x1,
                           PlaySound=lambda sound, flags: calls.append((sound, flags)))
    monkeypatch.setitem(sys.modules, "winsound", fake)
    fake.calls = calls
    return fake


@pytest.fixture
def run(monkeypatch):
    calls = []

    def fake(args, **kwargs):
        calls.append((args, kwargs))
        if fake.error:
            raise fake.error
        return subprocess.CompletedProcess(args, fake.returncode)

    fake.returncode, fake.error = 0, None
    fake.calls = calls
    monkeypatch.setattr(chime.subprocess, "run", fake)
    return fake


def test_the_chime_is_the_agreed_sample_beside_its_player():
    assert chime.CHIME == Path(chime.__file__).resolve().parent / "render-complete.wav"
    assert hashlib.sha256(chime.CHIME.read_bytes()).hexdigest() == CHIME_SHA256
    with wave.open(str(chime.CHIME), "rb") as sample:
        assert (sample.getnchannels(), sample.getframerate(), sample.getcomptype()) == (1, 48000, "NONE")
        assert sample.getnframes() / sample.getframerate() == pytest.approx(0.59, abs=0.005)


def test_windows_plays_the_file_and_waits_with_no_fallback_beep(spaced, winsound, run):
    assert chime.play(spaced, "win32") is True
    [(sound, flags)] = winsound.calls
    assert sound == str(spaced)
    assert flags & winsound.SND_FILENAME and flags & winsound.SND_NODEFAULT
    assert not flags & winsound.SND_ASYNC
    assert not run.calls


def test_a_mac_runs_afplay_with_the_path_as_one_argument(spaced, run):
    assert chime.play(spaced, "darwin") is True
    [(args, kwargs)] = run.calls
    assert args == ["/usr/bin/afplay", str(spaced)]
    assert not kwargs.get("shell")
    assert kwargs["timeout"] == chime.TIMEOUT
    assert kwargs["stdin"] == subprocess.DEVNULL


def windows_cannot_play(winsound, run):
    def refuse(sound, flags):
        raise RuntimeError("Failed to play sound")
    winsound.PlaySound = refuse
    return "win32"


def mac_has_no_afplay(winsound, run):
    run.error = FileNotFoundError(chime.AFPLAY)
    return "darwin"


def afplay_fails(winsound, run):
    run.returncode = 1
    return "darwin"


def afplay_hangs(winsound, run):
    run.error = subprocess.TimeoutExpired(chime.AFPLAY, chime.TIMEOUT)
    return "darwin"


def another_system(winsound, run):
    return "linux"


@pytest.mark.parametrize("trouble", [windows_cannot_play, mac_has_no_afplay, afplay_fails,
                                     afplay_hangs, another_system])
def test_a_chime_that_cannot_play_ends_quietly(spaced, winsound, run, trouble):
    assert chime.play(spaced, trouble(winsound, run)) is False


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_a_missing_chime_plays_nothing(tmp_path, winsound, run, platform):
    assert chime.play(tmp_path / "render-complete.wav", platform) is False
    assert not winsound.calls and not run.calls


def test_the_panel_starts_it_with_no_arguments_and_nothing_waits_on_it(monkeypatch):
    played = []
    monkeypatch.setattr(chime, "play", lambda: played.append(True) or False)
    assert chime.main() == 0
    assert played == [True]
