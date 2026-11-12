-- Watchlist: skips openings and endings that the video file marks as chapters
-- ("Opening", "OP", "Ending", "ED", "Credits"...). Shift+S turns it on and off.
-- An ending that is the last chapter moves on to the next episode, if there is one.

local enabled = mp.get_opt("watchlist-skip") ~= "no"
local skipped = {}

local function kind_of(title)
    local t = (title or ""):lower():gsub("^%s+", ""):gsub("%s+$", "")
    if t == "" then return nil end
    for _, p in ipairs({"^op$", "^op[%s%d%-_:]", "^nc ?op", "opening", "^intro$", "^intro[%s%-_:]"}) do
        if t:find(p) then return "opening" end
    end
    for _, p in ipairs({"^ed$", "^ed[%s%d%-_:]", "^nc ?ed", "ending", "^outro", "credits"}) do
        if t:find(p) then return "ending" end
    end
    return nil
end

mp.register_event("file-loaded", function() skipped = {} end)

mp.observe_property("chapter", "number", function(_, chapter)
    if not enabled or chapter == nil or chapter < 0 or skipped[chapter] then return end
    local chapters = mp.get_property_native("chapter-list") or {}
    local current = chapters[chapter + 1]
    local kind = current and kind_of(current.title)
    if not kind then return end
    skipped[chapter] = true
    -- Several chapters in a row can make up one opening or ending.
    local last = chapter + 1
    while chapters[last + 1] and kind_of(chapters[last + 1].title) == kind do
        skipped[last] = true
        last = last + 1
    end
    local after = chapters[last + 1]
    if after then
        mp.set_property_number("time-pos", after.time)
    else
        local pos = mp.get_property_number("playlist-pos", 0)
        local count = mp.get_property_number("playlist-count", 1)
        if pos + 1 >= count then return end   -- the last thing playing: let it run
        mp.command("playlist-next")
    end
    mp.osd_message("Skipped the " .. kind .. "   (Shift+S: stop skipping)", 3)
    -- The app's build check asks for a note of what happened.
    local log = mp.get_opt("watchlist-skip-log")
    if log and log ~= "" then
        local from = current.time
        mp.add_timeout(0.5, function()
            local f = io.open(log, "a")
            if f then
                f:write(string.format("%s %.1f %.1f\n", kind, from, mp.get_property_number("time-pos", -1)))
                f:close()
            end
        end)
    end
end)

mp.add_key_binding("S", "watchlist-toggle-skip", function()
    enabled = not enabled
    mp.osd_message(enabled and "Skipping openings and endings" or "Not skipping openings and endings", 2)
end)

-- Where the episode playing is from (your own site and its page), so a wrong one is easy
-- to tell. The app sets it as each episode opens.
mp.register_script_message("show-source", function()
    local src = mp.get_property("user-data/watchlist/source", "")
    if src == "" then src = mp.get_property("path", "") end
    mp.osd_message("Playing from " .. src .. "\nThe wrong episode? Right-click it in Watch Anything → Relink", 8)
end)

-- The app's subtitle finder asks whether the subtitles are in sync: Y / N, or Esc to stop.
-- Only those three keys are taken while it asks - seeking with the keys or the mouse still works.
local asking = nil
local function stop_asking()
    if not asking then return end
    asking:remove()
    asking = nil
    for _, k in ipairs({"y", "Y", "n", "N", "ESC"}) do mp.remove_key_binding("wa-sync-" .. k) end
end
mp.register_script_message("ask-sync", function(text)
    stop_asking()
    asking = mp.create_osd_overlay("ass-events")
    -- (long strings: the ASS tags' backslashes stay as they are)
    asking.data = [[{\an8\fs30\bord2\1c&HFFFFFF&}Are the subtitles in sync?\N{\fs22}]] .. (text or "")
        .. [[\N{\fs24\1c&HFFE07C&}Y{\1c&HFFFFFF&} = yes, use them for every episode     ]]
        .. [[{\1c&HFFE07C&}N{\1c&HFFFFFF&} = no, try others     {\1c&HFFE07C&}Esc{\1c&HFFFFFF&} = stop]]
    asking:update()
    local function answer(a)
        return function()
            stop_asking()
            mp.commandv("script-message", "watchlist-sync", a)
        end
    end
    for _, k in ipairs({"y", "Y"}) do mp.add_forced_key_binding(k, "wa-sync-" .. k, answer("yes")) end
    for _, k in ipairs({"n", "N"}) do mp.add_forced_key_binding(k, "wa-sync-" .. k, answer("no")) end
    mp.add_forced_key_binding("ESC", "wa-sync-ESC", answer("stop"))
end)
mp.register_script_message("stop-asking", stop_asking)

function from_site_now()
    local path = mp.get_property("path") or ""
    return path:find("^https?://") ~= nil and not path:find("^https?://127%.0%.0%.1:%d+/stream/")
end

-- Your own subtitle file (the app opens a window to choose it) and switching sub / dub:
-- the app does them, so the player only says so.
mp.register_script_message("load-subtitle", function()
    mp.osd_message("Choose the subtitle file in the window that opens…", 3)
    mp.commandv("script-message", "watchlist-load-subtitle")
end)
mp.register_script_message("switch-version", function()
    mp.commandv("script-message", "watchlist-switch-version")
end)
-- Every server of the episode (the app lists them, checking each) and finding it on another
-- of your sites: the app does them too.
mp.register_script_message("servers", function()
    if not from_site_now() then
        mp.osd_message("Servers: only for videos from your own sites", 2)
        return
    end
    mp.commandv("script-message", "watchlist-servers")
end)
mp.register_script_message("other-site", function()
    if not from_site_now() then
        mp.osd_message("Another site: only for videos from your own sites", 2)
        return
    end
    mp.osd_message("Looking for it on your other sites…", 3)
    mp.commandv("script-message", "watchlist-other-site")
end)

-- Anything loading (an episode opening - found on its site, a torrent's first part, a file -
-- or a video stopping to load more): a box saying so, with a bar moving along, instead of a
-- player that looks stuck. While something loads, Next and Previous wait (pressed again and
-- again they'd only pile more on): they say so instead.
local loading = { overlay = mp.create_osd_overlay("ass-events"), timer = nil, step = 0, busy = false, text = "" }
local function loading_stop()
    if loading.timer then loading.timer:kill() end
    loading.timer = nil
    loading.busy = false
    loading.overlay:remove()
end
local function loading_draw()
    local w, h = mp.get_osd_size()
    if not w or w == 0 then w, h = 1280, 720 end
    loading.overlay.res_x, loading.overlay.res_y = w, h
    local bw, bh = 360, 6
    local x0, y0 = (w - bw) / 2, h / 2 + 22
    local seg = bw / 4
    local pos = (loading.step * 9) % (bw + seg) - seg
    local a, b = math.max(0, pos), math.min(bw, pos + seg)
    local box = string.format([[{\an7\pos(%d,%d)\bord0\shad0\1c&H1A1414&\1a&H30&\p1}m 0 0 l %d 0 l %d %d l 0 %d{\p0}]],
        x0 - 24, y0 - 62, bw + 48, bw + 48, 100, 100)
    local text = string.format([[{\an8\pos(%d,%d)\fs22\bord0\shad0\1c&HF2E9E6&}%s]], w / 2, y0 - 48, loading.text)
    local track = string.format([[{\an7\pos(%d,%d)\bord0\shad0\1c&H554A4A&\p1}m 0 0 l %d 0 l %d %d l 0 %d{\p0}]],
        x0, y0, bw, bw, bh, bh)
    local bar = b > a and string.format([[{\an7\pos(%d,%d)\bord0\shad0\1c&HFF7A8B&\p1}m 0 0 l %d 0 l %d %d l 0 %d{\p0}]],
        x0 + a, y0, b - a, b - a, bh, bh) or ""
    loading.overlay.data = box .. "\n" .. text .. "\n" .. track .. "\n" .. bar
    loading.overlay:update()
    loading.step = loading.step + 1
end
local function loading_start(text)
    if loading.timer then loading.timer:kill() end
    loading.busy, loading.text, loading.step = true, text, 0
    -- (shown only when it takes a moment: what opens at once shows nothing)
    loading.timer = mp.add_timeout(0.8, function()
        loading_draw()
        loading.timer = mp.add_periodic_timer(0.05, loading_draw)
    end)
end
mp.register_event("start-file", function()
    local title = mp.get_property("playlist/" .. (mp.get_property_number("playlist-pos", 0)) .. "/title") or "the episode"
    loading_start("Getting " .. title .. " ready…")
end)
mp.register_event("file-loaded", function()
    -- (opened paused - a watch party waiting for everyone: nothing is loading any more)
    if mp.get_property_bool("pause") and not mp.get_property_bool("paused-for-cache") then loading_stop() end
end)
mp.register_event("end-file", loading_stop)
mp.observe_property("playback-time", "number", function(_, t)
    if t and loading.busy and not mp.get_property_bool("paused-for-cache") and not mp.get_property_bool("seeking") then
        loading_stop()
    end
end)
mp.observe_property("paused-for-cache", "bool", function(_, waiting)
    if waiting then
        if not loading.busy then loading_start("Loading…") end
    elseif loading.busy and mp.get_property_number("playback-time") then
        loading_stop()
    end
end)
local function step(command, what)
    if loading.busy then
        mp.osd_message("Still loading - " .. what .. " in a moment", 1.5)
        return
    end
    mp.command(command)
end
-- A watch party's host moved to another episode: it's on its way (the app says when it's here).
mp.register_script_message("show-loading", function(text) loading_start(text or "Loading…") end)
mp.register_script_message("next", function() step("playlist-next", "next episode") end)
mp.register_script_message("prev", function() step("playlist-prev", "previous episode") end)

-- A video from a site that's stuck loading for 5 seconds (it never starts, or stops to
-- load and doesn't come back): a small box at the side offering another source. T (or a
-- click on the box) asks the app, which plays the episode from the site's next server.
local stuck = { overlay = mp.create_osd_overlay("ass-events"), since = nil, shown = false, quiet_until = 0, last = nil }
local function from_site()
    -- (a video from one of your sites - not a torrent's stream or a file: those have no other source)
    local path = mp.get_property("path") or ""
    return path:find("^https?://") ~= nil and not path:find("^https?://127%.0%.0%.1:%d+/stream/")
end
local function stuck_hide()
    if stuck.shown then
        stuck.overlay:remove()
        mp.remove_key_binding("watchlist-other-source-key")
        mp.remove_key_binding("watchlist-other-source-click")
    end
    stuck.shown = false
end
local function stuck_ask()
    stuck_hide()
    stuck.since, stuck.quiet_until = nil, mp.get_time() + 20
    mp.commandv("script-message", "watchlist-other-source")
end
local function stuck_box()
    local w, h = mp.get_osd_size()
    if not w or w == 0 then w, h = 1280, 720 end
    stuck.overlay.res_x, stuck.overlay.res_y = w, h
    local bw, bh = 300, 74
    local x0, y0 = w - bw - 28, h / 2 - bh / 2
    stuck.box = { x0, y0, x0 + bw, y0 + bh }
    stuck.overlay.data = string.format([[{\an7\pos(%d,%d)\bord0\shad0\1c&H3A1B22&\1a&H20&\p1}m 0 0 l %d 0 l %d %d l 0 %d{\p0}]],
            x0, y0, bw, bw, bh, bh) .. "\n" ..
        string.format([[{\an7\pos(%d,%d)\fs20\bord0\shad0\1c&HF2E9E6&\b1}Not working?{\b0}\N{\fs17\1c&HCFC2BE&}Press T to try another source]],
            x0 + 16, y0 + 12)
    stuck.overlay:update()
    if not stuck.shown then
        mp.msg.info("offered another source")
        mp.add_forced_key_binding("t", "watchlist-other-source-key", stuck_ask)
        mp.add_forced_key_binding("MBTN_LEFT", "watchlist-other-source-click", function()
            local mx, my = mp.get_mouse_pos()
            local b = stuck.box
            if b and mx >= b[1] and mx <= b[3] and my >= b[2] and my <= b[4] then
                stuck_ask()
            else
                mp.command("cycle pause")            -- (a click anywhere else does what it always does)
            end
        end)
    end
    stuck.shown = true
end
-- When to offer it: only when the video is really stuck, not just loading. What counts is
-- whether data is still arriving: an episode opening gets 30 seconds (and only then if nothing
-- has come for 10), one that stops mid-way gets 10 seconds of nothing coming; one that's slow
-- but moving says "Loading slowly…" and is offered another source only after a minute.
local flow = { last = mp.get_time(), opening = true, since = nil, slow = false }
mp.observe_property("cache-speed", "number", function(_, v)
    if v and v > 0 then flow.last = mp.get_time() end
end)
mp.add_periodic_timer(1, function()
    local now = mp.get_time()
    local t = mp.get_property_number("playback-time")
    local cache_wait = mp.get_property_bool("paused-for-cache")
    local moving = t ~= nil and stuck.last ~= nil and math.abs(t - stuck.last) > 0.2
    stuck.last = t
    if moving then flow.opening = false end
    local waiting = cache_wait or (flow.opening and mp.get_property_bool("idle-active") == false)
    if not from_site() or (mp.get_property_bool("pause") and not cache_wait)
            or moving or not waiting or now < stuck.quiet_until then
        flow.since, flow.slow = nil, false
        stuck_hide()
        return
    end
    flow.since = flow.since or now
    local waited, nothing_for = now - flow.since, now - flow.last
    local offer
    if flow.opening then
        offer = (waited >= 30 and nothing_for >= 10) or waited >= 60
    else
        offer = (waited >= 10 and nothing_for >= 10) or waited >= 60
    end
    if waited >= 10 and nothing_for < 10 and loading.busy and not flow.slow then
        flow.slow = true
        loading.text = "Loading slowly…"        -- (still coming, just slowly: not stuck)
    end
    if offer then stuck_box() end
end)
mp.register_event("start-file", function()
    stuck.last = nil
    flow.opening, flow.since, flow.slow, flow.last = true, nil, false, mp.get_time()
    stuck_hide()
end)
mp.register_event("end-file", stuck_hide)

-- Every key the player answers to and what it does (the right-click menu's "Keyboard shortcuts",
-- or ? / F1): read from the player's own key list, so whatever is bound is there. Choosing one
-- does it.
local KEY_NAMES = {
    SPACE = "Space", ENTER = "Enter", ESC = "Esc", BS = "Backspace", DEL = "Delete", TAB = "Tab",
    LEFT = "←", RIGHT = "→", UP = "↑", DOWN = "↓", PGUP = "Page Up", PGDWN = "Page Down",
    HOME = "Home", END = "End", INS = "Insert", SHARP = "#", MENU = "Menu key",
    MBTN_LEFT = "Click", MBTN_LEFT_DBL = "Double-click", MBTN_RIGHT = "Right-click", MBTN_MID = "Middle-click",
    MBTN_BACK = "Mouse back button", MBTN_FORWARD = "Mouse forward button",
    WHEEL_UP = "Wheel up", WHEEL_DOWN = "Wheel down", WHEEL_LEFT = "Wheel left", WHEEL_RIGHT = "Wheel right",
    KP_ADD = "Num +", KP_SUBTRACT = "Num −", KP_MULTIPLY = "Num *", KP_DIVIDE = "Num /", KP_ENTER = "Num Enter",
    KP_BEGIN = "Num 5", KP_UP = "Num ↑", KP_DOWN = "Num ↓", KP_LEFT = "Num ←", KP_RIGHT = "Num →",
    KP_HOME = "Num Home", KP_END = "Num End", KP_PGUP = "Num Page Up", KP_PGDWN = "Num Page Down",
    PLAY = "Media Play", PAUSE = "Media Pause", PLAYPAUSE = "Media Play/Pause", PLAYONLY = "Media Play",
    PAUSEONLY = "Media Pause", STOP = "Media Stop", NEXT = "Media Next", PREV = "Media Previous",
    FORWARD = "Media Forward", REWIND = "Media Rewind", MUTE = "Media Mute", VOLUME_UP = "Volume up key",
    VOLUME_DOWN = "Volume down key", POWER = "Power key", ZOOMIN = "Zoom in key", ZOOMOUT = "Zoom out key",
    CLOSE_WIN = "Closing the window",
}
local function one_key(k)
    local mods, last = k:match("^(.*%+)(.+)$")                       -- ("Ctrl+Shift+a", "Alt++")
    if not mods then mods, last = "", k end
    last = KEY_NAMES[last] or (last:gsub("^KP(%d)$", "Num %1"))
    if #last == 1 and last:match("%u") and not mods:find("Shift") then mods = mods .. "Shift+" end
    return mods .. last
end
local function key_name(key)
    local a, b = key:match("^(%w)%-(%w+)$")                           -- ("g-a": g, then a)
    if a then return one_key(a) .. " then " .. one_key(b) end
    return one_key(key)
end

-- What a command does, in words (the first pattern that fits).
local function amount(n) return tostring(math.abs(tonumber(n))) end
local SAYS = {
    {"^cycle pause$", "Play / pause"}, {"^set pause yes$", "Pause"}, {"^set pause no$", "Play"},
    {"^quit%-watch%-later", "Close the player (the player remembers where you were)"}, {"^quit", "Close the player"},
    {"^seek%s+0%s+absolute", "Back to the start"}, {"^revert%-seek mark", "Mark this spot to go back to"},
    {"^revert%-seek", "Undo the last jump"},
    {"sub%-seek %-1", "Back to the previous subtitle line"}, {"sub%-seek", "On to the next subtitle line"},
    {"seek%s+(%-?[%d.]+)%s+exact", function(n) return (tonumber(n) < 0 and "Back " or "Forward ") .. amount(n) .. " s (exactly)" end},
    {"seek%s+(%-?[%d.]+)", function(n) return (tonumber(n) < 0 and "Back " or "Forward ") .. amount(n) .. " s" end},
    {"^frame%-back%-step", "One frame back (paused)"}, {"^frame%-step", "One frame on (paused)"},
    {"^add chapter %-1", "Previous chapter"}, {"^add chapter 1", "Next chapter"},
    {"^ab%-loop", "Repeat a part (press at its start, then at its end; again to stop)"},
    {"loop%-file", "Repeat this video on / off"},
    {"add volume%s+(%-?[%d.]+)", function(n) return (tonumber(n) < 0 and "Volume down " or "Volume up ") .. amount(n) .. "%" end},
    {"^cycle mute", "Mute on / off"}, {"^cycle audio", "Next audio track (language)"},
    {"add audio%-delay%s+(%-?[%d.]+)", function(n) return "Sound " .. (tonumber(n) < 0 and "earlier" or "later") .. " by " .. amount(n) .. " s" end},
    {"^set speed 1$", "Normal speed"}, {"cycle%-values speed", "Speed 1.25× / 1.5× / 2×"},
    {"add speed%s+(%-?[%d.]+)", function(n) return (tonumber(n) < 0 and "Slower" or "Faster") .. " (" .. amount(n) .. "× a press)" end},
    {"multiply speed 0%.5", "Half the speed"}, {"multiply speed 2", "Double the speed"},
    {"multiply speed 1/", "A little slower"}, {"multiply speed", "A little faster"},
    {"^cycle sub%-visibility", "Subtitles on / off"}, {"^cycle secondary%-sub%-visibility", "Second subtitles on / off"},
    {"^cycle sub down", "Previous subtitle track"}, {"^cycle sub$", "Next subtitle track"},
    {"add sub%-delay%s+(%-?[%d.]+)", function(n) return "Subtitles " .. (tonumber(n) < 0 and "earlier" or "later") .. " by " .. amount(n) .. " s" end},
    {"sub%-step %-1", "Subtitles one line earlier"}, {"sub%-step", "Subtitles one line later"},
    {"add sub%-scale%s+%-", "Smaller subtitles"}, {"add sub%-scale", "Bigger subtitles"},
    {"add sub%-pos%s+%-", "Subtitles higher"}, {"add sub%-pos", "Subtitles lower"},
    {"sub%-ass%-override", "Subtitles in the player's style / their own"},
    {"sub%-ass%-use%-video%-data", "Subtitles sized to the video / the window"},
    {"watchlist_skip next", "Next episode"}, {"watchlist_skip prev", "Previous episode"},
    {"watchlist_skip show%-source", "Where this is playing from"}, {"watchlist_skip servers", "Servers (other copies of this episode)"},
    {"watchlist_skip other%-site", "Find it on another of your sites"}, {"watchlist_skip switch%-version", "Switch sub / dub"},
    {"watchlist_skip load%-subtitle", "Add a subtitle file"}, {"watchlist_skip shortcuts", "These keyboard shortcuts"},
    {"watchlist%-toggle%-skip", "Skip openings and endings on / off"},
    {"playlist%-next", "Next episode"}, {"playlist%-prev", "Previous episode"},
    {"playlist%-pos 0", "First episode in the list"}, {"playlist%-pos%-1", "Last episode in the list"},
    {"uosc/menu", "The player's menu"}, {"uosc/items", "Episodes"}, {"uosc/subtitles", "Subtitles"}, {"uosc/audio", "Audio tracks"},
    {"select/context%-menu", "The player's menu"}, {"select/menu", "Search every command"},
    {"select/select%-sid", "Choose subtitles"}, {"select/select%-aid", "Choose the audio track"},
    {"select/select%-chapter", "Choose a chapter"}, {"select/select%-playlist", "Choose an episode"},
    {"select/select%-binding", "Search every key"}, {"select/select%-audio%-device", "Choose where the sound goes"},
    {"select/select%-subtitle%-line", "Jump to a subtitle line"}, {"select/select%-track", "Choose a track"},
    {"select/select%-vid", "Choose the video track"}, {"select/show%-properties", "Show the player's settings"},
    {"select/select%-secondary", "Choose second subtitles"}, {"select/select%-edition", "Choose an edition"},
    {"select/select%-watch", "Recently played (the player's own list)"},
    {"^cycle fullscreen", "Full screen on / off"}, {"set fullscreen no", "Leave full screen"},
    {"^cycle ontop", "Keep the window on top on / off"},
    {"set window%-scale 0%.5", "Window at half the video's size"}, {"set window%-scale 1", "Window at the video's size"},
    {"set window%-scale 2", "Window at twice the video's size"},
    {"show%-progress", "Show the time"}, {"osd%-level", "Always show the time on / off"},
    {"stats/display%-stats%-toggle", "Video details (stay on)"}, {"stats/display%-stats", "Video details"},
    {"stats/display%-page", "Video details: a page"}, {"show%-text %${playlist}", "Show the episode list"},
    {"show%-text %${track%-list}", "Show the tracks"},
    {"^screenshot window", "Screenshot (as shown)"}, {"^screenshot video", "Screenshot (without subtitles)"},
    {"^screenshot each%-frame", "Screenshot every frame on / off"}, {"^screenshot", "Screenshot"},
    {"video%-aspect%-override", "Change the picture's shape (16:9, 4:3…)"},
    {"add panscan %-", "Zoom out (fill less)"}, {"add panscan", "Zoom in to fill the screen"},
    {"add video%-zoom%s+%-", "Zoom out"}, {"add video%-zoom", "Zoom in"},
    {"set video%-zoom 0", "Reset zoom and position"}, {"cursor%-centric%-zoom%s+%-", "Zoom out where the mouse is"},
    {"cursor%-centric%-zoom", "Zoom in where the mouse is"},
    {"drag%-to%-pan", "Drag the picture around"}, {"video%-pan", "Move the picture"}, {"video%-align", "Move the picture"},
    {"video%-scale", "Stretch the picture"}, {"video%-rotate", "Turn the picture"},
    {"add contrast %-", "Less contrast"}, {"add contrast", "More contrast"},
    {"add brightness %-", "Darker"}, {"add brightness", "Brighter"}, {"add gamma %-", "Less gamma"}, {"add gamma", "More gamma"},
    {"add saturation %-", "Less colour"}, {"add saturation", "More colour"},
    {"cycle deband", "Smooth banding on / off"}, {"cycle deinterlace", "Deinterlace on / off"},
    {"cycle%-values hwdec", "Hardware decoding on / off"}, {"cycle edition", "Next edition"}, {"cycle video", "Video on / off"},
    {"discnav", "Disc menu"}, {"playlist%-play%-index current", "Reload the video"},
    {"update%-clipboard", "Play the address you copied"}, {"commands/open", "The player's command line"},
    {"osc/visibility", "Controls: always / auto / never"},
}
local function says(cmd, comment)
    if cmd == "ignore" or cmd == "" then return nil end
    if cmd:find("osc/visibility") and mp.get_property("osc") == "no" then return nil end    -- (its controls aren't on)
    for _, p in ipairs(SAYS) do
        local a, b = cmd:match(p[1])
        if a ~= nil then
            if type(p[2]) == "function" then return p[2](a, b) end
            return p[2]
        end
    end
    if comment and comment ~= "" then return comment end
    return cmd
end

local GROUPS = {
    {"Playing", {"pause", "Pause", "Play", "Close", "speed", "Repeat", "frame", "Undo", "Mark", "start", "Reload", "Faster", "Slower"}},
    {"Going back and on", {"Back ", "Forward ", "chapter", "subtitle line", "time"}},
    {"Episodes and where they're from", {"episode", "Servers", "sites", "playing from", "sub / dub", "Episodes", "Recently", "Skip"}},
    {"Sound", {"Volume", "Mute", "Sound", "audio", "sound"}},
    {"Subtitles", {"ubtitle"}},
    {"Picture and window", {"Full screen", "full screen", "Zoom", "picture", "Window", "window", "Screenshot", "Brighter", "Darker",
                            "contrast", "gamma", "colour", "banding", "Deinterlace", "Hardware", "Video", "edition"}},
}
local function group_of(text)
    for i, g in ipairs(GROUPS) do
        for _, w in ipairs(g[2]) do
            if text:find(w, 1, true) then return i end
        end
    end
    return #GROUPS + 1
end

local function odd_key(k)
    -- (mouse buttons, media keys and the number pad after the keys everyone has)
    return k:find("MBTN") or k:find("WHEEL") or k:find("KP") or (KEY_NAMES[k] and KEY_NAMES[k]:find("key")) or KEY_NAMES[k] and KEY_NAMES[k]:find("^Media")
end

function shortcut_list()
    local active = {}
    for _, b in ipairs(mp.get_property_native("input-bindings") or {}) do
        local section = b.section or ""
        if (section == "default" or section:find("^input_")) and b.key ~= "MOUSE_MOVE"
                and not (b.cmd or ""):find("uosc/__keybinding") then
            local have = active[b.key]
            if not have or (b.priority or 0) >= (have.priority or 0) then active[b.key] = b end
        end
    end
    local by_text = {}
    for key, b in pairs(active) do
        local text = says(b.cmd or "", b.comment)
        if text then
            by_text[text] = by_text[text] or {text = text, cmd = b.cmd, keys = {}}
            table.insert(by_text[text].keys, key)
        end
    end
    local groups = {}
    for _, row in pairs(by_text) do
        table.sort(row.keys, function(a, b)
            local oa, ob = odd_key(a) and true or false, odd_key(b) and true or false
            if oa ~= ob then return not oa end
            return #a < #b or (#a == #b and a < b)
        end)
        local g = group_of(row.text)
        groups[g] = groups[g] or {}
        table.insert(groups[g], row)
    end
    -- One list, a heading for each kind; a thing done by many keys takes a line for every three
    -- (so no key is cut off at the menu's edge).
    local items = {}
    for i = 1, #GROUPS + 1 do
        local rows = groups[i]
        if rows then
            table.sort(rows, function(a, b) return a.text < b.text end)
            items[#items + 1] = {title = GROUPS[i] and GROUPS[i][1] or "Everything else", bold = true,
                                 selectable = false, separator = false, value = "ignore"}
            for _, row in ipairs(rows) do
                for at = 1, #row.keys, 3 do
                    local names = {}
                    for j = at, math.min(at + 2, #row.keys) do names[#names + 1] = key_name(row.keys[j]) end
                    items[#items + 1] = {title = row.text, hint = table.concat(names, "  ·  "), value = row.cmd,
                                         muted = at > 1}
                end
            end
            items[#items].separator = true
        end
    end
    return items
end

mp.register_script_message("shortcuts", function()
    local items = shortcut_list()
    local utils = require "mp.utils"
    local opts = mp.get_property_native("script-opts") or {}
    if opts["uosc-color"] then
        mp.commandv("script-message-to", "uosc", "open-menu", utils.format_json({
            type = "watchlist-shortcuts", title = "Keyboard shortcuts", search_style = "on_demand",
            footnote = "Choose one to do it now · type to search", items = items}))
        return
    end
    -- (mpv's own controls: the list on the screen)
    local lines = {}
    for _, r in ipairs(items) do
        lines[#lines + 1] = r.hint and (r.hint .. "   " .. r.title) or ("— " .. r.title .. " —")
    end
    mp.osd_message(table.concat(lines, "\n"), 20)
end)
