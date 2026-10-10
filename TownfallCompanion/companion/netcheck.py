"""Why a phone can't open the companion's page, found step by step: the checks behind the window's "Check the
connection".

gather() measures: it asks the companion for /check at 127.0.0.1 and at each of the PC's addresses, and runs one
PowerShell survey of the network adapters, the network profile and Windows Firewall. steps() turns what it found
into plain-language results, each with what to do about it. Everything that decides is a plain function of what
was measured, so the tests give it samples; whatever measures takes its runner as an argument.
"""
import base64
import dataclasses
import ipaddress
import json
import ntpath
import os
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field

OK, PROBLEM, NOTE, WAIT, UNKNOWN, CHECKING = "ok", "problem", "note", "wait", "unknown", "checking"
RULE_NAME = "Townfall Companion (Python)"
NETWORK_ADVICE = ("The phone must be on the Wi-Fi of the router this PC is connected to: not a guest network, and "
                  "with mobile data off. "
                  "Turn off any VPN on the phone. Some routers keep the devices on their Wi-Fi apart (a setting "
                  "called AP isolation or client isolation): turn it off in the router's settings. And check the "
                  "address on the phone letter by letter, with http:// and the port.")

# What the PC's addresses belong to, by words in the adapter's name or description: (word, kind, what it is).
# A VPN or a virtual adapter has an address of its own that no phone on the Wi-Fi can reach.
ADAPTER_WORDS = (
    ("vEthernet (WSL", "virtual", "WSL (Linux on Windows)"),
    ("Docker", "virtual", "Docker"),
    ("VirtualBox", "virtual", "VirtualBox"),
    ("VMware", "virtual", "VMware"),
    ("vEthernet", "virtual", "a Hyper-V switch (WSL, Docker and virtual machines use them)"),
    ("Hyper-V", "virtual", "Hyper-V"),
    ("Wi-Fi Direct", "hotspot", "the PC's mobile hotspot"),
    ("Bluetooth", "virtual", "Bluetooth"),
    ("Loopback", "virtual", "a loopback adapter"),
    ("Tailscale", "vpn", "Tailscale (a VPN)"),
    ("ZeroTier", "vpn", "ZeroTier (a VPN)"),
    ("Hamachi", "vpn", "Hamachi (a VPN)"),
    ("Radmin", "vpn", "Radmin VPN"),
    ("NordLynx", "vpn", "NordVPN"),
    ("WireGuard", "vpn", "WireGuard (a VPN)"),
    ("Wintun", "vpn", "a VPN"),
    ("OpenVPN", "vpn", "OpenVPN"),
    ("TAP-", "vpn", "a VPN"),
    ("Mullvad", "vpn", "Mullvad VPN"),
    ("AnyConnect", "vpn", "Cisco AnyConnect (a VPN)"),
    ("PANGP", "vpn", "GlobalProtect (a VPN)"),
    ("Forti", "vpn", "FortiClient (a VPN)"),
    ("Cloudflare", "vpn", "Cloudflare WARP (a VPN)"),
    ("VPN", "vpn", "a VPN"),
)
RANK = {"": 0, "hotspot": 1, "virtual": 2, "vpn": 2, "no-router": 3}

# One PowerShell run for everything the checks need, as JSON whatever the language of Windows: enums as their
# names, and anything not ASCII escaped, since the console's code page isn't known. A section whose cmdlets fail
# (an old or stripped-down Windows) is null.
SURVEY = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
function Section([scriptblock]$query) { try { , @(& $query) } catch { $null } }
$survey = [ordered]@{
  addresses = Section { Get-NetIPAddress -AddressFamily IPv4 -AddressState Preferred |
    Select-Object InterfaceIndex, InterfaceAlias, IPAddress }
  adapters = Section { Get-NetAdapter |
    Select-Object InterfaceIndex, Name, InterfaceDescription, Status, Virtual, HardwareInterface }
  routes = Section { Get-NetRoute -AddressFamily IPv4 -DestinationPrefix 0.0.0.0/0 |
    Select-Object InterfaceIndex, NextHop }
  profiles = Section { Get-NetConnectionProfile |
    Select-Object Name, InterfaceAlias, InterfaceIndex, @{n='NetworkCategory'; e={"$($_.NetworkCategory)"}} }
  firewall = Section { Get-NetFirewallProfile -PolicyStore ActiveStore |
    Select-Object Name, @{n='Enabled'; e={"$($_.Enabled)"}}, @{n='DefaultInboundAction'; e={"$($_.DefaultInboundAction)"}},
      @{n='AllowInboundRules'; e={"$($_.AllowInboundRules)"}} }
  rules = Section { Get-NetFirewallApplicationFilter -PolicyStore ActiveStore |
    Where-Object { $_.Program -like '*\python*.exe' } | ForEach-Object {
      $program = $_.Program
      $_ | Get-NetFirewallRule | ForEach-Object {
        $ports = $_ | Get-NetFirewallPortFilter
        [pscustomobject]@{ Name = $_.Name; DisplayName = $_.DisplayName; Program = $program; Enabled = "$($_.Enabled)";
          Direction = "$($_.Direction)"; Action = "$($_.Action)"; Profile = "$($_.Profile)";
          Protocol = "$($ports.Protocol)"; LocalPort = @($ports.LocalPort) } } } }
  products = Section { Get-CimInstance -Namespace root/SecurityCenter2 -ClassName FirewallProduct |
    ForEach-Object { $_.displayName } }
}
$json = ConvertTo-Json -InputObject $survey -Depth 5 -Compress
[regex]::Replace($json, '[^\x00-\x7F]', { param($m) '\u{0:x4}' -f [int][char]$m.Value })
"""
SECTIONS = ("addresses", "adapters", "routes", "profiles", "firewall", "rules", "products")


class _Pending:
    def __repr__(self):
        return "PENDING"


PENDING = _Pending()  # not measured yet


@dataclass
class Step:
    title: str
    state: str          # OK, PROBLEM, NOTE, WAIT, UNKNOWN or CHECKING
    found: str          # what the check found
    fix: str = ""       # what to do about it
    action: str = None  # a button for it: "allow-python"


@dataclass
class Address:
    ip: str
    adapter: str = ""        # the connection's name in Windows, e.g. "Wi-Fi"
    description: str = ""    # the hardware, e.g. "Intel(R) Wi-Fi 6E AX210 160MHz"
    index: int = None        # Windows' interface index
    hardware: bool = None    # a network card, not software; None: not known
    default_route: bool = False  # Windows sends traffic for the internet this way

    @property
    def kind(self):
        """("", "") for an address a phone on the same network can reach, else (kind, what it belongs to): "vpn",
        "virtual", "hotspot" (reachable only from the PC's own mobile hotspot) or "no-router"."""
        if self.ip.startswith("169.254."):
            return "no-router", "no address from the router"
        text = f"{self.adapter} {self.description}".casefold()
        kind, what = next(((kind, what) for word, kind, what in ADAPTER_WORDS if word.casefold() in text), ("", ""))
        if not kind and ipaddress.ip_address(self.ip) in ipaddress.ip_network("100.64.0.0/10"):
            kind, what = "vpn", "probably a VPN (such as Tailscale)"
        if not kind and self.hardware is False:
            kind, what = "virtual", "a virtual adapter"
        if kind == "virtual" and self.default_route:
            return "", ""  # the PC's own connection runs through it, as with a Hyper-V external switch
        return kind, what

    @property
    def name(self):
        """The address with where it is, for a sentence: '192.168.1.50 (Wi-Fi)'."""
        return f"{self.ip} ({self.adapter})" if self.adapter else self.ip


@dataclass
class Findings:
    """What gather() measured; PENDING until it has."""
    port: int
    listen: str               # the address the companion listens on: 0.0.0.0 is every one
    program: str              # the program file that runs the companion
    default_ip: str = None    # the address Windows sends from, the one the startup message names
    local: object = PENDING   # 127.0.0.1: None if it answers, else why not
    survey: object = PENDING  # parse_survey(): None if PowerShell gave nothing
    addresses: object = PENDING  # [Address], the most likely one for the phone first
    lan: object = PENDING     # [(ip, None if it answers, else why not)]
    why_no_survey: str = ""


def parse_survey(text):
    """The survey's sections, each a list or None if it failed; None if there is no survey at all."""
    try:
        survey = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(survey, dict):
        return None
    # ConvertTo-Json writes a list of one as the thing itself.
    return {key: [survey[key]] if isinstance(survey.get(key), (dict, str)) else survey.get(key) for key in SECTIONS}


def addresses_from(survey, default_ip=None):
    """The PC's IPv4 addresses with their adapters, from the survey, loopback left out, the most likely one for the
    phone first; None without the survey."""
    if not survey or survey["addresses"] is None:
        return None
    adapters = {row.get("InterfaceIndex"): row for row in survey["adapters"] or []}
    routed = {row.get("InterfaceIndex") for row in survey["routes"] or []}
    found = []
    for row in survey["addresses"]:
        ip = row.get("IPAddress")
        if not ip or ip.startswith("127."):
            continue
        adapter = adapters.get(row.get("InterfaceIndex"), {})
        hardware = adapter.get("HardwareInterface")
        found.append(Address(ip=ip, adapter=row.get("InterfaceAlias") or adapter.get("Name") or "",
                             description=adapter.get("InterfaceDescription") or "", index=row.get("InterfaceIndex"),
                             hardware=hardware if isinstance(hardware, bool) else None,
                             default_route=row.get("InterfaceIndex") in routed))
    return rank(found, default_ip)


def plain_addresses(default_ip=None, resolve=socket.gethostbyname_ex):
    """The PC's IPv4 addresses without PowerShell: what its name resolves to, and the one Windows sends from."""
    try:
        ips = resolve(socket.gethostname())[2]
    except OSError:
        ips = []
    found = [Address(ip=ip, default_route=ip == default_ip)
             for ip in dict.fromkeys([*([default_ip] if default_ip else []), *ips]) if not ip.startswith("127.")]
    return rank(found, default_ip)


def rank(addresses, default_ip=None):
    """Real adapters first, then the PC's hotspot, then virtual ones and VPNs; among equals, the one with the route
    to the internet, then the one Windows sends from, then home-network addresses."""
    def key(address):
        private = ipaddress.ip_address(address.ip).is_private
        return RANK[address.kind[0]], not address.default_route, address.ip != default_ip, not private
    return sorted(addresses, key=key)


def from_phone(peer, own):
    """Whether a visit to /check came from elsewhere, not from this PC checking itself."""
    try:
        return not ipaddress.ip_address(peer).is_loopback and peer not in own
    except ValueError:
        return False


def same_program(a, b):
    """Windows paths compare without regard to case, and firewall rules may hold %variables%."""
    def norm(path):
        return ntpath.normcase(ntpath.normpath(ntpath.expandvars(path or "")))
    return norm(a) == norm(b)


def _named(value, names):
    """An enum from the survey as its name; numbers too, for output of an older or hand-run survey."""
    return names.get(value, str(value)) if isinstance(value, int) and not isinstance(value, bool) else str(value)


def firewall_profile(category):
    """The Windows Firewall profile for a network category: Public, Private or Domain."""
    category = _named(category, {0: "Public", 1: "Private", 2: "DomainAuthenticated"})
    return {"DomainAuthenticated": "Domain"}.get(category, category)


def _has_profile(value, profile):
    if isinstance(value, int) and not isinstance(value, bool):
        return value == 0 or bool(value & {"Domain": 1, "Private": 2, "Public": 4}.get(profile, 0))
    names = [name.strip() for name in str(value).split(",")]
    return "Any" in names or profile in names


def _covers_port(rule, port):
    if str(rule.get("Protocol", "Any")) not in ("TCP", "Any", "6", ""):
        return False
    ports = rule.get("LocalPort", ["Any"])
    for entry in ports if isinstance(ports, list) else [ports]:
        entry = str(entry)
        low, _, high = entry.partition("-")
        if entry == "Any" or (low.isdigit() and int(low) <= port <= int(high if high.isdigit() else low)):
            return True
    return False


def _true(value):
    return value is True or value == 1 or str(value) == "True"


def firewall_verdict(survey, program, port, profile):
    """What Windows Firewall does with a phone that connects to program on port, on a network with this profile
    ("Private", "Public" or "Domain"), as (verdict, detail):
      "open"          the firewall is off for the profile, or lets everything in; detail: other firewalls installed
      "shielded"      it blocks everything, allowed apps too
      "blocked"       a rule blocks program; detail: the rule's name
      "allowed"       a rule lets program in; detail: the rule's name
      "elsewhere"     rules let program in on other profiles only; detail: those profiles
      "other-python"  no rule for program, but some for another python.exe; detail: its path
      "missing"       no rule at all
      "unknown"       the survey has no firewall information
    """
    if not survey or survey["firewall"] is None or survey["rules"] is None:
        return "unknown", None
    settings = next((row for row in survey["firewall"] if row.get("Name") == profile), None)
    if settings:
        if _named(settings.get("Enabled"), {0: "False", 1: "True"}) == "False":
            return "open", [name for name in survey["products"] or [] if isinstance(name, str)]
        if str(_named(settings.get("DefaultInboundAction"), {2: "Allow", 4: "Block"})) == "Allow":
            return "open", []
        if _named(settings.get("AllowInboundRules"), {0: "False", 1: "True"}) == "False":
            return "shielded", None
    inbound = _inbound_rules(survey, program, port)
    here = [rule for rule in inbound if _has_profile(rule.get("Profile", "Any"), profile)]
    for action, verdict in (("Block", "blocked"), ("Allow", "allowed")):  # a block rule wins over an allow rule
        rule = next((rule for rule in here if _action(rule) == action), None)
        if rule:
            return verdict, rule.get("DisplayName") or "(no name)"
    elsewhere = sorted({name for rule in inbound if _action(rule) == "Allow"
                        for name in ("Domain", "Private", "Public") if _has_profile(rule.get("Profile", "Any"), name)})
    if elsewhere:
        return "elsewhere", elsewhere
    others = [rule.get("Program") for rule in survey["rules"]
              if rule.get("Program") and not same_program(rule.get("Program"), program)]
    if others:
        return "other-python", others[0]
    return "missing", None


def _action(rule):
    return _named(rule.get("Action"), {2: "Allow", 4: "Block"})


def _inbound_rules(survey, program, port):
    """program's enabled inbound rules that cover TCP connections to port."""
    return [rule for rule in survey["rules"] if same_program(rule.get("Program"), program)
            and _true(_named(rule.get("Enabled"), {1: "True", 2: "False"}))
            and _named(rule.get("Direction"), {1: "Inbound", 2: "Outbound"}) == "Inbound" and _covers_port(rule, port)]


def best_address(found):
    return found.addresses[0] if isinstance(found.addresses, list) and found.addresses else None


def connection_profile(survey, address):
    """The network profile row of the address's connection (or of the only connection), or None."""
    if not survey or not survey["profiles"]:
        return None
    rows = survey["profiles"]
    if address is not None:
        for row in rows:
            if row.get("InterfaceIndex") == address.index:
                return row
    return rows[0] if len(rows) == 1 else None


def local_step(found):
    title = "The companion on this PC"
    url = f"http://127.0.0.1:{found.port}"
    if found.local is PENDING:
        return Step(title, CHECKING, f"Asking {url} ...")
    if found.local is None:
        return Step(title, OK, f"It answers at {url}.")
    return Step(title, PROBLEM, f"It doesn't answer at {url} ({found.local}).",
                "It may have stopped: the log below says why. Close this window and start Start Companion.py again.")


def lan_step(found):
    title = "On the network"
    if found.listen in ("127.0.0.1", "localhost") or found.listen.startswith("127."):
        return Step(title, PROBLEM, f"companion.ini has listen = {found.listen}: only this PC can open the page.",
                    "Open TownfallCompanion\\companion.ini in Notepad, change it to listen = 0.0.0.0, save it, and "
                    "start the companion again.")
    if found.local not in (PENDING, None):
        return Step(title, UNKNOWN, "Not checked: the companion doesn't answer on this PC (above).")
    if found.lan is PENDING:
        return Step(title, CHECKING, "Asking the PC's network addresses ...")
    if not found.lan:
        return Step(title, PROBLEM, "This PC has no network address, so no phone can reach it.",
                    "Connect the PC to the router the phone uses, by Wi-Fi or cable.")
    if found.listen != "0.0.0.0":
        own = [ip for ip, _ in found.lan]
        if found.listen not in own:
            return Step(title, PROBLEM, f"companion.ini has listen = {found.listen}, which isn't an address of this PC "
                        f"(it has {', '.join(own)}).",
                        "Set listen = 0.0.0.0 in TownfallCompanion\\companion.ini and start the companion again.")
    failed = [(ip, why) for ip, why in found.lan if why and found.listen in ("0.0.0.0", ip)]
    if failed:
        ip, why = failed[0]
        return Step(title, PROBLEM, f"It doesn't answer at http://{ip}:{found.port}, not even from this PC ({why}).",
                    "Close this window and start the companion again. If that doesn't help, a security program may "
                    "hold the port: set another one in companion.ini, e.g. port = 18790.")
    answering = [ip for ip, why in found.lan if not why]
    if found.listen != "0.0.0.0":
        return Step(title, OK, f"It listens only at {found.listen} (listen in companion.ini), and answers there.")
    return Step(title, OK, f"It answers at {', '.join(answering)} from this PC too: it listens on the network.")


def adapter_step(found):
    title = "Network adapters"
    if found.addresses is PENDING:
        return Step(title, CHECKING, "Asking Windows ...")
    if not found.addresses:
        return Step(title, PROBLEM, "This PC has no network address besides 127.0.0.1 (the PC itself).",
                    "Connect the PC to the router the phone uses, by Wi-Fi or cable.")
    best = found.addresses[0]
    kind, what = best.kind
    lines = []
    for other in found.addresses[1:]:
        other_kind, other_what = other.kind
        if other_kind == "hotspot":
            lines.append(f"{other.name} is {other_what}: only for a phone connected to that hotspot.")
        elif other_kind:
            lines.append(f"Not {other.name}: it belongs to {other_what}, which phones can't reach.")
        else:
            lines.append(f"{other.name} is another network of this PC: use it if the phone is on that one.")
    banner = next((address for address in found.addresses if address.ip == found.default_ip), None)
    if banner is not None and banner is not best:
        lines.append(f"The startup message in the log names {banner.ip}; {best.ip} is the better choice.")
    if found.survey is None:
        lines.append(f"Adapter names: not available ({found.why_no_survey or 'PowerShell gave no answer'}).")
    described = ", ".join(part for part in (best.adapter, best.description) if part)
    vpn =next((address for address in found.addresses if address.kind[0] == "vpn" and address.default_route), None)
    if kind == "no-router":
        return Step(title, PROBLEM, f"{best.name} has no address from the router (169.254...).",
                    "Reconnect the Wi-Fi or the cable, or restart the router.")
    if kind == "hotspot":
        return Step(title, NOTE, "\n".join([f"The only address is {best.ip}, the PC's own mobile hotspot.", *lines]),
                    "That works only for a phone connected to the PC's hotspot. Otherwise connect the PC to the "
                    "router the phone uses.")
    if kind:
        return Step(title, PROBLEM, "\n".join([f"The only address is {best.name}: it belongs to {what}.", *lines]),
                    "Connect the PC to the router the phone uses, by Wi-Fi or cable. If a VPN is on, turn it off "
                    "while you play, or allow local network access in its settings.")
    fix = (f"A VPN is on ({vpn.adapter}): if the phone can't connect, turn it off while you play, or allow local "
           f"network access in its settings." if vpn else "")
    use = f"Use {best.ip} ({described})." if described else f"Use {best.ip}."
    return Step(title, NOTE if vpn else OK, "\n".join([use, *lines]), fix)


def network_profile(found):
    """The Windows Firewall profile of the network the phone should use: "Private", "Public" or "Domain". Windows
    lets programs in by the profile, and either works once Python is allowed in on it."""
    row = connection_profile(found.survey, best_address(found)) if found.survey is not PENDING else None
    return firewall_profile(row.get("NetworkCategory")) if row else "Private"


def firewall_step(found, reached=False):
    """reached: a phone got through, which settles what the firewall lets in, whatever its rules seem to say."""
    title = "Windows Firewall"
    if found.survey is PENDING:
        return Step(title, CHECKING, "Asking Windows ...")
    profile = network_profile(found)
    verdict, detail = firewall_verdict(found.survey, found.program, found.port, profile)
    python = f"Python ({found.program})"
    allow = f"Click the button: Windows asks for permission, then its firewall lets Python in on {profile} networks."
    if reached and verdict != "allowed" and not (verdict == "open" and not detail):
        return Step(title, OK, "A phone got through: the firewall lets it in.")
    if verdict == "unknown":
        return Step(title, UNKNOWN, "Not available: Windows didn't list its firewall settings.")
    if verdict == "open":
        if detail:
            return Step(title, NOTE, f"Windows Firewall is off for {profile} networks; {', '.join(detail)} looks "
                        "after the firewall instead.",
                        f"If the phone can't connect, allow {python} in {detail[0]}.")
        return Step(title, OK, f"Windows Firewall lets everything in on {profile} networks.")
    if verdict == "shielded":
        return Step(title, PROBLEM, f"Windows Firewall blocks every incoming connection on {profile} networks, "
                    "allowed apps too.",
                    f"Windows Security → Firewall & network protection → {profile} network: untick \"Blocks all "
                    "incoming connections, including those in the list of allowed apps\".")
    if verdict == "blocked":
        return Step(title, PROBLEM, f"A firewall rule named \"{detail}\" blocks {python} on {profile} networks. "
                    "Windows makes one when its question about Python is answered with Cancel, or for a kind of "
                    "network left unticked there.",
                    "Click the button: Windows asks for permission, then its firewall turns that rule off and lets "
                    f"Python in on {profile} networks.", "allow-python")
    if verdict == "allowed":
        return Step(title, OK, f"{python} may receive connections on {profile} networks (rule \"{detail}\").")
    if verdict == "elsewhere":
        return Step(title, PROBLEM, f"{python} is allowed in on {' and '.join(detail)} networks only, and Windows "
                    f"counts this network as {profile}.", allow, "allow-python")
    if verdict == "other-python":
        return Step(title, PROBLEM, f"The firewall's rules are for another Python ({detail}), not for the one "
                    f"that runs the companion ({found.program}).", allow, "allow-python")
    return Step(title, PROBLEM, f"No firewall rule lets {python} in on {profile} networks.", allow, "allow-python")


def phone_address(found):
    """The address the phone should open: the one the companion listens on, or the PC's most likely one."""
    if found.listen != "0.0.0.0" and not found.listen.startswith("127."):
        return found.listen
    best = best_address(found)
    return best.ip if best else None


def phone_step(found, pc_side, visit=None):
    """pc_side: the other steps; visit: (ip, "14:02:31") of the latest phone that got through to the companion."""
    title = "Phone test"
    if visit:
        return Step(title, OK, f"A phone reached the PC from {visit[0]} at {visit[1]}.")
    address = phone_address(found)
    target = f"http://{address}:{found.port}/check" if address else "/check at the address above"
    if any(step.state == CHECKING for step in pc_side):
        fix = ""
    elif any(step.state == PROBLEM for step in pc_side):
        fix = "Fix what the steps above say first; then try again."
    else:
        fix = ("Everything on the PC is ready. If the phone shows an error or keeps loading, the cause lies between "
               "the phone and the router. " + NETWORK_ADVICE)
    return Step(title, WAIT, f"On the phone, open the address above or scan its QR code, or open {target}, which "
                "needs no PIN. This line changes when the phone gets through.", fix)


def steps(found, visit=None):
    """visit: (ip, "14:02:31") of the latest phone that got through to the companion (its page, the PIN page or
    /check), or None. Once one has, the way from the phone works, whatever the firewall's rules seem to say."""
    pc_side = [local_step(found), lan_step(found), adapter_step(found), firewall_step(found, reached=bool(visit))]
    return [*pc_side, phone_step(found, pc_side, visit)]


def probe(url, timeout=3.0):
    """None if the companion answers at url, else why not, in a few words. Never through a proxy: one set up for
    the internet must not stand in for the PC's own network."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as response:
            response.read(65536)
        return None
    except urllib.error.HTTPError:
        return None  # an answer, if not a happy one
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, ConnectionRefusedError):
            return "connection refused"
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return f"no answer within {timeout:g} s"
        return str(reason)


def system_program(*parts):
    """A program in Windows' System32 folder, by its full path: never one that happens to be on the PATH first."""
    return os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", *parts)


def powershell():
    program = system_program("WindowsPowerShell", "v1.0", "powershell.exe")
    return program if os.path.isfile(program) else "powershell"


def encoded(script):
    """A PowerShell script as -EncodedCommand takes it: no quoting to get wrong on the way."""
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def survey(run=subprocess.run):
    """(the PowerShell survey's text, why there is none)."""
    try:
        result = run([powershell(), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded(SURVEY)],
                     capture_output=True, timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except FileNotFoundError:
        return None, "PowerShell isn't on this system"
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"PowerShell failed: {exc}"
    text = result.stdout.decode("ascii", errors="replace") if isinstance(result.stdout, bytes) else result.stdout
    return text, "" if text and text.strip() else f"PowerShell gave no answer (exit code {result.returncode})"


def gather(found, run=subprocess.run, fetch=probe, progress=lambda found: None):
    """Measures everything into found (a Findings), calling progress with a copy after each part."""
    found.local = fetch(f"http://127.0.0.1:{found.port}/check")
    progress(dataclasses.replace(found))
    text, found.why_no_survey = survey(run)
    found.survey = parse_survey(text)
    found.addresses = addresses_from(found.survey, found.default_ip) or plain_addresses(found.default_ip)
    progress(dataclasses.replace(found))
    found.lan = [(address.ip, fetch(f"http://{address.ip}:{found.port}/check")) for address in found.addresses]
    progress(dataclasses.replace(found))
    return found


def this_program():
    """The program file this process runs, as Windows sees it. A venv's python.exe only starts the real one,
    which is the one that listens and the one the firewall judges."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                        ctypes.POINTER(wintypes.DWORD))
        length = wintypes.DWORD(32768)
        path = ctypes.create_unicode_buffer(length.value)
        if kernel32.QueryFullProcessImageNameW(kernel32.GetCurrentProcess(), 0, path, ctypes.byref(length)):
            return path.value
    return sys.executable


def allow_rule(found):
    """What the Allow button runs after Windows' permission prompt, as (program, arguments): PowerShell turns off the
    rules that block the companion's Python on the kind of network the PC is on, and adds one that lets it in there.
    The network itself stays as it is."""
    profile = network_profile(found)
    blocking = [rule["Name"] for rule in _inbound_rules(found.survey, found.program, found.port)
                if _action(rule) == "Block" and _has_profile(rule.get("Profile", "Any"), profile) and rule.get("Name")] \
        if found.survey else []
    quoted = lambda text: "'" + str(text).replace("'", "''") + "'"
    script = "\n".join([
        "$ErrorActionPreference = 'Stop'",
        "try {",
        *(f"  Disable-NetFirewallRule -Name {quoted(name)}" for name in blocking),
        f"  New-NetFirewallRule -DisplayName {quoted(RULE_NAME)} -Direction Inbound -Action Allow -Protocol TCP "
        f"-Program {quoted(found.program)} -Profile {profile} "
        f"-Description {quoted('Lets the phone open the Townfall Companion page.')} | Out-Null",
        "  exit 0",
        "} catch { exit 1 }",
    ])
    return powershell(), f"-NoProfile -NonInteractive -EncodedCommand {encoded(script)}"


def run_elevated(program, arguments, wait_s=120):
    """Runs program after Windows' permission prompt (UAC) and waits for it: its exit code, or None if the prompt
    was declined. Windows only."""
    import ctypes
    from ctypes import wintypes

    class ShellExecuteInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", wintypes.ULONG), ("hwnd", wintypes.HWND),
                    ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                    ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
                    ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                    ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = (ctypes.POINTER(ShellExecuteInfo),)
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    see_mask_nocloseprocess, sw_hide, error_cancelled = 0x40, 0, 1223
    info = ShellExecuteInfo(cbSize=ctypes.sizeof(ShellExecuteInfo), fMask=see_mask_nocloseprocess, lpVerb="runas",
                            lpFile=program, lpParameters=arguments, nShow=sw_hide)
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        error = ctypes.get_last_error()
        if error == error_cancelled:
            return None
        raise ctypes.WinError(error)
    if not info.hProcess:
        return 0  # nothing to wait for: the check that follows says whether it worked
    try:
        kernel32.WaitForSingleObject(info.hProcess, wait_s * 1000)
        code = wintypes.DWORD()
        kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        return code.value
    finally:
        kernel32.CloseHandle(info.hProcess)
