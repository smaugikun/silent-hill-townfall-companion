"""Stands in for radvideo64.exe and ffmpeg.exe in the video converter's tests: `fake_video_tools.py
radvideo ...` or `fake_video_tools.py ffmpeg ...`.

The game's "videos" are text files; one with AUDIO in it has a sound of its own, one with BROKEN in it
can't be decoded.
  radvideo binkconv <in> <out.mp4> /o /#   copies the text into the MP4
  ffmpeg -hide_banner -i <file>             describes it on stderr, " Audio: " included if it has sound
  ffmpeg ... <out>                          writes what it was asked to do as JSON: inputs, maps, video codec
"""
import json
import sys
from pathlib import Path

tool, args = sys.argv[1], sys.argv[2:]
if tool == "radvideo":
    source, mp4 = Path(args[1]), Path(args[2])
    text = source.read_text(encoding="utf-8")
    if "BROKEN" in text:
        sys.exit(3)
    mp4.write_text("mp4 of " + text, encoding="utf-8")
elif args == ["-hide_banner", "-i", args[-1]]:
    text = Path(args[-1]).read_text(encoding="utf-8", errors="replace")
    print("  Stream #0:0: Video: h264", file=sys.stderr)
    if "AUDIO" in text:
        print("  Stream #0:1: Audio: aac", file=sys.stderr)
    print("At least one output file must be specified", file=sys.stderr)
    sys.exit(1)
else:
    inputs = [args[i + 1] for i, a in enumerate(args) if a == "-i"]
    maps = [args[i + 1] for i, a in enumerate(args) if a == "-map"]
    video = args[args.index("-c:v") + 1]
    first = Path(inputs[0]).read_text(encoding="utf-8", errors="replace")
    Path(args[-1]).write_text(json.dumps({"from": first, "inputs": len(inputs), "maps": maps, "video": video}),
                              encoding="utf-8")
