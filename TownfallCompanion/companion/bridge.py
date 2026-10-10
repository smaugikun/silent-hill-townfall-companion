#!/usr/bin/env python3
import argparse
import errno
import hashlib
import hmac
import html
import json
import math
import os
import queue
import re
import signal
import socket
import struct
import sys
import tempfile
import threading
import time
from http import HTTPStatus
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

import config
import game_profile
from crtv_native import NativeFrames
from game_audio import GameAudio

STATIC = Path(__file__).resolve().parent / "static"
# Bump when the phone UI starts relying on something new in the bridge (static/app.js checks it).
BRIDGE_VERSION = 31
# Taken by another program, or kept by Windows for itself (Hyper-V and WSL reserve ranges of ports).
PORT_UNAVAILABLE = {errno.EADDRINUSE, errno.EACCES, getattr(errno, "WSAEACCES", errno.EACCES)}
PORT_TRIES = 20
# Written ~10x/sec by the UE4SS mod (Scripts/main.lua).
DEFAULT_TELEMETRY_FILE = Path(tempfile.gettempdir()) / "townfall-companion-telemetry.json"
TELEMETRY_STALE_AFTER = 2.0
# The companion and the mod also talk through two small heartbeat files next to the telemetry: the companion's says
# it is here and how many phones have the page open (the mod reads the game only while one does), the game's
# says the game is running (the companion closes once it has stopped).
HEARTBEAT_FILE = "townfall-companion-bridge.json"
GAME_FILE = "townfall-companion-game.json"
HEARTBEAT_EVERY = 1.0
STREAM_IDLE_S = 3.0  # the picture and sound streams end after this long with nothing new; the phone opens them again
EVENT_PING_S = 5.0   # /events says it is alive this often when nothing else came (the phone reconnects after 12 s)
HEARTBEAT_FRESH_S = 4.0
GAME_GONE_AFTER = 60.0  # a game that hasn't said it runs for this long has closed (a level load can stall it a while)
MAX_BODY = 4096  # the phone's commands are tiny; more is not from the phone
DISCARD_LIMIT = 1 << 20  # a refused body up to this size is read and dropped; a bigger one closes the connection
# Phone commands for the mod, next to the telemetry file: /api/control "type" -> file.
COMMAND_FILES = {"crtv": "townfall-companion-commands.json", "steer": "townfall-companion-steer.json",
                 "confirm": "townfall-companion-confirm.json", "release": "townfall-companion-release.json",
                 "audio": "townfall-companion-audio.json", "fine_tune": "townfall-companion-fine-tune.json",
                 "align": "townfall-companion-align.json", "mode": "townfall-companion-mode.json",
                 "note": "townfall-companion-note.json", "look": "townfall-companion-look.json"}
# Whether the phone's turns move the mini-game's image, or why not (static/app.js alignmentOff), for the game's log.
ALIGN_STATES = {"on", "not in view", "player dead", "no game data", "crtv down", "centre held", "game paused",
                "game samples stopped", "page hidden", "motion sensor off", "motion sensor quiet",
                "motion sensor unchanged"}
# A problem on the phone's page, for the game's log: printable characters, no quote or backslash (the mod reads it
# with a plain pattern).
NOTE_TEXT = re.compile(r'[ !#-\[\]-~]{1,200}')
# The phone's buttons and its mode, shown in the companion's window as they come (the rest comes many times a second).
SHOWN = {"confirm": "phone pressed the D-pad's centre", "release": "phone let go of the D-pad's centre",
         "fine_tune": "phone pressed fine-tune {direction}", "mode": "phone switched to {selector}",
         "note": "phone: {text}"}

state_lock = threading.Lock()
telemetry = {
    "player": {"x": 0.0, "y": 0.0, "yaw": 0.0, "alive": True},
    "enemies": [],
    "signals": [],
    "crtv": {"active": False, "frequency": 0.0, "signalType": "none"},
    "cutscene": None,
    "gameLive": False,
    # The phone UI checks this, so a bridge left running from before an update is noticed.
    "bridge": BRIDGE_VERSION,
}
last_command_seq = 0
clients = []
clients_lock = threading.Lock()


def snapshot():
    with state_lock:
        return json.loads(json.dumps(telemetry))


def broadcast(payload):
    data = json.dumps(payload, separators=(",", ":"))
    dead = []
    with clients_lock:
        for q in clients:
            try:
                q.put_nowait(data)
            except queue.Full:
                try:
                    q.get_nowait()
                    q.put_nowait(data)
                except Exception:
                    dead.append(q)
        for q in dead:
            if q in clients:
                clients.remove(q)


def read_heartbeat(path):
    """The heartbeat file's content if it is no older than HEARTBEAT_FRESH_S, else None."""
    try:
        beat = json.loads(Path(path).read_text(encoding="utf-8"))
        if time.time() - float(beat["time"]) <= HEARTBEAT_FRESH_S:
            return beat
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def stop_older(beat):
    """Ends the companion of an older version that wrote `beat`: one that stayed open from before an update (it
    closes a minute after the game does) would go on serving the new page and mod its old commands. Its heartbeat
    is a second old at most, so its pid is that companion's. True once its port is free."""
    try:
        os.kill(int(beat["pid"]), signal.SIGTERM)  # on Windows: the process ends at once
        port = int(beat["port"])
    except (OSError, KeyError, TypeError, ValueError):
        return False
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            time.sleep(0.1)  # still answering
        except OSError:
            return True
    return False


# The heartbeat file stays open and is rewritten in place, padded to HEARTBEAT_BYTES, so the mod can keep it open
# too (main.lua): opening a file in %TEMP% waits for the virus scanner, on the game's thread.
HEARTBEAT_BYTES = 256
heartbeat_files = {}  # path -> the open file


def write_heartbeat(path, port):
    with clients_lock:
        phones = len(clients)
    # nativeFps / nativeWidth: how often and how wide the game sends the CRTV's screen; nothing without a phone.
    # version: a companion started after an update replaces one still running from before it (stop_older).
    beat = {"time": int(time.time()), "pid": os.getpid(), "port": port, "phones": phones, "version": BRIDGE_VERSION,
            "nativeFps": Handler.stream_fps if phones else 0, "nativeWidth": Handler.stream_width}
    try:
        out = heartbeat_files.get(path)
        if out is None:
            out = heartbeat_files[path] = open(path, "r+b" if Path(path).exists() else "w+b")
        out.seek(0)
        out.write(json.dumps(beat).encode("ascii").ljust(HEARTBEAT_BYTES))
        out.flush()
    except OSError:
        heartbeat_files.pop(path, None)  # opened again in a second


def close_heartbeats():
    for out in heartbeat_files.values():
        out.close()
    heartbeat_files.clear()


def ipc_files(telemetry_file):
    """Everything the companion and the mod leave in the temp folder."""
    folder = Path(telemetry_file).parent
    names = [*COMMAND_FILES.values(), HEARTBEAT_FILE, GAME_FILE]
    return [Path(telemetry_file), *(folder / name for name in names)]


def remove_ipc_files(telemetry_file):
    for path in ipc_files(telemetry_file):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def read_game_beat(path, within):
    """When the game last said it runs (its heartbeat's time), if no longer ago than `within` seconds; else None,
    also when the file can't be read."""
    try:
        beat = float(json.loads(Path(path).read_text(encoding="utf-8"))["time"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return beat if time.time() - beat <= within else None


def heartbeat_loop(telemetry_file, port, server, gone_after=GAME_GONE_AFTER):
    """Says every second that the companion is here and how many phones have the page open, and closes it
    once a game it has seen running has stopped saying so for `gone_after` seconds. Started without the game,
    it just keeps running. A read that fails changes nothing: the mod rewrites the file every second, and a
    read in that moment finds it empty."""
    folder = Path(telemetry_file).parent
    last_beat = None  # the newest heartbeat time read, once the game has been seen
    while True:
        write_heartbeat(folder / HEARTBEAT_FILE, port)
        beat = read_game_beat(folder / GAME_FILE, gone_after)
        if beat is not None:
            last_beat = beat if last_beat is None else max(last_beat, beat)
        elif last_beat is not None and time.time() - last_beat > gone_after:
            print("The game has closed: closing the companion.")
            server.shutdown()
            return
        time.sleep(HEARTBEAT_EVERY)


def merge_telemetry(payload):
    with state_lock:
        for key in ("player", "crtv", "audio"):
            if isinstance(payload.get(key), dict):
                telemetry.setdefault(key, {}).update(payload[key])
        for key in ("enemies", "signals"):
            if isinstance(payload.get(key), list):
                telemetry[key] = payload[key]
        if "cutscene" in payload:  # null when there is none: replaced, not merged
            telemetry["cutscene"] = payload["cutscene"]
        if isinstance(payload.get("t"), (int, float)):  # the game's clock at the sample, for timing on the phone
            telemetry["t"] = payload["t"]
        if "world" in payload:  # the world's clock: standing still, the game is paused (null: unreadable)
            telemetry["world"] = payload["world"] if isinstance(payload["world"], (int, float)) else None
        out = json.loads(json.dumps(telemetry))
    broadcast(out)


game_file_live = False


def update_live():
    """Tell clients whether the game's data is flowing, apart from whether they reach the bridge; the phone
    shows and plays nothing without it."""
    with state_lock:
        telemetry["gameLive"] = game_file_live
        out = json.loads(json.dumps(telemetry))
    broadcast(out)


def finite(value):
    """A JSON number as a float. Not a bool or a string (the phone only ever sends numbers), and not NaN, infinity
    or too big for a float: the mod can't read those, and a hand-made request could carry them."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("a number is expected")
    try:
        number = float(value)
    except OverflowError:
        raise ValueError("number too large") from None
    if not math.isfinite(number):
        raise ValueError("not a finite number")
    return number


def send_to_game(commands_dir, payload):
    """Hand a phone command to the UE4SS mod, which polls these files (tf_commands.lua)."""
    global last_command_seq
    if payload["type"] == "crtv":
        # active switches the in-game CRTV on (VIEW); without it only the dial moves, up or down. The mod puts away
        # what the phone switched on by the phone's mode.
        command = {}
        if "active" in payload:
            if payload["active"] is not True:
                raise ValueError("active can only be true")
            command["active"] = True
            if "animate" in payload:
                if not isinstance(payload["animate"], bool):
                    raise ValueError("animate must be true or false")
                command["animate"] = payload["animate"]  # the character's raise animation, or silently
        command["frequency"] = min(1.0, max(0.0, finite(payload["frequency"])))
    elif payload["type"] == "steer":
        # The phone's heading, and how far up it is tilted (degrees): the mod turns the player and tilts the camera by
        # how far they change (tf_commands.lua).
        command = {"yaw": finite(payload["yaw"]) % 360}
        if "pitch" in payload:
            pitch = finite(payload["pitch"])
            if abs(pitch) > 90:
                raise ValueError("pitch must be within 90 degrees of level")
            command["pitch"] = pitch
    elif payload["type"] == "fine_tune":
        direction = payload.get("direction")
        if not isinstance(direction, str) or direction not in ("up", "down", "left", "right"):
            raise ValueError("direction must be up, down, left, or right")
        command = {"direction": direction}
    elif payload["type"] == "mode":
        # VIEW or AV OUT, and whether in VIEW the monitor shows the CRTV, and in the mini-game; the phone says it every
        # couple of seconds.
        if payload.get("selector") not in ("VIEW", "AV_OUT"):
            raise ValueError("selector must be VIEW or AV_OUT")
        if not isinstance(payload.get("onMonitor"), bool) or not isinstance(payload.get("miniGameShown"), bool):
            raise ValueError("onMonitor and miniGameShown must be true or false")
        command = {"selector": payload["selector"], "onMonitor": payload["onMonitor"],
                   "miniGameShown": payload["miniGameShown"]}
    elif payload["type"] == "align":
        # The phone's lean and tilt from its neutral pose (degrees), how many degrees move the image half its range,
        # and which neutral pose (session).
        roll, pitch, dpu = finite(payload["roll"]), finite(payload["pitch"]), finite(payload["dpu"])
        session = payload["session"]
        if isinstance(session, bool) or not isinstance(session, int) or not 0 < session < 2 ** 31:
            raise ValueError("session must be a whole number")
        if abs(roll) > 180 or abs(pitch) > 90 or not 1 <= dpu <= 90:
            raise ValueError("alignment pose out of range")
        if not isinstance(payload["state"], str) or payload["state"] not in ALIGN_STATES:
            raise ValueError("unknown alignment state")
        command = {"roll": roll, "pitch": pitch, "dpu": dpu, "session": session, "state": payload["state"]}
    elif payload["type"] == "look":
        # Whether the phone looks around, how far from where the character faces and how far up (degrees).
        yaw, pitch = finite(payload["yaw"]), finite(payload["pitch"])
        if not isinstance(payload["on"], bool):
            raise ValueError("on must be true or false")
        if abs(yaw) > 180 or abs(pitch) > 90:
            raise ValueError("yaw must be within a half turn, pitch within a quarter")
        command = {"on": payload["on"], "yaw": yaw, "pitch": pitch}
    elif payload["type"] == "note":
        if not isinstance(payload["text"], str) or not NOTE_TEXT.fullmatch(payload["text"]):
            raise ValueError("a note is up to 200 plain characters")
        command = {"text": payload["text"]}
    elif payload["type"] == "audio":
        if not isinstance(payload["muteGame"], bool):
            raise ValueError("muteGame must be true or false")
        command = {"muteGame": payload["muteGame"]}  # the game's CRTV sound quiet while the phone plays it
    else:
        command = {}  # confirm, release: the press is the whole command
    with state_lock:
        # The mod acts on a changed seq; the clock keeps it changing across bridge restarts.
        last_command_seq = max(int(time.time() * 1000), last_command_seq + 1)
        # seq goes last, so a file the mod reads half-written has none and is skipped.
        command["seq"] = last_command_seq
        (commands_dir / COMMAND_FILES[payload["type"]]).write_text(json.dumps(command), encoding="utf-8")


PIN_COOKIE = "tfc_pin"
PIN_TRIES = 5        # wrong PINs from one address before it has to wait
PIN_LOCK_S = 60
pin_token = None     # set by main(): what the cookie of a phone that knows the PIN holds; None: no PIN
phone_info = {}      # set by main(): {"urls": [...], "pin": "..."} for the banner in the companion's window
visitors = {}        # address -> time of its first connection: what got through to the companion (gui.py's check)
running = None       # set by main(): the server, for stop()
pin_failures = {}    # address -> (wrong tries, locked until)
pin_lock = threading.Lock()

LOGIN_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Townfall Companion</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0b0a09;color:#d8cfb8;
font:16px/1.4 monospace}form{width:min(24rem,92vw);text-align:center}h1{font-size:1.1rem;letter-spacing:.2em}
input,button{width:100%;box-sizing:border-box;margin-top:1rem;padding:1.1rem;font:inherit;text-align:center;
background:#1a1713;color:inherit;border:1px solid #5a5240;border-radius:8px}
input{font-size:2.6rem;letter-spacing:.4em;padding-left:1.4em}button{font-size:1.5rem;letter-spacing:.2em}
p{min-height:1.4em;font-size:1.1rem;color:#c0604a}</style>
</head><body><form id="f"><h1>TOWNFALL COMPANION</h1><input id="p" type="password" inputmode="numeric" pattern="[0-9]*" maxlength="12" autocomplete="off"
placeholder="PIN" autofocus><button>OPEN</button><p id="m"></p></form><script>
f.onsubmit=async e=>{e.preventDefault();const r=await fetch("/login",{method:"POST",headers:{"Content-Type":"application/json"},
body:JSON.stringify({pin:p.value})});if(r.ok)location.reload();else{m.textContent=r.status==429?"Too many tries, wait a minute":"Wrong PIN";p.value=""}};
</script></body></html>"""

# The connection check (/check) needs no PIN: it tells a phone that the network lets it through, and nothing more.
CHECK_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Townfall Companion: connection check</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0b0a09;color:#d8cfb8;
font:18px/1.5 monospace}main{width:min(26rem,90vw);text-align:center}h1{font-size:1.1rem;letter-spacing:.2em}
a{display:block;margin-top:1.5rem;padding:1.1rem;color:inherit;border:1px solid #5a5240;border-radius:8px;
text-decoration:none;letter-spacing:.2em}</style>
</head><body><main><h1>TOWNFALL COMPANION</h1><p>Your phone reached Townfall Companion on {pc}.</p>
<p>Now open {address}{pin}.</p><a href="/">OPEN</a></main></body></html>"""


def pin_cookie_value(pin):
    return hashlib.sha256(f"townfall-companion:{pin}".encode("utf-8")).hexdigest()


class Handler(SimpleHTTPRequestHandler):
    server_version = "TownfallCompanion/0.1"
    # Connections stay open between requests: the phone sends its pose and presses many times a second, and over Wi-Fi a
    # new connection for each can hang and keep one of the few the browser allows per address. Responses carry their
    # length; the two that run on (the event feed, the picture stream) close their connection when they end. A
    # connection that sends or takes nothing for `timeout` seconds is dropped, so a phone that went away frees it.
    protocol_version = "HTTP/1.1"
    timeout = 30
    commands_dir = frames = audio = None  # set by main()
    stream_fps, stream_width = 15, 640     # the CRTV stream's, set by main()
    shown_mode = None  # the phone's mode as last shown: it says it every couple of seconds, shown when it changes
    # Windows' registry can map .js to text/plain, which browsers refuse for module scripts.
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".js": "text/javascript"}
    _no_cache = False
    _connection_said = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC), **kwargs)

    def setup(self):
        super().setup()
        visitors.setdefault(self.client_address[0], time.time())

    def log_message(self, fmt, *args):
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))

    def log_error(self, fmt, *args):
        if not fmt.startswith("Request timed out"):  # a kept-open connection the phone stopped using: as it should be
            super().log_error(fmt, *args)

    def log_request(self, code="-", size="-"):
        # The phone sends commands many times a second: only what failed is shown.
        if isinstance(code, int) and code >= 400:
            super().log_request(code, size)

    def _json(self, obj, status=200):
        raw = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _has_pin(self):
        cookies = self.headers.get("Cookie", "")
        for part in cookies.split(";"):
            name, _, value = part.strip().partition("=")
            if name == PIN_COOKIE and hmac.compare_digest(value, pin_token):
                return True
        return False

    def _gate(self, path, method):
        """True if the request may go on. With a PIN, a phone that doesn't have it yet gets the PIN page for the
        page itself and a refusal for everything else."""
        if pin_token is None or self._has_pin():
            return True
        if method == "POST" and path == "/login":
            return True
        self._discard_body()
        if method == "GET" and path in ("/", "/index.html"):
            raw = LOGIN_PAGE.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)
        else:
            self._json({"ok": False, "error": "PIN needed"}, 401)
        return False

    def _login(self, payload):
        who = self.client_address[0]
        now = time.time()
        with pin_lock:
            tries, until = pin_failures.get(who, (0, 0.0))
            if until > now:
                return self._json({"ok": False, "error": "too many tries"}, 429)
            sent = payload.get("pin")
            if isinstance(sent, str) and hmac.compare_digest(pin_cookie_value(sent), pin_token):
                pin_failures.pop(who, None)
                good = True
            else:
                tries += 1
                pin_failures[who] = (0, now + PIN_LOCK_S) if tries >= PIN_TRIES else (tries, 0.0)
                good = False
        if not good:
            return self._json({"ok": False, "error": "wrong PIN"}, 401)
        raw = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Set-Cookie", f"{PIN_COOKIE}={pin_token}; Max-Age=31536000; Path=/; SameSite=Strict; HttpOnly")
        self.end_headers()
        self.wfile.write(raw)

    def _body_length(self):
        """The request body's length, or None if it doesn't say a usable one."""
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            return None
        return length if length >= 0 else None

    def _discard_body(self):
        """Reads a body nothing will use, so the connection can carry the next request (and the client isn't cut off
        while still sending it); one too big for that, or of no telling length, closes the connection instead."""
        length = self._body_length()
        if length is not None and length <= DISCARD_LIMIT:
            self.rfile.read(length)
        else:
            self.close_connection = True

    def _check_page(self):
        # The address as the phone typed it, so the page can say where to go next.
        address = "http://" + (self.headers.get("Host") or f"{lan_ip()}:{self.server.server_address[1]}")
        raw = (CHECK_PAGE.replace("{pin}", " and enter the PIN" if pin_token else "")
               .replace("{pc}", html.escape(socket.gethostname()))
               .replace("{address}", html.escape(address))).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _read_json(self):
        length = self._body_length()
        if length is None:
            self.close_connection = True  # no telling where the body ends
            raise ValueError("bad Content-Length")
        if length == 0:
            return {}
        if length > MAX_BODY:
            self._discard_body()
            raise ValueError("body too large")
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("a JSON object is expected")
        return payload

    def do_HEAD(self):
        if self._gate(urlparse(self.path).path, "HEAD"):
            super().do_HEAD()

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/check":
            return self._check_page()
        if not self._gate(path, "GET"):
            return
        if path == "/api/state":  # what the phone gets, to look at in a browser when something seems off
            return self._json(snapshot())
        if path == "/api/crtv/status":
            return self._json(self.frames.status() if self.frames else {"status": "disabled"})
        if path == "/api/crtv/stream":
            return self._stream_frames()
        if path == "/api/audio/stream":
            return self._stream_audio()
        if path == "/api/crtv/frame":  # one picture, to look at in a browser
            frame = self.frames.latest() if self.frames else None
            if frame is None:
                self.send_response(HTTPStatus.NO_CONTENT)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            meta, raw, (width, height) = frame
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", self.frames.content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-CRTV-Sequence", str(meta["seq"]))
            self.send_header("X-CRTV-Size", f"{width}x{height}")
            self.end_headers()
            self.wfile.write(raw)
            return
        if path == "/events":
            q = queue.Queue(maxsize=4)
            with clients_lock:
                clients.append(q)
            self.close_connection = True
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                initial = json.dumps(snapshot(), separators=(",", ":"))
                self.wfile.write(f"data: {initial}\n\n".encode("utf-8"))
                self.wfile.flush()
                while True:
                    try:
                        data = q.get(timeout=EVENT_PING_S)
                        self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                    except queue.Empty:
                        self.wfile.write(b"event: ping\ndata: {}\n\n")  # the phone hears the feed is alive
                    self.wfile.flush()
            except (ConnectionError, TimeoutError):
                pass
            finally:
                with clients_lock:
                    if q in clients:
                        clients.remove(q)
            return

        if path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def _stream_frames(self):
        """The CRTV's screen as it comes, each new picture the moment it is there, on one connection: a 4-byte
        big-endian length, then the JPEG, again and again. Even pacing and no request per picture."""
        self.close_connection = True
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        sent, sent_at = None, time.time()
        try:
            while time.time() - sent_at < STREAM_IDLE_S:
                frame = self.frames.latest() if self.frames else None
                if frame and frame[0]["seq"] != sent:
                    sent, sent_at = frame[0]["seq"], time.time()
                    self.wfile.write(len(frame[1]).to_bytes(4, "big") + frame[1])
                    self.wfile.flush()
                else:
                    time.sleep(0.005)
        except (ConnectionError, TimeoutError):
            pass  # the phone went away, or left the stream

    def _stream_audio(self):
        """The game's sound as it comes (static/audio-stream.js): "TFAU", the sample rate (uint32) and the channels
        (uint16), little-endian, then 16-bit samples, interleaved. It ends when no sound has come for STREAM_IDLE_S
        (the game closed or paused its mixer) or the format changes (a game started anew): the phone opens it again."""
        said = self.audio.format() if self.audio else None
        if not said:
            return self._json({"ok": False, "error": "no sound from the game"}, 503)
        self.close_connection = True
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        position, heard_at = None, time.time()
        try:
            self.wfile.write(b"TFAU" + struct.pack("<IH", *said))
            self.wfile.flush()
            while time.time() - heard_at < STREAM_IDLE_S and self.audio.format() == said:
                data, position = self.audio.read(position)
                if data:
                    self.wfile.write(data)
                    self.wfile.flush()
                    heard_at = time.time()
                else:
                    time.sleep(0.01)
        except (ConnectionError, TimeoutError):
            pass  # the phone went away, or stopped playing

    def send_head(self):
        # The page's own files (GET and HEAD). Without Cache-Control the phone's Chrome keeps them for a
        # while on a guess and runs an old style.css / app.js after an update; no-cache makes it ask each
        # time (a 304 if unchanged).
        self._no_cache = True
        try:
            return super().send_head()
        finally:
            self._no_cache = False

    def send_response(self, code, message=None):
        self._connection_said = False
        super().send_response(code, message)

    def send_header(self, keyword, value):
        if keyword.lower() == "connection":
            self._connection_said = True
        super().send_header(keyword, value)

    def end_headers(self):
        if self._no_cache:
            self.send_header("Cache-Control", "no-cache")
        if self.close_connection and not self._connection_said:
            self.send_header("Connection", "close")  # the client mustn't send another request on it
        super().end_headers()

    def do_POST(self):
        path = urlparse(self.path).path
        if not self._gate(path, "POST"):
            return
        try:
            payload = self._read_json()
        except Exception as exc:
            return self._json({"ok": False, "error": f"invalid json: {exc}"}, 400)

        if path == "/login":
            if pin_token is None:
                return self._json({"ok": True})
            return self._login(payload)

        if path == "/api/control":
            kind = payload.get("type")
            if not isinstance(kind, str) or kind not in COMMAND_FILES:  # a list or object would not even hash
                return self._json({"ok": False, "error": f"unknown command type: {kind!r}"}, 400)
            try:
                send_to_game(self.commands_dir, payload)
            except (KeyError, TypeError, ValueError) as exc:
                return self._json({"ok": False, "error": f"bad {kind} command: {exc}"}, 400)
            if kind in SHOWN:
                shown = SHOWN[kind].format(direction=payload.get("direction"), selector=payload.get("selector"),
                                           text=payload.get("text"))
                repeated = kind == "mode" and shown == Handler.shown_mode
                if kind == "mode":
                    Handler.shown_mode = shown
                if not repeated:
                    self.log_message("%s", shown)
            return self._json({"ok": True})

        return self._json({"ok": False, "error": "not found"}, 404)


def watch_telemetry_file(path):
    """Feeds the game's telemetry file, as the mod rewrites it, into the state the phone gets."""
    global game_file_live
    last_raw, live, read_at = None, False, 0.0
    while True:
        try:
            now_live = time.time() - path.stat().st_mtime < TELEMETRY_STALE_AFTER
            raw = path.read_text(encoding="utf-8") if now_live else None
            read_at = time.time()
        except OSError:
            # Gone, or held by whoever is writing it (Windows refuses a read meanwhile): the game has left
            # only once the file stays unreadable as long as it may be old.
            now_live, raw = live and time.time() - read_at < TELEMETRY_STALE_AFTER, None
        if now_live != live:
            live = game_file_live = now_live
            print(f"Game telemetry {'live' if live else 'stale'}: {path}")
            update_live()
        if raw and raw != last_raw:
            try:
                merge_telemetry(json.loads(raw))
                last_raw = raw
            except ValueError:
                pass  # read it mid-write; the next poll sees the whole file
        time.sleep(0.05)


class BridgeServer(ThreadingHTTPServer):
    # Windows lets a server bind 0.0.0.0:port while another app listens on 127.0.0.1:port, and
    # local requests then silently go to that app. Exclusive use turns the clash into an error.
    allow_reuse_address = not hasattr(socket, "SO_EXCLUSIVEADDRUSE")

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def handle_error(self, request, client_address):
        # Phones drop idle connections all the time (screen lock, tab switch); that's not an error.
        if isinstance(sys.exc_info()[1], ConnectionError):
            return
        super().handle_error(request, client_address)


def lan_ip():
    # Connecting a UDP socket sends nothing; it only makes the OS pick the outgoing interface.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.0.2.1", 80))
            return s.getsockname()[0]
        except OSError:
            return None


def page_version():
    """Changes whenever a file of the phone's page does. The page reloads itself when it sees another one:
    an open page reconnects to a restarted bridge by itself, and kept running its old code after an
    update."""
    files = sorted(p for p in STATIC.rglob("*") if p.is_file())
    listing = "\n".join(f"{p.relative_to(STATIC)}:{p.stat().st_size}:{p.stat().st_mtime_ns}" for p in files)
    return hashlib.sha1(listing.encode("utf-8")).hexdigest()[:12]


# The docs come in the mod's download, beside the TownfallCompanion folder.
PICTURE_PROBLEM = ("The CRTV's picture can't be streamed to the phone. How to fix it: see "
                   '"The CRTV\'s picture can\'t be streamed" in docs\\TROUBLESHOOTING.md (in the mod\'s download).')


def prepare_game_profile():
    """After a game update, the game's own functions and structures are looked up again for tf_native.dll
    (game_profile.py): a few seconds, once, in a process of its own at low priority, long done before anyone is in the
    game. The DLL picks it up as soon as it is there, also while the game runs. Only for the game the mod is installed
    in. Said only when it fails, and then only where to read what to do: `py game_profile.py` says why."""
    game = config.installed_game_dir()
    exe = game / game_profile.GAME_EXE if game else None
    if not exe or not exe.is_file() or not game_profile.needed(exe):
        return
    try:
        game_profile.write_apart(exe)
    except Exception:  # a game that changed what the DLL relies on, its PDB gone, or the mod's folder not writable
        print(PICTURE_PROBLEM)


def connect_banner():
    """Where the phone connects and the PIN, framed so it stands out from the startup messages."""
    lines = ["Open this on the phone: " + (", ".join(phone_info.get("urls", [])) or "(this PC has no network address)"),
             "PIN: " + (phone_info["pin"] if phone_info.get("pin") else "none (set pin in the settings to ask for one)")]
    width = max(len(line) for line in lines) + 4
    return "\n".join(["", "+" + "-" * width + "+", *("|  " + line.ljust(width - 2) + "|" for line in lines),
                      "+" + "-" * width + "+", ""])


def open_server(host, port):
    """The server and its port: `port`, or the first free one of the next few if that one is unavailable."""
    for candidate in range(port, min(port + PORT_TRIES, 65536)):
        try:
            return BridgeServer((host, candidate), Handler), candidate
        except OSError as exc:
            if exc.errno not in PORT_UNAVAILABLE:
                raise
    raise SystemExit(f"Ports {port} to {candidate} are all taken by other programs. Set another port in companion.ini.")


def stop():
    """Ends main() from another thread, as closing the companion's window does; main() cleans up as after Ctrl+C."""
    if running:
        running.shutdown()


def main():
    parser = argparse.ArgumentParser(description="Townfall Companion: serves the phone's page and passes what "
                                     "happens between the phone and the game. Settings: companion.ini.")
    parser.add_argument("--settings", type=Path, default=config.SETTINGS_FILE,
                        help="the settings file, written with the defaults if it isn't there (default: %(default)s)")
    parser.add_argument("--host", help="overrides listen in the settings")
    parser.add_argument("--port", type=int, help="overrides port in the settings")
    parser.add_argument("--pin", help="overrides pin in the settings; empty turns the PIN off")
    parser.add_argument("--game-gone-after", type=float, default=GAME_GONE_AFTER, help=argparse.SUPPRESS)  # tests
    parser.add_argument("--stream-fps", type=int, choices=range(1, 31), default=30, metavar="1-30",
                        help="the most pictures a second the CRTV stream sends (default: %(default)s)")
    parser.add_argument("--stream-width", type=int, choices=(320, 480, 640), default=640,
                        help="the CRTV stream's picture width in pixels (default: %(default)s)")
    parser.add_argument("--telemetry-file", type=Path, default=DEFAULT_TELEMETRY_FILE,
                        help="JSON file written by the UE4SS mod; phone commands go next to it")
    args = parser.parse_args()
    try:
        config.disable_ue4ss_console()
        console_problem = None
    except OSError as exc:
        console_problem = f"couldn't change {config.UE4SS_SETTINGS_FILE}: {exc}"
    settings = config.load(args.settings)
    already = read_heartbeat(args.telemetry_file.parent / HEARTBEAT_FILE)
    version = already.get("version") if already else None
    if already and not (isinstance(version, int) and version >= BRIDGE_VERSION):  # older ones don't say
        print(f"An older Townfall Companion is still running (port {already.get('port')}): this one replaces it.")
        if not stop_older(already):
            raise SystemExit("The older Townfall Companion didn't stop: close its window, then start this again.")
    elif already:
        raise SystemExit(f"Townfall Companion is already running (port {already.get('port')}): use that window, "
                         "or close it first.")
    global pin_token
    pin = settings.pin if args.pin is None else args.pin.strip()
    if pin and not (pin.isdigit() and 4 <= len(pin) <= 12):
        raise SystemExit("--pin must be 4 to 12 digits, or empty for none")
    pin_token = pin_cookie_value(pin) if pin else None
    host = args.host or settings.listen

    Handler.commands_dir = args.telemetry_file.parent
    Handler.frames = NativeFrames(Handler.commands_dir)
    Handler.audio = GameAudio()
    Handler.stream_fps, Handler.stream_width = args.stream_fps, args.stream_width
    telemetry["page"] = page_version()
    wanted = args.port or settings.port
    server, port = open_server(host, wanted)
    global running
    running = server

    # Only what a player needs: the version, problems, and (below) the port and the phone's address and PIN.
    print(f"Townfall Companion {config.VERSION}")
    if console_problem:
        print(f"UE4SS console: {console_problem}. To hide it, set ConsoleEnabled, GuiConsoleEnabled and "
              "GuiConsoleVisible to 0 in that file yourself.")
    threading.Thread(target=heartbeat_loop, args=(args.telemetry_file, port, server, args.game_gone_after),
                     daemon=True).start()
    threading.Thread(target=watch_telemetry_file, args=(args.telemetry_file,), daemon=True).start()
    threading.Thread(target=prepare_game_profile, daemon=True).start()

    if port != wanted:
        print(f"Port {wanted} is taken by another program, so the companion uses port {port}. The phone's address "
              f"changes with it: if you entered the old one in Chrome's \"Insecure origins treated as secure\" flag, "
              f"enter this one there too.")
    if host == "0.0.0.0":
        if not lan_ip():
            print("On the phone: this PC has no network address; connect it to the network the phone is on")
    else:
        print(f"Listening on http://{host}:{port}")
    phone_info.update(pin=pin or None, urls=[f"http://{lan_ip()}:{port}"] if host == "0.0.0.0" and lan_ip() else [])
    print(connect_banner(), end="")
    print(f"The phone asks for the PIN once (change it in {settings.file.name}).")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        Handler.frames.close()
        Handler.audio.close()
        close_heartbeats()
        server.server_close()
        remove_ipc_files(args.telemetry_file)


if __name__ == "__main__":
    main()
