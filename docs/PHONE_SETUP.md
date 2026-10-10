# Phone setup

The phone needs no app: it opens a page the companion serves on your PC.

## 1. Connect

1. Double-click `Start Companion.py` on the PC. Its window shows the address for the phone, for example
   `http://192.168.1.50:8790`, with a QR code, and the PIN.
2. Put the phone on the **same Wi-Fi (or LAN)** as the PC. A guest network or a network that isolates its
   devices from each other won't work.
3. Open that address in the phone's browser, or scan the QR code: **Chrome** on Android, **Safari** on an iPhone.
4. Enter the PIN (the phone remembers it), and bookmark the page.
5. Tap the screen once. Browsers only play sound, go full screen and vibrate after a tap.

`127.0.0.1` or `localhost` on a phone is the phone itself, so always use the address the window shows. If the page
doesn't open: [Troubleshooting](TROUBLESHOOTING.md#the-page-doesnt-open-or-keeps-saying-connection-lost).

The address stays the same as long as the PC keeps its network address and the port stays free. If another
program has taken port 8790, the companion uses the next free port and says so; set a fixed `port` in
`TownfallCompanion\companion.ini` if you want it never to change.

### The Windows firewall

The first time the companion starts, Windows asks whether Python may communicate on networks: allow it. If the
phone still can't get in, the companion's window says why, and its **Allow Python through the firewall** button lets
Python in on the network the PC is on
([Troubleshooting](TROUBLESHOOTING.md#the-page-doesnt-open-or-keeps-saying-connection-lost)). By hand: Windows
Security → *Firewall & network protection* → *Allow an app through firewall* → *Change settings* → find **Python**
and tick the kind of network the PC is on, *Private* or *Public* (Settings → *Network & internet* shows which).

What stays on the PC: the game and the companion talk through files and shared memory on the PC, never over the
network. Only the phone's page goes over the network, on the companion's port.

## 2. The Chrome flag (Android, recommended)

The companion's page is a plain `http://` page on your home network, and Chrome gives some features only to
secure (`https://`) pages. You can tell Chrome to treat the companion's address as secure:

1. In Chrome on the phone, open `chrome://flags/#unsafely-treat-insecure-origin-as-secure`
2. The flag is called **Insecure origins treated as secure**. Set it to **Enabled**.
3. In its text box, enter the companion's address exactly as the window shows it, with the port, e.g.
   `http://192.168.1.50:8790`
4. Tap **Relaunch**.

The flag applies only to the address you enter, and only in Chrome on that phone. If the address changes
(another port, or the PC got a new network address), enter the new one.

| | Without the flag | With the flag |
|---|---|---|
| The CRTV's picture and sound | ✓ | ✓ |
| TUNING buttons, D-pad, AV OUT / VIEW switch, settings | ✓ | ✓ |
| Vibration | ✓ | ✓ |
| Turning the phone to look around with the CRTV, or to steer your character | ✗ | ✓ |
| Leaning and tilting the phone during fine tuning | ✗ | ✓ |
| Auto pickup, and the phone noticing it is put down | ✗ | ✓ |
| Keeping the screen on | ✗ (set a longer screen timeout instead) | ✓ |

The settings (⚙) show under *Status* whether the rotation sensor and "Screen stays on" work.

## 3. iPhone

Safari has no such flag, so on an iPhone the motion sensors and keeping the screen on don't work over the
companion's `http://` address; iPhones also can't vibrate from a web page. The screen, sound, voices,
videos, buttons and settings work. During fine tuning, move the picture with the mouse or the controller
instead. Set *Auto-Lock* to a longer time while you play.
