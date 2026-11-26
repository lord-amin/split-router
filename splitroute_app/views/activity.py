from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from gi.repository import Adw, Gtk

from ..notifier import GITHUB_REPO, open_url
from ..paths import CONFIG_DIR, ICON_PATH, LOG_FILE, PERSISTENT_RULES, VERSION, WORKSPACE_FILE
from .widgets import clear_box, make_card, make_kv_row, make_page_header

if TYPE_CHECKING:
    from ..app_model import AppModel


class ActivityView(Gtk.Box):
    def __init__(self, model: AppModel) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.model = model
        self.set_margin_top(24)
        self.set_margin_bottom(24)
        self.set_margin_start(24)
        self.set_margin_end(24)

        clr = Gtk.Button(label="Clear")
        clr.connect("clicked", lambda *_: (self.model.activity.clear(), self.refresh()))
        self.clr_btn = clr
        self.append(make_page_header("Activity Log", "Everything SplitRoute changed during this session", clr))

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        self.list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        scroll.set_child(self.list_box)
        self.append(scroll)

    def refresh(self) -> None:
        clear_box(self.list_box)
        self.clr_btn.set_sensitive(bool(self.model.activity))
        if not self.model.activity:
            self.list_box.append(Adw.StatusPage(
                icon_name="view-list-symbolic",
                title="No activity yet",
                description="Applied changes, adapter actions and path checks appear here.",
            ))
            return
        for e in self.model.activity:
            card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            card.add_css_class("card")
            inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            inner.set_margin_top(10)
            inner.set_margin_bottom(10)
            inner.set_margin_start(12)
            inner.set_margin_end(12)
            card.append(inner)

            ic = Gtk.Image.new_from_icon_name("emblem-ok-symbolic" if e.ok else "dialog-error-symbolic")
            inner.append(ic)
            tl = Gtk.Label(label=e.date.strftime("%H:%M:%S"), xalign=0)
            tl.add_css_class("monospace")
            tl.add_css_class("dim-label")
            tl.set_size_request(80, -1)
            inner.append(tl)

            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            col.set_hexpand(True)
            t = Gtk.Label(label=e.title[:1].upper() + e.title[1:], xalign=0)
            t.add_css_class("heading")
            col.append(t)
            if e.detail:
                d = Gtk.Label(label=e.detail, xalign=0)
                d.add_css_class("caption")
                d.add_css_class("monospace")
                d.add_css_class("dim-label")
                d.set_selectable(True)
                d.set_wrap(True)
                col.append(d)
            inner.append(col)
            self.list_box.append(card)


class AboutView(Gtk.ScrolledWindow):
    def __init__(self, model: AppModel) -> None:
        super().__init__()
        self.model = model
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_margin_top(36)
        box.set_margin_bottom(36)
        box.set_margin_start(36)
        box.set_margin_end(36)
        box.set_halign(Gtk.Align.CENTER)
        self.set_child(box)

        ic = Gtk.Image.new_from_file(str(ICON_PATH)) if ICON_PATH.is_file() else Gtk.Image.new_from_icon_name("network-vpn-symbolic")
        ic.set_pixel_size(84)
        box.append(ic)

        title = Gtk.Label(label="SplitRoute")
        title.add_css_class("title-1")
        ver = Gtk.Label(label=f"Version {VERSION}")
        ver.add_css_class("dim-label")
        sub = Gtk.Label(label="Linux policy-free split tunneling, per-destination kernel routing, and isolated nftables kill switch.")
        sub.set_justify(Gtk.Justification.CENTER)
        sub.add_css_class("dim-label")
        box.append(title)
        box.append(ver)
        box.append(sub)

        auth_card, auth_in = make_card()
        auth_in.set_halign(Gtk.Align.CENTER)
        c_lbl = Gtk.Label(label="MAINTAINED BY")
        c_lbl.add_css_class("caption")
        c_lbl.add_css_class("dim-label")
        a_lbl = Gtk.Label(label="lord-amin")
        a_lbl.add_css_class("title-2")
        email_lbl = Gtk.Label(label="sadeghi.yaser@gmail.com")
        email_lbl.add_css_class("caption")
        email_lbl.add_css_class("dim-label")
        links = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        links.set_halign(Gtk.Align.CENTER)
        gh = Gtk.Button(label="GitHub Repository")
        gh.connect("clicked", lambda *_: open_url(f"https://github.com/{GITHUB_REPO}"))
        iss = Gtk.Button(label="Issues")
        iss.connect("clicked", lambda *_: open_url(f"https://github.com/{GITHUB_REPO}/issues"))
        links.append(gh)
        links.append(iss)
        auth_in.append(c_lbl)
        auth_in.append(a_lbl)
        auth_in.append(email_lbl)
        auth_in.append(links)
        box.append(auth_card)

        self.upd_btn = Gtk.Button(label="Check for Updates")
        self.upd_btn.set_halign(Gtk.Align.CENTER)
        self.upd_btn.connect("clicked", lambda *_: model.check_for_updates(manual=True))
        box.append(self.upd_btn)

        foot = Gtk.Label(label=f"© {datetime.now().year} lord-amin · MIT License")
        foot.add_css_class("caption")
        foot.add_css_class("dim-label")
        box.append(foot)

    def refresh(self) -> None:
        self.upd_btn.set_sensitive(not self.model.checking_for_updates)
        self.upd_btn.set_label("Checking…" if self.model.checking_for_updates else "Check for Updates")


class SetupAssistantDialog(Adw.Window):
    def __init__(self, parent: Gtk.Window | None, model: AppModel, open_import_cb) -> None:
        super().__init__(transient_for=parent, modal=True, title="Welcome to SplitRoute")
        self.model = model
        self.open_import_cb = open_import_cb
        self.set_default_size(640, 560)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(outer)
        outer.append(Adw.HeaderBar())

        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.body.set_margin_top(20)
        self.body.set_margin_bottom(20)
        self.body.set_margin_start(24)
        self.body.set_margin_end(24)
        self.body.set_vexpand(True)
        outer.append(self.body)

        self._render()

    def _render(self) -> None:
        clear_box(self.body)
        m = self.model
        hdr = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        t = Gtk.Label(label="Welcome to SplitRoute", xalign=0)
        t.add_css_class("title-1")
        s = Gtk.Label(label="Set up how routing starts on Linux. Everything here can be changed later in Persistence.", xalign=0)
        s.add_css_class("dim-label")
        hdr.append(t)
        hdr.append(s)
        self.body.append(hdr)

        def step(done: bool, title: str, detail: str, widget: Gtk.Widget | None) -> None:
            card, inner = make_card()
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            ic = Gtk.Image.new_from_icon_name("emblem-ok-symbolic" if done else "emblem-system-symbolic")
            row.append(ic)
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            col.set_hexpand(True)
            tl = Gtk.Label(label=title, xalign=0)
            tl.add_css_class("heading")
            dl = Gtk.Label(label=detail, xalign=0)
            dl.add_css_class("caption")
            dl.add_css_class("dim-label")
            dl.set_wrap(True)
            col.append(tl)
            col.append(dl)
            row.append(col)
            if widget is not None:
                widget.set_valign(Gtk.Align.CENTER)
                row.append(widget)
            inner.append(row)
            self.body.append(card)

        inst_btn = Gtk.Button(label="Installed" if m.background_service_running else "Install…")
        inst_btn.set_sensitive(not m.background_service_running)
        inst_btn.connect("clicked", lambda *_: m.set_service(True))
        step(
            m.background_service_running,
            "Install the background service & probe",
            "Applies your rules and nftables kill switch at boot, enables per-interface speed tests, and keeps repairing routes.",
            inst_btn,
        )

        login_sw = Gtk.Switch()
        login_sw.set_active(m.open_at_login)
        login_sw.connect("notify::active", lambda b, _p: m.set_open_at_login(b.get_active()) if b.get_active() != m.open_at_login else None)
        step(m.open_at_login, "Open the app at login", "Optional. Routing works without the app running.", login_sw)

        tray_on = bool(m.ui_prefs.get("menuBarEnabled", True))
        tray_sw = Gtk.Switch()
        tray_sw.set_active(tray_on)
        tray_sw.connect("notify::active", lambda b, _p: m.set_ui_pref("menuBarEnabled", b.get_active()))
        step(tray_on, "Show the system tray icon", "Status and quick actions; the app keeps running when the window is closed.", tray_sw)

        imports = len(m.import_candidates)
        imp_btn = None
        if imports > 0:
            imp_btn = Gtk.Button(label="Review…")
            imp_btn.connect("clicked", lambda *_: (self.close(), self.open_import_cb()))
        step(
            imports == 0,
            "No unmanaged static routes found" if imports == 0 else f"Import {imports} existing static route" + ("" if imports == 1 else "s"),
            "Routes added by hand or by scripts can become rules, so they survive reboots.",
            imp_btn,
        )

        nr_btn = Gtk.Button(label="New Rule…")
        nr_btn.connect("clicked", lambda *_: (self.close(), m.open_main_window("rules"), m.new_rule()))
        step(bool(m.workspace.rules), "Create your first rule", "Send a domain or IP through the interface you choose.", nr_btn)

        sp = Gtk.Box()
        sp.set_vexpand(True)
        self.body.append(sp)

        foot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        foot.append(Gtk.Label(label="You can reopen this anytime from Persistence › Setup Assistant.", xalign=0))
        sp2 = Gtk.Box()
        sp2.set_hexpand(True)
        foot.append(sp2)
        done = Gtk.Button(label="Done")
        done.add_css_class("suggested-action")
        done.connect("clicked", self._finish)
        foot.append(done)
        self.body.append(foot)

    def _finish(self, *_args) -> None:
        self.model.workspace.setup_completed = True
        self.model.save_workspace()
        self.close()


class SettingsDialog(Adw.PreferencesWindow):
    def __init__(self, parent: Gtk.Window | None, model: AppModel) -> None:
        super().__init__(transient_for=parent, modal=True, title="Settings")
        self.model = model
        self.set_default_size(520, 560)

        page = Adw.PreferencesPage()
        self.add(page)

        mon = Adw.PreferencesGroup(title="Monitoring")
        ref_combo = Gtk.ComboBoxText()
        for val, lbl in [("2", "2 seconds"), ("5", "5 seconds"), ("10", "10 seconds"), ("30", "30 seconds")]:
            ref_combo.append(val, lbl)
        ref_combo.set_active_id(str(int(model.workspace.settings.refresh_seconds)))
        ref_combo.connect("changed", lambda c: self._set_refresh(float(c.get_active_id() or "5")))
        r_row = Adw.ActionRow(title="Refresh every")
        r_row.add_suffix(ref_combo)
        mon.add(r_row)

        sys_sw = Adw.SwitchRow(title="Show system interfaces (docker, veth, lo…)")
        sys_sw.set_active(model.workspace.settings.show_system_interfaces)
        sys_sw.connect("notify::active", lambda b, _p: self._set_setting("show_system_interfaces", b.get_active()))
        mon.add(sys_sw)

        ll_sw = Adw.SwitchRow(title="Hide link-local kernel entries in Routing Table")
        ll_sw.set_active(model.workspace.settings.hide_link_layer_routes)
        ll_sw.connect("notify::active", lambda b, _p: self._set_setting("hide_link_layer_routes", b.get_active()))
        mon.add(ll_sw)
        page.add(mon)

        pub = Adw.PreferencesGroup(
            title="Public IP",
            description="Addresses come from api.ipify.org / api6.ipify.org; location and ISP from the provider below.",
        )
        geo_combo = Gtk.ComboBoxText()
        geo_combo.append("ipinfo", "ipinfo.io")
        geo_combo.append("ipwhois", "ipwho.is")
        geo_combo.set_active_id(model.workspace.settings.geo_provider)
        geo_combo.connect("changed", lambda c: self._set_setting("geo_provider", c.get_active_id() or "ipinfo"))
        g_row = Adw.ActionRow(title="Location lookup")
        g_row.add_suffix(geo_combo)
        pub.add(g_row)
        page.add(pub)

        upd = Adw.PreferencesGroup(title="Updates")
        upd_sw = Adw.SwitchRow(title="Check for new versions once a day")
        upd_sw.set_active(model.workspace.settings.check_for_updates)
        upd_sw.connect("notify::active", lambda b, _p: self._set_setting("check_for_updates", b.get_active()))
        upd.add(upd_sw)
        chk_btn = Gtk.Button(label="Check Now")
        chk_btn.set_valign(Gtk.Align.CENTER)
        chk_btn.connect("clicked", lambda *_: model.check_for_updates(manual=True))
        chk_row = Adw.ActionRow(title=f"GitHub Releases ({GITHUB_REPO})")
        chk_row.add_suffix(chk_btn)
        upd.add(chk_row)
        page.add(upd)

        files = Adw.PreferencesGroup(title="Files")
        files.add(Adw.ActionRow(title="Workspace", subtitle=str(WORKSPACE_FILE)))
        files.add(Adw.ActionRow(title="Rules (root)", subtitle=PERSISTENT_RULES))
        files.add(Adw.ActionRow(title="Helper log", subtitle=LOG_FILE))
        open_btn = Gtk.Button(label="Open Data Folder")
        open_btn.set_valign(Gtk.Align.CENTER)
        open_btn.connect("clicked", lambda *_: open_url(CONFIG_DIR.as_uri()))
        f_row = Adw.ActionRow(title="Configuration folder")
        f_row.add_suffix(open_btn)
        files.add(f_row)
        page.add(files)

    def _set_refresh(self, val: float) -> None:
        self.model.workspace.settings.refresh_seconds = val
        self.model.save_workspace()

    def _set_setting(self, attr: str, val) -> None:
        setattr(self.model.workspace.settings, attr, val)
        self.model.save_workspace()

