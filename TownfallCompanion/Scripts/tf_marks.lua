-- Marks another mod puts on the CRTV's dial, for the phone's dial too. The mod writes them to
-- %TEMP%\townfall-companion-dial-marks.txt: a first line "TFMARKS1 <count>", then one line a mark,
-- "<frequency 0..1> <red> <green> <blue>" (linear RGB 0..1, as Unreal's FLinearColor). The file stays open and is
-- read again once a second; a half-written one leaves the marks as they were.

local M = {}

local READ_EVERY_S = 1 -- marks change only when the other mod moves its stations
local RETRY_S = 5      -- no file yet: no mod writes one, or not yet
local MAX_MARKS = 64
local path, file, json, readAt = nil, nil, "[]", -math.huge

function M.init(tempDir)
    path = tempDir and (tempDir .. "\\townfall-companion-dial-marks.txt")
end

local function parse(text)
    local count = tonumber(text:match("^TFMARKS1 (%d+)"))
    if not count or count > MAX_MARKS then return nil end
    local marks = {}
    for line in text:gmatch("[^\r\n]+") do
        local f, r, g, b = line:match("^(%S+) (%S+) (%S+) (%S+)$")
        f, r, g, b = tonumber(f), tonumber(r), tonumber(g), tonumber(b)
        if f and r and g and b then
            if f < 0 or f > 1 or math.min(r, g, b) < 0 then return nil end
            marks[#marks + 1] = string.format('{"frequency":%.4f,"colour":[%.3f,%.3f,%.3f]}',
                f, math.min(r, 1), math.min(g, 1), math.min(b, 1))
        end
    end
    return #marks == count and "[" .. table.concat(marks, ",") .. "]" or nil
end

-- The marks as a JSON array for the telemetry; [] while there are none.
function M.json()
    local now = os.clock()
    if not path or now - readAt < (file and READ_EVERY_S or RETRY_S) then return json end
    readAt = now
    file = file or io.open(path, "r")
    if not file then return json end
    file:seek("set", 0)
    json = parse(file:read("a") or "") or json
    return json
end

return M
