"""The release archive (tools/build-package.py): what goes in, and that its checks refuse what mustn't."""
import importlib.util
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "tools" / "build-package.py"
spec = importlib.util.spec_from_file_location("build_package", BUILD)
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class PackageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        run = subprocess.run([sys.executable, str(BUILD), "--out", cls.tmp.name], capture_output=True, text=True, timeout=120)
        assert run.returncode == 0, run.stdout + run.stderr
        cls.archive = zipfile.ZipFile(next(Path(cls.tmp.name).glob("TownfallCompanion-*.zip")))
        cls.names = cls.archive.namelist()
        cls.description = next(Path(cls.tmp.name).glob("TownfallCompanion-*-nexus-description.txt")).read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.archive.close()
        cls.tmp.cleanup()

    def test_one_folder_as_it_goes_into_the_mods_folder(self):
        self.assertTrue(all(name.startswith("TownfallCompanion/") for name in self.names))
        for name in ("enabled.txt", "Scripts/main.lua", "companion/bridge.py", "Start Companion.bat", "README.md",
                     "LICENSE", "THIRD_PARTY_NOTICES.md", "docs/PHONE_SETUP.md", "docs/TROUBLESHOOTING.md"):
            self.assertIn(f"TownfallCompanion/{name}", self.names)

    def test_no_developer_files(self):
        for part in ("tests/", "tools/", ".gitignore", ".gitattributes"):
            self.assertFalse([n for n in self.names if part in n], part)

    def test_the_nexus_description_is_written_next_to_it(self):
        self.assertTrue(self.description.startswith("[center][size=6][b]Townfall Companion[/b][/size][/center]"))

    def test_the_bat_files_have_crlf(self):
        for name in (n for n in self.names if n.endswith(".bat")):
            data = self.archive.read(name)
            self.assertNotIn(b"\n", data.replace(b"\r\n", b""), name)

    def test_the_checks_refuse_what_mustnt_ship(self):
        good = {name: self.archive.read(name) for name in self.names}
        self.assertEqual(build.problems(good), [])
        for bad, why in (("TownfallCompanion/tools/vgmstream/vgmstream-cli.exe", "a program"),
                         ("TownfallCompanion/companion.ini", "made on a PC"),
                         ("TownfallCompanion/cache/clips/Bink/x.mp4", "made on a PC"),
                         ("TownfallCompanion/companion/x.bk2", "a game file type"),
                         ("TownfallCompanion/companion/__pycache__/bridge.cpython-312.pyc", "made on a PC"),
                         ("TownfallCompanion/docs/shot.png", "an image outside the mod's art"),
                         ("TownfallCompanion/companion/static/shot.webp", "an image outside the mod's art"),
                         ("README.md", "outside the mod folder")):
            found = build.problems({**good, bad: b"x"})
            self.assertTrue(any(why in p for p in found), (bad, found))
        lf_bat = {**good, "TownfallCompanion/Start Companion.bat": b"@echo off\nexit\n"}
        self.assertTrue(any("CRLF" in p for p in build.problems(lf_bat)))
        missing = dict(good)
        del missing["TownfallCompanion/Scripts/main.lua"]
        self.assertIn("missing: TownfallCompanion/Scripts/main.lua", build.problems(missing))


class NexusDescriptionTest(unittest.TestCase):
    """The Nexus description is the README in BBCode: the same text, never edited apart from it."""

    def test_markdown_becomes_bbcode(self):
        readme = ("# Title\n\nA paragraph that\nwraps, with **bold**, *italic*, `C:\\path` and [a guide](docs/X.md#part).\n\n"
                  "## Steps\n\n1. One\n   continued.\n2. Two\n\n- [Site](https://example.com)\n")
        self.assertEqual(build.nexus_description(readme, "https://github.com/someone/repo/"), (
            "[center][size=6][b]Title[/b][/size][/center]\n\n"
            "A paragraph that wraps, with [b]bold[/b], [i]italic[/i], [font=Courier New]C:\\path[/font] and "
            "[url=https://github.com/someone/repo/blob/main/docs/X.md#part]a guide[/url].\n\n"
            "[line]\n[size=5][b]Steps[/b][/size]\n\n"
            "[list=1]\n[*]One continued.\n[*]Two\n[/list]\n\n"
            "[list]\n[*][url=https://example.com]Site[/url]\n[/list]\n"))
        self.assertIn("a guide (docs/X.md in the download)", build.nexus_description(readme, ""))

    def test_github_only_lines_stay_out_of_the_nexus_description(self):
        readme = "Intro.\n\n<!-- github-only -->\nAlso on [Nexus](https://example.com).\n<!-- /github-only -->\n\nMore.\n"
        self.assertEqual(build.nexus_description(readme, ""), "Intro.\n\nMore.\n")
        text = build.nexus_description((ROOT / "README.md").read_text(encoding="utf-8"), build.REPO_URL)
        self.assertNotIn("Also on", text)  # the README's own marked line
        self.assertNotIn("github-only", text)

    def test_images_become_links_and_tables_become_lines(self):
        readme = "![The banner](docs/images/b.webp)\n\n| One | Two |\n| --- | --- |\n| ![a](docs/images/a.jpg) | **b** |\n"
        self.assertEqual(build.nexus_description(readme, "https://github.com/someone/repo"), (
            "[url=https://github.com/someone/repo/blob/main/docs/images/b.webp]The banner[/url]\n\n"
            "[b]One[/b] · [b]Two[/b]\n"
            "[url=https://github.com/someone/repo/blob/main/docs/images/a.jpg]a[/url] · [b]b[/b]\n"))
        # Without a repository the pictures are left out: the archive doesn't hold docs/images.
        self.assertNotIn("docs/images", build.nexus_description(readme, ""))

    def test_the_readme_converts_without_markdown_left(self):
        text = build.nexus_description((ROOT / "README.md").read_text(encoding="utf-8"), build.REPO_URL)
        self.assertNotRegex(text, r"(?m)\*\*|`|\]\(|^- |^\d+\. |^  |^\||!\[")
        for section in ("Installation on the PC", "Setup on the phone", "Every time you play", "Troubleshooting"):
            self.assertIn(f"[size=5][b]{section}[/b][/size]", text)


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
