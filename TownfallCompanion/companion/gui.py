"""The companion's window: the address to open on the phone with its QR code, the PIN, whether the game and the
phones are connected, the companion's log, and the connection check (netcheck.py).

Start Companion.py opens it when Python has tkinter. bridge.main() runs in a thread meanwhile, printing into the log
(and on into a console window, if there is one), and closing the window stops it. Without tkinter, which
Python's installer can leave out, or without a screen, the companion runs in its console window instead.
"""
import os
import queue
import signal
import sys
import threading
import time
import traceback
import tkinter as tk
from tkinter import font as tkfont

import bridge
import config
import netcheck
import qr

BACKGROUND, PANEL, TEXT, DIM, BORDER, BRIGHT = "#12100e", "#1d1a16", "#d8cfb8", "#8f8672", "#5a5240", "#f3ead2"
BADGES = {  # step state -> (badge, colour)
    netcheck.OK: (" OK ", "#5f8a4c"), netcheck.PROBLEM: (" FIX ", "#b0503c"), netcheck.NOTE: (" NOTE ", "#a8822f"),
    netcheck.WAIT: (" WAIT ", "#4f6f8f"), netcheck.UNKNOWN: (" N/A ", "#5a544a"), netcheck.CHECKING: (" ... ", "#5a544a"),
}
ACTIONS = {"allow-python": "Allow Python through the firewall"}  # a step's button: action -> label
LOG_LINES = 2000  # the bridge logs every request; older lines go


class LogStream:
    """Where print() goes while the window is open: into its log, and on into the console window, if there is one.
    Whole lines only: print() writes the text and its line break separately, and another thread's line could come
    in between."""
    encoding, errors = "utf-8", "replace"

    def __init__(self, events, console):
        self.events, self.console = events, console
        self.pending = threading.local()

    def write(self, text):
        lines, newline, rest = (getattr(self.pending, "text", "") + text).rpartition("\n")
        self.pending.text = rest
        if newline:
            self.emit(lines + newline)
        return len(text)

    def flush(self):
        text, self.pending.text = getattr(self.pending, "text", ""), ""
        if text:
            self.emit(text)

    def emit(self, text):
        self.events.put(("log", text))
        try:
            self.console.write(text)
            self.console.flush()
        except (AttributeError, OSError, ValueError):
            pass  # no console (pythonw), or it can't show a character: the window has it

    def isatty(self):
        return False


class Window:
    def __init__(self, root, companion=bridge):
        self.root, self.companion = root, companion
        self.events = queue.Queue()  # (kind, value) from other threads, handled in Tk's
        self.thread = None
        self.served = self.closing = self.checking = self.gone = False
        self.findings = None
        self.visit = None            # (ip, time) of the latest phone that got through to the companion
        self.shown = None            # what the address area and QR code show, to redraw only on a change
        self.step_buttons = []
        self.ticks = 0
        self.scale = max(1.0, root.winfo_fpixels("1i") / 96)
        families = set(tkfont.families(root))
        self.ui = next((f for f in ("Segoe UI", "Helvetica") if f in families), "TkDefaultFont")
        self.mono = next((f for f in ("Consolas", "Courier New") if f in families), "TkFixedFont")
        self.build()

    def build(self):
        root = self.root
        root.title("Townfall Companion")
        root.configure(bg=BACKGROUND)
        width = min(int(960 * self.scale), root.winfo_screenwidth() - int(40 * self.scale))
        height = min(int(820 * self.scale), root.winfo_screenheight() - int(100 * self.scale))
        root.geometry(f"{width}x{height}")
        root.minsize(int(700 * self.scale), int(560 * self.scale))
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(3, weight=1)

        header = tk.Frame(root, bg=BACKGROUND)
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 0))
        tk.Label(header, text="TOWNFALL COMPANION", font=(self.ui, 15, "bold"), bg=BACKGROUND, fg=TEXT).pack(side="left")
        tk.Label(header, text=config.VERSION, font=(self.ui, 9), bg=BACKGROUND, fg=DIM).pack(side="left", padx=8, pady=(6, 0))
        self.status = tk.Label(header, text="Starting ...", font=(self.ui, 10), bg=BACKGROUND, fg=DIM)
        self.status.pack(side="right")

        self.stopped = tk.Label(root, font=(self.ui, 11, "bold"), bg="#5a2418", fg=BRIGHT, anchor="w", justify="left",
                                padx=12, pady=8)

        top = tk.Frame(root, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        top.grid(row=2, column=0, sticky="ew", padx=16, pady=12)
        top.columnconfigure(0, weight=1)
        self.addresses = tk.Frame(top, bg=PANEL)
        self.addresses.grid(row=0, column=0, sticky="nsew", padx=16, pady=12)
        side = tk.Frame(top, bg=PANEL)
        side.grid(row=0, column=1, sticky="ne", padx=16, pady=12)
        self.qr_size = int(200 * self.scale)
        self.qr = tk.Canvas(side, width=self.qr_size, height=self.qr_size, bg="white", highlightthickness=0)
        self.qr.pack()
        self.qr_caption = tk.Label(side, font=(self.ui, 9), bg=PANEL, fg=DIM, wraplength=self.qr_size, justify="center",
                                   height=2)
        self.qr_caption.pack(pady=(6, 0))

        panes = tk.PanedWindow(root, orient="vertical", bg=BACKGROUND, sashwidth=6, bd=0)
        panes.grid(row=3, column=0, sticky="nsew", padx=16, pady=(0, 12))
        checks = tk.Frame(panes, bg=BACKGROUND)
        bar = tk.Frame(checks, bg=BACKGROUND)
        bar.pack(fill="x")
        tk.Label(bar, text="Connection check", font=(self.ui, 11, "bold"), bg=BACKGROUND, fg=TEXT).pack(side="left")
        self.check_button = self.button(bar, "Check the connection", self.check)
        self.check_button.pack(side="right")
        self.steps = self.text_pane(checks, (self.ui, 10))
        for state, (_, colour) in BADGES.items():
            self.steps.tag_configure(state, background=colour, foreground="white", font=(self.mono, 9, "bold"))
        self.steps.tag_configure("title", font=(self.ui, 10, "bold"), foreground=BRIGHT)
        self.steps.tag_configure("gap", font=(self.ui, 5))  # a short line between steps
        self.steps.tag_configure("body", lmargin1=int(56 * self.scale), lmargin2=int(56 * self.scale))
        self.steps.tag_configure("fix", lmargin1=int(56 * self.scale), lmargin2=int(56 * self.scale), foreground="#e2c58a")
        panes.add(checks, minsize=int(140 * self.scale), height=int(330 * self.scale))
        logs = tk.Frame(panes, bg=BACKGROUND)
        tk.Label(logs, text="Log", font=(self.ui, 11, "bold"), bg=BACKGROUND, fg=TEXT).pack(anchor="w", pady=(6, 0))
        self.log = self.text_pane(logs, (self.mono, 9))
        panes.add(logs, minsize=int(100 * self.scale))

    def button(self, parent, text, command):
        return tk.Button(parent, text=text, command=command, font=(self.ui, 9), bg="#2b2620", fg=TEXT,
                         activebackground="#3a3329", activeforeground=BRIGHT, relief="flat", padx=10, pady=3,
                         cursor="hand2")

    def text_pane(self, parent, font):
        frame = tk.Frame(parent, bg=BORDER, padx=1, pady=1)
        frame.pack(fill="both", expand=True, pady=(6, 0))
        text = tk.Text(frame, font=font, bg=PANEL, fg=TEXT, wrap="word", relief="flat", padx=10, pady=8,
                       insertbackground=TEXT, selectbackground=BORDER, height=6, state="disabled")
        bar = tk.Scrollbar(frame, command=text.yview)
        text.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)
        return text

    def start(self):
        """Runs the companion in a thread, its output in the log; the first check runs once it listens."""
        self.streams = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = LogStream(self.events, sys.__stdout__)
        self.thread = threading.Thread(target=self.serve, name="companion", daemon=True)
        self.thread.start()
        self.root.after(100, self.poll)

    def finish(self):
        sys.stdout, sys.stderr = self.streams
        # The heartbeat thread beats until the process ends. One beat after main() cleaned up would say for a few
        # seconds that the companion is still here, and a new one wouldn't start meanwhile.
        folder = self.companion.Handler.commands_dir
        if folder is not None and self.thread is not None and not self.thread.is_alive():
            beat = folder / self.companion.HEARTBEAT_FILE
            if (self.companion.read_heartbeat(beat) or {}).get("pid") == os.getpid():
                try:
                    beat.unlink()
                except OSError:
                    pass

    def serve(self):
        problem = None
        try:
            self.companion.main()
        except SystemExit as stop:
            if stop.code not in (None, 0):
                problem = stop.code if isinstance(stop.code, str) else f"The companion stopped (code {stop.code})."
        except BaseException:
            traceback.print_exc()
            problem = "The companion stopped because of an error: the log says which."
        sys.stdout.flush()
        self.events.put(("ended", problem))

    def poll(self):
        texts = []
        for _ in range(500):
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                texts.append(value)
            elif kind == "findings":
                self.findings = value
                self.show_addresses()
                self.show_steps()
            elif kind == "checked":
                self.checking = False
                self.check_button.configure(state="normal", text="Check the connection")
            elif kind == "allowed":
                self.allowed(value)
            elif kind == "ended":
                self.ended(value)
            if self.gone:
                return
        if texts:
            self.append_log("".join(texts))
        self.ticks += 1
        if self.ticks % 5 == 0 and not self.closing:
            self.show_status()
        self.root.after(100, self.poll)

    def show_status(self):
        companion = self.companion
        if not self.served and companion.running is not None and companion.phone_info:
            self.served = True  # main() has said where it listens: the rest of its startup is done
            self.show_addresses()
            self.check()
        if not self.served or not self.thread.is_alive():
            return
        with companion.clients_lock:
            phones = len(companion.clients)
        folder = companion.Handler.commands_dir
        running = folder is not None and companion.read_game_beat(folder / companion.GAME_FILE, 5)
        if companion.game_file_live:
            game = "Game: connected"
        elif running:
            game = "Game: running, not in gameplay" if phones else "Game: running, waiting for a phone"
        else:
            game = "Game: not running"
        self.status.configure(text=f"{game}    Phones with the page open: {phones}",
                              fg=BRIGHT if companion.game_file_live else DIM)
        visit = self.phone_visit()
        if visit != self.visit:
            self.visit = visit
            self.show_steps()

    def phone_visit(self):
        """(ip, time) of the latest phone that got through to the companion, or None. The PC's own visits don't
        count: the check's probes, or the page opened on the PC, never pass the firewall."""
        if self.findings is None or not isinstance(self.findings.addresses, list):
            return None
        own = {address.ip for address in self.findings.addresses} | {self.findings.default_ip}
        phones = [(at, ip) for ip, at in list(self.companion.visitors.items()) if netcheck.from_phone(ip, own)]
        if not phones:
            return None
        at, ip = max(phones)
        return ip, time.strftime("%H:%M:%S", time.localtime(at))

    def phone_urls(self):
        """[(url, what it is)], the one to open first; empty while the companion isn't listening, or listens
        only on this PC."""
        if not self.served:
            return []
        host, port = self.companion.running.server_address[:2]
        if host.startswith("127."):
            return []
        if host != "0.0.0.0":
            return [(f"http://{host}:{port}", "")]
        if self.findings and isinstance(self.findings.addresses, list):
            return [(f"http://{address.ip}:{port}", ": ".join(part for part in (address.adapter, address.kind[1]) if part))
                    for address in self.findings.addresses]
        return [(url, "") for url in self.companion.phone_info.get("urls", [])]

    def show_addresses(self):
        urls = self.phone_urls()
        pin = self.companion.phone_info.get("pin")
        shown = (tuple(urls), pin, self.served)
        if shown == self.shown:
            return
        self.shown = shown
        for child in self.addresses.winfo_children():
            child.destroy()

        def label(text, size, colour=DIM, weight="normal"):
            tk.Label(self.addresses, text=text, font=(self.ui, size, weight), bg=PANEL, fg=colour, justify="left",
                     anchor="w").pack(anchor="w")

        if not self.served:
            label("Starting ...", 11)
        elif not urls:
            host = self.companion.running.server_address[0]
            label("Only this PC can open the page", 14, BRIGHT, "bold")
            label(f"listen = {host} in companion.ini keeps phones out (see the check below).", 10)
        else:
            label("Open this on the phone", 10)
            self.address_row(urls[0][0], (self.mono, 22, "bold"), urls[0][1])
            for url, note in urls[1:4]:
                self.address_row(url, (self.mono, 11), note)
            if len(urls) > 1:
                label("Use the first one; the others are other adapters of this PC.", 9)
        label(f"PIN  {pin}" if pin else "No PIN (set pin in companion.ini to ask for one)", 18 if pin else 10,
              BRIGHT, "bold")
        if urls:
            label("\nThe phone has to be on this PC's network (the router's Wi-Fi, not mobile data). It asks for "
                  "the PIN once.\n"
                  "If the page doesn't open on the phone, the connection check below says why.", 10)
        self.draw_qr(urls[0][0] if urls else None)

    def address_row(self, url, font, note):
        row = tk.Frame(self.addresses, bg=PANEL)
        row.pack(anchor="w", fill="x", pady=(2, 6))
        value = tk.StringVar(value=url)
        entry = tk.Entry(row, textvariable=value, font=font, state="readonly", readonlybackground=PANEL, fg=BRIGHT,
                         relief="flat", width=len(url) + 1, highlightthickness=0)
        entry.pack(side="left")
        copy = self.button(row, "Copy", None)
        copy.configure(command=lambda: self.copy(url, copy))
        copy.pack(side="left", padx=(8, 0))
        if note:
            tk.Label(row, text=note, font=(self.ui, 9), bg=PANEL, fg=DIM).pack(side="left", padx=(10, 0))

    def copy(self, url, button):
        self.root.clipboard_clear()
        self.root.clipboard_append(url)
        button.configure(text="Copied")
        self.root.after(1500, lambda: button.winfo_exists() and button.configure(text="Copy"))

    def draw_qr(self, url):
        self.qr.delete("all")
        self.qr.configure(bg="white" if url else PANEL)
        if not url:
            self.qr_caption.configure(text="")
            return
        modules = qr.encode(url)
        cell = self.qr_size // (len(modules) + 8)  # four light modules around it, so the camera finds its edge
        offset = (self.qr_size - cell * len(modules)) // 2
        for r, row in enumerate(modules):
            for c, dark in enumerate(row):
                if dark:
                    x, y = offset + c * cell, offset + r * cell
                    self.qr.create_rectangle(x, y, x + cell, y + cell, fill="black", width=0)
        self.qr_caption.configure(text="Scan it with the phone's camera")

    def append_log(self, text):
        log = self.log
        at_end = log.yview()[1] >= 0.999
        log.configure(state="normal")
        log.insert("end", text)
        excess = int(log.index("end-1c").split(".")[0]) - LOG_LINES
        if excess > 0:
            log.delete("1.0", f"{excess + 1}.0")
        log.configure(state="disabled")
        if at_end:
            log.see("end")

    def check(self):
        if self.checking or not self.served or self.closing:
            return
        self.checking = True
        self.check_button.configure(state="disabled", text="Checking ...")
        host, port = self.companion.running.server_address[:2]
        found = netcheck.Findings(port=port, listen=host, program=netcheck.this_program(),
                                  default_ip=self.companion.lan_ip())
        self.events.put(("findings", found))
        threading.Thread(target=self.gather, args=(found,), name="check", daemon=True).start()

    def gather(self, found):
        try:
            netcheck.gather(found, progress=lambda copy: self.events.put(("findings", copy)))
        except Exception:
            traceback.print_exc()
        self.events.put(("checked", None))

    def show_steps(self):
        if self.findings is None:
            return
        steps = self.steps
        top = steps.yview()[0]
        for button in self.step_buttons:
            button.destroy()
        self.step_buttons = []
        steps.configure(state="normal")
        steps.delete("1.0", "end")
        for number, step in enumerate(netcheck.steps(self.findings, self.visit)):
            if number:
                steps.insert("end", "\n", "gap")
            badge, _ = BADGES[step.state]
            steps.insert("end", badge.center(7), step.state)
            steps.insert("end", "  " + step.title + "\n", "title")
            steps.insert("end", step.found + "\n", "body")
            if step.fix:
                steps.insert("end", step.fix + "\n", "fix")
            label = ACTIONS.get(step.action)
            if label and self.served:
                button = self.button(steps, label, lambda action=step.action: self.act(action))
                self.step_buttons.append(button)
                steps.insert("end", " ", "body")  # the button lines up with the text above
                steps.window_create("end", window=button, pady=4)
                steps.insert("end", "\n")
        steps.configure(state="disabled")
        steps.yview_moveto(top)

    def act(self, action):
        if action == "allow-python":
            program, arguments = netcheck.allow_rule(self.findings)
            print(f"Asking Windows for permission to let Python in on "
                  f"{netcheck.network_profile(self.findings)} networks.")
            threading.Thread(target=self.elevate, args=(program, arguments), daemon=True).start()

    def elevate(self, program, arguments):
        try:
            self.events.put(("allowed", netcheck.run_elevated(program, arguments)))
        except OSError as exc:
            self.events.put(("allowed", str(exc)))

    def allowed(self, result):
        if result is None:
            print("Nothing changed: Windows' permission question was answered with No.")
        elif result == 0:
            print(f"Firewall rule added: \"{netcheck.RULE_NAME}\" lets Python in on "
                  f"{netcheck.network_profile(self.findings)} networks.")
            self.check()
        else:
            print(f"The firewall rule couldn't be added ({result if isinstance(result, str) else f'exit code {result}'}).")

    def ended(self, problem):
        if self.closing or (problem is None and self.served):
            self.quit()  # closed, or the companion has closed itself (the game has exited)
            return
        self.stopped.configure(text=(problem or "The companion has stopped.") + "\nClose this window to quit.")
        self.stopped.grid(row=1, column=0, sticky="ew", padx=16, pady=(12, 0))
        self.status.configure(text="Stopped", fg=DIM)
        self.check_button.configure(state="disabled")

    def close(self):
        if self.closing or self.gone:
            return
        if self.thread is None or not self.thread.is_alive():
            self.quit()
            return
        self.closing = True
        self.status.configure(text="Closing ...")
        threading.Thread(target=self.stop_companion, daemon=True).start()
        self.root.after(15000, self.quit)  # whatever it is stuck on

    def stop_companion(self):
        deadline = time.time() + 10
        while self.companion.running is None and self.thread.is_alive() and time.time() < deadline:
            time.sleep(0.05)  # still starting: stop() needs the server it starts
        if self.thread.is_alive():
            self.companion.stop()

    def quit(self):
        if not self.gone:
            self.gone = True
            self.root.destroy()


def run():
    """Shows the window and runs the companion until the window is closed; False if there is no screen for it."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # sharp text on a scaled screen, not a blurry bitmap
        except (AttributeError, OSError):
            pass
    try:
        root = tk.Tk()
    except tk.TclError:
        return False
    window = Window(root)
    window.start()
    interrupt = signal.signal(signal.SIGINT, lambda *_: root.after(0, window.close))  # Ctrl+C in the console
    try:
        root.mainloop()
    finally:
        signal.signal(signal.SIGINT, interrupt)
        window.finish()
    return True
