"""Townfall's own sounds for the phone, read from the game's FMOD banks as the phone asks for them.

Nothing is extracted beforehand. vgmstream (free and open source, github.com/vgmstream/vgmstream)
lists each bank's named streams and decodes one of them to WAV, which is kept in the cache folder,
so each sound is decoded once (about 0.2 s). Listing the two biggest banks takes about 9 s, so the
listings are kept too, until a bank changes (a game update).

What the phone can ask for (catalogue(), GET /sounds/sounds.json):
  sounds    the CRTV's own sounds (Environment and PlayerFoley banks), by name
  dialogue  each cutscene's dialogue track (Cinematics_EN.bank: <Cutscene>_71_DX)
  lines     the spoken lines (Dialogue_EN.bank), named as the game's Dialoc plays them (e.g. 10c5)
"""
import json
import re
import subprocess
import threading
import wave
from pathlib import Path

import config

BANKS_IN_GAME = config.GAME_CONTENT / "FMOD" / "Banks" / "Desktop"

CRTV_SOUNDS = ("CRTV_NoSignal_Loop", "CRTV_EnemyTunning_", "CRTV_TempSignal_", "CRTV_FineTuning_",
               "CRTV_SignalStored_Loop", "CRTV_Tuning_")
# kind -> [(bank, which of its streams)]
CATALOGUE = {
    "sounds": [("Environment", lambda name: name.startswith(CRTV_SOUNDS)),
               ("PlayerFoley", lambda name: name.startswith(("PROP_HAP_CRTV_Rise", "PROP_HAP_CRTV_Lower")))],
    "dialogue": [("Cinematics_EN", lambda name: name.endswith("_DX"))],
    "lines": [("Dialogue_EN", lambda name: True)],
}


def wav_seconds(path):
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


def list_streams(vgmstream, bank):
    """{stream name: stream number} of a bank. One call lists them all: vgmstream prints each stream's
    "stream index: N" before its "stream name: X"."""
    out = subprocess.run([str(vgmstream), "-m", "-S", "0", str(bank)], capture_output=True, text=True, errors="replace").stdout
    streams, number = {}, None
    for line in out.splitlines():
        if m := re.match(r"stream index: (\d+)", line):
            number = int(m[1])
        elif (m := re.match(r"stream name: (.+)$", line)) and number is not None:
            streams.setdefault(m[1].strip(), number)
            number = None
    return streams


def decode_stream(vgmstream, bank, number, out):
    """Writes one stream of a bank as a WAV file; whether it worked."""
    # -i: once through, without vgmstream's loop repeats and fade (the phone loops the loops)
    result = subprocess.run([str(vgmstream), "-i", "-s", str(number), "-o", str(out), str(bank)], capture_output=True)
    if result.returncode:
        Path(out).unlink(missing_ok=True)  # a half-written file must not be kept as the sound
        return False
    return Path(out).is_file()


class GameSounds:
    def __init__(self, game_dir, vgmstream, cache_dir):
        self.banks_dir = Path(game_dir) / BANKS_IN_GAME if game_dir else None
        self.vgmstream = Path(vgmstream) if vgmstream else None
        self.cache_dir = Path(cache_dir)
        self.index = {}             # kind -> {name: (bank file, stream number)}
        self.problem = None         # why there are none, once the banks were looked at
        self.ready = threading.Event()
        self._locks, self._locks_lock = {}, threading.Lock()

    def start(self):
        """Lists the banks in the background; catalogue() and wav() wait for it."""
        threading.Thread(target=self._build_index, daemon=True).start()

    def _build_index(self):
        try:
            if not (self.vgmstream and self.vgmstream.is_file()):
                self.problem = (f"missing {config.missing_tool('vgmstream', self.vgmstream)}. Unpack it into "
                                "TownfallCompanion\\tools\\vgmstream and start again, or set vgmstream in companion.ini.")
            elif not self.banks_dir:
                self.problem = config.NO_GAME
            elif not self.banks_dir.is_dir():
                self.problem = (f"the game's sound banks not found in {self.banks_dir}: set game in companion.ini to "
                                "the Townfall install folder (the one with Townfall\\Content in it).")
            else:
                for kind, banks in CATALOGUE.items():
                    self.index[kind] = {}
                    for bank_name, wanted in banks:
                        bank = self.banks_dir / f"{bank_name}.bank"
                        if bank.is_file():
                            self.index[kind].update({name: (bank, number) for name, number in self._streams(bank).items()
                                                     if wanted(name) and re.fullmatch(r"[\w\- .]+", name)})
                if not any(self.index.values()):
                    self.problem = f"no CRTV sounds found in {self.banks_dir}"
        except (OSError, ValueError) as exc:
            self.problem = f"the game's sound banks couldn't be read: {exc}"
        finally:
            self.ready.set()

    def _streams(self, bank):
        """{stream name: stream number} of a bank, from the kept listing while the bank is unchanged."""
        stat = bank.stat()
        version = f"{stat.st_size}-{int(stat.st_mtime)}"
        kept = self.cache_dir / "banks" / f"{bank.stem}.json"
        try:
            listing = json.loads(kept.read_text(encoding="utf-8"))
            if listing["version"] == version:
                return listing["streams"]
        except (OSError, ValueError, KeyError):
            pass
        streams = list_streams(self.vgmstream, bank)
        if streams:  # none means vgmstream failed (bank locked a moment, tool blocked): ask again next time
            kept.parent.mkdir(parents=True, exist_ok=True)
            kept.write_text(json.dumps({"version": version, "streams": streams}), encoding="utf-8")
        return streams

    def catalogue(self, timeout=30):
        """{"sounds": [...], "dialogue": [...], "lines": [...]}, or None (see problem)."""
        if not self.ready.wait(timeout) or self.problem:
            return None
        return {kind: sorted(names) for kind, names in self.index.items()}

    def wav(self, kind, name, timeout=30):
        """The WAV file of one sound, decoded the first time it is asked for; None if there's no such sound."""
        if not self.ready.wait(timeout) or self.problem:
            return None
        entry = self.index.get(kind, {}).get(name)
        if not entry:
            return None
        bank, number = entry
        out = self.cache_dir / kind / f"{name}.wav"
        with self._lock(out):
            if not out.is_file():
                part = out.with_name(out.stem + ".part.wav")
                try:
                    out.parent.mkdir(parents=True, exist_ok=True)
                    if not decode_stream(self.vgmstream, bank, number, part):
                        return None
                    part.replace(out)
                except OSError as exc:
                    print(f"Game sound {kind}/{name} couldn't be made: {exc}", flush=True)
                    try:
                        part.unlink(missing_ok=True)
                    except OSError:
                        pass  # held by something (antivirus); the next try starts over
                    return None
        return out

    def _lock(self, path):
        with self._locks_lock:
            return self._locks.setdefault(path, threading.Lock())
