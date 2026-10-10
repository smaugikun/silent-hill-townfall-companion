# Troubleshooting

Where to look:

- **The companion's window** (`Start Companion.py`) shows the phone's address with its QR code, the PIN, whether
  the game is connected, and a **connection check** that tests the way from the phone to the PC.
- **The phone's settings** (⚙ → *Status*) show the connection, the game, the sensors and the sound.
- **`...\Win64\ue4ss\UE4SS.log`** shows whether the in-game part runs (lines starting with `[TF-`). The game
  rewrites it on every start, so look right after a session.

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

### Only a console window opens, not the companion's window

With `window = off` in `companion.ini` that's how it should be. Otherwise Python was installed without tkinter,
which its installer includes unless *tcl/tk and IDLE* was unticked: run the installer again, choose *Modify*, and
tick it. Either way the companion works in the console window, which shows the phone's address and the PIN too; the
connection check isn't there, but the phone test is (see
[The page doesn't open](#the-page-doesnt-open-or-keeps-saying-connection-lost)).

When the companion's window opens, the console window it started in closes: the companion's window has its
messages in its log, and closing it stops the companion.

### "Port 8790 is taken by another program, so the companion uses port 8791"

Not an error: open the new address it shows on the phone (and enter it in the Chrome flag, if you use it).
Often the other program is a companion window still open from before. For a fixed address, set a port in
`companion.ini` (below).

### "Townfall Companion is already running"

Another companion window is open (look in the taskbar, it may be minimized): use that one, or close it first.
If it isn't there, wait ten seconds after closing one; it checks for a companion that is still alive.

### The phone asks for a PIN, or says "Wrong PIN"

The PIN is in the companion's window (with the phone's address), and is `pin` in
`TownfallCompanion\companion.ini`. Five wrong tries lock that phone out for a minute. Set `pin =` empty to turn it
off, or put another number of 4 to 12 digits; restart the companion for it to apply. A phone that has the old PIN is
asked again.

### Settings in companion.ini

`TownfallCompanion\companion.ini` is written with the defaults on the first start. Normally nothing needs
changing:

- `port`: the port the phone connects to (8790). Set a free one, e.g. `port = 18790`, for an address that never
  changes, or when the window says all ports are taken.
- `listen`: `0.0.0.0` lets the phone connect; `127.0.0.1` allows only this PC.
- `pin`: the number the phone asks for once (a new settings file gets a random one; empty: none).
- `window`: `on` (the default) opens the companion's own window with the QR code and the connection check;
  `off` keeps it in the console window.

If the window complains about `companion.ini` itself, fix that line, or delete the file: the next start writes
it again with the defaults.

## The phone

### The page doesn't open, or keeps saying CONNECTION LOST

Is the companion's window still open? Its **connection check** tests the way from the phone to the PC when the
companion starts, and again when you click **Check the connection**. Each line says what it found and, if something
is wrong, what to do about it:

- **The companion on this PC** and **On the network**: whether the companion answers at `127.0.0.1` and at the
  PC's own network addresses. With `listen = 127.0.0.1` in `companion.ini` only the PC itself can open the page:
  set `listen = 0.0.0.0` and start the companion again.
- **Network adapters**: which of the PC's addresses the phone should open. VirtualBox, VMware, Hyper-V (also used
  by WSL and Docker) and VPNs give the PC addresses of their own that no phone can reach. The window shows the
  right address first, with its QR code.
- **Windows Firewall**: whether the firewall lets this Python in on the network the PC is on. Windows calls each
  network *Private* or *Public* and lets programs in by that kind; either works once Python is allowed on it.
  **Allow Python through the firewall** does that, after Windows asks for permission: it turns off a rule that
  blocks Python there (Windows makes one when its question about Python is answered with *Cancel*) and adds one
  that lets it in. The companion never changes the firewall without that click. If another security program (an
  antivirus with its own firewall) looks after the firewall, the check names it: allow Python there.
- **Phone test**: opening the page on the phone (the address or its QR code) is the test. To test without the
  PIN, open the address with `/check` at the end, e.g. `http://192.168.1.50:8790/check` (this works without the
  window too): it says *Your phone reached Townfall Companion*. Once a phone has got through, this line says from
  which address and when, and the Windows Firewall line says OK whatever its rules look like: the way works.

When everything on the PC is fine but the phone doesn't get through:

- The phone must be on the Wi-Fi of the router the PC is connected to: not a guest network, and with mobile data
  off.
- Turn off any VPN on the phone. A VPN on the PC can get in the way too: turn it off while you play, or allow local
  network access in its settings.
- Some routers keep the devices on their Wi-Fi apart: turn off *AP isolation* (or *client isolation*) in the
  router's settings.
- Type the address exactly as the window shows it, with `http://` and the port, or scan the QR code.
- Not `127.0.0.1` or `localhost`: on a phone those are the phone itself.

### The screen says RESTART THE COMPANION

The mod was updated while the companion was running, so the phone has the new page and the companion is still the
old one: close the companion's window and start `Start Companion.py` again.

### The screen says WAITING FOR GAME

The game isn't sending: it's in a menu or loading, or the in-game part didn't load (see UE4SS below).

### Turning or tilting the phone does nothing, Auto pickup doesn't react, the screen turns off

Looking around, steering, the tilt during fine tuning, Auto pickup and keeping the screen on all need Chrome's *Insecure
origins treated as secure* flag for the companion's address: see
[Phone setup](PHONE_SETUP.md#2-the-chrome-flag-android-recommended). If the port changed, the flag needs the new
address. iPhones can't do these over the companion's address; set a longer *Auto-Lock* instead.

### The phone doesn't vibrate

Settings → *Vibration* → *Test vibration*. Nothing? Check the phone's own vibration settings, silent mode and
battery saver. iPhones can't vibrate from a web page.

## Sound and picture

### No sound on the phone

- Tap the phone's screen once (browsers wait for a tap), and keep the page open with the screen on: a phone that
  turns its screen off or switches to another app stops playing and hands the CRTV back to the PC, as AV OUT does.
- Settings: *In VIEW the phone plays the sound* on, and the *Phone volume* up (at 0 the phone is silent and the
  game plays its own sound). In AV OUT the sound always plays on the PC.
- Settings → *Status* → *Sound* says *playing* while the game's sound comes through. *No sound from the game yet*:
  the companion gets none. `UE4SS.log` should have `[TF-AUDIO] game sound tap: listening`; another state there
  says what is missing.

### The game still plays the CRTV sound too

Turn on *Silence it in the game* in the settings. What the phone plays then goes quiet on the PC, as soon as it
comes through to the phone: the CRTV, the voices heard through it, the waypoints' and transmissions' talking, and
the CRTV's videos. Everything else stays in the game: your character's own voice, the story's cutscenes, the world.
The *Phone volume* slider sets the phone's own sound; the game's volume settings set it too.

### The CRTV doesn't show on the monitor in VIEW

That is the default: in VIEW the game's CRTV is switched on without its raise animation, so its voices and
fine tuning work on the phone but nothing shows on the monitor. Turn on *In VIEW, show the game's CRTV on the monitor*
and your character raises it, as with the radio button on your keyboard or controller.

Fine tuning has a setting of its own, *In VIEW, show the game's CRTV during fine tuning*: off, the monitor doesn't
show the CRTV or the hands holding it during fine tuning, even when you raised it yourself. Switching
either setting, or the AV OUT / VIEW selector, shows or hides the CRTV on the monitor at once. Only a CRTV the phone
switched on is put away again; one you raised with the radio button stays up.

Without the phone (AV OUT, its screen off, the page closed, or nothing heard from it for 5 seconds) none of these
settings apply: the game shows the CRTV as it does without the mod.

### The CRTV goes down by itself in VIEW

The phone tells the game every couple of seconds that it is in VIEW. With the phone's screen off or another app in
front, it tells the game AV OUT instead; and when it says nothing for 5 seconds (the page closed, the Wi-Fi gone),
the game takes it as AV OUT too. Either way the CRTV the phone switched on is put away, and the PC has the CRTV
again. Back on the page in VIEW, the phone switches it on again. `UE4SS.log` says why it was put away, in a line
starting with `[TF-CRTV] putting the phone's CRTV away`.

### The CRTV stays on the monitor, or the hands stay up, after leaving VIEW

Give it a moment: your character finishes raising the CRTV before lowering it, so flicking the switch fast shows a
whole raise and lowering, and a CRTV that doesn't go down by itself is put away after 3 seconds. While the game is
paused, nothing happens until you go on. If it stays up, report it with the `[TF-CRTV]` lines from `UE4SS.log`
around that moment.

### The phone shows static instead of the CRTV's screen

In VIEW the phone shows the game's own CRTV screen, fine tuning included, as long as the CRTV is up in the game.
`UE4SS.log` says what the mod's native part is doing in lines starting with `[TF-NATIVE]`: `tf_native.dll loaded`,
then `rhi-source-identified, capture streaming` while the picture goes to the phone.

After a game update, the companion reads the game's new version once as it starts, in a few seconds. Until then the
log says `waiting-for-game-profile` (or `game-profile-for-another-version`), and the picture comes as soon as it is
done, also while you play. If the companion's window says the CRTV's picture can't be streamed, see the next
section.

### The companion says "The CRTV's picture can't be streamed"

After a game update the companion reads the game's new version once, as it starts, from a file Townfall comes with
(`Townfall-Win64-Shipping.pdb`, next to the game's program). It says nothing while that works. When it doesn't, the
phone shows static instead of the CRTV's screen, while the sound and everything else go on working. Try these in
order, and start the companion again after each:

1. In Steam, right-click Townfall → *Properties* → *Installed Files* → *Verify integrity of game files*. This
   repairs that file if it's missing or damaged.
2. If an antivirus watches the game's folder, allow the mod's folder there (`...\ue4ss\Mods\TownfallCompanion`): the
   companion keeps what it read in its `Scripts` folder.
3. If the message stays, the update changed something the CRTV's picture is built on, and the picture needs a new
   version of the mod. Look for one on [the mod's Nexus page](https://www.nexusmods.com/silenthilltownfall/mods/125),
   or report it there if there isn't one yet.

### `UE4SS.log` says `tf_native.dll not loaded`

`TownfallCompanion\Scripts\tf_native.dll` takes the CRTV's picture and sound from the running game. Without it the
phone gets neither. Some antivirus programs distrust a DLL that attaches to a game's functions and quarantine it:
restore it from the quarantine (or extract it again from the mod's download), and allow the mod's folder
(`...\ue4ss\Mods\TownfallCompanion`) in the antivirus.

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
`Scripts\main.lua` directly in it, not one folder deeper (not
`Mods\TownfallCompanion\TownfallCompanion\`). If `ue4ss\Mods\mods.txt` lists `TownfallCompanion`, its line must end
in `: 1`.

### `UE4SS.log` says `this UE4SS has no LoopInGameThreadWithDelay; the mod stays idle`

The mod loaded, but your UE4SS is older than the game-thread timers it needs (UE4SS added them in
December 2025). Update to the newest
[UE4SS for SILENT HILL Townfall](https://www.nexusmods.com/silenthilltownfall/mods/4) package. If that still prints
this line, report it (below), with the UE4SS version it came with.

### The game feels slower

Every 30 s the log has a `[TF-PERF]` line: how many ms per second of the game's time the mod used. Normal is
about 25 ms per second or less. Much more? Please report it with that line.

## Still stuck?

Report it on [the mod's Nexus page](https://www.nexusmods.com/silenthilltownfall/mods/125) or as an
[issue on GitHub](https://github.com/smaugikun/silent-hill-townfall-companion/issues), with: the companion window's
text, the `[TF-` lines from `UE4SS.log`, and Settings → *Status* from the phone.
