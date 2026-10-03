"""Runs the UE4SS mod in TownfallCompanion against a fake UE4SS/Townfall world.

No game needed. Requires lupa, which bundles Lua 5.4 (the version UE4SS embeds):

    pip install lupa
    python -m unittest discover -s tests -v
"""
import json
import tempfile
import time
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
        lua = lua54.LuaRuntime()
        make_world = lua.execute(FAKE_UE4SS.read_text(encoding="utf-8"))
        self.world = make_world(SCRIPTS.as_posix(), tmp.name, lua.table(**self.options))
        self.addCleanup(self.world.closeFiles)  # the mod keeps command files open; runs before tmp.cleanup
        self.world.start()

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
        # The phone holds its videos and sound while it does; the sample clock (t) runs on meanwhile.
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
        self.assertEqual(sorted(player), ["alive", "pitch", "x", "y", "yaw"])  # no height: the phone is a map
        self.assertIs(player["alive"], True)
        self.assertEqual(player["pitch"], -5)  # the camera, a little down

    def test_the_cameras_pitch_up_positive_down_negative(self):
        # The phone moves the monsters up and down with it. Unreal keeps it in 0..360: 350 is 10 down.
        self.world.enterGameplay(UE_X, UE_Y, UE_Z)
        for unreal, phone in ((350, -10), (20, 20), (-30, -30)):
            self.world.lookUp(unreal)
            self.world.tick(1)
            self.assertEqual(self.telemetry()["player"]["pitch"], phone)

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
        del crtv["needleColours"]  # NeedleColourTest
        return crtv

    # Values below are what the game reported: the dial runs 0..1 and a signal is 0 or 1.
    def test_tuned_enemy_signal_is_sent(self):
        self.tune(True, 0.23, 1.0, 2)
        self.world.tick(1)
        self.assertEqual(self.crtv(), {"active": True, "frequency": 0.23, "signalType": "enemy", "video": None,
                                       "videoTime": None, "fineTune": None})

    def test_signal_type_is_none_without_strength(self):
        self.tune(True, 0.5, 0, 2)
        self.world.tick(1)
        self.assertEqual(self.telemetry()["crtv"]["signalType"], "none")

    def test_lowered_crtv_ignores_cached_signal(self):
        self.tune(False, 0.0, 1.0, 2)
        self.world.tick(1)
        self.assertEqual(self.crtv(), {"active": False, "frequency": 0.0, "signalType": "none", "video": None,
                                       "videoTime": None, "fineTune": None})

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


class CrtvScreenTest(ModTest):
    """What the in-game CRTV's screen plays, from its Bink players, so the phone can show the same."""

    def setUp(self):
        super().setUp()
        self.world.enterGameplay(UE_X, UE_Y, UE_Z)
        self.radio = self.world.radio
        self.radio["active"] = True

    def crtv(self):
        self.world.tick(1)
        return self.telemetry()["crtv"]

    def tune(self, signal_type):
        """What the CRTV is tuned to: None, or ERadioSignalFMODType (1 a found waypoint, 2 an enemy)."""
        self.radio["cachedHighestSignalStrength"] = 0.0 if signal_type is None else 1.0
        self.radio["cachedHighestStrengthSignalType"] = signal_type or 0

    def test_enemy_video_and_time(self):
        self.tune(2)
        self.world.playVideo("EnemyVideoPlayer_Bink", "./Movies/CRTV_Movies/Bink/Enraged_Focused.bk2", 12.5)
        crtv = self.crtv()
        self.assertEqual((crtv["video"], crtv["videoTime"]), ("Bink/Enraged_Focused", 12.5))
        self.assertEqual(self.logged("[TF-CRTV] screen plays Bink/Enraged_Focused"), 1)

    def test_story_video_with_a_windows_path_and_no_time(self):
        self.tune(1)
        self.world.playVideo("WaypointVideoPlayer_Bink", r"C:\Game\Movies\CRTV_Movies\Bink\Shipping\Mov_CRTV_Clinic.bk2")
        crtv = self.crtv()
        self.assertEqual((crtv["video"], crtv["videoTime"]), ("Bink/Shipping/Mov_CRTV_Clinic", None))

    def test_a_waypoint_video_playing_untuned_is_not_shown(self):
        # The game plays the waypoint's video as soon as the CRTV is up and shows it only once tuned in:
        # with the dial at 0, Mov_CRTV_Clinic plays but isn't on the screen.
        self.world.playVideo("WaypointVideoPlayer_Bink", "./Movies/CRTV_Movies/Bink/Shipping/Mov_CRTV_Clinic.bk2", 4)
        self.assertIsNone(self.crtv()["video"])
        self.tune(0)  # on the waypoint's channel, not found yet: the fine-tune mini-game
        self.assertIsNone(self.crtv()["video"])
        self.assertEqual(self.logged("[TF-CRTV] screen plays Bink/Shipping/Mov_CRTV_Clinic"), 0)
        self.tune(1)
        self.assertEqual(self.crtv()["video"], "Bink/Shipping/Mov_CRTV_Clinic")

    def test_only_the_tuned_signals_player_counts(self):
        self.world.playVideo("EnemyVideoPlayer_Bink", "./Movies/CRTV_Movies/Bink/Enraged_Focused.bk2", 1)
        self.world.playVideo("WaypointVideoPlayer_Bink", "./Movies/CRTV_Movies/Bink/Shipping/Mov_CRTV_Clinic.bk2", 1)
        self.tune(1)
        self.assertEqual(self.crtv()["video"], "Bink/Shipping/Mov_CRTV_Clinic")
        self.tune(2)
        self.assertEqual(self.crtv()["video"], "Bink/Enraged_Focused")
        self.world.playVideo("EnemyVideoPlayer_Bink", None)
        self.assertIsNone(self.crtv()["video"])  # not the waypoint's in its place

    def test_fine_tune_positions_along_the_bar(self):
        self.assertIsNone(self.crtv()["fineTune"])
        ui = self.world.fineTuneUi
        ui["visible"] = True
        ui["box"]["x"] = 190  # 20 wide: centre 200 of the bar's 100-500
        tune = self.crtv()["fineTune"]
        self.assertEqual(tune, {"box": 0.25, "zone": 0.5, "text": "FINE TUNE - SEARCHING"})
        self.assertEqual(self.logged('[TF-CRTV] fine tune "FINE TUNE - SEARCHING": bar 100.0+400.0 box 190.0+20.0 '
                                     'diamond 285.0+30.0'), 1)

    def test_fine_tune_with_centred_slots_and_a_moving_box(self):
        ui = self.world.fineTuneUi
        ui["visible"], ui["align"] = True, 0.5  # slot positions are then the centres
        ui["band"]["x"], ui["box"]["x"], ui["zone"]["x"] = 300, 200, 300
        ui["box"]["t"]["X"] = 50  # the widget moves the box by its render offset: centre 250 of 100-500
        tune = self.crtv()["fineTune"]
        self.assertEqual((tune["box"], tune["zone"]), (0.375, 0.5))

    def test_nothing_while_lowered_or_stopped(self):
        self.tune(2)
        self.world.playVideo("EnemyVideoPlayer_Bink", "./Movies/CRTV_Movies/Bink/Enraged_Focused.bk2", 3)
        self.radio["active"] = False
        self.assertIsNone(self.crtv()["video"])
        self.radio["active"] = True
        self.world.playVideo("EnemyVideoPlayer_Bink", None)
        self.assertIsNone(self.crtv()["video"])


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
        sent = {e["id"]: (e["signal"], e["channel"], e["video"]) for e in self.telemetry()["enemies"]}
        self.assertEqual(sent, {"Fearful_1": (0.6, 0.23, "Bink/TheFallen_Focused"),
                                "Enraged_2": (0.0, 0.23, "Bink/TheFallen_Focused")})

    def test_a_new_enemy_is_logged_once_with_its_clip_and_its_tolerance_sent(self):
        for _ in range(3):
            self.world.tick(10)
        self.assertEqual(self.logged("[TF-ENEMY] new BP_TestAIAgent_C"), 2)
        self.assertEqual(self.logged("crtv channel 0.230 clip Bink/TheFallen_Focused"), 2)  # which creature the phone shows
        self.assertEqual([e["tolerance"] for e in self.telemetry()["enemies"]], [0.02, 0.02])  # clear within
        self.assertEqual([e["toleranceOuter"] for e in self.telemetry()["enemies"]], [0.05, 0.05])  # comes in within

    def test_enemy_without_signal_source_is_still_tracked(self):
        self.world.spawnEnemy("Crazed_3", UE_X - 1000, UE_Y, UE_Z, True)
        self.raise_crtv()
        self.world.tick(1)
        self.assertEqual(self.sent()["Crazed_3"], (False, False))
        crazed = next(e for e in self.telemetry()["enemies"] if e["id"] == "Crazed_3")
        self.assertIsNone(crazed["channel"])
        self.assertIsNone(crazed["video"])
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
        self.clinic = self.world.addWaypoint("Clinic", UE_X + 1000, UE_Y, UE_Z, 0.15, True, "Bink/Shipping/Mov_CRTV_Clinic")
        self.world.addWaypoint("Later", UE_X + 6000, UE_Y, UE_Z, 0.62, False, "Bink/Shipping/Mov_CRTV_BodyBags", True)
        self.world.tick(1)

    def signals(self):
        return {s["id"]: s for s in self.telemetry()["signals"]}

    def test_only_active_waypoints_are_sent(self):
        clinic = self.signals()["Clinic"]
        self.assertEqual(list(self.signals()), ["Clinic"])
        self.assertEqual((clinic["kind"], clinic["channel"], clinic["tolerance"], clinic["toleranceOuter"], clinic["video"]),
                         ("waypoint", 0.15, 0.02, 0.04, "Bink/Shipping/Mov_CRTV_Clinic"))
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
                                     "reach 2000-10000 cm video Bink/Shipping/Mov_CRTV_Clinic"), 1)

    def test_a_video_the_story_sets_later_is_sent(self):
        self.world.setWaypointVideo(self.clinic, None)
        self.world.tick(1)
        self.assertIsNone(self.signals()["Clinic"]["video"])
        self.world.setWaypointVideo(self.clinic, "Bink/Shipping/Mov_CRTV_Pharmacy")
        self.world.tick(1)
        self.assertEqual(self.signals()["Clinic"]["video"], "Bink/Shipping/Mov_CRTV_Pharmacy")
        self.assertEqual(self.logged("video Bink/Shipping/Mov_CRTV_Pharmacy"), 1)

    def test_a_reach_without_cutoff_is_full_everywhere(self):
        # Return_to_Clinic: 1000 to -1 cm, and the game gives it full strength.
        self.clinic["distanceSignalFalloffBegin"], self.clinic["distanceSignalCutoff"] = 1000, -1
        self.world.movePlayer(UE_X - 50000, UE_Y, UE_Z)
        self.world.tick(1)
        self.assertEqual(self.signals()["Clinic"]["rangeSignal"], 1.0)

    def test_quiet_until_it_talks(self):
        self.assertIsNone(self.signals()["Clinic"]["dialogue"])
        self.assertEqual(self.logged("[TF-SIGNAL] waypoint Clinic is quiet"), 1)

    def test_the_line_it_says_and_how_far_in(self):
        self.world.speak(self.clinic, "10c5", 2350)
        self.world.tick(1)
        self.assertEqual(self.signals()["Clinic"]["dialogue"], {"line": "10c5", "id": "Clinic_Clear", "ms": 2350, "clear": True})
        self.assertEqual(self.logged('[TF-SIGNAL] waypoint Clinic says line "10c5" (dialogue "Clinic_Clear", clear)'), 1)
        self.world.speak(self.clinic, "10c6", 400, True)
        self.world.tick(1)
        self.assertEqual(self.signals()["Clinic"]["dialogue"], {"line": "10c6", "id": "Clinic_Dist", "ms": 400, "clear": False})
        self.world.speak(self.clinic, None)
        self.world.tick(1)
        self.assertIsNone(self.signals()["Clinic"]["dialogue"])
        self.assertEqual(self.logged("[TF-SIGNAL] waypoint Clinic is quiet"), 2)

    def test_an_unreadable_dialogue_keeps_the_waypoint(self):
        self.clinic["WaypointDialogueClear"] = None
        self.world.speak(self.clinic, "10c5", 0)
        self.world.tick(1)
        self.assertIsNone(self.signals()["Clinic"]["dialogue"])
        self.assertEqual(self.logged("[TF-SIGNAL] waypoint Clinic dialogue unreadable"), 1)

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
        self.world.addWaypoint("Clinic", UE_X + 1000, UE_Y, UE_Z, 0.15, True, "Bink/Shipping/Mov_CRTV_Clinic")
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
    def command(self, seq, active, frequency):
        # Written the way bridge.py writes it.
        self.commands_file.write_text(json.dumps({"seq": seq, "active": active, "frequency": frequency}))

    def play(self, command_before_load=True):
        if command_before_load:
            self.command(100, True, 0.5)  # left over from an earlier session
        self.world.enterGameplay(UE_X, UE_Y, UE_Z)
        self.radio = self.world.radio
        self.world.tick(3)

    def test_leftover_command_is_not_applied_on_load(self):
        self.play()
        self.assertFalse(self.radio["active"])
        self.assertEqual(self.logged("phone command"), 0)

    def test_new_command_raises_and_tunes_the_crtv(self):
        self.play()
        self.command(101, True, 0.23)
        self.world.tick(1)
        self.assertEqual((self.radio["active"], self.radio["frequency"]), (True, 0.23))
        self.assertEqual(self.logged("[TF-CRTV] phone command: active=true frequency=0.230"), 1)

    def test_a_command_is_applied_once(self):
        self.play()
        self.command(101, True, 0.23)
        self.world.tick(1)
        self.radio["active"] = False  # the player lowers it in the game afterwards
        self.world.tick(10)
        self.assertFalse(self.radio["active"])
        self.assertEqual(self.logged("phone command"), 1)

    def test_lowering_leaves_the_dial(self):
        self.play()
        self.command(101, True, 0.23)
        self.world.tick(1)
        self.command(102, False, 0.4)
        self.world.tick(1)
        self.assertEqual((self.radio["active"], self.radio["frequency"]), (False, 0.23))

    def test_without_active_only_the_dial_moves_up_or_down(self):
        self.play()
        self.commands_file.write_text(json.dumps({"frequency": 0.3, "seq": 101}))  # tuned behind the scenes
        self.world.tick(1)
        self.assertEqual((self.radio["active"], self.radio["frequency"]), (False, 0.3))  # still down
        self.radio["active"] = True  # raised with the controller: it comes up where the phone left it
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
        self.commands_file.write_text('{"seq": 101, "active": ')
        self.world.tick(3)
        self.assertFalse(self.radio["active"])
        self.assertEqual(self.logged("error"), 0)

    def test_commands_wait_for_gameplay(self):
        self.world.enterMenu()
        self.command(100, True, 0.5)
        self.world.tick(3)
        self.assertEqual(self.logged("phone command"), 0)


class SteeringTest(ModTest):
    """The phone sends its own heading; the player turns by as much as the phone turned."""

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

    def test_tilting_the_phone_leaves_the_games_camera_pitch_alone(self):
        # Steering turns the player by yaw only (commits 43a48f9, 8c8bd3e): tilt is the scanner view's, on the phone.
        self.play()
        self.steer(350, pitch=10)
        self.world.tick(1)
        self.steer(350, pitch=25)  # 15 degrees up on the phone
        self.world.tick(1)
        self.assertEqual(tuple(self.world.controlRotation()), (-5, 40))
        self.steer(20, pitch=-5)   # turned 30 clockwise, tilted 30 down
        self.world.tick(1)
        self.assertEqual(tuple(self.world.controlRotation()), (-5, 70))

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
    """The phone's F key: the player's press in the fine-tune mini-game."""

    def setUp(self):
        super().setUp()
        self.confirm_file = self.commands_file.with_name("townfall-companion-confirm.json")

    def press(self, age=0.0):
        self.confirm_file.write_text(json.dumps({"seq": int((time.time() - age) * 1000)}))

    def play(self):
        self.world.enterGameplay(UE_X, UE_Y, UE_Z)
        self.world.tick(2)

    def test_first_press_counts_when_there_was_no_file(self):
        self.play()
        self.press()
        self.world.tick(3)
        self.assertEqual(self.world.radio["confirms"], 1)
        self.assertEqual(self.logged("[TF-CRTV] phone pressed F (fine-tune confirm)"), 1)

    def test_a_leftover_press_is_not_replayed(self):
        self.press(age=60)
        self.play()
        self.world.tick(3)
        self.assertEqual(self.world.radio["confirms"], 0)

    def test_a_late_press_is_dropped(self):
        self.play()
        self.press(age=5)
        self.world.tick(1)
        self.assertEqual(self.world.radio["confirms"], 0)


class GameSoundTest(ModTest):
    """The phone plays the CRTV's sound; the game's goes quiet while the phone keeps asking."""

    def setUp(self):
        super().setUp()
        self.audio_file = self.commands_file.with_name("townfall-companion-audio.json")
        self.world.enterGameplay(UE_X, UE_Y, UE_Z)
        self.enemy = self.world.spawnEnemy("Fearful_1", UE_X + 1000, UE_Y, UE_Z)
        self.waypoint = self.world.addWaypoint("Clinic", UE_X + 1000, UE_Y, UE_Z, 0.15, True)
        self.world.tick(2)
        self.seq = int(time.time() * 1000)

    def ask(self, mute, dialogue=False, video=False):
        self.seq += 100  # the bridge's clock, one request later
        self.audio_file.write_text(json.dumps({"muteGame": mute, "dialogue": dialogue, "video": video, "seq": self.seq}))

    def volumes(self):
        radio = self.world.radio
        return [c["volume"] for c in (self.world.player["SFX_CRTV"], radio["staticAudioComponent"],
                                      self.enemy["RadioSignalClear"], self.enemy["RadioSignalDist"])]

    def video(self):
        return self.world.crtvWidget["WaypointVideoAudioComponent"]["volume"]

    def talking(self):
        owner = self.waypoint.GetOwner(self.waypoint)
        return [owner["ClearSignal"]["volume"], owner["DistortedSignal"]["volume"]]

    def test_silenced_while_the_phone_asks(self):
        self.ask(True)
        self.world.tick(4)
        self.assertEqual(self.volumes(), [0] * 4)
        self.assertTrue(self.telemetry()["audio"]["gameSoundOff"])
        self.assertFalse(self.telemetry()["audio"]["cutsceneDialogue"])  # no dialogue mix: it stays in the game
        self.assertEqual(self.logged("[TF-AUDIO] game CRTV sound off, the phone plays it (4 sources)"), 1)

    def test_the_screens_video_sound_stays_unless_the_phone_plays_the_video(self):
        # Without the converted videos the phone plays none: muting the game's would leave the video silent everywhere.
        self.ask(True)
        self.world.tick(4)
        self.assertEqual(self.video(), 1)
        self.ask(True, video=True)
        self.world.tick(1)
        self.assertEqual(self.video(), 0)
        self.assertEqual(self.logged("[TF-AUDIO] CRTV video sound off in the game, the phone plays the video"), 1)
        self.ask(True, video=False)
        self.world.tick(1)
        self.assertEqual((self.video(), self.volumes()), (1, [0] * 4))
        self.assertEqual(self.logged("[TF-AUDIO] CRTV video sound back on in the game"), 1)
        self.ask(True, video=True)
        for _ in range(7):
            self.world.tick(4)  # the phone stopped asking
        self.assertEqual(self.video(), 1)

    def test_the_waypoints_talking_stays_unless_the_phone_plays_it(self):
        # The waypoint talks on its ClearSignal and DistortedSignal; muted with the CRTV's sound, the
        # story's talking would be heard neither on the phone nor in the game.
        self.ask(True)
        self.world.tick(4)
        self.assertEqual(self.talking(), [1, 1])
        self.ask(True, dialogue=True)
        self.world.tick(1)
        self.assertEqual(self.talking(), [0, 0])
        self.assertEqual(self.logged("[TF-AUDIO] waypoint dialogue off in the game, the phone plays it"), 1)
        self.ask(True, dialogue=False)
        self.world.tick(1)
        self.assertEqual((self.talking(), self.volumes()), ([1, 1], [0] * 4))
        self.assertEqual(self.logged("[TF-AUDIO] waypoint dialogue back on in the game"), 1)

    def test_back_on_when_the_phone_stops_asking(self):
        self.ask(True, dialogue=True, video=True)
        for _ in range(7):
            self.world.tick(4)
        self.assertEqual(self.volumes() + self.talking() + [self.video()], [1] * 7)
        self.assertFalse(self.telemetry()["audio"]["gameSoundOff"])
        self.assertEqual(self.logged("[TF-AUDIO] game CRTV sound back on (the phone stopped asking)"), 1)

    def test_back_on_when_the_phone_says_so(self):
        self.ask(True, dialogue=True, video=True)
        self.world.tick(4)
        self.ask(False)
        self.world.tick(1)
        self.assertEqual(self.volumes() + self.talking() + [self.video()], [1] * 7)

    def test_a_monster_arriving_meanwhile_is_silenced_too(self):
        self.ask(True)
        self.world.tick(4)
        later = self.world.spawnEnemy("Enraged_2", UE_X - 1000, UE_Y, UE_Z)
        self.world.tick(4)
        self.assertEqual((later["RadioSignalClear"]["volume"], later["RadioSignalDist"]["volume"]), (0, 0))


class CutsceneTest(ModTest):
    """The videos cutscenes show on screens, for the phone to show."""

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

    def test_the_bink_players_video_and_time(self):
        self.world.playCutsceneVideo("bink", "./Movies/Cutscene_Diegetic_Movies/Bink/Cutscene_WatchingZoesSignal_1.bk2", 3.5)
        self.assertEqual(self.cutscene(), {"video": "Cutscene_Diegetic_Movies/Bink/Cutscene_WatchingZoesSignal_1",
                                           "videoTime": 3.5, "sequence": None, "sequenceTime": None})
        self.assertEqual(self.logged("[TF-CUTSCENE] screen video: Cutscene_Diegetic_Movies/Bink/Cutscene_WatchingZoesSignal_1"), 1)
        self.world.playCutsceneVideo("bink", None)
        self.assertIsNone(self.cutscene())

    def test_the_media_players_mp4_by_file_url(self):
        self.world.playCutsceneVideo("media", "file://E:/Game/Content/Movies/Cutscene_Diegetic_Movies/Cutscene_TooLate_Zoe_1.mp4")
        self.assertEqual(self.cutscene()["video"], "Cutscene_Diegetic_Movies/Cutscene_TooLate_Zoe_1")

    def test_the_cutscene_playing_and_how_far_in(self):
        sequence = self.world.playSequence("LS_SearchingForSignals", 12.5)
        self.assertEqual(self.cutscene(), {"video": None, "videoTime": None,
                                           "sequence": "LS_SearchingForSignals", "sequenceTime": 12.5})
        self.assertEqual(self.logged("[TF-CUTSCENE] sequence: LS_SearchingForSignals"), 1)
        sequence["playing"] = False
        self.assertIsNone(self.cutscene())

    def test_no_lookups_by_path_after_the_level_start(self):
        # They cost 14-21 ms each in the game; the players are caught as they load instead.
        self.world.addWaypoint("Clinic", UE_X + 1000, UE_Y, UE_Z, 0.15, True)
        self.world.tick(2)
        before = self.world.staticFindCalls
        self.world.tick(5)
        self.world.playCutsceneVideo("bink", "./Movies/Cutscene_Diegetic_Movies/Bink/Cutscene_TooLate_Zoe_2.bk2", 1)
        self.assertEqual(self.cutscene()["video"], "Cutscene_Diegetic_Movies/Bink/Cutscene_TooLate_Zoe_2")
        self.assertEqual(self.world.staticFindCalls, before)

    def test_a_freed_player_is_never_used(self):
        # The main menu's opening tape leaves the Bink player behind; the level change frees it, and a
        # call on it then crashes the game. With the phone asking for quiet, nothing may touch it.
        self.world.playCutsceneVideo("bink", "./Movies/Cutscene_Diegetic_Movies/Bink/Cutscene_OpeningTapeContent_Bink.bk2", 1)
        self.world.tick(1)
        self.world.freeCutscenePlayer("bink")
        self.world.enterGameplay(UE_X, UE_Y, UE_Z)
        audio_file = self.commands_file.with_name("townfall-companion-audio.json")
        for i in range(3):
            audio_file.write_text(json.dumps({"muteGame": True, "seq": int(time.time() * 1000) + i}))
            self.world.tick(4)
        self.assertEqual(self.world.freedCalls, 0)
        self.assertIsNone(self.telemetry()["cutscene"])


class CutsceneDialogueTest(ModTest):
    """The phone plays a cutscene's dialogue track; the game's dialogue mix goes quiet meanwhile."""

    def setUp(self):
        super().setUp()
        self.world.addFmodMix("FMODVCA", "Music")
        self.dialogue = self.world.addFmodMix("FMODVCA", "Dialogue")
        self.world.enterGameplay(UE_X, UE_Y, UE_Z)
        self.world.tick(2)
        self.audio_file = self.commands_file.with_name("townfall-companion-audio.json")
        self.seq = int(time.time() * 1000)

    def ask(self, dialogue):
        self.seq += 100
        self.audio_file.write_text(json.dumps({"muteGame": True, "dialogue": dialogue, "seq": self.seq}))

    def test_the_mixes_are_logged_and_dialogue_picked(self):
        self.assertEqual(self.logged("[TF-AUDIO] FMOD mixes: VCA Music, VCA Dialogue; dialogue: VCA Dialogue"), 1)
        self.assertTrue(self.telemetry()["audio"]["cutsceneDialogue"])  # the phone may play cutscene dialogue

    def test_quiet_only_in_a_cutscene_the_phone_plays(self):
        self.ask(True)
        self.world.tick(4)
        self.assertEqual(self.dialogue["volume"], 1)  # no cutscene
        sequence = self.world.playSequence("LS_WakeUp", 3)
        self.ask(True)
        self.world.tick(4)
        self.assertEqual(self.dialogue["volume"], 0)
        sequence["playing"] = False
        self.world.tick(4)
        self.assertEqual(self.dialogue["volume"], 1)
        self.assertEqual(self.logged("[TF-AUDIO] cutscene dialogue back on in the game"), 1)

    def test_not_when_the_phone_has_no_track_for_it(self):
        self.world.playSequence("LS_Unknown", 3)
        self.ask(False)
        self.world.tick(4)
        self.assertEqual(self.dialogue["volume"], 1)


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


class NoTempDirTest(ModTest):
    options = {"noTemp": True}

    def test_telemetry_is_disabled_but_tracking_runs(self):
        self.world.enterGameplay(UE_X, UE_Y, UE_Z)
        self.world.tick(3)
        self.assertEqual(self.logged("telemetry disabled"), 1)
        self.assertEqual(self.logged("tracking local pawn"), 1)
        self.assertFalse(self.telemetry_file.exists())


if __name__ == "__main__":
    unittest.main()
