# Townfall Companion

![Townfall Companion](docs/images/banner.webp)

Your phone becomes the CRTV, the handheld TV scanner from **SILENT HILL: Townfall**: it shows what the CRTV
shows, plays its sound and the signals' voices, and can tune it and turn your character. The phone needs no app,
only its browser.

The mod is also available on **[Nexus Mods](https://www.nexusmods.com/silenthilltownfall/mods/125)**.

## Features

**Townfall Companion turns your phone into the CRTV handheld scanner from SILENT HILL: Townfall.**

Your phone's browser shows the CRTV with its dial, screen, tuning buttons and F key (saves the tuned frequency), plays its sounds and voices, and shows the signals, monsters and videos the game's CRTV finds. Turn the phone to look around with the scanner. No phone app is needed: the page runs from your PC over your home network. Audio and video are read and converted locally.

- **VIEW mode** - the CRTV is on your phone. You can tune it the same way you would on the keyboard or controller.
- **AV OUT mode** - the CRTV stays on your PC/TV screen.
- **Motion** - turning the phone turns the scanner view.
- **Auto Pickup** - picking the phone up switches to VIEW; laying it flat or setting it on a stand switches to AV OUT.
- **Extras** - a PIN so others on your network can't open the page, vibration for buttons and nearby signals, etc.
- The CRTV on the phone has an extra button (**F**) for saving the frequency.

Many features can be modified in the settings found in the phone "app", in the top-right corner.


| CRTV video | Monster scanner | Settings |
| --- | --- | --- |
| ![CRTV video on the phone](docs/images/crtv-blue.jpg) | ![Monster scanner on the phone](docs/images/crtv-red.jpg) | ![Townfall Companion settings](docs/images/settings.jpg) |

## Requirements

- **[UE4SS for SILENT HILL Townfall](https://www.nexusmods.com/silenthilltownfall/mods/4)** by AeonGreyh, installed as
  its page says. The UE4SS releases on GitHub crash this game; this package has the fix.
- **[Python](https://www.python.org/downloads/) 3.10 or newer**, with **"Add python.exe to PATH"** ticked.
- **[vgmstream](https://vgmstream.org)** (`vgmstream-win64.zip`): reads the game's sound. Without it the phone is silent.
- **[RAD Video Tools](https://www.radgametools.com/bnkdown.htm)** (`RADTools.7z`) and
  **[FFmpeg](https://www.gyan.dev/ffmpeg/builds/)**: convert the game's videos for the phone. Without them the
  phone shows static or a drawn picture where the CRTV plays a video.
- A phone with Chrome (Android) on the same Wi-Fi as the PC. iPhones work with [limits](docs/PHONE_SETUP.md#3-iphone).

Tested on SILENT HILL: Townfall Steam build 25534608.

## Installation

1. Install UE4SS for Townfall and Python. Start the game once to check it runs, then close it.
2. Put the mod's **`TownfallCompanion` folder** into `Townfall\Binaries\Win64\ue4ss\Mods` (Steam: *Manage* →
   *Browse local files*). `enabled.txt` must be directly in `Mods\TownfallCompanion\`, not one folder deeper.
3. Put the downloaded tools in the folders under `TownfallCompanion\tools\`: `vgmstream\`, `radtools\`
   (`radvideo64.exe`) and `ffmpeg\` (`ffmpeg.exe`). Each folder has a `PUT FILES HERE.txt` listing exactly what
   goes in it. `RADTools.7z` opens with right-click → *Extract All* on Windows 11, or with
   [7-Zip](https://www.7-zip.org).
4. Double-click **`Start Companion.py`** (it runs with the Python you installed, or open Command Prompt in the
   TownfallCompanion folder and run: `py "Start Companion.py"`) and keep its window open; it closes by itself about
   a minute after you exit the game. If Windows Firewall asks, allow Python on **private networks**. It converts the
   game's videos in the background, and its window shows the address for the phone and the PIN. Each time it
   starts it also hides UE4SS's console windows (`ue4ss\UE4SS-settings.ini`), which applies at the next game
   launch. If double-clicking opens an editor instead, use *Open with* → Python, or `Start Companion.bat`, which
   does the same.
5. On the phone, open that address in Chrome and bookmark it, enter the PIN once, and tap the screen once
   (browsers play sound only after a tap). On Android, also set Chrome's *Insecure origins treated as secure*
   flag for that address, or turning the phone and Auto pickup won't work: [Phone setup](docs/PHONE_SETUP.md).

## Every time you play

1. Double-click **`Start Companion.py`** (or `Start Companion.bat`) and keep its window open. It closes by itself
   about a minute after you exit the game.
2. Open the bookmarked address on the phone.
3. Start the game. The phone shows WAITING FOR GAME until you're in gameplay.

The PIN keeps others on your network out, but it isn't encryption: use the companion on your home network only.
You can change it, or turn it off, with `pin` in `companion.ini`.

**Updating:** close the companion and the game, delete the `Scripts` and `companion` folders in `TownfallCompanion`
and extract the new version over it. Your settings, cache and tools stay.
**Uninstalling:** delete `...\ue4ss\Mods\TownfallCompanion`.

## Troubleshooting

- **The phone can't open the page:** same Wi-Fi, Python allowed through the firewall on private networks, and the
  address from the window (not `127.0.0.1`).
- **WAITING FOR GAME:** get into gameplay, and check that `ue4ss\UE4SS.log` has `[TF-COMPANION] Townfall Companion loaded`.
- **No sound:** tap the phone once, keep its screen on, and check the window for "Game sounds unavailable"
  (usually vgmstream is missing).
- **PIN forgotten:** it is in the window and as `pin` in `companion.ini`.

More: [Troubleshooting](docs/TROUBLESHOOTING.md) · [Phone setup](docs/PHONE_SETUP.md)

The mod contains no files from the game: the companion decodes the sounds and converts the videos from your own
copy, on your PC, and keeps them in `TownfallCompanion\cache`.

## Credits

Townfall Companion by smaugikun
([source on GitHub](https://github.com/smaugikun/silent-hill-townfall-companion), also on
[Nexus Mods](https://www.nexusmods.com/silenthilltownfall/mods/125)), code and art under
[CC BY-NC-SA 4.0](LICENSE): no commercial use. UE4SS by the UE4SS-RE Team and contributors
([RE-UE4SS](https://github.com/UE4SS-RE/RE-UE4SS)); its Townfall package and signature by Aeon Greyh. More in
[Third-party notices](THIRD_PARTY_NOTICES.md).

SILENT HILL is a trademark of KONAMI. This mod is unofficial and not made or endorsed by the game's makers or
publishers.
