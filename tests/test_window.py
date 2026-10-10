"""The companion's window (companion/gui.py): it builds, shows a check and the log, and gets whole log lines.
Skipped where Python has no tkinter; the window itself also where there is no screen."""
import queue
import sys
import threading
import time
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "TownfallCompanion" / "companion"))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # test_netcheck's made-up surveys
try:
    import tkinter
except ImportError:
    tkinter = None
else:
    import gui
    import netcheck
    from test_netcheck import measured, survey


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

    def test_a_line_to_fix_has_its_button_and_check_the_connection_next_to_it(self):
        try:
            root = tkinter.Tk()
        except tkinter.TclError:
            self.skipTest("no screen")
        try:
            root.withdraw()
            window = gui.Window(root)
            window.served = True
            window.findings = measured(survey(rules=[]))  # no rule lets Python in
            window.show_steps()
            self.assertEqual([button.cget("text") for button in window.step_buttons],
                             ["Open Windows' allowed apps", "Check the connection"])
            checks = []
            window.check = lambda: checks.append(True)
            window.show_steps()
            window.step_buttons[1].invoke()
            self.assertEqual(checks, [True])
        finally:
            root.destroy()

    def test_a_phone_that_got_through_counts_after_it_has_gone_and_the_pc_never_does(self):
        try:
            root = tkinter.Tk()
        except tkinter.TclError:
            self.skipTest("no screen")
        try:
            root.withdraw()
            window = gui.Window(root)
            window.findings = netcheck.Findings(port=8790, listen="0.0.0.0", program=r"C:\Python312\python.exe",
                                                default_ip="192.168.1.50", addresses=[netcheck.Address("192.168.1.50")])
            window.companion = types.SimpleNamespace(visitors={"127.0.0.1": 300.0, "192.168.1.50": 200.0})
            self.assertIsNone(window.phone_visit())  # the check's own probes and the page opened on the PC
            window.companion.visitors.update({"192.168.1.77": 100.0, "192.168.1.78": 150.0})
            self.assertEqual(window.phone_visit(), ("192.168.1.78", time.strftime("%H:%M:%S", time.localtime(150.0))))
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
