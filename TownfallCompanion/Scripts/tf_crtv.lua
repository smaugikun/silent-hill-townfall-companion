-- Reads the CRTV, Townfall's handheld radio, through the player character
-- (ATownfallPlayerCharacter) and the game mode's UHandheldRadioManager.
-- In-game: frequency is the tuning dial position 0..1, and the
-- signal is 0 or 1 depending on whether something is tuned in.

local common = require("tf_common")
local player = require("tf_player")

-- ERadioSignalFMODType from Townfall_enums.hpp
local SIGNAL_TYPES = { [0] = "waypoint", "waypoint_tuned", "enemy", "walk_and_talk" }

local M = {}

-- The CRTV now: {active, frequency, strength, type}. Read once per telemetry sample (main.lua) and
-- handed to whatever else needs it: each read is several calls on the game thread.
function M.read()
    local pawn = player.pawn()
    local crtv = {
        active = pawn:GetIsRadioInActiveMode(),
        frequency = pawn:GetRadioCurrentTunedFrequency(),
        strength = 0,
        type = "none",
    }
    -- The radio keeps its last signal cached after it is lowered (logged: active=false signal=1.00 type=enemy).
    if crtv.active then
        local radio = pawn:GetRadio()
        local strength = radio.cachedHighestSignalStrength
        if strength > 0 then
            local signalType = radio.cachedHighestStrengthSignalType
            crtv.strength, crtv.type = strength, SIGNAL_TYPES[signalType] or tostring(signalType)
        end
    end
    return crtv
end

-- The in-game CRTV screen (UCRTVWidget) plays enemy and waypoint videos on two Bink players.
-- Returns what the screen shows as the path under Movies/CRTV_Movies without extension
-- (e.g. "Bink/Shipping/Mov_CRTV_Clinic"), and how far into it in seconds if the game says; nil if
-- it shows none. A player playing isn't enough: the waypoint's plays its video as soon as the CRTV is
-- up, dial anywhere (e.g. at 0.000, type none, Mov_CRTV_Clinic playing), and the screen shows it only
-- once the signal is tuned in. So only the player of the signal tuned in counts: the enemy's while
-- tuned to an enemy, the waypoint's once a waypoint is found.
local VIDEO_PLAYERS = { enemy = "EnemyVideoPlayer_Bink", waypoint_tuned = "WaypointVideoPlayer_Bink" }

local function playingVideo(radio, signalType)
    local widget = radio:GetCRTVWidget()
    if not VIDEO_PLAYERS[signalType] or not widget or not widget:IsValid() then return nil end
    return common.playing(widget[VIDEO_PLAYERS[signalType]], common.CRTV_VIDEO)
end

-- The fine-tune mini-game on the CRTV screen (WBP_PortableTVScreen): a box
-- (Image_DigitalNeedle, presumably) runs to and fro along a bar (Image_NarrowBand) and the player presses
-- A / F while it is over the diamond (Image_FineTuneZone). Returns the box's and the diamond's centres
-- as 0..1 of the bar, and the screen's text, or nil while it isn't shown (the phone runs the box between
-- updates from their time stamp, main.lua).
-- Positions come from each image's canvas slot (position, size, alignment) plus its render offset;
-- how the widget really moves the box is not known yet, so the raw values are logged for checking.
local function slotSpan(image)
    local slot = image.Slot
    local pos, size, align = slot:GetPosition(), slot:GetSize(), slot:GetAlignment()
    return pos.X - align.X * size.X + image.RenderTransform.Translation.X, size.X
end

-- `expected`: the CRTV is tuned to a waypoint that isn't found yet, which is when the game runs the mini-game
-- (signal type "waypoint", the phone's F key finds it). If the screen then doesn't read as showing it, the phone
-- has none to show while the monitor has: the log says which read comes back empty.
local function fineTune(radio, expected)
    local function nothing(why)
        if expected then common.logChange("fine tune read", "TF-CRTV", "mini-game expected, none sent to the phone: " .. why) end
        return nil
    end
    local widget = radio:GetCRTVWidget()
    local canvas = widget and widget:IsValid() and widget.Canvas_FineTuning
    if not canvas or not canvas:IsValid() then return nothing("the CRTV's screen widget has no fine-tune canvas") end
    if not canvas:IsVisible() then return nothing("the fine-tune canvas isn't shown") end
    local bandX, bandW = slotSpan(widget.Image_NarrowBand)
    local boxX, boxW = slotSpan(widget.Image_DigitalNeedle)
    local zoneX, zoneW = slotSpan(widget.Image_FineTuneZone)
    local ok, text = pcall(function() return common.str(widget.DialocTextBlock_FineTune:GetText()) end)
    text = ok and text or ""
    common.logChange("fine tune", "TF-CRTV", string.format("fine tune \"%s\": bar %.1f+%.1f box %.1f+%.1f diamond %.1f+%.1f",
        text, bandX, bandW, boxX, boxW, zoneX, zoneW))
    if bandW <= 0 then return nothing("the bar has no width") end
    common.clearChannel("fine tune read")
    return {
        box = (boxX + boxW / 2 - bandX) / bandW,
        zone = (zoneX + zoneW / 2 - bandX) / bandW,
        text = text,
    }
end

local function radioManager()
    return player.pawn():GetWorld().AuthorityGameMode.HandheldRadioManager
end

-- The colours of the needles the in-game CRTV's dial shows for signals (UCRTVTuningDisplayWidget:
-- enemy, waypoint, not yet discovered), as WBP_CRTV_Needles sets them, for the phone's dial: linear
-- RGB 0..1 as JSON, once read. From the widget's class default object: one lookup by path (slow
-- here, tf_cutscene.lua) at a level start, until it is loaded.
local NEEDLES = "/Game/Townfall/Characters/CharacterProps/Radio/WBP_CRTV_Needles.Default__WBP_CRTV_Needles_C"
local needleColours

function M.onLevelStart()
    if needleColours then return end
    local needles = StaticFindObject(NEEDLES)
    if not (needles and needles:IsValid()) then
        return common.logChange("needle colours", "TF-CRTV", "needle colours: WBP_CRTV_Needles not loaded yet")
    end
    local ok, json = pcall(function()
        local function rgb(c) return string.format("[%.3f,%.3f,%.3f]", c.R, c.G, c.B) end
        return string.format('{"enemy":%s,"waypoint":%s,"undiscovered":%s}', rgb(needles.EnemySignalNeedleColour),
            rgb(needles.WaypointSignalNeedleColour), rgb(needles.NotDiscoveredSignalNeedleColour))
    end)
    if ok then needleColours = json end
    common.logChange("needle colours", "TF-CRTV", "needle colours: " .. tostring(json))
end

-- The player's press in the fine-tune mini-game (A on a controller), as the phone's F key.
function M.confirmFineTune()
    player.pawn():GetRadio():PlayerInput_FineTuneConfirm_Pressed()
end

-- The CRTV is switched on and off in one of two ways. Animated, as the controller's L1 does: BP_Bill's own requests
-- play the animation, and the radio's active mode follows when it is done (the SDK dump has RequestRadioON and
-- RequestRadioOFF(Force) on BP_Bill). Silent: SetRadioInActiveMode alone flips the state, the CRTV works (voices,
-- sound, mini-game) and nothing shows on the monitor. If the requests aren't there (another build of the game)
-- the silent way is the fallback for the animated, logged once.
-- The two are not interchangeable: what the character holds up on the monitor and the radio's active mode are
-- apart. A CRTV he raised is lowered with his request, never with the silent flip (that leaves it on the
-- monitor with the radio off). One switched on silently is up as far as the radio goes, so apply() never asks him
-- to raise it: the phone puts it away and raises it again the other way (scanner.js).
-- Lowered with Force: played with the phone, the unforced request left the CRTV on the monitor until a weapon
-- was drawn, which puts it away by itself. Not confirmed in the game that Force is what fixes it: if the
-- CRTV still stays up, the log has "phone command: active=false (animated)" and no change after it.
local REQUEST_GAP_S = 1.5 -- a raise takes a moment: asking for the same again at once would only start it over
local lastRequest = { active = nil, at = -math.huge }

local function request(pawn, active, animate)
    if not animate then return pawn:SetRadioInActiveMode(active) end
    local now = os.clock()
    if lastRequest.active == active and now - lastRequest.at < REQUEST_GAP_S then return end
    lastRequest.active, lastRequest.at = active, now
    local ok, err = pcall(function()
        if active then pawn:RequestRadioON() else pawn:RequestRadioOFF(true) end
    end)
    if ok then
        common.clearChannel("radio request")
    else
        common.logChange("radio request", "TF-CRTV", "RequestRadioON/OFF failed: switching the CRTV without the animation")
        common.logChange("radio request error", "TF-CRTV", "RequestRadioON/OFF error: " .. tostring(err))
        pawn:SetRadioInActiveMode(active)
    end
end

-- The phone's CRTV command: raises (true) or lowers (false) the in-game CRTV, or leaves it as it is (nil),
-- and sets its dial, also while it is down (it comes up there), unless the command lowers it. Uses the
-- player character's and radio's own functions. A dial the game doesn't take while the CRTV is up is
-- logged: the phone's needle then goes back to the game's.
function M.apply(active, frequency, animate)
    local pawn = player.pawn()
    -- An animated lowering is asked for even if the radio already says it is down: the character can still hold
    -- the CRTV up on the monitor (the radio switched off silently under it, or a request of his own half done).
    if active ~= nil and (pawn:GetIsRadioInActiveMode() ~= active or (animate and not active)) then
        request(pawn, active, animate)
    end
    if active == false then return end
    local wanted = math.max(0, math.min(1, frequency))
    pawn:GetRadio():SetTunedFrequency(wanted)
    if not pawn:GetIsRadioInActiveMode() then return end -- lowered, the game reports its dial as 0
    local has = pawn:GetRadioCurrentTunedFrequency()
    if math.abs(has - wanted) > 0.002 then
        common.logChange("phone dial", "TF-CRTV", string.format("the game didn't take the phone's dial %.3f: it has %.3f",
            wanted, has))
    else
        common.clearChannel("phone dial")
    end
end

-- TMap:Find hands some value types back wrapped in a RemoteUnrealParam; accept both.
local function unwrap(v)
    if type(v) ~= "number" and v and v.get then return v:get() end
    return v
end

-- Per-enemy view of the CRTV `state` (M.read). Enemies carry a URadioStaticSourceComponent and the
-- radio manager keeps each one's signal strength, keyed by that component. Returns
-- function(source) -> {signal, tuned}, or nil while the CRTV is lowered, when those values are stale.
function M.sourceReader(state)
    if not (state and state.active) then return nil end
    local manager = radioManager()
    local strengths = manager.StaticSourceStrengthMap
    local tunedAddress
    if state.type == "enemy" then
        local tuned = manager:GetHighestSignalStrengthStaticSource()
        if tuned and tuned:IsValid() then tunedAddress = tuned:GetAddress() end
    end
    return function(source)
        return {
            signal = strengths:Contains(source) and unwrap(strengths:Find(source)) or 0,
            tuned = tunedAddress ~= nil and source:GetAddress() == tunedAddress,
        }
    end
end

-- Address of the waypoint component the raised CRTV `state` (M.read) is tuned to, or nil.
function M.tunedWaypointAddress(state)
    if not (state and state.active and (state.type == "waypoint" or state.type == "waypoint_tuned")) then return nil end
    local waypoint = radioManager():GetHighestSignalStrengthWaypoint()
    return waypoint and waypoint:IsValid() and waypoint:GetAddress() or nil
end

-- The telemetry "crtv" object for `state` (M.read); video/videoTime are what its screen plays and
-- fineTune its mini-game (null while lowered or not shown), needleColours the game's colours for the
-- dial's signals.
function M.json(state)
    local video, seconds, tune
    if state.active then
        local radio = player.pawn():GetRadio()
        video, seconds = playingVideo(radio, state.type)
        local expected = state.type == "waypoint"
        tune = common.optional("fine tune error", "TF-CRTV", function() return fineTune(radio, expected) end, nil)
        if not expected then common.clearChannel("fine tune read") end
    end
    return string.format('{"active":%s,"frequency":%.3f,"signalType":"%s","video":%s,"videoTime":%s,'
        .. '"fineTune":%s,"needleColours":%s}',
        tostring(state.active), state.frequency, state.type, common.jsonString(video), common.jsonNumber(seconds, "%.2f"),
        tune and string.format('{"box":%.4f,"zone":%.4f,"text":%s}', tune.box, tune.zone, common.jsonString(tune.text))
            or "null",
        needleColours or "null")
end

-- Once a second: logs what the CRTV reports and plays.
function M.update()
    if not player.isTracking() then return end
    local crtv = M.read()
    common.logChange("crtv", "TF-CRTV", string.format("active=%s frequency=%.2f signal=%.2f type=%s",
        tostring(crtv.active), crtv.frequency, crtv.strength, crtv.type))
    local video = crtv.active and playingVideo(player.pawn():GetRadio(), crtv.type)
    common.logChange("crtv video", "TF-CRTV", "screen plays " .. (video or "nothing"))
end

return M
