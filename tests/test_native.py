"""tf_native.dll and the companion's side of it. The DLL against the installed game's executable (its build check),
the copy and alignment code against a fake engine (native/build/tf_native_tests.dll), and the bridge reading the
frames it publishes. The DLL parts need `py native/build.py` first and are skipped without it; the game check needs
the game installed."""
import ctypes as ct
import http.client
import io
import json
import os
import struct
import sys
import time
import tempfile
import threading
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
COMPANION = ROOT / "TownfallCompanion" / "companion"
sys.path.insert(0, str(COMPANION))
import bridge  # noqa: E402
import config  # noqa: E402
from crtv_native import HEADER, MAPPING_SIZE, NativeFrames, WindowsFrames, parse_header  # noqa: E402
from game_audio import GameAudio  # noqa: E402
import game_profile  # noqa: E402

DLL = ROOT / "TownfallCompanion" / "Scripts" / "tf_native.dll"
TEST_DLL = ROOT / "native" / "build" / "tf_native_tests.dll"
GAME = config.find_game_dir()
GAME_EXE = GAME / "Townfall" / "Binaries" / "Win64" / "Townfall-Win64-Shipping.exe" if GAME else None


def private_temp(test):
    """Points this process's temp folder at a folder of its own while `test` runs: the DLL writes its state there,
    never where a running game's copy would."""
    folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)  # the DLL keeps its status file open
    saved = {key: os.environ.get(key) for key in ("TMP", "TEMP")}
    os.environ["TMP"] = os.environ["TEMP"] = folder.name

    def restore():
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        folder.cleanup()
    test.addClassCleanup(restore)
    return Path(folder.name)


@unittest.skipUnless(GAME, "Townfall isn't installed here")
class FmodNamesTest(unittest.TestCase):
    """native/audio.c finds FMOD by name: each of its libraries and functions must be in the game's FMOD plugin."""

    def test_every_fmod_library_and_function_audio_c_asks_for_is_in_the_game(self):
        import re
        import pefile
        source = (ROOT / "native" / "audio.c").read_text(encoding="utf-8")
        plugin = GAME / "Engine" / "Plugins" / "FMODStudio" / "Binaries" / "Win64"
        libraries = {name: plugin / name for name in re.findall(r'GetModuleHandleW\(L"([\w.]+)"\)', source)}
        self.assertEqual(sorted(libraries), ["fmod.dll", "fmodstudio.dll"])
        exports = {}
        for name, path in libraries.items():
            self.assertTrue(path.exists(), path)
            image = pefile.PE(str(path), fast_load=True)
            image.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_EXPORT"]])
            exports[name] = {symbol.name.decode() for symbol in image.DIRECTORY_ENTRY_EXPORT.symbols if symbol.name}
            image.close()
        wanted = re.findall(r'\{(core|studio), "(\w+)"', source)
        self.assertGreater(len(wanted), 5)
        for module, function in wanted:
            self.assertIn(function, exports["fmod.dll" if module == "core" else "fmodstudio.dll"], function)
        self.assertIn("?update@System@Studio@FMOD@@QEAA?AW4FMOD_RESULT@@XZ", exports["fmodstudio.dll"])
        self.assertIn('GetProcAddress(studio, "?update@System@Studio@FMOD@@QEAA?AW4FMOD_RESULT@@XZ")', source)


@unittest.skipUnless(DLL.exists(), "tf_native.dll isn't built (py native/build.py)")
class DllFileTest(unittest.TestCase):
    """The DLL as a file: it says what it is, and it touches no process but the game's own."""

    @classmethod
    def setUpClass(cls):
        import pefile
        cls.image = pefile.PE(str(DLL))
        cls.addClassCleanup(cls.image.close)

    def test_it_says_what_it_is_and_which_version(self):
        strings = {key.decode(): value.decode() for info in self.image.FileInfo[0] if hasattr(info, "StringTable")
                   for table in info.StringTable for key, value in table.entries.items()}
        self.assertEqual((strings["ProductName"], strings["OriginalFilename"]), ("Townfall Companion", "tf_native.dll"))
        self.assertEqual((strings["FileVersion"], strings["ProductVersion"]), (config.VERSION, config.VERSION))
        self.assertIn("MIT License", strings["LegalCopyright"])
        self.assertIn("github.com/smaugikun/silent-hill-townfall-companion", strings["Comments"])

    def test_it_reads_and_writes_no_other_process(self):
        imported = {entry.name.decode() for library in self.image.DIRECTORY_ENTRY_IMPORT
                    for entry in library.imports if entry.name}
        self.assertFalse(imported & {"ReadProcessMemory", "WriteProcessMemory", "OpenProcess",
                                     "CreateRemoteThread", "VirtualAllocEx"}, imported)


@unittest.skipUnless(DLL.exists(), "tf_native.dll isn't built (py native/build.py)")
class DllTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = private_temp(cls)
        cls.dll = ct.CDLL(str(DLL))
        for name in ("tf_native_init", "tf_native_update", "tf_native_align"):
            getattr(cls.dll, name).argtypes, getattr(cls.dll, name).restype = [ct.c_void_p], ct.c_int
        cls.dll.tf_native_state.restype = ct.c_int
        cls.dll.tf_native_validate.argtypes = [ct.c_void_p, ct.c_wchar_p]
        cls.dll.tf_native_validate.restype = ct.c_int

    def test_outside_the_game_it_stays_off_and_says_why(self):
        self.assertEqual(self.dll.tf_native_init(None), 0)
        self.assertEqual(self.dll.tf_native_state(), -1)  # no hook installed
        self.assertEqual((self.dll.tf_native_update(None), self.dll.tf_native_align(None)), (0, 0))
        status = json.loads((self.temp / "townfall-companion-native.json").read_text())
        self.assertEqual(status["status"], "game-not-present")

    @unittest.skipUnless(GAME_EXE and GAME_EXE.exists(), "the game isn't installed")
    def test_the_profile_is_taken_only_for_the_game_it_was_read_from_unpatched(self):
        import pefile
        profile = game_profile.PROFILE_FILE  # as the companion makes it, once after a game update
        if game_profile.needed(GAME_EXE, profile):
            game_profile.write_apart(GAME_EXE, profile)
        image = pefile.PE(str(GAME_EXE))
        self.addCleanup(image.close)
        mapped = image.get_memory_mapped_image()
        buffer = ct.create_string_buffer(image.OPTIONAL_HEADER.SizeOfImage)
        ct.memmove(buffer, mapped, len(mapped))
        self.assertEqual(self.dll.tf_native_validate(buffer, str(profile)), 1)
        timestamp = image.DOS_HEADER.e_lfanew + 8  # another build
        buffer[timestamp] = bytes([buffer[timestamp][0] ^ 1])
        self.assertEqual(self.dll.tf_native_validate(buffer, str(profile)), 0)
        ct.memmove(buffer, mapped, len(mapped))
        values = dict(zip([name for name, _ in game_profile.FIELDS], game_profile.LAYOUT.unpack(profile.read_bytes())[3:]))
        buffer[values["end_viewport_rva"]] = b"\x90"  # someone else's patch where the DLL hooks
        self.assertEqual(self.dll.tf_native_validate(buffer, str(profile)), 0)
        ct.memmove(buffer, mapped, len(mapped))
        cut = Path(self.temp) / "cut.profile"
        cut.write_bytes(profile.read_bytes()[:-4])  # a profile of another layout, or half written
        self.assertEqual(self.dll.tf_native_validate(buffer, str(cut)), 0)


class GameProfileTest(unittest.TestCase):
    """What the companion reads from the installed game for the DLL (game_profile.py)."""

    def test_the_dlls_layout_is_the_profiles(self):
        sys.path.insert(0, str(ROOT / "native"))
        try:
            import build
        finally:
            sys.path.remove(str(ROOT / "native"))
        self.assertEqual((ROOT / "native" / "profile.h").read_text(encoding="ascii"), build.header(),
                         "profile.h is out of date: py native/build.py")

    @unittest.skipUnless(GAME_EXE and GAME_EXE.exists(), "the game isn't installed")
    def test_the_games_program_is_read_as_pefile_reads_it(self):
        import pefile
        image = pefile.PE(str(GAME_EXE), fast_load=True)
        self.addCleanup(image.close)
        image.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DEBUG"]])
        [codeview] = [item for item in image.DIRECTORY_ENTRY_DEBUG if item.struct.Type == 2]
        with game_profile.Executable(GAME_EXE) as exe:
            self.assertEqual((exe.timestamp, exe.image_size), (image.FILE_HEADER.TimeDateStamp,
                                                               image.OPTIONAL_HEADER.SizeOfImage))
            self.assertEqual(exe.identity(), image.get_data(codeview.struct.AddressOfRawData, 24))
            entry = image.OPTIONAL_HEADER.AddressOfEntryPoint
            self.assertEqual(exe.data(entry, 64), image.get_data(entry, 64))

    @unittest.skipUnless(GAME_EXE and GAME_EXE.exists(), "the game isn't installed")
    def test_a_profile_is_needed_until_one_for_this_game_is_there(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "tf_native.profile"
            self.assertTrue(game_profile.needed(GAME_EXE, path), "none yet")
            with game_profile.Executable(GAME_EXE) as exe:
                game = {"timestamp": exe.timestamp, "image_size": exe.image_size, "identity": exe.identity()}
            values = {name: (b"\0" * int(kind[:-1]) if kind != "I" else 0) for name, kind in game_profile.FIELDS}
            path.write_bytes(game_profile.pack({**values, **game}))
            self.assertFalse(game_profile.needed(GAME_EXE, path))
            path.write_bytes(game_profile.pack({**values, **game, "timestamp": game["timestamp"] + 1}))
            self.assertTrue(game_profile.needed(GAME_EXE, path), "made for another version of the game")
            path.write_bytes(game_profile.pack({**values, **game})[:-1])
            self.assertTrue(game_profile.needed(GAME_EXE, path), "of another layout")

    def test_read_apart_a_file_that_isnt_the_game_is_said_as_unreadable(self):
        with tempfile.TemporaryDirectory() as folder:
            exe = Path(folder) / "Townfall-Win64-Shipping.exe"
            exe.write_text("not a program")
            with self.assertRaisesRegex(OSError, "isn't a Windows program"):
                game_profile.write_apart(exe, Path(folder) / "tf_native.profile")
            self.assertFalse((Path(folder) / "tf_native.profile").exists())

    @unittest.skipUnless(GAME_EXE and GAME_EXE.exists(), "the game isn't installed")
    def test_read_apart_the_profile_is_the_same(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "tf_native.profile"
            game_profile.write_apart(GAME_EXE, path)
            self.assertFalse(game_profile.needed(GAME_EXE, path))
            if not game_profile.needed(GAME_EXE):
                self.assertEqual(path.read_bytes(), game_profile.PROFILE_FILE.read_bytes())

    def test_a_game_that_changed_a_function_the_dll_uses_gets_no_profile(self):
        values = {name: (b"\0" * int(kind[:-1]) if kind != "I" else 0) for name, kind in game_profile.FIELDS}
        signatures = dict(game_profile.SIGNATURES, END_VIEWPORT="base1:0(FRHIViewport*, base10:1)")
        saved = game_profile.read
        game_profile.read = lambda exe: (values, signatures)
        self.addCleanup(setattr, game_profile, "read", saved)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "tf_native.profile"
            with self.assertRaisesRegex(ValueError, "FRHICommandListImmediate::EndDrawingViewport"):
                game_profile.write("Townfall.exe", path)
            self.assertFalse(path.exists())
            game_profile.read = lambda exe: (values, dict(game_profile.SIGNATURES))
            game_profile.write("Townfall.exe", path)
            self.assertEqual(path.read_bytes(), game_profile.pack(values))


@unittest.skipUnless(TEST_DLL.exists(), "the fake-engine test DLL isn't built (py native/build.py)")
class FakeEngineTest(unittest.TestCase):
    """capture.c, alignment.c and look.c, with the engine's functions replaced by fakes (native/test_harness.c)."""

    @classmethod
    def setUpClass(cls):
        private_temp(cls)
        cls.dll = ct.CDLL(str(TEST_DLL))
        cls.dll.run_capture_test.argtypes, cls.dll.run_capture_test.restype = [ct.c_int, ct.c_wchar_p], ct.c_int
        cls.dll.crtv_test_alignment.argtypes, cls.dll.crtv_test_alignment.restype = [ct.c_uint], ct.c_int
        cls.dll.crtv_test_alignment_packet.argtypes = [ct.c_uint, ct.c_wchar_p]
        cls.dll.crtv_test_alignment_packet.restype = ct.c_int

    def capture(self, scenario):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "frame.bin"
            self.assertEqual(self.dll.run_capture_test(scenario, str(output)), 1)
            return output.read_bytes() if output.exists() else None

    def picture(self, scenario):
        """The picture the DLL published: the JPEG, opened, and its header's meta."""
        data = self.capture(scenario)
        meta, length = parse_header(data)
        self.assertEqual(len(data), HEADER.size + length)
        return Image.open(io.BytesIO(data[HEADER.size:])), meta

    def assertColour(self, pixel, colour):
        self.assertTrue(all(abs(a - b) <= 40 for a, b in zip(pixel, colour)), f"{pixel} isn't {colour}")

    def test_waits_for_submission_and_gpu_fence_before_mapping(self):
        image, meta = self.picture(1)
        self.assertEqual((image.format, image.size, meta["width"], meta["height"]), ("JPEG", (16, 8), 16, 8))
        self.assertColour(image.getpixel((3, 4)), (255, 0, 0))  # the texture's red half, its alpha unused
        self.assertColour(image.getpixel((12, 4)), (0, 0, 255))  # and its blue half; the padding never shows
        self.assertEqual(meta["sequence"], 1)

    def test_the_picture_is_the_screen_alone_scaled_to_the_phones_width(self):
        self.assertEqual(self.dll.crtv_test_picture_scale(), 1)  # see native/test_harness.c

    def test_what_it_may_not_touch_is_left_alone(self):
        for scenario, case in ((2, "an untracked resource"), (3, "an active render pass"), (4, "an unknown pixel format"),
                               (5, "a command list that executes"), (6, "a multi-GPU list"), (7, "the game thread"),
                               (10, "the executor's address taken for the immediate list")):
            with self.subTest(case):
                self.assertIsNone(self.capture(scenario))

    def test_a_timeout_keeps_the_copy_in_flight_alive(self):
        self.assertIsNone(self.capture(8))

    def test_a_pending_copy_survives_the_source_being_cleared(self):
        image, _ = self.picture(9)
        self.assertColour(image.getpixel((3, 4)), (255, 0, 0))

    def test_continuous_capture_reuses_its_buffers_paces_and_stops(self):
        image, meta = self.picture(11)
        self.assertEqual(meta["sequence"], 2)
        self.assertColour(image.getpixel((3, 4)), (255, 0, 0))

    def test_without_word_from_the_mod_no_new_copies_start(self):
        self.assertIsNone(self.capture(12))

    def test_alignment_moves_the_image_itself_only_in_its_stages(self):
        for scenario in range(11):  # applied in both stages, up as up, clamped; the rest refused (test_harness.c)
            with self.subTest(scenario=scenario):
                self.assertEqual(self.dll.crtv_test_alignment(scenario), 1)

    def test_alignment_packets_are_applied_once_on_the_game_thread_whatever_their_age(self):
        with tempfile.TemporaryDirectory() as folder:
            for scenario in range(7):
                with self.subTest(scenario=scenario):
                    self.assertEqual(self.dll.crtv_test_alignment_packet(scenario, str(Path(folder) / "align.bin")), 1)

    def test_the_crtv_looks_where_the_phone_does_signals_and_radar_alike(self):
        self.assertEqual(self.dll.crtv_test_look_turns(), 1)  # see native/test_harness.c

    def test_the_crtvs_picture_faces_where_the_phone_looks_only_for_the_games_calls(self):
        # radar update; capture update; another capture; not looking; the game moves it; the character's facing known
        for scenario in range(6):
            with self.subTest(scenario=scenario):
                self.assertEqual(self.dll.crtv_test_look_capture(scenario), 1)

    def test_the_crtvs_sound_is_tapped_from_its_own_bus_and_the_voices_heard_over_it(self):
        self.assertEqual(self.dll.crtv_test_audio_rank(), 1)  # see native/test_harness.c
        for scenario in range(3):  # stereo through; more channels out than in; a DSP that isn't a tap
            with self.subTest(scenario=scenario):
                self.assertEqual(self.dll.crtv_test_audio_tap(scenario), 1)

    def test_the_phones_sound_is_the_crtvs_buses_mixed_once_handed_over_a_block_at_a_time(self):
        self.assertEqual(self.dll.crtv_test_audio_nested(), 1)  # see native/test_harness.c
        self.dll.crtv_test_audio_mix.argtypes, self.dll.crtv_test_audio_mix.restype = [ct.c_wchar_p], ct.c_int
        name = f"Local\\TownfallCompanionAudioTest_{os.getpid()}"
        self.addCleanup(self.dll.crtv_test_audio_close)
        self.assertEqual(self.dll.crtv_test_audio_mix(name), 1)
        audio = GameAudio(name)  # the companion's side, reading what the DLL wrote
        self.addCleanup(audio.close)
        self.assertEqual(audio.format(), (48000, 2))
        data, position = audio.read(None)  # 0.05 s behind the newest: across the ring's end
        self.assertEqual((len(data), position), (2400 * 4, 65537))
        self.assertEqual(data[-8:], struct.pack("<4h", 16384, 16384, 16384, 16384))
        self.assertEqual(set(data[:-8]), {0})

    def test_while_the_phone_plays_its_sound_the_game_has_its_buses_quiet_after_the_phone_took_theirs(self):
        self.dll.crtv_test_audio_mute.argtypes, self.dll.crtv_test_audio_mute.restype = [ct.c_uint, ct.c_wchar_p], ct.c_int
        with tempfile.TemporaryDirectory() as folder:
            for scenario in range(4):  # quiet; said too long ago; brought back; a bad packet
                with self.subTest(scenario=scenario):
                    self.assertEqual(self.dll.crtv_test_audio_mute(scenario, str(Path(folder) / "sound.bin")), 1)

    def test_the_phones_look_is_taken_when_sane_once_and_while_fresh(self):
        self.dll.crtv_test_look_packet.argtypes, self.dll.crtv_test_look_packet.restype = [ct.c_uint, ct.c_wchar_p], ct.c_int
        with tempfile.TemporaryDirectory() as folder:
            # good; not a number; beyond a half turn; wrong magic; the same command again; stopped looking; bad flag;
            # pitch past straight up
            for scenario in range(8):
                with self.subTest(scenario=scenario):
                    self.assertEqual(self.dll.crtv_test_look_packet(scenario, str(Path(folder) / "look.bin")), 1)


class PictureHeaderTest(unittest.TestCase):
    """What the bridge takes from the DLL's shared memory as the CRTV's picture, and what it refuses."""

    def header(self, **changes):
        fields = {"magic": b"TFJPEG01", "width": 640, "height": 434, "length": 50000, "reserved": 0, "sequence": 1,
                  "unix_ms": 1}
        fields.update(changes)
        return HEADER.pack(*fields.values())

    def test_a_picture_says_its_size_its_number_and_its_jpegs_length(self):
        self.assertEqual(parse_header(self.header()), ({"width": 640, "height": 434, "sequence": 1, "unixMs": 1}, 50000))

    def test_anything_else_is_refused(self):
        for changes in ({"magic": b"TFRAW001"}, {"width": 0}, {"width": 641}, {"height": 0}, {"length": 0},
                        {"length": (1 << 20) + 1}, {"sequence": 0}, {"unix_ms": 0}, {"reserved": 1}):
            with self.subTest(**{k: str(v) for k, v in changes.items()}), self.assertRaises(ValueError):
                parse_header(self.header(**changes))


class SharedFramesTest(unittest.TestCase):
    """The bridge's side: the newest picture from shared memory, as the DLL publishes it, passed on to the phone."""

    def setUp(self):
        self.now = 1000.0
        kernel = ct.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileMappingW.argtypes = [ct.c_void_p, ct.c_void_p, ct.c_uint32, ct.c_uint32, ct.c_uint32, ct.c_wchar_p]
        kernel.CreateFileMappingW.restype = ct.c_void_p
        kernel.MapViewOfFile.argtypes = [ct.c_void_p, ct.c_uint32, ct.c_uint32, ct.c_uint32, ct.c_size_t]
        kernel.MapViewOfFile.restype = ct.c_void_p
        name = f"Local\\TownfallCompanionTest_{os.getpid()}_{self._testMethodName}"
        self.handle = kernel.CreateFileMappingW(ct.c_void_p(-1), None, 4, 0, MAPPING_SIZE, name)
        self.assertTrue(self.handle)
        self.view = kernel.MapViewOfFile(self.handle, 2, 0, 0, MAPPING_SIZE)
        self.assertTrue(self.view)
        self.reader = WindowsFrames(name)
        self.temp = tempfile.TemporaryDirectory()
        self.frames = NativeFrames(self.temp.name, mapping=self.reader, clock=lambda: self.now)

    def tearDown(self):
        self.frames.close()
        self.reader.kernel.UnmapViewOfFile(self.view)
        self.reader.kernel.CloseHandle(self.handle)
        self.temp.cleanup()

    def publish(self, sequence=1, timestamp=1000000, color=(240, 20, 30), counter=2):
        """As picture.c writes it: an odd counter while writing, even once whole. Returns the JPEG."""
        out = io.BytesIO()
        Image.new("RGB", (32, 20), color).save(out, "JPEG")
        jpeg = out.getvalue()
        ct.c_uint32.from_address(self.view).value = counter - 1
        header = HEADER.pack(b"TFJPEG01", 32, 20, len(jpeg), 0, sequence, timestamp)
        ct.memmove(self.view + 8, header, len(header))
        ct.memmove(self.view + 64, jpeg, len(jpeg))
        ct.c_uint32.from_address(self.view).value = counter
        return jpeg

    def test_the_jpeg_is_passed_on_as_published_and_read_once_per_picture(self):
        jpeg = self.publish()
        frame = self.frames.latest()
        self.assertEqual((frame[1], frame[2]), (jpeg, (32, 20)))
        self.assertIs(self.frames.latest(), frame)
        self.assertIsNone(self.reader.snapshot(self.frames.marker)[2])

    def test_stale_and_half_written_pictures_are_not_shown(self):
        self.publish()
        self.assertIsNotNone(self.frames.latest())
        self.now += 4
        self.assertIsNone(self.frames.latest())
        ct.c_uint32.from_address(self.view).value = 3
        self.assertIsNone(self.reader.snapshot())
        self.assertIsNone(self.frames.latest())

    def test_a_restarted_game_with_the_same_counter_still_gives_a_new_picture(self):
        self.publish()
        first = self.frames.latest()
        self.now += 1
        jpeg = self.publish(timestamp=1001000, color=(20, 240, 30))
        second = self.frames.latest()
        self.assertNotEqual(first[0]["seq"], second[0]["seq"])
        self.assertEqual(second[1], jpeg)

    def test_the_stream_pushes_each_new_picture_once(self):
        old = bridge.Handler.frames, bridge.pin_token
        bridge.Handler.frames, bridge.pin_token = self.frames, None
        server = bridge.ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            first = self.publish(sequence=1)
            conn = http.client.HTTPConnection(*server.server_address, timeout=5)
            conn.request("GET", "/api/crtv/stream")
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(int.from_bytes(response.read(4), "big")), first)
            second = self.publish(sequence=2, timestamp=1000100, color=(20, 240, 30))
            self.assertEqual(response.read(int.from_bytes(response.read(4), "big")), second)
            conn.close()
        finally:
            server.shutdown()
            thread.join()
            server.server_close()
            bridge.Handler.frames, bridge.pin_token = old

    def test_a_header_saying_more_than_there_is_room_for_is_refused(self):
        self.publish()
        ct.c_uint32.from_address(self.view + 8 + 16).value = (1 << 20) + 1  # the JPEG's length
        self.assertIsNone(self.frames.latest())

    def test_the_frame_endpoint_needs_the_pin_and_says_when_there_is_none(self):
        self.publish()
        old = bridge.Handler.frames, bridge.pin_token
        bridge.Handler.frames = self.frames
        bridge.pin_token = bridge.pin_cookie_value("4116")
        server = bridge.ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        conn = http.client.HTTPConnection(*server.server_address, timeout=5)
        try:
            conn.request("GET", "/api/crtv/frame")
            response = conn.getresponse()
            response.read()
            self.assertEqual(response.status, 401)
            conn.request("POST", "/login", json.dumps({"pin": "4116"}), {"Content-Type": "application/json"})
            response = conn.getresponse()
            cookie = response.getheader("Set-Cookie").split(";")[0]
            response.read()
            conn.request("GET", "/api/crtv/frame", headers={"Cookie": cookie})
            response = conn.getresponse()
            self.assertEqual((response.status, response.getheader("Content-Type")), (200, "image/jpeg"))
            self.assertEqual(Image.open(io.BytesIO(response.read())).size, (32, 20))
            self.now += 4
            conn.request("GET", "/api/crtv/frame", headers={"Cookie": cookie})
            response = conn.getresponse()
            response.read()
            self.assertEqual(response.status, 204)
        finally:
            conn.close()
            server.shutdown()
            thread.join()
            server.server_close()
            bridge.Handler.frames, bridge.pin_token = old

    def test_the_heartbeat_asks_for_frames_only_while_a_phone_is_there(self):
        old = bridge.Handler.stream_fps, list(bridge.clients)
        path = Path(self.temp.name) / "heartbeat.json"
        try:
            bridge.Handler.stream_fps = 15
            bridge.clients[:] = [object()]
            bridge.write_heartbeat(path, 8790)
            self.assertEqual(json.loads(path.read_text())["nativeFps"], 15)
            bridge.clients.clear()
            bridge.write_heartbeat(path, 8790)
            self.assertEqual(json.loads(path.read_text())["nativeFps"], 0)
        finally:
            bridge.close_heartbeats()
            bridge.Handler.stream_fps, clients = old
            bridge.clients[:] = clients



class SharedSoundTest(unittest.TestCase):
    """The bridge's side of the phone's sound: what tf_native.dll mixes into shared memory (native/audio.c), read
    behind it and streamed to the phone."""
    CAPACITY = 1024

    def setUp(self):
        kernel = ct.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileMappingW.argtypes = [ct.c_void_p, ct.c_void_p, ct.c_uint32, ct.c_uint32, ct.c_uint32, ct.c_wchar_p]
        kernel.CreateFileMappingW.restype = ct.c_void_p
        kernel.MapViewOfFile.argtypes = [ct.c_void_p, ct.c_uint32, ct.c_uint32, ct.c_uint32, ct.c_size_t]
        kernel.MapViewOfFile.restype = ct.c_void_p
        kernel.UnmapViewOfFile.argtypes = [ct.c_void_p]
        kernel.CloseHandle.argtypes = [ct.c_void_p]
        self.name = f"Local\\TownfallCompanionAudioTest_{os.getpid()}_{self._testMethodName}"
        size = 64 + self.CAPACITY * 4
        self.handle = kernel.CreateFileMappingW(ct.c_void_p(-1), None, 4, 0, size, self.name)
        self.view = kernel.MapViewOfFile(self.handle, 2, 0, 0, size)
        self.assertTrue(self.view)
        self.addCleanup(kernel.CloseHandle, self.handle)
        self.addCleanup(kernel.UnmapViewOfFile, self.view)
        self.audio = GameAudio(self.name)
        self.addCleanup(self.audio.close)
        self.written = 0

    def start(self, rate=8000):
        """As audio.c makes it: the header, the magic last."""
        ct.memmove(self.view, struct.pack("<8sIIII", b"TFAUDIO1", rate, 2, self.CAPACITY, 64), 24)

    def play(self, frames):
        """`frames` frames as the mixer thread writes them: each frame's samples its number (and minus it), then
        `written` raised."""
        for frame in range(self.written, self.written + frames):
            slot = self.view + 64 + (frame % self.CAPACITY) * 4
            ct.memmove(slot, struct.pack("<2h", frame % 30000, -(frame % 30000)), 4)
        self.written += frames
        ct.c_int64.from_address(self.view + 24).value = self.written

    def frames(self, data):
        return [struct.unpack_from("<h", data, i)[0] for i in range(0, len(data), 4)]

    def test_no_sound_until_the_game_shares_it(self):
        self.assertIsNone(GameAudio(self.name + "_none").format())
        self.assertIsNone(self.audio.format())  # there, but not started
        self.assertEqual(self.audio.read(None), (b"", None))
        self.start()
        self.assertEqual(self.audio.format(), (8000, 2))

    def test_a_reader_starts_just_behind_and_then_takes_each_frame_once_across_the_rings_end(self):
        self.start()
        self.play(1000)
        data, position = self.audio.read(None)
        self.assertEqual(self.frames(data), list(range(600, 1000)))  # 0.05 s at 8000 Hz
        self.play(100)
        data, position = self.audio.read(position)
        self.assertEqual((self.frames(data), position), (list(range(1000, 1100)), 1100))
        self.assertEqual(self.audio.read(position), (b"", 1100))

    def test_a_reader_held_up_or_a_game_started_anew_starts_again_just_behind(self):
        self.start()
        self.play(100)
        _, position = self.audio.read(None)
        self.play(900)  # more than three quarters of the ring: part of it may be overwritten as it is read
        data, position = self.audio.read(position)
        self.assertEqual(self.frames(data), list(range(600, 1000)))
        self.written = 0  # the game started again: its count starts over
        self.play(500)
        data, position = self.audio.read(position)
        self.assertEqual((self.frames(data), position), (list(range(100, 500)), 500))

    def stream(self):
        old = bridge.Handler.audio, bridge.pin_token
        bridge.Handler.audio, bridge.pin_token = self.audio, None
        server = bridge.ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()

        def stop():
            server.shutdown()
            thread.join()
            server.server_close()
            bridge.Handler.audio, bridge.pin_token = old
        self.addCleanup(stop)
        conn = http.client.HTTPConnection(*server.server_address, timeout=5)
        self.addCleanup(conn.close)
        conn.request("GET", "/api/audio/stream")
        return conn.getresponse()

    def test_without_sound_from_the_game_the_stream_says_so_and_the_phone_tries_again(self):
        response = self.stream()
        self.assertEqual(response.status, 503)
        self.assertIn(b"no sound from the game", response.read())

    def test_the_stream_says_its_format_then_sends_the_sound_as_it_comes(self):
        self.start(rate=48000)
        self.play(10)
        response = self.stream()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.read(10), b"TFAU" + struct.pack("<IH", 48000, 2))
        self.assertEqual(self.frames(response.read(40)), list(range(10)))  # from just behind: all there is
        time.sleep(0.05)
        self.play(5)
        self.assertEqual(self.frames(response.read(20)), list(range(10, 15)))


if __name__ == "__main__":
    unittest.main()
