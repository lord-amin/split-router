from __future__ import annotations

import concurrent.futures
import json
import socket
import time
import urllib.error
import urllib.request
from typing import Any

from .models import IPIdentity, PublicIPResult, is_valid_ipv4, is_valid_ipv6
from .paths import VERSION, find_probe_binary
from .shell import run_cmd


class Probes:
    @staticmethod
    def _http_get_default(url: str, timeout: float = 7.0) -> tuple[int, bytes]:
        req = urllib.request.Request(url, headers={"User-Agent": f"SplitRoute/{VERSION}"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()

    @staticmethod
    def public_address(v6: bool, interface: str | None = None) -> tuple[str | None, float | None, str | None]:
        """Returns (ip, latency_ms, error)."""
        probe = find_probe_binary()
        if probe is not None and interface:
            args = [str(probe), "public-ip", "--interface", interface]
            if v6:
                args.append("--v6")
            res = run_cmd(args, timeout=9.0)
            if res.ok and res.stdout.strip():
                try:
                    data = json.loads(res.stdout.strip())
                    if data.get("ip"):
                        return data["ip"], data.get("latencyMs"), None
                    return None, None, data.get("error") or "No connectivity on this path"
                except Exception:
                    pass
        if interface and probe is None:
            return None, None, "Build/install splitroute-probe to pin probes to this interface"

        host = "https://api6.ipify.org/" if v6 else "https://api.ipify.org/"
        t0 = time.monotonic()
        try:
            status, body = Probes._http_get_default(host, timeout=6.0)
            ms = (time.monotonic() - t0) * 1000.0
            ip = body.decode("utf-8", errors="replace").strip()
            if status == 200 and (is_valid_ipv4(ip) or is_valid_ipv6(ip)):
                return ip, ms, None
            return None, None, f"HTTP {status}"
        except Exception as exc:
            return None, None, f"No {'IPv6' if v6 else 'IPv4'} connectivity ({exc})"

    @staticmethod
    def probe_path(id: str, title: str, interface: str | None) -> tuple[PublicIPResult, list[str]]:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            f4 = pool.submit(Probes.public_address, False, interface)
            f6 = pool.submit(Probes.public_address, True, interface)
            ip4, ms4, err4 = f4.result()
            ip6, ms6, err6 = f6.result()

        res = PublicIPResult(id=id, title=title, interface=interface)
        found: list[str] = []
        if ip4:
            res.v4 = IPIdentity(ip=ip4)
            res.latency_ms = ms4
            found.append(ip4)
        else:
            res.v4_error = err4
        if ip6:
            res.v6 = IPIdentity(ip=ip6)
            if res.latency_ms is None:
                res.latency_ms = ms6
            found.append(ip6)
        else:
            res.v6_error = "No IPv6 connectivity on this path" if err6 else None
        return res, found

    @staticmethod
    def geolocate(ip: str, preferred: str = "ipinfo") -> IPIdentity | None:
        providers = ["ipinfo", "ipwhois"] if preferred == "ipinfo" else ["ipwhois", "ipinfo"]
        for p in providers:
            ident = Probes._geolocate_once(ip, p)
            if ident is not None:
                return ident
        return None

    @staticmethod
    def _geolocate_once(ip: str, provider: str) -> IPIdentity | None:
        url = f"https://ipinfo.io/{ip}/json" if provider == "ipinfo" else f"https://ipwho.is/{ip}"
        try:
            status, body = Probes._http_get_default(url, timeout=6.0)
            if status != 200:
                return None
            j = json.loads(body.decode("utf-8"))
        except Exception:
            return None

        if provider == "ipinfo":
            if j.get("bogon") is True:
                return IPIdentity(ip=ip, org="Private/reserved address")
            return IPIdentity(
                ip=ip,
                city=j.get("city") or None,
                region=j.get("region") or None,
                country=j.get("country") or None,
                org=j.get("org") or None,
                timezone=j.get("timezone") or None,
                coords=j.get("loc") or None,
            )
        else:
            if j.get("success") is False:
                return None
            conn = j.get("connection") or {}
            asn = f"AS{conn['asn']} " if conn.get("asn") else ""
            org = conn.get("org") or conn.get("isp")
            tz = (j.get("timezone") or {}).get("id")
            la, lo = j.get("latitude"), j.get("longitude")
            coords = f"{la:.4f},{lo:.4f}" if isinstance(la, (int, float)) and isinstance(lo, (int, float)) else None
            return IPIdentity(
                ip=ip,
                city=j.get("city") or None,
                region=j.get("region") or None,
                country=j.get("country_code") or j.get("country") or None,
                org=(asn + org) if org else None,
                timezone=tz,
                coords=coords,
            )

    @staticmethod
    def tcp_connect(ip: str, port: int = 443, timeout: float = 5.0) -> tuple[bool, float | str]:
        t0 = time.monotonic()
        try:
            with socket.create_connection((ip, port), timeout=timeout):
                return True, (time.monotonic() - t0) * 1000.0
        except Exception as exc:
            return False, str(exc)

    @staticmethod
    def dns_leak_test() -> dict[str, Any]:
        status, body = Probes._http_get_default("https://bash.ws/id", timeout=8.0)
        test_id = body.decode("utf-8").strip()
        if status != 200 or not test_id.isdigit():
            raise RuntimeError("Leak test service did not return an id")

        def lookup(idx: int) -> None:
            try:
                socket.getaddrinfo(f"{idx}.{test_id}.bash.ws", None)
            except Exception:
                pass

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            list(pool.map(lookup, range(1, 11)))

        _, res_body = Probes._http_get_default(f"https://bash.ws/dnsleak/test/{test_id}?json", timeout=12.0)
        arr = json.loads(res_body.decode("utf-8"))
        your_ip = None
        servers = []
        conclusion = None
        for o in arr:
            cc = (o.get("country") or "").upper() or None
            srv = {
                "ip": o.get("ip", ""),
                "country": o.get("country_name"),
                "countryCode": cc,
                "asn": o.get("asn"),
                "flag": IPIdentity(ip=o.get("ip", ""), country=cc).flag,
            }
            typ = o.get("type")
            if typ == "ip":
                your_ip = srv
            elif typ == "dns":
                servers.append(srv)
            elif typ == "conclusion":
                conclusion = o.get("ip")
        return {"yourIP": your_ip, "servers": servers, "conclusion": conclusion}

