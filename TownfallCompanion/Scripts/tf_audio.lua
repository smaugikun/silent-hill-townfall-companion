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
local SORT_PER_UPDATE = 200 -- new FMOD components looked at per update: a level load makes thousands

-- A cutscene's dialogue plays through the Dialoc plugin, on speaker components: FMODAudioComponents that a
-- DialocAudioComponentActor makes while the game runs, or FMODDialogueComponents. (The object dump holds no FMOD
-- bus or VCA to turn down, only these.) Every new FMOD component is queued by NotifyOnNewObject and sorted a few
-- at a time by whose it is: the doors' and lights' are let go, the speakers kept.
local FMOD_COMPONENT = "/Script/FMODStudio.FMODAudioComponent"
local SPEAKER_ACTOR = "/Script/DialocPluginCore.DialocAudioComponentActor"
local DIALOGUE_COMPONENT = "/Script/DialocPlugin.FMODDialogueComponent"

local M = {}

local wanted, askedAt, off = false, -math.huge, false
local phoneDialogue, dialogueOff, linesOff = false, false, false
local phoneVideo, videoOff = false, false
local unsorted, nextUnsorted = {}, 1  -- new FMOD components, and the next one to look at
local speakers = {}                   -- Dialoc's speaker components
local speakerActor, dialogueComponent -- the classes to tell them by, looked up once they are needed
local classLookedUpAt = -math.huge
local dialogueVerified = false        -- a speaker was heard in a cutscene: the game can be silenced there

local componentClass = StaticFindObject(FMOD_COMPONENT)
if componentClass and componentClass:IsValid() then
    NotifyOnNewObject(FMOD_COMPONENT, function(c) unsorted[#unsorted + 1] = c end)
else
    common.log("TF-AUDIO", "%s not found, cutscene dialogue stays in the game", FMOD_COMPONENT)
end

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

local function lookUp(path)
    local class = StaticFindObject(path) -- by path: slow in this UE4SS build, so rarely
    return class and class:IsValid() and class or nil
end

-- Whether an FMOD component is one of Dialoc's speakers: a dialogue component, or one made on its actor.
local function isSpeaker(c)
    if not c:IsValid() then return false end
    if dialogueComponent and c:IsA(dialogueComponent) then return true end
    local outer = c:GetOuter()
    return speakerActor ~= nil and outer ~= nil and outer:IsValid() and outer:IsA(speakerActor)
end

-- Sorts the next few new FMOD components; the speakers are kept.
local function sortNewComponents()
    if nextUnsorted > #unsorted then
        if nextUnsorted > 1 then unsorted, nextUnsorted = {}, 1 end
        return
    end
    if not (speakerActor and speakerActor:IsValid()) and os.clock() - classLookedUpAt > 30 then
        classLookedUpAt = os.clock()
        speakerActor = lookUp(SPEAKER_ACTOR)
        dialogueComponent = lookUp(DIALOGUE_COMPONENT)
    end
    if not speakerActor and not dialogueComponent then -- no Dialoc here: nothing to find
        if #unsorted > 20000 then unsorted, nextUnsorted = {}, 1 end
        return
    end
    local last = math.min(#unsorted, nextUnsorted + SORT_PER_UPDATE - 1)
    for i = nextUnsorted, last do
        local c = unsorted[i]
        unsorted[i] = false -- let go of it
        local ok, mine = pcall(isSpeaker, c)
        if ok and mine then speakers[#speakers + 1] = c end
    end
    nextUnsorted = last + 1
end

-- The speakers there are now: each is checked with IsValid() before use, the game frees them with a level.
local function liveSpeakers()
    local live = {}
    for _, c in ipairs(speakers) do
        if c:IsValid() then live[#live + 1] = c end
    end
    speakers = live
    return live
end

local function speakerPlaying()
    for _, c in ipairs(liveSpeakers()) do
        local ok, playing = pcall(function() return c:IsPlaying() end)
        if ok and playing then return true end
    end
    return false
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
    return dialogueVerified
end

-- A few times a second: silences again what started since (a new monster, a restarted loop), or
-- turns the sound back on once the phone stops asking.
function M.update()
    sortNewComponents() -- also in menus and while loading: that is when most of them are made
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

    -- A cutscene's dialogue plays on Dialoc's speakers. The game's goes quiet only while a cutscene plays and the
    -- phone plays its dialogue; once it has, it stays quiet until the cutscene ends: the phone's request dropping
    -- out between two lines, or when the cutscene runs again, let the game's words through. Not before a speaker
    -- has been heard in a cutscene: only then is it known that this is where the dialogue plays, and the phone
    -- (telemetry audio.cutsceneDialogue) may take it over.
    local ok, sequence = pcall(cutscene.sequence)
    local inCutscene = ok and sequence ~= nil
    if inCutscene and not dialogueVerified and speakerPlaying() then
        dialogueVerified = true
        common.log("TF-AUDIO", "a Dialoc speaker talks in the cutscene: the phone may play its dialogue")
    end
    if keep and inCutscene and dialogueVerified and (talking or dialogueOff) then
        setVolume(liveSpeakers(), 0) -- every time: a speaker made since is silenced too
        if not dialogueOff then
            dialogueOff = true
            common.log("TF-AUDIO", "cutscene dialogue off in the game, the phone plays it")
        end
    elseif dialogueOff then
        setVolume(liveSpeakers(), 1)
        dialogueOff = false
        common.log("TF-AUDIO", "cutscene dialogue back on in the game")
    end
end

return M
