from __future__ import annotations

from typing import TYPE_CHECKING

from gi.repository import Adw, Gtk

from ..models import (
    LINK_LOCAL_RANGES,
    TELEGRAM_LIST_ID,
    BlockList,
    compile_nftables_preview,
    normalize_cidr,
    parse_ks_entry,
)
from .widgets import PendingBanner, clear_box, make_card, make_kv_row, make_page_header, make_pill

if TYPE_CHECKING:
    from ..app_model import AppModel


class BlockListEditorDialog(Adw.Window):
    def __init__(self, parent: Gtk.Window | None, model: AppModel, bl: BlockList) -> None:
        super().__init__(transient_for=parent, modal=True)
        self.model = model
        self.bl = BlockList.from_dict(bl.to_dict())
        is_edit = any(b.id == bl.id for b in model.workspace.kill_switch.block_lists)
        self.set_title("Edit Block List" if is_edit else "New Block List")
        self.set_default_size(520, 520)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(outer)
        outer.append(Adw.HeaderBar())

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        body.set_margin_top(16)
        body.set_margin_bottom(16)
        body.set_margin_start(20)
        body.set_margin_end(20)
        body.set_vexpand(True)
        outer.append(body)

        grp = Adw.PreferencesGroup()
        self.name_row = Adw.EntryRow(title="Name (e.g. WhatsApp)")
        self.name_row.set_text(self.bl.name)
        self.name_row.connect("changed", lambda *_: self._validate())
        grp.add(self.name_row)

        self.src_row = Adw.EntryRow(title="Source URL for updates (optional)")
        self.src_row.set_text(self.bl.source_url or "")
        grp.add(self.src_row)
        body.append(grp)

        lbl = Gtk.Label(label="IP ranges (IPv4 or IPv6 CIDR) or domains, one per line", xalign=0)
        lbl.add_css_class("caption")
        lbl.add_css_class("dim-label")
        body.append(lbl)

        frame = Gtk.Frame()
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        self.tv = Gtk.TextView()
        self.tv.set_monospace(True)
        self.tv.set_top_margin(8)
        self.tv.set_bottom_margin(8)
        self.tv.set_left_margin(8)
        self.tv.set_right_margin(8)
        self.tv.get_buffer().set_text("\n".join(self.bl.entries))
        self.tv.get_buffer().connect("changed", lambda *_: self._validate())
        scroll.set_child(self.tv)
        frame.set_child(scroll)
        body.append(frame)

        self.err_lbl = Gtk.Label(label="", xalign=0)
        self.err_lbl.add_css_class("caption")
        self.err_lbl.set_wrap(True)
        body.append(self.err_lbl)

        outer.append(Gtk.Separator())
        foot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        foot.set_margin_top(12)
        foot.set_margin_bottom(12)
        foot.set_margin_start(16)
        foot.set_margin_end(16)
        sp = Gtk.Box()
        sp.set_hexpand(True)
        foot.append(sp)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        self.save_btn = Gtk.Button(label="Save")
        self.save_btn.add_css_class("suggested-action")
        self.save_btn.connect("clicked", self._save)
        foot.append(cancel)
        foot.append(self.save_btn)
        outer.append(foot)
        self._validate()

    def _entries(self) -> list[str]:
        buf = self.tv.get_buffer()
        txt = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        return [t.strip() for t in txt.replace(",", " ").replace("\n", " ").split() if t.strip()]

    def _validate(self) -> None:
        ents = self._entries()
        bad = [x for x in ents if parse_ks_entry(x) is None]
        self.err_lbl.set_visible(bool(bad))
        self.err_lbl.set_label(f"Invalid: {', '.join(bad)}" if bad else "")
        ok = bool(self.name_row.get_text().strip()) and bool(ents) and not bad
        self.save_btn.set_sensitive(ok)

    def _save(self, *_args) -> None:
        self.bl.name = self.name_row.get_text().strip()
        self.bl.entries = self._entries()
        src = self.src_row.get_text().strip()
        self.bl.source_url = src or None
        lst = self.model.workspace.kill_switch.block_lists
        for i, b in enumerate(lst):
            if b.id == self.bl.id:
                lst[i] = self.bl
                break
        else:
            lst.append(self.bl)
        self.model.save_workspace()
        self.close()


class KillSwitchView(Gtk.ScrolledWindow):
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

        prev_btn = Gtk.Button(label="Preview nftables Rules")
        prev_btn.connect("clicked", self._show_preview)
        self.root.append(make_page_header(
            "Kill Switch",
            "Stop traffic from leaking out of Ethernet/Wi-Fi when your full-tunnel VPN disconnects",
            prev_btn,
        ))

        self.pending_banner = PendingBanner(model, get_window)
        self.root.append(self.pending_banner)

        self.status_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.root.append(self.status_box)

        # Mode card
        mode_card, mode_inner = make_card("Mode", "preferences-system-symbolic")
        m_btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        m_btns.add_css_class("linked")
        self.m_dest = Gtk.ToggleButton(label="Block selected services")
        self.m_full = Gtk.ToggleButton(label="Block everything except allowed")
        self.m_full.set_group(self.m_dest)
        self.m_dest.connect("toggled", lambda b: self._set_mode("destinations") if b.get_active() else None)
        self.m_full.connect("toggled", lambda b: self._set_mode("fullSystem") if b.get_active() else None)
        m_btns.append(self.m_dest)
        m_btns.append(self.m_full)
        mode_inner.append(m_btns)
        self.mode_desc = Gtk.Label(label="", xalign=0)
        self.mode_desc.add_css_class("caption")
        self.mode_desc.add_css_class("dim-label")
        self.mode_desc.set_wrap(True)
        mode_inner.append(self.mode_desc)
        self.root.append(mode_card)

        self.ifaces_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.root.append(self.ifaces_box)

        self.detail_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.root.append(self.detail_box)

    def _set_mode(self, mode: str) -> None:
        if self.model.workspace.kill_switch.mode != mode:
            self.model.workspace.kill_switch.mode = mode
            self.model.save_workspace()

    def _show_preview(self, *_args) -> None:
        ks = self.model.workspace.kill_switch
        blocked = [c for bl in ks.block_lists if bl.enabled for x in bl.entries for c in [normalize_cidr(x)] if c]
        allowed = [c for x in ks.allow for c in [normalize_cidr(x)] if c]
        allowed += [c for x in self.model.net.learned_servers for c in [normalize_cidr(x)] if c]
        if ks.allow_local_networks:
            for i in self.model.net.interfaces:
                if i.name in ks.interfaces:
                    for a in i.ipv4:
                        if a.get("peer"):
                            allowed.append(f"{a['peer']}/32")
                        c = normalize_cidr(f"{a['address']}/{a.get('prefix', 32)}")
                        if c:
                            allowed.append(c)
            allowed += LINK_LOCAL_RANGES
        if ks.allow_rule_destinations:
            for r in (self.model.net.helper_status or {}).get("installedRoutes") or []:
                allowed.append(f"{r['destination']}/{r['prefix']}")
        dns = self.model.net.system_dns if ks.allow_dns else []
        text = compile_nftables_preview(ks.mode, ks.interfaces, blocked, allowed, dns, ks.allow_dhcp)

        win = Adw.Window(transient_for=self.get_window(), modal=True, title="nftables Rules Preview")
        win.set_default_size(700, 440)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.append(Adw.HeaderBar())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_top(16)
        box.set_margin_bottom(16)
        box.set_margin_start(16)
        box.set_margin_end(16)
        note = Gtk.Label(label="Domains are resolved by the root helper when applied, so they are not expanded here.", xalign=0)
        note.add_css_class("caption")
        note.add_css_class("dim-label")
        box.append(note)
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        lbl = Gtk.Label(label=text, xalign=0, yalign=0)
        lbl.add_css_class("monospace")
        lbl.set_selectable(True)
        scroll.set_child(lbl)
        box.append(scroll)
        outer.append(box)
        win.set_content(outer)
        win.present()

    def refresh(self) -> None:
        m = self.model
        ks = m.workspace.kill_switch
        live = (m.net.helper_status or {}).get("killSwitch")
        self.pending_banner.refresh()

        # Status card
        clear_box(self.status_box)
        active = bool(live and live.get("active") and live.get("pfEnabled"))
        st_card, st_inner = make_card()
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        icon = Gtk.Image.new_from_icon_name("security-high-symbolic" if active else "security-low-symbolic")
        icon.set_pixel_size(36)
        top.append(icon)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        col.set_hexpand(True)
        t_lbl = Gtk.Label(label="Armed" if active else ("On, not applied yet" if ks.enabled else "Off"), xalign=0)
        t_lbl.add_css_class("title-2")
        col.append(t_lbl)
        tunnel = m.full_tunnel
        if active and live:
            ifs_s = " and ".join(live.get("interfaces") or [])
            msg = (
                f"Tunnel {tunnel.name} is up, so protected traffic flows through it. If it drops, {ifs_s} block it."
                if tunnel
                else f"No tunnel is up: protected traffic is being blocked on {ifs_s} right now."
            )
        else:
            msg = "Uses Linux nftables in its own isolated table (inet splitroute); ufw/iptables/system rules are not modified."
        m_lbl = Gtk.Label(label=msg, xalign=0)
        m_lbl.add_css_class("dim-label")
        m_lbl.set_wrap(True)
        col.append(m_lbl)
        if not m.net.service.daemon_installed and ks.enabled:
            w = Gtk.Label(label="Turn on Persistence so the kill switch comes back after a reboot.", xalign=0)
            w.add_css_class("caption")
            col.append(w)
        for err in (live or {}).get("errors") or []:
            el = Gtk.Label(label=err, xalign=0)
            el.add_css_class("caption")
            el.set_wrap(True)
            col.append(el)
        top.append(col)

        sw = Gtk.Switch()
        sw.set_valign(Gtk.Align.CENTER)
        sw.set_active(ks.enabled)
        sw.connect("notify::active", self._on_ks_toggle)
        top.append(sw)
        st_inner.append(top)

        if active and live:
            st_inner.append(Gtk.Separator())
            mode_s = "Selected services" if live.get("mode") == "destinations" else "Everything except allowed"
            st_inner.append(make_kv_row("Mode", mode_s, mono=False))
            cnt = len(live.get("blocked") or []) if live.get("mode") == "destinations" else len(live.get("allowed") or [])
            st_inner.append(make_kv_row("Blocked ranges" if live.get("mode") == "destinations" else "Allowed ranges", str(cnt), mono=False))
            off_btn = Gtk.Button(label="Turn Off Now")
            off_btn.add_css_class("destructive-action")
            off_btn.set_halign(Gtk.Align.END)
            off_btn.connect("clicked", lambda *_: m.turn_off_kill_switch_now())
            st_inner.append(off_btn)
        self.status_box.append(st_card)

        # Mode text
        self.m_dest.set_active(ks.mode == "destinations")
        self.m_full.set_active(ks.mode == "fullSystem")
        self.mode_desc.set_label(
            "Only the services listed below are blocked on the guarded interfaces. While the VPN tunnel is up their traffic goes through it; when the tunnel drops, they cannot fall back."
            if ks.mode == "destinations"
            else "Everything on the guarded interfaces is blocked except your local network, the VPN server, your routing-rule destinations and DNS."
        )

        # Guarded interfaces
        clear_box(self.ifaces_box)
        if_card, if_inner = make_card("Guarded interfaces", "network-wired-symbolic")
        note = Gtk.Label(label="Traffic leaving through these interfaces is filtered. Never guard the VPN tunnel itself.", xalign=0)
        note.add_css_class("caption")
        note.add_css_class("dim-label")
        if_inner.append(note)

        candidates = [
            i for i in m.net.interfaces
            if (i.is_physical or i.kind in ("ppp", "ipsec")) and (i.is_active or i.name in ks.interfaces)
        ]
        seen_names = {c.name for c in candidates}
        for name in ks.interfaces:
            if name not in seen_names:
                from ..scanner import InterfaceInfo
                candidates.append(InterfaceInfo(name=name, kind=m.interface(name).kind if m.interface(name) else "other"))

        for i in candidates:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            chk = Gtk.CheckButton(label=f"{m.display_name(i.name)} ({i.name})")
            chk.set_active(i.name in ks.interfaces)
            chk.connect("toggled", self._on_iface_toggled, i.name)
            row.append(chk)
            if i.primary_ipv4:
                ip_l = Gtk.Label(label=i.primary_ipv4)
                ip_l.add_css_class("caption")
                ip_l.add_css_class("monospace")
                ip_l.add_css_class("dim-label")
                row.append(ip_l)
            if not i.is_active:
                row.append(make_pill("Inactive", "gray"))
            if_inner.append(row)
        self.ifaces_box.append(if_card)

        # Mode details
        clear_box(self.detail_box)
        if ks.mode == "destinations":
            self.detail_box.append(self._block_lists_card())
        else:
            self.detail_box.append(self._allow_card())

    def _on_ks_toggle(self, sw: Gtk.Switch, _p) -> None:
        on = sw.get_active()
        if on == self.model.workspace.kill_switch.enabled:
            return
        self.model.workspace.kill_switch.enabled = on
        if on and not self.model.workspace.kill_switch.interfaces:
            self.model.workspace.kill_switch.interfaces = self.model.default_guarded_interfaces
        self.model.save_workspace()

    def _on_iface_toggled(self, chk: Gtk.CheckButton, name: str) -> None:
        ks = self.model.workspace.kill_switch
        lst = [x for x in ks.interfaces if x != name]
        if chk.get_active():
            lst.append(name)
        ks.interfaces = sorted(lst)
        self.model.save_workspace()

    def _block_lists_card(self) -> Gtk.Box:
        m = self.model
        ks = m.workspace.kill_switch
        add_btn = Gtk.Button(label="Add List")
        add_btn.connect("clicked", lambda *_: BlockListEditorDialog(self.get_window(), m, BlockList()).present())
        card, inner = make_card("Blocked services", "action-unavailable-symbolic", add_btn)

        if not ks.block_lists:
            lbl = Gtk.Label(label="No lists. Add one with the IP ranges or domains of a service.", xalign=0)
            lbl.add_css_class("dim-label")
            inner.append(lbl)

        for bl in ks.block_lists:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            sw = Gtk.Switch()
            sw.set_active(bl.enabled)
            sw.set_valign(Gtk.Align.CENTER)
            sw.connect("notify::active", self._toggle_block_list, bl.id)
            row.append(sw)

            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            col.set_hexpand(True)
            col.append(Gtk.Label(label=bl.name, xalign=0))
            preview = ", ".join(bl.entries[:5]) + (" …" if len(bl.entries) > 5 else "")
            sub = Gtk.Label(label=f"{len(bl.entries)} entries · {preview}", xalign=0)
            sub.add_css_class("caption")
            sub.add_css_class("monospace")
            sub.add_css_class("dim-label")
            col.append(sub)
            row.append(col)

            if bl.source_url:
                upd = Gtk.Button(label="Update from Source")
                upd.set_tooltip_text(bl.source_url)
                upd.connect("clicked", lambda *_, bid=bl.id: m.update_block_list(bid))
                row.append(upd)
            ed = Gtk.Button(label="Edit…")
            ed.connect("clicked", lambda *_, b=bl: BlockListEditorDialog(self.get_window(), m, b).present())
            row.append(ed)
            if bl.id != TELEGRAM_LIST_ID:
                dl = Gtk.Button.new_from_icon_name("user-trash-symbolic")
                dl.add_css_class("flat")
                dl.connect("clicked", lambda *_, bid=bl.id: self._delete_block_list(bid))
                row.append(dl)
            inner.append(row)
            inner.append(Gtk.Separator())

        if not any(b.id == TELEGRAM_LIST_ID for b in ks.block_lists):
            tg_btn = Gtk.Button(label="Add Telegram Preset")
            tg_btn.set_halign(Gtk.Align.START)
            tg_btn.connect("clicked", lambda *_: (ks.block_lists.insert(0, BlockList.telegram()), m.save_workspace()))
            inner.append(tg_btn)
        return card

    def _toggle_block_list(self, sw: Gtk.Switch, _p, bid: str) -> None:
        for b in self.model.workspace.kill_switch.block_lists:
            if b.id == bid:
                if b.enabled != sw.get_active():
                    b.enabled = sw.get_active()
                    self.model.save_workspace()
                break

    def _delete_block_list(self, bid: str) -> None:
        self.model.workspace.kill_switch.block_lists = [
            b for b in self.model.workspace.kill_switch.block_lists if b.id != bid
        ]
        self.model.save_workspace()

    def _allow_card(self) -> Gtk.Box:
        m = self.model
        ks = m.workspace.kill_switch
        card, inner = make_card("Allowed through guarded interfaces", "security-high-symbolic")

        def add_chk(label: str, active: bool, attr: str) -> None:
            chk = Gtk.CheckButton(label=label)
            chk.set_active(active)
            chk.connect("toggled", lambda c, a=attr: (setattr(ks, a, c.get_active()), m.save_workspace()))
            inner.append(chk)

        add_chk("Local networks, link-local, multicast", ks.allow_local_networks, "allow_local_networks")
        add_chk("Destinations of your routing rules", ks.allow_rule_destinations, "allow_rule_destinations")
        add_chk(f"DNS to the system resolvers ({', '.join(m.net.system_dns)})", ks.allow_dns, "allow_dns")
        add_chk("DHCP (keeps Ethernet/Wi-Fi getting an address)", ks.allow_dhcp, "allow_dhcp")
        add_chk("Learn VPN servers automatically (xray, sing-box, OpenVPN, WireGuard…)", ks.learn_vpn_servers, "learn_vpn_servers")

        inner.append(Gtk.Separator())
        learned = m.net.learned_servers
        l_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        l_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        l_col.set_hexpand(True)
        l_col.append(Gtk.Label(label="VPN servers learned", xalign=0))
        l_txt = ", ".join(learned) if learned else "None yet. Connect your VPN, then press Detect."
        ll = Gtk.Label(label=l_txt, xalign=0)
        ll.add_css_class("caption")
        ll.add_css_class("monospace")
        ll.add_css_class("dim-label")
        ll.set_wrap(True)
        l_col.append(ll)
        l_row.append(l_col)

        det_btn = Gtk.Button(label="Detect VPN Servers")
        det_btn.set_sensitive(m.full_tunnel is not None)
        det_btn.connect("clicked", lambda *_: m.detect_vpn_servers())
        fgt_btn = Gtk.Button(label="Forget")
        fgt_btn.set_sensitive(bool(learned))
        fgt_btn.connect("clicked", lambda *_: m.forget_learned_servers())
        l_row.append(det_btn)
        l_row.append(fgt_btn)
        inner.append(l_row)

        inner.append(Gtk.Separator())
        inner.append(Gtk.Label(label="Extra allowed destinations (one per line: IP, CIDR or domain)", xalign=0))
        frame = Gtk.Frame()
        tv = Gtk.TextView()
        tv.set_monospace(True)
        tv.set_top_margin(6)
        tv.set_bottom_margin(6)
        tv.set_left_margin(6)
        tv.set_right_margin(6)
        tv.get_buffer().set_text("\n".join(ks.allow))
        tv.get_buffer().connect("changed", self._on_allow_changed, tv)
        sc = Gtk.ScrolledWindow()
        sc.set_min_content_height(80)
        sc.set_child(tv)
        frame.set_child(sc)
        inner.append(frame)

        if not learned and not ks.allow:
            w = Gtk.Label(label="Without the VPN server allowed, the VPN cannot reconnect while this mode is on.", xalign=0)
            w.add_css_class("caption")
            inner.append(w)
        return card

    def _on_allow_changed(self, buf: Gtk.TextBuffer, _tv) -> None:
        txt = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        items = [t.strip() for t in txt.replace(",", " ").replace("\n", " ").split() if t.strip()]
        if items != self.model.workspace.kill_switch.allow:
            self.model.workspace.kill_switch.allow = items
            self.model.save_workspace()
