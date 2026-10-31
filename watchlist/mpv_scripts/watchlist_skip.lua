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

-- Your own subtitle file (the app opens a window to choose it) and switching sub / dub:
-- the app does them, so the player only says so.
mp.register_script_message("load-subtitle", function()
    mp.osd_message("Choose the subtitle file in the window that opens…", 3)
    mp.commandv("script-message", "watchlist-load-subtitle")
end)
mp.register_script_message("switch-version", function()
    mp.commandv("script-message", "watchlist-switch-version")
end)

-- An episode that takes a moment to open (found on its site just now): a box saying so,
-- with a bar moving along, instead of a player that looks stuck.
local loading = { overlay = mp.create_osd_overlay("ass-events"), timer = nil, step = 0 }
local function loading_stop()
    if loading.timer then loading.timer:kill() end
    loading.timer = nil
    loading.overlay:remove()
end
local function loading_draw()
    local w, h = mp.get_osd_size()
    if not w or w == 0 then w, h = 1280, 720 end
    loading.overlay.res_x, loading.overlay.res_y = w, h
    local title = mp.get_property("playlist/" .. (mp.get_property_number("playlist-pos", 0)) .. "/title") or "the next episode"
    local bw, bh = 360, 6
    local x0, y0 = (w - bw) / 2, h / 2 + 22
    local seg = bw / 4
    local pos = (loading.step * 9) % (bw + seg) - seg
    local a, b = math.max(0, pos), math.min(bw, pos + seg)
    local box = string.format("{\\an7\\pos(%d,%d)\\bord0\\shad0\\1c&H1A1414&\\1a&H30&\\p1}m 0 0 l %d 0 l %d %d l 0 %d{\\p0}",
        x0 - 24, y0 - 62, bw + 48, bw + 48, 100, 100)
    local text = string.format("{\\an8\\pos(%d,%d)\\fs22\\bord0\\shad0\\1c&HF2E9E6&}Getting %s ready…", w / 2, y0 - 48, title)
    local track = string.format("{\\an7\\pos(%d,%d)\\bord0\\shad0\\1c&H554A4A&\\p1}m 0 0 l %d 0 l %d %d l 0 %d{\\p0}",
        x0, y0, bw, bw, bh, bh)
    local bar = b > a and string.format("{\\an7\\pos(%d,%d)\\bord0\\shad0\\1c&HFF7A8B&\\p1}m 0 0 l %d 0 l %d %d l 0 %d{\\p0}",
        x0 + a, y0, b - a, b - a, bh, bh) or ""
    loading.overlay.data = box .. "\n" .. text .. "\n" .. track .. "\n" .. bar
    loading.overlay:update()
    loading.step = loading.step + 1
end
mp.register_event("start-file", function()
    loading_stop()
    local path = mp.get_property("path") or ""
    if not path:find("^https?://") then return end
    loading.step = 0
    -- (only when it takes a moment: one that opens at once shows nothing)
    loading.timer = mp.add_timeout(0.8, function()
        loading_draw()
        loading.timer = mp.add_periodic_timer(0.05, loading_draw)
    end)
end)
mp.register_event("file-loaded", loading_stop)
mp.register_event("end-file", loading_stop)
mp.observe_property("playback-time", "number", function(_, t) if t and loading.timer then loading_stop() end end)

-- A video from a site that's stuck loading for 5 seconds (it never starts, or stops to
-- load and doesn't come back): a small box at the side offering another source. T (or a
-- click on the box) asks the app, which plays the episode from the site's next server.
local stuck = { overlay = mp.create_osd_overlay("ass-events"), since = nil, shown = false, quiet_until = 0, last = nil }
local function from_site()
    local path = mp.get_property("path") or ""
    return path:find("^https?://") ~= nil
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
mp.add_periodic_timer(1, function()
    local t = mp.get_property_number("playback-time")
    local waiting = mp.get_property_bool("paused-for-cache") or (mp.get_property_bool("idle-active") == false and t == nil)
    local moving = t ~= nil and stuck.last ~= nil and math.abs(t - stuck.last) > 0.2
    stuck.last = t
    if not from_site() or mp.get_property_bool("pause") and not mp.get_property_bool("paused-for-cache")
            or moving or not waiting or mp.get_time() < stuck.quiet_until then
        stuck.since = nil
        stuck_hide()
        return
    end
    stuck.since = stuck.since or mp.get_time()
    if mp.get_time() - stuck.since >= 5 then stuck_box() end
end)
mp.register_event("start-file", function() stuck.since, stuck.last = nil, nil; stuck_hide() end)
mp.register_event("end-file", stuck_hide)
