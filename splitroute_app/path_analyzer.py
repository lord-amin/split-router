from __future__ import annotations

import shutil
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from .models import RouteRule, cidr_contains, is_valid_domain, is_valid_ipv4
from .scanner import CATCH_ALL_DESTINATIONS, NetworkScanner, NetworkSnapshot, interface_display_name


@dataclass
class PathHop:
    ip: str
    lookup: dict | None = None
    interface_name: str | None = None
    interface_label: str = "—"
    verdict: str = "noRoute"  # direct | vpn | tunnel | local | noRoute | blocked
    headline: str = ""
    explanation: list[str] = field(default_factory=list)
    rule_id: str | None = None
    tcp443: str | None = None


@dataclass
class PathCheckResult:
    query: str
    is_domain: bool = False
    ipv4: list[str] = field(default_factory=list)
    ipv6: list[str] = field(default_factory=list)
    dns_ms: float | None = None
    hops: list[PathHop] = field(default_factory=list)
    error: str | None = None


class PathAnalyzer:
    @staticmethod
    def normalize(raw: str) -> str:
        s = raw.strip().lower()
        if "://" in s:
            s = s.split("://", 1)[1]
        if "/" in s:
            s = s.split("/", 1)[0]
        if "@" in s:
            s = s.rsplit("@", 1)[1]
        if s.count(":") == 1:
            s = s.split(":", 1)[0]
        return s

    @staticmethod
    def _resolve(host: str, family: int) -> list[str]:
        try:
            infos = socket.getaddrinfo(host, None, family, socket.SOCK_STREAM)
            seen: list[str] = []
            for info in infos:
                ip = info[4][0].split("%")[0]
                if ip.lower().startswith("::ffff:"):
                    continue
                if ip not in seen:
                    seen.append(ip)
            return seen
        except Exception:
            return []

    @classmethod
    def analyze(
        cls,
        raw: str,
        snapshot: NetworkSnapshot,
        rules: list[RouteRule],
        aliases: dict[str, str],
    ) -> PathCheckResult:
        query = cls.normalize(raw)
        res = PathCheckResult(query=query)
        if is_valid_ipv4(query):
            res.ipv4 = [query]
        elif is_valid_domain(query):
            res.is_domain = True
            t0 = time.monotonic()
            res.ipv4 = cls._resolve(query, socket.AF_INET)
            res.dns_ms = (time.monotonic() - t0) * 1000.0
            res.ipv6 = cls._resolve(query, socket.AF_INET6)
            if not res.ipv4 and not res.ipv6:
                res.error = f"Could not resolve {query}. Check the name or your DNS."
                return res
            if not res.ipv4:
                res.error = f"{query} has no IPv4 address; only IPv6 paths exist."
        else:
            res.error = "Enter an IPv4 address (e.g. 203.0.113.10) or a domain (e.g. example.com)."
            return res

        res.hops = [cls.hop(ip, snapshot, rules, aliases) for ip in res.ipv4]
        return res

    @staticmethod
    def _ks_blocks(ks: dict, ip: str, iface: str) -> bool:
        if not ks.get("active") or iface not in (ks.get("interfaces") or []):
            return False
        mode = ks.get("mode", "destinations")
        if mode == "destinations":
            return any(cidr_contains(c, ip) for c in (ks.get("blocked") or []))
        return (
            not any(cidr_contains(c, ip) for c in (ks.get("allowed") or []))
            and ip not in (ks.get("dnsServers") or [])
        )

    @classmethod
    def hop(
        cls,
        ip: str,
        snapshot: NetworkSnapshot,
        rules: list[RouteRule],
        aliases: dict[str, str],
    ) -> PathHop:
        lookup = NetworkScanner.lookup_route(ip)
        if not lookup or not lookup.get("interface"):
            return PathHop(
                ip=ip,
                verdict="noRoute",
                headline="No route to this address",
                explanation=[f"The kernel has no route that covers {ip}."],
            )
        ifname = lookup["interface"]
        info = next((i for i in snapshot.interfaces if i.name == ifname), None)
        label = interface_display_name(ifname, info, aliases)
        kind = info.kind if info else NetworkScanner.classify(ifname)

        if kind == "loopback":
            verdict = "local"
            headline = "Stays on this Linux machine (loopback)"
        elif kind in ("ppp", "ipsec"):
            verdict = "vpn"
            headline = f"Through {label} ({ifname})"
        elif kind == "tunnel":
            verdict = "tunnel"
            headline = f"Through tunnel {label} ({ifname})"
        else:
            verdict = "direct"
            headline = f"Direct via {label} ({ifname})"

        gw = lookup.get("gateway")
        if gw and is_valid_ipv4(gw):
            headline += f" → gateway {gw}"

        # Find best matching route entry in table
        best_route = None
        best_prefix = -1
        for r in snapshot.routes_v4:
            if r.netif != ifname:
                continue
            c = r.cidr()
            if not c:
                continue
            net_addr, prefix = c
            if cidr_contains(f"{net_addr}/{prefix}", ip) and prefix > best_prefix:
                best_prefix = prefix
                best_route = r

        lines: list[str] = []
        if best_route is not None:
            matched_lbl = "default (0.0.0.0/0)" if best_route.is_default else (
                f"{best_route.destination}/32" if best_route.is_host else best_route.destination
            )
            static_str = " (static)" if best_route.is_static else ""
            lines.append(f"Matching route: {matched_lbl}{static_str}")
            lookup["matchedRouteLabel"] = matched_lbl
            lookup["flags"] = list(best_route.flags_display)
        else:
            lookup["matchedRouteLabel"] = "kernel FIB"
            lookup["flags"] = []

        installed = (snapshot.helper_status or {}).get("installedRoutes") or []
        owner = None
        if best_route is not None and best_route.cidr() is not None:
            b_addr, b_pref = best_route.cidr()  # type: ignore[misc]
            owner = next(
                (m for m in installed if m.get("destination") == b_addr and m.get("prefix") == b_pref),
                None,
            )

        rule_id = None
        if owner is not None:
            rule_id = owner.get("ruleID")
            rname = next((r.name for r in rules if r.id == rule_id), "deleted rule")
            dom = owner.get("sourceDomain")
            dom_str = f" for {dom}" if dom else ""
            lines.append(f"Installed by SplitRoute rule “{rname}”{dom_str}.")
        elif info and info.is_full_tunnel and best_route and (best_route.destination in CATCH_ALL_DESTINATIONS or best_route.is_default):
            lines.append(f"This is a catch-all/default route of tunnel {ifname}: everything without a more specific route goes into it.")
        elif best_route and best_route.is_default:
            lines.append(f"Uses the default route of {ifname}.")
        elif best_route and best_route.is_host and best_route.is_static:
            lines.append("Static host route added outside SplitRoute (by hand or another tool). Use Routing Table › Import to manage it.")
        elif best_route and not gw:
            lines.append(f"The address is directly reachable on {ifname}.")

        if owner is None:
            for r in rules:
                if not r.enabled:
                    continue
                matched_rule = False
                for d in r.parsed_destinations():
                    if d.kind == "host" and d.address == ip:
                        matched_rule = True
                    elif d.kind == "network" and cidr_contains(f"{d.address}/{d.prefix}", ip):
                        matched_rule = True
                if matched_rule:
                    lines.append(f"Rule “{r.name}” covers this IP but its route is not active. Apply your changes or check the rule's status.")
                    break

        bl = (snapshot.helper_status or {}).get("blockList")
        ks = (snapshot.helper_status or {}).get("killSwitch")
        if bl and bl.get("active") and any(cidr_contains(c, ip) for c in (bl.get("denied") or [])):
            verdict = "blocked"
            headline = "Blocked by your block list"
            lines.insert(0, "Connections to this address are refused on every interface, the VPN tunnel included.")
        elif ks and ks.get("active"):
            if cls._ks_blocks(ks, ip, ifname):
                verdict = "blocked"
                headline = f"Blocked by the kill switch on {label} ({ifname})"
                lines.insert(0, "Packets to this address would leave through a guarded interface, so nftables drops them.")
            elif verdict == "tunnel":
                leaks = [i for i in (ks.get("interfaces") or []) if cls._ks_blocks(ks, ip, i)]
                if leaks:
                    lines.append(f"Kill switch: if the tunnel goes down, this traffic is blocked on {', '.join(leaks)} instead of leaking.")

        if verdict == "tunnel" and info and info.is_full_tunnel:
            lines.append("To bypass the tunnel, add a rule that sends this destination via your Ethernet/Wi-Fi router.")

        return PathHop(
            ip=ip,
            lookup=lookup,
            interface_name=ifname,
            interface_label=label,
            verdict=verdict,
            headline=headline,
            explanation=lines,
            rule_id=rule_id,
        )


class TracerouteRunner:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.running: bool = False
        self._proc: subprocess.Popen[str] | None = None

    def start(self, target: str, on_update: Callable[[], None]) -> None:
        self.stop()
        if not is_valid_ipv4(target):
            return
        tr_bin = shutil.which("traceroute")
        if tr_bin:
            args = [tr_bin, "-n", "-q", "1", "-w", "1", "-m", "20", target]
        else:
            tp_bin = shutil.which("tracepath")
            if not tp_bin:
                self.lines = ["Neither traceroute nor tracepath is installed."]
                on_update()
                return
            args = [tp_bin, "-n", "-m", "20", target]

        self.lines = [f"traceroute to {target}, max 20 hops"]
        self.running = True
        on_update()

        def worker() -> None:
            try:
                proc = subprocess.Popen(
                    args,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                self._proc = proc
                assert proc.stdout is not None
                for raw in proc.stdout:
                    line = raw.rstrip("\n")
                    if line:
                        self.lines.append(line)
                        on_update()
                proc.wait()
            except Exception as exc:
                self.lines.append(f"Could not start traceroute: {exc}")
            finally:
                self.running = False
                self._proc = None
                on_update()

        threading.Thread(target=worker, daemon=True).start()

    def stop(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except Exception:
                pass
        self._proc = None
        self.running = False

