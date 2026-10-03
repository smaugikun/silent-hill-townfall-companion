-- Logging, unit conversion and JSON helpers shared by the TownfallCompanion modules.

local M = {}

function M.log(tag, fmt, ...)
    print(string.format("[" .. tag .. "] " .. fmt .. "\n", ...))
end

-- Logs msg only when it differs from the last one on the same channel, so a
-- persistent state or failure shows up once instead of on every tick.
local lastMsg = {}
function M.logChange(channel, tag, msg)
    if lastMsg[channel] ~= msg then
        lastMsg[channel] = msg
        M.log(tag, "%s", msg)
    end
end

-- Forgets the channel's last message, so it is logged again if it comes back.
function M.clearChannel(channel)
    lastMsg[channel] = nil
end

-- fn's result; if it fails, `fallback`, with the error logged once on `channel` (until fn works
-- again). One failing part, e.g. after a game update renamed a property, mustn't take the rest down.
function M.optional(channel, tag, fn, fallback)
    local ok, result = pcall(fn)
    if ok then
        M.clearChannel(channel)
        return result
    end
    M.logChange(channel, tag, "read error: " .. tostring(result))
    return fallback
end

-- A number the game may not give: fn's result, or nil if it fails or isn't a number.
function M.tryNumber(fn)
    local ok, n = pcall(fn)
    return ok and type(n) == "number" and n or nil
end

-- Unreal: cm, yaw 0 = +X, clockwise from above. Phone: metres, +Y = north.
function M.toPhone(loc, yaw)
    return loc.Y / 100, loc.X / 100, yaw
end

-- Signal strength 0..1 at `distance`: full up to `begin`, linear to 0 at `cutoff` (the game's
-- distanceSignalFalloffBegin / distanceSignalCutoff pairs, in cm). A negative cutoff never cuts off:
-- Return_to_Clinic's is -1, and the game gives its signal full strength.
function M.falloff(distance, begin, cutoff)
    if cutoff < 0 or distance <= begin then return 1 end
    if distance >= cutoff then return 0 end
    return (cutoff - distance) / (cutoff - begin)
end

function M.distance(a, b)
    local dx, dy, dz = a.X - b.X, a.Y - b.Y, a.Z - b.Z
    return math.sqrt(dx * dx + dy * dy + dz * dz)
end

-- FString properties come back as UE4SS FString objects; UFunction results may be Lua strings.
function M.str(value)
    if type(value) == "string" then return value end
    return value and value:ToString() or nil
end

-- The CRTV's videos (enemies', waypoints', the screen's) as the path under Content/Movies/CRTV_Movies
-- without extension, e.g. "Bink/Shipping/Mov_CRTV_Clinic", from the game's URL for it.
M.CRTV_VIDEO = "CRTV_Movies/(.+)%.bk2$"

-- A video URL from the game, with forward slashes, matched against `pattern`; nil if it doesn't match.
function M.videoPath(url, pattern)
    return ((M.str(url) or ""):gsub("\\", "/")):match(pattern)
end

-- What a media player (Bink or Unreal's) plays, as videoPath(its URL, pattern), and how far in
-- (seconds) if it says; nil if it plays nothing that matches.
function M.playing(mediaPlayer, pattern)
    if not (mediaPlayer and mediaPlayer:IsValid() and mediaPlayer:IsPlaying()) then return nil end
    local path = M.videoPath(mediaPlayer:GetUrl(), pattern)
    if path then return path, M.tryNumber(function() return mediaPlayer:GetTime().Ticks / 1e7 end) end
end

-- JSON for a string or a number that may be missing (null).
function M.jsonString(s)
    if s == nil then return "null" end
    return '"' .. (s:gsub('[%c"\\]', function(c) return string.format("\\u%04x", c:byte()) end)) .. '"'
end

function M.jsonNumber(n, fmt)
    return n and string.format(fmt, n) or "null"
end

return M
