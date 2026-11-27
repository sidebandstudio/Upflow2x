-- Upflow2x  --  upscale and smooth hand-drawn animation
-- Resolve script panel: Workspace > Scripts > Upflow2x

-- ---------------------------------------------------------------------------
-- paths and config
-- ---------------------------------------------------------------------------

local function script_dir()
    local info = debug.getinfo(1, "S")
    local src = info.source or ""
    if src:sub(1, 1) == "@" then src = src:sub(2) end
    return (src:gsub("\\", "/")):match("(.*/)")
end

local function trim_trailing_sep(path)
    if not path then return "" end
    -- A drive or the disk's root keeps its separator. D: alone means the
    -- folder that drive was last in, and / trimmed is nothing at all.
    if path:match("^%a:[/\\]+$") or path:match("^[/\\]+$") then
        return path:sub(1, path:match("^%a:") and 3 or 1)
    end
    local out = path:gsub("[/\\]+$", "")
    return out
end

local function join_path(a, b)
    if not a or a == "" then return b end
    local last = a:sub(-1)
    if last == "/" or last == "\\" then return a .. b end
    return a .. "/" .. b
end

local function read_conf(path)
    local conf = {}
    local f = io.open(path, "r")
    if not f then return conf end
    for line in f:lines() do
        local k, v = line:match("^%s*([^=]+)%s*=%s*(.-)%s*$")
        if k and v then conf[k] = v end
    end
    f:close()
    return conf
end

local function parse_bool(value, default_value)
    if value == nil then return default_value end
    local s = tostring(value):lower()
    if s == "1" or s == "true" or s == "yes" or s == "on" then return true end
    if s == "0" or s == "false" or s == "no" or s == "off" then return false end
    return default_value
end

local function is_windows()
    return package.config:sub(1, 1) == "\\"
end

local function shell_quote(s)
    if not s then return "" end
    if is_windows() then
        -- A backslash before the closing quote escapes it, so a folder like
        -- D:\Cache\ ran on into the next argument. Doubled, it stays one.
        local body = s:gsub('"', '""'):gsub("(\\+)$", "%1%1")
        return '"' .. body .. '"'
    end
    -- Single quotes, so $, backticks and backslashes in a path stay literal.
    -- Inside double quotes the shell would expand `$HOME` in a file name, or
    -- run a backticked command.
    return "'" .. s:gsub("'", "'\\''") .. "'"
end

local function short_path(path, max_len)
    if not path then return "" end
    local limit = max_len or 56
    if #path <= limit then return path end
    return "..." .. path:sub(#path - limit + 4)
end

local function basename(path)
    if not path then return "" end
    return (path:gsub("\\", "/")):match("([^/]+)$") or path
end

-- A file name cut to one line of the Source card, keeping its start and end.
-- Long scene pack names wrapped and pushed the clip's range out of the card.
-- CJK characters count double, and the cut never splits a character.
local function short_name(name, width)
    width = width or 64
    local chars, total = {}, 0
    for ch in name:gmatch("[\1-\127\194-\244][\128-\191]*") do
        chars[#chars + 1] = ch
        total = total + (#ch >= 3 and 2 or 1)
    end
    if total <= width then return name end
    local function take(from, to, step, room)
        local out, used = {}, 0
        for k = from, to, step do
            local w = #chars[k] >= 3 and 2 or 1
            if used + w > room then break end
            used = used + w
            out[#out + 1] = chars[k]
        end
        return out
    end
    local tail = take(#chars, 1, -1, 20)
    local head = take(1, #chars - #tail, 1, width - 23)
    local rest = {}
    for k = #tail, 1, -1 do rest[#rest + 1] = tail[k] end
    return table.concat(head) .. "..." .. table.concat(rest)
end

local root = trim_trailing_sep(script_dir() or "")
-- Launched through Eternal2xLauncher.lua the config lives in Resolve's Utility
-- folder rather than beside this file, so the launcher passes its path down.
local CONF_PATH = _G.ETERNAL2X_CONF or join_path(root, "Eternal2x.conf")
local conf = read_conf(CONF_PATH)
local REPO_ROOT = trim_trailing_sep(conf["repo_root"] or _G.ETERNAL2X_ROOT or root or "")
local PYTHON = conf["python"] or (is_windows() and "python" or "python3")
local UPDATE_URL = conf["update_url"] or ""
-- The repo moved to sidebandstudio, then was renamed Upflow2x. An install
-- keeps the feed it was set up with through every update, and the old ones
-- only work while GitHub redirects them, so they are swapped for the new one.
if UPDATE_URL == "https://raw.githubusercontent.com/Alitleis123/Eternal2x.com/main/update/latest.json"
        or UPDATE_URL == "https://raw.githubusercontent.com/sidebandstudio/Eternal2x.com/main/update/latest.json" then
    UPDATE_URL = "https://raw.githubusercontent.com/sidebandstudio/Upflow2x/main/update/latest.json"
end

-- Workspace > Scripts lists the launcher by its file name. Installs from
-- before the rename have it as Eternal2x.lua beside the config, and updates
-- never touch that file, so the panel renames it once, to Upflow2x.lua.
-- The old one only goes once a whole new one is in place, so a failure along
-- the way leaves the menu entry that works.
local function read_file(path)
    local f = io.open(path, "rb")
    if not f then return nil end
    local body = f:read("*a")
    f:close()
    return body
end

-- Every launcher ever installed opens with this line.
local function is_launcher(body)
    local first = body and body:match("^[^\r\n]*") or ""
    return first:find("^%-%- %w+ launcher script installed into Resolve Scripts/") ~= nil
end

local function same_path(a, b)
    local function norm(p)
        p = p:gsub("\\", "/"):gsub("/+", "/")
        return is_windows() and p:lower() or p
    end
    return norm(a) == norm(b)
end

-- A launcher that would run: the header, the call into the panel, and Lua
-- that compiles, so one cut short anywhere is refused.
local function complete_launcher(body)
    return is_launcher(body) and body:find("pcall(dofile, ui_script)", 1, true) ~= nil
        and (loadstring or load)(body) ~= nil
end

local function exists(path)
    if read_file(path) then return true end
    -- An unreadable file is still there. Renaming a file onto itself only
    -- succeeds when it exists, on macOS and Linux.
    return not is_windows() and os.rename(path, path) ~= nil
end

local function rename_launcher()
    if not _G.ETERNAL2X_CONF then return end
    local dir = CONF_PATH:match("^(.*)[/\\][^/\\]*$")
    if not dir then return end
    local old_path, new_path = join_path(dir, "Eternal2x.lua"), join_path(dir, "Upflow2x.lua")
    -- With the config beside this panel, Eternal2x.lua there is the panel.
    if same_path(old_path, join_path(REPO_ROOT, "Installer/Eternal2x.lua")) then return end
    if not is_launcher(read_file(old_path)) then return end
    local existing = read_file(new_path)
    if existing then
        -- Someone else's script, or one cut short: keep the old launcher.
        if not complete_launcher(existing) then return end
    elseif exists(new_path) then
        return
    else
        local launcher = read_file(join_path(REPO_ROOT, "Installer/Eternal2xLauncher.lua"))
        if not complete_launcher(launcher) then return end
        -- Written whole under a name of its own first, so a full disk or a
        -- second Resolve doing the same never finds half a launcher.
        local temp = new_path .. "." .. os.time() .. (tostring({}):gsub("%W", "")) .. ".part"
        local out = io.open(temp, "wb")
        if not out then return end
        local wrote = out:write(launcher)
        local closed = out:close()
        -- Windows refuses to rename onto a file that exists, but macOS
        -- replaces it, so look once more just before.
        if not (wrote and closed) or read_file(temp) ~= launcher or exists(new_path)
                or not os.rename(temp, new_path) then
            os.remove(temp)
            return
        end
        if read_file(new_path) ~= launcher then return end
    end
    os.remove(old_path)
end
pcall(rename_launcher)
local AUTO_UPDATE = parse_bool(conf["auto_update"], true)
local OUTPUT_DIR = conf["output_dir"] or ""
local DO_UPSCALE = parse_bool(conf["upscale"], true)
local DO_INTERPOLATE = parse_bool(conf["interpolate"], true)
-- How big a change Analyse counts as a new drawing, 1 to 9, 5 the default.
local THRESHOLD = math.max(1, math.min(9, math.floor(tonumber(conf["threshold"] or "5") or 5)))
local FORMAT = conf["format"] or "png"
-- A short sound when a render lands on the timeline. Off unless turned on,
-- also for a config from before it existed.
local CHIME = parse_bool(conf["render_chime"], false)

local RUN_DIR = REPO_ROOT ~= "" and REPO_ROOT or root
-- The update check runs each time the panel opens, so it logs apart from
-- renders. Sharing a log wiped the render's, the one worth sending in.
local UPDATE_LOG_PATH = join_path(RUN_DIR, ".eternal2x_update.log")
-- Opens Explorer at a render, for the same reason the run goes through a
-- script: see Show in Folder.
local REVEAL_SCRIPT = join_path(RUN_DIR, ".eternal2x_reveal.cmd")

-- Each run has files of its own, named by a tag the panel gives it:
--   status  Python's progress and result, moved into place whole each time
--   tmp     where Python writes the status before moving it
--   stop    Stop creates this, and the run ends at its next frame
--   exit    written by the shell once Python exits, however it exits. A
--           Python that cannot start never writes a status, so this is how
--           the panel tells a run still going from one that died
--   marks   the frames marked on the clip. A long clip's list does not fit on
--           a Windows command line
--   script  the command a run starts from on Windows
--   log     everything the run printed
--   reset   where Reset moves the status to read it, see claim_status
--   seen    no longer written or read. An earlier build of 0.5.2 noted here
--           that a panel had shown how the run ended. Named, so it goes with
--           the run's other files. The status, left empty, says it now
-- Python finds tmp and stop beside the status, so they follow its name. A
-- run the panel has given up on can still be writing, and with files of its
-- own it never writes over, stops or ends the run after it. Tag "" is the one
-- set of names every run had before 0.5.2, so a run from then is still found.
local function run_files(tag)
    local names
    if tag == "" then
        names = { status = ".eternal2x_status.json", tmp = ".eternal2x_status.tmp",
                  stop = ".eternal2x_status.stop", exit = ".eternal2x_exit",
                  marks = ".eternal2x_marks.txt", script = ".eternal2x_run.cmd",
                  log = ".upflow2x_last_run.log", seen = ".upflow2x_last_run.seen",
                  reset = ".eternal2x_status.json.reset" }
    else
        local base = ".upflow2x_run-" .. tag
        names = { status = base .. ".json", tmp = base .. ".tmp", stop = base .. ".stop",
                  exit = base .. ".exit", marks = base .. ".marks", script = base .. ".cmd",
                  log = base .. ".log", seen = base .. ".seen", reset = base .. ".json.reset" }
    end
    for kind, name in pairs(names) do names[kind] = join_path(RUN_DIR, name) end
    return names
end

-- Names the run the panel owns, the only one it follows, stops or imports.
-- A panel opened later reads it to find that run. Opening a file to write
-- empties it, so a new pointer is written beside it and moved into place.
local RUN_POINTER = join_path(RUN_DIR, ".upflow2x_run")
local POINTER_OLD = RUN_POINTER .. ".old"
local RUN_TAG, STATUS_PATH, STOP_PATH, EXIT_PATH, RUN_SCRIPT, LOG_PATH

local function use_run(tag)
    local files = run_files(tag)
    RUN_TAG = tag
    STATUS_PATH, STOP_PATH, EXIT_PATH = files.status, files.stop, files.exit
    RUN_SCRIPT, LOG_PATH = files.script, files.log
end
use_run((read_file(RUN_POINTER) or read_file(POINTER_OLD) or ""):match("^%s*([%w%-]*)"))

local function save_conf()
    local f = io.open(CONF_PATH, "w")
    if not f then return end
    f:write("repo_root=" .. REPO_ROOT .. "\n")
    f:write("python=" .. PYTHON .. "\n")
    f:write("update_url=" .. UPDATE_URL .. "\n")
    f:write("auto_update=" .. (AUTO_UPDATE and "true" or "false") .. "\n")
    f:write("output_dir=" .. OUTPUT_DIR .. "\n")
    f:write("upscale=" .. (DO_UPSCALE and "true" or "false") .. "\n")
    f:write("interpolate=" .. (DO_INTERPOLATE and "true" or "false") .. "\n")
    f:write("threshold=" .. tostring(THRESHOLD) .. "\n")
    f:write("format=" .. FORMAT .. "\n")
    f:write("render_chime=" .. (CHIME and "true" or "false") .. "\n")
    f:close()
end

local function read_version()
    local vf = io.open(join_path(REPO_ROOT, "VERSION"), "r")
    if not vf then return "?" end
    local v = vf:read("*l") or "?"
    vf:close()
    -- Tolerate a UTF-8 BOM left by a Windows editor.
    v = v:gsub("^\239\187\191", "")
    return v:match("^%s*(.-)%s*$") or "?"
end

local CURRENT_VERSION = read_version()

-- ---------------------------------------------------------------------------
-- Resolve
-- ---------------------------------------------------------------------------

-- A script run from Workspace > Scripts gets Resolve as a global, the way
-- Blackmagic's Lua examples use it: `resolve`, or `Resolve()`. There is no Lua
-- DaVinciResolveScript module, so require is only a last resort.
local function global(name)
    -- A plain lookup, so globals Fusion provides through a metatable count.
    local ok, value = pcall(function() return _G[name] end)
    if ok then return value end
    return nil
end

local function get_resolve()
    local r = global("resolve")
    if not r and global("Resolve") then
        local ok, got = pcall(global("Resolve"))
        if ok then r = got end
    end
    local app = global("bmd")
    if not r and app and app.scriptapp then
        local ok, got = pcall(app.scriptapp, "Resolve")
        if ok then r = got end
    end
    if not r then
        local ok, mod = pcall(require, "DaVinciResolveScript")
        if ok and type(mod) == "table" and mod.scriptapp then r = mod.scriptapp("Resolve") end
    end
    if not r then
        return nil, "Could not reach Resolve. Run this from Workspace > Scripts."
    end
    return r, nil
end

-- Where the timeline clip starts and ends within its media, in seconds from
-- the start of the file, so only that part is rebuilt. Seconds mean the same
-- whatever the timeline's rate. nil means use the whole file.
--
-- Resolve counts source frames from the start of the file, at the media's
-- own rate, but the source time from the media's start timecode, so on
-- footage that starts at 01:00:00:00 the times are an hour out. The frames
-- are used, and without an end frame the times' difference, anchored on the
-- start frame. The end frame is the last one the cut uses: on Resolve Studio
-- 20.3.1 a 119 frame cut from 1200 reported 1318. Where the timeline's rate
-- differs from the media's, the end time fell short of it, and a render from
-- the times came out a timeline frame short of the cut.
local function clip_range(item, props, rate)
    local function call(name)
        local ok, value = pcall(function() return item[name](item) end)
        return ok and tonumber(value) or nil
    end
    local fps = tonumber(tostring(props["FPS"] or ""):match("^%s*([%d%.]+)"))
    local f0 = call("GetSourceStartFrame")
    if not (fps and fps > 0 and f0) then return nil end
    local start = f0 / fps
    local f1 = call("GetSourceEndFrame")
    -- At the timeline's own rate Resolve reports the end frame as the last one
    -- used at 60 fps but one past it at 23.976 (20.3.1), so the clip's length
    -- settles it. A retimed clip's length is no guide and keeps the end frame.
    local length = call("GetDuration")
    local range = nil
    if f1 and length and rate and math.abs(rate - fps) < 0.001 and math.abs(f1 - f0 - length) <= 1 then
        range = { start = start, finish = (f0 + length) / fps }
    elseif f1 and f1 >= f0 then
        range = { start = start, finish = (f1 + 1) / fps }
    else
        local t0, t1 = call("GetSourceStartTime"), call("GetSourceEndTime")
        if t0 and t1 and t1 > t0 then range = { start = start, finish = start + (t1 - t0) } end
    end
    -- Every clip has a range, so the panel only calls one trimmed that starts
    -- after the file's first frame, or ends before its last where Resolve
    -- says how many frames the file has.
    if range then
        local frames = tonumber(tostring(props["Frames"] or ""):match("^%s*(%d+)"))
        range.trimmed = f0 > 0 or (frames ~= nil and range.finish < (frames - 0.5) / fps)
    end
    return range
end

-- A file's name without its folder or extension.
local function stem_of(path)
    return ((path:match("([^/\\]+)$") or path):gsub("%.[^.]*$", ""))
end

-- What a render is called: its folder for a sequence of frames, its file
-- name for one file. nil for a path Upflow2x does not write.
local function render_name(path)
    return path:match("([^/\\]+)[/\\]frame_[%d%[%]%-]+%.png$")
        or path:match("([^/\\]+)%.mkv$") or path:match("([^/\\]+)%.mp4$")
end

-- Whether `name` is what Stages/resolve_smooth.py calls a render of a file
-- whose name is `stem`: <stem>, a range _f<a>-<b> if it was trimmed, _<n>x
-- if it was slowed, then _smooth, then _<nn> for the second render and on.
local function rendered_from(name, stem)
    if stem == "" or name:sub(1, #stem) ~= stem then return false end
    local rest = name:sub(#stem + 1):gsub("^_f%d+%-%d+", "", 1):gsub("^_%dx", "", 1)
    return rest == "_smooth" or rest:match("^_smooth_%d%d+$") ~= nil
end

-- How many times longer than its clip a render `name` of a file `stem` is,
-- from the _<n>x that rendered_from passes over.
local function slowed_by(name, stem)
    local rest = name:sub(#stem + 1):gsub("^_f%d+%-%d+", "", 1)
    return tonumber(rest:match("^_(%d)x_smooth")) or 1
end

-- The clip a render under the playhead was made from: on a track below it,
-- starting on the same frame, its file the one the render is named after.
-- Earlier renders stacked in between are passed over. nil if there is none.
local function original_under(timeline, item, name)
    local ok, found = pcall(function()
        local start = item:GetStart()
        for track = tonumber(item:GetTrackTypeAndIndex()[2]) - 1, 1, -1 do
            for _, it in ipairs(timeline:GetItemListInTrack("video", track) or {}) do
                local mpi = it:GetStart() == start and it:GetMediaPoolItem()
                local file = mpi and (mpi:GetClipProperty() or {})["File Path"]
                if file and rendered_from(name, stem_of(file)) then return it end
            end
        end
    end)
    return ok and found or nil
end

-- The playhead as a timeline frame, the unit GetStart counts in. Resolve gives
-- it only as timecode, which numbers frames at the nominal rate, 24 for
-- 23.976. Drop-frame timecode, written with ;, skips two numbers a minute
-- (four at 59.94) except every tenth minute.
local function playhead_frame(timeline)
    local h, m, s, sep, f = tostring(timeline:GetCurrentTimecode() or "")
        :match("^(%d+):(%d+):(%d+)([:;])(%d+)$")
    if not h then return nil end
    local rate = tonumber(tostring(timeline:GetSetting("timelineFrameRate") or ""):match("[%d.]+"))
    local nominal = math.floor((rate or 24) + 0.5)
    local minutes = tonumber(h) * 60 + tonumber(m)
    local frame = (minutes * 60 + tonumber(s)) * nominal + tonumber(f)
    if sep == ";" then
        frame = frame - math.floor(nominal / 15) * (minutes - math.floor(minutes / 10))
    end
    return frame
end

local function clip_is_on(it)
    return not (it.GetClipEnabled and it:GetClipEnabled() == false)
end

local function track_is_on(timeline, track)
    return not (timeline.GetIsTrackEnabled and timeline:GetIsTrackEnabled("video", track) == false)
end

-- GetCurrentVideoItem is meant to be the clip that shows under the playhead,
-- and in Resolve 20.3.1 it passed over a video clip or track switched off.
-- It still returned a render switched off on the track above its original.
-- So a clip it returns switched off is passed over for the top one switched
-- on, on a track switched on. With none, the top clip is kept.
local function shown_item(timeline, top)
    local kind = top.GetTrackTypeAndIndex and top:GetTrackTypeAndIndex()
    local track = kind and tonumber(kind[2])
    if clip_is_on(top) and (not track or track_is_on(timeline, track)) then return top end
    local frame = playhead_frame(timeline)
    if not frame then return top end
    for n = timeline:GetTrackCount("video"), 1, -1 do
        if track_is_on(timeline, n) then
            for _, it in ipairs(timeline:GetItemListInTrack("video", n) or {}) do
                if it:GetStart() <= frame and frame < it:GetEnd() and clip_is_on(it) then
                    return it
                end
            end
        end
    end
    return top
end

-- The rates Resolve shows for 24000/1001 and its kin. It counts with the
-- exact rate, which over a long clip comes out a frame different. Any other
-- rate, 23.98 say, is what it says.
local NTSC = { { 23.976, 24000 / 1001 }, { 29.97, 30000 / 1001 }, { 47.952, 48000 / 1001 },
               { 59.94, 60000 / 1001 }, { 119.88, 120000 / 1001 } }

local function exact_rate(text)
    local r = tonumber(tostring(text or ""):match("^%s*([%d%.]+)"))
    if not r then return nil end
    for _, pair in ipairs(NTSC) do
        if math.abs(r - pair[1]) < 1e-6 then return pair[2] end
    end
    return r
end

-- How the clip's markers line up with its frames. Resolve counts them in
-- timeline frames from the start of the media, not from the clip's left edge,
-- so a trimmed clip's first frame is at its left offset. A clip at another
-- rate than the timeline has `per` source frames to each timeline frame.
-- Measured on Resolve 20.3.1. Guessed bounds would read every marker as the
-- wrong frame, so a clip whose bounds Resolve will not give is `unreadable`.
local function marked_clip(item, timeline, props)
    local ok, clip = pcall(function()
        local fps, rate = exact_rate(props["FPS"]), exact_rate(timeline:GetSetting("timelineFrameRate"))
        if not (fps and rate and fps > 0 and rate > 0) then error("no frame rates") end
        local per = fps / rate
        local origin
        if item.GetLeftOffset then
            origin = tonumber(item:GetLeftOffset())
        else
            origin = tonumber(item:GetSourceStartFrame()) / per
        end
        local length = tonumber(item:GetDuration())
        if not (origin and length) then error("no clip bounds") end
        return { item = item, origin = math.floor(origin + 0.5), per = per, length = length }
    end)
    return ok and clip or { item = item, unreadable = true }
end

local function find_clip_path()
    local resolve, err = get_resolve()
    if not resolve then return nil, err end
    local project = resolve:GetProjectManager():GetCurrentProject()
    if not project then return nil, "No project is open." end
    local timeline = project:GetCurrentTimeline()
    if not timeline then return nil, "No timeline is open." end

    -- Resolve's API has no timeline selection. GetCurrentVideoItem is the clip
    -- under the playhead, so that is the one Upflow2x works on.
    local item = nil
    if timeline.GetCurrentVideoItem then
        item = timeline:GetCurrentVideoItem()
    end
    if not item then
        return nil, "No clip under the playhead. Move the playhead over a clip on the timeline."
    end
    -- A failed lookup keeps the top clip, as before it was made.
    local looked, shown = pcall(shown_item, timeline, item)
    if looked and shown then item = shown end

    local mpi = item:GetMediaPoolItem()
    if not mpi then return nil, "That item has no source media." end
    local props = mpi:GetClipProperty() or {}
    local path = props["File Path"]
    if not path or path == "" then return nil, "Could not read the clip's file path." end

    -- A render placed above its original is the top clip under the playhead
    -- from then on, even switched off. Smoothing it would smooth the render
    -- again, so the clip it was made from is used instead.
    local name = render_name(path)
    local below = name and original_under(timeline, item, name)
    local render = nil
    if below then
        -- Markers an editor puts on the render, the clip they see, count too.
        -- A slowed one shows each frame of the original that many frames on.
        render = marked_clip(item, timeline, props)
        item = below
        props = below:GetMediaPoolItem():GetClipProperty() or {}
        path = props["File Path"]
        render.slow = slowed_by(name, stem_of(path or ""))
    end

    -- Where the clip sits, so the result can go on a track above it, lined up
    -- with it. Without these calls, as before Resolve 18, the result goes at
    -- the end of the timeline instead.
    local place = nil
    pcall(function()
        local kind = item:GetTrackTypeAndIndex()
        place = { track = tonumber(kind[2]), start = tonumber(item:GetStart()),
                  duration = tonumber(item:GetDuration()), timeline = timeline:GetUniqueId(),
                  item = item:GetUniqueId() }
        place.source_start = tonumber(item:GetSourceStartFrame())
    end)
    if place and not (place.track and place.start and place.duration and place.timeline
                      and place.item) then
        place = nil
    end
    local rate = nil
    pcall(function() rate = tonumber(timeline:GetSetting("timelineFrameRate")) end)
    local range = clip_range(item, props, rate)
    local clip = marked_clip(item, timeline, props)
    -- Without the part of the file a trimmed clip uses, the stage reads the
    -- whole file from its first frame, and every marker would land on the
    -- wrong frame of it.
    if not range and not clip.unreadable and clip.origin ~= 0 then clip.unreadable = true end
    clip.also = render
    return path, nil, range, place, clip
end

-- Any API call above can raise, and this runs while the panel opens, where an
-- error would stop the window from ever appearing.
local function selected_clip_path()
    local ok, path, err, range, place, item = pcall(find_clip_path)
    if not ok then return nil, "Could not read the clip from Resolve: " .. tostring(path) end
    return path, err, range, place, item
end

-- ---------------------------------------------------------------------------
-- running stages
-- ---------------------------------------------------------------------------

local function build_command(module_name, args, background, log)
    local cd = (is_windows() and "cd /d " or "cd ") .. shell_quote(REPO_ROOT)
    local run = shell_quote(PYTHON) .. " -m " .. module_name .. (args or "")
    local redirect = " > " .. shell_quote(log or LOG_PATH) .. " 2>&1"
    if not background then
        return cd .. " && " .. run .. redirect
    end
    if is_windows() then
        -- Nesting quoted paths inside `cmd /c "..."` flips cmd's quote state,
        -- so the inner paths end up unquoted and an & in a file name splits
        -- the command. Write the command to a script and start that instead.
        -- % is doubled because a batch file expands it. The redirection sits
        -- inside the script, so it applies to the child process and not to
        -- `start`, which would leave the log empty. chcp 65001 makes cmd read
        -- the rest of the script as UTF-8, so non-ASCII paths survive.
        -- Binary mode keeps Lua from turning \r\n into \r\r\n. The exit code
        -- line puts the redirection first, because `echo 2> file` would
        -- redirect stderr instead of writing 2. A script not written whole
        -- would start a shell with no Python, so nothing starts.
        local f = io.open(RUN_SCRIPT, "wb")
        if not f then return nil end
        local wrote = f:write("@echo off\r\nchcp 65001 >nul\r\n", (cd:gsub("%%", "%%%%")), "\r\n",
                ((run .. redirect):gsub("%%", "%%%%")), "\r\n",
                ">", (shell_quote(EXIT_PATH):gsub("%%", "%%%%")), " echo %ERRORLEVEL%\r\n")
        if not (f:close() and wrote) then return nil end
        -- /MIN gives the run its own minimised window. With /B it shared the
        -- console os.execute opens, which stayed on screen for the whole
        -- render, and closing it killed the render without a word.
        -- It starts in the plugin folder and runs the script by name. Given
        -- the full path, `cmd /c` strips its quotes when it holds & and
        -- splits there, and `call` would double a ^ in it.
        local dir = (REPO_ROOT ~= "" and REPO_ROOT or root):gsub("/", "\\")
        return "start \"Upflow2x\" /D " .. shell_quote(dir) .. " /MIN cmd /c .\\"
            .. basename(RUN_SCRIPT)
    end
    -- The subshell backgrounds the whole run, and records how it ended.
    return "(" .. cd .. " && " .. run .. redirect .. "; echo $? > "
        .. shell_quote(EXIT_PATH) .. ") &"
end

-- cmd expands %NAME% in a command line even inside quotes, and nothing
-- escapes it there, so a plugin folder whose path holds a % cannot be handed
-- to it. The batch file the render runs from doubles them, but `start /D` and
-- `cd /d` see the path as typed.
local function launch_problem()
    if is_windows() and (REPO_ROOT ~= "" and REPO_ROOT or root):find("%%") then
        return "The plugin folder's path contains %, which Windows cannot start Python "
            .. "from. Move the Upflow2x folder to a path without %, then re-run the installer."
    end
    return nil
end

-- On Windows, os.execute runs its command in a console window that flashes up
-- for as long as the command takes, as on every update check. Started with
-- CreateProcessW and CREATE_NO_WINDOW, cmd gets a console nobody sees, and
-- the programs it runs share it. The command line is the one os.execute
-- builds, so quoting is unchanged, and a render still opens its own
-- minimised window through `start`. UTF-16 also carries non-ASCII paths that
-- os.execute's ANSI code page cannot. Without LuaJIT's ffi, os.execute runs.
local hidden_execute = nil
local hidden_start = nil
if is_windows() then
    pcall(function()
        local ffi = require("ffi")
        -- Declared once per Lua state; a second cdef of the same names fails.
        if not pcall(ffi.typeof, "E2X_STARTUPINFOW") then
            ffi.cdef[[
                typedef struct {
                    uint32_t cb; void *reserved, *desktop, *title;
                    uint32_t x, y, xsize, ysize, xchars, ychars, fill, flags;
                    uint16_t show, reserved2; void *reserved3, *input, *output, *error;
                } E2X_STARTUPINFOW;
                typedef struct { void *process, *thread; uint32_t pid, tid; } E2X_PROCESS_INFORMATION;
                int CreateProcessW(const wchar_t *app, wchar_t *cmdline, void *pattr, void *tattr,
                                   int inherit, uint32_t flags, void *env, const wchar_t *cwd,
                                   E2X_STARTUPINFOW *si, E2X_PROCESS_INFORMATION *pi);
                uint32_t WaitForSingleObject(void *handle, uint32_t ms);
                int GetExitCodeProcess(void *process, uint32_t *code);
                int CloseHandle(void *handle);
                int MultiByteToWideChar(uint32_t page, uint32_t flags, const char *s, int n,
                                        wchar_t *out, int size);
            ]]
        end
        local k32 = ffi.load("kernel32")
        hidden_execute = function(cmd)
            local line = (os.getenv("ComSpec") or "cmd.exe") .. " /c " .. cmd
            local size = k32.MultiByteToWideChar(65001, 0, line, #line, nil, 0)
            if size <= 0 then return nil end
            local wide = ffi.new("wchar_t[?]", size + 1)
            k32.MultiByteToWideChar(65001, 0, line, #line, wide, size)
            local si = ffi.new("E2X_STARTUPINFOW")
            si.cb = ffi.sizeof(si)
            local pi = ffi.new("E2X_PROCESS_INFORMATION")
            local code = ffi.new("uint32_t[1]")
            if k32.CreateProcessW(nil, wide, nil, nil, 0, 0x08000000, nil, nil, si, pi) == 0 then
                return nil
            end
            -- Started, so it must not run a second time through os.execute,
            -- whatever goes wrong while waiting for it.
            pcall(function()
                k32.WaitForSingleObject(pi.process, 0xFFFFFFFF)
                k32.GetExitCodeProcess(pi.process, code)
            end)
            k32.CloseHandle(pi.thread)
            k32.CloseHandle(pi.process)
            return true, tonumber(code[0])
        end
        -- Starts `cmd` in `dir` and leaves it running, with no window.
        hidden_start = function(cmd, dir)
            local function wide(s)
                local size = k32.MultiByteToWideChar(65001, 0, s, #s, nil, 0)
                if size <= 0 then return nil end
                local out = ffi.new("wchar_t[?]", size + 1)
                k32.MultiByteToWideChar(65001, 0, s, #s, out, size)
                return out
            end
            local line = wide((os.getenv("ComSpec") or "cmd.exe") .. " /c " .. cmd)
            local cwd = wide(dir)
            if not line or not cwd then return false end
            local si = ffi.new("E2X_STARTUPINFOW")
            si.cb = ffi.sizeof(si)
            local pi = ffi.new("E2X_PROCESS_INFORMATION")
            if k32.CreateProcessW(nil, line, nil, nil, 0, 0x08000000, nil, cwd, si, pi) == 0 then
                return false
            end
            k32.CloseHandle(pi.thread)
            k32.CloseHandle(pi.process)
            return true
        end
    end)
end

-- Runs a command and returns its exit code, like os.execute.
local function execute(cmd)
    if hidden_execute then
        local ok, started, code = pcall(hidden_execute, cmd)
        if ok and started then return code end
    end
    return os.execute(cmd)
end

local function run_blocking(module_name, args, log)
    local cmd = build_command(module_name, args, false, log)
    print("[Upflow2x] " .. cmd)
    local ok = execute(cmd)
    return (ok == true or ok == 0)
end

-- Runs a script in the plugin folder by name, as a render does. With no
-- window, there is none to close by mistake, which would end the run without
-- a word. Where that cannot start, `fallback` opens a minimised one.
local function start_script(script, fallback)
    if hidden_start then
        local dir = (REPO_ROOT ~= "" and REPO_ROOT or root):gsub("/", "\\")
        local ok, started = pcall(hidden_start, ".\\" .. basename(script), dir)
        if ok and started then return true end
    end
    local ok = execute(fallback)
    return (ok == true or ok == 0)
end

-- True once the run is on its way. Python reports from then on.
local function run_background(module_name, args)
    local cmd = build_command(module_name, args, true)
    if not cmd then return false end
    print("[Upflow2x] " .. cmd)
    if is_windows() then return start_script(RUN_SCRIPT, cmd) end
    local ok = execute(cmd)
    return (ok == true or ok == 0)
end

local function read_log(path)
    local f = io.open(path or LOG_PATH, "r")
    if not f then return "" end
    local s = f:read("*a") or ""
    f:close()
    return s
end

-- The last line of the run log worth showing, such as Python's final error.
local function last_log_line(path)
    local last = nil
    for line in read_log(path):gmatch("[^\r\n]+") do
        if line:match("%S") then last = line end
    end
    return last and (last:match("^%s*(.-)%s*$")) or nil
end

-- Encode a code point as UTF-8, for \u escapes in the status file.
local function utf8_char(cp)
    if cp < 0x80 then return string.char(cp) end
    if cp < 0x800 then
        return string.char(0xC0 + math.floor(cp / 0x40), 0x80 + cp % 0x40)
    end
    if cp < 0x10000 then
        return string.char(0xE0 + math.floor(cp / 0x1000),
                           0x80 + math.floor(cp / 0x40) % 0x40, 0x80 + cp % 0x40)
    end
    return string.char(0xF0 + math.floor(cp / 0x40000),
                       0x80 + math.floor(cp / 0x1000) % 0x40,
                       0x80 + math.floor(cp / 0x40) % 0x40, 0x80 + cp % 0x40)
end

local JSON_ESCAPES = { ['"'] = '"', ["\\"] = "\\", ["/"] = "/", b = "\b",
                       f = "\f", n = "\n", r = "\r", t = "\t" }

-- Read one string value out of a flat JSON object, decoding its escapes.
-- File paths can hold quotes, backslashes and non-ASCII letters, and a plain
-- pattern match would hand Resolve the escaped form, which it cannot find.
local function json_string(s, key)
    local start = select(2, s:find('"' .. key .. '"%s*:%s*"'))
    if not start then return nil end
    local out, i = {}, start + 1
    while i <= #s do
        local c = s:sub(i, i)
        if c == '"' then return table.concat(out) end
        if c == "\\" then
            local e = s:sub(i + 1, i + 1)
            if e == "u" then
                local cp = tonumber(s:sub(i + 2, i + 5), 16) or 0xFFFD
                i = i + 6
                -- A surrogate pair carries a character outside the BMP.
                if cp >= 0xD800 and cp <= 0xDBFF and s:sub(i, i + 1) == "\\u" then
                    local low = tonumber(s:sub(i + 2, i + 5), 16) or 0
                    cp = 0x10000 + (cp - 0xD800) * 0x400 + (low - 0xDC00)
                    i = i + 6
                end
                out[#out + 1] = utf8_char(cp)
            else
                out[#out + 1] = JSON_ESCAPES[e] or e
                i = i + 2
            end
        else
            out[#out + 1] = c
            i = i + 1
        end
    end
    return nil
end

-- The status file is written by Stages/resolve_smooth.py as a flat JSON
-- object with a fixed shape, so the panel reads it without a full parser.
local function parse_status(s)
    if s == "" then return nil end
    return {
        stage    = json_string(s, "stage") or "",
        message  = json_string(s, "message") or "",
        fraction = tonumber(s:match('"fraction"%s*:%s*([%-%d%.eE]+)')) or 0,
        import_path = json_string(s, "import_path") or "",
        sequence_pattern = json_string(s, "sequence_pattern") or "",
        sequence_frames = tonumber(s:match('"sequence_frames"%s*:%s*(%d+)')) or 0,
        marks    = json_string(s, "marks") or "",
        upscaled = s:match('"upscaled"%s*:%s*(%a+)') == "true",
        slow_motion = tonumber(s:match('"slow_motion"%s*:%s*(%d+)')) or 1,
        updated  = tonumber(s:match('"updated"%s*:%s*([%d%.eE]+)')),
        fps      = tonumber(s:match('"fps"%s*:%s*([%d%.]+)')) or 0,
        done     = s:match('"done"%s*:%s*(%a+)') == "true",
        ok       = s:match('"ok"%s*:%s*(%a+)') == "true",
    }
end

local function read_status_at(path)
    local f = io.open(path, "r")
    if not f then return nil end
    local s = f:read("*a") or ""
    f:close()
    return parse_status(s)
end

-- Reset reads a status by moving it aside. If moving it back fails, it is
-- read from there while the status itself is missing, until the run is dealt
-- with. Not for a run from before 0.5.2: 0.5.1 reuses its names and never
-- clears that one, so it could be an older run's. Returns what it holds.
local function claimed()
    if RUN_TAG == "" or exists(STATUS_PATH) then return nil end
    return read_file(STATUS_PATH .. ".reset")
end

local function read_status()
    local st = read_status_at(STATUS_PATH)
    if st then return st end
    -- Only a finished one: Reset deletes a claim of a run still going.
    local kept = parse_status(claimed() or "")
    return kept and kept.done and kept or nil
end

-- The exit code of the last background run, or nil while it is still going.
local function read_exit_code()
    local f = io.open(EXIT_PATH, "r")
    if not f then return nil end
    local s = f:read("*a") or ""
    f:close()
    return s:match("%-?%d+") or "?"
end

-- ---------------------------------------------------------------------------
-- window
-- ---------------------------------------------------------------------------

local ui = fu.UIManager
local disp = bmd.UIDispatcher(ui)

-- No AVI: Resolve refuses its lossless FFV1 there, so that file is MKV. A
-- saved avi from an older version falls back to the image sequence.
local FORMATS = { "png", "mp4", "mkv" }
local FORMAT_LABELS = { "Image sequence", "MP4", "MKV" }

local function index_of(list, value)
    for i, v in ipairs(list) do
        if v == value then return i - 1 end
    end
    return 0
end

-- Resolve's own greys, so the panel sits beside the Inspector without
-- standing out, and the amber from upflow2x.com for what is pressed or on.
local C = {
    panel = "#28282e", field = "#1d1d21", raise = "#37363c", hover = "#423f47",
    line = "#45444b", hair = "#35343a",
    text = "#dcdcd8", soft = "#a3a2a8", mute = "#7a7b80",
    amber = "#f2a93b", amber_hover = "#f6bd63", amber_down = "#d9932c", on_amber = "#17171a",
}
-- "$name" in a style sheet becomes that colour.
local function themed(sheet)
    return (sheet:gsub("%$([%w_]+)", C))
end

-- Resolve's UI Manager ignores #name selectors in a window's style sheet, so
-- every widget that looks different from the rest carries its own. Text the
-- panel needs read is $text or $soft, at least 4.5:1 on the panel's grey.
-- $mute is for what is switched off. Amber fills only Render, and marks a
-- switch that is on and a Stop waiting for its second press.
-- A focused control gets a light border, or an underline for a link, for
-- whoever moves through the panel with Tab.
local TITLE = themed("QLabel { font-size: 17px; font-weight: 600; color: $text; }")
local BADGE = themed([[QLabel { background-color: $amber; color: $on_amber; border-radius: 4px;
    padding: 1px 5px; font-family: Consolas, Menlo, monospace; font-size: 10px; font-weight: 700; }]])
local VERSION = themed("QLabel { color: $soft; font-size: 11px; }")
local INFO = themed([[QLabel { color: $soft; border: 1px solid $line; border-radius: 8px;
    font-family: Georgia, serif; font-size: 10px; font-weight: 700; }]])
-- The clip's name, plain under the title.
local SOURCE = themed("QLabel { color: $text; font-size: 12px; }")
local NOTE = themed("QLabel { color: $soft; font-size: 11px; }")
-- What a switch does to the render, under it.
local MODE = themed("QLabel { color: $soft; font-size: 11px; padding: 0px 2px; }")
local STATUS = themed("QLabel { color: $text; font-size: 11px; }")
local FIELD_LABEL = themed("QLabel { color: $soft; font-size: 12px; }")
local META = themed("QLabel { color: $soft; font-size: 11px; }")
local PROGRESS = themed("QLabel { color: $soft; font-size: 11px; font-family: Consolas, Menlo, monospace; }")
local START = themed([[
    QPushButton { background-color: $amber; border: none; border-radius: 8px;
        color: $on_amber; font-size: 15px; font-weight: 700; }
    QPushButton:hover { background-color: $amber_hover; }
    QPushButton:pressed { background-color: $amber_down; }
    QPushButton:focus { border: 2px solid $text; }
    QPushButton:disabled { background-color: $raise; color: $mute; border: none; }
]])
-- The whole tile is the switch, since a checkbox only takes a click on its
-- dot and words. On, its name turns bold and its border amber, and the line
-- under it starts with On, so the state never rests on colour alone.
local TOGGLE = themed([[
    QPushButton { background-color: $raise; border: 1px solid $line; border-radius: 8px;
        padding: 0px 12px; min-height: 34px; font-size: 13px; color: $soft; }
    QPushButton:hover { background-color: $hover; }
    QPushButton:checked { border-color: $amber; color: $text; font-weight: 700;
        background-color: rgba(242, 169, 59, 28); }
    QPushButton:checked:hover { background-color: rgba(242, 169, 59, 48); }
    QPushButton:focus { border-color: $text; }
]])
local BUTTON = themed([[
    QPushButton { background-color: $raise; border: 1px solid $line; border-radius: 6px;
        min-height: 24px; padding: 3px 12px; font-size: 12px; color: $text; }
    QPushButton:hover { background-color: $hover; }
    QPushButton:pressed { background-color: $field; }
    QPushButton:focus { border-color: $text; }
    QPushButton:disabled { background-color: $panel; border-color: $hair; color: $mute; }
]])
local LINK = themed([[
    QPushButton { background-color: transparent; border: none; padding: 2px 4px;
        min-height: 18px; font-size: 11px; color: $soft; }
    QPushButton:hover { color: $amber; }
    QPushButton:focus { color: $text; text-decoration: underline; }
    QPushButton:disabled { color: $mute; }
]])
-- Stop after one press, waiting for the second.
local LINK_ASKING = themed([[
    QPushButton { background-color: transparent; border: none; padding: 2px 4px;
        min-height: 18px; font-size: 11px; color: $amber; font-weight: 700; }
    QPushButton:focus { text-decoration: underline; }
]])
local WINDOW = themed([[
    QWidget { background-color: $panel; color: $text; font-size: 12px; }
    QComboBox {
        background-color: $field; border: 1px solid $hair; border-radius: 5px;
        padding: 4px 8px; min-height: 18px; color: $text;
    }
    QComboBox:hover { border-color: $line; }
    QComboBox:focus { border-color: $text; }
    QComboBox QAbstractItemView {
        background-color: $field; border: 1px solid $line; outline: none;
        selection-background-color: $soft; selection-color: $field;
    }
    QSlider::groove:horizontal { height: 3px; border-radius: 1px; background-color: $field; }
    QSlider::sub-page:horizontal { border-radius: 1px; background-color: $soft; }
    QSlider::handle:horizontal {
        width: 12px; height: 12px; margin: -5px 0; border-radius: 6px; background-color: $text;
    }
    QSlider::handle:horizontal:hover { background-color: #ffffff; }
    QSlider::handle:horizontal:focus {
        width: 10px; height: 10px; margin: -6px 0; border-radius: 7px;
        border: 2px solid $soft; background-color: #ffffff;
    }
    QToolTip { background-color: $field; color: $text; border: 1px solid $line; padding: 5px; }
]])
-- A thin bar, amber up to how far the run has got. Empty, it keeps its
-- space but shows nothing, so the panel doesn't jump when a run starts.
local function bar_style(fraction)
    if not fraction then
        return "QLabel { background-color: transparent; }"
    end
    local f = math.max(0.001, math.min(0.998, fraction))
    -- Qt reads only a decimal point, and some locales format with a comma.
    local function stop(value) return (string.format("%.3f", value):gsub(",", ".")) end
    return themed(string.format([[QLabel { border-radius: 2px; background-color: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 0, stop: 0 $amber, stop: %s $amber,
        stop: %s $field, stop: 1 $field); }]], stop(f), stop(f + 0.001)))
end

local RENDER_LABEL = "\xE2\x96\xB6  Render"
local ANALYSIS_HINT = "Mark the drawings with M, or let Analyse do it."

-- What each part does, shown when the pointer rests on it, so the panel
-- itself only needs names.
local TIPS = {
    clip = "Upflow2x works on the clip under the playhead, since Resolve doesn't tell scripts which clip is selected. This box follows the playhead. With the playhead in a gap, it keeps the last clip.",
    detection = "Analyse finds the real drawings and puts a marker on each one. Delete a marker to leave its frame out, or add one with M. Render uses the marked frames, and finds the drawings itself on a clip with none.",
    threshold = "How big a change Analyse counts as a new drawing. Lower catches small moves, like only a mouth. Higher counts only big ones.",
    render = "Render draws on your GPU and puts the result on the track above the clip. Resolve stays usable while it runs.",
    upscale = "On Windows, Real-ESRGAN can draw a 2x file using DirectML. On Mac, or if the model cannot run, Resolve uses Super Scale on import.\nIt shows on a timeline bigger than the clip.",
    interp = "Draw the missing in-betweens with the RIFE network, on the GPU on Windows. Its first use downloads about 50 MB.\nThe render plays at a third of the clip's speed, ready to time remap.\nOff keeps every drawing on its frame and the clip's length, and draws nothing, to upscale only.",
    format = "Image sequence is lossless and imports most reliably. MP4 is one small file but lossy, and MKV is one lossless file. All are 8-bit RGB.",
    -- The same list as Installer/install_upflow2x.py's REQUIREMENTS.
    needs = "Upflow2x needs:\n"
        .. "DaVinci Resolve Studio 18 or newer. The free edition blocks script panels.\n"
        .. "Windows 10 or 11, or macOS 12 or newer.\n"
        .. "A DirectX 12 GPU on Windows draws and upscales. Without one, and on a Mac, "
        .. "the in-betweens run on the CPU and Super Scale upscales.\n"
        .. "An internet connection for setup and the first render, which downloads about 50 MB.",
    folder = "Where renders are saved, a folder for each project. By default that's an Upflow2x folder beside the project's Resolve cache, or Resolve's own folder. Choose a folder to keep them somewhere else, like a drive with more space.",
}

-- A Settings row: its name, then its control. The name carries the tip.
local function row(id, name, control, tip)
    return ui:HGroup{ Weight = 0, Spacing = 8,
        ui:Label{ ID = id, Text = name, StyleSheet = FIELD_LABEL, Weight = 0.25, ToolTip = tip,
                  Hidden = true, Alignment = { AlignLeft = true, AlignVCenter = true } },
        control,
    }
end

-- Under a switch, whether it is on and what that does, in words show_modes writes.
local function mode_line(id, tip)
    return ui:Label{ ID = id, Text = "", StyleSheet = MODE, ToolTip = tip, Weight = 0,
                     MinimumSize = { 0, 16 }, Alignment = { AlignHCenter = true, AlignTop = true } }
end

-- Two pages in one window, so nothing opens on top of it. The main page is
-- what a render needs: the clip, the two switches and Render. Settings holds
-- the rest, behind a button in the title row that becomes Back there. The
-- title row and the clip's name show on both, and so does how a run is
-- going, under either page, so a run started from Settings stays in sight.
-- Each widget a page or a run shows or hides has an ID, as only those can
-- be reached once the window is built. They start as the main page shows
-- them, so nothing flashes as it opens.
-- The width every row is laid out for. Resolve 20.3.1 opened the window 211
-- wide, and the rows kept this width with Settings and Render cut off, so
-- the window is held to it as a minimum, width only: a zero height leaves
-- the height to the layout, page by page.
local PANEL_WIDTH = 380
local win = disp:AddWindow({
    ID = "Eternal2x",
    WindowTitle = "Upflow2x  v" .. CURRENT_VERSION,
    Geometry = { 100, 100, PANEL_WIDTH, 198 },
    MinimumSize = { PANEL_WIDTH, 0 },
    StyleSheet = WINDOW,
}, ui:VGroup{
    Spacing = 10,

    ui:HGroup{ Weight = 0, Spacing = 6,
        ui:Label{ ID = "Title", Text = "Upflow", StyleSheet = TITLE, Weight = 0 },
        -- Held to its own height, or it stretches to the Settings button's.
        ui:Label{ ID = "Badge", Text = "2X", StyleSheet = BADGE, Weight = 0,
                  Alignment = { AlignCenter = true }, MaximumSize = { 16777215, 18 } },
        ui:HGap(0, 1),
        ui:Button{ ID = "SettingsBtn", Text = "Settings", StyleSheet = BUTTON, Weight = 0,
                   ToolTip = "Analyse, threshold, output, where renders are saved, and updates." },
        ui:Button{ ID = "BackBtn", Text = "Back", StyleSheet = BUTTON, Weight = 0, Hidden = true,
                   ToolTip = "Back to Render. Settings are saved as you change them." },
    },

    ui:Label{ ID = "SourceLabel", Text = "No clip under the playhead.", StyleSheet = SOURCE,
              WordWrap = true, MinimumSize = { PANEL_WIDTH - 20, 18 }, ToolTip = TIPS.clip, Weight = 0 },

    -- The main page.
    ui:HGroup{ Weight = 0, Spacing = 8,
        ui:VGroup{ Weight = 1, Spacing = 4,
            ui:Button{ ID = "UpscaleCB", Text = "Upscaling", Checkable = true, Checked = DO_UPSCALE,
                       StyleSheet = TOGGLE, ToolTip = TIPS.upscale, Weight = 0 },
            mode_line("UpscaleMode", TIPS.upscale),
        },
        ui:VGroup{ Weight = 1, Spacing = 4,
            ui:Button{ ID = "InterpCB", Text = "Interpolate", Checkable = true,
                       Checked = DO_INTERPOLATE, StyleSheet = TOGGLE, ToolTip = TIPS.interp,
                       Weight = 0 },
            mode_line("InterpMode", TIPS.interp),
        },
    },
    ui:Button{ ID = "SmoothBtn", Text = RENDER_LABEL, StyleSheet = START, ToolTip = TIPS.render,
               Weight = 0, MinimumSize = { 0, 44 }, MaximumSize = { 16777215, 44 } },

    -- Settings. Analyse and the threshold it uses share a row. Render finds
    -- the drawings itself on a clip with no markers, so neither is needed.
    ui:HGroup{ Weight = 0, Spacing = 8,
        ui:Button{ ID = "AnalyseBtn", Text = "Analyse", StyleSheet = BUTTON, ToolTip = TIPS.detection,
                   Weight = 0, MinimumSize = { 84, 0 }, Hidden = true },
        ui:Label{ ID = "ThresholdLabel", Text = "Threshold", StyleSheet = FIELD_LABEL, Weight = 0,
                  ToolTip = TIPS.threshold, Hidden = true,
                  Alignment = { AlignRight = true, AlignVCenter = true } },
        ui:Slider{ ID = "ThresholdSlider", Weight = 1, Minimum = 1, Maximum = 9, PageStep = 1,
                   Value = THRESHOLD, Orientation = "Horizontal", ToolTip = TIPS.threshold,
                   Hidden = true },
        ui:Label{ ID = "ThresholdValue", Text = tostring(THRESHOLD), StyleSheet = FIELD_LABEL,
                  Weight = 0, MinimumSize = { 10, 0 }, Hidden = true,
                  Alignment = { AlignRight = true, AlignVCenter = true } },
    },
    ui:Label{ ID = "AnalysisLabel", Text = ANALYSIS_HINT, StyleSheet = NOTE, WordWrap = true,
              Weight = 0, Hidden = true },
    row("OutputLabel", "Output", ui:ComboBox{ ID = "FormatCombo", Weight = 0.75, Hidden = true,
                                              ToolTip = TIPS.format }, TIPS.format),
    row("FolderLabel", "Save to", ui:ComboBox{ ID = "FolderCombo", Weight = 0.75, Hidden = true },
        TIPS.folder),
    -- A checkbox, so its tick says whether it is on.
    -- Toggled is asked for as well as Clicked, as only Clicked comes unasked.
    ui:CheckBox{ ID = "ChimeCB", Text = "Render chime", Checked = CHIME, Weight = 0, Hidden = true,
                 Events = { Clicked = true, Toggled = true },
                 ToolTip = "Play a short sound when a finished render lands on the timeline.",
                 StyleSheet = themed([[
                     QCheckBox { color: $text; font-size: 12px; spacing: 8px; }
                     QCheckBox:focus { text-decoration: underline; }
                 ]]) },
    ui:HGroup{ Weight = 0, Spacing = 8,
        ui:Label{ ID = "SubTitle", Text = "Upflow2x v" .. CURRENT_VERSION, StyleSheet = VERSION,
                  Weight = 0, Hidden = true, Alignment = { AlignLeft = true, AlignVCenter = true } },
        ui:Label{ ID = "NeedsInfo", Text = "i", ToolTip = TIPS.needs, StyleSheet = INFO, Weight = 0,
                  Hidden = true, Alignment = { AlignCenter = true },
                  MinimumSize = { 16, 16 }, MaximumSize = { 16, 16 } },
        ui:HGap(0, 1),
        ui:Button{ ID = "UpdateBtn", Text = "Check for Updates", StyleSheet = BUTTON, Weight = 0,
                   Hidden = true },
    },

    -- How a run is going, under either page, each part only while it has
    -- something to say.
    ui:Label{ ID = "ProgressBar", Text = "", StyleSheet = bar_style(nil), Weight = 0, Hidden = true,
              MinimumSize = { 0, 4 }, MaximumSize = { 16777215, 4 } },
    -- Two lines while a run goes, set by set_busy.
    ui:Label{ ID = "Progress", Text = "", StyleSheet = PROGRESS, WordWrap = true, Weight = 0,
              Hidden = true, MinimumSize = { 0, 16 } },
    -- As tall as its message, set by set_status. Resolve does not grow a
    -- wrapped label to fit, so a finished render's message was cut off top
    -- and bottom.
    ui:Label{ ID = "Status", Text = "Ready.", StyleSheet = STATUS, WordWrap = true, Weight = 0,
              Hidden = true, MinimumSize = { 0, 17 },
              Alignment = { AlignLeft = true, AlignTop = true } },
    ui:HGroup{ Weight = 0, Spacing = 2,
        ui:Button{ ID = "CompareBtn", Text = "Show Original", StyleSheet = LINK, Enabled = false, Weight = 0,
                   Hidden = true,
                   ToolTip = "Switch the render off and on to compare it with the original under it." },
        ui:Button{ ID = "FolderBtn", Text = "Show in Folder", StyleSheet = LINK, Enabled = false, Weight = 0,
                   Hidden = true, ToolTip = "Open the folder the render was saved to." },
        ui:HGap(0, 1),
        ui:Button{ ID = "RefreshStatusBtn", Text = "Refresh Progress", StyleSheet = LINK, Weight = 0,
                   Hidden = true, ToolTip = "Read how far the run has got, if the panel stopped updating." },
        ui:Button{ ID = "CancelBtn", Text = "Reset", StyleSheet = LINK, Weight = 0, Hidden = true,
                   ToolTip = "Clear a run that stopped without finishing." },
    },
    ui:Label{ ID = "Meta", Text = "", StyleSheet = META, WordWrap = true, Weight = 0, Hidden = true },
    -- Any height the window has to spare goes here, below everything.
    ui:VGap(0, 1),
})


local items = win:GetItems()

for _, label in ipairs(FORMAT_LABELS) do items.FormatCombo:AddItem(label) end
items.FormatCombo.CurrentIndex = index_of(FORMATS, FORMAT)

-- Save to: Resolve's folder, the folder chosen last, and a way to choose one.
-- Refilling it changes the selection, which is not a choice.
local FILLING_FOLDERS = false
local function fill_folders()
    FILLING_FOLDERS = true
    items.FolderCombo:Clear()
    items.FolderCombo:AddItem("Resolve's folder")
    if OUTPUT_DIR ~= "" then items.FolderCombo:AddItem(short_path(OUTPUT_DIR, 30)) end
    items.FolderCombo:AddItem("Choose a folder...")
    items.FolderCombo.CurrentIndex = OUTPUT_DIR ~= "" and 1 or 0
    items.FolderCombo.ToolTip = OUTPUT_DIR ~= "" and OUTPUT_DIR
        or "Beside the project's Resolve cache, or in Resolve's own folder."
    FILLING_FOLDERS = false
end
fill_folders()

-- Under each switch, On or Off and what that does, read from the switch
-- itself so a setting restored from the config shows too. Upscaling asks for
-- a 2x upscale, drawn by Real-ESRGAN where it can run and left to Super
-- Scale where it cannot, so the words name the factor, not a file size or a
-- GPU. Interpolate on makes the render three times as long, and off keeps
-- the clip's length. The tips say the rest.
local DOT = " \xC2\xB7 "
local function show_modes()
    items.UpscaleMode.Text = items.UpscaleCB.Checked and ("On" .. DOT .. "2x")
        or ("Off" .. DOT .. "original size")
    items.InterpMode.Text = items.InterpCB.Checked and ("On" .. DOT .. "3x longer")
        or ("Off" .. DOT .. "same length")
end
show_modes()

-- ---------------------------------------------------------------------------
-- state
-- ---------------------------------------------------------------------------

local SOURCE_PATH = nil
local SOURCE_RANGE = nil
-- The clip on the timeline, whose markers say which frames count, and the one
-- the running analysis will mark, as marked_clip describes them.
local SOURCE_ITEM = nil
local ANALYSED_ITEM = nil
-- Where the source clip sits on the timeline, and where the running render's
-- clip sat when it started, since the playhead can move while it renders.
local SOURCE_PLACE = nil
local RUN_PLACE = nil
-- The last render on disk, and the clip it became on the timeline.
local LAST_OUTPUT = nil
local LAST_ITEM = nil
local RUNNING = false
-- When this panel started its run, which counts as the first heartbeat until
-- Python, a second or two later, writes its own.
local LAUNCHED_AT = nil
local IMPORTED = false
-- "analysis" or "render", for the run this panel started. nil for one it
-- picked up from an earlier panel, since nothing says which that is.
local RUN_KIND = nil

-- The window: which page it shows, what shows on it, kept here rather than
-- read back, and how tall the labels that wrap are.
--   page      "main" or "settings"
--   timed     whether a timer drives the panel. Without one, Refresh Progress
--             is the only way the clip and a run are read again, so it always
--             shows. Set once the timer is made
--   changed   something was shown or hidden, or a label's height changed
--   fit_again ask for the window's height once more on the next tick
--   ticked    a tick has reached the panel. With a timer made, `since` is
--             when the panel was ready for one
--   recover   what the run the panel owns, not followed, still needs, which
--             keeps its way in on screen until it is handled:
--               "pending"  a finished render not yet imported
--               "failed"   a failure no panel has shown, from its status or
--                          only its exit code
--               "stale"    a run that went quiet, which Reset can clear
--               "gave_up"  Reset gave up on it, and it may still finish
--   stop_sent Stop was sent to the run the panel owns, so if it lands
--             anyway, it makes no sound
local view = {
    page = "main", timed = false, changed = false, fit_again = false, ticked = false,
    source_height = 18, status_height = 17, progress_height = 16,
    -- What each page shows. Everything else shows on both, or as a run needs it.
    main = { "SettingsBtn", "UpscaleCB", "UpscaleMode", "InterpCB", "InterpMode", "SmoothBtn" },
    settings = { "BackBtn", "AnalyseBtn", "ThresholdLabel", "ThresholdSlider", "ThresholdValue",
                 "AnalysisLabel", "OutputLabel", "FormatCombo", "FolderLabel", "FolderCombo",
                 "ChimeCB", "SubTitle", "NeedsInfo", "UpdateBtn" },
    feedback = { "ProgressBar", "Progress", "Status", "CompareBtn", "FolderBtn",
                 "RefreshStatusBtn", "CancelBtn", "Meta" },
    shown = {},
}
for _, id in ipairs(view.main) do view.shown[id] = true end
for _, id in ipairs(view.settings) do view.shown[id] = false end
for _, id in ipairs(view.feedback) do view.shown[id] = false end

-- How many lines `text` wraps to at about `per_line` characters a line, for
-- labels Resolve does not grow to fit. A guess on the high side, as a spare
-- line costs less than a cut one.
local function lines_of(text, per_line)
    local lines = 0
    for part in ((text or "") .. "\n"):gmatch("([^\n]*)\n") do
        lines = lines + math.max(1, math.ceil(#part / per_line))
    end
    return math.max(1, lines)
end

-- A window keeps its size when what it shows gets smaller, so after a change
-- it is asked for the height it needs. The heights are in pixels, measured
-- on an offscreen render of these style sheets at 380 wide. Where one comes
-- out short, Qt keeps the window as tall as its layout needs. Qt lays a
-- window out on its next pass, and may hold it at the old height until
-- then, so with a timer the ask is made once more on the next tick. Nothing
-- else resizes it, so its size never moves while a run only reports progress.
local function fit_window()
    local shown, gap = view.shown, 10
    local height = (view.page == "settings" and 289 or 198) + view.source_height - 18
    if shown.Progress then height = height + 4 + view.progress_height + 2 * gap end
    if shown.Status then height = height + view.status_height + gap end
    if shown.CompareBtn or shown.FolderBtn or shown.RefreshStatusBtn or shown.CancelBtn then
        height = height + 22 + gap
    end
    if shown.Meta then height = height + lines_of(items.Meta.Text, 56) * 15 + gap end
    -- The panel's width, or the window's where someone has made it wider. A
    -- width read back is never taken when it is narrower: the window can
    -- report the size it came up at before it is laid out, and taking that
    -- kept Resolve's 211 wide window from then on.
    local width = PANEL_WIDTH
    pcall(function()
        local size = win:Size()
        local now = tonumber(size[1] or size.Width)
        if now and now > width then width = math.floor(now + 0.5) end
    end)
    pcall(function() win:Resize({ width, height }) end)
    view.fit_again = view.timed
end

-- Shows the page's controls, and of how a run is going only what has
-- something to say: the bar while there is progress, a message other than
-- Ready, or one the clip's line, or in Settings the analysis line, already
-- says, unless it answers a press, Show Original and Show in Folder once
-- they have a render, Stop while a run goes, and Reset for a run that went
-- quiet. Refresh Progress shows while a run goes or the run the panel owns
-- needs something, without a working timer, and always in Settings, as a
-- timer can stall with no sign.
local function show_what_matters()
    -- A timer made, and no tick a few seconds after the panel was ready for
    -- one, is a timer that does not reach the panel.
    if view.timed and not view.ticked and view.since and os.time() - view.since >= 3 then
        view.timed = false
    end
    local function set(id, shown)
        if view.shown[id] ~= shown then
            view.shown[id] = shown
            items[id].Hidden = not shown
            view.changed = true
        end
    end
    local settings = view.page == "settings"
    for _, id in ipairs(view.main) do set(id, not settings) end
    for _, id in ipairs(view.settings) do set(id, settings) end
    local progress = (items.Progress.Text or "") ~= ""
    set("ProgressBar", progress)
    set("Progress", progress)
    local said = items.Status.Text or ""
    set("Status", said ~= "" and said ~= "Ready."
        and (said ~= items.SourceLabel.Text or said == view.answer)
        and not (settings and said == items.AnalysisLabel.Text))
    set("CompareBtn", items.CompareBtn.Enabled == true)
    set("FolderBtn", items.FolderBtn.Enabled == true)
    set("CancelBtn", RUNNING or view.recover == "stale")
    set("RefreshStatusBtn", RUNNING or view.recover ~= nil or not view.timed or settings)
    set("Meta", (items.Meta.Text or "") ~= "")
    if view.changed then
        view.changed = false
        fit_window()
    end
end

-- With answer, the message answers a press, and shows even when the clip's
-- line already says it.
local function set_status(msg, answer)
    msg = msg or ""
    view.answer = answer and msg or nil
    items.Status.Text = msg
    -- The whole message, for one too long for the box.
    items.Status.ToolTip = msg
    local height = lines_of(msg, 56) * 15 + 2
    if view.status_height ~= height then
        view.status_height = height
        items.Status.MinimumSize = { 0, height }
        view.changed = true
    end
    print("[Upflow2x] " .. msg)
    show_what_matters()
end

local function set_meta(text)
    items.Meta.Text = text or ""
    show_what_matters()
end

-- A stage at 0% is still shown: installing the AI runtime on first use stays
-- at 0% for as long as pip takes, and a blank bar then looks stuck.
local function set_progress(fraction, stage)
    if not fraction or (fraction <= 0 and (stage or "") == "") then
        items.Progress.Text = ""
        items.ProgressBar.StyleSheet = bar_style(nil)
        show_what_matters()
        return
    end
    fraction = math.min(1, fraction)
    items.ProgressBar.StyleSheet = bar_style(fraction)
    items.Progress.Text = string.format("%d%%  %s", math.floor(fraction * 100 + 0.5), stage or "")
    show_what_matters()
end

-- Stop takes two presses. A render stopped partway deletes the frames it has
-- drawn, and Stop sits beside Refresh Progress. The second press counts for
-- this many seconds after the first, and after that Stop asks again.
local STOP_CONFIRM_FOR = 5
local STOP_ASKED = nil

-- A Stop pressed once and left goes back to plain Stop, and the status back
-- to what it said before, unless something else has been said since.
local function stop_unasked(now)
    local asked = STOP_ASKED
    if not asked or (not now and math.abs(os.time() - asked.at) <= STOP_CONFIRM_FOR) then return end
    STOP_ASKED = nil
    items.CancelBtn.StyleSheet = LINK
    if items.Status.Text == asked.prompt then set_status(asked.status) end
end

local function set_busy(busy)
    RUNNING = busy
    items.SmoothBtn.Enabled = not busy
    items.AnalyseBtn.Enabled = not busy
    items.UpdateBtn.Enabled = not busy
    items.SmoothBtn.Text = busy and "Working..." or RENDER_LABEL
    items.CancelBtn.Text = busy and "Stop" or "Reset"
    items.CancelBtn.ToolTip = busy
        and "End the run. Press twice, since a render stopped partway deletes the frames it has drawn."
        or "Clear a run that stopped without finishing."
    STOP_ASKED = nil
    items.CancelBtn.StyleSheet = LINK
    -- Two lines while a run goes, so a stage name that wraps does not move
    -- everything under it on a tick, and one once it has ended.
    local progress_height = busy and 30 or 16
    if view.progress_height ~= progress_height then
        view.progress_height = progress_height
        items.Progress.MinimumSize = { 0, progress_height }
        view.changed = true
    end
    show_what_matters()
end

-- After a run fails, Show in Folder becomes Open Log. The log says why it
-- failed, and it sits in a hidden folder nobody would find to send it in.
local SHOW_LOG = false

local function offer_log(on)
    -- Only a log with something in it, or it opens an empty page.
    SHOW_LOG = on and last_log_line() ~= nil
    items.FolderBtn.Text = SHOW_LOG and "Open Log" or "Show in Folder"
    items.FolderBtn.ToolTip = SHOW_LOG
        and ("Open the log of the run that failed, to read it or send it in.\n" .. LOG_PATH)
        or "Open the folder the render was saved to."
    items.FolderBtn.Enabled = SHOW_LOG or LAST_OUTPUT ~= nil
    show_what_matters()
end

-- Seconds as text that Python's float() reads, whatever the locale.
local function seconds(value)
    return (string.format("%.6f", value):gsub(",", "."))
end

-- The part of the file a trimmed clip uses. nil for a clip that uses all of
-- it, or when Resolve does not say, since every clip has a range.
local function range_text(range)
    if not (range and range.trimmed) then return nil end
    return (string.format("Trimmed: %.2f to %.2f s", range.start, range.finish)
            :gsub("(%d),(%d)", "%1.%2"))
end

-- The timer reads the clip twice a second, and a tip set again closes under
-- the pointer, so the line is only written when it changes. It is as tall as
-- its text, as Resolve does not grow a wrapped label to fit.
local function show_source(text, tip)
    if items.SourceLabel.Text ~= text then
        items.SourceLabel.Text = text
        local height = lines_of(text, 52) * 16 + 2
        if view.source_height ~= height then
            view.source_height = height
            items.SourceLabel.MinimumSize = { PANEL_WIDTH - 20, height }
            view.changed = true
        end
        show_what_matters()
    end
    if items.SourceLabel.ToolTip ~= tip then items.SourceLabel.ToolTip = tip end
end

-- With keep, a playhead in a gap leaves the last clip, marked as kept.
local function refresh_source(keep)
    -- The clip's name, and a second line only when there is more to know:
    -- that it is the last clip used, kept while the playhead is off every
    -- clip, so it is not taken for the clip under the playhead, and which
    -- part a trimmed clip uses.
    local function source_text(path, range, kept)
        local notes = {}
        if kept then notes[#notes + 1] = "Last usable clip" end
        notes[#notes + 1] = range_text(range)
        local name = short_name(basename(path))
        if #notes == 0 then return name end
        return name .. "\n" .. table.concat(notes, DOT)
    end
    local path, err, range, place, item = selected_clip_path()
    if not path then
        if keep and SOURCE_PATH then
            show_source(source_text(SOURCE_PATH, SOURCE_RANGE, true),
                        TIPS.clip .. "\n\n" .. SOURCE_PATH)
            return false
        end
        SOURCE_PATH = nil
        SOURCE_RANGE = nil
        SOURCE_PLACE = nil
        SOURCE_ITEM = nil
        show_source(err or "No clip under the playhead.", TIPS.clip)
        return false
    end
    -- Another clip, even one cut from the same part of the same file, or
    -- another part of the file, makes the last analysis say nothing about it.
    local was, at = SOURCE_RANGE, SOURCE_PLACE
    if path ~= SOURCE_PATH or (range and range.start) ~= (was and was.start)
            or (range and range.finish) ~= (was and was.finish)
            or (place and place.item) ~= (at and at.item)
            or (place and place.timeline) ~= (at and at.timeline) then
        items.AnalysisLabel.Text = ANALYSIS_HINT
        items.AnalysisLabel.ToolTip = ""
    end
    SOURCE_PATH = path
    SOURCE_RANGE = range
    SOURCE_PLACE = place
    SOURCE_ITEM = item
    show_source(source_text(path, range, false), TIPS.clip .. "\n\n" .. path)
    return true
end

-- Upflow2x's own markers carry this, so analysing again replaces them and
-- leaves every marker the editor placed.
local MARK_TAG = "eternal2x"

-- The frames marked on the clip, as source frames from its first frame,
-- sorted, with those marked on a render of it stacked above. Every marker on
-- the clip counts, whatever its colour. Markers on the timeline's ruler do
-- not, since editors keep beat markers there. A marker reads as the source
-- frame its timeline frame shows. nil if Resolve would not say where they are.
local function clip_marks(clip)
    if not clip then return {} end
    local out, seen = {}, {}
    for _, c in ipairs({ clip, clip.also }) do
        local ok = pcall(function()
            local markers = c.item:GetMarkers()
            if type(markers) ~= "table" then error("no markers table") end
            for key in pairs(markers) do
                local t = tonumber(key)
                if t then
                    if clip.unreadable or c.unreadable then error("no clip bounds") end
                    -- In timeline frames from the clip's first frame, which the
                    -- render shares with its original, then in the original's frames.
                    -- A slowed render runs its own length, longer than the
                    -- original's, and shows each of its frames that many on.
                    local slow = c.slow or 1
                    t = math.floor(t + 0.5) - c.origin
                    if t >= 0 and t < (slow > 1 and c.length or clip.length) then
                        local f = math.floor(t / slow * clip.per + 1e-6)
                        if not seen[f] then
                            seen[f] = true
                            out[#out + 1] = f
                        end
                    end
                end
            end
        end)
        if not ok then return nil end
    end
    table.sort(out)
    return out
end

-- Put a marker on each frame Analyse found, given as source frames from the
-- clip's first frame, after taking down the ones it put there last time. Each
-- goes on the first timeline frame that shows it, so it reads back as that
-- frame or a later one of the same drawing, never the drawing before. On a
-- timeline slower than the clip, two found frames can fall on one timeline
-- frame, which then reads as the later one. The earlier is counted instead of
-- marked. Returns how many it placed and how many shared a frame.
local function mark_drawings(clip, marks)
    local placed, shared, used = 0, 0, {}
    if clip.unreadable then return placed, shared end
    pcall(function()
        local item = clip.item
        local markers = item:GetMarkers()
        if type(markers) ~= "table" then return end
        for key, info in pairs(markers) do
            if type(info) == "table" and info.customData == MARK_TAG then
                item:DeleteMarkerAtFrame(tonumber(key))
            end
        end
        for f in marks:gmatch("%d+") do
            local t = math.ceil(tonumber(f) / clip.per - 1e-6)
            if used[t] then
                shared = shared + 1
            elseif t < clip.length then
                used[t] = true
                if item:AddMarker(clip.origin + t, "Yellow", "Upflow2x",
                                  "Delete this marker to leave the frame out.", 1, MARK_TAG) then
                    placed = placed + 1
                end
            end
        end
    end)
    return placed, shared
end

-- The open project's name and Resolve's cache folder, which decide where the
-- stage puts a render. Either can be missing, and the stage then falls back.
local function render_folder_hints()
    local name, cache = "", ""
    pcall(function()
        local project = get_resolve():GetProjectManager():GetCurrentProject()
        local got = project:GetName()
        if type(got) == "string" then name = got end
        got = project:GetSetting("perfCacheClipsLocation")
        if type(got) == "string" then cache = got end
    end)
    return name, cache
end

local function current_args(extra)
    local args = ""
    if SOURCE_PATH and SOURCE_PATH ~= "" then
        args = args .. " --video " .. shell_quote(SOURCE_PATH)
        if SOURCE_RANGE then
            args = args .. " --start-time " .. seconds(SOURCE_RANGE.start)
                        .. " --end-time " .. seconds(SOURCE_RANGE.finish)
        end
    end
    local fmt = FORMATS[(items.FormatCombo.CurrentIndex or 0) + 1] or "png"
    args = args .. " --format " .. fmt
    -- --network-upscale tells the stage this panel skips Super Scale for a
    -- render that comes back already 2x.
    if items.UpscaleCB.Checked then
        args = args .. " --network-upscale"
    else
        args = args .. " --no-upscale"
    end
    -- Every render plays at a third of the clip's speed, with all the new
    -- frames drawn, so there is more to time remap. With no in-betweens
    -- there is nothing new to draw, so an upscale alone keeps the length.
    if items.InterpCB.Checked then
        args = args .. " --slow-motion 3"
    else
        args = args .. " --no-interpolate"
    end
    if THRESHOLD ~= 5 then
        -- Each step doubles or halves how big a change counts, around the default.
        args = args .. " --threshold " .. string.format("%.6f", 0.004 * 2 ^ ((THRESHOLD - 5) / 2)):gsub(",", ".")
    end
    -- Renders go in a folder per project: in the folder chosen under Save to,
    -- or in one Upflow2x folder beside Resolve's cache, rather than among the
    -- clips they came from.
    local project, cache = render_folder_hints()
    if project ~= "" then args = args .. " --project " .. shell_quote(project) end
    if OUTPUT_DIR ~= "" then
        args = args .. " --output-dir " .. shell_quote(OUTPUT_DIR)
    elseif cache ~= "" then
        args = args .. " --cache-dir " .. shell_quote(cache)
    end
    args = args .. " --status-file " .. shell_quote(STATUS_PATH)
    return args .. (extra or "")
end

-- One clip property. Asked for a single key, Resolve can hand back the value or
-- a table holding it, depending on the build.
local function clip_property(mpi, key)
    if not mpi.GetClipProperty then return nil end
    local ok, got = pcall(function() return mpi:GetClipProperty(key) end)
    if not ok then return nil end
    if type(got) == "table" then got = got[key] end
    return got
end

-- The render is finished on disk; bring it in from here rather than from the
-- Python process, which would need Resolve's external scripting enabled.
-- An image sequence has no frame rate of its own, so Resolve gives it the
-- project's, and a 60 fps render in a 24 fps project plays at 40% speed.
-- Resolve lets a sequence's rate be changed, so set it, then read it back,
-- because a refusal is not always reported. Done before the clip is appended,
-- since its length on the timeline depends on it.
local function set_sequence_fps(mpi, fps)
    if not mpi.SetClipProperty then return "", false end
    local text = (math.abs(fps - math.floor(fps + 0.5)) < 0.001)
        and tostring(math.floor(fps + 0.5)) or string.format("%.3f", fps)
    local called, set = pcall(function() return mpi:SetClipProperty("FPS", text) end)
    -- Only a rate read back as set counts. A yes from SetClipProperty alone
    -- is not enough, and a clip placed at the wrong rate plays at the wrong
    -- speed and covers more of the timeline.
    local confirmed = false
    if called and set then
        local got = tonumber(tostring(clip_property(mpi, "FPS") or ""):match("^%s*([%d%.]+)"))
        confirmed = got ~= nil and math.abs(got - fps) <= 0.01
    end
    if confirmed then return " at " .. text .. " fps", true end
    return " (set its frame rate to " .. text .. " in Clip Attributes, or it plays at the project's)",
        false
end

-- The first entry of a list from Resolve, whatever its keys. Resolve 20.3
-- adds a number under __flags to every list, which is not an entry.
local function first_of(list)
    if type(list) ~= "table" then return nil end
    if list[1] ~= nil then return list[1] end
    for k, v in pairs(list) do
        if k ~= "__flags" then return v end
    end
    return nil
end

-- The lowest video track above the source clip with nothing over `length`
-- frames from its start, so no clip of the edit is covered or cut. Adds a
-- track when every one has something there.
local function free_track(timeline, at, length)
    local finish = at.start + length
    for track = at.track + 1, tonumber(timeline:GetTrackCount("video")) or 0 do
        local clear = true
        for _, other in ipairs(timeline:GetItemListInTrack("video", track) or {}) do
            if other:GetStart() < finish and other:GetEnd() > at.start then
                clear = false
                break
            end
        end
        if clear then return track, false end
    end
    if not timeline:AddTrack("video") then return nil end
    return tonumber(timeline:GetTrackCount("video")), true
end

-- Where the source clip is now. It can be moved while the render runs, so it
-- is found again by its id. nil if it is gone, or now uses another part of
-- its media, since the render would then not match it.
local function find_source(timeline, place)
    for track = 1, tonumber(timeline:GetTrackCount("video")) or 0 do
        for _, it in ipairs(timeline:GetItemListInTrack("video", track) or {}) do
            if it:GetUniqueId() == place.item then
                local source_start = nil
                pcall(function() source_start = tonumber(it:GetSourceStartFrame()) end)
                if tonumber(it:GetDuration()) ~= place.duration
                    or source_start ~= place.source_start then
                    return nil
                end
                return { track = track, start = tonumber(it:GetStart()) }
            end
        end
    end
    return nil
end

-- The timeline's frame rate and the clip's. nil when either is unknown.
local function rates(timeline, mpi)
    local ours, theirs = exact_rate(timeline:GetSetting("timelineFrameRate")), exact_rate(clip_property(mpi, "FPS"))
    if not (ours and theirs and ours > 0 and theirs > 0) then return nil end
    return ours, theirs
end

-- How many of the render's frames to place over the source clip's
-- `duration` timeline frames: as many as fit without running past it.
-- Resolve rounds a clip's length on the timeline down, so that is the most
-- whose length at the timeline's rate is under duration + 1. The render can
-- have a frame more than that, as Resolve reports where a clip ends
-- inconsistently. On a timeline faster than the clip it can fall a frame
-- short, since no whole number of the clip's frames fits exactly.
local function frames_covering(timeline, mpi, duration, frames)
    local ours, theirs = rates(timeline, mpi)
    if not ours then return nil end
    return math.min(frames, math.ceil((duration + 1) * theirs / ours - 1e-6) - 1)
end

-- How many timeline frames the clip covers: its frames at its own rate,
-- counted at the timeline's. nil when either rate is unknown.
local function timeline_length(timeline, mpi, frames)
    local ours, theirs = rates(timeline, mpi)
    if not ours then return nil end
    return math.ceil(frames * ours / theirs - 0.001)
end

-- Put the render on a track above the clip it came from, starting on the same
-- frame, so it lines up with the edit and the audio. nil when that cannot be
-- done: another timeline is open, the source clip is gone or was re-trimmed,
-- or a call failed. Each step is guarded on its own, so a failure after the
-- clip went in cannot lead to a second copy at the end of the timeline.
-- `slow` is how many times longer than the clip the render is.
local function place_above(pool, project, mpi, frames, slow)
    local place = RUN_PLACE
    if not (place and frames and frames > 0) then return nil end
    local ok, timeline, at, count, length = pcall(function()
        local timeline = project:GetCurrentTimeline()
        if not timeline or timeline:GetUniqueId() ~= place.timeline then return nil end
        local at = find_source(timeline, place)
        if not at then return nil end
        -- A slowed render runs past the original anyway, so all of it goes on.
        local count = slow > 1 and frames
            or frames_covering(timeline, mpi, place.duration, frames)
        return timeline, at, count, count and timeline_length(timeline, mpi, count)
    end)
    if not (ok and timeline and at and count and count > 0 and length) then return nil end

    local made, track, added = pcall(free_track, timeline, at, math.max(length, place.duration))
    if not (made and track) then return nil end
    -- Resolve 20.3 places frames startFrame to endFrame - 1: asked for 10 to
    -- 20, it puts 10 frames on the timeline.
    local appended, item = pcall(function()
        return first_of(pool:AppendToTimeline({ {
            mediaPoolItem = mpi, startFrame = 0, endFrame = count,
            trackIndex = track, recordFrame = at.start, mediaType = 1,
        } }))
    end)
    if not (appended and item) then
        if added then pcall(function() timeline:DeleteTrack("video", track) end) end
        return nil
    end
    if added then pcall(function() timeline:SetTrackName("video", track, "Upflow2x") end) end
    -- Where it really ends, in case this Resolve counts frames differently.
    local over = 0
    if slow == 1 then
        pcall(function() over = tonumber(item:GetEnd()) - (at.start + place.duration) end)
    end
    return track, item, over
end

-- The upscale only shows on a timeline bigger than the original clip. On one
-- the same size it is scaled back down. `factor` is how much bigger than its
-- file the clip plays: 2 with Super Scale, 1 for a render already drawn at 2x.
local function upscale_hint(project, mpi, factor)
    local ok, width = pcall(function()
        return project:GetCurrentTimeline():GetSetting("timelineResolutionWidth")
    end)
    local w, h = tostring(clip_property(mpi, "Resolution") or ""):match("(%d+)%s*[xX]%s*(%d+)")
    w, h, width = tonumber(w), tonumber(h), ok and tonumber(width) or nil
    if not (w and h and width) or width * 2 > w * factor then return nil end
    return string.format("To see the 2x upscale, set the timeline to %dx%d.", w * factor, h * factor)
end

local function import_result(st)
    local path = st.import_path
    if not path or path == "" then return false, "Nothing to import." end
    local resolve, err = get_resolve()
    if not resolve then return false, err end
    local project = resolve:GetProjectManager():GetCurrentProject()
    if not project then return false, "No project is open." end
    if not project.GetMediaPool then return false, "Could not reach the media pool." end
    local pool = project:GetMediaPool()
    if not pool or not pool.ImportMedia then
        return false, "This Resolve version cannot import from a script."
    end

    local function try_import(what)
        local ok, got = pcall(function() return pool:ImportMedia({ what }) end)
        if ok and type(got) == "table" and #got > 0 then return got[1] end
        return nil
    end

    -- Handed a single frame, Resolve imports a single still. A sequence needs
    -- the clipInfo form from Blackmagic's README: a printf pattern plus the
    -- first and last frame numbers. If that is refused there is no fallback,
    -- since the first frame alone would go on the timeline as a still.
    local mpi = nil
    if st.sequence_pattern ~= "" and st.sequence_frames > 0 then
        mpi = try_import({ FilePath = st.sequence_pattern, StartIndex = 0,
                           EndIndex = st.sequence_frames - 1 })
    else
        mpi = try_import(path)
    end
    if not mpi then
        -- Where it is, so it can be dragged in by hand. A sequence is its
        -- folder, not its first frame.
        local where = st.sequence_pattern ~= "" and path:match("^(.+)[/\\]") or path
        return false, "Resolve would not import it.", where
    end

    local notes = "Imported " .. basename(st.sequence_pattern ~= "" and path:match("^(.+)[/\\]")
                                         or path)
    local hint = nil
    local rate_ok = true
    if st.sequence_pattern ~= "" and st.fps > 0 then
        local said
        said, rate_ok = set_sequence_fps(mpi, st.fps)
        notes = notes .. said
    end
    if st.upscaled then
        -- Drawn at 2x already, even if Upscale was unticked since. Super
        -- Scale on top would make it 4x.
        notes = notes .. ", upscaled 2x"
        hint = upscale_hint(project, mpi, 1)
    elseif DO_UPSCALE and mpi.SetClipProperty then
        -- Resolve accepts 1, 2, 3, 4 or Auto here. "2x" is rejected. The
        -- README calls the value an integer, so try a number if text is refused.
        -- pcall's first value only says nothing raised. Resolve reports a
        -- refusal, as on the free edition, by returning false, so check that.
        local called, scaled = pcall(function() return mpi:SetClipProperty("Super Scale", "2") end)
        if not (called and scaled) then
            called, scaled = pcall(function() return mpi:SetClipProperty("Super Scale", 2) end)
        end
        notes = notes .. ((called and scaled) and ", upscaled 2x"
                          or ", upscale not applied (Super Scale needs Resolve Studio)")
        if called and scaled then hint = upscale_hint(project, mpi, 2) end
    end
    -- At the wrong rate it would play at the wrong speed and cover more of
    -- the timeline than the original, so it stays in the media pool.
    if not rate_ok then
        return true, notes .. " It is in the media pool, not on the timeline.", nil, hint
    end
    -- Whether it went onto the timeline, above its clip or at the end, and
    -- not only into the media pool.
    local landed = false
    if pool.AppendToTimeline then
        local frames = st.sequence_frames > 0 and st.sequence_frames
            or tonumber(clip_property(mpi, "Frames"))
        local track, item, over = place_above(pool, project, mpi, frames, st.slow_motion)
        if track then
            LAST_ITEM = item
            landed = true
            notes = notes .. ", placed on V" .. track .. " above the original"
            if over > 0 then
                notes = notes .. string.format(", but it runs %d frame%s past it. Trim its end.",
                                               over, over == 1 and "" or "s")
            else
                notes = notes .. "."
            end
        else
            local called, appended = pcall(function() return pool:AppendToTimeline({ mpi }) end)
            local added = called and appended and (type(appended) ~= "table" or #appended > 0)
            if added then LAST_ITEM = first_of(appended) end
            landed = added and true or false
            notes = notes .. (added and ", added to the end of the timeline."
                                    or ". Drag it onto your timeline.")
        end
    end
    return true, notes, nil, hint, landed
end

-- How long the current stage has left, from its pace since the panel first
-- saw it. Each stage counts from 0 to 1 on its own, so its start is reset.
local STAGE = { name = nil, at = 0, from = 0 }

local function time_left(st)
    if st.stage ~= STAGE.name or st.fraction < STAGE.from then
        STAGE.name, STAGE.at, STAGE.from = st.stage, os.time(), st.fraction
        return ""
    end
    local spent, done = os.time() - STAGE.at, st.fraction - STAGE.from
    if st.done or spent < 3 or done < 0.02 or st.fraction >= 1 then return "" end
    local left = spent * (1 - st.fraction) / done
    if left < 60 then return string.format("  about %d s left", math.ceil(left)) end
    return string.format("  about %d min left", math.ceil(left / 60))
end

-- The folder holding a render: a sequence is its folder, a file is itself.
local function render_location(st)
    if st.sequence_pattern ~= "" then return st.import_path:match("^(.+)[/\\]") end
    return st.import_path
end

-- With Render chime on, plays Stages/play_render_chime.py in the configured
-- Python once a finished render lands on the timeline. It starts out of
-- sight and is never waited on, and nothing says if it cannot play: a sound
-- is no reason to hold up or fail a render. On Windows it goes through a
-- script of its own, never a run's, started with no window as a run is, and
-- not at all where that cannot be done.
local function chime()
    if not CHIME or REPO_ROOT == "" then return end
    local helper = join_path(REPO_ROOT, "Stages/play_render_chime.py")
    if not exists(helper) then return end
    pcall(function()
        if not is_windows() then
            execute(shell_quote(PYTHON) .. " " .. shell_quote(helper) .. " >/dev/null 2>&1 &")
            return
        end
        if not hidden_start or launch_problem() then return end
        local script = join_path(RUN_DIR, ".upflow2x_chime.cmd")
        local line = shell_quote(PYTHON) .. " " .. shell_quote((helper:gsub("/", "\\")))
            .. " >nul 2>&1"
        local f = io.open(script, "wb")
        if not f then return end
        f:write("@echo off\r\nchcp 65001 >nul\r\n", (line:gsub("%%", "%%%%")), "\r\n")
        f:close()
        hidden_start(".\\" .. basename(script), (RUN_DIR:gsub("/", "\\")))
    end)
end

-- Marks the run the panel owns as one whose end a panel has shown, by leaving
-- its status file in place, empty. Every panel, 0.5.1's too, reads an empty
-- status as none, so it is no progress, render or failure to either, and a
-- crash's last progress goes with it. The shell writes the exit code later,
-- 1 after a Stop, and this keeps a panel opened then from taking that for a
-- failure no one saw. It says nothing of another run: a tagged run's files
-- are its own, and 0.5.1, which reuses one set of names, deletes the status
-- before each run it starts.
local function mark_seen()
    local f = io.open(STATUS_PATH, "w")
    if f then f:close() end
    -- A claim Reset could not move back is dealt with too.
    os.remove(STATUS_PATH .. ".reset")
end

-- Whether a panel has shown how the run the panel owns ended.
local function end_shown()
    return read_file(STATUS_PATH) == "" or claimed() == ""
end

-- With quiet, as when Stop or Reset finds the run finished, a render that
-- lands makes no sound. Someone pressing Stop is not waiting for a chime.
local function apply_status(st, quiet)
    if not st then return false end
    set_progress(st.fraction, st.stage .. time_left(st))
    -- Until the run is done its message only ever says Starting, and shown on
    -- every poll it would hide what the panel itself has to say.
    if st.done and st.message ~= "" then set_status(st.message) end
    if st.done then
        -- Handled now. Left as it was, Refresh Progress in a reopened panel
        -- would import the same render again, so it is emptied.
        mark_seen()
        view.recover = nil
        set_busy(false)
        if not st.ok then
            set_progress(0, nil)
            local why = st.message ~= "" and st.message
                or "Failed. See the console for details."
            if RUN_KIND == "analysis" then items.AnalysisLabel.Text = why end
            -- A Stop is what the editor asked for, with nothing to look into.
            offer_log(st.message ~= "Stopped.")
            set_status(why)
            return true
        end
        set_progress(1.0, "Finished")
        if RUN_KIND == "analysis" then
            local placed, shared = 0, 0
            if ANALYSED_ITEM then placed, shared = mark_drawings(ANALYSED_ITEM, st.marks) end
            local report = st.message
            if placed > 0 then
                report = report .. " Marked " .. placed .. " frames on the clip."
                if shared > 0 then
                    report = report .. " " .. shared
                        .. (shared == 1 and " more found frame shares a timeline frame with another"
                            or " more found frames share timeline frames with others")
                        .. ", as the clip runs faster than the timeline."
                end
                local on = st.message:match("[Oo]n (%d)s")
                items.AnalysisLabel.Text = "Placed " .. placed .. " markers."
                    .. (on and (" Animated on " .. on .. "s.") or "")
            elseif st.message ~= "" then
                items.AnalysisLabel.Text = st.message
            end
            items.AnalysisLabel.ToolTip = report
            if placed > 0 then
                set_status("Check the markers, then press Render.")
            elseif st.marks ~= "" and ANALYSED_ITEM and ANALYSED_ITEM.unreadable then
                set_status("Analysis complete. Resolve would not say where the clip's frames are, so no markers were placed.")
            else
                set_status("Analysis complete.")
            end
            return true
        end
        if st.import_path ~= "" and not IMPORTED then
            IMPORTED = true
            LAST_OUTPUT = render_location(st)
            offer_log(false)
            local ok, notes, where, hint, landed = import_result(st)
            -- A slowed render runs past its original, so off it shows the next shot.
            items.CompareBtn.Enabled = LAST_ITEM ~= nil and st.slow_motion == 1
            items.CompareBtn.Text = "Show Original"
            if ok then
                set_status(st.message .. " " .. notes)
                -- Three lines of status leave no room for a tip as well.
                if hint then set_meta(hint) end
                -- Only once it is on the timeline, and once, since the
                -- status is emptied above and nothing imports it again. Not
                -- for a run someone asked to stop, which finished anyway. A
                -- render past its last Stop check leaves the stop file in
                -- place as it lands, so a Stop sent from an earlier panel
                -- counts too.
                if landed and not quiet and not view.stop_sent and not exists(STOP_PATH) then
                    chime()
                end
            elseif where then
                -- The status box fits three lines, too few for a path as well.
                set_meta(short_path(where, 68))
                print("[Upflow2x] The render is at " .. where)
                set_status(st.message .. " " .. notes .. " Drag it in from the path below.")
            else
                set_status(st.message .. " " .. notes ..
                           " The render is finished, so you can drag it in.")
            end
        end
        return true
    end
    return false
end

-- Check on the background run. The exit code is read before the status, so a
-- run that wrote its final status and then exited is never taken for a crash.
-- Python updates the status at least once a frame. This long without a word,
-- and no exit code, means the run was killed from outside, so nothing could
-- report it, as when its window is closed.
local STALLED_AFTER = 180

-- When the run the panel owns started: its tag begins with the time. nil for
-- a run from before 0.5.2, whose names do not say.
local function started_at()
    local t = RUN_TAG:match("^(%d+)%-")
    return t and tonumber(t)
end

-- The run the panel owns is followed again: it reports again after a stall
-- this panel saw, or it is one an earlier panel left going, found quiet as
-- the panel opened, or Reset gave up on. It is followed as a run going, with
-- Stop and its progress, and what was said while it was quiet goes. An
-- analysis this panel started marks its clip when it ends, as one followed
-- from the start does. Nothing says which clip a run from an earlier panel
-- was, so it marks none. The timer, started as the panel opened, reads it
-- from here on. `said` is what the status says, by default that it reports.
local function follow_again(st, said)
    view.recover = nil
    -- Its heartbeat counts until it writes again, or before its first, its start.
    LAUNCHED_AT = (st and st.updated) or LAUNCHED_AT or started_at()
    set_busy(true)
    if st then set_progress(st.fraction, st.stage) end
    set_status(said or "The run is reporting again. Its progress shows here.")
end

local function poll()
    stop_unasked()
    local exited = read_exit_code()
    local st = read_status()
    if apply_status(st) then return true end
    if not exited then
        -- With no status yet, the quiet counts from the launch: a run whose
        -- window closed before Python started leaves no file at all.
        local last = (st and st.updated and st.updated > 0) and st.updated or LAUNCHED_AT
        local quiet = last and (os.time() - last) or 0
        -- Quiet this long, the button clears the run, so it says Reset.
        if quiet > STALLED_AFTER then stop_unasked(true) end
        if quiet > STALLED_AFTER and not st then
            items.CancelBtn.Text = "Reset"
            set_status(string.format("Python has not started after %d minutes. Press Reset "
                .. "and run it again. Log: %s", math.floor(quiet / 60 + 0.5), LOG_PATH))
        elseif quiet > STALLED_AFTER then
            items.CancelBtn.Text = "Reset"
            set_status(string.format("No progress for %d minutes, so the run has stopped. "
                .. "Press Reset and run it again.", math.floor(quiet / 60 + 0.5)))
        elseif st and st.updated and math.abs(os.time() - st.updated) < STALLED_AFTER
                and (not RUNNING or items.CancelBtn.Text ~= "Stop") then
            -- Fresh progress from a run the panel had stopped following, or
            -- offered Reset for, so the warning no longer holds.
            follow_again(st)
        end
        return false
    end

    -- Python exited without saying it was done: it could not start, could not
    -- load a package, or crashed. Its last words are the end of the log.
    local why = last_log_line() or ("Python exited with code " .. exited .. ".")
    -- Shown now, with its log. Its exit code proves it ended, so its last
    -- progress, which would show the crash again, can go.
    mark_seen()
    view.recover = nil
    set_busy(false)
    set_progress(0, nil)
    -- The status box fits three lines, and the log's path took two of them.
    offer_log(true)
    print("[Upflow2x] Full log: " .. LOG_PATH)
    local msg = "Stopped without finishing: " .. why
        .. (SHOW_LOG and " Open Log shows the rest." or "")
    if RUN_KIND == "analysis" then items.AnalysisLabel.Text = msg end
    set_status(msg)
    return true
end

-- One look at the run the panel owns, even one started from an earlier
-- panel: the exit code, then the status, each read once. Python writes its
-- last status before it exits and the shell then writes the exit code, so a
-- status read after the exit code is final. "finished" is a run with its
-- result, "live" a run Stop can end, and "gone" one that died or went quiet,
-- or no run at all. The status and exit code read come back with it.
local function look()
    local exited = read_exit_code()
    local st = read_status()
    if st and st.done then return "finished", st, exited end
    if exited then return "gone", st, exited end
    -- Python writes its status at least once a frame, so a recent unfinished
    -- one is a run going. Its first comes only once it has loaded its
    -- packages, which can take a while, so until then a run counts from its
    -- start, and a panel opened meanwhile follows it rather than starting
    -- another over it. Only while it has written no status: an empty one is a
    -- run whose end a panel showed.
    local last = (st and st.updated) or (RUNNING and LAUNCHED_AT)
        or (not st and not exists(STATUS_PATH) and started_at())
    -- Either way of the clock: a heartbeat from before the clock was moved
    -- back is in the future, and is no more alive than an old one.
    if last and math.abs(os.time() - last) < STALLED_AFTER then return "live", st, exited end
    return "gone", st, exited
end

-- ---------------------------------------------------------------------------
-- polling
-- ---------------------------------------------------------------------------

-- ui:Timer is not present on every Resolve build, so it is created defensively
-- and the Refresh Progress button covers the case where it is missing. Its
-- Timeout reaches the dispatcher, as in Blackmagic's UIManager examples, and
-- the window handler is kept for builds that route it there instead.
local timer = nil
local have_timer = pcall(function()
    timer = ui:Timer({ ID = "PollTimer", Interval = 500 })
end)

-- Where the playhead is, or nil. Cheap enough to read every tick.
local function playhead_spot()
    local ok, spot = pcall(function()
        local timeline = get_resolve():GetProjectManager():GetCurrentProject():GetCurrentTimeline()
        return timeline:GetUniqueId() .. " " .. tostring(timeline:GetCurrentTimecode())
    end)
    return ok and spot or nil
end

-- Between runs the card follows the playhead, so there is nothing to press.
-- During playback the playhead moves every tick, and reading the clip under
-- it each time would cost Resolve, so the card catches up once it stops.
local pick_source
local last_spot = nil
local function on_timeout(ev)
    -- Ticks reach the panel, so it needs no Refresh Progress on the main view.
    view.ticked = true
    if not view.timed then
        view.timed = true
        show_what_matters()
    end
    -- Once, the tick after a change, by when Qt has laid the window out.
    if view.fit_again then fit_window() end
    view.fit_again = false
    if RUNNING then
        poll()
        return
    end
    local spot = playhead_spot()
    local still = spot == last_spot
    last_spot = spot
    if not still then return end
    local missing = not SOURCE_PATH and items.SourceLabel.Text
    if pick_source(true) and missing and items.Status.Text == missing then
        set_status("Ready.")
    end
end

if have_timer and timer then
    pcall(function() win:AddChild(timer) end)
    pcall(function() disp.On.Timeout = on_timeout end)
    pcall(function() win.On.PollTimer.Timeout = on_timeout end)
    view.timed = true
end

-- A timer that will not start is no timer, so Refresh Progress shows.
local function start_polling()
    if not (have_timer and timer) then return end
    local started = timer.Start ~= nil and pcall(function() timer:Start() end)
    if not started and view.timed then
        view.timed = false
        show_what_matters()
    end
end

local CANNOT_START = "Could not start the run. Check that Upflow2x's folder can be written to, "
    .. "then try again."

local WAITING = "A render finished while the panel was closed. "
    .. "Press Refresh Progress to add it to the timeline."
local PENDING = "A finished render has not been added yet. "
    .. "Press Refresh Progress to add it to the timeline."

-- A finished run met by Stop, Reset or a new run. This panel's run is
-- handled as a poll would, importing its render once. A render with no run
-- to follow, such as one that finished while the panel was closed or after
-- Reset gave up on it, waits for Refresh Progress. With quiet, a render that
-- lands makes no sound, as apply_status says.
-- False when there is nothing worth keeping, such as an old failure.
local function settle(st, quiet)
    if RUNNING or RUN_KIND == "analysis" then return apply_status(st, quiet) end
    if st.ok and st.import_path ~= "" then
        view.recover = "pending"
        set_status(PENDING)
        return true
    end
    return false
end

-- How Reset takes the status of a run the look found gone. It can still
-- finish just after the look, so the status is moved aside in one step and
-- read there, never deleted unread. A finished one goes back in place for the
-- caller to handle, and a status written after the move is a new file Reset
-- leaves. If moving it back fails, it stays aside, where read_status finds
-- it, so a finished one is still the run's result.
local function claim_status()
    local aside = STATUS_PATH .. ".reset"
    -- One kept aside from an earlier claim comes first, and is never deleted.
    local kept = parse_status(claimed() or "")
    if kept and kept.done then
        os.rename(aside, STATUS_PATH)
        return kept
    end
    os.remove(aside)
    if not os.rename(STATUS_PATH, aside) then return nil end
    local st = read_status_at(aside)
    -- An empty one says a panel showed how the run ended, so it goes back too.
    if (st and st.done) or read_file(aside) == "" then
        os.rename(aside, STATUS_PATH)
        return st
    end
    os.remove(aside)
    return st
end

-- The run the panel owns comes before a new one, which would take its place:
-- a result not yet imported, or a run Reset gave up on that turns out to be
-- going. Checked before the new run changes anything that belongs to it: the
-- clip, where its render goes, its markers. True when it comes first, and
-- the new run doesn't start. Also whether that look found its exit code: the
-- run has ended, and its status, read after the exit code, was final.
local function last_run_first()
    local state, st, exited = look()
    local ended = exited ~= nil
    if state == "finished" then
        if settle(st) then return true, ended end
        -- Nothing worth keeping, and a finished run writes nothing more.
        os.remove(STATUS_PATH)
        os.remove(STATUS_PATH .. ".reset")
        return false, ended
    end
    -- Render and Analyse can only be pressed while no run is being followed,
    -- so a live one here is one Reset gave up on, writing again. One still
    -- loading, before its first status, is followed from the moment the panel
    -- opens, which keeps them unpressable.
    if state ~= "live" or not st then return false, ended end
    -- Still going, so it is followed rather than replaced.
    follow_again(st, "A run started earlier is still going. Its progress shows here.")
    start_polling()
    return true, ended
end

local RUNS_STARTED = 0

-- Moves the pointer written to `new` into place. Windows moves a file only
-- onto a name not in use, so there the pointer steps aside as
-- POINTER_OLD first, which a panel reads while no pointer is in place. The
-- pointer in place is never deleted, so a move that fails partway leaves the
-- last run found. False when the new pointer is not in place.
local function commit_pointer(new)
    local moved = os.rename(new, RUN_POINTER)
    if not moved and exists(RUN_POINTER) then
        -- With the pointer in place, an old one is left from a move that
        -- finished.
        os.remove(POINTER_OLD)
        if os.rename(RUN_POINTER, POINTER_OLD) then
            moved = os.rename(new, RUN_POINTER)
            if not moved then os.rename(POINTER_OLD, RUN_POINTER) end
        end
    end
    if moved then os.remove(POINTER_OLD) end
    return moved == true
end

-- Starts a run with files of its own, `marks` among them, and makes it the
-- run the panel owns, the one the pointer names. The pointer goes in before
-- the run starts, so a run that starts is always one a panel opened later can
-- find: if it cannot go in, nothing starts. If the run then cannot start, the
-- last run's pointer goes back, so a run Reset gave up on that finishes after
-- all is still found. Nothing else of the last run changes until the new one
-- is on its way. Then the last run's files go when `ended`, the look before
-- it, showed that run had ended. One that may still be going keeps them, and
-- they are the only files it can write to. Returns whether the run started,
-- and if not, why.
local function launch(ended, extra, marks)
    local last = RUN_TAG
    local tag
    repeat
        RUNS_STARTED = RUNS_STARTED + 1
        tag = string.format("%d-%d-%s", os.time(), RUNS_STARTED,
                            ((tostring({}):gsub("%W", "")):sub(-6)))
    until not (exists(run_files(tag).status) or exists(run_files(tag).exit))
    local files = run_files(tag)
    -- `name` written whole with `body`, or nil. A write can look fine and
    -- still fail as the file closes.
    local function written(name, body)
        local f = io.open(name, "w")
        local wrote = f and f:write(body)
        local closed = f and f:close()
        if wrote and closed then return name end
        os.remove(name)
        return nil
    end
    -- Named for the run, so another panel starting one at the same time
    -- never puts its tag in this one's place.
    local new = written(RUN_POINTER .. ".new-" .. tag, tag)
    if not new then return false, CANNOT_START end
    if marks and #marks > 0 then
        if not written(files.marks, table.concat(marks, ",")) then
            os.remove(new)
            return false, "Could not write the marked frames to " .. files.marks .. "."
        end
        extra = extra .. " --marks-file " .. shell_quote(files.marks)
    end
    if not commit_pointer(new) then
        os.remove(new)
        os.remove(files.marks)
        return false, CANNOT_START
    end
    use_run(tag)
    if not run_background("Stages.resolve_smooth", current_args(extra)) then
        -- It never started, so the last run is the one the panel owns again,
        -- here and in the pointer. Before 0.5.2's first run there was none.
        use_run(last)
        if last == "" then
            os.remove(RUN_POINTER)
        else
            local back = written(RUN_POINTER .. ".back-" .. tag, last)
            if back and not commit_pointer(back) then os.remove(back) end
        end
        for _, path in pairs(files) do os.remove(path) end
        return false, CANNOT_START
    end
    if ended then
        for _, path in pairs(run_files(last)) do os.remove(path) end
    end
    -- The new run is the one the panel owns, so the last one's needs go,
    -- and a Stop sent to the last one says nothing of it.
    view.recover = nil
    view.stop_sent = nil
    LAUNCHED_AT = os.time()
    start_polling()
    return true
end

-- ---------------------------------------------------------------------------
-- handlers
-- ---------------------------------------------------------------------------

function win.On.Eternal2x.Close(ev)
    save_conf()
    disp:ExitLoop()
end

-- Settings and Back only change the page. Every setting is saved as it
-- changes, so leaving starts nothing. Focus goes to the button that leads
-- back, for whoever moves through the panel with the keyboard.
function win.On.SettingsBtn.Clicked(ev)
    view.page = "settings"
    show_what_matters()
    pcall(function() items.BackBtn:SetFocus("OtherFocusReason") end)
end

function win.On.BackBtn.Clicked(ev)
    view.page = "main"
    show_what_matters()
    pcall(function() items.SettingsBtn:SetFocus("OtherFocusReason") end)
end

-- Work on the clip under the playhead now, not the one there when the panel
-- opened. If the playhead has moved off every clip, keep the last one.
-- With quiet, as the timer calls it, a gap says nothing.
function pick_source(quiet)
    if refresh_source(true) or SOURCE_PATH then return true end
    -- An answer to a press shows, though the clip's line says it too.
    if not quiet then set_status(items.SourceLabel.Text, true) end
    return false
end

function win.On.AnalyseBtn.Clicked(ev)
    if REPO_ROOT == "" then
        set_status("Plugin folder not configured. Re-run the installer.")
        return
    end
    if launch_problem() then
        set_status(launch_problem())
        return
    end
    local first, ended = last_run_first()
    if first then return end
    if not pick_source() then return end
    -- Backgrounded like the render: analysis is quick on a short clip but can
    -- take tens of seconds on a long one, and Resolve must not freeze for it.
    local started, why = launch(ended, " --analyse")
    if not started then
        items.AnalysisLabel.Text = why
        set_status(why)
        return
    end
    offer_log(false)
    RUN_KIND = "analysis"
    ANALYSED_ITEM = SOURCE_ITEM
    IMPORTED = false
    STAGE.name = nil
    set_busy(true)
    set_progress(0.01, "Starting")
    -- The analysis line first, so the status knows it would only repeat it.
    items.AnalysisLabel.Text = "Analysing..."
    items.AnalysisLabel.ToolTip = ""
    set_status("Analysing...")
end

function win.On.SmoothBtn.Clicked(ev)
    if REPO_ROOT == "" then
        set_status("Plugin folder not configured. Re-run the installer.")
        return
    end
    if launch_problem() then
        set_status(launch_problem())
        return
    end
    local first, ended = last_run_first()
    if first then return end
    if not pick_source() then return end
    -- Read before anything starts. Rendering as if unmarked would throw away
    -- the frames the editor chose.
    local marks = clip_marks(SOURCE_ITEM)
    if not marks then
        set_status("Could not read the markers on the clip from Resolve, so nothing was rendered.")
        return
    end
    local started, why = launch(ended, "", marks)
    if not started then
        set_status(why)
        return
    end
    offer_log(false)
    IMPORTED = false
    STAGE.name = nil
    RUN_PLACE = SOURCE_PLACE
    RUN_KIND = "render"
    LAST_ITEM = nil
    items.CompareBtn.Enabled = false
    set_busy(true)
    set_progress(0.01, "Starting")
    if #marks > 0 then
        set_status("Rendering the " .. #marks .. " marked frames.")
    else
        set_status("Rendering. No markers, so Upflow2x finds the drawings.")
    end
end

-- Switch the smoothed clip off and on, so the original under it shows through.
function win.On.CompareBtn.Clicked(ev)
    local ok, showing = pcall(function()
        local now = not LAST_ITEM:GetClipEnabled()
        if not LAST_ITEM:SetClipEnabled(now) then error("refused") end
        return now
    end)
    if not ok then
        LAST_ITEM = nil
        items.CompareBtn.Enabled = false
        set_status("The smoothed clip is no longer on the timeline.")
        return
    end
    items.CompareBtn.Text = showing and "Show Original" or "Show Upflow2x"
    set_status(showing and "Showing the Upflow2x version." or "Showing the original.")
end

-- After a failed run it opens the log in a text editor instead. Finder hides
-- a file whose name starts with a dot, so showing it there would find nothing.
function win.On.FolderBtn.Clicked(ev)
    local target = SHOW_LOG and LOG_PATH or LAST_OUTPUT
    if not target then return end
    local cmd
    if is_windows() then
        if launch_problem() then
            set_status(launch_problem())
            return
        end
        -- /select opens the parent with the render picked out. Explorer
        -- reports 1 even when it worked, so its answer is not checked. cmd
        -- expands %NAME% in a command line, so the path goes in a script,
        -- where doubling % keeps it literal, started as the render is.
        -- Notepad goes through start, so the script ends without waiting on it.
        local f = io.open(REVEAL_SCRIPT, "wb")
        if not f then return end
        local quoted = shell_quote((target:gsub("/", "\\")))
        f:write("@echo off\r\nchcp 65001 >nul\r\n",
                SHOW_LOG and "start \"\" notepad " or "explorer /select,",
                (quoted:gsub("%%", "%%%%")), "\r\n")
        f:close()
        local dir = (REPO_ROOT ~= "" and REPO_ROOT or root):gsub("/", "\\")
        cmd = "start \"Upflow2x\" /D " .. shell_quote(dir) .. " /MIN cmd /c .\\"
            .. basename(REVEAL_SCRIPT)
    elseif SHOW_LOG then
        local q = shell_quote(target)
        cmd = "open -t " .. q .. " 2>/dev/null || xdg-open " .. q .. " >/dev/null 2>&1 &"
    else
        local q = shell_quote(target)
        cmd = "open -R " .. q .. " 2>/dev/null || xdg-open " ..
              shell_quote(target:match("^(.+)/") or ".") .. " >/dev/null 2>&1 &"
    end
    print("[Upflow2x] " .. cmd)
    if is_windows() then
        start_script(REVEAL_SCRIPT, cmd)
    else
        execute(cmd)
    end
end

function win.On.RefreshStatusBtn.Clicked(ev)
    -- A finished run's status is gone once handled, but the shell can still
    -- write the exit code after it, so that alone is not a run to report on:
    -- a successful run leaves 0 behind. A run this panel started is polled
    -- even before Python reports, so one that never started is caught, and
    -- so is a failure no panel has shown, or a run Reset gave up on that
    -- died after all, from only their exit code.
    local exited = read_exit_code()
    local unshown = (view.recover == "failed" or view.recover == "gave_up")
        and exited ~= nil and exited ~= "0"
    if not read_status() and not RUNNING and not unshown then
        -- Without a timer, this is also how the card catches up.
        pick_source(true)
        set_status(view.recover == "gave_up" and "The run you reset has not finished."
            or "No run in progress.")
        return
    end
    poll()
end

function win.On.CancelBtn.Clicked(ev)
    -- One look decides, and nothing reads the run again to change its mind.
    -- With two reads, a run that finished in between was taken for gone, and
    -- its result was cleared before the import.
    local offered = items.CancelBtn.Text
    local state, st, exited = look()
    if state == "live" and offered ~= "Stop" then
        -- Pressed as Reset, for a run that went quiet and has reported again
        -- since. That press only follows it again: only presses of a button
        -- that says Stop end a run.
        follow_again(st)
        return
    end
    if state == "live" then
        stop_unasked()
        if not STOP_ASKED then
            local prompt = "Press Stop again to end the run. "
                .. "Stopping a render deletes the frames it has drawn so far."
            if RUN_KIND == "analysis" then
                prompt = "Press Stop again to end the analysis."
            elseif RUN_KIND == "render" then
                prompt = "Press Stop again to end the render. "
                    .. "Stopping deletes the frames it has drawn so far."
            end
            STOP_ASKED = { at = os.time(), prompt = prompt, status = items.Status.Text }
            items.CancelBtn.StyleSheet = LINK_ASKING
            set_status(prompt)
            return
        end
        -- The run sees this at its next frame, then reports Stopped. One
        -- that finished since the look has nothing left to stop, and the
        -- next poll handles its result.
        local f = io.open(STOP_PATH, "w")
        if f then f:close() end
        -- Too late to stop, it may still land, and then without a sound.
        if f then view.stop_sent = true end
        set_busy(true)
        start_polling()
        set_status(f and "Stopping..." or "Could not stop the run.")
        return
    end
    -- Gone with no exit code, the run may only have gone quiet, and may
    -- still finish after a new run has taken its place.
    local giving_up = state == "gone" and not exited and (st ~= nil or RUNNING)
    if state == "gone" then st = claim_status() end
    -- Found finished by a press of Stop or Reset: it lands with no sound.
    if st and st.done and settle(st, true) then return end
    -- The exit code stays: it shows the run has ended, so the next run can
    -- clear its files. A run known to have ended, by its exit code or by a
    -- result with nothing to keep, is marked as one whose end a panel dealt
    -- with, so a panel opened later does not report it again. One that only
    -- went quiet is not, as it may still finish, or fail and say why.
    if exited or (st and st.done) then mark_seen() end
    if exited then os.remove(STOP_PATH) end
    -- A run given up on can still finish, so Refresh Progress stays to look.
    view.recover = giving_up and "gave_up" or nil
    IMPORTED = false
    set_busy(false)
    set_progress(0, nil)
    set_status(giving_up and RUN_KIND ~= "analysis" and "Ready. If the run you reset finishes after all, "
        .. "its render is in the Save to folder." or "Ready.")
end

function win.On.ThresholdSlider.ValueChanged(ev)
    THRESHOLD = math.max(1, math.min(9, math.floor((tonumber(items.ThresholdSlider.Value) or 5) + 0.5)))
    items.ThresholdValue.Text = tostring(THRESHOLD)
    save_conf()
end

function win.On.FormatCombo.CurrentIndexChanged(ev)
    FORMAT = FORMATS[(items.FormatCombo.CurrentIndex or 0) + 1] or "png"
    save_conf()
end

function win.On.FolderCombo.CurrentIndexChanged(ev)
    if FILLING_FOLDERS then return end
    local index = items.FolderCombo.CurrentIndex or 0
    -- Showing the folder already in use is no choice. Resolve may deliver
    -- the refill's own events after it, and refilling for them would loop.
    if index == (OUTPUT_DIR ~= "" and 1 or 0) then return end
    if index == 0 then
        OUTPUT_DIR = ""
    elseif index == (OUTPUT_DIR ~= "" and 2 or 1) then
        -- Fusion's folder dialog, which Resolve's scripts share. Cancelled,
        -- it hands back nothing and the folder stays as it was.
        local ok, chosen = pcall(function()
            return global("fu"):RequestDir(OUTPUT_DIR ~= "" and OUTPUT_DIR or nil)
        end)
        if ok and type(chosen) == "string" and chosen ~= "" then
            OUTPUT_DIR = trim_trailing_sep(chosen)
        elseif not ok then
            set_status("Resolve could not open a folder dialog here.")
        end
    end
    save_conf()
    fill_folders()
end

function win.On.UpscaleCB.Clicked(ev)
    DO_UPSCALE = items.UpscaleCB.Checked
    show_modes()
    save_conf()
end

function win.On.InterpCB.Clicked(ev)
    DO_INTERPOLATE = items.InterpCB.Checked
    show_modes()
    save_conf()
end

-- Render chime is saved the moment its tick changes. Toggled comes with every
-- change: a click, Space, and Windows' accessibility Toggle, which sends no
-- click. Clicked stays for a build that does not send Toggled. A click sends
-- both, so it is saved only when it differs from what is saved. The state is
-- the one the event gives, or without one, the box's own.
local function chime_changed(ev)
    local on = ev and ev.On
    if type(on) ~= "boolean" then on = items.ChimeCB.Checked end
    on = on == true or on == 1
    if on == CHIME then return end
    CHIME = on
    save_conf()
end
win.On.ChimeCB.Toggled = chime_changed
win.On.ChimeCB.Clicked = chime_changed

local function run_update(auto_mode)
    if REPO_ROOT == "" then
        set_status("Plugin folder not configured. Re-run the installer.")
        return
    end
    if UPDATE_URL == "" then
        set_status("No update URL configured.")
        return
    end
    if launch_problem() then
        if not auto_mode then set_status(launch_problem()) end
        return
    end
    -- An update replaces the files a running render is using, or one still
    -- loading them.
    if (look()) == "live" then
        if not auto_mode then
            set_status("A render is still running. Check for updates once it finishes.")
        end
        return
    end
    local args = " --meta-url " .. shell_quote(UPDATE_URL)
    if auto_mode then args = args .. " --auto" end
    if not auto_mode then set_status("Checking for updates...") end
    -- A check that never starts, say because cd failed, must not report the
    -- verdict of the one before.
    os.remove(UPDATE_LOG_PATH)
    local ok = run_blocking("Stages.resolve_update", args, UPDATE_LOG_PATH)
    if auto_mode then return end
    -- The updater's last line is its verdict. An earlier "Updating..." line is
    -- followed by whether it was applied, or why not.
    local line = last_log_line(UPDATE_LOG_PATH)
    if line and line ~= "" then
        set_status(line)
    elseif ok then
        set_status("Update check complete.")
    else
        set_status("Update check failed. See the console for details.")
    end
end

function win.On.UpdateBtn.Clicked(ev)
    run_update(false)
end

-- ---------------------------------------------------------------------------
-- start
-- ---------------------------------------------------------------------------

win:Show()

if REPO_ROOT == "" then
    set_meta("Plugin folder not configured.")
    set_status("Run Installer/install_upflow2x.py, then restart Resolve.")
else
    refresh_source()
    start_polling()
    set_status(SOURCE_PATH and "Ready." or items.SourceLabel.Text)
    -- One look at the run the panel owns, as an earlier panel left it.
    local state, waiting, exited = look()
    local failed = exited ~= nil and exited ~= "0"
    if state == "live" then
        -- Closed mid-run and reopened: follow that run, and import it when
        -- done, even one still loading before its first status.
        follow_again(waiting, "A run started earlier is still going. Its progress shows here.")
    elseif state == "finished" and waiting.ok and waiting.import_path ~= "" then
        -- It finished after its panel closed, so nothing has imported it, and
        -- the next run would delete its status unseen.
        LAST_OUTPUT = render_location(waiting)
        items.FolderBtn.Enabled = LAST_OUTPUT ~= nil
        view.recover = "pending"
        set_status(WAITING)
    else
        local seen = end_shown()
        if not seen and ((state == "finished" and not waiting.ok and waiting.message ~= "Stopped.")
                or (state == "gone" and failed)) then
            -- It failed after its panel closed. A successful run leaves exit
            -- code 0 behind, which is no failure, and a run whose end a panel
            -- showed, a Stop or a failure, leaves its own, seen.
            view.recover = "failed"
            set_status("The last run did not finish. Press Refresh Progress to see why.")
        elseif state == "gone" and waiting and not exited then
            -- Quiet too long, with no exit code to say it ended.
            view.recover = "stale"
            set_status("A run started earlier stopped reporting. Press Reset to clear it.")
        end
        if AUTO_UPDATE then run_update(true) end
    end
end

-- Shown, the window is given its size once, whatever it came up at, and
-- with a timer once more on the first tick, by when Qt has laid it out.
fit_window()

-- Ticks can only arrive from here on.
view.since = os.time()
disp:RunLoop()
