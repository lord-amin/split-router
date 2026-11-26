from __future__ import annotations

from typing import TYPE_CHECKING

from gi.repository import Adw, Gtk

from ..models import parse_ks_entry
from .widgets import PendingBanner, clear_box, make_card, make_page_header, make_pill

if TYPE_CHECKING:
    from ..app_model import AppModel


class BlockListView(Gtk.Box):
    def __init__(self, model: AppModel, get_window) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.model = model
        self.get_window = get_window

        self.set_margin_top(24)
        self.set_margin_bottom(24)
        self.set_margin_start(24)
        self.set_margin_end(24)

        self.append(make_page_header(
            "Block List",
            "Stop every connection to an address, network or domain — through any interface, the VPN included",
        ))
        self.pending_banner = PendingBanner(model, get_window)
        self.append(self.pending_banner)

        self.status_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.append(self.status_box)

        add_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.val_entry = Gtk.Entry(placeholder_text="IP, network or domain to block")
        self.val_entry.set_hexpand(True)
        self.val_entry.connect("changed", lambda *_: self._sync_add_btn())
        self.val_entry.connect("activate", lambda *_: self._add())
        self.note_entry = Gtk.Entry(placeholder_text="Note (optional)")
        self.note_entry.set_size_request(220, -1)
        self.note_entry.connect("activate", lambda *_: self._add())
        self.add_btn = Gtk.Button(label="Block")
        self.add_btn.add_css_class("destructive-action")
        self.add_btn.set_sensitive(False)
        self.add_btn.connect("clicked", lambda *_: self._add())
        add_bar.append(self.val_entry)
        add_bar.append(self.note_entry)
        add_bar.append(self.add_btn)
        self.append(add_bar)

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        self.list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        scroll.set_child(self.list_box)
        self.append(scroll)

    def _sync_add_btn(self) -> None:
        self.add_btn.set_sensitive(parse_ks_entry(self.val_entry.get_text()) is not None)

    def _add(self) -> None:
        v = self.val_entry.get_text().strip().lower()
        if parse_ks_entry(v) is None:
            return
        self.model.block(v, note=self.note_entry.get_text().strip())
        self.val_entry.set_text("")
        self.note_entry.set_text("")

    def refresh(self) -> None:
        m = self.model
        entries = m.workspace.automation.blocked
        live = (m.net.helper_status or {}).get("blockList")
        self.pending_banner.refresh()

        clear_box(self.status_box)
        active = bool(live and live.get("active"))
        card, inner = make_card()
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        col.set_hexpand(True)
        denied_cnt = len(live.get("denied") or []) if live else 0
        title = (
            f"Blocking {denied_cnt} range" + ("" if denied_cnt == 1 else "s")
            if active
            else ("Not applied yet" if any(e.enabled for e in entries) else "Off")
        )
        t_lbl = Gtk.Label(label=title, xalign=0)
        t_lbl.add_css_class("title-2")
        col.append(t_lbl)
        desc = Gtk.Label(
            label=(
                "Outgoing connections are refused immediately and replies are dropped across every interface, including the VPN tunnel. "
                "Blocking a domain also blocks all subdomains via systemd-resolved + /etc/hosts."
            ),
            xalign=0,
        )
        desc.add_css_class("caption")
        desc.add_css_class("dim-label")
        desc.set_wrap(True)
        col.append(desc)
        for err in (live or {}).get("errors") or []:
            el = Gtk.Label(label=err, xalign=0)
            el.add_css_class("caption")
            col.append(el)
        row.append(col)
        if m.automation_pending or m.daemon_outdated:
            b = Gtk.Button(label="Apply" if m.automation_pending else "Update Service")
            b.add_css_class("suggested-action")
            b.set_valign(Gtk.Align.CENTER)
            b.connect("clicked", lambda *_: m.apply(title="update the block list"))
            row.append(b)
        inner.append(row)
        self.status_box.append(card)

        clear_box(self.list_box)
        if not entries:
            self.list_box.append(Adw.StatusPage(
                icon_name="action-unavailable-symbolic",
                title="Nothing blocked",
                description="Add an IP (203.0.113.10), a network (203.0.113.0/24 or 2001:db8::/32) or a domain (ads.example.com). You can also block from Connections or Path Check.",
            ))
            return

        for e in entries:
            ecard = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            ecard.add_css_class("card")
            ein = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            ein.set_margin_top(10)
            ein.set_margin_bottom(10)
            ein.set_margin_start(14)
            ein.set_margin_end(14)
            ecard.append(ein)

            sw = Gtk.Switch()
            sw.set_active(e.enabled)
            sw.set_valign(Gtk.Align.CENTER)
            sw.connect("notify::active", self._toggle_entry, e.id)
            ein.append(sw)

            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            col.set_hexpand(True)
            top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            vl = Gtk.Label(label=e.value, xalign=0)
            vl.add_css_class("monospace")
            vl.add_css_class("heading")
            top.append(vl)
            parsed = parse_ks_entry(e.value)
            if parsed is None:
                top.append(make_pill("Invalid", "red"))
            elif parsed[0] == "domain":
                top.append(make_pill("Domain", "gray"))
            else:
                top.append(make_pill("Address" if parsed[1].endswith(("/32", "/128")) else "Network", "gray"))
            if not e.enabled:
                top.append(make_pill("Off", "gray"))
            elif m.automation_pending:
                top.append(make_pill("Not applied", "orange"))
            elif active:
                top.append(make_pill("Blocked", "red"))
            else:
                top.append(make_pill("Inactive", "gray"))
            col.append(top)

            if e.note:
                nl = Gtk.Label(label=e.note, xalign=0)
                nl.add_css_class("caption")
                nl.add_css_class("dim-label")
                col.append(nl)
            ein.append(col)

            if parsed and parsed[0] == "cidr":
                ip_only = parsed[1].split("/")[0]
                pb = Gtk.Button(label="Path")
                pb.add_css_class("flat")
                pb.connect("clicked", lambda *_, d=ip_only: m.open_path_check(d))
                ein.append(pb)

            db = Gtk.Button.new_from_icon_name("user-trash-symbolic")
            db.add_css_class("flat")
            db.connect("clicked", lambda *_, eid=e.id: self._delete_entry(eid))
            ein.append(db)
            self.list_box.append(ecard)

    def _toggle_entry(self, sw: Gtk.Switch, _p, eid: str) -> None:
        for e in self.model.workspace.automation.blocked:
            if e.id == eid:
                if e.enabled != sw.get_active():
                    e.enabled = sw.get_active()
                    self.model.save_workspace()
                break

    def _delete_entry(self, eid: str) -> None:
        self.model.workspace.automation.blocked = [
            e for e in self.model.workspace.automation.blocked if e.id != eid
        ]
        self.model.save_workspace()

