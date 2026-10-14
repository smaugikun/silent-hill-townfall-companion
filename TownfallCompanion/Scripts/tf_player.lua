-- Finds the local player's controller and gameplay pawn (BP_Bill_C in Townfall).

local UEHelpers = require("UEHelpers")
local common = require("tf_common")
local log, logChange = common.log, common.logChange

local M = {}

local controller = CreateInvalidObject()
local pawn = CreateInvalidObject() -- gameplay pawn only; invalid in menus and while loading
local defaultPawnClass = CreateInvalidObject()
local pawnName, pawnAddress

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
    if ok and type(value) == "boolean" then return value end
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

local PITCH_LIMIT = 80 -- degrees the phone looks up or down at most; the game's camera may stop it sooner
local LOOK_SAY_S = 1    -- what became of looking up or down is logged at most this often while the phone tilts
local lookSet, lookSaidAt = nil, -math.huge -- the pitch the last look up or down set, and when it was logged
local verifyPitch

local function signed(pitch)
    return pitch and (pitch + 180) % 360 - 180 -- the game keeps 350 for 10 down
end

-- The engine's input settings, for its deprecated input scales: looked up at a level start (M.refresh), as each lookup
-- by path costs this game's UE4SS 14-21 ms.
local INPUT_SETTINGS = "/Script/Engine.Default__InputSettings"
local inputSettings
local function inputScale(axis)
    local settings = inputSettings
    local legacy = settings and settings:IsValid() and tryBool(function() return settings.bEnableLegacyInputScales end)
    if not legacy then return 1 end
    local scale = common.tryNumber(function() return controller["GetDeprecatedInput" .. axis .. "Scale"](controller) end)
    return scale and scale ~= 0 and scale or 1
end

-- Turns the player by `degrees` and looks `up` degrees further up (down if negative), as the mouse would.
function M.turn(degrees, up)
    local rotation = controller:GetControlRotation()
    local yaw = rotation.Yaw + degrees
    local before = signed(common.tryNumber(function() return rotation.Pitch end))
    if before == nil then
        before = signed(common.tryNumber(function() return pawn:GetBaseAimRotation().Pitch end))
        if before == nil then
            -- This game's UE4SS build can expose Yaw without exposing Pitch. Preserve the opaque rotator when
            -- possible; missing pitch must never disable horizontal steering. Normal pawn input adds vertical
            -- motion without needing to read or replace the camera's existing pitch.
            local turned = pcall(function() rotation.Yaw = yaw; controller:SetControlRotation(rotation) end)
            if not turned then
                turned = pcall(function() pawn:AddControllerYawInput(degrees / inputScale("Yaw")) end)
            end
            local tilted = true
            if up and up ~= 0 then
                tilted = pcall(function() pawn:AddControllerPitchInput(up / inputScale("Pitch")) end)
            end
            logChange("pitch read", "TF-PLAYER", string.format(
                "control pitch unreadable: horizontal steering %s; vertical input %s",
                turned and "applied" or "unavailable", tilted and "available" or "unavailable"))
            return turned
        end
        logChange("pitch read", "TF-PLAYER", "control pitch unreadable: using the pawn's aim pitch")
    else
        common.clearChannel("pitch read")
    end
    local looks = before and up and up ~= 0
    local pitch = looks and math.max(-PITCH_LIMIT, math.min(PITCH_LIMIT, before + up)) or before
    controller:SetControlRotation({ Pitch = pitch or 0,
                                    Yaw = yaw,
                                    Roll = common.tryNumber(function() return rotation.Roll end) or 0 })
    local now = controller:GetControlRotation()
    if angleDelta(now.Yaw, yaw) > 1 then
        logChange("turn check", "TF-PLAYER", string.format("turn by %.1f didn't take: yaw %.1f, wanted %.1f",
            degrees, now.Yaw % 360, yaw % 360))
    else
        common.clearChannel("turn check")
    end
    if looks then
        -- The game's camera may keep the pitch within limits of its own, or set it back between two turns: this says
        -- which, from what the camera had, what was set, and what it reads at once.
        if os.clock() - lookSaidAt >= LOOK_SAY_S then
            local after = signed(common.tryNumber(function() return now.Pitch end))
            log("TF-PLAYER", "looking %s by %.1f: the camera's pitch was %.1f%s, set to %.1f, reads %s",
                up > 0 and "up" or "down", math.abs(up), before,
                lookSet and string.format(" (%.1f set before)", lookSet) or "", pitch,
                after and string.format("%.1f", after) or "nothing")
            lookSaidAt = os.clock()
        end
        lookSet = pitch
        verifyPitch = pitch
    end
    return true
end

-- Read on a later game tick as well: an immediate setter readback cannot show a camera overwrite between frames.
function M.verifyTurn()
    if verifyPitch == nil then return end
    local wanted = verifyPitch
    verifyPitch = nil
    local kept = M.pitch()
    if kept == nil or angleDelta(kept, wanted) > 1 then
        logChange("later pitch", "TF-PLAYER", string.format("after the next game tick: pitch %s, previously set %.1f",
            kept and string.format("%.1f", kept) or "unreadable", wanted))
    else
        common.clearChannel("later pitch")
    end
end

-- How far up the player looks (degrees, -180..180), or nil.
function M.pitch()
    local pitch = signed(common.tryNumber(function() return controller:GetControlRotation().Pitch end))
    return pitch
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
    local address = p:GetAddress()
    local newPawn = name ~= pawnName or address ~= pawnAddress
    if newPawn then
        verifyPitch, lookSet = nil, nil
        pawnName, pawnAddress = name, address
        log("TF-PLAYER", "controller=%s", controller:GetFullName())
        log("TF-PLAYER", "pawn=%s", name)
    end
    if isMenuPawn(p) then return logChange("player", "TF-PLAYER", "menu/loading (DefaultPawn), not tracking") end

    pawn = p
    if newPawn and not (inputSettings and inputSettings:IsValid()) then
        inputSettings = StaticFindObject(INPUT_SETTINGS) or false
    end
    logChange("player", "TF-PLAYER", "tracking local pawn")
    return newPawn
end

return M
