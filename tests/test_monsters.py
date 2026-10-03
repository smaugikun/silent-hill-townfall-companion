"""The phone's monster kinds (static/monsters.js) and the sprite sheets it plays them from must agree."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class MonsterArtTest(unittest.TestCase):
    """The phone's monster kinds and the sheets it plays them from must agree."""

    def setUp(self):
        self.page = (ROOT / "TownfallCompanion/companion/static/monsters.js").read_text(encoding="utf-8")
        self.sheets = sorted((ROOT / "TownfallCompanion/companion/static/monster").glob("*.webp"))

    def test_every_animation_has_its_sheet(self):
        kinds = re.findall(r'"(\w+)"', re.search(r"const KINDS = \[(.*?)\]", self.page).group(1))
        runs = re.findall(r'"(\w+)"', re.search(r"const RUNS = \[(.*?)\]", self.page).group(1))
        moving = [*(f"{k}-walk" for k in kinds), *(f"{k}-run" for k in runs)]
        self.assertEqual(sorted(p.stem for p in self.sheets), sorted([*kinds, *moving, *(f"{m}-away" for m in moving)]))

    def test_each_sheet_is_a_whole_grid_of_frames(self):
        frames, columns = map(int, re.search(r"const FRAMES = (\d+), COLUMNS = (\d+)", self.page).groups())
        rows = -(-frames // columns)
        for path in self.sheets:
            data = path.read_bytes()
            self.assertEqual((data[:4], data[8:16]), (b"RIFF", b"WEBPVP8X"), path.name)  # extended WebP, with alpha
            width = int.from_bytes(data[24:27], "little") + 1
            height = int.from_bytes(data[27:30], "little") + 1
            self.assertEqual((width % columns, height % rows), (0, 0), path.name)

    def test_the_games_names_lead_to_creatures_there_are(self):
        kinds = re.findall(r'"(\w+)"', re.search(r"const KINDS = \[(.*?)\]", self.page).group(1))
        names = re.findall(r'\["(\w+)", "(\w+)"\]', re.search(r"const GAME_NAMES = \[(.*?)\];", self.page, re.S).group(1))
        self.assertTrue(names)
        self.assertEqual({kind for _, kind in names} - set(kinds), set())


if __name__ == "__main__":
    unittest.main()
