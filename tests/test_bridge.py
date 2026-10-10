"""Runs the bridge on a free local port against temp folders. Standard library only.

    python -m unittest discover -s tests -v
"""
import ctypes
import html
import http.client
import io
import json
import queue
import re
import runpy
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
import urllib.error
import urllib.request
from pathlib import Path

COMPANION = Path(__file__).resolve().parents[1] / "TownfallCompanion" / "companion"
BRIDGE = COMPANION / "bridge.py"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class BridgeTest(unittest.TestCase):
    extra_args = ()

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)  # a just-written heartbeat: see ReplaceOlderTest
        root = Path(cls.tmp.name)
        cls.telemetry = root / "game" / "townfall-companion-telemetry.json"
        cls.telemetry.parent.mkdir()
        cls.port = free_port()
        cls.bridge = subprocess.Popen(
            [sys.executable, "-u", str(BRIDGE), "--host", "127.0.0.1", "--port", str(cls.port),
             "--telemetry-file", str(cls.telemetry),
             "--settings", str(root / "companion.ini"), "--pin", "", *cls.extra_args],  # no PIN unless a test asks
            # Not into a pipe: the bridge logs every request, and a pipe nobody reads fills up and blocks it.
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                urllib.request.urlopen(cls.url("/api/state"), timeout=1)
                break
            except urllib.error.HTTPError:
                break  # answers: with a PIN it refuses
            except OSError:
                time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.bridge.terminate()
        cls.bridge.wait(timeout=5)
        cls.tmp.cleanup()

    @classmethod
    def url(cls, path):
        return f"http://127.0.0.1:{cls.port}{path}"

    def get(self, path, headers=None):
        try:
            with urllib.request.urlopen(urllib.request.Request(self.url(path), headers=headers or {}), timeout=5) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()

    def state_when(self, predicate, timeout=2.0):
        """The bridge's state once predicate(state) holds; fails after timeout."""
        deadline = time.time() + timeout
        while True:
            state = json.loads(self.get("/api/state")[2])
            if predicate(state):
                return state
            if time.time() > deadline:
                self.fail(f"state never matched: {state}")
            time.sleep(0.05)

    def post(self, path, body):
        request = urllib.request.Request(self.url(path), data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=5) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code


class ConnectionTest(BridgeTest):
    """The phone sends many small requests a second: they share one kept-open connection, and the two responses that
    run on close theirs when they end."""

    def test_many_requests_go_over_one_connection(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            for _ in range(3):
                connection.request("POST", "/api/control", body=json.dumps({"type": "confirm"}),
                                   headers={"Content-Type": "application/json"})
                response = connection.getresponse()
                self.assertEqual((response.status, json.loads(response.read())), (200, {"ok": True}))
                self.assertNotEqual(response.getheader("Connection"), "close")
            connection.request("GET", "/app.js")
            response = connection.getresponse()
            self.assertEqual((response.status, response.getheader("Content-Type")), (200, "text/javascript"))
            response.read()
            connection.request("GET", "/api/state")
            self.assertEqual(connection.getresponse().status, 200)
        finally:
            connection.close()

    def test_a_refused_body_is_read_and_dropped_and_the_connection_carries_on(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request("POST", "/api/control", body="x" * 5000, headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            response.read()
            self.assertEqual((response.status, response.getheader("Connection")), (400, None))
            connection.request("GET", "/api/state")
            self.assertEqual(connection.getresponse().status, 200)
        finally:
            connection.close()

    def test_a_body_of_no_telling_length_closes_the_connection(self):
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as raw:
            raw.sendall(b"POST /api/control HTTP/1.1\r\nHost: phone\r\nContent-Length: lots\r\n\r\n{}")
            answer = b""
            while chunk := raw.recv(4096):
                answer += chunk
        self.assertTrue(answer.startswith(b"HTTP/1.1 400"), answer[:40])
        self.assertIn(b"\r\nConnection: close\r\n", answer)

    def test_the_event_feed_pings_and_closes_its_connection(self):
        with urllib.request.urlopen(self.url("/events"), timeout=10) as feed:
            self.assertEqual(feed.headers["Connection"], "close")
            self.assertTrue(feed.readline().startswith(b"data: "))  # the state at once
            lines = [feed.readline() for _ in range(2)]
            while lines[-2:] != [b"event: ping\n", b"data: {}\n"]:  # within 5 s when nothing else comes
                lines.append(feed.readline())


class StaticTest(BridgeTest):
    def test_page_files_are_revalidated(self):
        for path in ("/", "/style.css", "/app.js", "/crtv/layers.css"):
            status, headers, _ = self.get(path)
            self.assertEqual((status, headers["Cache-Control"]), (200, "no-cache"), path)
        with urllib.request.urlopen(urllib.request.Request(self.url("/style.css"), method="HEAD"), timeout=5) as r:
            self.assertEqual(r.headers["Cache-Control"], "no-cache")
        self.assertEqual(self.get("/app.js")[1]["Content-Type"], "text/javascript")

    def test_the_connection_check_says_where_to_go_next(self):
        status, headers, body = self.get("/check")
        page = body.decode("utf-8")
        self.assertEqual((status, headers["Content-Type"]), (200, "text/html; charset=utf-8"))
        self.assertIn(f"Your phone reached Townfall Companion on {html.escape(socket.gethostname())}.", page)
        self.assertIn(f"Now open http://127.0.0.1:{self.port}.", page)  # no PIN to enter
        # The address comes from the request, so it is shown as text, never as markup.
        tricky = self.get("/check", {"Host": "<b>x</b>"})[2].decode("utf-8")
        self.assertIn("Now open http://&lt;b&gt;x&lt;/b&gt;.", tricky)
        self.assertNotIn("<b>x</b>", tricky)

    def test_the_api_is_never_cached(self):
        self.assertEqual(self.get("/api/state")[1]["Cache-Control"], "no-store")


class CommandTest(BridgeTest):
    def command_file(self, name):
        return json.loads((self.telemetry.parent / name).read_text(encoding="utf-8"))

    def test_crtv_command_is_written_for_the_mod(self):
        self.assertEqual(self.post("/api/control", {"type": "crtv", "active": True, "frequency": 1.4}), 200)
        written = self.command_file("townfall-companion-commands.json")
        self.assertEqual(list(written), ["active", "frequency", "seq"])  # seq last, see tf_commands.lua
        self.assertEqual((written["active"], written["frequency"]), (True, 1.0))
        # Without active only the dial moves (AV OUT: the phone tunes, never raises or lowers).
        self.assertEqual(self.post("/api/control", {"type": "crtv", "frequency": 0.4}), 200)
        self.assertEqual(list(self.command_file("townfall-companion-commands.json")), ["frequency", "seq"])
        # animate: the character's raise animation, or silently.
        self.assertEqual(self.post("/api/control", {"type": "crtv", "active": True, "animate": True, "frequency": 0.4}), 200)
        written = self.command_file("townfall-companion-commands.json")
        self.assertEqual(list(written), ["active", "animate", "frequency", "seq"])
        self.assertTrue(written["animate"])

    def test_steer_command_and_rising_seq(self):
        self.assertEqual(self.post("/api/control", {"type": "steer", "yaw": 10, "pitch": -100}), 400)  # past straight down
        self.post("/api/control", {"type": "steer", "yaw": 370, "pitch": -12.5})
        first = self.command_file("townfall-companion-steer.json")
        self.assertEqual(list(first), ["yaw", "pitch", "seq"])  # the heading and the tilt: the mod turns and tilts by both
        self.post("/api/control", {"type": "steer", "yaw": -10})  # a page from before sends the heading only
        second = self.command_file("townfall-companion-steer.json")
        self.assertEqual((first["yaw"], first["pitch"], second["yaw"], list(second)), (10.0, -12.5, 350.0, ["yaw", "seq"]))
        self.assertGreater(second["seq"], first["seq"])

    def test_bad_commands_are_refused(self):
        for body in ({"type": "crtv", "active": "yes", "frequency": 0.2},
                     {"type": "crtv", "active": True, "animate": "yes", "frequency": 0.2}, {"type": "crtv", "active": True},
                     {"type": "steer", "yaw": "north"},
                     {"type": "steer", "yaw": "12.5"}, {"type": "steer", "yaw": True},  # numbers only
                     {"type": "steer", "yaw": float("nan")}, {"type": "crtv", "frequency": float("inf")},
                     {"type": "steer", "yaw": 10 ** 400}):  # too big for a float
            self.assertEqual(self.post("/api/control", body), 400, body)
        self.assertEqual(self.post("/api/control", {"type": "steer", "yaw": 12}), 200)  # an integer is a number

    def test_sound_request_for_the_game(self):
        self.assertEqual(self.post("/api/control", {"type": "audio", "muteGame": True}), 200)
        sent = self.command_file("townfall-companion-audio.json")
        self.assertEqual(list(sent), ["muteGame", "seq"])
        self.assertIs(sent["muteGame"], True)
        for body in ({"type": "audio", "muteGame": "yes"}, {"type": "audio"}):
            self.assertEqual(self.post("/api/control", body), 400, body)

    def test_confirm_is_a_bare_press(self):
        self.assertEqual(self.post("/api/control", {"type": "confirm"}), 200)
        self.assertEqual(list(self.command_file("townfall-companion-confirm.json")), ["seq"])

    def test_unknown_commands_are_refused(self):
        self.assertEqual(self.post("/api/control", {"type": "calibrate", "phoneHeading": 10}), 400)
        self.assertEqual(self.post("/api/control", {"yaw": 10}), 400)
        for kind in (["crtv"], {"crtv": 1}, 7, None):  # not even a name
            self.assertEqual(self.post("/api/control", {"type": kind, "yaw": 10}), 400, kind)

    def test_a_body_that_is_not_a_small_object_is_refused(self):
        self.assertEqual(self.post("/api/control", ["steer", 10]), 400)
        self.assertEqual(self.post("/api/control", {"type": "steer", "yaw": 10, "padding": "x" * 5000}), 400)


class GameLiveTest(BridgeTest):
    def test_game_live_follows_the_telemetry_file(self):
        state = json.loads(self.get("/api/state")[2])
        self.assertFalse(state["gameLive"])
        self.assertEqual(state["bridge"], 31)  # static/app.js warns about older bridges
        self.assertRegex(state["page"], r"^[0-9a-f]{12}$")  # an open page reloads when its files change
        self.telemetry.write_text(json.dumps({"t": 812.25, "world": 30.5, "player": {"x": 1, "y": 2, "yaw": 3},
                                              "signals": [{"id": "Clinic", "channel": 0.15}]}))
        time.sleep(0.5)
        state = json.loads(self.get("/api/state")[2])
        self.assertTrue(state["gameLive"])
        self.assertEqual((state["player"]["yaw"], state["signals"]), (3, [{"id": "Clinic", "channel": 0.15}]))
        self.assertEqual(state["t"], 812.25)  # the game's clock, which the phone times speech by
        self.assertEqual(state["world"], 30.5)  # the world's, which stands still while the game is paused


class SampleTimingTest(unittest.TestCase):
    def test_the_phone_calls_samples_stopped_only_after_an_unchanged_one_would_have_come(self):
        """A player standing still gets an unchanged sample only every KEEPALIVE_S (main.lua); the phone holds the
        picture, sound and alignment after SAMPLE_GAP_MS (app.js) without one, and the bridge calls the game gone
        after TELEMETRY_STALE_AFTER. Past the keepalive, the 100 ms sample loop and the bridge's 50 ms poll, short
        of the game gone."""
        main = (COMPANION.parent / "Scripts" / "main.lua").read_text(encoding="utf-8")
        page = (COMPANION / "static" / "app.js").read_text(encoding="utf-8")
        bridge = BRIDGE.read_text(encoding="utf-8")
        keepalive = float(re.search(r"^local KEEPALIVE_S = ([\d.]+)", main, re.M).group(1))
        sample_ms = int(re.search(r"^local SAMPLE_MS = (\d+)", main, re.M).group(1))
        gap = int(re.search(r"^const SAMPLE_GAP_MS = (\d+);", page, re.M).group(1))
        stale = float(re.search(r"^TELEMETRY_STALE_AFTER = ([\d.]+)", bridge, re.M).group(1))
        self.assertLess(keepalive * 1000 + sample_ms + 50, gap * 0.5)  # a late write or two still fits
        self.assertLess(gap, stale * 1000)


class TelemetryReadTest(BridgeTest):
    def test_a_moment_the_file_cant_be_read_isnt_the_game_leaving(self):
        self.telemetry.write_text(json.dumps({"t": 1.0}))
        self.state_when(lambda s: s["gameLive"])
        # Held with no sharing, as Windows holds a file while it is replaced: every read fails meanwhile.
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.restype = ctypes.c_void_p
        for _ in range(50):  # the bridge may be reading it this very moment (sharing violation): try again
            handle = kernel32.CreateFileW(str(self.telemetry), 0x80000000, 0, None, 3, 0, None)  # GENERIC_READ, OPEN_EXISTING
            if handle != ctypes.c_void_p(-1).value:
                break
            time.sleep(0.01)
        self.assertNotEqual(handle, ctypes.c_void_p(-1).value, ctypes.get_last_error())
        try:
            time.sleep(0.5)
            self.assertTrue(json.loads(self.get("/api/state")[2])["gameLive"])
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
        self.telemetry.write_text(json.dumps({"t": 2.0}))
        self.state_when(lambda s: s["t"] == 2.0 and s["gameLive"])


class CutsceneTest(BridgeTest):
    def test_the_cutscene_comes_and_goes(self):
        cutscene = {"sequence": "LS_WatchingZoesSignal", "sequenceTime": 1.5}
        self.telemetry.write_text(json.dumps({"cutscene": cutscene}))
        self.state_when(lambda s: s["cutscene"] == cutscene)
        self.telemetry.write_text(json.dumps({"cutscene": None}))
        self.state_when(lambda s: s["cutscene"] is None)


class StartupTest(unittest.TestCase):
    """How the bridge starts: its settings file, its port, and what it says."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)  # see ReplaceOlderTest
        self.root = Path(self.tmp.name)
        self.bridges = []

    def tearDown(self):
        for bridge in self.bridges:
            bridge.kill()
            bridge.wait(timeout=5)
            bridge.stdout.close()
        self.tmp.cleanup()

    def start(self, *args, bridge=BRIDGE):
        """Starts a bridge; its output up to the line with its address, and the port it says."""
        process = subprocess.Popen(
            [sys.executable, "-u", str(bridge), "--host", "127.0.0.1",
             "--telemetry-file", str(self.root / f"telemetry{len(self.bridges)}.json"),  # one each: they would see each other
             "--pin", "", *args],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.bridges.append(process)
        lines = self.lines = queue.Queue()
        # Read it all, so the request log can't fill the pipe and stall the bridge.
        threading.Thread(target=lambda: [lines.put(line) for line in process.stdout], daemon=True).start()
        output = ""
        deadline = time.time() + 15
        while "Listening on" not in output:
            try:
                output += lines.get(timeout=max(0.1, deadline - time.time()))
            except queue.Empty:
                self.fail(f"the bridge didn't start (exit code {process.poll()}): {output}")
        return output, int(output.split("Listening on http://127.0.0.1:")[1].split()[0])

    def get(self, port, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
            return r.status, r.read()

    def test_the_settings_file_is_written_with_the_defaults(self):
        settings = self.root / "companion.ini"
        self.start("--settings", str(settings), "--port", str(free_port()))
        text = settings.read_text(encoding="utf-8")
        self.assertIn("port = 8790", text)
        self.assertIn("listen = 0.0.0.0", text)
        pin = [line for line in text.splitlines() if line.startswith("pin =")][0].split("=")[1].strip()
        self.assertRegex(pin, r"^\d{4}$")  # a new settings file gets a random PIN

    def test_the_phones_mode_is_shown_when_it_changes_not_each_time_the_phone_says_it(self):
        _, port = self.start("--settings", str(self.root / "companion.ini"), "--port", str(free_port()))
        for selector in ("VIEW", "VIEW", "VIEW", "AV_OUT"):
            request = urllib.request.Request(f"http://127.0.0.1:{port}/api/control", method="POST",
                                             headers={"Content-Type": "application/json"},
                                             data=json.dumps({"type": "mode", "selector": selector,
                                                              "onMonitor": False, "miniGameShown": False}).encode())
            with urllib.request.urlopen(request, timeout=5) as r:
                self.assertEqual(r.status, 200)
        output = ""
        while "phone switched to AV_OUT" not in output:
            output += self.lines.get(timeout=5)
        self.assertEqual(output.count("phone switched to VIEW"), 1)

    def test_the_port_comes_from_the_settings(self):
        port = free_port()
        settings = self.root / "companion.ini"
        settings.write_text(f"[bridge]\nport = {port}\n", encoding="utf-8")
        output, used = self.start("--settings", str(settings))
        self.assertEqual(used, port)
        self.assertNotIn("is taken", output)
        self.assertEqual(self.get(used, "/api/state")[0], 200)

    def test_a_taken_port_moves_on_to_a_free_one(self):
        with socket.socket() as other:  # another program on the port
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                other.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            other.bind(("127.0.0.1", 0))
            other.listen()
            taken = other.getsockname()[1]
            output, used = self.start("--settings", str(self.root / "companion.ini"), "--port", str(taken))
            self.assertGreater(used, taken)
            self.assertIn(f"Port {taken} is taken by another program, so the companion uses port {used}.", output)
            self.assertEqual(self.get(used, "/api/state")[0], 200)

    def test_a_port_that_isnt_a_number_is_said_plainly(self):
        settings = self.root / "companion.ini"
        settings.write_text("[bridge]\nport = eighty\n", encoding="utf-8")
        run = subprocess.run([sys.executable, str(BRIDGE), "--settings", str(settings)],
                             capture_output=True, text=True, timeout=15)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("port must be a number from 1 to 65535, not 'eighty'", run.stderr)

    def test_its_settings_live_in_the_mods_folder(self):
        mod = self.root / "Townfall" / "Binaries" / "Win64" / "ue4ss" / "Mods" / "TownfallCompanion"
        shutil.copytree(COMPANION, mod / "companion", ignore=shutil.ignore_patterns("__pycache__"))
        self.start("--port", str(free_port()), bridge=mod / "companion" / "bridge.py")
        self.assertTrue((mod / "companion.ini").is_file())

    def test_the_startup_says_only_what_a_player_needs(self):
        output, _ = self.start("--settings", str(self.root / "companion.ini"), "--port", str(free_port()))
        self.assertTrue(output.startswith("Townfall Companion "), output)
        for noise in ("Settings:", "Game:", "Game sounds:", "Game sounds ready", "CRTV stream:",
                      "UE4SS console: disabled", "On this PC", "On the phone: http"):
            self.assertNotIn(noise, output)


class ConfigTest(unittest.TestCase):
    """Where config.py looks for things."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(COMPANION))
        import config
        cls.config = config

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.saved = self.config.MOD_DIR
        self.config.MOD_DIR = self.root

    def tearDown(self):
        self.config.MOD_DIR = self.saved
        self.tmp.cleanup()

    def test_the_window_is_there_unless_the_settings_turn_it_off(self):
        settings = self.root / "companion.ini"
        self.assertTrue(self.config.window_wanted(settings), "no settings file yet: the first start")
        self.assertFalse(settings.exists(), "and none made by asking")
        for text, wanted in (("[bridge]\nport = 8790\n", True), ("[bridge]\nwindow = on\n", True),
                             ("[bridge]\nwindow = off\n", False), ("[bridge]\nwindow = maybe\n", False),
                             ("not a settings file", False)):
            with self.subTest(text=text):
                settings.write_text(text, encoding="utf-8")
                self.assertIs(self.config.window_wanted(settings), wanted)
        self.assertIn("\nwindow = on\n", self.config.DEFAULT_SETTINGS)

    def test_the_pin_is_digits_or_empty_and_existing_settings_have_none(self):
        settings = self.root / "companion.ini"
        settings.write_text("[bridge]\nport = 8790\n", encoding="utf-8")  # a file from before PINs
        self.assertEqual(self.config.load(settings).pin, "")
        for good in ("123456", "1234", ""):
            settings.write_text(f"[bridge]\npin = {good}\n", encoding="utf-8")
            self.assertEqual(self.config.load(settings).pin, good)
        for bad in ("123", "12ab56", "1234567890123"):
            settings.write_text(f"[bridge]\npin = {bad}\n", encoding="utf-8")
            with self.assertRaises(SystemExit):
                self.config.load(settings)

    def test_a_settings_file_from_an_earlier_version_still_loads(self):
        settings = self.root / "companion.ini"
        settings.write_text('[bridge]\npin = 2468\n[paths]\ngame = "D:\\Games\\Townfall"\nvgmstream =\n',
                            encoding="utf-8")  # 1.x had the game's and vgmstream's folders
        loaded = self.config.load(settings)
        self.assertEqual((loaded.pin, loaded.port, loaded.listen), ("2468", 8790, "0.0.0.0"))
        self.assertNotIn("[paths]", self.config.DEFAULT_SETTINGS)

    def test_a_note_after_a_value(self):
        settings = self.root / "companion.ini"
        settings.write_text("[bridge]\nport = 8795 ; mine, 8790 was taken\n", encoding="utf-8")
        self.assertEqual(self.config.load(settings).port, 8795)

    def test_the_ue4ss_consoles_are_switched_off_and_the_rest_of_the_file_is_kept(self):
        ini = self.root / "UE4SS-settings.ini"
        ini.write_bytes(b"[Debug]\r\nConsoleEnabled = 1 ; old\r\nGuiConsoleEnabled=1\r\nGuiConsoleVisible = 1\r\n"
                        b"[Other]\r\nFoo = 1\r\n")
        self.assertTrue(self.config.disable_ue4ss_console(ini))
        self.assertEqual(ini.read_bytes(), b"[Debug]\r\nConsoleEnabled = 0 ; old\r\nGuiConsoleEnabled=0\r\n"
                                           b"GuiConsoleVisible = 0\r\n[Other]\r\nFoo = 1\r\n")
        self.assertFalse(self.config.disable_ue4ss_console(ini))  # already off: untouched

    def test_missing_ue4ss_console_keys_are_added_under_debug_in_order(self):
        ini = self.root / "UE4SS-settings.ini"
        ini.write_bytes(b"[Debug]\nFoo = 1")  # no key, no line break at the end
        self.assertTrue(self.config.disable_ue4ss_console(ini))
        self.assertEqual(ini.read_bytes(), b"[Debug]\nConsoleEnabled = 0\nGuiConsoleEnabled = 0\n"
                                           b"GuiConsoleVisible = 0\nFoo = 1")
        ini.write_bytes(b"; keys the user commented out\r\n[Debug]\r\n; ConsoleEnabled = 1\r\nGuiConsoleVisible = 1\r\n")
        self.assertTrue(self.config.disable_ue4ss_console(ini))
        self.assertEqual(ini.read_bytes(), b"; keys the user commented out\r\n[Debug]\r\nConsoleEnabled = 0\r\n"
                                           b"GuiConsoleEnabled = 0\r\n; ConsoleEnabled = 1\r\nGuiConsoleVisible = 0\r\n")

    def test_without_a_debug_section_one_is_added_and_the_rest_is_kept(self):
        ini = self.root / "UE4SS-settings.ini"
        ini.write_bytes(b"[Overlay]\r\nWidth=  \r\n")  # trailing spaces stay
        self.assertTrue(self.config.disable_ue4ss_console(ini))
        self.assertEqual(ini.read_bytes(), b"[Overlay]\r\nWidth=  \r\n\r\n[Debug]\r\nConsoleEnabled = 0\r\n"
                                           b"GuiConsoleEnabled = 0\r\nGuiConsoleVisible = 0\r\n")

    def test_no_file_is_not_a_problem_but_an_unwritable_one_is_reported(self):
        self.assertIsNone(self.config.disable_ue4ss_console(self.root / "missing.ini"))
        unreadable = self.root / "folder.ini"
        unreadable.mkdir()  # reading it fails on every platform
        with self.assertRaises(OSError):
            self.config.disable_ue4ss_console(unreadable)

    def test_a_failed_replace_leaves_the_file_and_no_scraps(self):
        ini = self.root / "UE4SS-settings.ini"
        ini.write_bytes(b"[Debug]\nConsoleEnabled = 1\n")
        real_replace = self.config.os.replace
        self.config.os.replace = lambda *args: (_ for _ in ()).throw(PermissionError("locked"))
        try:
            with self.assertRaises(PermissionError):
                self.config.disable_ue4ss_console(ini)
        finally:
            self.config.os.replace = real_replace
        self.assertEqual(ini.read_bytes(), b"[Debug]\nConsoleEnabled = 1\n")
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["UE4SS-settings.ini"])


class PinTest(BridgeTest):
    extra_args = ("--pin", "123456")

    def login(self, pin):
        request = urllib.request.Request(self.url("/login"), data=json.dumps({"pin": pin}).encode(),
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=5) as r:
                return r.status, r.headers.get("Set-Cookie")
        except urllib.error.HTTPError as e:
            return e.code, None

    def test_nothing_opens_without_the_pin(self):
        status, _, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b'placeholder="PIN"', body)  # the PIN page, not the app
        for path in ("/api/state", "/events", "/app.js", "/api/audio/stream", "/api/crtv/frame"):
            self.assertEqual(self.get(path)[0], 401, path)
        self.assertEqual(self.post("/api/control", {"type": "crtv", "frequency": 0.4}), 401)
        self.assertFalse((self.telemetry.parent / "townfall-companion-commands.json").exists())

    def test_the_right_pin_opens_it_for_that_phone(self):
        self.assertEqual(self.login("654321")[0], 401)
        status, cookie = self.login("123456")
        self.assertEqual(status, 200)
        self.assertIn("HttpOnly", cookie)
        mine = {"Cookie": cookie.split(";")[0]}
        self.assertEqual(self.get("/api/state", mine)[0], 200)
        self.assertNotIn(b'placeholder="PIN"', self.get("/", mine)[2])
        self.assertEqual(self.get("/api/state", {"Cookie": "tfc_pin=forged"})[0], 401)

    def test_the_connection_check_needs_no_pin_and_shows_nothing_of_the_game(self):
        status, headers, body = self.get("/check")
        page = body.decode("utf-8")
        self.assertEqual(status, 200)
        self.assertIn(f"Now open http://127.0.0.1:{self.port} and enter the PIN.", page)
        self.assertIsNone(headers["Set-Cookie"])  # it doesn't let the phone in
        for secret in ("123456", "gameLive", "enemies", "signals", "crtv"):
            self.assertNotIn(secret, page)
        self.assertEqual(self.get("/api/state")[0], 401)  # and the rest stays shut


class PinLockoutTest(PinTest):
    def test_too_many_wrong_pins_lock_the_address_for_a_while(self):
        for _ in range(5):
            self.assertEqual(self.login("000000")[0], 401)
        self.assertEqual(self.login("123456")[0], 429)  # even the right one, now


class HeartbeatTest(BridgeTest):
    def beat(self):
        return json.loads((self.telemetry.parent / "townfall-companion-bridge.json").read_text(encoding="utf-8"))

    def test_the_companion_says_it_is_here_and_how_many_phones_listen(self):
        deadline = time.time() + 5
        while not (self.telemetry.parent / "townfall-companion-bridge.json").exists() and time.time() < deadline:
            time.sleep(0.1)
        first = self.beat()
        self.assertEqual((first["port"], first["phones"]), (self.port, 0))
        self.assertLess(abs(time.time() - first["time"]), 3)
        stream = urllib.request.urlopen(self.url("/events"), timeout=5)  # a phone opens the page
        try:
            time.sleep(2.2)
            self.assertEqual(self.beat()["phones"], 1)
        finally:
            stream.close()

    def test_a_second_companion_doesnt_start(self):
        result = subprocess.run([sys.executable, str(BRIDGE), "--telemetry-file", str(self.telemetry),
                                 "--settings", str(self.telemetry.parent / "second.ini"), "--pin", ""],
                                capture_output=True, text=True, timeout=20)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already running", result.stderr)


class ReplaceOlderTest(unittest.TestCase):
    def test_a_companion_from_before_an_update_is_replaced(self):
        # The virus scanner can still hold the heartbeat the companion just wrote when the folder goes.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            root, old_port = Path(folder), free_port()
            # An older companion: it listens, and its heartbeat doesn't give a version.
            old = subprocess.Popen([sys.executable, "-c", "import socket, time; s = socket.socket(); "
                                    f"s.bind(('127.0.0.1', {old_port})); s.listen(); time.sleep(60)"])
            new = None
            try:
                for _ in range(50):
                    try:
                        socket.create_connection(("127.0.0.1", old_port), timeout=0.2).close()
                        break
                    except OSError:
                        time.sleep(0.1)
                (root / "townfall-companion-bridge.json").write_text(json.dumps(
                    {"time": int(time.time()), "pid": old.pid, "port": old_port, "phones": 0}))
                new = subprocess.Popen(
                    [sys.executable, "-u", str(BRIDGE), "--host", "127.0.0.1", "--port", str(free_port()),
                     "--telemetry-file", str(root / "telemetry.json"), "--settings", str(root / "companion.ini"),
                     "--pin", ""],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                output = ""
                for line in new.stdout:
                    output += line
                    if "Listening on" in line:
                        break
                self.assertIn("this one replaces it", output)
                self.assertIsNotNone(old.wait(timeout=5), "the older one ended")
                self.assertIsNone(new.poll(), "the new one runs")
            finally:
                for process in (old, new):
                    if process:
                        process.kill()
                        process.wait(timeout=5)
                        if process.stdout:
                            process.stdout.close()


class BannerTest(unittest.TestCase):
    def test_the_address_and_pin_are_framed_for_the_companions_window(self):
        sys.path.insert(0, str(COMPANION))
        try:
            import bridge
        finally:
            sys.path.remove(str(COMPANION))
        bridge.phone_info.clear()
        bridge.phone_info.update(pin="123456", urls=["http://192.168.1.50:8790"])
        banner = bridge.connect_banner()
        self.assertIn("Open this on the phone: http://192.168.1.50:8790", banner)
        self.assertIn("PIN: 123456", banner)
        widths = {len(line) for line in banner.strip("\n").splitlines()}
        self.assertEqual(len(widths), 1)  # a closed frame
        bridge.phone_info.update(pin=None)
        self.assertIn("PIN: none", bridge.connect_banner())
        bridge.phone_info.clear()


class GameProfileStartupTest(unittest.TestCase):
    """At its start, after a game update, the companion reads the game's new version for tf_native.dll
    (game_profile.py), in the background: said only when it fails, and then only where to read what to do; the rest
    goes on whatever comes of it."""

    def setUp(self):
        sys.path.insert(0, str(COMPANION))
        try:
            import bridge
            import config
            import game_profile
        finally:
            sys.path.remove(str(COMPANION))
        self.bridge, self.config, self.game_profile = bridge, config, game_profile
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.game = Path(tmp.name)
        self.exe = self.game / game_profile.GAME_EXE
        self.exe.parent.mkdir(parents=True)
        self.exe.write_bytes(b"MZ")
        saved = config.installed_game_dir, game_profile.needed, game_profile.write_apart
        self.addCleanup(self.restore, saved)
        self.written = []
        config.installed_game_dir = lambda: self.game
        game_profile.needed = lambda exe: True
        game_profile.write_apart = self.write

    def write(self, exe):
        self.written.append(exe)

    def restore(self, saved):
        self.config.installed_game_dir, self.game_profile.needed, self.game_profile.write_apart = saved

    def said(self):
        import contextlib
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.bridge.prepare_game_profile()
        return out.getvalue()

    def test_a_new_version_of_the_game_is_read_without_a_word(self):
        self.assertEqual((self.said(), self.written), ("", [self.exe]))

    def test_nothing_is_read_or_said_when_it_was_read_already_or_the_mod_isnt_in_a_game(self):
        self.game_profile.needed = lambda exe: False
        self.assertEqual((self.said(), self.written), ("", []))
        self.game_profile.needed = lambda exe: True
        self.config.installed_game_dir = lambda: None
        self.assertEqual((self.said(), self.written), ("", []))

    def test_any_failure_says_the_same_and_where_to_read_how_to_fix_it(self):
        def changed(exe):
            raise ValueError("changed: FRHICommandListImmediate::EndDrawingViewport")

        def unreadable(exe):
            raise FileNotFoundError("Townfall-Win64-Shipping.pdb")
        for failure in (changed, unreadable):
            self.game_profile.write_apart = failure
            self.assertEqual(self.said(), "The CRTV's picture can't be streamed to the phone. How to fix it: see "
                             '"The CRTV\'s picture can\'t be streamed" in docs\\TROUBLESHOOTING.md '
                             "(in the mod's download).\n")


class CleanupTest(unittest.TestCase):
    def test_everything_left_in_the_temp_folder_is_removed(self):
        sys.path.insert(0, str(COMPANION))
        try:
            import bridge
        finally:
            sys.path.remove(str(COMPANION))
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            telemetry = folder / "townfall-companion-telemetry.json"
            names = ["townfall-companion-commands.json", "townfall-companion-steer.json",
                     "townfall-companion-confirm.json", "townfall-companion-audio.json",
                     "townfall-companion-bridge.json", "townfall-companion-game.json"]
            for path in [telemetry, *(folder / n for n in names)]:
                path.write_text("{}")
            (folder / "somebody-elses.json").write_text("{}")
            bridge.remove_ipc_files(telemetry)
            self.assertEqual([p.name for p in folder.iterdir()], ["somebody-elses.json"])
            bridge.remove_ipc_files(telemetry)  # nothing left: no error


class GameExitTest(unittest.TestCase):
    def test_the_game_is_running_while_its_heartbeat_is_recent(self):
        sys.path.insert(0, str(COMPANION))
        try:
            import bridge
        finally:
            sys.path.remove(str(COMPANION))
        with tempfile.TemporaryDirectory() as tmp:
            beat = Path(tmp) / "game.json"
            self.assertFalse(bridge.read_game_beat(beat, 60))
            beat.write_text(json.dumps({"time": int(time.time())}))
            self.assertTrue(bridge.read_game_beat(beat, 60))
            beat.write_text(json.dumps({"time": int(time.time()) - 200}))
            self.assertFalse(bridge.read_game_beat(beat, 60))

    def start(self, folder):
        return subprocess.Popen(
            [sys.executable, "-u", str(BRIDGE), "--host", "127.0.0.1", "--port", str(free_port()),
             "--telemetry-file", str(folder / "townfall-companion-telemetry.json"), "--settings", str(folder / "c.ini"),
             "--pin", "",
             "--game-gone-after", "3"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def test_the_companion_closes_when_a_game_it_has_seen_is_gone(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            game = folder / "townfall-companion-game.json"
            running = threading.Event()
            running.set()

            def say_it_runs():  # as the mod does, rewriting the file in place
                while running.is_set():
                    game.write_text(json.dumps({"time": int(time.time())}))
                    time.sleep(0.2)

            writer = threading.Thread(target=say_it_runs, daemon=True)
            writer.start()
            process = self.start(folder)
            try:
                time.sleep(4)
                self.assertIsNone(process.poll())  # it keeps running while the game says so
                running.clear()
                writer.join()
                game.write_text(json.dumps({"time": int(time.time()) - 100}))  # and stops saying so
                self.assertEqual(process.wait(timeout=15), 0)  # a normal exit: the window closes
            finally:
                running.clear()
                if process.poll() is None:
                    process.kill()
                    process.wait()
            self.assertEqual([p.name for p in folder.glob("townfall-companion-*")], [])  # its files are gone too

    def test_a_heartbeat_read_mid_write_is_not_the_game_leaving(self):
        sys.path.insert(0, str(COMPANION))
        try:
            import bridge
        finally:
            sys.path.remove(str(COMPANION))
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            game = folder / bridge.GAME_FILE
            game.write_text(json.dumps({"time": time.time()}))
            closed = threading.Event()
            server = types.SimpleNamespace(shutdown=closed.set)
            threading.Thread(target=bridge.heartbeat_loop, args=(folder / "t.json", 0, server, 3), daemon=True).start()
            time.sleep(1.5)
            game.write_text("")  # read while the mod rewrites it
            time.sleep(1.2)
            self.assertFalse(closed.is_set())
            self.assertTrue(closed.wait(timeout=5))  # no newer beat came: gone 3 s after the last one
            bridge.close_heartbeats()  # as main() does as it ends

    def test_started_without_the_game_it_keeps_running(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:  # see ReplaceOlderTest
            process = self.start(Path(tmp))
            try:
                time.sleep(6)
                self.assertIsNone(process.poll())
            finally:
                process.kill()
                process.wait()


class WindowHookTest(unittest.TestCase):
    """What the companion's window (gui.py) needs from the bridge: main() in a thread, the addresses that got
    through to it, and stop()."""
    HARNESS = "\n".join([
        "import sys, threading",
        "sys.path.insert(0, sys.argv.pop(1))",
        "import bridge",
        "thread = threading.Thread(target=bridge.main)",
        "thread.start()",
        "sys.stdin.readline()",
        "print('visitors', sorted(bridge.visitors), flush=True)",
        "bridge.stop()",
        "thread.join(10)",
        "print('stopped', not thread.is_alive(), flush=True)",
    ])

    def test_main_runs_in_a_thread_knows_who_got_through_and_stops(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:  # see ReplaceOlderTest
            folder = Path(tmp)
            process = subprocess.Popen(
                [sys.executable, "-u", "-c", self.HARNESS, str(COMPANION), "--host", "127.0.0.1",
                 "--port", str(free_port()), "--telemetry-file", str(folder / "townfall-companion-telemetry.json"),
                 "--settings", str(folder / "c.ini"), "--pin", "4321"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            try:
                output = ""
                while "Listening on" not in output:
                    line = process.stdout.readline()
                    if not line:
                        self.fail(f"the bridge didn't start: {output}")
                    output += line
                port = int(output.split("Listening on http://127.0.0.1:")[1].split()[0])
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/check", timeout=5) as r:
                    self.assertEqual(r.status, 200)
                output, _ = process.communicate("\n", timeout=30)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
            self.assertIn("visitors ['127.0.0.1']", output)  # the window tells this PC's own visits apart itself
            self.assertIn("stopped True", output)
            self.assertEqual(process.returncode, 0)


class LauncherTest(unittest.TestCase):
    """Start Companion.py, the double-click launcher. With tkinter it opens the companion's window (gui.py), from a
    copy of itself without a console; without, as here, it runs bridge.py in the console and keeps that window open
    on errors."""
    LAUNCHER = COMPANION.parent / "Start Companion.py"
    # As a Python installed without tkinter (an option in its installer): importing it fails.
    WITHOUT_TKINTER = ("import runpy, sys; sys.modules['tkinter'] = None; sys.argv.pop(0); "
                       "runpy.run_path(sys.argv[0], run_name='__main__')")

    def launcher(self, *args):
        return [sys.executable, "-u", "-c", self.WITHOUT_TKINTER, str(self.LAUNCHER), *args]

    def run_launcher(self, *args):
        return subprocess.run(self.launcher(*args), input="\n", capture_output=True, text=True, timeout=30)

    def test_without_window_on_in_the_settings_it_stays_in_the_console(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:  # see ReplaceOlderTest
            folder = Path(tmp)
            (folder / "c.ini").write_text("[bridge]\nwindow = off\n", encoding="utf-8")
            process = subprocess.Popen(  # with tkinter: only the setting keeps the window away
                [sys.executable, "-u", str(self.LAUNCHER), "--host", "127.0.0.1", "--port", str(free_port()),
                 "--pin", "", "--settings", str(folder / "c.ini"),
                 "--telemetry-file", str(folder / "townfall-companion-telemetry.json")],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            try:
                output = ""
                while "Listening on" not in output:
                    line = process.stdout.readline()
                    if not line:
                        self.fail(f"the companion didn't start: {output}")
                    output += line
            finally:
                process.kill()
                process.wait()
                process.stdout.close()

    def test_without_tkinter_it_runs_in_the_console_as_before(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:  # see ReplaceOlderTest
            folder = Path(tmp)
            process = subprocess.Popen(
                self.launcher("--host", "127.0.0.1", "--port", str(free_port()), "--pin", "2468",
                              "--settings", str(folder / "c.ini"),
                              "--telemetry-file", str(folder / "townfall-companion-telemetry.json")),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            try:
                output = ""
                while "The phone asks for the PIN once" not in output:
                    line = process.stdout.readline()
                    if not line:
                        self.fail(f"the companion didn't start: {output}")
                    output += line
                self.assertIn("PIN: 2468", output)
                port = int(output.split("Listening on http://127.0.0.1:")[1].split()[0])
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/check", timeout=5) as r:
                    self.assertEqual(r.status, 200)
            finally:
                process.kill()
                process.wait()
                process.stdout.close()

    @unittest.skipUnless(sys.platform == "win32", "consoles and their windows are Windows'")
    def test_for_the_window_it_hands_over_to_the_same_python_without_a_console(self):
        launcher = runpy.run_path(str(self.LAUNCHER), run_name="launcher")  # its functions, without starting it
        started, saved = [], sys.argv
        sys.argv = [str(COMPANION / "bridge.py"), "--port", "18790"]
        try:
            handed_over = launcher["leave_console"](None, lambda command, **options: started.append((command, options)))
        finally:
            sys.argv = saved
        self.assertTrue(handed_over)
        (command, options), = started
        # This very python.exe (the firewall's rule is for it), this file, its arguments too.
        self.assertEqual(command, [sys.executable, str(self.LAUNCHER.resolve()), "--port", "18790"])
        self.assertEqual(options["env"][launcher["WINDOW_ONLY"]], "1")  # that copy doesn't hand over again
        self.assertTrue(options["creationflags"] & subprocess.CREATE_NO_WINDOW)  # no console window at all

    def test_without_its_python_it_stays_in_the_console(self):
        launcher = runpy.run_path(str(self.LAUNCHER), run_name="launcher")
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(launcher["leave_console"](Path(tmp) / "python.exe", lambda *a, **k: self.fail("started")))

    def test_it_runs_the_bridge(self):
        result = self.run_launcher("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("usage: bridge.py", result.stdout)

    def test_a_problem_stays_on_screen_until_enter_is_pressed(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Path(tmp) / "companion.ini"
            settings.write_text("[bridge]\npin = 12\n", encoding="utf-8")
            result = self.run_launcher("--settings", str(settings), "--telemetry-file", str(Path(tmp) / "t.json"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("pin must be 4 to 12 digits", result.stdout)
        self.assertIn("Press Enter to close this window.", result.stdout)


if __name__ == "__main__":
    unittest.main()
