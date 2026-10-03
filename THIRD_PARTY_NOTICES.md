# Credits and third-party notices

## Townfall Companion

By **smaugikun**. The code, documentation, CRTV device art and monster figures are licensed under the
[Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International License](LICENSE) (CC BY-NC-SA 4.0).
Commercial use is not permitted. Source code: https://github.com/smaugikun/silent-hill-townfall-companion.
Parts of the code were written with the help of AI.

The code uses only Python's standard library and the browser's own APIs: no third-party code is included.

### The CRTV device art

`TownfallCompanion/companion/static/crtv/*.png` (the device shown on the phone) is a fan recreation of the
CRTV from SILENT HILL: Townfall by smaugikun, assembled from AI-generated parts. CC BY-NC-SA 4.0; it covers
this artwork only, not the game's CRTV design.

### The monsters

`TownfallCompanion/companion/static/monster/*.webp` (the monsters shown on the phone) are a fan
interpretation of the game's monsters by smaugikun: 3D models made with [Meshy](https://www.meshy.ai) (AI)
and animated in Blender. They contain no material from the game. The models are credited to Meshy under its
license (CC BY 4.0 for models made on Meshy's free plan); the animated figures are under CC BY-NC-SA 4.0.
Neither covers the game's monster designs.

## Required, not included

Each of these is installed by the user from its own source; none of their files are in this mod.

### UE4SS

- **UE4SS** by the UE4SS-RE Team and contributors: https://github.com/UE4SS-RE/RE-UE4SS (MIT License).
- **UE4SS for SILENT HILL Townfall**, the package this mod requires, by Aeon Greyh:
  https://www.nexusmods.com/silenthilltownfall/mods/4. It redistributes an official UE4SS build with a custom
  StaticConstructObject signature for SILENT HILL Townfall, by Aeon Greyh. Its page doesn't allow uploading
  it elsewhere or modifying it: this mod only links to it and never changes any of its files.

### Python

Runs the companion on the PC. https://www.python.org, under the Python Software Foundation License.

### vgmstream

Reads the game's sounds from its FMOD banks on the user's PC. https://github.com/vgmstream/vgmstream,
under the ISC License (copyright its many authors, see its COPYING file).

## Video tools, not included

Used by the companion to convert the game's videos automatically on the user's PC. When the companion starts, it pre-caches missing videos in the background; a phone request can also
convert a not-yet-cached clip immediately as a fallback. `Convert Game Videos.bat` is only a manual pre-launch pre-cache step.

### RAD Video Tools

Decodes the game's Bink 2 videos to a temporary AVI for FFmpeg. By RAD Game Tools, © Epic Games, Inc.:
https://www.radgametools.com/bnkdown.htm. Downloaded by the user from RAD's site under RAD's terms.

### FFmpeg

Compresses/remuxes the converted videos for phone playback and adds separate game audio when needed. By the FFmpeg developers, https://ffmpeg.org. Builds such as
https://www.gyan.dev/ffmpeg/builds/ are under the GPL v3. Downloaded or installed by the user.

## The game

SILENT HILL is a trademark of KONAMI. Townfall Companion is an unofficial fan mod, not made or endorsed by
the game's makers or publishers. It contains no files from the game: the sounds and videos the phone plays
are read or converted on the user's PC from the user's own copy of the game, into
`TownfallCompanion/cache/`, and are never distributed.
