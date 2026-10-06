"""Runs the phone's CRTV tests (scanner.test.mjs, finetune.test.mjs) under Node.js, so `python -m unittest discover -s
tests` covers the page's CRTV logic too. Skipped where Node.js (20.11 or newer) isn't installed."""
import shutil
import subprocess
import unittest
from pathlib import Path

NODE_TESTS = sorted(Path(__file__).parent.glob("*.test.mjs"))


class ScannerTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js isn't installed")
    def test_the_phones_crtv_state_machine(self):
        run = subprocess.run(["node", "--test", *map(str, NODE_TESTS)], capture_output=True, text=True, timeout=120)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


if __name__ == "__main__":
    unittest.main()
