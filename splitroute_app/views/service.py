from __future__ import annotations

from typing import TYPE_CHECKING

from gi.repository import Gtk

from ..models import parse_dt
from ..paths import LOG_FILE, PERSISTENT_RULES, RUN_DIR, SYSTEMD_UNIT
from .widgets import PendingBanner, clear_box, make_card, make_kv_row, make_page_header

if TYPE_CHECKING:
    from ..app_model import AppModel


class ServiceView(Gtk.ScrolledWindow):
    def __init__(self, model: AppModel, get_window, open_setup_cb) -> None:
        super().__init__()
        self.model = model
        self.get_window = get_window
        self.open_setup_cb = open_setup_cb
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self.root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.root.set_margin_top(24)
        self.root.set_margin_bottom(24)
        self.root.set_margin_start(24)
        self.root.set_margin_end(24)
        self.set_child(self.root)

        self.root.append(make_page_header("Persistence", "Keep routes in place across reboots, reconnects and IP changes"))

        self.top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        self.top_row.set_homogeneous(True)
        self.root.append(self.top_row)

        self.startup_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.root.append(self.startup_box)

        self.rules_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.root.append(self.rules_box)

        self.pending_banner = PendingBanner(model, get_window)
        self.root.append(self.pending_banner)

        rel_btn = Gtk.Button(label="Reload")
        rel_btn.connect("clicked", lambda *_: self.model.load_helper_log())
        log_card, self.log_inner = make_card("Helper log", "utilities-terminal-symbolic", rel_btn)
        scroll = Gtk.ScrolledWindow()
        scroll.set_min_content_height(200)
        self.log_lbl = Gtk.Label(label="", xalign=0, yalign=0)
        self.log_lbl.add_css_class("caption")
        self.log_lbl.add_css_class("monospace")
        self.log_lbl.set_selectable(True)
        self.log_lbl.set_wrap(True)
        scroll.set_child(self.log_lbl)
        self.log_inner.append(scroll)
        self.root.append(log_card)

    def refresh(self) -> None:
        m = self.model
        s = m.net.service
        self.pending_banner.refresh()

        clear_box(self.top_row)
        svc_card, svc_in = make_card("Background route service", "system-run-symbolic")
        hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        col.set_hexpand(True)
        st_lbl = Gtk.Label(label="On" if (s.daemon_installed and s.loaded) else "Off", xalign=0)
        st_lbl.add_css_class("title-2")
        col.append(st_lbl)
        desc = Gtk.Label(
            label=f"A root systemd service re-applies everything at boot, on netlink network changes, and every {m.workspace.settings.service_interval_seconds} s.",
            xalign=0,
        )
        desc.add_css_class("caption")
        desc.add_css_class("dim-label")
        desc.set_wrap(True)
        col.append(desc)
        hdr.append(col)
        sw = Gtk.Switch()
        sw.set_active(s.daemon_installed)
        sw.set_valign(Gtk.Align.CENTER)
        sw.connect("notify::active", lambda b, _p: m.set_service(b.get_active()) if b.get_active() != s.daemon_installed else None)
        hdr.append(sw)
        svc_in.append(hdr)
        svc_in.append(Gtk.Separator())
        svc_in.append(make_kv_row("Helper", f"Installed ({s.helper_version or '?'})" if s.helper_installed else "Not installed", mono=False))
        svc_in.append(make_kv_row("Probe cap_net_raw", "Enabled" if s.probe_has_cap else ("Installed (no cap)" if s.probe_installed else "Not installed"), mono=False))
        svc_in.append(make_kv_row("systemd unit", SYSTEMD_UNIT if s.daemon_installed else "Not installed"))
        svc_in.append(make_kv_row("State", s.state if s.loaded else "not loaded", mono=False))
        hs = m.net.helper_status
        last_run = f"{parse_dt(hs.get('updatedAt')).astimezone().strftime('%Y-%m-%d %H:%M:%S')} ({hs.get('trigger', '')})" if hs else ""
        svc_in.append(make_kv_row("Last run", last_run, mono=False))

        ctl = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        spin = Gtk.SpinButton.new_with_range(15, 3600, 15)
        spin.set_value(m.workspace.settings.service_interval_seconds)
        spin.connect("value-changed", self._on_interval_changed)
        ctl.append(Gtk.Label(label="Interval (s):"))
        ctl.append(spin)
        sp = Gtk.Box()
        sp.set_hexpand(True)
        ctl.append(sp)
        if m.daemon_outdated:
            ub = Gtk.Button(label="Update Service")
            ub.connect("clicked", lambda *_: m.set_service(True))
            ctl.append(ub)
        rb = Gtk.Button(label="Run Now")
        rb.connect("clicked", lambda *_: m.run_service_now())
        ctl.append(rb)
        svc_in.append(ctl)
        self.top_row.append(svc_card)

        how_card, how_in = make_card("How persistence works", "dialog-information-symbolic")
        for txt in [
            f"Persistent rules live in {PERSISTENT_RULES} and come back after a reboot.",
            f"“Until reboot” rules live in {RUN_DIR}, which Linux tmpfs clears at boot.",
            "Routes added from the Routing Table screen are one-off and are never restored.",
            "With the service off, rules only change when you press Apply.",
        ]:
            l = Gtk.Label(label=f"• {txt}", xalign=0)
            l.add_css_class("caption")
            l.set_wrap(True)
            how_in.append(l)
        self.top_row.append(how_card)

        # Startup card
        clear_box(self.startup_box)
        asst_btn = Gtk.Button(label="Setup Assistant…")
        asst_btn.connect("clicked", lambda *_: self.open_setup_cb())
        st_card, st_in = make_card("Startup", "system-shutdown-symbolic", asst_btn)

        def add_startup_row(title: str, detail: str, active: bool, cb) -> None:
            r = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            c = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            c.set_hexpand(True)
            tl = Gtk.Label(label=title, xalign=0)
            tl.add_css_class("heading")
            dl = Gtk.Label(label=detail, xalign=0)
            dl.add_css_class("caption")
            dl.add_css_class("dim-label")
            dl.set_wrap(True)
            c.append(tl)
            c.append(dl)
            r.append(c)
            tog = Gtk.Switch()
            tog.set_active(active)
            tog.set_valign(Gtk.Align.CENTER)
            tog.connect("notify::active", lambda b, _p: cb(b.get_active()) if b.get_active() != active else None)
            r.append(tog)
            st_in.append(r)

        add_startup_row(
            "Routes and kill switch at boot",
            "The background service runs as root from boot, even when the app is closed.",
            m.background_service_running,
            m.set_service,
        )
        st_in.append(Gtk.Separator())
        add_startup_row(
            "Open SplitRoute at login",
            "Starts the app automatically via XDG Autostart when you log in.",
            m.open_at_login,
            m.set_open_at_login,
        )
        st_in.append(Gtk.Separator())
        tray_on = bool(m.ui_prefs.get("menuBarEnabled", True))
        add_startup_row(
            "System tray / top bar icon",
            "Shows status, public IP and quick actions in the top bar, and keeps the app running when its window is closed.",
            tray_on,
            lambda v: m.set_ui_pref("menuBarEnabled", v),
        )
        if tray_on:
            style_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            style_row.append(Gtk.Label(label="Show in the tray"))
            sc = Gtk.ComboBoxText()
            sc.append("icon", "Icon")
            sc.append("flag", "Country flag")
            sc.append("flagAndIP", "Flag and public IP")
            sc.append("speed", "Download / upload speed")
            sc.set_active_id(m.ui_prefs.get("menuBarLabelStyle", "icon"))
            sc.connect("changed", lambda c: m.set_ui_pref("menuBarLabelStyle", c.get_active_id() or "icon"))
            style_row.append(sc)
            st_in.append(style_row)
        self.startup_box.append(st_card)

        # Rule persistence quick switcher
        clear_box(self.rules_box)
        rp_card, rp_in = make_card("Rule persistence", "view-list-symbolic")
        if not m.workspace.rules:
            rp_in.append(Gtk.Label(label="No rules", xalign=0))
        for r in m.workspace.rules:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            nl = Gtk.Label(label=r.name or "Untitled", xalign=0)
            nl.add_css_class("heading")
            dl = Gtk.Label(label=", ".join(r.destinations), xalign=0)
            dl.add_css_class("caption")
            dl.add_css_class("monospace")
            dl.add_css_class("dim-label")
            dl.set_hexpand(True)
            dl.set_ellipsize(3)
            row.append(nl)
            row.append(dl)
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            box.add_css_class("linked")
            b_p = Gtk.ToggleButton(label="Persistent")
            b_u = Gtk.ToggleButton(label="Until reboot")
            b_u.set_group(b_p)
            b_p.set_active(r.persistent)
            b_u.set_active(not r.persistent)
            b_p.connect("toggled", lambda b, rid=r.id: m.set_persistent(rid, True) if b.get_active() else None)
            b_u.connect("toggled", lambda b, rid=r.id: m.set_persistent(rid, False) if b.get_active() else None)
            box.append(b_p)
            box.append(b_u)
            row.append(box)
            rp_in.append(row)
        self.rules_box.append(rp_card)

        if not m.helper_log:
            m.load_helper_log()
        self.log_lbl.set_label(m.helper_log or f"No log entries yet ({LOG_FILE}).")

    def _on_interval_changed(self, spin: Gtk.SpinButton) -> None:
        val = int(spin.get_value())
        if val != self.model.workspace.settings.service_interval_seconds:
            self.model.workspace.settings.service_interval_seconds = val
            self.model.save_workspace()

