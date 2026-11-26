from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from gi.repository import GLib

from .models import (
    AutomationConfig,
    BlockEntry,
    BlockList,
    Destination,
    IPIdentity,
    KillSwitchConfig,
    PublicIPResult,
    RouteRule,
    RouteVia,
    Storage,
    TrafficHistory,
    TrafficRate,
    Workspace,
    is_private_ipv4,
    is_valid_interface_name,
    is_valid_ipv4,
    is_valid_ipv6,
    new_id,
    now_iso,
    parse_ks_entry,
)
from .notifier import AvailableUpdate, Notifier, UpdateChecker
from .path_analyzer import PathAnalyzer, PathCheckResult, TracerouteRunner
from .paths import (
    AUTOMATION_CONFIG,
    AUTOSTART_FILE,
    HELPER_BINARY,
    ICON_PATH,
    KILLSWITCH_CONFIG,
    LEARNED_SERVERS,
    LOG_FILE,
    PERSISTENT_RULES,
    PROBE_BINARY,
    RUN_DIR,
    RUNTIME_STATE,
    SESSION_RULES,
    STAGING_DIR,
    SYSTEM_DIR,
    SYSTEMD_UNIT,
    VERSION,
    find_helper_source_binary,
    find_probe_binary,
    find_probe_source_binary,
)
from .probes import Probes
from .scanner import InterfaceInfo, NetworkScanner, NetworkSnapshot, RouteEntry, interface_display_name
from .shell import PrivilegedCancelledError, PrivilegedFailedError, run_privileged, shq
from .speedtest import SpeedPath, SpeedTestRunner


@dataclass
class ActivityEntry:
    title: str
    ok: bool
    detail: str
    date: datetime = field(default_factory=datetime.now)


@dataclass
class PendingChange:
    kind: str  # Added | Removed | Modified
    rule: RouteRule


@dataclass
class ImportCandidate:
    id: str
    destination: str
    via: RouteVia
    entry: RouteEntry


class AppModel:
    def __init__(self) -> None:
        self.workspace: Workspace = Storage.load_workspace()
        self.ui_prefs: dict[str, Any] = Storage.load_ui_prefs()
        self.net: NetworkSnapshot = NetworkSnapshot()
        self.rates: dict[str, TrafficRate] = {}
        self.public_ips: list[PublicIPResult] = []
        self.public_ip_loading: bool = False
        self.public_ip_updated: datetime | None = None
        self._geo_cache: dict[str, IPIdentity] = {}
        self.activity: list[ActivityEntry] = []
        self.busy: str | None = None
        self.selection: str = "dashboard"
        self.path_query: str = ""
        self.path_result: PathCheckResult | None = None
        self.path_checking: bool = False
        self.traceroute = TracerouteRunner()
        self.backups = Storage.list_backups()
        self.helper_log: str = ""
        self.has_scanned: bool = False
        self.traffic = TrafficHistory.load()
        self.speed_test = SpeedTestRunner()
        self.available_update: AvailableUpdate | None = None
        self.checking_for_updates: bool = False

        self._last_egress: str | None = None
        self._last_public_refresh: float = 0.0
        self._last_bytes: dict[str, tuple[int, int]] = {}
        self._last_sample_ts: float | None = None
        self._refresh_inflight = False

        self._listeners: list[Callable[[], None]] = []
        self._alert_cb: Callable[[str, str], None] | None = None
        self._open_window_cb: Callable[[str | None], None] | None = None
        self._open_rule_editor_cb: Callable[[RouteRule], None] | None = None
        self._open_setup_cb: Callable[[], None] | None = None

    def subscribe(self, cb: Callable[[], None]) -> None:
        self._listeners.append(cb)

    def notify(self) -> None:
        for cb in list(self._listeners):
            try:
                cb()
            except Exception:
                pass

    def save_workspace(self) -> None:
        Storage.save_workspace(self.workspace)
        self.notify()

    def set_ui_pref(self, key: str, value: Any) -> None:
        self.ui_prefs[key] = value
        Storage.save_ui_prefs(self.ui_prefs)
        self.notify()

    def bind_ui(
        self,
        alert_cb: Callable[[str, str], None],
        open_window_cb: Callable[[str | None], None],
        open_rule_editor_cb: Callable[[RouteRule], None],
        open_setup_cb: Callable[[], None],
    ) -> None:
        self._alert_cb = alert_cb
        self._open_window_cb = open_window_cb
        self._open_rule_editor_cb = open_rule_editor_cb
        self._open_setup_cb = open_setup_cb

    def show_error(self, title: str, message: str) -> None:
        if self._alert_cb:
            GLib.idle_add(self._alert_cb, title, message)

    def open_main_window(self, page: str | None = None) -> None:
        if page:
            self.selection = page
        if self._open_window_cb:
            self._open_window_cb(page)
        self.notify()

    # Lifecycle & polling

    def start(self) -> None:
        self.refresh(initial=True)
        self._schedule_loop()
        threading.Thread(target=lambda: self.check_for_updates(manual=False), daemon=True).start()

    def _schedule_loop(self) -> None:
        sec = max(2, int(self.workspace.settings.refresh_seconds))
        GLib.timeout_add_seconds(sec, self._on_tick)

    def _on_tick(self) -> bool:
        self.refresh()
        self._schedule_loop()
        return False

    def refresh_all_now(self) -> None:
        self.refresh(force_public=True)

    def refresh(self, initial: bool = False, force_public: bool = False) -> None:
        if self._refresh_inflight:
            return
        self._refresh_inflight = True
        rules = list(self.workspace.rules)

        def worker() -> None:
            try:
                snap = NetworkScanner.scan(rules)
            finally:
                GLib.idle_add(self._on_scan_done, snap, initial, force_public)

        threading.Thread(target=worker, daemon=True).start()

    def _on_scan_done(self, snap: NetworkSnapshot, initial: bool, force_public: bool) -> None:
        self._refresh_inflight = False
        self._update_rates(snap)
        self.net = snap
        self.has_scanned = True
        self._process_events(snap.events)
        egress = (snap.internet_route or {}).get("interface")
        now_m = time.monotonic()
        if (
            initial
            or force_public
            or (self._last_egress is not None and egress != self._last_egress)
            or (now_m - self._last_public_refresh > 300)
        ):
            self.refresh_public_ips()
        self._last_egress = egress
        if initial and not self.workspace.setup_completed and self._open_setup_cb:
            self._open_setup_cb()
        self.notify()

    def _update_rates(self, snap: NetworkSnapshot) -> None:
        now = time.time()
        if self._last_sample_ts is not None:
            dt = now - self._last_sample_ts
            if dt > 0.5:
                for i in snap.interfaces:
                    prev = self._last_bytes.get(i.name)
                    if prev is None:
                        continue
                    rx_d = max(0, i.rx_bytes - prev[0])
                    tx_d = max(0, i.tx_bytes - prev[1])
                    self.rates[i.name] = TrafficRate(rx=rx_d / dt, tx=tx_d / dt)
                tracked = {
                    i.name: self.rates[i.name]
                    for i in snap.interfaces
                    if i.name in self.rates and i.is_up and not i.is_loopback
                }
                if self.traffic.add(tracked, dt, now):
                    self.traffic.save()
        self._last_sample_ts = now
        self._last_bytes = {i.name: (i.rx_bytes, i.tx_bytes) for i in snap.interfaces}

    def refresh_public_ips(self) -> None:
        if self.public_ip_loading:
            return
        self.public_ip_loading = True
        self.notify()
        provider = self.workspace.settings.geo_provider
        aliases = dict(self.workspace.aliases)
        candidates = [
            i for i in self.net.interfaces
            if i.is_active and i.ipv4 and (i.is_physical or i.is_vpn)
        ]
        egress = (self.net.internet_route or {}).get("interface")

        def worker() -> None:
            results: list[PublicIPResult] = []
            ips: set[str] = set()
            r_def, f_def = Probes.probe_path("default", "System default path", None)
            results.append(r_def)
            ips.update(f_def)

            for c in candidates:
                title = f"{interface_display_name(c.name, c, aliases)} ({c.name})"
                if c.is_full_tunnel or c.name == egress:
                    results.append(PublicIPResult(
                        id=c.name,
                        title=title,
                        interface=c.name,
                        v4_error="Carries the system default path (see above)",
                    ))
                else:
                    r_c, f_c = Probes.probe_path(c.name, title, c.name)
                    results.append(r_c)
                    ips.update(f_c)

            for ip in ips:
                if ip not in self._geo_cache:
                    ident = Probes.geolocate(ip, preferred=provider)
                    if ident:
                        self._geo_cache[ip] = ident

            for r in results:
                if r.v4 and r.v4.ip in self._geo_cache:
                    r.v4 = self._geo_cache[r.v4.ip]
                if r.v6 and r.v6.ip in self._geo_cache:
                    r.v6 = self._geo_cache[r.v6.ip]

            results.sort(key=lambda x: ("" if x.id == "default" else x.id))
            GLib.idle_add(self._on_public_ips_done, results)

        threading.Thread(target=worker, daemon=True).start()

    def _on_public_ips_done(self, results: list[PublicIPResult]) -> None:
        before = next((p.v4 for p in self.public_ips if p.id == "default"), None)
        self.public_ips = results
        self.public_ip_loading = False
        self.public_ip_updated = datetime.now()
        self._last_public_refresh = time.monotonic()
        after = next((p.v4 for p in results if p.id == "default"), None)
        if before and after and after.ip != before.ip and self.workspace.settings.notifications.public_ip:
            country = f" — country {before.country or '?'} → {after.country or '?'}" if before.country != after.country else ""
            flag = f" {after.flag}" if after.flag else ""
            sub = " · ".join(p for p in (after.location, after.org or "") if p)
            Notifier.post(f"Public IP changed{flag}", f"{before.ip} → {after.ip}{country}\n{sub}")
            self.log("Public IP changed", True, f"{before.ip} → {after.ip}")
        self.notify()

    def _process_events(self, events: list[dict[str, Any]]) -> None:
        if not events:
            return
        newest = events[-1].get("date", "")
        seen = self.ui_prefs.get("lastNotifiedEventDate", "")
        if not seen:
            self.set_ui_pref("lastNotifiedEventDate", newest)
            return
        fresh = [e for e in events if e.get("date", "") > seen]
        if not fresh:
            return
        self.set_ui_pref("lastNotifiedEventDate", newest)
        prefs = self.workspace.settings.notifications
        for e in fresh[-5:]:
            if prefs.wants(e):
                Notifier.post(e.get("title", "SplitRoute"), e.get("detail", ""), e.get("id"))

    def check_for_updates(self, manual: bool) -> None:
        now = time.time()
        if not manual:
            if not self.workspace.settings.check_for_updates:
                return
            if now - float(self.ui_prefs.get("lastUpdateCheck", 0.0)) < 86400:
                return
        self.checking_for_updates = True
        GLib.idle_add(self.notify)

        def worker() -> None:
            try:
                upd = UpdateChecker.latest()
                GLib.idle_add(self._on_update_checked, upd, manual, None)
            except Exception as exc:
                GLib.idle_add(self._on_update_checked, None, manual, str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _on_update_checked(self, upd: AvailableUpdate | None, manual: bool, err: str | None) -> None:
        self.checking_for_updates = False
        if err:
            if manual:
                self.show_error("Could not check for updates", err)
            self.notify()
            return
        self.set_ui_pref("lastUpdateCheck", time.time())
        self.available_update = upd
        if upd:
            if not manual and self.workspace.settings.notifications.updates:
                Notifier.post(f"SplitRoute {upd.version} is available", "Open the app to download it.")
            self.log(f"Update available: {upd.version}", True)
        elif manual:
            self.show_error("You're up to date", f"SplitRoute {VERSION} is the latest version.")
        self.notify()

    # Derived state

    @property
    def pending_changes(self) -> list[PendingChange]:
        changes: list[PendingChange] = []
        applied = {r.id: r for r in self.net.applied_rules}
        draft = {r.id: r for r in self.workspace.rules}
        for r in self.workspace.rules:
            if r.id in applied:
                if not applied[r.id].same_content(r):
                    changes.append(PendingChange("Modified", r))
            else:
                changes.append(PendingChange("Added", r))
        for a in self.net.applied_rules:
            if a.id not in draft:
                changes.append(PendingChange("Removed", a))
        return changes

    @property
    def kill_switch_pending(self) -> bool:
        applied = self.net.applied_kill_switch
        if applied is None:
            return self.workspace.kill_switch.enabled
        return applied.to_dict() != self.workspace.kill_switch.to_dict()

    @property
    def automation_pending(self) -> bool:
        applied = self.net.applied_automation
        if applied is None:
            return not self.workspace.automation.is_empty
        return applied.to_dict() != self.workspace.automation.to_dict()

    @property
    def pending_count(self) -> int:
        return len(self.pending_changes) + (1 if self.kill_switch_pending else 0) + (1 if self.automation_pending else 0)

    @property
    def has_pending(self) -> bool:
        return self.pending_count > 0

    @property
    def live_but_unmanaged(self) -> list[RouteRule]:
        pending_ids = {c.rule.id for c in self.pending_changes if c.kind != "Removed"}
        return [
            r for r in self.workspace.rules
            if r.id in pending_ids and (self.net.live_routes.get(r.id) and self.net.live_routes[r.id].all_live)
        ]

    @property
    def daemon_outdated(self) -> bool:
        s = self.net.service
        if s.helper_installed and s.helper_version and s.helper_version != VERSION:
            return True
        if s.daemon_installed and self.net.installed_unit is not None:
            return self.net.installed_unit != self.systemd_unit_content(self.workspace.settings.service_interval_seconds)
        return False

    @property
    def full_tunnel(self) -> InterfaceInfo | None:
        return next((i for i in self.net.interfaces if i.is_full_tunnel and i.is_up), None)

    @property
    def local_interface(self) -> InterfaceInfo | None:
        p = self.interface(self.net.primary_interface)
        if p and p.is_physical and p.ipv4:
            return p
        return (
            next((i for i in self.net.interfaces if i.is_physical and i.is_active and i.router), None)
            or next((i for i in self.net.interfaces if i.is_physical and i.ipv4), None)
        )

    @property
    def visible_interfaces(self) -> list[InterfaceInfo]:
        return [
            i for i in self.net.interfaces
            if self.workspace.settings.show_system_interfaces or not i.is_system_noise or i.name == self.net.primary_interface
        ]

    @property
    def internet_rate(self) -> TrafficRate:
        egress = (self.net.internet_route or {}).get("interface")
        if egress and egress in self.rates:
            return self.rates[egress]
        return TrafficRate(0.0, 0.0)

    @property
    def background_service_running(self) -> bool:
        return self.net.service.daemon_installed and self.net.service.loaded

    @property
    def open_at_login(self) -> bool:
        return AUTOSTART_FILE.is_file()

    @property
    def default_guarded_interfaces(self) -> list[str]:
        return [
            i.name for i in self.net.interfaces
            if (i.is_physical or i.kind in ("ppp", "ipsec")) and i.is_active
        ]

    def status_for(self, rule: RouteRule) -> dict[str, Any] | None:
        hs = self.net.helper_status or {}
        return next((st for st in (hs.get("rules") or []) if st.get("ruleID") == rule.id), None)

    def interface(self, name: str | None) -> InterfaceInfo | None:
        if not name:
            return None
        return next((i for i in self.net.interfaces if i.name == name), None)

    def display_name(self, name: str) -> str:
        return interface_display_name(name, self.interface(name), self.workspace.aliases)

    def describe_via(self, via: RouteVia) -> str:
        if via.kind == "interfaceRouter":
            info = self.interface(via.interface)
            r = f" → {info.router}" if (info and info.router) else ""
            return f"{self.display_name(via.interface)} ({via.interface}) router{r}"
        if via.kind == "interfaceDirect":
            return f"Direct on {self.display_name(via.interface)} ({via.interface})"
        return f"Gateway {via.gateway}"

    def describe_action(self, action_kind: str, profile_id: str = "") -> str:
        if action_kind == "activateProfile":
            pname = next((p.name for p in self.workspace.automation.profiles if p.id == profile_id), "deleted profile")
            return f"switch to “{pname}”"
        return {
            "clearProfile": "clear the profile",
            "killSwitchOn": "kill switch on",
            "killSwitchOff": "kill switch off",
            "killSwitchAuto": "kill switch to its setting",
            "learnVPNServers": "re-learn VPN servers",
        }.get(action_kind, action_kind)

    def owner_of(self, entry: RouteEntry) -> str | None:
        c = entry.cidr()
        if not c:
            return None
        addr, prefix = c
        installed = (self.net.helper_status or {}).get("installedRoutes") or []
        m = next((r for r in installed if r.get("destination") == addr and r.get("prefix") == prefix), None)
        if m:
            rname = next((r.name for r in self.workspace.rules if r.id == m.get("ruleID")), "deleted")
            return f"Rule: {rname}"
        info = self.interface(entry.netif)
        if info and info.is_full_tunnel and entry.destination in ("0.0.0.0/1", "128.0.0.0/1"):
            return "Tunnel catch-all"
        return None

    def log(self, title: str, ok: bool, detail: str = "") -> None:
        self.activity.insert(0, ActivityEntry(title=title, ok=ok, detail=detail.strip()))
        if len(self.activity) > 500:
            del self.activity[500:]

    # Privileged helper & systemd installation helpers

    def _install_helper_commands(self) -> list[str]:
        src = find_helper_source_binary()
        if src is None:
            raise RuntimeError("splitroute-helper binary not found. Run scripts/build.sh first.")
        cmds = [
            "install -d -o root -g root -m 755 /usr/local/libexec",
            f"install -d -o root -g root -m 755 {shq(SYSTEM_DIR)}",
            f"install -d -o root -g root -m 755 {shq(RUN_DIR)}",
        ]
        if src.resolve() != Path(HELPER_BINARY).resolve():
            cmds.append(f"install -o root -g root -m 755 {shq(str(src))} {shq(HELPER_BINARY)}")
        probe_src = find_probe_source_binary()
        if probe_src is not None:
            if probe_src.resolve() != Path(PROBE_BINARY).resolve():
                cmds.append(f"install -o root -g root -m 755 {shq(str(probe_src))} {shq(PROBE_BINARY)}")
            cmds.append(f"(setcap cap_net_raw=ep {shq(PROBE_BINARY)} || true)")
        return cmds

    @staticmethod
    def systemd_unit_content(interval: int) -> str:
        interval = max(15, int(interval))
        return (
            "[Unit]\n"
            "Description=SplitRoute per-destination routing and nftables kill switch service\n"
            "After=network-online.target systemd-resolved.service\n"
            "Wants=network-online.target\n\n"
            "[Service]\n"
            "Type=simple\n"
            f"ExecStart={HELPER_BINARY} daemon --interval {interval}\n"
            "ExecReload=/bin/kill -HUP $MAINPID\n"
            "Restart=on-failure\n"
            "RestartSec=5\n\n"
            "[Install]\n"
            "WantedBy=multi-user.target\n"
        )

    def _install_daemon_commands(self, interval: int) -> list[str]:
        unit_stage = STAGING_DIR / "splitroute-helper.service"
        unit_stage.write_text(self.systemd_unit_content(interval), encoding="utf-8")
        return [
            "(systemctl disable --now blueiprouter-helper.service || true)",
            f"install -o root -g root -m 644 {shq(str(unit_stage))} {shq(SYSTEMD_UNIT)}",
            "systemctl daemon-reload",
            "systemctl enable --now splitroute-helper.service",
        ]

    @staticmethod
    def _remove_daemon_commands() -> list[str]:
        return [
            "(systemctl disable --now splitroute-helper.service || true)",
            f"rm -f {shq(SYSTEMD_UNIT)}",
            "systemctl daemon-reload",
        ]

    def _run_privileged_async(
        self,
        title: str,
        commands: list[str],
        stop_on_error: bool = True,
        on_done: Callable[[bool], None] | None = None,
    ) -> None:
        self.busy = title
        self.notify()

        def worker() -> None:
            ok = False
            err_title = title
            err_msg = ""
            out = ""
            try:
                out = run_privileged(commands, title=title, stop_on_error=stop_on_error)
                ok = True
            except PrivilegedCancelledError:
                err_msg = "Cancelled by user"
            except PrivilegedFailedError as exc:
                err_msg = str(exc)
            except Exception as exc:
                err_msg = str(exc)

            def finish() -> None:
                self.busy = None
                if ok:
                    self.log(title, True, out)
                else:
                    self.log(title, False, err_msg)
                    if err_msg != "Cancelled by user":
                        self.show_error(err_title, err_msg)
                self.refresh()
                if on_done:
                    on_done(ok)

            GLib.idle_add(finish)

        threading.Thread(target=worker, daemon=True).start()

    # Rules CRUD

    def new_rule(self, destination: str | None = None) -> None:
        lan = self.local_interface
        iface_name = (
            (lan.name if lan else None)
            or self.net.primary_interface
            or next((i.name for i in self.net.interfaces if i.is_active and not i.is_loopback), "")
        )
        rule = RouteRule(
            destinations=[destination] if destination else [],
            via=RouteVia(kind="interfaceRouter", interface=iface_name),
        )
        if self._open_rule_editor_cb:
            self._open_rule_editor_cb(rule)

    def edit_rule(self, rule: RouteRule) -> None:
        if self._open_rule_editor_cb:
            self._open_rule_editor_cb(rule)

    def upsert_rule(self, rule: RouteRule) -> None:
        rule.updated_at = now_iso()
        for idx, r in enumerate(self.workspace.rules):
            if r.id == rule.id:
                self.workspace.rules[idx] = rule
                break
        else:
            self.workspace.rules.append(rule)
        self.save_workspace()

    def delete_rules(self, ids: set[str]) -> None:
        self.workspace.rules = [r for r in self.workspace.rules if r.id not in ids]
        self.save_workspace()

    def duplicate_rule(self, rule: RouteRule) -> None:
        copy = RouteRule.from_dict(rule.to_dict())
        copy.id = new_id()
        copy.name += " copy"
        self.workspace.rules.append(copy)
        self.save_workspace()

    def set_enabled(self, rule_id: str, value: bool) -> None:
        for r in self.workspace.rules:
            if r.id == rule_id:
                r.enabled = value
                break
        self.save_workspace()

    def set_persistent(self, rule_id: str, value: bool) -> None:
        for r in self.workspace.rules:
            if r.id == rule_id:
                r.persistent = value
                break
        self.save_workspace()

    def discard_changes(self) -> None:
        self.workspace.rules = [RouteRule.from_dict(r.to_dict()) for r in self.net.applied_rules]
        if self.net.applied_kill_switch is not None:
            self.workspace.kill_switch = KillSwitchConfig.from_dict(self.net.applied_kill_switch.to_dict())
        else:
            self.workspace.kill_switch.enabled = False
        if self.net.applied_automation is not None:
            self.workspace.automation = AutomationConfig.from_dict(self.net.applied_automation.to_dict())
        else:
            self.workspace.automation = AutomationConfig()
        self.save_workspace()
        self.log("Discarded pending changes", True)

    # Apply

    def apply(self, title: str = "apply routing changes") -> None:
        for r in self.workspace.rules:
            errs = r.validation_errors()
            if errs:
                self.show_error(f"Rule “{r.name or 'Untitled'}” is invalid", "\n".join(errs))
                return
        if self.workspace.kill_switch.enabled:
            k_errs = self.workspace.kill_switch.validation_errors()
            if k_errs:
                self.show_error("Kill switch settings are invalid", k_errs[0])
                return

        try:
            STAGING_DIR.mkdir(parents=True, exist_ok=True)
            k_path = STAGING_DIR / "killswitch.json"
            au_path = STAGING_DIR / "automation.json"
            p_path = STAGING_DIR / "rules.json"
            s_path = STAGING_DIR / "session-rules.json"
            from .models import atomic_write_json

            atomic_write_json(k_path, self.workspace.kill_switch.to_dict())
            atomic_write_json(au_path, self.workspace.automation.to_dict())
            atomic_write_json(p_path, {"version": 1, "rules": [r.to_dict() for r in self.workspace.rules if r.persistent]})
            atomic_write_json(s_path, {"version": 1, "rules": [r.to_dict() for r in self.workspace.rules if not r.persistent]})

            cmds = self._install_helper_commands() + [
                f"install -o root -g root -m 644 {shq(str(p_path))} {shq(PERSISTENT_RULES)}",
                f"install -o root -g root -m 644 {shq(str(s_path))} {shq(SESSION_RULES)}",
                f"install -o root -g root -m 644 {shq(str(k_path))} {shq(KILLSWITCH_CONFIG)}",
                f"install -o root -g root -m 644 {shq(str(au_path))} {shq(AUTOMATION_CONFIG)}",
                f"{shq(HELPER_BINARY)} apply --trigger app",
            ]
            needs_service = (
                any(r.enabled and r.persistent for r in self.workspace.rules)
                or self.workspace.kill_switch.enabled
                or not self.workspace.automation.is_empty
            )
            if (needs_service and not self.net.service.daemon_installed) or self.daemon_outdated:
                cmds += self._install_daemon_commands(self.workspace.settings.service_interval_seconds)
            elif self.net.service.daemon_installed:
                cmds.append("(systemctl restart splitroute-helper.service || true)")

            def after(ok: bool) -> None:
                if ok:
                    self.auto_backup("Applied")

            self._run_privileged_async(title, cmds, on_done=after)
        except Exception as exc:
            self.show_error("Could not apply", str(exc))

    def run_service_now(self) -> None:
        if not self.net.service.helper_installed:
            self.apply()
            return
        self._run_privileged_async("re-check all routes now", [f"{shq(HELPER_BINARY)} apply --trigger manual"])

    def set_service(self, enabled: bool) -> None:
        try:
            if enabled:
                cmds = self._install_helper_commands() + self._install_daemon_commands(self.workspace.settings.service_interval_seconds)
                self._run_privileged_async("turn on the background route service", cmds)
            else:
                self._run_privileged_async("turn off the background route service", self._remove_daemon_commands())
        except Exception as exc:
            self.show_error("Background service", str(exc))

    def set_open_at_login(self, enabled: bool) -> None:
        try:
            if enabled:
                AUTOSTART_FILE.parent.mkdir(parents=True, exist_ok=True)
                run_sh = Path(__file__).resolve().parent.parent / "scripts" / "run.sh"
                exec_cmd = str(run_sh) if run_sh.is_file() else "splitroute"
                icon = str(ICON_PATH) if ICON_PATH.is_file() else "network-vpn"
                desktop = (
                    "[Desktop Entry]\n"
                    "Type=Application\n"
                    "Name=SplitRoute\n"
                    "Comment=Per-destination routing and nftables kill switch\n"
                    f"Exec={exec_cmd}\n"
                    f"Icon={icon}\n"
                    "Terminal=false\n"
                    "Categories=Network;Settings;\n"
                    "X-GNOME-Autostart-enabled=true\n"
                )
                AUTOSTART_FILE.write_text(desktop, encoding="utf-8")
                self.log("Turned on Open at Login", True)
            else:
                if AUTOSTART_FILE.exists():
                    AUTOSTART_FILE.unlink()
                self.log("Turned off Open at Login", True)
        except Exception as exc:
            self.show_error("Open at Login", str(exc))
        self.notify()

    def load_helper_log(self) -> None:
        self.helper_log = NetworkScanner.tail_log(LOG_FILE)
        self.notify()

    # Block list & Kill switch

    def block(self, value: str, note: str = "") -> None:
        v = value.strip().lower()
        if parse_ks_entry(v) is None:
            return
        for b in self.workspace.automation.blocked:
            if b.value == v:
                b.enabled = True
                break
        else:
            self.workspace.automation.blocked.append(BlockEntry(value=v, note=note))
        self.log(f"Added {v} to the block list", True, "Press Apply to enforce it.")
        self.save_workspace()

    def block_and_apply(self, value: str, note: str = "") -> None:
        self.block(value, note=note)
        self.apply(title=f"block {value}")

    def detect_vpn_servers(self) -> None:
        try:
            cmds = self._install_helper_commands() + [f"{shq(HELPER_BINARY)} detect-vpn"]
            self._run_privileged_async("detect the servers your VPN is connected to", cmds)
        except Exception as exc:
            self.show_error("Detect VPN servers", str(exc))

    def forget_learned_servers(self) -> None:
        self._run_privileged_async("forget learned VPN servers", [f"rm -f {shq(LEARNED_SERVERS)}"])

    def update_block_list(self, list_id: str) -> None:
        target = next((b for b in self.workspace.kill_switch.block_lists if b.id == list_id), None)
        if not target or not target.source_url:
            return
        self.busy = f"downloading {target.name} ranges"
        self.notify()

        def worker() -> None:
            import urllib.request
            from .models import normalize_cidr

            try:
                req = urllib.request.Request(target.source_url, headers={"User-Agent": f"SplitRoute/{VERSION}"})
                with urllib.request.urlopen(req, timeout=15.0) as resp:
                    text = resp.read().decode("utf-8", errors="replace")
                entries = [ln.strip() for ln in text.splitlines() if normalize_cidr(ln.strip()) is not None]
                if not entries:
                    raise ValueError("Empty range list")

                def ok_done() -> None:
                    self.busy = None
                    target.entries = entries
                    self.log(f"Updated {target.name} ranges", True, f"{len(entries)} CIDRs")
                    self.save_workspace()

                GLib.idle_add(ok_done)
            except Exception as exc:
                def err_done() -> None:
                    self.busy = None
                    self.show_error("Could not download ranges", str(exc))
                    self.notify()

                GLib.idle_add(err_done)

        threading.Thread(target=worker, daemon=True).start()

    def turn_off_kill_switch_now(self) -> None:
        self.workspace.kill_switch.enabled = False
        self.save_workspace()
        self.apply(title="turn off the kill switch")

    # Import existing static routes

    @property
    def import_candidates(self) -> list[ImportCandidate]:
        sys_addrs: set[str] = set()
        for i in self.net.interfaces:
            for a in i.ipv4:
                sys_addrs.add(a["address"])
                if a.get("peer"):
                    sys_addrs.add(a["peer"])
            if i.router:
                sys_addrs.add(i.router)

        out: list[ImportCandidate] = []
        for e in self.net.routes_v4:
            if not e.is_static or e.is_link_layer or e.is_default or self.owner_of(e) is not None:
                continue
            c = e.cidr()
            if not c:
                continue
            addr, prefix = c
            if (
                addr in sys_addrs
                or prefix < 8
                or not is_valid_interface_name(e.netif)
                or e.netif.startswith("lo")
                or addr.startswith(("169.254.", "224.", "127."))
                or addr == "255.255.255.255"
            ):
                continue
            if is_valid_ipv4(e.gateway):
                info = self.interface(e.netif)
                if info and info.router == e.gateway:
                    via = RouteVia(kind="interfaceRouter", interface=e.netif)
                else:
                    via = RouteVia(kind="gateway", gateway=e.gateway)
            else:
                via = RouteVia(kind="interfaceDirect", interface=e.netif)
            dest = addr if prefix == 32 else f"{addr}/{prefix}"
            out.append(ImportCandidate(id=e.id, destination=dest, via=via, entry=e))
        return out

    def import_routes(self, candidates: list[ImportCandidate], persistent: bool) -> None:
        groups: dict[tuple[str, str, str], tuple[RouteVia, list[str]]] = {}
        for c in candidates:
            k = c.via.key_tuple()
            if k not in groups:
                groups[k] = (c.via, [])
            groups[k][1].append(c.destination)
        for via, dests in groups.values():
            lbl = self.display_name(via.interface) if via.interface else via.gateway
            self.workspace.rules.append(RouteRule(
                name=f"Imported via {lbl}",
                destinations=dests,
                via=via,
                persistent=persistent,
                notes=f"Imported from the routing table on {datetime.now().strftime('%Y-%m-%d')}",
            ))
        self.log(f"Imported {len(candidates)} static routes into {len(groups)} rules", True)
        self.selection = "rules"
        self.save_workspace()

    # Backups & Reset

    def auto_backup(self, reason: str) -> None:
        routes_raw = [{"destination": r.destination, "gateway": r.gateway, "netif": r.netif, "protocol": r.protocol} for r in self.net.routes_v4]
        bundle = Storage.make_backup_bundle(self.workspace, reason, routes_raw)
        Storage.save_auto_backup(bundle, max(1, self.workspace.settings.auto_backup_limit))
        self.backups = Storage.list_backups()
        self.notify()

    def restore_backup(self, bundle: dict[str, Any], replace: bool, include_settings: bool) -> None:
        self.auto_backup("Before restore")
        restored_rules = [RouteRule.from_dict(x) for x in bundle.get("rules", [])]
        if replace:
            self.workspace.rules = restored_rules
        else:
            by_id = {r.id: idx for idx, r in enumerate(self.workspace.rules)}
            for r in restored_rules:
                if r.id in by_id:
                    self.workspace.rules[by_id[r.id]] = r
                else:
                    self.workspace.rules.append(r)
        if bundle.get("killSwitch") and (replace or include_settings):
            self.workspace.kill_switch = KillSwitchConfig.from_dict(bundle["killSwitch"])
        if bundle.get("automation") and (replace or include_settings):
            self.workspace.automation = AutomationConfig.from_dict(bundle["automation"])
        if include_settings:
            self.workspace.aliases.update(bundle.get("aliases", {}))
            if bundle.get("settings"):
                from .models import AppSettings
                self.workspace.settings = AppSettings.from_dict(bundle["settings"])
        self.log("Restored backup", True, f"{len(restored_rules)} rules")
        self.selection = "rules"
        self.save_workspace()

    def delete_backup(self, path: Path) -> None:
        try:
            path.unlink()
        except Exception:
            pass
        self.backups = Storage.list_backups()
        self.notify()

    def reset_to_default(
        self,
        backup_first: bool,
        remove_routes: bool,
        delete_rules: bool,
        uninstall_service: bool,
        reset_aliases_settings: bool,
    ) -> None:
        if backup_first:
            self.auto_backup("Before reset")
        cmds: list[str] = []
        if remove_routes and self.net.service.helper_installed:
            cmds.append(f"{shq(HELPER_BINARY)} remove-all")
        if delete_rules:
            cmds.append(
                f"rm -f {shq(PERSISTENT_RULES)} {shq(SESSION_RULES)} {shq(KILLSWITCH_CONFIG)} "
                f"{shq(LEARNED_SERVERS)} {shq(AUTOMATION_CONFIG)} {shq(RUNTIME_STATE)}"
            )
        if uninstall_service:
            cmds += self._remove_daemon_commands()
            cmds.append(f"rm -f {shq(HELPER_BINARY)} {shq(PROBE_BINARY)}")
            cmds.append(f"rm -rf {shq(SYSTEM_DIR)} {shq(RUN_DIR)}")

        def after(ok: bool) -> None:
            if not ok:
                return
            if delete_rules:
                self.workspace.rules = []
                self.workspace.kill_switch = KillSwitchConfig()
                self.workspace.automation = AutomationConfig()
            if reset_aliases_settings:
                self.workspace.aliases = {}
                from .models import AppSettings
                self.workspace.settings = AppSettings()
            self.log("Reset to default", True)
            self.save_workspace()

        if cmds:
            self._run_privileged_async("reset routing to default", cmds, stop_on_error=False, on_done=after)
        else:
            after(True)

    # Adapters & Raw routes

    def set_interface_up(self, name: str, up: bool) -> None:
        if not is_valid_interface_name(name):
            return
        state = "up" if up else "down"
        self._run_privileged_async(f"{'enable' if up else 'disable'} interface {name}", [f"ip link set dev {shq(name)} {state}"])

    def set_wifi_power(self, name: str, on: bool) -> None:
        self._run_privileged_async(f"turn Wi-Fi {'on' if on else 'off'}", [f"nmcli radio wifi {'on' if on else 'off'}"])

    def renew_dhcp(self, name: str) -> None:
        if not is_valid_interface_name(name):
            return
        self._run_privileged_async(
            f"renew DHCP on {name}",
            [f"(nmcli dev reapply {shq(name)} || dhclient -r {shq(name)} && dhclient {shq(name)})"],
        )

    def set_mtu(self, name: str, mtu: int) -> None:
        if not is_valid_interface_name(name) or not (576 <= mtu <= 9000):
            self.show_error("Invalid MTU", "Use a value between 576 and 9000.")
            return
        self._run_privileged_async(f"set MTU of {name} to {mtu}", [f"ip link set dev {shq(name)} mtu {mtu}"])

    def set_dns(self, iface: str, connection_name: str | None, servers: list[str]) -> None:
        bad = [s for s in servers if not is_valid_ipv4(s) and not is_valid_ipv6(s)]
        if bad:
            self.show_error("Invalid DNS server", ", ".join(bad))
            return
        cmds: list[str] = []
        if connection_name:
            dns_val = " ".join(servers) if servers else ""
            cmds.append(f"nmcli con mod {shq(connection_name)} ipv4.dns {shq(dns_val)}")
            cmds.append(f"nmcli con mod {shq(connection_name)} ipv4.ignore-auto-dns {'yes' if servers else 'no'}")
            cmds.append(f"nmcli con up {shq(connection_name)}")
        elif is_valid_interface_name(iface):
            if servers:
                cmds.append(f"resolvectl dns {shq(iface)} " + " ".join(shq(s) for s in servers))
            else:
                cmds.append(f"resolvectl revert {shq(iface)}")
        self._run_privileged_async(f"set DNS servers for {iface}", cmds)

    def flush_dns(self) -> None:
        self._run_privileged_async("flush the DNS cache", ["resolvectl flush-caches"])

    def set_alias(self, name: str, alias: str) -> None:
        t = alias.strip()
        if not t:
            self.workspace.aliases.pop(name, None)
        else:
            self.workspace.aliases[name] = t
        self.save_workspace()

    def delete_raw_route(self, entry: RouteEntry) -> None:
        cmd = ["ip"]
        if entry.family == "inet6":
            cmd.append("-6")
        cmd += ["route", "del", entry.destination]
        if entry.gateway:
            cmd += ["via", entry.gateway]
        if entry.netif:
            cmd += ["dev", entry.netif]
        self._run_privileged_async(f"delete route {entry.destination} via {entry.netif}", [" ".join(shq(c) for c in cmd)])

    def add_one_off_route(self, destination: str, via: RouteVia) -> None:
        d = Destination.parse(destination)
        if d is None or d.kind == "domain":
            self.show_error("Invalid destination", "Use an IPv4 address or CIDR network. For domains, create a rule.")
            return
        ve = via.validation_error
        if ve:
            self.show_error("Invalid next hop", ve)
            return
        target = d.address if d.kind == "host" else f"{d.address}/{d.prefix}"
        args = ["ip", "route", "replace", target]
        if via.kind == "gateway":
            args += ["via", via.gateway]
        elif via.kind == "interfaceDirect":
            args += ["dev", via.interface]
        elif via.kind == "interfaceRouter":
            info = self.interface(via.interface)
            if not info or not info.router:
                self.show_error("No router", f"{via.interface} has no router right now.")
                return
            args += ["via", info.router, "dev", via.interface]
        self._run_privileged_async(f"add a one-off route for {d.display}", [" ".join(shq(a) for a in args)])

    # Path check

    def check_path(self, query: str | None = None) -> None:
        if query is not None:
            self.path_query = query
        q = self.path_query.strip()
        if not q:
            return
        self.traceroute.stop()
        self.traceroute.lines = []
        self.path_checking = True
        self.notify()

        rules = list(self.workspace.rules)
        aliases = dict(self.workspace.aliases)

        def worker() -> None:
            snap = NetworkScanner.scan(rules)
            res = PathAnalyzer.analyze(q, snap, rules, aliases)
            GLib.idle_add(self._set_path_result, res, False)
            for hop in res.hops:
                ok, val = Probes.tcp_connect(hop.ip, 443, timeout=5.0)
                hop.tcp443 = f"TCP 443 reachable in {val:.0f} ms" if ok else f"TCP 443 not reachable ({val})"
                GLib.idle_add(self._set_path_result, res, False)
            GLib.idle_add(self._set_path_result, res, True)

        threading.Thread(target=worker, daemon=True).start()

    def _set_path_result(self, res: PathCheckResult, finished: bool) -> None:
        self.path_result = res
        if finished:
            self.path_checking = False
            summary = "\n".join(f"{h.ip}: {h.headline}" for h in res.hops)
            self.log(f"Path check: {res.query}", res.error is None, summary)
        self.notify()

    def open_path_check(self, query: str) -> None:
        self.selection = "pathCheck"
        self.check_path(query)
        self.notify()

    # Speed test paths

    def speed_test_paths(self) -> list[SpeedPath]:
        out: list[SpeedPath] = []
        egress = (self.net.internet_route or {}).get("interface")
        lan = self.local_interface
        if lan:
            out.append(SpeedPath(
                id="direct",
                title=f"Direct over {self.display_name(lan.name)} ({lan.name})",
                detail="Bypasses the VPN: your ISP's raw speed, the same way your direct rules reach the internet.",
                interface_name=lan.name,
                pinned=True,
            ))
        out.append(SpeedPath(
            id="default",
            title="System default" + (f" — through {self.display_name(egress)} ({egress})" if egress else ""),
            detail="What your apps get right now, e.g. through the full-tunnel VPN.",
            interface_name=egress,
            pinned=False,
        ))
        for i in self.net.interfaces:
            if i.is_active and i.ipv4 and (i.is_vpn or i.is_physical):
                if (lan and i.name == lan.name) or i.name == egress:
                    continue
                out.append(SpeedPath(
                    id=i.name,
                    title=f"{self.display_name(i.name)} ({i.name})",
                    detail="Pinned to this interface. It must have internet access.",
                    interface_name=i.name,
                    pinned=True,
                ))
        return out

