from __future__ import annotations

import ipaddress
import json
import re
import socket
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import (
    BACKUPS_DIR,
    LEGACY_WORKSPACE_FILE,
    SPEED_HISTORY_FILE,
    TRAFFIC_HISTORY_FILE,
    UI_PREFS_FILE,
    VERSION,
    WORKSPACE_FILE,
    ensure_user_dirs,
)

TELEGRAM_LIST_ID = "7E1E6A00-0000-4000-8000-000000000001"
TELEGRAM_SOURCE_URL = "https://core.telegram.org/resources/cidr.txt"
TELEGRAM_ENTRIES = [
    "91.108.56.0/22", "91.108.4.0/22", "91.108.8.0/22", "91.108.16.0/22", "91.108.12.0/22",
    "149.154.160.0/20", "91.105.192.0/23", "91.108.20.0/22", "185.76.151.0/24", "95.161.64.0/20",
    "2001:b28:f23d::/48", "2001:b28:f23f::/48", "2001:67c:4e8::/48", "2001:b28:f23c::/48", "2a0a:f280::/32",
]

LINK_LOCAL_RANGES = [
    "169.254.0.0/16", "224.0.0.0/4", "255.255.255.255/32", "fe80::/10", "ff00::/8",
]

_DOMAIN_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_IFACE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{1,14}$")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_dt(raw: str | None) -> datetime:
    if not raw:
        return datetime.now(timezone.utc)
    try:
        s = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


def is_valid_ipv4(s: str) -> bool:
    try:
        ipaddress.IPv4Address(s.strip())
        return True
    except Exception:
        return False


def is_valid_ipv6(s: str) -> bool:
    try:
        base = s.strip().split("%")[0]
        ipaddress.IPv6Address(base)
        return True
    except Exception:
        return False


def is_private_ipv4(s: str) -> bool:
    try:
        ip = ipaddress.IPv4Address(s.strip())
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip in ipaddress.IPv4Network("100.64.0.0/10")
    except Exception:
        return False


def is_valid_domain(raw: str) -> bool:
    d = raw.strip().lower().rstrip(".")
    if not d or len(d) > 253:
        return False
    labels = d.split(".")
    if len(labels) < 2:
        return False
    if any(not _DOMAIN_LABEL_RE.match(lbl) for lbl in labels):
        return False
    if labels[-1].isdigit():
        return False
    return True


def is_valid_interface_name(s: str) -> bool:
    return bool(_IFACE_RE.match(s.strip()))


def normalize_cidr(raw: str) -> str | None:
    s = raw.strip().lower()
    if not s:
        return None
    try:
        if "/" in s:
            net = ipaddress.ip_network(s, strict=False)
            return str(net)
        addr = ipaddress.ip_address(s)
        prefix = 32 if addr.version == 4 else 128
        return f"{addr}/{prefix}"
    except Exception:
        return None


def cidr_contains(cidr: str, ip_str: str) -> bool:
    try:
        net = ipaddress.ip_network(cidr, strict=False)
        ip = ipaddress.ip_address(ip_str.strip())
        return net.version == ip.version and ip in net
    except Exception:
        return False


def parse_ks_entry(raw: str) -> tuple[str, str] | None:
    s = raw.strip().lower()
    if not s:
        return None
    c = normalize_cidr(s)
    if c is not None:
        return ("cidr", c)
    if is_valid_domain(s):
        return ("domain", s.rstrip("."))
    return None


@dataclass
class Destination:
    kind: str  # "host" | "network" | "domain"
    address: str = ""
    prefix: int = 32
    domain: str = ""

    @property
    def display(self) -> str:
        if self.kind == "host":
            return self.address
        if self.kind == "network":
            return f"{self.address}/{self.prefix}"
        return self.domain

    @property
    def kind_label(self) -> str:
        return {"host": "Host", "network": "Network", "domain": "Domain"}[self.kind]

    @classmethod
    def parse(cls, raw: str) -> Destination | None:
        s = raw.strip().lower()
        if not s:
            return None
        if "/" in s:
            try:
                addr_part, p_part = s.split("/", 1)
                ip = ipaddress.IPv4Address(addr_part)
                prefix = int(p_part)
                if not (1 <= prefix <= 32):
                    return None
                if prefix == 32:
                    return cls(kind="host", address=str(ip), prefix=32)
                net = ipaddress.IPv4Network(f"{ip}/{prefix}", strict=False)
                return cls(kind="network", address=str(net.network_address), prefix=prefix)
            except Exception:
                return None
        if is_valid_ipv4(s):
            return cls(kind="host", address=str(ipaddress.IPv4Address(s)), prefix=32)
        if is_valid_domain(s):
            return cls(kind="domain", domain=s.rstrip("."))
        return None


@dataclass
class RouteVia:
    kind: str = "interfaceRouter"  # interfaceRouter | interfaceDirect | gateway
    interface: str = ""
    gateway: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"kind": self.kind}
        if self.interface:
            d["interface"] = self.interface
        if self.gateway:
            d["gateway"] = self.gateway
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RouteVia:
        return cls(
            kind=d.get("kind", "interfaceRouter"),
            interface=d.get("interface", ""),
            gateway=d.get("gateway", ""),
        )

    @property
    def validation_error(self) -> str | None:
        if self.kind in ("interfaceRouter", "interfaceDirect"):
            return None if is_valid_interface_name(self.interface) else "Choose a valid interface"
        if self.kind == "gateway":
            return None if is_valid_ipv4(self.gateway) else "Gateway must be an IPv4 address"
        return "Invalid route mode"

    def key_tuple(self) -> tuple[str, str, str]:
        return (self.kind, self.interface, self.gateway)


@dataclass
class RouteRule:
    id: str = field(default_factory=new_id)
    name: str = ""
    destinations: list[str] = field(default_factory=list)
    via: RouteVia = field(default_factory=RouteVia)
    enabled: bool = True
    persistent: bool = True
    notes: str = ""
    updated_at: str = field(default_factory=now_iso)
    keep_routes_on_remove: bool = False

    def parsed_destinations(self) -> list[Destination]:
        out: list[Destination] = []
        for raw in self.destinations:
            d = Destination.parse(raw)
            if d is not None:
                out.append(d)
        return out

    def invalid_destinations(self) -> list[str]:
        return [raw for raw in self.destinations if Destination.parse(raw) is None]

    def validation_errors(self) -> list[str]:
        errs: list[str] = []
        if not self.name.strip():
            errs.append("Name is required")
        if not self.destinations:
            errs.append("Add at least one IP, network or domain")
        for bad in self.invalid_destinations():
            errs.append(f"“{bad}” is not a valid IPv4 address, CIDR network or domain")
        ve = self.via.validation_error
        if ve:
            errs.append(ve)
        return errs

    def same_content(self, other: RouteRule) -> bool:
        return (
            self.id == other.id
            and self.name == other.name
            and self.destinations == other.destinations
            and self.via.key_tuple() == other.via.key_tuple()
            and self.enabled == other.enabled
            and self.persistent == other.persistent
            and self.notes == other.notes
            and bool(self.keep_routes_on_remove) == bool(other.keep_routes_on_remove)
        )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "destinations": list(self.destinations),
            "via": self.via.to_dict(),
            "enabled": self.enabled,
            "persistent": self.persistent,
            "notes": self.notes,
            "updatedAt": self.updated_at,
        }
        if self.keep_routes_on_remove:
            d["keepRoutesOnRemove"] = True
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RouteRule:
        return cls(
            id=d.get("id", new_id()),
            name=d.get("name", ""),
            destinations=list(d.get("destinations", [])),
            via=RouteVia.from_dict(d.get("via", {})),
            enabled=bool(d.get("enabled", True)),
            persistent=bool(d.get("persistent", True)),
            notes=d.get("notes", ""),
            updated_at=d.get("updatedAt", now_iso()),
            keep_routes_on_remove=bool(d.get("keepRoutesOnRemove", False)),
        )


@dataclass
class BlockList:
    id: str = field(default_factory=new_id)
    name: str = ""
    entries: list[str] = field(default_factory=list)
    enabled: bool = True
    source_url: str | None = None

    @classmethod
    def telegram(cls) -> BlockList:
        return cls(
            id=TELEGRAM_LIST_ID,
            name="Telegram",
            entries=list(TELEGRAM_ENTRIES),
            enabled=True,
            source_url=TELEGRAM_SOURCE_URL,
        )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "entries": list(self.entries),
            "enabled": self.enabled,
        }
        if self.source_url:
            d["sourceURL"] = self.source_url
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BlockList:
        return cls(
            id=d.get("id", new_id()),
            name=d.get("name", ""),
            entries=list(d.get("entries", [])),
            enabled=bool(d.get("enabled", True)),
            source_url=d.get("sourceURL"),
        )


@dataclass
class KillSwitchConfig:
    enabled: bool = False
    mode: str = "destinations"  # destinations | fullSystem
    interfaces: list[str] = field(default_factory=list)
    block_lists: list[BlockList] = field(default_factory=lambda: [BlockList.telegram()])
    allow: list[str] = field(default_factory=list)
    allow_local_networks: bool = True
    allow_rule_destinations: bool = True
    allow_dns: bool = True
    allow_dhcp: bool = True
    learn_vpn_servers: bool = True

    def validation_errors(self) -> list[str]:
        errs: list[str] = []
        if not self.interfaces:
            errs.append("Choose at least one interface to guard")
        for i in self.interfaces:
            if not is_valid_interface_name(i):
                errs.append(f"Invalid interface {i}")
        for bl in self.block_lists:
            for x in bl.entries:
                if parse_ks_entry(x) is None:
                    errs.append(f"{bl.name}: “{x}” is not a CIDR, IP or domain")
        for x in self.allow:
            if parse_ks_entry(x) is None:
                errs.append(f"Allow list: “{x}” is not a CIDR, IP or domain")
        return errs

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "mode": self.mode,
            "interfaces": list(self.interfaces),
            "blockLists": [b.to_dict() for b in self.block_lists],
            "allow": list(self.allow),
            "allowLocalNetworks": self.allow_local_networks,
            "allowRuleDestinations": self.allow_rule_destinations,
            "allowDNS": self.allow_dns,
            "allowDHCP": self.allow_dhcp,
            "learnVPNServers": self.learn_vpn_servers,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> KillSwitchConfig:
        raw_lists = d.get("blockLists")
        lists = [BlockList.from_dict(x) for x in raw_lists] if raw_lists is not None else [BlockList.telegram()]
        return cls(
            enabled=bool(d.get("enabled", False)),
            mode=d.get("mode", "destinations"),
            interfaces=list(d.get("interfaces", [])),
            block_lists=lists,
            allow=list(d.get("allow", [])),
            allow_local_networks=bool(d.get("allowLocalNetworks", True)),
            allow_rule_destinations=bool(d.get("allowRuleDestinations", True)),
            allow_dns=bool(d.get("allowDNS", True)),
            allow_dhcp=bool(d.get("allowDHCP", True)),
            learn_vpn_servers=bool(d.get("learnVPNServers", True)),
        )


@dataclass
class NetworkMatch:
    id: str = field(default_factory=new_id)
    label: str = ""
    router_ip: str = ""
    router_mac: str = ""

    def matches(self, fp: dict[str, Any] | None) -> bool:
        if not fp:
            return False
        other_mac = fp.get("routerMAC", "")
        if self.router_mac and other_mac:
            return self.router_mac.lower() == other_mac.lower()
        return self.router_ip == fp.get("routerIP", "")

    def to_dict(self) -> dict[str, Any]:
        d = {"id": self.id, "label": self.label, "routerIP": self.router_ip}
        if self.router_mac:
            d["routerMAC"] = self.router_mac
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> NetworkMatch:
        return cls(
            id=d.get("id", new_id()),
            label=d.get("label", ""),
            router_ip=d.get("routerIP", ""),
            router_mac=d.get("routerMAC", ""),
        )


@dataclass
class RoutingProfile:
    id: str = field(default_factory=new_id)
    name: str = ""
    rule_ids: list[str] = field(default_factory=list)
    kill_switch: bool | None = None
    networks: list[NetworkMatch] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "ruleIDs": list(self.rule_ids),
            "networks": [n.to_dict() for n in self.networks],
        }
        if self.kill_switch is not None:
            d["killSwitch"] = self.kill_switch
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RoutingProfile:
        return cls(
            id=d.get("id", new_id()),
            name=d.get("name", ""),
            rule_ids=list(d.get("ruleIDs", [])),
            kill_switch=d.get("killSwitch"),
            networks=[NetworkMatch.from_dict(x) for x in d.get("networks", [])],
        )


@dataclass
class TriggerEvent:
    kind: str = "tunnelDown"
    interface: str = ""

    @property
    def label(self) -> str:
        return {
            "tunnelUp": "Full-tunnel VPN connects",
            "tunnelDown": "Full-tunnel VPN disconnects",
            "interfaceUp": f"{self.interface} comes up",
            "interfaceDown": f"{self.interface} goes down",
            "egressChanged": "Internet traffic switches interface",
            "networkChanged": "Joined a different network",
        }.get(self.kind, self.kind)

    def to_dict(self) -> dict[str, Any]:
        d = {"kind": self.kind}
        if self.interface:
            d["interface"] = self.interface
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TriggerEvent:
        return cls(kind=d.get("kind", "tunnelDown"), interface=d.get("interface", ""))


@dataclass
class TriggerAction:
    kind: str = ""
    profile_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = {"kind": self.kind}
        if self.profile_id:
            d["profileID"] = self.profile_id
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TriggerAction:
        return cls(kind=d.get("kind", ""), profile_id=d.get("profileID", ""))


@dataclass
class TriggerRule:
    id: str = field(default_factory=new_id)
    name: str = ""
    enabled: bool = True
    event: TriggerEvent = field(default_factory=TriggerEvent)
    actions: list[TriggerAction] = field(default_factory=list)
    notify: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "enabled": self.enabled,
            "event": self.event.to_dict(),
            "actions": [a.to_dict() for a in self.actions],
            "notify": self.notify,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TriggerRule:
        return cls(
            id=d.get("id", new_id()),
            name=d.get("name", ""),
            enabled=bool(d.get("enabled", True)),
            event=TriggerEvent.from_dict(d.get("event", {})),
            actions=[TriggerAction.from_dict(a) for a in d.get("actions", [])],
            notify=bool(d.get("notify", True)),
        )


@dataclass
class DomainResolver:
    id: str = field(default_factory=new_id)
    domain: str = ""
    nameservers: list[str] = field(default_factory=list)
    enabled: bool = True

    @property
    def validation_error(self) -> str | None:
        dom = self.domain.strip().lower()
        single = bool(dom) and "." not in dom and all(c.isalnum() or c == "-" for c in dom)
        if not is_valid_domain(dom) and not single:
            return f"“{self.domain}” is not a valid domain"
        if not self.nameservers:
            return f"{self.domain}: add at least one name server"
        for ns in self.nameservers:
            if not is_valid_ipv4(ns) and not is_valid_ipv6(ns):
                return f"{self.domain}: “{ns}” is not an IP address"
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "domain": self.domain,
            "nameservers": list(self.nameservers),
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DomainResolver:
        return cls(
            id=d.get("id", new_id()),
            domain=d.get("domain", ""),
            nameservers=list(d.get("nameservers", [])),
            enabled=bool(d.get("enabled", True)),
        )


@dataclass
class BlockEntry:
    id: str = field(default_factory=new_id)
    value: str = ""
    note: str = ""
    enabled: bool = True

    @property
    def is_valid(self) -> bool:
        return parse_ks_entry(self.value) is not None

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "value": self.value, "note": self.note, "enabled": self.enabled}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BlockEntry:
        return cls(
            id=d.get("id", new_id()),
            value=d.get("value", ""),
            note=d.get("note", ""),
            enabled=bool(d.get("enabled", True)),
        )


@dataclass
class AutomationConfig:
    profiles: list[RoutingProfile] = field(default_factory=list)
    manual_profile_id: str | None = None
    auto_switch_by_network: bool = True
    triggers: list[TriggerRule] = field(default_factory=list)
    resolvers: list[DomainResolver] = field(default_factory=list)
    blocked: list[BlockEntry] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return (
            not self.profiles
            and not self.triggers
            and not self.resolvers
            and not self.blocked
            and not self.manual_profile_id
        )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "profiles": [p.to_dict() for p in self.profiles],
            "autoSwitchByNetwork": self.auto_switch_by_network,
            "triggers": [t.to_dict() for t in self.triggers],
            "resolvers": [r.to_dict() for r in self.resolvers],
            "blocked": [b.to_dict() for b in self.blocked],
        }
        if self.manual_profile_id:
            d["manualProfileID"] = self.manual_profile_id
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AutomationConfig:
        return cls(
            profiles=[RoutingProfile.from_dict(x) for x in d.get("profiles", [])],
            manual_profile_id=d.get("manualProfileID") or None,
            auto_switch_by_network=bool(d.get("autoSwitchByNetwork", True)),
            triggers=[TriggerRule.from_dict(x) for x in d.get("triggers", [])],
            resolvers=[DomainResolver.from_dict(x) for x in d.get("resolvers", [])],
            blocked=[BlockEntry.from_dict(x) for x in d.get("blocked", [])],
        )


@dataclass
class NotificationPrefs:
    public_ip: bool = True
    egress: bool = True
    rule_failures: bool = True
    kill_switch: bool = True
    network_events: bool = False
    updates: bool = True

    def wants(self, event: dict[str, Any]) -> bool:
        if event.get("notify"):
            return True
        kind = event.get("kind", "")
        if kind in ("tunnelUp", "tunnelDown", "egressChanged"):
            return self.egress
        if kind in ("killSwitchArmed", "killSwitchDisarmed"):
            return self.kill_switch
        if kind == "ruleFailed":
            return self.rule_failures
        if kind in ("networkChanged", "profileChanged", "interfaceUp", "interfaceDown"):
            return self.network_events
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "publicIP": self.public_ip,
            "egress": self.egress,
            "ruleFailures": self.rule_failures,
            "killSwitch": self.kill_switch,
            "networkEvents": self.network_events,
            "updates": self.updates,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> NotificationPrefs:
        return cls(
            public_ip=bool(d.get("publicIP", True)),
            egress=bool(d.get("egress", True)),
            rule_failures=bool(d.get("ruleFailures", True)),
            kill_switch=bool(d.get("killSwitch", True)),
            network_events=bool(d.get("networkEvents", False)),
            updates=bool(d.get("updates", True)),
        )


@dataclass
class AppSettings:
    refresh_seconds: float = 5.0
    service_interval_seconds: int = 60
    geo_provider: str = "ipinfo"  # ipinfo | ipwhois
    auto_backup_limit: int = 25
    show_system_interfaces: bool = False
    hide_link_layer_routes: bool = True
    notifications: NotificationPrefs = field(default_factory=NotificationPrefs)
    check_for_updates: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "refreshSeconds": self.refresh_seconds,
            "serviceIntervalSeconds": self.service_interval_seconds,
            "geoProvider": self.geo_provider,
            "autoBackupLimit": self.auto_backup_limit,
            "showSystemInterfaces": self.show_system_interfaces,
            "hideLinkLayerRoutes": self.hide_link_layer_routes,
            "notifications": self.notifications.to_dict(),
            "checkForUpdates": self.check_for_updates,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AppSettings:
        return cls(
            refresh_seconds=float(d.get("refreshSeconds", 5.0)),
            service_interval_seconds=int(d.get("serviceIntervalSeconds", 60)),
            geo_provider=d.get("geoProvider", "ipinfo"),
            auto_backup_limit=int(d.get("autoBackupLimit", 25)),
            show_system_interfaces=bool(d.get("showSystemInterfaces", False)),
            hide_link_layer_routes=bool(d.get("hideLinkLayerRoutes", True)),
            notifications=NotificationPrefs.from_dict(d.get("notifications", {})),
            check_for_updates=bool(d.get("checkForUpdates", True)),
        )


@dataclass
class Workspace:
    rules: list[RouteRule] = field(default_factory=list)
    aliases: dict[str, str] = field(default_factory=dict)
    settings: AppSettings = field(default_factory=AppSettings)
    kill_switch: KillSwitchConfig = field(default_factory=KillSwitchConfig)
    setup_completed: bool = False
    automation: AutomationConfig = field(default_factory=AutomationConfig)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rules": [r.to_dict() for r in self.rules],
            "aliases": dict(self.aliases),
            "settings": self.settings.to_dict(),
            "killSwitch": self.kill_switch.to_dict(),
            "setupCompleted": self.setup_completed,
            "automation": self.automation.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Workspace:
        return cls(
            rules=[RouteRule.from_dict(x) for x in d.get("rules", [])],
            aliases=dict(d.get("aliases", {})),
            settings=AppSettings.from_dict(d.get("settings", {})),
            kill_switch=KillSwitchConfig.from_dict(d.get("killSwitch", {})),
            setup_completed=bool(d.get("setupCompleted", False)),
            automation=AutomationConfig.from_dict(d.get("automation", {})),
        )


@dataclass
class IPIdentity:
    ip: str
    city: str | None = None
    region: str | None = None
    country: str | None = None
    org: str | None = None
    timezone: str | None = None
    coords: str | None = None

    @property
    def flag(self) -> str | None:
        if not self.country or len(self.country) != 2:
            return None
        code = self.country.upper()
        if not ("AA" <= code <= "ZZ" and code.isalpha()):
            return None
        return "".join(chr(127397 + ord(ch)) for ch in code)

    @property
    def location(self) -> str:
        return ", ".join(p for p in (self.city, self.region, self.country) if p)


@dataclass
class PublicIPResult:
    id: str
    title: str
    interface: str | None = None
    v4: IPIdentity | None = None
    v6: IPIdentity | None = None
    v4_error: str | None = None
    v6_error: str | None = None
    latency_ms: float | None = None

    @property
    def ip(self) -> str | None:
        return self.v4.ip if self.v4 else (self.v6.ip if self.v6 else None)

    @property
    def primary(self) -> IPIdentity | None:
        return self.v4 or self.v6

    @property
    def error(self) -> str | None:
        if self.ip is not None:
            return None
        return self.v4_error or self.v6_error or "Not available"


@dataclass
class TrafficRate:
    rx: float = 0.0
    tx: float = 0.0


class TrafficHistory:
    def __init__(self, minutes: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.recent: dict[str, list[dict[str, Any]]] = {}
        self.minutes: dict[str, list[dict[str, Any]]] = minutes or {}
        self._bucket_start: float | None = None
        self._bucket: dict[str, list[float]] = {}

    def add(self, rates: dict[str, TrafficRate], seconds: float, ts: float) -> bool:
        for name, r in rates.items():
            lst = self.recent.setdefault(name, [])
            lst.append({"ts": ts, "rx": r.rx, "tx": r.tx})
            self.recent[name] = [x for x in lst if ts - x["ts"] <= 3600]
            b = self._bucket.setdefault(name, [0.0, 0.0])
            b[0] += r.rx * seconds
            b[1] += r.tx * seconds
        minute = float(int(ts // 60) * 60)
        if self._bucket_start is None:
            self._bucket_start = minute
            return False
        if minute <= self._bucket_start:
            return False
        for name, (rx, tx) in self._bucket.items():
            mlst = self.minutes.setdefault(name, [])
            mlst.append({"ts": self._bucket_start, "rx": rx, "tx": tx})
            self.minutes[name] = [x for x in mlst if ts - x["ts"] <= 7 * 86400]
        self._bucket = {}
        self._bucket_start = minute
        return True

    @classmethod
    def load(cls) -> TrafficHistory:
        try:
            data = json.loads(TRAFFIC_HISTORY_FILE.read_text(encoding="utf-8"))
            return cls(minutes=data.get("minutes", {}))
        except Exception:
            return cls()

    def save(self) -> None:
        ensure_user_dirs()
        atomic_write_json(TRAFFIC_HISTORY_FILE, {"minutes": self.minutes})


@dataclass
class BackupFile:
    path: Path
    created_at: datetime
    reason: str
    rule_count: int


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


class Storage:
    @staticmethod
    def load_workspace() -> Workspace:
        ensure_user_dirs()
        if not WORKSPACE_FILE.exists() and LEGACY_WORKSPACE_FILE.exists():
            try:
                data = json.loads(LEGACY_WORKSPACE_FILE.read_text(encoding="utf-8"))
                ws = Workspace.from_dict(data)
                Storage.save_workspace(ws)
                return ws
            except Exception:
                pass
        try:
            data = json.loads(WORKSPACE_FILE.read_text(encoding="utf-8"))
            return Workspace.from_dict(data)
        except Exception:
            ws = Workspace()
            Storage.save_workspace(ws)
            return ws

    @staticmethod
    def save_workspace(ws: Workspace) -> None:
        ensure_user_dirs()
        atomic_write_json(WORKSPACE_FILE, ws.to_dict())

    @staticmethod
    def load_ui_prefs() -> dict[str, Any]:
        ensure_user_dirs()
        defaults = {
            "menuBarEnabled": True,
            "menuBarLabelStyle": "icon",  # icon | flag | flagAndIP | speed
            "speedTestEngine": "cloudflare",
            "speedTestPath": "direct",
            "speedTestLibreServer": "auto",
            "speedTestSkipDataWarning": False,
            "speedTestUnit": "mbps",
            "lastNotifiedEventDate": "",
            "lastUpdateCheck": 0.0,
        }
        try:
            data = json.loads(UI_PREFS_FILE.read_text(encoding="utf-8"))
            defaults.update(data)
        except Exception:
            pass
        return defaults

    @staticmethod
    def save_ui_prefs(prefs: dict[str, Any]) -> None:
        ensure_user_dirs()
        atomic_write_json(UI_PREFS_FILE, prefs)

    @staticmethod
    def make_backup_bundle(ws: Workspace, reason: str, routes_v4: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "format": "SplitRoute.backup",
            "formatVersion": 1,
            "createdAt": now_iso(),
            "appVersion": VERSION,
            "hostName": socket.gethostname(),
            "reason": reason,
            "rules": [r.to_dict() for r in ws.rules],
            "aliases": dict(ws.aliases),
            "settings": ws.settings.to_dict(),
            "routingSnapshot": routes_v4,
            "killSwitch": ws.kill_switch.to_dict(),
            "automation": ws.automation.to_dict(),
        }

    @staticmethod
    def read_backup(path: Path) -> dict[str, Any]:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("format") not in ("SplitRoute.backup", "BlueIPRouter.backup"):
            raise ValueError("This file is not a SplitRoute backup.")
        return data

    @staticmethod
    def save_auto_backup(bundle: dict[str, Any], limit: int) -> None:
        ensure_user_dirs()
        dt = parse_dt(bundle.get("createdAt"))
        stamp = dt.strftime("%Y%m%d-%H%M%S")
        reason = "".join(ch for ch in bundle.get("reason", "auto").lower().replace(" ", "-") if ch.isalpha() or ch == "-")
        path = BACKUPS_DIR / f"{stamp}-{reason or 'backup'}.json"
        atomic_write_json(path, bundle)
        all_files = Storage.list_backups()
        for old in all_files[max(1, limit):]:
            try:
                old.path.unlink()
            except Exception:
                pass

    @staticmethod
    def list_backups() -> list[BackupFile]:
        ensure_user_dirs()
        out: list[BackupFile] = []
        for p in BACKUPS_DIR.glob("*.json"):
            try:
                b = Storage.read_backup(p)
                out.append(BackupFile(
                    path=p,
                    created_at=parse_dt(b.get("createdAt")),
                    reason=b.get("reason", "Backup"),
                    rule_count=len(b.get("rules", [])),
                ))
            except Exception:
                continue
        out.sort(key=lambda x: x.created_at, reverse=True)
        return out


class SpeedHistoryStore:
    @staticmethod
    def load() -> list[dict[str, Any]]:
        try:
            data = json.loads(SPEED_HISTORY_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            return []

    @staticmethod
    def save(items: list[dict[str, Any]]) -> None:
        ensure_user_dirs()
        atomic_write_json(SPEED_HISTORY_FILE, items[-200:])


def format_bytes(n: int | float) -> str:
    val = float(max(0, n))
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if val < 1024 or unit == "TiB":
            if unit == "B":
                return f"{int(val)} B"
            return f"{val:.1f} {unit}"
        val /= 1024.0
    return f"{val:.1f} TiB"


def format_rate(bps: float | None) -> str:
    if bps is None:
        return "—"
    return f"{format_bytes(bps)}/s"


def short_rate(bps: float) -> str:
    b = max(0.0, bps)
    if b >= 1_048_576:
        return f"{b / 1_048_576:.1f}M"
    if b >= 1024:
        return f"{b / 1024:.0f}K"
    return f"{b:.0f}B"


def compile_nftables_preview(
    mode: str,
    interfaces: list[str],
    blocked: list[str],
    allowed: list[str],
    dns_servers: list[str],
    allow_dhcp: bool,
    kill_switch: bool = True,
    denied: list[str] | None = None,
) -> str:
    denied = denied or []

    def split_v4_v6(items: list[str]) -> tuple[list[str], list[str]]:
        v4, v6 = set(), set()
        for raw in items:
            c = normalize_cidr(raw)
            if not c:
                continue
            if ":" in c:
                v6.add(c)
            else:
                v4.add(c)
        return sorted(v4), sorted(v6)

    lines = [
        "#!/usr/sbin/nft -f",
        "# SplitRoute firewall preview",
        "table inet splitroute {",
    ]

    def add_set(name: str, typ: str, elems: list[str]) -> None:
        if elems:
            lines.append(f"  set {name} {{ type {typ}; flags interval; auto-merge; elements = {{ {', '.join(elems)} }}; }}")

    dv4, dv6 = split_v4_v6(denied)
    add_set("sr_deny_v4", "ipv4_addr", dv4)
    add_set("sr_deny_v6", "ipv6_addr", dv6)
    ifs = [f'"{i}"' for i in interfaces if is_valid_interface_name(i)]
    ks_active = kill_switch and bool(ifs)
    bv4, bv6, av4, av6, nv4, nv6 = [], [], [], [], [], []
    if ks_active:
        if mode == "destinations":
            bv4, bv6 = split_v4_v6(blocked)
            add_set("sr_block_v4", "ipv4_addr", bv4)
            add_set("sr_block_v6", "ipv6_addr", bv6)
        else:
            av4, av6 = split_v4_v6(allowed)
            nv4, nv6 = split_v4_v6(dns_servers)
            add_set("sr_allow_v4", "ipv4_addr", av4)
            add_set("sr_allow_v6", "ipv6_addr", av6)
            add_set("sr_dns_v4", "ipv4_addr", nv4)
            add_set("sr_dns_v6", "ipv6_addr", nv6)

    if_expr = f"oifname {ifs[0]}" if len(ifs) == 1 else f"oifname {{ {', '.join(ifs)} }}"
    lines.append("  chain output {")
    lines.append("    type filter hook output priority filter; policy accept;")
    if dv4:
        lines.append("    ip daddr @sr_deny_v4 reject")
    if dv6:
        lines.append("    ip6 daddr @sr_deny_v6 reject")
    if ks_active:
        if mode == "destinations":
            if bv4:
                lines.append(f"    {if_expr} ip daddr @sr_block_v4 drop")
            if bv6:
                lines.append(f"    {if_expr} ip6 daddr @sr_block_v6 drop")
        else:
            if av4:
                lines.append(f"    {if_expr} ip daddr @sr_allow_v4 accept")
            if av6:
                lines.append(f"    {if_expr} ip6 daddr @sr_allow_v6 accept")
            if nv4:
                lines.append(f"    {if_expr} ip daddr @sr_dns_v4 udp dport 53 accept")
                lines.append(f"    {if_expr} ip daddr @sr_dns_v4 tcp dport 53 accept")
            if nv6:
                lines.append(f"    {if_expr} ip6 daddr @sr_dns_v6 udp dport 53 accept")
                lines.append(f"    {if_expr} ip6 daddr @sr_dns_v6 tcp dport 53 accept")
            if allow_dhcp:
                lines.append(f"    {if_expr} udp sport 68 udp dport 67 accept")
                lines.append(f"    {if_expr} udp sport 546 udp dport 547 accept")
            lines.append(f"    {if_expr} drop")
    lines.append("  }")
    lines.append("  chain input {")
    lines.append("    type filter hook input priority filter; policy accept;")
    if dv4:
        lines.append("    ip saddr @sr_deny_v4 drop")
    if dv6:
        lines.append("    ip6 saddr @sr_deny_v6 drop")
    lines.append("  }")
    lines.append("}")
    return "\n".join(lines) + "\n"

