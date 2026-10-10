-- Commands from the phone, written by bridge.py as small JSON files in %TEMP%:
--   townfall-companion-commands.json  {"active":true,"animate":true,"frequency":0.23,"seq":...}  the CRTV's dial, and
--                                     active: switched on (VIEW; animate: raised with the character's animation, else
--                                     silently)
--   townfall-companion-steer.json     {"yaw":132.5,"seq":...}                    the phone's heading
--   townfall-companion-confirm.json   {"seq":...}                                the D-pad's centre pressed
--   townfall-companion-release.json   {"seq":...}                                and let go
--   townfall-companion-fine-tune.json {"direction":"up","seq":...}               a fine-tune arrow (the D-pad)
--   townfall-companion-align.json     {"roll":3.5,"pitch":-1.2,"dpu":30,"session":7,"state":"on","seq":...} the phone's
--                                     lean and tilt from its neutral pose, for the image stages (tf_alignment.lua)
--   townfall-companion-mode.json      {"selector":"VIEW","onMonitor":false,"miniGameShown":false,"seq":...} the
--                                     phone's mode, every couple of seconds: whether the CRTV is the phone's, and in
--                                     VIEW whether the monitor shows it, and in the mini-game (tf_crtv.lua)
--   townfall-companion-note.json      {"text":"page error: ...","seq":...}       a problem on the phone's page, for the log
--   townfall-companion-look.json      {"on":true,"yaw":-35.5,"pitch":4,"seq":...} whether the phone looks around, how
--                                     far from where the character faces and how far up: the CRTV looks there
--                                     (tf_native.lua)
--   townfall-companion-audio.json     {"muteGame":true,"seq":...}                  the game's CRTV sound quiet while the
--                                     phone plays it
-- seq is the bridge's clock in ms; a changed seq means a new command.

local common = require("tf_common")
local player = require("tf_player")
local crtv = require("tf_crtv")
local audio = require("tf_audio")
local alignment = require("tf_alignment")
local native = require("tf_native")

local STEER_MAX_AGE = 2   -- seconds; steering older than this is from a phone that stopped sending
local LOOK_MAX_AGE = 2    -- and a look: the DLL holds it as long
local STEER_GAP_MS = 2000 -- after a longer pause the phone's next heading only sets a new starting point
local CONFIRM_MAX_AGE = 1 -- a late press would land at another moment of the mini-game
local AUDIO_MAX_AGE = 5   -- the phone repeats its sound request every 2 s

local M = {}

local channels = {}
local steeringUntil = -math.huge -- os.clock(): the phone steers the character until then
local looking = false            -- the phone's last look was on

function M.init(tempDir)
    if not tempDir then return end
    channels.crtv = { path = tempDir .. "\\townfall-companion-commands.json" }
    channels.steer = { path = tempDir .. "\\townfall-companion-steer.json" }
    channels.confirm = { path = tempDir .. "\\townfall-companion-confirm.json" }
    channels.release = { path = tempDir .. "\\townfall-companion-release.json" }
    channels.mode = { path = tempDir .. "\\townfall-companion-mode.json" }
    channels.fineTune = { path = tempDir .. "\\townfall-companion-fine-tune.json" }
    channels.audio = { path = tempDir .. "\\townfall-companion-audio.json" }
    channels.align = { path = tempDir .. "\\townfall-companion-align.json" }
    channels.note = { path = tempDir .. "\\townfall-companion-note.json" }
    channels.look = { path = tempDir .. "\\townfall-companion-look.json" }
end

-- os.time has whole seconds, so this is coarse: enough to drop commands from a phone long gone.
local function tooOld(seq, maxAge)
    return os.time() - seq / 1000 > maxAge
end

-- A JSON number, exponent included: Python writes a tiny yaw as 3.2e-05.
local function number(text, key)
    return tonumber(text:match('"' .. key .. '"%s*:%s*(%-?[%d%.]+[eE]?[%+%-]?%d*)'))
end

-- The file's text if it holds a new command. The bridge writes seq last, so a half-written
-- file has none. The first command seen is only a baseline: one left over from an earlier
-- session must not raise the CRTV or turn the player on load. A file that isn't there yet
-- holds no leftover, so then the first command that shows up counts.
-- Each file stays open once it is there: opening a file the bridge has just written waits for the
-- virus scanner (4 ms typical in %TEMP%, up to 12 ms in the game), reading through an
-- open handle doesn't (0.02 ms). The bridge rewrites the file in place, so the handle sees each command.
local function fresh(channel)
    if not channel then return nil end
    if not channel.file then
        channel.file = io.open(channel.path, "r")
        if not channel.file then
            channel.seq = channel.seq or ""
            return nil
        end
    end
    channel.file:seek("set", 0)
    local text = channel.file:read("a") or ""
    local seq = text:match('"seq"%s*:%s*(%d+)')
    if not seq or seq == channel.seq then return nil end
    local first = channel.seq == nil
    channel.seq = seq
    if not first then return text, tonumber(seq) end
end

-- Runs every few ticks on the game thread; commands wait until the player is in gameplay.
function M.poll()
    -- In the menus too: whatever went wrong on the phone's page.
    local note = fresh(channels.note)
    local text = note and note:match('"text"%s*:%s*"([^"]*)"')
    if text then common.log("TF-PHONE", "%s", text) end

    if not player.isTracking() then return end

    -- The phone's mode before its CRTV command: the phone says VIEW before it switches the CRTV on.
    local mode = fresh(channels.mode)
    local selector = mode and mode:match('"selector"%s*:%s*"([%w_]+)"')
    local onMonitor = mode and mode:match('"onMonitor"%s*:%s*(%a+)')
    local miniGame = mode and mode:match('"miniGameShown"%s*:%s*(%a+)')
    if selector and (onMonitor == "true" or onMonitor == "false") and (miniGame == "true" or miniGame == "false") then
        crtv.setPhone(selector == "VIEW", onMonitor == "true", miniGame == "true")
        local settings = selector ~= "VIEW" and "" or string.format(" (on the monitor: %s, in the mini-game: %s)",
            onMonitor == "true" and "raised" or "not shown", miniGame == "true" and "shown" or "hidden")
        common.logChange("phone mode", "TF-CRTV", "phone switched to " .. selector .. settings)
    end

    -- active switches the CRTV on (VIEW); without it only the dial moves, also while the CRTV is down.
    local text = fresh(channels.crtv)
    if text then
        local active = text:match('"active"%s*:%s*(%a+)')
        local frequency = number(text, "frequency")
        if frequency and (active == nil or active == "true") then
            local animate = text:match('"animate"%s*:%s*(%a+)') == "true"
            crtv.apply(active == "true", frequency, animate)
            common.log("TF-CRTV", "phone command: active=%s%s frequency=%.3f", active or "as is",
                active and (animate and " (animated)" or " (silent)") or "", frequency)
        end
    end

    crtv.pump() -- carries out what the phone's mode and commands ask of the CRTV

    local align, alignSeq = fresh(channels.align)
    if align and not tooOld(alignSeq, 1) then
        local roll, pitch, dpu = number(align, "roll"), number(align, "pitch"), number(align, "dpu")
        local session, state = number(align, "session"), align:match('"state"%s*:%s*"([%a ]+)"')
        if roll and pitch and dpu and session and dpu > 0 then alignment.apply(roll, pitch, dpu, session, alignSeq, state) end
    end

    local look, lookSeq = fresh(channels.look)
    local yaw = look and not tooOld(lookSeq, LOOK_MAX_AGE) and number(look, "yaw")
    local pitch = look and number(look, "pitch")
    local on = look and look:match('"on"%s*:%s*(%a+)')
    if yaw and pitch and math.abs(yaw) <= 180 and math.abs(pitch) <= 90 and (on == "true" or on == "false") then
        looking = on == "true"
        -- Steering, the camera looks where the phone does, and the CRTV with it: up and down as the camera.
        if looking and os.clock() < steeringUntil then pitch = player.pitch() or pitch end
        native.look(looking, yaw, pitch, lookSeq)
        common.logChange("look", "TF-CRTV", on == "true" and "the CRTV looks where the phone points"
            or "the CRTV looks as the character holds it")
    end

    -- The phone sends its heading and how far up it is tilted; the player turns and looks up or down by as much as the
    -- phone did since the one before, as with the mouse (which goes on adding its own). The CRTV follows the camera.
    local steer, seq = fresh(channels.steer)
    local heading = steer and number(steer, "yaw")
    if heading and not tooOld(seq, STEER_MAX_AGE) then
        local tilt = number(steer, "pitch")
        local last = channels.steer.last
        if last and seq - last.seq <= STEER_GAP_MS then
            player.turn((heading - last.heading + 180) % 360 - 180, tilt and last.tilt and tilt - last.tilt or 0)
            common.logChange("steer", "TF-PLAYER", "turned by the phone")
            steeringUntil = os.clock() + STEER_MAX_AGE
            if looking then native.look(true, 0, player.pitch() or 0, seq) end
        end
        channels.steer.last = { heading = heading, tilt = tilt, seq = seq }
    end

    -- Every press the phone sends is logged with what became of it: the D-pad is checked in the game by its log.
    local confirm, confirmSeq = fresh(channels.confirm)
    if confirm then
        if tooOld(confirmSeq, CONFIRM_MAX_AGE) then
            common.log("TF-CRTV", "phone pressed the D-pad's centre %.1f s ago: too late, dropped", os.time() - confirmSeq / 1000)
        else
            common.log("TF-CRTV", "phone pressed the D-pad's centre: %s", crtv.pressCentre() or "no raised CRTV, ignored")
        end
    end
    local release = fresh(channels.release)
    if release then
        local released = crtv.releaseCentre()
        if released then common.log("TF-CRTV", "phone let go of the D-pad's centre: %s released", released) end
    end

    local fineTune, fineTuneSeq = fresh(channels.fineTune)
    if fineTune then
        local direction = fineTune:match('"direction"%s*:%s*"(%a+)"')
        if tooOld(fineTuneSeq, CONFIRM_MAX_AGE) then
            common.log("TF-CRTV", "phone pressed fine-tune %s %.1f s ago: too late, dropped", tostring(direction),
                os.time() - fineTuneSeq / 1000)
        else
            local pressed = crtv.pressFineTune(direction)
            if pressed then
                common.log("TF-CRTV", "phone pressed %s", pressed)
            else
                common.log("TF-CRTV", "phone pressed fine-tune %s: no raised CRTV, ignored", tostring(direction))
            end
        end
    end

    -- Applied at once: until the game's goes quiet, both play.
    local sound, soundSeq = fresh(channels.audio)
    local mute = sound and sound:match('"muteGame"%s*:%s*(%a+)')
    if (mute == "true" or mute == "false") and not tooOld(soundSeq, AUDIO_MAX_AGE) then
        audio.request(mute == "true")
        audio.update()
    end
end

return M
