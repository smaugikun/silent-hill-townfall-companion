# Troubleshooting

Where to look:

- **The companion's window** (`Start Companion.py`) says what it found, which port it uses, and what is
  missing.
- **The phone's settings** (⚙ → *Status*) show the connection, the game, the sensors and the sound.
- **`...\Win64\ue4ss\UE4SS.log`** shows whether the in-game part runs (lines starting with `[TF-`). The game
  rewrites it on every start, so look right after a session.
- **`http://127.0.0.1:8790/api/state`** (or the port the window shows) on the PC shows what the phone gets.

## The companion

### Nothing happens, or "needs Python 3.10 or newer"

Install Python 3.10 or newer from [python.org](https://www.python.org/downloads/) and tick **"Add python.exe to
PATH"** in the installer. Already installed without it? Run the installer again, choose *Modify*, and tick
*Add Python to environment variables*.

### Double-clicking `Start Companion.py` opens an editor, or nothing opens

Windows has no program set for `.py` files, or an editor you installed took them over. Right-click the file →
*Open with* → *Choose another app* → **Python** (tick *Always use this app*). Or open a Command Prompt in the
`TownfallCompanion` folder and run `py "Start Companion.py"` (or `python "Start Companion.py"`). The window stays
open on errors and shows them.

### "Port 8790 is taken by another program, so the companion uses port 8791"

Not an error: open the new address it shows on the phone (and enter it in the Chrome flag, if you use it).
Often the other program is a companion window still open from before. For a fixed address, set a port in
`companion.ini` (below).

### "Townfall Companion is already running"

Another companion window is open (look in the taskbar, it may be minimized): use that one, or close it first.
If it isn't there, wait ten seconds after closing one; it checks for a companion that is still alive.

### The phone asks for a PIN, or says "Wrong PIN"

The PIN is in the companion's window (with the phone's address), and is `pin` in
`TownfallCompanion\companion.ini`. Five wrong tries
lock that phone out for a minute. Set `pin =` empty to turn it off, or put another number of 4 to 12 digits;
restart the companion for it to apply. A phone that has the old PIN is asked again.

### Settings in companion.ini

`TownfallCompanion\companion.ini` is written with the defaults on the first start. Normally nothing needs
changing:

- `port`: the port the phone connects to (8790). Set a free one, e.g. `port = 18790`, for an address that never
  changes, or when the window says all ports are taken.
- `listen`: `0.0.0.0` lets the phone connect; `127.0.0.1` allows only this PC.
- `pin`: the number the phone asks for once (a new settings file gets a random one; empty: none).
- `game`: the folder that contains `Townfall\Content`, e.g. `game = D:\SteamLibrary\steamapps\common\Townfall`.
  Only needed when the window says **"Townfall not found"**, **"the game's sound banks not found"** or
  **"The game's videos aren't in …"**.
- `vgmstream`, `ffmpeg`, `radvideo`: a tool's full path, if it isn't in its folder under `tools\` (`vgmstream\`,
  `ffmpeg\`, `radtools\`; each has a `PUT FILES HERE.txt`) or on the PATH.

If the window complains about `companion.ini` itself, fix that line, or delete the file: the next start writes
it again with the defaults.

### "Missing tools" (or "missing vgmstream-cli.exe"), but I put the files in

The companion looks only in the `tools` folder next to its own `companion` folder, and the window prints that
path. Put the three folders there, each with the files its `PUT FILES HERE.txt` lists: `vgmstream\` (`vgmstream-cli.exe`
and its `.dll` files), `radtools\` (`radvideo64.exe`) and `ffmpeg\` (`ffmpeg.exe`). If the printed path has
`TownfallCompanion` twice (`...\Mods\TownfallCompanion\TownfallCompanion\tools`), the mod folder is one level too
deep, and UE4SS won't load it either: move it up so that `enabled.txt` is in `...\Mods\TownfallCompanion\`.

## The phone

### The page doesn't open, or keeps saying CONNECTION LOST

- Use the address the companion's window shows, not `127.0.0.1`.
- Phone and PC on the same Wi-Fi or LAN; not a guest network; no VPN on either; mobile data doesn't count.
- Allow Python through the Windows firewall on private networks, and set your Wi-Fi to *Private* in Windows
  (see [Phone setup](PHONE_SETUP.md#the-windows-firewall)).
- Is the companion's window still open?

### The screen says RESTART THE BRIDGE

The page is newer than the companion still running from before an update: close the companion's window and
start `Start Companion.py` again.

### The screen says WAITING FOR GAME

The game isn't sending: it's in a menu or loading, or the in-game part didn't load (see UE4SS below).

### Turning the phone does nothing, Auto pickup doesn't react, the screen turns off

These need Chrome's *Insecure origins treated as secure* flag for the companion's address: see
[Phone setup](PHONE_SETUP.md#2-the-chrome-flag-android-recommended). If the port changed, the flag needs the new
address. iPhones can't do these over the companion's address; set a longer *Auto-Lock* instead.

### The phone doesn't vibrate

Settings → *Vibration* → *Test vibration*. Nothing? Check the phone's own vibration settings, silent mode and
battery saver. iPhones can't vibrate from a web page.

## Sound and videos

### No sound on the phone

- Tap the phone's screen once (browsers wait for a tap), and keep the page open with the screen on: a phone that
  dims its screen or switches to another app stops playing, and the game's sound comes back a few seconds later.
- Settings: *In VIEW the phone plays the sound* on, and the *Phone volume* up (at 0 the phone is silent and the
  game plays its own sound). In AV OUT the sound always plays on the PC.
- The companion's window says **"Game sounds unavailable, the phone will be silent"** and why: usually
  **vgmstream** is missing. Unpack `vgmstream-win64.zip` into `TownfallCompanion\tools\vgmstream\` and start the
  companion again.

### The game still plays the CRTV sound too

Turn on *Silence it in the game* in the settings. It quiets only what the phone plays instead. Some sounds
always stay in the game: the CRTV's clicks and beeps, the monsters' voices, and everything outside the CRTV (the
story cutscenes' dialogue and music, the sound of videos on screens in cutscenes). A CRTV video's sound stays in
the game too when the videos aren't converted. The *Phone volume* slider only sets the phone's own sound.

### The CRTV doesn't show on the monitor in VIEW

That is the default: in VIEW the game's CRTV is switched on without its raise animation, so its voices and
mini-game work on the phone but nothing shows on the monitor. Turn on *In VIEW, show the game's CRTV on the
monitor* and your character raises it as with L1 on a controller. Switching that setting, or the AV OUT / VIEW
selector, shows or hides the CRTV on the monitor at once; the phone only ever puts away a CRTV it raised itself,
not one you raised with the controller or keyboard.

### The CRTV stays on the monitor, or the hands stay up, after leaving VIEW

Your character needs about a second to raise the CRTV and half a second to lower it, and he drops a request
that comes in the middle of either. So the phone's request is kept and carried out when he is ready: a lowering
waits for the raise to finish, a raise for the lowering to finish, and the last thing you asked for wins.
Flicking the selector fast therefore shows a full raise and lowering, not a half one. Lowering is asked for the
way the controller does it (radio and hands go down together); only if he hasn't done it after 3 seconds is it
forced, which makes the radio vanish at once and lets the hands lag. If it still goes wrong, report the
`[TF-CRTV] phone command: ...` lines around it, and `the character didn't carry out the phone's request:
dropped` if that is there.

### The fine-tune mini-game shows on the monitor but not on the phone

The game keeps the mini-game's screen in an object the mod doesn't know yet. While you are tuned to a waypoint
that isn't found yet, the mod looks through the CRTV's widget, the radio and its actor for it, and lists what it
meets as `[TF-PROBE] mini-game: ...` lines in `UE4SS.log` (a flag or number comes with its value then).
`[TF-CRTV] mini-game widget: ...` says it found the screen. If it says `no object held by the CRTV's widget, the
radio or its actor has a fine-tune canvas`, report the `[TF-PROBE] mini-game:` lines: they name what the screen
is made of.

### A signal's voice is heard only in the game

Settings → *Status* → *Talking* says why, e.g. a line that isn't in the game's sound banks.

### No story videos, only a drawn picture

As soon as the companion starts, it begins pre-caching all missing videos automatically in one background worker, without waiting for gameplay telemetry or a save to load. If the phone requests a clip
before the background worker reaches it, that clip converts immediately as a request-time fallback.

The companion window will report the progress, for example:

```text
Game videos: pre-caching 18 missing clip(s) in the background ...
Game video: pre-caching Bink/Shipping/Mov_CRTV_Clinic.mp4 ...
Game videos: pre-cache 1/18 (1 ready)
```

The automatic converter uses RAD's Bink-to-AVI path internally, because that is the reliable path for
Townfall's clips. FFmpeg immediately compresses the temporary AVI into the cached phone MP4, then the AVI
is deleted. RAD and FFmpeg run in the background; closing the companion stops an active conversion, and
the next start continues with whatever clips are still missing. Background conversion also runs at reduced
CPU priority (and FFmpeg uses one encoding thread) so on-demand phone audio/dialogue stays responsive during play.

If automatic conversion says a tool is missing, put RAD Video Tools in `TownfallCompanion\tools\radtools\`, FFmpeg in
`tools\ffmpeg\` and vgmstream in `tools\vgmstream\` (each folder has a `PUT FILES HERE.txt`; FFmpeg can also be
installed with `winget install Gyan.FFmpeg.Essentials`). To convert every clip before you start
Townfall, run `py companion/convert_videos.py` in a Command Prompt in `TownfallCompanion` (add `--force` to redo
all); it also retries a video that reported **FAILED**.

## UE4SS and the game

### `UE4SS.log` doesn't exist, or the game starts without UE4SS

UE4SS isn't installed in the right place, or your antivirus removed its `dwmapi.dll` from
`Townfall\Binaries\Win64`. Install [UE4SS for SILENT HILL Townfall](https://www.nexusmods.com/silenthilltownfall/mods/4)
again as its page says, and allow it in your antivirus.

### The game crashes on start after installing UE4SS

Use the package from that Nexus page, not UE4SS from GitHub: it carries the fix that makes UE4SS work on
Townfall. After a game update, it may need an update too.

### `UE4SS.log` has no `[TF-COMPANION] Townfall Companion loaded`

The mod isn't where UE4SS looks. It must be `...\Win64\ue4ss\Mods\TownfallCompanion\` with `enabled.txt` and
`Scripts\main.lua` directly in it, not one folder deeper (not `Mods\TownfallCompanion\TownfallCompanion\`). If `ue4ss\Mods\mods.txt` lists `TownfallCompanion`, its
line must end in `: 1`.

### `UE4SS.log` says `this UE4SS has no LoopInGameThreadWithDelay; the mod stays idle`

The mod loaded, but your UE4SS is older than the game-thread timers it needs (UE4SS added them in
December 2025). Update to the newest [UE4SS for SILENT HILL Townfall](https://www.nexusmods.com/silenthilltownfall/mods/4)
package. If that still prints this line, report it on [the mod's Nexus page](https://www.nexusmods.com/silenthilltownfall/mods/125), with the UE4SS version it came with.

### The game feels slower

Every 30 s the log has a `[TF-PERF]` line: how many ms per second of the game's time the mod used. Normal is
about 25 ms per second or less. Much more? Please report it with that line.

## Still stuck?

Report it on [the mod's Nexus page](https://www.nexusmods.com/silenthilltownfall/mods/125) with: the companion window's text, the `[TF-` lines from
`UE4SS.log`, and Settings → *Status* from the phone.
