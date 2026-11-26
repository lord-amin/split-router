from __future__ import annotations

import re
from dataclasses import dataclass

from .scanner import InterfaceInfo
from .shell import run_cmd

_PROC_RE = re.compile(r'users:\(\("([^"]+)",pid=(\d+)')


@dataclass
class LiveConnection:
    id: str
    pid: int
    process: str
    proto: str
    local_ip: str
    local_port: str
    remote_ip: str
    remote_port: str
    state: str
    interface: str | None


class ConnectionScanner:
    @staticmethod
    def split_host_port(addr: str) -> tuple[str, str] | None:
        if addr.startswith("[") and "]" in addr:
            close = addr.index("]")
            host = addr[1:close].split("%")[0]
            port = addr[close + 1 :].lstrip(":")
            return (host, port)
        if ":" in addr:
            host, port = addr.rsplit(":", 1)
            host = host.split("%")[0]
            return (host, port)
        return None

    @classmethod
    def scan(cls, interfaces: list[InterfaceInfo]) -> list[LiveConnection]:
        by_addr: dict[str, str] = {}
        for i in interfaces:
            for a in i.ipv4:
                by_addr[a["address"]] = i.name
            for a in i.ipv6:
                by_addr[a["address"]] = i.name

        res = run_cmd(["ss", "-tunap"], timeout=8.0)
        if not res.ok:
            return []

        out: list[LiveConnection] = []
        seen: set[str] = set()
        for line in res.stdout.splitlines()[1:]:
            fields = line.split()
            if len(fields) < 6:
                continue
            netid = fields[0].upper()
            state = fields[1]
            local_raw = fields[4]
            remote_raw = fields[5]
            if remote_raw in ("*:*", "0.0.0.0:*", "[::]:*"):
                continue
            lp = cls.split_host_port(local_raw)
            rp = cls.split_host_port(remote_raw)
            if not lp or not rp or rp[0] in ("*", "0.0.0.0", "::"):
                continue

            m = _PROC_RE.search(line)
            if not m:
                # Without root, kernel/root processes don't expose users:((...)) in ss; skip just like lsof without root.
                continue
            proc_name = m.group(1)
            pid = int(m.group(2))
            key = f"{pid}|{netid}|{lp[0]}:{lp[1]}|{rp[0]}:{rp[1]}"
            if key in seen:
                continue
            seen.add(key)
            out.append(LiveConnection(
                id=key,
                pid=pid,
                process=proc_name,
                proto=netid,
                local_ip=lp[0],
                local_port=lp[1],
                remote_ip=rp[0],
                remote_port=rp[1],
                state=state,
                interface=by_addr.get(lp[0]),
            ))
        return out

