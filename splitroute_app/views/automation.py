from __future__ import annotations

from typing import TYPE_CHECKING

from gi.repository import Adw, Gtk

from ..models import TriggerAction, TriggerEvent, TriggerRule, parse_dt
from .widgets import PendingBanner, clear_box, make_banner, make_card, make_page_header

if TYPE_CHECKING:
    from ..app_model import AppModel


class TriggerEditorDialog(Adw.Window):
    def __init__(self, parent: Gtk.Window | None, model: AppModel, trigger: TriggerRule) -> None:
        super().__init__(transient_for=parent, modal=True, title="Trigger")
        self.model = model
        self.trigger = TriggerRule.from_dict(trigger.to_dict())
        self.set_default_size(540, 580)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(outer)
        outer.append(Adw.HeaderBar())

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        outer.append(scroll)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        body.set_margin_top(16)
        body.set_margin_bottom(16)
        body.set_margin_start(20)
        body.set_margin_end(20)
        scroll.set_child(body)

        t_grp = Adw.PreferencesGroup(title="Trigger")
        self.name_row = Adw.EntryRow(title="Name (e.g. VPN dropped)")
        self.name_row.set_text(self.trigger.name)
        t_grp.add(self.name_row)

        self.ev_combo = Gtk.ComboBoxText()
        for k, lbl in [
            ("tunnelDown", "Full-tunnel VPN disconnects"),
            ("tunnelUp", "Full-tunnel VPN connects"),
            ("interfaceDown", "An interface goes down"),
            ("interfaceUp", "An interface comes up"),
            ("egressChanged", "Internet traffic switches interface"),
            ("networkChanged", "Joined a different network"),
        ]:
            self.ev_combo.append(k, lbl)
        self.ev_combo.set_active_id(self.trigger.event.kind)
        self.ev_combo.connect("changed", lambda *_: self._sync_iface())
        ev_row = Adw.ActionRow(title="When")
        ev_row.add_suffix(self.ev_combo)
        t_grp.add(ev_row)

        self.if_combo = Gtk.ComboBoxText()
        ifaces = sorted({i.name for i in model.net.interfaces if not i.is_system_noise} | ({self.trigger.event.interface} if self.trigger.event.interface else set()))
        for name in ifaces:
            self.if_combo.append(name, f"{model.display_name(name)} ({name})")
        if self.trigger.event.interface:
            self.if_combo.set_active_id(self.trigger.event.interface)
        elif ifaces:
            self.if_combo.set_active_id(ifaces[0])
        self.if_row = Adw.ActionRow(title="Interface")
        self.if_row.add_suffix(self.if_combo)
        t_grp.add(self.if_row)
        body.append(t_grp)

        act_grp = Adw.PreferencesGroup(
            title="Then",
            description="Triggers run inside the background service within seconds of the change.",
        )
        self.notify_sw = Adw.SwitchRow(title="Send a notification")
        self.notify_sw.set_active(self.trigger.notify)
        act_grp.add(self.notify_sw)

        self.learn_sw = Adw.SwitchRow(title="Re-learn VPN servers (for the kill switch)")
        self.learn_sw.set_active(any(a.kind == "learnVPNServers" for a in self.trigger.actions))
        act_grp.add(self.learn_sw)

        self.ks_combo = Gtk.ComboBoxText()
        self.ks_combo.append("none", "Unchanged")
        self.ks_combo.append("killSwitchOn", "Force kill switch on")
        self.ks_combo.append("killSwitchOff", "Force kill switch off")
        self.ks_combo.append("killSwitchAuto", "Return kill switch to its setting")
        cur_ks = next((a.kind for a in self.trigger.actions if a.kind in ("killSwitchOn", "killSwitchOff", "killSwitchAuto")), "none")
        self.ks_combo.set_active_id(cur_ks)
        ks_row = Adw.ActionRow(title="Kill switch")
        ks_row.add_suffix(self.ks_combo)
        act_grp.add(ks_row)

        self.prof_combo = Gtk.ComboBoxText()
        self.prof_combo.append("none", "Unchanged")
        self.prof_combo.append("clearProfile", "Clear the profile (all enabled rules)")
        for p in model.workspace.automation.profiles:
            self.prof_combo.append(f"p:{p.id}", f"Switch to profile “{p.name}”")
        cur_prof = "none"
        for a in self.trigger.actions:
            if a.kind == "clearProfile":
                cur_prof = "clearProfile"
            elif a.kind == "activateProfile":
                cur_prof = f"p:{a.profile_id}"
        self.prof_combo.set_active_id(cur_prof)
        if not self.prof_combo.get_active_id():
            self.prof_combo.set_active_id("none")
        p_row = Adw.ActionRow(title="Profile")
        p_row.add_suffix(self.prof_combo)
        act_grp.add(p_row)
        body.append(act_grp)

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
        save = Gtk.Button(label="Save")
        save.add_css_class("suggested-action")
        save.connect("clicked", self._save)
        foot.append(cancel)
        foot.append(save)
        outer.append(foot)
        self._sync_iface()

    def _sync_iface(self) -> None:
        k = self.ev_combo.get_active_id()
        self.if_row.set_visible(k in ("interfaceUp", "interfaceDown"))

    def _save(self, *_args) -> None:
        ev_kind = self.ev_combo.get_active_id() or "tunnelDown"
        ev_if = self.if_combo.get_active_id() or "" if ev_kind in ("interfaceUp", "interfaceDown") else ""
        actions: list[TriggerAction] = []
        if self.learn_sw.get_active():
            actions.append(TriggerAction(kind="learnVPNServers"))
        ks_k = self.ks_combo.get_active_id()
        if ks_k and ks_k != "none":
            actions.append(TriggerAction(kind=ks_k))
        pk = self.prof_combo.get_active_id()
        if pk == "clearProfile":
            actions.append(TriggerAction(kind="clearProfile"))
        elif pk and pk.startswith("p:"):
            actions.append(TriggerAction(kind="activateProfile", profile_id=pk[2:]))

        t = TriggerRule(
            id=self.trigger.id,
            name=self.name_row.get_text().strip(),
            enabled=self.trigger.enabled,
            event=TriggerEvent(kind=ev_kind, interface=ev_if),
            actions=actions,
            notify=self.notify_sw.get_active(),
        )
        lst = self.model.workspace.automation.triggers
        for i, existing in enumerate(lst):
            if existing.id == t.id:
                lst[i] = t
                break
        else:
            lst.append(t)
        self.model.save_workspace()
        self.close()


class AutomationView(Gtk.ScrolledWindow):
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

        new_btn = Gtk.Button(label="New Trigger")
        new_btn.add_css_class("suggested-action")
        new_btn.connect("clicked", lambda *_: TriggerEditorDialog(self.get_window(), self.model, TriggerRule()).present())
        self.root.append(make_page_header(
            "Automation",
            "React when a VPN connects or drops, or when you change networks",
            new_btn,
        ))
        self.pending_banner = PendingBanner(model, get_window)
        self.root.append(self.pending_banner)

        self.svc_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.triggers_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.notif_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.events_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for b in (self.svc_box, self.triggers_box, self.notif_box, self.events_box):
            self.root.append(b)

    def refresh(self) -> None:
        m = self.model
        self.pending_banner.refresh()

        clear_box(self.svc_box)
        if not m.background_service_running:
            on_b = Gtk.Button(label="Turn On")
            on_b.connect("clicked", lambda *_: m.set_service(True))
            self.svc_box.append(make_banner(
                "Triggers need the background service",
                "The service watches the network and runs triggers, even when this app is closed.",
                tone="yellow",
                actions=[on_b],
            ))

        # Triggers card
        clear_box(self.triggers_box)
        t_card, t_in = make_card("Triggers", "emblem-system-symbolic")
        triggers = m.workspace.automation.triggers
        if not triggers:
            lbl = Gtk.Label(
                label="No triggers yet. Examples: when the full-tunnel VPN disconnects, notify me; when it reconnects, re-learn its servers.",
                xalign=0,
            )
            lbl.add_css_class("dim-label")
            lbl.set_wrap(True)
            t_in.append(lbl)
            sug_b = Gtk.Button(label="Add Suggested Triggers")
            sug_b.set_halign(Gtk.Align.START)
            sug_b.connect("clicked", self._add_suggested)
            t_in.append(sug_b)
        for t in triggers:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            sw = Gtk.Switch()
            sw.set_active(t.enabled)
            sw.set_valign(Gtk.Align.CENTER)
            sw.connect("notify::active", self._toggle_trigger, t.id)
            row.append(sw)

            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            col.set_hexpand(True)
            col.append(Gtk.Label(label=t.name or t.event.label, xalign=0))
            parts = [m.describe_action(a.kind, a.profile_id) for a in t.actions]
            if t.notify:
                parts.append("notify")
            act_s = ", ".join(parts) if parts else "record only"
            sub = Gtk.Label(label=f"When: {t.event.label}  ·  Then: {act_s}", xalign=0)
            sub.add_css_class("caption")
            sub.add_css_class("dim-label")
            col.append(sub)
            row.append(col)

            ed = Gtk.Button(label="Edit…")
            ed.connect("clicked", lambda *_, tr=t: TriggerEditorDialog(self.get_window(), m, tr).present())
            dl = Gtk.Button.new_from_icon_name("user-trash-symbolic")
            dl.add_css_class("flat")
            dl.connect("clicked", lambda *_, tid=t.id: self._delete_trigger(tid))
            row.append(ed)
            row.append(dl)
            t_in.append(row)
            t_in.append(Gtk.Separator())
        self.triggers_box.append(t_card)

        # Notifications card
        clear_box(self.notif_box)
        n_card, n_in = make_card("Notifications", "preferences-desktop-notifications-symbolic")
        np = m.workspace.settings.notifications
        for attr, title in [
            ("public_ip", "Public IP or country changes"),
            ("egress", "VPN connects, disconnects, or internet egress switches"),
            ("kill_switch", "Kill switch turns on or off"),
            ("rule_failures", "A routing rule fails or is only partly applied"),
            ("network_events", "Joined a different network or profile switched"),
            ("updates", "New SplitRoute version is available"),
        ]:
            chk = Gtk.CheckButton(label=title)
            chk.set_active(getattr(np, attr))
            chk.connect("toggled", lambda c, a=attr: (setattr(np, a, c.get_active()), m.save_workspace()))
            n_in.append(chk)
        self.notif_box.append(n_card)

        # Recent events
        clear_box(self.events_box)
        e_card, e_in = make_card("Recent events", "document-open-recent-symbolic")
        events = m.net.events
        if not events:
            lbl = Gtk.Label(label="No events yet. The background service records VPN and network changes here.", xalign=0)
            lbl.add_css_class("dim-label")
            e_in.append(lbl)
        for e in reversed(events[-40:]):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            dt = parse_dt(e.get("date")).astimezone().strftime("%b %d %H:%M:%S")
            dl = Gtk.Label(label=dt, xalign=0)
            dl.add_css_class("caption")
            dl.add_css_class("monospace")
            dl.add_css_class("dim-label")
            dl.set_size_request(140, -1)
            row.append(dl)
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            col.append(Gtk.Label(label=e.get("title", ""), xalign=0))
            if e.get("detail"):
                det = Gtk.Label(label=e["detail"], xalign=0)
                det.add_css_class("caption")
                det.add_css_class("dim-label")
                det.set_wrap(True)
                col.append(det)
            row.append(col)
            e_in.append(row)
        self.events_box.append(e_card)

    def _add_suggested(self, *_args) -> None:
        self.model.workspace.automation.triggers += [
            TriggerRule(name="VPN dropped", event=TriggerEvent(kind="tunnelDown"), actions=[], notify=True),
            TriggerRule(name="VPN reconnected", event=TriggerEvent(kind="tunnelUp"), actions=[TriggerAction(kind="learnVPNServers")], notify=True),
            TriggerRule(name="New network", event=TriggerEvent(kind="networkChanged"), actions=[], notify=True),
        ]
        self.model.save_workspace()

    def _toggle_trigger(self, sw: Gtk.Switch, _p, tid: str) -> None:
        for t in self.model.workspace.automation.triggers:
            if t.id == tid:
                if t.enabled != sw.get_active():
                    t.enabled = sw.get_active()
                    self.model.save_workspace()
                break

    def _delete_trigger(self, tid: str) -> None:
        self.model.workspace.automation.triggers = [
            t for t in self.model.workspace.automation.triggers if t.id != tid
        ]
        self.model.save_workspace()

