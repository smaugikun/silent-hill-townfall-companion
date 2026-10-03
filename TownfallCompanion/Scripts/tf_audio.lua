-- The CRTV's sound in the game, silenced while the phone plays it (the phone's "Silence it in the
-- game"). The phone asks again every couple of seconds; once it stops asking (closed, out of Wi-Fi)
-- the game's sound comes back.
--
-- Only what the phone plays instead: the CRTV's static and signal loops and the radio signals monsters
-- give off. The CRTV screen's video soundtrack goes quiet only while the phone plays that video (without
-- the converted videos it can't, and the game keeps it). Talking goes quiet only while the phone says it
-- plays it too: the waypoints' dialogue (on their ClearSignal and DistortedSignal; muted with the rest,
-- the talking would be heard nowhere) and a cutscene's. The monster voice through the CRTV and the
-- cutscenes' other sound stay in the game, and one-shot sounds (clicks, beeps) have no component to
-- reach. Not the cutscenes' video player either: a SetVolume on it after a level change, once the game
-- has freed it, crashes the game; every object here is checked with IsValid() before anything is read
-- from it. Volume only: a silenced
-- sound still plays to its end, in case the story waits for it.

local common = require("tf_common")
local player = require("tf_player")
local enemies = require("tf_enemies")
local signals = require("tf_signals")
local cutscene = require("tf_cutscene")

local HOLD_S = 5 -- the phone's request lasts this long
-- FMOD VCA or bus names that carry dialogue, in order of preference (the pak lists assets named
-- Dialogue, DX and VO; which one mixes the cutscenes' dialogue is a guess until seen in-game).
local DIALOGUE_MIX = { "^Dialogue$", "^DX$", "^VO$", "Dialog", "DX" }

local M = {}

local wanted, askedAt, off = false, -math.huge, false
local phoneDialogue, dialogueOff, linesOff = false, false, false
local phoneVideo, videoOff = false, false
local statics, dialogueMix -- FMODBlueprintStatics' default object; {class, object, name} of the dialogue mix

-- A list of UFMODAudioComponents: add(get) takes the one get() returns if it is there, once.
local function componentList()
    local list, seen = {}, {}
    function list.add(get)
        local ok, c = pcall(get)
        if ok and c and c.IsValid and c:IsValid() and not seen[c:GetAddress()] then
            seen[c:GetAddress()] = true
            list[#list + 1] = c
        end
    end
    return list
end

-- The CRTV's sound sources to silence, those there right now (its screen's video apart).
local function crtvSources()
    local list = componentList()
    local pawn = player.pawn()
    local radio = pawn:GetRadio()
    list.add(function() return pawn.SFX_CRTV end)
    list.add(function() return radio.staticAudioComponent end)
    for _, e in ipairs(enemies.nearby()) do
        if e:IsValid() then
            list.add(function() return e.RadioSignalClear end)
            list.add(function() return e.RadioSignalDist end)
        end
    end
    return list
end

-- The CRTV screen's video soundtrack.
local function videoSources()
    local list = componentList()
    local radio = player.pawn():GetRadio()
    list.add(function() return radio:GetCRTVWidget().WaypointVideoAudioComponent end)
    return list
end

-- What the waypoints talk on: the components their dialogue is set to play on, and their actors'
-- ClearSignal and DistortedSignal (the same two on BP_RadioWaypoint, the only FMOD components it has).
local function talkingSources()
    local list = componentList()
    for _, c in ipairs(signals.dialogueComponents()) do list.add(function() return c end) end
    for _, w in ipairs(signals.activeComponents()) do
        local owner = w:IsValid() and w:GetOwner()
        if owner and owner:IsValid() then
            list.add(function() return owner.ClearSignal end)
            list.add(function() return owner.DistortedSignal end)
        end
    end
    return list
end

-- Whether any active waypoint is saying a line now, silenced or not (a silenced sound plays on to its end).
local function waypointSpeaking()
    for _, sound in ipairs(signals.dialogueComponents()) do
        local ok, playing = pcall(function() return sound:IsPlaying() end)
        if ok and playing then return true end
    end
    return false
end

local function setVolume(list, volume)
    local count = 0
    for _, c in ipairs(list) do
        if pcall(function() c:SetVolume(volume) end) then count = count + 1 end
    end
    return count
end

-- Once per level: FMOD's VCAs and buses loaded now, logged, and the one mixing dialogue picked.
function M.onLevelStart()
    statics = statics or StaticFindObject("/Script/FMODStudio.Default__FMODBlueprintStatics")
    local found, names = {}, {}
    for _, class in ipairs({ "FMODVCA", "FMODBus" }) do
        for _, o in ipairs(FindAllOf(class) or {}) do
            local name = o:GetFName():ToString()
            found[#found + 1] = { class = class, object = o, name = name }
            names[#names + 1] = class:sub(5) .. " " .. name
        end
    end
    dialogueMix = nil
    for _, pattern in ipairs(DIALOGUE_MIX) do
        for _, f in ipairs(found) do
            if not dialogueMix and f.name:find(pattern) then dialogueMix = f end
        end
    end
    common.logChange("fmod mixes", "TF-AUDIO", string.format("FMOD mixes: %s; dialogue: %s",
        #names > 0 and table.concat(names, ", ") or "none loaded",
        dialogueMix and (dialogueMix.class:sub(5) .. " " .. dialogueMix.name) or "not found, cutscene dialogue stays in the game"))
end

local function setDialogueVolume(volume)
    if not (dialogueMix and dialogueMix.object:IsValid() and statics) then return false end
    if dialogueMix.class == "FMODVCA" then statics:VCASetVolume(dialogueMix.object, volume)
    else statics:BusSetVolume(dialogueMix.object, volume) end
    return true
end

-- A request from the phone: true silences the game's CRTV sound for HOLD_S, false brings it back.
-- `dialogue`: the phone plays the talking going on (a waypoint's line, a cutscene's dialogue), so the
-- game's goes quiet too. `video`: the phone plays the CRTV screen's video with its sound, ditto.
function M.request(muteGame, dialogue, video)
    wanted, askedAt = muteGame, os.clock()
    phoneDialogue, phoneVideo = muteGame and dialogue == true, muteGame and video == true
end

function M.isOff()
    return off
end

-- Whether a cutscene's dialogue can go quiet in the game here (its FMOD mix is loaded). The phone plays
-- it only then: otherwise it would be heard twice.
function M.canSilenceDialogue()
    return dialogueMix ~= nil and dialogueMix.object:IsValid()
end

-- A few times a second: silences again what started since (a new monster, a restarted loop), or
-- turns the sound back on once the phone stops asking.
function M.update()
    if not player.isTracking() then return end
    local keep = wanted and os.clock() - askedAt < HOLD_S
    if keep then
        local count = setVolume(crtvSources(), 0)
        if not off then common.log("TF-AUDIO", "game CRTV sound off, the phone plays it (%d sources)", count) end
        off = true
    elseif off then
        setVolume(crtvSources(), 1)
        off = false
        common.log("TF-AUDIO", "game CRTV sound back on%s", wanted and " (the phone stopped asking)" or "")
    end

    if keep and phoneVideo then
        setVolume(videoSources(), 0)
        if not videoOff then common.log("TF-AUDIO", "CRTV video sound off in the game, the phone plays the video") end
        videoOff = true
    elseif videoOff then
        setVolume(videoSources(), 1)
        videoOff = false
        common.log("TF-AUDIO", "CRTV video sound back on in the game")
    end

    local talking = keep and phoneDialogue
    -- A line the phone took over stays quiet in the game until it ends: the phone's request dropping out for a
    -- moment (its copy catching up, a hiccup) let the game's rest of the line in, and both talked.
    if talking or (linesOff and keep and waypointSpeaking()) then
        setVolume(talkingSources(), 0)
        if not linesOff then common.log("TF-AUDIO", "waypoint dialogue off in the game, the phone plays it") end
        linesOff = true
    elseif linesOff then
        setVolume(talkingSources(), 1)
        linesOff = false
        common.log("TF-AUDIO", "waypoint dialogue back on in the game")
    end

    -- Only during a cutscene whose dialogue the phone plays: the mix carries all dialogue.
    local ok, sequence = pcall(cutscene.sequence)
    local quiet = talking and ok and sequence ~= nil
    if quiet and not dialogueOff then
        if setDialogueVolume(0) then
            dialogueOff = true
            common.log("TF-AUDIO", "cutscene dialogue off in the game, the phone plays it")
        end
    elseif not quiet and dialogueOff then
        setDialogueVolume(1)
        dialogueOff = false
        common.log("TF-AUDIO", "cutscene dialogue back on in the game")
    end
end

return M
