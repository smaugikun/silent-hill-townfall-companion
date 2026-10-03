-- Tracks ATownfallEnemyCharacter actors (BP_TestAIAgent_C and friends) near the player.

local common = require("tf_common")
local player = require("tf_player")
local crtv = require("tf_crtv")
local log = common.log

local ENEMY_CLASS = "/Script/Townfall.TownfallEnemyCharacter"
local RANGE = 6000 -- cm; enemies this close at the last update go out with every telemetry tick

-- EEnemyAlertState from Townfall_enums.hpp
local ALERT_NAMES = { [0] = "none", "investigating", "stunned", "searching", "spotted" }

local M = {}

local tracked = {} -- every enemy constructed since the level loaded; pruned as they go away
local nearby = {}  -- within RANGE at the last update
local enemyLog = {} -- enemy name -> last logged alert/death (for [TF-ENEMY] change lines), signal source and video

-- FindAllOf walks every UObject (~20 ms in Townfall), too slow to repeat during play.
-- Enemies are spawned on demand, so hear about them as they're constructed instead.
-- This runs inside StaticConstructObject: only remember the object here.
local enemyClass = StaticFindObject(ENEMY_CLASS)
if enemyClass and enemyClass:IsValid() then
    NotifyOnNewObject(ENEMY_CLASS, function(e) tracked[#tracked + 1] = e end)
else
    log("TF-ENEMY", "%s not found, only enemies present at level load will be tracked", ENEMY_CLASS)
end

-- BP_TestAIAgent_C carries its CRTV signal source as RadioStaticSource; other enemy
-- classes may not have the property at all.
local function staticSource(e)
    local ok, source = pcall(function() return e.RadioStaticSource end)
    return ok and source and source:IsValid() and source or nil
end

-- What the enemy's signal source says about tuning in, reach (cm) and its clip; constant per enemy.
-- The tuned-in CRTV plays a prerecorded clip from Content/Movies/CRTV_Movies, which the phone plays too.
local function readSourceInfo(source)
    local ok, info = pcall(function()
        return {
            channel = source.TuningFrequency,
            tolerance = source.TuningTolerance_Inner,
            toleranceOuter = source.TuningTolerance_Outer,
            reach = {
                outdoor = { source.distanceSignalFalloffBegin_ActiveOutdoor, source.distanceSignalCutoff_ActiveOutdoor },
                indoor = { source.distanceSignalFalloffBegin_ActiveIndoor, source.distanceSignalCutoff_ActiveIndoor },
            },
            video = common.videoPath(source.CrtvVideoSignal_Url, common.CRTV_VIDEO),
        }
    end)
    return ok and type(info.channel) == "number" and info or nil
end

-- How strongly a raised CRTV would pick the enemy up at this distance, from its own falloff and
-- cutoff (the game also weighs line of sight and elevation). Feeds the phone's own scanner.
local function rangeSignal(info, distance, indoor)
    local falloff = info and info.reach[indoor and "indoor" or "outdoor"]
    return falloff and common.falloff(distance, falloff[1], falloff[2]) or 0
end

-- CRTV readings must never cost the enemy positions: failures are logged once and
-- count as "not detected".
local function crtvFailed(err)
    common.logChange("crtv view", "TF-CRTV", "per-enemy read error: " .. tostring(err))
end

local function crtvView(reader, e)
    local source = reader and staticSource(e)
    if not source then return nil end
    local ok, view = pcall(reader, source)
    if ok then return view end
    crtvFailed(view)
end

local function read(e, name)
    local alert = e.AlertState
    local enemy = {
        name = name or e:GetFName():ToString(),
        loc = e:K2_GetActorLocation(),
        alert = ALERT_NAMES[alert] or tostring(alert),
        dead = e:GetHasBegunDeath(),
    }

    local prev = enemyLog[enemy.name]
    enemy.source = prev and prev.source
    if not prev then
        log("TF-ENEMY", "new %s alert=%s dead=%s", e:GetFullName(), enemy.alert, tostring(enemy.dead))
        local source = staticSource(e)
        enemy.source = source and readSourceInfo(source)
        if enemy.source then  -- the clip names the kind of monster the phone shows (static/monsters.js)
            log("TF-ENEMY", "%s crtv channel %.3f clip %s", enemy.name, enemy.source.channel, enemy.source.video or "none")
        else
            log("TF-ENEMY", "%s %s", enemy.name, source and "crtv source unreadable" or "has no RadioStaticSource")
        end
    elseif prev.alert ~= enemy.alert or prev.dead ~= enemy.dead then
        log("TF-ENEMY", "%s alert=%s dead=%s", enemy.name, enemy.alert, tostring(enemy.dead))
    end
    enemyLog[enemy.name] = { alert = enemy.alert, dead = enemy.dead, source = enemy.source }
    return enemy
end

-- The enemies within RANGE at the last update.
function M.nearby()
    return nearby
end

-- One full walk right after a level load, where a hitch goes unnoticed. It also
-- catches enemies that existed before this mod was (re)loaded.
function M.onLevelStart()
    nearby, enemyLog = {}, {}
    common.clearChannel("enemy summary")
    tracked = FindAllOf("TownfallEnemyCharacter") or {}
    log("TF-ENEMY", "level load: %d enemies", #tracked)
end

-- Prunes destroyed enemies and re-picks the ones within RANGE of the player.
function M.update()
    if not player.isTracking() then
        nearby = {}
        return
    end
    local p = player.read()
    local alive, inRange = {}, {}
    for _, e in ipairs(tracked) do
        local name = e:IsValid() and e:GetFName():ToString()
        -- NotifyOnNewObject also reports class default objects, which aren't in the world.
        if name and not name:find("^Default__") then
            alive[#alive + 1] = e
            local loc = read(e, name).loc
            local dx, dy = loc.X - p.X, loc.Y - p.Y
            if dx * dx + dy * dy <= RANGE * RANGE then inRange[#inRange + 1] = e end
        end
    end
    tracked, nearby = alive, inRange
    common.logChange("enemy summary", "TF-ENEMY", string.format("%d enemies, %d within %d m", #alive, #inRange, RANGE // 100))
end

-- Enemies in range as the comma-separated objects of the telemetry "enemies" array, with the CRTV
-- `crtvState` (tf_crtv.read, nil if unreadable).
-- signal: strength of the enemy's static on the raised in-game CRTV, 0..1 (0 when lowered or out of range).
-- detected: signal > 0. tuned: the CRTV is tuned to an enemy and this is the strongest source.
-- rangeSignal: what a raised CRTV would pick up at this distance, whether or not it is raised.
-- channel: dial position (0..1) the enemy tunes in at; tolerance: how near the dial must be for it to come
-- in clear, toleranceOuter: for it to come in at all; null without a readable signal source.
-- video: the enemy's tuned-in clip under Content/Movies/CRTV_Movies without extension, or null.
function M.json(crtvState)
    local ok, reader = pcall(crtv.sourceReader, crtvState)
    if not ok then
        crtvFailed(reader)
        reader = nil
    end
    local p = player.read()
    local indoor = player.isIndoor()
    local out = {}
    for _, e in ipairs(nearby) do
        if e:IsValid() then
            local enemy = read(e)
            local view = crtvView(reader, e)
            local signal = view and view.signal or 0
            local info = enemy.source
            local x, y = common.toPhone(enemy.loc)
            out[#out + 1] = string.format('{"id":%s,"x":%.2f,"y":%.2f,"alive":%s,"signal":%.2f,"detected":%s,"tuned":%s,'
                .. '"rangeSignal":%.2f,"channel":%s,"tolerance":%s,"toleranceOuter":%s,"video":%s}',
                common.jsonString(enemy.name), x, y, tostring(not enemy.dead),
                signal, tostring(signal > 0), tostring(view ~= nil and view.tuned),
                rangeSignal(info, common.distance(enemy.loc, p), indoor),
                common.jsonNumber(info and info.channel, "%.3f"), common.jsonNumber(info and info.tolerance, "%.3f"),
                common.jsonNumber(info and info.toleranceOuter, "%.3f"), common.jsonString(info and info.video))
        end
    end
    return table.concat(out, ",")
end

return M
