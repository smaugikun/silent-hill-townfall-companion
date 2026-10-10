"""Runs the UE4SS mod in TownfallCompanion against a fake UE4SS/Townfall world.

No game needed. Requires lupa, which bundles Lua 5.4 (the version UE4SS embeds):

    pip install lupa
    python -m unittest discover -s tests -v
"""
import json
import struct
import tempfile
import time
import types
import unittest
from pathlib import Path

from lupa import lua54

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "TownfallCompanion" / "Scripts"
FAKE_UE4SS = Path(__file__).with_name("fake_ue4ss.lua")

# Where the player really stood in Level_Townfall (UE cm), and that spot in the phone frame (m).
UE_X, UE_Y, UE_Z = -28877.0, -1753.5, 706.5
PHONE_X, PHONE_Y = -17.535, -288.77


class ModTest(unittest.TestCase):
    options = {}

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.telemetry_file = Path(tmp.name) / "townfall-companion-telemetry.json"
        self.commands_file = Path(tmp.name) / "townfall-companion-commands.json"
        self.tmp = Path(tmp.name)
        self.bridge_beat = self.tmp / "townfall-companion-bridge.json"
        self.beat(phones=1)  # the companion is running, with a phone on the page
        lua = lua54.LuaRuntime()
        make_world = lua.execute(FAKE_UE4SS.read_text(encoding="utf-8"))
        self.world = make_world(SCRIPTS.as_posix(), tmp.name, lua.table(**self.options))
        self.addCleanup(self.world.closeFiles)  # the mod keeps command files open; runs before tmp.cleanup
        self.world.start()

    def beat(self, phones=1, age=0, fps=0):
        """The companion's heartbeat file, as the companion writes it."""
        self.bridge_beat.write_text(json.dumps({"time": int(time.time()) - age, "pid": 1, "port": 8790, "phones": phones,
                                                "nativeFps": fps if phones else 0, "nativeWidth": 640}))

    def mode(self, selector, on_monitor=False, mini_game_shown=False):
        """The phone's mode, as bridge.py writes it: the phone says it every couple of seconds."""
        self.mode_seq = getattr(self, "mode_seq", int(time.time() * 1000)) + 1
        (self.tmp / "townfall-companion-mode.json").write_text(json.dumps(
            {"selector": selector, "onMonitor": on_monitor, "miniGameShown": mini_game_shown, "seq": self.mode_seq}))

    def logs(self):
        return list(self.world.logs.values())

    def logged(self, text):
        return sum(text in line for line in self.logs())

    def telemetry(self):
        return json.loads(self.telemetry_file.read_text(encoding="utf-8"))

    def enemy_ids(self):
        return sorted(e["id"] for e in self.telemetry()["enemies"])


class PlayerTest(ModTest):
    def test_menu_pawn_is_not_tracked(self):
        self.world.enterMenu()
        self.world.tick(10)
        self.assertEqual(self.logs()[0], "[TF-COMPANION] Townfall Companion loaded")
        self.assertEqual(self.logged("menu/loading (DefaultPawn), not tracking"), 1)
        self.assertFalse(self.telemetry_file.exists())

    def test_each_sample_carries_the_games_clock(self):
        # The phone times speech and the fine-tune box by it, not by when the sample reaches it.
        self.world.enterGameplay(UE_X, UE_Y, UE_Z)
        self.world.tick(1)
        first = self.telemetry()["t"]
        self.world.tick(1)
        self.assertEqual(self.telemetry()["t"] - first, 1.0)

    def test_the_worlds_clock_stands_still_in_the_pause_menu(self):
        # The phone holds its screen and sound while it does; the sample clock (t) runs on meanwhile.
        self.world.enterGameplay(UE_X, UE_Y, UE_Z)
        self.world.tick(1)
        running = self.telemetry()["world"]
        self.world.tick(1)
        self.assertEqual(self.telemetry()["world"] - running, 1.0)
        self.world.paused = True
        self.world.tick(1)
        held, t = self.telemetry()["world"], self.telemetry()["t"]
        self.world.tick(1)
        self.world.tick(1)
        self.assertEqual((self.telemetry()["world"], self.telemetry()["t"] - t), (held, 2.0))

    def test_player_is_written_in_phone_frame(self):
        self.world.enterGameplay(UE_X, UE_Y, UE_Z, -121.1)
        self.world.tick(1)
        player = self.telemetry()["player"]
        self.assertAlmostEqual(player["x"], PHONE_X, delta=0.01)
        self.assertAlmostEqual(player["y"], PHONE_Y, delta=0.01)
        self.assertAlmostEqual(player["yaw"], 238.9, delta=0.05)
        self.assertEqual(sorted(player), ["alive", "x", "y", "yaw"])  # no height: the phone is a map
        self.assertIs(player["alive"], True)

    def test_walking_along_ue_x_is_north_on_the_phone(self):
        self.world.enterGameplay(UE_X, UE_Y, UE_Z)
        self.world.tick(1)
        start = self.telemetry()["player"]
        self.world.movePlayer(UE_X + 500, UE_Y, UE_Z)
        self.world.tick(1)
        moved = self.telemetry()["player"]
        self.assertAlmostEqual(moved["y"] - start["y"], 5.0, delta=0.01)
        self.assertAlmostEqual(moved["x"], start["x"], delta=0.01)

class EnemyTest(ModTest):
    def setUp(self):
        super().setUp()
        self.world.enterGameplay(UE_X, UE_Y, UE_Z)

    def test_spawns_are_tracked_without_repeated_full_scans(self):
        self.world.spawnEnemy("Fearful_1", UE_X + 1000, UE_Y, UE_Z)  # loaded with the level
        self.world.tick(1)
        self.world.constructDefaultObject("Default__BP_TestAIAgent_C")
        self.world.spawnEnemy("Enraged_2", UE_X, UE_Y + 2500, UE_Z)  # 25 m east
        self.world.spawnEnemy("Fearful_3", UE_X + 20000, UE_Y, UE_Z)  # 200 m, out of range
        for _ in range(5):
            self.world.tick(10)
        self.assertEqual(self.enemy_ids(), ["Enraged_2", "Fearful_1"])
        self.assertEqual(self.world.findAllOfCalls, 1)
        self.assertEqual(self.logged("[TF-ENEMY] 3 enemies, 2 within 60 m"), 1)
        self.assertEqual(self.logged("Default__"), 0)

    def test_out_of_range_enemy_appears_when_player_gets_close(self):
        self.world.spawnEnemy("Fearful_3", UE_X + 20000, UE_Y, UE_Z)
        self.world.tick(1)
        self.assertEqual(self.enemy_ids(), [])
        self.world.movePlayer(UE_X + 18500, UE_Y, UE_Z)
        self.world.tick(1)
        self.assertEqual(self.enemy_ids(), ["Fearful_3"])

    def test_enemy_position_and_state(self):
        enemy = self.world.spawnEnemy("Fearful_1", UE_X + 1000, UE_Y, UE_Z)
        self.world.tick(1)
        sent = self.telemetry()["enemies"][0]
        self.assertAlmostEqual(sent["y"], PHONE_Y + 10, delta=0.01)
        self.assertEqual(sent["alive"], True)

        enemy["AlertState"] = 4
        self.world.tick(3)
        self.world.tick(3)
        self.assertEqual(self.logged("Fearful_1 alert=spotted dead=false"), 1)  # logged; the phone doesn't use it

        enemy["begunDeath"] = True
        self.world.tick(1)
        self.assertFalse(self.telemetry()["enemies"][0]["alive"])

    def test_destroyed_enemy_is_dropped(self):
        enemy = self.world.spawnEnemy("Fearful_1", UE_X + 1000, UE_Y, UE_Z)
        self.world.tick(1)
        enemy["destroyed"] = True
        self.world.tick(1)
        self.assertEqual(self.enemy_ids(), [])
        self.assertEqual(self.logged("[TF-ENEMY] 0 enemies, 0 within 60 m"), 1)

    def test_enemy_read_error_keeps_player_telemetry(self):
        enemy = self.world.spawnEnemy("Fearful_1", UE_X + 1000, UE_Y, UE_Z)
        self.world.tick(1)
        enemy["fail"] = True
        self.world.movePlayer(UE_X + 100, UE_Y, UE_Z)
        self.world.tick(10)
        self.world.tick(10)
        telemetry = self.telemetry()
        self.assertEqual(telemetry["enemies"], [])
        self.assertAlmostEqual(telemetry["player"]["y"], PHONE_Y + 1, delta=0.01)
        self.assertEqual(self.logged("[TF-ENEMY] read error"), 1)
        self.assertEqual(self.logged("enemy update error"), 1)

    def test_level_reload_scans_again(self):
        self.world.spawnEnemy("Fearful_1", UE_X + 1000, UE_Y, UE_Z)
        self.world.tick(1)
        self.world.enterMenu()
        self.world.tick(1)
        self.world.enterGameplay(UE_X, UE_Y, UE_Z)
        self.world.tick(1)
        self.assertEqual(self.world.findAllOfCalls, 2)
        self.assertEqual(self.logged("[TF-ENEMY] new BP_TestAIAgent_C"), 2)


class CrtvTest(ModTest):
    def setUp(self):
        super().setUp()
        self.world.enterGameplay(UE_X, UE_Y, UE_Z)
        self.radio = self.world.radio

    def tune(self, active, frequency, strength, signal_type):
        self.radio["active"] = active
        self.radio["frequency"] = frequency
        self.radio["cachedHighestSignalStrength"] = strength
        self.radio["cachedHighestStrengthSignalType"] = signal_type

    def crtv(self):
        crtv = self.telemetry()["crtv"]
        del crtv["needleColours"], crtv["miniGame"]  # NeedleColourTest, NativeTest
        return crtv

    # Values below are what the game reported: the dial runs 0..1 and a signal is 0 or 1.
    def test_tuned_enemy_signal_is_sent(self):
        self.tune(True, 0.23, 1.0, 2)
        self.world.tick(1)
        self.assertEqual(self.crtv(), {"active": True, "frequency": 0.23, "signalType": "enemy"})

    def test_signal_type_is_none_without_strength(self):
        self.tune(True, 0.5, 0, 2)
        self.world.tick(1)
        self.assertEqual(self.telemetry()["crtv"]["signalType"], "none")

    def test_lowered_crtv_ignores_cached_signal(self):
        self.tune(False, 0.0, 1.0, 2)
        self.world.tick(1)
        self.assertEqual(self.crtv(), {"active": False, "frequency": 0.0, "signalType": "none"})

    def test_state_is_logged_on_change(self):
        self.tune(True, 0.23, 1.0, 1)
        for _ in range(3):
            self.world.tick(10)
        self.assertEqual(self.logged("[TF-CRTV] active=true frequency=0.23 signal=1.00 type=waypoint_tuned"), 1)
        self.radio["frequency"] = 0.3
        self.world.tick(1)
        self.assertEqual(self.logged("[TF-CRTV] active="), 2)

    def test_radio_failure_keeps_player_and_enemies(self):
        self.world.spawnEnemy("Fearful_1", UE_X + 1000, UE_Y, UE_Z)
        self.tune(True, 0.23, 1.0, 2)
        self.radio["fail"] = True
        self.world.tick(10)
        self.world.tick(10)
        telemetry = self.telemetry()
        self.assertIsNone(telemetry["crtv"])
        self.assertAlmostEqual(telemetry["player"]["x"], PHONE_X, delta=0.01)
        self.assertEqual(self.enemy_ids(), ["Fearful_1"])
        self.assertEqual(self.logged("[TF-CRTV] read error"), 1)
        self.assertEqual(self.logged("crtv update error"), 1)


class EnemyCrtvTest(ModTest):
    def setUp(self):
        super().setUp()
        self.world.enterGameplay(UE_X, UE_Y, UE_Z)
        self.radio = self.world.radio
        self.near = self.world.spawnEnemy("Fearful_1", UE_X + 1000, UE_Y, UE_Z)
        self.far = self.world.spawnEnemy("Enraged_2", UE_X, UE_Y + 2500, UE_Z)
        self.world.tick(1)

    def raise_crtv(self, tuned_to=None):
        self.radio["active"] = True
        self.radio["frequency"] = 0.23
        if tuned_to is not None:
            self.radio["cachedHighestSignalStrength"] = 1.0
            self.radio["cachedHighestStrengthSignalType"] = 2
            self.radio["highestSource"] = tuned_to["RadioStaticSource"]

    def sent(self):
        return {e["id"]: (e["detected"], e["tuned"]) for e in self.telemetry()["enemies"]}

    def test_lowered_crtv_detects_nothing(self):
        self.world.setSourceValue(self.near, "StaticSourceStrengthMap", 1.0)
        self.world.tick(1)
        self.assertEqual(self.sent(), {"Fearful_1": (False, False), "Enraged_2": (False, False)})

    def test_raised_crtv_detects_enemies_with_signal(self):
        self.raise_crtv()
        self.world.setSourceValue(self.near, "StaticSourceStrengthMap", 1.0)
        self.world.tick(1)
        self.assertEqual(self.sent(), {"Fearful_1": (True, False), "Enraged_2": (False, False)})

    def test_tuned_enemy_is_the_strongest_source(self):
        self.raise_crtv(tuned_to=self.near)
        self.world.setSourceValue(self.near, "StaticSourceStrengthMap", 1.0)
        self.world.setSourceValue(self.far, "StaticSourceStrengthMap", 0.4)
        self.world.tick(1)
        self.assertEqual(self.sent(), {"Fearful_1": (True, True), "Enraged_2": (True, False)})

    def test_signal_and_channel_are_sent(self):
        self.raise_crtv()
        self.world.setSourceValue(self.near, "StaticSourceStrengthMap", 0.6)
        self.world.tick(1)
        sent = {e["id"]: (e["signal"], e["channel"]) for e in self.telemetry()["enemies"]}
        self.assertEqual(sent, {"Fearful_1": (0.6, 0.23), "Enraged_2": (0.0, 0.23)})

    def test_a_new_enemy_is_logged_once_with_its_channel_and_its_tolerance_sent(self):
        for _ in range(3):
            self.world.tick(10)
        self.assertEqual(self.logged("[TF-ENEMY] new BP_TestAIAgent_C"), 2)
        self.assertEqual(self.logged("crtv channel 0.230"), 2)
        self.assertEqual([e["tolerance"] for e in self.telemetry()["enemies"]], [0.02, 0.02])  # clear within
        self.assertEqual([e["toleranceOuter"] for e in self.telemetry()["enemies"]], [0.05, 0.05])  # comes in within

    def test_enemy_without_signal_source_is_still_tracked(self):
        self.world.spawnEnemy("Crazed_3", UE_X - 1000, UE_Y, UE_Z, True)
        self.raise_crtv()
        self.world.tick(1)
        self.assertEqual(self.sent()["Crazed_3"], (False, False))
        crazed = next(e for e in self.telemetry()["enemies"] if e["id"] == "Crazed_3")
        self.assertIsNone(crazed["channel"])
        self.assertEqual(self.logged("Crazed_3 has no RadioStaticSource"), 1)
        self.assertEqual(self.logged("read error"), 0)

    def test_unreadable_radio_maps_keep_enemy_positions(self):
        self.world.manager["StaticSourceStrengthMap"]["broken"] = True
        self.raise_crtv()
        self.world.tick(10)
        self.world.tick(10)
        self.assertEqual(self.sent(), {"Fearful_1": (False, False), "Enraged_2": (False, False)})
        self.assertEqual(self.logged("[TF-CRTV] per-enemy read error"), 1)


class WaypointSignalTest(ModTest):
    """CRTV waypoints: reach 2000-10000 cm in the fake; only the ones the waypoint manager has active go out."""

    def setUp(self):
        super().setUp()
        self.world.enterGameplay(UE_X, UE_Y, UE_Z)
        self.clinic = self.world.addWaypoint("Clinic", UE_X + 1000, UE_Y, UE_Z, 0.15, True)
        self.world.addWaypoint("Later", UE_X + 6000, UE_Y, UE_Z, 0.62, False, True)
        self.world.tick(1)

    def signals(self):
        return {s["id"]: s for s in self.telemetry()["signals"]}

    def test_only_active_waypoints_are_sent(self):
        clinic = self.signals()["Clinic"]
        self.assertEqual(list(self.signals()), ["Clinic"])
        self.assertEqual((clinic["kind"], clinic["channel"], clinic["tolerance"], clinic["toleranceOuter"]),
                         ("waypoint", 0.15, 0.02, 0.04))
        self.assertNotIn("video", clinic)  # the phone shows the game's own screen
        self.assertAlmostEqual(clinic["y"], PHONE_Y + 10, delta=0.01)
        self.assertEqual((clinic["rangeSignal"], clinic["signal"], clinic["tuned"], clinic["found"]), (1.0, 0.0, False, False))

    def test_waypoint_that_becomes_active_shows_up(self):
        self.world.setWaypointActive("Later", True)
        self.world.tick(1)
        later = self.signals()["Later"]
        self.assertEqual((later["kind"], later["rangeSignal"]), ("signal_object", 0.5))  # 60 m of 20-100 m
        self.assertEqual(self.logged("[TF-SIGNAL] 2 waypoints, 2 active: Clinic, Later"), 1)

    def test_tuned_in_the_game(self):
        radio = self.world.radio
        for key, value in (("active", True), ("frequency", 0.15), ("cachedHighestSignalStrength", 1.0),
                           ("cachedHighestStrengthSignalType", 1), ("highestWaypoint", self.clinic)):
            radio[key] = value
        self.clinic["untuned"] = 0.7
        self.clinic["fullyTuned"] = True
        self.world.tick(1)
        clinic = self.signals()["Clinic"]
        self.assertEqual((clinic["signal"], clinic["tuned"], clinic["found"]), (0.7, True, True))

    def test_waypoint_info_is_logged_once(self):
        self.world.tick(5)
        self.assertEqual(self.logged("[TF-SIGNAL] waypoint Clinic: channel 0.150 tolerance 0.020/0.040 "
                                     "reach 2000-10000 cm"), 1)

    def test_a_reach_without_cutoff_is_full_everywhere(self):
        # Return_to_Clinic: 1000 to -1 cm, and the game gives it full strength.
        self.clinic["distanceSignalFalloffBegin"], self.clinic["distanceSignalCutoff"] = 1000, -1
        self.world.movePlayer(UE_X - 50000, UE_Y, UE_Z)
        self.world.tick(1)
        self.assertEqual(self.signals()["Clinic"]["rangeSignal"], 1.0)

    def test_templates_and_second_copies_are_not_sent(self):
        self.world.addWaypointTemplate("Clinic", 0.15)
        self.world.addWaypointTemplate("", -1)
        self.world.addWaypoint("Clinic", UE_X + 3000, UE_Y, UE_Z, 0.15, True)
        self.world.tick(1)
        self.assertEqual([s["id"] for s in self.telemetry()["signals"]], ["Clinic"])
        self.assertAlmostEqual(self.signals()["Clinic"]["y"], PHONE_Y + 10, delta=0.01)  # the first one placed


class NeedleColourTest(ModTest):
    """The in-game dial's colours for signals, read once from WBP_CRTV_Needles for the phone's dial."""

    def test_read_at_the_level_start_and_sent(self):
        self.world.enterGameplay(UE_X, UE_Y, UE_Z)
        self.world.tick(1)
        self.assertEqual(self.telemetry()["crtv"]["needleColours"],
                         {"enemy": [1, 0.05, 0.02], "waypoint": [0.1, 0.35, 1], "undiscovered": [0.8, 0.8, 0.75]})
        self.assertEqual(self.logged("[TF-CRTV] needle colours: {"), 1)

    def test_looked_up_once(self):
        # Lookups by path take 14-21 ms here; one, at the first level start, is enough.
        for _ in range(2):
            self.world.enterGameplay(UE_X, UE_Y, UE_Z)
            self.world.tick(3)
            self.world.enterMenu()
            self.world.tick(1)
        self.assertEqual(self.world.needleLookups, 1)


class NeedlesNotLoadedTest(ModTest):
    options = {"noNeedles": True}

    def test_null_and_tried_again_at_the_next_level_start(self):
        self.world.enterGameplay(UE_X, UE_Y, UE_Z)
        self.world.tick(2)
        self.assertIsNone(self.telemetry()["crtv"]["needleColours"])
        self.assertEqual(self.logged("[TF-CRTV] needle colours: WBP_CRTV_Needles not loaded yet"), 1)
        self.world.enterMenu()
        self.world.tick(1)
        self.world.enterGameplay(UE_X, UE_Y, UE_Z)
        self.world.tick(2)
        self.assertEqual(self.world.needleLookups, 2)


class NoWaypointManagerTest(ModTest):
    options = {"noWaypointManager": True}

    def test_nothing_is_sent_and_it_is_logged(self):
        self.world.enterGameplay(UE_X, UE_Y, UE_Z)
        self.world.addWaypoint("Clinic", UE_X + 1000, UE_Y, UE_Z, 0.15, True)
        self.world.tick(2)
        self.assertEqual(self.telemetry()["signals"], [])
        self.assertEqual(self.logged("(no URadioWaypointManager)"), 1)


class RangeSignalTest(ModTest):
    """What the phone's own scanner uses: signal from distance and the enemy's falloff (1500-4000 cm
    outdoors, 1000-2500 cm indoors in the fake), with the in-game CRTV lowered."""

    def setUp(self):
        super().setUp()
        self.world.enterGameplay(UE_X, UE_Y, UE_Z)
        for name, metres in (("Near_1", 10), ("Mid_2", 27.5), ("Far_3", 45)):
            self.world.spawnEnemy(name, UE_X + metres * 100, UE_Y, UE_Z)

    def sent(self):
        return {e["id"]: e for e in self.telemetry()["enemies"]}

    def test_outdoors(self):
        self.world.tick(1)
        sent = self.sent()
        self.assertEqual({k: v["rangeSignal"] for k, v in sent.items()}, {"Near_1": 1.0, "Mid_2": 0.5, "Far_3": 0.0})
        self.assertEqual((sent["Near_1"]["channel"], sent["Near_1"]["tolerance"]), (0.23, 0.02))
        self.assertFalse(sent["Near_1"]["detected"])  # the in-game CRTV stays lowered

    def test_indoors_reaches_less(self):
        self.world.player["bInInterior"] = True
        self.world.movePlayer(UE_X + 1000, UE_Y, UE_Z)  # Mid_2 is now 17.5 m away
        self.world.tick(1)
        self.assertEqual(self.sent()["Mid_2"]["rangeSignal"], 0.5)


class PhoneCommandTest(ModTest):
    phone_mode = None  # what the phone says it is in, every second (seconds)

    def seconds(self, n):
        """n seconds on the game's clock, the phone saying its mode every second meanwhile, as it does."""
        for _ in range(n):
            if self.phone_mode:
                self.mode(*self.phone_mode)
            self.world.tick(1)  # one tick is a second on the game's clock

    def say(self, selector, on_monitor=False):
        """The phone switches to `selector` (in VIEW, the monitor showing the CRTV or not), and keeps saying it."""
        self.phone_mode = (selector, on_monitor)
        self.mode(selector, on_monitor)

    def command(self, seq, active, frequency, animate=False):
        # Written the way bridge.py writes it: active only ever switches the CRTV on.
        switch = {"active": True, "animate": animate} if active else {}
        self.commands_file.write_text(json.dumps({**switch, "frequency": frequency, "seq": seq}))

    def play(self, command_before_load=True):
        if command_before_load:
            self.command(100, True, 0.5)  # left over from an earlier session
        self.world.enterGameplay(UE_X, UE_Y, UE_Z)
        self.radio = self.world.radio
        self.world.tick(3)

    def state(self, flag, using, alpha):
        """What the character keeps: the radio's active mode, IsUsingRadio and the animation's RadioAlpha (the hands)."""
        self.radio["active"] = flag
        self.world.player["IsUsingRadio"] = using
        self.world.anim["RadioAlpha"] = alpha

    def requests(self):
        return list(self.radio["requests"].values())

    def test_leftover_command_is_not_applied_on_load(self):
        self.play()
        self.assertFalse(self.radio["active"])
        self.assertEqual(self.logged("phone command"), 0)

    def test_new_command_raises_and_tunes_the_crtv(self):
        self.play()
        self.command(101, True, 0.23)
        self.world.tick(1)
        self.assertEqual((self.radio["active"], self.radio["frequency"]), (True, 0.23))
        self.assertEqual(self.logged("[TF-CRTV] phone command: active=true (silent) frequency=0.230"), 1)

    def test_a_command_is_applied_once(self):
        self.play()
        self.command(101, True, 0.23)
        self.world.tick(1)
        self.radio["active"] = False  # the player lowers it in the game afterwards
        self.world.tick(10)
        self.assertFalse(self.radio["active"])
        self.assertEqual(self.logged("phone command"), 1)

    def test_silently_by_default_and_put_away_silently_in_av_out(self):
        self.play()
        self.say("VIEW")
        self.command(101, True, 0.23)
        self.seconds(1)
        self.assertTrue(self.radio["active"])
        self.assertEqual(self.requests(), [])  # no request: nothing shows on the monitor
        self.assertEqual(self.logged("phone command: active=true (silent)"), 1)
        self.say("AV_OUT")
        self.seconds(1)
        self.assertEqual((self.radio["active"], self.requests()), (False, []))
        self.assertEqual(self.radio["frequency"], 0.23)  # the dial stays where the phone left it
        self.assertEqual(self.logged("[TF-CRTV] putting the phone's CRTV away: the phone is in AV OUT"), 1)

    def test_with_the_animation_up_and_down_through_the_request_the_radio_button_runs(self):
        self.play()
        self.say("VIEW", on_monitor=True)
        self.command(101, True, 0.23, animate=True)
        self.seconds(1)
        self.assertEqual(self.requests(), ["on"])  # RequestRadioON: Bill raises it with his animation
        self.assertEqual(self.logged("phone command: active=true (animated)"), 1)
        self.say("AV_OUT")
        self.seconds(1)
        # Plain first, as the radio button lowers it: radio and hands go down together.
        self.assertEqual(self.requests(), ["on", "off"])
        self.assertFalse(self.radio["active"])

    def test_a_crtv_the_player_raised_is_not_the_phones_to_put_away(self):
        self.play()
        self.say("VIEW")
        self.state(True, True, 1.0)    # raised with the radio button
        self.command(101, True, 0.23)  # the phone, a step behind, asks for it on
        self.seconds(1)
        self.say("AV_OUT")
        self.seconds(3)
        self.assertEqual((self.radio["active"], self.requests()), (True, []))

    def test_once_down_a_crtv_the_phone_switched_on_is_no_longer_the_phones(self):
        self.play()
        self.say("VIEW")
        self.command(101, True, 0.23)
        self.seconds(1)
        self.radio["active"] = False  # put away with the radio button...
        self.seconds(1)
        self.state(True, True, 1.0)   # ...and raised with it again
        self.say("AV_OUT")
        self.seconds(3)
        self.assertEqual((self.radio["active"], self.requests()), (True, []))

    def test_a_raise_after_the_phone_left_view_is_left_out(self):
        self.play()
        self.say("AV_OUT")
        self.command(101, True, 0.23)
        self.seconds(1)
        self.assertFalse(self.radio["active"])
        self.assertEqual(self.radio["frequency"], 0.23)  # the dial still moves
        self.assertEqual(self.logged("[TF-CRTV] the phone isn't in VIEW: its CRTV stays as it is"), 1)

    def test_a_phone_that_stops_saying_its_mode_has_its_crtv_put_away(self):
        # Its page closed without a word, it fell asleep, or the Wi-Fi went.
        self.play()
        self.say("VIEW")
        self.command(101, True, 0.23)
        self.seconds(3)
        self.phone_mode = None
        self.seconds(4)
        self.assertTrue(self.radio["active"])  # a moment's silence is nothing
        self.seconds(2)
        self.assertFalse(self.radio["active"])
        self.assertEqual(self.logged("[TF-CRTV] putting the phone's CRTV away: the phone went quiet"), 1)

    def test_wanted_on_the_monitor_a_silent_crtv_is_switched_off_for_the_phone_to_raise_it(self):
        self.play()
        self.say("VIEW")
        self.command(101, True, 0.23)
        self.seconds(1)
        self.say("VIEW", on_monitor=True)
        self.seconds(1)
        self.assertEqual((self.radio["active"], self.requests()), (False, []))  # off at once, the character left alone
        self.assertEqual(self.logged("putting the phone's CRTV away: the phone wants it shown the other way"), 1)
        self.command(102, True, 0.23, animate=True)  # the phone, once the game reports it down
        self.seconds(1)
        self.assertEqual(self.requests(), ["on"])

    def test_no_longer_wanted_on_the_monitor_a_raised_crtv_is_lowered_with_the_animation(self):
        self.play()
        self.say("VIEW", on_monitor=True)
        self.command(101, True, 0.23, animate=True)
        self.seconds(1)
        self.say("VIEW")
        self.seconds(1)
        self.assertEqual((self.radio["active"], self.requests()), (False, ["on", "off"]))  # never the silent flip
        self.command(102, True, 0.23)
        self.seconds(1)
        self.assertEqual((self.radio["active"], self.requests()), (True, ["on", "off"]))

    def test_a_lowering_waits_for_the_raise_to_finish(self):
        # Asked while the radio is still coming up, the game drops it or half does it: the radio gone, the hands lagging.
        self.play()
        self.say("VIEW", on_monitor=True)
        self.radio["ignoreRequests"] = True
        self.command(101, True, 0.23, animate=True)
        self.seconds(1)
        self.state(False, True, 0.4)  # coming up
        self.say("AV_OUT")
        self.seconds(2)
        self.assertEqual(self.requests(), ["on"])
        self.radio["ignoreRequests"] = False
        self.state(True, True, 1.0)   # up
        self.seconds(1)
        self.assertEqual(self.requests(), ["on", "off"])  # plain: radio and hands go down together
        self.assertFalse(self.radio["active"])

    def test_a_raise_waits_for_the_lowering_to_finish(self):
        self.play()
        self.say("VIEW", on_monitor=True)
        self.state(False, False, 0.5)  # the hands are still going down
        self.command(101, True, 0.23, animate=True)
        self.seconds(2)
        self.assertEqual(self.requests(), [])
        self.state(False, False, 0.0)
        self.seconds(1)
        self.assertEqual(self.requests(), ["on"])

    def test_back_in_view_before_it_is_put_away_the_crtv_stays_the_phones(self):
        self.play()
        self.say("VIEW", on_monitor=True)
        self.command(101, True, 0.23, animate=True)
        self.seconds(1)
        self.radio["ignoreRequests"] = True  # the character is busy
        self.say("AV_OUT")
        self.seconds(1)
        self.assertEqual(self.requests(), ["on", "off"])
        self.say("VIEW", on_monitor=True)
        self.seconds(6)
        self.assertEqual(self.requests(), ["on", "off"])  # no more lowering, and nothing to raise
        self.radio["ignoreRequests"] = False
        self.say("AV_OUT")
        self.seconds(1)
        self.assertEqual(self.requests(), ["on", "off", "off"])  # still the phone's: AV OUT puts it away
        self.assertFalse(self.radio["active"])

    def test_back_in_view_while_it_is_still_coming_up_it_comes_up_and_stays_the_phones(self):
        self.play()
        self.say("VIEW", on_monitor=True)
        self.radio["ignoreRequests"] = True
        self.command(101, True, 0.23, animate=True)
        self.seconds(1)
        self.state(False, True, 0.4)  # coming up
        self.say("AV_OUT")
        self.seconds(1)
        self.say("VIEW", on_monitor=True)
        self.seconds(1)
        self.state(True, True, 1.0)
        self.seconds(3)
        self.assertEqual(self.requests(), ["on"])  # up, as the phone last wanted
        self.radio["ignoreRequests"] = False
        self.say("AV_OUT")
        self.seconds(1)
        self.assertEqual(self.requests(), ["on", "off"])

    def test_a_lowering_the_game_ignores_is_asked_again_and_then_forced(self):
        # Forced, the radio goes at once but the hands lag, so the plain request is the one asked first. The phone
        # saying AV OUT again meanwhile doesn't start the asking over.
        self.play()
        self.say("VIEW", on_monitor=True)
        self.command(101, True, 0.23, animate=True)
        self.seconds(1)
        self.radio["ignoreRequests"] = True
        self.say("AV_OUT")
        self.seconds(9)
        self.assertEqual(self.requests(), ["on", "off", "off", "off (forced)", "off (forced)", "off (forced)"])
        self.seconds(5)
        self.assertEqual(self.logged("the character didn't carry out the phone's request: dropped"), 1)
        asked = len(self.requests())
        self.seconds(6)
        self.assertEqual(len(self.requests()), asked)  # and the asking stopped: the CRTV is left to the game

    def test_a_paused_game_is_asked_again_once_it_runs_and_nothing_is_given_up_meanwhile(self):
        self.play()
        self.say("VIEW", on_monitor=True)
        self.command(101, True, 0.23, animate=True)
        self.seconds(1)
        self.radio["ignoreRequests"] = True
        self.say("AV_OUT")
        self.seconds(1)
        self.world.paused = True  # the pause menu: the world's clock stands still
        self.seconds(20)
        self.assertEqual(self.requests(), ["on", "off"])
        self.world.paused = False
        self.seconds(3)
        self.assertEqual(self.requests(), ["on", "off", "off"])
        self.assertEqual(self.logged("dropped"), 0)

    def test_a_build_without_the_characters_state_is_asked_at_once(self):
        self.play()
        self.world.player["IsUsingRadio"] = None  # nothing to wait for
        self.say("VIEW", on_monitor=True)
        self.command(101, True, 0.23, animate=True)
        self.seconds(1)
        self.assertEqual(self.requests(), ["on"])
        self.say("AV_OUT")
        self.seconds(2)
        self.assertEqual(self.requests(), ["on", "off"])

    def test_an_animated_raise_is_not_asked_for_when_the_radio_is_up_already(self):
        # Putting it away to raise it the other way is the mod's, by the phone's mode: the game's state settles in
        # between.
        self.play()
        self.command(101, True, 0.23)
        self.world.tick(1)
        self.command(102, True, 0.23, animate=True)
        self.world.tick(1)
        self.assertEqual(self.requests(), [])

    def test_an_animated_request_the_game_hasnt_acted_on_yet_is_not_repeated_at_once(self):
        self.play()
        self.radio["ignoreRequests"] = True  # the animation takes a moment: active stays down meanwhile
        self.command(101, True, 0.23, animate=True)
        self.world.tick(1)
        self.command(102, True, 0.23, animate=True)  # the phone says it again
        self.world.tick(1)
        self.assertEqual(self.requests(), ["on"])
        self.command(103, True, 0.23, animate=True)
        self.world.tick(2)  # a moment later it is asked again
        self.assertEqual(self.requests(), ["on", "on"])

    def test_without_active_only_the_dial_moves_up_or_down(self):
        self.play()
        self.commands_file.write_text(json.dumps({"frequency": 0.3, "seq": 101}))  # tuned behind the scenes
        self.world.tick(1)
        self.assertEqual((self.radio["active"], self.radio["frequency"]), (False, 0.3))  # still down
        self.radio["active"] = True  # raised with the radio button: it comes up where the phone left it
        self.commands_file.write_text(json.dumps({"frequency": 0.35, "seq": 102}))
        self.world.tick(1)
        self.assertEqual((self.radio["active"], self.radio["frequency"]), (True, 0.35))
        self.assertEqual(self.logged("[TF-CRTV] phone command: active=as is"), 2)
        self.assertEqual(self.logged("didn't take"), 0)

    def test_a_dial_the_game_doesnt_take_is_logged(self):
        self.play()
        self.radio["SetTunedFrequency"] = lambda radio, frequency: None  # the game holds on to its own
        self.command(101, True, 0.23)
        self.world.tick(1)
        self.assertEqual(self.logged("[TF-CRTV] the game didn't take the phone's dial 0.230: it has 0.000"), 1)

    def test_dial_is_clamped(self):
        self.play(command_before_load=False)
        self.command(101, True, 0.5)  # first file seen: baseline
        self.world.tick(1)
        self.command(102, True, 1.7)
        self.world.tick(1)
        self.assertEqual(self.radio["frequency"], 1)

    def test_garbage_is_ignored(self):
        self.play()
        for garbage in ('{"seq": 101, "active": ', '{"active": false, "frequency": 0.3, "seq": 102}'):
            self.commands_file.write_text(garbage)
            self.world.tick(3)
        self.assertEqual((self.radio["active"], self.radio["frequency"]), (False, 0))
        self.assertEqual(self.logged("error"), 0)

    def test_commands_wait_for_gameplay(self):
        self.world.enterMenu()
        self.command(100, True, 0.5)
        self.world.tick(3)
        self.assertEqual(self.logged("phone command"), 0)


class SteeringTest(ModTest):
    """The phone sends its own heading and tilt; the player turns and looks up or down by as much as the phone did."""

    def setUp(self):
        super().setUp()
        self.steer_file = self.commands_file.with_name("townfall-companion-steer.json")
        self.seq = int(time.time() * 1000)

    def steer(self, heading, age=0.0, after_ms=100, pitch=None):
        # seq is the bridge's clock: after_ms since the phone's heading before, age seconds stale by now.
        self.seq += after_ms
        command = {"yaw": heading} if pitch is None else {"yaw": heading, "pitch": pitch}
        self.steer_file.write_text(json.dumps({**command, "seq": self.seq - int(age * 1000)}))

    def play(self):
        self.steer(10, age=60)  # left over from an earlier session
        self.world.enterGameplay(UE_X, UE_Y, UE_Z, 40)
        self.world.tick(2)

    def test_leftover_steering_is_not_applied(self):
        self.play()
        self.assertEqual(tuple(self.world.controlRotation()), (-5, 40))

    def test_the_player_turns_as_far_as_the_phone_and_keeps_pitch(self):
        self.play()
        self.steer(350)  # where the phone points doesn't matter, only how far it turns
        self.world.tick(1)
        self.assertEqual(tuple(self.world.controlRotation()), (-5, 40))
        self.steer(20)   # 30 degrees clockwise, across north
        self.world.tick(1)
        self.assertEqual(tuple(self.world.controlRotation()), (-5, 70))
        self.assertEqual(self.logged("[TF-PLAYER] turned by the phone"), 1)

    def test_tilting_the_phone_looks_up_and_down_as_far_as_the_phone_tilts(self):
        self.play()
        self.steer(350, pitch=10)  # how far the phone is tilted doesn't matter, only how far it tilts
        self.world.tick(1)
        self.steer(350, pitch=25)  # 15 degrees up on the phone
        self.world.tick(1)
        self.assertEqual(tuple(self.world.controlRotation()), (10, 40))
        self.steer(20, pitch=-5)   # turned 30 clockwise, tilted 30 down
        self.world.tick(1)
        self.assertEqual(tuple(self.world.controlRotation()), (-20, 70))

    def test_looking_up_stops_short_of_straight_up(self):
        self.play()
        self.steer(350, pitch=-80)
        self.world.tick(1)
        self.steer(350, pitch=89)  # 169 degrees up from -5
        self.world.tick(1)
        self.assertEqual(self.world.controlRotation()[0], 80)

    def test_a_phone_page_from_before_turns_without_tilting(self):
        self.play()
        self.steer(350, pitch=10)
        self.world.tick(1)
        self.steer(20)  # no tilt in it
        self.world.tick(1)
        self.assertEqual(tuple(self.world.controlRotation()), (-5, 70))

    def test_steering_the_crtv_looks_up_and_down_with_the_camera(self):
        self.play()
        look = self.commands_file.with_name("townfall-companion-look.json")
        packet_file = self.commands_file.with_name("townfall-companion-native-look.bin")
        self.steer(350, pitch=0)
        self.world.tick(1)
        self.steer(350, pitch=20)  # the camera from -5 to 15 degrees up
        self.world.tick(1)
        look.write_text(json.dumps({"on": True, "yaw": 0, "pitch": 33, "seq": self.seq + 10}))  # the phone's own tilt
        self.world.tick(1)
        self.assertEqual(struct.unpack("<8sddQQ", packet_file.read_bytes())[2], 15)  # the camera's, not the phone's
        self.steer(350, pitch=30)  # 10 further up: the CRTV goes along at once
        self.world.tick(1)
        self.assertEqual(struct.unpack("<8sddQQ", packet_file.read_bytes())[1:4], (0, 25, 1))

    def test_a_tiny_heading_written_with_an_exponent(self):
        self.play()
        self.steer(20)
        self.world.tick(1)
        self.steer(3.2e-05)  # json.dumps writes it as 3.2e-05; read as 3.2 it would turn 16.8 degrees
        self.world.tick(1)
        self.assertAlmostEqual(self.world.controlRotation()[1], 20, places=3)

    def test_mouse_and_phone_add_up(self):
        self.play()
        self.steer(100)
        self.world.tick(1)
        self.world.setYaw(200)  # the player turns with the mouse meanwhile
        self.steer(90)
        self.world.tick(1)
        self.assertEqual(self.world.controlRotation()[1], 190)

    def test_after_a_pause_the_phone_starts_over(self):
        self.play()
        self.steer(100)
        self.world.tick(1)
        self.steer(160, after_ms=5000)
        self.world.tick(1)
        self.assertEqual(self.world.controlRotation()[1], 40)

    def test_stale_steering_is_ignored(self):
        self.play()
        self.steer(100)
        self.world.tick(1)
        self.steer(150, age=10)
        self.world.tick(1)
        self.assertEqual(self.world.controlRotation()[1], 40)

    def test_missing_control_pitch_does_not_break_yaw_steering(self):
        self.play()
        self.steer(100)
        self.world.tick(1)
        self.world.dropControlPitch()
        self.steer(130)
        self.world.tick(1)
        self.assertEqual(self.world.controlRotation()[1], 70)
        self.assertEqual(self.logged("[TF-PLAYER] turned by the phone"), 1)

    def test_a_turn_the_game_refuses_is_logged(self):
        self.play()
        self.world.lockCamera()
        self.steer(100)
        self.world.tick(1)
        self.steer(130)
        self.world.tick(1)
        self.assertEqual(self.logged("[TF-PLAYER] turn by 30.0 didn't take: yaw 40.0, wanted 70.0"), 1)


class FineTuneConfirmTest(ModTest):
    """The phone's D-pad centre: the player's press in the fine-tune mini-game, logged with what became of it."""

    def setUp(self):
        super().setUp()
        self.confirm_file = self.commands_file.with_name("townfall-companion-confirm.json")

    def press(self, age=0.0):
        self.confirm_file.write_text(json.dumps({"seq": int((time.time() - age) * 1000)}))

    def play(self, raised=True):
        self.world.enterGameplay(UE_X, UE_Y, UE_Z)
        self.world.radio["active"] = raised
        self.world.tick(2)

    def test_first_press_counts_when_there_was_no_file(self):
        self.play()
        self.press()
        self.world.tick(3)
        self.assertEqual(self.world.radio["confirms"], 1)
        self.assertEqual(self.logged("[TF-CRTV] phone pressed the D-pad's centre: confirm"), 1)

    def test_a_leftover_press_is_not_replayed(self):
        self.press(age=60)
        self.play()
        self.world.tick(3)
        self.assertEqual(self.world.radio["confirms"], 0)

    def test_a_late_press_is_dropped_and_logged(self):
        self.play()
        self.press(age=5)
        self.world.tick(1)
        self.assertEqual(self.world.radio["confirms"], 0)
        self.assertEqual(self.logged("too late, dropped"), 1)

    def test_the_phones_mode_is_logged_when_it_changes(self):
        self.play()
        for _ in range(3):  # said every couple of seconds
            self.mode("VIEW")
            self.world.tick(1)
        self.mode("VIEW", on_monitor=True)
        self.world.tick(1)
        self.mode("VIEW", on_monitor=True, mini_game_shown=True)
        self.world.tick(1)
        self.mode("AV_OUT")
        self.world.tick(1)
        for settings in ("not shown, in the mini-game: hidden", "raised, in the mini-game: hidden",
                         "raised, in the mini-game: shown"):
            self.assertEqual(self.logged(f"[TF-CRTV] phone switched to VIEW (on the monitor: {settings})"), 1)
        self.assertEqual(self.logged("[TF-CRTV] phone switched to AV_OUT"), 1)

    def test_a_press_without_a_raised_crtv_is_logged(self):
        self.play(raised=False)
        self.press()
        self.world.tick(1)
        self.assertEqual(self.world.radio["confirms"], 0)
        self.assertEqual(self.logged("[TF-CRTV] phone pressed the D-pad's centre: no raised CRTV, ignored"), 1)


class GameSoundTest(ModTest):
    """The phone plays the CRTV's sound; the game's goes quiet (tf_native.dll, in its mixer) while the phone keeps
    asking."""

    def setUp(self):
        super().setUp()
        self.audio_file = self.commands_file.with_name("townfall-companion-audio.json")
        self.world.enterGameplay(UE_X, UE_Y, UE_Z)
        self.world.tick(2)
        self.seq = int(time.time() * 1000)

    def ask(self, mute):
        self.seq += 100  # the bridge's clock, one request later
        self.audio_file.write_text(json.dumps({"muteGame": mute, "seq": self.seq}))

    def told(self):
        """What the DLL was last told (tf_native_sound's packet: 1 quiet, 0 back on), and how often it was told."""
        magic, mute = struct.unpack("<8sQ", (self.tmp / "townfall-companion-native-sound.bin").read_bytes())
        self.assertEqual(magic, b"TFSOUND1")
        return mute, self.world.native["sounds"]

    def test_quiet_in_the_game_while_the_phone_asks_and_the_dll_told_again_every_2_s(self):
        self.ask(True)
        self.world.tick(2)
        self.assertEqual(self.told(), (1, 1))
        self.assertTrue(self.telemetry()["audio"]["gameSoundOff"])
        self.assertEqual(self.logged("[TF-AUDIO] game CRTV sound off, the phone plays it"), 1)
        self.ask(True)  # the phone says it again
        self.world.tick(1)
        self.assertEqual(self.told(), (1, 1))
        self.world.tick(1)
        self.assertEqual(self.told(), (1, 2))  # the DLL lets the game's sound back 6 s after its last word

    def test_back_on_when_the_phone_stops_asking(self):
        self.ask(True)
        for _ in range(7):
            self.world.tick(1)
        self.assertEqual(self.told()[0], 0)
        self.assertFalse(self.telemetry()["audio"]["gameSoundOff"])
        self.assertEqual(self.logged("[TF-AUDIO] game CRTV sound back on (the phone stopped asking)"), 1)

    def test_back_on_at_once_when_the_phone_says_so(self):
        self.ask(True)
        self.world.tick(1)
        self.ask(False)
        self.world.tick(1)
        self.assertEqual(self.told()[0], 0)
        self.assertEqual(self.logged("[TF-AUDIO] game CRTV sound back on"), 1)


class CutsceneTest(ModTest):
    """The cutscene playing: the phone doesn't raise the CRTV over one."""

    def setUp(self):
        super().setUp()
        self.world.enterGameplay(UE_X, UE_Y, UE_Z)
        self.world.tick(1)

    def cutscene(self):
        self.world.tick(1)
        return self.telemetry()["cutscene"]

    def test_none_while_no_cutscene_player_is_loaded(self):
        self.assertIsNone(self.cutscene())
        self.assertEqual(self.logged("error"), 0)

    def test_the_cutscene_playing(self):
        sequence = self.world.playSequence("LS_SearchingForSignals")
        self.assertEqual(self.cutscene(), {"sequence": "LS_SearchingForSignals"})
        self.assertEqual(self.logged("[TF-CUTSCENE] sequence: LS_SearchingForSignals"), 1)
        sequence["playing"] = False
        self.assertIsNone(self.cutscene())

    def test_no_lookups_by_path_after_the_level_start(self):
        # They cost 14-21 ms each in the game; the sequence players are caught as they are made instead.
        self.world.addWaypoint("Clinic", UE_X + 1000, UE_Y, UE_Z, 0.15, True)
        self.world.tick(2)
        before = self.world.staticFindCalls
        self.world.tick(5)
        self.world.playSequence("LS_TooLate")
        self.assertEqual(self.cutscene()["sequence"], "LS_TooLate")
        self.assertEqual(self.world.staticFindCalls, before)


class NoRadioRequestsTest(ModTest):
    options = {"noRadioRequests": True}

    def test_the_crtv_is_switched_without_the_animation_and_logged_once(self):
        commands = self.commands_file
        commands.write_text(json.dumps({"active": True, "frequency": 0.5, "seq": 100}))  # left over from an earlier session
        self.world.enterGameplay(UE_X, UE_Y, UE_Z)
        self.world.tick(3)
        self.mode("VIEW", on_monitor=True)
        commands.write_text(json.dumps({"active": True, "animate": True, "frequency": 0.5, "seq": 101}))
        self.world.tick(1)
        self.assertTrue(self.world.radio["active"])
        self.mode("AV_OUT")
        self.world.tick(2)
        self.assertFalse(self.world.radio["active"])
        self.assertEqual(self.logged("RequestRadioON/OFF failed"), 1)


class PerfTest(ModTest):
    def test_game_thread_time_is_logged_every_30_seconds(self):
        self.world.enterGameplay(UE_X, UE_Y, UE_Z)
        for _ in range(29):
            self.world.tick(10)
        self.assertEqual(self.logged("[TF-PERF]"), 0)
        for _ in range(2):
            self.world.tick(10)
        self.assertEqual(self.logged("[TF-PERF] last 30 s: 0.0 ms of game thread per second; "), 1)
        self.assertEqual(self.logged("sample 300 calls 0 ms (max 0 ms)"), 1)


class MissingEnemyClassTest(ModTest):
    options = {"noEnemyClass": True}

    def test_is_reported_and_level_enemies_still_tracked(self):
        self.assertEqual(self.logged("TownfallEnemyCharacter not found"), 1)
        self.world.spawnEnemy("Fearful_1", UE_X + 1000, UE_Y, UE_Z)
        self.world.enterGameplay(UE_X, UE_Y, UE_Z)
        self.world.tick(1)
        self.assertEqual(self.enemy_ids(), ["Fearful_1"])


class NativeTest(ModTest):
    """tf_native.lua tells tf_native.dll (faked here: its calls are counted) which texture is the CRTV's screen."""

    def setUp(self):
        super().setUp()
        self.beat(phones=1, fps=15)
        self.world.enterGameplay(UE_X, UE_Y, UE_Z)
        self.world.radio["active"] = True
        self.world.tick(1)

    def source(self):
        magic, texture, radio, fps, width, _ = struct.unpack(
            "<8sQQIIQ", (self.tmp / "townfall-companion-native-source.bin").read_bytes())
        self.assertEqual(magic, b"TFNATV03")
        self.assertEqual(radio, self.world.radio["address"] if self.world.radio["active"] else 0)
        return texture, fps, width

    def status(self, **fields):
        """The DLL's state, as it writes it."""
        (self.tmp / "townfall-companion-native.json").write_text(json.dumps(
            {"status": "rhi-source-identified", "captureStatus": "streaming", **fields}))

    def test_the_raised_crtvs_screen_texture_goes_to_the_dll(self):
        self.assertEqual(self.source(), (self.world.screenTexture["address"], 15, 640))
        self.assertEqual(self.world.native["inits"], 1)
        self.assertGreater(self.world.native["updates"], 0)
        self.assertEqual(self.logged("[TF-NATIVE] tf_native.dll loaded"), 1)

    def test_nothing_while_the_crtv_is_down_or_no_phone_watches(self):
        self.world.radio["active"] = False
        self.world.tick(1)
        self.assertEqual(self.source()[0], 0)
        self.world.radio["active"] = True
        self.beat(phones=0)
        self.world.tick(1)
        self.assertEqual(self.source()[:2], (0, 0))  # the DLL stops copying

    def test_a_texture_drawn_from_another_widget_is_not_taken(self):
        self.world.radar["Widget"] = self.world.radio  # valid, but not the CRTV's screen widget
        self.world.tick(1)
        self.assertEqual(self.source()[0], 0)

    def test_the_dlls_state_is_logged_when_it_changes(self):
        self.status()
        self.world.tick(3)
        self.assertEqual(self.logged("[TF-NATIVE] rhi-source-identified, capture streaming"), 1)

    def test_the_mini_game_goes_to_the_phone(self):
        self.assertIsNone(self.telemetry()["crtv"]["miniGame"])
        self.status(radio={"active": 1, "fineTuning": 1, "mode": 1, "stage": 2})
        self.world.tick(2)  # read by the native loop, sent with the next sample
        self.assertEqual(self.telemetry()["crtv"]["miniGame"], {"mode": 1, "stage": 2})

    def raise_by_phone(self, animate):
        self.commands_file.write_text(json.dumps({"active": True, "animate": animate, "frequency": 0.3,
                                                  "seq": int(time.time() * 1000)}))
        self.world.tick(1)

    MINI_GAME = {"active": 1, "fineTuning": 1, "mode": 1, "stage": 0}
    NO_MINI_GAME = {"active": 1, "fineTuning": 0, "mode": 0, "stage": 0}

    def test_in_view_by_default_the_crtv_and_the_hands_are_hidden_for_the_mini_game(self):
        # However the CRTV was raised: here with the radio button (setUp).
        prop, hands = self.world.radio["SpawnedRadio"], self.world.player["Mesh1P"]
        for was in (False, True):  # afterwards as the game had them
            with self.subTest(was_hidden=was):
                prop["bHidden"] = hands["bHiddenInGame"] = was
                self.mode("VIEW")
                self.status(radio=self.MINI_GAME)
                self.world.tick(2)
                self.assertTrue(prop["bHidden"])
                self.assertTrue(hands["bHiddenInGame"])
                self.assertFalse(hands["propagated"])  # the hands alone: what hangs on them keeps its own state
                self.mode("VIEW")
                self.status(radio=self.NO_MINI_GAME)
                self.world.tick(2)
                self.assertEqual((prop["bHidden"], hands["bHiddenInGame"]), (was, was))
        self.assertEqual(self.logged("[TF-CRTV] the CRTV and the hands holding it are hidden for the mini-game "
                                     "(the phone's setting)"), 2)
        self.assertEqual(self.logged("[TF-CRTV] the CRTV is back as it was"), 2)

    def test_shown_in_the_mini_game_or_in_av_out_the_crtv_and_the_hands_stay_in_view(self):
        for selector, shown in (("VIEW", True), ("AV_OUT", False)):
            with self.subTest(selector=selector):
                self.mode(selector, mini_game_shown=shown)
                self.status(radio=self.MINI_GAME)
                self.world.tick(2)
                self.assertFalse(self.world.radio["SpawnedRadio"]["bHidden"])
                self.assertFalse(self.world.player["Mesh1P"]["bHiddenInGame"])

    def test_the_mini_game_setting_is_its_own(self):
        self.mode("VIEW", on_monitor=True)  # raised on the monitor, still hidden in the mini-game
        self.status(radio=self.MINI_GAME)
        self.world.tick(2)
        self.assertTrue(self.world.radio["SpawnedRadio"]["bHidden"])

    def test_the_phones_setting_and_mode_show_or_hide_the_crtv_in_the_mini_game_at_once(self):
        prop = self.world.radio["SpawnedRadio"]
        self.mode("VIEW")
        self.status(radio=self.MINI_GAME)
        self.world.tick(2)
        for selector, shown, hidden in (("VIEW", True, False), ("VIEW", False, True), ("AV_OUT", False, False)):
            with self.subTest(selector=selector, shown=shown):
                self.mode(selector, mini_game_shown=shown)
                self.world.tick(1)
                self.assertIs(prop["bHidden"], hidden)

    def test_a_phone_gone_quiet_leaves_the_crtv_and_the_hands_as_the_game_has_them(self):
        # Switched off, out of Wi-Fi, the page closed without a word: the phone's settings no longer count.
        prop, hands = self.world.radio["SpawnedRadio"], self.world.player["Mesh1P"]
        self.mode("VIEW")
        self.status(radio=self.MINI_GAME)
        self.world.tick(2)
        self.assertTrue(prop["bHidden"] and hands["bHiddenInGame"])
        for _ in range(4):
            self.world.tick(1)
        self.assertTrue(prop["bHidden"], "a moment's silence is nothing")
        for _ in range(2):
            self.world.tick(1)
        self.assertEqual((prop["bHidden"], hands["bHiddenInGame"]), (False, False))  # mini-game still running
        self.assertEqual(self.logged("[TF-CRTV] the CRTV is back as it was"), 1)

    def test_the_way_the_phone_switched_it_on_changes_after_the_mini_game_not_in_it(self):
        self.world.radio["active"] = False
        self.mode("VIEW")
        self.raise_by_phone(animate=False)  # silently
        self.status(radio=self.MINI_GAME)
        self.world.tick(2)
        self.mode("VIEW", on_monitor=True)  # now to show on the monitor: the mini-game goes on
        self.world.tick(1)
        self.assertTrue(self.world.radio["active"])
        self.mode("VIEW", on_monitor=True)
        self.status(radio=self.NO_MINI_GAME)
        self.world.tick(2)
        self.assertFalse(self.world.radio["active"])  # after it: off, for the phone to raise it with the animation

    def test_the_game_sound_tap_logs_its_state_the_buses_once_and_which_carry_sound(self):
        (self.tmp / "townfall-companion-native-buses.txt").write_text(
            "bus:/\nbus:/PreMaster/SFX/CRTV (tapped)\nbus:/PreMaster/Dialogue (tapped)\n")
        self.status(audio=1, audioRate=0, audioBuses=0, audioLevels="")
        self.world.tick(2)
        self.assertEqual(self.logged("[TF-AUDIO] game sound tap: waiting for the CRTV's bus"), 1)
        self.assertEqual(self.logged("FMOD buses"), 0, "none listed yet")
        self.status(audio=3, audioRate=48000, audioBuses=3, audioLevels="CRTV 0.0000/0.0000, Dialogue 0.0000/0.0000")
        self.world.tick(2)
        self.assertEqual(self.logged("[TF-AUDIO] game sound tap: listening (48000 Hz)"), 1)
        self.assertEqual(self.logged("[TF-AUDIO] FMOD buses (3): bus:/, bus:/PreMaster/SFX/CRTV (tapped), "
                                     "bus:/PreMaster/Dialogue (tapped)"), 1)
        self.status(audio=3, audioRate=48000, audioBuses=3, audioLevels="CRTV 0.1200/0.5000, Dialogue 0.0004/0.0020")
        self.world.tick(2)
        self.assertEqual(self.logged("[TF-AUDIO] levels"), 0, "the levels are in the DLL's status, not the log")
        self.assertEqual(self.logged("FMOD buses"), 1)

    def test_the_mini_games_mode_and_stage_are_logged_as_they_change(self):
        self.status(radio={"active": 1, "fineTuning": 1, "mode": 0, "stage": 0})
        self.world.tick(2)
        self.status(radio={"active": 1, "fineTuning": 1, "mode": 1, "stage": 2})
        self.world.tick(2)
        self.status(radio={"active": 1, "fineTuning": 0, "mode": 0, "stage": 0})
        self.world.tick(2)
        for line in ("mini-game: standard, bar", "mini-game: advanced, stabilise image", "mini-game: off"):
            self.assertEqual(self.logged("[TF-NATIVE] " + line), 1, line)

    def test_the_centre_stores_the_signal_when_the_advanced_mini_game_waits_for_it(self):
        press = self.tmp / "townfall-companion-confirm.json"
        release = self.tmp / "townfall-companion-release.json"
        self.status(radio={"active": 1, "fineTuning": 1, "mode": 1, "stage": 4})
        self.world.tick(1)
        press.write_text(json.dumps({"seq": int(time.time() * 1000)}))
        self.world.tick(1)
        release.write_text(json.dumps({"seq": int(time.time() * 1000)}))
        self.world.tick(1)
        radio = self.world.radio
        self.assertEqual((radio["stores"], radio["storeReleases"], radio["confirms"]), (1, 1, 0))
        self.assertEqual(self.logged("[TF-CRTV] phone pressed the D-pad's centre: store signal"), 1)
        self.assertEqual(self.logged("[TF-CRTV] phone let go of the D-pad's centre: store signal released"), 1)

    def test_the_image_stages_are_logged_for_comparing_the_stick_and_the_phone(self):
        self.status(radio={"active": 1, "fineTuning": 1, "mode": 1, "stage": 0})
        self.world.tick(2)
        self.assertEqual(self.logged("[TF-ALIGN]"), 0)  # not in an image stage
        self.status(radio={"active": 1, "fineTuning": 1, "mode": 1, "stage": 2}, alignmentResult=1, alignmentCalls=5)
        self.world.tick(3)
        self.assertEqual(self.logged("[TF-ALIGN] stabilise: image at ?, ?;"), 1)  # once while nothing changes
        self.assertEqual(self.logged("phone commands 0, moves applied 5, last applied"), 1)

    def test_the_phones_look_goes_to_the_dll_as_a_packet(self):
        look = self.tmp / "townfall-companion-look.json"
        seq = int(time.time() * 1000)
        look.write_text(json.dumps({"on": False, "yaw": 0, "pitch": 0, "seq": seq}))
        self.world.tick(1)
        look.write_text(json.dumps({"on": True, "yaw": -42.5, "pitch": 12.5, "seq": seq + 50}))
        self.world.tick(1)
        self.assertEqual(self.world.native["looks"], 2)  # the file came after the mod started: both count
        packet = struct.unpack("<8sddQQ", (self.tmp / "townfall-companion-native-look.bin").read_bytes())
        self.assertEqual(packet, (b"TFLOOK04", -42.5, 12.5, 1, seq + 50))
        self.assertEqual(self.logged("[TF-CRTV] the CRTV looks as the character holds it"), 1)
        self.assertEqual(self.logged("[TF-CRTV] the CRTV looks where the phone points"), 1)

    def test_the_image_goes_where_the_phone_points_from_where_it_was_as_the_phone_took_over(self):
        self.status(radio={"active": 1, "fineTuning": 1, "mode": 1, "stage": 2})
        self.world.radio["AdvancedTuningMotionControl_Current"] = types.SimpleNamespace(X=0.2, Y=-0.1)
        align = self.tmp / "townfall-companion-align.json"
        seq = int(time.time() * 1000)
        pose = {"roll": 10, "pitch": 5, "dpu": 20, "session": 3, "state": "on"}
        align.write_text(json.dumps({**pose, "seq": seq}))
        self.world.tick(1)
        self.assertEqual(self.world.native["aligns"], 0, "the phone's pose and the image's position: the anchor")
        align.write_text(json.dumps({**pose, "roll": 14, "pitch": 3, "seq": seq + 50}))
        self.world.tick(1)
        self.assertEqual(self.world.native["aligns"], 1)
        magic, radio, x, y, sent = struct.unpack("<8sQddQ", (self.tmp / "townfall-companion-native-align.bin").read_bytes())
        self.assertEqual((magic, radio, sent), (b"TFALIGN1", self.world.radio["address"], seq + 50))
        self.assertAlmostEqual(x, 4 / 20)  # leaned 4 degrees right: the image there, from where it is now
        self.assertAlmostEqual(y, -2 / 20)  # the top 2 degrees away from you: the game's Y goes down
        align.write_text(json.dumps({**pose, "state": "centre held", "seq": seq + 100}))
        self.world.tick(1)
        self.assertEqual(self.logged("[TF-ALIGN] phone tilt: on"), 1)  # the first said it, the second the same
        self.assertEqual(self.logged("[TF-ALIGN] phone tilt: centre held"), 1)  # when and why it stopped


class NoNativeTest(ModTest):
    options = {"noNative": True}

    def test_without_the_dll_the_mod_runs_and_says_so_once(self):
        self.world.enterGameplay(UE_X, UE_Y, UE_Z)
        self.world.tick(3)
        self.assertEqual(self.logged("[TF-NATIVE] tf_native.dll not loaded"), 1)
        self.assertEqual(self.telemetry()["player"]["alive"], True)


class NoTempDirTest(ModTest):
    options = {"noTemp": True}

    def test_telemetry_is_disabled_but_tracking_runs(self):
        self.world.enterGameplay(UE_X, UE_Y, UE_Z)
        self.world.tick(3)
        self.assertEqual(self.logged("telemetry disabled"), 1)
        self.assertEqual(self.logged("tracking local pawn"), 1)
        self.assertFalse(self.telemetry_file.exists())


class PresenceTest(ModTest):
    """The mod reads the game only while the companion's heartbeat says a phone has the page open."""

    def test_the_game_says_it_is_running(self):
        self.world.tick(2)
        beat = json.loads((self.tmp / "townfall-companion-game.json").read_text(encoding="utf-8"))
        self.assertLess(abs(time.time() - beat["time"]), 3)

    def test_nothing_is_read_while_no_phone_has_the_page_open(self):
        self.beat(phones=0)
        self.world.enterGameplay(UE_X, UE_Y, UE_Z)
        self.world.tick(3)
        self.assertFalse(self.telemetry_file.exists())
        self.beat(phones=1)  # a phone opens the page
        self.world.tick(2)
        self.assertTrue(self.telemetry_file.exists())
        self.beat(phones=0)  # and closes it: the file is left as it is
        written = self.world.telemetryWrites
        self.world.tick(3)
        self.assertEqual(self.world.telemetryWrites, written)

    def test_nothing_is_read_once_the_companion_has_gone(self):
        self.beat(age=60)  # its heartbeat stopped a minute ago
        self.world.enterGameplay(UE_X, UE_Y, UE_Z)
        self.world.tick(3)
        self.assertFalse(self.telemetry_file.exists())

    def test_an_unchanged_state_is_not_rewritten_at_every_sample(self):
        self.world.enterGameplay(UE_X, UE_Y, UE_Z)
        self.world.tick(1)
        written = self.world.telemetryWrites
        self.world.tick(10)  # ten samples a moment apart, nothing moving
        self.assertLessEqual(self.world.telemetryWrites - written, 1)
        before = self.telemetry()["player"]
        self.world.movePlayer(UE_X + 500, UE_Y, UE_Z)
        self.world.tick(1)
        self.assertNotEqual(self.telemetry()["player"], before)  # a change reaches the file at once

    def test_the_file_is_still_rewritten_often_enough_to_look_alive(self):
        self.world.enterGameplay(UE_X, UE_Y, UE_Z)
        before = []
        for _ in range(4):
            self.world.tick(1)  # a second of game time each
            before.append(self.telemetry()["t"])
        self.assertEqual(len(set(before)), 4)  # the companion calls a file older than 2 s the game having left


if __name__ == "__main__":
    unittest.main()
