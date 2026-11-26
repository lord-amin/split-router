from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import (
    AutomationConfig,
    Destination,
    KillSwitchConfig,
    RouteRule,
    is_valid_interface_name,
    is_valid_ipv4,
)
from .paths import (
    AUTOMATION_CONFIG,
    EVENTS_FILE,
    HELPER_BINARY,
    KILLSWITCH_CONFIG,
    LEARNED_SERVERS,
    PERSISTENT_RULES,
    PROBE_BINARY,
    RUNTIME_STATE,
    SESSION_RULES,
    STATUS_FILE,
    SYSTEMD_UNIT,
)
from .shell import run_cmd


KIND_LABELS = {
    "wifi": "Wi-Fi",
    "ethernet": "Ethernet",
    "tunnel": "Tunnel",
    "ppp": "PPP VPN",
    "ipsec": "IPsec VPN",
    "loopback": "Loopback",
    "bridge": "Bridge",
    "wirelessDirect": "Wi-Fi Direct",
    "system": "System",
    "other": "Other",
}

KIND_ICONS = {
    "wifi": "network-wireless-symbolic",
    "ethernet": "network-wired-symbolic",
    "tunnel": "network-vpn-symbolic",
    "ppp": "network-vpn-symbolic",
    "ipsec": "network-vpn-symbolic",
    "loopback": "view-refresh-symbolic",
    "bridge": "network-workgroup-symbolic",
    "wirelessDirect": "network-wireless-signal-good-symbolic",
    "system": "emblem-system-symbolic",
    "other": "network-transmit-receive-symbolic",
}

VPN_KINDS = {"tunnel", "ppp", "ipsec"}
PHYSICAL_KINDS = {"wifi", "ethernet"}
CATCH_ALL_DESTINATIONS = {"0.0.0.0/1", "128.0.0.0/1"}

ROUTE_FLAG_LEGEND = [
    ("U", "Up (route is usable)"),
    ("G", "Uses a gateway"),
    ("H", "Host route (/32 single IP)"),
    ("S", "Static (added manually or by SplitRoute)"),
    ("D", "DHCP / dynamic route"),
    ("K", "Kernel / directly attached subnet"),
    ("L", "Link-scope / local"),
    ("!", "Link down (carrier lost)"),
]


@dataclass
class InterfaceInfo:
    name: str
    index: int = 0
    flags: list[str] = field(default_factory=list)
    is_up: bool = False
    is_running: bool = False
    is_point_to_point: bool = False
    is_loopback: bool = False
    mtu: int = 0
    mac: str = ""
    ipv4: list[dict[str, Any]] = field(default_factory=list)
    ipv6: list[dict[str, Any]] = field(default_factory=list)
    rx_bytes: int = 0
    tx_bytes: int = 0
    rx_packets: int = 0
    tx_packets: int = 0
    rx_errors: int = 0
    tx_errors: int = 0
    hardware_port: str | None = None
    service_name: str | None = None
    router: str | None = None
    dns: list[str] = field(default_factory=list)
    kind: str = "other"
    is_full_tunnel: bool = False
    route_count: int = 0

    @property
    def primary_ipv4(self) -> str | None:
        return self.ipv4[0]["address"] if self.ipv4 else None

    @property
    def is_active(self) -> bool:
        return self.is_up and (bool(self.ipv4) or (self.is_running and bool(self.ipv6)))

    @property
    def is_vpn(self) -> bool:
        return self.kind in VPN_KINDS

    @property
    def is_physical(self) -> bool:
        return self.kind in PHYSICAL_KINDS

    @property
    def is_system_noise(self) -> bool:
        if self.kind in ("system", "wirelessDirect", "loopback", "bridge"):
            return True
        if self.name.startswith(("docker", "br-", "veth", "virbr", "srdns", "birdns")):
            return True
        if self.kind == "tunnel" and not self.ipv4:
            return True
        return False

    @property
    def kind_label(self) -> str:
        return "Full tunnel" if self.is_full_tunnel else KIND_LABELS.get(self.kind, "Other")

    @property
    def icon_name(self) -> str:
        return KIND_ICONS.get(self.kind, "network-transmit-receive-symbolic")


@dataclass
class RouteEntry:
    id: str
    family: str  # inet | inet6
    destination: str
    gateway: str
    protocol: str
    scope: str
    netif: str
    metric: int
    raw_flags: list[str] = field(default_factory=list)
    prefsrc: str = ""

    @property
    def is_default(self) -> bool:
        return self.destination in ("default", "0.0.0.0/0", "::/0")

    @property
    def is_static(self) -> bool:
        return self.protocol in ("static", "boot", "")

    @property
    def is_link_layer(self) -> bool:
        return self.scope == "link" and self.protocol == "kernel" and not self.is_default

    @property
    def is_host(self) -> bool:
        return "/" not in self.destination and not self.is_default

    @property
    def flags_display(self) -> str:
        f = ["U"]
        if self.gateway:
            f.append("G")
        if self.is_host:
            f.append("H")
        if self.protocol in ("static", "boot"):
            f.append("S")
        elif self.protocol == "dhcp":
            f.append("D")
        elif self.protocol == "kernel":
            f.append("K")
        if self.scope == "link":
            f.append("L")
        if "linkdown" in self.raw_flags:
            f.append("!")
        return "".join(f)

    def cidr(self) -> tuple[str, int] | None:
        if self.family != "inet":
            return None
        if self.is_default:
            return ("0.0.0.0", 0)
        if "/" in self.destination:
            a, p = self.destination.split("/", 1)
            try:
                return (a, int(p))
            except Exception:
                return None
        if is_valid_ipv4(self.destination):
            return (self.destination, 32)
        return None


@dataclass
class ServiceStatus:
    helper_installed: bool = False
    helper_version: str | None = None
    probe_installed: bool = False
    probe_has_cap: bool = False
    daemon_installed: bool = False
    loaded: bool = False
    state: str | None = None
    last_exit: str | None = None
    runs: int | None = None
    interval: int | None = None


@dataclass
class LiveCount:
    live: int = 0
    expected: int = 0

    @property
    def all_live(self) -> bool:
        return self.expected > 0 and self.live == self.expected


@dataclass
class NetworkSnapshot:
    interfaces: list[InterfaceInfo] = field(default_factory=list)
    routes_v4: list[RouteEntry] = field(default_factory=list)
    routes_v6: list[RouteEntry] = field(default_factory=list)
    internet_route: dict[str, Any] | None = None
    primary_interface: str | None = None
    primary_router: str | None = None
    system_dns: list[str] = field(default_factory=list)
    dns_resolvers: list[dict[str, Any]] = field(default_factory=list)
    helper_status: dict[str, Any] | None = None
    applied_rules: list[RouteRule] = field(default_factory=list)
    live_routes: dict[str, LiveCount] = field(default_factory=dict)
    service: ServiceStatus = field(default_factory=ServiceStatus)
    applied_kill_switch: KillSwitchConfig | None = None
    applied_automation: AutomationConfig | None = None
    runtime: dict[str, Any] | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    network: dict[str, Any] | None = None
    installed_unit: str | None = None
    learned_servers: list[str] = field(default_factory=list)
    taken_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def interface_display_name(name: str, info: InterfaceInfo | None, aliases: dict[str, str]) -> str:
    alias = aliases.get(name, "").strip()
    if alias:
        return alias
    if info is not None:
        if info.is_full_tunnel:
            return "Full-tunnel VPN"
        if info.hardware_port:
            return info.hardware_port
        return info.kind_label
    return name


class NetworkScanner:
    @staticmethod
    def _load_json(path: str) -> Any:
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception:
            return None

    @staticmethod
    def _read_proc_net_dev() -> dict[str, tuple[int, int, int, int, int, int]]:
        out: dict[str, tuple[int, int, int, int, int, int]] = {}
        try:
            lines = Path("/proc/net/dev").read_text(encoding="utf-8").splitlines()[2:]
        except Exception:
            return out
        for line in lines:
            if ":" not in line:
                continue
            name, rest = line.split(":", 1)
            fields = rest.split()
            if len(fields) < 12:
                continue
            try:
                out[name.strip()] = (
                    int(fields[0]), int(fields[1]), int(fields[2]),
                    int(fields[8]), int(fields[9]), int(fields[10]),
                )
            except Exception:
                continue
        return out

    @staticmethod
    def classify(name: str, nm_type: str | None = None) -> str:
        if nm_type == "wifi" or Path(f"/sys/class/net/{name}/wireless").exists() or name.startswith(("wl", "wlan")):
            return "wifi"
        if name.startswith("p2p"):
            return "wirelessDirect"
        if name.startswith("lo"):
            return "loopback"
        if name.startswith(("ppp",)):
            return "ppp"
        if name.startswith(("ipsec", "xfrm")):
            return "ipsec"
        if name.startswith(("tun", "tap", "wg", "utun", "tailscale", "nordlynx", "mullvad", "proton", "nebula", "zt")):
            return "tunnel"
        if nm_type == "bridge" or name.startswith(("br-", "docker", "virbr")):
            return "bridge"
        if name.startswith(("veth", "srdns", "birdns", "dummy", "ifb")):
            return "system"
        if nm_type == "ethernet" or name.startswith(("en", "eth", "usb")):
            return "ethernet"
        if Path(f"/sys/class/net/{name}/device").exists():
            return "ethernet"
        return "other"

    @staticmethod
    def lookup_route(dst: str) -> dict[str, Any] | None:
        res = run_cmd(["ip", "-j", "route", "get", dst], timeout=3.0)
        if not res.ok:
            return None
        try:
            arr = json.loads(res.stdout)
            if arr:
                it = arr[0]
                return {
                    "routeTo": it.get("dst", dst),
                    "gateway": it.get("gateway"),
                    "interface": it.get("dev"),
                    "prefSrc": it.get("prefsrc"),
                }
        except Exception:
            pass
        return None

    @staticmethod
    def _parse_routes(family: str) -> list[RouteEntry]:
        args = ["ip", "-j", "-4" if family == "inet" else "-6", "route", "show"]
        res = run_cmd(args, timeout=6.0)
        if not res.ok:
            return []
        try:
            items = json.loads(res.stdout)
        except Exception:
            return []
        out: list[RouteEntry] = []
        for idx, it in enumerate(items):
            out.append(RouteEntry(
                id=f"{family}-{idx}",
                family=family,
                destination=it.get("dst", ""),
                gateway=it.get("gateway", ""),
                protocol=it.get("protocol", ""),
                scope=it.get("scope", ""),
                netif=it.get("dev", ""),
                metric=int(it.get("metric", 0) or 0),
                raw_flags=list(it.get("flags", [])),
                prefsrc=it.get("prefsrc", ""),
            ))
        return out

    @staticmethod
    def _nmcli_devices() -> dict[str, tuple[str, str]]:
        """Returns dev -> (nm_type, connection_name)."""
        res = run_cmd(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "dev"], timeout=4.0)
        out: dict[str, tuple[str, str]] = {}
        if not res.ok:
            return out
        for line in res.stdout.splitlines():
            parts = line.split(":", 3)
            if len(parts) >= 4:
                dev, typ, _state, conn = parts
                out[dev] = (typ, conn if conn != "--" else "")
        return out

    @staticmethod
    def _dns_info() -> tuple[list[str], dict[str, list[str]], list[dict[str, Any]]]:
        """Parses resolvectl status into global DNS, per-interface DNS, and detailed resolver blocks."""
        res = run_cmd(["resolvectl", "status"], timeout=4.0)
        global_dns: list[str] = []
        by_iface: dict[str, list[str]] = {}
        blocks: list[dict[str, Any]] = []
        if res.ok:
            current_link: str | None = None
            current_servers: list[str] = []
            current_domains: list[str] = []
            in_global = False

            def flush_block() -> None:
                nonlocal current_link, current_servers, current_domains
                if current_servers:
                    if current_link:
                        by_iface[current_link] = list(dict.fromkeys(current_servers))
                    if not global_dns:
                        global_dns.extend(current_servers)
                    blocks.append({
                        "interface": current_link,
                        "domains": list(current_domains),
                        "nameservers": list(dict.fromkeys(current_servers)),
                    })
                current_servers = []
                current_domains = []

            for line in res.stdout.splitlines():
                s = line.strip()
                if s == "Global":
                    flush_block()
                    in_global = True
                    current_link = None
                    continue
                m = re.match(r"^Link \d+ \(([^)]+)\)$", s)
                if m:
                    flush_block()
                    in_global = False
                    current_link = m.group(1)
                    continue
                if s.startswith("DNS Servers:") or s.startswith("Current DNS Server:"):
                    vals = s.split(":", 1)[1].strip().split()
                    for v in vals:
                        ip_only = v.split("%")[0].split("#")[0]
                        if ip_only and ip_only != "127.0.0.53":
                            current_servers.append(ip_only)
                            if in_global and ip_only not in global_dns:
                                global_dns.append(ip_only)
                elif s.startswith("DNS Domain:"):
                    vals = s.split(":", 1)[1].strip().split()
                    current_domains.extend(vals)
            flush_block()

        if not global_dns:
            try:
                for line in Path("/etc/resolv.conf").read_text(encoding="utf-8").splitlines():
                    f = line.split()
                    if len(f) >= 2 and f[0] == "nameserver" and f[1] != "127.0.0.53":
                        global_dns.append(f[1])
            except Exception:
                pass
        global_dns = list(dict.fromkeys(global_dns))
        return global_dns, by_iface, blocks

    @staticmethod
    def _neighbour_mac(ip: str, iface: str) -> str | None:
        res = run_cmd(["ip", "-j", "neigh", "show", ip, "dev", iface], timeout=2.0)
        if not res.ok:
            return None
        try:
            arr = json.loads(res.stdout)
            for it in arr:
                if it.get("lladdr"):
                    return str(it["lladdr"]).lower()
        except Exception:
            pass
        return None

    @staticmethod
    def _service_status() -> ServiceStatus:
        s = ServiceStatus()
        hp = Path(HELPER_BINARY)
        s.helper_installed = hp.is_file() and os.access(hp, os.X_OK)
        if s.helper_installed:
            v = run_cmd([HELPER_BINARY, "version"], timeout=2.0).stdout.strip()
            s.helper_version = v or None
        pp = Path(PROBE_BINARY)
        s.probe_installed = pp.is_file() and os.access(pp, os.X_OK)
        if s.probe_installed:
            gc = run_cmd(["getcap", PROBE_BINARY], timeout=2.0).stdout
            s.probe_has_cap = "cap_net_raw" in gc
        s.daemon_installed = Path(SYSTEMD_UNIT).is_file()
        if s.daemon_installed:
            r = run_cmd(
                ["systemctl", "show", "splitroute-helper.service",
                 "--property=ActiveState,SubState,ExecMainStatus,NRestarts"],
                timeout=3.0,
            )
            if r.ok:
                props = {}
                for ln in r.stdout.splitlines():
                    if "=" in ln:
                        k, v = ln.split("=", 1)
                        props[k.strip()] = v.strip()
                active = props.get("ActiveState", "")
                sub = props.get("SubState", "")
                s.loaded = active == "active"
                s.state = f"{active} ({sub})" if sub else active
                s.last_exit = props.get("ExecMainStatus")
        return s

    @classmethod
    def scan(cls, rules: list[RouteRule] | None = None) -> NetworkSnapshot:
        snap = NetworkSnapshot()
        rules = rules or []
        snap.routes_v4 = cls._parse_routes("inet")
        snap.routes_v6 = cls._parse_routes("inet6")
        snap.internet_route = cls.lookup_route("1.1.1.1")

        nm_devs = cls._nmcli_devices()
        global_dns, dns_by_if, dns_blocks = cls._dns_info()
        snap.system_dns = global_dns
        snap.dns_resolvers = dns_blocks

        proc_stats = cls._read_proc_net_dev()
        addr_res = run_cmd(["ip", "-j", "addr", "show"], timeout=5.0)
        raw_ifaces: list[dict[str, Any]] = []
        if addr_res.ok:
            try:
                raw_ifaces = json.loads(addr_res.stdout)
            except Exception:
                raw_ifaces = []

        routers_by_if: dict[str, str] = {}
        for r in snap.routes_v4:
            if r.is_default and is_valid_ipv4(r.gateway) and r.netif not in routers_by_if:
                routers_by_if[r.netif] = r.gateway

        egress_if = snap.internet_route.get("interface") if snap.internet_route else None

        infos: list[InterfaceInfo] = []
        for it in raw_ifaces:
            name = it.get("ifname", "")
            flags = list(it.get("flags", []))
            is_up = "UP" in flags
            is_running = "RUNNING" in flags or "LOWER_UP" in flags
            is_p2p = "POINTOPOINT" in flags
            is_lo = "LOOPBACK" in flags
            mac = ""
            if it.get("link_type") == "ether" and it.get("address") not in (None, "", "00:00:00:00:00:00"):
                mac = str(it["address"]).lower()
            v4_list, v6_list = [], []
            for a in it.get("addr_info", []):
                if a.get("family") == "inet":
                    v4_list.append({
                        "address": a.get("local", ""),
                        "prefix": a.get("prefixlen", 32),
                        "peer": a.get("peer", ""),
                    })
                elif a.get("family") == "inet6":
                    v6_list.append({
                        "address": a.get("local", ""),
                        "prefix": a.get("prefixlen", 128),
                    })
            st = proc_stats.get(name, (0, 0, 0, 0, 0, 0))
            nm_type, conn_name = nm_devs.get(name, ("", ""))
            kind = cls.classify(name, nm_type)
            catch_all = sum(1 for r in snap.routes_v4 if r.netif == name and r.destination in CATCH_ALL_DESTINATIONS)
            is_full = catch_all >= 2 or (kind in VPN_KINDS and egress_if == name)
            router = routers_by_if.get(name)
            if not router and is_up and v4_list and not is_p2p and not is_lo and kind in PHYSICAL_KINDS:
                gw_res = run_cmd(["nmcli", "-g", "IP4.GATEWAY", "dev", "show", name], timeout=2.0)
                if gw_res.ok and is_valid_ipv4(gw_res.stdout.strip()):
                    router = gw_res.stdout.strip()
                    routers_by_if[name] = router
            hw_port = conn_name or (KIND_LABELS.get(kind) if kind in PHYSICAL_KINDS else None)
            infos.append(InterfaceInfo(
                name=name,
                index=int(it.get("ifindex", 0) or 0),
                flags=flags,
                is_up=is_up,
                is_running=is_running,
                is_point_to_point=is_p2p,
                is_loopback=is_lo,
                mtu=int(it.get("mtu", 0) or 0),
                mac=mac,
                ipv4=v4_list,
                ipv6=v6_list,
                rx_bytes=st[0],
                rx_packets=st[1],
                rx_errors=st[2],
                tx_bytes=st[3],
                tx_packets=st[4],
                tx_errors=st[5],
                hardware_port=hw_port,
                service_name=conn_name or name,
                router=router,
                dns=dns_by_if.get(name, []),
                kind=kind,
                is_full_tunnel=is_full,
                route_count=sum(1 for r in snap.routes_v4 if r.netif == name),
            ))
        snap.interfaces = infos

        # Primary physical or active interface
        phys_defaults = sorted(
            [r for r in snap.routes_v4 if r.is_default and is_valid_ipv4(r.gateway)],
            key=lambda r: r.metric,
        )
        if phys_defaults:
            snap.primary_interface = phys_defaults[0].netif
            snap.primary_router = phys_defaults[0].gateway
        elif egress_if:
            snap.primary_interface = egress_if

        for info in infos:
            if info.is_up and not info.is_point_to_point and not info.is_loopback and info.ipv4 and info.is_physical and info.router:
                snap.network = {
                    "interface": info.name,
                    "routerIP": info.router,
                    "routerMAC": cls._neighbour_mac(info.router, info.name) or "",
                }
                break

        snap.helper_status = cls._load_json(STATUS_FILE)
        p_cfg = cls._load_json(PERSISTENT_RULES) or {}
        s_cfg = cls._load_json(SESSION_RULES) or {}
        snap.applied_rules = [
            RouteRule.from_dict(x) for x in ((p_cfg.get("rules") or []) + (s_cfg.get("rules") or []))
        ]
        ks_raw = cls._load_json(KILLSWITCH_CONFIG)
        if ks_raw is not None:
            snap.applied_kill_switch = KillSwitchConfig.from_dict(ks_raw)
        au_raw = cls._load_json(AUTOMATION_CONFIG)
        if au_raw is not None:
            snap.applied_automation = AutomationConfig.from_dict(au_raw)
        snap.runtime = cls._load_json(RUNTIME_STATE)
        snap.events = cls._load_json(EVENTS_FILE) or []
        snap.learned_servers = cls._load_json(LEARNED_SERVERS) or []
        snap.service = cls._service_status()
        try:
            snap.installed_unit = Path(SYSTEMD_UNIT).read_text(encoding="utf-8")
        except Exception:
            snap.installed_unit = None

        snap.live_routes = cls._live_check(rules, infos, snap.routes_v4)
        snap.taken_at = datetime.now(timezone.utc)
        return snap

    @staticmethod
    def _live_check(
        rules: list[RouteRule],
        interfaces: list[InterfaceInfo],
        routes_v4: list[RouteEntry],
    ) -> dict[str, LiveCount]:
        by_name = {i.name: i for i in interfaces}
        # Index table routes by destination -> list[(gateway, netif)]
        table_map: dict[str, list[tuple[str, str]]] = {}
        for r in routes_v4:
            table_map.setdefault(r.destination, []).append((r.gateway, r.netif))

        out: dict[str, LiveCount] = {}
        for rule in rules:
            if not rule.enabled:
                continue
            gw = ""
            iface = ""
            if rule.via.kind == "gateway":
                gw = rule.via.gateway
            elif rule.via.kind == "interfaceDirect":
                info = by_name.get(rule.via.interface)
                if not info or not info.is_up:
                    out[rule.id] = LiveCount(0, 0)
                    continue
                iface = rule.via.interface
            elif rule.via.kind == "interfaceRouter":
                info = by_name.get(rule.via.interface)
                if not info or not info.is_up or not info.ipv4 or (not info.router and not info.is_point_to_point):
                    out[rule.id] = LiveCount(0, 0)
                    continue
                iface = rule.via.interface
                gw = info.router or ""

            expected = 0
            live = 0
            for d in rule.parsed_destinations():
                targets: list[str] = []
                if d.kind == "host":
                    targets = [d.address]
                elif d.kind == "network":
                    targets = [f"{d.address}/{d.prefix}"]
                elif d.kind == "domain":
                    try:
                        infos = socket.getaddrinfo(d.domain, None, socket.AF_INET, socket.SOCK_STREAM)
                        targets = sorted({x[4][0] for x in infos})
                    except Exception:
                        targets = []
                for t in targets:
                    if gw and t == gw:
                        continue
                    expected += 1
                    for r_gw, r_if in table_map.get(t, []):
                        if gw:
                            if r_gw == gw and (not iface or r_if == iface):
                                live += 1
                                break
                        else:
                            if not r_gw and r_if == iface:
                                live += 1
                                break
            out[rule.id] = LiveCount(live=live, expected=expected)
        return out

    @staticmethod
    def tail_log(path: str, lines: int = 300) -> str:
        try:
            all_lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
            return "\n".join(all_lines[-lines:])
        except Exception:
            return ""

