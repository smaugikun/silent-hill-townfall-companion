"""Runs the bridge on a free local port against temp folders. Standard library only.

    python -m unittest discover -s tests -v
"""
import ctypes
import json
import queue
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
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
    vgmstream = True  # False: the bridge gets a vgmstream that isn't there

    # The fake game's sound banks (tests/fake_vgmstream.py reads them): stream names, one per line.
    BANKS = {
        "Environment": ["CRTV_NoSignal_Loop", "CRTV_Tuning_StaticClick_01", "AMB_Rain_Loop"],
        "PlayerFoley": ["PROP_HAP_CRTV_Rise", "FS_Step_01"],
        "Cinematics_EN": ["WakeUp_71_DX", "WakeUp_20_MX"],
        "Dialogue_EN": ["10c5"],
    }

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.clips = root / "clips"
        (cls.clips / "Bink").mkdir(parents=True)
        cls.clip_bytes = bytes(range(256)) * 40
        (cls.clips / "Bink" / "Enraged_Focused.mp4").write_bytes(cls.clip_bytes)
        cls.banks = root / "Townfall-install" / "Townfall" / "Content" / "FMOD" / "Banks" / "Desktop"
        cls.banks.mkdir(parents=True)
        for bank, names in cls.BANKS.items():
            (cls.banks / f"{bank}.bank").write_text("\n".join(names), encoding="utf-8")
        (cls.banks / "BinkAudio.bank").write_text("Lazy", encoding="utf-8")
        movie = root / "Townfall-install" / "Townfall" / "Content" / "Movies" / "CRTV_Movies" / "Bink" / "Lazy.bk2"
        movie.parent.mkdir(parents=True)
        movie.write_text("bink", encoding="utf-8")
        vgmstream = root / "vgmstream.cmd"
        vgmstream.write_text(f'@"{sys.executable}" "{Path(__file__).with_name("fake_vgmstream.py")}" %*\n')
        radvideo = root / "radvideo.cmd"
        ffmpeg = root / "ffmpeg.cmd"
        radvideo.write_text(f'@"{sys.executable}" "{Path(__file__).with_name("fake_video_tools.py")}" radvideo %*\n')
        ffmpeg.write_text(f'@"{sys.executable}" "{Path(__file__).with_name("fake_video_tools.py")}" ffmpeg %*\n')
        (root / "secret.txt").write_text("not a clip")
        cls.telemetry = root / "game" / "townfall-companion-telemetry.json"
        cls.telemetry.parent.mkdir()
        cls.port = free_port()
        cls.bridge = subprocess.Popen(
            [sys.executable, "-u", str(BRIDGE), "--host", "127.0.0.1", "--port", str(cls.port),
             "--clips-dir", str(cls.clips), "--telemetry-file", str(cls.telemetry),
             "--game-dir", str(root / "Townfall-install"), "--sound-cache", str(root / "sound-cache"),
             "--vgmstream", str(vgmstream if cls.vgmstream else root / "missing" / "vgmstream-cli.exe"),
             "--radvideo", str(radvideo), "--ffmpeg", str(ffmpeg),
             "--settings", str(root / "companion.ini"), *cls.extra_args],
            # Not into a pipe: the bridge logs every request, and a pipe nobody reads fills up and blocks it.
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                urllib.request.urlopen(cls.url("/api/state"), timeout=1)
                break
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


class SoundTest(BridgeTest):
    """The game's sounds, read from its banks as the phone asks for them."""

    def decoded(self, bank):
        log = self.banks / f"{bank}.bank.decoded"
        return log.read_text(encoding="utf-8").split() if log.exists() else []

    def test_the_list_holds_what_the_phone_plays(self):
        status, _, body = self.get("/sounds/sounds.json")
        self.assertEqual((status, json.loads(body)), (200, {
            "sounds": ["CRTV_NoSignal_Loop", "CRTV_Tuning_StaticClick_01", "PROP_HAP_CRTV_Rise"],
            "dialogue": ["WakeUp_71_DX"], "lines": ["10c5"]}))

    def test_a_sound_is_decoded_once_and_kept(self):
        for path in ("/sounds/lines/10c5.wav", "/sounds/lines/10c5.wav", "/sounds/dialogue/WakeUp_71_DX.wav",
                     "/sounds/CRTV_NoSignal_Loop.wav"):
            status, headers, body = self.get(path)
            self.assertEqual((status, headers["Content-Type"], body[:4]), (200, "audio/wav", b"RIFF"), path)
        self.assertEqual(self.decoded("Dialogue_EN"), ["10c5"])
        self.assertEqual(self.decoded("Cinematics_EN"), ["WakeUp_71_DX"])
        ranged = self.get("/sounds/lines/10c5.wav", {"Range": "bytes=0-3"})  # the phone seeks in long tracks
        self.assertEqual((ranged[0], ranged[2]), (206, b"RIFF"))

    def test_only_listed_sounds(self):
        for path in ("/sounds/AMB_Rain_Loop.wav", "/sounds/lines/nope.wav", "/sounds/WakeUp_20_MX.wav",
                     "/sounds/../clips/Bink/Enraged_Focused.mp4", "/sounds/lines/..%2F..%2Fsecret.txt.wav"):
            self.assertEqual(self.get(path)[0], 404, path)


class NoVgmstreamTest(BridgeTest):
    vgmstream = False

    def test_no_sounds_and_the_reason(self):
        status, _, body = self.get("/sounds/sounds.json")
        self.assertEqual(status, 404)
        error = json.loads(body)["error"]
        self.assertIn("missing vgmstream-cli.exe (not at", error)
        self.assertIn("set vgmstream in companion.ini", error)  # and what to do about it
        self.assertEqual(self.get("/sounds/CRTV_NoSignal_Loop.wav")[0], 404)
        self.assertEqual(self.get("/api/state")[0], 200)  # the rest carries on


class ClipTest(BridgeTest):
    def test_whole_clip(self):
        status, headers, body = self.get("/clips/Bink/Enraged_Focused.mp4")
        self.assertEqual((status, headers["Content-Type"], body), (200, "video/mp4", self.clip_bytes))

    def test_an_uncached_game_video_is_converted_once_when_requested(self):
        path = "/clips/Bink/Lazy.mp4"
        self.assertFalse((self.clips / "Bink" / "Lazy.mp4").exists())
        status, headers, body = self.get(path)
        made = json.loads(body)
        self.assertEqual((status, headers["Content-Type"], made["video"]), (200, "video/mp4", "copy"))
        decoded = self.banks / "BinkAudio.bank.decoded"
        self.assertEqual(decoded.read_text(encoding="utf-8").split(), ["Lazy"])

        # The second request is the cached MP4: no second RAD/FFmpeg/vgmstream conversion.
        status, _, again = self.get(path)
        self.assertEqual((status, json.loads(again)), (200, made))
        self.assertEqual(decoded.read_text(encoding="utf-8").split(), ["Lazy"])

    def test_byte_ranges(self):
        for header, expected in (("bytes=100-199", self.clip_bytes[100:200]),
                                 ("bytes=10000-", self.clip_bytes[10000:]),
                                 ("bytes=-50", self.clip_bytes[-50:])):
            status, _, body = self.get("/clips/Bink/Enraged_Focused.mp4", {"Range": header})
            self.assertEqual((status, body), (206, expected), header)
        self.assertEqual(self.get("/clips/Bink/Enraged_Focused.mp4", {"Range": "bytes=20000-"})[0], 416)

    def test_nothing_outside_the_clips_folder(self):
        for path in ("/clips/../secret.txt", "/clips/..%2Fsecret.txt", "/clips/Bink/missing.mp4"):
            self.assertEqual(self.get(path)[0], 404, path)


class StaticTest(BridgeTest):
    def test_page_files_are_revalidated(self):
        for path in ("/", "/style.css", "/app.js", "/crtv/layers.css"):
            status, headers, _ = self.get(path)
            self.assertEqual((status, headers["Cache-Control"]), (200, "no-cache"), path)
        with urllib.request.urlopen(urllib.request.Request(self.url("/style.css"), method="HEAD"), timeout=5) as r:
            self.assertEqual(r.headers["Cache-Control"], "no-cache")
        self.assertEqual(self.get("/app.js")[1]["Content-Type"], "text/javascript")
        self.assertEqual(self.get("/monster/lunger.webp")[1]["Content-Type"], "image/webp")

    def test_clips_and_api_keep_their_own_caching(self):
        _, headers, _ = self.get("/clips/Bink/Enraged_Focused.mp4")
        self.assertEqual(headers["Cache-Control"], "no-cache")  # re-converted clips must reach the phone
        again = self.get("/clips/Bink/Enraged_Focused.mp4", {"If-Modified-Since": headers["Last-Modified"]})
        self.assertEqual(again[0], 304)
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

    def test_steer_command_and_rising_seq(self):
        self.post("/api/control", {"type": "steer", "yaw": 370, "pitch": -100})
        first = self.command_file("townfall-companion-steer.json")
        self.assertEqual(first["pitch"], -90.0)  # the tilt, kept to straight down
        self.post("/api/control", {"type": "steer", "yaw": -10})
        second = self.command_file("townfall-companion-steer.json")
        self.assertEqual((first["yaw"], second["yaw"]), (10.0, 350.0))
        self.assertGreater(second["seq"], first["seq"])

    def test_bad_commands_are_refused(self):
        for body in ({"type": "crtv", "active": "yes", "frequency": 0.2}, {"type": "crtv", "active": True},
                     {"type": "steer", "yaw": "north"}):
            self.assertEqual(self.post("/api/control", body), 400, body)

    def test_sound_request_for_the_game(self):
        self.assertEqual(self.post("/api/control", {"type": "audio", "muteGame": True}), 200)
        sent = self.command_file("townfall-companion-audio.json")
        self.assertEqual((sent["muteGame"], sent["dialogue"], sent["video"]), (True, False, False))
        self.assertEqual(self.post("/api/control", {"type": "audio", "muteGame": True, "video": True}), 200)
        self.assertEqual(self.command_file("townfall-companion-audio.json")["video"], True)
        for body in ({"type": "audio", "muteGame": "yes"}, {"type": "audio", "muteGame": True, "video": "yes"}):
            self.assertEqual(self.post("/api/control", body), 400, body)

    def test_confirm_is_a_bare_press(self):
        self.assertEqual(self.post("/api/control", {"type": "confirm"}), 200)
        self.assertEqual(list(self.command_file("townfall-companion-confirm.json")), ["seq"])

    def test_unknown_commands_are_refused(self):
        self.assertEqual(self.post("/api/control", {"type": "calibrate", "phoneHeading": 10}), 400)
        self.assertEqual(self.post("/api/control", {"yaw": 10}), 400)


class GameLiveTest(BridgeTest):
    def test_game_live_follows_the_telemetry_file(self):
        state = json.loads(self.get("/api/state")[2])
        self.assertFalse(state["gameLive"])
        self.assertEqual(state["bridge"], 10)  # static/app.js warns about older bridges
        self.assertRegex(state["page"], r"^[0-9a-f]{12}$")  # an open page reloads when its files change
        self.telemetry.write_text(json.dumps({"t": 812.25, "world": 30.5, "player": {"x": 1, "y": 2, "yaw": 3},
                                              "signals": [{"id": "Clinic", "channel": 0.15}]}))
        time.sleep(0.5)
        state = json.loads(self.get("/api/state")[2])
        self.assertTrue(state["gameLive"])
        self.assertEqual((state["player"]["yaw"], state["signals"]), (3, [{"id": "Clinic", "channel": 0.15}]))
        self.assertEqual(state["t"], 812.25)  # the game's clock, which the phone times speech by
        self.assertEqual(state["world"], 30.5)  # the world's, which stands still while the game is paused


class TelemetryReadTest(BridgeTest):
    def test_a_moment_the_file_cant_be_read_isnt_the_game_leaving(self):
        self.telemetry.write_text(json.dumps({"t": 1.0}))
        self.state_when(lambda s: s["gameLive"])
        # Held with no sharing, as Windows holds a file while it is replaced: every read fails meanwhile.
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.restype = ctypes.c_void_p
        handle = kernel32.CreateFileW(str(self.telemetry), 0x80000000, 0, None, 3, 0, None)  # GENERIC_READ, OPEN_EXISTING
        self.assertNotEqual(handle, ctypes.c_void_p(-1).value, ctypes.get_last_error())
        try:
            time.sleep(0.5)
            self.assertTrue(json.loads(self.get("/api/state")[2])["gameLive"])
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
        self.telemetry.write_text(json.dumps({"t": 2.0}))
        self.state_when(lambda s: s["t"] == 2.0 and s["gameLive"])


class CutsceneTest(BridgeTest):
    def test_the_cutscene_video_comes_and_goes(self):
        video = {"video": "Cutscene_Diegetic_Movies/Bink/Cutscene_WatchingZoesSignal_1", "videoTime": 1.5}
        self.telemetry.write_text(json.dumps({"cutscene": video}))
        self.state_when(lambda s: s["cutscene"] == video)
        self.telemetry.write_text(json.dumps({"cutscene": None}))
        self.state_when(lambda s: s["cutscene"] is None)



class DemoPriorityTest(BridgeTest):
    """The demo game (--demo, for development) gives way to the real game, and comes back when it stops."""
    extra_args = ("--demo",)

    def test_the_real_game_first(self):
        state = self.state_when(lambda s: s["gameLive"] and s["demo"] and s["enemies"])
        self.assertEqual(len(state["enemies"]), 4)
        self.telemetry.write_text(json.dumps({"player": {"x": 1, "y": 2, "yaw": 3}, "enemies": [],
                                              "cutscene": {"video": "Cutscene_Diegetic_Movies/Left_Over"}}))
        self.state_when(lambda s: s["gameLive"] and not s["demo"] and s["player"]["yaw"] == 3)
        # The game stops as it really does, leaving the file unwritten (deleting it here raced the bridge
        # reading it: Windows refuses to delete an open file). The demo goes on, without the game's cutscene.
        self.state_when(lambda s: s["demo"] and s["cutscene"] is None, timeout=4)

    def test_the_phone_cant_switch_it(self):
        self.assertEqual(self.post("/api/demo", {"on": False}), 404)


class DemoTest(BridgeTest):
    extra_args = ("--demo",)

    def test_demo_game_follows_the_phone(self):
        state = self.state_when(lambda s: s["gameLive"] and s["signals"])
        self.assertTrue(state["crtv"]["active"])  # the simulated player starts with the CRTV up
        self.assertTrue(all("rangeSignal" in e and "tolerance" in e for e in state["enemies"]))

        self.post("/api/control", {"type": "crtv", "active": False, "frequency": 0.3})
        self.state_when(lambda s: not s["crtv"]["active"] and s["crtv"]["frequency"] == 0)
        self.post("/api/control", {"type": "crtv", "active": True, "frequency": 0.3})
        self.state_when(lambda s: s["crtv"]["active"] and s["crtv"]["frequency"] == 0.3)
        time.sleep(0.5)  # held by the phone: the simulated player doesn't sweep it away
        self.assertEqual(json.loads(self.get("/api/state")[2])["crtv"]["frequency"], 0.3)

        self.post("/api/control", {"type": "steer", "yaw": 350})  # the phone's heading: a starting point
        time.sleep(0.3)
        start = json.loads(self.get("/api/state")[2])["player"]["yaw"]
        self.post("/api/control", {"type": "steer", "yaw": 20})  # the phone turned 30 degrees clockwise
        self.state_when(lambda s: abs((s["player"]["yaw"] - start - 30 + 180) % 360 - 180) < 0.2)

        # On the waypoint's channel the fine-tune mini-game runs; a press on the diamond finds it, and
        # then the demo CRTV's screen plays its video, as the mod reports the game's.
        self.post("/api/control", {"type": "crtv", "active": True, "frequency": 0.15})
        state = self.state_when(lambda s: s["crtv"]["fineTune"] is not None)
        self.assertEqual((state["crtv"]["signalType"], state["crtv"]["video"]), ("waypoint", None))
        deadline = time.time() + 8
        while time.time() < deadline:  # the box passes the diamond every 1.2 s; keep pressing near it
            state = json.loads(self.get("/api/state")[2])
            if state["crtv"]["signalType"] == "waypoint_tuned":
                break
            tune = state["crtv"]["fineTune"]
            if tune and abs(tune["box"] - tune["zone"]) < 0.12:
                self.post("/api/control", {"type": "confirm"})
            time.sleep(0.03)
        self.assertEqual((state["crtv"]["video"], state["crtv"]["fineTune"], state["signals"][0]["found"]),
                         ("Bink/Video_CRTV_Room204Door_Signal", None, True))

        # On its channel the waypoint talks, clear now it is found, a line from the game's banks; each
        # sample stamped with the demo's clock.
        first = self.state_when(lambda s: (s["signals"][0]["dialogue"] or {}).get("clear"))
        said = first["signals"][0]["dialogue"]
        self.assertEqual((said["line"], said["id"]), ("10c5", "demo-waypoint"))
        later = self.state_when(lambda s: s["t"] > first["t"] + 0.5 and s["signals"][0]["dialogue"])
        progress = later["signals"][0]["dialogue"]["ms"] - said["ms"]
        self.assertAlmostEqual(progress / 1000, later["t"] - first["t"], delta=0.05)  # in step with the clock
        self.state_when(lambda s: s["signals"][0]["dialogue"] is None, timeout=3)  # 2 s long, then a breath


class StartupTest(unittest.TestCase):
    """How the bridge starts: its settings file, its port, and the game found from where it is installed."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
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
            [sys.executable, "-u", str(bridge), "--host", "127.0.0.1", "--telemetry-file", str(self.root / "telemetry.json"),
             "--sound-cache", str(self.root / "sounds"), *args],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.bridges.append(process)
        lines = queue.Queue()
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
        self.assertIn("[paths]", text)

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

    def test_the_game_is_found_from_where_the_mod_is_installed(self):
        game = self.root / "Townfall-install"
        banks = game / "Townfall" / "Content" / "FMOD" / "Banks" / "Desktop"
        banks.mkdir(parents=True)
        (banks / "Environment.bank").write_text("CRTV_NoSignal_Loop", encoding="utf-8")
        mod = game / "Townfall" / "Binaries" / "Win64" / "ue4ss" / "Mods" / "TownfallCompanion"
        shutil.copytree(COMPANION, mod / "companion", ignore=shutil.ignore_patterns("__pycache__"))
        vgmstream = self.root / "vgmstream.cmd"
        vgmstream.write_text(f'@"{sys.executable}" "{Path(__file__).with_name("fake_vgmstream.py")}" %*\n')
        output, port = self.start("--port", str(free_port()), "--vgmstream", str(vgmstream),
                                  bridge=mod / "companion" / "bridge.py")
        self.assertIn(f"Game:         {game}", output)
        self.assertTrue((mod / "companion.ini").is_file())  # its settings live in the mod's folder
        status, body = self.get(port, "/sounds/sounds.json")
        self.assertEqual((status, json.loads(body)["sounds"]), (200, ["CRTV_NoSignal_Loop"]))


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
        self.saved = self.config.MOD_DIR, self.config.TOOLS_DIR
        self.config.MOD_DIR, self.config.TOOLS_DIR = self.root, self.root / "tools"

    def tearDown(self):
        self.config.MOD_DIR, self.config.TOOLS_DIR = self.saved
        self.tmp.cleanup()

    def test_a_downloaded_tool_is_found_anywhere_in_the_tools_folder(self):
        self.assertIsNone(self.config.find_tool("no-such-tool.exe"))
        unpacked = self.root / "tools" / "no-such-tool-8.0-essentials_build" / "bin" / "no-such-tool.exe"  # as zips unpack
        unpacked.parent.mkdir(parents=True)
        unpacked.write_bytes(b"")
        self.assertEqual(self.config.find_tool("no-such-tool.exe"), unpacked)

    def test_paths_in_the_settings(self):
        settings = self.root / "companion.ini"
        settings.write_text('[paths]\ngame = "D:\\Games\\Townfall"\nvgmstream = tools\\vgm\\vgmstream-cli.exe\n',
                            encoding="utf-8")
        loaded = self.config.load(settings)
        self.assertEqual(loaded.game, Path("D:/Games/Townfall"))  # quotes are fine
        self.assertEqual(loaded.vgmstream, self.root / "tools" / "vgm" / "vgmstream-cli.exe")  # from the mod folder
        self.assertEqual((loaded.port, loaded.listen), (8790, "0.0.0.0"))  # the rest stays default

    def test_a_note_after_a_value(self):
        settings = self.root / "companion.ini"
        settings.write_text("[bridge]\nport = 8795 ; mine, 8790 was taken\n", encoding="utf-8")
        self.assertEqual(self.config.load(settings).port, 8795)


if __name__ == "__main__":
    unittest.main()
