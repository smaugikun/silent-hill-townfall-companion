#!/usr/bin/env python3
import argparse
import errno
import hashlib
import hmac
import json
import math
import os
import queue
import re
import socket
import sys
import tempfile
import threading
import time
from http import HTTPStatus
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import unquote, urlparse

import config
from convert_videos import GameVideos
from game_sounds import GameSounds, wav_seconds

STATIC = Path(__file__).resolve().parent / "static"
# Bump when the phone UI starts relying on something new in the bridge (static/app.js checks it).
BRIDGE_VERSION = 10
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
HEARTBEAT_FRESH_S = 4.0
GAME_GONE_AFTER = 60.0  # a game that hasn't said it runs for this long has closed (a level load can stall it a while)
MAX_BODY = 4096  # the phone's commands are tiny; more is not from the phone
# Phone commands for the mod, next to the telemetry file: /api/control "type" -> file.
COMMAND_FILES = {"crtv": "townfall-companion-commands.json", "steer": "townfall-companion-steer.json",
                 "confirm": "townfall-companion-confirm.json", "audio": "townfall-companion-audio.json"}

state_lock = threading.Lock()
telemetry = {
    "player": {"x": 0.0, "y": 0.0, "yaw": 0.0, "alive": True},
    "enemies": [],
    "signals": [],
    "crtv": {"active": False, "frequency": 0.0, "signalType": "none"},
    "cutscene": None,
    "gameLive": False,
    "demo": False,
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


def write_heartbeat(path, port):
    with clients_lock:
        phones = len(clients)
    beat = {"time": int(time.time()), "pid": os.getpid(), "port": port, "phones": phones}
    tmp = Path(path).with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(beat), encoding="utf-8")
        os.replace(tmp, path)  # whole or not at all: the mod reads it at any moment
    except OSError:
        pass  # the mod or a virus scanner holds it for a moment; the next one comes in a second


def ipc_files(telemetry_file):
    """Everything the companion and the mod leave in the temp folder."""
    folder = Path(telemetry_file).parent
    names = [*COMMAND_FILES.values(), HEARTBEAT_FILE, GAME_FILE, HEARTBEAT_FILE.replace(".json", ".tmp")]
    return [Path(telemetry_file), *(folder / name for name in names)]


def remove_ipc_files(telemetry_file):
    for path in ipc_files(telemetry_file):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def read_game_beat(path, within):
    """Whether the game's heartbeat is no older than `within` seconds."""
    try:
        return time.time() - float(json.loads(Path(path).read_text(encoding="utf-8"))["time"]) <= within
    except (OSError, ValueError, KeyError, TypeError):
        return False


def heartbeat_loop(telemetry_file, port, server, gone_after=GAME_GONE_AFTER):
    """Says every second that the companion is here and how many phones have the page open, and closes it
    once a game it has seen running has stopped saying so. Started without the game, it just keeps running."""
    folder = Path(telemetry_file).parent
    seen = False
    while True:
        write_heartbeat(folder / HEARTBEAT_FILE, port)
        if read_game_beat(folder / GAME_FILE, gone_after):
            seen = True
        elif seen:
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


# The demo game (demo_loop), for development: runs with --demo while the real game isn't sending; the real
# game always wins.
demo_on = threading.Event()
game_file_live = False


def demo_running():
    return demo_on.is_set() and not game_file_live


def update_live():
    """Tell clients whether game data is flowing (the game's or the demo's), apart from whether they
    reach the bridge; the phone shows and plays nothing without it."""
    with state_lock:
        telemetry["gameLive"] = game_file_live or demo_running()
        telemetry["demo"] = demo_running()
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
        # active raises or lowers the in-game CRTV (VIEW); without it only the dial moves, while it is up (AV OUT)
        command = {}
        if "active" in payload:
            if not isinstance(payload["active"], bool):
                raise ValueError("active must be true or false")
            command["active"] = payload["active"]
            if "animate" in payload:
                if not isinstance(payload["animate"], bool):
                    raise ValueError("animate must be true or false")
                command["animate"] = payload["animate"]  # the character's raise animation, or silently
        command["frequency"] = min(1.0, max(0.0, finite(payload["frequency"])))
    elif payload["type"] == "steer":
        command = {"yaw": finite(payload["yaw"]) % 360}  # the mod turns the player by yaw only (tf_commands.lua)
    elif payload["type"] == "audio":
        flags = {"dialogue": payload.get("dialogue", False), "video": payload.get("video", False)}
        if not isinstance(payload["muteGame"], bool) or not all(isinstance(v, bool) for v in flags.values()):
            raise ValueError("muteGame, dialogue and video must be true or false")
        # dialogue / video: the phone plays the talking (a waypoint's line, a cutscene's dialogue) / the CRTV
        # screen's video with its sound, so the game's can go quiet too
        command = {"muteGame": payload["muteGame"], **flags}
    else:
        command = {}  # confirm: the press is the whole command
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


def pin_cookie_value(pin):
    return hashlib.sha256(f"townfall-companion:{pin}".encode("utf-8")).hexdigest()


class Handler(SimpleHTTPRequestHandler):
    server_version = "TownfallCompanion/0.1"
    commands_dir = sounds = videos = None  # set by main()
    # Windows' registry can map .js to text/plain, which browsers refuse for module scripts; older
    # Pythons don't know .webp.
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".js": "text/javascript", ".webp": "image/webp"}
    _no_cache = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC), **kwargs)

    def log_message(self, fmt, *args):
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))

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

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        if length > MAX_BODY:
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
        if not self._gate(path, "GET"):
            return
        if path == "/api/state":  # what the phone gets, to look at in a browser when something seems off
            return self._json(snapshot())
        if path == "/events":
            q = queue.Queue(maxsize=4)
            with clients_lock:
                clients.append(q)
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            try:
                initial = json.dumps(snapshot(), separators=(",", ":"))
                self.wfile.write(f"data: {initial}\n\n".encode("utf-8"))
                self.wfile.flush()
                while True:
                    try:
                        data = q.get(timeout=15)
                        self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                    except queue.Empty:
                        self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
            except ConnectionError:
                pass
            finally:
                with clients_lock:
                    if q in clients:
                        clients.remove(q)
            return

        if path.startswith("/clips/"):
            relative = unquote(path[len("/clips/"):])
            media = self.videos.mp4(relative)
            if not media:
                return self._json({"ok": False, "error": self.videos.problem or "no such video"}, 404)
            return self._serve_media(self.videos.cache_dir, media.relative_to(self.videos.cache_dir).as_posix(),
                                     {".mp4": "video/mp4"})
        if path == "/sounds/sounds.json":
            catalogue = self.sounds.catalogue()
            if catalogue is None:
                return self._json({"ok": False, "error": self.sounds.problem or "the game's sounds aren't listed yet"}, 404)
            return self._json(catalogue)
        sound = re.fullmatch(r"/sounds/(?:(dialogue|lines)/)?([^/]+)\.wav", path)
        if sound:
            wav = self.sounds.wav(sound[1] or "sounds", unquote(sound[2]))
            if not wav:
                return self._json({"ok": False, "error": "no such sound"}, 404)
            return self._serve_media(self.sounds.cache_dir, wav.relative_to(self.sounds.cache_dir).as_posix(),
                                     {".wav": "audio/wav"})
        if path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def send_head(self):
        # The page's own files (GET and HEAD). Without Cache-Control the phone's Chrome keeps them for a
        # while on a guess and runs an old style.css / app.js after an update; no-cache makes it ask each
        # time (a 304 if unchanged).
        self._no_cache = True
        try:
            return super().send_head()
        finally:
            self._no_cache = False

    def end_headers(self):
        if self._no_cache:
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def _serve_media(self, root, relative, types):
        """A file of one of `types` (suffix -> Content-Type) under root, nowhere else. Phones stream
        video with byte ranges, and iOS Safari won't play a clip without. no-cache with Last-Modified:
        a phone asks each time and gets a 304 unless the file was converted again."""
        root = root.resolve()
        media = (root / relative).resolve()
        if root not in media.parents or media.suffix not in types or not media.is_file():
            return self._json({"ok": False, "error": "not found"}, 404)
        modified = self.date_time_string(int(media.stat().st_mtime))
        if self.headers.get("If-Modified-Since") == modified and "Range" not in self.headers:
            self.send_response(HTTPStatus.NOT_MODIFIED)
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            return

        size = media.stat().st_size
        start, end = 0, size - 1
        ranged = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", "").strip())
        if ranged and (ranged[1] or ranged[2]):
            if ranged[1]:
                start = int(ranged[1])
                end = min(int(ranged[2]), size - 1) if ranged[2] else size - 1
            else:
                start = max(0, size - int(ranged[2]))
            if start > end:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            self.send_response(HTTPStatus.PARTIAL_CONTENT)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", types[media.suffix])
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Last-Modified", modified)
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        remaining = end - start + 1
        with media.open("rb") as f:
            f.seek(start)
            try:
                while remaining > 0:
                    chunk = f.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            except ConnectionError:
                pass  # the player dropped this range to ask for another one

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


def read_command(path, last_seq):
    """What tf_commands.lua does with a command file: (the command, its seq) if the seq is new."""
    try:
        command = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, last_seq
    seq = command.get("seq")
    if seq is None or seq == last_seq:
        return None, last_seq
    return command, seq


def demo_lines(sounds, count=4):
    """[(name, seconds)] of a few of the game's spoken lines, for the demo waypoint to say; none
    without the game's sounds."""
    lines = []
    for name in ((sounds.catalogue() if sounds else None) or {}).get("lines", [])[:count]:
        wav = sounds.wav("lines", name)
        if wav:
            lines.append((name, wav_seconds(wav)))
    return lines


def demo_loop(commands_dir, sounds):
    """A stand-in for Townfall and the mod, so the phone can be tried without the game.

    Telemetry shaped like Townfall's: a player turning slowly, whose CRTV is up for 16 s (its dial
    sweeping) and down for 8 s; four enemies circling, each on its own channel, with static that
    fades out at 35 m; one active waypoint signal, which has to be fine-tuned: on its channel a box runs
    to and fro along the fine-tune bar, and a press (F / A, "confirm") while it is over the diamond finds
    it. Tuned to a monster or a found waypoint, the CRTV's screen plays its video. On the waypoint's
    channel it talks, line after line of the game's (distorted until found). The phone's commands
    are read from the files the mod reads: a CRTV the phone raised stays up, tuned where the phone left
    it, until the phone lowers it; the player turns by as much as the phone turns, and stops turning
    on its own for 3 s after. It only runs while switched on and the real game isn't sending."""
    files = {kind: commands_dir / name for kind, name in COMMAND_FILES.items()}
    lines = []  # filled in the background: the first listing of the game's sound banks takes a while
    threading.Thread(target=lambda: lines.extend(demo_lines(sounds)), daemon=True).start()
    speech = None  # (line index, when it started) while the waypoint talks
    seqs = {kind: read_command(path, None)[1] for kind, path in files.items()}  # old files are a baseline
    t0 = time.time()
    phone_holds_until = steered_until = 0.0  # the simulated player keeps their hands off until then
    phone_heading = None  # (heading, seq) of the phone's last steer command, as the mod keeps it
    sound_asked = None    # when the phone last asked for the game's CRTV sound off (tf_audio.lua)
    active, dial, yaw = True, 0.5, 0.0
    channels = (0.23, 0.41, 0.62, 0.78)
    creatures = ("Enraged", "Sorrowful", "TheFallen", "TheWall")
    playing, playing_since = None, 0.0
    found = False
    zone, zone_width, box_period = 0.55, 0.1, 2.4  # the box crosses the bar in half a period
    while True:
        if not demo_running():
            time.sleep(0.2)
            continue
        now = time.time()
        t = now - t0
        command, seqs["crtv"] = read_command(files["crtv"], seqs["crtv"])
        if command:
            if "active" in command:
                active = command["active"]
                phone_holds_until = math.inf if active else now + 8.0
            if command.get("active", True):  # as the mod: the dial moves up or down, unless it is lowered
                dial = command["frequency"]
        command, seqs["steer"] = read_command(files["steer"], seqs["steer"])
        if command:
            if phone_heading and command["seq"] - phone_heading[1] <= 2000:
                yaw = (yaw + (command["yaw"] - phone_heading[0] + 180.0) % 360.0 - 180.0) % 360.0
            phone_heading, steered_until = (command["yaw"], command["seq"]), now + 3.0
        if now > phone_holds_until:
            active = t % 24.0 < 16.0
            dial = 0.5 + 0.45 * math.sin(t * 0.15)
        if now > steered_until:
            yaw = (yaw + 0.7) % 360.0
        on_waypoint = active and abs(dial - 0.15) < 0.02
        box = 1.0 - abs(2.0 * (t % box_period) / box_period - 1.0)
        command, seqs["audio"] = read_command(files["audio"], seqs["audio"])
        if command:
            sound_asked = now if command["muteGame"] else None
        command, seqs["confirm"] = read_command(files["confirm"], seqs["confirm"])
        if command and on_waypoint and not found:
            found = abs(box - zone) <= zone_width / 2

        enemies = []
        for i, phase in enumerate((0.0, 1.7, 3.3, 4.8)):
            r = 20.0 + 14.0 * math.sin(t * 0.25 + phase)
            a = t * (0.08 + i * 0.012) + phase
            x, y = math.cos(a) * r, math.sin(a) * r
            reach = round(max(0.0, 1.0 - math.hypot(x, y) / 35.0), 2)
            signal = reach if active else 0.0
            enemies.append({
                "id": f"mock-{i + 1}", "x": x, "y": y, "alive": True,
                "signal": signal, "detected": signal > 0, "tuned": signal > 0 and abs(dial - channels[i]) < 0.02,
                "rangeSignal": reach, "channel": channels[i], "tolerance": 0.02,
                "video": f"Bink/{creatures[i]}_Focused",
            })
        said = None
        if on_waypoint and lines:
            index, started = speech or (0, now)
            if now - started > lines[index][1] + 0.8:  # a breath, then the next line
                index, started = (index + 1) % len(lines), now
            speech = (index, started)
            if now - started <= lines[index][1]:
                said = {"line": lines[index][0], "id": "demo-waypoint", "ms": int((now - started) * 1000), "clear": found}
        else:
            speech = None
        reach = round(max(0.0, 1.0 - math.hypot(18.0, 30.0) / 60.0), 2)
        signals = [{
            "id": "demo-waypoint", "kind": "waypoint", "x": 18.0, "y": 30.0,
            "channel": 0.15, "tolerance": 0.02, "rangeSignal": reach, "signal": reach if active else 0.0,
            "tuned": on_waypoint, "found": found, "video": "Bink/Video_CRTV_Room204Door_Signal", "dialogue": said,
        }]
        tuned = next((s for s in enemies + signals if s["tuned"]), None)
        signal_type = ("none" if not tuned else "enemy" if tuned in enemies
                       else "waypoint_tuned" if found else "waypoint")
        video = tuned["video"] if tuned and signal_type != "waypoint" else None
        fine_tune = ({"box": round(box, 4), "zone": zone, "text": "FINE TUNE - SEARCHING"}
                     if signal_type == "waypoint" else None)
        if video != playing:
            playing, playing_since = video, now
        merge_telemetry({
            "t": round(now, 3),
            "player": {"x": 0.0, "y": 0.0, "yaw": round(yaw, 1)},
            "enemies": enemies,
            "signals": signals,
            "audio": {"gameSoundOff": sound_asked is not None and now - sound_asked < 5.0},
            "cutscene": None,  # the demo has none, and one the game left behind mustn't go on playing
            "crtv": {"active": active, "frequency": round(dial, 3) if active else 0.0, "signalType": signal_type,
                     "video": video, "videoTime": round(now - playing_since, 2) if video else None,
                     "fineTune": fine_tune},
        })
        time.sleep(0.1)


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
    update (2026-09-30: asking for sounds that no longer existed)."""
    files = sorted(p for p in STATIC.rglob("*") if p.is_file())
    listing = "\n".join(f"{p.relative_to(STATIC)}:{p.stat().st_size}:{p.stat().st_mtime_ns}" for p in files)
    return hashlib.sha1(listing.encode("utf-8")).hexdigest()[:12]


def connect_banner():
    """Where the phone connects and the PIN, framed so it stands out from the startup messages."""
    lines = ["Open this on the phone: " + (", ".join(phone_info.get("urls", [])) or "(this PC has no network address)"),
             "PIN: " + (phone_info["pin"] if phone_info.get("pin") else "none (set pin in the settings to ask for one)")]
    width = max(len(line) for line in lines) + 4
    return "\n".join(["", "+" + "-" * width + "+", *("|  " + line.ljust(width - 2) + "|" for line in lines),
                      "+" + "-" * width + "+", ""])


def report_sounds(sounds):
    catalogue = sounds.catalogue(timeout=300)
    if catalogue:
        print(f"Game sounds ready: {len(catalogue['sounds'])} CRTV sounds, {len(catalogue['dialogue'])} cutscene "
              f"dialogue tracks, {len(catalogue['lines'])} spoken lines (decoded as the phone asks, kept in {sounds.cache_dir})")
    else:
        print(f"Game sounds unavailable, the phone will be silent: {sounds.problem}")
    if phone_info:
        print(connect_banner(), end="")  # the last of the startup messages: say it again, so it isn't scrolled away


def open_server(host, port):
    """The server and its port: `port`, or the first free one of the next few if that one is unavailable."""
    for candidate in range(port, min(port + PORT_TRIES, 65536)):
        try:
            return BridgeServer((host, candidate), Handler), candidate
        except OSError as exc:
            if exc.errno not in PORT_UNAVAILABLE:
                raise
    raise SystemExit(f"Ports {port} to {candidate} are all taken by other programs. Set another port in companion.ini.")


def main():
    parser = argparse.ArgumentParser(description="Townfall Companion: serves the phone's page and passes what "
                                     "happens between the phone and the game. Settings: companion.ini.")
    parser.add_argument("--settings", type=Path, default=config.SETTINGS_FILE,
                        help="the settings file, written with the defaults if it isn't there (default: %(default)s)")
    parser.add_argument("--host", help="overrides listen in the settings")
    parser.add_argument("--port", type=int, help="overrides port in the settings")
    parser.add_argument("--game-dir", type=Path, help="overrides game in the settings")
    parser.add_argument("--vgmstream", type=Path, help="overrides vgmstream in the settings")
    parser.add_argument("--radvideo", type=Path, help="overrides RAD Video Tools in the settings")
    parser.add_argument("--ffmpeg", type=Path, help="overrides FFmpeg in the settings")
    parser.add_argument("--pin", help="overrides pin in the settings; empty turns the PIN off")
    parser.add_argument("--game-gone-after", type=float, default=GAME_GONE_AFTER, help=argparse.SUPPRESS)  # tests
    parser.add_argument("--demo", action="store_true",
                        help="for development: a simulated game while the real one isn't sending; the real game wins")
    parser.add_argument("--telemetry-file", type=Path, default=DEFAULT_TELEMETRY_FILE,
                        help="JSON file written by the UE4SS mod; phone commands go next to it")
    parser.add_argument("--clips-dir", type=Path, default=config.CLIPS_DIR,
                        help="the game's videos converted for the phone, served at /clips/")
    parser.add_argument("--sound-cache", type=Path, default=config.SOUNDS_DIR,
                        help="where the game's sounds are kept once decoded")
    args = parser.parse_args()
    try:
        console_changed, console_problem = config.disable_ue4ss_console(), None
    except OSError as exc:
        console_changed, console_problem = None, f"couldn't change {config.UE4SS_SETTINGS_FILE}: {exc}"
    settings = config.load(args.settings)
    already = read_heartbeat(args.telemetry_file.parent / HEARTBEAT_FILE)
    if already:
        raise SystemExit(f"Townfall Companion is already running (port {already.get('port')}): use that window, "
                         "or close it first.")
    global pin_token
    pin = settings.pin if args.pin is None else args.pin.strip()
    if pin and not (pin.isdigit() and 4 <= len(pin) <= 12):
        raise SystemExit("--pin must be 4 to 12 digits, or empty for none")
    pin_token = pin_cookie_value(pin) if pin else None
    host = args.host or settings.listen
    game_dir = args.game_dir or settings.game or config.find_game_dir()
    vgmstream = args.vgmstream or config.tool(settings, "vgmstream")
    radvideo = args.radvideo or config.tool(settings, "radvideo")
    ffmpeg = args.ffmpeg or config.tool(settings, "ffmpeg")

    Handler.commands_dir = args.telemetry_file.parent
    Handler.videos = GameVideos(game_dir, radvideo, ffmpeg, vgmstream, args.clips_dir)
    Handler.sounds = GameSounds(game_dir, vgmstream, args.sound_cache)
    Handler.sounds.start()
    telemetry["page"] = page_version()
    wanted = args.port or settings.port
    server, port = open_server(host, wanted)

    if args.demo:
        demo_on.set()
        update_live()
    print(f"Townfall Companion {config.VERSION}")
    if console_changed:
        print("UE4SS console: disabled for the next Townfall launch (UE4SS.log still works)")
    elif console_problem:
        print(f"UE4SS console: {console_problem}. To hide it, set ConsoleEnabled, GuiConsoleEnabled and "
              "GuiConsoleVisible to 0 in that file yourself.")
    print(f"Settings:     {settings.file}")
    print(f"Game:         {game_dir or 'not found (set game in the settings)'}")
    if game_dir:
        print("Game sounds:  reading the game's sound banks (the first time takes about 10 s)")
    if Handler.videos.index:
        missing = Handler.videos.missing_tools()
        if missing:
            print("Game videos:  cached clips work; automatic conversion needs " + "; ".join(missing))
        else:
            print(f"Game videos:  {len(Handler.videos.index)} found; missing clips start pre-caching now")
            Handler.videos.start_precache()
    threading.Thread(target=heartbeat_loop, args=(args.telemetry_file, port, server, args.game_gone_after),
                     daemon=True).start()
    threading.Thread(target=watch_telemetry_file, args=(args.telemetry_file,), daemon=True).start()
    threading.Thread(target=demo_loop, args=(Handler.commands_dir, Handler.sounds), daemon=True).start()
    threading.Thread(target=report_sounds, args=(Handler.sounds,), daemon=True).start()

    if port != wanted:
        print(f"Port {wanted} is taken by another program, so the companion uses port {port}. The phone's address "
              f"changes with it: if you entered the old one in Chrome's \"Insecure origins treated as secure\" flag, "
              f"enter this one there too.")
    if host == "0.0.0.0":
        print(f"On this PC:   http://127.0.0.1:{port}")
        ip = lan_ip()
        if ip:
            print(f"On the phone: http://{ip}:{port}  (same network; 127.0.0.1 on a phone is the phone itself)")
        else:
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
        Handler.videos.close()
        server.server_close()
        remove_ipc_files(args.telemetry_file)


if __name__ == "__main__":
    main()
