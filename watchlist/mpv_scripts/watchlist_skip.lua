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
