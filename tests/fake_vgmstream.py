"""Stands in for vgmstream-cli in the bridge's and the video converter's tests.

A "bank" is a text file with one stream name per line. `-m -S 0 <bank>` lists them as vgmstream does;
`-i -s N -o <out.wav> <bank>` writes stream N as a 2 s WAV and notes the name in <bank>.decoded, so a
test can count decodes.
"""
import sys
import wave

args = sys.argv[1:]
bank = args[-1]
with open(bank, encoding="utf-8") as f:
    names = [line.strip() for line in f if line.strip()]
if "-m" in args:
    for number, name in enumerate(names, 1):
        print(f"stream index: {number}")
        print(f"stream name: {name}")
else:
    number, out = int(args[args.index("-s") + 1]), args[args.index("-o") + 1]
    with open(bank + ".decoded", "a", encoding="utf-8") as log:
        log.write(names[number - 1] + "\n")
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(b"\x00\x00" * 96000)
