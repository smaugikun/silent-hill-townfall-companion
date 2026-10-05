-- Diagnostics: what the game keeps about the CRTV on the character and on his animation, so that what raises
-- his hands (and keeps them up when the radio goes) can be named from UE4SS.log. Once per pawn it lists the
-- functions and properties whose names have "radio", "crtv" or "handheld" in them, on his class and on the
-- animation instance of his mesh; then, once a second, it logs a number or flag among them when it changes.
-- Nothing here changes the game, and it stays silent where the UE4SS build can't list members.

local common = require("tf_common")
local player = require("tf_player")

local M = {}

local MAX_WATCHED = 40
local probedPawn
local targets = {} -- {label, object, names}: what is watched

local function interesting(name)
    local lower = name:lower()
    return lower:find("radio", 1, true) or lower:find("crtv", 1, true) or lower:find("handheld", 1, true)
end

-- The class and its parents, up to (not far above) the character class.
local function members(object)
    local functions, properties = {}, {}
    local class = object:GetClass()
    for _ = 1, 12 do
        if not (class and class:IsValid()) then break end
        class:ForEachFunction(function(f)
            local name = f:GetFName():ToString()
            if interesting(name) then functions[#functions + 1] = name end
        end)
        class:ForEachProperty(function(p)
            local name = p:GetFName():ToString()
            if interesting(name) then properties[#properties + 1] = name end
        end)
        class = class:GetSuperStruct()
    end
    table.sort(functions)
    table.sort(properties)
    return functions, properties
end

local function probe(pawn)
    targets = {}
    local anim = pawn.Mesh:GetAnimInstance()
    for _, target in ipairs({ { "pawn", pawn }, { "anim", anim } }) do
        local label, object = target[1], target[2]
        local functions, properties = members(object)
        common.log("TF-PROBE", "%s %s: functions %s", label, object:GetClass():GetFName():ToString(),
            #functions > 0 and table.concat(functions, ", ") or "none")
        common.log("TF-PROBE", "%s properties %s", label, #properties > 0 and table.concat(properties, ", ") or "none")
        targets[#targets + 1] = { label = label, object = object, names = properties }
    end
end

-- Once a second, while a phone has the page open (main.lua).
function M.update()
    if not player.isTracking() then return end
    local pawn = player.pawn()
    local id = pawn:GetFullName()
    if id ~= probedPawn then
        probedPawn = id
        local ok, err = pcall(probe, pawn)
        if not ok then common.logChange("probe", "TF-PROBE", "can't list the character's members: " .. tostring(err)) end
    end
    local watching = 0
    for _, target in ipairs(targets) do
        for _, name in ipairs(target.names) do
            watching = watching + 1
            if watching > MAX_WATCHED then return end
            local ok, value = pcall(function() return target.object[name] end)
            if ok and (type(value) == "boolean" or type(value) == "number") then
                common.logChange("probe " .. target.label .. "." .. name, "TF-PROBE",
                    string.format("%s.%s = %s", target.label, name, tostring(value)))
            end
        end
    end
end

return M
