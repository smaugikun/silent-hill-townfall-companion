-- Finds the local player's controller and gameplay pawn (BP_Bill_C in Townfall).

local UEHelpers = require("UEHelpers")
local common = require("tf_common")
local log, logChange = common.log, common.logChange

local M = {}

local controller = CreateInvalidObject()
local pawn = CreateInvalidObject() -- gameplay pawn only; invalid in menus and while loading
local defaultPawnClass = CreateInvalidObject()
local pawnName

-- Splash and main menu spawn an engine DefaultPawn at the origin; only gameplay pawns count.
local function isMenuPawn(p)
    if not defaultPawnClass:IsValid() then
        defaultPawnClass = StaticFindObject("/Script/Engine.DefaultPawn") or CreateInvalidObject()
    end
    return defaultPawnClass:IsValid() and p:IsA(defaultPawnClass)
end

local function angleDelta(a, b)
    return math.abs((a - b + 180) % 360 - 180)
end

function M.isTracking()
    return pawn:IsValid()
end

-- The gameplay pawn (an ATownfallPlayerCharacter), or an invalid object in menus and while loading.
function M.pawn()
    return pawn
end

-- ATownfallCharacter.bInInterior; the CRTV reaches less far indoors.
function M.isIndoor()
    local ok, indoor = pcall(function() return pawn.bInInterior end)
    return ok and indoor == true
end

-- Unreal location and yaw in [0, 360).
function M.read()
    -- Control yaw isn't normalized (logged both -121 and 249); actor yaw always matched it mod 360.
    return pawn:K2_GetActorLocation(), controller:GetControlRotation().Yaw % 360
end

-- How far up the camera looks, in degrees (down negative). Unreal keeps it in 0..360: 350 is 10 down.
function M.pitch()
    return (controller:GetControlRotation().Pitch + 180) % 360 - 180
end

-- Turns the player by `degrees` (clockwise) and looks up by `up` degrees (down negative; nil or 0 keeps the
-- pitch), the camera kept within PITCH_LIMIT of level. Logs when the game doesn't take the new yaw, e.g.
-- while it holds the camera (the phone's turning seemed to stop after a respawn).
local PITCH_LIMIT = 80
function M.turn(degrees, up)
    local rotation = controller:GetControlRotation()
    local yaw = rotation.Yaw + degrees
    local pitch = rotation.Pitch
    if up and up ~= 0 then
        pitch = math.max(-PITCH_LIMIT, math.min(PITCH_LIMIT, (pitch + 180) % 360 - 180 + up))
    end
    controller:SetControlRotation({ Pitch = pitch, Yaw = yaw, Roll = rotation.Roll })
    local now = controller:GetControlRotation().Yaw
    if angleDelta(now, yaw) > 1 then
        logChange("turn check", "TF-PLAYER", string.format("turn by %.1f didn't take: yaw %.1f, wanted %.1f",
            degrees, now % 360, yaw % 360))
    else
        common.clearChannel("turn check")
    end
end

-- Looks the player up again. Returns true when a new gameplay pawn was just
-- acquired, which in Townfall means a level (or save) finished loading.
function M.refresh()
    pawn = CreateInvalidObject()
    local p = controller:IsValid() and controller.Pawn
    if not (p and p:IsValid()) then
        -- No pawn yet (menu, loading) or the controller was replaced: look it up again.
        controller = UEHelpers.GetPlayerController()
        if not controller:IsValid() then return logChange("player", "TF-PLAYER", "no local PlayerController") end
        p = controller.Pawn
        if not (p and p:IsValid()) then return logChange("player", "TF-PLAYER", "PlayerController has no Pawn") end
    end

    local name = p:GetFullName()
    local newPawn = name ~= pawnName
    if newPawn then
        pawnName = name
        log("TF-PLAYER", "controller=%s", controller:GetFullName())
        log("TF-PLAYER", "pawn=%s", name)
    end
    if isMenuPawn(p) then return logChange("player", "TF-PLAYER", "menu/loading (DefaultPawn), not tracking") end

    pawn = p
    logChange("player", "TF-PLAYER", "tracking local pawn")
    return newPawn
end

return M
