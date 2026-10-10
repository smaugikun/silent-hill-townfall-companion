-- The cutscene playing (its level sequence): the phone doesn't raise the CRTV over one.
--
-- Cutscenes are level sequence players, caught as they are constructed (NotifyOnNewObject) plus one
-- FindAllOf per level. A level change frees them: the list starts over at every level start and every
-- object is checked with IsValid() before use (a call on a freed one crashes the game).

local common = require("tf_common")

local SEQUENCE_PLAYER_CLASS = "/Script/LevelSequence.LevelSequencePlayer"

local M = {}

local sequences = {} -- every level sequence player constructed since the level loaded

local sequenceClass = StaticFindObject(SEQUENCE_PLAYER_CLASS)
if sequenceClass and sequenceClass:IsValid() then
    NotifyOnNewObject(SEQUENCE_PLAYER_CLASS, function(s) sequences[#sequences + 1] = s end)
end

-- Once per level: the players may have been constructed before the mod was watching.
function M.onLevelStart()
    sequences = FindAllOf("LevelSequencePlayer") or {}
end

-- The cutscene playing: its level sequence's name, or nil.
local function sequence()
    for _, s in ipairs(sequences) do
        if s:IsValid() and s:IsPlaying() then
            local asset = s:GetSequence()
            if not (asset and asset:IsValid()) then return nil end
            return asset:GetFName():ToString()
        end
    end
end

function M.update()
    local alive = {}
    for _, s in ipairs(sequences) do
        if s:IsValid() then alive[#alive + 1] = s end
    end
    sequences = alive
    local ok, name = pcall(sequence)
    common.logChange("sequence", "TF-CUTSCENE", ok and ("sequence: " .. (name or "none"))
        or ("sequence unreadable: " .. tostring(name)))
end

-- The telemetry "cutscene" object, or null when none plays.
function M.json()
    local ok, name = pcall(sequence)
    if not ok or not name then return "null" end
    return string.format('{"sequence":%s}', common.jsonString(name))
end

return M
