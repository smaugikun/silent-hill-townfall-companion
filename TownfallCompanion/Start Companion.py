"""Starts Townfall Companion: the phone's page and its link to the game.

Double-click this file (it runs with the Python you installed), keep its window open while you play, and close
the window to stop. The window shows the address to open on the phone, and the PIN.

It runs companion/bridge.py in this process. If something goes wrong, the window stays open with the message.
To run the video converter by hand instead: py companion/convert_videos.py
"""
import runpy
import sys
import traceback
from pathlib import Path

COMPANION = Path(__file__).resolve().parent / "companion"

if sys.version_info < (3, 10):
    print(f"Townfall Companion needs Python 3.10 or newer, and this is {sys.version.split()[0]}.")
    print("Get it from https://www.python.org/downloads/ and tick 'Add python.exe to PATH'.")
    input("Press Enter to close this window.")
    raise SystemExit(1)

sys.path.insert(0, str(COMPANION))
sys.argv[0] = str(COMPANION / "bridge.py")
try:
    runpy.run_path(str(COMPANION / "bridge.py"), run_name="__main__")
except KeyboardInterrupt:
    pass
except SystemExit as stop:
    if stop.code not in (None, 0):  # a message or an error: leave it on screen
        print(stop.code if isinstance(stop.code, str) else f"The companion stopped (code {stop.code}).")
        input("Press Enter to close this window.")
        raise SystemExit(1)
except Exception:
    traceback.print_exc()
    input("Press Enter to close this window.")
    raise SystemExit(1)
