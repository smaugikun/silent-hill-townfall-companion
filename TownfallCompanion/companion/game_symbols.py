"""Reads the game's own PDB (shipped next to Townfall-Win64-Shipping.exe) with Windows' dbghelp.dll: function
addresses with their signatures, data symbols, and struct and enum layouts. Offline: the game isn't attached to
or changed."""
import ctypes as ct
import os
from pathlib import Path


class SymbolInfo(ct.Structure):
    _fields_ = [
        ("SizeOfStruct", ct.c_uint32), ("TypeIndex", ct.c_uint32),
        ("Reserved", ct.c_uint64 * 2), ("Index", ct.c_uint32), ("Size", ct.c_uint32),
        ("ModBase", ct.c_uint64), ("Flags", ct.c_uint32), ("Value", ct.c_uint64),
        ("Address", ct.c_uint64), ("Register", ct.c_uint32), ("Scope", ct.c_uint32),
        ("Tag", ct.c_uint32), ("NameLen", ct.c_uint32), ("MaxNameLen", ct.c_uint32),
        ("Name", ct.c_char * 1),
    ]


# SymTagEnum values used here, and the IMAGEHLP_SYMBOL_TYPE_INFO queries.
TAG_FUNCTION, TAG_DATA = 5, 7
TI_GET_SYMTAG, TI_GET_SYMNAME, TI_GET_LENGTH, TI_GET_TYPEID, TI_GET_BASETYPE = 0, 1, 2, 4, 5
TI_FINDCHILDREN, TI_GET_OFFSET, TI_GET_VALUE, TI_GET_CHILDRENCOUNT, TI_GET_BITPOSITION = 7, 10, 11, 13, 14
_CALLBACK = ct.WINFUNCTYPE(ct.c_int, ct.POINTER(SymbolInfo), ct.c_uint32, ct.c_void_p)


class Pdb:
    """The symbols of `exe`, from its exactly matching PDB. Use as a context manager."""

    def __init__(self, exe):
        self.exe = Path(exe).resolve(strict=True)
        system = Path(os.environ["WINDIR"]) / "System32"
        self.kernel = ct.WinDLL(str(system / "kernel32.dll"), use_last_error=True)
        self.dbg = dbg = ct.WinDLL(str(system / "dbghelp.dll"), use_last_error=True)
        self.kernel.GetCurrentProcess.restype = ct.c_void_p
        self.kernel.LocalFree.argtypes = [ct.c_void_p]
        self.kernel.LocalFree.restype = ct.c_void_p
        dbg.SymSetOptions.argtypes = [ct.c_uint32]
        dbg.SymSetOptions.restype = ct.c_uint32
        dbg.SymInitializeW.argtypes = [ct.c_void_p, ct.c_wchar_p, ct.c_int]
        dbg.SymInitializeW.restype = ct.c_int
        dbg.SymLoadModuleExW.argtypes = [ct.c_void_p, ct.c_void_p, ct.c_wchar_p, ct.c_wchar_p,
                                         ct.c_uint64, ct.c_uint32, ct.c_void_p, ct.c_uint32]
        dbg.SymLoadModuleExW.restype = ct.c_uint64
        dbg.SymCleanup.argtypes = [ct.c_void_p]
        dbg.SymCleanup.restype = ct.c_int
        dbg.SymEnumSymbols.argtypes = [ct.c_void_p, ct.c_uint64, ct.c_char_p, _CALLBACK, ct.c_void_p]
        dbg.SymEnumSymbols.restype = ct.c_int
        dbg.SymGetTypeInfo.argtypes = [ct.c_void_p, ct.c_uint64, ct.c_uint32, ct.c_int, ct.c_void_p]
        dbg.SymGetTypeInfo.restype = ct.c_int
        dbg.SymGetTypeFromName.argtypes = [ct.c_void_p, ct.c_uint64, ct.c_char_p, ct.POINTER(SymbolInfo)]
        dbg.SymGetTypeFromName.restype = ct.c_int
        dbg.SymFromName.argtypes = [ct.c_void_p, ct.c_char_p, ct.POINTER(SymbolInfo)]
        dbg.SymFromName.restype = ct.c_int
        for step in (dbg.SymNext, dbg.SymPrev):
            step.argtypes = [ct.c_void_p, ct.POINTER(SymbolInfo)]
            step.restype = ct.c_int
        self.process = self.kernel.GetCurrentProcess()
        # Exact symbols only (SYMOPT_EXACT_SYMBOLS), no dialogs, no symbol server: the PDB next to the exe.
        dbg.SymSetOptions(0x2 | 0x200 | 0x400 | 0x1000 | 0x80000)
        if not dbg.SymInitializeW(self.process, str(self.exe.parent), False):
            raise ct.WinError(ct.get_last_error())
        self.base = dbg.SymLoadModuleExW(self.process, None, str(self.exe), None, 0, 0, None, 0)
        if not self.base:
            error = ct.get_last_error()
            dbg.SymCleanup(self.process)
            raise ct.WinError(error)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.dbg.SymCleanup(self.process)

    def _query(self, index, kind, ctype=ct.c_uint32):
        result = ctype()
        return result.value if self.dbg.SymGetTypeInfo(self.process, self.base, index, kind, ct.byref(result)) else None

    def _name(self, index):
        pointer = ct.c_void_p()
        if not self.dbg.SymGetTypeInfo(self.process, self.base, index, TI_GET_SYMNAME, ct.byref(pointer)) or \
                not pointer.value:
            return None
        try:
            return ct.wstring_at(pointer.value)
        finally:
            self.kernel.LocalFree(pointer)

    def _type_name(self, index, depth=0):
        name = self._name(index)
        if name is not None:
            return name
        tag, target = self._query(index, TI_GET_SYMTAG), self._query(index, TI_GET_TYPEID)
        if depth < 5 and target is not None and target != index:
            return {"tag": tag, "target": self._type_name(target, depth + 1)}
        return {"tag": tag, "baseType": self._query(index, TI_GET_BASETYPE),
                "length": self._query(index, TI_GET_LENGTH, ct.c_uint64)}

    def _children(self, index):
        count = self._query(index, TI_GET_CHILDRENCOUNT) or 0
        if not count:
            return []
        if count > 4096:
            raise ValueError("unexpectedly large type child list")
        params = (ct.c_uint32 * (count + 2))()
        params[0], params[1] = count, 0
        if not self.dbg.SymGetTypeInfo(self.process, self.base, index, TI_FINDCHILDREN, ct.byref(params)):
            raise ct.WinError(ct.get_last_error())
        return list(params)[2:]

    def _describe(self, index):
        target = self._query(index, TI_GET_TYPEID)
        result = {"name": self._type_name(index), "tag": self._query(index, TI_GET_SYMTAG),
                  "length": self._query(index, TI_GET_LENGTH, ct.c_uint64), "offset": self._query(index, TI_GET_OFFSET),
                  "bitPosition": self._query(index, TI_GET_BITPOSITION),
                  "target": self._type_name(target) if target is not None else None}
        variant = ct.create_string_buffer(24)
        if self.dbg.SymGetTypeInfo(self.process, self.base, index, TI_GET_VALUE, ct.byref(variant)):
            kind = int.from_bytes(variant.raw[:2], "little")  # a VARIANT: its integer types
            integer_sizes = {2: (2, True), 3: (4, True), 16: (1, True), 17: (1, False),
                             18: (2, False), 19: (4, False), 20: (8, True), 21: (8, False)}
            if kind in integer_sizes:
                size, signed = integer_sizes[kind]
                result["value"] = int.from_bytes(variant.raw[8:8 + size], "little", signed=signed)
        return result

    def symbols(self, mask):
        """Every symbol matching `mask` (dbghelp's * and ? wildcards): {name, rva, size, tag}, with "signature"
        ({type, arguments}) for a function and "dataType" for data."""
        matches = []

        @_CALLBACK
        def collect(pointer, size, context):
            info = pointer.contents
            name = ct.string_at(ct.addressof(info) + SymbolInfo.Name.offset, info.NameLen).decode("utf-8", "replace")
            matches.append({"name": name, "rva": info.Address - self.base, "size": info.Size,
                            "typeIndex": info.TypeIndex, "tag": info.Tag})
            return True

        if not self.dbg.SymEnumSymbols(self.process, self.base, mask.encode("ascii"), collect, None):
            raise ct.WinError(ct.get_last_error())
        return [self._typed(symbol) for symbol in matches]

    def symbol(self, name):
        """One symbol named exactly `name`, as symbols() gives it, or None: in milliseconds, where symbols() reads
        through every symbol of the PDB (seconds). Of overloads, any one."""
        buffer = ct.create_string_buffer(ct.sizeof(SymbolInfo) + 2048)
        info = ct.cast(buffer, ct.POINTER(SymbolInfo))
        info.contents.SizeOfStruct, info.contents.MaxNameLen = ct.sizeof(SymbolInfo), 2048
        if not self.dbg.SymFromName(self.process, name.encode("ascii"), info):
            return None
        found = info.contents
        return self._typed({"name": name, "rva": found.Address - self.base, "size": found.Size,
                            "typeIndex": found.TypeIndex, "tag": found.Tag})

    def namesakes(self, name, count=16):
        """The symbols named `name` among the `count` each way beside (by address) the one symbol() gives, that one
        too, as symbols() gives them: several symbols of one name (a class's vtables, one for each of its bases) lie
        side by side. About a second the first time (dbghelp orders the symbols by address), then milliseconds."""
        found, wanted = [], name.encode("utf-8")
        for step in (self.dbg.SymNext, self.dbg.SymPrev):
            buffer = ct.create_string_buffer(ct.sizeof(SymbolInfo) + 2048)
            info = ct.cast(buffer, ct.POINTER(SymbolInfo))
            info.contents.SizeOfStruct, info.contents.MaxNameLen = ct.sizeof(SymbolInfo), 2048
            if not self.dbg.SymFromName(self.process, name.encode("ascii"), info):
                return []
            for taken in range(count + 1):
                if taken and not step(self.process, info):
                    break
                symbol = info.contents
                if (taken or step == self.dbg.SymNext) and \
                        ct.string_at(ct.addressof(symbol) + SymbolInfo.Name.offset, symbol.NameLen) == wanted:
                    found.append({"name": name, "rva": symbol.Address - self.base, "size": symbol.Size,
                                  "typeIndex": symbol.TypeIndex, "tag": symbol.Tag})
        return [self._typed(symbol) for symbol in found]

    def _typed(self, symbol):
        """`symbol` with its signature (a function) or its type (data), from its type index."""
        index = symbol.pop("typeIndex")
        if symbol["tag"] == TAG_FUNCTION and index:
            symbol["signature"] = {"type": self._describe(index),
                                   "arguments": [self._describe(child) for child in self._children(index)]}
        elif symbol["tag"] == TAG_DATA and index:
            symbol["dataType"] = self._describe(index)
        return symbol

    def members(self, name):
        """A struct, class or enum by name, the quick way: its own description, and {member name: [index]} (methods
        can share a name), each described by describe(index) only when asked: an engine class has hundreds."""
        buffer = ct.create_string_buffer(ct.sizeof(SymbolInfo) + 2048)
        info = ct.cast(buffer, ct.POINTER(SymbolInfo))
        info.contents.SizeOfStruct, info.contents.MaxNameLen = ct.sizeof(SymbolInfo), 2048
        if not self.dbg.SymGetTypeFromName(self.process, self.base, name.encode("ascii"), info):
            raise LookupError(f"type not in the PDB: {name}")
        index = info.contents.TypeIndex
        names = {}
        for child in self._children(index):
            names.setdefault(self._name(child), []).append(child)
        return self._describe(index), names

    def describe(self, index):
        """A member's (or an enum value's) details, from members(): name, offset, bitPosition, target, value."""
        return self._describe(index)
