"""Auto pickup (static/pickup.js): what the selector does when the phone is set on a stand, taken off it, laid
flat and picked up. Driven with synthetic sensor readings by pickup_sim.mjs; needs Node.js, else skipped."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PICKUP = ROOT / "TownfallCompanion" / "companion" / "static" / "pickup.js"
SIM = Path(__file__).with_name("pickup_sim.mjs")


class PickupTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "needs Node.js")
    def test_taken_off_a_stand_it_goes_to_view_even_when_the_stand_means_av_out(self):
        run = subprocess.run(["node", str(SIM), str(PICKUP)], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        steps = json.loads(run.stdout)
        self.assertEqual(steps["onStand"], ["AV_OUT"])  # set on a stand at 66 degrees: the Stand setting (AV OUT)
        self.assertEqual(steps["pickedUpFromStand"], ["AV_OUT", "VIEW"])  # lifted off it: VIEW
        self.assertEqual(steps["laidFlat"], ["AV_OUT", "VIEW", "AV_OUT"])
        self.assertEqual(steps["pickedUpFromFlat"], ["AV_OUT", "VIEW", "AV_OUT", "VIEW"])

    def test_the_page_uses_that_decision(self):
        page = (ROOT / "TownfallCompanion" / "companion" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("pickupPosition(held, stand, pickup.standAvOut)", page)


if __name__ == "__main__":
    unittest.main()
