-- Townfall Companion - UE4SS mod entry point.
--
-- Tracks the local player (tf_player), nearby enemies (tf_enemies), the CRTV (tf_crtv), its active
-- waypoints (tf_signals) and the cutscene playing (tf_cutscene), and writes them to
-- %TEMP%\townfall-companion-telemetry.json for the companion's bridge (UE4SS Lua has no sockets).
-- The phone's commands come back through files too (tf_commands); tf_audio quiets the game's CRTV
-- sound while the phone plays it. Two small heartbeat files tell the companion and the mod about each other:
-- the companion's (how many phones have the page open: the mod reads the game only while one is, and idles
-- otherwise) and the game's (every second: the companion closes a minute after it stops).

local common = require("tf_common")
local log, logChange, optional = common.log, common.logChange, common.optional

log("TF-COMPANION", "Townfall Companion loaded")

local player = require("tf_player")
local enemies = require("tf_enemies")
local crtv = require("tf_crtv")
local signals = require("tf_signals")
local cutscene = require("tf_cutscene")
local audio = require("tf_audio")
local commands = require("tf_commands")
local record = require("tf_record")

local SAMPLE_MS = 100  -- telemetry rate
local COMMAND_MS = 50  -- phone commands; steering needs the quick turnaround
local UPDATE_MS = 1000 -- player lookup, enemy range checks and state logs
local AUDIO_MS = 250   -- silencing the game's CRTV sound again where it restarted

local tempDir = os.getenv("TEMP") or os.getenv("TMP")
local telemetryPath = tempDir and (tempDir .. "\\townfall-companion-telemetry.json")
local bridgeBeatPath = tempDir and (tempDir .. "\\townfall-companion-bridge.json")
local gameBeatPath = tempDir and (tempDir .. "\\townfall-companion-game.json")
if not telemetryPath then log("TF-COMPANION", "no TEMP directory, telemetry disabled") end
commands.init(tempDir)

-- Whether a companion is running and how many phones have the page open, from its heartbeat.
local bridgeUp, phoneHere = false, false
local function readBridge()
    local f = bridgeBeatPath and io.open(bridgeBeatPath, "r")
    if not f then return false, false end
    local text = f:read("a") or ""
    f:close()
    local time = tonumber(text:match('"time"%s*:%s*(%d+)'))
    local phones = tonumber(text:match('"phones"%s*:%s*(%d+)')) or 0
    local fresh = time ~= nil and math.abs(os.time() - time) <= 5
    return fresh, fresh and phones > 0
end

-- Every second: says the game runs (the companion closes once it stops), and whether a companion is
-- running and a phone has its page open.
local function presence()
    local f = gameBeatPath and io.open(gameBeatPath, "w")
    if f then
        f:write(string.format('{"time":%d}', os.time()))
        f:close()
    end
    bridgeUp, phoneHere = readBridge()
end

local function whenPhone(fn)
    return function()
        if phoneHere then fn() end
    end
end

local function refresh()
    if player.refresh() then
        enemies.onLevelStart()
        crtv.onLevelStart()
        signals.onLevelStart()
        cutscene.onLevelStart()
    end
end

-- A part that fails (e.g. a game update renamed a property) is logged once and sent empty: it
-- mustn't take the player's position, or the other parts, down with it.
local KEEPALIVE_S = 0.5
local lastBody, lastWrite = nil, -math.huge

local function sample()
    if not telemetryPath or not player.isTracking() then return end
    local x, y, yaw = common.toPhone(player.read())
    local radio = optional("crtv read", "TF-CRTV", crtv.read, nil) -- read once, used by the parts below
    local enemyJson = optional("enemy read", "TF-ENEMY", function() return enemies.json(radio) end, "")
    local signalJson = optional("signal read", "TF-SIGNAL", function() return signals.json(radio) end, "")
    local crtvJson = radio and optional("crtv json", "TF-CRTV", function() return crtv.json(radio) end, "null") or "null"
    local cutsceneJson = optional("cutscene read", "TF-CUTSCENE", cutscene.json, "null")
    local worldTime = common.tryNumber(function() return player.pawn():GetGameTimeSinceCreation() end)
    local pitch = common.tryNumber(player.pitch) or 0
    local alive = player.isAlive()
    logChange("player life", "TF-PLAYER", alive and "alive" or "dead")

    -- Nothing but the clocks changed: the file is rewritten only every KEEPALIVE_S (the companion calls a
    -- file older than 2 s the game having left), not ten times a second.
    local body = string.format('"player":{"x":%.2f,"y":%.2f,"yaw":%.1f,"pitch":%.1f,"alive":%s},"enemies":[%s],'
        .. '"signals":[%s],"crtv":%s,"cutscene":%s,"audio":{"gameSoundOff":%s},"recording":%s}',
        x, y, yaw, pitch, tostring(alive), enemyJson, signalJson, crtvJson, cutsceneJson, tostring(audio.isOff()),
        tostring(record.isOn()))
    local now = os.clock()
    if body == lastBody and now - lastWrite < KEEPALIVE_S then return end
    local f, err = io.open(telemetryPath, "w")
    if not f then return logChange("telemetry", "TF-COMPANION", "cannot write telemetry: " .. tostring(err)) end
    -- t: when this was read, on the game's clock (s), however late it reaches the phone: the phone
    -- keeps speech in step and runs the fine-tune box by it. world: the world's own clock (s, the player's
    -- time in it), which stands still while the game is paused: the phone then holds everything too.
    f:write(string.format('{"t":%.3f,"world":%s,', now, common.jsonNumber(worldTime, "%.2f")), body)
    f:close()
    lastBody, lastWrite = body, now
    logChange("telemetry", "TF-COMPANION", "telemetry -> " .. telemetryPath)
end

-- The game waits for every loop below, so each one's time on the game thread is added up and logged
-- every PERF_REPORT_S. os.clock is wall time in 1 ms steps on Windows (MSVC): totals over many calls
-- are what tell.
local PERF_REPORT_S = 30
local perf, perfSince = {}, os.clock()

local function measure(channel, seconds)
    local p = perf[channel] or { calls = 0, total = 0, max = 0 }
    perf[channel] = p
    p.calls, p.total, p.max = p.calls + 1, p.total + seconds * 1000, math.max(p.max, seconds * 1000)
end

local function reportPerf()
    local elapsed = os.clock() - perfSince
    if elapsed < PERF_REPORT_S then return end
    local parts, total = {}, 0
    for channel, p in pairs(perf) do
        total = total + p.total
        parts[#parts + 1] = string.format("%s %d calls %.0f ms (max %.0f ms)", channel, p.calls, p.total, p.max)
    end
    table.sort(parts)
    log("TF-PERF", "last %.0f s: %.1f ms of game thread per second; %s", elapsed, total / elapsed, table.concat(parts, ", "))
    perf, perfSince = {}, os.clock()
end

local function guarded(channel, fn)
    return function()
        local started = os.clock()
        local ok, err = pcall(fn)
        measure(channel, os.clock() - started)
        if ok then
            common.clearChannel(channel)
        else
            logChange(channel, "TF-COMPANION", channel .. " error: " .. tostring(err))
        end
    end
end

-- Every loop runs on the game thread. Not LoopAsync: in this UE4SS build its callbacks run on a
-- second OS thread in the same Lua state as game-thread callbacks, with no lock around either,
-- and that race crashes Townfall inside UE4SS (access violation).
if not LoopInGameThreadWithDelay then
    log("TF-COMPANION", "this UE4SS has no LoopInGameThreadWithDelay; the mod stays idle")
    return
end
presence() -- once now, so the first loops below already know
LoopInGameThreadWithDelay(UPDATE_MS, guarded("presence", presence))
LoopInGameThreadWithDelay(UPDATE_MS, guarded("refresh", refresh))
-- Only while a phone has the page open: nobody else reads what these gather.
LoopInGameThreadWithDelay(UPDATE_MS, guarded("enemy update", whenPhone(enemies.update)))
LoopInGameThreadWithDelay(UPDATE_MS, guarded("crtv update", whenPhone(crtv.update)))
LoopInGameThreadWithDelay(UPDATE_MS, guarded("signal update", whenPhone(signals.update)))
LoopInGameThreadWithDelay(UPDATE_MS, guarded("cutscene update", whenPhone(cutscene.update)))
LoopInGameThreadWithDelay(SAMPLE_MS, guarded("sample", whenPhone(sample)))
LoopInGameThreadWithDelay(SAMPLE_MS, guarded("record", record.update)) -- nothing while it is off
LoopInGameThreadWithDelay(COMMAND_MS, guarded("phone command", commands.poll))
LoopInGameThreadWithDelay(AUDIO_MS, guarded("audio", audio.update))
LoopInGameThreadWithDelay(UPDATE_MS, reportPerf)
