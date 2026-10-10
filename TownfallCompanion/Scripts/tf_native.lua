-- The native part, tf_native.dll (its source: native/ in the repository). It copies the CRTV's screen from the GPU
-- while a phone watches, for the companion to send to the phone, moves the image in the alignment stages of the
-- fine-tune mini-game (tf_alignment.lua), turns the CRTV's direction to where the phone looks (M.look), takes the
-- CRTV's sound from the game's mixer for the phone and quiets the game's while the phone plays it (M.sound), and reads
-- the radio's mini-game state, which the game keeps where Lua can't reach. It works only with the game build it was
-- made for; otherwise it stays off.
--
-- UE4SS gives a DLL no Lua C API, so values go through small files in %TEMP%: this module writes which texture is the
-- CRTV's screen, which radio is the player's, and how often and how wide the phone wants the screen; the DLL writes its
-- state (townfall-companion-native.json, also what the companion's /api/crtv/status shows).

local common = require("tf_common")
local player = require("tf_player")

local M = {}

local update, align, look, sound -- the DLL's functions, once loaded
local sourcePath, alignPath, lookPath, soundPath, statusPath, busesPath
local wantFps, wantWidth = 0, 640
local lastStatus, lastMiniGame
local statusFile    -- the DLL's state, kept open: the DLL rewrites it in place
local miniGame      -- {mode, stage} while the radio fine-tunes, from the DLL's last state
local aligned = {}  -- {result, calls}: what the DLL did with the phone's last pose, and how many it applied

-- The game's values (its PDB): EFineTuningState and EAdvancedTuningStage.
local MODES = { [0] = "standard", "advanced", "signal found" }
local STAGES = { [0] = "fine tuning", "calibration message", "stabilise image", "align image", "before storing", "storing" }

function M.init(tempDir)
    local here = debug.getinfo(1, "S").source:match("^@(.*[\\/])")
    if not (tempDir and here) then return end
    local dll = here .. "tf_native.dll"
    local init, err = package.loadlib(dll, "tf_native_init")
    if not init then
        return common.log("TF-NATIVE", "tf_native.dll not loaded (%s): the phone gets no picture or sound", tostring(err))
    end
    update, align = package.loadlib(dll, "tf_native_update"), package.loadlib(dll, "tf_native_align")
    look, sound = package.loadlib(dll, "tf_native_look"), package.loadlib(dll, "tf_native_sound")
    sourcePath = tempDir .. "\\townfall-companion-native-source.bin"
    alignPath = tempDir .. "\\townfall-companion-native-align.bin"
    lookPath = tempDir .. "\\townfall-companion-native-look.bin"
    soundPath = tempDir .. "\\townfall-companion-native-sound.bin"
    statusPath = tempDir .. "\\townfall-companion-native.json"
    busesPath = tempDir .. "\\townfall-companion-native-buses.txt"
    init()
    common.log("TF-NATIVE", "tf_native.dll loaded")
end

-- From the companion's heartbeat (main.lua): pictures a second (0: none, no phone watching) and their width.
function M.request(fps, width)
    wantFps = math.max(0, math.min(30, math.floor(tonumber(fps) or 0)))
    wantWidth = (width == 320 or width == 480) and width or 640
end

-- The player's raised radio, or nil.
local function raisedRadio()
    if not player.isTracking() then return nil end
    local pawn = player.pawn()
    if not pawn:GetIsRadioInActiveMode() then return nil end
    local radio = pawn:GetRadio()
    return radio and radio:IsValid() and radio or nil
end

-- The texture the CRTV's screen is drawn into, as the radio's own material shows it (RadarTexture), and only that
-- radio's: its widget must be the CRTV's screen widget. 0 if anything doesn't match.
local function screenTexture(radio)
    local widget = radio:GetCRTVWidget()
    local wrapped = radio.RadarPropMap:Find(radio.PlayerCharacterType)
    local setup = wrapped and wrapped.get and wrapped:get() or wrapped
    local radar = setup and setup.RadarTexture
    if not (radar and widget and widget:IsValid() and radar.Widget and radar.Widget:IsValid()
            and radar.Material and radar.Material:IsValid()) then return 0 end
    if radar.Widget:GetAddress() ~= widget:GetAddress() then return 0 end
    local texture = radar.Material:K2_GetTextureParameterValue(radar.MaterialTextureName)
    return texture and texture:IsValid() and texture:GetAddress() or 0
end

local function readMiniGame(text)
    aligned = { result = tonumber(text:match('"alignmentResult"%s*:%s*(%d+)')),
                calls = tonumber(text:match('"alignmentCalls"%s*:%s*(%d+)')) }
    local radio = text:match('"radio"%s*:%s*(%b{})')
    local tuning = radio and tonumber(radio:match('"fineTuning"%s*:%s*(%d+)'))
    if tuning == 1 then
        miniGame = { mode = tonumber(radio:match('"mode"%s*:%s*(%d+)')), stage = tonumber(radio:match('"stage"%s*:%s*(%d+)')) }
    else
        miniGame = nil
    end
    local now = miniGame and string.format("%s, %s", MODES[miniGame.mode] or tostring(miniGame.mode),
        miniGame.mode == 1 and (STAGES[miniGame.stage] or tostring(miniGame.stage)) or "bar") or (radio and "off")
    if now and now ~= lastMiniGame then
        lastMiniGame = now
        common.log("TF-NATIVE", "mini-game: %s", now)
    end
end

-- The packets for the DLL go through files kept open: opening a file in %TEMP% waits for the virus scanner (4 to
-- 12 ms in the game, on the game thread), writing through an open handle doesn't. Each kind of packet has one size,
-- so it is rewritten in place. True once written.
local packetFiles = {}
local function send(path, packet)
    local f = packetFiles[path]
    if not f then
        f = io.open(path, "w+b")
        if not f then return false end
        packetFiles[path] = f
    end
    f:seek("set", 0)
    f:write(packet)
    f:flush()
    return true
end

-- The DLL listening to the game's FMOD mixer (audio.c): its state and the mixer's buses, logged as they change (the
-- bus list is read when its count or the state changes). How loud each tapped bus is: /api/crtv/status.
local AUDIO_STATES = { [-6] = "no shared memory for the companion", [-5] = "FMOD's update couldn't be hooked",
    [-4] = "a tap couldn't be attached",
    [-3] = "no DSP could be made", [-2] = "not FMOD 2", [-1] = "an FMOD function is missing",
    [0] = "waiting for the game to load FMOD", [1] = "waiting for the CRTV's bus", [2] = "attaching", [3] = "listening" }
local busesLogged
local function readAudio(text)
    local state = tonumber(text:match('"audio"%s*:%s*(%-?%d+)'))
    if not state then return end
    common.logChange("audio state", "TF-AUDIO", "game sound tap: " .. (AUDIO_STATES[state] or tostring(state))
        .. (state == 3 and string.format(" (%s Hz)", text:match('"audioRate"%s*:%s*(%d+)') or "?") or ""))
    local listed = string.format("%d %s", state, text:match('"audioBuses"%s*:%s*(%d+)') or "0")
    if listed ~= busesLogged and not listed:match(" 0$") then
        local f = io.open(busesPath, "r")
        if f then
            local list = {}
            for line in f:lines() do list[#list + 1] = line end
            f:close()
            busesLogged = listed
            common.log("TF-AUDIO", "FMOD buses (%d): %s", #list, table.concat(list, ", "))
        end
    end
end

-- Every half second, on the game thread. Without a phone watching it tells the DLL so: the copying stops.
function M.update()
    if not update then return end
    local radio = common.optional("native radio", "TF-NATIVE", raisedRadio, nil)
    local texture = 0
    if radio and wantFps > 0 then
        texture = common.optional("native source", "TF-NATIVE", function() return screenTexture(radio) end, 0)
    end
    if not send(sourcePath, string.pack("<c8I8I8I4I4I8", "TFNATV03", texture, radio and radio:GetAddress() or 0,
        wantFps, wantWidth, os.time())) then return end
    update()
    statusFile = statusFile or io.open(statusPath, "r")
    if not statusFile then return end
    statusFile:seek("set", 0)
    local text = statusFile:read("a") or ""
    if not text:find("}", 1, true) then return end -- read while the DLL rewrote it: the next one comes in half a second
    readMiniGame(text)
    readAudio(text)
    local status = text:match('"status"%s*:%s*"([^"]+)"')
    if status then
        status = status .. ", capture " .. (text:match('"captureStatus"%s*:%s*"([^"]+)"') or "?")
        if status ~= lastStatus then
            lastStatus = status
            common.log("TF-NATIVE", "%s", status)
        end
    end
end

-- Moves the alignment stage's image by (x, y) units on `radio` (an address from the live pawn); seq is the phone
-- command's (the bridge's clock in ms). The DLL applies it once, and only in an alignment stage.
function M.align(radio, x, y, seq)
    if align and send(alignPath, string.pack("<c8I8ddI8", "TFALIGN1", radio, x, y, seq)) then align() end
end

-- Whether the phone looks around, how far it points from where the character faces (degrees, clockwise seen from
-- above, as the game's yaw) and how far up (degrees above the horizon): the CRTV looks there, turning along when the
-- character turns. seq is the phone command's (the bridge's clock in ms). The DLL holds a look for 2 s; the phone says
-- it every second.
function M.look(on, yaw, pitch, seq)
    if look and send(lookPath, string.pack("<c8ddI8I8", "TFLOOK04", yaw, pitch, on and 1 or 0, seq)) then look() end
end

-- Whether the game's CRTV sound is quiet because the phone plays it (tf_audio.lua). The DLL lets it back by itself 6 s
-- after it was last told so.
function M.sound(mute)
    if sound and send(soundPath, string.pack("<c8I8", "TFSOUND1", mute and 1 or 0)) then sound() end
end

-- Whether the DLL is loaded (it may still stay off for another game build: its status says).
function M.loaded()
    return update ~= nil
end

-- The radio's mini-game while it runs: {mode (0 standard, 1 advanced, 2 signal found), stage (advanced: 0 fine
-- tuning ... 4 before storing, 5 storing)}, as of the DLL's last state (up to a second old); nil otherwise.
function M.miniGame()
    return miniGame
end

-- What the DLL did with the phone's poses: {result (1 applied, 2 bad packet or move, 3 not in an image stage), calls
-- (applied so far)}.
function M.alignment()
    return aligned
end

return M
