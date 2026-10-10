-- A fake UE4SS runtime and Townfall world for running the mod outside the game.
-- Only the parts of the UE4SS Lua API the mod uses are faked, with the shapes the
-- real game showed. Returns a constructor used by test_mod.py.

return function(scriptsDir, tempDir, options)
    local world = { logs = {}, findAllOfCalls = 0, staticFindCalls = 0 } -- findAllOfCalls counts enemy walks
    local loops, enemies, waypoints = {}, {}, {}
    local notify = {} -- class path -> NotifyOnNewObject callback
    local ENEMY_CLASS = "/Script/Townfall.TownfallEnemyCharacter"
    local WAYPOINT_CLASS = "/Script/Townfall.RadioWaypointSourceComponent"
    local controller
    local serial = 0
    local function nextId()
        serial = serial + 1
        return serial
    end

    print = function(s) world.logs[#world.logs + 1] = (s:gsub("\n$", "")) end
    os.getenv = function(k)
        if k == "TEMP" and not options.noTemp then return tempDir end
    end
    -- The mod keeps the command files, the DLL's packets and its heartbeat open (tf_commands.lua, tf_native.lua,
    -- main.lua); tests close them before removing the folder.
    local opened, realOpen = {}, io.open
    world.telemetryWrites = 0 -- how often the telemetry file was written (the mod keeps it open)
    io.open = function(path, mode)
        path = path:gsub("\\", "/") -- the mod joins %TEMP% paths with "\"; elsewhere than Windows that must still open
        local f, err = realOpen(path, mode)
        if f and (mode == "r" or mode == "w+b") then opened[#opened + 1] = f end
        if f and path:find("telemetry.json", 1, true) then
            return setmetatable({ write = function(_, ...)
                world.telemetryWrites = world.telemetryWrites + 1
                return f:write(...)
            end }, { __index = function(_, name) return function(_, ...) return f[name](f, ...) end end }), err
        end
        return f, err
    end
    function world.closeFiles()
        for _, f in ipairs(opened) do pcall(f.close, f) end
        opened = {}
    end
    -- Game time, in seconds: world.tick moves it on. Nothing the mod does takes any. The world's own
    -- clock stands still while world.paused (the pause menu); os.clock runs on.
    local clock = 0
    os.clock = function() return clock end
    local worldTime = 0
    world.paused = false
    package.path = scriptsDir .. "/?.lua;" .. package.path

    local function object(t)
        t.address = nextId()
        t.IsValid = function(self) return not self.destroyed end
        t.GetAddress = function(self) return self.address end
        t.GetFullName = t.GetFullName or function(self) return self.fullName or ("FakeObject_" .. self.address) end
        return t
    end

    -- TMap<URadioStaticSourceComponent*, float>. Like UE4SS's LuaTMap, Find throws on a
    -- missing key and hands the value back wrapped (RemoteUnrealParam-style :get()).
    local function fakeMap()
        local values = {}
        return {
            values = values,
            Contains = function(self, key)
                if self.broken then error("Tried interacting with a map with an unsupported key type") end
                return values[key:GetAddress()] ~= nil
            end,
            Find = function(self, key)
                local v = values[key:GetAddress()]
                if v == nil then error("Map key not found.") end
                return { get = function() return v end }
            end,
        }
    end
    function CreateInvalidObject() return object({ destroyed = true }) end

    local activeWaypoints = {} -- waypoint name -> true, as URadioWaypointManager reports it
    local waypointManager = object({ IsWaypointActive = function(self, name) return activeWaypoints[name] == true end })

    local DefaultPawnClass = object({ name = "DefaultPawn" })
    local SEQUENCE_PLAYER_CLASS = "/Script/LevelSequence.LevelSequencePlayer"
    local sequencePlayers = {}
    -- The in-game CRTV dial's needle colours (linear RGBA), on WBP_CRTV_Needles' class default object.
    local NEEDLES = "/Game/Townfall/Characters/CharacterProps/Radio/WBP_CRTV_Needles.Default__WBP_CRTV_Needles_C"
    world.needleLookups = 0
    world.needles = object({
        EnemySignalNeedleColour = { R = 1, G = 0.05, B = 0.02, A = 1 },
        WaypointSignalNeedleColour = { R = 0.1, G = 0.35, B = 1, A = 1 },
        NotDiscoveredSignalNeedleColour = { R = 0.8, G = 0.8, B = 0.75, A = 1 },
    })
    function StaticFindObject(path)
        world.staticFindCalls = world.staticFindCalls + 1 -- each one walks every object in the game's UE4SS
        if path == SEQUENCE_PLAYER_CLASS then return object({ name = "LevelSequencePlayer" }) end
        if path == NEEDLES then
            world.needleLookups = world.needleLookups + 1
            return options.noNeedles and CreateInvalidObject() or world.needles
        end
        if path == "/Script/Engine.DefaultPawn" then return DefaultPawnClass end
        if path == ENEMY_CLASS then
            return options.noEnemyClass and CreateInvalidObject() or object({ name = "TownfallEnemyCharacter" })
        end
        if path == WAYPOINT_CLASS then return object({ name = "RadioWaypointSourceComponent" }) end
        if path == "/Script/Townfall.Default__RadioWaypointManager" then
            -- Get() is static; UE4SS calls it on the class default object.
            return object({ Get = function() return options.noWaypointManager and CreateInvalidObject() or waypointManager end })
        end
        error("unexpected StaticFindObject " .. path)
    end
    function NotifyOnNewObject(path, fn)
        assert(path == ENEMY_CLASS or path == WAYPOINT_CLASS or path == SEQUENCE_PLAYER_CLASS,
            "unexpected NotifyOnNewObject " .. path)
        notify[path] = fn
    end
    local instances = { TownfallEnemyCharacter = enemies, RadioWaypointSourceComponent = waypoints,
                        LevelSequencePlayer = sequencePlayers }
    function FindAllOf(cls)
        local list = assert(instances[cls], "unexpected FindAllOf " .. cls)
        if cls == "TownfallEnemyCharacter" then world.findAllOfCalls = world.findAllOfCalls + 1 end
        local found = {}
        for _, o in ipairs(list) do
            if o:IsValid() then found[#found + 1] = o end
        end
        return #found > 0 and found or nil
    end
    function LoopInGameThreadWithDelay(ms, fn)
        loops[#loops + 1] = { ms = ms, fn = fn }
        return #loops
    end
    -- In the UE4SS build Townfall uses, LoopAsync runs Lua on a second thread in the same Lua state
    -- as the game thread, unlocked; that crashes the game. Fail any test that uses it.
    function LoopAsync() error("LoopAsync races the game thread in UE4SS; use LoopInGameThreadWithDelay") end
    function ExecuteInGameThread() error("everything already runs on the game thread") end
    package.preload["UEHelpers"] = function()
        return { GetPlayerController = function() return controller or CreateInvalidObject() end }
    end

    local function fstring(s) return { ToString = function() return s end } end

    local function actor(name, class, x, y, z)
        return object({
            name = name, class = class, loc = { X = x, Y = y, Z = z },
            GetFullName = function(self)
                local className = type(self.class) == "table" and self.class.name or self.class
                return className .. " /Game/Townfall/Maps/Level_Townfall.Level_Townfall:PersistentLevel." .. self.name
            end,
            GetFName = function(self) return { ToString = function() return self.name end } end,
            IsA = function(self, class) return self.class == class end,
            K2_GetActorLocation = function(self)
                if self.fail then error("K2_GetActorLocation not found") end
                return { X = self.loc.X, Y = self.loc.Y, Z = self.loc.Z }
            end,
            AddControllerPitchInput = function(self, value)
                if controller and not controller.locked then
                    controller.pitch = math.max(-80, math.min(80, (controller.pitch or 0) + value))
                end
            end,
        })
    end

    local function possess(pawn)
        if controller then controller.destroyed = true; controller.Pawn.destroyed = true end
        controller = object({
            name = "Controller_" .. pawn.name, Pawn = pawn, yaw = 0, pitch = -5,
            GetFullName = function(self) return self.name end,
            GetControlRotation = function(self) return { Pitch = self.pitch, Yaw = self.yaw, Roll = 0 } end,
            SetControlRotation = function(self, r)
                if not self.locked then self.pitch, self.yaw = r.Pitch, r.Yaw end
            end,
        })
    end

    function world.controlRotation() return controller.pitch, controller.yaw end
    function world.dropControlPitch() controller.pitch = nil end
    -- The game holds the camera: SetControlRotation has no effect.
    function world.lockCamera() controller.locked = true end

    -- tf_native.dll, faked: its functions count their calls. options.noNative: it isn't there.
    world.native = { inits = 0, updates = 0, aligns = 0, looks = 0, sounds = 0 }
    package.loadlib = function(_, name)
        if options.noNative then return nil, "The specified module could not be found." end
        local count = ({ tf_native_init = "inits", tf_native_update = "updates", tf_native_align = "aligns",
                         tf_native_look = "looks", tf_native_sound = "sounds" })[name]
        return count and function() world.native[count] = world.native[count] + 1 end
    end

    function world.start() dofile(scriptsDir .. "/main.lua") end

    function world.enterMenu()
        possess(actor("DefaultPawn_" .. nextId(), DefaultPawnClass, 0, 0, 0))
    end

    -- The player character with its CRTV. Tests change world.radio's fields directly;
    -- `fail` makes every radio read throw, like a function renamed by a game update.
    function world.enterGameplay(x, y, z, yaw)
        local pawn = actor("BP_Bill_C_" .. nextId(), "BP_Bill_C", x, y, z)
        pawn.bInInterior = false
        local born = worldTime
        pawn.GetGameTimeSinceCreation = function() return worldTime - born end
        local radio = object({
            active = false, frequency = 0, cachedHighestSignalStrength = 0, cachedHighestStrengthSignalType = 0,
            fullName = "HandheldRadio_C /Game/Townfall/Radio.HandheldRadio_C",
        })
        -- The CRTV screen (UCRTVWidget).
        local widget = object({})
        widget.fullName = "WBP_CRTV_C /Game/Townfall/UI/WBP_CRTV.WBP_CRTV_C"
        radio.GetCRTVWidget = function() return widget end
        radio.confirms, radio.stores, radio.storeReleases = 0, 0, 0
        radio.PlayerInput_FineTuneConfirm_Pressed = function() radio.confirms = radio.confirms + 1 end
        radio.PlayerInput_AdvancedTuningStoreSignal_Pressed = function() radio.stores = radio.stores + 1 end
        radio.PlayerInput_AdvancedTuningStoreSignal_Released = function() radio.storeReleases = radio.storeReleases + 1 end
        world.crtvWidget = widget
        -- The radio's own material shows the screen's texture (RadarTexture), drawn from that widget: what
        -- tf_native.lua hands to the DLL.
        local screenTexture = object({ fullName = "TextureRenderTarget2D /Engine/Transient.CRTVScreen" })
        local radar = { Widget = widget, MaterialTextureName = "Texture",
                        Material = object({ K2_GetTextureParameterValue = function() return screenTexture end }) }
        radio.PlayerCharacterType = 0
        radio.RadarPropMap = { Find = function() return { get = function() return { RadarTexture = radar } end } end }
        world.screenTexture, world.radar = screenTexture, radar
        -- The CRTV prop the character holds (BP_Radio_Prop).
        radio.SpawnedRadio = object({ bHidden = false,
            SetActorHiddenInGame = function(self, hidden) self.bHidden = hidden end })
        local manager = {
            GetHighestSignalStrengthWaypoint = function() return radio.highestWaypoint or CreateInvalidObject() end,
            GetHighestSignalStrengthStaticSource = function() return radio.highestSource or CreateInvalidObject() end,
            StaticSourceStrengthMap = fakeMap(),
        }
        world.manager = manager
        pawn.GetRadio = function()
            if radio.fail then error("GetRadio not found") end
            return radio
        end
        radio.SetTunedFrequency = function(self, frequency) radio.frequency = frequency end
        pawn.SetRadioInActiveMode = function(self, active) radio.active = active end
        -- What the character keeps about the radio (as UE4SS.log showed it): IsUsingRadio, and RadioAlpha on his
        -- animation (the hands). The requests move both at once unless radio.ignoreRequests; a test that wants
        -- a raise or lowering half done sets them itself.
        local anim = object({ RadioAlpha = 0 })
        pawn.IsUsingRadio = false
        -- BP_Bill's own requests, what the radio button runs; `noRadioRequests`: a build without them.
        radio.requests = {}
        if not options.noRadioRequests then
            pawn.RequestRadioON = function()
                radio.requests[#radio.requests + 1] = "on"
                if not radio.ignoreRequests then radio.active, pawn.IsUsingRadio, anim.RadioAlpha = true, true, 1 end
            end
            pawn.RequestRadioOFF = function(self, force)
                radio.requests[#radio.requests + 1] = force and "off (forced)" or "off"
                if not radio.ignoreRequests then radio.active, pawn.IsUsingRadio, anim.RadioAlpha = false, false, 0 end
            end
        end
        pawn.GetIsRadioInActiveMode = function() return radio.active end
        pawn.Mesh = object({ GetAnimInstance = function() return anim end })
        -- The first-person body (UE4SS_ObjectDump: BP_Bill_C.Mesh1P, Simon_FirstPersonBody_AnimBP): the hands.
        pawn.Mesh1P = object({ bHiddenInGame = false,
            SetHiddenInGame = function(self, hidden, children) self.bHiddenInGame, self.propagated = hidden, children end })
        world.anim = anim
        pawn.GetRadioCurrentTunedFrequency = function() return radio.frequency end
        pawn.GetWorld = function() return { AuthorityGameMode = { HandheldRadioManager = manager } } end

        world.player, world.radio = pawn, radio
        possess(pawn)
        controller.yaw = yaw or 0
    end

    function world.movePlayer(x, y, z) world.player.loc = { X = x, Y = y, Z = z } end
    function world.setYaw(yaw) controller.yaw = yaw end

    -- The game spawns an enemy: it is constructed (NotifyOnNewObject fires) and is in the world.
    -- BP_TestAIAgent_C has a RadioStaticSource; with `noSource` the property doesn't exist,
    -- as for an enemy class that has none (UE4SS throws on unknown properties).
    function world.spawnEnemy(name, x, y, z, noSource)
        local e = actor(name, "BP_TestAIAgent_C", x, y, z)
        e.AlertState, e.begunDeath = 0, false
        e.GetHasBegunDeath = function(self) return self.begunDeath end
        if noSource then
            setmetatable(e, { __index = function(_, k)
                if k == "RadioStaticSource" then error("property RadioStaticSource not found") end
            end })
        else
            e.RadioStaticSource = object({
                TuningFrequency = 0.23, TuningTolerance_Inner = 0.02, TuningTolerance_Outer = 0.05,
                distanceSignalFalloffBegin_ActiveOutdoor = 1500, distanceSignalFalloffBegin_ActiveIndoor = 1000,
                distanceSignalCutoff_ActiveOutdoor = 4000, distanceSignalCutoff_ActiveIndoor = 2500,
            })
        end
        enemies[#enemies + 1] = e
        if notify[ENEMY_CLASS] then notify[ENEMY_CLASS](e) end
        return e
    end

    -- A CRTV waypoint (URadioWaypointSourceComponent) on an actor at x, y, z. Reach 2000-10000 cm.
    function world.addWaypoint(name, x, y, z, channel, isActive, isSignalObject)
        local owner = actor("BP_CRTVWaypoint_" .. name, "BP_CRTVWaypoint_C", x, y, z)
        local c = object({
            WaypointName = fstring(name), TuningFrequency = channel,
            TuningTolerance_Inner = 0.02, TuningTolerance_Outer = 0.04,
            distanceSignalFalloffBegin = 2000, distanceSignalCutoff = 10000,
            bIsSignalObject = isSignalObject == true, untuned = 0, fullyTuned = false,
            GetOwner = function() return owner end,
            GetCurrentUntunedSignalStrength = function(self) return self.untuned end,
            HasBeenFullyTuned = function(self) return self.fullyTuned end,
        })
        waypoints[#waypoints + 1] = c
        activeWaypoints[name] = isActive or nil
        if notify[WAYPOINT_CLASS] then notify[WAYPOINT_CLASS](c) end
        return c
    end

    function world.setWaypointActive(name, isActive) activeWaypoints[name] = isActive or nil end

    -- A cutscene: a level sequence player (constructed, so NotifyOnNewObject fires) playing `name`.
    function world.playSequence(name)
        local s = object({
            playing = true,
            IsPlaying = function(self) return self.playing end,
            GetSequence = function() return object({ GetFName = function() return fstring(name) end }) end,
        })
        sequencePlayers[#sequencePlayers + 1] = s
        if notify[SEQUENCE_PLAYER_CLASS] then notify[SEQUENCE_PLAYER_CLASS](s) end
        return s
    end

    -- A waypoint component that isn't in the level: a Blueprint's template, with no owning actor.
    function world.addWaypointTemplate(name, channel)
        local c = world.addWaypoint(name, 0, 0, 0, channel, true)
        c.GetOwner = function() return nil end
        return c
    end

    -- Sets what the radio manager holds for an enemy's signal source in one of its per-source maps.
    function world.setSourceValue(enemy, mapName, value)
        world.manager[mapName].values[enemy.RadioStaticSource:GetAddress()] = value
    end

    -- Loading a Blueprint class constructs its default object, which NotifyOnNewObject also reports.
    function world.constructDefaultObject(name)
        if notify[ENEMY_CLASS] then notify[ENEMY_CLASS](actor(name, "BP_TestAIAgent_C", 0, 0, 0)) end
    end

    -- One second of game time: the 1 s loops run once, the 100 ms loop `samples` times.
    function world.tick(samples)
        for _, l in ipairs(loops) do if l.ms >= 1000 then l.fn() end end
        for _ = 1, samples or 1 do
            for _, l in ipairs(loops) do if l.ms < 1000 then l.fn() end end
        end
        clock = clock + 1
        if not world.paused then worldTime = worldTime + 1 end
    end

    return world
end
