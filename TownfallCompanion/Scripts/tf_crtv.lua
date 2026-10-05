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

-- An object's property that may not exist on it (UE4SS answers nil or throws, by build): the object it holds, or nil.
local function member(object, name)
    local ok, value = pcall(function() return object[name] end)
    local kind = type(value)
    if ok and (kind == "userdata" or kind == "table") and value.IsValid and value:IsValid() then return value end
end

-- The mini-game is not on the widget that plays the CRTV's videos (UE4SS.log 2026-10-06: that widget has no
-- Canvas_FineTuning, also with the CRTV raised by the controller and the mini-game on the monitor). It is on
-- another user widget, found by the canvas it has. Looking at all user widgets walks every object (~20 ms), so
-- not more often than SCAN_GAP_S, and only while the game should be running the mini-game; the widget found is
-- kept while it lives and shows its canvas.
local SCAN_GAP_S = 3
local tuneWidget, lastScan = nil, -math.huge

local function showsMiniGame(widget)
    local canvas = widget and widget:IsValid() and member(widget, "Canvas_FineTuning")
    return canvas and canvas:IsVisible()
end

local function scanForMiniGame()
    local hidden, count, like = nil, 0, {}
    for _, widget in ipairs(FindAllOf("UserWidget") or {}) do
        count = count + 1
        if widget:IsValid() then
            local name = widget:GetFullName() or ""
            if not name:find("Default__", 1, true) then
                local canvas = member(widget, "Canvas_FineTuning")
                if canvas and canvas:IsVisible() then return widget, count end
                hidden = hidden or (canvas and widget)
                local lower = name:lower()
                if #like < 8 and (lower:find("crtv", 1, true) or lower:find("tv", 1, true) or lower:find("tun", 1, true)) then
                    like[#like + 1] = name
                end
            end
        end
    end
    return hidden, count, like
end

local function findMiniGame()
    if showsMiniGame(tuneWidget) then return tuneWidget end
    if os.clock() - lastScan < SCAN_GAP_S then return nil end
    lastScan = os.clock()
    local widget, count, like = scanForMiniGame()
    tuneWidget = widget
    if widget then
        common.logChange("fine tune widget", "TF-CRTV", "mini-game widget: " .. tostring(widget:GetFullName()))
    else
        common.logChange("fine tune widget", "TF-CRTV", string.format(
            "no user widget of %d has a fine-tune canvas; named like the CRTV's screen: %s", count,
            #like > 0 and table.concat(like, ", ") or "none"))
    end
    return showsMiniGame(widget) and widget or nil
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
    local canvas = widget and widget:IsValid() and member(widget, "Canvas_FineTuning")
    if expected and not (canvas and canvas:IsVisible()) then
        local found = findMiniGame()
        if found then widget, canvas = found, member(found, "Canvas_FineTuning") end
    end
    if not canvas then return nothing("no widget has a fine-tune canvas (the CRTV's screen widget has none)") end
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
-- Lowering is asked for plainly first, as the controller does it: radio and hands go down together. Played with
-- the phone, that was often not carried out (the CRTV stayed on the monitor until a weapon was drawn), so the
-- ask that follows is forced. Forced, the radio goes at once but the character's hands stay up (UE4SS.log
-- 2026-10-06: "the radio disappears, the hand stays"), so it is the last resort, and the hands are then put down
-- by clearHand().
local REQUEST_GAP_S = 1.5 -- a raise takes a moment: asking for the same again at once would only start it over
local LOWER_ASKS_S = 6    -- lowerings this close together are one try at lowering: the first plain, the rest forced
local lastRequest = { active = nil, at = -math.huge }
local lowering = { asks = 0, at = -math.huge }
local handWatch = nil     -- os.clock() of the last lowering, until the hands have been looked at

local function request(pawn, active, animate)
    if not animate then return pawn:SetRadioInActiveMode(active) end
    local now = os.clock()
    if lastRequest.active == active and now - lastRequest.at < REQUEST_GAP_S then return end
    lastRequest.active, lastRequest.at = active, now
    if not active then
        if now - lowering.at > LOWER_ASKS_S then lowering.asks = 0 end
        lowering.asks, lowering.at = lowering.asks + 1, now
        handWatch = now
    end
    local ok, err = pcall(function()
        if active then pawn:RequestRadioON() else pawn:RequestRadioOFF(lowering.asks > 1) end
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

-- The character's hands while he holds the CRTV are, presumably, an animation (montage) on his mesh. Once the
-- game reports the radio down after the phone lowered it, the hands are looked at: a radio or CRTV montage still
-- playing is stopped, so the hands go down with the radio. A montage of another name is not the mod's to stop; its
-- name is logged, and with none playing the log says so, so what keeps the hands up can be named next.
local HAND_WATCH_S = 8   -- how long after a lowering the hands are looked at
local HAND_SETTLE_S = 0.5 -- the lowering's own end, so a montage that is just finishing isn't cut

local function activeMontage(pawn)
    local anim = pawn.Mesh:GetAnimInstance()
    local montage = anim:GetCurrentActiveMontage()
    if montage and montage:IsValid() then return anim, montage, tostring(montage:GetFullName()) end
    return anim
end

local function radioMontage(name)
    name = name:lower()
    return name:find("radio", 1, true) or name:find("crtv", 1, true)
end

-- Called every few ticks with the phone's commands (tf_commands.lua); it must not throw into them.
function M.pump()
    if not handWatch then return end
    local now = os.clock()
    if now - handWatch > HAND_WATCH_S then handWatch = nil return end
    local pawn = player.pawn()
    if now - handWatch < HAND_SETTLE_S or pawn:GetIsRadioInActiveMode() then return end
    handWatch = nil
    local ok, err = pcall(function()
        local anim, montage, name = activeMontage(pawn)
        if not montage then
            common.logChange("hands", "TF-CRTV", "CRTV lowered: no montage on the character, his hands aren't an animation the mod can stop")
        elseif radioMontage(name) then
            anim:Montage_Stop(0.2, montage)
            common.logChange("hands", "TF-CRTV", "CRTV lowered with its montage still playing: stopped " .. name)
        else
            common.logChange("hands", "TF-CRTV", "CRTV lowered, montage playing: " .. name .. " (not the radio's, left alone)")
        end
    end)
    if not ok then common.logChange("hands error", "TF-CRTV", "hands: " .. tostring(err)) end
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
    -- What plays on the character, beside the CRTV's state: tells what his hands do when the radio goes.
    common.optional("montage read", "TF-CRTV", function()
        local _, montage, name = activeMontage(player.pawn())
        common.logChange("montage", "TF-CRTV", "character montage: " .. (montage and name or "none"))
    end)
end

return M
