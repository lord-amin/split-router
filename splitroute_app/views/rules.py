from __future__ import annotations

import socket
import threading
from typing import TYPE_CHECKING

from gi.repository import Adw, GLib, Gtk

from ..models import Destination, RouteRule, RouteVia
from .widgets import (
    PendingBanner,
    clear_box,
    make_banner,
    make_page_header,
    make_pill,
    rule_state_pill,
)

if TYPE_CHECKING:
    from ..app_model import AppModel


class RuleEditorDialog(Adw.Window):
    def __init__(self, parent: Gtk.Window | None, model: AppModel, rule: RouteRule) -> None:
        super().__init__(transient_for=parent, modal=True, title="Edit Routing Rule")
        self.model = model
        self.rule = RouteRule.from_dict(rule.to_dict())
        self.is_new = not any(r.id == rule.id for r in model.workspace.rules)
        self.set_title("New Routing Rule" if self.is_new else "Edit Routing Rule")
        self.set_default_size(640, 720)
        self.preview_resolved: dict[str, list[str]] = {}

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(outer)

        hb = Adw.HeaderBar()
        outer.append(hb)

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        outer.append(scroll)

        form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        form.set_margin_top(18)
        form.set_margin_bottom(18)
        form.set_margin_start(20)
        form.set_margin_end(20)
        scroll.set_child(form)

        # General
        gen_grp = Adw.PreferencesGroup(title="General")
        self.name_row = Adw.EntryRow(title="Name")
        self.name_row.set_text(self.rule.name)
        self.name_row.connect("changed", lambda *_: self._refresh_preview())
        gen_grp.add(self.name_row)

        self.enabled_row = Adw.SwitchRow(title="Enabled")
        self.enabled_row.set_active(self.rule.enabled)
        gen_grp.add(self.enabled_row)

        self.persist_row = Adw.SwitchRow(
            title="Persistent (restored after reboot)",
            subtitle="Off = Until reboot only",
        )
        self.persist_row.set_active(self.rule.persistent)
        gen_grp.add(self.persist_row)

        self.notes_row = Adw.EntryRow(title="Notes")
        self.notes_row.set_text(self.rule.notes)
        gen_grp.add(self.notes_row)

        self.keep_row = Adw.SwitchRow(
            title="Leave routes in place when removed",
            subtitle="Use for routes another app also needs, like a VPN server's own route",
        )
        self.keep_row.set_active(self.rule.keep_routes_on_remove)
        gen_grp.add(self.keep_row)
        form.append(gen_grp)

        # Destinations
        dest_grp = Adw.PreferencesGroup(
            title="Destinations",
            description="One per line: an IPv4 address (203.0.113.10), a network (10.20.0.0/16) or a domain (example.com).",
        )
        tv_frame = Gtk.Frame()
        self.dest_view = Gtk.TextView()
        self.dest_view.set_monospace(True)
        self.dest_view.set_top_margin(8)
        self.dest_view.set_bottom_margin(8)
        self.dest_view.set_left_margin(8)
        self.dest_view.set_right_margin(8)
        self.dest_view.get_buffer().set_text("\n".join(self.rule.destinations))
        self.dest_view.get_buffer().connect("changed", lambda *_: self._refresh_preview())
        tv_scroll = Gtk.ScrolledWindow()
        tv_scroll.set_min_content_height(100)
        tv_scroll.set_child(self.dest_view)
        tv_frame.set_child(tv_scroll)
        dest_grp.add(tv_frame)

        self.chips_box = Gtk.FlowBox()
        self.chips_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.chips_box.set_row_spacing(4)
        self.chips_box.set_column_spacing(6)
        self.chips_box.set_margin_top(6)
        dest_grp.add(self.chips_box)
        form.append(dest_grp)

        # Send traffic via
        via_grp = Adw.PreferencesGroup(title="Send traffic via")
        mode_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        mode_box.add_css_class("linked")
        mode_box.set_margin_bottom(8)
        self.m_router = Gtk.ToggleButton(label="Interface's router")
        self.m_direct = Gtk.ToggleButton(label="Directly on interface")
        self.m_gw = Gtk.ToggleButton(label="Specific gateway")
        self.m_direct.set_group(self.m_router)
        self.m_gw.set_group(self.m_router)
        mode_box.append(self.m_router)
        mode_box.append(self.m_direct)
        mode_box.append(self.m_gw)
        via_grp.add(mode_box)

        self.iface_combo = Gtk.ComboBoxText()
        cur_if = self.rule.via.interface
        choices = [i for i in model.net.interfaces if not i.is_system_noise or i.name == cur_if]
        seen_if = {i.name for i in choices}
        for i in choices:
            ip_s = f" · {i.primary_ipv4}" if i.primary_ipv4 else ""
            act_s = "" if i.is_active else " (inactive)"
            self.iface_combo.append(i.name, f"{model.display_name(i.name)} — {i.name}{ip_s}{act_s}")
        if cur_if and cur_if not in seen_if:
            self.iface_combo.append(cur_if, cur_if)
        if cur_if:
            self.iface_combo.set_active_id(cur_if)
        elif choices:
            self.iface_combo.set_active_id(choices[0].name)
        self.iface_combo.connect("changed", lambda *_: self._refresh_preview())
        via_grp.add(self.iface_combo)

        self.mode_help = Gtk.Label(label="", xalign=0)
        self.mode_help.add_css_class("caption")
        self.mode_help.add_css_class("dim-label")
        self.mode_help.set_wrap(True)
        self.mode_help.set_margin_top(6)
        via_grp.add(self.mode_help)

        self.gw_row = Adw.EntryRow(title="Gateway IP (e.g. 192.168.1.1)")
        self.gw_row.set_text(self.rule.via.gateway)
        self.gw_row.connect("changed", lambda *_: self._refresh_preview())
        via_grp.add(self.gw_row)
        form.append(via_grp)

        if self.rule.via.kind == "interfaceDirect":
            self.m_direct.set_active(True)
        elif self.rule.via.kind == "gateway":
            self.m_gw.set_active(True)
        else:
            self.m_router.set_active(True)

        self.m_router.connect("toggled", lambda *_: self._refresh_preview())
        self.m_direct.connect("toggled", lambda *_: self._refresh_preview())
        self.m_gw.connect("toggled", lambda *_: self._refresh_preview())

        # What will run
        prev_grp = Adw.PreferencesGroup(title="What will run")
        self.preview_lbl = Gtk.Label(label="", xalign=0)
        self.preview_lbl.add_css_class("caption")
        self.preview_lbl.add_css_class("monospace")
        self.preview_lbl.set_selectable(True)
        self.preview_lbl.set_wrap(True)
        prev_grp.add(self.preview_lbl)
        self.resolve_btn = Gtk.Button(label="Resolve domains now")
        self.resolve_btn.set_halign(Gtk.Align.START)
        self.resolve_btn.set_margin_top(6)
        self.resolve_btn.connect("clicked", self._resolve_domains)
        prev_grp.add(self.resolve_btn)
        form.append(prev_grp)

        self.err_lbl = Gtk.Label(label="", xalign=0)
        self.err_lbl.add_css_class("caption")
        self.err_lbl.set_wrap(True)
        form.append(self.err_lbl)

        # Footer
        outer.append(Gtk.Separator())
        foot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        foot.set_margin_top(12)
        foot.set_margin_bottom(12)
        foot.set_margin_start(16)
        foot.set_margin_end(16)
        if not self.is_new:
            del_btn = Gtk.Button(label="Delete")
            del_btn.add_css_class("destructive-action")
            del_btn.connect("clicked", self._on_delete)
            foot.append(del_btn)
        sp = Gtk.Box()
        sp.set_hexpand(True)
        foot.append(sp)
        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", lambda *_: self.close())
        self.save_btn = Gtk.Button(label="Save")
        self.save_btn.connect("clicked", lambda *_: self._save(apply_now=False))
        self.save_apply_btn = Gtk.Button(label="Save & Apply")
        self.save_apply_btn.add_css_class("suggested-action")
        self.save_apply_btn.connect("clicked", lambda *_: self._save(apply_now=True))
        foot.append(cancel_btn)
        foot.append(self.save_btn)
        foot.append(self.save_apply_btn)
        outer.append(foot)

        self._refresh_preview()

    def _build_draft(self) -> RouteRule:
        buf = self.dest_view.get_buffer()
        txt = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        dests = [
            tok.strip().lower()
            for tok in txt.replace(",", " ").replace("\n", " ").split()
            if tok.strip()
        ]
        if self.m_router.get_active():
            via = RouteVia(kind="interfaceRouter", interface=self.iface_combo.get_active_id() or "")
        elif self.m_direct.get_active():
            via = RouteVia(kind="interfaceDirect", interface=self.iface_combo.get_active_id() or "")
        else:
            via = RouteVia(kind="gateway", gateway=self.gw_row.get_text().strip())
        return RouteRule(
            id=self.rule.id,
            name=self.name_row.get_text(),
            destinations=dests,
            via=via,
            enabled=self.enabled_row.get_active(),
            persistent=self.persist_row.get_active(),
            notes=self.notes_row.get_text(),
            keep_routes_on_remove=self.keep_row.get_active(),
        )

    def _refresh_preview(self) -> None:
        is_gw = self.m_gw.get_active()
        self.iface_combo.set_visible(not is_gw)
        self.mode_help.set_visible(not is_gw)
        self.gw_row.set_visible(is_gw)

        draft = self._build_draft()
        if not is_gw:
            iface = draft.via.interface
            info = self.model.interface(iface)
            if self.m_router.get_active():
                if info and info.router:
                    self.mode_help.set_label(
                        f"Uses {iface}'s current router ({info.router}). If the router changes (e.g. another network), routes follow it automatically."
                    )
                elif info and info.is_point_to_point:
                    self.mode_help.set_label(f"{iface} is point-to-point, so traffic is sent directly on it.")
                else:
                    self.mode_help.set_label(f"{iface} has no router right now; the rule waits until it gets one.")
            else:
                self.mode_help.set_label(f"Sends packets straight out of {iface} without a gateway. Use this for VPN/PPP/tunnel interfaces.")

        # Chips
        child = self.chips_box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.chips_box.remove(child)
            child = nxt
        for raw in draft.destinations:
            d = Destination.parse(raw)
            if d:
                self.chips_box.append(make_pill(f"{d.kind_label}: {d.display}", "blue"))
            else:
                self.chips_box.append(make_pill(f"Invalid: {raw}", "red"))

        # Command preview
        if draft.via.kind == "interfaceRouter":
            info = self.model.interface(draft.via.interface)
            hop = f"via {info.router} dev {draft.via.interface}" if (info and info.router) else f"dev {draft.via.interface}"
        elif draft.via.kind == "interfaceDirect":
            hop = f"dev {draft.via.interface}"
        else:
            hop = f"via {draft.via.gateway or '<gateway>'}"

        lines = []
        has_domain = False
        for d in draft.parsed_destinations():
            if d.kind == "host":
                lines.append(f"ip route replace {d.address}/32 {hop} metric 10")
            elif d.kind == "network":
                lines.append(f"ip route replace {d.address}/{d.prefix} {hop} metric 10")
            elif d.kind == "domain":
                has_domain = True
                if d.domain in self.preview_resolved:
                    for ip in self.preview_resolved[d.domain]:
                        lines.append(f"ip route replace {ip}/32 {hop} metric 10    # {d.domain}")
                else:
                    lines.append(f"ip route replace <each IPv4 of {d.domain}> {hop} metric 10")
        self.preview_lbl.set_label("\n".join(lines) if lines else "Add a destination to see the commands.")
        self.resolve_btn.set_sensitive(has_domain)

        errs = draft.validation_errors()
        self.err_lbl.set_visible(bool(errs))
        self.err_lbl.set_label("\n".join(errs))
        self.save_btn.set_sensitive(not errs)
        self.save_apply_btn.set_sensitive(not errs)

    def _resolve_domains(self, *_args) -> None:
        draft = self._build_draft()
        names = [d.domain for d in draft.parsed_destinations() if d.kind == "domain"]
        if not names:
            return
        self.resolve_btn.set_sensitive(False)

        def worker() -> None:
            out: dict[str, list[str]] = {}
            for n in names:
                try:
                    infos = socket.getaddrinfo(n, None, socket.AF_INET, socket.SOCK_STREAM)
                    ips = sorted({x[4][0] for x in infos})
                    out[n] = ips or ["(no IPv4 address)"]
                except Exception:
                    out[n] = ["(lookup failed)"]

            def done() -> None:
                self.preview_resolved = out
                self._refresh_preview()

            GLib.idle_add(done)

        threading.Thread(target=worker, daemon=True).start()

    def _on_delete(self, *_args) -> None:
        self.model.delete_rules({self.rule.id})
        self.close()

    def _save(self, apply_now: bool) -> None:
        draft = self._build_draft()
        if draft.validation_errors():
            return
        self.model.upsert_rule(draft)
        self.close()
        if apply_now:
            self.model.apply()


class RulesView(Gtk.Box):
    def __init__(self, model: AppModel, get_window, open_import_cb) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.model = model
        self.get_window = get_window
        self.open_import_cb = open_import_cb
        self.search_text = ""

        self.set_margin_top(24)
        self.set_margin_bottom(24)
        self.set_margin_start(24)
        self.set_margin_end(24)

        trailing = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        imp_btn = Gtk.Button(label="Import Static Routes")
        imp_btn.connect("clicked", lambda *_: self.open_import_cb())
        new_btn = Gtk.Button(label="New Rule")
        new_btn.add_css_class("suggested-action")
        new_btn.connect("clicked", lambda *_: self.model.new_rule())
        trailing.append(imp_btn)
        trailing.append(new_btn)
        self.append(make_page_header("Routing Rules", "Send IPs, networks or domains out of the interface you choose", trailing))

        self.pending_banner = PendingBanner(model, get_window)
        self.append(self.pending_banner)
        self.svc_banner_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.append(self.svc_banner_box)

        self.search_entry = Gtk.SearchEntry(placeholder_text="Search rules")
        self.search_entry.connect("search-changed", lambda e: self._on_search(e.get_text()))
        self.append(self.search_entry)

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        self.list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        scroll.set_child(self.list_box)
        self.append(scroll)

    def _on_search(self, text: str) -> None:
        self.search_text = text.strip().lower()
        self.refresh()

    def refresh(self) -> None:
        m = self.model
        self.pending_banner.refresh()

        clear_box(self.svc_banner_box)
        if not m.net.service.daemon_installed and any(r.persistent for r in m.workspace.rules):
            turn_on = Gtk.Button(label="Turn On")
            turn_on.connect("clicked", lambda *_: m.set_service(True))
            self.svc_banner_box.append(make_banner(
                "Persistent rules need the background service",
                "Without it, routes are not restored after a reboot or network change.",
                tone="yellow",
                actions=[turn_on],
            ))

        clear_box(self.list_box)
        q = self.search_text
        rules = [
            r for r in m.workspace.rules
            if not q or q in r.name.lower() or any(q in d for d in r.destinations) or q in r.notes.lower()
        ]
        if not m.workspace.rules:
            st = Adw.StatusPage(
                icon_name="network-wired-symbolic",
                title="No routing rules",
                description="Create a rule to send a domain or IP directly over Ethernet/Wi-Fi, through a VPN, or via a specific gateway.",
            )
            btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            btns.set_halign(Gtk.Align.CENTER)
            b1 = Gtk.Button(label="New Rule")
            b1.add_css_class("suggested-action")
            b1.connect("clicked", lambda *_: m.new_rule())
            b2 = Gtk.Button(label="Import Existing Static Routes…")
            b2.connect("clicked", lambda *_: self.open_import_cb())
            btns.append(b1)
            btns.append(b2)
            st.set_child(btns)
            self.list_box.append(st)
            return

        pending_ids = {c.rule.id for c in m.pending_changes}
        for rule in rules:
            card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            card.add_css_class("card")
            inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            inner.set_margin_top(10)
            inner.set_margin_bottom(10)
            inner.set_margin_start(14)
            inner.set_margin_end(14)
            card.append(inner)

            sw = Gtk.Switch()
            sw.set_active(rule.enabled)
            sw.set_valign(Gtk.Align.CENTER)
            sw.connect("notify::active", lambda s, _p, rid=rule.id: m.set_enabled(rid, s.get_active()))
            inner.append(sw)

            info_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            info_col.set_hexpand(True)
            top_line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            nl = Gtk.Label(label=rule.name or "Untitled", xalign=0)
            nl.add_css_class("heading")
            top_line.append(nl)
            st = m.status_for(rule)
            live = m.net.live_routes.get(rule.id)
            top_line.append(rule_state_pill(st, rule.id in pending_ids, live))
            top_line.append(make_pill("Persistent" if rule.persistent else "Until reboot", "gray"))
            info_col.append(top_line)

            dest_lbl = Gtk.Label(label=", ".join(rule.destinations), xalign=0)
            dest_lbl.add_css_class("monospace")
            dest_lbl.set_ellipsize(3)
            info_col.append(dest_lbl)

            via_lbl = Gtk.Label(label=f"Via {m.describe_via(rule.via)}", xalign=0)
            via_lbl.add_css_class("caption")
            via_lbl.add_css_class("dim-label")
            info_col.append(via_lbl)

            if st:
                resolved = st.get("resolved") or {}
                msgs = st.get("messages") or []
                routes = st.get("routes") or []
                summary = ""
                if resolved:
                    summary = " · ".join(f"{k}: {', '.join(v)}" for k, v in sorted(resolved.items()))
                elif msgs:
                    summary = msgs[0]
                elif routes:
                    summary = ", ".join(routes[:3]) + (" …" if len(routes) > 3 else "")
                if summary:
                    sl = Gtk.Label(label=summary, xalign=0)
                    sl.add_css_class("caption")
                    sl.add_css_class("monospace")
                    sl.add_css_class("dim-label")
                    sl.set_ellipsize(3)
                    info_col.append(sl)
            inner.append(info_col)

            # Actions
            act = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            act.set_valign(Gtk.Align.CENTER)
            if rule.destinations:
                p_btn = Gtk.Button.new_from_icon_name("system-search-symbolic")
                p_btn.add_css_class("flat")
                p_btn.set_tooltip_text(f"Check Path for {rule.destinations[0]}")
                p_btn.connect("clicked", lambda *_, d=rule.destinations[0]: m.open_path_check(d))
                act.append(p_btn)
            dup_btn = Gtk.Button.new_from_icon_name("edit-copy-symbolic")
            dup_btn.add_css_class("flat")
            dup_btn.set_tooltip_text("Duplicate")
            dup_btn.connect("clicked", lambda *_, r=rule: m.duplicate_rule(r))
            act.append(dup_btn)
            ed_btn = Gtk.Button(label="Edit…")
            ed_btn.connect("clicked", lambda *_, r=rule: m.edit_rule(r))
            act.append(ed_btn)
            del_btn = Gtk.Button.new_from_icon_name("user-trash-symbolic")
            del_btn.add_css_class("flat")
            del_btn.connect("clicked", lambda *_, r=rule: self._confirm_delete(r))
            act.append(del_btn)
            inner.append(act)

            self.list_box.append(card)

    def _confirm_delete(self, rule: RouteRule) -> None:
        dlg = Adw.MessageDialog(
            transient_for=self.get_window(),
            heading=f"Delete “{rule.name}”?",
            body="Its routes are removed when you apply changes.",
        )
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("delete", "Delete")
        dlg.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", lambda _d, resp: self.model.delete_rules({rule.id}) if resp == "delete" else None)
        dlg.present()

