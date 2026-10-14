"""Regression cases from the adversarial review: transitions, corrupt data and resource lifetime."""
import ctypes as ct
import json
import struct
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "TownfallCompanion" / "companion"))
import bridge
from game_audio import GameAudio, HEADER
import test_mod


class ModRecoveryTest(test_mod.ModTest):
    def play(self):
        self.world.enterGameplay(test_mod.UE_X, test_mod.UE_Y, test_mod.UE_Z, 40)
        self.world.tick(2)
        self.seq = int(time.time() * 1000)

    def write(self, kind, **data):
        self.seq += 1
        (self.tmp / bridge.COMMAND_FILES[kind]).write_text(json.dumps({**data, "seq": self.seq}))

    def steering(self, enabled, session=1, selector="VIEW"):
        self.write("mode", selector=selector, onMonitor=False, miniGameShown=False,
                   steering=enabled, steerSession=session)
        self.world.tick(1)

    def pose(self, yaw, pitch=0, session=1):
        self.write("steer", yaw=yaw, pitch=pitch, session=session)
        self.world.tick(1)

    def test_partial_heartbeat_keeps_a_recent_value_but_expires_it(self):
        self.beat(fps=15)
        self.world.enterGameplay(test_mod.UE_X, test_mod.UE_Y, test_mod.UE_Z)
        self.world.tick(2)
        packet = self.tmp / "townfall-companion-native-source.bin"
        self.bridge_beat.write_text("{")
        self.world.tick(1)
        self.assertEqual(struct.unpack("<8sQQIIQ", packet.read_bytes())[3], 15)
        self.world.advanceWall(20)
        written = self.world.telemetryWrites
        self.world.tick(2)
        self.assertEqual(struct.unpack("<8sQQIIQ", packet.read_bytes())[3], 0)
        self.assertEqual(self.world.telemetryWrites, written)

    def test_late_steering_in_av_out_is_ignored_and_restart_sets_a_baseline(self):
        self.play(); self.steering(True); self.pose(10); self.pose(30, 15)
        self.assertEqual(tuple(self.world.controlRotation()), (10, 60))
        self.steering(False, session=2, selector="AV_OUT")
        self.pose(80, 40, session=1)
        self.assertEqual(tuple(self.world.controlRotation()), (10, 60))
        self.steering(True, session=3)
        self.pose(90, 60, session=1)  # late packet from the old active session
        self.pose(140, 40, session=3)
        self.assertEqual(tuple(self.world.controlRotation()), (10, 60))
        self.pose(145, 45, session=3)
        self.assertEqual(tuple(self.world.controlRotation()), (15, 65))

    def test_missing_pitch_uses_normal_input_and_logs_the_fallback(self):
        self.play(); self.steering(True); self.pose(10)
        self.world.dropControlPitch()
        self.pose(30, 15)
        self.assertEqual(tuple(self.world.controlRotation()), (10, 60))
        self.assertEqual(self.logged("control pitch unreadable: horizontal steering applied; vertical input available"), 1)

    def test_pitch_is_checked_on_the_next_tick(self):
        self.play(); self.steering(True); self.pose(10); self.pose(10, 15)
        self.world.setPitch(-5)  # model a camera/game restoring its state after the immediate setter readback
        self.world.tick(1)
        self.assertEqual(self.logged("after the next game tick: pitch -5.0, previously set 10.0"), 1)

    def test_false_is_dead_result_is_authoritative_even_when_health_is_zero(self):
        self.play()
        self.world.player.IsDead = lambda *_: False
        self.world.player.Health = 0
        self.world.tick(1)
        self.assertTrue(self.telemetry()["player"]["alive"])

    def store_stage(self):
        self.play()
        self.world.radio.active = True
        (self.tmp / "townfall-companion-native.json").write_text(json.dumps({
            "radio": {"active": 1, "fineTuning": 1, "mode": 1, "stage": 4}}))
        self.world.tick(1)

    def test_lost_centre_release_expires_and_renewals_preserve_long_holds(self):
        self.store_stage()
        for _ in range(5):
            self.write("confirm", session=7)
            self.world.tick(1)
        self.assertEqual((self.world.radio.stores, self.world.radio.storeReleases), (1, 0))
        self.world.tick(1); self.world.tick(1)
        self.assertEqual((self.world.radio.stores, self.world.radio.storeReleases), (1, 1))
        self.write("confirm", session=7)  # late renewal must not press the expired session again
        self.world.tick(1)
        self.assertEqual(self.world.radio.stores, 1)

    def test_release_is_sent_to_the_pressed_radio_after_it_is_lowered(self):
        self.store_stage()
        self.write("confirm", session=7); self.world.tick(1)
        self.world.radio.active = False
        self.write("release", session=7); self.world.tick(1)
        self.assertEqual(self.world.radio.storeReleases, 1)

    def test_old_hold_renewals_do_not_press_a_new_pawns_radio(self):
        self.store_stage()
        self.write("confirm", session=7); self.world.tick(1)
        self.world.enterGameplay(test_mod.UE_X, test_mod.UE_Y, test_mod.UE_Z)
        self.world.radio.active = True
        self.world.tick(2)
        self.write("confirm", session=7); self.world.tick(1)
        self.assertEqual(self.world.radio.stores, 0)
        self.write("confirm", session=8); self.world.tick(1)
        self.assertEqual(self.world.radio.stores, 1)

    def test_death_restores_pc_audio_without_waiting_for_the_mute_lease(self):
        self.play()
        sound = self.tmp / "townfall-companion-native-sound.bin"
        self.write("audio", muteGame=True); self.world.tick(1)
        self.assertEqual(struct.unpack("<8sQ", sound.read_bytes())[1], 1)
        self.world.player.IsDead = lambda *_: True
        self.world.tick(1)
        self.assertEqual(struct.unpack("<8sQ", sound.read_bytes())[1], 0)

    def test_a_newer_release_in_the_same_poll_prevents_an_old_press(self):
        self.store_stage()
        self.write("confirm", session=7)
        self.write("release", session=7)
        self.world.tick(1)
        self.assertEqual((self.world.radio.stores, self.world.radio.storeReleases), (0, 0))

    def test_screen_off_handoff_keeps_the_source_active_and_restores_pc_audio(self):
        self.play()
        self.write("mode", selector="VIEW", onMonitor=False, miniGameShown=False)
        self.write("crtv", active=True, animate=False, frequency=.3)
        self.write("audio", muteGame=True); self.world.tick(1)
        self.assertTrue(self.world.radio.active)
        self.write("mode", selector="AV_OUT", onMonitor=False, miniGameShown=False, handoff=True)
        self.write("audio", muteGame=False); self.world.tick(1)
        self.assertTrue(self.world.radio.active)
        self.assertEqual(struct.unpack("<8sQ", (self.tmp / "townfall-companion-native-sound.bin").read_bytes())[1], 0)
        self.assertEqual(self.world.radio.requests[1], "on")  # raise it on the PC without switching off the source

    def test_view_mutes_before_raising_and_old_audio_requests_cannot_undo_the_handoff(self):
        self.play()
        client = "b" * 24
        self.write("mode", selector="VIEW", onMonitor=False, miniGameShown=False, takeAudio=True, client=client, order=10)
        self.write("crtv", active=True, animate=False, frequency=.3)
        self.world.tick(1)
        self.assertTrue(self.world.audioMutedWhenRaised, "the very first source block must already be muted on the PC")
        self.write("audio", muteGame=False, client=client, order=9)  # delayed from before VIEW
        self.world.tick(1)
        self.assertTrue(self.telemetry()["audio"]["gameSoundOff"])
        self.write("mode", selector="AV_OUT", onMonitor=False, miniGameShown=False, takeAudio=False, client=client, order=12)
        self.write("audio", muteGame=True, client=client, order=11)
        self.world.tick(1)
        self.assertEqual(struct.unpack("<8sQ", (self.tmp / "townfall-companion-native-sound.bin").read_bytes())[1], 0)


class HiddenPitchSteeringTest(test_mod.ModTest):
    options = {"hiddenControlPitch": True}

    def test_unreadable_pitch_from_the_first_frame_never_blocks_yaw_or_resets_mouse_pitch(self):
        self.world.enterGameplay(test_mod.UE_X, test_mod.UE_Y, test_mod.UE_Z, 40)
        self.world.tick(2)
        seq = int(time.time() * 1000)
        (self.tmp / bridge.COMMAND_FILES["mode"]).write_text(json.dumps({
            "selector": "VIEW", "onMonitor": False, "miniGameShown": False, "steering": True, "steerSession": 1, "seq": seq}))
        self.world.tick(1)
        def pose(yaw, pitch, offset):
            (self.tmp / bridge.COMMAND_FILES["steer"]).write_text(json.dumps({
                "yaw": yaw, "pitch": pitch, "session": 1, "seq": seq + offset}))
            self.world.tick(1)
        pose(10, 0, 1)
        pose(30, 15, 2)
        self.assertEqual(tuple(self.world.controlRotation()), (10, 60))
        self.world.setPitch(25)  # mouse input changes the hidden pitch between phone samples
        pose(35, 20, 3)
        self.assertEqual(tuple(self.world.controlRotation()), (30, 65))
        self.assertGreater(self.world.normalLookInputs.pitch, 0)
        self.assertEqual(self.logged("control pitch unreadable: skipping rotation"), 0)


class ReadonlyRotationSteeringTest(HiddenPitchSteeringTest):
    options = {"hiddenControlPitch": True, "readonlyRotation": True}


class BridgeRecoveryTest(unittest.TestCase):
    def test_cleanup_inventory_includes_native_and_legacy_files_but_preserves_unrelated_files(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name); telemetry = folder / "townfall-companion-telemetry.json"
            inventory = bridge.ipc_files(telemetry)
            self.assertEqual(len(inventory), 22)
            for path in inventory:
                path.write_text("{}")
            other = folder / "unrelated.bin"; other.write_bytes(b"keep")
            self.assertEqual(bridge.remove_ipc_files(telemetry), [])
            self.assertEqual(list(folder.iterdir()), [other])

    def test_cleanup_waits_for_the_game_and_reclaims_files_on_a_later_safe_attempt(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name); telemetry = folder / "townfall-companion-telemetry.json"
            for path in bridge.ipc_files(telemetry):
                path.write_text("{}")
            (folder / bridge.GAME_FILE).write_text(json.dumps({"time": time.time()}))
            bridge.remove_ipc_files(telemetry)
            self.assertTrue((folder / "townfall-companion-native-source.bin").exists())
            self.assertFalse((folder / bridge.HEARTBEAT_FILE).exists())
            (folder / bridge.GAME_FILE).write_text("{")  # a heartbeat caught mid-write is still game-owned
            bridge.remove_ipc_files(telemetry)
            self.assertTrue((folder / "townfall-companion-native-source.bin").exists())
            (folder / bridge.GAME_FILE).write_text(json.dumps({"time": time.time() - 30}))
            bridge.remove_ipc_files(telemetry)
            self.assertEqual(list(folder.iterdir()), [])

    def test_reordered_release_and_mode_requests_do_not_get_overwritten(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name); client = "a" * 24
            bridge.send_to_game(folder, {"type": "release", "session": 7, "client": client, "order": 3})
            self.assertFalse(bridge.send_to_game(folder, {"type": "confirm", "session": 7, "client": client, "order": 2}))
            self.assertFalse((folder / bridge.COMMAND_FILES["confirm"]).exists())
            mode = {"type": "mode", "selector": "AV_OUT", "onMonitor": False, "miniGameShown": False,
                    "steering": False, "steerSession": 8, "client": client, "order": 5}
            bridge.send_to_game(folder, mode)
            self.assertFalse(bridge.send_to_game(folder, {**mode, "selector": "VIEW", "order": 4}))
            self.assertEqual(json.loads((folder / bridge.COMMAND_FILES["mode"]).read_text())["selector"], "AV_OUT")

    def test_corrupt_telemetry_expires_and_recovers_without_restarting_the_worker(self):
        with tempfile.TemporaryDirectory() as name, patch.object(bridge, "TELEMETRY_STALE_AFTER", .15):
            path = Path(name) / "telemetry.json"
            stop = threading.Event()
            worker = threading.Thread(target=bridge.watch_telemetry_file, args=(path, stop))
            saved = bridge.game_file_live
            bridge.game_file_live = False
            def wait_for(predicate):
                end = time.monotonic() + 2
                while not predicate() and time.monotonic() < end:
                    time.sleep(.01)
                self.assertTrue(predicate())
            worker.start()
            try:
                path.write_text('{"player":{"yaw":17}}')
                wait_for(lambda: bridge.game_file_live)
                path.write_bytes(b'\xff')
                wait_for(lambda: not bridge.game_file_live)
                self.assertTrue(worker.is_alive())
                path.write_text('[1,2,3]'); time.sleep(.1)
                self.assertFalse(bridge.game_file_live)
                path.write_text('{"player":{"yaw":21}}')
                wait_for(lambda: bridge.game_file_live)
                self.assertEqual(bridge.telemetry["player"]["yaw"], 21)
            finally:
                stop.set(); worker.join(2); bridge.game_file_live = saved
            self.assertFalse(worker.is_alive())

    def test_stopped_heartbeat_worker_cannot_recreate_deleted_files(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name); stop = threading.Event()
            worker = threading.Thread(target=bridge.heartbeat_loop,
                args=(folder / "t.json", 1, types.SimpleNamespace(shutdown=lambda: None), 60, stop))
            worker.start()
            stop.set(); worker.join(2)
            bridge.close_heartbeats()
            bridge.remove_ipc_files(folder / "t.json")
            self.assertFalse(worker.is_alive())
            self.assertEqual(list(folder.iterdir()), [])

    def test_audio_close_waits_for_a_complete_read_and_never_reopens_after_close(self):
        memory = ct.create_string_buffer(64 + 1024 * 4)
        ct.memmove(ct.addressof(memory), HEADER.pack(b"TFAUDIO1", 8000, 2, 1024, 64), HEADER.size)
        audio = GameAudio.__new__(GameAudio)
        audio.lock, audio.closed = threading.RLock(), False
        audio.view, audio.handle = ct.addressof(memory), 1
        unmapped = threading.Event()
        audio.kernel = types.SimpleNamespace(UnmapViewOfFile=lambda _: unmapped.set(), CloseHandle=lambda _: None)
        started, resume = threading.Event(), threading.Event()
        original = audio._written
        def written():
            started.set(); self.assertTrue(resume.wait(2)); return original()
        audio._written = written
        errors = []
        def read():
            try: audio.read(None)
            except Exception as error: errors.append(error)
        reader = threading.Thread(target=read)
        closer = threading.Thread(target=audio.close)
        reader.start(); self.assertTrue(started.wait(2)); closer.start()
        try:
            self.assertFalse(unmapped.wait(.05))
        finally:
            resume.set(); reader.join(2); closer.join(2)
        self.assertEqual(errors, [])
        self.assertTrue(unmapped.is_set())
        self.assertEqual(audio.read(None), (b"", None))
        self.assertIsNone(audio.format())
