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

local function tryBool(fn)
    local ok, value = pcall(fn)
    return ok and type(value) == "boolean" and value or nil
end

-- Whether the gameplay pawn is alive. Townfall keeps its pawn around during death and retry, so a valid pawn alone
-- isn't enough: the player Blueprint's IsDead says it, else the character's Health (ATownfallCharacter).
function M.isAlive()
    if not pawn:IsValid() then return false end
    local dead = tryBool(function() return pawn:IsDead() end)
    if dead ~= nil then return not dead end
    local health = common.tryNumber(function() return pawn.Health end)
    return health == nil or health > 0
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

-- Turns the player by `degrees` (clockwise). Steering deliberately controls yaw only; forcing vertical
-- camera input fights Townfall's own camera behaviour and feels like a free camera.
local PITCH_LIMIT = 80 -- degrees the phone looks up or down at most; the game's camera may stop it sooner

-- Turns the player by `degrees` and looks `up` degrees further up (down if negative), as the mouse would.
function M.turn(degrees, up)
    local rotation = controller:GetControlRotation()
    local yaw = rotation.Yaw + degrees
    local pitch = common.tryNumber(function() return rotation.Pitch end)
    if pitch and up and up ~= 0 then
        pitch = math.max(-PITCH_LIMIT, math.min(PITCH_LIMIT, (pitch + 180) % 360 - 180 + up))
    end
    controller:SetControlRotation({ Pitch = pitch or 0,
                                    Yaw = yaw,
                                    Roll = common.tryNumber(function() return rotation.Roll end) or 0 })
    local now = controller:GetControlRotation().Yaw
    if angleDelta(now, yaw) > 1 then
        logChange("turn check", "TF-PLAYER", string.format("turn by %.1f didn't take: yaw %.1f, wanted %.1f",
            degrees, now % 360, yaw % 360))
    else
        common.clearChannel("turn check")
    end
end

-- How far up the player looks (degrees, -180..180: the game keeps 350 for 10 down), or nil.
function M.pitch()
    local value = common.tryNumber(function() return controller:GetControlRotation().Pitch end)
    return value and (value + 180) % 360 - 180
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
