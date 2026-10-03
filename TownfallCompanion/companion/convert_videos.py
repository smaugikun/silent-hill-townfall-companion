"""Converts the game's videos for the phone: the clips the CRTV shows, and the videos on screens in
cutscenes. "Convert Game Videos.bat" runs it; once is enough. A run converts only what is missing (--force
redoes everything). It takes every video under the folders in SOURCES, and the mod reports the path of the one
playing, so new videos there need no mapping; videos in another folder (e.g. a DLC's own) would need adding
to SOURCES and to the mod's path patterns (CRTV_VIDEO in tf_common.lua, tf_cutscene.lua).

The game's videos are Bink 2, and the only decoder that gets them right is RAD's own (FFmpeg with the
open Bink 2 patch repeats every third frame): RAD Video Tools' radvideo64.exe decodes each into a
temporary uncompressed AVI, and FFmpeg encodes that to H.264. A video without sound of its own gets the
soundtrack the game plays alongside it, from BinkAudio.bank (vgmstream). The game's own MP4s are copied.
Everything goes to TownfallCompanion\\cache\\clips, which the bridge serves at /clips/: made from the
user's own game, never shipped.
"""
import argparse
import shutil
import subprocess
import tempfile
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


def decode_bink(radvideo, bk2, avi):
    # binkconv: /o overwrites, /# closes the tool when done instead of waiting for its Done button. Its
    # window opens minimized and doesn't take the focus.
    startup = None
    if hasattr(subprocess, "STARTUPINFO"):
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 7  # SW_SHOWMINNOACTIVE
    result = subprocess.run([str(radvideo), "binkconv", str(bk2), str(avi), "/o", "/#"], startupinfo=startup)
    if result.returncode or not avi.is_file():
        raise Failed(f"radvideo64.exe couldn't decode it (exit code {result.returncode})")


def convert(source, mp4, tools, soundtracks, bank):
    """Makes mp4 from the game's video, with the soundtrack the game plays alongside it if it has none."""
    mp4.parent.mkdir(parents=True, exist_ok=True)
    part = mp4.with_name(mp4.stem + ".part.mp4")
    try:
        with tempfile.TemporaryDirectory(prefix="townfall-companion-") as tmp:
            video, bink = source, source.suffix.lower() == ".bk2"
            if bink:
                video = Path(tmp) / "video.avi"  # uncompressed, ~27 MB per second of video
                decode_bink(tools["radvideo"], source, video)
            sound = None
            if source.stem in soundtracks and not has_sound(tools["ffmpeg"], video):
                sound = Path(tmp) / "sound.wav"
                if not decode_stream(tools["vgmstream"], bank, soundtracks[source.stem], sound):
                    raise Failed("vgmstream couldn't decode its soundtrack")
            if not bink and not sound:
                shutil.copyfile(source, part)
            else:
                encode = ["-c:v", "libx264", "-preset", "medium", "-crf", "23", "-pix_fmt", "yuv420p"] if bink else ["-c:v", "copy"]
                run([tools["ffmpeg"], "-hide_banner", "-loglevel", "error", "-y", "-i", video,
                     *(["-i", sound, "-map", "0:v", "-map", "1:a"] if sound else ["-map", "0:v", "-map", "0:a?"]),
                     *encode, "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", part])
        part.replace(mp4)
    finally:
        part.unlink(missing_ok=True)


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
