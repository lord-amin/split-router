from __future__ import annotations

from typing import TYPE_CHECKING

from gi.repository import Adw, Gtk

from ..models import format_bytes, format_rate
from ..scanner import InterfaceInfo
from .widgets import clear_box, make_card, make_kv_row, make_pill

if TYPE_CHECKING:
    from ..app_model import AppModel


class AdaptersView(Gtk.Paned):
    def __init__(self, model: AppModel, get_window) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)
        self.model = model
        self.get_window = get_window
        self.selected_name: str | None = None
        self.set_position(280)

        # Left sidebar list
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        left.set_size_request(240, -1)
        scroll_l = Gtk.ScrolledWindow()
        scroll_l.set_vexpand(True)
        self.left_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.left_list.set_margin_top(10)
        self.left_list.set_margin_bottom(10)
        self.left_list.set_margin_start(10)
        self.left_list.set_margin_end(10)
        scroll_l.set_child(self.left_list)
        left.append(scroll_l)
        left.append(Gtk.Separator())
        self.sys_chk = Gtk.CheckButton(label="Show system interfaces")
        self.sys_chk.set_margin_top(8)
        self.sys_chk.set_margin_bottom(8)
        self.sys_chk.set_margin_start(10)
        self.sys_chk.set_active(model.workspace.settings.show_system_interfaces)
        self.sys_chk.connect("toggled", self._on_sys_toggled)
        left.append(self.sys_chk)
        self.set_start_child(left)

        # Right detail scroll
        scroll_r = Gtk.ScrolledWindow()
        scroll_r.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.detail_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.detail_box.set_margin_top(24)
        self.detail_box.set_margin_bottom(24)
        self.detail_box.set_margin_start(24)
        self.detail_box.set_margin_end(24)
        scroll_r.set_child(self.detail_box)
        self.set_end_child(scroll_r)

    def _on_sys_toggled(self, chk: Gtk.CheckButton) -> None:
        self.model.workspace.settings.show_system_interfaces = chk.get_active()
        self.model.save_workspace()

    def refresh(self) -> None:
        m = self.model
        clear_box(self.left_list)
        vis = m.visible_interfaces
        active = [i for i in vis if i.is_active]
        inactive = [i for i in vis if not i.is_active]

        if self.selected_name is None or not m.interface(self.selected_name):
            self.selected_name = m.net.primary_interface or (active[0].name if active else (vis[0].name if vis else None))

        def add_section(title: str, items: list[InterfaceInfo]) -> None:
            if not items:
                return
            lbl = Gtk.Label(label=title.upper(), xalign=0)
            lbl.add_css_class("caption")
            lbl.add_css_class("dim-label")
            lbl.set_margin_top(6)
            lbl.set_margin_bottom(2)
            self.left_list.append(lbl)
            for i in items:
                btn = Gtk.Button()
                btn.add_css_class("flat")
                if i.name == self.selected_name:
                    btn.add_css_class("suggested-action")
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                row.append(Gtk.Image.new_from_icon_name(i.icon_name))
                col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
                col.set_hexpand(True)
                t = Gtk.Label(label=m.display_name(i.name), xalign=0)
                t.add_css_class("heading")
                sub = Gtk.Label(label=f"{i.name} · {i.primary_ipv4 or ('no IPv4' if i.is_up else 'down')}", xalign=0)
                sub.add_css_class("caption")
                sub.add_css_class("dim-label")
                col.append(t)
                col.append(sub)
                row.append(col)
                btn.set_child(row)
                btn.connect("clicked", lambda *_, n=i.name: self._select(n))
                self.left_list.append(btn)

        add_section("Active", active)
        add_section("Inactive", inactive)

        clear_box(self.detail_box)
        info = m.interface(self.selected_name)
        if not info:
            self.detail_box.append(Adw.StatusPage(icon_name="network-wired-symbolic", title="Select an adapter"))
            return
        self._render_detail(info)

    def _select(self, name: str) -> None:
        self.selected_name = name
        self.refresh()

    def _render_detail(self, info: InterfaceInfo) -> None:
        m = self.model
        # Header
        hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        ic = Gtk.Image.new_from_icon_name(info.icon_name)
        ic.set_pixel_size(36)
        hdr.append(ic)
        hcol = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        t = Gtk.Label(label=m.display_name(info.name), xalign=0)
        t.add_css_class("title-1")
        hcol.append(t)
        pills = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        nl = Gtk.Label(label=info.name)
        nl.add_css_class("monospace")
        nl.add_css_class("dim-label")
        pills.append(nl)
        pills.append(make_pill(info.kind_label, "blue"))
        pills.append(make_pill("Up · Running" if info.is_running else ("Up" if info.is_up else "Down"), "green" if info.is_up else "gray"))
        if info.name == m.net.primary_interface:
            pills.append(make_pill("Primary", "blue"))
        if info.name == (m.net.internet_route or {}).get("interface"):
            pills.append(make_pill("Carries internet traffic", "purple"))
        hcol.append(pills)
        hdr.append(hcol)
        self.detail_box.append(hdr)

        # Addresses + Traffic
        row1 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        row1.set_homogeneous(True)
        addr_card, addr_in = make_card("Addresses", "network-server-symbolic")
        for a in info.ipv4:
            addr_in.append(make_kv_row("IPv4", f"{a['address']}/{a.get('prefix', '?')}"))
            if a.get("peer"):
                addr_in.append(make_kv_row("Peer", a["peer"]))
        for a in info.ipv6[:4]:
            addr_in.append(make_kv_row("IPv6", a["address"]))
        if not info.ipv4 and not info.ipv6:
            addr_in.append(Gtk.Label(label="No addresses", xalign=0))
        addr_in.append(make_kv_row("MAC", info.mac))
        addr_in.append(make_kv_row("Router", info.router or ""))
        addr_in.append(make_kv_row("DNS", ", ".join(info.dns)))
        row1.append(addr_card)

        tr_card, tr_in = make_card("Traffic", "utilities-system-monitor-symbolic")
        rate = m.rates.get(info.name)
        tr_in.append(make_kv_row("Download", format_rate(rate.rx if rate else None), mono=False))
        tr_in.append(make_kv_row("Upload", format_rate(rate.tx if rate else None), mono=False))
        tr_in.append(make_kv_row("Received", f"{format_bytes(info.rx_bytes)} · {info.rx_packets} pkts", mono=False))
        tr_in.append(make_kv_row("Sent", f"{format_bytes(info.tx_bytes)} · {info.tx_packets} pkts", mono=False))
        tr_in.append(make_kv_row("Errors", f"in {info.rx_errors} · out {info.tx_errors}", mono=False))
        row1.append(tr_card)
        self.detail_box.append(row1)

        # Configuration + Routes
        row2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        row2.set_homogeneous(True)
        cfg_card, cfg_in = make_card("Configuration", "preferences-system-symbolic")
        cfg_in.append(make_kv_row("Connection", info.service_name or "", mono=False))
        cfg_in.append(make_kv_row("Index", str(info.index)))
        cfg_in.append(make_kv_row("MTU", str(info.mtu)))
        cfg_in.append(make_kv_row("Flags", " ".join(info.flags)))
        cfg_in.append(make_kv_row("Routes via it", str(info.route_count)))
        al_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        al_lbl = Gtk.Label(label="Alias", xalign=0)
        al_lbl.add_css_class("dim-label")
        al_lbl.set_size_request(130, -1)
        al_ent = Gtk.Entry(placeholder_text="Friendly name")
        al_ent.set_text(m.workspace.aliases.get(info.name, ""))
        al_ent.set_hexpand(True)
        al_save = Gtk.Button(label="Save")
        al_save.connect("clicked", lambda *_, n=info.name, e=al_ent: m.set_alias(n, e.get_text()))
        al_row.append(al_lbl)
        al_row.append(al_ent)
        al_row.append(al_save)
        cfg_in.append(al_row)
        row2.append(cfg_card)

        rt_card, rt_in = make_card("Routes through this adapter", "go-jump-symbolic")
        routes = [r for r in m.net.routes_v4 if r.netif == info.name and not r.is_link_layer]
        if not routes:
            rt_in.append(Gtk.Label(label="None", xalign=0))
        for r in routes[:14]:
            rr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            dl = Gtk.Label(label=r.destination, xalign=0)
            dl.add_css_class("monospace")
            dl.set_hexpand(True)
            rr.append(dl)
            own = m.owner_of(r)
            if own:
                ol = Gtk.Label(label=own)
                ol.add_css_class("caption")
                ol.add_css_class("dim-label")
                rr.append(ol)
            gl = Gtk.Label(label=r.gateway or "—", xalign=1)
            gl.add_css_class("monospace")
            gl.add_css_class("dim-label")
            rr.append(gl)
            rt_in.append(rr)
        if len(routes) > 14:
            more = Gtk.Label(label=f"+ {len(routes) - 14} more in Routing Table", xalign=0)
            more.add_css_class("caption")
            more.add_css_class("dim-label")
            rt_in.append(more)
        row2.append(rt_card)
        self.detail_box.append(row2)

        # Controls card
        ctrl_card, ctrl_in = make_card("Controls", "system-run-symbolic")
        note = Gtk.Label(label="Privileged actions ask for an administrator password via PolicyKit. Changes made here are live and are not part of your rules.", xalign=0)
        note.add_css_class("dim-label")
        note.set_wrap(True)
        ctrl_in.append(note)

        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        if info.is_up:
            dis_b = Gtk.Button(label="Disable Interface")
            dis_b.connect("clicked", lambda *_, n=info.name: self._confirm_down(n, wifi=False))
            btns.append(dis_b)
        else:
            en_b = Gtk.Button(label="Enable Interface")
            en_b.connect("clicked", lambda *_, n=info.name: m.set_interface_up(n, True))
            btns.append(en_b)
        if info.kind == "wifi":
            w_off = Gtk.Button(label="Wi-Fi Off")
            w_off.connect("clicked", lambda *_, n=info.name: self._confirm_down(n, wifi=True))
            w_on = Gtk.Button(label="Wi-Fi On")
            w_on.connect("clicked", lambda *_, n=info.name: m.set_wifi_power(n, True))
            btns.append(w_off)
            btns.append(w_on)
        if info.is_physical:
            dhcp_b = Gtk.Button(label="Renew DHCP")
            dhcp_b.connect("clicked", lambda *_, n=info.name: m.renew_dhcp(n))
            btns.append(dhcp_b)
        test_b = Gtk.Button(label="Test Internet Path")
        test_b.connect("clicked", lambda *_: m.open_path_check("1.1.1.1"))
        btns.append(test_b)
        ctrl_in.append(btns)

        ctrl_in.append(Gtk.Separator())
        mtu_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        ml = Gtk.Label(label="MTU", xalign=0)
        ml.set_size_request(130, -1)
        mtu_ent = Gtk.Entry()
        mtu_ent.set_text(str(info.mtu))
        mtu_ent.set_size_request(100, -1)
        mtu_btn = Gtk.Button(label="Set MTU")
        mtu_btn.connect("clicked", lambda *_, n=info.name, e=mtu_ent: m.set_mtu(n, int(e.get_text()) if e.get_text().isdigit() else 0))
        mtu_row.append(ml)
        mtu_row.append(mtu_ent)
        mtu_row.append(mtu_btn)
        ctrl_in.append(mtu_row)

        dns_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        dl = Gtk.Label(label="DNS servers", xalign=0)
        dl.set_size_request(130, -1)
        dns_ent = Gtk.Entry(placeholder_text="1.1.1.1 8.8.8.8 (empty = automatic)")
        dns_ent.set_text(" ".join(info.dns))
        dns_ent.set_hexpand(True)
        dns_btn = Gtk.Button(label="Set DNS")
        dns_btn.connect("clicked", lambda *_, i=info, e=dns_ent: m.set_dns(i.name, i.service_name, [s for s in e.get_text().replace(",", " ").split() if s]))
        flush_b = Gtk.Button(label="Flush DNS Cache")
        flush_b.connect("clicked", lambda *_: m.flush_dns())
        dns_row.append(dl)
        dns_row.append(dns_ent)
        dns_row.append(dns_btn)
        dns_row.append(flush_b)
        ctrl_in.append(dns_row)

        self.detail_box.append(ctrl_card)

    def _confirm_down(self, name: str, wifi: bool) -> None:
        heading = "Turn off Wi-Fi?" if wifi else f"Disable {name}?"
        body = (
            "This is your primary connection. You will lose network access through it."
            if name == self.model.net.primary_interface
            else f"Traffic routed through {name} will stop."
        )
        dlg = Adw.MessageDialog(transient_for=self.get_window(), heading=heading, body=body)
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("ok", "Turn Wi-Fi Off" if wifi else f"Disable {name}")
        dlg.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect(
            "response",
            lambda _d, resp: (self.model.set_wifi_power(name, False) if wifi else self.model.set_interface_up(name, False))
            if resp == "ok"
            else None,
        )
        dlg.present()

