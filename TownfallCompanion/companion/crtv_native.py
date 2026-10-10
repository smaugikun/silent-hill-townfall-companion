"""The CRTV screen's picture as tf_native.dll publishes it (native/picture.c): a JPEG of the screen, cut out of the
game's texture and scaled to the width the phone wants, in shared memory. The companion passes it on as it is."""
import ctypes as ct
import json
import struct
import threading
import time
from pathlib import Path

MAPPING_NAME = r"Local\TownfallCompanionPicture"
HEADER = struct.Struct("<8s4I2Q")  # "TFJPEG01", width, height, the JPEG's bytes, 0, the picture's number, Unix ms
HEADER_AT, PICTURE_AT = 8, 64      # after the counter (odd while the DLL writes), and where the JPEG starts
MAX_BYTES = 1 << 20
MAPPING_SIZE = PICTURE_AT + MAX_BYTES
STALE_AFTER = 3.0


def parse_header(data):
    """The picture's {width, height, sequence, unixMs} and its JPEG's length; ValueError if it isn't one."""
    magic, width, height, length, reserved, sequence, unix_ms = HEADER.unpack_from(data)
    if (magic != b"TFJPEG01" or reserved or not 0 < width <= 640 or not 0 < height <= 2048
            or not 0 < length <= MAX_BYTES or not sequence or not unix_ms):
        raise ValueError("not a picture of the CRTV's screen")
    return {"width": width, "height": height, "sequence": sequence, "unixMs": unix_ms}, length


class WindowsFrames:
    def __init__(self, name=MAPPING_NAME):
        self.name = name
        self.handle = self.view = None
        self.kernel = ct.WinDLL("kernel32", use_last_error=True)
        self.kernel.OpenFileMappingW.argtypes = [ct.c_uint32, ct.c_int, ct.c_wchar_p]
        self.kernel.OpenFileMappingW.restype = ct.c_void_p
        self.kernel.MapViewOfFile.argtypes = [ct.c_void_p, ct.c_uint32, ct.c_uint32, ct.c_uint32, ct.c_size_t]
        self.kernel.MapViewOfFile.restype = ct.c_void_p
        self.kernel.UnmapViewOfFile.argtypes = [ct.c_void_p]
        self.kernel.UnmapViewOfFile.restype = ct.c_int
        self.kernel.CloseHandle.argtypes = [ct.c_void_p]
        self.kernel.CloseHandle.restype = ct.c_int

    def snapshot(self, cached_marker=None):
        """(marker, meta, JPEG) of the picture there now, the JPEG None if it is the one `cached_marker` names; None
        while there is none, or it is being written."""
        if not self.view:
            self.handle = self.kernel.OpenFileMappingW(4, False, self.name)
            if not self.handle:
                return None
            self.view = self.kernel.MapViewOfFile(self.handle, 4, 0, 0, MAPPING_SIZE)
            if not self.view:
                self.close()
                return None
        for _ in range(2):
            before = ct.c_uint32.from_address(self.view).value
            if not before or before & 1:
                return None
            meta, length = parse_header(ct.string_at(self.view + HEADER_AT, HEADER.size))
            key = before, meta["unixMs"], meta["sequence"]
            jpeg = None if key == cached_marker else ct.string_at(self.view + PICTURE_AT, length)
            after = ct.c_uint32.from_address(self.view).value
            if before == after and not after & 1:
                return key, meta, jpeg
        return None

    def close(self):
        if self.view:
            self.kernel.UnmapViewOfFile(self.view)
        if self.handle:
            self.kernel.CloseHandle(self.handle)
        self.view = self.handle = None


class NativeFrames:
    content_type = "image/jpeg"

    def __init__(self, directory, mapping=None, clock=time.time):
        self.directory = Path(directory)
        self.clock = clock
        self.mapping = mapping if mapping is not None else WindowsFrames()
        self.lock = threading.Lock()
        self.marker = self.cached = None
        self.cached_at = 0

    def latest(self):
        """(meta, JPEG, (width, height)) of the newest picture, the same object while it stays the newest; None while
        there is no fresh one."""
        with self.lock:
            try:
                snapshot = self.mapping.snapshot(self.marker)
                if snapshot is None:
                    return self.cached if self.cached and self.clock() - self.cached_at <= 0.2 else None
                marker, meta, jpeg = snapshot
                if not -1 <= self.clock() - meta["unixMs"] / 1000 <= STALE_AFTER:
                    return None
                if jpeg is None:
                    return self.cached
                self.cached = ({"status": "live", "kind": "native-rhi", "seq": f"{meta['unixMs']}:{meta['sequence']}"},
                               jpeg, (meta["width"], meta["height"]))
                self.cached_at = self.clock()
                self.marker = marker
                return self.cached
            except (OSError, ValueError, KeyError, TypeError):
                return None

    def status(self):
        path = self.directory / "townfall-companion-native.json"  # tf_native.dll's state
        try:
            if not -1 <= self.clock() - path.stat().st_mtime <= STALE_AFTER:
                return {"status": "stale", "kind": "native-rhi"}
            data = json.loads(path.read_text())
            if not isinstance(data, dict):
                return {"status": "invalid"}
            return {"kind": "native-rhi", **data}
        except (OSError, ValueError):
            return {"status": "waiting-for-game", "kind": "native-rhi"}

    def close(self):
        with self.lock:
            self.mapping.close()
