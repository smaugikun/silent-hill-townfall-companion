# Changelog

## 2.0.0

A rewrite: the phone shows the game's own CRTV, live, instead of rebuilding it from files taken out of the game.

**New**

- The CRTV's screen comes live from the running game, as the game draws it: its videos, monsters, signals, static
  and fine tuning.
- The CRTV's sound comes live from the game's own mixer: the CRTV, the voices heard over it, the waypoints'
  and transmissions' talking, and the CRTV's videos.
- CRTV fine tuning on the phone: the D-pad's arrows and centre work as on a controller, and leaning and
  tilting the phone moves the picture in its image stages (with a sensitivity setting).
- Looking around: turn the phone and the game's CRTV looks where it points, without turning your character;
  ⌖ lines it up with your character again.
- Settings: show the game's CRTV on the monitor in VIEW, show it during fine tuning, silence the CRTV's sound in the
  game while the phone plays it, and the phone's own volume.
- Game updates: the companion reads each new version of the game by itself, in a few seconds.

**Changed**

- The D-pad replaces the F key, and is larger.
- Steering with the phone looks up and down too, as the mouse does; the CRTV looks where the camera does.
- When the phone's screen turns off, the page closes or the phone is silent for 5 seconds, the CRTV goes back to the
  PC, as in AV OUT.
- The companion opens its window by default: the address with its QR code, the PIN, and the connection check.
  Its messages only speak up when something needs your attention.
- The mod is now under the MIT License.

**Removed**

- Converting the game's videos and sounds, the tools that needed (vgmstream, RAD Video Tools, FFmpeg) and the
  `cache` folder. From 1.x, the `tools` and `cache` folders can be deleted.
- The mod's own monster models: the phone shows the game's monsters.

## 1.0.0

First release.
