"""What tf_native.dll needs to know about the installed game, read from the game's own files on this PC.

The DLL calls and hooks a few of the game's engine functions and reads some of its structures (native/*.c). Where those
are changes with every game update. The game ships its PDB (its debug symbols, Townfall-Win64-Shipping.pdb) next to its
executable, and Windows' dbghelp.dll reads it (game_symbols.py): so after an update the companion looks everything up
again, once (seconds), in a process of its own at low priority (write_apart), and keeps it next to the DLL
(Scripts/tf_native.profile). The DLL takes it only for the game it runs in. Everything the DLL takes for granted is
checked here first: each function's arguments and result, and each member it reads, with its size. An update that
changed any of that gets no profile, and the DLL stays off rather than guess. Run by hand, this says why.

    py game_profile.py [path\\to\\Townfall-Win64-Shipping.exe] [--show]
    py game_profile.py --write <exe> <profile>   (write_apart's process: its outcome as a line)
"""
import mmap
import os
import struct
import subprocess
import sys
from pathlib import Path

GAME_EXE = Path("Townfall", "Binaries", "Win64", "Townfall-Win64-Shipping.exe")  # inside the install folder
PROFILE_FILE = Path(__file__).resolve().parents[1] / "Scripts" / "tf_native.profile"
MAGIC, VERSION = b"TFPROF01", 1

# The engine functions the DLL calls or hooks: key -> (name, argument count, first argument's type if that is needed to
# tell overloads apart).
FUNCTIONS = {
    "END_VIEWPORT": ("FRHICommandListImmediate::EndDrawingViewport", 3, None),
    "TRANSITION": ("FRHICommandListBase::TransitionInternal", 2, None),
    "RETAIN_TEXTURE": ("TRefCountPtr<FRHITexture>::operator=", 1, {"tag": 14, "target": "FRHITexture"}),
    "READBACK_CTOR": ("FRHIGPUTextureReadback::FRHIGPUTextureReadback", 1, None),
    "ENQUEUE_COPY": ("FRHIGPUTextureReadback::EnqueueCopy", 5, None),
    "LOCK_READBACK": ("FRHIGPUTextureReadback::Lock", 2, None),
    "UNLOCK_READBACK": ("FRHIGPUTextureReadback::Unlock", 0, None),
    "POLL_FENCE": ("FD3D12GPUFence::Poll", 1, None),
    "ANGLE_2D": ("FNoCodeTools::Get2DAngleBetweenVectors", 2, None),
    "RADAR_SPACE": ("UHandheldRadioComponent::GetRadarSpaceTransform", 0, None),
    "RADAR_UPDATE": ("UHandheldRadioComponent::UpdateRadar", 0, None),
    "CAPTURE_UPDATE": ("USceneCaptureComponent2D::UpdateSceneCaptureContents", 2, None),
}
# Where the CRTV's signals measure how far the character faces from them (look.c): the one call each of these makes to
# FNoCodeTools::Get2DAngleBetweenVectors, by its return address.
LOOK_CALLERS = {
    "LOOK_ENEMY": "URadioStaticSourceComponent::UpdateAudioComponentParameters",
    "LOOK_WAYPOINT": "URadioWaypointSourceComponent::UpdateAudioComponentParameters",
}

# Each function's result and arguments, `this` aside, as the DLL calls or hooks it (native/*.c), in the PDB's words
# (signature_text): an update that changed one gets no profile. Base types: base1 void, base6 int, base7 unsigned,
# base8 float, base10 bool, with their bytes.
SIGNATURES = {
    "GET_RESOURCE": "FTextureResource*()",
    "END_VIEWPORT": "base1:0(FRHIViewport*, base10:1, base10:1)",
    "TRANSITION": "base1:0(TArrayView<FRHITransitionInfo const ,int>, ERHITransitionCreateFlags)",
    "RETAIN_TEXTURE": "TRefCountPtr<FRHITexture>*(FRHITexture*)",
    "READBACK_CTOR": "base1:0(FName)",
    "ENQUEUE_COPY": "base1:0(FRHICommandList*, FRHITexture*, UE::Math::TIntVector3<int>*, base7:4, "
                    "UE::Math::TIntVector3<int>*)",
    "LOCK_READBACK": "base1:0*(base6:4*, base6:4*)",
    "UNLOCK_READBACK": "base1:0()",
    "POLL_FENCE": "base10:1(FRHIGPUMask)",
    "ANGLE_2D": "base8:4(UE::Math::TVector<double>*, UE::Math::TVector<double>*)",
    "RADAR_SPACE": "UE::Math::TTransform<double>()",
    "RADAR_UPDATE": "base1:0()",
    "CAPTURE_UPDATE": "base1:0(FSceneInterface*, ISceneRenderBuilder*)",
}

# The profile as the DLL reads it, in this order after the magic, the version and the size (native/profile.h is made
# from this list by native/build.py): field -> struct format.
FIELDS = [
    ("timestamp", "I"), ("image_size", "I"), ("identity", "24s"),
    ("get_resource_rva", "I"), ("get_resource_code", "16s"),
    *((f"{key.lower()}_{part}", kind) for key in FUNCTIONS for part, kind in (("rva", "I"), ("code", "16s"))),
    ("texture_rhi_offset", "I"), ("extent_offset", "I"), ("format_offset", "I"),
    ("radio_active_offset", "I"), ("radio_fine_tuning_offset", "I"), ("radio_fine_state_offset", "I"),
    ("radio_stage_offset", "I"), ("radio_image_offset", "I"), ("radio_capture_offset", "I"),
    ("component_to_world_offset", "I"),
    ("align_advanced", "I"), ("align_stabilise", "I"), ("align_rgb", "I"),
    ("look_enemy_return_rva", "I"), ("look_waypoint_return_rva", "I"),
    ("executor_rva", "I"), ("immediate_list_rva", "I"),
    ("readback_size", "I"), ("readback_fence_offset", "I"), ("readback_mask_offset", "I"),
    ("fence_pending_offset", "I"), ("tracked_access_offset", "I"),
    ("inside_pass_offset", "I"), ("inside_pass_mask", "I"), ("gpu_mask_offset", "I"), ("executing_offset", "I"),
    ("d3d12_texture_vtable_rva", "I"),
    ("access_copy_src", "I"), ("access_srv_mask", "I"), ("transition_texture", "I"),
    ("pixel_rgba8", "I"), ("pixel_bgra8", "I"),
]
LAYOUT = struct.Struct("<8sII" + "".join(kind for _, kind in FIELDS))


class Executable:
    """A PE file's headers, and its bytes by RVA, read through a memory map. OSError for a file that isn't one."""

    def __init__(self, path):
        self.file = open(path, "rb")
        try:
            if os.fstat(self.file.fileno()).st_size < 0x400:
                raise OSError(f"{path} isn't a Windows program")
            self.map = mmap.mmap(self.file.fileno(), 0, access=mmap.ACCESS_READ)
            header = struct.unpack_from("<I", self.map, 0x3C)[0]
            if self.map[:2] != b"MZ" or self.map[header:header + 4] != b"PE\0\0":
                raise OSError(f"{path} isn't a Windows program")
            sections, self.timestamp = struct.unpack_from("<HI", self.map, header + 6)
            optional_size = struct.unpack_from("<H", self.map, header + 20)[0]
            optional = header + 24
            if struct.unpack_from("<H", self.map, optional)[0] != 0x20B:
                raise OSError(f"{path} isn't a 64-bit program")
            self.image_size = struct.unpack_from("<I", self.map, optional + 56)[0]
            self.debug = struct.unpack_from("<II", self.map, optional + 112 + 6 * 8)  # the debug directory: rva, size
            table = optional + optional_size
            self.sections = [struct.unpack_from("<IIII", self.map, table + 40 * i + 8) for i in range(sections)]
        except Exception:
            self.close()
            raise

    def data(self, rva, size):
        """`size` bytes at `rva`, as loaded."""
        for virtual_size, address, raw_size, raw in self.sections:
            if address <= rva and rva + size <= address + min(virtual_size, raw_size):
                return bytes(self.map[raw + rva - address:raw + rva - address + size])
        raise OSError(f"nothing at {rva:#x} in the program's file")

    def identity(self):
        """The CodeView record that ties the program to its PDB ("RSDS", a GUID and an age): 24 bytes."""
        rva, size = self.debug
        for at in range(0, size, 28):
            kind, length, address = struct.unpack_from("<III", self.data(rva + at, 28), 12)
            if kind == 2:
                identity = self.data(address, 24)
                if length >= 24 and identity[:4] == b"RSDS":
                    return identity
        raise OSError("the program has no PDB identity")

    def close(self):
        if getattr(self, "map", None):
            self.map.close()
        self.file.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def type_text(kind):
    """A type as game_symbols describes it, as text: its name; a pointer with *; a base type as base<kind>:<bytes>."""
    if isinstance(kind, str):
        return kind
    if not isinstance(kind, dict):
        return "?"
    if kind.get("tag") == 14:
        return type_text(kind.get("target")) + "*"
    if kind.get("tag") == 16 or "baseType" in kind:
        return f"base{kind.get('baseType')}:{kind.get('length')}"
    return f"{kind.get('tag')}<{type_text(kind.get('target'))}>"


def signature_text(item):
    """A function's result and arguments as text, `this` aside: e.g. "base8:4(FVector2D*, FVector2D*)"."""
    signature = item["signature"]
    return (type_text(signature["type"].get("target")) + "("
            + ", ".join(type_text(argument.get("target")) for argument in signature["arguments"]) + ")")


class Lookup:
    """The game's symbols, with the checks every value the DLL takes needs."""

    def __init__(self, pdb, exe):
        self.pdb, self.exe, self.types = pdb, exe, {}

    def layout(self, name):
        """A struct, class or enum: (its description, {member name: [index]}), each described only when asked."""
        if name not in self.types:
            self.types[name] = self.pdb.members(name)
        return self.types[name]

    def length(self, name):
        return self.layout(name)[0]["length"]

    def child(self, layout, name):
        """The one data member (or base) `name` of `layout`, described: name, offset, bitPosition, target."""
        matches = [item for item in map(self.pdb.describe, self.layout(layout)[1].get(name, []))
                   if item["offset"] is not None]
        if len(matches) != 1:
            raise ValueError(f"{layout} has no {name}")
        return matches[0]

    def member(self, layout, name, size=None):
        """A member's offset in `layout`; `size`: the bytes the DLL reads there."""
        item = self.child(layout, name)
        if size is not None and (self.member_size(item) != size or item["bitPosition"] is not None):
            raise ValueError(f"{layout}::{name} isn't {size} bytes of its own")
        return item["offset"]

    def member_size(self, member):
        target = member["target"]
        if isinstance(target, dict):
            return 8 if target.get("tag") == 14 else target.get("length")
        return self.length(target)

    def enum_value(self, name, value):
        matches = [item for item in map(self.pdb.describe, self.layout(name)[1].get(value, [])) if "value" in item]
        if len(matches) != 1:
            raise ValueError(f"{name} has no {value}")
        return matches[0]["value"]

    def function(self, key):
        """FUNCTIONS[key]. Found by its name at once (pdb.symbol) when that is the one the DLL uses, its arguments and
        its whole signature as SIGNATURES has them; else (an overload, or the game changed it) from every symbol of that
        name, which takes seconds."""
        name, arguments, first = FUNCTIONS[key]

        def fits(item):
            listed = item.get("signature", {}).get("arguments", [])
            return item["tag"] == 5 and len(listed) == arguments and (first is None or listed[0]["target"] == first)
        quick = self.pdb.symbol(name)
        if quick and fits(quick) and signature_text(quick) == SIGNATURES[key]:
            return quick
        matches = [item for item in self.pdb.symbols(name) if item["name"] == name and fits(item)]
        if len(matches) != 1:
            raise ValueError(f"expected one {name} with {arguments} argument(s), found {len(matches)}")
        return matches[0]

    def symbol(self, name, fits):
        """The one symbol `name` that `fits` (a test of its dict): by its name at once if that one fits, or one of
        its namesakes beside it (a class's other vtables); else from every symbol of that name (seconds). None if not
        exactly one fits."""
        quick = self.pdb.symbol(name)
        if quick and fits(quick):
            return quick
        beside = [item for item in self.pdb.namesakes(name) if fits(item)]
        if len(beside) == 1:
            return beside[0]
        matches = [item for item in self.pdb.symbols(name) if item["name"] == name and fits(item)]
        return matches[0] if len(matches) == 1 else None

    def returns_from_calls(self, caller, target_rva):
        """The return addresses (RVAs) of `caller`'s direct calls (E8 rel32) to `target_rva`."""
        code = self.exe.data(caller["rva"], caller["size"])
        return [caller["rva"] + at + 5 for at in range(len(code) - 4)
                if code[at] == 0xE8 and caller["rva"] + at + 5 + int.from_bytes(code[at + 1:at + 5], "little",
                                                                                   signed=True) == target_rva]


def read(exe_path, pdb_class=None):
    """The profile's values for the game at `exe_path` (FIELDS' names), and each function's signature as text. Raises
    ValueError (or LookupError, OSError) saying what is missing or changed."""
    if pdb_class is None:
        from game_symbols import Pdb as pdb_class
    values, signatures = {}, {}
    with Executable(exe_path) as exe, pdb_class(exe_path) as pdb:
        find = Lookup(pdb, exe)
        values.update(timestamp=exe.timestamp, image_size=exe.image_size, identity=exe.identity())

        # UTexture::GetResource is there twice (its const and non-const overloads, compiled the same): either.
        resource = find.symbol("UTexture::GetResource", lambda item: item["tag"] == 5 and
                               not item["signature"]["arguments"] and signature_text(item) == SIGNATURES["GET_RESOURCE"])
        if not resource:
            resources = [item for item in pdb.symbols("UTexture::GetResource")
                         if item["tag"] == 5 and not item["signature"]["arguments"]]
            if not resources or len({item["size"] for item in resources}) != 1:
                raise ValueError("expected UTexture::GetResource overloads of one size")
            resource = resources[0]
        values.update(get_resource_rva=resource["rva"], get_resource_code=exe.data(resource["rva"], 16))
        signatures["GET_RESOURCE"] = signature_text(resource)
        if find.member("FTextureResource", "FTexture") != 0:
            raise ValueError("FTextureResource doesn't start with FTexture")
        desc = find.member("FRHITexture", "TextureDesc")
        values.update(texture_rhi_offset=find.member("FTexture", "TextureRHI", 8),
                      extent_offset=desc + find.member("FRHITextureDesc", "Extent", 8),
                      format_offset=desc + find.member("FRHITextureDesc", "Format", 1))

        functions = {key: find.function(key) for key in FUNCTIONS}
        for key, item in functions.items():
            if item["size"] < 16:
                raise ValueError(f"{FUNCTIONS[key][0]} is shorter than its check")
            values[f"{key.lower()}_rva"], values[f"{key.lower()}_code"] = item["rva"], exe.data(item["rva"], 16)
            signatures[key] = signature_text(item)

        radio = "UHandheldRadioComponent"
        values.update(radio_active_offset=find.member(radio, "bInActiveMode", 1),
                      radio_fine_tuning_offset=find.member(radio, "bFineTuning", 1),
                      radio_fine_state_offset=find.member(radio, "FineTuningState", 1),
                      radio_stage_offset=find.member(radio, "AdvancedTuningStage", 4),
                      radio_image_offset=find.member(radio, "AdvancedTuningMotionControl_Current", 16))
        # The alignment image's position: two doubles, x then y (alignment.c writes them as the stick functions do).
        current = find.child(radio, "AdvancedTuningMotionControl_Current")
        vector = "UE::Math::TVector2<double>"
        if current["target"] != vector or [find.member(vector, name, 8) for name in "XY"] != [0, 8]:
            raise ValueError("the alignment image's position isn't two doubles")
        values.update(align_advanced=find.enum_value("EFineTuningState", "AdvancedMode"),
                      align_stabilise=find.enum_value("EAdvancedTuningStage", "StabiliseRgbImage"),
                      align_rgb=find.enum_value("EAdvancedTuningStage", "AlignRgbImage"))

        # The CRTV's direction (look.c): its angle as a float, the radar's place in the world, and the radio's scene
        # capture with where a scene component keeps its place in the world.
        if functions["ANGLE_2D"]["signature"]["type"]["target"] != {"tag": 16, "baseType": 8, "length": 4}:
            raise ValueError("Get2DAngleBetweenVectors doesn't give a float")
        transform = "UE::Math::TTransform<double>"
        if functions["RADAR_SPACE"]["signature"]["type"]["target"] != transform or find.length(transform) != 96:
            raise ValueError("GetRadarSpaceTransform doesn't give a transform of doubles")
        if [find.member(transform, name) for name in ("Rotation", "Translation", "Scale3D")] != [0, 32, 64]:
            raise ValueError("a transform isn't rotation, translation and scale")
        if [find.member("UE::Math::TQuat<double>", name, 8) for name in "XYZW"] != [0, 8, 16, 24]:
            raise ValueError("a rotation isn't four doubles")
        capture = find.child(radio, "SceneCaptureComponent")
        if capture["target"] != "TObjectPtr<USceneCaptureComponent2D>" or find.length(capture["target"]) != 8:
            raise ValueError("the radio's scene capture isn't an object pointer")
        if find.member("USceneCaptureComponent2D", "USceneCaptureComponent") or \
                find.member("USceneCaptureComponent", "USceneComponent"):
            raise ValueError("a scene capture isn't a scene component at its start")
        to_world = find.child("USceneComponent", "ComponentToWorld")
        if to_world["target"] != transform:
            raise ValueError("a scene component's place isn't a transform")
        values.update(radio_capture_offset=capture["offset"], component_to_world_offset=to_world["offset"])
        angle = functions["ANGLE_2D"]["rva"]
        for key, name in LOOK_CALLERS.items():
            caller = find.symbol(name, lambda item: item["tag"] == 5 and len(find.returns_from_calls(item, angle)) == 1)
            if not caller:
                raise ValueError(f"expected {name} to call Get2DAngleBetweenVectors once")
            values[f"{key.lower()}_return_rva"] = find.returns_from_calls(caller, angle)[0]

        # The render thread's command list (capture.c).
        executor = find.symbol("GRHICommandList", lambda item: item["tag"] == 7 and item["dataType"]["name"] ==
                               "FRHICommandListExecutor" and item["size"] == find.length("FRHICommandListExecutor"))
        if not executor:
            raise ValueError("expected one GRHICommandList, an FRHICommandListExecutor")
        immediate = find.member("FRHICommandListExecutor", "CommandListImmediate")
        if immediate + find.length("FRHICommandListImmediate") > executor["size"]:
            raise ValueError("the immediate command list isn't inside GRHICommandList")
        for derived, base in (("FRHICommandListImmediate", "FRHICommandList"),
                              ("FRHICommandList", "FRHIComputeCommandList"),
                              ("FRHIComputeCommandList", "FRHICommandListBase")):
            if find.member(derived, base) != 0:
                raise ValueError(f"{derived} doesn't start with {base}")
        values.update(executor_rva=executor["rva"], immediate_list_rva=executor["rva"] + immediate)

        if find.length("TRefCountPtr<FRHITexture>") != 8 or \
                find.length("TArrayView<FRHITransitionInfo const ,int>") != 16:
            raise ValueError("a texture reference or a transition list changed size")
        if find.length("FRHITransitionInfo") != 48 or [find.member("FRHITransitionInfo", name) for name in (
                "Texture", "Type", "AccessBefore", "AccessAfter", "Flags", "CommitInfo")] != [8, 16, 20, 24, 28, 32]:
            raise ValueError("a transition's layout changed")
        if find.length("FRHIGPUTextureReadback") != 88:
            raise ValueError("a texture readback changed size")
        values.update(readback_size=88,
                      readback_fence_offset=find.member("FRHIGPUMemoryReadback", "Fence", 8),
                      readback_mask_offset=find.member("FRHIGPUMemoryReadback", "LastCopyGPUMask", 4),
                      fence_pending_offset=find.member("FRHIGPUFence", "NumPendingWriteCommands", 4),
                      tracked_access_offset=find.member("FRHIViewableResource", "TrackedAccess")
                      + find.member("FRHITrackedAccess", "Access", 4))
        persistent = find.member("FRHICommandListBase", "PersistentState")
        state = "FRHICommandListBase::FPersistentState"
        passes = [find.child(state, name) for name in ("bInsideRenderPass", "bInsideComputePass")]
        pass_offset = find.member(state, "bInsideRenderPass")
        if len(passes) != 2 or not all(item["bitPosition"] is not None and item["offset"] == pass_offset
                                       for item in passes):
            raise ValueError("a command list's render and compute pass flags aren't bits of one byte")
        values.update(inside_pass_offset=persistent + pass_offset,
                      inside_pass_mask=sum(1 << item["bitPosition"] for item in passes),
                      gpu_mask_offset=persistent + find.member(state, "CurrentGPUMask", 4),
                      executing_offset=find.member("FRHICommandListBase", "bExecuting", 1))
        # The D3D12 texture's vtables (one for each base it has): its FRHITexture one, 72 bytes.
        vtable = find.symbol("FD3D12Texture::`vftable'", lambda item: item["size"] == 72)
        if not vtable:
            raise ValueError("expected the D3D12 texture's vtable")
        values["d3d12_texture_vtable_rva"] = vtable["rva"]
        for field, kind, name in (("access_copy_src", "ERHIAccess", "CopySrc"),
                                  ("access_srv_mask", "ERHIAccess", "SRVMask"),
                                  ("transition_texture", "FRHITransitionInfo::EType", "Texture"),
                                  ("pixel_rgba8", "EPixelFormat", "PF_R8G8B8A8"),
                                  ("pixel_bgra8", "EPixelFormat", "PF_B8G8R8A8")):
            values[field] = find.enum_value(kind, name)
    return values, signatures


def pack(values):
    """The profile file's bytes."""
    return LAYOUT.pack(MAGIC, VERSION, LAYOUT.size, *(values[name] for name, _ in FIELDS))


def needed(exe_path, profile_path=PROFILE_FILE):
    """Whether the game at `exe_path` has no profile at `profile_path` yet: none made, or made for another version of
    it (its program's build stamp, size and PDB identity tell, without reading the PDB)."""
    try:
        with Executable(exe_path) as exe:
            game = exe.timestamp, exe.image_size, exe.identity()
        data = Path(profile_path).read_bytes()
        magic, version, size, *profile = struct.unpack_from("<8sIIII24s", data)
        return (magic, version, size, len(data), tuple(profile)) != (MAGIC, VERSION, LAYOUT.size, LAYOUT.size, game)
    except (OSError, ValueError, struct.error):
        return True


def write(exe_path, profile_path=PROFILE_FILE):
    """Looks the game at `exe_path` up and writes its profile, whole or not at all. Raises ValueError (or LookupError,
    OSError) saying what is missing or changed: then there is no profile, and the DLL stays off."""
    values, signatures = read(exe_path)
    changed = [FUNCTIONS[key][0] if key in FUNCTIONS else "UTexture::GetResource"
               for key, text in SIGNATURES.items() if signatures.get(key) != text]
    if changed:
        raise ValueError("changed: " + ", ".join(changed))
    staged = Path(profile_path).with_suffix(".tmp")
    staged.write_bytes(pack(values))
    os.replace(staged, profile_path)


def write_apart(exe_path, profile_path=PROFILE_FILE):
    """write() in a process of its own, at low priority: the game keeps its speed, and the memory dbghelp takes for
    the PDB (1.7 GB for a moment) is Windows' again as soon as it is done. Raises as write() does: ValueError for a
    game that changed what the DLL relies on, OSError for files that couldn't be read."""
    flags = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen([sys.executable, __file__, "--write", str(exe_path), str(profile_path)],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, creationflags=flags)
    outcome = None
    for line in process.stdout:
        word, _, rest = line.strip().partition(" ")
        if word in ("changed", "unreadable"):
            outcome = word, rest
    process.stdout.close()
    if process.wait() == 0:
        return
    if outcome and outcome[0] == "changed":
        raise ValueError(outcome[1])
    raise OSError(outcome[1] if outcome else f"its reading stopped (code {process.returncode})")


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    args = [arg for arg in sys.argv[1:] if not arg.startswith("--")]
    if "--write" in sys.argv:  # write_apart's process
        try:
            write(args[0], args[1])
        except (ValueError, LookupError) as exc:
            print(f"changed {exc}", flush=True)
            raise SystemExit(1)
        except Exception as exc:
            print(f"unreadable {exc}", flush=True)
            raise SystemExit(2)
        return
    import config
    exe = Path(args[0]) if args else config.find_game_dir() / GAME_EXE
    values, signatures = read(exe)
    if "--show" in sys.argv:
        for name, value in values.items():
            print(name, value.hex() if isinstance(value, bytes) else value)
        for key, text in signatures.items():
            print(f'    "{key}": "{text}",')
    print(f"{len(pack(values))} bytes")


if __name__ == "__main__":
    main()
