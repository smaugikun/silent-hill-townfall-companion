-- Diagnostics: what the game keeps behind the CRTV's screen. Starting from the objects the mod can reach (the CRTV's
-- screen widget, the radio, the radio's actor) it looks through the objects they hold, a few steps deep and only
-- those that look like part of the CRTV's UI, for one that has a given property, and lists every class it meets
-- with its properties (a flag or number with its value now) in the log, so that what the mini-game's screen is
-- made of can be read from UE4SS.log. Nothing here changes the game; where the UE4SS build can't list properties
-- it finds nothing and says so once.

local common = require("tf_common")

local M = {}

local MAX_OBJECTS = 120 -- looked at per search
local MAX_DEPTH = 3     -- steps from where it starts
local MAX_NAMES = 100   -- properties listed per class
local MAX_LISTINGS = 2  -- a class is listed this often over the game: twice, so that what moves shows
local listed = {}       -- class name -> times listed

local SCALAR = { BoolProperty = true, FloatProperty = true, DoubleProperty = true, IntProperty = true,
                 Int64Property = true, ByteProperty = true, EnumProperty = true, UInt32Property = true }

-- What looks like part of the CRTV's UI, by the name of its class or of the property that holds it.
local function crtvLike(name)
    name = name:lower()
    for _, word in ipairs({ "widget", "wbp_", "canvas", "image", "text", "panel", "overlay", "box", "radio", "crtv", "tv",
                            "screen", "tun", "fine", "dialoc", "needle", "zone", "band", "mini", "bink", "media" }) do
        if name:find(word, 1, true) then return true end
    end
    return false
end

-- {name, type} of the properties of the class and of its parents.
local function properties(class)
    local list = {}
    for _ = 1, 12 do
        if not (class and class:IsValid()) then break end
        class:ForEachProperty(function(property)
            -- Its type, e.g. "ObjectProperty": from its class, or from the start of its full name.
            local ok, kind = pcall(function() return property:GetClass():GetFName():ToString() end)
            if not ok then
                ok, kind = pcall(function() return property:GetFullName():match("^(%S+)") end)
            end
            list[#list + 1] = { property:GetFName():ToString(), ok and kind or "?" }
        end)
        class = class:GetSuperStruct()
    end
    return list
end

local function describe(object, list)
    local parts = {}
    for index, entry in ipairs(list) do
        if index > MAX_NAMES then parts[#parts + 1] = "..."; break end
        local name, kind = entry[1], entry[2]
        local text = name .. ":" .. kind:gsub("Property$", "")
        if SCALAR[kind] then
            local ok, value = pcall(function() return object[name] end)
            if ok and (type(value) == "number" or type(value) == "boolean") then
                text = text .. "=" .. (type(value) == "number" and string.format("%.3g", value) or tostring(value))
            end
        end
        parts[#parts + 1] = text
    end
    return table.concat(parts, ", ")
end

-- `roots`: {{name, object}, ...}. The first object that has `property` (an object), or nil. `tag` names the
-- search in the log.
function M.explore(roots, property, tag)
    local seen, queue, head, found = {}, {}, 1, nil
    local function push(object, depth, path)
        local ok, address = pcall(function() return object:GetAddress() end)
        if ok and address and not seen[address] then
            seen[address] = true
            queue[#queue + 1] = { object = object, depth = depth, path = path }
        end
    end
    for _, root in ipairs(roots) do
        if root[2] then push(root[2], 0, root[1]) end
    end
    local failure
    while head <= #queue and head <= MAX_OBJECTS do
        local item = queue[head]
        head = head + 1
        local ok, err = pcall(function()
            local object = item.object
            if not object:IsValid() then return end
            local class = object:GetClass()
            local className = class:GetFName():ToString()
            local list = properties(class)
            if not found and common.member(object, property) then found = object end
            if (listed[className] or 0) < MAX_LISTINGS then
                listed[className] = (listed[className] or 0) + 1
                common.log("TF-PROBE", "%s: %s (%s): %s", tag, className, item.path, describe(object, list))
            end
            if item.depth < MAX_DEPTH then
                for _, entry in ipairs(list) do
                    if (entry[2] == "?" or entry[2]:find("Object", 1, true)) and crtvLike(entry[1]) then
                        local child = common.member(object, entry[1])
                        if child then push(child, item.depth + 1, item.path .. "." .. entry[1]) end
                    end
                end
            end
        end)
        failure = failure or (not ok and err or nil)
    end
    if failure then common.logChange("probe", "TF-PROBE", tag .. ": couldn't look through some objects: " .. tostring(failure)) end
    return found
end

return M
