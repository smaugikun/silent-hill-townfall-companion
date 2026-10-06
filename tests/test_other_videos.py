"""A video the game reports that is in none of the folders the companion indexes (the CRTV's screen background, say) is
found under the game's Movies folder, so that the phone asking for it makes the companion convert it. Standard
library only; needs no converter tools."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "TownfallCompanion" / "companion"))

import convert_videos  # noqa: E402


class OtherVideoTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        game = self.root / "game"
        self.movies = game / convert_videos.MOVIES
        for name in ("CRTV_Movies/Bink/Known.bk2", "UI/Static/Background.bk2", "UI/Plain.mp4"):
            (self.movies / name).parent.mkdir(parents=True, exist_ok=True)
            (self.movies / name).write_text("video", encoding="utf-8")
        (game / "secret.bk2").write_text("not a movie", encoding="utf-8")
        self.videos = convert_videos.GameVideos(game, None, None, None, self.root / "clips")

    def test_a_path_the_index_doesnt_cover_is_found_under_movies(self):
        self.assertEqual(self.videos.other_video(Path("UI/Static/Background.mp4")), (self.movies / "UI/Static/Background.bk2").resolve())
        self.assertEqual(self.videos.other_video(Path("UI/Plain.mp4")), (self.movies / "UI/Plain.mp4").resolve())

    def test_what_is_not_there_is_not_found(self):
        self.assertIsNone(self.videos.other_video(Path("UI/Static/Missing.mp4")))

    def test_nothing_outside_the_movies_folder(self):
        for name in ("../../../../secret.mp4", "UI/../../../../../secret.mp4", "/secret.mp4"):
            self.assertIsNone(self.videos.other_video(Path(name)), name)

    def test_the_indexed_folders_are_still_the_index(self):
        self.assertIn("Bink/Known.mp4", self.videos.index)
        self.assertNotIn("UI/Static/Background.mp4", self.videos.index)  # found on request, not pre-cached

    def test_a_request_for_one_with_no_tools_fails_cleanly(self):
        self.assertIsNone(self.videos.mp4("UI/Static/Background.mp4"))
        self.assertIn("missing", self.videos.problem)  # not "no such video": it was found, and can't be converted yet
        self.assertIsNone(self.videos.mp4("UI/Static/Missing.mp4"))
        self.assertIsNone(self.videos.mp4("../secret.mp4"))


if __name__ == "__main__":
    unittest.main()
