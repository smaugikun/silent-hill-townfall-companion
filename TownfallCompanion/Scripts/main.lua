-- Townfall Companion - UE4SS mod entry point.
--
-- Tracks the local player (tf_player), nearby enemies (tf_enemies), the CRTV (tf_crtv), its active
-- waypoints (tf_signals) and the cutscene playing (tf_cutscene), and writes them to
-- %TEMP%\townfall-companion-telemetry.json for the companion's bridge (UE4SS Lua has no sockets).
-- The phone's commands come back through files too (tf_commands); tf_audio quiets the game's CRTV
-- sound while the phone plays it; tf_native (a DLL) copies the CRTV's screen for the phone while one
-- watches, and moves the mini-game's alignment image (tf_alignment). Two small heartbeat files tell the companion and the mod about each other:
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
local native = require("tf_native")
local alignment = require("tf_alignment")

local SAMPLE_MS = 100  -- telemetry rate
local COMMAND_MS = 50  -- phone commands; steering needs the quick turnaround
local UPDATE_MS = 1000 -- player lookup, enemy range checks and state logs
local AUDIO_MS = 250   -- the game's CRTV sound back once the phone stops asking for it quiet
local NATIVE_MS = 500  -- which texture the CRTV's screen is, for tf_native.dll

local tempDir = os.getenv("TEMP") or os.getenv("TMP")
local telemetryPath = tempDir and (tempDir .. "\\townfall-companion-telemetry.json")
local bridgeBeatPath = tempDir and (tempDir .. "\\townfall-companion-bridge.json")
local gameBeatPath = tempDir and (tempDir .. "\\townfall-companion-game.json")
if not telemetryPath then log("TF-COMPANION", "no TEMP directory, telemetry disabled") end
commands.init(tempDir)
native.init(tempDir)

-- From the companion's heartbeat: whether it runs with a phone on the page, and how often and how wide the phone
-- wants the CRTV's screen. The file stays open: the companion rewrites it in place every second, and a read in the
-- middle of that keeps what the one before said.
local phoneHere = false
local bridgeBeat
local lastBridge = { false, 0, nil }
local function readBridge()
    bridgeBeat = bridgeBeat or (bridgeBeatPath and io.open(bridgeBeatPath, "r"))
    if not bridgeBeat then return false, 0, nil end
    bridgeBeat:seek("set", 0)
    local text = bridgeBeat:read("a") or ""
    local time = tonumber(text:match('"time"%s*:%s*(%d+)'))
    if not (time and text:find("}", 1, true)) then return lastBridge[1], lastBridge[2], lastBridge[3] end
    local phones = tonumber(text:match('"phones"%s*:%s*(%d+)')) or 0
    lastBridge = { math.abs(os.time() - time) <= 5 and phones > 0, tonumber(text:match('"nativeFps"%s*:%s*(%d+)')) or 0,
                   tonumber(text:match('"nativeWidth"%s*:%s*(%d+)')) }
    return lastBridge[1], lastBridge[2], lastBridge[3]
end

-- Every second: says the game runs (the companion closes once it stops), and whether a phone has the page open. The
-- heartbeat file stays open and is rewritten in place (its length stays): opening a file waits for the virus scanner.
local gameBeat
local function presence()
    gameBeat = gameBeat or (gameBeatPath and io.open(gameBeatPath, "w+b"))
    if gameBeat then
        gameBeat:seek("set", 0)
        gameBeat:write(string.format('{"time":%d}', os.time()))
        gameBeat:flush()
    end
    local fps, width
    phoneHere, fps, width = readBridge()
    native.request(phoneHere and fps or 0, width)
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
-- The telemetry file stays open and is rewritten in place (opening one waits for the virus scanner): spaces pad a
-- shorter text to the length before, so nothing of the last one is left after it.
local telemetryFile, telemetryLength = nil, 0

local function sample()
    if not telemetryPath or not player.isTracking() then return end
    local x, y, yaw = common.toPhone(player.read())
    local radio = optional("crtv read", "TF-CRTV", crtv.read, nil) -- read once, used by the parts below
    local enemyJson = optional("enemy read", "TF-ENEMY", function() return enemies.json(radio) end, "")
    local signalJson = optional("signal read", "TF-SIGNAL", function() return signals.json(radio) end, "")
    local crtvJson = radio and optional("crtv json", "TF-CRTV", function() return crtv.json(radio) end, "null") or "null"
    local cutsceneJson = optional("cutscene read", "TF-CUTSCENE", cutscene.json, "null")
    local worldTime = common.tryNumber(function() return player.pawn():GetGameTimeSinceCreation() end)
    local alive = player.isAlive()
    logChange("player life", "TF-PLAYER", alive and "alive" or "dead")

    -- Nothing but the clocks changed: the file is rewritten only every KEEPALIVE_S (the companion calls a
    -- file older than 2 s the game having left), not ten times a second.
    local body = string.format('"player":{"x":%.2f,"y":%.2f,"yaw":%.1f,"alive":%s},"enemies":[%s],'
        .. '"signals":[%s],"crtv":%s,"cutscene":%s,"audio":{"gameSoundOff":%s}}',
        x, y, yaw, tostring(alive), enemyJson, signalJson, crtvJson, cutsceneJson, tostring(audio.isOff()))
    local now = os.clock()
    if body == lastBody and now - lastWrite < KEEPALIVE_S then return end
    local err
    if not telemetryFile then telemetryFile, err = io.open(telemetryPath, "w+b") end
    if not telemetryFile then
        return logChange("telemetry", "TF-COMPANION", "cannot write telemetry: " .. tostring(err))
    end
    -- t: when this was read, on the game's clock (s), however late it reaches the phone: the phone tells a new sample
    -- from one it has by it, and times the world's clock with it. world: the world's own clock (s, the player's time
    -- in it), which stands still while the game is paused: the phone then holds everything too.
    local text = string.format('{"t":%.3f,"world":%s,', now, common.jsonNumber(worldTime, "%.2f")) .. body
    telemetryFile:seek("set", 0)
    telemetryFile:write(text, string.rep(" ", telemetryLength - #text))
    telemetryFile:flush()
    telemetryLength = math.max(telemetryLength, #text)
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
LoopInGameThreadWithDelay(NATIVE_MS, guarded("native", function()
    native.update()
    crtv.hideInMiniGame() -- by the mini-game state native.update just read
    alignment.logImageStage()
    alignment.watchPhone()
end))
LoopInGameThreadWithDelay(COMMAND_MS, guarded("phone command", commands.poll))
LoopInGameThreadWithDelay(AUDIO_MS, guarded("audio", audio.update))
LoopInGameThreadWithDelay(UPDATE_MS, reportPerf)
