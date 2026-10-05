-- A fake UE4SS runtime and Townfall world for running the mod outside the game.
-- Only the parts of the UE4SS Lua API the mod uses are faked, with the shapes the
-- real game showed. Returns a constructor used by test_mod.py.

return function(scriptsDir, tempDir, options)
    local world = { logs = {}, findAllOfCalls = 0, staticFindCalls = 0, radioActorCalls = 0 } -- findAllOfCalls counts enemy walks
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
    os.getenv = function(k) if k == "TEMP" and not options.noTemp then return tempDir end end
    -- The mod keeps the command files open (tf_commands.lua); tests close them before removing the folder.
    local opened, realOpen = {}, io.open
    world.telemetryWrites = 0 -- how often the telemetry file was opened for writing
    io.open = function(path, mode)
        path = path:gsub("\\", "/") -- the mod joins %TEMP% paths with "\"; elsewhere than Windows that must still open
        if mode == "w" and path:find("telemetry.json", 1, true) then world.telemetryWrites = world.telemetryWrites + 1 end
        local f, err = realOpen(path, mode)
        if f and mode == "r" then opened[#opened + 1] = f end
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
        -- The class of a fake object lists its own fields as properties (an object field is an ObjectProperty).
        t.GetClass = t.GetClass or function(self)
            local function property(name, value)
                local kind = type(value) == "table" and value.IsValid and "ObjectProperty"
                    or type(value) == "boolean" and "BoolProperty" or "FloatProperty"
                return { GetFName = function() return { ToString = function() return name end } end,
                         GetClass = function() return { GetFName = function() return { ToString = function() return kind end } end } end }
            end
            return {
                IsValid = function() return true end,
                GetFName = function() return { ToString = function() return (self.fullName or "FakeClass"):match("^(%S+)") end } end,
                ForEachProperty = function(_, visit)
                    local names = {}
                    for name, value in pairs(self) do
                        local kind = type(value)
                        if type(name) == "string" and (kind == "number" or kind == "boolean" or (kind == "table" and value.IsValid)) then
                            names[#names + 1] = name
                        end
                    end
                    table.sort(names)
                    for _, name in ipairs(names) do visit(property(name, self[name])) end
                end,
                GetSuperStruct = function() return CreateInvalidObject() end,
            }
        end
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
    -- The cutscenes' screen video players: assets, there once something loads them (world.playCutsceneVideo).
    local CUTSCENE_PLAYERS = {
        bink = "/Game/Movies/Cutscene_Diegetic_Movies/Bink/Generic/CinematicMoviePlayer_Bink.CinematicMoviePlayer_Bink",
        media = "/Game/Movies/Cutscene_Diegetic_Movies/Cutscene_Diegetic_MediaPlayer.Cutscene_Diegetic_MediaPlayer",
    }
    local cutscenePlayers = {}
    local MEDIA_CLASSES = { bink = "/Script/BinkMediaPlayer.BinkMediaPlayer", media = "/Script/MediaAssets.MediaPlayer" }
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
        for _, playerPath in pairs(CUTSCENE_PLAYERS) do
            if path == playerPath then return cutscenePlayers[path] or CreateInvalidObject() end
        end
        for _, class in pairs(MEDIA_CLASSES) do
            if path == class then return object({ name = class }) end
        end
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
        assert(path == ENEMY_CLASS or path == WAYPOINT_CLASS or path == SEQUENCE_PLAYER_CLASS
            or path == MEDIA_CLASSES.bink or path == MEDIA_CLASSES.media, "unexpected NotifyOnNewObject " .. path)
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
    -- FindFirstOf: the first instance of a class; tests register one in world.firstOf[class].
    world.firstOf = {}
    function FindFirstOf(cls) return world.firstOf[cls] end
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
    -- A UFMODAudioComponent: its volume, and what it plays (world.speak).
    local function fmodSound()
        return object({
            volume = 1, playing = false, ms = 0, ProgrammerSoundName = fstring(""),
            SetVolume = function(self, v) self.volume = v end,
            IsPlaying = function(self) return self.playing end,
            GetTimelinePosition = function(self) return self.ms end,
        })
    end

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
    function world.lookUp(pitch) controller.pitch = pitch end
    function world.dropControlPitch() controller.pitch = nil end
    -- The game holds the camera: SetControlRotation has no effect.
    function world.lockCamera() controller.locked = true end

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
        -- The CRTV screen (UCRTVWidget) and its two Bink players; world.playVideo starts one.
        local function binkPlayer()
            return object({
                url = "", playing = false, seconds = nil,
                IsPlaying = function(self) return self.playing end,
                GetUrl = function(self) return fstring(self.url) end,
                GetTime = function(self) return { Ticks = self.seconds and self.seconds * 1e7 } end,
            })
        end
        -- Its fine-tune mini-game: the bar, the box running along it and the diamond, each an image in
        -- a canvas slot (x/w, the slots' alignment) with a render offset (t.X). Tests change world.fineTuneUi.
        local tuneUi = {
            visible = false, text = "FINE TUNE - SEARCHING", align = 0,
            band = { x = 100, w = 400, t = { X = 0, Y = 0 } },
            box = { x = 100, w = 20, t = { X = 0, Y = 0 } },
            zone = { x = 285, w = 30, t = { X = 0, Y = 0 } },
        }
        local function uiImage(key)
            return object({
                Slot = {
                    GetPosition = function() return { X = tuneUi[key].x, Y = 0 } end,
                    GetSize = function() return { X = tuneUi[key].w, Y = 10 } end,
                    GetAlignment = function() return { X = tuneUi.align, Y = 0 } end,
                },
                RenderTransform = { Translation = tuneUi[key].t },
            })
        end
        local widget = object({
            EnemyVideoPlayer_Bink = binkPlayer(), WaypointVideoPlayer_Bink = binkPlayer(),
            Canvas_FineTuning = object({ IsVisible = function() return tuneUi.visible end }),
            Image_NarrowBand = uiImage("band"), Image_DigitalNeedle = uiImage("box"), Image_FineTuneZone = uiImage("zone"),
            DialocTextBlock_FineTune = object({ GetText = function() return fstring(tuneUi.text) end }),
        })
        widget.fullName = "WBP_CRTV_C /Game/Townfall/UI/WBP_CRTV.WBP_CRTV_C"
        -- The game keeps the mini-game on another widget than the one that plays the CRTV's videos (UE4SS.log
        -- 2026-10-06): this moves the fake's canvas, bar, box and diamond over to a widget the CRTV's widget holds.
        function world.splitMiniGame()
            local other = object({ fullName = "WBP_PortableTVScreen_C /Game/Townfall/UI/WBP_PortableTVScreen.WBP_PortableTVScreen_C" })
            for _, key in ipairs({ "Canvas_FineTuning", "Image_NarrowBand", "Image_DigitalNeedle", "Image_FineTuneZone",
                                   "DialocTextBlock_FineTune" }) do
                other[key], widget[key] = widget[key], nil
            end
            widget.MiniGameScreen = other
            world.miniGameWidget = other
            return other
        end
        widget.WaypointVideoAudioComponent = fmodSound()
        radio.staticAudioComponent = fmodSound()
        pawn.SFX_CRTV = fmodSound()
        radio.GetCRTVWidget = function() return widget end
        radio.confirms = 0
        radio.PlayerInput_FineTuneConfirm_Pressed = function() radio.confirms = radio.confirms + 1 end
        world.crtvWidget, world.fineTuneUi = widget, tuneUi
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
        -- What the character keeps about the radio (UE4SS.log 2026-10-06): IsUsingRadio, and RadioAlpha on his
        -- animation (the hands). The requests move both at once unless radio.ignoreRequests; a test that wants
        -- a raise or lowering half done sets them itself.
        local anim = object({ RadioAlpha = 0 })
        pawn.IsUsingRadio = false
        -- BP_Bill's own requests, what the controller's L1 runs; `noRadioRequests`: a build without them.
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
        -- The radio's actor isn't modelled; the mod asks for it once per look for the mini-game's screen.
        pawn.GetRadioActor = function() world.radioActorCalls = world.radioActorCalls + 1; return nil end
        pawn.Mesh = object({ GetAnimInstance = function() return anim end })
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
        e.RadioSignalClear, e.RadioSignalDist = fmodSound(), fmodSound()
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
                CrtvVideoSignal_Url = { ToString = function() return "./Movies/CRTV_Movies/Bink/TheFallen_Focused.bk2" end },
            })
        end
        enemies[#enemies + 1] = e
        if notify[ENEMY_CLASS] then notify[ENEMY_CLASS](e) end
        return e
    end

    -- A CRTV waypoint (URadioWaypointSourceComponent) on an actor at x, y, z. Reach 2000-10000 cm.
    -- `video` is relative to CRTV_Movies without extension, as the game's URL has it.
    function world.addWaypoint(name, x, y, z, channel, isActive, video, isSignalObject)
        local owner = actor("BP_CRTVWaypoint_" .. name, "BP_CRTVWaypoint_C", x, y, z)
        owner.ClearSignal, owner.DistortedSignal = fmodSound(), fmodSound()
        local c = object({
            WaypointName = fstring(name), TuningFrequency = channel,
            -- Its dialogue plays on the actor's two FMOD components (BP_RadioWaypoint).
            WaypointDialogueClearComponent = owner.ClearSignal, WaypointDialogueDistComponent = owner.DistortedSignal,
            WaypointDialogueClear = { DialogueID = fstring(name .. "_Clear") },
            WaypointDialogueDist = { DialogueID = fstring(name .. "_Dist") },
            TuningTolerance_Inner = 0.02, TuningTolerance_Outer = 0.04,
            distanceSignalFalloffBegin = 2000, distanceSignalCutoff = 10000,
            CrtvVideoSignal_Url = fstring(video and ("./Movies/CRTV_Movies/" .. video .. ".bk2") or ""),
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

    -- The waypoint says `line` (Dialoc's programmer sound), `ms` in, on its clear or distorted
    -- component; line nil: it falls quiet.
    function world.speak(c, line, ms, distorted)
        for _, sound in ipairs({ c.WaypointDialogueClearComponent, c.WaypointDialogueDistComponent }) do
            sound.playing = false
        end
        if not line then return end
        local sound = distorted and c.WaypointDialogueDistComponent or c.WaypointDialogueClearComponent
        sound.playing, sound.ms, sound.ProgrammerSoundName = true, ms or 0, fstring(line)
    end

    -- A cutscene plays `url` on the Bink player ("bink") or Unreal's media player ("media"), `seconds` in;
    -- url nil stops it.
    -- The first time, the player asset loads: NotifyOnNewObject fires for its class.
    function world.playCutsceneVideo(which, url, seconds)
        local path = CUTSCENE_PLAYERS[which]
        local p = cutscenePlayers[path]
        if not p then
            p = object({
                IsPlaying = function(self) return self.playing end,
                GetUrl = function(self) return fstring(self.url) end,
                GetTime = function(self) return { Ticks = self.seconds and self.seconds * 1e7 } end,
                GetFullName = function() return (which == "bink" and "BinkMediaPlayer " or "MediaPlayer ") .. path end,
            })
            cutscenePlayers[path] = p
            if notify[MEDIA_CLASSES[which]] then notify[MEDIA_CLASSES[which]](p) end
        end
        p.url, p.playing, p.seconds = url or "", url ~= nil, seconds
        return p
    end

    -- The game frees a cutscene player (a level change): IsValid turns false, and any other call on it
    -- would crash the game (EXCEPTION_ACCESS_VIOLATION). Counted in world.freedCalls, as a
    -- pcall in the mod would swallow the error here while nothing can catch the crash in the game.
    world.freedCalls = 0
    function world.freeCutscenePlayer(which)
        local p = cutscenePlayers[CUTSCENE_PLAYERS[which]]
        p.destroyed = true
        for _, method in ipairs({ "IsPlaying", "GetUrl", "GetTime", "GetFullName", "SetVolume" }) do
            p[method] = function()
                world.freedCalls = world.freedCalls + 1
                error(method .. " called on a freed object")
            end
        end
        cutscenePlayers[CUTSCENE_PLAYERS[which]] = nil
    end

    -- A cutscene: a level sequence player (constructed, so NotifyOnNewObject fires) playing `name`,
    -- `seconds` in at 30 fps; world.stopSequence ends it.
    function world.playSequence(name, seconds)
        local frames = seconds * 30
        local s = object({
            playing = true,
            IsPlaying = function(self) return self.playing end,
            GetSequence = function() return object({ GetFName = function() return fstring(name) end }) end,
            GetCurrentTime = function()
                return { Time = { FrameNumber = { Value = math.floor(frames) }, SubFrame = frames % 1 },
                         Rate = { Numerator = 30, Denominator = 1 } }
            end,
        })
        sequencePlayers[#sequencePlayers + 1] = s
        if notify[SEQUENCE_PLAYER_CLASS] then notify[SEQUENCE_PLAYER_CLASS](s) end
        return s
    end

    function world.setWaypointVideo(c, video)
        c.CrtvVideoSignal_Url = fstring(video and ("./Movies/CRTV_Movies/" .. video .. ".bk2") or "")
    end

    -- A waypoint component that isn't in the level: a Blueprint's template, with no owning actor.
    function world.addWaypointTemplate(name, channel)
        local c = world.addWaypoint(name, 0, 0, 0, channel, true)
        c.GetOwner = function() return nil end
        return c
    end

    -- The in-game CRTV screen plays `url` on its "EnemyVideoPlayer_Bink" or "WaypointVideoPlayer_Bink";
    -- `seconds` nil: the playback time can't be read. url nil stops it.
    function world.playVideo(playerName, url, seconds)
        local bink = world.crtvWidget[playerName]
        bink.url, bink.playing, bink.seconds = url or "", url ~= nil, seconds
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
