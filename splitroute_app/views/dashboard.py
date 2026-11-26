from __future__ import annotations

from typing import TYPE_CHECKING

from gi.repository import Gtk

from ..models import format_rate
from ..notifier import open_url
from ..paths import VERSION
from .widgets import (
    PendingBanner,
    clear_box,
    copy_to_clipboard,
    make_banner,
    make_card,
    make_kv_row,
    make_page_header,
    make_pill,
    make_stat_tile,
)

if TYPE_CHECKING:
    from ..app_model import AppModel


class DashboardView(Gtk.ScrolledWindow):
    def __init__(self, model: AppModel, get_window) -> None:
        super().__init__()
        self.model = model
        self.get_window = get_window
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self.root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.root.set_margin_top(24)
        self.root.set_margin_bottom(24)
        self.root.set_margin_start(24)
        self.root.set_margin_end(24)
        self.set_child(self.root)

        check_btn = Gtk.Button(label="Check a Path")
        check_btn.connect("clicked", lambda *_: self.model.open_main_window("pathCheck"))
        self.root.append(make_page_header("Dashboard", "Where your traffic goes right now", check_btn))

        self.banners_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.root.append(self.banners_box)
        self.pending_banner = PendingBanner(model, get_window)
        self.root.append(self.pending_banner)

        self.tiles_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        self.tiles_box.set_homogeneous(True)
        self.root.append(self.tiles_box)

        self.identity_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.root.append(self.identity_box)

        self.split_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        self.split_box.set_homogeneous(True)
        self.root.append(self.split_box)

        self.ifaces_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.root.append(self.ifaces_box)

    def refresh(self) -> None:
        m = self.model
        self.pending_banner.refresh()

        # Banners
        clear_box(self.banners_box)
        if m.available_update:
            u = m.available_update
            rel_btn = Gtk.Button(label="Release Notes")
            rel_btn.connect("clicked", lambda *_, url=u.page_url: open_url(url))
            dl_btn = Gtk.Button(label="Download")
            dl_btn.add_css_class("suggested-action")
            dl_btn.connect("clicked", lambda *_, url=u.download_url or u.page_url: open_url(url))
            self.banners_box.append(make_banner(
                f"SplitRoute {u.version} is available",
                f"You have {VERSION}.",
                tone="blue",
                icon="software-update-available-symbolic",
                actions=[rel_btn, dl_btn],
            ))
        if m.daemon_outdated:
            upd_btn = Gtk.Button(label="Update Service")
            upd_btn.add_css_class("suggested-action")
            upd_btn.connect("clicked", lambda *_: m.apply(title="update the background service"))
            self.banners_box.append(make_banner(
                "Background service needs an update",
                f"Installed helper ({m.net.service.helper_version or 'older'}) differs from app ({VERSION}).",
                tone="orange",
                actions=[upd_btn],
            ))

        # 4 Stat tiles
        clear_box(self.tiles_box)
        lan = m.local_interface
        lan_val = (lan.primary_ipv4 or "—") if lan else "—"
        lan_det = f"{m.display_name(lan.name)} ({lan.name}) · router {lan.router or '—'}" if lan else "No Ethernet or Wi-Fi connection"
        self.tiles_box.append(make_stat_tile("Local IP", lan_val, lan_det, "computer-symbolic", "blue"))

        pub = next((p for p in m.public_ips if p.id == "default"), None)
        pub_val = (pub.v4.ip if (pub and pub.v4) else ("Checking…" if m.public_ip_loading else "—"))
        pub_det = "Default path"
        if pub and pub.v4:
            pub_det = " · ".join(x for x in (pub.v4.location, pub.v4.org) if x) or "Default path"
        elif pub and pub.v4_error:
            pub_det = pub.v4_error
        self.tiles_box.append(make_stat_tile(
            "Public IPv4", pub_val, pub_det, "network-server-symbolic", "teal",
            badge=(pub.v4.flag if (pub and pub.v4) else None),
        ))

        route = m.net.internet_route or {}
        eg_if = route.get("interface")
        eg_info = m.interface(eg_if)
        eg_tone = "purple" if (eg_info and eg_info.kind == "tunnel") else ("blue" if (eg_info and eg_info.is_vpn) else "green")
        eg_val = m.display_name(eg_if) if eg_if else "No route"
        eg_det = f"{eg_if} via {route.get('gateway') or '—'}" if eg_if else "No default internet route"
        self.tiles_box.append(make_stat_tile("Internet Egress", eg_val, eg_det, eg_info.icon_name if eg_info else "go-next-symbolic", eg_tone))

        hs = m.net.helper_status or {}
        statuses = hs.get("rules") or []
        active_cnt = sum(1 for st in statuses if st.get("state") == "applied")
        prob_cnt = sum(1 for st in statuses if st.get("state") in ("failed", "partial", "waiting"))
        live_only = len(m.live_but_unmanaged)
        r_val = f"{active_cnt} / {len(m.workspace.rules)} managed" + (f" · {live_only} live" if live_only else "")
        r_det = f"{len(hs.get('installedRoutes') or [])} managed routes · {prob_cnt} need attention · {len(m.pending_changes)} unapplied"
        self.tiles_box.append(make_stat_tile("Routing Rules", r_val, r_det, "network-wired-symbolic", "orange" if prob_cnt else "indigo"))

        # Public identity card
        clear_box(self.identity_box)
        trailing = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        if m.public_ip_updated:
            t_lbl = Gtk.Label(label=f"Checked {m.public_ip_updated.strftime('%H:%M')}")
            t_lbl.add_css_class("caption")
            t_lbl.add_css_class("dim-label")
            trailing.append(t_lbl)
        ref_btn = Gtk.Button(label="Refresh")
        ref_btn.set_sensitive(not m.public_ip_loading)
        ref_btn.connect("clicked", lambda *_: m.refresh_public_ips())
        trailing.append(ref_btn)
        id_card, id_inner = make_card("My public IP", "emblem-default-symbolic", trailing)
        cols = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        cols.set_homogeneous(True)
        cols.append(self._identity_col("IPv4", pub.v4 if pub else None, pub.v4_error if pub else None))
        cols.append(self._identity_col("IPv6", pub.v6 if pub else None, pub.v6_error if pub else None))
        id_inner.append(cols)
        if eg_if:
            foot = Gtk.Label(label=f"As seen through the system default path: {m.display_name(eg_if)} ({eg_if}).", xalign=0)
            foot.add_css_class("caption")
            foot.add_css_class("dim-label")
            id_inner.append(foot)
        self.identity_box.append(id_card)

        # Split: Public IP by path & Managed routes
        clear_box(self.split_box)
        self.split_box.append(self._public_by_path_card())
        self.split_box.append(self._managed_routes_card())

        # Active interfaces
        clear_box(self.ifaces_box)
        self.ifaces_box.append(self._interfaces_card())

    def _identity_col(self, label: str, ident, err: str | None) -> Gtk.Box:
        m = self.model
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        lbl = Gtk.Label(label=label, xalign=0)
        lbl.add_css_class("caption")
        lbl.add_css_class("dim-label")
        lbl.set_size_request(40, -1)
        top.append(lbl)
        if ident is not None:
            if ident.flag:
                f = Gtk.Label(label=ident.flag)
                f.add_css_class("title-2")
                top.append(f)
            ip_lbl = Gtk.Label(label=ident.ip, xalign=0)
            ip_lbl.add_css_class("title-3")
            ip_lbl.add_css_class("monospace")
            ip_lbl.set_selectable(True)
            ip_lbl.set_hexpand(True)
            top.append(ip_lbl)
            cp = Gtk.Button.new_from_icon_name("edit-copy-symbolic")
            cp.add_css_class("flat")
            cp.set_tooltip_text("Copy")
            cp.connect("clicked", lambda *_, s=ident.ip: copy_to_clipboard(s))
            top.append(cp)
        else:
            st = Gtk.Label(label="Checking…" if m.public_ip_loading else "not available", xalign=0)
            st.add_css_class("dim-label")
            top.append(st)
        col.append(top)

        if ident is not None:
            col.append(make_kv_row("Location", ident.location, mono=False))
            col.append(make_kv_row("ISP/Org", ident.org or "", mono=False))
            col.append(make_kv_row("Timezone", ident.timezone or "", mono=False))
            col.append(make_kv_row("Coords", ident.coords or "", mono=True))
        elif err and not m.public_ip_loading:
            el = Gtk.Label(label=err, xalign=0)
            el.add_css_class("caption")
            el.add_css_class("dim-label")
            el.set_wrap(True)
            col.append(el)
        return col

    def _public_by_path_card(self) -> Gtk.Box:
        m = self.model
        ref = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        ref.add_css_class("flat")
        ref.set_sensitive(not m.public_ip_loading)
        ref.connect("clicked", lambda *_: m.refresh_public_ips())
        card, inner = make_card("Public IP by path", "network-transmit-receive-symbolic", ref)

        if not m.public_ips:
            lbl = Gtk.Label(label="Checking…" if m.public_ip_loading else "Not checked yet", xalign=0)
            lbl.add_css_class("dim-label")
            inner.append(lbl)
            return card

        for idx, r in enumerate(m.public_ips):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            left.set_hexpand(True)
            t = Gtk.Label(label=r.title, xalign=0)
            t.add_css_class("heading")
            left.append(t)
            if r.error:
                sub = Gtk.Label(label=r.error, xalign=0)
                sub.add_css_class("caption")
                sub.add_css_class("dim-label")
                sub.set_wrap(True)
                left.append(sub)
            elif r.primary:
                info_str = " · ".join(x for x in (r.primary.location, r.primary.org) if x)
                if info_str:
                    sub = Gtk.Label(label=info_str, xalign=0)
                    sub.add_css_class("caption")
                    sub.add_css_class("dim-label")
                    left.append(sub)
            row.append(left)
            if r.latency_ms is not None:
                ms_lbl = Gtk.Label(label=f"{r.latency_ms:.0f} ms")
                ms_lbl.add_css_class("caption")
                ms_lbl.add_css_class("dim-label")
                row.append(ms_lbl)
            if r.primary and r.primary.flag:
                row.append(Gtk.Label(label=r.primary.flag))
            right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            v4_txt = r.v4.ip if r.v4 else ("—" if r.error else "no IPv4")
            v4_l = Gtk.Label(label=v4_txt, xalign=1)
            v4_l.add_css_class("monospace")
            v4_l.set_selectable(True)
            right.append(v4_l)
            if r.v6:
                v6_l = Gtk.Label(label=r.v6.ip, xalign=1)
                v6_l.add_css_class("caption")
                v6_l.add_css_class("monospace")
                v6_l.add_css_class("dim-label")
                right.append(v6_l)
            row.append(right)
            inner.append(row)
            if idx < len(m.public_ips) - 1:
                inner.append(Gtk.Separator())
        return card

    def _managed_routes_card(self) -> Gtk.Box:
        m = self.model
        card, inner = make_card("Managed routes", "go-jump-symbolic")
        routes = (m.net.helper_status or {}).get("installedRoutes") or []
        if not routes:
            msg = "No rules yet. Add one in Routing Rules." if not m.workspace.rules else "No routes installed yet."
            lbl = Gtk.Label(label=msg, xalign=0)
            lbl.add_css_class("dim-label")
            inner.append(lbl)
            btn = Gtk.Button(label="New Rule")
            btn.set_halign(Gtk.Align.START)
            btn.connect("clicked", lambda *_: (m.open_main_window("rules"), m.new_rule()))
            inner.append(btn)
            return card

        for r in routes[:12]:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            dot = Gtk.Image.new_from_icon_name("emblem-ok-symbolic")
            dot.add_css_class("bir-status-dot-green")
            row.append(dot)
            dst = r["destination"] if r.get("prefix") == 32 else f"{r['destination']}/{r['prefix']}"
            dl = Gtk.Label(label=dst, xalign=0)
            dl.add_css_class("monospace")
            row.append(dl)
            if r.get("sourceDomain"):
                sd = Gtk.Label(label=r["sourceDomain"], xalign=0)
                sd.add_css_class("caption")
                sd.add_css_class("dim-label")
                row.append(sd)
            sp = Gtk.Box()
            sp.set_hexpand(True)
            row.append(sp)
            gw = r.get("gateway")
            iface = r.get("interface")
            nh = f"{gw} ({iface})" if (gw and iface) else (gw or (f"direct on {iface}" if iface else "?"))
            nhl = Gtk.Label(label=nh, xalign=1)
            nhl.add_css_class("caption")
            nhl.add_css_class("monospace")
            nhl.add_css_class("dim-label")
            row.append(nhl)
            chk = Gtk.Button.new_from_icon_name("system-search-symbolic")
            chk.add_css_class("flat")
            chk.set_tooltip_text("Check Path")
            chk.connect("clicked", lambda *_, d=r["destination"]: m.open_path_check(d))
            row.append(chk)
            inner.append(row)

        if len(routes) > 12:
            more = Gtk.Button(label=f"Show all {len(routes)}…")
            more.set_halign(Gtk.Align.START)
            more.connect("clicked", lambda *_: m.open_main_window("routes"))
            inner.append(more)
        return card

    def _interfaces_card(self) -> Gtk.Box:
        m = self.model
        manage_btn = Gtk.Button(label="Manage")
        manage_btn.add_css_class("flat")
        manage_btn.connect("clicked", lambda *_: m.open_main_window("adapters"))
        card, inner = make_card("Active interfaces", "network-workgroup-symbolic", manage_btn)

        active_ifaces = [i for i in m.net.interfaces if i.is_active and i.ipv4 and not i.is_loopback]
        grid = Gtk.Grid(column_spacing=18, row_spacing=8)
        headers = ["Interface", "Type", "IPv4", "Router / Peer", "Down", "Up"]
        for c, h in enumerate(headers):
            hl = Gtk.Label(label=h, xalign=0)
            hl.add_css_class("caption")
            hl.add_css_class("dim-label")
            grid.attach(hl, c, 0, 1, 1)

        egress = (m.net.internet_route or {}).get("interface")
        for r_idx, i in enumerate(active_ifaces, start=1):
            name_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            name_box.append(Gtk.Image.new_from_icon_name(i.icon_name))
            dn = Gtk.Label(label=m.display_name(i.name), xalign=0)
            dn.add_css_class("heading")
            name_box.append(dn)
            raw_n = Gtk.Label(label=i.name, xalign=0)
            raw_n.add_css_class("caption")
            raw_n.add_css_class("monospace")
            raw_n.add_css_class("dim-label")
            name_box.append(raw_n)
            if i.name == egress:
                name_box.append(make_pill("Internet", "purple"))
            if i.name == m.net.primary_interface:
                name_box.append(make_pill("Primary", "blue"))
            grid.attach(name_box, 0, r_idx, 1, 1)

            tl = Gtk.Label(label=i.kind_label, xalign=0)
            tl.add_css_class("dim-label")
            grid.attach(tl, 1, r_idx, 1, 1)

            ip_l = Gtk.Label(label=i.primary_ipv4 or "—", xalign=0)
            ip_l.add_css_class("monospace")
            ip_l.set_selectable(True)
            grid.attach(ip_l, 2, r_idx, 1, 1)

            peer_txt = i.router or (i.ipv4[0].get("peer") if i.ipv4 else None) or "—"
            gw_l = Gtk.Label(label=peer_txt, xalign=0)
            gw_l.add_css_class("monospace")
            grid.attach(gw_l, 3, r_idx, 1, 1)

            rate = m.rates.get(i.name)
            dl_l = Gtk.Label(label=format_rate(rate.rx if rate else None), xalign=0)
            dl_l.add_css_class("numeric")
            grid.attach(dl_l, 4, r_idx, 1, 1)
            ul_l = Gtk.Label(label=format_rate(rate.tx if rate else None), xalign=0)
            ul_l.add_css_class("numeric")
            grid.attach(ul_l, 5, r_idx, 1, 1)

        inner.append(grid)
        return card

