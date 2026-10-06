-- Commands from the phone, written by bridge.py as small JSON files in %TEMP%:
--   townfall-companion-commands.json  {"active":true,"animate":true,"frequency":0.23,"seq":...}  the CRTV, while the phone
--                                     controls it (animate: raised with the character's animation, else silently)
--   townfall-companion-steer.json     {"yaw":132.5,"seq":...}                    the phone's heading
--   townfall-companion-confirm.json   {"seq":...}                                the fine-tune press (F / A)
--   townfall-companion-audio.json     {"muteGame":true,"dialogue":true,"video":true,"seq":...} the game's CRTV
--                                     sound (and talking, and the screen's video) quiet while the phone plays it
--   townfall-companion-record.json    {"on":true,"seq":...}                      record the CRTV's screen to a file (tf_record.lua)
-- seq is the bridge's clock in ms; a changed seq means a new command.

local common = require("tf_common")
local player = require("tf_player")
local crtv = require("tf_crtv")
local audio = require("tf_audio")
local record = require("tf_record")

local STEER_MAX_AGE = 2   -- seconds; steering older than this is from a phone that stopped sending
local STEER_GAP_MS = 2000 -- after a longer pause the phone's next heading only sets a new starting point
local CONFIRM_MAX_AGE = 1 -- a late press would land at another moment of the mini-game
local AUDIO_MAX_AGE = 5   -- the phone repeats its sound request every 2 s
local RECORD_MAX_AGE = 10  -- the phone says it once, when its switch is flipped

local M = {}

local channels = {}

function M.init(tempDir)
    if not tempDir then return end
    channels.crtv = { path = tempDir .. "\\townfall-companion-commands.json" }
    channels.steer = { path = tempDir .. "\\townfall-companion-steer.json" }
    channels.confirm = { path = tempDir .. "\\townfall-companion-confirm.json" }
    channels.audio = { path = tempDir .. "\\townfall-companion-audio.json" }
    channels.record = { path = tempDir .. "\\townfall-companion-record.json" }
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
    if not player.isTracking() then return end

    -- active raises or lowers the CRTV (VIEW); without it only the dial moves, while the CRTV is up (AV OUT).
    local text = fresh(channels.crtv)
    if text then
        local active = text:match('"active"%s*:%s*(%a+)')
        local frequency = number(text, "frequency")
        if frequency and (active == nil or active == "true" or active == "false") then
            local raise = nil
            if active then raise = active == "true" end
            local animate = text:match('"animate"%s*:%s*(%a+)') == "true"
            crtv.apply(raise, frequency, animate)
            common.log("TF-CRTV", "phone command: active=%s%s frequency=%.3f", active or "as is",
                active and (animate and " (animated)" or " (silent)") or "", frequency)
        end
    end

    crtv.pump() -- after a lowering: puts the character's hands down if they stayed up

    -- The phone sends its heading; the player turns by as much as the phone did since the one before.
    -- Phone tilt remains a scanner-view effect and does not take over Townfall's vertical camera.
    local steer, seq = fresh(channels.steer)
    local heading = steer and number(steer, "yaw")
    if heading and not tooOld(seq, STEER_MAX_AGE) then
        local last = channels.steer.last
        if last and seq - last.seq <= STEER_GAP_MS then
            player.turn((heading - last.heading + 180) % 360 - 180)
            common.logChange("steer", "TF-PLAYER", "turned by the phone")
        end
        channels.steer.last = { heading = heading, seq = seq }
    end

    local confirm, confirmSeq = fresh(channels.confirm)
    if confirm and not tooOld(confirmSeq, CONFIRM_MAX_AGE) then
        crtv.confirmFineTune()
        common.log("TF-CRTV", "phone pressed F (fine-tune confirm)")
        record.event("phone pressed F")
    end

    local recording, recordSeq = fresh(channels.record)
    local on = recording and recording:match('"on"%s*:%s*(%a+)')
    if (on == "true" or on == "false") and not tooOld(recordSeq, RECORD_MAX_AGE) then record.set(on == "true") end

    -- Applied at once: the phone asks as a line starts on it, and until the game's goes quiet both talk.
    local sound, soundSeq = fresh(channels.audio)
    local mute = sound and sound:match('"muteGame"%s*:%s*(%a+)')
    if (mute == "true" or mute == "false") and not tooOld(soundSeq, AUDIO_MAX_AGE) then
        audio.request(mute == "true", sound:match('"dialogue"%s*:%s*true') ~= nil, sound:match('"video"%s*:%s*true') ~= nil)
        audio.update()
    end
end

return M
