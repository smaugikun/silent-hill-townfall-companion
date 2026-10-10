"""tools/package.py: what goes into a release archive."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
try:
    import package
finally:
    sys.path.remove(str(ROOT / "tools"))


class PackageTest(unittest.TestCase):
    def test_the_last_commit_gives_the_mod_and_the_players_documents_only(self):
        names = set(package.chosen()) | {package.DLL}
        self.assertEqual(package.problems(names), [])
        self.assertIn("TownfallCompanion/companion/bridge.py", names)
        self.assertIn("docs/NATIVE_DLL.md", names)  # what the DLL does and doesn't, next to it
        self.assertFalse([name for name in names if name.startswith(("tests/", "native/", "tools/")) or
                          name.endswith(("DEVELOPMENT.md", "NEXUS_PAGE.md", "requirements-dev.txt"))])

    def test_what_a_pc_makes_for_itself_and_the_games_files_are_refused(self):
        refused = ["TownfallCompanion/companion.ini", "TownfallCompanion/Scripts/tf_native.profile",
                   "TownfallCompanion/cache/videos/a.mp4", "TownfallCompanion/tools/vgmstream/vgmstream-cli.exe",
                   "TownfallCompanion/companion/__pycache__/bridge.cpython-312.pyc", "TownfallCompanion/Scripts/x.uasset"]
        found = package.problems(set(package.REQUIRED) | set(refused))
        self.assertEqual(found, [f"not for a release: {name}" for name in sorted(refused)])

    def test_a_missing_part_of_the_mod_is_said(self):
        self.assertEqual(package.problems(set(package.REQUIRED) - {package.DLL}), [f"missing: {package.DLL}"])


if __name__ == "__main__":
    unittest.main()
