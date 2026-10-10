"""Starts Townfall Companion: the phone's page and its link to the game.

Double-click this file (it runs with the Python you installed), keep its window open while you play, and close
the window to stop. It opens the companion's window: the address to open on the phone with its QR code, the PIN,
and a check for when the phone can't open the page. The console window it starts in then closes, so only the
companion's window is there. With window = off in companion.ini, or a Python installed without tkinter (an option
in its installer), it stays in the console instead, which shows the address and the PIN too.

It runs companion/bridge.py in this process. If something goes wrong, the window stays open with the message.
"""
import os
import runpy
import subprocess
import sys
import traceback
from pathlib import Path

COMPANION = Path(__file__).resolve().parent / "companion"
WINDOW_ONLY = "TOWNFALL_COMPANION_WINDOW_ONLY"  # set for the copy that runs without a console (leave_console)

if sys.version_info < (3, 10):
    print(f"Townfall Companion needs Python 3.10 or newer, and this is {sys.version.split()[0]}.")
    print("Get it from https://www.python.org/downloads/ and tick 'Add python.exe to PATH'.")
    input("Press Enter to close this window.")
    raise SystemExit(1)


def leave_console(python=None, start=subprocess.Popen):
    """Starts the companion again with this same Python but without a console window, so that only the companion's
    own window shows: True once that copy runs, and this one, with its console, can end. False to stay in the
    console: not Windows, or no screen for a window here.

    The same python.exe, not pythonw.exe: Windows Firewall judges each program by itself, and the rule that lets
    the phone in is for the Python the player allowed when Windows asked."""
    python = Path(python or sys.executable)
    if sys.platform != "win32" or not python.exists():
        return False
    try:
        import tkinter
        root = tkinter.Tk()  # whether a window can open here: it goes again before it is ever shown
        root.withdraw()
        root.destroy()
    except Exception:
        return False
    try:
        start([str(python), str(Path(__file__).resolve()), *sys.argv[1:]], env={**os.environ, WINDOW_ONLY: "1"},
              creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP, close_fds=True)
    except OSError:
        return False
    return True


def window():
    """Runs the companion in its window until that is closed, if companion.ini asks for it (from a copy without a
    console, leave_console); False to run it in this console instead: not asked for, this Python has no tkinter, or
    there is no screen to show it on."""
    import argparse
    import config
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--settings", type=Path, default=config.SETTINGS_FILE)
    if not config.window_wanted(parser.parse_known_args()[0].settings):
        return False
    try:
        import gui
    except ImportError as missing:
        if missing.name in ("tkinter", "_tkinter"):
            return False
        raise
    if not os.environ.get(WINDOW_ONLY) and leave_console():
        return True
    return gui.run()


def main():
    sys.path.insert(0, str(COMPANION))
    sys.argv[0] = str(COMPANION / "bridge.py")
    try:
        if not window():
            if os.environ.get(WINDOW_ONLY):
                return  # no screen for the window after all, and no console to run in: nothing to show
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


if __name__ == "__main__":
    main()
