-- Reads the CRTV, Townfall's handheld radio, through the player character
-- (ATownfallPlayerCharacter) and the game mode's UHandheldRadioManager.
-- In-game: frequency is the tuning dial position 0..1, and the strength of the signal tuned in is 0 or 1.

local common = require("tf_common")
local player = require("tf_player")
local native = require("tf_native")

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

local function radioManager()
    return player.pawn():GetWorld().AuthorityGameMode.HandheldRadioManager
end

-- The colours of the needles the in-game CRTV's dial shows for signals (UCRTVTuningDisplayWidget:
-- enemy, waypoint, not yet discovered), as WBP_CRTV_Needles sets them, for the phone's dial: linear
-- RGB 0..1 as JSON, once read. From the widget's class default object: one lookup by path (slow in this
-- UE4SS build) at a level start, until it is loaded.
local NEEDLES = "/Game/Townfall/Characters/CharacterProps/Radio/WBP_CRTV_Needles.Default__WBP_CRTV_Needles_C"
local needleColours
local hidden -- what M.hideInMiniGame hid, {prop, was, hands, handsWere}: and whether each was hidden before
local wish, own -- the character's pending raise or lowering, and the CRTV the phone switched on: see M.pump

function M.onLevelStart()
    hidden, own, wish = nil, nil, nil -- freed with the level; a new character's clock starts over
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

-- The player's raised radio, or nil.
local function raisedRadio()
    if not player.isTracking() then return nil end
    local pawn = player.pawn()
    if not pawn:GetIsRadioInActiveMode() then return nil end
    local radio = pawn:GetRadio()
    return radio and radio:IsValid() and radio or nil
end

-- The phone's D-pad centre, as the controller's button. In the advanced mini-game's image stages and after (stage 2
-- on, tf_native.miniGame: the image locked in is stored with it), it is the store button, held until the phone lets
-- go (releaseCentre); otherwise the fine-tune confirm. Returns what it pressed, or nil without a raised radio.
local storing = false
function M.pressCentre()
    local radio = raisedRadio()
    if not radio then return nil end
    local game = native.miniGame()
    if game and game.mode == 1 and (game.stage or 0) >= 2 then
        radio:PlayerInput_AdvancedTuningStoreSignal_Pressed()
        storing = true
        return "store signal"
    end
    radio:PlayerInput_FineTuneConfirm_Pressed()
    return "confirm"
end

-- The centre let go: ends a store press. Returns "store signal" if there was one.
function M.releaseCentre()
    if not storing then return nil end
    storing = false
    local radio = raisedRadio()
    if radio then radio:PlayerInput_AdvancedTuningStoreSignal_Released() end
    return "store signal"
end

-- The phone's D-pad arrows, as on a controller: the fine-tune mini-game's arrows while it runs; otherwise left and
-- right jump to the next frequency down or up (the radio's quick tune). Returns what it pressed, or nil without a
-- raised radio.
local FINE_TUNE_INPUTS = {
    up = "PlayerInput_FineTuneUp_Pressed",
    down = "PlayerInput_FineTuneDown_Pressed",
    left = "PlayerInput_FineTuneLeft_Pressed",
    right = "PlayerInput_FineTuneRight_Pressed",
}
local QUICK_TUNE_INPUTS = { left = "PlayerInput_Radio_QuickTuneLeft_Pressed", right = "PlayerInput_Radio_QuickTuneRight_Pressed" }

function M.pressFineTune(direction)
    local input = FINE_TUNE_INPUTS[direction]
    local radio = input and raisedRadio()
    if not radio then return nil end
    if not native.miniGame() and QUICK_TUNE_INPUTS[direction] then
        radio[QUICK_TUNE_INPUTS[direction]](radio)
        return "quick tune " .. direction
    end
    radio[input](radio)
    return "fine-tune " .. direction
end

-- The CRTV is switched on and off in one of two ways. Animated, as the radio button on the keyboard or controller
-- does: BP_Bill's own requests (RequestRadioON, RequestRadioOFF(Force)) play the animation. Silent:
-- SetRadioInActiveMode alone flips the state, the CRTV works (voices, sound, mini-game) and nothing shows on the
-- monitor. If the requests aren't there (another build of the game) the silent way is the fallback for the animated,
-- logged once.
-- The two are not interchangeable: what the character holds up on the monitor and the radio's active mode are
-- apart. A CRTV he raised is lowered with his request, never with the silent flip (that leaves it on the monitor
-- with the radio off); one switched on silently is put away with the silent flip.
--
-- What the game does with an animated request (watched in UE4SS.log, with BP_Bill_C and its animation):
--   * IsUsingRadio turns true at once on RequestRadioON; the animation's RadioAlpha (the hands, and the radio drawn
--     with the radio montage) ramps from 0 to 1 in about a second; the radio's active mode turns true only then.
--   * RequestRadioOFF asked once the radio was fully up was carried out every time, in about half a second. Asked
--     while it was still coming up, or RequestRadioON asked while it was going down, the request was dropped or
--     half done: the radio gone and the hands lagging, or the radio staying up. Forced, the radio goes at once
--     and the hands still ramp down: it is the last resort.
-- So a wish (raised or lowered) is kept, and carried out when the character is ready for it: a lowering waits for
-- the raise to finish, a raise for the lowering to finish. The latest wish wins. Its times are on the world's
-- clock, which stands still in the pause menu: a paused game carries nothing out, and nothing is asked again or
-- given up meanwhile.
local REQUEST_GAP_S = 1.5 -- a request takes a moment to show: asking for the same again at once would only start it over
local FORCE_AFTER_S = 3   -- a plain lowering still not carried out by then is asked again, forced
local WISH_TTL_S = 12     -- a wish not carried out by then is given up (the phone says a raise again while it wants it)
local HANDS_DOWN = 0.05   -- RadioAlpha at or below this: the hands are down
-- wish (declared at the top): {up, at: when it was last wished, askedAt, firstAskedAt}.

-- What the phone switched on is the phone's to put away, and nothing else. own (declared at the top): how the mod
-- switched it on for the phone, {way = "silent" | "animated", up = seen up since}, from the moment it asked until
-- the CRTV is down again, however it came down (the radio button, the game). It is put away when the phone is out
-- of VIEW, and when the phone wants it the other way (the phone then switches it on again that way), but not in the
-- middle of a mini-game.
-- phone: the phone's mode as it last said it (tf_commands.lua), {view, onMonitor, miniGameShown, at}; nil until it
-- says. The phone says it every couple of seconds: one that stopped (its page closed or asleep, the Wi-Fi gone) is out
-- of VIEW, and the game goes back to how it is without the phone.
local PHONE_QUIET_S = 5
local phone = nil

-- true: the phone is in VIEW; false: it isn't, or went quiet; nil: it hasn't said yet.
local function phoneView()
    if not phone then return nil end
    return phone.view and os.clock() - phone.at <= PHONE_QUIET_S
end

-- The world's clock (s): it stands still in the pause menu.
local function worldClock(pawn)
    return common.tryNumber(function() return pawn:GetGameTimeSinceCreation() end) or os.clock()
end

-- The character's state around the radio, nil if this build of the game doesn't have it.
local function readState(pawn)
    local ok, state = pcall(function()
        local using = pawn.IsUsingRadio
        if type(using) ~= "boolean" then return nil end
        local alpha = pawn.Mesh:GetAnimInstance().RadioAlpha
        return { flag = pawn:GetIsRadioInActiveMode(), using = using,
                 alpha = type(alpha) == "number" and alpha or (using and 1 or 0) }
    end)
    return ok and state or nil
end

-- One animated request; if the requests aren't there, the silent switch instead (logged once).
local function request(pawn, active, force)
    common.log("TF-CRTV", "asking the character to %s the CRTV%s", active and "raise" or "lower", force and " (forced)" or "")
    local ok, err = pcall(function()
        if active then pawn:RequestRadioON() else pawn:RequestRadioOFF(force == true) end
    end)
    if ok then
        common.clearChannel("radio request")
    else
        common.logChange("radio request", "TF-CRTV", "RequestRadioON/OFF failed: switching the CRTV without the animation")
        common.logChange("radio request error", "TF-CRTV", "RequestRadioON/OFF error: " .. tostring(err))
        pawn:SetRadioInActiveMode(active)
    end
end

local function ask(pawn, now)
    if wish.askedAt and now - wish.askedAt < REQUEST_GAP_S then return end
    wish.firstAskedAt = wish.firstAskedAt or now
    wish.askedAt = now
    if wish.up then own = { way = "animated", up = false } end
    request(pawn, wish.up, not wish.up and now - wish.firstAskedAt >= FORCE_AFTER_S)
end

local function carryOut(pawn, state, now)
    local down = not state.flag and not state.using and state.alpha <= HANDS_DOWN
    if wish.up then
        if state.flag then
            wish = nil                      -- up
        elseif down then
            ask(pawn, now)                  -- nothing coming up and nothing going down: raise it
        end                                 -- else it is coming up, or still going down: wait
    elseif down then
        wish = nil                          -- down
    elseif state.flag and state.using then
        ask(pawn, now)                      -- fully up: the request that always worked
    elseif state.flag and state.alpha <= HANDS_DOWN then
        pawn:SetRadioInActiveMode(false)    -- on silently, nothing held up: the silent switch-off
        wish = nil
    end                                     -- else it is coming up, or already going down: wait
end

-- Puts the phone's CRTV away the way the mod switched it on: silently at once, or with the character's animation
-- (a wish, carried out by M.pump).
local function putAway(pawn, now, why)
    common.log("TF-CRTV", "putting the phone's CRTV away: %s", why)
    if own.way == "silent" then
        own, wish = nil, nil
        pawn:SetRadioInActiveMode(false)
    else
        wish = { up = false, at = now }
    end
end

-- What the phone's mode asks of the CRTV the phone switched on.
local function keep(pawn, now)
    local view = phoneView()
    if view == false and wish and wish.up then wish = nil end -- a raise for a phone that left VIEW
    if not own then return end
    local up = pawn:GetIsRadioInActiveMode()
    if up then own.up = true end
    local lowering = wish ~= nil and not wish.up
    local wanted = view and (phone.onMonitor and "animated" or "silent") or nil
    if view == nil or own.way == wanted then
        -- Back in VIEW before it was put away: the phone's again, and still coming up if it was.
        if lowering then wish = not own.up and { up = true, at = now, askedAt = now } or nil end
    elseif not lowering and not (view and native.miniGame()) then
        putAway(pawn, now, view and "the phone wants it shown the other way"
            or phone.view and "the phone went quiet" or "the phone is in AV OUT")
    end
    if own and not up and (own.up or not wish) then
        own = nil                           -- down again, or it never came up: no longer the phone's
        if wish and not wish.up then wish = nil end
    end
end

-- Every few ticks, with the phone's commands (tf_commands.lua); it must not throw into them.
function M.pump()
    if not (wish or own) then return end
    local ok, err = pcall(function()
        local pawn = player.pawn()
        local now = worldClock(pawn)
        keep(pawn, now)
        if not wish then return end
        if now - wish.at > WISH_TTL_S then
            if not wish.up then own = nil end -- left to the game, as if the player had raised it
            wish = nil
            return common.logChange("radio wish", "TF-CRTV", "the character didn't carry out the phone's request: dropped")
        end
        local state = readState(pawn)
        if state then return carryOut(pawn, state, now) end
        -- This build gives no state to wait for: asked until the radio is as wished.
        if pawn:GetIsRadioInActiveMode() == wish.up then wish = nil else ask(pawn, now) end
    end)
    if not ok then common.logChange("radio wish error", "TF-CRTV", "carrying out the phone's request: " .. tostring(err)) end
end

-- The phone's mode, said every couple of seconds (tf_commands.lua): whether it is in VIEW, and whether in VIEW the
-- monitor shows the CRTV ("In VIEW, show the game's CRTV on the monitor"), and in the mini-game ("... in the
-- mini-game"). The monitor follows at once.
function M.setPhone(view, onMonitor, miniGameShown)
    phone = { view = view, onMonitor = onMonitor, miniGameShown = miniGameShown, at = os.clock() }
    M.hideInMiniGame()
end

-- The phone's CRTV command: switches the in-game CRTV on (active), with the character's animation or silently, or
-- leaves it as it is (nil); and sets its dial, also while it is down (it comes up there). Only a phone in VIEW
-- switches it on, and it says VIEW first: a raise from one that said it isn't is late, and left out. Uses the
-- player character's and radio's own functions. A dial the game doesn't take while the CRTV is up is logged: the
-- phone's needle then goes back to the game's.
function M.apply(active, frequency, animate)
    local pawn = player.pawn()
    if active and phoneView() == false then
        common.log("TF-CRTV", "the phone isn't in VIEW: its CRTV stays as it is")
    elseif active and not animate then
        wish = nil
        if not pawn:GetIsRadioInActiveMode() then
            pawn:SetRadioInActiveMode(true)
            own = { way = "silent", up = false }
        end
    elseif active and wish and wish.up then
        wish.at = worldClock(pawn)          -- the phone says it again: the ask stays as it was
    elseif active then
        wish = { up = true, at = worldClock(pawn) }
        M.pump()
    end
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
    local waypoint = M.tunedWaypoint()
    return waypoint and waypoint:GetAddress() or nil
end

-- The waypoint signal the radio is tuned to most strongly (a URadioWaypointSourceComponent), or nil.
function M.tunedWaypoint()
    local waypoint = radioManager():GetHighestSignalStrengthWaypoint()
    return waypoint and waypoint:IsValid() and waypoint or nil
end

-- The telemetry "crtv" object for `state` (M.read), with the game's colours for the dial's signals and the mini-game
-- while it runs (tf_native.miniGame: {mode, stage}). While the CRTV is up the phone shows the game's own screen, so
-- nothing of the screen itself is read here.
function M.json(state)
    local game = native.miniGame()
    return string.format('{"active":%s,"frequency":%.3f,"signalType":"%s","needleColours":%s,"miniGame":%s}',
        tostring(state.active), state.frequency, state.type, needleColours or "null",
        game and string.format('{"mode":%d,"stage":%d}', game.mode or -1, game.stage or -1) or "null")
end

-- The advanced mini-game raises the CRTV in both hands, however it was switched on. In VIEW, unless the phone wants
-- it shown ("In VIEW, show the game's CRTV in the mini-game"), the CRTV, the prop the character holds (BP_Radio_Prop,
-- the radio's SpawnedRadio: its meshes and lights), and the hands holding it, the character's first-person body
-- (BP_Bill's Mesh1P; the third-person body is never seen from his eyes), are hidden while it runs; after, or once the
-- phone leaves VIEW, goes quiet or wants them shown, each is as hidden as it was before. Every few ticks (main.lua),
-- and as soon as the phone says its mode.
function M.hideInMiniGame()
    local hide = phoneView() == true and not phone.miniGameShown and native.miniGame() ~= nil
    if hidden and not hide then
        if hidden.prop:IsValid() then hidden.prop:SetActorHiddenInGame(hidden.was) end
        if hidden.hands and hidden.hands:IsValid() then hidden.hands:SetHiddenInGame(hidden.handsWere, false) end
        hidden = nil
        common.log("TF-CRTV", "the CRTV is back as it was")
    end
    if hide and not hidden then
        local radio = raisedRadio()
        local prop = radio and common.member(radio, "SpawnedRadio")
        if prop then
            local hands = common.member(player.pawn(), "Mesh1P")
            hidden = { prop = prop, was = prop.bHidden == true, hands = hands,
                       handsWere = hands and hands.bHiddenInGame == true }
            prop:SetActorHiddenInGame(true)
            if hands then hands:SetHiddenInGame(true, false) end
            common.log("TF-CRTV", "the CRTV %s hidden for the mini-game (the phone's setting)",
                hands and "and the hands holding it are" or "is (no hands found to hide)")
        end
    end
end

-- Once a second: logs what the CRTV reports.
function M.update()
    if not player.isTracking() then return end
    local crtv = M.read()
    common.logChange("crtv", "TF-CRTV", string.format("active=%s frequency=%.2f signal=%.2f type=%s",
        tostring(crtv.active), crtv.frequency, crtv.strength, crtv.type))
end

return M
