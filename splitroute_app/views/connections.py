from __future__ import annotations

import threading
from datetime import datetime
from typing import TYPE_CHECKING

from gi.repository import GLib, Gtk

from ..connections import ConnectionScanner, LiveConnection
from ..models import cidr_contains, is_private_ipv4, is_valid_ipv4
from .widgets import clear_box, copy_to_clipboard, make_page_header, make_pill, make_stat_tile

if TYPE_CHECKING:
    from ..app_model import AppModel


class ConnectionsView(Gtk.Box):
    def __init__(self, model: AppModel) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.model = model
        self.connections: list[LiveConnection] = []
        self.paused = False
        self.leaks_only = False
        self.iface_filter = "all"
        self.search_text = ""
        self.updated: datetime | None = None
        self._scanning = False

        self.set_margin_top(24)
        self.set_margin_bottom(24)
        self.set_margin_start(24)
        self.set_margin_end(24)

        trailing = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.updated_lbl = Gtk.Label(label="")
        self.updated_lbl.add_css_class("caption")
        self.updated_lbl.add_css_class("dim-label")
        self.pause_btn = Gtk.Button(label="Pause")
        self.pause_btn.connect("clicked", self._toggle_pause)
        trailing.append(self.updated_lbl)
        trailing.append(self.pause_btn)
        self.append(make_page_header("Connections", "Which app is talking to which address, and through which interface — live", trailing))

        self.summary_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.append(self.summary_box)

        # Filters
        fbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.search_entry = Gtk.SearchEntry(placeholder_text="Filter by app, IP or port")
        self.search_entry.set_hexpand(True)
        self.search_entry.connect("search-changed", lambda e: self._on_search(e.get_text()))
        fbar.append(self.search_entry)

        self.iface_combo = Gtk.ComboBoxText()
        self.iface_combo.append("all", "All interfaces")
        self.iface_combo.set_active_id("all")
        self.iface_combo.connect("changed", self._on_iface_changed)
        fbar.append(self.iface_combo)

        self.leaks_chk = Gtk.CheckButton(label="Only outside VPN tunnel")
        self.leaks_chk.connect("toggled", self._on_leaks_toggled)
        fbar.append(self.leaks_chk)

        self.count_lbl = Gtk.Label(label="0 connections")
        self.count_lbl.add_css_class("dim-label")
        fbar.append(self.count_lbl)
        self.append(fbar)

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        self.list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        scroll.set_child(self.list_box)
        self.append(scroll)

        note = Gtk.Label(label="Shows your own apps' connections via ss. Processes running as root (VPN cores, system daemons) are not listed.", xalign=0)
        note.add_css_class("caption")
        note.add_css_class("dim-label")
        self.append(note)

        GLib.timeout_add_seconds(3, self._tick)

    def _tick(self) -> bool:
        if not self.paused and self.get_mapped():
            self._scan_now()
        return True

    def _toggle_pause(self, *_args) -> None:
        self.paused = not self.paused
        self.pause_btn.set_label("Resume" if self.paused else "Pause")

    def _on_search(self, text: str) -> None:
        self.search_text = text.strip().lower()
        self._render_rows()

    def _on_iface_changed(self, combo: Gtk.ComboBoxText) -> None:
        self.iface_filter = combo.get_active_id() or "all"
        self._render_rows()

    def _on_leaks_toggled(self, btn: Gtk.CheckButton) -> None:
        self.leaks_only = btn.get_active()
        self._render_rows()

    def refresh(self) -> None:
        if self.get_mapped() and not self.connections and not self._scanning:
            self._scan_now()
        else:
            self._render_summary()
            self._render_rows()

    def _scan_now(self) -> None:
        if self._scanning:
            return
        self._scanning = True
        ifaces = list(self.model.net.interfaces)

        def worker() -> None:
            conns = ConnectionScanner.scan(ifaces)
            GLib.idle_add(self._on_scanned, conns)

        threading.Thread(target=worker, daemon=True).start()

    def _on_scanned(self, conns: list[LiveConnection]) -> None:
        self._scanning = False
        self.connections = conns
        self.updated = datetime.now()
        self.updated_lbl.set_label(f"Updated {self.updated.strftime('%H:%M:%S')}")

        # Update interface filter options without losing selection
        prev = self.iface_filter
        self.iface_combo.handler_block_by_func(self._on_iface_changed)
        self.iface_combo.remove_all()
        self.iface_combo.append("all", "All interfaces")
        for i in sorted({c.interface for c in conns if c.interface}):
            self.iface_combo.append(i, f"{self.model.display_name(i)} ({i})")
        self.iface_combo.set_active_id(prev if prev in {c.interface for c in conns if c.interface} else "all")
        self.iface_filter = self.iface_combo.get_active_id() or "all"
        self.iface_combo.handler_unblock_by_func(self._on_iface_changed)

        self._render_summary()
        self._render_rows()

    def _is_leak(self, c: LiveConnection) -> bool:
        tunnel = self.model.full_tunnel.name if self.model.full_tunnel else None
        if not tunnel or not c.interface or c.interface == tunnel or c.interface.startswith("lo"):
            return False
        if is_valid_ipv4(c.remote_ip) and is_private_ipv4(c.remote_ip):
            return False
        if c.remote_ip.startswith(("fe80", "fd")) or c.remote_ip == "::1":
            return False
        installed = (self.model.net.helper_status or {}).get("installedRoutes") or []
        if is_valid_ipv4(c.remote_ip):
            for r in installed:
                if cidr_contains(f"{r['destination']}/{r['prefix']}", c.remote_ip):
                    return False
        return True

    def _render_summary(self) -> None:
        clear_box(self.summary_box)
        tunnel = self.model.full_tunnel.name if self.model.full_tunnel else None
        self.leaks_chk.set_sensitive(tunnel is not None)
        by_if: dict[str, list[LiveConnection]] = {}
        for c in self.connections:
            by_if.setdefault(c.interface or "?", []).append(c)
        for key in sorted(by_if):
            lst = by_if[key]
            title = "Unknown" if key == "?" else f"{self.model.display_name(key)} ({key})"
            apps = len({c.process for c in lst})
            self.summary_box.append(make_stat_tile(
                title, str(len(lst)), f"{apps} apps",
                "network-vpn-symbolic" if key == tunnel else "network-transmit-receive-symbolic",
                "purple" if key == tunnel else "blue",
            ))
        if tunnel is not None:
            leaks = [c for c in self.connections if self._is_leak(c)]
            det = "nothing leaking" if not leaks else ", ".join(sorted({c.process for c in leaks})[:3])
            self.summary_box.append(make_stat_tile(
                "Outside the tunnel", str(len(leaks)), det,
                "dialog-warning-symbolic" if leaks else "emblem-default-symbolic",
                "orange" if leaks else "green",
            ))

    def _render_rows(self) -> None:
        clear_box(self.list_box)
        rows = [
            c for c in self.connections
            if (self.iface_filter == "all" or c.interface == self.iface_filter)
            and (not self.leaks_only or self._is_leak(c))
            and (
                not self.search_text
                or self.search_text in c.process.lower()
                or self.search_text in c.remote_ip
                or self.search_text == c.remote_port
            )
        ]
        rows.sort(key=lambda c: (c.process.lower(), c.remote_ip))
        self.count_lbl.set_label(f"{len(rows)} connections")

        tunnel = self.model.full_tunnel.name if self.model.full_tunnel else None
        for c in rows[:250]:
            card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            card.add_css_class("card")
            inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            inner.set_margin_top(8)
            inner.set_margin_bottom(8)
            inner.set_margin_start(12)
            inner.set_margin_end(12)
            card.append(inner)

            leak = self._is_leak(c)
            if leak:
                warn = Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
                warn.set_tooltip_text("Outside the VPN tunnel")
                inner.append(warn)

            proc_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            proc_box.set_size_request(180, -1)
            pl = Gtk.Label(label=c.process, xalign=0)
            pl.add_css_class("heading")
            pidl = Gtk.Label(label=str(c.pid), xalign=0)
            pidl.add_css_class("caption")
            pidl.add_css_class("monospace")
            pidl.add_css_class("dim-label")
            proc_box.append(pl)
            proc_box.append(pidl)
            inner.append(proc_box)

            rem = Gtk.Label(label=f"{c.remote_ip}:{c.remote_port}", xalign=0)
            rem.add_css_class("monospace")
            rem.set_selectable(True)
            rem.set_size_request(210, -1)
            inner.append(rem)

            proto = Gtk.Label(label=f"{c.proto} · {c.state or '—'}", xalign=0)
            proto.add_css_class("caption")
            proto.add_css_class("dim-label")
            proto.set_size_request(120, -1)
            inner.append(proto)

            if c.interface:
                tone = "purple" if c.interface == tunnel else ("orange" if leak else "gray")
                inner.append(make_pill(f"{self.model.display_name(c.interface)} ({c.interface})", tone))
            else:
                inner.append(make_pill("—", "gray"))

            sp = Gtk.Box()
            sp.set_hexpand(True)
            inner.append(sp)

            path_btn = Gtk.Button(label="Path")
            path_btn.add_css_class("flat")
            path_btn.connect("clicked", lambda *_, ip=c.remote_ip: self.model.open_path_check(ip))
            inner.append(path_btn)

            if is_valid_ipv4(c.remote_ip):
                rule_btn = Gtk.Button(label="Rule")
                rule_btn.add_css_class("flat")
                rule_btn.connect("clicked", lambda *_, ip=c.remote_ip: (self.model.open_main_window("rules"), self.model.new_rule(ip)))
                inner.append(rule_btn)

            blk_btn = Gtk.Button(label="Block")
            blk_btn.add_css_class("flat")
            blk_btn.add_css_class("destructive-action")
            blk_btn.connect("clicked", lambda *_, ip=c.remote_ip, proc=c.process: self.model.block_and_apply(ip, note=f"Blocked from Connections ({proc})"))
            inner.append(blk_btn)

            cp_btn = Gtk.Button.new_from_icon_name("edit-copy-symbolic")
            cp_btn.add_css_class("flat")
            cp_btn.set_tooltip_text("Copy Address")
            cp_btn.connect("clicked", lambda *_, ip=c.remote_ip: copy_to_clipboard(ip))
            inner.append(cp_btn)

            self.list_box.append(card)

