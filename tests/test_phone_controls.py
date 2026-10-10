"""The phone's controls for the game's mini-games: the D-pad (the fine-tune keys) and the phone's pose moving the
alignment stages' image. The bridge's checks on the commands, and the mod's Lua that carries them out."""
import json
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path

from lupa import lua54

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "TownfallCompanion" / "Scripts"
sys.path.insert(0, str(ROOT / "TownfallCompanion" / "companion"))
import bridge  # noqa: E402


class BridgeCommandTest(unittest.TestCase):
    def test_the_phones_mode_is_checked_and_written(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            bridge.send_to_game(path, {"type": "mode", "selector": "VIEW", "onMonitor": True, "miniGameShown": False})
            command = json.loads((path / bridge.COMMAND_FILES["mode"]).read_text())
            self.assertEqual((command["selector"], command["onMonitor"], command["miniGameShown"]), ("VIEW", True, False))
            for selector in (None, "view", "SCANNER", 1):
                with self.subTest(selector=selector), self.assertRaises(ValueError):
                    bridge.send_to_game(path, {"type": "mode", "selector": selector, "onMonitor": False,
                                               "miniGameShown": False})
            for flag in (None, 1, "true"):
                for key in ("onMonitor", "miniGameShown"):
                    with self.subTest(key=key, flag=flag), self.assertRaises(ValueError):
                        bridge.send_to_game(path, {"type": "mode", "selector": "VIEW", "onMonitor": False,
                                                   "miniGameShown": False, key: flag})

    def test_the_phone_only_ever_switches_the_crtv_on(self):
        # Putting away what the phone switched on is the mod's, by the phone's mode.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            bridge.send_to_game(path, {"type": "crtv", "active": True, "animate": False, "frequency": 0.3})
            command = json.loads((path / bridge.COMMAND_FILES["crtv"]).read_text())
            self.assertEqual((command["active"], command["animate"]), (True, False))
            for active in (False, 1, "true", None):
                with self.subTest(active=active), self.assertRaises(ValueError):
                    bridge.send_to_game(path, {"type": "crtv", "active": active, "frequency": 0.3})

    def test_the_centre_is_pressed_and_let_go(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            for kind in ("confirm", "release"):
                bridge.send_to_game(path, {"type": kind})
                self.assertEqual(list(json.loads((path / bridge.COMMAND_FILES[kind]).read_text())), ["seq"])

    def test_the_phones_look_is_on_or_off_a_yaw_within_a_half_turn_and_a_pitch_within_a_quarter(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            bridge.send_to_game(path, {"type": "look", "on": True, "yaw": -35.5, "pitch": 12.5})
            command = json.loads((path / bridge.COMMAND_FILES["look"]).read_text())
            self.assertEqual((command["on"], command["yaw"], command["pitch"]), (True, -35.5, 12.5))
            for yaw in (181, -181, float("nan"), "10", True, None):
                with self.subTest(yaw=yaw), self.assertRaises(ValueError):
                    bridge.send_to_game(path, {"type": "look", "on": True, "yaw": yaw, "pitch": 0})
            for pitch in (91, -91, float("inf"), None):
                with self.subTest(pitch=pitch), self.assertRaises(ValueError):
                    bridge.send_to_game(path, {"type": "look", "on": True, "yaw": 0, "pitch": pitch})
            for on in (1, "true", None):
                with self.subTest(on=on), self.assertRaises(ValueError):
                    bridge.send_to_game(path, {"type": "look", "on": on, "yaw": 0, "pitch": 0})

    def test_a_note_from_the_page_is_plain_text_for_the_log(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            bridge.send_to_game(path, {"type": "note", "text": "page error: x is not a function app.js:12:3"})
            self.assertEqual(json.loads((path / bridge.COMMAND_FILES["note"]).read_text())["text"],
                             "page error: x is not a function app.js:12:3")
            for text in ("", "x" * 201, 'a "quote"', "back" + chr(92) + "slash", "new" + chr(10) + "line", "ü", 7, None):
                with self.subTest(text=text), self.assertRaises(ValueError):
                    bridge.send_to_game(path, {"type": "note", "text": text})

    def test_fine_tune_directions_are_checked_and_written(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            for direction in ("up", "down", "left", "right"):
                bridge.send_to_game(path, {"type": "fine_tune", "direction": direction})
                command = json.loads((path / bridge.COMMAND_FILES["fine_tune"]).read_text())
                self.assertEqual(command["direction"], direction)
                self.assertGreater(command["seq"], 0)
            for direction in (None, True, 1, [], {}, "center", "Up"):
                with self.subTest(direction=direction), self.assertRaises(ValueError):
                    bridge.send_to_game(path, {"type": "fine_tune", "direction": direction})

    def test_alignment_pose_sensitivity_and_session_are_checked_and_written(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            bridge.send_to_game(path, {"type": "align", "roll": -12.5, "pitch": 30, "dpu": 15, "session": 7,
                                       "state": "centre held"})
            command = json.loads((path / bridge.COMMAND_FILES["align"]).read_text())
            self.assertEqual((command["roll"], command["pitch"], command["dpu"], command["session"], command["state"]),
                             (-12.5, 30, 15, 7, "centre held"))
            ok = {"roll": 0, "pitch": 0, "dpu": 15, "session": 1, "state": "on"}
            for payload in ({**ok, "roll": float("nan")}, {**ok, "pitch": float("inf")}, {**ok, "roll": 181},
                            {**ok, "pitch": 91}, {**ok, "dpu": 0}, {**ok, "dpu": 100}, {**ok, "roll": True},
                            {**ok, "session": "1"}, {**ok, "session": 0}, {**ok, "session": True},
                            {**ok, "state": "ON"}, {**ok, "state": "on%s"}, {**ok, "state": ["on"]},
                            {"roll": 0, "pitch": 0, "session": 1, "state": "on"}, {"x": 0, "y": 0, "session": 1}):
                with self.subTest(payload=payload), self.assertRaises((KeyError, ValueError)):  # both a 400
                    bridge.send_to_game(path, {"type": "align", **payload})


class AlignmentStatesTest(unittest.TestCase):
    def test_every_reason_the_phone_gives_for_its_tilt_being_off_is_one_the_bridge_takes(self):
        page = (ROOT / "TownfallCompanion" / "companion" / "static" / "app.js").read_text(encoding="utf-8")
        body = re.search(r"function alignmentOff\(.*?\n}\n", page, re.S).group(0)
        reasons = set(re.findall(r'return "([^"]+)";', body))
        self.assertGreaterEqual(len(reasons), 10)
        self.assertEqual(reasons | {"on"}, bridge.ALIGN_STATES)


class FineTuneKeysTest(unittest.TestCase):
    """tf_crtv.lua presses the radio's own inputs; tf_commands.lua passes the phone's presses on once."""

    def setUp(self):
        self.lua = lua54.LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute("""
            calls = {}; tracking = true; active = true; valid = true
            opened = {}; local originalOpen = io.open
            io.open = function(path, mode)
                local file, err = originalOpen(path, mode)
                if file then opened[#opened + 1] = file end
                return file, err
            end
            radio = {IsValid = function() return valid end}
            for _, key in ipairs({"Up", "Down", "Left", "Right", "Confirm"}) do
                radio["PlayerInput_FineTune" .. key .. "_Pressed"] = function() calls[#calls + 1] = key end
            end
            radio.PlayerInput_Radio_QuickTuneLeft_Pressed = function() calls[#calls + 1] = "QuickLeft" end
            radio.PlayerInput_Radio_QuickTuneRight_Pressed = function() calls[#calls + 1] = "QuickRight" end
            radio.PlayerInput_AdvancedTuningStoreSignal_Pressed = function() calls[#calls + 1] = "Store" end
            radio.PlayerInput_AdvancedTuningStoreSignal_Released = function() calls[#calls + 1] = "Stored" end
            pawn = {GetIsRadioInActiveMode = function() return active end, GetRadio = function() return radio end}
            logged = {}
            package.loaded.tf_common = {logChange = function() end,
                log = function(tag, format, ...) logged[#logged + 1] = tag .. " " .. string.format(format, ...) end}
            package.loaded.tf_player = {isTracking = function() return tracking end, pawn = function() return pawn end}
            package.loaded.tf_audio = {request = function() end, update = function() end}
            package.loaded.tf_alignment = {apply = function() end}
            miniGame = nil
            looks = {}
            package.loaded.tf_native = {miniGame = function() return miniGame end,
                                        look = function(on, yaw, pitch, seq)
                                            looks[#looks + 1] = on and (yaw .. " " .. pitch) or "off"
                                        end}
        """)
        self.crtv = self.lua.execute((SCRIPTS / "tf_crtv.lua").read_text(encoding="utf-8"))
        self.lua.globals().package.loaded.tf_crtv = self.crtv

    def calls(self):
        return list(self.lua.globals().calls.values())

    def test_the_radios_own_inputs_are_pressed(self):
        self.lua.execute("miniGame = {mode = 0, stage = 0}")
        for direction in ("up", "down", "left", "right"):
            self.assertEqual(self.crtv.pressFineTune(direction), "fine-tune " + direction)
        self.assertEqual(self.crtv.pressCentre(), "confirm")
        self.assertIsNone(self.crtv.releaseCentre())  # a confirm has nothing to let go
        self.assertEqual(self.calls(), ["Up", "Down", "Left", "Right", "Confirm"])

    def test_the_centre_is_the_held_store_button_once_the_advanced_mini_game_waits_for_it(self):
        for stage, expected in ((0, "confirm"), (1, "confirm"), (2, "store signal"), (3, "store signal"),
                                (5, "store signal")):
            with self.subTest(stage=stage):
                self.lua.execute(f"miniGame = {{mode = 1, stage = {stage}}}")
                self.assertEqual(self.crtv.pressCentre(), expected)
                self.crtv.releaseCentre()
        self.lua.execute("miniGame = {mode = 0, stage = 4}")  # the standard mini-game has no store stage
        self.assertEqual(self.crtv.pressCentre(), "confirm")
        self.assertEqual(self.calls(), ["Confirm", "Confirm", "Store", "Stored", "Store", "Stored", "Store", "Stored",
                                        "Confirm"])

    def test_outside_the_mini_game_left_and_right_jump_between_frequencies(self):
        self.assertEqual([self.crtv.pressFineTune(d) for d in ("left", "right", "up")],
                         ["quick tune left", "quick tune right", "fine-tune up"])
        self.assertEqual(self.calls(), ["QuickLeft", "QuickRight", "Up"])

    def test_nothing_is_pressed_for_an_unknown_key_or_without_a_raised_radio(self):
        self.assertFalse(self.crtv.pressFineTune("forward"))
        for variable in ("tracking", "active", "valid"):
            self.lua.globals()[variable] = False
            self.assertFalse(self.crtv.pressFineTune("up"))
            self.assertIsNone(self.crtv.pressCentre())
            self.lua.globals()[variable] = True
        self.assertEqual(self.calls(), [])

    def test_a_press_counts_once_and_not_when_left_over_or_late(self):
        self.crtv.pump = self.lua.eval("function() end")
        commands = self.lua.execute((SCRIPTS / "tf_commands.lua").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            commands.init(directory)
            file = Path(directory) / bridge.COMMAND_FILES["fine_tune"]
            now = int(time.time() * 1000)
            try:
                file.write_text(json.dumps({"direction": "left", "seq": now}))
                commands.poll()
                self.assertEqual(self.calls(), [], "a file there before is only the starting point")
                file.write_text(json.dumps({"direction": "right", "seq": now + 1}))
                commands.poll()
                commands.poll()
                self.assertEqual(self.calls(), ["QuickRight"])  # no mini-game: right jumps a frequency
                file.write_text(json.dumps({"direction": "up", "seq": now - 5000}))
                commands.poll()
                self.assertEqual(self.calls(), ["QuickRight"], "too late")
            finally:
                self.lua.execute("for _, file in ipairs(opened) do file:close() end")  # tf_commands keeps them open

    def test_the_phones_look_goes_to_the_dll_once_and_not_when_late(self):
        self.crtv.pump = self.lua.eval("function() end")
        commands = self.lua.execute((SCRIPTS / "tf_commands.lua").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            commands.init(directory)
            file = Path(directory) / bridge.COMMAND_FILES["look"]
            now = int(time.time() * 1000)
            try:
                file.write_text(json.dumps({"on": True, "yaw": 10, "pitch": 0, "seq": now}))
                commands.poll()
                file.write_text(json.dumps({"on": True, "yaw": -35.5, "pitch": 12.5, "seq": now + 1}))
                commands.poll()
                commands.poll()
                file.write_text(json.dumps({"on": True, "yaw": 20, "pitch": 0, "seq": now - 5000}))
                commands.poll()
                file.write_text(json.dumps({"on": False, "yaw": 0, "pitch": 0, "seq": now + 2}))
                commands.poll()
                file.write_text(json.dumps({"yaw": 30, "pitch": 0, "seq": now + 3}))  # neither on nor off
                commands.poll()
                file.write_text(json.dumps({"on": True, "yaw": 30, "seq": now + 4}))  # no pitch
                commands.poll()
                self.assertEqual(list(self.lua.globals().looks.values()), ["-35.5 12.5", "off"],
                                 "the first is only a start; late or unclear: dropped")
            finally:
                self.lua.execute("for _, file in ipairs(opened) do file:close() end")

    def test_a_note_from_the_page_goes_to_the_log_once_even_outside_gameplay(self):
        self.crtv.pump = self.lua.eval("function() end")
        commands = self.lua.execute((SCRIPTS / "tf_commands.lua").read_text(encoding="utf-8"))
        self.lua.globals().tracking = False
        with tempfile.TemporaryDirectory() as directory:
            commands.init(directory)
            file = Path(directory) / bridge.COMMAND_FILES["note"]
            now = int(time.time() * 1000)
            try:
                file.write_text(json.dumps({"text": "page error: old", "seq": now}))
                commands.poll()
                file.write_text(json.dumps({"text": "page error: x is not a function app.js:12:3", "seq": now + 1}))
                commands.poll()
                commands.poll()
                self.assertEqual(list(self.lua.globals().logged.values()),
                                 ["TF-PHONE page error: x is not a function app.js:12:3"])
            finally:
                self.lua.execute("for _, file in ipairs(opened) do file:close() end")


class AlignmentTest(unittest.TestCase):
    """tf_alignment.lua holds the mini-game's image where the phone points, through tf_native.dll (faked: it moves the
    image as the game's stick function does, clamped to -1..1)."""

    def setUp(self):
        self.lua = lua54.LuaRuntime()
        self.lua.execute("""
            moves = {}; active = true; address = 1234
            image = {X = 0.2, Y = -0.1}
            game = {mode = 1, stage = 2}
            local radio = {IsValid = function() return true end, GetAddress = function() return address end,
                           AdvancedTuningMotionControl_Current = image}
            local pawn = {GetIsRadioInActiveMode = function() return active end, GetRadio = function() return radio end}
            package.loaded.tf_player = {isTracking = function() return true end, pawn = function() return pawn end}
            logs = {}
            package.loaded.tf_common = {logChange = function(key, tag, text) logs[#logs + 1] = text end}
            package.loaded.tf_crtv = {tunedWaypoint = function() return nil end}
            local function clamp(v) return math.max(-1, math.min(1, v)) end
            package.loaded.tf_native = {loaded = function() return true end, miniGame = function() return game end,
                align = function(radio, x, y, seq)
                    moves[#moves + 1] = {radio = radio, x = x, y = y, seq = seq}
                    image.X, image.Y = clamp(image.X + x), clamp(image.Y + y)
                end}
        """)
        self.alignment = self.lua.execute((SCRIPTS / "tf_alignment.lua").read_text(encoding="utf-8"))

    def pose(self, roll, pitch, session=7, seq=1000, state="on", dpu=20):
        self.alignment.apply(roll, pitch, dpu, session, seq, state)

    def image(self):
        image = self.lua.globals().image
        return round(image.X, 6), round(image.Y, 6)

    def test_the_image_goes_where_the_phone_points(self):
        self.pose(10, 5)
        self.assertEqual(len(self.lua.globals().moves), 0, "the phone takes over where the image is")
        self.pose(14, 3, seq=1050)  # 4 degrees right, the top 2 degrees away from you
        self.assertEqual(self.image(), (0.4, -0.2))
        self.assertEqual(self.lua.globals().moves[1].seq, 1050)
        self.pose(6, 7)  # the top towards you: up
        self.assertEqual(self.image(), (0.0, 0.0))
        self.pose(14, 3)  # back to the same pose: back to the same place
        self.assertEqual(self.image(), (0.4, -0.2))
        self.lua.globals().image.X = -0.5  # the mouse moved it
        self.pose(14, 3)
        self.assertEqual(self.image(), (0.4, -0.2), "the phone's pose has its one place")

    def test_past_an_edge_the_image_comes_off_it_the_moment_the_phone_turns_back(self):
        self.pose(10, 5)
        self.pose(40, 5)  # far past the right edge
        self.assertEqual(self.image(), (1.0, -0.1))
        self.pose(38, 5)
        self.assertEqual(self.image(), (0.9, -0.1))
        self.pose(10, -80)  # laid nearly flat: the top far away, the image at its bottom edge
        self.assertEqual(self.image(), (-0.5, -1.0))
        self.pose(10, -75)  # raised a little: it comes off the edge at once
        self.assertEqual(self.image(), (-0.5, -0.75))

    def test_while_the_tilt_is_off_the_image_stays_and_then_goes_where_the_phone_points(self):
        self.pose(0, 0)
        self.pose(10, 0, state="centre held")
        self.assertEqual(self.image(), (0.2, -0.1))
        self.pose(10, 0)
        self.assertEqual(self.image(), (0.7, -0.1))

    def test_a_new_page_another_radio_or_a_new_run_of_the_stages_takes_a_new_anchor(self):
        self.pose(0, 0)
        self.pose(10, 0, session=8)
        self.assertEqual(self.image(), (0.2, -0.1), "the page loaded again")
        self.lua.globals().address = 99
        self.pose(20, 0, session=8)
        self.assertEqual(self.image(), (0.2, -0.1), "another radio")
        self.lua.execute("game = {mode = 1, stage = 1}")
        self.pose(30, 0, session=8)
        self.lua.execute("game = {mode = 1, stage = 2}; image.X = 0")
        self.pose(40, 0, session=8)
        self.assertEqual(self.image(), (0.0, -0.1), "the stages began again: the image's new start")
        self.pose(44, 0, session=8)
        self.assertEqual(self.image(), (0.2, -0.1))

    def test_the_log_says_when_and_why_the_tilt_stops_on_the_phone_or_in_the_mod(self):
        self.pose(0, 0)
        self.pose(0, 0, state="centre held")
        self.lua.globals().active = False
        self.pose(0, 0)
        self.assertEqual(list(self.lua.globals().logs.values()),
                         ["phone tilt: on", "phone tilt: centre held", "phone tilt: on, but the CRTV isn't raised"])

    def test_a_phone_that_goes_quiet_with_its_tilt_on_is_logged(self):
        self.lua.execute("clock = 100; os.clock = function() return clock end")
        self.pose(0, 0, state="centre held")
        self.lua.execute("clock = 110")
        self.alignment.watchPhone()
        self.assertEqual(list(self.lua.globals().logs.values()), ["phone tilt: centre held"], "off: nothing to miss")
        self.pose(0, 0)
        self.lua.execute("clock = 112")
        self.alignment.watchPhone()
        self.assertEqual(len(self.lua.globals().logs), 2, "it says how it is every second")
        self.lua.execute("clock = 114")
        self.alignment.watchPhone()
        self.assertEqual(self.lua.globals().logs[3], "phone tilt: no word from the phone for 3 s (it last said on)")


if __name__ == "__main__":
    unittest.main()
