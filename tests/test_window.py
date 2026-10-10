"""The companion's window (companion/gui.py): it builds, shows a check and the log, and gets whole log lines.
Skipped where Python has no tkinter; the window itself also where there is no screen."""
import queue
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "TownfallCompanion" / "companion"))
try:
    import tkinter
except ImportError:
    tkinter = None
else:
    import gui
    import netcheck


@unittest.skipIf(tkinter is None, "this Python has no tkinter")
class WindowTest(unittest.TestCase):
    def test_the_log_gets_whole_lines_from_each_thread(self):
        events = queue.Queue()
        stream = gui.LogStream(events, None)
        stream.write("Townfall Companion ")
        other = threading.Thread(target=lambda: [stream.write("a line from another thread"), stream.write("\n")])
        other.start()
        other.join()
        stream.write("1.0.0\nhalf")
        stream.flush()
        lines = [events.get_nowait()[1] for _ in range(events.qsize())]
        self.assertEqual(lines, ["a line from another thread\n", "Townfall Companion 1.0.0\n", "half"])

    def test_the_window_builds_and_shows_a_check_and_the_log(self):
        try:
            root = tkinter.Tk()
        except tkinter.TclError:
            self.skipTest("no screen")
        try:
            root.withdraw()
            window = gui.Window(root)
            window.findings = netcheck.Findings(port=8790, listen="0.0.0.0", program=r"C:\Python312\python.exe")
            window.show_steps()
            window.append_log("Townfall Companion 1.0.0\n")
            window.draw_qr("http://192.168.1.50:8790")
            root.update_idletasks()
            self.assertIn("The companion on this PC", window.steps.get("1.0", "end"))
            self.assertIn("Townfall Companion 1.0.0", window.log.get("1.0", "end"))
            self.assertGreater(len(window.qr.find_all()), 100)  # the modules of a version 2 code
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
