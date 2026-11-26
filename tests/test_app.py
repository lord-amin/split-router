from __future__ import annotations

import unittest

from splitroute_app.models import (
    Destination,
    DomainResolver,
    KillSwitchConfig,
    RouteRule,
    RouteVia,
    Workspace,
    compile_nftables_preview,
    is_private_ipv4,
    is_valid_domain,
    is_valid_interface_name,
    is_valid_ipv4,
    normalize_cidr,
)
from splitroute_app.path_analyzer import PathAnalyzer


class TestModelsAndValidation(unittest.TestCase):
    def test_ipv4_and_domain(self) -> None:
        self.assertTrue(is_valid_ipv4("192.168.20.1"))
        self.assertFalse(is_valid_ipv4("999.0.0.1"))
        self.assertTrue(is_private_ipv4("10.0.0.5"))
        self.assertFalse(is_private_ipv4("1.1.1.1"))
        self.assertTrue(is_valid_domain("api.telegram.org"))
        self.assertFalse(is_valid_domain("1.2.3.4"))
        self.assertTrue(is_valid_interface_name("enp3s0"))
        self.assertTrue(is_valid_interface_name("br-eb9c51a89dfc"[:15]))
        self.assertFalse(is_valid_interface_name("bad iface"))

    def test_destination_parse(self) -> None:
        h = Destination.parse("1.2.3.4")
        self.assertIsNotNone(h)
        assert h is not None
        self.assertEqual(h.kind, "host")
        self.assertEqual(h.address, "1.2.3.4")

        n = Destination.parse("192.168.1.99/24")
        self.assertIsNotNone(n)
        assert n is not None
        self.assertEqual(n.kind, "network")
        self.assertEqual(n.display, "192.168.1.0/24")

        d = Destination.parse("Example.COM")
        self.assertIsNotNone(d)
        assert d is not None
        self.assertEqual(d.kind, "domain")
        self.assertEqual(d.domain, "example.com")

    def test_rule_validation_and_roundtrip(self) -> None:
        r = RouteRule(
            name="Internal",
            destinations=["10.236.0.4", "example.com"],
            via=RouteVia(kind="interfaceRouter", interface="enp3s0"),
        )
        self.assertEqual(r.validation_errors(), [])
        ws = Workspace(rules=[r], kill_switch=KillSwitchConfig(enabled=True, interfaces=["enp3s0"]))
        ws2 = Workspace.from_dict(ws.to_dict())
        self.assertEqual(len(ws2.rules), 1)
        self.assertTrue(ws2.rules[0].same_content(r))
        self.assertEqual(ws2.kill_switch.interfaces, ["enp3s0"])

    def test_nftables_preview(self) -> None:
        nft = compile_nftables_preview(
            "fullSystem",
            ["enp3s0"],
            [],
            ["192.168.20.0/24"],
            ["1.1.1.1"],
            True,
            True,
            ["91.108.56.0/22"],
        )
        self.assertIn("table inet splitroute", nft)
        self.assertIn("ip daddr @sr_deny_v4 reject", nft)
        self.assertIn('oifname "enp3s0" drop', nft)

    def test_resolver_validation(self) -> None:
        dr = DomainResolver(domain="corp.local", nameservers=["10.0.0.53"])
        self.assertIsNone(dr.validation_error)
        bad = DomainResolver(domain="bad domain", nameservers=[])
        self.assertIsNotNone(bad.validation_error)


class TestPathAndNormalize(unittest.TestCase):
    def test_normalize_query(self) -> None:
        self.assertEqual(PathAnalyzer.normalize("HTTPS://Example.COM/foo/bar"), "example.com")
        self.assertEqual(PathAnalyzer.normalize("1.1.1.1:443"), "1.1.1.1")

    def test_cidr_normalize(self) -> None:
        self.assertEqual(normalize_cidr("1.2.3.4"), "1.2.3.4/32")
        self.assertEqual(normalize_cidr("2001:b28:f23d::1/48"), "2001:b28:f23d::/48")

    def test_install_helper_commands_not_same_file(self) -> None:
        from splitroute_app.app_model import AppModel
        from splitroute_app.paths import HELPER_BINARY, PROBE_BINARY

        m = AppModel()
        cmds = m._install_helper_commands()
        for c in cmds:
            self.assertNotIn(f"{HELPER_BINARY} {HELPER_BINARY}", c)
            self.assertNotIn(f"{PROBE_BINARY} {PROBE_BINARY}", c)


if __name__ == "__main__":
    unittest.main()
