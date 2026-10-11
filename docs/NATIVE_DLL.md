# tf_native.dll

The native part of Townfall Companion: a small Windows DLL that runs inside the game and gets out what Lua can't
reach: the CRTV's picture (a GPU texture), the CRTV's sound (inside the game's FMOD mixer), and a few game
functions the phone steers. This page is the map of it: what it does, how the pieces talk, what can go wrong, and
how to build and test it.

Source: `native/`. Shipped as `TownfallCompanion/Scripts/tf_native.dll` (built, never committed: it is in
`.gitignore` and goes into the release zip via `tools/package.py`).

## What it doesn't do

- **No network.** It uses none of Windows' networking functions: its import table lists only `KERNEL32`, `ole32`
  (for the JPEG encoder) and the C runtime. The phone's page comes from the companion, not from the DLL.
- **No additional program.** The mod's Lua script loads it into the game's process. It uses Windows system
  components, including WIC through COM for JPEG encoding, which can load system DLLs. FMOD's functions come from
  the game's own FMOD libraries, already loaded.
- **No game files are changed.** The DLL writes its status and sound-bus list in `%TEMP%`, and picture and audio
  data in two shared-memory blocks. Lua writes the command packets in `%TEMP%`; the companion separately writes
  the mod's profile and settings.
- **In the running game, and only while it runs,** it changes: the first bytes of the functions it hooks (MinHook's
  jumps), the position of the picture during fine tuning when the phone moves it, the CRTV camera's aim for the
  moment the game draws it, and the CRTV's sound in the game while the phone plays it.

## What it does

| Job | File | How |
|---|---|---|
| Starts up, checks the game, reports its state | `tf_native.c` | loads the game profile, checks it fits the running game, installs the hooks, writes a status file |
| Copies the CRTV's screen from the GPU | `capture.c` | hooks the end of every frame on the render thread and queues one GPU copy of the CRTV's texture at a time |
| Makes the phone's picture | `picture.c` | crops the screen out of the texture, scales it, encodes JPEG with Windows' WIC on its own thread, puts it in shared memory |
| Takes the CRTV's sound | `audio.c` | hooks FMOD's per-frame update, puts its own DSP on the CRTV's buses and the master bus, mixes the phone's sound into a ring in shared memory, can silence those buses in the game |
| Looks around with the phone | `look.c` | turns what the CRTV "faces" (signal strength, radar, the CRTV's camera) by the phone's angle, without turning the character |
| Moves the picture during fine tuning | `alignment.c` | writes the image position in fine tuning's image stages directly, clamped like the game does |

All of these are `#include`d into `tf_native.c`: one translation unit, one DLL. `profile.h` is generated (below);
`vendor/minhook` is MinHook (BSD 2-Clause), compiled in.

## How it is loaded and called

UE4SS's Lua can load a DLL (`package.loadlib`) but gives it no Lua C API. So the DLL's exports are called as plain
functions, and every value travels in a small binary file in `%TEMP%` that Lua writes right before the call:

| Export | Called by | Reads | Does |
|---|---|---|---|
| `tf_native_init` | `tf_native.lua`, once | — | pins itself in memory (a Lua reload must not unload hooked code), starts the sound tap, loads the profile, installs the hooks |
| `tf_native_update` | `tf_native.lua`, every 0.5 s, game thread | `townfall-companion-native-source.bin` | processes the source and reports status at most once a second; takes the CRTV texture, fps, width and radio state; retries the profile every 5 s |
| `tf_native_align` | `tf_alignment.lua` | `townfall-companion-native-align.bin` | moves the fine tuning picture once |
| `tf_native_look` | `tf_native.lua` | `townfall-companion-native-look.bin` | takes where the phone looks |
| `tf_native_sound` | `tf_native.lua` | `townfall-companion-native-sound.bin` | silences (6 s, renewed) or restores the CRTV's sound in the game |
| `tf_native_state`, `tf_native_validate` | the tests only | — | the state; a profile checked against a game image |

The packet files stay open on both sides and are rewritten in place. Reusing handles avoids repeated opens, which
were measured taking milliseconds on the game thread with the local virus scanner. File I/O can still wait. Each packet starts with an
8-byte magic and has a fixed size (checked with `_Static_assert`):

| Packet | Magic | Size | Content |
|---|---|---|---|
| source | `TFNATV03` | 40 | texture (UTexture*), radio (UHandheldRadioComponent*), fps (0..30), width (320/480/640), Unix time (must be within 4 s) |
| align | `TFALIGN1` | 40 | radio, x and y to move (the image spans -1..1), the command's time (ms; a repeat is ignored) |
| look | `TFLOOK04` | 40 | yaw (±180°, from the character's facing), pitch (±90°), looking (0/1), the command's time (ms) |
| sound | `TFSOUND1` | 16 | mute (0/1) |

## What it hands to the companion

Two named shared-memory blocks carry picture and audio data without intermediate media files. The native code
copies data into them, and Python copies the JPEG and samples into its own objects for streaming:

**`Local\TownfallCompanionPicture`** (`crtv_native.py`): the latest picture.

| Offset | What |
|---|---|
| 0 | a counter: odd while a picture is being written, even when whole (the reader retries on odd or a change) |
| 8 | header `TFJPEG01`: width, height, bytes, sequence, Unix ms (40 bytes) |
| 64 | the JPEG (at most 1 MB; a 640-wide picture is about 50 KB) |

**`Local\TownfallCompanionAudio`** (`game_audio.py`): the phone's sound.

| Offset | What |
|---|---|
| 0 | header `TFAUDIO1`: rate, channels (2), capacity (65536 frames, 1.4 s at 48 kHz), header size (64) |
| 24 | `written`: frames written since the DLL started, raised after the frames it counts are in |
| 64 | the ring: 16-bit stereo |

The companion streams the picture at `/api/crtv/stream` and the sound at `/api/audio/stream` ("TFAU" + rate +
channels + raw 16-bit samples).

**Status:** `%TEMP%\townfall-companion-native.json`, rewritten in place (padded to 2048 bytes), once a second:
the state, the capture's state and why it waits, frames published, timings, the look and `lookStatus`, the sound's state, rate,
buses and levels, and the radio's fine tuning state. `tf_native.lua` logs it as `[TF-NATIVE]` / `[TF-AUDIO]` lines
when it changes, and the companion serves it at `/api/crtv/status`. `%TEMP%\townfall-companion-native-buses.txt`
lists the game's FMOD buses, the tapped ones marked.

## The game profile: surviving game updates

Engine addresses, member offsets and enum values can change between game builds. The production DLL obtains these
values from a game profile instead of fixed addresses. It still relies on specific signatures, structure layouts,
rendering behavior and the CRTV crop described below:

1. The game ships its debug symbols, `Townfall-Win64-Shipping.pdb`, next to its executable.
2. At start, the companion (`game_profile.py`, through Windows' `dbghelp.dll` in `game_symbols.py`) checks whether
   `Scripts\tf_native.profile` fits the installed game. If not (a game update), it reads the PDB once: about 4.5 s,
   in a separate low-priority process.
3. It checks the required function signatures (`SIGNATURES`) and selected structure layout and size assumptions,
   and looks up the required member offsets and enum values. Compatible offset and enum-value changes go into the
   new profile. If the symbols are missing or a checked assumption is incompatible, no new profile is written and the companion
   says *The CRTV's picture can't be streamed to the phone*. `py TownfallCompanion/companion/game_profile.py --show`
   says what changed.
4. The DLL loads the profile (`TFPROF01`, version 1, 436 bytes) and checks it against the running game: build
   timestamp, image size, the PDB identity (CodeView GUID and age), and the first 16 bytes of every function it
   will call. A mismatch leaves picture capture, looking and alignment off; audio is independent of the profile.
5. Until a fitting profile is there, the DLL retries every 5 seconds, so a profile written mid-game is picked up.

The sound needs no profile: FMOD's functions are found by name in the game's `fmod.dll` / `fmodstudio.dll`.

`profile.h` is generated by `native/build.py` from `game_profile.FIELDS`: the C struct the profile is read into,
a macro per value (`END_VIEWPORT_RVA` → `profile.end_viewport_rva`), and the list of functions whose first bytes
are checked. Change `FIELDS`, and bump `game_profile.VERSION`.

**When a game update needs a mod update:** when required symbols disappear, a signature or checked layout becomes
incompatible, or behavior the profile cannot describe changes (for example the CRTV crop or audio-bus paths).
Compatible address, offset and enum-value changes can be handled by generating a new profile. For incompatible
changes, adjust the relevant code and `SIGNATURES`/`FIELDS`, rebuild and test.

## The hooks

| Hooked function | Thread | Why |
|---|---|---|
| `FRHICommandListImmediate::EndDrawingViewport` | render | the end of a frame: the CRTV's texture is finished, the copy is queued here |
| `FMOD::Studio::System::update` | game | FMOD's own frame: lists the buses and attaches the DSPs, a step at a time |
| `FNoCodeTools::Get2DAngleBetweenVectors` | game | signal strength by angle; only the two CRTV call sites (told apart by return address) get the phone's angle |
| `UHandheldRadioComponent::GetRadarSpaceTransform` | game | the radar, turned by the phone's angle |
| `UHandheldRadioComponent::UpdateRadar` | game | the arrow towards the monster, measured from the turned capture |
| the scene capture's update | game | the CRTV's camera, aimed where the phone looks, then put back |

All hooks pass everything else through unchanged. Without the profile only the FMOD hook exists.
If a look hook cannot be installed, the DLL attempts to remove all the look hooks it created. Picture capture can
continue, but `look` is false and `lookStatus` reports `hook-install-failed`. If MinHook also fails to remove a hook,
`lookStatus` reports `hook-cleanup-failed`; phone looking stays disabled and the DLL remains pinned.

## Safety rules in the code

- The DLL reads and writes memory in its own process. Selected reads and writes, including the CRTV camera rotation,
  check page accessibility with `VirtualQuery` first (`read_memory`, `write_memory`) and reject inaccessible pages.
  This does not establish Unreal object lifetime or type, prevent memory changing after the check, or guarantee
  that native code cannot crash. See [VirtualQuery](https://learn.microsoft.com/en-us/windows/win32/api/memoryapi/nf-memoryapi-virtualquery).
- The GPU copy is only added to the immediate command list, never on the game thread, never inside a render pass,
  never on a list that is executing, never with more than one GPU. Each case has a wait reason in the status.
- A texture in an unknown resource state is refused (`source-state-unverified`), not guessed.
- One copy in flight. An unfinished copy is never freed; after 5 s without the GPU finishing it, capture stops
  (`gpu-timeout`) rather than risk it.
- Capture is paced at the phone's requested fps (≤ 30), with intervals rounded up to whole milliseconds. It slows
  down when measured GPU-queue and readback CPU time would exceed a tenth of the interval. This budget excludes
  JPEG encoding on the picture thread. A queue or readback step over 50 ms stops capture (`capture-too-slow`).
- Only RGBA8/BGRA8 textures up to 2048×2048.
- FMOD DSPs pass blocks on unchanged until all taps and the master publication DSP are attached. Partial or failed
  setup cannot mute the game. Once ready, muting writes silence only into buses selected for the phone's CRTV audio,
  including waypoint dialogue and transmissions. Existing taps remain attached in pass-through mode if setup fails.

## States and what they mean

The DLL's `state`: `1` picture capture and alignment initialized with a fitting profile; `0` waiting for one
(sound is independent); below 0 initialization failed: `-1` the game isn't there, `-3` couldn't pin itself,
`-4` MinHook or the viewport hook couldn't be initialized, `-5` no usable temp or DLL path. Audio already installed
before a viewport-hook failure can remain attached; new mute commands are refused. Looking has its own `look`
boolean and `lookStatus`: `waiting-for-game-profile`, `ready`, `hook-install-failed`, or `hook-cleanup-failed`.

Status strings (`[TF-NATIVE]` lines):

| Status | Meaning |
|---|---|
| `waiting-for-game-profile` | no profile yet (after an update, until the companion has read the game) |
| `game-profile-for-another-version` | a profile is there, but for another game build |
| `waiting-for-crtv-source` | running; no CRTV texture from Lua (CRTV down, no phone) |
| `invalid-source`, `rhi-source-not-ready` | Lua named a texture that isn't (yet) a live GPU texture |
| `rhi-source-identified` | a texture resource was read; check `captureStatus`, `captureWait` and fresh frames to confirm streaming |
| `hook-install-failed`, `game-not-present` | off |

`streaming` requires a fresh capture request, a picture published within the last 3 seconds, and no current wait
reason. It describes recent output, not a guarantee that the next GPU copy will finish.
Other capture states: `disabled`, `armed`, `waiting-for-source`, `gpu-copy-pending`, `writing-frame`, `frame-ready`; problems: `gpu-timeout`,
`source-state-unverified`, `picture-failed`, `unsupported-format`, `readback-map-failed`, `allocation-failed`,
`capture-too-slow`, `shared-memory-failed`, `source-dimensions-changed`.

Sound states (`audio` in the status): `0` FMOD not loaded yet, `1` waiting for the CRTV's bus, `2` attaching,
`3` listening; `-1` an FMOD function missing, `-2` not FMOD 2, `-3`/`-4` a DSP couldn't be made/attached,
`-5` no hook on the update, `-6` no shared memory.

## The picture's crop

The game draws the CRTV's screen into a 2048×2048 texture, but the screen fills only a band of it:
`SCREEN_BOX = {6, 345, 2044, 1728}` (left, top, right, bottom), measured in the game (`picture.c`). Other
texture sizes are taken whole. If the game ever changes how it lays out that texture, the phone's picture shows
cut or shifted: re-measure and change `SCREEN_BOX`.

## The sound's buses

`TAP_PATHS` in `audio.c` names Townfall's buses, most wanted first: `bus:/World/Sfx/Player/CRTV`,
`bus:/Dialogue/WaypointSignals`, `bus:/Dialogue/Transmissions`, `bus:/Cinematics/Sequences/CRTV_Videos` go to the
phone; the CRTV's child buses and `bus:/Dialogue/Character` are only measured (their levels in the status, to compare
with). If the game renames its buses, any bus with "crtv" in its path is still taken. The buses file in `%TEMP%`
lists what the game has.

## Building

```
py -m pip install -r requirements-dev.txt
py native/build.py
```

`build.py` writes `profile.h`, compiles `tf_native.c` (and `test_harness.c`, the fake-engine test DLL) with Zig
(`zig cc -target x86_64-windows-gnu -std=c11 -O2 -s -shared`, plus MinHook's sources, linking `ole32` for WIC),
copies the DLL to `TownfallCompanion/Scripts/`, and writes the installed game's profile if it needs one. The C
runtime is Windows' own (UCRT).

The Windows build has been reproduced byte-for-byte using the pinned Zig toolchain and the same source and build
inputs, including builds from separate Python environments. This is not a guarantee across arbitrary compiler
versions or platforms. To check a release's DLL against its source, check out the release tag, install the pinned
requirements, run `py native/build.py`, and compare the DLL's SHA-256 with a published DLL hash, if one is provided
(the release archive's hash is different). The build uses `-s`: with
debug symbols, the linker stamps the DLL with an ID of its symbol file, which records where Zig keeps its build files,
and that differs between environments (the code was the same; 20 bytes of timestamp and ID weren't).

The build enables `-Wall -Wextra -Wshadow` to report problems the compiler can see. These warning flags are also
used for the fake-engine test DLL.

## Testing

`tests/test_native.py`:

- **DllTest** loads the real DLL: outside the game it stays off and says why; the real profile is taken only for
  the exact game build it was read from, and refused for a patched one.
- **GameProfileTest** reads the installed game: the profile's layout matches `profile.h`, the companion's own PE
  reader agrees with `pefile`, an unreadable file is reported, a changed function signature gives no profile.
- **The fake-engine tests** (`tf_native_tests.dll` from `test_harness.c`, which includes `tf_native.c`): the
  capture's state machine and every refusal case, the picture's crop and scale, the FMOD tap (rank, pass-through,
  nesting, mixing, muting and partial setup failures), the look's turning, inaccessible rotation memory and hook
  installation/cleanup failures, the alignment's packets, and capture status freshness.
- **FMOD's names**: every library and function `audio.c` asks for is exported by the game's FMOD.

The game-dependent tests are skipped when the game isn't installed.

## In the game

`UE4SS.log` shows the DLL's life:

```
[TF-NATIVE] tf_native.dll loaded
[TF-NATIVE] rhi-source-identified, capture streaming
[TF-AUDIO] game sound tap: listening
```

`[TF-PERF]` every 30 s shows the mod's game-thread time; the DLL's own part is the `native` entry (20-30 ms
per 30 s). The capture itself runs on the render thread and the JPEG on its own thread, so it doesn't show there.
