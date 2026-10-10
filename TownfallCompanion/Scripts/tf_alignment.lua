-- The phone's turn moves the image in the alignment stages of the fine-tune mini-game, through the function the
-- controller's right stick reaches (tf_native.dll applies it only in those stages). The phone sends how far it leans
-- (roll) and tilts (pitch, the top towards you positive) from its pose as the alignment screen opened (alignment.js),
-- and the image goes where it points: the phone's pose and the image's position as the phone takes over are the
-- anchor, and from there every pose has its one place (dpu degrees move the image half its range, which spans -1..1).
-- Turned past an edge, the image stays at the edge and the anchor goes along, so it comes off the edge the moment the
-- phone turns back. A new neutral pose (session), another radio or a new run of the stages takes a new anchor; while
-- the phone's tilt is off the anchor stays, so the image comes back to where the phone points. The phone also says
-- whether its tilt is on, or why not; that, and why the mod can't use it, goes to the log.

local common = require("tf_common")
local player = require("tf_player")
local native = require("tf_native")
local crtv = require("tf_crtv")

local M = {}

local anchor -- {roll, pitch, x, y, session, radio}: the phone's pose and the image's position as the phone took over
local received = 0 -- the phone's alignment commands so far
local heard -- {at, state, roll, pitch}: when the phone last said how its tilt is (it does every second), and what
local SILENT_S = 3

local function imageStage()
    local game = native.miniGame()
    return game ~= nil and game.mode == 1 and (game.stage == 2 or game.stage == 3)
end

-- Holds the image where the phone points; nil, or why the mod can't.
local function move(roll, pitch, dpu, session, seq, on)
    if not native.loaded() then return "tf_native.dll isn't loaded" end
    if not player.isTracking() then return "the player isn't found" end
    local pawn = player.pawn()
    local radio = pawn:GetIsRadioInActiveMode() and pawn:GetRadio()
    if not radio or not radio:IsValid() then anchor = nil; return "the CRTV isn't raised" end
    if not imageStage() then anchor = nil; return nil end
    if not on then return nil end
    local ok, x, y = pcall(function()
        local image = radio.AdvancedTuningMotionControl_Current
        return image.X, image.Y
    end)
    if not ok or type(x) ~= "number" or type(y) ~= "number" then return "the image's position can't be read" end
    local address = radio:GetAddress()
    if not anchor or anchor.session ~= session or anchor.radio ~= address then
        anchor = {roll = roll, pitch = pitch, x = x, y = y, session = session, radio = address}
        return nil
    end
    local function follow(axis, offset)
        local target = anchor[axis] + offset
        local edge = math.max(-1, math.min(1, target))
        anchor[axis] = anchor[axis] + edge - target
        return edge
    end
    local leaned = (roll - anchor.roll + 180) % 360 - 180
    -- Tilting the top towards you raises the game's Y, as the phone's pitch rises.
    local tx, ty = follow("x", leaned / dpu), follow("y", (pitch - anchor.pitch) / dpu)
    if tx ~= x or ty ~= y then native.align(address, tx - x, ty - y, seq) end
end

function M.apply(roll, pitch, dpu, session, seq, state)
    received = received + 1
    heard = {at = os.clock(), state = state, roll = roll, pitch = pitch}
    local why = move(roll, pitch, dpu, session, seq, state == "on")
    if state then
        common.logChange("phone tilt", "TF-ALIGN", "phone tilt: " .. state .. (state == "on" and why and ", but " .. why or ""))
    end
end

-- Every half second (main.lua): a phone whose tilt was on and that went quiet (its page stopped, or its requests no
-- longer reach the companion) is logged once.
function M.watchPhone()
    if heard and heard.state == "on" and os.clock() - heard.at > SILENT_S then
        common.logChange("phone tilt", "TF-ALIGN",
            string.format("phone tilt: no word from the phone for %d s (it last said on)", SILENT_S))
    end
end

-- In the image stages, every half second (main.lua), when something changed: where the game has the image and its
-- target, how far apart, the game's tolerances, the phone's last lean and tilt, and what became of the phone's moves.
local RESULTS = { [0] = "none yet", "applied", "bad packet or move", "not in an image stage" }
function M.logImageStage()
    local game = native.miniGame()
    if not (game and game.mode == 1 and (game.stage == 2 or game.stage == 3)) or not player.isTracking() then return end
    local function try(fn)
        local ok, value = pcall(fn)
        return ok and type(value) == "number" and string.format("%.2f", value) or "?"
    end
    local radio = player.pawn():GetRadio()
    local target = crtv.tunedWaypoint()
    local done = native.alignment()
    common.logChange("image stage", "TF-ALIGN", string.format(
        "%s: image at %s, %s; target %s, %s; distance %s (tolerance %s, in range %s);"
            .. " phone lean %s, tilt %s; phone commands %d, moves applied %s, last %s",
        game.stage == 2 and "stabilise" or "align",
        try(function() return radio.AdvancedTuningMotionControl_Current.X end),
        try(function() return radio.AdvancedTuningMotionControl_Current.Y end),
        try(function() return target.AdvancedTuningMotionControl_Target.X end),
        try(function() return target.AdvancedTuningMotionControl_Target.Y end),
        try(function() return radio:GetCRTVWidget().AdvancedTuningMotionControl_DistanceToTarget end),
        try(function() return radio.AdvancedTuningMotionControl_Tolerance end),
        try(function() return radio.AdvancedTuningMotionControl_ToleranceInRange end),
        try(function() return heard.roll end), try(function() return heard.pitch end),
        received, tostring(done.calls or "?"), RESULTS[done.result] or tostring(done.result)))
end

return M
