"""Drive the real panel (Installer/Eternal2x.lua) under a Lua runtime.

These tests load the actual UI script, click the actual buttons, and inspect
the exact shell command it hands to Python. That is the part that normally
needs Resolve open.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("lupa", reason="lupa provides the Lua runtime")
from lupa import LuaRuntime  # noqa: E402

HARNESS = Path(__file__).resolve().parent / "lua_harness.lua"


@pytest.fixture
def lua():
    return LuaRuntime(unpack_returned_tuples=True)


def _plugin_tree(dest: Path, repo_root: Path) -> Path:
    for name in ("Installer", "Stages", "Pipeline"):
        shutil.copytree(repo_root / name, dest / name)
    (dest / "VERSION").write_text("0.3.0\n")
    return dest


@pytest.fixture
def installed(tmp_path, repo_root):
    """A full install: launcher plus config in Comp, plugin tree elsewhere."""
    plugin_root = _plugin_tree(tmp_path / "Eternal2x", repo_root)
    comp = tmp_path / "Comp"
    comp.mkdir()
    shutil.copy2(plugin_root / "Installer" / "Eternal2xLauncher.lua", comp / "Upflow2x.lua")
    (comp / "Eternal2x.conf").write_text(
        f"repo_root={plugin_root}\n"
        f"python={sys.executable}\n"
        "update_url=https://example.com/latest.json\n"
        "auto_update=false\n",
        encoding="utf-8",
    )
    return comp, plugin_root


@pytest.fixture
def direct(tmp_path, repo_root):
    """The UI script with its config beside it, for exercising the panel itself."""
    root = _plugin_tree(tmp_path / "Eternal2x", repo_root)
    (root / "Installer" / "Eternal2x.conf").write_text(
        f"repo_root={root}\n"
        f"python={sys.executable}\n"
        "update_url=https://example.com/latest.json\n"
        "auto_update=false\n"
    )
    return root


def follow(H):
    """The card reads the clip once the playhead has stayed put for a tick."""
    H.fire("PollTimer", "Timeout")
    H.fire("PollTimer", "Timeout")


def load(lua, script: Path, clip="/media/shot.mov"):
    # Forward slashes: a Windows path interpolated into Lua source turns
    # backslashes into escape sequences, and "D:\a\..." fails to parse.
    # Lua accepts forward slashes on Windows.
    H = lua.eval(f'dofile("{HARNESS.as_posix()}")')
    H.load(str(script), clip)
    return H


def ui_script(root: Path) -> Path:
    return root / "Installer" / "Eternal2x.lua"


# --------------------------------------------------------------------------
# structure
# --------------------------------------------------------------------------

def test_panel_loads(lua, direct):
    assert load(lua, ui_script(direct)).window is not None


def test_every_control_is_present(lua, direct):
    H = load(lua, ui_script(direct))
    ids = set(H.widget_ids().split(","))
    for expected in ("SmoothBtn", "AnalyseBtn", "RefreshStatusBtn",
                     "CancelBtn", "UpdateBtn", "FormatCombo",
                     "UpscaleCB", "InterpCB", "Status",
                     "Progress", "SourceLabel", "AnalysisLabel", "CompareBtn", "FolderBtn"):
        assert expected in ids, f"missing {expected}; have {sorted(ids)}"


def test_every_control_has_a_handler(lua, direct):
    H = load(lua, ui_script(direct))
    handlers = set(H.handler_ids().split(","))
    for expected in ("SmoothBtn.Clicked", "AnalyseBtn.Clicked", "PollTimer.Timeout",
                     "RefreshStatusBtn.Clicked", "CancelBtn.Clicked",
                     "UpdateBtn.Clicked",
                     "UpscaleCB.Clicked",
                     "InterpCB.Clicked", "CompareBtn.Clicked",
                     "FolderBtn.Clicked"):
        assert expected in handlers, f"no handler for {expected}"


def test_the_panel_shows_what_upflow2x_needs(lua, direct):
    from Installer.install_upflow2x import REQUIREMENTS
    H = load(lua, ui_script(direct))
    tip = H.widgets["NeedsInfo"].ToolTip
    assert tip.splitlines() == ["Upflow2x needs:"] + list(REQUIREMENTS)


def test_there_is_no_quality_choice(lua, direct):
    """Pixel tracking doubled lines and smeared fast characters, and no check
    caught every case, so the network draws every in-between."""
    H = load(lua, ui_script(direct))
    assert "QualityCombo" not in H.widget_ids().split(",")
    H.click("SmoothBtn")
    assert "--quality" not in H.last_command()


def test_output_format_choices_match_the_stage(lua, direct):
    from Stages.resolve_smooth import build_parser
    H = load(lua, ui_script(direct))
    assert H.combo_items("FormatCombo").count(",") == 2

    accepted = None
    for action in build_parser()._actions:
        if "--format" in action.option_strings:
            accepted = set(action.choices)
    H.click("AnalyseBtn")
    chosen = H.last_command().split("--format ")[1].split()[0]
    assert chosen in accepted


def test_the_default_output_format_is_lossless(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("AnalyseBtn")
    assert "--format png" in H.last_command()


def test_output_format_selection_reaches_the_command(lua, direct):
    H = load(lua, ui_script(direct))
    H.set_combo("FormatCombo", 1)      # MP4
    H.click("AnalyseBtn")
    assert "--format mp4" in H.last_command()


def test_the_stage_hears_the_project_and_resolves_cache_folder(lua, direct):
    """Renders landed beside each clip, among the recordings. They go beside
    Resolve's cache now, a folder per project."""
    H = load(lua, ui_script(direct))
    H.project_name, H.cache_location = "Show One", "D:/Cache/CacheClip"
    H.click("SmoothBtn")
    cmd = H.last_command()
    quote = '"' if sys.platform.startswith("win") else "'"
    assert f"--project {quote}Show One{quote}" in cmd
    assert f"--cache-dir {quote}D:/Cache/CacheClip{quote}" in cmd
    assert "--output-dir" not in cmd


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows argument quoting")
def test_a_folder_ending_in_a_backslash_keeps_the_next_argument(lua, direct):
    """Quoted as it was, D:\\CacheClip\\ escaped its own closing quote, and
    the status file went into the cache folder's name."""
    import json
    import subprocess

    from Stages.resolve_smooth import build_parser
    H = load(lua, ui_script(direct))
    H.cache_location = "D:\\CacheClip\\"
    H.click("SmoothBtn")
    line = next(x for x in H.last_command().splitlines() if "-m Stages.resolve_smooth" in x)
    line = line.split(" > ")[0].replace(
        "-m Stages.resolve_smooth", '-c "import json, sys; print(json.dumps(sys.argv[1:]))"')
    got = subprocess.run(line, capture_output=True, text=True, check=True)
    args = build_parser().parse_args(json.loads(got.stdout))
    assert args.cache_dir == "D:\\CacheClip\\"
    assert args.status_file.endswith(run_file(direct, "status").name)


def test_an_output_folder_in_the_settings_wins(lua, direct):
    conf = direct / "Installer" / "Eternal2x.conf"
    conf.write_text(conf.read_text() + "output_dir=/renders\n")
    H = load(lua, ui_script(direct))
    H.project_name, H.cache_location = "Show", "D:/Cache/CacheClip"
    H.click("SmoothBtn")
    cmd = H.last_command()
    assert "--output-dir" in cmd and "--cache-dir" not in cmd
    assert "--project" in cmd, "a chosen folder gets a folder per project too"
    assert H.combo_items("FolderCombo") == "Resolve's folder,/renders,Choose a folder..."
    assert H.widgets["FolderCombo"].CurrentIndex == 1


def conf_value(root, key):
    for line in (root / "Installer" / "Eternal2x.conf").read_text().splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1]
    return None


def test_renders_save_to_resolves_folder_until_another_is_chosen(lua, direct):
    H = load(lua, ui_script(direct))
    assert H.combo_items("FolderCombo") == "Resolve's folder,Choose a folder..."
    assert H.widgets["FolderCombo"].CurrentIndex == 0


def test_a_folder_chosen_under_save_to_is_used_and_kept(lua, direct):
    H = load(lua, ui_script(direct))
    H.chosen_dir = "D:/Renders/"
    H.set_combo("FolderCombo", 1)
    assert conf_value(direct, "output_dir") == "D:/Renders"
    assert H.combo_items("FolderCombo") == "Resolve's folder,D:/Renders,Choose a folder..."
    assert H.widgets["FolderCombo"].CurrentIndex == 1
    H.click("SmoothBtn")
    assert "--output-dir" in H.last_command()
    # Opened again, the dialog starts from it, and choosing nothing keeps it.
    H.chosen_dir = None
    H.set_combo("FolderCombo", 2)
    assert H.dir_dialog_start == "D:/Renders"
    assert conf_value(direct, "output_dir") == "D:/Renders"
    assert H.widgets["FolderCombo"].CurrentIndex == 1


@pytest.mark.parametrize("chosen,kept", [("D:\\", "D:\\"), ("D:/", "D:/"), ("/", "/"),
                                         ("D:\\Renders\\", "D:\\Renders")])
def test_a_drive_or_disk_root_can_be_chosen(lua, direct, chosen, kept):
    """D: alone is the folder that drive was last in, and / trimmed is nothing."""
    H = load(lua, ui_script(direct))
    H.chosen_dir = chosen
    H.set_combo("FolderCombo", 1)
    assert conf_value(direct, "output_dir") == kept


def test_an_event_that_changes_nothing_refills_nothing(lua, direct):
    """Resolve may deliver the refill's own index changes after it."""
    H = load(lua, ui_script(direct))
    H.chosen_dir = "D:/Renders/"
    H.set_combo("FolderCombo", 1)
    lua.globals().H = H
    lua.execute("""
        local combo = H.widgets.FolderCombo
        local clear = combo.Clear
        H.clears = 0
        function combo:Clear() H.clears = H.clears + 1 return clear(self) end
    """)
    for _ in range(5):
        H.fire("FolderCombo", "CurrentIndexChanged")
    assert H.clears == 0
    assert conf_value(direct, "output_dir") == "D:/Renders"


def test_cancelling_the_folder_dialog_changes_nothing(lua, direct):
    H = load(lua, ui_script(direct))
    H.chosen_dir = None
    H.set_combo("FolderCombo", 1)
    assert conf_value(direct, "output_dir") == ""
    assert H.widgets["FolderCombo"].CurrentIndex == 0


def test_resolves_folder_can_be_chosen_again(lua, direct):
    conf = direct / "Installer" / "Eternal2x.conf"
    conf.write_text(conf.read_text() + "output_dir=D:/Renders\n")
    H = load(lua, ui_script(direct))
    H.set_combo("FolderCombo", 0)
    assert conf_value(direct, "output_dir") == ""
    assert H.combo_items("FolderCombo") == "Resolve's folder,Choose a folder..."
    H.click("SmoothBtn")
    assert "--output-dir" not in H.last_command()


def test_every_render_plays_at_a_third_of_the_speed(lua, direct):
    """There is no setting for it: every render comes out three times
    longer, with more drawn frames to time remap. An upscale alone has
    nothing new to draw, so it keeps the clip's length."""
    from Stages.resolve_smooth import build_parser
    H = load(lua, ui_script(direct))
    assert "LengthCombo" not in H.widget_ids().split(",")
    H.click("SmoothBtn")
    assert "--slow-motion 3" in H.last_command()
    build_parser().parse_args(["--slow-motion", "3"])

    H.set_checkbox("InterpCB", False)
    H.click("SmoothBtn")
    assert "--slow-motion" not in H.last_command()


def test_everything_is_in_one_window(lua, direct):
    """A second window opened on top of the panel and had to be closed."""
    H = load(lua, ui_script(direct))
    assert list(H.windows.keys()) == ["Eternal2x"]
    for control in ("ThresholdSlider", "FormatCombo", "UpdateBtn", "SubTitle"):
        assert control in H.window.__items, f"{control} is not on the panel"


def test_the_version_is_shown(lua, direct):
    H = load(lua, ui_script(direct))
    assert "0.3.0" in H.widgets["SubTitle"].Text


def test_a_byte_order_mark_in_version_does_not_leak_into_the_title(lua, direct):
    (direct / "VERSION").write_bytes(b"\xef\xbb\xbf0.3.0\n")
    H = load(lua, ui_script(direct))
    assert "﻿" not in H.widgets["SubTitle"].Text
    assert "0.3.0" in H.widgets["SubTitle"].Text


# --------------------------------------------------------------------------
# what shows: the main view, Settings, and how a run is going
# --------------------------------------------------------------------------

MAIN_VIEW = {"Title", "Badge", "SettingsBtn", "SourceLabel", "UpscaleCB", "UpscaleMode",
             "InterpCB", "InterpMode", "SmoothBtn"}
# Refresh Progress is always in Settings, for a timer that stalls.
SETTINGS_VIEW = {"Title", "Badge", "BackBtn", "SourceLabel", "AnalyseBtn", "ThresholdLabel",
                 "ThresholdSlider", "ThresholdValue", "AnalysisLabel", "OutputLabel",
                 "FormatCombo", "FolderLabel", "FolderCombo", "ChimeCB", "SubTitle", "NeedsInfo",
                 "UpdateBtn", "RefreshStatusBtn"}
FEEDBACK = {"ProgressBar", "Progress", "Status", "CompareBtn", "FolderBtn",
            "RefreshStatusBtn", "CancelBtn", "Meta"}


def shown(H):
    """The IDs of the widgets the window shows."""
    return {i for i in H.widget_ids().split(",") if H.widgets[i].Hidden is not True}


def words(H):
    """The words the window shows, buttons included, as a person reads them."""
    out = []
    for i in shown(H):
        text = H.widgets[i].Text
        out += [w for w in str(text or "").split() if any(c.isalnum() for c in w)]
    return out


def test_the_main_view_is_the_clip_two_switches_and_render(lua, direct):
    """It held every control, three headings, info dots, the version and a
    tall empty status box. Idle, it shows what a render needs, in 12 words."""
    H = load(lua, ui_script(direct))
    assert shown(H) == MAIN_VIEW
    assert H.text_of("SourceLabel") == "shot.mov"
    assert len(words(H)) <= 15, words(H)
    geometry = H.window.__attrs.Geometry
    assert (geometry[3], geometry[4]) == (380, 198)


def test_settings_holds_the_rest_and_back_returns_without_starting_anything(lua, direct):
    H = load(lua, ui_script(direct))
    commands = H.command_count()
    H.click("SettingsBtn")
    assert shown(H) == SETTINGS_VIEW
    assert "0.3.0" in H.text_of("SubTitle")
    H.click("BackBtn")
    assert shown(H) == MAIN_VIEW
    assert H.command_count() == commands, "changing page ran something"


def test_a_setting_changed_in_settings_applies_at_once(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("SettingsBtn")
    H.set_slider("ThresholdSlider", 7)
    H.set_combo("FormatCombo", 1)
    H.chosen_dir = "D:/Renders/"
    H.set_combo("FolderCombo", 1)
    assert [conf_value(direct, k) for k in ("threshold", "format", "output_dir")] == [
        "7", "mp4", "D:/Renders"]
    H.click("BackBtn")
    H.click("SmoothBtn")
    cmd = H.last_command()
    assert "--format mp4" in cmd and "--output-dir" in cmd and "--threshold" in cmd


def test_an_analysis_started_in_settings_reports_there_and_after_back(lua, direct):
    import time
    H = load(lua, ui_script(direct))
    H.click("SettingsBtn")
    H.click("AnalyseBtn")
    assert {"ProgressBar", "Progress", "AnalysisLabel", "CancelBtn", "RefreshStatusBtn"} <= shown(H)
    assert (H.text_of("CancelBtn"), H.text_of("AnalysisLabel")) == ("Stop", "Analysing...")
    assert H.status() == "Analysing..." and "Status" not in shown(H), "said twice in Settings"
    H.click("CancelBtn")
    assert "Status" in shown(H) and H.status() == "Press Stop again to end the analysis."
    write_status(direct, fraction=1.0, done=True, ok=True, message="Animated on 2s.",
                 marks="0,2", updated=time.time())
    H.click("RefreshStatusBtn")
    assert "CancelBtn" not in shown(H), "Stop stayed after the run ended"
    H.click("BackBtn")
    assert "Status" in shown(H) and H.status() == "Check the markers, then press Render."


def test_a_render_shows_progress_and_stop_then_what_its_result_offers(lua, direct):
    import time
    H = load(lua, ui_script(direct))
    assert not shown(H) & FEEDBACK
    H.click("SmoothBtn")
    write_status(direct, fraction=0.4, updated=time.time())
    H.click("RefreshStatusBtn")
    assert shown(H) & FEEDBACK == {"ProgressBar", "Progress", "Status", "RefreshStatusBtn",
                                   "CancelBtn"}
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path="/tmp/out.mp4", updated=time.time())
    H.click("RefreshStatusBtn")
    assert shown(H) & FEEDBACK == {"ProgressBar", "Progress", "Status", "CompareBtn",
                                   "FolderBtn", "Meta"}
    assert "set the timeline to 3840x2160" in H.text_of("Meta")


def test_a_failed_run_offers_only_its_log(lua, direct):
    H = load(lua, ui_script(direct))
    die(H, direct)
    assert shown(H) & FEEDBACK == {"Status", "FolderBtn"}
    assert H.text_of("FolderBtn") == "Open Log"


def test_without_a_timer_refresh_progress_always_shows_and_reads_the_clip(lua, direct):
    H = lua.eval(f'dofile("{HARNESS.as_posix()}")')
    H.no_timer = True
    H.under_playhead = False
    H.load(str(ui_script(direct)), "/media/shot.mov")
    assert "RefreshStatusBtn" in shown(H)
    H.under_playhead = True
    H.click("RefreshStatusBtn")
    assert H.text_of("SourceLabel") == "shot.mov"
    assert {"RefreshStatusBtn", "Status"} <= shown(H)
    assert H.status() == "No run in progress."


def test_a_render_waiting_at_startup_offers_refresh_and_its_folder(lua, direct):
    write_status(direct, fraction=1.0, done=True, ok=True, message="Done.",
                 import_path=str(direct / "shot_smooth.mp4"))
    H = load(lua, ui_script(direct))
    assert {"Status", "RefreshStatusBtn", "FolderBtn"} <= shown(H)
    H.click("RefreshStatusBtn")
    assert H.imported_count() == 1
    assert "RefreshStatusBtn" not in shown(H)


def test_a_run_left_going_shows_its_stop_on_either_page_when_reopened(lua, direct):
    import time
    write_status(direct, fraction=0.4, updated=time.time())
    H = load(lua, ui_script(direct))
    assert {"Status", "CancelBtn", "RefreshStatusBtn"} <= shown(H)
    H.click("SettingsBtn")
    assert {"Status", "CancelBtn", "RefreshStatusBtn"} <= shown(H)
    H.click("CancelBtn")
    assert H.status().startswith("Press Stop again to end the run.")


def test_no_clip_is_said_once_and_a_press_still_answers(lua, direct):
    H = lua.eval(f'dofile("{HARNESS.as_posix()}")')
    H.under_playhead = False
    H.load(str(ui_script(direct)), "/media/shot.mov")
    assert "playhead" in H.text_of("SourceLabel") and "Status" not in shown(H)
    H.click("SmoothBtn")
    assert H.command_count() == 0
    assert "Status" in shown(H) and "playhead" in H.status()


def test_a_long_message_gets_room_and_the_window_grows_to_fit(lua, direct):
    """Resolve does not grow a wrapped label to fit, so a finished render's
    message was cut off top and bottom."""
    H = load(lua, ui_script(direct))
    finish_render(H, direct, message="Rendered 44 drawings into 1440 smooth frames. " * 3)
    status = H.widgets["Status"]
    assert status.WordWrap and status.Alignment.AlignTop
    assert status.MinimumSize[2] >= -(-len(status.Text) // 56) * 15
    assert H.size[2] >= 198 + status.MinimumSize[2]


def test_the_progress_line_holds_two_lines_only_while_a_run_goes(lua, direct):
    import time
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    assert H.widgets["Progress"].MinimumSize[2] == 30
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path="/tmp/out.mp4", updated=time.time())
    H.click("RefreshStatusBtn")
    assert "Progress" in shown(H) and H.widgets["Progress"].MinimumSize[2] == 16


def test_progress_ticks_never_resize_the_window(lua, direct):
    import time
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    H.fire("PollTimer", "Timeout")
    before = len(H.resizes)
    for step in range(1, 10):
        write_status(direct, fraction=step / 10, stage="Interpolating and upscaling",
                     updated=time.time())
        H.fire("PollTimer", "Timeout")
    assert len(H.resizes) == before


def test_back_asks_for_the_smaller_page_once_more_on_the_next_tick(lua, direct):
    """Qt keeps a window's size when its contents shrink, and may only lay it
    out after the change, so it is asked once more, then left alone."""
    H = load(lua, ui_script(direct))
    H.click("SettingsBtn")
    settings = H.size[2]
    H.click("BackBtn")
    main = H.size[2]
    assert main < settings
    asked = len(H.resizes)
    H.fire("PollTimer", "Timeout")
    assert len(H.resizes) == asked + 1 and H.size[2] == main
    H.fire("PollTimer", "Timeout")
    assert len(H.resizes) == asked + 1, "it resized on a tick with nothing new"


# --------------------------------------------------------------------------
# a run that needs looking at keeps its way in on screen
# --------------------------------------------------------------------------

def press(H, control):
    """Click a control the way a person can: only while it shows."""
    assert control in shown(H), f"{control} is not on screen to press"
    H.click(control)


def test_a_waiting_render_keeps_refresh_through_other_messages_and_imports_once(lua, direct):
    """Its Refresh Progress showed only while the status said to press it, so
    an update check in Settings took the only way to the render away."""
    write_status(direct, fraction=1.0, done=True, ok=True, message="Done.",
                 import_path=str(direct / "shot_smooth.mp4"))
    H = load(lua, ui_script(direct))
    assert "RefreshStatusBtn" in shown(H)
    press(H, "SettingsBtn")
    press(H, "UpdateBtn")
    assert "Refresh Progress" not in H.status()
    press(H, "BackBtn")
    assert "RefreshStatusBtn" in shown(H), "the waiting render lost its way in"
    press(H, "RefreshStatusBtn")
    assert H.imported_count() == 1
    assert "RefreshStatusBtn" not in shown(H)
    press(H, "SettingsBtn")
    press(H, "RefreshStatusBtn")
    assert H.imported_count() == 1


def test_a_run_that_failed_while_the_panel_was_closed_can_be_looked_up(lua, direct):
    write_status(direct, done=True, ok=False, message="The clip's media is missing.")
    run_file(direct, "log").write_text("Traceback (most recent call last):\nOSError: gone\n")
    run_file(direct, "exit").write_text("2\n")
    H = load(lua, ui_script(direct))
    assert {"Status", "RefreshStatusBtn"} <= shown(H) and "CancelBtn" not in shown(H)
    press(H, "RefreshStatusBtn")
    assert H.status() == "The clip's media is missing."
    assert "FolderBtn" in shown(H) and H.text_of("FolderBtn") == "Open Log"
    assert "RefreshStatusBtn" not in shown(H)


def test_a_run_that_died_while_the_panel_was_closed_can_be_looked_up(lua, direct):
    """Python could not start, so only its exit code and its log are left."""
    run_file(direct, "log").write_text("ModuleNotFoundError: No module named 'cv2'\n")
    run_file(direct, "exit").write_text("1\n")
    H = load(lua, ui_script(direct))
    assert {"Status", "RefreshStatusBtn"} <= shown(H)
    press(H, "RefreshStatusBtn")
    assert "No module named 'cv2'" in H.status()
    assert "FolderBtn" in shown(H) and H.text_of("FolderBtn") == "Open Log"


def test_a_finished_runs_exit_code_left_behind_is_no_error(lua, direct):
    run_file(direct, "exit").write_text("0\n")
    run_file(direct, "log").write_text("Done.\n")
    H = load(lua, ui_script(direct))
    assert shown(H) == MAIN_VIEW
    press(H, "SettingsBtn")
    press(H, "RefreshStatusBtn")
    assert H.status() == "No run in progress."
    assert "FolderBtn" not in shown(H)


def test_a_run_that_went_quiet_while_closed_shows_its_progress_and_resets_in_one_press(
        lua, direct):
    import time
    write_status(direct, fraction=0.3, updated=time.time() - 10 * 60)
    H = load(lua, ui_script(direct))
    assert {"Status", "RefreshStatusBtn", "CancelBtn"} <= shown(H)
    assert H.text_of("CancelBtn") == "Reset"
    press(H, "RefreshStatusBtn")
    assert "30%" in H.widgets["Progress"].Text and "No progress" in H.status()
    press(H, "CancelBtn")
    assert H.status() == GAVE_UP
    assert "CancelBtn" not in shown(H) and "RefreshStatusBtn" in shown(H)


def test_a_render_that_finishes_after_reset_imports_with_refresh_and_no_new_render(lua, direct):
    """Refresh Progress used to be always there for it. Nothing else would
    bring in a render that Reset gave up on and that finished after all."""
    import time
    H = load(lua, ui_script(direct))
    press(H, "SmoothBtn")
    write_status(direct, fraction=0.3, updated=time.time() - 10 * 60)
    press(H, "RefreshStatusBtn")
    press(H, "CancelBtn")
    assert H.status() == GAVE_UP
    commands = H.command_count()
    press(H, "RefreshStatusBtn")
    assert H.status() == "The run you reset has not finished."
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path="/tmp/late.mp4", updated=time.time())
    press(H, "RefreshStatusBtn")
    assert H.imported_count() == 1 and H.imported_at(1) == "/tmp/late.mp4"
    assert H.placements[1].recordFrame == 86400, "it did not go where its clip is"
    assert H.command_count() == commands, "a new render started"
    assert "RefreshStatusBtn" not in shown(H)


def test_a_reset_run_that_dies_after_all_shows_why(lua, direct):
    import time
    H = load(lua, ui_script(direct))
    press(H, "SmoothBtn")
    write_status(direct, fraction=0.3, updated=time.time() - 10 * 60)
    press(H, "RefreshStatusBtn")
    press(H, "CancelBtn")
    run_file(direct, "log").write_text("MemoryError\n")
    run_file(direct, "exit").write_text("1\n")
    press(H, "RefreshStatusBtn")
    assert "MemoryError" in H.status() and H.text_of("FolderBtn") == "Open Log"


def test_a_new_run_clears_what_the_last_one_needed(lua, direct):
    import time
    H = load(lua, ui_script(direct))
    press(H, "SmoothBtn")
    write_status(direct, fraction=0.3, updated=time.time() - 10 * 60)
    press(H, "RefreshStatusBtn")
    press(H, "CancelBtn")
    press(H, "SmoothBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path="/tmp/new.mp4", updated=time.time())
    press(H, "RefreshStatusBtn")
    assert H.imported_at(1) == "/tmp/new.mp4"
    assert not shown(H) & {"RefreshStatusBtn", "CancelBtn"}


def test_a_timer_that_cannot_start_leaves_refresh_on_the_main_view(lua, direct):
    H = lua.eval(f'dofile("{HARNESS.as_posix()}")')
    H.timer_start_fails = True
    H.under_playhead = False
    H.load(str(ui_script(direct)), "/media/shot.mov")
    assert "RefreshStatusBtn" in shown(H)
    H.under_playhead = True
    press(H, "RefreshStatusBtn")
    assert H.text_of("SourceLabel") == "shot.mov"


def test_a_timer_whose_ticks_never_arrive_puts_refresh_back(lua, direct):
    H = load(lua, ui_script(direct))
    assert "RefreshStatusBtn" not in shown(H)
    advance_clock(lua, 5)
    press(H, "SettingsBtn")
    press(H, "BackBtn")
    assert "RefreshStatusBtn" in shown(H), "no tick came, and nothing reads the clip again"
    H.fire("PollTimer", "Timeout")
    assert "RefreshStatusBtn" not in shown(H), "a tick came after all"


def stop_a_render(H, root):
    """Render, end it with two presses of Stop, and see it report Stopped."""
    import time
    press(H, "SmoothBtn")
    write_status(root, fraction=0.4, updated=time.time())
    press(H, "CancelBtn")
    press(H, "CancelBtn")
    write_status(root, fraction=0.4, done=True, ok=False, message="Stopped.",
                 updated=time.time())
    press(H, "RefreshStatusBtn")
    assert H.status() == "Stopped."


@pytest.mark.parametrize("exit_arrives", ["before reopening", "after reopening"])
def test_a_stop_seen_before_closing_is_no_failure_when_reopened(lua, direct, exit_arrives):
    """Python exits 1 after a Stop, and the shell writes that once it has. The
    panel had shown Stopped, and opened again it said the run did not finish."""
    H = load(lua, ui_script(direct))
    stop_a_render(H, direct)
    if exit_arrives == "before reopening":
        run_file(direct, "exit").write_text("1\n")
    H = load(lua, ui_script(direct))
    assert shown(H) == MAIN_VIEW
    run_file(direct, "exit").write_text("1\n")
    press(H, "SettingsBtn")
    press(H, "RefreshStatusBtn")
    assert H.status() == "No run in progress." and "FolderBtn" not in shown(H)
    H = load(lua, ui_script(direct))
    assert shown(H) == MAIN_VIEW
    assert run_file(direct, "exit").exists(), "the proof the run ended went"


def test_a_crash_seen_before_closing_is_not_shown_again_when_reopened(lua, direct):
    H = load(lua, ui_script(direct))
    die(H, direct)
    assert "Stopped without finishing" in H.status()
    H = load(lua, ui_script(direct))
    assert shown(H) == MAIN_VIEW


@pytest.mark.parametrize("left", ["status", "exit only"])
def test_an_unseen_failure_prompts_and_rests_once_it_is_seen(lua, direct, left):
    """From a run before 0.5.2, under the names every run had then."""
    if left == "status":
        write_status(direct, done=True, ok=False, message="The clip's media is missing.")
    run_file(direct, "log").write_text("ModuleNotFoundError: No module named 'cv2'\n")
    run_file(direct, "exit").write_text("1\n")
    H = load(lua, ui_script(direct))
    assert run_tag(direct) == ""
    assert {"Status", "RefreshStatusBtn"} <= shown(H)
    press(H, "RefreshStatusBtn")
    assert ("media is missing" if left == "status" else "cv2") in H.status()
    H = load(lua, ui_script(direct))
    assert shown(H) == MAIN_VIEW


def test_the_next_runs_unseen_failure_prompts_after_one_was_seen(lua, direct):
    """What a panel saw of one run never hides another run's failure."""
    H = load(lua, ui_script(direct))
    die(H, direct)
    H = load(lua, ui_script(direct))
    first = run_tag(direct)
    press(H, "SmoothBtn")
    assert run_tag(direct) != first
    run_file(direct, "log").write_text("MemoryError\n")
    run_file(direct, "exit").write_text("1\n")
    H = load(lua, ui_script(direct))
    assert {"Status", "RefreshStatusBtn"} <= shown(H)
    press(H, "RefreshStatusBtn")
    assert "MemoryError" in H.status()


REPORTING = "The run is reporting again. Its progress shows here."


def reopen_over_a_quiet_run(lua, direct):
    """A panel opened over a run that went quiet, which then reports again."""
    import time
    write_status(direct, fraction=0.3, updated=time.time() - 10 * 60)
    H = load(lua, ui_script(direct))
    assert "CancelBtn" in shown(H) and H.text_of("CancelBtn") == "Reset"
    write_status(direct, fraction=0.5, updated=time.time())
    return H


@pytest.mark.parametrize("separator", ["\\\\", "/"])
def test_a_quiet_run_that_reports_again_is_followed_after_refresh(lua, direct, separator):
    """Its progress came back, but Reset and the quiet prompt stayed, and a
    press of the button labelled Reset armed Stop."""
    lua.execute(f'package.config = "{separator}" .. package.config:sub(2)')
    H = reopen_over_a_quiet_run(lua, direct)
    press(H, "RefreshStatusBtn")
    assert "50%" in H.widgets["Progress"].Text
    assert H.text_of("CancelBtn") == "Stop" and H.status() == REPORTING
    assert not H.enabled("SmoothBtn")
    press(H, "CancelBtn")
    assert not run_file(direct, "stop").exists()
    press(H, "CancelBtn")
    assert run_file(direct, "stop").exists()


@pytest.mark.parametrize("separator", ["\\\\", "/"])
def test_reset_pressed_after_a_quiet_run_reports_again_only_follows_it(lua, direct, separator):
    """Pressed before anything read the run again, the button still said Reset."""
    lua.execute(f'package.config = "{separator}" .. package.config:sub(2)')
    H = reopen_over_a_quiet_run(lua, direct)
    press(H, "CancelBtn")
    assert not run_file(direct, "stop").exists()
    assert H.text_of("CancelBtn") == "Stop" and H.status() == REPORTING
    press(H, "CancelBtn")
    assert not run_file(direct, "stop").exists(), "one press labelled Stop ended it"
    assert H.status().startswith("Press Stop again")
    press(H, "CancelBtn")
    assert run_file(direct, "stop").exists()


def test_reset_on_a_followed_run_that_reports_again_only_follows_it(lua, direct):
    import time
    H = load(lua, ui_script(direct))
    press(H, "SmoothBtn")
    write_status(direct, fraction=0.3, updated=time.time() - 10 * 60)
    press(H, "RefreshStatusBtn")
    assert H.text_of("CancelBtn") == "Reset" and "No progress" in H.status()
    write_status(direct, fraction=0.6, updated=time.time())
    press(H, "CancelBtn")
    assert not run_file(direct, "stop").exists()
    assert H.text_of("CancelBtn") == "Stop" and H.status() == REPORTING
    press(H, "CancelBtn")
    press(H, "CancelBtn")
    assert run_file(direct, "stop").exists()


def test_a_quiet_run_that_reports_again_imports_once_when_it_finishes(lua, direct):
    import time
    H = reopen_over_a_quiet_run(lua, direct)
    press(H, "RefreshStatusBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path="/tmp/late.mp4", updated=time.time())
    H.fire("PollTimer", "Timeout")
    assert H.imported_count() == 1 and H.enabled("SmoothBtn")
    assert "CancelBtn" not in shown(H)
    H.fire("PollTimer", "Timeout")
    press(H, "SettingsBtn")
    press(H, "RefreshStatusBtn")
    assert H.imported_count() == 1


def refresh_in_settings(H):
    """Settings, then its Refresh Progress, as a person reaches it."""
    if "SettingsBtn" in shown(H):
        press(H, "SettingsBtn")
    press(H, "RefreshStatusBtn")


def test_a_crash_after_a_heartbeat_once_seen_is_not_shown_again_by_refresh(lua, direct):
    """Its last progress stayed on disk, so opened again the panel was quiet
    but Refresh Progress showed the old crash once more, every time."""
    import time
    H = load(lua, ui_script(direct))
    press(H, "SmoothBtn")
    write_status(direct, fraction=0.4, updated=time.time())
    run_file(direct, "log").write_text("RuntimeError: boom\n")
    run_file(direct, "exit").write_text("1\n")
    press(H, "RefreshStatusBtn")
    assert "boom" in H.status() and H.text_of("FolderBtn") == "Open Log"
    refresh_in_settings(H)
    assert H.status() == "No run in progress."
    assert "FolderBtn" in shown(H), "the log went in the session that showed the crash"
    for _ in range(2):
        H = load(lua, ui_script(direct))
        assert shown(H) == MAIN_VIEW
        refresh_in_settings(H)
        refresh_in_settings(H)
        assert H.status() == "No run in progress." and "FolderBtn" not in shown(H)
    assert run_file(direct, "exit").exists(), "the proof the run ended went"


def test_an_unseen_crash_after_a_heartbeat_is_shown_once(lua, direct):
    """From a run before 0.5.2, which wrote progress, then crashed."""
    import time
    write_status(direct, fraction=0.4, updated=time.time() - 60)
    run_file(direct, "log").write_text("MemoryError\n")
    run_file(direct, "exit").write_text("1\n")
    H = load(lua, ui_script(direct))
    assert run_tag(direct) == "" and {"Status", "RefreshStatusBtn"} <= shown(H)
    press(H, "RefreshStatusBtn")
    assert "MemoryError" in H.status()
    refresh_in_settings(H)
    assert H.status() == "No run in progress."
    H = load(lua, ui_script(direct))
    assert shown(H) == MAIN_VIEW
    refresh_in_settings(H)
    assert H.status() == "No run in progress."


def as_0_5_1_starts_a_run(root):
    """What v0.5.1's clear_status does before each run it starts, with the
    names every run had then."""
    for kind in ("status", "exit", "stop"):
        run_file(root, kind, "").unlink(missing_ok=True)


def test_a_new_0_5_1_crash_after_one_was_seen_is_shown(lua, direct):
    """0.5.1 reuses one set of names. A note that the last crash was seen,
    left where 0.5.1 never clears it, hid the next crash, with the same
    exit code."""
    run_file(direct, "log").write_text("ModuleNotFoundError: No module named 'cv2'\n")
    run_file(direct, "exit").write_text("1\n")
    H = load(lua, ui_script(direct))
    press(H, "RefreshStatusBtn")
    assert "cv2" in H.status()
    as_0_5_1_starts_a_run(direct)
    run_file(direct, "log", "").write_text("MemoryError\n")
    run_file(direct, "exit", "").write_text("1\n")
    H = load(lua, ui_script(direct))
    assert {"Status", "RefreshStatusBtn"} <= shown(H), "the new crash was hidden"
    press(H, "RefreshStatusBtn")
    assert "MemoryError" in H.status()


def test_a_seen_note_from_an_earlier_build_hides_nothing_and_goes_with_its_run(lua, direct):
    seen = direct / ".upflow2x_last_run.seen"
    seen.write_text("")
    run_file(direct, "log").write_text("MemoryError\n")
    run_file(direct, "exit").write_text("1\n")
    H = load(lua, ui_script(direct))
    assert {"Status", "RefreshStatusBtn"} <= shown(H), "an old note hid the crash"
    press(H, "SmoothBtn")
    assert run_tag(direct) != "" and not seen.exists()


def test_a_0_5_1_run_stopped_here_stays_quiet_when_its_exit_code_comes_later(lua, direct):
    """A 0.5.1 run this panel followed and stopped. Its exit code comes after
    the panel showed Stopped."""
    import time
    write_status(direct, fraction=0.3, updated=time.time())
    H = load(lua, ui_script(direct))
    assert run_tag(direct) == "" and H.text_of("CancelBtn") == "Stop"
    press(H, "CancelBtn")
    press(H, "CancelBtn")
    assert run_file(direct, "stop").exists()
    write_status(direct, fraction=0.3, done=True, ok=False, message="Stopped.",
                 updated=time.time())
    press(H, "RefreshStatusBtn")
    assert H.status() == "Stopped."
    run_file(direct, "exit").write_text("1\n")
    H = load(lua, ui_script(direct))
    assert shown(H) == MAIN_VIEW
    refresh_in_settings(H)
    assert H.status() == "No run in progress."
    assert run_file(direct, "status").read_text() == "", "0.5.1 would read more than nothing"


AMBER = "#f2a93b"


def test_amber_fills_only_render(lua, direct):
    """Amber filled the slider, the list highlight and both switches, so
    Render did not stand out. Now it fills only Render and the brand badge."""
    H = load(lua, ui_script(direct))
    filled = sorted(i for i in H.widget_ids().split(",")
                    if f"background-color: {AMBER}" in (H.widgets[i].StyleSheet or ""))
    assert filled == ["Badge", "SmoothBtn"]
    assert AMBER not in H.window.__attrs.StyleSheet, "the slider and lists stay grey"


SWITCH_WORDS = {
    ("UpscaleCB", True): "On \u00b7 2x",
    ("UpscaleCB", False): "Off \u00b7 original size",
    ("InterpCB", True): "On \u00b7 3x longer",
    ("InterpCB", False): "Off \u00b7 same length",
}
SWITCHES = [(True, True), (True, False), (False, True), (False, False)]


def assert_render_matches(H, upscale, interp):
    """The words under the switches, and the flags Render then sends."""
    assert H.text_of("UpscaleMode") == SWITCH_WORDS["UpscaleCB", upscale]
    assert H.text_of("InterpMode") == SWITCH_WORDS["InterpCB", interp]
    H.click("SmoothBtn")
    cmd = H.last_command()
    assert ("--network-upscale" in cmd, "--no-upscale" in cmd) == (upscale, not upscale)
    assert ("--slow-motion 3" in cmd, "--no-interpolate" in cmd) == (interp, not interp)


@pytest.mark.parametrize("upscale,interp", SWITCHES)
def test_each_switch_says_in_words_what_it_does_to_the_render(lua, direct, upscale, interp):
    """Only an amber border said a switch was on. The line under each one
    says On or Off, and for Interpolate the length, matching what Render sends."""
    H = load(lua, ui_script(direct))
    # Through the panel's own handlers, away and back, so each change is shown.
    for switch, on in (("UpscaleCB", upscale), ("InterpCB", interp)):
        H.set_checkbox(switch, not on)
        H.set_checkbox(switch, on)
    assert conf_value(direct, "upscale") == str(upscale).lower()
    assert conf_value(direct, "interpolate") == str(interp).lower()
    assert_render_matches(H, upscale, interp)


@pytest.mark.parametrize("upscale,interp", SWITCHES)
def test_a_restored_setting_shows_its_words_from_the_start(lua, direct, upscale, interp):
    conf = direct / "Installer" / "Eternal2x.conf"
    conf.write_text(conf.read_text() + f"upscale={str(upscale).lower()}\n"
                    f"interpolate={str(interp).lower()}\n")
    H = load(lua, ui_script(direct))
    assert bool(H.widgets["UpscaleCB"].Checked) == upscale
    assert bool(H.widgets["InterpCB"].Checked) == interp
    assert_render_matches(H, upscale, interp)


@pytest.mark.parametrize("separator", ["\\\\", "/"])
def test_the_upscale_words_assume_no_gpu_runtime_on_either_system(lua, direct, separator):
    """DirectML is Windows only, and on a Mac Super Scale does the upscale, so
    the words name the factor. The tip, and the stage's help, say how."""
    from Stages.resolve_smooth import build_parser
    lua.execute(f'package.config = "{separator}" .. package.config:sub(2)')
    H = load(lua, ui_script(direct))
    assert H.text_of("UpscaleMode") == SWITCH_WORDS["UpscaleCB", True]
    assert not {"DirectML", "GPU"} & set(words(H))
    tip = H.widgets["UpscaleCB"].ToolTip
    assert "On Mac" in tip and "Super Scale" in tip
    flag = next(a.help for a in build_parser()._actions if "--network-upscale" in a.option_strings)
    assert "Without a GPU" in flag and "Super Scale" in flag


def style_rule(sheet, selector):
    """The properties of the first rule for `selector` in a style sheet."""
    found = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", sheet)
    assert found, f"no {selector} rule in {sheet}"
    return {k.strip(): v.strip() for k, _, v in
            (part.partition(":") for part in found.group(1).split(";")) if v.strip()}


def test_a_switch_that_is_on_is_bold_as_well_as_amber(lua, direct):
    H = load(lua, ui_script(direct))
    sheet = H.widgets["UpscaleCB"].StyleSheet
    assert style_rule(sheet, "QPushButton:checked")["font-weight"] == "700"
    assert "font-weight" not in style_rule(sheet, "QPushButton")


def test_every_control_shows_where_keyboard_focus_is(lua, direct):
    H = load(lua, ui_script(direct))
    buttons = [i for i in H.widget_ids().split(",") if H.widgets[i].__kind == "Button"]
    assert len(buttons) >= 9
    for button in buttons:
        assert "QPushButton:focus" in H.widgets[button].StyleSheet, button
    sheet = H.window.__attrs.StyleSheet
    assert "QComboBox:focus" in sheet and "QSlider::handle:horizontal:focus" in sheet
    assert "QCheckBox:focus" in H.widgets["ChimeCB"].StyleSheet
    # Stop, waiting for its second press, keeps a focus mark too.
    H.click("SmoothBtn")
    H.click("CancelBtn")
    assert "QPushButton:focus" in H.widgets["CancelBtn"].StyleSheet


def luminance(colour):
    channels = [int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    r, g, b = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_dropdown_highlight_distinguishes_the_row_and_keeps_its_text_legible(lua, direct):
    panel = load(lua, ui_script(direct))
    popup = style_rule(panel.window.__attrs.StyleSheet, "QComboBox QAbstractItemView")
    highlight = popup["selection-background-color"]
    assert contrast(highlight, popup["background-color"]) >= 3
    assert contrast(highlight, popup["selection-color"]) >= 4.5


def test_text_on_the_panel_reads_at_4_5_to_1_or_better(lua, direct):
    """The version and the hints under the links were 3.4:1 on the panel's grey."""
    H = load(lua, ui_script(direct))
    panel = style_rule(H.window.__attrs.StyleSheet, "QWidget")["background-color"]
    checked = 0
    for wid in H.widget_ids().split(","):
        widget = H.widgets[wid]
        kind = {"Label": "QLabel", "Button": "QPushButton",
                "CheckBox": "QCheckBox"}.get(widget.__kind)
        if not kind or not widget.StyleSheet:
            continue
        rule = style_rule(widget.StyleSheet, kind)
        if "color" not in rule:
            continue    # the progress bar, which has no text
        back = rule.get("background-color", "transparent")
        back = panel if back == "transparent" else back
        assert contrast(rule["color"], back) >= 4.5, f"{wid}: {rule['color']} on {back}"
        checked += 1
    assert checked >= 20


# --------------------------------------------------------------------------
# the install chain
# --------------------------------------------------------------------------

def test_launcher_reaches_the_panel(lua, installed):
    comp, _ = installed
    assert load(lua, comp / "Upflow2x.lua").window is not None


@pytest.fixture
def no_real_install(tmp_path, monkeypatch):
    """Point the launcher's fallback away from this machine's real install."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("APPDATA", str(home / "AppData" / "Roaming"))
    return home


def test_launcher_reports_a_missing_config(lua, tmp_path, repo_root, no_real_install):
    comp = tmp_path / "Comp"
    comp.mkdir()
    shutil.copy2(repo_root / "Installer" / "Eternal2xLauncher.lua", comp / "Upflow2x.lua")
    H = load(lua, comp / "Upflow2x.lua")
    assert "Missing repo_root" in " ".join(str(v) for v in dict(H.prints).values())


def test_launcher_finds_the_installed_config_when_its_own_path_is_mapped(
        lua, tmp_path, repo_root, installed, no_real_install):
    """Resolve may report the script as Scripts:/... which io.open cannot open."""
    comp, plugin_root = installed
    if sys.platform.startswith("win"):
        utility = (no_real_install / "AppData" / "Roaming" / "Blackmagic Design"
                   / "DaVinci Resolve" / "Support" / "Fusion" / "Scripts" / "Utility")
    elif sys.platform == "darwin":
        utility = (no_real_install / "Library" / "Application Support" / "Blackmagic Design"
                   / "DaVinci Resolve" / "Fusion" / "Scripts" / "Utility")
    else:
        utility = no_real_install / ".local" / "share" / "DaVinciResolve" / "Fusion" / "Scripts" / "Utility"
    utility.mkdir(parents=True)
    shutil.move(str(comp / "Eternal2x.conf"), str(utility / "Eternal2x.conf"))

    H = load(lua, comp / "Upflow2x.lua")
    assert H.window is not None, "the launcher did not fall back to the installed config"
    H.click("AnalyseBtn")
    assert f"cd '{plugin_root}'" in H.last_command() or f'"{plugin_root}"' in H.last_command()


def test_buttons_run_from_the_folder_that_holds_the_code(lua, installed):
    """The whole install chain: config in Comp, code elsewhere, cd to the code."""
    comp, plugin_root = installed
    H = load(lua, comp / "Upflow2x.lua")
    H.click("AnalyseBtn")

    cmd = H.last_command()
    assert cmd is not None, "clicking Analyse ran nothing"
    assert f"cd '{plugin_root}'" in cmd or f'"{plugin_root}"' in cmd, (
        f"ran: {cmd}\nexpected a cd into {plugin_root}")


def test_the_configured_interpreter_is_used(lua, installed):
    comp, _ = installed
    H = load(lua, comp / "Upflow2x.lua")
    H.click("AnalyseBtn")
    assert sys.executable in H.last_command()


def test_the_version_survives_the_launcher(lua, installed):
    comp, _ = installed
    H = load(lua, comp / "Upflow2x.lua")
    assert "0.3.0" in H.widgets["SubTitle"].Text


def test_the_update_url_survives_the_launcher(lua, installed):
    comp, _ = installed
    H = load(lua, comp / "Upflow2x.lua")
    H.click("UpdateBtn")
    assert "example.com/latest.json" in (H.last_command() or "")


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def test_analyse_runs_the_stage_in_analyse_mode(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("AnalyseBtn")
    cmd = H.last_command()
    assert "-m Stages.resolve_smooth" in cmd
    assert "--analyse" in cmd


def test_analyse_also_runs_in_the_background(lua, direct):
    """Analysis is quick on a short clip but slow on a long one, and Resolve
    must not freeze for it either."""
    H = load(lua, ui_script(direct))
    H.click("AnalyseBtn")
    cmd = H.last_command()
    assert cmd.rstrip().endswith("&") or "start " in cmd
    assert "--status-file" in cmd


def test_analyse_result_arrives_through_the_status_file(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("AnalyseBtn")
    write_status(direct, fraction=1.0, done=True, ok=True,
                 message="48 frames, 24 unique drawings. Animated on 2s.")
    H.click("RefreshStatusBtn")
    assert "24 unique drawings" in H.widgets["AnalysisLabel"].Text
    assert "Analysis complete" in H.status()


def test_a_failed_analyse_says_so_in_the_panel(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("AnalyseBtn")
    write_status(direct, done=True, ok=False, message="The clip's media is missing.")
    H.click("RefreshStatusBtn")
    assert "media is missing" in H.widgets["AnalysisLabel"].Text
    assert H.enabled("AnalyseBtn")


def test_analyse_never_imports_anything(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("AnalyseBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, message="On 2s.",
                 import_path="/tmp/should-not-be-imported.mp4")
    H.click("RefreshStatusBtn")
    assert H.imported_count() == 0, "Analyse imported a clip"


def test_smooth_runs_in_the_background(lua, direct):
    """A render takes minutes; blocking here would freeze Resolve."""
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    cmd = H.last_command()
    assert "-m Stages.resolve_smooth" in cmd
    assert "--analyse" not in cmd
    assert cmd.rstrip().endswith("&") or "start " in cmd


def test_smooth_passes_a_status_file(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    assert "--status-file" in H.last_command()


def test_interpolate_off_reaches_the_command(lua, direct):
    H = load(lua, ui_script(direct))
    H.set_checkbox("InterpCB", False)
    H.click("SmoothBtn")
    assert "--no-interpolate" in H.last_command()


def test_a_hold_pattern_or_grain_saved_before_is_not_sent(lua, direct):
    """The panel no longer offers either, so a value an older version saved
    would steer every run with no way to change it."""
    conf = direct / "Installer" / "Eternal2x.conf"
    conf.write_text(conf.read_text() + "base_hold=3\ngrain=true\n")
    H = load(lua, ui_script(direct))
    H.click("AnalyseBtn")
    assert "--base-hold" not in H.last_command()
    assert "--grain" not in H.last_command()


def test_unchecking_upscale_reaches_the_command(lua, direct):
    H = load(lua, ui_script(direct))
    H.set_checkbox("UpscaleCB", False)
    H.click("AnalyseBtn")
    assert "--no-upscale" in H.last_command()


def test_unchecking_interpolate_reaches_the_command(lua, direct):
    H = load(lua, ui_script(direct))
    H.set_checkbox("InterpCB", False)
    H.click("AnalyseBtn")
    assert "--no-interpolate" in H.last_command()




def test_defaults_send_no_opt_out_flags(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("AnalyseBtn")
    cmd = H.last_command()
    assert "--no-upscale" not in cmd and "--no-interpolate" not in cmd


def test_a_clip_path_with_spaces_is_quoted(lua, direct):
    H = load(lua, ui_script(direct), clip="/media/My Footage/shot 01.mov")
    assert "My Footage" in H.widgets["SourceLabel"].ToolTip


def test_every_module_the_panel_calls_exists(lua, direct, repo_root):
    for button in ("AnalyseBtn", "SmoothBtn", "UpdateBtn"):
        H = load(lua, ui_script(direct))
        H.click(button)
        module = H.last_command().split("-m ")[1].split()[0]
        assert (repo_root / Path(module.replace(".", "/") + ".py")).exists(), \
            f"{button} calls missing module {module}"
        # The run ends, so the next panel does not follow it as one still loading.
        run_file(direct, "exit").write_text("0\n")


def test_the_command_the_panel_builds_actually_runs(lua, direct):
    """Run the panel's own command string and confirm it resolves the package."""
    H = load(lua, ui_script(direct))
    H.click("AnalyseBtn")
    cmd = H.last_command()

    subprocess.run(cmd, shell=True, capture_output=True, text=True)
    log = run_file(direct, "log")
    assert log.exists(), "the panel's command produced no log"
    text = log.read_text()
    for missing in ("No module named 'Stages'", "No module named 'Pipeline'"):
        assert missing not in text, f"the panel cannot reach the plugin modules:\n{text}"


# --------------------------------------------------------------------------
# source clip
# --------------------------------------------------------------------------

def test_the_selected_clip_is_shown_on_open(lua, direct):
    H = load(lua, ui_script(direct), clip="/media/scene 04.mov")
    assert "scene 04.mov" in H.widgets["SourceLabel"].Text


def test_refresh_picks_up_the_selection(lua, direct):
    H = load(lua, ui_script(direct), clip="/media/shot.mov")
    follow(H)
    assert "shot.mov" in H.widgets["SourceLabel"].Text
    assert "Ready" in H.status()


# --------------------------------------------------------------------------
# progress reporting
# --------------------------------------------------------------------------

LEGACY_RUN_FILES = {"status": ".eternal2x_status.json", "tmp": ".eternal2x_status.tmp",
                    "stop": ".eternal2x_status.stop", "exit": ".eternal2x_exit",
                    "marks": ".eternal2x_marks.txt", "script": ".eternal2x_run.cmd",
                    "log": ".upflow2x_last_run.log"}
RUN_FILE_ENDINGS = {"status": ".json", "tmp": ".tmp", "stop": ".stop", "exit": ".exit",
                    "marks": ".marks", "script": ".cmd", "log": ".log"}


def run_tag(root: Path) -> str:
    """The tag of the run the panel owns, from its pointer file. "" for none,
    which is how every run was named before 0.5.2."""
    pointer = root / ".upflow2x_run"
    return pointer.read_text().strip() if pointer.exists() else ""


def run_file(root: Path, kind: str, tag: str | None = None) -> Path:
    """A file of the run tagged `tag`, by default the one the panel owns."""
    tag = run_tag(root) if tag is None else tag
    if not tag:
        return root / LEGACY_RUN_FILES[kind]
    return root / f".upflow2x_run-{tag}{RUN_FILE_ENDINGS[kind]}"


def write_status(root: Path, tag: str | None = None, **fields):
    """The status a run writes, by default the run the panel owns."""
    payload = {"stage": "Rendering", "fraction": 0.5, "message": "",
               "import_path": "", "done": False, "ok": False, "updated": 0}
    payload.update(fields)
    run_file(root, "status", tag).write_text(json.dumps(payload))


def test_progress_is_shown_while_running(lua, direct):
    H = load(lua, ui_script(direct))
    write_status(direct, stage="Rendering", fraction=0.5)
    H.click("RefreshStatusBtn")
    assert "50%" in H.widgets["Progress"].Text


def test_a_stage_at_zero_percent_is_still_named(lua, direct):
    H = load(lua, ui_script(direct))
    write_status(direct, stage="Installing AI runtime", fraction=0.0)
    H.click("RefreshStatusBtn")
    assert "Installing AI runtime" in H.widgets["Progress"].Text


def bar_fill(H):
    """How far the amber runs in the progress bar's gradient, or None."""
    stops = re.findall(r"stop: ([\d.]+) ", H.widgets["ProgressBar"].StyleSheet)
    return float(stops[1]) if len(stops) > 1 else None


def test_progress_bar_fills_as_it_goes(lua, direct):
    H = load(lua, ui_script(direct))
    filled = []
    for fraction in (0.1, 0.9):
        write_status(direct, fraction=fraction)
        H.click("RefreshStatusBtn")
        filled.append(bar_fill(H))
    assert filled == [0.1, 0.9], "the progress bar did not grow"


def test_the_progress_bar_fills_under_a_decimal_comma_locale(lua, direct):
    """Qt drops a gradient whose stops read 0,420."""
    for name in ("de_DE.UTF-8", "de_DE", "German_Germany.1252"):
        if lua.eval(f'os.setlocale("{name}", "numeric")'):
            break
    else:
        pytest.skip("no decimal comma locale here")
    try:
        assert lua.eval('string.format("%.1f", 0.5)') == "0,5"
        H = load(lua, ui_script(direct))
        write_status(direct, fraction=0.42)
        H.click("RefreshStatusBtn")
        assert bar_fill(H) == 0.42
    finally:
        lua.eval('os.setlocale("C", "numeric")')


def test_the_progress_bar_is_empty_until_a_run_starts(lua, direct):
    H = load(lua, ui_script(direct))
    assert bar_fill(H) is None
    H.click("SmoothBtn")
    assert bar_fill(H) is not None
    run_file(direct, "exit").write_text("1\n")
    H.click("CancelBtn")
    assert bar_fill(H) is None


def test_completion_message_is_surfaced(lua, direct):
    H = load(lua, ui_script(direct))
    write_status(direct, fraction=1.0, done=True, ok=True,
                 message="Done. 12 drawings rebuilt into 24 smooth frames.")
    H.click("RefreshStatusBtn")
    assert "12 drawings" in H.status()


def test_a_finished_render_is_imported_once_even_after_reopening(lua, direct):
    """The status file outlives the panel. Pressing Refresh Progress in a new
    panel must not import the last render again and append a second copy."""
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, message="Done.",
                 import_path=str(direct / "shot_smooth.mp4"))
    H.click("RefreshStatusBtn")
    assert H.imported_count() == 1
    H.click("RefreshStatusBtn")

    H = load(lua, ui_script(direct))
    H.click("RefreshStatusBtn")
    assert H.imported_count() == 0, "the reopened panel imported the render again"
    assert "No run in progress" in H.status()


def test_a_failure_is_surfaced(lua, direct):
    H = load(lua, ui_script(direct))
    write_status(direct, done=True, ok=False, message="The clip's media is missing.")
    H.click("RefreshStatusBtn")
    assert "media is missing" in H.status()


def test_buttons_are_disabled_while_a_render_runs(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    assert not H.enabled("SmoothBtn"), "Smooth stayed clickable during a render"
    assert not H.enabled("AnalyseBtn")


def test_buttons_come_back_when_the_render_finishes(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, message="Done.")
    H.click("RefreshStatusBtn")
    assert H.enabled("SmoothBtn")
    assert H.enabled("AnalyseBtn")


def test_reset_clears_a_stuck_run(lua, direct):
    """The run ended without saying it was done, and the panel never polled."""
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    run_file(direct, "exit").write_text("1\n")
    H.click("CancelBtn")
    assert H.enabled("SmoothBtn")
    assert H.widgets["Progress"].Text == ""
    # Left empty, which reads as no status, and says its end was dealt with.
    assert run_file(direct, "status").read_text() == ""


def test_refreshing_with_no_run_says_so(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("RefreshStatusBtn")
    assert "No run in progress" in H.status()


# --------------------------------------------------------------------------
# settings persistence
# --------------------------------------------------------------------------

def test_settings_are_saved(lua, direct):
    H = load(lua, ui_script(direct))
    H.set_checkbox("UpscaleCB", False)

    conf = (direct / "Installer" / "Eternal2x.conf").read_text()
    assert "upscale=false" in conf


def test_a_saved_avi_output_falls_back_to_the_image_sequence(lua, direct):
    """Resolve Studio 20.3.1 refuses FFV1 in AVI, so the lossless file is now
    MKV, and a setting saved by an older version picks the safe default."""
    conf = direct / "Installer" / "Eternal2x.conf"
    conf.write_text(conf.read_text() + "format=avi\n")
    H = load(lua, ui_script(direct))
    assert "MKV" in H.combo_items("FormatCombo") and "AVI" not in H.combo_items("FormatCombo")
    H.click("SmoothBtn")
    assert "--format png" in H.last_command()


def test_saving_settings_keeps_the_rest_of_the_config(lua, direct):
    H = load(lua, ui_script(direct))
    H.set_checkbox("UpscaleCB", False)
    conf = (direct / "Installer" / "Eternal2x.conf").read_text()
    assert f"repo_root={direct}" in conf
    assert "update_url=https://example.com/latest.json" in conf


def test_saved_settings_are_restored_next_time(lua, direct):
    H = load(lua, ui_script(direct))
    H.set_checkbox("UpscaleCB", False)

    H2 = load(lua, ui_script(direct))
    H2.click("AnalyseBtn")
    assert "--no-upscale" in H2.last_command()


def test_a_config_that_turned_updates_off_keeps_them_off(lua, direct):
    """The panel has no box for it now, but someone who turned it off before
    keeps their choice."""
    H = load(lua, ui_script(direct))
    H.set_checkbox("UpscaleCB", False)
    assert "auto_update=false" in (direct / "Installer" / "Eternal2x.conf").read_text()


# --------------------------------------------------------------------------
# background launching
# --------------------------------------------------------------------------

def test_the_background_command_redirects_into_the_log(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    cmd = H.last_command()
    assert run_file(direct, "log").name in cmd
    assert "2>&1" in cmd


def test_the_windows_background_form_keeps_the_redirect_inside_cmd(repo_root):
    """`start ... > log` binds the redirect to start, leaving the log empty."""
    lua_src = (repo_root / "Installer" / "Eternal2x.lua").read_text(encoding="utf-8")
    block = lua_src[lua_src.index("local function build_command"):
                    lua_src.index("local function run_blocking")]
    assert 'cmd /c' in block, (
        "the Windows background launch must wrap the command in cmd /c so the "
        "redirection applies to the child process"
    )


def test_a_background_run_does_not_block(lua, direct):
    """The panel must not wait on the render, or Resolve freezes."""
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    assert not H.enabled("SmoothBtn"), "the panel should show it is busy"
    assert "Rendering" in H.status()


# --------------------------------------------------------------------------
# the panel imports the finished render itself
# --------------------------------------------------------------------------

def test_python_is_given_the_clip_path_directly(lua, direct):
    """The stage must not have to reconnect to Resolve.

    A Python process spawned from Lua can only import DaVinciResolveScript if
    the user has external scripting configured. The panel already holds a
    connection, so it passes the path down and does the import itself.
    """
    H = load(lua, ui_script(direct), clip="/media/shot.mov")
    H.click("AnalyseBtn")
    assert "--video" in H.last_command()
    assert "/media/shot.mov" in H.last_command()


def test_a_finished_render_is_imported_and_appended(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path="/tmp/out/frame_000000.png")
    H.click("RefreshStatusBtn")

    assert H.imported_count() == 1
    assert H.imported_at(1) == "/tmp/out/frame_000000.png"
    assert H.appended == 1
    assert "above the original" in H.status()


def finish_render(H, root, **fields):
    H.click("SmoothBtn")
    status = {"fraction": 1.0, "done": True, "ok": True, "message": "Rendered.",
              "import_path": "/tmp/out.mp4"}
    status.update(fields)
    write_status(root, **status)
    H.click("RefreshStatusBtn")


def test_the_render_lines_up_on_a_new_track_above_the_original(lua, direct):
    H = load(lua, ui_script(direct))
    finish_render(H, direct)

    assert len(H.placements) == 1
    placed = H.placements[1]
    assert placed.trackIndex == 2
    assert placed.recordFrame == 86400, "it does not start where the original does"
    # Resolve 20.3 reads endFrame as one past the last frame, so 0 to 48 is
    # all 48 frames. 0 to 47 left the original showing on the last one.
    assert (placed.startFrame, placed.endFrame) == (0, 48)
    assert placed.mediaType == 1
    assert H.track_names[2] == "Upflow2x"
    assert "V2 above the original" in H.status()


def test_a_finished_render_message_fits_the_status_box(lua, direct):
    """In Resolve its three lines were cut off at the top and bottom of a
    two-line box."""
    H = load(lua, ui_script(direct))
    finish_render(H, direct, message="Rendered 25 drawings into 337 smooth frames.")
    status = H.widgets["Status"]
    assert status.MinimumSize[2] >= -(-len(status.Text) // 56) * 15, "the box is shorter than its lines"
    assert status.ToolTip == status.Text, "a longer message has no other way to be read"


def test_a_free_track_above_is_used_as_it_is(lua, direct):
    H = load(lua, ui_script(direct))
    lua.execute("local H = ...; H.tracks[2] = {}; H.tracks[3] = {}", H)
    finish_render(H, direct)
    assert H.placements[1].trackIndex == 2
    assert len(H.tracks) == 3, "a track was added though one was free"
    assert H.track_names[2] is None, "the user's track was renamed"


def test_a_track_with_a_clip_in_the_way_is_skipped(lua, direct):
    H = load(lua, ui_script(direct))
    # Track 2 has a clip overlapping the span, track 3 one well after it.
    lua.execute("local H = ...; H.tracks[2] = { { 86440, 86500 } }; "
                "H.tracks[3] = { { 90000, 90100 } }", H)
    finish_render(H, direct)
    assert H.placements[1].trackIndex == 3


def test_a_sequence_is_placed_with_its_own_length(lua, direct):
    H = load(lua, ui_script(direct))
    finish_render(H, direct, import_path="/tmp/out/frame_000000.png",
                  sequence_pattern="/tmp/out/frame_%06d.png", sequence_frames=118, fps=60.0)
    assert H.placements[1].endFrame == 118


def test_without_the_placement_calls_it_goes_at_the_end(lua, direct):
    H = load(lua, ui_script(direct))
    H.no_place_api = True
    follow(H)
    finish_render(H, direct)
    assert len(H.placements) == 0
    assert H.appended == 1
    assert "end of the timeline" in H.status()


def test_a_different_timeline_gets_the_render_at_its_end(lua, direct):
    """Lined up against another timeline, the frame numbers mean nothing."""
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    H.timeline_id = "another-timeline"
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path="/tmp/out.mp4")
    H.click("RefreshStatusBtn")
    assert len(H.placements) == 0
    assert "end of the timeline" in H.status()


def test_a_refused_placement_removes_its_track_and_appends(lua, direct):
    H = load(lua, ui_script(direct))
    H.place_works = False
    finish_render(H, direct)
    assert len(H.tracks) == 1, "the empty track it added was left behind"
    assert H.appended == 1
    assert "end of the timeline" in H.status()


def test_a_source_moved_during_the_render_is_followed(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    lua.execute("local H = ...; H.src_span[1] = 86500; H.src_span[2] = 86548", H)
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path="/tmp/out.mp4")
    H.click("RefreshStatusBtn")
    assert H.placements[1].recordFrame == 86500


def test_a_source_retrimmed_during_the_render_gets_the_render_at_the_end(lua, direct):
    """Lined up with a different cut of the clip, it would not match it."""
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    lua.execute("local H = ...; H.src_span[2] = 86430", H)
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path="/tmp/out.mp4")
    H.click("RefreshStatusBtn")
    assert len(H.placements) == 0
    assert "end of the timeline" in H.status()


def test_a_source_deleted_during_the_render_gets_the_render_at_the_end(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    lua.execute("local H = ...; table.remove(H.tracks[1], 1)", H)
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path="/tmp/out.mp4")
    H.click("RefreshStatusBtn")
    assert len(H.placements) == 0
    assert H.appended == 1


def test_a_render_longer_than_the_source_is_cut_to_it(lua, direct):
    """Lined up over a 48 frame clip, the rest of a 120 frame render would
    cover the next shot."""
    H = load(lua, ui_script(direct))
    H.media_frames = "120"
    lua.execute("local H = ...; H.tracks[2] = { { 86460, 86480 } }", H)
    finish_render(H, direct)
    assert H.placements[1].endFrame == 48
    assert H.placements[1].trackIndex == 2


def test_a_render_a_frame_longer_than_the_source_ends_with_it(lua, direct):
    """Resolve can report a clip's last frame one past where it ends, so the
    render has a frame more. Seen in Resolve 20.3.1."""
    H = load(lua, ui_script(direct))
    H.media_frames = "49"
    finish_render(H, direct)
    assert H.placements[1].endFrame == 48
    assert H.tracks[2][1][2] == 86448, "it runs past the original"


def _with_source(lua, direct, **fields):
    H = load(lua, ui_script(direct))
    sets = "; ".join(f"H.{k} = {str(v).lower() if isinstance(v, bool) else repr(v)}"
                     for k, v in fields.items())
    lua.execute(f"local H = ...; H.tracks = nil; H.src_span = nil; {sets}", H)
    follow(H)
    return H


def test_a_slower_render_on_a_faster_timeline_ends_inside_the_original(lua, direct):
    """20 frames at 24 fps last 50 frames of a 60 fps timeline, two past a
    48 frame original, so 19 are placed."""
    H = _with_source(lua, direct, timeline_fps="60")
    finish_render(H, direct, import_path="/tmp/out/frame_000000.png",
                  sequence_pattern="/tmp/out/frame_%06d.png", sequence_frames=20, fps=24.0)
    assert H.placements[1].endFrame == 19
    assert H.tracks[2][1][2] <= 86448, "it runs into the next shot"
    assert "past it" not in H.status()


def test_a_long_ntsc_render_fills_the_original(lua, direct):
    """23.976 is 24000/1001. Counted with 23.976, 999 frames came out one
    short of a 999 frame original on a 24 fps timeline."""
    H = _with_source(lua, direct, item_duration=999)
    finish_render(H, direct, import_path="/tmp/out/frame_000000.png",
                  sequence_pattern="/tmp/out/frame_%06d.png", sequence_frames=999, fps=23.976)
    assert H.placements[1].endFrame == 999
    assert H.tracks[2][1][2] == 86400 + 999


def test_a_rate_near_ntsc_is_taken_as_it_is(lua, direct):
    """23.98 is not 24000/1001. Taken for it, 1000 frames were cut to 999
    over a 1000 frame original, a frame short."""
    H = _with_source(lua, direct, item_duration=1000)
    finish_render(H, direct, import_path="/tmp/out/frame_000000.png",
                  sequence_pattern="/tmp/out/frame_%06d.png", sequence_frames=1000, fps=23.98)
    assert H.placements[1].endFrame == 1000
    assert H.tracks[2][1][2] == 86400 + 1000


def test_a_placement_that_runs_past_the_original_says_so(lua, direct):
    """A Resolve that rounds lengths to nearest, not down, places 47.6
    timeline frames as 48 over a 47 frame original."""
    H = _with_source(lua, direct, item_duration=47, place_rounds=True)
    finish_render(H, direct, import_path="/tmp/out/frame_000000.png",
                  sequence_pattern="/tmp/out/frame_%06d.png", sequence_frames=120, fps=60.0)
    assert H.placements[1].endFrame == 119
    assert "runs 1 frame past it" in H.status()


def test_a_slowed_render_is_placed_at_its_full_length(lua, direct):
    """Three times longer than the original, so none of it is cut to fit."""
    H = load(lua, ui_script(direct))
    H.media_frames = "144"
    finish_render(H, direct, slow_motion=3)
    assert H.placements[1].endFrame == 144
    assert "past it" not in H.status()


def test_a_slowed_render_at_another_rate_is_placed_whole(lua, direct):
    """119 frames at 60 fps cover 47 frames of a 24 fps timeline. Three times
    that, rounded, cut three of the render's 357 frames off its end."""
    H = _with_source(lua, direct, item_duration=47)
    finish_render(H, direct, import_path="/tmp/out/frame_000000.png",
                  sequence_pattern="/tmp/out/frame_%06d.png", sequence_frames=357, fps=60.0,
                  slow_motion=3)
    assert H.placements[1].endFrame == 357
    assert "past it" not in H.status()


def test_a_slowed_render_cannot_be_compared(lua, direct):
    """It runs longer than the original, so switched off it shows whatever
    comes after the original instead. The render says how much slower it is,
    since a panel opened again while it ran is back on Same as clip."""
    H = load(lua, ui_script(direct))
    H.media_frames = "144"
    finish_render(H, direct, slow_motion=3)
    assert not H.enabled("CompareBtn")


def test_a_render_a_frame_short_is_placed_whole(lua, direct):
    H = load(lua, ui_script(direct))
    H.media_frames = "47"
    finish_render(H, direct)
    assert H.placements[1].endFrame == 47


def test_a_sequence_at_an_unconfirmed_rate_stays_in_the_media_pool(lua, direct):
    """At the wrong rate it plays at the wrong speed and covers more timeline."""
    H = load(lua, ui_script(direct))
    H.fps_sticks = False
    finish_render(H, direct, import_path="/tmp/out/frame_000000.png",
                  sequence_pattern="/tmp/out/frame_%06d.png", sequence_frames=120, fps=60.0)
    assert H.appended == 0
    assert "Clip Attributes" in H.status() and "media pool" in H.status()


def test_a_failed_track_rename_does_not_add_a_second_copy(lua, direct):
    H = load(lua, ui_script(direct))
    H.rename_throws = True
    finish_render(H, direct)
    assert H.appended == 1
    assert "above the original" in H.status()


def test_a_placement_that_raises_removes_its_track_and_appends_once(lua, direct):
    H = load(lua, ui_script(direct))
    H.place_throws = True
    finish_render(H, direct)
    assert len(H.tracks) == 1, "the empty track it added was left behind"
    assert H.appended == 1
    assert "end of the timeline" in H.status()


def _stacked(lua, direct, top, *under):
    """`top` under the playhead, with `under` lined up below it on tracks 1
    and up, as an original and earlier renders are."""
    H = load(lua, ui_script(direct), clip=top)
    paths = ", ".join("'" + p.replace("\\", "\\\\") + "'" for p in under)
    lua.execute(f"local H = ...; H.tracks = nil; H.src_span = nil; "
                f"H.item_track = {len(under) + 1}; H.under_paths = {{ {paths} }}", H)
    follow(H)
    return H


def test_a_render_over_its_original_is_not_smoothed_again(lua, direct):
    """The render placed above is the top clip under the playhead from then
    on, so smoothing again with other settings smoothed the render. Seen in
    Resolve 20.3.1."""
    H = _stacked(lua, direct, "/r/shot_f7-73_smooth_02/frame_[000000-000047].png",
                 "/media/shot.mov")
    assert "shot.mov" in H.text_of("SourceLabel")
    H.click("AnalyseBtn")
    assert "/media/shot.mov" in H.last_command()
    assert "shot_f7-73" not in H.last_command()


def test_a_slowed_render_over_its_original_is_skipped_too(lua, direct):
    H = _stacked(lua, direct, "/r/shot_f7-73_3x_smooth/frame_[000000-000143].png",
                 "/media/shot.mov")
    H.click("AnalyseBtn")
    assert "/media/shot.mov" in H.last_command()


def test_a_single_file_render_over_its_original_is_skipped_too(lua, direct):
    H = _stacked(lua, direct, "/r/shot_smooth.mkv", "/media/shot.mov")
    H.click("AnalyseBtn")
    assert "/media/shot.mov" in H.last_command()


def _under_a_clip(lua, direct, setup=""):
    """B.mov on V2 over A.mov on V1, the playhead ten frames in."""
    H = load(lua, ui_script(direct), clip="/media/B.mov")
    lua.execute("local H = ...; H.tracks = nil; H.src_span = nil; H.item_track = 2; "
                "H.under_paths = { '/media/A.mov' }; H.playhead_tc = '01:00:00:10'; " + setup, H)
    follow(H)
    return H


def test_a_clip_switched_off_is_passed_over(lua, direct):
    """GetCurrentVideoItem returns the top clip even switched off, as measured
    in Resolve 20.3.1, so the panel took a clip the viewer did not show."""
    H = _under_a_clip(lua, direct, "H.item_off = true")
    assert "A.mov" in H.text_of("SourceLabel")
    H.click("AnalyseBtn")
    assert "/media/A.mov" in H.last_command()


def test_a_clip_on_a_track_switched_off_is_passed_over(lua, direct):
    H = _under_a_clip(lua, direct, "H.tracks_off = { [2] = true }")
    assert "A.mov" in H.text_of("SourceLabel")


def test_the_top_clip_is_used_while_it_shows(lua, direct):
    H = _under_a_clip(lua, direct)
    assert "B.mov" in H.text_of("SourceLabel")


def test_the_playhead_counts_whole_frames_on_ntsc_timelines(lua, direct):
    """Timecode on a 23.976 timeline numbers 24 frames a second. Taken as
    23.976, an hour in lands 86 frames early, outside the clip."""
    H = _under_a_clip(lua, direct, "H.item_off = true; H.timeline_fps = '23.976'")
    assert "A.mov" in H.text_of("SourceLabel")


def test_the_playhead_is_found_in_drop_frame_timecode(lua, direct):
    """01:00:00;00 is frame 107892 at 29.97 drop-frame, not 108000."""
    H = _under_a_clip(lua, direct, "H.item_off = true; H.timeline_fps = '29.97'; "
                                   "H.item_start = 107892; H.playhead_tc = '01:00:00;10'")
    assert "A.mov" in H.text_of("SourceLabel")


def test_the_third_smooth_skips_both_earlier_renders(lua, direct):
    """Two runs leave renders on V2 and V3. The third took the V2 render."""
    H = _stacked(lua, direct, "C:\\r\\shot_f0-47_smooth_02.mkv",
                 "C:\\media\\shot.mov", "C:\\r\\shot_f0-47_smooth.mkv")
    H.click("SmoothBtn")
    assert "shot.mov" in H.last_command()
    assert "_f0-47_smooth" not in H.last_command()


def test_a_clip_that_is_not_a_render_is_used_as_it_is(lua, direct):
    H = _stacked(lua, direct, "/media/smooth_jazz.mov", "/media/shot.mov")
    H.click("AnalyseBtn")
    assert "/media/smooth_jazz.mov" in H.last_command()


def test_a_file_named_like_a_render_of_another_clip_is_used(lua, direct):
    """Only a render named after the clip under it stands for that clip."""
    for top in ("/media/camera_smooth123.mov", "/media/camera_smooth.mp4",
                "/media/camera_smooth_02/frame_000000.png"):
        H = _stacked(lua, direct, top, "/media/background.mov")
        H.click("AnalyseBtn")
        assert top.split("/")[2] in H.last_command(), top
        assert "background.mov" not in H.last_command(), top


def test_a_render_with_nothing_under_it_is_used(lua, direct):
    """Dragged somewhere else, or on its own timeline: it is all there is."""
    H = load(lua, ui_script(direct), clip="/r/shot_smooth/frame_[000000-000047].png")
    H.click("AnalyseBtn")
    assert "shot_smooth" in H.last_command()


def test_compare_switches_the_smoothed_clip_off_and_on(lua, direct):
    H = load(lua, ui_script(direct))
    assert not H.enabled("CompareBtn"), "Compare works before there is anything to compare"
    finish_render(H, direct)
    assert H.enabled("CompareBtn")

    H.click("CompareBtn")
    assert H.last_placed.enabled is False
    assert H.widgets["CompareBtn"].Text == "Show Upflow2x"
    assert "original" in H.status()
    H.click("CompareBtn")
    assert H.last_placed.enabled is True
    assert H.widgets["CompareBtn"].Text == "Show Original"


def test_compare_turns_off_when_a_new_run_starts(lua, direct):
    H = load(lua, ui_script(direct))
    finish_render(H, direct)
    H.click("SmoothBtn")
    assert not H.enabled("CompareBtn")


def test_show_in_folder_points_at_the_render(lua, direct):
    H = load(lua, ui_script(direct))
    assert not H.enabled("FolderBtn")
    finish_render(H, direct, import_path="/tmp/renders/out.mp4")
    H.click("FolderBtn")
    cmd = H.last_command()
    assert "/tmp/renders/out.mp4" in cmd.replace("\\", "/")
    assert "explorer /select," in cmd or "open -R" in cmd


def test_show_in_folder_opens_a_sequence_by_its_folder(lua, direct):
    H = load(lua, ui_script(direct))
    finish_render(H, direct, import_path="/tmp/renders/shot_smooth/frame_000000.png",
                  sequence_pattern="/tmp/renders/shot_smooth/frame_%06d.png",
                  sequence_frames=24, fps=24.0)
    H.click("FolderBtn")
    cmd = H.last_command().replace("\\", "/")
    assert "/tmp/renders/shot_smooth" in cmd and "frame_000000" not in cmd


def test_show_in_folder_works_after_a_refused_import(lua, direct):
    H = load(lua, ui_script(direct))
    H.import_works = False
    finish_render(H, direct, import_path="/tmp/renders/out.mp4")
    assert H.enabled("FolderBtn")


def test_a_timeline_too_small_for_the_upscale_says_so(lua, direct):
    H = load(lua, ui_script(direct))
    finish_render(H, direct)
    assert "3840x2160" in H.widgets["Meta"].Text


def test_a_big_enough_timeline_gets_no_upscale_tip(lua, direct):
    H = load(lua, ui_script(direct))
    H.timeline_width = "3840"
    finish_render(H, direct)
    assert "3840x2160" not in H.widgets["Meta"].Text


def test_no_upscale_tip_when_upscale_is_off(lua, direct):
    H = load(lua, ui_script(direct))
    H.set_checkbox("UpscaleCB", False)
    finish_render(H, direct)
    assert "3840x2160" not in H.widgets["Meta"].Text


def test_the_time_left_is_estimated_from_the_pace(lua, direct):
    H = load(lua, ui_script(direct))
    lua.execute("CLOCK = 1000; os.time = function() return CLOCK end")
    write_status(direct, stage="Rendering", fraction=0.1)
    H.click("RefreshStatusBtn")
    assert "left" not in H.widgets["Progress"].Text, "an estimate from one reading"
    lua.execute("CLOCK = 1010")
    write_status(direct, stage="Rendering", fraction=0.3)
    H.click("RefreshStatusBtn")
    # 0.2 of the work in 10 s leaves 0.7 for 35 s.
    assert "about 35 s left" in H.widgets["Progress"].Text


def test_a_new_stage_restarts_the_estimate(lua, direct):
    H = load(lua, ui_script(direct))
    lua.execute("CLOCK = 1000; os.time = function() return CLOCK end")
    write_status(direct, stage="Seeking", fraction=0.1)
    H.click("RefreshStatusBtn")
    lua.execute("CLOCK = 1010")
    write_status(direct, stage="Rendering", fraction=0.3)
    H.click("RefreshStatusBtn")
    assert "left" not in H.widgets["Progress"].Text


def test_super_scale_is_applied_with_a_value_resolve_accepts(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path="/tmp/out.mp4")
    H.click("RefreshStatusBtn")
    assert H.property_of("Super Scale") == "2"


def test_the_panel_asks_for_the_network_upscale_only_when_ticked(lua, direct):
    """The stage only draws at 2x for a panel that then leaves Super Scale off."""
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    assert "--network-upscale" in H.last_command()
    H.set_checkbox("UpscaleCB", False)
    H.click("SmoothBtn")
    assert "--network-upscale" not in H.last_command()


def test_a_render_drawn_at_2x_gets_no_super_scale(lua, direct):
    """Real-ESRGAN drew it at 2x. Super Scale on top would make it 4x."""
    H = load(lua, ui_script(direct))
    finish_render(H, direct, upscaled=True)
    assert H.property_of("Super Scale") is None
    assert "upscaled 2x" in H.status()


def test_a_render_drawn_at_2x_asks_for_a_timeline_its_own_size(lua, direct):
    H = load(lua, ui_script(direct))
    H.media_resolution = "3840x2160"
    finish_render(H, direct, upscaled=True)
    assert "set the timeline to 3840x2160" in H.widgets["Meta"].Text


def test_a_render_drawn_at_2x_on_a_timeline_its_size_gets_no_tip(lua, direct):
    H = load(lua, ui_script(direct))
    H.media_resolution, H.timeline_width = "3840x2160", "3840"
    finish_render(H, direct, upscaled=True)
    assert "set the timeline" not in H.widgets["Meta"].Text


def test_upscale_unchecked_leaves_super_scale_alone(lua, direct):
    H = load(lua, ui_script(direct))
    H.set_checkbox("UpscaleCB", False)
    H.click("SmoothBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path="/tmp/out.mp4")
    H.click("RefreshStatusBtn")
    assert H.property_of("Super Scale") is None


def test_a_refused_import_still_tells_the_user_where_the_render_is(lua, direct):
    H = load(lua, ui_script(direct))
    H.import_works = False
    H.click("SmoothBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path="/tmp/renders/out.mp4")
    H.click("RefreshStatusBtn")

    status = H.status()
    # The status box fits two lines, so the path goes on the line below it.
    assert "would not import" in status
    assert H.widgets["Meta"].Text == "/tmp/renders/out.mp4"
    assert "drag it in" in status.lower()
    assert H.enabled("SmoothBtn"), "the panel stayed busy after a failed import"


def test_a_refused_sequence_is_pointed_at_by_its_folder(lua, direct):
    """The first frame's name alone, frame_000000.png, says nothing."""
    H = load(lua, ui_script(direct))
    H.import_works = False
    H.click("SmoothBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path="/tmp/renders/shot_smooth/frame_000000.png",
                 sequence_pattern="/tmp/renders/shot_smooth/frame_%06d.png",
                 sequence_frames=24, fps=24.0)
    H.click("RefreshStatusBtn")
    assert H.widgets["Meta"].Text == "/tmp/renders/shot_smooth"


def test_the_import_happens_once_not_on_every_poll(lua, direct):
    """Polling repeatedly must not append the clip again and again."""
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path="/tmp/out.mp4")
    for _ in range(4):
        H.click("RefreshStatusBtn")
    assert H.imported_count() == 1
    assert H.appended == 1


def test_a_failed_run_imports_nothing(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    write_status(direct, done=True, ok=False, message="The clip's media is missing.")
    H.click("RefreshStatusBtn")
    assert H.imported_count() == 0


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX shell quoting")
@pytest.mark.parametrize("name", ["Épisode $HOME 1.mov", "it's `date`.mov", "a\\b \"q\".mov"])
def test_odd_clip_names_reach_python_unchanged(lua, direct, tmp_path, name):
    """$ and backticks expanded inside the old double quotes, and a quote
    ended the path early."""
    import subprocess
    clip = f"/media/{name}"
    H = load(lua, ui_script(direct), clip=clip)
    H.click("AnalyseBtn")
    cmd = H.last_command()
    quoted = cmd.split("--video ")[1].split(" --format")[0]
    seen = subprocess.run(["sh", "-c", f"printf %s {quoted}"],
                          capture_output=True, text=True).stdout
    assert seen == clip


@pytest.mark.parametrize("path", ["/tmp/Épisode 1/frame_000000.png",
                                  'C:\\clips\\a "b"\\out.mp4'])
def test_the_import_path_is_decoded_before_resolve_sees_it(lua, direct, path):
    """The JSON escapes used to reach ImportMedia, which then found nothing."""
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path=path)
    H.click("RefreshStatusBtn")
    assert H.imported_at(1) == path


def test_a_refused_super_scale_is_not_reported_as_done(lua, direct):
    """Resolve says no by returning false, not by raising, on the free edition."""
    H = load(lua, ui_script(direct))
    H.super_scale_works = False
    H.click("SmoothBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path="/tmp/out.mp4")
    H.click("RefreshStatusBtn")
    assert "upscaled 2x" not in H.status()
    assert "upscale not applied" in H.status()


# LuaJIT's ffi as the panel uses it, recording what CreateProcessW is given.
# The tests' Lua has no ffi of its own.
FAKE_FFI = r'''
CREATED = {}
package.preload.ffi = function()
    local ffi = {}
    function ffi.typeof(name) error("no such type: " .. name) end
    function ffi.cdef(text) end
    function ffi.sizeof(x) return 104 end
    function ffi.new(kind) return { [0] = 0 } end
    function ffi.load(name)
        return {
            MultiByteToWideChar = function(page, flags, s, n, out)
                if out then out.text = s end
                return n
            end,
            CreateProcessW = function(app, line, pa, ta, inherit, flags, env, cwd, si, pi)
                CREATED[#CREATED + 1] = { line = line.text, flags = flags,
                                          cwd = cwd and cwd.text }
                return REFUSE and 0 or 1
            end,
            WaitForSingleObject = function()
                if WAIT_FAILS then error("wait failed") end
                return 0
            end,
            GetExitCodeProcess = function(process, code) code[0] = 0 return 1 end,
            CloseHandle = function() CLOSED = (CLOSED or 0) + 1 return 1 end,
        }
    end
    return ffi
end
'''


def test_windows_commands_start_without_a_console_window(lua, direct):
    """os.execute flashed a console window up on every update check. The same
    command line now goes to CreateProcessW with CREATE_NO_WINDOW."""
    lua.execute('package.config = "\\\\" .. package.config:sub(2)')
    lua.execute(FAKE_FFI)
    H = load(lua, ui_script(direct))
    H.click("AnalyseBtn")
    first = lua.eval("CREATED")[1]
    assert first.flags == 0x08000000
    assert not any("start" in c for c in H.commands.values()), "os.execute ran it as well"


def test_the_windows_run_opens_no_window(lua, direct):
    """The render's minimised window could be opened and closed, which ended
    the render. It now starts with no window, from the plugin folder."""
    lua.execute('package.config = "\\\\" .. package.config:sub(2)')
    lua.execute(FAKE_FFI)
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    created = lua.eval("CREATED")
    last = created[len(created)]
    assert last.flags == 0x08000000
    assert last.line.endswith(" /c .\\" + run_file(direct, "script").name), last.line
    assert "start" not in last.line and "/MIN" not in last.line, last.line
    assert last.cwd == str(direct).replace("/", "\\"), last.cwd
    assert not any("start" in c for c in H.commands.values()), "os.execute ran it as well"


def test_a_command_that_started_never_runs_twice(lua, direct):
    """A failure while waiting for the started command fell through to
    os.execute, which ran the command again."""
    lua.execute('package.config = "\\\\" .. package.config:sub(2)')
    lua.execute(FAKE_FFI + "\nWAIT_FAILS = true")
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    assert not any("start" in c for c in H.commands.values()), "os.execute ran it again"
    assert lua.eval("CLOSED") == 2 * len(lua.eval("CREATED")), "a process handle was left open"


def test_without_create_process_the_command_still_runs(lua, direct):
    lua.execute('package.config = "\\\\" .. package.config:sub(2)')
    lua.execute(FAKE_FFI + "\nREFUSE = true")
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    assert H.last_command().startswith('start "Upflow2x" /D ')


def test_the_windows_background_run_goes_through_a_script(lua, direct):
    """An & in a path split the old nested `cmd /c "..."` command.

    Pretends to be Windows by changing the directory separator Lua reports,
    then checks the script the panel writes instead of a nested command.
    """
    lua.execute('package.config = "\\\\" .. package.config:sub(2)')
    H = load(lua, ui_script(direct), clip="C:/clips/Tom & Jerry 100%.mov")
    H.click("SmoothBtn")
    cmd = H.last_command()
    outer = cmd.split("\n")[0]
    assert outer.startswith('start "Upflow2x" /D "'), outer
    assert outer.endswith(" /MIN cmd /c .\\" + run_file(direct, "script").name), outer
    assert "/" not in outer.split("/D ")[1].split(" /MIN")[0], outer
    assert "Tom & Jerry" not in outer, "the clip path must not be on the outer command line"

    script = run_file(direct, "script").read_bytes()
    assert b"\r\r\n" not in script
    text = script.decode("utf-8")
    assert text.startswith("@echo off\r\nchcp 65001 >nul\r\n")
    assert '"C:/clips/Tom & Jerry 100%%.mov"' in text, "the path must be quoted and % doubled"
    assert "2>&1" in text


# --------------------------------------------------------------------------
# reaching Resolve the way a real Scripts-menu run does
# --------------------------------------------------------------------------

def test_the_panel_reaches_resolve_without_a_lua_module(lua, direct):
    """Resolve gives Lua a Resolve() global. There is no DaVinciResolveScript.lua."""
    H = load(lua, ui_script(direct), clip="/media/shot.mov")
    assert lua.eval('package.preload["DaVinciResolveScript"]') is None
    assert "shot.mov" in H.widgets["SourceLabel"].Text


def test_the_panel_uses_a_resolve_global_when_there_is_one(lua, direct):
    H = lua.eval(f'dofile("{HARNESS.as_posix()}")')
    lua.execute('resolve = nil')
    H.load(str(ui_script(direct)), "/media/first.mov")
    lua.execute('resolve = Resolve(); Resolve = nil')
    follow(H)
    assert "first.mov" in H.widgets["SourceLabel"].Text


def test_no_clip_under_the_playhead_is_explained(lua, direct):
    H = lua.eval(f'dofile("{HARNESS.as_posix()}")')
    H.under_playhead = False
    H.load(str(ui_script(direct)), "/media/shot.mov")
    assert "playhead" in H.widgets["SourceLabel"].Text


def test_a_throwing_api_does_not_stop_the_panel_opening(lua, direct):
    """The clip is read while the panel opens, where an error would kill it."""
    H = lua.eval(f'dofile("{HARNESS.as_posix()}")')
    H.make_resolve = lua.eval(
        'function() return { GetProjectManager = function() error("no access") end } end')
    H.load(str(ui_script(direct)), "/media/shot.mov")
    assert H.window.__shown
    assert "no access" in H.widgets["SourceLabel"].Text


# --------------------------------------------------------------------------
# a run that dies is reported, not waited on forever
# --------------------------------------------------------------------------

def test_a_run_that_died_without_a_status_is_reported(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    run_file(direct, "log").write_text(
        "Traceback (most recent call last):\nModuleNotFoundError: No module named 'cv2'\n")
    run_file(direct, "exit").write_text("1\n")
    H.click("RefreshStatusBtn")
    assert "No module named 'cv2'" in H.status()
    assert H.enabled("SmoothBtn"), "the panel stayed busy after the run died"




def die(H, root, log="Traceback (most recent call last):\nRuntimeError: boom\n"):
    """A render whose Python exits without saying it is done."""
    H.click("SmoothBtn")
    run_file(root, "log").write_text(log)
    run_file(root, "exit").write_text("1\n")
    H.click("RefreshStatusBtn")


def test_a_run_that_died_offers_its_log(lua, direct):
    """The log sits in a hidden folder, and its path took two of the status
    box's three lines, so nobody could find it to send it in."""
    H = load(lua, ui_script(direct))
    assert H.text_of("FolderBtn") == "Show in Folder" and not H.enabled("FolderBtn")
    die(H, direct)
    assert H.text_of("FolderBtn") == "Open Log" and H.enabled("FolderBtn")
    assert H.status() == "Stopped without finishing: RuntimeError: boom Open Log shows the rest."
    assert run_file(direct, "log").name in H.widgets["FolderBtn"].ToolTip
    H.click("FolderBtn")
    cmd = H.last_command().replace("\\", "/")
    assert run_file(direct, "log").as_posix() in cmd
    assert 'start "" notepad ' in cmd or "open -t " in cmd


def test_the_log_opens_in_notepad_on_windows(lua, direct):
    lua.execute('package.config = "\\\\" .. package.config:sub(2)')
    H = load(lua, ui_script(direct))
    die(H, direct)
    H.click("FolderBtn")
    script = (direct / ".eternal2x_reveal.cmd").read_bytes().decode("utf-8")
    line = script.split("\r\n")[2]
    assert line.startswith('start "" notepad "'), line
    assert line.endswith(run_file(direct, "log").name + '"'), line


def test_the_log_opens_in_a_text_editor_on_a_mac(lua, direct):
    """Finder hides a file whose name starts with a dot, so open -R found nothing."""
    lua.execute('package.config = "/" .. package.config:sub(2)')
    H = load(lua, ui_script(direct))
    die(H, direct)
    H.click("FolderBtn")
    assert H.last_command().startswith("open -t '")
    assert "open -R" not in H.last_command()


def test_a_run_that_failed_with_a_message_offers_its_log(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    run_file(direct, "log").write_text("Traceback (most recent call last):\nValueError: odd\n")
    write_status(direct, done=True, ok=False, message="Upflow2x hit an unexpected error: odd")
    H.click("RefreshStatusBtn")
    assert H.text_of("FolderBtn") == "Open Log"
    assert H.status() == "Upflow2x hit an unexpected error: odd"


def test_a_stop_does_not_offer_the_log(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    run_file(direct, "log").write_text("Stopped.\n")
    write_status(direct, done=True, ok=False, message="Stopped.")
    H.click("RefreshStatusBtn")
    assert H.text_of("FolderBtn") == "Show in Folder" and not H.enabled("FolderBtn")


def test_an_empty_log_is_not_offered(lua, direct):
    H = load(lua, ui_script(direct))
    die(H, direct, log="")
    assert H.text_of("FolderBtn") == "Show in Folder" and not H.enabled("FolderBtn")
    assert H.status() == "Stopped without finishing: Python exited with code 1."


def test_the_next_run_puts_show_in_folder_back(lua, direct):
    H = load(lua, ui_script(direct))
    die(H, direct)
    finish_render(H, direct, import_path="/tmp/renders/out.mp4")
    assert H.text_of("FolderBtn") == "Show in Folder"
    H.click("FolderBtn")
    assert "/tmp/renders/out.mp4" in H.last_command().replace("\\", "/")
    assert "notepad" not in H.last_command() and "open -t" not in H.last_command()


def test_a_run_still_going_is_not_reported_as_dead(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    write_status(direct, fraction=0.4)
    H.click("RefreshStatusBtn")
    assert not H.enabled("SmoothBtn")
    assert "40%" in H.widgets["Progress"].Text


def test_a_finished_run_that_exited_is_not_taken_for_a_crash(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path="/tmp/out.mp4")
    run_file(direct, "exit").write_text("0\n")
    H.click("RefreshStatusBtn")
    assert "Stopped without finishing" not in H.status()
    assert "above the original" in H.status()


def test_starting_a_run_clears_the_last_exit_code(lua, direct):
    old_exit = run_file(direct, "exit")
    old_exit.write_text("1\n")
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    assert not old_exit.exists()
    assert not run_file(direct, "exit").exists()


def test_the_background_command_records_how_python_exited(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    cmd = H.last_command()
    if sys.platform.startswith("win"):
        return  # the POSIX form; the Windows script is checked below
    assert cmd.startswith("(") and cmd.endswith(") &"), cmd
    assert "echo $? > " in cmd and run_file(direct, "exit").name in cmd


def test_the_windows_script_records_how_python_exited(lua, direct):
    lua.execute('package.config = "\\\\" .. package.config:sub(2)')
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    text = run_file(direct, "script").read_bytes().decode("utf-8")
    last = text.rstrip("\r\n").split("\r\n")[-1]
    # Redirect first: `echo 2> file` would redirect stderr instead of writing 2.
    assert last.startswith(">") and last.endswith(" echo %ERRORLEVEL%"), last
    assert run_file(direct, "exit").name in last


# --------------------------------------------------------------------------
# image sequences are imported as sequences
# --------------------------------------------------------------------------

def test_a_sequence_is_imported_with_the_documented_clipinfo(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path="/tmp/out/frame_000000.png",
                 sequence_pattern="/tmp/out/frame_%06d.png", sequence_frames=24)
    H.click("RefreshStatusBtn")
    assert H.sequence_import_count() == 1
    assert H.last_sequence_field("FilePath") == "/tmp/out/frame_%06d.png"
    assert H.last_sequence_field("StartIndex") == 0
    assert H.last_sequence_field("EndIndex") == 23
    assert H.imported_count() == 1, "the first frame was imported as well"
    assert "above the original" in H.status()


def test_a_refused_sequence_import_is_not_swapped_for_a_still(lua, direct):
    """Handed its first frame instead, Resolve imports one still image, which
    the panel would append and call done."""
    H = load(lua, ui_script(direct))
    H.sequence_import_works = False
    H.click("SmoothBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path="/tmp/out/frame_000000.png",
                 sequence_pattern="/tmp/out/frame_%06d.png", sequence_frames=24)
    H.click("RefreshStatusBtn")
    assert H.imported_count() == 0 and H.appended == 0
    assert "would not import" in H.status() and H.widgets["Meta"].Text == "/tmp/out"


# --------------------------------------------------------------------------
# the clip is read when the button is pressed
# --------------------------------------------------------------------------

def test_smooth_uses_the_clip_under_the_playhead_now(lua, direct):
    H = load(lua, ui_script(direct), clip="/media/first.mov")
    lua.execute('Resolve = (function(make) return function() return make("/media/second.mov") end end)(...)',
                lua.eval('function(H) return H.make_resolve end')(H))
    H.click("SmoothBtn")
    assert "/media/second.mov" in H.last_command()


def test_smooth_keeps_the_last_clip_when_the_playhead_is_in_a_gap(lua, direct):
    H = load(lua, ui_script(direct), clip="/media/first.mov")
    H.under_playhead = False
    H.click("SmoothBtn")
    assert "/media/first.mov" in H.last_command()
    assert H.text_of("SourceLabel") == "first.mov\nLast usable clip"


# --------------------------------------------------------------------------
# update checks report their verdict
# --------------------------------------------------------------------------

def updater_writes(direct, text):
    """Play the updater: write its log when the panel runs it."""
    def run(cmd):
        if "Stages.resolve_update" in cmd:
            (direct / ".eternal2x_update.log").write_text(text)
    return run


def test_a_failed_update_is_not_reported_as_updating(lua, direct):
    H = load(lua, ui_script(direct))
    H.exit_code = 1
    H.on_execute = updater_writes(
        direct, "Updating Eternal2x: 0.3.0 -> 0.3.2\nUpdate package checksum mismatch.\n")
    H.click("UpdateBtn")
    assert H.status() == "Update package checksum mismatch."


def test_opening_the_panel_keeps_the_render_log(lua, direct):
    """The update check runs on open. The render log is the one users send in."""
    conf = direct / "Installer" / "Eternal2x.conf"
    conf.write_text(conf.read_text().replace("auto_update=false", "auto_update=true"))
    H = load(lua, ui_script(direct))
    cmd = H.last_command()
    assert "Stages.resolve_update" in cmd
    assert ".eternal2x_update.log" in cmd and run_file(direct, "log").name not in cmd


@pytest.mark.parametrize("old", [
    "https://raw.githubusercontent.com/Alitleis123/Eternal2x.com/main/update/latest.json",
    "https://raw.githubusercontent.com/sidebandstudio/Eternal2x.com/main/update/latest.json",
])
def test_an_install_set_up_with_an_old_feed_checks_the_new_one(lua, direct, old):
    """Installs keep the feed they were set up with through every update."""
    import re
    conf = direct / "Installer" / "Eternal2x.conf"
    conf.write_text(re.sub(r"update_url=.*", "update_url=" + old, conf.read_text()))
    H = load(lua, ui_script(direct))
    H.click("UpdateBtn")
    assert "sidebandstudio/Upflow2x/main/update/latest.json" in H.last_command()
    assert "Eternal2x.com/main" not in H.last_command()


def _old_install(tmp_path, repo_root):
    """A launcher from before the rename, Eternal2x.lua, beside its config."""
    plugin_root = _plugin_tree(tmp_path / "Eternal2x", repo_root)
    utility = tmp_path / "Utility"
    utility.mkdir()
    old = utility / "Eternal2x.lua"
    launcher = (repo_root / "Installer" / "Eternal2xLauncher.lua").read_text(encoding="utf-8")
    old.write_text(launcher.replace("-- Upflow2x launcher", "-- Eternal2x launcher", 1),
                   encoding="utf-8")
    (utility / "Eternal2x.conf").write_text(f"repo_root={plugin_root}\nauto_update=false\n",
                                            encoding="utf-8")
    return utility, old


def test_an_old_launcher_is_renamed_so_the_menu_says_upflow2x(lua, tmp_path, repo_root):
    """Updates never touch the launcher, so the panel renames it once."""
    utility, old = _old_install(tmp_path, repo_root)
    assert load(lua, old).window is not None
    assert not old.exists() and (utility / "Upflow2x.lua").exists()
    assert not list(utility.glob("*.part")), "the temporary copy was left behind"
    assert load(lua, utility / "Upflow2x.lua").window is not None


@pytest.mark.parametrize("there", ["-- someone else's script\n", ""])
def test_a_script_already_called_upflow2x_keeps_the_old_launcher(lua, tmp_path, repo_root, there):
    """Unrelated, or cut short: deleting the old one would leave no menu entry."""
    utility, old = _old_install(tmp_path, repo_root)
    (utility / "Upflow2x.lua").write_text(there, encoding="utf-8")
    load(lua, old)
    assert old.exists()
    assert (utility / "Upflow2x.lua").read_text(encoding="utf-8") == there


def test_a_launcher_cut_short_keeps_the_old_one(lua, tmp_path, repo_root):
    """Header and call intact, the last line missing: it would not compile."""
    utility, old = _old_install(tmp_path, repo_root)
    launcher = (repo_root / "Installer" / "Eternal2xLauncher.lua").read_text(encoding="utf-8")
    cut = launcher.rstrip()
    assert cut.endswith("end")
    (utility / "Upflow2x.lua").write_text(cut[:-3], encoding="utf-8")
    load(lua, old)
    assert old.exists()


def test_a_launcher_that_cannot_be_written_keeps_the_old_one(lua, tmp_path, repo_root):
    """A full disk: the write reports failure through its return value."""
    utility, old = _old_install(tmp_path, repo_root)
    lua.execute("""
        local real = io.open
        io.open = function(path, mode)
            if tostring(path):find("%.part$") then
                return { write = function() return nil, "No space left on device" end,
                         close = function() return true end }
            end
            return real(path, mode)
        end
    """)
    load(lua, old)
    assert old.exists() and not (utility / "Upflow2x.lua").exists()


def test_the_launcher_run_beside_the_panel_never_deletes_the_panel(lua, direct):
    """There, Eternal2x.lua beside the config is the panel itself."""
    H = load(lua, direct / "Installer" / "Eternal2xLauncher.lua")
    assert H.window is not None
    assert ui_script(direct).exists()
    assert not (direct / "Installer" / "Upflow2x.lua").exists()


def test_a_panel_run_from_its_own_folder_never_renames_itself(lua, direct):
    """There, the config sits beside the panel, which is also Eternal2x.lua."""
    load(lua, ui_script(direct))
    assert ui_script(direct).exists()
    assert not (direct / "Installer" / "Upflow2x.lua").exists()


def test_a_file_that_is_not_a_launcher_is_left_alone(lua, installed):
    comp, _ = installed
    (comp / "Eternal2x.lua").write_text("-- someone else's script\n", encoding="utf-8")
    load(lua, comp / "Upflow2x.lua")
    assert (comp / "Eternal2x.lua").exists()


def test_an_update_check_that_never_ran_does_not_repeat_the_last_verdict(lua, direct):
    (direct / ".eternal2x_update.log").write_text(
        "Update applied. Restart Resolve to use the new version.\n")
    H = load(lua, ui_script(direct))
    H.exit_code = 1
    H.click("UpdateBtn")
    assert "Update applied" not in H.status()
    assert "failed" in H.status()


def test_a_reopened_panel_follows_the_run_it_left_behind(lua, direct):
    """Closed mid-render and reopened, the panel must not start a second run
    over the first one's files, and should still import the first."""
    import time
    write_status(direct, fraction=0.4, updated=time.time())
    H = load(lua, ui_script(direct))
    assert not H.enabled("SmoothBtn") and not H.enabled("AnalyseBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, message="Done.",
                 import_path=str(direct / "shot_smooth.mp4"), updated=time.time())
    H.click("RefreshStatusBtn")
    assert H.imported_count() == 1
    assert H.enabled("SmoothBtn")


GAVE_UP = "Ready. If the run you reset finishes after all, its render is in the Save to folder."


def press_stop(H):
    """Stop asks on its first press and ends the run on the second."""
    H.click("CancelBtn")
    H.click("CancelBtn")


def test_reset_does_not_drop_a_run_that_is_still_reporting(lua, direct):
    """Reset is for a run that stopped. A live one would carry on underneath
    the next run, sharing its files."""
    import time
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    write_status(direct, fraction=0.4, updated=time.time())
    press_stop(H)
    assert run_file(direct, "status").exists()
    assert not H.enabled("SmoothBtn")
    assert "Stopping" in H.status()


def test_reset_does_not_drop_a_run_that_has_not_reported_yet(lua, direct):
    """Python takes a moment to start. Reset in that gap, then Smooth, used to
    launch a second run over the first."""
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    press_stop(H)
    assert not H.enabled("SmoothBtn")
    assert "Stopping" in H.status()


def test_a_heartbeat_from_before_the_clock_moved_back_is_not_a_live_run(lua, direct):
    """Stamped an hour ahead of now, it would block Reset for that hour."""
    import time
    write_status(direct, fraction=0.4, updated=time.time() + 3600)
    H = load(lua, ui_script(direct))
    assert H.enabled("SmoothBtn")
    H.click("CancelBtn")
    assert H.status() == GAVE_UP


def test_the_panel_keeps_its_own_word_while_a_run_reports_progress(lua, direct):
    """Python's status says Starting until the run is done. Shown on every
    poll, it hid why Reset did nothing and sat under a bar at 52%."""
    import time
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    write_status(direct, fraction=0.52, message="Starting", updated=time.time())
    press_stop(H)
    H.click("RefreshStatusBtn")
    assert "Stopping" in H.status()
    assert "52%" in H.widgets["Progress"].Text


def test_the_stall_warning_goes_once_progress_resumes(lua, direct):
    import time
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    write_status(direct, fraction=0.3, updated=time.time() - 10 * 60)
    H.click("RefreshStatusBtn")
    assert "No progress" in H.status()
    assert H.text_of("CancelBtn") == "Reset", "the warning says press Reset"
    write_status(direct, fraction=0.4, updated=time.time())
    H.click("RefreshStatusBtn")
    assert "No progress" not in H.status()
    assert H.text_of("CancelBtn") == "Stop"


def test_reset_clears_a_run_that_went_quiet(lua, direct):
    import time
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    write_status(direct, fraction=0.4, updated=time.time() - 10 * 60)
    H.click("CancelBtn")
    assert H.enabled("SmoothBtn")
    assert H.status() == GAVE_UP


def test_no_update_while_a_render_runs(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    assert not H.enabled("UpdateBtn")


def test_no_update_while_a_render_from_an_earlier_panel_runs(lua, direct):
    """Reopened mid-render, the panel must not replace the files it is using."""
    import time
    write_status(direct, fraction=0.4, updated=time.time())
    conf = direct / "Installer" / "Eternal2x.conf"
    conf.write_text(conf.read_text().replace("auto_update=false", "auto_update=true"))
    H = load(lua, ui_script(direct))
    assert H.command_count() == 0, "the auto update ran during a render"
    H.click("UpdateBtn")
    assert H.command_count() == 0
    assert "still running" in H.status()


def test_a_sequence_gets_the_source_frame_rate(lua, direct):
    """Left alone, a 60 fps sequence plays at the project's rate, too slow."""
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path="/tmp/out/frame_000000.png",
                 sequence_pattern="/tmp/out/frame_%06d.png", sequence_frames=24, fps=60.0)
    H.click("RefreshStatusBtn")
    assert H.property_of("FPS") == "60"
    assert "at 60 fps" in H.status()


def test_a_fractional_frame_rate_is_kept(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path="/tmp/out/frame_000000.png",
                 sequence_pattern="/tmp/out/frame_%06d.png", sequence_frames=24, fps=23.976)
    H.click("RefreshStatusBtn")
    assert H.property_of("FPS") == "23.976"


def test_a_frame_rate_resolve_ignored_is_reported(lua, direct):
    H = load(lua, ui_script(direct))
    H.fps_sticks = False
    H.click("SmoothBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path="/tmp/out/frame_000000.png",
                 sequence_pattern="/tmp/out/frame_%06d.png", sequence_frames=24, fps=60.0)
    H.click("RefreshStatusBtn")
    assert "Clip Attributes" in H.status()
    assert "at 60 fps" not in H.status()


def test_a_single_file_keeps_its_own_frame_rate(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path="/tmp/out.mp4", fps=60.0)
    H.click("RefreshStatusBtn")
    assert H.property_of("FPS") is None



# --------------------------------------------------------------------------
# only the part of the file the timeline clip uses is processed
# --------------------------------------------------------------------------

def test_a_trimmed_clip_sends_only_its_range(lua, direct):
    """Two seconds of an 84 second file should cost two seconds of work."""
    H = lua.eval(f'dofile("{HARNESS.as_posix()}")')
    H.source_range = lua.table(24.5, 26.5)
    H.load(str(ui_script(direct)), "/media/shot.mov")
    H.click("AnalyseBtn")
    assert "--start-time 24.500000 --end-time 26.500000" in H.last_command()
    assert H.text_of("SourceLabel") == "shot.mov\nTrimmed: 24.50 to 26.50 s"


def test_smooth_sends_the_range_too(lua, direct):
    H = lua.eval(f'dofile("{HARNESS.as_posix()}")')
    H.source_range = lua.table(1.0, 3.0)
    H.load(str(ui_script(direct)), "/media/shot.mov")
    H.click("SmoothBtn")
    assert "--start-time 1.000000 --end-time 3.000000" in H.last_command()


def test_a_build_without_trim_info_uses_the_whole_file(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("AnalyseBtn")
    assert "--start-time" not in H.last_command()
    assert H.text_of("SourceLabel") == "shot.mov"


def test_a_clip_from_the_files_first_frame_is_not_called_trimmed(lua, direct):
    """Every clip has a range, and the old line read "2.00 s long, from
    0.00 s of the file" for one that uses all of it."""
    H = lua.eval(f'dofile("{HARNESS.as_posix()}")')
    H.source_range = lua.table(0.0, 2.0)
    H.load(str(ui_script(direct)), "/media/shot.mov")
    assert H.text_of("SourceLabel") == "shot.mov"


def test_a_clip_that_ends_before_its_file_does_is_trimmed(lua, direct):
    H = lua.eval(f'dofile("{HARNESS.as_posix()}")')
    H.source_range = lua.table(0.0, 2.0)
    H.clip_frames = "240"
    H.load(str(ui_script(direct)), "/media/shot.mov")
    assert H.text_of("SourceLabel") == "shot.mov\nTrimmed: 0.00 to 2.00 s"
    H.clip_frames = "48"
    H.load(str(ui_script(direct)), "/media/shot.mov")
    assert H.text_of("SourceLabel") == "shot.mov"


def test_trim_frames_are_converted_at_the_media_rate(lua, direct):
    """Older builds give frames, which count at the clip's own rate. The end
    frame is the last one the cut uses."""
    H = lua.eval(f'dofile("{HARNESS.as_posix()}")')
    H.source_frames = lua.table(1470, 1589)
    H.clip_fps = "60"
    H.load(str(ui_script(direct)), "/media/shot.mov")
    H.click("AnalyseBtn")
    assert "--start-time 24.500000 --end-time 26.500000" in H.last_command()


def test_media_with_a_start_timecode_still_sends_file_seconds(lua, direct):
    """Resolve's source times count from the media's start timecode, so on
    footage that starts at 01:00:00:00 they are an hour past the file."""
    H = lua.eval(f'dofile("{HARNESS.as_posix()}")')
    H.source_range = lua.table(24.5, 26.5)
    H.start_tc = 3600.0
    H.load(str(ui_script(direct)), "/media/shot.mov")
    H.click("SmoothBtn")
    assert "--start-time 24.500000 --end-time 26.500000" in H.last_command()


def test_the_end_frame_wins_over_the_end_time(lua, direct):
    """Measured on Resolve Studio 20.3.1: a 47 frame cut of 60 fps media on a
    24 fps timeline covers 117.5 source frames from 1200 and reported end
    frame 1317, the last one used, but an end time at frame 1316. Rendered
    from the times it came out a timeline frame short of the cut."""
    H = lua.eval(f'dofile("{HARNESS.as_posix()}")')
    H.clip_fps = "60"
    H.start_tc = 3600.0
    H.source_range = lua.table(20.0, 1316 / 60)
    H.source_frames = lua.table(1200, 1317)
    H.load(str(ui_script(direct)), "/media/shot.mov")
    H.click("SmoothBtn")
    assert "--start-time 20.000000 --end-time 21.966667" in H.last_command()


def test_a_cut_at_the_timeline_rate_ends_where_the_clip_does(lua, direct):
    """Measured on Resolve Studio 20.3.1: at 23.976 fps a 40 frame cut from
    frame 20 reports end frame 60, one past its last, so the render read a
    frame past the cut, which can be the next shot."""
    H = lua.eval(f'dofile("{HARNESS.as_posix()}")')
    H.source_frames = lua.table(20, 60)
    H.clip_fps, H.timeline_fps, H.item_duration = "23.976", "23.976", 40
    H.load(str(ui_script(direct)), "/media/shot.mov")
    H.click("AnalyseBtn")
    assert "--start-time 0.834168 --end-time 2.502503" in H.last_command()


def test_a_cut_whose_end_frame_is_its_last_keeps_it(lua, direct):
    """At 60 fps the same build reports the last frame used: 119 frames
    from 1200 end at 1318."""
    H = lua.eval(f'dofile("{HARNESS.as_posix()}")')
    H.source_frames = lua.table(1200, 1318)
    H.clip_fps, H.timeline_fps, H.item_duration = "60", "60", 119
    H.load(str(ui_script(direct)), "/media/shot.mov")
    H.click("AnalyseBtn")
    assert "--start-time 20.000000 --end-time 21.983333" in H.last_command()


def test_moving_to_another_cut_of_the_same_file_picks_up_its_range(lua, direct):
    H = lua.eval(f'dofile("{HARNESS.as_posix()}")')
    H.source_range = lua.table(1.0, 2.0)
    H.load(str(ui_script(direct)), "/media/shot.mov")
    H.source_range = lua.table(10.0, 12.0)
    H.click("SmoothBtn")
    assert "--start-time 10.000000 --end-time 12.000000" in H.last_command()



# --------------------------------------------------------------------------
# a run killed from outside is noticed
# --------------------------------------------------------------------------

def test_a_run_that_went_quiet_is_flagged(lua, direct):
    """Closing the run's window kills it before it can write anything."""
    import time
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    write_status(direct, fraction=0.05, updated=time.time() - 30 * 60)
    H.click("RefreshStatusBtn")
    assert "No progress for 30 minutes" in H.status()
    assert "Reset" in H.status()


def test_a_run_that_is_still_reporting_is_not_flagged(lua, direct):
    import time
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    write_status(direct, fraction=0.05, updated=time.time())
    H.click("RefreshStatusBtn")
    assert "No progress" not in H.status()


def advance_clock(lua, seconds):
    lua.execute(f"local real = os.time; os.time = function(t) "
                f"if t then return real(t) end return real() + {seconds} end")


def test_a_run_that_never_started_is_flagged(lua, direct):
    """Its window closed before Python wrote a status, so there is no file to
    go quiet. The panel said Working, and Refresh said no run, for good."""
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    advance_clock(lua, 600)
    H.click("RefreshStatusBtn")
    assert "Python has not started after 10 minutes" in H.status()
    H.click("CancelBtn")
    assert H.enabled("SmoothBtn")


def test_a_launch_that_fails_is_reported_at_once(lua, direct):
    H = load(lua, ui_script(direct))
    H.exit_code = 1
    H.click("SmoothBtn")
    assert "Could not start the run" in H.status()
    assert H.enabled("SmoothBtn") and H.enabled("AnalyseBtn")


def test_an_unwritable_run_script_does_not_rerun_the_last_one(lua, direct):
    """The script left from the last run would start in its place."""
    H = load(lua, ui_script(direct))
    lua.execute('package.config = "\\\\" .. package.config:sub(2)')
    lua.execute("""
        local real_open = io.open
        io.open = function(path, mode)
            if tostring(path):find("%.cmd$") and (mode or "r"):find("w") then return nil end
            return real_open(path, mode)
        end
    """)
    before = H.command_count()
    H.click("AnalyseBtn")
    assert H.command_count() == before, "a run started without its script"
    assert "Could not start the run" in H.status()
    assert "Could not start the run" in H.text_of("AnalysisLabel")
    assert H.enabled("SmoothBtn")


def test_stop_ends_a_run_that_is_going(lua, direct):
    """With no window to close, Stop is how a run ends early."""
    import time
    H = load(lua, ui_script(direct))
    assert H.text_of("CancelBtn") == "Reset"
    H.click("SmoothBtn")
    assert H.text_of("CancelBtn") == "Stop"
    write_status(direct, fraction=0.3, updated=time.time())
    press_stop(H)
    assert run_file(direct, "stop").exists()
    assert "Stopping" in H.status()
    write_status(direct, fraction=0.3, done=True, ok=False, message="Stopped.")
    H.click("RefreshStatusBtn")
    assert H.status() == "Stopped."
    assert H.text_of("CancelBtn") == "Reset"
    assert H.enabled("SmoothBtn")


def test_a_stop_left_over_does_not_stop_the_next_run(lua, direct):
    run_file(direct, "stop").write_text("")
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    assert not run_file(direct, "stop").exists()


def test_one_press_of_stop_only_asks(lua, direct):
    """A stopped render deletes what it drew, and Stop sits beside Refresh
    Progress, so a slip of the pointer ended a long render."""
    import time
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    plain = H.widgets["CancelBtn"].StyleSheet
    write_status(direct, fraction=0.6, updated=time.time())
    H.click("CancelBtn")
    assert not run_file(direct, "stop").exists()
    assert not H.enabled("SmoothBtn"), "the render was dropped"
    assert H.status().startswith("Press Stop again to end the render.")
    assert "deletes the frames" in H.status()
    asking = H.widgets["CancelBtn"].StyleSheet
    armed = style_rule(asking, "QPushButton")
    assert asking != plain and armed["color"] == AMBER and armed["font-weight"] == "700"
    H.click("CancelBtn")
    assert run_file(direct, "stop").exists()
    assert H.widgets["CancelBtn"].StyleSheet == plain


def test_an_unanswered_stop_goes_back_to_how_it_was(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    plain = H.widgets["CancelBtn"].StyleSheet
    working = H.status()
    H.click("CancelBtn")
    advance_clock(lua, 6)
    H.fire("PollTimer", "Timeout")
    assert H.status() == working
    assert H.widgets["CancelBtn"].StyleSheet == plain
    # A press long after the first asks again rather than stopping.
    H.click("CancelBtn")
    assert not run_file(direct, "stop").exists()
    assert H.status().startswith("Press Stop again")


def test_a_second_press_too_late_asks_again_without_a_timer(lua, direct):
    """Some Resolve builds have no timer, so nothing resets Stop between presses."""
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    H.click("CancelBtn")
    advance_clock(lua, 6)
    H.click("CancelBtn")
    assert not run_file(direct, "stop").exists()
    H.click("CancelBtn")
    assert run_file(direct, "stop").exists()


def test_stop_says_what_a_stopped_render_loses(lua, direct):
    """Since 0.5.1 a stopped render is deleted. The tip still said its frames stay."""
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    tip = H.widgets["CancelBtn"].ToolTip
    assert "deletes" in tip and "stay" not in tip


def test_stopping_an_analysis_asks_without_warning_of_frames(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("AnalyseBtn")
    H.click("CancelBtn")
    assert H.status() == "Press Stop again to end the analysis."


def test_stop_on_a_run_from_an_earlier_panel_does_not_call_it_a_render(lua, direct):
    """Reopened mid-run, the panel cannot tell an analysis from a render."""
    import time
    write_status(direct, stage="Analysing", fraction=0.4, updated=time.time())
    H = load(lua, ui_script(direct))
    H.click("CancelBtn")
    assert H.status() == ("Press Stop again to end the run. "
                          "Stopping a render deletes the frames it has drawn so far.")
    assert not run_file(direct, "stop").exists()
    H.click("CancelBtn")
    assert run_file(direct, "stop").exists()


@pytest.mark.parametrize("asked", [False, True])
@pytest.mark.parametrize("exited", [False, True])
def test_a_render_that_finishes_between_looks_is_imported_not_reset(lua, direct, asked,
                                                                      exited):
    """Stop pressed once, the render finished before the timer looked again,
    and the next press found no run going, so it reset as for a dead one. The
    status went, and Refresh Progress had nothing left to import."""
    import time
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    write_status(direct, fraction=0.9, updated=time.time())
    if asked:
        H.click("CancelBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path=str(direct / "shot_smooth.mp4"), updated=time.time())
    if exited:
        run_file(direct, "exit").write_text("0\n")
    H.click("CancelBtn")
    assert H.imported_count() == 1
    assert "above the original" in H.status()
    assert not run_file(direct, "stop").exists()
    assert H.enabled("SmoothBtn") and H.text_of("CancelBtn") == "Reset"
    H.click("RefreshStatusBtn")
    assert "No run in progress" in H.status()
    H.click("CancelBtn")
    assert H.imported_count() == 1


def test_a_run_that_stopped_between_looks_says_stopped(lua, direct):
    import time
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    press_stop(H)
    write_status(direct, fraction=0.4, done=True, ok=False, message="Stopped.",
                 updated=time.time())
    H.click("CancelBtn")
    assert H.status() == "Stopped."
    assert H.imported_count() == 0 and H.enabled("SmoothBtn")


def test_a_quiet_run_that_finishes_before_reset_is_imported(lua, direct):
    """Flagged as stalled, Stop reads Reset. A slow run can still finish then."""
    import time
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    write_status(direct, fraction=0.3, updated=time.time() - 10 * 60)
    H.click("RefreshStatusBtn")
    assert H.text_of("CancelBtn") == "Reset"
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path=str(direct / "shot_smooth.mp4"), updated=time.time())
    H.click("CancelBtn")
    assert H.imported_count() == 1


def finish_during_next_click(H, root, exited, **fields):
    """The run publishes its finished status, and with exited its exit code,
    right after the panel's next click first reads the status file."""
    import time

    def publish():
        status = {"fraction": 1.0, "done": True, "ok": True, "message": "Rendered.",
                  "import_path": str(root / "shot_smooth.mp4"), "updated": time.time()}
        status.update(fields)
        write_status(root, **status)
        if exited:
            run_file(root, "exit").write_text("0\n")

    H.after_status_read = publish


@pytest.mark.parametrize("asked", [False, True])
@pytest.mark.parametrize("exited", [False, True])
def test_a_render_that_finishes_during_a_stop_press_keeps_its_result(lua, direct, asked,
                                                                       exited):
    """The press read the run as going, the render finished, and a second
    read found it gone, so the press cleared its result: Ready, no import."""
    import time
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    write_status(direct, fraction=0.9, updated=time.time())
    if asked:
        H.click("CancelBtn")
    finish_during_next_click(H, direct, exited)
    H.click("CancelBtn")
    assert H.after_status_read is None, "the render did not finish during the press"
    assert run_file(direct, "status").exists(), "Stop deleted the finished render"
    assert H.status() != "Ready."
    assert H.imported_count() == 0
    H.click("RefreshStatusBtn")
    assert H.imported_count() == 1
    assert "above the original" in H.status()
    H.click("RefreshStatusBtn")
    H.click("CancelBtn")
    assert H.imported_count() == 1


@pytest.mark.parametrize("reported", [False, True])
@pytest.mark.parametrize("exited", [False, True])
def test_a_quiet_render_that_finishes_during_reset_keeps_its_result(lua, direct, reported,
                                                                     exited):
    """Taken for gone by the look, it finished before Reset cleared up."""
    import time
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    if reported:
        write_status(direct, fraction=0.3, updated=time.time() - 10 * 60)
    else:
        advance_clock(lua, 10 * 60)
    H.click("RefreshStatusBtn")
    assert H.text_of("CancelBtn") == "Reset"
    finish_during_next_click(H, direct, exited)
    H.click("CancelBtn")
    assert H.after_status_read is None
    assert H.imported_count() == 1
    assert "above the original" in H.status()
    assert not Path(str(run_file(direct, "status")) + ".reset").exists()
    H.click("RefreshStatusBtn")
    assert H.imported_count() == 1


@pytest.mark.parametrize("case", ["exited, no status", "exited mid-run", "went quiet",
                                  "never reported"])
def test_a_run_that_is_really_gone_resets_in_one_press(lua, direct, case):
    import time
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    if case == "exited mid-run":
        write_status(direct, fraction=0.5, updated=time.time())
    if case.startswith("exited"):
        run_file(direct, "exit").write_text("1\n")
    elif case == "went quiet":
        write_status(direct, fraction=0.3, updated=time.time() - 10 * 60)
    else:
        advance_clock(lua, 10 * 60)
    H.click("CancelBtn")
    exited = case.startswith("exited")
    # Without an exit code the run may only have gone quiet, so Reset says
    # where its render goes if it finishes after all.
    assert H.status() == ("Ready." if exited else GAVE_UP)
    assert H.enabled("SmoothBtn")
    assert not run_file(direct, "stop").exists()
    # Nothing is left to read. A run known to have ended keeps an empty
    # status, which says a panel dealt with its end, so none reports it again.
    status = run_file(direct, "status")
    assert (status.read_text() == "") if exited else not status.exists()
    assert not Path(str(status) + ".reset").exists()
    # The exit code stays, as proof the run ended, so the next run clears its files.
    assert run_file(direct, "exit").exists() == exited


def test_reset_keeps_a_render_that_finished_while_the_panel_was_closed(lua, direct):
    """The panel said to press Refresh Progress for it. Reset deleted it."""
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path=str(direct / "shot_smooth.mp4"))
    H = load(lua, ui_script(direct))
    H.click("CancelBtn")
    assert run_file(direct, "status").exists()
    assert "Press Refresh Progress to add it" in H.status()
    assert H.imported_count() == 0
    H.click("RefreshStatusBtn")
    assert H.imported_count() == 1


def test_reset_with_no_run_keeps_a_render_that_lands_during_it(lua, direct):
    H = load(lua, ui_script(direct))
    finish_during_next_click(H, direct, exited=True)
    H.click("CancelBtn")
    assert run_file(direct, "status").exists()
    assert "Press Refresh Progress to add it" in H.status()
    H.click("RefreshStatusBtn")
    assert H.imported_count() == 1


def test_reset_clears_an_old_failure_from_a_closed_panel(lua, direct):
    write_status(direct, fraction=0.2, done=True, ok=False, message="The clip is missing.")
    H = load(lua, ui_script(direct))
    H.click("CancelBtn")
    assert H.status() == "Ready."
    # Left empty, which reads as no status, and says its end was dealt with.
    assert run_file(direct, "status").read_text() == ""


def publish_after_next_claim(H, root, exited=False, **fields):
    """The run publishes a status, by default a finished render, and with
    exited its exit code, right after the panel's next click moves the status
    file aside."""
    import time

    def publish():
        status = {"fraction": 1.0, "done": True, "ok": True, "message": "Rendered.",
                  "import_path": str(root / "shot_smooth.mp4"), "updated": time.time()}
        status.update(fields)
        write_status(root, **status)
        if exited:
            run_file(root, "exit").write_text("0\n")

    H.after_claim = publish


def reset_a_quiet_render(lua, H, root, reported=True):
    """A render this panel started that has gone quiet, so Reset gives up on it."""
    import time
    H.click("SmoothBtn")
    if reported:
        write_status(root, fraction=0.3, updated=time.time() - 10 * 60)
    else:
        advance_clock(lua, 10 * 60)


@pytest.mark.parametrize("start", ["SmoothBtn", "AnalyseBtn"])
@pytest.mark.parametrize("exited", [False, True])
def test_a_render_that_finishes_just_after_resets_claim_survives_the_next_start(
        lua, direct, start, exited):
    """Reset moved the quiet run's status aside, the render then finished and
    wrote a new one, Reset said Ready, and Render or Analyse cleared it unseen."""
    H = load(lua, ui_script(direct))
    reset_a_quiet_render(lua, H, direct)
    publish_after_next_claim(H, direct, exited)
    H.click("CancelBtn")
    assert H.after_claim is None, "the render did not finish during Reset"
    assert H.status() == GAVE_UP
    launches = H.command_count()
    for _ in range(3):
        H.click(start)
        assert H.command_count() == launches, "a new run started over the finished render"
        assert run_file(direct, "status").exists(), "the finished render was cleared"
        assert "Press Refresh Progress to add it" in H.status()
        assert H.imported_count() == 0
    H.click("RefreshStatusBtn")
    assert H.imported_count() == 1
    assert "placed on V2 above the original" in H.status()
    H.click(start)
    assert H.command_count() == launches + 1
    H.click("RefreshStatusBtn")
    assert H.imported_count() == 1


def test_a_quiet_render_that_is_still_going_after_reset_is_followed_not_doubled(lua, direct):
    import time
    H = load(lua, ui_script(direct))
    reset_a_quiet_render(lua, H, direct)
    publish_after_next_claim(H, direct, fraction=0.6, done=False, ok=False,
                             import_path="", updated=time.time())
    H.click("CancelBtn")
    launches = H.command_count()
    H.click("SmoothBtn")
    assert H.command_count() == launches, "a second run started over the one still going"
    assert not H.enabled("SmoothBtn") and not H.enabled("AnalyseBtn")
    assert "still going" in H.status()
    assert "60%" in H.widgets["Progress"].Text
    H.click("RefreshStatusBtn")
    assert "has not started" not in H.status() and "No progress" not in H.status()
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path=str(direct / "shot_smooth.mp4"), updated=time.time())
    H.click("RefreshStatusBtn")
    assert H.imported_count() == 1
    assert "placed on V2 above the original" in H.status()


# --------------------------------------------------------------------------
# a run Reset gave up on, and the run that takes its place
# --------------------------------------------------------------------------

def give_up_on_a_quiet_render(H, root):
    """Render A of the first clip goes quiet, and Reset gives up on it. A
    stays alive, as a run killed from outside would not. Returns A's tag."""
    import time
    H.click("SmoothBtn")
    a = run_tag(root)
    write_status(root, fraction=0.3, updated=time.time() - 10 * 60)
    H.click("CancelBtn")
    assert H.status() == GAVE_UP
    return a


def render_the_next_clip(H, root):
    """Render B of another clip, at another place on the timeline, takes A's
    place. Returns B's tag."""
    H.next_clip("/media/shot_b.mov", "clip-b", 90000, 24)
    launches = H.command_count()
    H.click("SmoothBtn")
    assert H.command_count() == launches + 1, "B did not start"
    return run_tag(root)


def ends(root, tag, name, exited=True):
    """The run tagged `tag` finishes its render of `name`, and with exited
    its shell then writes its exit code."""
    import time
    write_status(root, tag, fraction=1.0, done=True, ok=True, message=f"Rendered {name}.",
                 import_path=f"/renders/{name}_smooth.mp4", updated=time.time())
    if exited:
        run_file(root, "exit", tag).write_text("0\n")


@pytest.mark.parametrize("a_exits", [False, True])
@pytest.mark.parametrize("order", ["A ends first", "B ends first"])
def test_a_run_given_up_on_that_ends_after_the_next_starts_stays_apart(lua, direct, order,
                                                                      a_exits):
    """Both used to write one status, exit and stop file: A's late result went
    in as B's, at B's place, or B's progress wrote over it."""
    import time
    H = load(lua, ui_script(direct))
    a = give_up_on_a_quiet_render(H, direct)
    b = render_the_next_clip(H, direct)
    assert a != b and run_file(direct, "status", a) != run_file(direct, "status", b)
    if order == "A ends first":
        ends(direct, a, "shot", a_exits)
    write_status(direct, b, fraction=0.5, updated=time.time())
    H.click("RefreshStatusBtn")
    assert "50%" in H.widgets["Progress"].Text, "B's progress was not what the panel saw"
    assert not H.enabled("SmoothBtn"), "A's end ended B"
    assert H.imported_count() == 0
    ends(direct, b, "shot_b")
    if order == "B ends first":
        ends(direct, a, "shot", a_exits)
    H.click("RefreshStatusBtn")
    assert H.imported_count() == 1
    assert H.imported_at(1) == "/renders/shot_b_smooth.mp4"
    assert H.placements[1].recordFrame == 90000, "B went in at another clip's place"
    assert "Rendered shot_b." in H.status()
    for click in ("RefreshStatusBtn", "CancelBtn", "RefreshStatusBtn"):
        H.click(click)
    assert H.imported_count() == 1, "A's result came in too"
    left = json.loads(run_file(direct, "status", a).read_text())
    assert left["import_path"] == "/renders/shot_smooth.mp4", "A's result was written over"


def test_a_late_stop_or_exit_from_a_run_given_up_on_never_reaches_the_next(lua, direct):
    """A's stage takes its Stop file away as it ends, and its shell then writes
    its exit code. Shared, that cancelled B's Stop, or made B look dead."""
    import time
    H = load(lua, ui_script(direct))
    a = give_up_on_a_quiet_render(H, direct)
    b = render_the_next_clip(H, direct)
    write_status(direct, b, fraction=0.4, updated=time.time())
    press_stop(H)
    assert run_file(direct, "stop", b).exists() and not run_file(direct, "stop", a).exists()
    run_file(direct, "stop", a).unlink(missing_ok=True)
    run_file(direct, "exit", a).write_text("1\n")
    assert run_file(direct, "stop", b).exists(), "A's end took B's Stop"
    H.click("RefreshStatusBtn")
    assert "Stopped without finishing" not in H.status(), "A's exit code ended B"
    assert not H.enabled("SmoothBtn")


@pytest.mark.parametrize("b_done", [False, True])
def test_a_panel_opened_again_follows_only_the_run_it_owns(lua, direct, b_done):
    """The pointer names B, so a reopened panel never takes A's result for it."""
    import time
    H = load(lua, ui_script(direct))
    a = give_up_on_a_quiet_render(H, direct)
    b = render_the_next_clip(H, direct)
    ends(direct, a, "shot")
    if b_done:
        ends(direct, b, "shot_b")
    else:
        write_status(direct, b, fraction=0.5, updated=time.time())
    H = load(lua, ui_script(direct))
    assert run_tag(direct) == b
    if b_done:
        assert "finished while the panel was closed" in H.status()
    else:
        assert "still going" in H.status() and not H.enabled("SmoothBtn")
        ends(direct, b, "shot_b")
    H.click("RefreshStatusBtn")
    assert H.imported_count() == 1
    assert H.imported_at(1) == "/renders/shot_b_smooth.mp4"
    H.click("RefreshStatusBtn")
    assert H.imported_count() == 1
    assert run_file(direct, "status", a).exists()


def test_a_run_given_up_on_keeps_its_files_while_it_may_still_write(lua, direct):
    """Only an exit code shows a run has ended. Without one, its files stay."""
    H = load(lua, ui_script(direct))
    a = give_up_on_a_quiet_render(H, direct)
    write_status(direct, a, fraction=0.31, updated=0)
    render_the_next_clip(H, direct)
    assert run_file(direct, "status", a).exists()


def test_the_first_run_after_0_5_1_gets_files_of_its_own(lua, direct):
    """A 0.5.1 run used one set of names. Its result still comes in, and the
    next run takes new names and clears the old ones once that run has ended."""
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path="/renders/old_smooth.mp4")
    (direct / ".eternal2x_exit").write_text("0\n")
    (direct / ".upflow2x_last_run.log").write_text("Done.\n")
    H = load(lua, ui_script(direct))
    assert run_tag(direct) == ""
    H.click("RefreshStatusBtn")
    assert H.imported_at(1) == "/renders/old_smooth.mp4"
    H.click("SmoothBtn")
    assert run_tag(direct) != ""
    for name in (".eternal2x_status.json", ".eternal2x_exit", ".upflow2x_last_run.log"):
        assert not (direct / name).exists(), name
    assert run_file(direct, "status").name.startswith(".upflow2x_run-")


@pytest.mark.parametrize("start", ["SmoothBtn", "AnalyseBtn"])
@pytest.mark.parametrize("left", ["exited, no status", "exited mid-run", "went quiet",
                                  "old failure", "old stop", "reopened, quiet"])
def test_a_start_still_recovers_from_a_run_that_is_really_gone(lua, direct, start, left):
    import time
    if left == "reopened, quiet":
        write_status(direct, fraction=0.3, updated=time.time() - 10 * 60)
        H = load(lua, ui_script(direct))
    else:
        H = load(lua, ui_script(direct))
        H.click("SmoothBtn")
        if left == "exited mid-run":
            write_status(direct, fraction=0.5, updated=time.time())
        if left.startswith("exited"):
            run_file(direct, "exit").write_text("1\n")
        elif left == "went quiet":
            write_status(direct, fraction=0.3, updated=time.time() - 10 * 60)
        else:
            write_status(direct, fraction=0.3, done=True, ok=False,
                         message="Stopped." if left == "old stop" else "The clip is missing.")
        H.click("CancelBtn")
    launches = H.command_count()
    old = run_tag(direct)
    H.click(start)
    assert H.command_count() == launches + 1
    assert not H.enabled("SmoothBtn")
    assert run_tag(direct) != old, "the new run did not get files of its own"
    assert not Path(str(run_file(direct, "status", old)) + ".reset").exists()
    if left.startswith("exited"):
        # Its exit code shows it ended, so its files go.
        assert not run_file(direct, "exit", old).exists()
        assert not run_file(direct, "status", old).exists()
    elif left == "reopened, quiet":
        # It may only have gone quiet, so it keeps the one file it writes.
        assert run_file(direct, "status", old).exists()


@pytest.mark.parametrize("start", ["SmoothBtn", "AnalyseBtn"])
def test_a_start_keeps_a_render_that_finished_while_the_panel_was_closed(lua, direct, start):
    """The panel said to press Refresh Progress for it, and Render deleted it."""
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path=str(direct / "shot_smooth.mp4"))
    H = load(lua, ui_script(direct))
    H.click(start)
    assert H.command_count() == 0
    assert run_file(direct, "status").exists()
    assert "Press Refresh Progress to add it" in H.status()
    H.click("RefreshStatusBtn")
    assert H.imported_count() == 1
    H.click(start)
    assert H.command_count() == 1


# --------------------------------------------------------------------------
# a run that cannot start leaves the run before it in place
# --------------------------------------------------------------------------

def break_writes(lua, pattern):
    """Files whose path matches the Lua `pattern` open to write, which empties
    them, then fail as they close, as on a full disk. Until mend_writes."""
    lua.execute(f"""
        REAL_OPEN = REAL_OPEN or io.open
        local real_open = REAL_OPEN
        io.open = function(path, mode)
            local f = real_open(path, mode)
            if not (f and tostring(path):find("{pattern}") and (mode or "r"):find("w")) then
                return f
            end
            return {{ write = function(self) return self end,
                     close = function() f:close() return nil, "No space left on device" end }}
        end
    """)


def mend_writes(lua):
    lua.execute("if REAL_OPEN then io.open = REAL_OPEN; REAL_OPEN = nil end")


def back_to_the_first_clip(lua, H, span):
    """The playhead goes back to the clip it was on before next_clip, which
    sits at `span`."""
    lua.execute("local H, span = ...; H.clip_file, H.item_id, H.src_span = nil, nil, span",
                H, span)


def retry_renders_the_next_clip(H, root, a, imported):
    """With A's render in, Render of the next clip starts and goes in at that
    clip's place."""
    H.next_clip("/media/shot_b.mov", "clip-b", 90000, 24)
    launches, placed = H.command_count(), len(H.placements)
    H.click("SmoothBtn")
    assert H.command_count() == launches + 1, "the retry did not start"
    b = run_tag(root)
    assert b not in ("", a)
    ends(root, b, "shot_b")
    H.click("RefreshStatusBtn")
    assert H.imported_count() == imported + 1
    assert H.imported_at(imported + 1) == "/renders/shot_b_smooth.mp4"
    assert len(H.placements) == placed + 1 and H.placements[placed + 1].recordFrame == 90000


@pytest.mark.parametrize("start", ["SmoothBtn", "AnalyseBtn"])
@pytest.mark.parametrize("a_ends", ["during the launch", "after it"])
def test_a_launch_that_fails_leaves_the_run_given_up_on_in_place(lua, direct, start, a_ends):
    """B's launch failed after the pointer named B, so when A finished after
    all, Refresh Progress said no run."""
    H = load(lua, ui_script(direct))
    a = give_up_on_a_quiet_render(H, direct)
    first = H.src_span
    H.next_clip("/media/shot_b.mov", "clip-b", 90000, 24)
    H.exit_code = 1
    if a_ends == "during the launch":
        H.on_execute = lambda cmd: ends(direct, a, "shot")
    launches = H.command_count()
    H.click(start)
    H.on_execute = None
    H.exit_code = 0
    assert H.command_count() == launches + 1, "B was not tried"
    assert "Could not start the run" in H.status()
    assert H.enabled("SmoothBtn") and H.enabled("AnalyseBtn")
    assert run_tag(direct) == a, "the pointer names a run that never started"
    assert all(p.name.startswith(f".upflow2x_run-{a}.")
               for p in direct.glob(".upflow2x_run-*")), "B left files behind"
    if a_ends == "after it":
        ends(direct, a, "shot")
    back_to_the_first_clip(lua, H, first)
    H.click("RefreshStatusBtn")
    assert H.imported_count() == 1
    assert H.imported_at(1) == "/renders/shot_smooth.mp4"
    assert len(H.placements) == 1 and H.placements[1].recordFrame == 86400, \
        "A did not go in above its own clip"
    retry_renders_the_next_clip(H, direct, a, 1)


@pytest.mark.parametrize("start", ["SmoothBtn", "AnalyseBtn"])
def test_a_panel_opened_after_a_failed_launch_finds_the_run_before(lua, direct, start):
    H = load(lua, ui_script(direct))
    a = give_up_on_a_quiet_render(H, direct)
    H.next_clip("/media/shot_b.mov", "clip-b", 90000, 24)
    H.exit_code = 1
    H.click(start)
    H.exit_code = 0
    ends(direct, a, "shot")
    H = load(lua, ui_script(direct))
    assert run_tag(direct) == a
    assert "finished while the panel was closed" in H.status()
    H.click("RefreshStatusBtn")
    assert H.imported_count() == 1
    assert H.imported_at(1) == "/renders/shot_smooth.mp4"
    retry_renders_the_next_clip(H, direct, a, 1)


def test_marked_frames_that_cannot_be_written_leave_the_run_given_up_on_in_place(lua, direct):
    H = load(lua, ui_script(direct))
    a = give_up_on_a_quiet_render(H, direct)
    first = H.src_span
    H.next_clip("/media/shot_b.mov", "clip-b", 90000, 24)
    for frame in (0, 6):
        H.add_marker(frame)
    break_writes(lua, "%.marks$")
    launches = H.command_count()
    H.click("SmoothBtn")
    mend_writes(lua)
    assert H.command_count() == launches
    assert "Could not write the marked frames" in H.status()
    assert run_tag(direct) == a
    assert not list(direct.glob(".upflow2x_run-*.marks")), "B's half-written marks stayed"
    ends(direct, a, "shot")
    back_to_the_first_clip(lua, H, first)
    H.click("RefreshStatusBtn")
    assert H.imported_at(1) == "/renders/shot_smooth.mp4"
    assert len(H.placements) == 1 and H.placements[1].recordFrame == 86400
    retry_renders_the_next_clip(H, direct, a, 1)


def test_a_pointer_that_cannot_be_written_still_names_the_run_before(lua, direct):
    """Opening the pointer to write empties it, so a write that failed after
    that left it naming no run, and the next panel could not find A."""
    H = load(lua, ui_script(direct))
    a = give_up_on_a_quiet_render(H, direct)
    H.next_clip("/media/shot_b.mov", "clip-b", 90000, 24)
    break_writes(lua, "%.upflow2x_run%.new%-")
    launches = H.command_count()
    H.click("SmoothBtn")
    mend_writes(lua)
    assert H.command_count() == launches, "B started with no pointer to find it by"
    assert "Could not start the run" in H.status()
    assert run_tag(direct) == a
    ends(direct, a, "shot")
    H = load(lua, ui_script(direct))
    assert "finished while the panel was closed" in H.status()
    H.click("RefreshStatusBtn")
    assert H.imported_at(1) == "/renders/shot_smooth.mp4"


def break_renames(lua, *patterns):
    """Moving a file whose path matches one of the Lua `patterns` fails, as
    when another program holds it open. Until mend_renames."""
    listed = ", ".join(f'"{p}"' for p in patterns)
    lua.execute(f"""
        REAL_RENAME = REAL_RENAME or os.rename
        local real_rename = REAL_RENAME
        os.rename = function(from, to)
            for _, pattern in ipairs({{ {listed} }}) do
                if tostring(from):find(pattern) then return nil, "Permission denied" end
            end
            return real_rename(from, to)
        end
    """)


def mend_renames(lua):
    lua.execute("if REAL_RENAME then os.rename = REAL_RENAME; REAL_RENAME = nil end")


@pytest.mark.parametrize("restore", ["works", "fails"])
def test_a_pointer_that_cannot_be_moved_into_place_leaves_the_run_before_in_charge(
        lua, direct, restore):
    """B's pointer could not replace A's. Removing A's pointer to make room
    lost it when the move then failed, and B, started first and only asked to
    stop, could render on with no panel able to find it."""
    H = load(lua, ui_script(direct))
    a = give_up_on_a_quiet_render(H, direct)
    H.next_clip("/media/shot_b.mov", "clip-b", 90000, 24)
    held = ["%.upflow2x_run%.new%-"] + (["%.upflow2x_run%.old$"] if restore == "fails" else [])
    break_renames(lua, *held)
    launches = H.command_count()
    H.click("SmoothBtn")
    mend_renames(lua)
    assert H.command_count() == launches, "B started with no pointer to find it by"
    assert "Could not start the run" in H.status()
    assert H.enabled("SmoothBtn")
    assert all(p.name.startswith(f".upflow2x_run-{a}.")
               for p in direct.glob(".upflow2x_run-*")), "B left files behind"
    pointer, old = direct / ".upflow2x_run", direct / ".upflow2x_run.old"
    if restore == "works":
        assert pointer.read_text().strip() == a and not old.exists()
    else:
        # A is still found: as the pointer that stepped aside.
        assert not pointer.exists() and old.read_text().strip() == a
    ends(direct, a, "shot")
    H = load(lua, ui_script(direct))
    assert "finished while the panel was closed" in H.status()
    H.click("RefreshStatusBtn")
    assert H.imported_at(1) == "/renders/shot_smooth.mp4"
    retry_renders_the_next_clip(H, direct, a, 1)
    assert run_tag(direct) not in ("", a) and not old.exists()


def test_a_pointer_waiting_to_go_in_is_named_for_its_run(lua, direct):
    """Two panels shared one waiting pointer, so one could move the other's
    tag into place. And it went in only after the run started, so a run whose
    pointer then could not go in rendered with no panel able to find it."""
    lua.execute("""
        REAL_RENAME = REAL_RENAME or os.rename
        local real_rename = REAL_RENAME
        MOVED = {}
        os.rename = function(from, to)
            local name = tostring(from):match("[^/\\\\]+$")
            if name:find("^%.upflow2x_run%.new%-") then
                local f = io.open(from, "r")
                MOVED[#MOVED + 1] = name .. "=" .. (f and f:read("*a") or "")
                if f then f:close() end
            end
            return real_rename(from, to)
        end""")
    pointer = direct / ".upflow2x_run"
    at_start = []
    H = load(lua, ui_script(direct))
    H.on_execute = lambda cmd: at_start.append(
        (pointer.read_text() if pointer.exists() else None,
         sorted(p.name for p in direct.glob(".upflow2x_run.new*"))))
    press(H, "SmoothBtn")
    H.on_execute = None
    mend_renames(lua)
    tag = run_tag(direct)
    assert at_start == [(tag, [])], "the run started before a panel could find it"
    assert list(lua.eval("MOVED").values()) == [f".upflow2x_run.new-{tag}={tag}"]


def test_a_run_script_that_fails_as_it_closes_starts_nothing(lua, direct):
    """An empty script still starts a shell, which never starts Python."""
    H = load(lua, ui_script(direct))
    lua.execute('package.config = "\\\\" .. package.config:sub(2)')
    break_writes(lua, "%.cmd$")
    H.click("SmoothBtn")
    mend_writes(lua)
    assert H.command_count() == 0
    assert "Could not start the run" in H.status()
    assert run_tag(direct) == ""
    assert not list(direct.glob(".upflow2x_run-*"))


def test_a_failed_first_launch_after_0_5_1_still_finds_the_0_5_1_run(lua, direct):
    import time
    write_status(direct, fraction=0.3, updated=time.time() - 10 * 60)
    H = load(lua, ui_script(direct))
    H.click("CancelBtn")
    assert H.status() == GAVE_UP
    H.exit_code = 1
    H.click("SmoothBtn")
    H.exit_code = 0
    assert run_tag(direct) == ""
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path="/renders/old_smooth.mp4", updated=time.time())
    H.click("RefreshStatusBtn")
    assert H.imported_at(1) == "/renders/old_smooth.mp4"


def test_reset_of_a_dead_run_needs_one_press(lua, direct):
    """Nothing is lost clearing a run that already ended, so it does not ask."""
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    run_file(direct, "exit").write_text("1\n")
    H.click("CancelBtn")
    assert H.status() == "Ready."
    assert H.enabled("SmoothBtn")


def test_a_render_that_finished_while_the_panel_was_closed_is_offered(lua, direct):
    write_status(direct, fraction=1.0, done=True, ok=True, message="Done.",
                 import_path=str(direct / "shot_smooth.mp4"))
    H = load(lua, ui_script(direct))
    assert "finished while the panel was closed" in H.status()
    assert H.enabled("FolderBtn")
    assert H.imported_count() == 0
    H.click("RefreshStatusBtn")
    assert H.imported_count() == 1


def test_the_windows_run_gets_its_own_minimised_window(lua, direct):
    """Sharing os.execute's console left it open, and closing it killed the run."""
    lua.execute('package.config = "\\\\" .. package.config:sub(2)')
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    outer = H.last_command().split("\n")[0]
    assert " /MIN " in outer and " /B " not in outer, outer


def test_a_percent_in_the_plugin_path_is_refused_on_windows(lua, tmp_path, repo_root):
    """cmd expands %NAME% in a command line even inside quotes."""
    root = _plugin_tree(tmp_path / "VFX %TEMP% tools" / "Eternal2x", repo_root)
    (root / "Installer" / "Eternal2x.conf").write_text(
        f"repo_root={root}\npython={sys.executable}\n"
        "update_url=https://example.com/latest.json\nauto_update=false\n")
    H = lua.eval(f'dofile("{HARNESS.as_posix()}")')
    lua.execute('package.config = "\\\\" .. package.config:sub(2)')
    H.load(str(ui_script(root)), "/media/shot.mov")
    for button in ("AnalyseBtn", "SmoothBtn", "UpdateBtn"):
        H.click(button)
        assert H.command_count() == 0, f"{button} ran a command from the % folder"
        assert "contains %" in H.status()
    assert H.enabled("SmoothBtn"), "the panel was left busy"


def test_show_in_folder_keeps_a_percent_in_the_path_literal(lua, direct):
    """cmd would expand %TEMP% in a command line and open somewhere else."""
    H = load(lua, ui_script(direct))
    lua.execute('package.config = "\\\\" .. package.config:sub(2)')
    finish_render(H, direct, import_path="C:/renders/%TEMP%/out.mp4")
    H.click("FolderBtn")
    command, _, script = H.last_command().partition("\n")
    assert "%TEMP%" not in command, "the path went through cmd's own command line"
    assert "%%TEMP%%" in script and "explorer /select," in script


def test_a_frame_rate_that_cannot_be_read_back_is_not_trusted(lua, direct):
    """Resolve said yes to 60 fps, but the readback failed, so 60 is unproven."""
    H = load(lua, ui_script(direct))
    H.fps_sticks = False
    H.fps_read_throws = True
    finish_render(H, direct, import_path="/tmp/out/frame_000000.png",
                  sequence_pattern="/tmp/out/frame_%06d.png", sequence_frames=120, fps=60.0)
    assert H.appended == 0
    assert "at 60 fps" not in H.status()


def test_a_saved_quality_from_an_older_version_is_dropped(lua, direct):
    conf = direct / "Installer" / "Eternal2x.conf"
    conf.write_text(conf.read_text() + "quality=better\n")
    H = load(lua, ui_script(direct))
    H.set_checkbox("UpscaleCB", False)
    assert "quality=" not in conf.read_text()
    H.click("SmoothBtn")
    assert "--quality" not in H.last_command()


# --------------------------------------------------------------------------
# frames marked on the clip
# --------------------------------------------------------------------------

def marks_sent(direct):
    """The frames Render handed the stage, from the file it wrote them to."""
    return run_file(direct, "marks").read_text()


def test_the_run_button_says_render(lua, direct):
    """It smooths and upscales, so Smooth Clip undersold it."""
    H = load(lua, ui_script(direct))
    assert "Render" in H.widgets["SmoothBtn"].Text


def test_render_hands_over_the_frames_marked_on_the_clip(lua, direct):
    H = load(lua, ui_script(direct))
    for frame in (12, 0, 6):
        H.add_marker(frame)
    H.click("SmoothBtn")
    assert "--marks-file" in H.last_command()
    assert marks_sent(direct) == "0,6,12"
    assert "3 marked frames" in H.status()


def test_render_without_markers_finds_the_drawings_itself(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    assert "--marks" not in H.last_command()
    assert "No markers" in H.status()


def test_markers_past_the_end_of_the_clip_do_not_count(lua, direct):
    """The clip may be trimmed shorter than the media its markers sit on."""
    H = load(lua, ui_script(direct))
    H.add_marker(2)
    H.add_marker(400)
    H.click("SmoothBtn")
    assert marks_sent(direct) == "2"


def test_analyse_marks_the_drawings_it_found_on_the_clip(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("AnalyseBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, marks="0,2,4,8",
                 message="12 frames, 4 unique drawings. Animated on 2s.")
    H.click("RefreshStatusBtn")
    assert H.marker_frames() == "0,2,4,8"
    assert H.tagged_markers() == 4
    assert H.widgets["AnalysisLabel"].Text == "Placed 4 markers. Animated on 2s."
    assert "Marked 4 frames on the clip" in H.widgets["AnalysisLabel"].ToolTip
    assert "Render" in H.status()


def _analysed(lua, direct):
    H = lua.eval(f'dofile("{HARNESS.as_posix()}")')
    H.source_range = lua.table(1.0, 3.0)
    H.load(str(ui_script(direct)), "/media/shot.mov")
    H.click("AnalyseBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, marks="0,2,4,8",
                 message="12 frames, 4 unique drawings. Animated on 2s.")
    H.click("RefreshStatusBtn")
    return H


def test_rendering_the_analysed_clip_keeps_its_analysis(lua, direct):
    H = _analysed(lua, direct)
    H.click("SmoothBtn")
    assert H.widgets["AnalysisLabel"].Text == "Placed 4 markers. Animated on 2s."


def test_another_part_of_the_file_clears_the_last_analysis(lua, direct):
    H = _analysed(lua, direct)
    H.source_range = lua.table(5.0, 7.0)
    H.click("SmoothBtn")
    assert "Placed" not in H.widgets["AnalysisLabel"].Text
    assert H.widgets["AnalysisLabel"].ToolTip == ""


def test_another_clip_from_the_same_part_of_the_file_clears_the_analysis(lua, direct):
    H = _analysed(lua, direct)
    H.item_id = "another-cut"
    follow(H)
    assert "Placed" not in H.widgets["AnalysisLabel"].Text


def test_the_card_waits_while_the_playhead_moves(lua, direct):
    """Playing back, the playhead moves every tick. Reading the clip under it
    each time would cost Resolve, so the card catches up once it stops."""
    H = lua.eval(f'dofile("{HARNESS.as_posix()}")')
    H.under_playhead = False
    H.load(str(ui_script(direct)), "/media/shot.mov")
    H.under_playhead = True
    for tc in ("01:00:00:01", "01:00:00:02", "01:00:00:03"):
        H.playhead_tc = tc
        H.fire("PollTimer", "Timeout")
    assert "shot.mov" not in H.widgets["SourceLabel"].Text, "it read the clip mid-playback"
    H.fire("PollTimer", "Timeout")
    assert "shot.mov" in H.widgets["SourceLabel"].Text


def test_finding_a_clip_clears_the_missing_clip_message(lua, direct):
    H = lua.eval(f'dofile("{HARNESS.as_posix()}")')
    H.under_playhead = False
    H.load(str(ui_script(direct)), "/media/shot.mov")
    assert "playhead" in H.status()
    H.under_playhead = True
    follow(H)
    assert "shot.mov" in H.widgets["SourceLabel"].Text
    assert H.status() == "Ready."


def test_refresh_progress_also_picks_up_the_clip(lua, direct):
    """Without a timer, nothing else would move the card."""
    H = lua.eval(f'dofile("{HARNESS.as_posix()}")')
    H.under_playhead = False
    H.load(str(ui_script(direct)), "/media/shot.mov")
    H.under_playhead = True
    H.click("RefreshStatusBtn")
    assert "shot.mov" in H.widgets["SourceLabel"].Text


def test_the_clip_card_follows_the_playhead_from_the_start(lua, direct):
    """No button to press: the timer runs as soon as the panel opens."""
    H = load(lua, ui_script(direct))
    assert H.window.__items["PollTimer"].__running


def test_the_clip_card_keeps_the_last_clip_in_a_gap(lua, direct):
    """As Analyse and Render do, so moving off the clip does not lose it."""
    H = load(lua, ui_script(direct))
    H.under_playhead = False
    follow(H)
    assert "shot.mov" in H.widgets["SourceLabel"].Text
    assert H.widgets["SourceLabel"].ToolTip.endswith("\n\n/media/shot.mov")
    assert H.status() == "Ready.", "a gap reported itself in the status"
    assert H.text_of("SourceLabel") == "shot.mov\nLast usable clip"
    H.under_playhead = True
    follow(H)
    assert H.text_of("SourceLabel") == "shot.mov", "the clip under the playhead was called kept"


def test_with_no_clip_the_card_has_no_path(lua, direct):
    H = lua.eval(f'dofile("{HARNESS.as_posix()}")')
    H.under_playhead = False
    H.load(str(ui_script(direct)), "/media/shot.mov")
    assert "shot.mov" not in H.widgets["SourceLabel"].ToolTip
    assert "playhead" in H.widgets["SourceLabel"].ToolTip
    assert "Last usable clip" not in H.text_of("SourceLabel")


def test_a_clip_kept_from_before_keeps_its_tip(lua, direct):
    """The playhead in a gap: Analyse keeps the last clip, and its path."""
    H = load(lua, ui_script(direct))
    H.under_playhead = False
    H.click("AnalyseBtn")
    assert "shot.mov" in H.widgets["SourceLabel"].Text
    assert H.widgets["SourceLabel"].ToolTip.endswith("/media/shot.mov")
    assert H.text_of("SourceLabel").endswith("\nLast usable clip")


def test_analysing_again_replaces_only_its_own_markers(lua, direct):
    """The editor's markers stay. Eternal2x's from last time go."""
    H = load(lua, ui_script(direct))
    H.add_marker(5)
    H.add_marker(9, "eternal2x")
    H.click("AnalyseBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, marks="0,2", message="On 2s.")
    H.click("RefreshStatusBtn")
    assert H.marker_frames() == "0,2,5"


def test_the_threshold_slider_sets_how_big_a_change_counts(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("AnalyseBtn")
    assert "--threshold" not in H.last_command(), "the default should send nothing"
    assert "ThresholdSlider" in H.window.__items, "the threshold should be on the panel"
    H.set_slider("ThresholdSlider", 7)
    assert H.widgets["ThresholdValue"].Text == "7"
    H.click("AnalyseBtn")
    assert "--threshold 0.008000" in H.last_command()
    conf = (direct / "Installer" / "Eternal2x.conf").read_text()
    assert "threshold=7" in conf


def test_a_trimmed_clip_counts_its_markers_from_its_left_edge(lua, direct):
    """Resolve keys the markers from the media's start: M pressed 10 frames
    into a clip trimmed 20 frames in reads back as frame 30."""
    H = load(lua, ui_script(direct))
    H.left_offset, H.source_frames = 20, lua.table(20, 67)
    follow(H)
    for key in (30, 20, 5, 75):
        H.add_marker(key)
    H.click("SmoothBtn")
    assert marks_sent(direct) == "0,10", "the trimmed-off frames count for nothing"


def test_analyse_marks_a_trimmed_clip_where_its_frames_are(lua, direct):
    H = load(lua, ui_script(direct))
    H.left_offset, H.source_frames = 20, lua.table(20, 67)
    follow(H)
    H.click("AnalyseBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, marks="0,2,4", message="On 2s.")
    H.click("RefreshStatusBtn")
    assert H.marker_frames() == "20,22,24"


def test_markers_on_a_clip_at_another_rate_become_its_own_frames(lua, direct):
    """A 30 fps clip on a 23.976 timeline: markers sit on timeline frames."""
    H = load(lua, ui_script(direct))
    H.clip_fps, H.timeline_fps = "30", "23.976"
    H.left_offset, H.source_frames = 47, lua.table(59, 118)
    follow(H)
    H.add_marker(47)
    H.add_marker(57)
    H.click("SmoothBtn")
    assert marks_sent(direct) == "0,12", "timeline frame 10 shows source frame 12"
    H.click("AnalyseBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, marks="0,13,25", message="On 2s.")
    H.click("RefreshStatusBtn")
    assert H.marker_frames() == "47,57,58,67"


def test_markers_that_cannot_be_read_stop_the_render(lua, direct):
    """Rendering as if the clip were unmarked would undo the editor's choice."""
    H = load(lua, ui_script(direct))
    H.markers_fail = True
    before = H.last_command()
    H.click("SmoothBtn")
    assert H.last_command() == before, "it should not have started"
    assert "Could not read the markers" in H.status()


def test_an_analysis_with_nothing_to_mark_clears_its_old_markers(lua, direct):
    H = load(lua, ui_script(direct))
    H.add_marker(5)
    H.add_marker(9, "eternal2x")
    H.click("AnalyseBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, marks="",
                 message="Every frame is a new drawing, so there is nothing to rebuild.")
    H.click("RefreshStatusBtn")
    assert H.marker_frames() == "5"


def test_markers_on_a_render_above_its_original_count_too(lua, direct):
    """The editor sees the render on top and may mark that instead."""
    H = _stacked(lua, direct, "/r/shot_smooth/frame_[000000-000047].png", "/media/shot.mov")
    lua.execute("local H = ...; H.under_markers[1][0] = {}; H.under_markers[1][12] = {}", H)
    follow(H)
    H.add_marker(6)
    H.click("SmoothBtn")
    assert "shot.mov" in H.last_command()
    assert marks_sent(direct) == "0,6,12"
    H.click("AnalyseBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, marks="0,3", message="On 3s.")
    H.click("RefreshStatusBtn")
    assert H.marker_frames() == "6", "Analyse marks the original, not the render"


def test_markers_on_a_slowed_render_count_as_the_frames_they_show(lua, direct):
    """Three times longer, its frame 6 shows the original's frame 2, and its
    frame 120 the original's frame 40, past the original's own end."""
    H = _stacked(lua, direct, "/r/shot_3x_smooth/frame_[000000-000143].png", "/media/shot.mov")
    lua.execute("local H = ...; H.under_markers[1][0] = {}; "
                "H.src_span[2] = H.src_span[1] + 144", H)
    follow(H)
    H.add_marker(6)
    H.add_marker(120)
    H.click("SmoothBtn")
    assert marks_sent(direct) == "0,2,40"


def test_a_render_of_a_file_named_3x_is_not_taken_for_a_slowed_one(lua, direct):
    H = _stacked(lua, direct, "/r/shot_3x_smooth/frame_[000000-000047].png",
                 "/media/shot_3x.mov")
    H.add_marker(12)
    H.click("SmoothBtn")
    assert marks_sent(direct) == "12"


def test_a_marker_near_the_end_of_a_slowed_render_counts(lua, direct):
    """119 frames at 60 fps cover 47 frames of a 24 fps timeline, rounded
    down, and three times as many 142. The render's frame 141 shows the
    original's frame 117."""
    H = _stacked(lua, direct, "/r/shot_3x_smooth/frame_[000000-000356].png", "/media/shot.mov")
    lua.execute("local H = ...; H.clip_fps = '60'; H.src_span[2] = H.src_span[1] + 142; "
                "H.under_spans[1][2] = H.under_spans[1][1] + 47", H)
    follow(H)
    H.add_marker(141)
    H.click("SmoothBtn")
    assert marks_sent(direct) == "117"


def test_a_long_clip_name_leaves_room_for_its_range(lua, direct):
    """Scene pack names run long, and wrapped they hid the range line."""
    name = ("『葬送のフリーレン』第2期ノンクレジットオープニング映像／OPテーマ：「lulu.」"
            "Mrs. GREEN APPLE／フリーレンOP - TOHO animation チャンネル (1080p, h264).mp4")
    H = lua.eval(f'dofile("{HARNESS.as_posix()}")')
    H.source_range = lua.table(1.0, 3.0)
    H.load(str(ui_script(direct)), "/media/" + name)
    first, second = H.text_of("SourceLabel").split("\n")
    assert first.startswith("『葬送のフリーレン』") and first.endswith("(1080p, h264).mp4")
    assert "..." in first and len(first) < len(name)
    assert "\ufffd" not in first
    assert second == "Trimmed: 1.00 to 3.00 s"


def test_a_short_clip_name_is_left_whole(lua, direct):
    H = load(lua, ui_script(direct), clip="/media/Gojo s laughter.mp4")
    assert H.text_of("SourceLabel") == "Gojo s laughter.mp4"


def test_analyse_markers_never_read_back_as_the_drawing_before(lua, direct):
    """30 fps on 23.976: rounded both ways, a drawing at 127 read back as 126,
    the last frame of the drawing before it."""
    H = load(lua, ui_script(direct))
    H.clip_fps, H.timeline_fps, H.item_duration, H.left_offset = "30", "23.976", 200, 47
    H.source_frames = lua.table(59, 308)
    lua.execute("local H = ...; H.src_span = nil; H.tracks = nil", H)
    follow(H)
    H.click("AnalyseBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, marks="0,125,127,129", message="On 2s.")
    H.click("RefreshStatusBtn")
    H.click("SmoothBtn")
    sent = [int(m) for m in marks_sent(direct).split(",")]
    assert sent == [0, 125, 127, 130]
    drawn = (0, 125, 127, 129, 10 ** 6)
    assert all(drawn[i] <= f < drawn[i + 1] for i, f in enumerate(sent)), \
        "each mark reads as its own drawing, not the one before or after"


def test_markers_on_a_clip_whose_bounds_cannot_be_read_stop_the_render(lua, direct):
    """Guessing the left offset would read every marker as the wrong frame."""
    H = load(lua, ui_script(direct))
    H.left_offset_fails = True
    follow(H)
    H.add_marker(30)
    before = H.last_command()
    H.click("SmoothBtn")
    assert H.last_command() == before
    assert "Could not read the markers" in H.status()


def test_markers_on_a_trimmed_clip_without_its_source_range_stop_the_render(lua, direct):
    """The stage would read the whole file from its first frame, so markers
    counted from the clip's left edge would keep the wrong frames."""
    H = load(lua, ui_script(direct))
    H.left_offset = 20
    follow(H)
    H.add_marker(30)
    before = H.last_command()
    H.click("SmoothBtn")
    assert H.last_command() == before
    assert "Could not read the markers" in H.status()


def test_analyse_places_no_markers_on_a_trimmed_clip_without_its_source_range(lua, direct):
    H = load(lua, ui_script(direct))
    H.left_offset = 20
    follow(H)
    H.click("AnalyseBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, marks="0,2,4", message="On 2s.")
    H.click("RefreshStatusBtn")
    assert H.marker_frames() == ""
    assert "no markers were placed" in H.status()


def test_markers_on_an_untrimmed_clip_without_its_source_range_still_render(lua, direct):
    """On a build without the source frame calls the whole file is used, and
    an untrimmed clip's markers already count from its first frame."""
    H = load(lua, ui_script(direct))
    H.add_marker(10)
    H.click("SmoothBtn")
    assert marks_sent(direct) == "10"


def test_an_unmarked_clip_whose_bounds_cannot_be_read_still_renders(lua, direct):
    H = load(lua, ui_script(direct))
    H.left_offset_fails = True
    follow(H)
    H.click("SmoothBtn")
    assert "--marks-file" not in H.last_command()
    assert "No markers" in H.status()


def test_no_marker_list_from_resolve_stops_the_render(lua, direct):
    H = load(lua, ui_script(direct))
    H.markers_nil = True
    before = H.last_command()
    H.click("SmoothBtn")
    assert H.last_command() == before
    assert "Could not read the markers" in H.status()


def test_a_marks_file_that_fails_to_close_stops_the_render(lua, direct):
    """A buffered write can succeed and the close still lose its end."""
    H = load(lua, ui_script(direct))
    lua.execute("""
        local real = io.open
        io.open = function(path, mode)
            if tostring(path):find("%.marks$") and mode == "w" then
                return { write = function(self) return self end,
                         close = function() return nil, "disk full" end }
            end
            return real(path, mode)
        end""")
    H.add_marker(0)
    H.add_marker(6)
    before = H.last_command()
    H.click("SmoothBtn")
    assert H.last_command() == before
    assert "Could not write the marked frames" in H.status()


def test_markers_on_a_render_at_another_rate_count_as_the_original_frames(lua, direct):
    """A 30 fps original under a 24 fps render on a 24 fps timeline: the
    render's marker 12 timeline frames in is the original's frame 15."""
    H = _stacked(lua, direct, "/r/shot_smooth/frame_[000000-000047].png", "/media/shot.mov")
    lua.execute("local H = ...; H.under_fps = '30'; H.under_markers[1][0] = {}", H)
    follow(H)
    H.add_marker(12)
    H.click("SmoothBtn")
    assert marks_sent(direct) == "0,15"


def test_render_markers_past_the_end_of_the_original_do_not_count(lua, direct):
    H = _stacked(lua, direct, "/r/shot_smooth/frame_[000000-000047].png", "/media/shot.mov")
    lua.execute("local H = ...; H.under_spans[1][2] = H.under_spans[1][1] + 24", H)
    follow(H)
    H.add_marker(30)
    H.click("SmoothBtn")
    assert "--marks-file" not in H.last_command()
    assert "No markers" in H.status()


def test_found_frames_that_share_a_timeline_frame_are_reported(lua, direct):
    """30 fps on 23.976: source frame 4 is never shown, so frames 4 and 5
    share timeline frame 4. The panel says so rather than dropping one."""
    H = load(lua, ui_script(direct))
    H.clip_fps, H.timeline_fps = "30", "23.976"
    follow(H)
    H.click("AnalyseBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, marks="0,2,4,5,7", message="On 1s.")
    H.click("RefreshStatusBtn")
    assert H.marker_frames() == "0,2,4,6"
    assert "1 more found frame shares a timeline frame" in H.widgets["AnalysisLabel"].ToolTip


def test_ntsc_rates_count_exactly_over_a_long_clip(lua, direct):
    """23.976 is 24000/1001. Rounded, frame 1001 of a 24 fps timeline read
    as 999, the drawing before the 1000 it shows."""
    H = load(lua, ui_script(direct))
    H.clip_fps, H.timeline_fps, H.item_duration = "23.976", "24", 1200
    lua.execute("local H = ...; H.src_span = nil; H.tracks = nil", H)
    follow(H)
    H.add_marker(1001)
    H.click("SmoothBtn")
    assert marks_sent(direct) == "1000"
    H.clip_fps, H.timeline_fps = "30", "29.97"
    lua.execute("local H = ...; H.markers = {}", H)
    H.add_marker(999)
    H.click("SmoothBtn")
    assert marks_sent(direct) == "999", "rounded, 999 timeline frames came out as 1000"


def test_several_shared_frames_read_as_a_plural(lua, direct):
    H = load(lua, ui_script(direct))
    H.clip_fps, H.timeline_fps = "60", "24"
    follow(H)
    H.click("AnalyseBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, marks="0,3,5,6,7", message="On 1s.")
    H.click("RefreshStatusBtn")
    text = H.widgets["AnalysisLabel"].ToolTip
    assert "2 more found frames share timeline frames with others, as the clip runs faster" in text


# --------------------------------------------------------------------------
# Render chime
# --------------------------------------------------------------------------

CHIME_HELPER = "play_render_chime.py"
CHIME_PYTHON = "C:/Program Files/Python 311/python.exe"


def chime_tree(tmp_path, repo_root, chime=None):
    """A plugin folder with spaces in its path and in its Python's, with the
    chime's helper in Stages, as the plugin installs it, and Render chime as
    given, or no word of it, as in a config from before it."""
    root = _plugin_tree(tmp_path / "Plug in folder" / "Upflow2x", repo_root)
    (root / "Stages" / CHIME_HELPER).write_text("# plays the chime\n")
    lines = [f"repo_root={root}", f"python={CHIME_PYTHON}",
             "update_url=https://example.com/latest.json", "auto_update=false"]
    if chime is not None:
        lines.append(f"render_chime={'true' if chime else 'false'}")
    (root / "Installer" / "Eternal2x.conf").write_text("\n".join(lines) + "\n")
    return root


def on_a_mac(lua):
    lua.execute('package.config = "/" .. package.config:sub(2)')


def chimes(H):
    """The chime commands the panel ran on the macOS path."""
    return [c for c in H.commands.values() if CHIME_HELPER in c]


def land_a_render(H, root, **fields):
    """Render, and the render finishes, read by the timer as a person waits."""
    import time
    press(H, "SmoothBtn")
    status = {"fraction": 1.0, "done": True, "ok": True, "message": "Rendered.",
              "import_path": "/tmp/out.mp4", "updated": time.time()}
    status.update(fields)
    write_status(root, **status)
    H.fire("PollTimer", "Timeout")


def test_render_chime_is_a_settings_checkbox_off_by_default_and_saved_at_once(
        lua, tmp_path, repo_root):
    root = chime_tree(tmp_path, repo_root)
    H = load(lua, ui_script(root))
    assert shown(H) == MAIN_VIEW, "the main view changed"
    press(H, "SettingsBtn")
    assert "ChimeCB" in shown(H)
    assert H.text_of("ChimeCB") == "Render chime" and not H.widgets["ChimeCB"].Checked
    H.set_checkbox("ChimeCB", True)
    assert conf_value(root, "render_chime") == "true"
    H = load(lua, ui_script(root))
    assert H.widgets["ChimeCB"].Checked is True
    press(H, "SettingsBtn")
    H.set_checkbox("ChimeCB", False)
    assert conf_value(root, "render_chime") == "false"


def test_a_render_that_lands_chimes_once(lua, tmp_path, repo_root):
    root = chime_tree(tmp_path, repo_root, chime=True)
    on_a_mac(lua)
    H = load(lua, ui_script(root))
    land_a_render(H, root)
    assert "placed on V2 above the original" in H.status()
    # The plugin folder as its config gives it, and the helper under it.
    helper = f"{root}/Stages/{CHIME_HELPER}"
    assert chimes(H) == [f"'{CHIME_PYTHON}' '{helper}' >/dev/null 2>&1 &"]
    H.fire("PollTimer", "Timeout")
    refresh_in_settings(H)
    assert len(chimes(H)) == 1, "it chimed again for the same render"
    H = load(lua, ui_script(root))
    refresh_in_settings(H)
    assert chimes(H) == [], "a reopened panel chimed for a render already in"


def test_a_render_brought_in_with_refresh_chimes_once(lua, tmp_path, repo_root):
    """Finished while the panel was closed, and brought in by hand."""
    root = chime_tree(tmp_path, repo_root, chime=True)
    write_status(root, fraction=1.0, done=True, ok=True, message="Done.",
                 import_path=str(root / "shot_smooth.mp4"))
    on_a_mac(lua)
    H = load(lua, ui_script(root))
    assert chimes(H) == []
    press(H, "RefreshStatusBtn")
    assert H.imported_count() == 1 and len(chimes(H)) == 1
    refresh_in_settings(H)
    assert len(chimes(H)) == 1


def test_a_render_added_at_the_end_of_another_timeline_chimes(lua, tmp_path, repo_root):
    root = chime_tree(tmp_path, repo_root, chime=True)
    on_a_mac(lua)
    H = load(lua, ui_script(root))
    press(H, "SmoothBtn")
    H.timeline_id = "another timeline"
    import time
    write_status(root, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path="/tmp/out.mp4", updated=time.time())
    H.fire("PollTimer", "Timeout")
    assert "added to the end of the timeline" in H.status() and len(chimes(H)) == 1


@pytest.mark.parametrize("case", ["off", "failed", "stopped", "analysis", "media pool only",
                                  "import refused", "no render path", "still running",
                                  "helper missing"])
def test_no_chime_unless_a_render_lands_with_render_chime_on(lua, tmp_path, repo_root, case):
    import time
    root = chime_tree(tmp_path, repo_root, chime=None if case == "off" else True)
    if case == "helper missing":
        (root / "Stages" / CHIME_HELPER).unlink()
    on_a_mac(lua)
    H = load(lua, ui_script(root))
    if case == "analysis":
        press(H, "SettingsBtn")
        press(H, "AnalyseBtn")
        write_status(root, fraction=1.0, done=True, ok=True, message="Animated on 2s.",
                     marks="0,2", updated=time.time())
        H.fire("PollTimer", "Timeout")
        assert H.status() == "Check the markers, then press Render."
    elif case == "failed":
        land_a_render(H, root, ok=False, message="The clip's media is missing.")
    elif case == "stopped":
        land_a_render(H, root, ok=False, message="Stopped.")
    elif case == "media pool only":
        H.fps_sticks = False
        land_a_render(H, root, import_path="/tmp/out/frame_000000.png",
                      sequence_pattern="/tmp/out/frame_%06d.png", sequence_frames=120,
                      fps=60.0)
        assert "in the media pool, not on the timeline" in H.status()
    elif case == "import refused":
        H.import_works = False
        land_a_render(H, root)
        assert "would not import" in H.status()
    elif case == "no render path":
        land_a_render(H, root, import_path="")
    elif case == "still running":
        land_a_render(H, root, done=False, ok=False, fraction=0.6)
        assert H.text_of("CancelBtn") == "Stop"
    else:
        land_a_render(H, root)
        assert "above the original" in H.status()
    assert chimes(H) == []


def test_on_windows_the_chime_starts_with_no_window_from_a_script_of_its_own(
        lua, tmp_path, repo_root):
    root = chime_tree(tmp_path, repo_root, chime=True)
    lua.execute('package.config = "\\\\" .. package.config:sub(2)')
    lua.execute(FAKE_FFI)
    H = load(lua, ui_script(root))
    land_a_render(H, root)
    created = [lua.eval("CREATED")[i] for i in range(1, len(lua.eval("CREATED")) + 1)]
    started = [c for c in created if c.line.endswith(" /c .\\.upflow2x_chime.cmd")]
    assert len(started) == 1
    assert started[0].flags == 0x08000000, "it would open a window"
    assert started[0].cwd == str(root).replace("/", "\\")
    script = (root / ".upflow2x_chime.cmd").read_bytes().decode("utf-8")
    helper = str(root / "Stages" / CHIME_HELPER).replace("/", "\\")
    assert script == ('@echo off\r\nchcp 65001 >nul\r\n'
                      f'"{CHIME_PYTHON}" "{helper}" >nul 2>&1\r\n')
    assert not any(CHIME_HELPER in c or "chime" in c for c in H.commands.values()), \
        "it also ran in a console"
    assert CHIME_HELPER not in run_file(root, "script").read_text(), "it used the run's script"


def test_on_windows_without_a_hidden_start_there_is_no_chime(lua, tmp_path, repo_root):
    """A minimised console for a sound is worse than no sound."""
    root = chime_tree(tmp_path, repo_root, chime=True)
    lua.execute('package.config = "\\\\" .. package.config:sub(2)')
    H = load(lua, ui_script(root))
    land_a_render(H, root)
    assert "above the original" in H.status()
    assert not (root / ".upflow2x_chime.cmd").exists()
    assert not any("chime" in c for c in H.commands.values())


# --------------------------------------------------------------------------
# the window's width, as Resolve 20.3.1 measured it
# --------------------------------------------------------------------------

PANEL_WIDTH = 380


def load_native(lua, script, size):
    """The panel in a window Resolve opened at `size` rather than its Geometry."""
    H = lua.eval(f'dofile("{HARNESS.as_posix()}")')
    H.native_size = lua.table(*size)
    H.load(str(script), "/media/shot.mov")
    return H


def asked_widths(H):
    return [H.resizes[i][1] for i in range(1, len(H.resizes) + 1)]


def test_the_window_can_never_be_narrower_than_the_panel(lua, direct):
    H = load(lua, ui_script(direct))
    least = H.window.__attrs.MinimumSize
    assert least is not None and least[1] == PANEL_WIDTH
    # Left to the layout, so each page and state is as tall as it needs.
    assert least[2] == 0


def test_a_window_that_opens_narrow_gets_the_panels_width(lua, direct):
    """Resolve opened it 211 wide, with Settings and Render cut off at 370,
    and the panel took that width back and kept it."""
    H = load_native(lua, ui_script(direct), (211, 249))
    assert len(H.resizes) >= 1, "nothing put the width right as it opened"
    assert H.size[1] == PANEL_WIDTH
    H.fire("PollTimer", "Timeout")
    assert H.size[1] == PANEL_WIDTH and set(asked_widths(H)) == {PANEL_WIDTH}


def test_clip_row_holds_window_width_even_when_it_wraps(lua, direct):
    H = load(lua, ui_script(direct))
    label = H.widgets["SourceLabel"]
    initial_height = label.MinimumSize[2]
    assert label.MinimumSize[1] == PANEL_WIDTH - 20
    H.under_playhead = False
    follow(H)
    assert label.MinimumSize[1] == PANEL_WIDTH - 20
    assert label.MinimumSize[2] > initial_height


@pytest.mark.parametrize("step", ["settings", "back", "busy", "recovered"])
def test_no_page_or_state_asks_for_a_window_narrower_than_the_panel(lua, direct, step):
    import time
    if step == "recovered":
        write_status(direct, fraction=0.3, updated=time.time() - 10 * 60)
    H = load_native(lua, ui_script(direct), (211, 249))
    if step == "recovered":
        assert {"Status", "CancelBtn"} <= shown(H)
    if step in ("settings", "back"):
        press(H, "SettingsBtn")
    if step == "back":
        press(H, "BackBtn")
    if step == "busy":
        press(H, "SmoothBtn")
        assert "CancelBtn" in shown(H)
    H.fire("PollTimer", "Timeout")
    assert asked_widths(H) and min(asked_widths(H)) == PANEL_WIDTH, asked_widths(H)
    assert H.size[1] == PANEL_WIDTH


def test_a_window_made_wider_stays_wider(lua, direct):
    import time
    H = load_native(lua, ui_script(direct), (520, 300))
    press(H, "SettingsBtn")
    press(H, "BackBtn")
    press(H, "SmoothBtn")
    write_status(direct, fraction=0.5, updated=time.time())
    H.fire("PollTimer", "Timeout")
    assert set(asked_widths(H)) == {520}


# --------------------------------------------------------------------------
# Render chime: saved from the event that changed it
# --------------------------------------------------------------------------

def count_conf_writes(lua, H):
    lua.globals().H = H
    lua.execute("""
        local real_open = io.open
        H.conf_writes = 0
        io.open = function(path, mode)
            if tostring(path):find("Eternal2x%.conf$") and mode == "w" then
                H.conf_writes = H.conf_writes + 1
            end
            return real_open(path, mode)
        end""")


def test_render_chime_ticked_by_accessibility_is_saved_at_once(lua, tmp_path, repo_root):
    """Windows' accessibility Toggle ticks the box with no click. The panel
    only listened for clicks, so the tick showed and nothing was saved."""
    root = chime_tree(tmp_path, repo_root)
    H = load(lua, ui_script(root))
    press(H, "SettingsBtn")
    H.toggle("ChimeCB", True)
    assert conf_value(root, "render_chime") == "true"
    H = load(lua, ui_script(root))
    assert H.widgets["ChimeCB"].Checked is True
    press(H, "SettingsBtn")
    H.toggle("ChimeCB", False)
    assert conf_value(root, "render_chime") == "false"
    H = load(lua, ui_script(root))
    assert not H.widgets["ChimeCB"].Checked


def test_render_chime_asks_for_both_events_it_handles(lua, tmp_path, repo_root):
    """Only Clicked reaches a handler unless the Events attribute asks for more."""
    H = load(lua, ui_script(chime_tree(tmp_path, repo_root)))
    events = H.widgets["ChimeCB"].Events
    assert events is not None and events.Toggled is True and events.Clicked is True


def test_a_click_on_render_chime_saves_it_once_and_nothing_else(lua, tmp_path, repo_root):
    root = chime_tree(tmp_path, repo_root)
    H = load(lua, ui_script(root))
    press(H, "SettingsBtn")
    count_conf_writes(lua, H)
    H.set_checkbox("ChimeCB", True)
    assert H.conf_writes == 1, "a click comes as Toggled and Clicked, and was saved twice"
    assert conf_value(root, "render_chime") == "true"
    # The other settings are saved as the panel has them, unchanged.
    assert [conf_value(root, k) for k in ("upscale", "interpolate", "format", "threshold")] == [
        "true", "true", "png", "5"]
    assert H.widgets["UpscaleCB"].Checked and H.widgets["InterpCB"].Checked


def test_render_chime_follows_the_state_its_event_gives(lua, tmp_path, repo_root):
    """The event's On is the state the tick changed to."""
    root = chime_tree(tmp_path, repo_root)
    H = load(lua, ui_script(root))
    press(H, "SettingsBtn")
    lua.globals().H = H
    lua.execute('H.widgets.ChimeCB.Checked = true; H.window.On.ChimeCB.Toggled({ On = true })')
    assert conf_value(root, "render_chime") == "true"
    lua.execute('H.widgets.ChimeCB.Checked = false; H.window.On.ChimeCB.Toggled({})')
    assert conf_value(root, "render_chime") == "false", "with no On, the box's own state counts"


# --------------------------------------------------------------------------
# Render chime: a render Stop finds finished lands without a sound
# --------------------------------------------------------------------------

@pytest.mark.parametrize("press_at", ["first press", "armed press"])
def test_a_render_stop_finds_finished_is_imported_once_without_a_chime(
        lua, tmp_path, repo_root, press_at):
    import time
    root = chime_tree(tmp_path, repo_root, chime=True)
    on_a_mac(lua)
    H = load(lua, ui_script(root))
    press(H, "SmoothBtn")
    write_status(root, fraction=0.9, updated=time.time())
    if press_at == "armed press":
        press(H, "CancelBtn")
        assert H.status().startswith("Press Stop again")
    write_status(root, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path="/tmp/out.mp4", updated=time.time())
    press(H, "CancelBtn")
    assert H.imported_count() == 1 and "above the original" in H.status()
    assert chimes(H) == [], "pressing Stop rang the chime"
    assert not run_file(root, "stop").exists()
    H.fire("PollTimer", "Timeout")
    refresh_in_settings(H)
    assert H.imported_count() == 1 and chimes(H) == []


def test_a_render_that_lands_after_stop_was_sent_has_no_chime(lua, tmp_path, repo_root):
    """Too late to stop, it finished, and the next poll brought it in."""
    import time
    root = chime_tree(tmp_path, repo_root, chime=True)
    on_a_mac(lua)
    H = load(lua, ui_script(root))
    press(H, "SmoothBtn")
    write_status(root, fraction=0.95, updated=time.time())
    press(H, "CancelBtn")
    press(H, "CancelBtn")
    assert run_file(root, "stop").exists()
    write_status(root, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path="/tmp/out.mp4", updated=time.time())
    H.fire("PollTimer", "Timeout")
    assert H.imported_count() == 1 and chimes(H) == []
    # The next render is a new one, and rings as any other.
    land_a_render(H, root, import_path="/tmp/next.mp4")
    assert H.imported_count() == 2 and len(chimes(H)) == 1


# --------------------------------------------------------------------------
# PR 30 review: the run the panel owns, across reopen, Reset and launch
# --------------------------------------------------------------------------

STILL_GOING = "A run started earlier is still going. Its progress shows here."


def test_a_run_reopened_before_its_first_heartbeat_is_followed_not_replaced(lua, direct):
    """Python loads its packages before it writes a status. Opened again
    then, the panel took the run for gone, and Render started a second run
    over it, whose pointer hid the first one's result."""
    import time
    H = load(lua, ui_script(direct))
    press(H, "SmoothBtn")
    a = run_tag(direct)
    assert not run_file(direct, "status").exists() and not run_file(direct, "exit").exists()
    H = load(lua, ui_script(direct))
    assert H.status() == STILL_GOING
    assert {"CancelBtn", "RefreshStatusBtn"} <= shown(H) and H.text_of("CancelBtn") == "Stop"
    assert not H.enabled("SmoothBtn"), "a second render could start over the first"
    write_status(direct, fraction=0.2, updated=time.time())
    H.fire("PollTimer", "Timeout")
    assert "20%" in H.widgets["Progress"].Text
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path="/tmp/a.mp4", updated=time.time())
    H.fire("PollTimer", "Timeout")
    assert H.imported_count() == 1 and H.imported_at(1) == "/tmp/a.mp4"
    assert run_tag(direct) == a and H.command_count() == 0


def test_a_run_reopened_before_its_first_heartbeat_is_followed_only_for_a_while(lua, direct):
    H = load(lua, ui_script(direct))
    press(H, "SmoothBtn")
    H = load(lua, ui_script(direct))
    assert H.status() == STILL_GOING
    advance_clock(lua, 200)
    H.fire("PollTimer", "Timeout")
    assert H.status().startswith("Python has not started after 3 minutes")
    assert H.text_of("CancelBtn") == "Reset"
    press(H, "CancelBtn")
    assert H.status() == GAVE_UP and H.enabled("SmoothBtn")


def test_a_run_that_never_reported_is_not_waited_on_once_its_start_is_long_past(lua, direct):
    H = load(lua, ui_script(direct))
    press(H, "SmoothBtn")
    a = run_tag(direct)
    advance_clock(lua, 200)
    H = load(lua, ui_script(direct))
    assert shown(H) == MAIN_VIEW and H.enabled("SmoothBtn")
    press(H, "SmoothBtn")
    assert run_tag(direct) not in ("", a) and H.command_count() == 1


def test_a_pointer_put_back_after_a_failed_first_launch_leaves_no_pointer(lua, direct):
    """Before 0.5.2's first run there is no pointer, and so after one that
    could not start, there is none again."""
    H = load(lua, ui_script(direct))
    H.exit_code = 1
    press(H, "SmoothBtn")
    H.exit_code = 0
    assert "Could not start the run" in H.status()
    assert not (direct / ".upflow2x_run").exists()
    assert not list(direct.glob(".upflow2x_run*"))


@pytest.mark.parametrize("then", ["refresh", "reopen"])
def test_a_finished_render_reset_could_not_put_back_is_still_brought_in(lua, direct, then):
    """It finished between Reset's look and its claim. Moving it back from
    the claim failed, and Refresh Progress, which read only the status's own
    name, said no run."""
    import time
    H = load(lua, ui_script(direct))
    press(H, "SmoothBtn")
    write_status(direct, fraction=0.3, updated=time.time() - 10 * 60)
    H = load(lua, ui_script(direct))
    assert "CancelBtn" in shown(H) and H.text_of("CancelBtn") == "Reset"
    finish_during_next_click(H, direct, exited=True, import_path="/tmp/late.mp4")
    break_renames(lua, "%.reset$")
    press(H, "CancelBtn")
    mend_renames(lua)
    assert H.after_status_read is None, "the render did not finish during Reset"
    claim = Path(str(run_file(direct, "status")) + ".reset")
    assert not run_file(direct, "status").exists() and claim.exists()
    assert "Press Refresh Progress" in H.status() and "RefreshStatusBtn" in shown(H)
    if then == "reopen":
        H = load(lua, ui_script(direct))
        assert "finished while the panel was closed" in H.status()
    press(H, "RefreshStatusBtn")
    assert H.imported_count() == 1 and H.imported_at(1) == "/tmp/late.mp4"
    assert not claim.exists() and run_file(direct, "status").read_text() == ""
    refresh_in_settings(H)
    assert H.imported_count() == 1
    H = load(lua, ui_script(direct))
    assert shown(H) == MAIN_VIEW
    refresh_in_settings(H)
    assert H.imported_count() == 0


@pytest.mark.parametrize("stopped", [True, False])
def test_a_stop_sent_before_closing_keeps_its_render_silent_when_reopened(
        lua, tmp_path, repo_root, stopped):
    """Past its last Stop check, the render landed anyway after the panel had
    closed. The panel opened next had no word of the Stop, and rang. One no
    one stopped still rings."""
    import time
    root = chime_tree(tmp_path, repo_root, chime=True)
    on_a_mac(lua)
    H = load(lua, ui_script(root))
    press(H, "SmoothBtn")
    write_status(root, fraction=0.95, updated=time.time())
    if stopped:
        press(H, "CancelBtn")
        press(H, "CancelBtn")
        assert run_file(root, "stop").exists()
    write_status(root, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path="/tmp/out.mp4", updated=time.time())
    H = load(lua, ui_script(root))
    assert "finished while the panel was closed" in H.status()
    press(H, "RefreshStatusBtn")
    assert H.imported_count() == 1
    assert len(chimes(H)) == (0 if stopped else 1), "a render someone stopped rang"


def test_reset_of_a_quiet_worker_keeps_its_confirmed_stop(lua, tmp_path, repo_root):
    import time
    from Stages.resolve_smooth import StatusFile, Stopped

    root = chime_tree(tmp_path, repo_root, chime=True)
    on_a_mac(lua)
    H = load(lua, ui_script(root))
    press(H, "SmoothBtn")
    write_status(root, fraction=0.5, updated=time.time())
    press(H, "CancelBtn")
    press(H, "CancelBtn")
    write_status(root, fraction=0.5, updated=time.time() - 600)
    press(H, "RefreshStatusBtn")
    assert H.text_of("CancelBtn") == "Reset"
    press(H, "CancelBtn")
    with pytest.raises(Stopped):
        StatusFile(str(run_file(root, "status"))).check_stop()

    write_status(root, done=True, ok=True, import_path="/tmp/finished.mp4")
    reopened = load(lua, ui_script(root))
    press(reopened, "RefreshStatusBtn")
    assert reopened.imported_count() == 1 and not chimes(reopened)


def test_a_0_5_1_runs_leftover_claim_is_never_read_as_its_result(lua, direct):
    """0.5.1 reuses its names and never clears that one, so a finished status
    left aside long ago could be another run's. A new 0.5.1 crash shows."""
    import json
    claim = Path(str(run_file(direct, "status", "")) + ".reset")
    claim.write_text(json.dumps({"stage": "", "fraction": 1.0, "message": "Rendered.",
                                 "import_path": "/renders/old_smooth.mp4", "done": True,
                                 "ok": True, "updated": 0}))
    run_file(direct, "log", "").write_text("MemoryError\n")
    run_file(direct, "exit", "").write_text("1\n")
    H = load(lua, ui_script(direct))
    assert H.status() == "The last run did not finish. Press Refresh Progress to see why."
    press(H, "RefreshStatusBtn")
    assert "MemoryError" in H.status() and H.imported_count() == 0


def stall_then_reset_an_analysis(H, root):
    import time
    press(H, "SettingsBtn")
    press(H, "AnalyseBtn")
    write_status(root, fraction=0.3, updated=time.time() - 10 * 60)
    press(H, "RefreshStatusBtn")
    assert H.text_of("CancelBtn") == "Reset"
    press(H, "CancelBtn")
    assert H.status() == "Ready."


def test_an_analysis_that_reports_again_after_reset_still_marks_its_clip(lua, direct):
    """Followed again by Refresh Progress, its result was taken for a render's,
    and its drawings were never marked."""
    import time
    H = load(lua, ui_script(direct))
    stall_then_reset_an_analysis(H, direct)
    write_status(direct, fraction=0.6, updated=time.time())
    press(H, "RefreshStatusBtn")
    assert H.status() == REPORTING and H.text_of("CancelBtn") == "Stop"
    write_status(direct, fraction=1.0, done=True, ok=True, message="Animated on 2s.",
                 marks="0,2", updated=time.time())
    H.fire("PollTimer", "Timeout")
    assert H.tagged_markers() == 2
    assert H.status() == "Check the markers, then press Render."


def test_a_run_from_an_earlier_panel_that_reports_again_marks_no_clip(lua, direct):
    """Nothing says which clip it was, so the clip under the playhead is left alone."""
    import time
    H = load(lua, ui_script(direct))
    press(H, "SettingsBtn")
    press(H, "AnalyseBtn")
    write_status(direct, fraction=0.3, updated=time.time() - 10 * 60)
    H = load(lua, ui_script(direct))
    assert H.text_of("CancelBtn") == "Reset"
    write_status(direct, fraction=0.6, updated=time.time())
    press(H, "RefreshStatusBtn")
    assert H.status() == REPORTING
    write_status(direct, fraction=1.0, done=True, ok=True, message="Animated on 2s.",
                 marks="0,2", updated=time.time())
    H.fire("PollTimer", "Timeout")
    assert H.tagged_markers() == 0 and H.imported_count() == 0


@pytest.mark.parametrize("left", ["exit only", "progress, then exit"])
def test_a_run_reset_after_it_ended_is_not_reported_again_when_reopened(lua, direct, left):
    """Stop, pressed once on a run that had already exited 1, said Ready, but
    left nothing to say so, and the next panel reported the same failure."""
    import time
    H = load(lua, ui_script(direct))
    press(H, "SmoothBtn")
    if left == "progress, then exit":
        write_status(direct, fraction=0.5, updated=time.time())
    run_file(direct, "log").write_text("MemoryError\n")
    run_file(direct, "exit").write_text("1\n")
    assert H.text_of("CancelBtn") == "Stop"
    press(H, "CancelBtn")
    assert H.status() == "Ready."
    H = load(lua, ui_script(direct))
    assert shown(H) == MAIN_VIEW
    refresh_in_settings(H)
    assert H.status() == "No run in progress." and "FolderBtn" not in shown(H)
    assert run_file(direct, "exit").exists(), "the proof the run ended went"
