"""Converts the game's videos for the phone: the clips the CRTV shows, and the videos on screens in
cutscenes. "Convert Game Videos.bat" runs it; once is enough. A run converts only what is missing (--force
redoes everything). It takes every video under the folders in SOURCES, and the mod reports the path of the one
playing, so new videos there need no mapping; videos in another folder (e.g. a DLC's own) would need adding
to SOURCES and to the mod's path patterns (CRTV_VIDEO in tf_common.lua, tf_cutscene.lua).

The game's videos are Bink 2, and the only decoder that gets them right is RAD's own (FFmpeg with the
open Bink 2 patch repeats every third frame): RAD Video Tools' radvideo64.exe decodes each into a
MP4 directly, then FFmpeg makes a small phone copy (maximum 640x480, H.264) and adds an external soundtrack when needed. A video without sound of its own gets the
soundtrack the game plays alongside it, from BinkAudio.bank (vgmstream). The game's own MP4s are copied.
Everything goes to TownfallCompanion\\cache\\clips, which the bridge serves at /clips/: made from the
user's own game, never shipped.
"""
import argparse
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

import config
from game_sounds import BANKS_IN_GAME, decode_stream, list_streams

MOVIES = config.GAME_CONTENT / "Movies"
BINK_AUDIO = BANKS_IN_GAME / "BinkAudio.bank"
# Folder under Movies -> where its videos go in the clips folder (the paths the mod reports).
SOURCES = {"CRTV_Movies": Path(), "Cutscene_Diegetic_Movies": Path("Cutscene_Diegetic_Movies")}


class Failed(Exception):
    pass


def find_tools(settings):
    tools, missing = {}, []
    for key in ("radvideo", "ffmpeg", "vgmstream"):
        path = config.tool(settings, key)
        if path and path.is_file():
            tools[key] = path
        else:
            missing.append("  " + config.missing_tool(key, path))
    if missing:
        raise SystemExit("Missing tools. Download each, unpack it anywhere into\n"
                         f"  {config.TOOLS_DIR}\nand run this again (or set its path in companion.ini):\n" + "\n".join(missing))
    return tools


def videos(movies):
    """(the game's file, its path in the clips folder) of every video the phone may show."""
    for folder, target in SOURCES.items():
        root = movies / folder
        for file in sorted(root.rglob("*")):
            if file.suffix.lower() in (".bk2", ".mp4") and file.is_file():
                yield file, target / file.relative_to(root).with_suffix(".mp4")


def run(args):
    result = subprocess.run([str(a) for a in args], capture_output=True, text=True, errors="replace")
    if result.returncode:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise Failed(f"{Path(args[0]).name} failed (exit code {result.returncode}){': ' + detail[-1] if detail else ''}")


def has_sound(ffmpeg, video):
    # Without an output FFmpeg only describes its input (and exits with an error).
    probe = subprocess.run([str(ffmpeg), "-hide_banner", "-i", str(video)], capture_output=True, text=True, errors="replace")
    return " Audio: " in probe.stderr


def decode_bink(radvideo, bk2, mp4):
    # RAD's converter can write MP4 directly. /o overwrites; /# closes the tool when done instead of
    # waiting for its Done button. Its window opens minimized and doesn't take the focus.
    startup = None
    if hasattr(subprocess, "STARTUPINFO"):
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 7  # SW_SHOWMINNOACTIVE
    result = subprocess.run([str(radvideo), "binkconv", str(bk2), str(mp4), "/o", "/#"], startupinfo=startup)
    if result.returncode or not mp4.is_file():
        raise Failed(f"radvideo64.exe couldn't convert it (exit code {result.returncode})")


def convert(source, mp4, tools, soundtracks, bank, preset=None):
    """Makes a small browser MP4 from the game's video without an uncompressed intermediate.

    Bink 2 goes straight through RAD to a temporary MP4. FFmpeg then makes the phone copy: at most
    640x480 while preserving aspect ratio, H.264 CRF 28 / veryfast / yuv420p, AAC 96k, +faststart.
    The game's own MP4s are still copied unchanged when they need no separate soundtrack.
    """
    mp4.parent.mkdir(parents=True, exist_ok=True)
    part = mp4.with_name(mp4.stem + ".part.mp4")
    try:
        with tempfile.TemporaryDirectory(prefix="townfall-companion-") as tmp:
            video, bink = source, source.suffix.lower() == ".bk2"
            if bink:
                video = Path(tmp) / "video.mp4"
                decode_bink(tools["radvideo"], source, video)

            sound = None
            if source.stem in soundtracks and not has_sound(tools["ffmpeg"], video):
                sound = Path(tmp) / "sound.wav"
                if not decode_stream(tools["vgmstream"], bank, soundtracks[source.stem], sound):
                    raise Failed("vgmstream couldn't decode its soundtrack")

            if not bink and not sound:
                shutil.copyfile(source, part)
            else:
                video_args = (["-vf", "scale=640:480:force_original_aspect_ratio=decrease:force_divisible_by=2",
                               "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p"]
                              if bink else ["-c:v", "copy"])
                run([tools["ffmpeg"], "-hide_banner", "-loglevel", "error", "-y", "-i", video,
                     *(["-i", sound, "-map", "0:v", "-map", "1:a"] if sound else
                       ["-map", "0:v", "-map", "0:a?"]),
                     *video_args, "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", part])
        part.replace(mp4)
    finally:
        part.unlink(missing_ok=True)


class GameVideos:
    """Townfall's videos for the phone, converted from the user's game only when requested.

    Existing MP4s in cache_dir are always usable, even if the game or conversion tools are unavailable.
    A missing clip is looked up in the game's Movies folders, converted under a per-file lock, then atomically
    renamed into the cache. Concurrent browser range requests therefore never see a half-written MP4.
    """

    def __init__(self, game_dir, radvideo, ffmpeg, vgmstream, cache_dir):
        self.game_dir = Path(game_dir) if game_dir else None
        self.movies = self.game_dir / MOVIES if self.game_dir else None
        self.bank = self.game_dir / BINK_AUDIO if self.game_dir else None
        self.cache_dir = Path(cache_dir)
        self.tools = {
            "radvideo": Path(radvideo) if radvideo else None,
            "ffmpeg": Path(ffmpeg) if ffmpeg else None,
            "vgmstream": Path(vgmstream) if vgmstream else None,
        }
        self.index = {}
        self.problem = None
        self._soundtracks = None
        self._soundtracks_lock = threading.Lock()
        self._locks, self._locks_lock = {}, threading.Lock()

        if not self.game_dir:
            self.problem = config.NO_GAME
        elif not (self.movies / "CRTV_Movies").is_dir():
            self.problem = (f"The game's videos aren't in {self.movies}: set game in companion.ini to the Townfall "
                            "install folder (the one with Townfall\\Content in it).")
        else:
            self.index = {relative.as_posix(): source for source, relative in videos(self.movies)}

    def missing_tools(self, source=None):
        """Descriptions of tools needed to make source (all video tools when source is omitted)."""
        keys = ["ffmpeg", "vgmstream"]
        if source is None or source.suffix.lower() == ".bk2":
            keys.insert(0, "radvideo")
        return [config.missing_tool(key, self.tools[key])
                for key in keys if not (self.tools[key] and self.tools[key].is_file())]

    def _soundtrack_map(self):
        if self._soundtracks is None:
            with self._soundtracks_lock:
                if self._soundtracks is None:
                    self._soundtracks = list_streams(self.tools["vgmstream"], self.bank) if self.bank.is_file() else {}
        return self._soundtracks

    def mp4(self, relative):
        """Cached/converted MP4 for a /clips/ relative path, or None when it cannot be provided."""
        relative = Path(str(relative).replace("\\", "/"))
        if relative.is_absolute() or ".." in relative.parts or relative.suffix.lower() != ".mp4":
            return None
        key = relative.as_posix()
        out = self.cache_dir / relative

        # Old/manual conversions remain valid, and need no tools or game files to be present.
        if out.is_file():
            return out

        source = self.index.get(key)
        if not source:
            return None
        missing = self.missing_tools(source)
        if missing:
            self.problem = "missing " + "; ".join(missing)
            return None

        with self._lock(out):
            if out.is_file():
                return out
            print(f"Game video: converting {key} the first time it is needed ...", flush=True)
            try:
                # RAD writes MP4 directly; FFmpeg makes the small 640x480-max phone copy and adds audio.
                convert(source, out, self.tools, self._soundtrack_map(), self.bank)
            except Failed as exc:
                self.problem = f"{key}: {exc}"
                print(f"Game video unavailable: {self.problem}", flush=True)
                return None
            print(f"Game video ready: {key} ({out.stat().st_size // 1024:,} KB, cached)", flush=True)
        return out

    def _lock(self, path):
        with self._locks_lock:
            return self._locks.setdefault(path, threading.Lock())


def main():
    parser = argparse.ArgumentParser(description="Converts Townfall's videos for the phone (see Convert Game Videos.bat).")
    parser.add_argument("--force", action="store_true", help="convert again what was converted already")
    parser.add_argument("--settings", type=Path, default=config.SETTINGS_FILE, help="the settings file (default: %(default)s)")
    parser.add_argument("--game-dir", type=Path, help="overrides game in the settings")
    parser.add_argument("--out", type=Path, default=config.CLIPS_DIR, help="where the videos go (default: %(default)s)")
    args = parser.parse_args()
    settings = config.load(args.settings)
    game = args.game_dir or settings.game or config.find_game_dir()
    if not game:
        raise SystemExit(config.NO_GAME)
    movies = game / MOVIES
    if not (movies / "CRTV_Movies").is_dir():
        raise SystemExit(f"The game's videos aren't in {movies}: set game in companion.ini to the Townfall install "
                         "folder (the one with Townfall\\Content in it).")
    every = list(videos(movies))
    print(f"Game:   {game}\nVideos: {len(every)}, into {args.out}")
    todo = [(source, relative) for source, relative in every if args.force or not (args.out / relative).is_file()]
    if not todo:
        print(f"All {len(every)} videos are ready for the phone.")
        return
    tools = find_tools(settings)  # only needed when there is something to convert

    bank = game / BINK_AUDIO
    soundtracks = list_streams(tools["vgmstream"], bank) if bank.is_file() else {}
    if not soundtracks:
        print(f"No soundtracks in {bank}: videos without sound of their own stay silent.")
    failed = []
    for number, (source, relative) in enumerate(todo, 1):
        print(f"[{number}/{len(todo)}] {relative.as_posix()} ... ", end="", flush=True)
        try:
            mp4 = args.out / relative
            convert(source, mp4, tools, soundtracks, bank)
        except Failed as exc:
            failed.append(relative)
            print(f"FAILED: {exc}")
            continue
        print(f"{mp4.stat().st_size // 1024:,} KB")
    print(f"{len(every) - len(failed)} of {len(every)} videos ready for the phone.")
    if failed:
        raise SystemExit(f"{len(failed)} failed; run this again to retry them: " + ", ".join(p.as_posix() for p in failed))


if __name__ == "__main__":
    main()
