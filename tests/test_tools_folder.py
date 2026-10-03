"""TownfallCompanion/tools ships a folder for each program the companion needs, with a note saying what goes in it."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "TownfallCompanion" / "companion"))
import config  # noqa: E402

TOOLS = ROOT / "TownfallCompanion" / "tools"
FOLDERS = {"vgmstream": "vgmstream", "radvideo": "rad", "ffmpeg": "ffmpeg"}  # config.TOOLS key -> its folder
NOTE = "PUT FILES HERE.txt"


class ToolsFolderTest(unittest.TestCase):
    def test_every_program_has_a_folder_with_a_note_naming_the_file_the_companion_looks_for(self):
        self.assertEqual(sorted(FOLDERS), sorted(config.TOOLS))  # a new program needs its folder and note too
        overview = (TOOLS / NOTE).read_text(encoding="ascii")  # plain ASCII: it is read in Notepad
        for key, folder in FOLDERS.items():
            exe = config.TOOLS[key][0]
            note = (TOOLS / folder / NOTE).read_text(encoding="ascii")
            self.assertIn(exe, note, folder)
            self.assertIn(exe, overview, folder)
            self.assertIn(folder + "\\", overview, folder)
            self.assertIn(f"TownfallCompanion\\tools\\{folder}\\{exe}", note, folder)  # where it ends up


if __name__ == "__main__":
    unittest.main()
