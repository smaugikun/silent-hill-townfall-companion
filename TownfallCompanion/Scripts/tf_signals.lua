-- CRTV waypoints and signal objects (URadioWaypointSourceComponent): the frequencies the game
-- treats as important. Found like enemies (one full walk per level load, then NotifyOnNewObject);
-- only the ones the URadioWaypointManager singleton reports active are sent to the phone, with the
-- line each one is saying, for the phone to play.

local common = require("tf_common")
local player = require("tf_player")
local crtv = require("tf_crtv")
local log = common.log

local COMPONENT_CLASS = "/Script/Townfall.RadioWaypointSourceComponent"
local MANAGER_DEFAULT = "/Script/Townfall.Default__RadioWaypointManager"

local M = {}

local tracked = {} -- every waypoint component constructed since the level loaded
local active = {}  -- {component, info} for the active ones, refreshed every update

local componentClass = StaticFindObject(COMPONENT_CLASS)
if componentClass and componentClass:IsValid() then
    NotifyOnNewObject(COMPONENT_CLASS, function(c) tracked[#tracked + 1] = c end)
else
    log("TF-SIGNAL", "%s not found, CRTV waypoints won't be shown", COMPONENT_CLASS)
end

-- URadioWaypointManager::Get() is static; UE4SS calls it on the class default object. That one is
-- looked up once per level: lookups by path are slow in this UE4SS build (tf_cutscene.lua).
local managerDefault
local function waypointManager()
    if managerDefault == nil then
        managerDefault = StaticFindObject(MANAGER_DEFAULT) or false
    end
    local ok, manager = pcall(function() return managerDefault:Get() end)
    return ok and manager and manager:IsValid() and manager or nil
end

-- Read again on every update: the story changes a waypoint's video (after a reload Return_to_Clinic
-- had none at first, then Mov_CRTV_Clinic). Logged when something changed.
local function readInfo(c, name)
    local ok, info = pcall(function()
        return {
            name = name,
            channel = c.TuningFrequency,
            tolerance = c.TuningTolerance_Inner,
            toleranceOuter = c.TuningTolerance_Outer,
            reach = { c.distanceSignalFalloffBegin, c.distanceSignalCutoff },
            video = common.videoPath(c.CrtvVideoSignal_Url, common.CRTV_VIDEO),
            kind = c.bIsSignalObject and "signal_object" or "waypoint",
        }
    end)
    local channel = "waypoint " .. c:GetAddress()
    if not ok then
        common.logChange(channel, "TF-SIGNAL", string.format("waypoint %s unreadable: %s", name, tostring(info)))
        return nil
    end
    common.logChange(channel, "TF-SIGNAL", string.format("%s %s: channel %.3f tolerance %.3f/%s reach %.0f-%.0f cm video %s",
        info.kind, info.name, info.channel, info.tolerance, common.jsonNumber(info.toleranceOuter, "%.3f"),
        info.reach[1], info.reach[2], info.video or "none"))
    return info
end

-- A waypoint placed in the level: named, and on an actor. Blueprint templates and class defaults
-- have no owner (counted, they would add a nameless waypoint on channel -1 and a second Return_to_Clinic).
local function levelWaypointName(c)
    local owner = c:GetOwner()
    if not owner or not owner:IsValid() then return nil end
    local name = common.str(c.WaypointName)
    return name ~= "" and name or nil
end

-- A waypoint's talking (Dialoc dialogue, SDK dump) plays on two FMOD components of its actor, which
-- can only be BP_RadioWaypoint's two: ClearSignal and DistortedSignal.
local DIALOGUE = { { component = "WaypointDialogueClearComponent", tag = "WaypointDialogueClear", clear = true },
                   { component = "WaypointDialogueDistComponent", tag = "WaypointDialogueDist", clear = false } }

-- What the waypoint says while the game plays it: the line, as the FMOD programmer sound Dialoc gave
-- the component (the name of a stream of Dialogue_EN.bank, like "10c5", seen in-game), the
-- dialogue's own ID, how far in (ms), and whether clear; the clear one first. nil while it is quiet.
local function speaking(c)
    for _, d in ipairs(DIALOGUE) do
        local sound = c[d.component]
        if sound and sound:IsValid() and sound:IsPlaying() then
            return {
                line = common.str(sound.ProgrammerSoundName) or "",
                id = common.str(c[d.tag].DialogueID) or "",
                ms = math.floor(tonumber(sound:GetTimelinePosition()) or 0),
                clear = d.clear,
            }
        end
    end
end

-- The active waypoints' components, as of the last update.
function M.activeComponents()
    local list = {}
    for _, s in ipairs(active) do list[#list + 1] = s.component end
    return list
end

-- The FMOD components the active waypoints talk on, those there right now.
function M.dialogueComponents()
    local list = {}
    for _, s in ipairs(active) do
        local c = s.component
        if c:IsValid() then
            for _, d in ipairs(DIALOGUE) do
                local ok, sound = pcall(function() return c[d.component] end)
                if ok and sound and sound:IsValid() then list[#list + 1] = sound end
            end
        end
    end
    return list
end

-- One full walk right after a level load, like tf_enemies.onLevelStart. Addresses get reused
-- once objects are freed, so what was learned per address starts over too.
function M.onLevelStart()
    managerDefault = nil
    tracked, active = FindAllOf("RadioWaypointSourceComponent") or {}, {}
    common.clearChannel("signal summary")
end

-- Prunes destroyed components and re-checks which waypoints the game has active.
function M.update()
    if not player.isTracking() then
        active = {}
        return
    end
    local manager = waypointManager()
    local alive, nowActive, seen = {}, {}, {}
    for _, c in ipairs(tracked) do
        if c:IsValid() then
            alive[#alive + 1] = c
            local name = levelWaypointName(c)
            if name and not seen[name] and manager and manager:IsWaypointActive(name) then
                seen[name] = true
                local info = readInfo(c, name)
                if info then nowActive[#nowActive + 1] = { component = c, info = info } end
            end
        end
    end
    tracked, active = alive, nowActive

    local names = {}
    for _, s in ipairs(nowActive) do names[#names + 1] = s.info.name end
    common.logChange("signal summary", "TF-SIGNAL", string.format("%d waypoints, %d active: %s%s", #alive, #nowActive,
        table.concat(names, ", "), manager and "" or " (no URadioWaypointManager)"))
end

local function dialogueJson(name, c)
    local ok, said = pcall(speaking, c)
    if not ok then
        common.logChange("dialogue " .. name, "TF-SIGNAL", string.format("waypoint %s dialogue unreadable: %s", name, tostring(said)))
        return "null"
    end
    common.logChange("dialogue " .. name, "TF-SIGNAL", said
        and string.format('waypoint %s says line "%s" (dialogue "%s", %s)', name, said.line, said.id, said.clear and "clear" or "distorted")
        or string.format("waypoint %s is quiet", name))
    return said and string.format('{"line":%s,"id":%s,"ms":%d,"clear":%s}',
        common.jsonString(said.line), common.jsonString(said.id), said.ms, tostring(said.clear)) or "null"
end

-- The active ones as the comma-separated objects of the telemetry "signals" array, with the CRTV
-- `crtvState` (tf_crtv.read, nil if unreadable).
-- signal/tuned are the raised in-game CRTV's view; rangeSignal is what a raised CRTV would pick up
-- at this distance (for the phone's own scanner); found: the player has tuned it in before;
-- dialogue: what it says right now (speaking()), or null.
function M.json(crtvState)
    local p = player.read()
    local raised = crtvState and crtvState.active
    local tunedAddress = crtv.tunedWaypointAddress(crtvState)
    local out = {}
    for _, s in ipairs(active) do
        local c, info = s.component, s.info
        if c:IsValid() then
            local loc = c:GetOwner():K2_GetActorLocation()
            local x, y = common.toPhone(loc)
            out[#out + 1] = string.format('{"id":%s,"kind":"%s","x":%.2f,"y":%.2f,"channel":%.3f,"tolerance":%.3f,'
                .. '"toleranceOuter":%s,"rangeSignal":%.2f,"signal":%.2f,"tuned":%s,"found":%s,"video":%s,"dialogue":%s}',
                common.jsonString(info.name), info.kind, x, y, info.channel, info.tolerance,
                common.jsonNumber(info.toleranceOuter, "%.3f"),
                common.falloff(common.distance(loc, p), info.reach[1], info.reach[2]),
                raised and c:GetCurrentUntunedSignalStrength() or 0,
                tostring(tunedAddress == c:GetAddress()), tostring(c:HasBeenFullyTuned()),
                common.jsonString(info.video), dialogueJson(info.name, c))
        end
    end
    return table.concat(out, ",")
end

return M
