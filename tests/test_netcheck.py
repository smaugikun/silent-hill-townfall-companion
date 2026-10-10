"""The connection check (companion/netcheck.py) on made-up PowerShell surveys and probe results: no Windows tools,
no network and no screen needed."""
import base64
import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "TownfallCompanion" / "companion"))
import netcheck  # noqa: E402

PYTHON = r"C:\Users\player\AppData\Local\Programs\Python\Python312\python.exe"
OTHER_PYTHON = r"C:\Program Files\Python311\python.exe"


def rule(action, profile, protocol="TCP", program=PYTHON.lower(), ports=("Any",), enabled="True", name="python.exe"):
    # Windows keeps the program of the rules its firewall question makes in lower case.
    return {"DisplayName": name, "Program": program, "Enabled": enabled, "Direction": "Inbound", "Action": action,
            "Profile": profile, "Protocol": protocol, "LocalPort": list(ports)}


def firewall(enabled="True", inbound="Block", rules_too="True"):
    return [{"Name": name, "Enabled": enabled, "DefaultInboundAction": inbound, "AllowInboundRules": rules_too}
            for name in ("Domain", "Private", "Public")]


# A PC on Wi-Fi at home, with WSL installed, that allowed Python on private networks when Windows asked.
HOME = {
    "addresses": [
        {"InterfaceIndex": 31, "InterfaceAlias": "vEthernet (WSL (Hyper-V firewall))", "IPAddress": "172.27.48.1"},
        {"InterfaceIndex": 12, "InterfaceAlias": "Wi-Fi", "IPAddress": "192.168.178.23"},
        {"InterfaceIndex": 1, "InterfaceAlias": "Loopback Pseudo-Interface 1", "IPAddress": "127.0.0.1"},
    ],
    "adapters": [
        {"InterfaceIndex": 12, "Name": "Wi-Fi", "InterfaceDescription": "Intel(R) Wi-Fi 6 AX201 160MHz", "Status": "Up",
         "Virtual": False, "HardwareInterface": True},
        {"InterfaceIndex": 31, "Name": "vEthernet (WSL (Hyper-V firewall))",
         "InterfaceDescription": "Hyper-V Virtual Ethernet Adapter", "Status": "Up", "Virtual": True,
         "HardwareInterface": False},
    ],
    "routes": [{"InterfaceIndex": 12, "NextHop": "192.168.178.1"}],
    "profiles": [{"Name": "FRITZ!Box 7590", "InterfaceAlias": "Wi-Fi", "InterfaceIndex": 12, "NetworkCategory": "Private"}],
    "firewall": firewall(),
    "rules": [rule("Allow", "Private"), rule("Allow", "Private", "UDP"), rule("Block", "Public"),
              rule("Block", "Public", "UDP")],
    "products": [],
}


def survey(**sections):
    """The home PC's survey as PowerShell writes it, with some sections replaced."""
    return json.dumps({**HOME, **sections})


def measured(text=None, listen="0.0.0.0", local=None, lan=None, default_ip="192.168.178.23", program=PYTHON):
    """Findings as gather() leaves them: by default everything answers."""
    found = netcheck.Findings(port=8790, listen=listen, program=program, default_ip=default_ip)
    found.local = local
    found.survey = netcheck.parse_survey(survey() if text is None else text)
    found.addresses = netcheck.addresses_from(found.survey, default_ip) or []
    found.lan = [(address.ip, None) for address in found.addresses] if lan is None else lan
    return found


def step(found, title, **kwargs):
    return next(s for s in netcheck.steps(found, **kwargs) if s.title == title)


class AddressTest(unittest.TestCase):
    def test_addresses_come_with_their_adapters_and_loopback_is_left_out(self):
        addresses = measured().addresses
        self.assertEqual([a.ip for a in addresses], ["192.168.178.23", "172.27.48.1"])
        wifi = addresses[0]
        self.assertEqual((wifi.adapter, wifi.description, wifi.hardware, wifi.default_route),
                         ("Wi-Fi", "Intel(R) Wi-Fi 6 AX201 160MHz", True, True))

    def test_the_real_adapter_comes_first_and_a_virtual_one_is_named(self):
        found = measured(default_ip="172.27.48.1")  # even when Windows sends from the virtual one
        self.assertEqual(found.addresses[0].ip, "192.168.178.23")
        adapters = step(found, "Network adapters")
        self.assertEqual(adapters.state, netcheck.OK)
        self.assertIn("Use 192.168.178.23 (Wi-Fi, Intel(R) Wi-Fi 6 AX201 160MHz).", adapters.found)
        self.assertIn("Not 172.27.48.1 (vEthernet (WSL (Hyper-V firewall))): it belongs to WSL", adapters.found)
        self.assertIn("The startup message in the log names 172.27.48.1; 192.168.178.23 is the better choice.",
                      adapters.found)

    def test_a_vpn_is_named_and_the_wifi_still_comes_first(self):
        text = survey(
            addresses=[*HOME["addresses"], {"InterfaceIndex": 40, "InterfaceAlias": "NordLynx", "IPAddress": "10.5.0.2"}],
            adapters=[*HOME["adapters"], {"InterfaceIndex": 40, "Name": "NordLynx",
                                          "InterfaceDescription": "NordLynx Tunnel", "HardwareInterface": False}],
            routes=[{"InterfaceIndex": 40, "NextHop": "0.0.0.0"}, *HOME["routes"]])
        found = measured(text, default_ip="10.5.0.2")
        self.assertEqual([a.ip for a in found.addresses], ["192.168.178.23", "10.5.0.2", "172.27.48.1"])
        adapters = step(found, "Network adapters")
        self.assertEqual(adapters.state, netcheck.NOTE)
        self.assertIn("Not 10.5.0.2 (NordLynx): it belongs to NordVPN", adapters.found)
        self.assertIn("A VPN is on (NordLynx)", adapters.fix)

    def test_a_virtual_switch_that_carries_the_pcs_connection_is_not_flagged(self):
        # A Hyper-V external switch: the network card has no address of its own, the switch has the router's.
        text = survey(
            addresses=[{"InterfaceIndex": 7, "InterfaceAlias": "vEthernet (External)", "IPAddress": "192.168.1.40"}],
            adapters=[{"InterfaceIndex": 7, "Name": "vEthernet (External)",
                       "InterfaceDescription": "Hyper-V Virtual Ethernet Adapter #2", "HardwareInterface": False}],
            routes=[{"InterfaceIndex": 7, "NextHop": "192.168.1.1"}], profiles=[])
        adapters = step(measured(text, default_ip="192.168.1.40"), "Network adapters")
        self.assertEqual((adapters.state, adapters.found.splitlines()[0]),
                         (netcheck.OK, "Use 192.168.1.40 (vEthernet (External), Hyper-V Virtual Ethernet Adapter #2)."))

    def test_only_a_virtual_address_is_a_problem(self):
        text = survey(
            addresses=[{"InterfaceIndex": 9, "InterfaceAlias": "Ethernet 2", "IPAddress": "192.168.56.1"}],
            adapters=[{"InterfaceIndex": 9, "Name": "Ethernet 2",
                       "InterfaceDescription": "VirtualBox Host-Only Ethernet Adapter", "HardwareInterface": False}],
            routes=[], profiles=[])
        adapters = step(measured(text, default_ip=None), "Network adapters")
        self.assertEqual(adapters.state, netcheck.PROBLEM)
        self.assertIn("it belongs to VirtualBox", adapters.found)
        self.assertIn("Connect the PC to the router", adapters.fix)

    def test_an_address_the_router_didnt_give_is_a_problem(self):
        text = survey(addresses=[{"InterfaceIndex": 12, "InterfaceAlias": "Wi-Fi", "IPAddress": "169.254.10.20"}],
                      routes=[])
        adapters = step(measured(text, default_ip=None), "Network adapters")
        self.assertEqual(adapters.state, netcheck.PROBLEM)
        self.assertIn("no address from the router", adapters.found)

    def test_a_single_row_is_read_as_a_list(self):
        # ConvertTo-Json writes a list of one as the thing itself.
        text = survey(addresses={"InterfaceIndex": 12, "InterfaceAlias": "Wi-Fi", "IPAddress": "192.168.178.23"},
                      profiles=HOME["profiles"][0], rules=rule("Allow", "Private"))
        found = measured(text)
        self.assertEqual([a.ip for a in found.addresses], ["192.168.178.23"])
        self.assertEqual(step(found, "Windows Firewall").state, netcheck.OK)

    def test_without_powershell_the_pcs_addresses_still_come(self):
        resolve = lambda name: (name, [], ["127.0.0.1", "192.168.0.7", "172.20.0.1"])
        addresses = netcheck.plain_addresses("192.168.0.7", resolve=resolve)
        self.assertEqual([a.ip for a in addresses], ["192.168.0.7", "172.20.0.1"])
        found = netcheck.Findings(port=8790, listen="0.0.0.0", program=PYTHON, default_ip="192.168.0.7", local=None,
                                  survey=None, addresses=addresses, lan=[(a.ip, None) for a in addresses],
                                  why_no_survey="PowerShell isn't on this system")
        steps = {s.title: s for s in netcheck.steps(found)}
        self.assertIn("Adapter names: not available (PowerShell isn't on this system).", steps["Network adapters"].found)
        self.assertEqual(steps["Network profile"].state, netcheck.UNKNOWN)
        self.assertEqual(steps["Windows Firewall"].state, netcheck.UNKNOWN)
        self.assertIn("Not available", steps["Windows Firewall"].found)

    def test_a_visit_from_this_pc_is_not_a_phone(self):
        own = {"192.168.178.23", "172.27.48.1"}
        self.assertFalse(netcheck.from_phone("127.0.0.1", own))
        self.assertFalse(netcheck.from_phone("192.168.178.23", own))
        self.assertTrue(netcheck.from_phone("192.168.178.40", own))
        self.assertFalse(netcheck.from_phone("not an address", own))


class ListenTest(unittest.TestCase):
    def test_everything_answering_is_ok(self):
        found = measured()
        self.assertEqual([s.state for s in netcheck.steps(found)][:5], [netcheck.OK] * 5)
        self.assertIn("It answers at 192.168.178.23, 172.27.48.1 from this PC too", step(found, "On the network").found)

    def test_listen_127_is_named_with_the_line_to_change(self):
        found = measured(listen="127.0.0.1", lan=[("192.168.178.23", "connection refused")])
        network = step(found, "On the network")
        self.assertEqual(network.state, netcheck.PROBLEM)
        self.assertIn("listen = 127.0.0.1: only this PC can open the page", network.found)
        self.assertIn("listen = 0.0.0.0", network.fix)

    def test_an_address_that_doesnt_answer_even_from_the_pc(self):
        found = measured(lan=[("192.168.178.23", "connection refused"), ("172.27.48.1", None)])
        network = step(found, "On the network")
        self.assertEqual(network.state, netcheck.PROBLEM)
        self.assertIn("It doesn't answer at http://192.168.178.23:8790, not even from this PC (connection refused).",
                      network.found)

    def test_a_listen_address_the_pc_doesnt_have(self):
        network = step(measured(listen="192.168.0.99"), "On the network")
        self.assertEqual(network.state, netcheck.PROBLEM)
        self.assertIn("listen = 192.168.0.99, which isn't an address of this PC", network.found)

    def test_a_companion_that_doesnt_answer_at_all(self):
        found = measured(local="connection refused", lan=[("192.168.178.23", "connection refused")])
        self.assertEqual(step(found, "The companion on this PC").state, netcheck.PROBLEM)
        self.assertEqual(step(found, "On the network").state, netcheck.UNKNOWN)  # said once, above

    def test_while_measuring_the_steps_say_so(self):
        found = netcheck.Findings(port=8790, listen="0.0.0.0", program=PYTHON)
        self.assertEqual({s.state for s in netcheck.steps(found)[:5]}, {netcheck.CHECKING})
        self.assertEqual(netcheck.steps(found)[5].fix, "")


class ProfileTest(unittest.TestCase):
    def test_a_private_network_is_fine(self):
        profile = step(measured(), "Network profile")
        self.assertEqual((profile.state, profile.found),
                         (netcheck.OK, "\"FRITZ!Box 7590\" (Wi-Fi) is a Private network in Windows."))

    def test_a_public_network_is_named_as_the_cause(self):
        text = survey(profiles=[{**HOME["profiles"][0], "NetworkCategory": "Public"}])
        found = measured(text)
        profile = step(found, "Network profile")
        self.assertEqual((profile.state, profile.action), (netcheck.PROBLEM, "wifi-settings"))
        self.assertIn("Windows treats \"FRITZ!Box 7590\" (Wi-Fi) as a Public network", profile.found)
        self.assertIn("Wi-Fi → FRITZ!Box 7590 properties → Network profile type: Private", profile.fix)
        # Allowed on Private, blocked on Public, as Windows' question leaves it: the network is what to change.
        wall = step(found, "Windows Firewall")
        self.assertEqual((wall.state, wall.action), (netcheck.PROBLEM, None))
        self.assertIn("A firewall rule named \"python.exe\" blocks", wall.found)
        self.assertIn("Make the network Private", wall.fix)
        allowed_on_private_only = step(measured(survey(profiles=[{**HOME["profiles"][0], "NetworkCategory": "Public"}],
                                                       rules=[rule("Allow", "Private")])), "Windows Firewall")
        self.assertEqual((allowed_on_private_only.state, allowed_on_private_only.action), (netcheck.PROBLEM, None))
        self.assertIn("allowed in on Private networks only, and this network is Public", allowed_on_private_only.found)

    def test_a_public_network_where_python_is_allowed_is_fine_as_it_is(self):
        text = survey(
            addresses=[{"InterfaceIndex": 5, "InterfaceAlias": "Ethernet", "IPAddress": "192.168.1.20"}],
            adapters=[{"InterfaceIndex": 5, "Name": "Ethernet", "InterfaceDescription": "Realtek PCIe GbE Family Controller",
                       "HardwareInterface": True}],
            routes=[{"InterfaceIndex": 5, "NextHop": "192.168.1.1"}],
            profiles=[{"Name": "Network", "InterfaceAlias": "Ethernet", "InterfaceIndex": 5, "NetworkCategory": "Public"}],
            rules=[rule("Allow", "Public")])
        found = measured(text, default_ip="192.168.1.20")
        profile = step(found, "Network profile")
        self.assertEqual((profile.state, profile.fix, profile.action), (netcheck.OK, "", None))
        self.assertIn("Python is allowed in on Public networks: the phone gets through", profile.found)
        self.assertEqual(step(found, "Windows Firewall").state, netcheck.OK)

    def test_a_work_network_is_left_to_its_administrator(self):
        text = survey(profiles=[{**HOME["profiles"][0], "NetworkCategory": "DomainAuthenticated"}])
        self.assertIn("work (domain) network", step(measured(text), "Network profile").found)

    def test_enum_numbers_are_understood_too(self):
        # The survey asks for names; numbers are what ConvertTo-Json writes without that.
        text = survey(profiles=[{**HOME["profiles"][0], "NetworkCategory": 0}],
                      firewall=[{"Name": "Public", "Enabled": 1, "DefaultInboundAction": 0, "AllowInboundRules": 1}],
                      rules=[{"DisplayName": "python.exe", "Program": PYTHON, "Enabled": 1, "Direction": 1,
                              "Action": 2, "Profile": 4, "Protocol": "TCP", "LocalPort": "Any"}])
        found = measured(text)
        self.assertEqual(step(found, "Network profile").state, netcheck.OK)  # Public, with Python allowed there
        self.assertEqual(step(found, "Windows Firewall").state, netcheck.OK)


class FirewallTest(unittest.TestCase):
    def verdict(self, port=8790, profile="Private", program=PYTHON, **sections):
        return netcheck.firewall_verdict(netcheck.parse_survey(survey(**sections)), program, port, profile)

    def test_an_allow_rule_for_this_python_on_this_profile(self):
        self.assertEqual(self.verdict(), ("allowed", "python.exe"))
        wall = step(measured(), "Windows Firewall")
        self.assertEqual(wall.state, netcheck.OK)
        self.assertIn(f"Python ({PYTHON}) may receive connections on Private networks", wall.found)

    def test_a_block_rule_wins_over_an_allow_rule(self):
        rules = [rule("Allow", "Private"), rule("Block", "Private, Public", name="Python")]
        self.assertEqual(self.verdict(rules=rules), ("blocked", "Python"))
        wall = step(measured(survey(rules=rules)), "Windows Firewall")
        self.assertEqual((wall.state, wall.action), (netcheck.PROBLEM, "firewall-rules"))
        self.assertIn("delete the rules named \"Python\"", wall.fix)

    def test_allowed_on_another_profile_only(self):
        self.assertEqual(self.verdict(profile="Public", rules=[rule("Allow", "Private")]), ("elsewhere", ["Private"]))
        self.assertEqual(self.verdict(profile="Public", rules=[rule("Allow", "Domain, Private")]),
                         ("elsewhere", ["Domain", "Private"]))
        self.assertEqual(self.verdict(profile="Public", rules=[rule("Allow", "Any")]), ("allowed", "python.exe"))

    def test_rules_for_another_python_dont_count(self):
        verdict = self.verdict(rules=[rule("Allow", "Private", program=OTHER_PYTHON)])
        self.assertEqual(verdict, ("other-python", OTHER_PYTHON))
        wall = step(measured(survey(rules=[rule("Allow", "Private", program=OTHER_PYTHON)])), "Windows Firewall")
        self.assertEqual(wall.action, "allow-python")
        self.assertIn(f"for another Python ({OTHER_PYTHON})", wall.found)

    def test_the_same_python_written_differently_counts(self):
        rules = [rule("Allow", "Private", program=r"%LOCALAPPDATA%\Programs\Python\Python312\python.exe")]
        local = str(Path(PYTHON).parents[3])  # ...\AppData\Local
        saved = netcheck.os.environ.get("LOCALAPPDATA")
        netcheck.os.environ["LOCALAPPDATA"] = local
        try:
            self.assertEqual(self.verdict(rules=rules)[0], "allowed")
        finally:
            if saved is None:
                del netcheck.os.environ["LOCALAPPDATA"]
            else:
                netcheck.os.environ["LOCALAPPDATA"] = saved

    def test_no_rule_offers_the_button(self):
        self.assertEqual(self.verdict(rules=[]), ("missing", None))
        wall = step(measured(survey(rules=[])), "Windows Firewall")
        self.assertEqual((wall.state, wall.action), (netcheck.PROBLEM, "allow-python"))
        self.assertIn("No firewall rule lets", wall.found)

    def test_disabled_outbound_and_other_port_rules_dont_count(self):
        for unhelpful in (rule("Allow", "Private", enabled="False"), {**rule("Allow", "Private"), "Direction": "Outbound"},
                          rule("Allow", "Private", "UDP"), rule("Allow", "Private", ports=("80", "443"))):
            self.assertEqual(self.verdict(rules=[unhelpful])[0], "missing", unhelpful)
        for helpful in (rule("Allow", "Private", ports=("8000-8800",)), rule("Allow", "Private", "Any", ports=("8790",))):
            self.assertEqual(self.verdict(rules=[helpful])[0], "allowed", helpful)

    def test_a_firewall_switched_off_names_the_security_program_in_charge(self):
        verdict = self.verdict(firewall=firewall(enabled="False"), rules=[], products=["Norton 360"])
        self.assertEqual(verdict, ("open", ["Norton 360"]))
        wall = step(measured(survey(firewall=firewall(enabled="False"), rules=[], products="Norton 360")),
                    "Windows Firewall")
        self.assertEqual(wall.state, netcheck.NOTE)
        self.assertIn("Norton 360 looks after the firewall", wall.found)
        self.assertEqual(self.verdict(firewall=firewall(inbound="Allow"), rules=[]), ("open", []))
        self.assertEqual(self.verdict(firewall=firewall(enabled="NotConfigured"))[0], "allowed")  # not "off"

    def test_blocking_every_connection_is_found(self):
        self.assertEqual(self.verdict(firewall=firewall(rules_too="False")), ("shielded", None))
        self.assertIn("untick \"Blocks all incoming connections",
                      step(measured(survey(firewall=firewall(rules_too="False"))), "Windows Firewall").fix)

    def test_the_button_adds_one_rule_for_private_networks_with_netsh(self):
        program, arguments = netcheck.allow_rule(PYTHON)
        self.assertTrue(program.lower().endswith(r"\system32\netsh.exe"), program)
        self.assertTrue(arguments.startswith("advfirewall firewall add rule "), arguments)
        for part in (f'name="{netcheck.RULE_NAME}"', "dir=in", "action=allow", f'program="{PYTHON}"',
                     "profile=private", "protocol=TCP", "enable=yes"):
            self.assertIn(part, arguments)
        self.assertNotIn("public", arguments.lower())


class PhoneTest(unittest.TestCase):
    def test_it_waits_for_the_phone_at_the_check_address(self):
        phone = step(measured(), "Phone test")
        self.assertEqual((phone.state, phone.action), (netcheck.WAIT, None))
        self.assertIn("open the address above or scan its QR code", phone.found)
        self.assertIn("open http://192.168.178.23:8790/check, which needs no PIN", phone.found)
        self.assertIn("isolation", phone.fix)  # the PC is ready: what is left is the phone's side
        self.assertIn("guest network", phone.fix)
        self.assertIn("VPN on the phone", phone.fix)

    def test_problems_on_the_pc_come_first(self):
        phone = step(measured(survey(rules=[])), "Phone test")
        self.assertEqual(phone.fix, "Fix what the steps above say first; then try again.")

    def test_a_phone_that_got_through(self):
        phone = step(measured(), "Phone test", visit=("192.168.178.40", "20:15:03"))
        self.assertEqual((phone.state, phone.found), (netcheck.OK, "A phone reached the PC from 192.168.178.40 at 20:15:03."))
        self.assertEqual(step(measured(), "Phone test", phones=1).found, "A phone has the page open.")

    def test_a_phone_that_got_through_settles_the_profile_and_the_firewall(self):
        found = measured(survey(profiles=[{**HOME["profiles"][0], "NetworkCategory": "Public"}], rules=[]))
        # a Public network and no rule for Python: judged by the rules, no way in
        self.assertEqual(step(found, "Network profile").state, netcheck.PROBLEM)
        self.assertEqual(step(found, "Windows Firewall").state, netcheck.PROBLEM)
        for reached in ({"phones": 1}, {"visit": ("192.168.178.40", "20:15:03")}):
            with self.subTest(**reached):
                profile, firewall = step(found, "Network profile", **reached), step(found, "Windows Firewall", **reached)
                self.assertEqual((profile.state, profile.fix, profile.action), (netcheck.OK, "", None))
                self.assertEqual((firewall.state, firewall.fix, firewall.action), (netcheck.OK, "", None))
                self.assertIn("a phone got through: nothing to change", profile.found)
                self.assertIn("A phone got through", firewall.found)

    def test_the_address_the_companion_listens_on_is_the_phones(self):
        found = measured(listen="172.27.48.1")
        self.assertIn("open http://172.27.48.1:8790/check", step(found, "Phone test").found)


class GatherTest(unittest.TestCase):
    def test_it_measures_with_the_runners_it_is_given(self):
        commands, urls, copies = [], [], []

        def run(command, **kwargs):
            commands.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, stdout=survey().encode("ascii"), stderr=b"")

        def fetch(url):
            urls.append(url)
            return "connection refused" if "172.27.48.1" in url else None

        found = netcheck.Findings(port=8790, listen="0.0.0.0", program=PYTHON, default_ip="192.168.178.23")
        netcheck.gather(found, run=run, fetch=fetch, progress=copies.append)
        self.assertEqual(urls, ["http://127.0.0.1:8790/check", "http://192.168.178.23:8790/check",
                                "http://172.27.48.1:8790/check"])
        self.assertEqual(found.lan, [("192.168.178.23", None), ("172.27.48.1", "connection refused")])
        self.assertEqual([a.ip for a in found.addresses], ["192.168.178.23", "172.27.48.1"])
        command, kwargs = commands[0]
        self.assertTrue(command[0].lower().endswith("powershell.exe"), command)
        script = base64.b64decode(command[command.index("-EncodedCommand") + 1]).decode("utf-16-le")
        self.assertEqual(script, netcheck.SURVEY)
        self.assertTrue(kwargs.get("capture_output"))
        self.assertEqual(len(copies), 3)  # after the first probe, the survey and the rest
        self.assertIs(copies[0].survey, netcheck.PENDING)
        self.assertIsNot(copies[-1], found)

    def test_without_powershell_the_checks_say_not_available(self):
        def run(command, **kwargs):
            raise FileNotFoundError(command[0])

        found = netcheck.Findings(port=8790, listen="0.0.0.0", program=PYTHON)
        netcheck.gather(found, run=run, fetch=lambda url: None)
        self.assertIsNone(found.survey)
        self.assertEqual(found.why_no_survey, "PowerShell isn't on this system")
        self.assertEqual(step(found, "Windows Firewall").state, netcheck.UNKNOWN)

    def test_a_survey_that_isnt_json_is_none(self):
        self.assertIsNone(netcheck.parse_survey("Get-NetIPAddress : Access denied"))
        self.assertIsNone(netcheck.parse_survey(""))
        self.assertIsNone(netcheck.parse_survey(None))
        partial = netcheck.parse_survey(json.dumps({"addresses": HOME["addresses"], "rules": None}))
        self.assertIsNone(partial["rules"])  # a section whose cmdlets failed
        self.assertIsNone(partial["firewall"])


if __name__ == "__main__":
    unittest.main()
