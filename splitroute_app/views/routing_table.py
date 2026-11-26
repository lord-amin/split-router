from __future__ import annotations

from typing import TYPE_CHECKING

from gi.repository import Adw, Gtk

from ..models import Destination, RouteVia
from ..scanner import ROUTE_FLAG_LEGEND, RouteEntry
from .widgets import clear_box, copy_to_clipboard, make_page_header, make_pill

if TYPE_CHECKING:
    from ..app_model import AppModel


class AddRouteDialog(Adw.Window):
    def __init__(self, parent: Gtk.Window | None, model: AppModel) -> None:
        super().__init__(transient_for=parent, modal=True, title="Add One-off Route")
        self.model = model
        self.set_default_size(480, 380)

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

        note = Gtk.Label(
            label="Adds a route right now without saving it. It is not restored after reboot. To keep it, create a rule instead.",
            xalign=0,
        )
        note.add_css_class("dim-label")
        note.set_wrap(True)
        body.append(note)

        grp = Adw.PreferencesGroup()
        self.dest_row = Adw.EntryRow(title="Destination (1.2.3.4 or 10.0.0.0/8)")
        self.dest_row.connect("changed", lambda *_: self._sync())
        grp.add(self.dest_row)

        self.mode_combo = Gtk.ComboBoxText()
        self.mode_combo.append("router", "Interface's router")
        self.mode_combo.append("direct", "Directly on interface")
        self.mode_combo.append("gw", "Gateway")
        self.mode_combo.set_active_id("router")
        self.mode_combo.connect("changed", lambda *_: self._sync())
        m_row = Adw.ActionRow(title="Via")
        m_row.add_suffix(self.mode_combo)
        grp.add(m_row)

        self.iface_combo = Gtk.ComboBoxText()
        for i in model.net.interfaces:
            if i.is_active:
                self.iface_combo.append(i.name, f"{model.display_name(i.name)} ({i.name})")
        default_if = (model.local_interface.name if model.local_interface else None) or model.net.primary_interface
        if default_if:
            self.iface_combo.set_active_id(default_if)
        self.if_row = Adw.ActionRow(title="Interface")
        self.if_row.add_suffix(self.iface_combo)
        grp.add(self.if_row)

        self.gw_row = Adw.EntryRow(title="Gateway IP")
        grp.add(self.gw_row)
        body.append(grp)

        outer.append(Gtk.Separator())
        foot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        foot.set_margin_top(12)
        foot.set_margin_bottom(12)
        foot.set_margin_start(16)
        foot.set_margin_end(16)
        rule_btn = Gtk.Button(label="Create Rule Instead")
        rule_btn.connect("clicked", self._to_rule)
        foot.append(rule_btn)
        sp = Gtk.Box()
        sp.set_hexpand(True)
        foot.append(sp)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        self.add_btn = Gtk.Button(label="Add Route")
        self.add_btn.add_css_class("suggested-action")
        self.add_btn.connect("clicked", self._add)
        foot.append(cancel)
        foot.append(self.add_btn)
        outer.append(foot)
        self._sync()

    def _sync(self) -> None:
        is_gw = self.mode_combo.get_active_id() == "gw"
        self.if_row.set_visible(not is_gw)
        self.gw_row.set_visible(is_gw)
        d = Destination.parse(self.dest_row.get_text())
        self.add_btn.set_sensitive(d is not None and d.kind != "domain")

    def _to_rule(self, *_args) -> None:
        dest = self.dest_row.get_text().strip()
        self.close()
        self.model.open_main_window("rules")
        self.model.new_rule(dest or None)

    def _add(self, *_args) -> None:
        mode = self.mode_combo.get_active_id()
        iface = self.iface_combo.get_active_id() or ""
        if mode == "router":
            via = RouteVia(kind="interfaceRouter", interface=iface)
        elif mode == "direct":
            via = RouteVia(kind="interfaceDirect", interface=iface)
        else:
            via = RouteVia(kind="gateway", gateway=self.gw_row.get_text().strip())
        dest = self.dest_row.get_text().strip()
        self.close()
        self.model.add_one_off_route(dest, via)


class ImportRoutesDialog(Adw.Window):
    def __init__(self, parent: Gtk.Window | None, model: AppModel) -> None:
        super().__init__(transient_for=parent, modal=True, title="Import Static Routes")
        self.model = model
        self.candidates = model.import_candidates
        self.checks: dict[str, Gtk.CheckButton] = {}
        self.set_default_size(740, 500)

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

        desc = Gtk.Label(
            label="These routes were added outside SplitRoute (by hand, a script or another tool). Imported routes become rules, grouped by next hop.",
            xalign=0,
        )
        desc.add_css_class("dim-label")
        desc.set_wrap(True)
        body.append(desc)

        if not self.candidates:
            body.append(Adw.StatusPage(
                icon_name="emblem-default-symbolic",
                title="No static routes to import",
                description="Every static route is already managed by a rule or belongs to the system or a VPN client.",
            ))
        else:
            scroll = Gtk.ScrolledWindow()
            scroll.set_vexpand(True)
            lst = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            for c in self.candidates:
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
                chk = Gtk.CheckButton()
                chk.set_active(True)
                chk.connect("toggled", lambda *_: self._sync_count())
                self.checks[c.id] = chk
                row.append(chk)
                dl = Gtk.Label(label=c.destination, xalign=0)
                dl.add_css_class("monospace")
                dl.set_size_request(160, -1)
                row.append(dl)
                gl = Gtk.Label(label=c.entry.gateway or "—", xalign=0)
                gl.add_css_class("monospace")
                gl.set_size_request(140, -1)
                row.append(gl)
                il = Gtk.Label(label=f"{model.display_name(c.entry.netif)} ({c.entry.netif})", xalign=0)
                il.set_size_request(150, -1)
                row.append(il)
                vl = Gtk.Label(label=model.describe_via(c.via), xalign=0)
                vl.add_css_class("dim-label")
                vl.set_hexpand(True)
                row.append(vl)
                lst.append(row)
            scroll.set_child(lst)
            body.append(scroll)

            bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            all_b = Gtk.Button(label="Select All")
            all_b.connect("clicked", lambda *_: [c.set_active(True) for c in self.checks.values()])
            none_b = Gtk.Button(label="Select None")
            none_b.connect("clicked", lambda *_: [c.set_active(False) for c in self.checks.values()])
            bar.append(all_b)
            bar.append(none_b)
            sp = Gtk.Box()
            sp.set_hexpand(True)
            bar.append(sp)
            self.persist_chk = Gtk.CheckButton(label="Persistent (survive reboot)")
            self.persist_chk.set_active(True)
            bar.append(self.persist_chk)
            body.append(bar)

        outer.append(Gtk.Separator())
        foot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        foot.set_margin_top(12)
        foot.set_margin_bottom(12)
        foot.set_margin_start(16)
        foot.set_margin_end(16)
        hint = Gtk.Label(label="Review the new rules, then press Apply.")
        hint.add_css_class("caption")
        hint.add_css_class("dim-label")
        foot.append(hint)
        sp2 = Gtk.Box()
        sp2.set_hexpand(True)
        foot.append(sp2)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        self.imp_btn = Gtk.Button(label="Import")
        self.imp_btn.add_css_class("suggested-action")
        self.imp_btn.connect("clicked", self._do_import)
        foot.append(cancel)
        foot.append(self.imp_btn)
        outer.append(foot)
        self._sync_count()

    def _sync_count(self) -> None:
        n = sum(1 for c in self.checks.values() if c.get_active())
        self.imp_btn.set_label(f"Import {n} Route" + ("" if n == 1 else "s"))
        self.imp_btn.set_sensitive(n > 0)

    def _do_import(self, *_args) -> None:
        chosen = [c for c in self.candidates if self.checks.get(c.id) and self.checks[c.id].get_active()]
        self.model.import_routes(chosen, persistent=self.persist_chk.get_active())
        self.close()


class RoutingTableView(Gtk.Box):
    def __init__(self, model: AppModel, get_window, open_import_cb) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.model = model
        self.get_window = get_window
        self.open_import_cb = open_import_cb
        self.family = "inet"
        self.iface_filter = "all"
        self.search_text = ""

        self.set_margin_top(24)
        self.set_margin_bottom(24)
        self.set_margin_start(24)
        self.set_margin_end(24)

        trailing = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        legend_btn = Gtk.MenuButton(label="Flags")
        pop = Gtk.Popover()
        pop_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        pop_box.set_margin_top(10)
        pop_box.set_margin_bottom(10)
        pop_box.set_margin_start(12)
        pop_box.set_margin_end(12)
        lh = Gtk.Label(label="Route flags", xalign=0)
        lh.add_css_class("heading")
        pop_box.append(lh)
        for code, desc in ROUTE_FLAG_LEGEND:
            r = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            cl = Gtk.Label(label=code, xalign=0)
            cl.add_css_class("monospace")
            cl.set_size_request(24, -1)
            r.append(cl)
            r.append(Gtk.Label(label=desc, xalign=0))
            pop_box.append(r)
        pop.set_child(pop_box)
        legend_btn.set_popover(pop)

        imp_btn = Gtk.Button(label="Import Static Routes")
        imp_btn.connect("clicked", lambda *_: self.open_import_cb())
        add_btn = Gtk.Button(label="Add Route")
        add_btn.connect("clicked", lambda *_: AddRouteDialog(self.get_window(), self.model).present())
        trailing.append(legend_btn)
        trailing.append(imp_btn)
        trailing.append(add_btn)
        self.append(make_page_header("Routing Table", "Every route the kernel knows about, live", trailing))

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        fam_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        fam_box.add_css_class("linked")
        self.v4_btn = Gtk.ToggleButton(label="IPv4")
        self.v6_btn = Gtk.ToggleButton(label="IPv6")
        self.v6_btn.set_group(self.v4_btn)
        self.v4_btn.set_active(True)
        self.v4_btn.connect("toggled", lambda b: self._set_family("inet") if b.get_active() else None)
        self.v6_btn.connect("toggled", lambda b: self._set_family("inet6") if b.get_active() else None)
        fam_box.append(self.v4_btn)
        fam_box.append(self.v6_btn)
        bar.append(fam_box)

        self.iface_combo = Gtk.ComboBoxText()
        self.iface_combo.append("all", "All interfaces")
        self.iface_combo.set_active_id("all")
        self.iface_combo.connect("changed", self._on_iface_changed)
        bar.append(self.iface_combo)

        self.hide_ll_chk = Gtk.CheckButton(label="Hide link-local kernel entries")
        self.hide_ll_chk.set_active(model.workspace.settings.hide_link_layer_routes)
        self.hide_ll_chk.connect("toggled", self._on_hide_ll)
        bar.append(self.hide_ll_chk)

        self.search_entry = Gtk.SearchEntry(placeholder_text="Filter routes")
        self.search_entry.set_hexpand(True)
        self.search_entry.connect("search-changed", lambda e: self._on_search(e.get_text()))
        bar.append(self.search_entry)

        self.count_lbl = Gtk.Label(label="0 routes")
        self.count_lbl.add_css_class("dim-label")
        bar.append(self.count_lbl)
        self.append(bar)

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        self.list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        scroll.set_child(self.list_box)
        self.append(scroll)

    def _set_family(self, fam: str) -> None:
        self.family = fam
        self.refresh()

    def _on_iface_changed(self, combo: Gtk.ComboBoxText) -> None:
        self.iface_filter = combo.get_active_id() or "all"
        self.refresh()

    def _on_hide_ll(self, chk: Gtk.CheckButton) -> None:
        self.model.workspace.settings.hide_link_layer_routes = chk.get_active()
        self.model.save_workspace()

    def _on_search(self, txt: str) -> None:
        self.search_text = txt.strip().lower()
        self.refresh()

    def refresh(self) -> None:
        m = self.model
        all_routes = m.net.routes_v4 if self.family == "inet" else m.net.routes_v6
        ifaces = sorted({r.netif for r in all_routes if r.netif})
        prev = self.iface_filter
        self.iface_combo.handler_block_by_func(self._on_iface_changed)
        self.iface_combo.remove_all()
        self.iface_combo.append("all", "All interfaces")
        for i in ifaces:
            self.iface_combo.append(i, f"{m.display_name(i)} ({i})")
        self.iface_combo.set_active_id(prev if prev in ifaces else "all")
        self.iface_filter = self.iface_combo.get_active_id() or "all"
        self.iface_combo.handler_unblock_by_func(self._on_iface_changed)

        hide_ll = m.workspace.settings.hide_link_layer_routes
        q = self.search_text
        entries = [
            e for e in all_routes
            if (self.iface_filter == "all" or e.netif == self.iface_filter)
            and (not hide_ll or not e.is_link_layer)
            and (
                not q
                or q in e.destination.lower()
                or q in e.gateway.lower()
                or q in e.netif.lower()
                or q in (m.owner_of(e) or "").lower()
            )
        ]
        self.count_lbl.set_label(f"{len(entries)} routes")

        clear_box(self.list_box)
        for e in entries[:300]:
            card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            card.add_css_class("card")
            inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            inner.set_margin_top(8)
            inner.set_margin_bottom(8)
            inner.set_margin_start(12)
            inner.set_margin_end(12)
            card.append(inner)

            dl = Gtk.Label(label=e.destination, xalign=0)
            dl.add_css_class("monospace")
            dl.set_selectable(True)
            dl.set_size_request(180, -1)
            inner.append(dl)

            gl = Gtk.Label(label=e.gateway or "—", xalign=0)
            gl.add_css_class("monospace")
            gl.set_size_request(140, -1)
            inner.append(gl)

            fl = Gtk.Label(label=e.flags_display, xalign=0)
            fl.add_css_class("monospace")
            fl.add_css_class("dim-label")
            fl.set_size_request(60, -1)
            inner.append(fl)

            il = Gtk.Label(label=f"{e.netif} ({m.display_name(e.netif)})", xalign=0)
            il.set_size_request(170, -1)
            il.set_ellipsize(3)
            inner.append(il)

            owner = m.owner_of(e)
            if owner:
                inner.append(make_pill(owner, "green" if owner.startswith("Rule") else "purple"))
            else:
                inner.append(make_pill("Static" if e.is_static else (e.protocol or "System"), "gray"))

            sp = Gtk.Box()
            sp.set_hexpand(True)
            inner.append(sp)

            c = e.cidr()
            if c and c[1] > 0:
                p_btn = Gtk.Button(label="Path")
                p_btn.add_css_class("flat")
                p_btn.connect("clicked", lambda *_, ip=c[0]: m.open_path_check(ip))
                inner.append(p_btn)
                dest_s = c[0] if c[1] == 32 else f"{c[0]}/{c[1]}"
                r_btn = Gtk.Button(label="Rule")
                r_btn.add_css_class("flat")
                r_btn.connect("clicked", lambda *_, d=dest_s: (m.open_main_window("rules"), m.new_rule(d)))
                inner.append(r_btn)

            cp_btn = Gtk.Button.new_from_icon_name("edit-copy-symbolic")
            cp_btn.add_css_class("flat")
            cp_btn.set_tooltip_text("Copy Destination")
            cp_btn.connect("clicked", lambda *_, d=e.destination: copy_to_clipboard(d))
            inner.append(cp_btn)

            del_btn = Gtk.Button.new_from_icon_name("user-trash-symbolic")
            del_btn.add_css_class("flat")
            del_btn.set_tooltip_text("Delete Route…")
            del_btn.connect("clicked", lambda *_, entry=e: self._confirm_delete(entry))
            inner.append(del_btn)
            self.list_box.append(card)

    def _confirm_delete(self, e: RouteEntry) -> None:
        dlg = Adw.MessageDialog(
            transient_for=self.get_window(),
            heading=f"Delete route {e.destination}?",
            body=(
                f"This is a default route. Removing it can cut off internet access through {e.netif}."
                if e.is_default
                else f"{e.destination} via {e.gateway or 'link'} on {e.netif}. The owning app or rule may add it back."
            ),
        )
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("del", "Delete Route")
        dlg.set_response_appearance("del", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", lambda _d, resp: self.model.delete_raw_route(e) if resp == "del" else None)
        dlg.present()

