"""Where the companion finds the game and its tools, and the settings in companion.ini.

The companion is installed inside the mod's folder (...\\Win64\\ue4ss\\Mods\\TownfallCompanion), so it
finds the game from there. What it makes from the game goes to TownfallCompanion\\cache, the tools the
user downloads to TownfallCompanion\\tools. companion.ini is written with the defaults on the first
start, so an update of the mod, which doesn't bring one, leaves the user's settings alone.
"""
import configparser
import os
import platform
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

if sys.version_info < (3, 10):
    raise SystemExit(f"Townfall Companion needs Python 3.10 or newer, and this is {platform.python_version()}. "
                     "Get it from https://www.python.org/downloads/")

VERSION = "1.0.0"  # the release's version: the bridge says it at start, the release archive is named by it

MOD_DIR = Path(__file__).resolve().parents[1]
SETTINGS_FILE = MOD_DIR / "companion.ini"
CLIPS_DIR = MOD_DIR / "cache" / "clips"    # the game's videos, converted automatically or by convert_videos.py
SOUNDS_DIR = MOD_DIR / "cache" / "sounds"  # the game's sounds, decoded as the phone asks for them
TOOLS_DIR = MOD_DIR / "tools"
GAME_CONTENT = Path("Townfall", "Content")  # inside the install folder
STEAM_APP_ID = 1636440
NO_GAME = "Townfall not found: set game in companion.ini to its install folder."
# The tools the user downloads: setting -> (program, where to get it).
TOOLS = {
    "vgmstream": ("vgmstream-cli.exe", "vgmstream, github.com/vgmstream/vgmstream/releases (vgmstream-win64.zip)"),
    "ffmpeg": ("ffmpeg.exe", "FFmpeg, ffmpeg.org/download.html (e.g. the gyan.dev essentials build)"),
    "radvideo": ("radvideo64.exe", "RAD Video Tools, radgametools.com/bnkdown.htm (RADTools.7z)"),
}

DEFAULT_SETTINGS = r"""; Townfall Companion settings, read when the companion starts. Nothing here needs changing for
; normal use. Delete this file to get the defaults back; updating the mod leaves it as it is.

[bridge]
; The port the phone connects to. If another program already uses it, the companion takes the next
; free one and shows the phone's address with it.
port = 8790
; 0.0.0.0: the phone, and anything else on your network, can connect.
; 127.0.0.1: only this PC can, so the phone can't.
listen = 0.0.0.0

[paths]
; Found by themselves: set one only if the companion says it can't find it.
; A relative path starts from this folder (TownfallCompanion).

; The Townfall install folder, the one with Townfall\Content in it,
; e.g. C:\Program Files (x86)\Steam\steamapps\common\Townfall
game =
; The tools you download are looked for anywhere in the tools folder, then on the PATH.
; vgmstream-cli.exe reads the game's sounds; automatic video conversion needs it too.
vgmstream =
; ffmpeg.exe and radvideo64.exe (RAD Video Tools): the companion converts videos with them as needed.
ffmpeg =
radvideo =
"""


@dataclass
class Settings:
    port: int
    listen: str
    game: Path        # None: find it
    vgmstream: Path   # None: find it, and so on
    ffmpeg: Path
    radvideo: Path
    file: Path


def load(path=SETTINGS_FILE):
    """The settings: the file's, over the defaults. Writes the file with the defaults first if it isn't there."""
    path = Path(path)
    if not path.exists():
        try:
            path.write_text(DEFAULT_SETTINGS, encoding="utf-8")
        except OSError:
            pass  # a folder we can't write to: the defaults it is
    parser = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=(";",))  # "port = 8791 ; mine"
    parser.read_string(DEFAULT_SETTINGS)
    try:
        parser.read(path, encoding="utf-8")
    except configparser.Error as exc:
        raise SystemExit(f"{path} can't be read: {exc}")
    port = parser.get("bridge", "port").strip()
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        raise SystemExit(f"{path}: port must be a number from 1 to 65535, not {port!r}")
    paths = {key: _path(parser.get("paths", key)) for key in ("game", "vgmstream", "ffmpeg", "radvideo")}
    return Settings(port=int(port), listen=parser.get("bridge", "listen").strip() or "0.0.0.0", file=path, **paths)


def _path(value):
    value = os.path.expandvars(value.strip().strip('"'))
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else MOD_DIR / path


def find_game_dir():
    """The Townfall install folder: the one this mod is installed in, else Steam's, else None."""
    for parent in MOD_DIR.parents:
        if (parent / GAME_CONTENT).is_dir():
            return parent
    return steam_game_dir()


def steam_game_dir():
    """The Townfall install folder (...\\steamapps\\common\\Townfall) from Steam's library list, or None."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            steam = Path(winreg.QueryValueEx(key, "SteamPath")[0])
        libraries = re.findall(r'"path"\s+"([^"]+)"',
                               (steam / "steamapps" / "libraryfolders.vdf").read_text(encoding="utf-8", errors="replace"))
    except (ImportError, OSError):
        return None
    for library in libraries:
        library = Path(library.replace("\\\\", "\\"))
        try:
            manifest = (library / "steamapps" / f"appmanifest_{STEAM_APP_ID}.acf").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        install = re.search(r'"installdir"\s+"([^"]+)"', manifest)
        if install:
            return library / "steamapps" / "common" / install[1]
    return None


def find_tool(exe):
    """A tool the user downloads: anywhere in TownfallCompanion\\tools (each comes unpacked in a folder
    of its own, some in a bin\\ inside it), else on the PATH, else None."""
    found = sorted(TOOLS_DIR.rglob(exe)) if TOOLS_DIR.is_dir() else []
    if found:
        return found[0]
    on_path = shutil.which(exe)
    return Path(on_path) if on_path else None


def tool(settings, key):
    """One of TOOLS: where the settings say, else found; None if neither."""
    return getattr(settings, key) or find_tool(TOOLS[key][0])


def missing_tool(key, path):
    """What to tell the user about a tool that isn't there."""
    exe, source = TOOLS[key]
    return f"{exe}{f' (not at {path})' if path else ''}: {source}"
