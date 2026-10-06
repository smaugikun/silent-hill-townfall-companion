-- Records what the game's CRTV screen does, for a bug report. The CRTV has many mini-games and the mod can't tell how
-- any of them works without watching it: while the phone's "Record the CRTV screen" setting is on, ten times a
-- second the state of every widget on the CRTV's screen is read (visibility, opacity, transform, place and size in
-- its slot, text, tint, picture), with the screen's, the radio's and the character's numbers and flags, the CRTV's
-- state and the phone's presses, and what changed since the last time is written to a text file in the mod's
-- folder. The first sample writes everything, so the file also holds the settings that never change.
-- Off by default: it is several hundred reads of the game ten times a second. It stops by itself after MAX_S.

local common = require("tf_common")
local player = require("tf_player")
local crtv = require("tf_crtv")

local M = {}

local FILE_NAME = "townfall-ui-recording.txt"
local MAX_S = 900       -- a recording this long stops by itself
local MAX_WIDGETS = 200 -- read per sample
local MAX_SCALARS = 120

local SCALAR = { BoolProperty = true, FloatProperty = true, DoubleProperty = true, IntProperty = true,
                 Int64Property = true, ByteProperty = true, EnumProperty = true, UInt32Property = true }

local file, startedAt, lines
local previous = {}  -- what was written for each thing the last time
local layout = {}    -- the screen widget's members, found once per widget: {widgets = {{name, object}}, scalars = {name}}
local radioNames = {} -- the radio's numbers and flags, found once per radio

-- Where the file goes: the mod's folder (from where this script is), else the temp folder. TF_RECORD_DIR overrides it.
local function folder()
    local dir = os.getenv("TF_RECORD_DIR")
    if dir then return dir end
    local source = debug and debug.getinfo and debug.getinfo(1, "S").source or ""
    local scripts = source:match("^@(.*)[/\\][^/\\]*$")
    return (scripts and scripts:match("^(.*)[/\\][^/\\]*$")) or os.getenv("TEMP") or os.getenv("TMP")
end

-- {name, type} of the properties of the class and of its parents, e.g. {"Image_NarrowBand", "ObjectProperty"}.
local function properties(class)
    local list = {}
    for _ = 1, 12 do
        if not (class and class:IsValid()) then break end
        class:ForEachProperty(function(property)
            local ok, kind = pcall(function() return property:GetClass():GetFName():ToString() end)
            if not ok then ok, kind = pcall(function() return property:GetFullName():match("^(%S+)") end) end
            list[#list + 1] = { property:GetFName():ToString(), ok and kind or "?" }
        end)
        class = class:GetSuperStruct()
    end
    return list
end

local function write(text)
    lines = lines + 1
    file:write(text, "\n")
end

-- Writes "name: text" with the time if `text` differs from the last time for `name`.
local function change(name, text)
    if previous[name] == text then return end
    previous[name] = text
    write(string.format("%7.2f  %s: %s", os.clock() - startedAt, name, text))
end

local function number(v) return string.format("%.3f", v) end

-- One widget as text. Whatever it doesn't have (a slot that isn't a canvas's, no text) is left out.
local function describe(widget)
    local parts = {}
    local function add(label, read)
        local ok, value = pcall(read)
        if ok and value ~= nil then parts[#parts + 1] = label .. "=" .. tostring(value) end
    end
    add("vis", function() return widget:GetVisibility() end)
    add("opacity", function() return number(widget.RenderOpacity) end)
    add("move", function()
        local t = widget.RenderTransform
        local text = string.format("%.1f,%.1f", t.Translation.X, t.Translation.Y)
        local ok, more = pcall(function() return string.format(" scale %.2f,%.2f turn %.1f", t.Scale.X, t.Scale.Y, t.Angle) end)
        return text .. (ok and more or "")
    end)
    add("slot", function()
        local slot = widget.Slot
        local pos, size = slot:GetPosition(), slot:GetSize()
        return string.format("%.1f,%.1f size %.1fx%.1f", pos.X, pos.Y, size.X, size.Y)
    end)
    add("text", function() return string.format("%q", common.str(widget:GetText())) end)
    add("tint", function()
        local c = widget.ColorAndOpacity
        return string.format("%.2f,%.2f,%.2f,%.2f", c.R, c.G, c.B, c.A)
    end)
    add("picture", function()
        local resource = widget.Brush.ResourceObject
        return resource and resource:IsValid() and tostring(resource:GetFullName()):match("([^%s/%.:]+)%s*$") or nil
    end)
    return table.concat(parts, " ")
end

-- The members of the screen widget: its widgets (object properties that answer as widgets) and its numbers and flags.
local function members(screen)
    local id = screen:GetAddress()
    if layout.id == id then return layout end
    layout = { id = id, widgets = {}, scalars = {} }
    for _, entry in ipairs(properties(screen:GetClass())) do
        local name, kind = entry[1], entry[2]
        if SCALAR[kind] and #layout.scalars < MAX_SCALARS then
            layout.scalars[#layout.scalars + 1] = name
        elseif (kind == "?" or kind:find("Object", 1, true)) and name ~= "Slot" and name ~= "WidgetTree" then
            local object = common.member(screen, name)
            if object and pcall(function() return object:GetVisibility() end) and #layout.widgets < MAX_WIDGETS then
                layout.widgets[#layout.widgets + 1] = { name, object }
            end
        end
    end
    return layout
end

local function scalars(prefix, object, names)
    for _, name in ipairs(names) do
        local ok, value = pcall(function() return object[name] end)
        if ok and type(value) == "number" then change(prefix .. name, number(value))
        elseif ok and type(value) == "boolean" then change(prefix .. name, tostring(value)) end
    end
end

local function sample()
    local pawn = player.pawn()
    local radio = pawn:GetRadio()
    local state = crtv.read()
    change("CRTV", string.format("active=%s dial=%.3f signal=%.2f type=%s", tostring(state.active), state.frequency,
        state.strength, state.type))
    pcall(function()
        scalars("pawn.", pawn, { "IsUsingRadio", "HasRequestedRadioON", "HasRequestedRadioOFF", "WasAimingBeforeRadio" })
        local anim = pawn.Mesh:GetAnimInstance()
        scalars("anim.", anim, { "RadioAlpha", "CRTV_LocomotionAlpha" })
        local montage = anim:GetCurrentActiveMontage()
        change("montage", montage:IsValid() and tostring(montage:GetFullName()):match("([^%s/%.:]+)%s*$") or "none")
    end)
    local radioId = radio:GetAddress()
    if radioNames.id ~= radioId then
        radioNames = { id = radioId }
        for _, entry in ipairs(properties(radio:GetClass())) do
            if SCALAR[entry[2]] and #radioNames < MAX_SCALARS then radioNames[#radioNames + 1] = entry[1] end
        end
    end
    scalars("radio.", radio, radioNames)
    local screen = radio:GetCRTVWidget()
    if screen and screen:IsValid() then
        local found = members(screen)
        scalars("screen.", screen, found.scalars)
        for _, widget in ipairs(found.widgets) do change(widget[1], describe(widget[2])) end
    end
end

function M.isOn() return file ~= nil end

function M.set(on)
    if on == M.isOn() then return end
    if not on then
        common.log("TF-RECORD", "UI recording stopped: %d lines", lines)
        file:close()
        file, previous = nil, {}
        return
    end
    local dir = folder()
    local path = dir and (dir .. "\\" .. FILE_NAME)
    local handle, err = path and io.open(path, "w")
    if not handle then
        return common.logChange("record", "TF-RECORD", "can't write the UI recording " .. tostring(path) .. ": " .. tostring(err))
    end
    file, startedAt, lines, previous, layout, radioNames = handle, os.clock(), 0, {}, {}, {}
    local pawn = player.pawn()
    write("Townfall Companion UI recording " .. os.date("%Y-%m-%d %H:%M:%S") .. "; a line is a change since the last one: "
        .. "seconds  name: state. The first sample lists everything.")
    write("pawn " .. tostring(pawn:IsValid() and pawn:GetFullName() or "none"))
    common.log("TF-RECORD", "UI recording started: %s", path)
end

-- Something that isn't read off the game but happened (the phone's press), in the recording.
function M.event(text)
    if file then write(string.format("%7.2f  EVENT: %s", os.clock() - startedAt, text)) end
end

-- Ten times a second (main.lua): nothing while it is off.
function M.update()
    if not file then return end
    if not player.isTracking() then return end
    if os.clock() - startedAt > MAX_S then
        common.log("TF-RECORD", "UI recording reached %d s", MAX_S)
        return M.set(false)
    end
    sample()
    file:flush()
end

return M
