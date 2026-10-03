-- The videos Townfall's cutscenes show on screens (Content/Movies/Cutscene_Diegetic_Movies, e.g. watching
-- Zoe's signal on the CRTV), and the cutscene playing (its level sequence and how far in), for the
-- phone to show the video and play the dialogue.
--
-- The videos play on one shared Bink player, or an Unreal media player for the game's MP4s: assets,
-- caught as they load (NotifyOnNewObject) plus one lookup per level. A lookup by path walks every
-- object in this UE4SS build (it has no hash tables for Townfall): 14 ms each, 21 at worst, so never
-- once a second ([TF-PERF]). Cutscenes are level sequence players, caught the same way.
-- A level change frees them: the lists start over at every level start and every object is checked
-- with IsValid() before use (a call on a freed one crashes the game, see tf_audio.lua).

local common = require("tf_common")

local PLAYERS = {
    ["/Game/Movies/Cutscene_Diegetic_Movies/Bink/Generic/CinematicMoviePlayer_Bink.CinematicMoviePlayer_Bink"] = true,
    ["/Game/Movies/Cutscene_Diegetic_Movies/Cutscene_Diegetic_MediaPlayer.Cutscene_Diegetic_MediaPlayer"] = true,
}
local PLAYER_CLASSES = { "/Script/BinkMediaPlayer.BinkMediaPlayer", "/Script/MediaAssets.MediaPlayer" }
local SEQUENCE_PLAYER_CLASS = "/Script/LevelSequence.LevelSequencePlayer"

local M = {}

local players = {}   -- the video players, once loaded
local sequences = {} -- every level sequence player constructed since the level loaded

local function addPlayer(p)
    for _, known in ipairs(players) do
        if known:GetAddress() == p:GetAddress() then return end
    end
    players[#players + 1] = p
end

for _, class in ipairs(PLAYER_CLASSES) do
    local classObject = StaticFindObject(class)
    if classObject and classObject:IsValid() then
        NotifyOnNewObject(class, function(p)
            -- The callback comes a tick later in this UE4SS: the object may be gone by then.
            if not p:IsValid() then return end
            local ok, name = pcall(function() return p:GetFullName() end)
            if ok and name then
                for path in pairs(PLAYERS) do
                    if name:find(path, 1, true) then addPlayer(p) end
                end
            end
        end)
    end
end
local sequenceClass = StaticFindObject(SEQUENCE_PLAYER_CLASS)
if sequenceClass and sequenceClass:IsValid() then
    NotifyOnNewObject(SEQUENCE_PLAYER_CLASS, function(s) sequences[#sequences + 1] = s end)
end

-- Once per level: the players may have loaded before the mod was watching.
function M.onLevelStart()
    players = {}
    for path in pairs(PLAYERS) do
        local p = StaticFindObject(path)
        if p and p:IsValid() then addPlayer(p) end
    end
    sequences = FindAllOf("LevelSequencePlayer") or {}
end

-- What a cutscene shows on a screen, as the path under Content/Movies without extension
-- ("Cutscene_Diegetic_Movies/Bink/Cutscene_WatchingZoesSignal_1"), and how far in (seconds), or nil.
local function playing()
    for _, p in ipairs(players) do
        local path, seconds = common.playing(p, "(Cutscene_Diegetic_Movies/.+)%.%w+$")
        if path then return path, seconds end
    end
end

-- The cutscene playing: its level sequence's name and how far in (seconds), or nil.
function M.sequence()
    for _, s in ipairs(sequences) do
        if s:IsValid() and s:IsPlaying() then
            local asset = s:GetSequence()
            if not (asset and asset:IsValid()) then return nil end
            return asset:GetFName():ToString(), common.tryNumber(function()
                local t = s:GetCurrentTime()
                return (t.Time.FrameNumber.Value + t.Time.SubFrame) * t.Rate.Denominator / t.Rate.Numerator
            end)
        end
    end
end

function M.update()
    local alive = {}
    for _, s in ipairs(sequences) do
        if s:IsValid() then alive[#alive + 1] = s end
    end
    sequences = alive
    common.logChange("cutscene", "TF-CUTSCENE", "screen video: " .. (playing() or "none"))
    local ok, name, seconds = pcall(M.sequence)
    common.logChange("sequence", "TF-CUTSCENE", ok and ("sequence: " .. (name or "none")
        .. (name and seconds == nil and " (time unreadable)" or "")) or ("sequence unreadable: " .. tostring(name)))
end

-- The telemetry "cutscene" object: the screen video and the sequence, or null when neither plays.
function M.json()
    local video, videoTime = playing()
    local ok, sequence, sequenceTime = pcall(M.sequence)
    if not ok then sequence, sequenceTime = nil, nil end
    if not video and not sequence then return "null" end
    return string.format('{"video":%s,"videoTime":%s,"sequence":%s,"sequenceTime":%s}', common.jsonString(video),
        common.jsonNumber(videoTime, "%.2f"), common.jsonString(sequence), common.jsonNumber(sequenceTime, "%.2f"))
end

return M
