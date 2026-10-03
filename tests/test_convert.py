"""Runs the video converter (TownfallCompanion/companion/convert_videos.py) against a fake game with fake
tools (fake_video_tools.py, fake_vgmstream.py). Standard library only.

    python -m unittest discover -s tests -v
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
CONVERTER = TESTS.parent / "TownfallCompanion" / "companion" / "convert_videos.py"


class ConvertTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.game = self.root / "Townfall-install"
        movies = self.game / "Townfall" / "Content" / "Movies"
        self.videos = {
            "CRTV_Movies/Bink/Silent.bk2": "bink",             # no sound of its own: the game's soundtrack goes in
            "CRTV_Movies/Bink/Loud.bk2": "bink AUDIO",         # has its own: kept
            "Cutscene_Diegetic_Movies/Screen.mp4": "mp4 AUDIO",  # an MP4 with sound: copied as it is
            "Cutscene_Diegetic_Movies/Quiet.mp4": "mp4",       # an MP4 without: its soundtrack goes in
            "CRTV_Movies/readme.txt": "not a video",
        }
        for name, text in self.videos.items():
            (movies / name).parent.mkdir(parents=True, exist_ok=True)
            (movies / name).write_text(text, encoding="utf-8")
        self.bank = self.game / "Townfall" / "Content" / "FMOD" / "Banks" / "Desktop" / "BinkAudio.bank"
        self.bank.parent.mkdir(parents=True)
        self.bank.write_text("Silent\nQuiet\nLoud\nUnused", encoding="utf-8")
        tools = {}
        for tool in ("radvideo", "ffmpeg"):
            tools[tool] = self.root / f"{tool}.cmd"
            tools[tool].write_text(f'@"{sys.executable}" "{TESTS / "fake_video_tools.py"}" {tool} %*\n')
        tools["vgmstream"] = self.root / "vgmstream.cmd"
        tools["vgmstream"].write_text(f'@"{sys.executable}" "{TESTS / "fake_vgmstream.py"}" %*\n')
        self.settings = self.root / "companion.ini"
        self.settings.write_text("[paths]\n" + "".join(f"{k} = {v}\n" for k, v in tools.items()), encoding="utf-8")
        self.out = self.root / "clips"

    def tearDown(self):
        self.tmp.cleanup()

    def convert(self, *args, settings=None):
        return subprocess.run([sys.executable, str(CONVERTER), "--settings", str(settings or self.settings),
                               "--game-dir", str(self.game), "--out", str(self.out), *args],
                              capture_output=True, text=True, timeout=60)

    def made(self, name):
        return json.loads((self.out / name).read_text(encoding="utf-8"))

    def decoded(self):
        log = self.bank.with_name(self.bank.name + ".decoded")
        return sorted(log.read_text(encoding="utf-8").split()) if log.exists() else []

    def test_every_video_where_the_mod_reports_it(self):
        run = self.convert()
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(sorted(p.relative_to(self.out).as_posix() for p in self.out.rglob("*") if p.is_file()),
                         ["Bink/Loud.mp4", "Bink/Silent.mp4",
                          "Cutscene_Diegetic_Movies/Quiet.mp4", "Cutscene_Diegetic_Movies/Screen.mp4"])
        self.assertIn("4 of 4 videos ready for the phone.", run.stdout)

    def test_a_silent_bink_gets_the_soundtrack_the_game_plays_with_it(self):
        self.convert()
        self.assertEqual(self.made("Bink/Silent.mp4"),
                         {"from": "mp4 of bink", "inputs": 2, "maps": ["0:v", "1:a"], "video": "libx264"})  # RAD's AVI is re-encoded

    def test_a_nonzero_rad_exit_fails_the_clip_even_when_it_wrote_something(self):
        # Since the AVI rewrite (9f1139f) RAD's exit code counts: a RAD that failed may have left a cut-short AVI that
        # FFmpeg still takes, and a clip made from it would be cached broken. If real RAD exits nonzero after a whole
        # AVI for some Townfall clips, accept the output in decode_bink and turn this test around.
        warn = self.game / "Townfall" / "Content" / "Movies" / "CRTV_Movies" / "Bink" / "WarnExit.bk2"
        warn.write_text("bink WARNEXIT", encoding="utf-8")  # the fake RAD writes its output, then exits with 7
        run = self.convert()
        self.assertEqual(run.returncode, 1)
        self.assertIn("Bink/WarnExit.mp4 ... FAILED: radvideo64.exe couldn't decode it (exit code 7)", run.stdout)
        self.assertIn("4 of 5 videos ready for the phone.", run.stdout)
        self.assertEqual([p.name for p in self.out.rglob("*WarnExit*")], [])  # nothing cached, nothing half-made

    def test_a_bink_with_its_own_sound_keeps_it(self):
        self.convert()
        self.assertEqual(self.made("Bink/Loud.mp4"),
                         {"from": "mp4 of bink AUDIO", "inputs": 1, "maps": ["0:v", "0:a?"], "video": "libx264"})
        self.assertNotIn("Loud", self.decoded())

    def test_the_games_mp4s(self):
        self.convert()
        self.assertEqual((self.out / "Cutscene_Diegetic_Movies" / "Screen.mp4").read_text(encoding="utf-8"), "mp4 AUDIO")
        self.assertEqual(self.made("Cutscene_Diegetic_Movies/Quiet.mp4"),
                         {"from": "mp4", "inputs": 2, "maps": ["0:v", "1:a"], "video": "copy"})  # not re-encoded
        self.assertEqual(self.decoded(), ["Quiet", "Silent"])

    def test_only_whats_missing_unless_forced(self):
        self.convert()
        (self.out / "Bink" / "Silent.mp4").unlink()
        run = self.convert()
        self.assertEqual(run.stdout.count("] "), 1, run.stdout)  # only Silent again
        self.assertEqual(self.decoded(), ["Quiet", "Silent", "Silent"])
        self.convert("--force")
        self.assertEqual(self.decoded(), ["Quiet", "Quiet", "Silent", "Silent", "Silent"])

    def test_a_video_that_fails_doesnt_stop_the_others(self):
        broken = self.game / "Townfall" / "Content" / "Movies" / "CRTV_Movies" / "Bink" / "Broken.bk2"
        broken.write_text("BROKEN", encoding="utf-8")
        run = self.convert()
        self.assertEqual(run.returncode, 1)
        self.assertIn("Bink/Broken.mp4 ... FAILED: radvideo64.exe couldn't decode it (exit code 3)", run.stdout)
        self.assertIn("4 of 5 videos ready for the phone.", run.stdout)
        self.assertIn("1 failed; run this again to retry them: Bink/Broken.mp4", run.stderr)
        self.assertEqual([p.name for p in self.out.rglob("*.part.mp4")], [])  # nothing half-made left

    def test_a_converter_cannot_read_what_is_typed_in_the_window(self):
        # Typing in the companion's window made RAD ask "cancel Bink 2?" (and 'q' quits FFmpeg): children get no input.
        code = (f"import sys; sys.path.insert(0, {str(CONVERTER.parent)!r}); import convert_videos as c; "
                "r = c._children.run([sys.executable, '-c', 'import sys; print(repr(sys.stdin.read()))'], "
                "capture_output=True, text=True); print(r.stdout.strip())")
        run = subprocess.run([sys.executable, "-c", code], input="typed keys", capture_output=True, text=True, timeout=60)
        self.assertEqual(run.stdout.strip(), "''", run.stderr)  # it would be 'typed keys' if the child shared our input

    def test_missing_tools_are_named_with_where_to_get_them(self):
        settings = self.root / "no-tools.ini"
        settings.write_text("[paths]\nradvideo = nowhere\\radvideo64.exe\nffmpeg = nowhere\\ffmpeg.exe\n"
                            "vgmstream = nowhere\\vgmstream-cli.exe\n", encoding="utf-8")
        run = self.convert(settings=settings)
        self.assertEqual(run.returncode, 1)
        for exe, source in (("radvideo64.exe", "radgametools.com/bnkdown.htm"), ("ffmpeg.exe", "ffmpeg.org"),
                            ("vgmstream-cli.exe", "github.com/vgmstream/vgmstream/releases")):
            self.assertRegex(run.stderr, rf"{exe} \(not at .*\): .*{source}")
        self.assertFalse(self.out.exists())

    def test_nothing_to_convert_needs_no_tools(self):
        self.convert()
        settings = self.root / "no-tools.ini"
        settings.write_text("[paths]\nradvideo = nowhere\\radvideo64.exe\n", encoding="utf-8")
        run = self.convert(settings=settings)
        self.assertEqual((run.returncode, run.stderr), (0, ""))
        self.assertIn("All 4 videos are ready for the phone.", run.stdout)

    def test_a_wrong_game_folder_is_said_plainly(self):
        run = self.convert("--game-dir", str(self.root / "elsewhere"))  # the last --game-dir counts
        self.assertEqual(run.returncode, 1)
        self.assertIn("The game's videos aren't in", run.stderr)
        self.assertIn("set game in companion.ini", run.stderr)


if __name__ == "__main__":
    unittest.main()
