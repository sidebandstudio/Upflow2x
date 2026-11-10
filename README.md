# Upflow2x

[![CI](https://github.com/sidebandstudio/Upflow2x/actions/workflows/ci.yml/badge.svg)](https://github.com/sidebandstudio/Upflow2x/actions/workflows/ci.yml)

Smooth 2x for hand-drawn animation, inside DaVinci Resolve Studio.

**[Download it for Windows or macOS](https://github.com/sidebandstudio/Upflow2x/releases/latest)**, then follow [Install](#install).

Anime is drawn on 2s or 3s: the artist draws twelve pictures a second, or
eight, and each one is held for two or three frames to fill 24fps. That is why anime looks
stepped when you slow it down, and why running it straight through a frame
interpolator does nothing useful. Most neighbouring frames are identical, so
there is no motion to interpolate between them.

Upflow2x recovers the real drawings first, then rebuilds the shot.

```
source     A A B B C C C D D        9 frames, held on 2s
drawings   A   B   C     D          4 unique drawings
rebuilt    A a B b C C c D D        in-betweens generated, C keeps its longer hold
```

![Six frames of hair blowing in the wind, drawn on 3s. As drawn, each drawing is held for three frames. From Upflow2x, each repeat is replaced by a new in-between.](https://upflow2x.com/assets/demo/frames.jpg)

*Six frames of hair in the wind, drawn on 3s. The [site](https://upflow2x.com)
has the moving version, with a divider to drag between the two.*

The result has the clip's frame rate and three frames for every one of the
clip's, so it plays at a third of the speed with every new frame drawn, ready
to time remap or slow further with optical flow. Every drawing lands on three times the frame
it was on in the source, so the shot keeps its timing, only slower. On a clip
with markers the render ends on the last one, since nothing after it counts.
Without markers, the last drawing has nothing after it to move toward, so it
holds for three times its length.

## What it does

- Finds duplicated frames and works out whether the clip is on 1s, 2s or 3s.
  It counts a change of colour, a slow fade or a slow pan as motion rather
  than a hold.
- Draws the in-between frames that were never drawn with the RIFE network,
  run on your own machine, on the GPU on Windows wherever DirectML loads. Moving characters stay in
  one piece.
- Never falls back to plain optical flow, which doubles lines and smears fast
  characters on drawn animation. Where the network cannot run, the drawings
  are kept as drawn.
- Keeps deliberate held poses still, instead of smearing them into drift.
- Cuts stay hard on a clip with no markers. Blending across a scene change
  would show both shots at once, so where two drawings look like different
  shots it snaps instead. A cut right beside another cut or very fast motion
  can still blend for a frame, so check montages. On a marked clip it checks
  nothing and draws every gap, so mark the last frame before a cut as well.
- Upscales each drawing 2x with Real-ESRGAN's anime model, on the GPU on
  Windows, then draws the in-betweens at 2x from the upscaled drawings. It
  clears compression blocks and keeps thin lines crisp, where Super Scale
  enlarges the blocks. Without a GPU, Resolve's Super Scale does the upscale.

It does a job like Twixtor's, but finds the held drawings first, so it only
generates the frames that were never drawn. It draws them with a network
trained for in-betweens rather than by warping pixels along optical flow,
which is where retimers tear, melt and double lines.

## Requirements

- **DaVinci Resolve Studio** 18 or newer. Since 19.1 the free edition blocks
  script panels like this one, so it cannot open Upflow2x. Without a GPU, the
  2x upscale falls back to Super Scale, which is Studio only too.
- Windows 10 or 11, or macOS 12 or newer.
- A DirectX 12 GPU on Windows draws the in-betweens and the 2x upscale. Without
  one, and on a Mac, the in-betweens run on the CPU and Super Scale upscales.
- An internet connection during setup and the first render, which downloads
  about 50 MB. Setup installs a private Python for Upflow2x automatically.

The installer window lists these before it installs anything, and the (i)
at the top of the panel shows them again.

## Install

### With the installer window

1. Download `Upflow2xInstaller.exe` on Windows, or extract
   `Upflow2xInstaller-mac.zip` and open `Upflow2xInstaller.app` on a Mac.
   Setup isn't signed yet. If Windows says it protected your PC, click
   **More info**, then **Run anyway**. If macOS says it can't check the app,
   open System Settings → Privacy & Security and click **Open Anyway**.
2. Click **Install** and wait for setup to finish. No separate Python download
   or terminal commands are needed.
3. Setup stores the plugin and its private Python in `%LOCALAPPDATA%\Upflow2x`
   on Windows, or `~/Library/Application Support/Upflow2x` on a Mac. The
   downloaded installer can be deleted afterwards.
4. Restart Resolve and open **Workspace → Scripts → Upflow2x**.

Up to v0.4.0 the plugin was called Eternal2x, and those releases list it as
**Workspace → Scripts → Eternal2x**.

Standalone installers ship with v0.5.1 and newer. Older releases and source
ZIPs use the manual install below.

### Manual

Extract the source ZIP somewhere permanent. This method requires Python
3.10 to 3.14 from [python.org](https://www.python.org/downloads/). An Intel Mac
or macOS 13 needs 3.13 or older, and macOS 12 needs 3.12 or older. From the
folder that contains `Installer`, run:

```
python -m pip install numpy opencv-python
python Installer/install_upflow2x.py
```

Use `python3` on macOS. The installer records the Python that runs it, so
install the packages into that same one. Then restart Resolve.

The installer writes to Resolve's Utility scripts folder, which Resolve lists
under Workspace → Scripts on every page:

- **Windows:** `%APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility\`
- **macOS:** `~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/`

Earlier versions used the Comp folder, which Resolve only lists on the Fusion
page. Re-running the installer moves it.

For manual installs, the config records where the extracted folder is, so
re-run the installer if you move it.

## Using it

1. Move the playhead over a clip on your timeline. Resolve does not tell
   scripts which clip is selected, so Upflow2x works on the clip under the
   playhead. The panel follows the playhead, and in a gap it keeps the
   last clip.
2. Open **Workspace → Scripts → Upflow2x**.
3. Click **Analyse**. It reads the clip, tells you what it found, for
   example `1438 frames, 719 unique drawings (719 held, 50%). Animated on 2s.`,
   and puts a marker on the clip at every drawing. A pose held for more than
   twice the clip's pattern is a pause. It gets a second marker just before
   the next drawing, so it stays still and then moves on at the clip's pace.
4. Check the markers. If Analyse marked too many or too few, move **Threshold**
   and analyse again. That replaces only its own markers, never yours, and
   puts back any of its markers you deleted. Then delete a marker to leave its
   frame out, even if it moved, or select the clip and press **M** to mark a
   frame Analyse missed.
5. Click **Render**.

Render keeps every marked frame as drawn and draws every frame between two
markers fresh, so a part you leave unmarked never counts. With Interpolate on,
the render ends on the last marker, so mark the last drawing you want in it. Every marker on the
clip counts, whatever its colour, and the clip's first frame always does.
Markers on the timeline's ruler do not, so beat markers are safe there. You can
skip Analyse and mark the frames yourself. A clip with no markers at all is
rendered from the drawings Upflow2x finds on its own.

![The Upflow2x panel open in DaVinci Resolve Studio, with Hero walk.mp4 on V1 and its render on V2.](https://upflow2x.com/assets/demo/resolve.jpg)

The render runs in the background and the panel shows progress and the time
left, so Resolve stays usable while it works. **Stop** ends it early. When it finishes, the rebuilt
clip is imported and placed on the track above the original, starting on the
same frame. With Interpolate on it runs three times as long, up to the last
marker if the clip has any. It goes on the lowest
track that is empty there, or a new track named Upflow2x. Your other clips are
never covered or moved. If you move the original while it renders, the result
follows it. If another timeline is open when it finishes, or the original was
deleted or re-trimmed, it goes at the end instead. An image sequence whose
frame rate Resolve would not set stays in the media pool, since it would play
at the wrong speed.

Then:

- **Show Original** switches the smoothed clip off so the original shows
  through, and **Show Upflow2x** switches it back. Scrub or play to compare.
  It only works on an upscale with Interpolate off. A slowed render has no
  original to line up with.
- **Show in Folder** opens the render's location.

Renders go in an `Upflow2x` folder beside the project's cache folder, with a
folder for each project. The cache folder is the Cache files location in
Project Settings → Master Settings → Working Folders. With the cache in
`D:\Resolve\CacheClip`, a project called Episode 1 renders to
`D:\Resolve\Upflow2x\Episode 1`. If the project has no cache folder, its
drive is unplugged or read only, or it is in OneDrive, renders go in
Resolve's own folder instead, again a folder for each project:
`%APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Upflow2x` on Windows, or
`~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Upflow2x` on
a Mac. Show in Folder opens it. Up to v0.5.0 each render was saved beside its
clip, and those stay where they are.

A 4K image sequence takes about 6 MB a frame, so a few seconds of footage can
fill a gigabyte. Upflow2x never deletes a render, so delete a project's
folder once its renders are off your timelines.

Only the part of the file the timeline clip uses is analysed and rebuilt. To
reach it, the file is still decoded from its start, once for Analyse and again
for the render, so a short cut late in a long episode shows Seeking for a
while first. The panel shows the range it will use under the clip's name, or
Whole file when Resolve does not report one.

### Settings

They are all on the panel, and it remembers them between sessions. Rest the
pointer on one to see what it does.

| Setting | What it does |
|---|---|
| **Threshold** | How big a change Analyse counts as a new drawing, 1 to 9. Lower catches small moves, like only a mouth or a slow drift. Higher counts only big ones, like the whole shot moving. It does nothing to frames you have marked. |
| **Output** | `Image sequence` is lossless and the most reliable to import. `MP4` is a single compact file but lossy. `MKV` is a lossless single file. All three are 8-bit RGB: transparency and 10-bit colour in the source are not kept. MP4 and MKV need an even width and height. |
| **Upscaling** | Upscales each drawing 2x with Real-ESRGAN's anime video model, on the GPU through DirectML. The in-betweens are then drawn at 2x from the upscaled drawings, so the model runs once a drawing rather than once a frame. The model ships with Upflow2x. Without a GPU, on a Mac for one, Resolve's Super Scale is applied to the imported clip instead. Either way it only shows on a timeline bigger than the clip, such as 3840x2160 for 1080p footage. A 2x render has four times the pixels. As an image sequence, a 4K frame of anime is about 6 MB, four to five times its 1080p size. |
| **Interpolate** | On, the RIFE network draws every missing in-between. On Windows it runs on the GPU through DirectML, and falls back to the CPU, about ten times slower. If its Python already has ONNX Runtime without DirectML, that one is used, on the CPU. Its first run installs ONNX Runtime and downloads the model, about 50 MB in all, so it needs a connection once; without one, every drawing is kept as drawn and the panel says why. On, the render is three times longer, so it takes three times as long to render and an image sequence takes three times the disk. Off skips the in-betweens, so every drawing comes back on its original frame and the render keeps the clip's length. With Upscaling on, that only upscales. |

Settings are remembered between sessions.

### What it will not do

If a clip has no duplicated frames, Analyse says so. Live action and
animation already on 1s have no held drawings to recover. Render still slows
them to a third of the speed, with two new frames drawn between every pair.
With Interpolate off it stops instead, since there is nothing to draw.

It also refuses a clip with a variable frame rate, as phone and screen
recordings often have. Every rebuilt frame lasts the same time, so such a clip
would come out a different length and drift against its audio. Render it to a
constant frame rate first, from Resolve or with
`ffmpeg -i clip.mp4 -r 24 clip_cfr.mp4` at your timeline's rate.

## From the command line

Every button is a thin wrapper around one module, so you can script it:

```
python -m Stages.resolve_smooth --video shot.mov --analyse
python -m Stages.resolve_smooth --video shot.mov --format mp4
python -m Stages.resolve_smooth --video shot.mov --base-hold 3 --no-interpolate
python -m Stages.resolve_smooth --video grainy.mov --grain
```

`--video` skips Resolve entirely and writes the result to `--output-dir`. Without
it, the result goes in an `Upflow2x` folder beside `--cache-dir`, Resolve's cache
folder, in a folder named by `--project`. With no cache folder, or one in
OneDrive, it goes in Resolve's own folder instead,
`%APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Upflow2x` on Windows or
`~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Upflow2x` on a
Mac. The panel passes the project and cache folder itself. Add `--network-upscale` and, where
Real-ESRGAN runs on a GPU, the render is 2x. Without it, or without a GPU, the
2x upscale is Resolve's Super Scale, applied when the panel imports the result,
so a `--video` run stays at source resolution.

Add `--json` and stdout carries a single JSON summary, errors included, while
progress goes to stderr.

## Updates

Upflow2x checks for updates at startup, or on demand with the
**Check for Updates** button. Updates are verified against a SHA-256 checksum
and checked for every file the plugin needs before being applied. If an
update fails partway, the files it replaced are put back. Restart Resolve
afterwards.

## Troubleshooting

| Problem | Fix |
|---|---|
| Resolve says you need Studio | Since 19.1 the free edition blocks script panels. Upflow2x needs Studio. |
| Clicking Upflow2x does nothing, or "Plugin folder not configured" | The plugin folder moved or was deleted after installing, and Workspace → Console says `UI script not found`. Run the installer again. After a manual install, re-run `python Installer/install_upflow2x.py` from where the folder is now. |
| "Could not reach Resolve" | Open the panel from Workspace → Scripts, not from a terminal. |
| "No clip under the playhead" | Move the playhead over the clip, and the panel picks it up. |
| "No duplicate frames. This clip is already on 1s." | The clip has no duplicated frames. It is live action, or already on 1s. |
| "Every frame differs, but the differences look like grain" | Mark the drawings with M, then press **Render**. From the command line, `--grain` raises the threshold above the grain. |
| "...has a variable frame rate" | Render the clip to a constant frame rate first, from Resolve or with ffmpeg, as the message says. |
| Rendering is slow | The time grows with the length of the clip on your timeline, and only the part it uses is rebuilt, so trimming helps. On a Radeon RX 7900 XT, under three seconds of 1080p, upscaled and slowed to 190 frames at 4K, takes about 33 seconds. Without a GPU the in-betweens are drawn on the CPU, about ten times slower, and Super Scale upscales. |
| Progress seems stuck | Press **Refresh Progress**. If Python stopped, the panel says so and shows its last error. If a run was interrupted, press **Reset**. |
| Resolve would not import the render | The render still finished. The panel prints where it is, so drag it into your media pool. Switching Output to `Image sequence` usually avoids this. |
| Upscale skipped | Without a GPU the upscale is Super Scale, which needs Resolve Studio. |
| Something else | Email the panel's status text and the `.upflow2x_last_run.log` file from the plugin folder, `%LOCALAPPDATA%\Upflow2x` on Windows or `~/Library/Application Support/Upflow2x` on a Mac. |

## Development

```
./test.sh
```

800+ tests, no Resolve required. The suite fakes the Resolve scripting API and
runs the Lua panel under an embedded Lua runtime, so buttons, commands and the
full render pipeline are all covered headlessly. See `tests/README.md`.

Tests run on Linux, macOS and Windows against Python 3.9 and 3.12.

To see which scripting API methods a Resolve build offers, turn on
Preferences → System → General → External scripting using → Local, set up
the environment variables from Blackmagic's scripting README, then run
`python -m Stages.resolve_probe` from the plugin folder.

To cut a release, bump `VERSION` and push a matching tag:

```
git tag v0.5.1 && git push origin v0.5.1
```

The release workflow builds the archives, publishes them, checks the published
downloads really resolve, and refreshes `update/latest.json`. The tag is derived
from `VERSION` in one place, so the archives, the tag and the update feed cannot
drift apart.

To build locally without publishing:

```
python -m pip install pyinstaller==6.22.3
python Installer/build_installer.py          # build this platform's standalone setup
python Installer/build_release.py            # build the folders and zips
python Installer/build_release.py --publish  # build, tag, upload, verify
python Installer/build_release.py --verify   # check the live feed is sound
```

Publishing requires both `dist/Upflow2xInstaller.exe` and
`dist/Upflow2xInstaller-mac.zip`. The release workflow builds each on its native
platform before publishing. The Mac build uses a universal Python and produces
one app for Apple Silicon and Intel. Source ZIPs remain the update packages.

`update/latest.json` always describes the release that is live, so only
`--publish` writes it, and only once the uploaded files match the build. A
plain build never touches it. Files already on a release are never replaced.
If you change anything that ships after a release goes out, bump `VERSION` and
cut a new one rather than re-uploading.

Archives are byte reproducible, so rebuilding a tag on any machine gives the
same checksums as the published ones.

## Licence

Required Notice: Copyright 2026 Ali Tleis (https://upflow2x.com)

Upflow2x is under the
[PolyForm Shield License 1.0.0](https://polyformproject.org/licenses/shield/1.0.0),
with the full text and notice in
[`LICENSE.md`](https://github.com/sidebandstudio/Upflow2x/blob/main/LICENSE.md).
You can use it for anything, including paid client work, and you can read,
change and share the code. You can't use it to make a product that competes
with Upflow2x, free ones included. The models and footage under Credits keep
their own licences.

## Credits

The in-betweens are drawn by [Practical-RIFE](https://github.com/hzwer/Practical-RIFE)
4.25 by Zhewei Huang, under the MIT licence, in the ONNX export made by
[vs-mlrt](https://github.com/AmusementClub/vs-mlrt). The model is downloaded
the first time in-betweens are drawn and checked against its SHA-256. It is not
part of the Upflow2x download.

The 2x upscale is drawn by Real-ESRGAN's anime video model, realesr-animevideov3,
by Xintao Wang, under the BSD 3-Clause licence
([Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN)). Its network file in
`Pipeline/networks` is built from the published weights by
`tools/make_upscaler_model.py`, and ships with its licence beside it.

The film on the site, [promo.mp4](https://upflow2x.com/assets/demo/promo.mp4),
uses footage from [Hero](https://studio.blender.org/projects/hero/) by the
Blender Foundation, [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/),
edited, and footsteps, clicks and paper sounds by [Kenney](https://kenney.nl/),
CC0. Its wind is made from noise.

## Questions

Email `hello@sideband.studio`
