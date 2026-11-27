"""Play the render chime once, then exit.

With Render chime on, the panel starts this in its own process, with no
arguments, once a render is on the timeline. It plays render-complete.wav
from beside this file and waits for it to end. No sound file, no sound
device, no player or a system it doesn't know ends it quietly, since a chime
that can't play must never get in the way of the render.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Optional

CHIME = Path(__file__).resolve().with_name("render-complete.wav")

# Apple's own player. It plays the file and exits, with no window.
AFPLAY = "/usr/bin/afplay"

# The chime lasts well under a second, so a player still going after this is stuck.
TIMEOUT = 10


def play(path: Path = CHIME, platform: Optional[str] = None) -> bool:
    """Play the WAV at path and wait for it to finish. False if it couldn't."""
    platform = platform or sys.platform
    if not path.is_file():
        return False
    try:
        if platform == "win32":
            import winsound
            # Without SND_NODEFAULT, Windows plays its own beep when the file
            # won't play. Without SND_ASYNC, PlaySound returns once it ends.
            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_NODEFAULT)
            return True
        if platform == "darwin":
            # A list of arguments and no shell, so spaces and quotes in the
            # path reach afplay as they are.
            done = subprocess.run([AFPLAY, str(path)], stdin=subprocess.DEVNULL,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  timeout=TIMEOUT, check=False)
            return done.returncode == 0
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return False
    return False


def main() -> int:
    # Nothing waits on this, so a chime that didn't play isn't an error.
    play()
    return 0


if __name__ == "__main__":
    sys.exit(main())
