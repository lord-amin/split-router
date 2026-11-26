from __future__ import annotations

from typing import TYPE_CHECKING

from gi.repository import GLib, Gtk

from .widgets import clear_box, make_banner, make_card, make_kv_row, make_page_header, make_pill

if TYPE_CHECKING:
    from ..app_model import AppModel

VERDICT_STYLES = {
    "direct": ("go-up-symbolic", "green"),
    "vpn": ("network-vpn-symbolic", "blue"),
    "tunnel": ("security-high-symbolic", "purple"),
    "local": ("go-home-symbolic", "gray"),
    "noRoute": ("dialog-error-symbolic", "red"),
    "blocked": ("action-unavailable-symbolic", "red"),
}


class PathCheckView(Gtk.ScrolledWindow):
    def __init__(self, model: AppModel) -> None:
        super().__init__()
        self.model = model
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self.root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.root.set_margin_top(24)
        self.root.set_margin_bottom(24)
        self.root.set_margin_start(24)
        self.root.set_margin_end(24)
        self.set_child(self.root)

        self.root.append(make_page_header("Path Check", "Find out which interface and gateway an IP or domain uses"))

        # Search bar
        sbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.query_entry = Gtk.Entry(placeholder_text="IP address or domain, e.g. example.com")
        self.query_entry.set_hexpand(True)
        self.query_entry.connect("activate", lambda *_: self._run_check())
        self.check_btn = Gtk.Button(label="Check")
        self.check_btn.add_css_class("suggested-action")
        self.check_btn.connect("clicked", lambda *_: self._run_check())
        sbar.append(self.query_entry)
        sbar.append(self.check_btn)
        self.root.append(sbar)

        self.suggestions_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.root.append(self.suggestions_box)

        self.result_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.root.append(self.result_box)

    def _run_check(self, target: str | None = None) -> None:
        if target is not None:
            self.query_entry.set_text(target)
        q = self.query_entry.get_text().strip()
        if q:
            self.model.check_path(q)

    def refresh(self) -> None:
        m = self.model
        if m.path_query and self.query_entry.get_text() != m.path_query:
            self.query_entry.set_text(m.path_query)
        self.check_btn.set_sensitive(not m.path_checking)

        clear_box(self.suggestions_box)
        lbl = Gtk.Label(label="Try:")
        lbl.add_css_class("dim-label")
        self.suggestions_box.append(lbl)
        sugs: list[str] = []
        for r in m.workspace.rules:
            for d in r.destinations:
                if d not in sugs:
                    sugs.append(d)
                if len(sugs) >= 4:
                    break
            if len(sugs) >= 4:
                break
        if "1.1.1.1" not in sugs:
            sugs.append("1.1.1.1")
        for s in sugs:
            b = Gtk.Button(label=s)
            b.connect("clicked", lambda *_, target=s: self._run_check(target))
            self.suggestions_box.append(b)

        clear_box(self.result_box)
        res = m.path_result
        if res is None:
            return

        if res.is_domain:
            d_card, d_inner = make_card("DNS", "system-search-symbolic")
            d_inner.append(make_kv_row("Name", res.query))
            d_inner.append(make_kv_row("IPv4", ", ".join(res.ipv4)))
            d_inner.append(make_kv_row("IPv6", ", ".join(res.ipv6)))
            d_inner.append(make_kv_row("Lookup time", f"{res.dns_ms:.0f} ms" if res.dns_ms is not None else "", mono=False))
            d_inner.append(make_kv_row("Resolver", ", ".join(m.net.system_dns)))
            self.result_box.append(d_card)

        if res.error:
            self.result_box.append(make_banner("Note", res.error, tone="orange"))

        for hop in res.hops:
            icon_name, tone = VERDICT_STYLES.get(hop.verdict, ("dialog-information-symbolic", "gray"))
            h_card, h_inner = make_card()
            top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            ic = Gtk.Image.new_from_icon_name(icon_name)
            ic.set_pixel_size(28)
            top.append(ic)

            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            col.set_hexpand(True)
            hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            ip_lbl = Gtk.Label(label=hop.ip, xalign=0)
            ip_lbl.add_css_class("title-3")
            ip_lbl.add_css_class("monospace")
            ip_lbl.set_selectable(True)
            ip_lbl.set_hexpand(True)
            hdr.append(ip_lbl)

            target_str = res.query if res.is_domain else hop.ip
            blk_btn = Gtk.Button(label="Block")
            blk_btn.add_css_class("destructive-action")
            blk_btn.connect("clicked", lambda *_, t=target_str: m.block_and_apply(t, note="Blocked from Path Check"))
            hdr.append(blk_btn)

            rule_obj = next((r for r in m.workspace.rules if r.id == hop.rule_id), None) if hop.rule_id else None
            if rule_obj is not None:
                ed = Gtk.Button(label=f"Edit Rule “{rule_obj.name}”")
                ed.connect("clicked", lambda *_, r=rule_obj: m.edit_rule(r))
                hdr.append(ed)
            else:
                cr = Gtk.Button(label="Create Rule for this IP")
                cr.connect("clicked", lambda *_, t=target_str: (m.open_main_window("rules"), m.new_rule(t)))
                hdr.append(cr)
            col.append(hdr)

            hl = Gtk.Label(label=hop.headline, xalign=0)
            hl.add_css_class("heading")
            col.append(hl)

            if hop.lookup:
                pills = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                pills.append(make_pill(f"Interface {hop.lookup.get('interface') or '?'}", tone))
                pills.append(make_pill(f"Gateway {hop.lookup.get('gateway') or 'none'}", "gray"))
                pills.append(make_pill(f"Route {hop.lookup.get('matchedRouteLabel', '')}", "gray"))
                col.append(pills)

            for line in hop.explanation:
                el = Gtk.Label(label=f"• {line}", xalign=0)
                el.add_css_class("caption")
                el.add_css_class("dim-label")
                el.set_wrap(True)
                col.append(el)

            tcp_txt = hop.tcp443 or "Testing TCP 443…"
            tl = Gtk.Label(label=tcp_txt, xalign=0)
            tl.add_css_class("caption")
            col.append(tl)

            top.append(col)
            h_inner.append(top)
            self.result_box.append(h_card)

        if res.hops:
            first_ip = res.hops[0].ip
            if m.traceroute.running:
                tr_btn = Gtk.Button(label="Stop")
                tr_btn.connect("clicked", lambda *_: (m.traceroute.stop(), self.refresh()))
            else:
                tr_btn = Gtk.Button(label=f"Run Traceroute to {first_ip}")
                tr_btn.connect("clicked", lambda *_, ip=first_ip: m.traceroute.start(ip, lambda: GLib.idle_add(self.refresh)))
            tr_card, tr_inner = make_card("Traceroute", "network-transmit-receive-symbolic", tr_btn)
            if not m.traceroute.lines:
                tl = Gtk.Label(label="Shows each router hop on the way. Hops through a tunnel appear as the tunnel's far end.", xalign=0)
                tl.add_css_class("dim-label")
                tr_inner.append(tl)
            else:
                out = Gtk.Label(label="\n".join(m.traceroute.lines), xalign=0)
                out.add_css_class("monospace")
                out.add_css_class("caption")
                out.set_selectable(True)
                out.set_wrap(True)
                tr_inner.append(out)
            self.result_box.append(tr_card)

