# Troubleshooting

Where to look:

- **The companion's window** (`Start Companion.bat`) says what it found, which port it uses, and what is
  missing.
- **The phone's settings** (⚙ → *Status*) show the connection, the game, the sensors and the sound.
- **`...\Win64\ue4ss\UE4SS.log`** shows whether the in-game part runs (lines starting with `[TF-`). The game
  rewrites it on every start, so look right after a session.
- **`http://127.0.0.1:8790/api/state`** (or the port the window shows) on the PC shows what the phone gets.

## The companion

### "Python was not found", or "needs Python 3.10 or newer"

Install Python 3.10 or newer from [python.org](https://www.python.org/downloads/) and tick **"Add python.exe to
PATH"** in the installer. Already installed without it? Run the installer again, choose *Modify*, and tick
*Add Python to environment variables*.

### The window opens and closes at once

Start `Start Companion.bat` by double-clicking it in its folder; it stays open on errors and shows them. If it
still closes, open a Command Prompt in `TownfallCompanion` and run `companion\run.bat bridge.py` to see the
message.

### "Port 8790 is taken by another program, so the companion uses port 8791"

Not an error: open the new address it shows on the phone (and enter it in the Chrome flag, if you use it).
Often the other program is a companion window still open from before. For a fixed address, set a port in
`companion.ini` (below).

### Settings in companion.ini

`TownfallCompanion\companion.ini` is written with the defaults on the first start. Normally nothing needs
changing:

- `port`: the port the phone connects to (8790). Set a free one, e.g. `port = 18790`, for an address that never
  changes, or when the window says all ports are taken.
- `listen`: `0.0.0.0` lets the phone connect; `127.0.0.1` allows only this PC.
- `game`: the folder that contains `Townfall\Content`, e.g. `game = D:\SteamLibrary\steamapps\common\Townfall`.
  Only needed when the window says **"Townfall not found"**, **"the game's sound banks not found"** or
  **"The game's videos aren't in …"**.
- `vgmstream`, `ffmpeg`, `radvideo`: a tool's full path, if it isn't in `tools\` or on the PATH.

If the window complains about `companion.ini` itself, fix that line, or delete the file: the next start writes
it again with the defaults.

## The phone

### The page doesn't open, or keeps saying CONNECTION LOST

- Use the address the companion's window shows, not `127.0.0.1`.
- Phone and PC on the same Wi-Fi or LAN; not a guest network; no VPN on either; mobile data doesn't count.
- Allow Python through the Windows firewall on private networks, and set your Wi-Fi to *Private* in Windows
  (see [Phone setup](PHONE_SETUP.md#the-windows-firewall)).
- Is the companion's window still open?

### The screen says RESTART THE BRIDGE

The page is newer than the companion still running from before an update: close the companion's window and
start `Start Companion.bat` again.

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

- Tap the phone's screen once (browsers wait for a tap).
- Settings: *In VIEW the phone plays the sound* on, and the volume up. In AV OUT the sound always plays on the PC.
- The companion's window says **"Game sounds unavailable, the phone will be silent"** and why: usually
  **vgmstream** is missing. Unpack `vgmstream-win64.zip` into `TownfallCompanion\tools\` and start the companion
  again.

### The game still plays the CRTV sound too

Turn on *Silence it in the game* in the settings. Some sounds always stay in the game: the CRTV's clicks and
beeps, the monsters' voices, the cutscenes' music and the sound of videos in cutscenes. A CRTV video's sound
stays in the game too when the videos aren't converted.

### A signal's voice is heard only in the game

Settings → *Status* → *Talking* says why, e.g. a line that isn't in the game's sound banks.

### No story videos, only a drawn picture

You do **not** need to run `Convert Game Videos.bat` first. When game telemetry becomes live, the companion
starts pre-caching all missing videos automatically in the background. If the phone requests a clip before
the background job reaches it, that clip converts immediately as a fallback. Check the companion window: if automatic conversion says a
tool is missing, put RAD Video Tools, FFmpeg and vgmstream in `TownfallCompanion\tools\` (FFmpeg can also
be installed with `winget install Gyan.FFmpeg.Essentials`). `Convert Game Videos.bat` is only an optional
pre-cache step if you want every clip ready in advance so its first play has no conversion delay. If a video
reports **FAILED**, the batch file can also be used to retry/pre-cache missing clips. Without the videos everything
else works.

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
`Scripts\main.lua` directly in it, not one folder deeper. If `ue4ss\Mods\mods.txt` lists `TownfallCompanion`, its
line must end in `: 1`.

### The game feels slower

Every 30 s the log has a `[TF-PERF]` line: how many ms per second of the game's time the mod used. Normal is
about 25 ms per second or less. Much more? Please report it with that line.

## Still stuck?

Report it on the mod's Nexus page with: the companion window's text, the `[TF-` lines from `UE4SS.log`, and
Settings → *Status* from the phone.
