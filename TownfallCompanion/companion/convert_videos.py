"""Converts the game's videos for the phone: the clips the CRTV shows, and the videos on screens in
cutscenes. The companion runs it by itself at its start; by hand: py companion/convert_videos.py. A run converts only what is missing (--force
redoes everything). It takes every video under the folders in SOURCES, and the mod reports the path of the one
playing, so new videos there need no mapping; videos in another folder (e.g. a DLC's own) would need adding
to SOURCES and to the mod's path patterns (CRTV_VIDEO in tf_common.lua, tf_cutscene.lua).

The game's videos are Bink 2, and the only decoder that gets them right is RAD's own (FFmpeg with the
open Bink 2 patch repeats every third frame): RAD Video Tools' radvideo64.exe decodes each into a
temporary AVI, then FFmpeg makes a small phone copy (maximum 640x480, H.264) and adds an external soundtrack when needed. The AVI is deleted automatically. A video without sound of its own gets the
soundtrack the game plays alongside it, from BinkAudio.bank (vgmstream). The game's own MP4s are copied.
Everything goes to TownfallCompanion\\cache\\clips, which the bridge serves at /clips/: made from the
user's own game, never shipped.
"""
import argparse
import atexit
import ctypes
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

import config
from game_sounds import BANKS_IN_GAME, list_streams

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
        raise SystemExit("Missing tools. Download each and put it in its folder (vgmstream, radtools, ffmpeg) in\n"
                         f"  {config.TOOLS_DIR}\n(the PUT FILES HERE.txt in each says what goes in), then run this again, "
                         "or set its path in companion.ini:\n" + "\n".join(missing))
    return tools


def videos(movies):
    """(the game's file, its path in the clips folder) of every video the phone may show."""
    for folder, target in SOURCES.items():
        root = movies / folder
        for file in sorted(root.rglob("*")):
            if file.suffix.lower() in (".bk2", ".mp4") and file.is_file():
                yield file, target / file.relative_to(root).with_suffix(".mp4")


class _ChildProcesses:
    """Runs converter tools headlessly and ties them to this Python process.

    On Windows each active child is put in a Job Object with KILL_ON_JOB_CLOSE. If the companion window
    closes or Python exits, Windows closes the job handle and kills RAD/FFmpeg too. A normal shutdown also
    stops them explicitly. On other platforms the same wrapper still hides/captures output where possible.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._active = {}
        self._closed = False

    @staticmethod
    def _windows_job(process):
        if os.name != "nt":
            return None
        from ctypes import wintypes

        class BasicLimit(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class ExtendedLimit(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimit),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = ExtendedLimit()
        info.BasicLimitInformation.LimitFlags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
        ok = ok and kernel32.AssignProcessToJobObject(job, wintypes.HANDLE(process._handle))
        if not ok:
            kernel32.CloseHandle(job)
            return None
        return job

    @staticmethod
    def _close_job(job):
        if job and os.name == "nt":
            ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(job)

    def run(self, args, capture_output=False, text=False, errors=None, low_priority=False):
        with self._lock:
            if self._closed:
                raise Failed("conversion stopped because the companion is closing")

        startup, flags = None, 0
        if os.name == "nt":
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = 0  # SW_HIDE: RAD/FFmpeg stay fully in the background
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            if low_priority:
                flags |= getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)

        process = subprocess.Popen(
            [str(a) for a in args],
            stdin=subprocess.DEVNULL,  # not the companion's window: keys typed there asked RAD "cancel Bink 2?", and 'q' quits FFmpeg
            stdout=subprocess.PIPE if capture_output else subprocess.DEVNULL,
            stderr=subprocess.PIPE if capture_output else subprocess.DEVNULL,
            text=text,
            errors=errors,
            startupinfo=startup,
            creationflags=flags,
        )
        job = self._windows_job(process)
        with self._lock:
            if self._closed:
                if job:
                    self._close_job(job)
                elif process.poll() is None:
                    process.terminate()
                raise Failed("conversion stopped because the companion is closing")
            self._active[process.pid] = (process, job)

        try:
            stdout, stderr = process.communicate()
            return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
        finally:
            with self._lock:
                self._active.pop(process.pid, None)
            self._close_job(job)

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            active = list(self._active.values())
            self._active.clear()
        for process, job in active:
            if job:
                self._close_job(job)  # closing a KILL_ON_JOB_CLOSE job kills the child
            elif process.poll() is None:
                try:
                    process.terminate()
                except OSError:
                    pass


_children = _ChildProcesses()
atexit.register(_children.close)


def run(args, low_priority=False):
    result = _children.run(args, capture_output=True, text=True, errors="replace", low_priority=low_priority)
    if result.returncode:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise Failed(f"{Path(args[0]).name} failed (exit code {result.returncode}){': ' + detail[-1] if detail else ''}")


def has_sound(ffmpeg, video, low_priority=False):
    # Without an output FFmpeg only describes its input (and exits with an error).
    probe = _children.run([ffmpeg, "-hide_banner", "-i", video], capture_output=True, text=True, errors="replace",
                          low_priority=low_priority)
    return " Audio: " in (probe.stderr or "")


def decode_bink(radvideo, bk2, avi, low_priority=False):
    # Keep the proven converter path from main: Bink -> temporary AVI. Direct Bink -> MP4 makes RAD return
    # 8006 for Townfall clips. The child is hidden and tied to the companion process by _ChildProcesses.
    result = _children.run([radvideo, "binkconv", bk2, avi, "/o", "/#"], low_priority=low_priority)
    if result.returncode or not Path(avi).is_file():
        raise Failed(f"radvideo64.exe couldn't decode it (exit code {result.returncode})")


def decode_soundtrack(vgmstream, bank, number, out, low_priority=False):
    """Decode one video soundtrack under the same child-process lifetime/priority rules as RAD/FFmpeg."""
    result = _children.run([vgmstream, "-i", "-s", str(number), "-o", out, bank],
                           capture_output=True, low_priority=low_priority)
    return result.returncode == 0 and Path(out).is_file()


def convert(source, mp4, tools, soundtracks, bank, background=False):
    """Makes a small browser MP4 from the game's video.

    Bink 2 uses RAD's reliable Bink-to-AVI conversion internally. FFmpeg immediately turns that temporary
    AVI into the phone copy: at most 640x480 while preserving aspect ratio, H.264 CRF 28 / veryfast /
    yuv420p, AAC 96k, +faststart. The AVI disappears with the temporary directory afterward.
    """
    mp4.parent.mkdir(parents=True, exist_ok=True)
    part = mp4.with_name(mp4.stem + ".part.mp4")
    try:
        with tempfile.TemporaryDirectory(prefix="townfall-companion-") as tmp:
            video, bink = source, source.suffix.lower() == ".bk2"
            if bink:
                video = Path(tmp) / "video.avi"
                decode_bink(tools["radvideo"], source, video, low_priority=background)

            sound = None
            if source.stem in soundtracks and not has_sound(tools["ffmpeg"], video, low_priority=background):
                sound = Path(tmp) / "sound.wav"
                if not decode_soundtrack(tools["vgmstream"], bank, soundtracks[source.stem], sound,
                                         low_priority=background):
                    raise Failed("vgmstream couldn't decode its soundtrack")

            if not bink and not sound:
                shutil.copyfile(source, part)
            else:
                video_args = (["-vf", "scale=640:480:force_original_aspect_ratio=decrease:force_divisible_by=2",
                               "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p",
                               *(["-threads", "1"] if background else [])]
                              if bink else ["-c:v", "copy"])
                run([tools["ffmpeg"], "-hide_banner", "-loglevel", "error", "-y", "-i", video,
                     *(["-i", sound, "-map", "0:v", "-map", "1:a"] if sound else
                       ["-map", "0:v", "-map", "0:a?"]),
                     *video_args, "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", part],
                    low_priority=background)
        part.replace(mp4)
    finally:
        part.unlink(missing_ok=True)


class GameVideos:
    """Townfall's videos for the phone, automatically cached from the user's game.

    At companion startup, start_precache() converts all missing clips in one background
    worker. If the phone reaches a clip before that worker does, mp4() converts that clip immediately as a
    request-time fallback. Existing MP4s are always usable even if the game or tools are unavailable.
    Per-file locks and atomic renames keep concurrent requests from ever seeing a half-written MP4.
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
        self._precache_started = False
        self._precache_lock = threading.Lock()
        self._conversion_lock = threading.Lock()
        self._stopping = threading.Event()

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

    def other_video(self, relative):
        """The game's file for a path the mod reported that is in no folder of the index (a video the CRTV's screen
        plays that nobody listed, e.g. its background): the same path under the game's Movies folder, `.bk2` or
        `.mp4`. Only inside that folder: the path comes from a request."""
        if not self.movies or not self.movies.is_dir():
            return None
        root = self.movies.resolve()
        for suffix in (".bk2", ".mp4"):
            try:
                file = (self.movies / relative.with_suffix(suffix)).resolve()
                file.relative_to(root)
            except (OSError, ValueError):
                continue
            if file.is_file():
                return file
        return None

    def _soundtrack_map(self):
        if self._soundtracks is None:
            with self._soundtracks_lock:
                if self._soundtracks is None:
                    self._soundtracks = list_streams(self.tools["vgmstream"], self.bank) if self.bank.is_file() else {}
        return self._soundtracks

    def start_precache(self):
        """Once per companion run, begin filling every missing phone-video cache entry in the background."""
        with self._precache_lock:
            if self._precache_started:
                return
            self._precache_started = True

        if not self.index:
            return
        missing = self.missing_tools()
        if missing:
            self.problem = "missing " + "; ".join(missing)
            print("Game videos: automatic pre-cache can't start: " + self.problem, flush=True)
            return

        todo = [key for key in sorted(self.index) if not (self.cache_dir / Path(key)).is_file()]
        if not todo:
            print(f"Game videos: all {len(self.index)} already cached.", flush=True)
            return
        print(f"Game videos: pre-caching {len(todo)} missing clip(s) in the background ...", flush=True)
        threading.Thread(target=self._precache, args=(todo,), daemon=True, name="townfall-video-precache").start()

    def _precache(self, keys):
        ready = 0
        for number, key in enumerate(keys, 1):
            if self._stopping.is_set():
                print("Game videos: pre-cache stopped; missing clips will continue next time.", flush=True)
                return
            if self.mp4(key, background=True):
                ready += 1
            print(f"Game videos: pre-cache {number}/{len(keys)} ({ready} ready)", flush=True)
        print(f"Game videos: background pre-cache finished; {ready}/{len(keys)} new clip(s) ready.", flush=True)

    def mp4(self, relative, background=False):
        """Cached/converted MP4 for a /clips/ relative path, or None when it cannot be provided."""
        if self._stopping.is_set():
            return None
        relative = Path(str(relative).replace("\\", "/"))
        if relative.is_absolute() or ".." in relative.parts or relative.suffix.lower() != ".mp4":
            return None
        key = relative.as_posix()
        out = self.cache_dir / relative

        # Old/manual conversions remain valid, and need no tools or game files to be present.
        if out.is_file():
            return out

        source = self.index.get(key) or self.other_video(relative)
        if not source:
            return None
        missing = self.missing_tools(source)
        if missing:
            self.problem = "missing " + "; ".join(missing)
            return None

        with self._lock(out):
            if out.is_file():
                return out
            action = "pre-caching" if background else "converting on request"
            print(f"Game video: {action} {key} ...", flush=True)
            try:
                # RAD behaves like a single-user desktop converter. Never launch two conversions at once:
                # a phone request may arrive while the background pre-cache is already working.
                with self._conversion_lock:
                    if out.is_file():
                        return out
                    # RAD makes a temporary AVI; FFmpeg makes the small 640x480-max phone MP4 and adds audio.
                    convert(source, out, self.tools, self._soundtrack_map(), self.bank, background=background)
            except (Failed, OSError) as exc:
                self.problem = f"{key}: {exc}"
                print(f"Game video unavailable: {self.problem}", flush=True)
                return None
            print(f"Game video ready: {key} ({out.stat().st_size // 1024:,} KB, cached)", flush=True)
        return out

    def close(self):
        """Stop background conversion. Finished cache files stay; missing/interrupted clips resume next run."""
        self._stopping.set()
        _children.close()

    def _lock(self, path):
        with self._locks_lock:
            return self._locks.setdefault(path, threading.Lock())


def main():
    parser = argparse.ArgumentParser(description="Converts Townfall's videos for the phone (the companion runs it by itself at its start).")
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
    try:
        soundtracks = list_streams(tools["vgmstream"], bank) if bank.is_file() else {}
    except OSError as exc:
        raise SystemExit(f"{tools['vgmstream']} couldn't run: {exc}")
    if not soundtracks:
        print(f"No soundtracks in {bank}: videos without sound of their own stay silent.")
    failed = []
    for number, (source, relative) in enumerate(todo, 1):
        print(f"[{number}/{len(todo)}] {relative.as_posix()} ... ", end="", flush=True)
        try:
            mp4 = args.out / relative
            convert(source, mp4, tools, soundtracks, bank)
        except (Failed, OSError) as exc:
            failed.append(relative)
            print(f"FAILED: {exc}")
            continue
        print(f"{mp4.stat().st_size // 1024:,} KB")
    print(f"{len(every) - len(failed)} of {len(every)} videos ready for the phone.")
    if failed:
        raise SystemExit(f"{len(failed)} failed; run this again to retry them: " + ", ".join(p.as_posix() for p in failed))


if __name__ == "__main__":
    main()
