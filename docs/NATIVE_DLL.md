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
- **Nothing outside the game.** It runs only inside the game's own process, loaded by the mod's Lua script. It opens
  no other program and loads no other library: FMOD's functions come from the game's own FMOD, already loaded.
- **Nothing on disk changes.** It writes only its own small files in `%TEMP%` (its status, and the list of the game's
  sound buses) and two blocks of shared memory for the companion. The game's files stay as they are.
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
| `tf_native_update` | `tf_native.lua`, every 0.5 s, game thread | `townfall-companion-native-source.bin` | takes the CRTV texture Lua found, the phone's fps and width, the radio's fine tuning state; retries the profile every 5 s; writes the status |
| `tf_native_align` | `tf_alignment.lua` | `townfall-companion-native-align.bin` | moves the fine tuning picture once |
| `tf_native_look` | `tf_native.lua` | `townfall-companion-native-look.bin` | takes where the phone looks |
| `tf_native_sound` | `tf_native.lua` | `townfall-companion-native-sound.bin` | silences (6 s, renewed) or restores the CRTV's sound in the game |
| `tf_native_state`, `tf_native_validate` | the tests only | — | the state; a profile checked against a game image |

The packet files stay open on both sides and are rewritten in place: opening a file in `%TEMP%` waits for the virus
scanner (milliseconds, on the game thread), reading through an open handle doesn't. Each packet starts with an
8-byte magic and has a fixed size (checked with `_Static_assert`):

| Packet | Magic | Size | Content |
|---|---|---|---|
| source | `TFNATV03` | 40 | texture (UTexture*), radio (UHandheldRadioComponent*), fps (0..30), width (320/480/640), Unix time (must be within 4 s) |
| align | `TFALIGN1` | 40 | radio, x and y to move (the image spans -1..1), the command's time (ms; a repeat is ignored) |
| look | `TFLOOK04` | 40 | yaw (±180°, from the character's facing), pitch (±90°), looking (0/1), the command's time (ms) |
| sound | `TFSOUND1` | 16 | mute (0/1) |

## What it hands to the companion

Two named shared-memory blocks, read by the companion's Python (no files, no copies):

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
the state, the capture's state and why it waits, frames published, timings, the look, the sound's state, rate,
buses and levels, and the radio's fine tuning state. `tf_native.lua` logs it as `[TF-NATIVE]` / `[TF-AUDIO]` lines
when it changes, and the companion serves it at `/api/crtv/status`. `%TEMP%\townfall-companion-native-buses.txt`
lists the game's FMOD buses, the tapped ones marked.

## The game profile: surviving game updates

Every engine function the DLL calls or hooks, and every structure member it reads, moves with each game build.
None of it is hard-coded. Instead:

1. The game ships its debug symbols, `Townfall-Win64-Shipping.pdb`, next to its executable.
2. At start, the companion (`game_profile.py`, through Windows' `dbghelp.dll` in `game_symbols.py`) checks whether
   `Scripts\tf_native.profile` fits the installed game. If not (a game update), it reads the PDB once: about 4.5 s,
   in a separate low-priority process.
3. It checks everything the DLL takes for granted: each function's exact signature (`SIGNATURES`), each member's
   offset and size, each enum value. If the update changed any of that, no profile is written and the companion
   says *The CRTV's picture can't be streamed to the phone*. `py TownfallCompanion/companion/game_profile.py --show`
   says what changed.
4. The DLL loads the profile (`TFPROF01`, version 1, 436 bytes) and checks it against the running game: build
   timestamp, image size, the PDB identity (CodeView GUID and age), and the first 16 bytes of every function it
   will call. Any difference and it stays off.
5. Until a fitting profile is there, the DLL retries every 5 seconds, so a profile written mid-game is picked up.

The sound needs no profile: FMOD's functions are found by name in the game's `fmod.dll` / `fmodstudio.dll`.

`profile.h` is generated by `native/build.py` from `game_profile.FIELDS`: the C struct the profile is read into,
a macro per value (`END_VIEWPORT_RVA` → `profile.end_viewport_rva`), and the list of functions whose first bytes
are checked. Change `FIELDS`, and bump `game_profile.VERSION`.

**When a game update needs a mod update:** only when it changed a signature, a member the DLL reads, or an enum
value it uses. Then: adjust the code that uses it, update `SIGNATURES`/`FIELDS`, rebuild, test.

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

## Safety rules in the code

- Memory that may be gone is read with `ReadProcessMemory` (`read_memory`): a failed read is an answer, not a crash.
- The GPU copy is only added to the immediate command list, never on the game thread, never inside a render pass,
  never on a list that is executing, never with more than one GPU. Each case has a wait reason in the status.
- A texture in an unknown resource state is refused (`source-state-unverified`), not guessed.
- One copy in flight. An unfinished copy is never freed; after 5 s without the GPU finishing it, capture stops
  (`gpu-timeout`) rather than risk it.
- Capture is paced: at most the phone's fps (≤ 30), and slower if its own CPU time would exceed a tenth of the time.
  Over 50 ms for one step stops it (`capture-too-slow`).
- Only RGBA8/BGRA8 textures up to 2048×2048.
- The FMOD DSPs pass every block on unchanged; muting writes silence only into the CRTV's own buses.

## States and what they mean

The DLL's `state`: `1` running with a fitting profile; `0` waiting for one (sound runs anyway); below 0 off:
`-1` the game isn't there, `-3` couldn't pin itself, `-4` a hook couldn't be installed, `-5` no usable temp or DLL path.

Status strings (`[TF-NATIVE]` lines):

| Status | Meaning |
|---|---|
| `waiting-for-game-profile` | no profile yet (after an update, until the companion has read the game) |
| `game-profile-for-another-version` | a profile is there, but for another game build |
| `waiting-for-crtv-source` | running; no CRTV texture from Lua (CRTV down, no phone) |
| `invalid-source`, `rhi-source-not-ready` | Lua named a texture that isn't (yet) a live GPU texture |
| `rhi-source-identified` | the texture is found: capture runs while a phone watches |
| `hook-install-failed`, `game-not-present` | off |

Capture states: `streaming`, `disabled`, `gpu-copy-pending`, `frame-ready`; problems: `gpu-timeout`,
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

The build is reproducible: the same source gives a byte-identical DLL in any environment (checked with two separate
Python environments: the same SHA-256). So a release's DLL can be checked against its source: check out the release's
tag, run `py native/build.py`, and compare the SHA-256 of `TownfallCompanion/Scripts/tf_native.dll` with the one in
the release notes. That needs `-s`: with
debug symbols, the linker stamps the DLL with an ID of its symbol file, which records where Zig keeps its build files,
and that differs between environments (the code was the same; 20 bytes of timestamp and ID weren't).

To check for problems the compiler can see: add `-Wall -Wextra -Wshadow` to the command in `compile_dll`; the
code builds without warnings.

## Testing

`tests/test_native.py`:

- **DllTest** loads the real DLL: outside the game it stays off and says why; the real profile is taken only for
  the exact game build it was read from, and refused for a patched one.
- **GameProfileTest** reads the installed game: the profile's layout matches `profile.h`, the companion's own PE
  reader agrees with `pefile`, an unreadable file is reported, a changed function signature gives no profile.
- **The fake-engine tests** (`tf_native_tests.dll` from `test_harness.c`, which includes `tf_native.c`): the
  capture's state machine and every refusal case, the picture's crop and scale, the FMOD tap (rank, pass-through,
  nesting, mixing, muting), the look's turning, the alignment's packets.
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
