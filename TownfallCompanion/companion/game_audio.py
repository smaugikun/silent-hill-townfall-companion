"""The game's sound, live: the CRTV's buses as tf_native.dll mixes them in the game's FMOD mixer (native/audio.c),
read from the shared memory it writes them to, for the phone (/api/audio/stream)."""
import ctypes as ct
import struct
import threading

MAPPING_NAME = r"Local\TownfallCompanionAudio"
HEADER = struct.Struct("<8sIIII")  # "TFAUDIO1", the rate, the channels, the ring's frames, where the ring starts
MAGIC = b"TFAUDIO1"
WRITTEN_AT = 24                    # an int64 after the header: frames written so far, raised after the frames are in
START_BEHIND_S = 0.05              # a new reader starts this far behind the newest sound
SAFE_SHARE = 0.75                  # further behind than this share of the ring, it may be overwritten while read
FILE_MAP_READ = 4


class GameAudio:
    def __init__(self, name=MAPPING_NAME):
        self.name = name
        self.handle = self.view = None
        self.lock = threading.Lock()  # one view for every phone's stream
        self.kernel = ct.WinDLL("kernel32", use_last_error=True)
        self.kernel.OpenFileMappingW.argtypes = [ct.c_uint32, ct.c_int, ct.c_wchar_p]
        self.kernel.OpenFileMappingW.restype = ct.c_void_p
        self.kernel.MapViewOfFile.argtypes = [ct.c_void_p, ct.c_uint32, ct.c_uint32, ct.c_uint32, ct.c_size_t]
        self.kernel.MapViewOfFile.restype = ct.c_void_p
        self.kernel.UnmapViewOfFile.argtypes = [ct.c_void_p]
        self.kernel.CloseHandle.argtypes = [ct.c_void_p]

    def _header(self):
        """(rate, channels, capacity, ring address) of the sound the game shares, or None while it shares none."""
        with self.lock:
            if not self.view:
                self.handle = self.kernel.OpenFileMappingW(FILE_MAP_READ, False, self.name)
                if not self.handle:
                    return None
                self.view = self.kernel.MapViewOfFile(self.handle, FILE_MAP_READ, 0, 0, 0)  # 0: all of it
                if not self.view:
                    self._close()
                    return None
            magic, rate, channels, capacity, header_size = HEADER.unpack(ct.string_at(self.view, HEADER.size))
        if magic != MAGIC or not rate or not channels or not capacity:
            return None
        return rate, channels, capacity, self.view + header_size

    def format(self):
        """(rate, channels) of the game's sound, or None while there is none."""
        header = self._header()
        return header[:2] if header else None

    def read(self, position):
        """The sound written since frame `position` (None: start just behind the newest), as 16-bit samples, and the
        frame to read from next. A reader held up too long, or a game started anew, starts again just behind."""
        header = self._header()
        if not header:
            return b"", position
        rate, channels, capacity, ring = header
        written = self._written()
        behind = int(START_BEHIND_S * rate)
        if position is None or position > written or written - position > capacity * SAFE_SHARE:
            position = max(0, written - behind)
        frames, frame_bytes = written - position, 2 * channels
        start = position % capacity
        first = min(frames, capacity - start)
        data = ct.string_at(ring + start * frame_bytes, first * frame_bytes)
        if frames > first:
            data += ct.string_at(ring, (frames - first) * frame_bytes)
        if self._written() - position > capacity:  # overwritten while it was copied
            return b"", max(0, self._written() - behind)
        return data, written

    def _written(self):
        return ct.c_int64.from_address(self.view + WRITTEN_AT).value

    def _close(self):
        if self.view:
            self.kernel.UnmapViewOfFile(self.view)
        if self.handle:
            self.kernel.CloseHandle(self.handle)
        self.view = self.handle = None

    def close(self):
        with self.lock:
            self._close()
