"""TownfallCompanion/tools ships a folder for each program the companion needs, with a note listing exactly the files
that must be in it."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "TownfallCompanion" / "companion"))
import config  # noqa: E402

TOOLS = ROOT / "TownfallCompanion" / "tools"
NOTE = "PUT FILES HERE.txt"
FOLDERS = {"vgmstream": "vgmstream", "radvideo": "radtools", "ffmpeg": "ffmpeg"}  # config.TOOLS key -> its folder
FILES = {  # folder -> the files that must be in it (vgmstream: its exe and the .dll files of its zip)
    "vgmstream": ["vgmstream-cli.exe", "avcodec-vgmstream-59.dll", "avformat-vgmstream-59.dll",
                  "avutil-vgmstream-57.dll", "libatrac9.dll", "libcelt-0061.dll", "libcelt-0110.dll",
                  "libg719_decode.dll", "libmpg123-0.dll", "libspeex-1.dll", "libvorbis.dll",
                  "swresample-vgmstream-4.dll"],
    "radtools": ["radvideo64.exe"],
    "ffmpeg": ["ffmpeg.exe"],
}


class ToolsFolderTest(unittest.TestCase):
    def test_every_program_has_a_folder_and_the_companion_looks_for_a_file_that_is_listed(self):
        self.assertEqual(sorted(FOLDERS), sorted(config.TOOLS))  # a new program needs its folder and note too
        for key, folder in FOLDERS.items():
            self.assertIn(config.TOOLS[key][0], FILES[folder], folder)

    def test_each_note_and_the_overview_list_exactly_those_files(self):
        overview = (TOOLS / NOTE).read_text(encoding="ascii")  # plain ASCII: it is read in Notepad
        for folder, files in FILES.items():
            note = (TOOLS / folder / NOTE).read_text(encoding="ascii")
            for name in files:
                self.assertIn(name, note, f"{folder}: {name}")
                self.assertIn(name, overview, f"overview: {name}")
            self.assertIn(f"tools\\{folder}\\", overview)
            self.assertIn(f"({len(files)} file{'s' if len(files) > 1 else ''})", overview, folder)
            self.assertIn(f"TownfallCompanion\\tools\\{folder}\\{files[0]}", note, folder)  # where it ends up


if __name__ == "__main__":
    unittest.main()
