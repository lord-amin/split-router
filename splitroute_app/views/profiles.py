from __future__ import annotations

from typing import TYPE_CHECKING

from gi.repository import Adw, Gtk

from ..models import NetworkMatch, RoutingProfile
from .widgets import PendingBanner, clear_box, make_card, make_kv_row, make_page_header, make_pill

if TYPE_CHECKING:
    from ..app_model import AppModel


class ProfileEditorDialog(Adw.Window):
    def __init__(self, parent: Gtk.Window | None, model: AppModel, profile: RoutingProfile) -> None:
        super().__init__(transient_for=parent, modal=True)
        self.model = model
        self.profile = RoutingProfile.from_dict(profile.to_dict())
        self.is_new = not any(p.id == profile.id for p in model.workspace.automation.profiles)
        self.set_title("New Profile" if self.is_new else "Edit Profile")
        self.set_default_size(560, 620)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(outer)
        outer.append(Adw.HeaderBar())

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        outer.append(scroll)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        body.set_margin_top(18)
        body.set_margin_bottom(18)
        body.set_margin_start(20)
        body.set_margin_end(20)
        scroll.set_child(body)

        grp = Adw.PreferencesGroup(title="Profile")
        self.name_row = Adw.EntryRow(title="Name (e.g. Home)")
        self.name_row.set_text(self.profile.name)
        self.name_row.connect("changed", lambda *_: self._sync_save_state())
        grp.add(self.name_row)

        self.ks_combo = Gtk.ComboBoxText()
        self.ks_combo.append("none", "Leave as configured")
        self.ks_combo.append("on", "Force on")
        self.ks_combo.append("off", "Force off")
        self.ks_combo.set_active_id("none" if self.profile.kill_switch is None else ("on" if self.profile.kill_switch else "off"))
        ks_row = Adw.ActionRow(title="Kill switch")
        ks_row.add_suffix(self.ks_combo)
        grp.add(ks_row)
        body.append(grp)

        rules_grp = Adw.PreferencesGroup(title="Active rules")
        self.rule_checks: dict[str, Gtk.Switch] = {}
        if not model.workspace.rules:
            rules_grp.add(Adw.ActionRow(title="No rules yet"))
        for r in model.workspace.rules:
            sw = Adw.SwitchRow(title=r.name or "Untitled", subtitle=", ".join(r.destinations))
            sw.set_active(r.id in self.profile.rule_ids)
            self.rule_checks[r.id] = sw
            rules_grp.add(sw)
        body.append(rules_grp)

        self.net_grp = Adw.PreferencesGroup(
            title="Use automatically on these networks",
            description="Networks are recognised by their router's MAC/IP address.",
        )
        self.net_list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.net_grp.add(self.net_list_box)
        body.append(self.net_grp)
        self._render_networks()

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
        self.save_btn = Gtk.Button(label="Create" if self.is_new else "Save")
        self.save_btn.add_css_class("suggested-action")
        self.save_btn.connect("clicked", self._save)
        foot.append(cancel)
        foot.append(self.save_btn)
        outer.append(foot)
        self._sync_save_state()

    def _sync_save_state(self) -> None:
        self.save_btn.set_sensitive(bool(self.name_row.get_text().strip()))

    def _render_networks(self) -> None:
        clear_box(self.net_list_box)
        for nm in self.profile.networks:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            col.set_hexpand(True)
            col.append(Gtk.Label(label=nm.label, xalign=0))
            sub = Gtk.Label(label=f"router {nm.router_ip}" + (f" · {nm.router_mac}" if nm.router_mac else ""), xalign=0)
            sub.add_css_class("caption")
            sub.add_css_class("monospace")
            sub.add_css_class("dim-label")
            col.append(sub)
            rem = Gtk.Button.new_from_icon_name("list-remove-symbolic")
            rem.add_css_class("flat")
            rem.connect("clicked", lambda *_, nid=nm.id: self._remove_net(nid))
            row.append(col)
            row.append(rem)
            self.net_list_box.append(row)

        fp = self.model.net.network
        if fp:
            already = any(nm.matches(fp) for nm in self.profile.networks)
            add_btn = Gtk.Button(label=f"Add Current Network ({fp.get('routerIP', '')})")
            add_btn.set_halign(Gtk.Align.START)
            add_btn.set_sensitive(not already)
            add_btn.connect("clicked", lambda *_: self._add_current_net(fp))
            self.net_list_box.append(add_btn)
        else:
            lbl = Gtk.Label(label="Not connected to a network with a router.", xalign=0)
            lbl.add_css_class("dim-label")
            self.net_list_box.append(lbl)

    def _remove_net(self, nid: str) -> None:
        self.profile.networks = [n for n in self.profile.networks if n.id != nid]
        self._render_networks()

    def _add_current_net(self, fp: dict) -> None:
        lbl = self.name_row.get_text().strip() or f"Network {fp.get('routerIP', '')}"
        self.profile.networks.append(NetworkMatch(
            label=lbl,
            router_ip=fp.get("routerIP", ""),
            router_mac=fp.get("routerMAC", ""),
        ))
        self._render_networks()

    def _save(self, *_args) -> None:
        self.profile.name = self.name_row.get_text().strip()
        ks_val = self.ks_combo.get_active_id()
        self.profile.kill_switch = None if ks_val == "none" else (ks_val == "on")
        self.profile.rule_ids = [rid for rid, sw in self.rule_checks.items() if sw.get_active()]
        profs = self.model.workspace.automation.profiles
        for i, p in enumerate(profs):
            if p.id == self.profile.id:
                profs[i] = self.profile
                break
        else:
            profs.append(self.profile)
        self.model.save_workspace()
        self.close()


class ProfilesView(Gtk.ScrolledWindow):
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

        new_btn = Gtk.Button(label="New Profile")
        new_btn.add_css_class("suggested-action")
        new_btn.connect("clicked", lambda *_: self._open_editor(RoutingProfile(rule_ids=[r.id for r in self.model.workspace.rules])))
        self.root.append(make_page_header("Profiles", "Switch which rules are active, by hand or by the network you join", new_btn))

        self.pending_banner = PendingBanner(model, get_window)
        self.root.append(self.pending_banner)

        self.active_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.root.append(self.active_box)
        self.list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.root.append(self.list_box)

    def _open_editor(self, profile: RoutingProfile) -> None:
        ProfileEditorDialog(self.get_window(), self.model, profile).present()

    def refresh(self) -> None:
        m = self.model
        self.pending_banner.refresh()
        auto = m.workspace.automation

        clear_box(self.active_box)
        card, inner = make_card("Active profile", "emblem-default-symbolic")
        info = (m.net.helper_status or {}).get("profile") or {}
        t = Gtk.Label(label=info.get("name") or "None — all enabled rules", xalign=0)
        t.add_css_class("title-2")
        sub = Gtk.Label(label=info.get("reason") or "Applied by the helper on the next pass", xalign=0)
        sub.add_css_class("dim-label")
        inner.append(t)
        inner.append(sub)
        inner.append(Gtk.Separator())

        man_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        ml = Gtk.Label(label="Manual choice", xalign=0)
        ml.set_hexpand(True)
        combo = Gtk.ComboBoxText()
        combo.append("none", "None (all enabled rules)")
        for p in auto.profiles:
            combo.append(p.id, p.name)
        combo.set_active_id(auto.manual_profile_id or "none")
        combo.connect("changed", self._on_manual_changed)
        man_row.append(ml)
        man_row.append(combo)
        inner.append(man_row)

        auto_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        al = Gtk.Label(label="Switch automatically when I join a network that a profile lists", xalign=0)
        al.set_hexpand(True)
        sw = Gtk.Switch()
        sw.set_active(auto.auto_switch_by_network)
        sw.connect("notify::active", lambda s, _p: self._set_auto_switch(s.get_active()))
        auto_row.append(al)
        auto_row.append(sw)
        inner.append(auto_row)

        if m.net.network:
            fp = m.net.network
            mac = f" ({fp['routerMAC']})" if fp.get("routerMAC") else ""
            fp_lbl = Gtk.Label(label=f"Current network: router {fp.get('routerIP', '')}{mac} on {fp.get('interface', '')}", xalign=0)
            fp_lbl.add_css_class("caption")
            fp_lbl.add_css_class("dim-label")
            inner.append(fp_lbl)
        hint = Gtk.Label(label="Order: a trigger's choice wins, then a matching network, then the manual choice. Press Apply after changes.", xalign=0)
        hint.add_css_class("caption")
        hint.add_css_class("dim-label")
        inner.append(hint)
        self.active_box.append(card)

        clear_box(self.list_box)
        if not auto.profiles:
            st = Adw.StatusPage(
                icon_name="view-paged-symbolic",
                title="No profiles",
                description="Without profiles every enabled rule applies everywhere. Create profiles such as Home, Office or Travel.",
            )
            b = Gtk.Button(label="New Profile")
            b.set_halign(Gtk.Align.CENTER)
            b.connect("clicked", lambda *_: self._open_editor(RoutingProfile(rule_ids=[r.id for r in m.workspace.rules])))
            st.set_child(b)
            self.list_box.append(st)
            return

        active_id = info.get("id")
        for p in auto.profiles:
            is_active = active_id == p.id
            trail = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            if is_active:
                trail.append(make_pill("Active", "green"))
            ed = Gtk.Button(label="Edit…")
            ed.connect("clicked", lambda *_, prof=p: self._open_editor(prof))
            dl = Gtk.Button.new_from_icon_name("user-trash-symbolic")
            dl.add_css_class("flat")
            dl.connect("clicked", lambda *_, pid=p.id: self._delete_profile(pid))
            trail.append(ed)
            trail.append(dl)

            p_card, p_inner = make_card(p.name, "emblem-default-symbolic" if is_active else "view-paged-symbolic", trail)
            rules = [r.name for r in m.workspace.rules if r.id in p.rule_ids]
            p_inner.append(make_kv_row("Rules", ", ".join(rules) if rules else "none", mono=False))
            ks_s = "unchanged" if p.kill_switch is None else ("forced on" if p.kill_switch else "forced off")
            p_inner.append(make_kv_row("Kill switch", ks_s, mono=False))
            nets = ", ".join(n.label for n in p.networks) if p.networks else "manual only"
            p_inner.append(make_kv_row("Networks", nets, mono=False))
            self.list_box.append(p_card)

    def _on_manual_changed(self, combo: Gtk.ComboBoxText) -> None:
        val = combo.get_active_id()
        self.model.workspace.automation.manual_profile_id = None if (not val or val == "none") else val
        self.model.save_workspace()

    def _set_auto_switch(self, val: bool) -> None:
        self.model.workspace.automation.auto_switch_by_network = val
        self.model.save_workspace()

    def _delete_profile(self, pid: str) -> None:
        auto = self.model.workspace.automation
        auto.profiles = [p for p in auto.profiles if p.id != pid]
        if auto.manual_profile_id == pid:
            auto.manual_profile_id = None
        self.model.save_workspace()

