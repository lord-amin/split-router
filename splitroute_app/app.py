from __future__ import annotations

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

from .app_model import AppModel
from .notifier import Notifier
from .paths import APP_ID, ICON_PATH, VERSION
from .tray import TrayController
from .views.activity import AboutView, ActivityView, SettingsDialog, SetupAssistantDialog
from .views.adapters import AdaptersView
from .views.automation import AutomationView
from .views.backup import BackupView
from .views.blocklist import BlockListView
from .views.connections import ConnectionsView
from .views.dashboard import DashboardView
from .views.dns import DNSView
from .views.killswitch import KillSwitchView
from .views.path_check import PathCheckView
from .views.profiles import ProfilesView
from .views.routing_table import ImportRoutesDialog, RoutingTableView
from .views.rules import RuleEditorDialog, RulesView
from .views.service import ServiceView
from .views.speedtest import SpeedTestView
from .views.traffic import TrafficView
from .views.widgets import clear_box, install_css, make_pill

SIDEBAR_SECTIONS = [
    ("Overview", [
        ("dashboard", "Dashboard", "view-grid-symbolic"),
        ("connections", "Connections", "network-transmit-receive-symbolic"),
        ("traffic", "Traffic", "utilities-system-monitor-symbolic"),
        ("speedTest", "Speed Test", "media-playback-start-symbolic"),
    ]),
    ("Routing", [
        ("rules", "Routing Rules", "network-wired-symbolic"),
        ("profiles", "Profiles", "view-paged-symbolic"),
        ("killSwitch", "Kill Switch", "security-high-symbolic"),
        ("blockList", "Block List", "action-unavailable-symbolic"),
        ("routes", "Routing Table", "view-list-symbolic"),
        ("pathCheck", "Path Check", "system-search-symbolic"),
        ("dns", "DNS", "network-server-symbolic"),
    ]),
    ("System", [
        ("adapters", "Network Adapters", "network-workgroup-symbolic"),
        ("automation", "Automation", "emblem-system-symbolic"),
        ("service", "Persistence", "document-open-recent-symbolic"),
        ("backup", "Backup & Restore", "document-save-symbolic"),
        ("activity", "Activity Log", "text-x-generic-symbolic"),
        ("about", "About", "help-about-symbolic"),
    ]),
]


class SplitRouteWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, model: AppModel) -> None:
        super().__init__(application=app, title="SplitRoute")
        self.model = model
        self.set_default_size(1260, 820)
        self.set_size_request(980, 660)
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        install_css()

        split = Adw.NavigationSplitView()
        split.set_min_sidebar_width(225)
        split.set_max_sidebar_width(265)
        self.set_content(split)

        # Sidebar page
        sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        s_header = Adw.HeaderBar()
        s_header.set_show_end_title_buttons(False)
        brand_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        brand_icon = Gtk.Image.new_from_file(str(ICON_PATH)) if ICON_PATH.is_file() else Gtk.Image.new_from_icon_name("network-vpn-symbolic")
        brand_icon.set_pixel_size(22)
        s_title = Gtk.Label(label="SplitRoute")
        s_title.add_css_class("heading")
        ver_pill = make_pill(f"v{VERSION}", "green")
        brand_box.append(brand_icon)
        brand_box.append(s_title)
        brand_box.append(ver_pill)
        s_header.set_title_widget(brand_box)
        sidebar_box.append(s_header)

        s_scroll = Gtk.ScrolledWindow()
        s_scroll.set_vexpand(True)
        self.sidebar_list = Gtk.ListBox()
        self.sidebar_list.add_css_class("navigation-sidebar")
        self.sidebar_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.sidebar_list.connect("row-selected", self._on_sidebar_selected)
        self._row_map: dict[str, Gtk.ListBoxRow] = {}
        self._badge_map: dict[str, Gtk.Label] = {}
        self._build_sidebar()
        s_scroll.set_child(self.sidebar_list)
        sidebar_box.append(s_scroll)

        sidebar_box.append(Gtk.Separator())
        self.footer_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self.footer_box.set_margin_top(10)
        self.footer_box.set_margin_bottom(12)
        self.footer_box.set_margin_start(14)
        self.footer_box.set_margin_end(14)
        sidebar_box.append(self.footer_box)

        split.set_sidebar(Adw.NavigationPage.new(sidebar_box, "SplitRoute"))

        # Content page
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        c_header = Adw.HeaderBar()

        self.busy_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.busy_spin = Gtk.Spinner()
        self.busy_lbl = Gtk.Label(label="")
        self.busy_lbl.add_css_class("dim-label")
        self.busy_box.append(self.busy_spin)
        self.busy_box.append(self.busy_lbl)
        self.busy_box.set_visible(False)
        c_header.pack_end(self.busy_box)

        self.apply_btn = Gtk.Button(label="Applied")
        self.apply_btn.connect("clicked", lambda *_: self.model.apply())
        c_header.pack_end(self.apply_btn)

        self.refresh_btn = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        self.refresh_btn.set_tooltip_text("Refresh network state (Ctrl+R)")
        self.refresh_btn.connect("clicked", lambda *_: self.model.refresh_all_now())
        c_header.pack_end(self.refresh_btn)

        # Menu button for Settings, Setup Assistant, New Rule, Flush DNS
        menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic")
        gmenu = Gio.Menu()
        gmenu.append("New Routing Rule", "app.new-rule")
        gmenu.append("Flush DNS Cache", "app.flush-dns")
        gmenu.append("Setup Assistant…", "app.setup")
        gmenu.append("Settings…", "app.settings")
        gmenu.append("Quit SplitRoute", "app.quit")
        menu_btn.set_menu_model(gmenu)
        c_header.pack_end(menu_btn)

        content_box.append(c_header)

        self.stack = Gtk.Stack()
        self.stack.set_vexpand(True)
        self.stack.set_hexpand(True)
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        content_box.append(self.stack)

        get_win = lambda: self
        open_imp = lambda: ImportRoutesDialog(self, self.model).present()
        open_setup = lambda: SetupAssistantDialog(self, self.model, open_imp).present()

        self.pages: dict[str, Gtk.Widget] = {
            "dashboard": DashboardView(model, get_win),
            "connections": ConnectionsView(model),
            "traffic": TrafficView(model),
            "speedTest": SpeedTestView(model, get_win),
            "rules": RulesView(model, get_win, open_imp),
            "profiles": ProfilesView(model, get_win),
            "killSwitch": KillSwitchView(model, get_win),
            "blockList": BlockListView(model, get_win),
            "routes": RoutingTableView(model, get_win, open_imp),
            "pathCheck": PathCheckView(model),
            "dns": DNSView(model, get_win),
            "adapters": AdaptersView(model, get_win),
            "automation": AutomationView(model, get_win),
            "service": ServiceView(model, get_win, open_setup),
            "backup": BackupView(model, get_win),
            "activity": ActivityView(model),
            "about": AboutView(model),
        }
        for key, widget in self.pages.items():
            self.stack.add_named(widget, key)

        split.set_content(Adw.NavigationPage.new(content_box, "Detail"))

        self.connect("close-request", self._on_close_request)

    def _build_sidebar(self) -> None:
        for sec_title, items in SIDEBAR_SECTIONS:
            hdr_row = Gtk.ListBoxRow()
            hdr_row.set_selectable(False)
            hdr_row.set_activatable(False)
            lbl = Gtk.Label(label=sec_title.upper(), xalign=0)
            lbl.add_css_class("caption")
            lbl.add_css_class("dim-label")
            lbl.set_margin_top(10)
            lbl.set_margin_bottom(2)
            lbl.set_margin_start(8)
            hdr_row.set_child(lbl)
            self.sidebar_list.append(hdr_row)

            for key, title, icon_name in items:
                row = Gtk.ListBoxRow()
                row._page_key = key  # type: ignore[attr-defined]
                hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
                hbox.set_margin_top(6)
                hbox.set_margin_bottom(6)
                hbox.set_margin_start(6)
                hbox.set_margin_end(6)
                hbox.append(Gtk.Image.new_from_icon_name(icon_name))
                tl = Gtk.Label(label=title, xalign=0)
                tl.set_hexpand(True)
                hbox.append(tl)
                badge = make_pill("0", "orange")
                badge.set_visible(False)
                hbox.append(badge)
                row.set_child(hbox)
                self.sidebar_list.append(row)
                self._row_map[key] = row
                self._badge_map[key] = badge

    def _on_sidebar_selected(self, _lb: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        if row is None:
            return
        key = getattr(row, "_page_key", None)
        if key and key != self.model.selection:
            self.model.selection = key
            self.sync()

    def _on_close_request(self, *_args) -> bool:
        if self.model.ui_prefs.get("menuBarEnabled", True):
            self.hide()
            return True
        return False

    def sync(self) -> None:
        m = self.model
        sel = m.selection or "dashboard"
        if sel in self._row_map and self.sidebar_list.get_selected_row() is not self._row_map[sel]:
            self.sidebar_list.select_row(self._row_map[sel])
        if self.stack.get_visible_child_name() != sel:
            self.stack.set_visible_child_name(sel)

        # Badges
        badges = {
            "rules": len(m.pending_changes),
            "killSwitch": 1 if m.kill_switch_pending else 0,
            "automation": 1 if m.automation_pending else 0,
        }
        for k, b_lbl in self._badge_map.items():
            val = badges.get(k, 0)
            b_lbl.set_visible(val > 0)
            if val > 0:
                b_lbl.set_label(str(val))

        # Toolbar
        if m.busy:
            self.busy_box.set_visible(True)
            self.busy_spin.start()
            self.busy_lbl.set_label(m.busy[:1].upper() + m.busy[1:])
        else:
            self.busy_spin.stop()
            self.busy_box.set_visible(False)

        cnt = m.pending_count
        if cnt > 0:
            self.apply_btn.set_label(f"Apply {cnt} Change" + ("" if cnt == 1 else "s"))
            self.apply_btn.add_css_class("suggested-action")
            self.apply_btn.set_sensitive(True)
        else:
            self.apply_btn.set_label("Applied")
            self.apply_btn.remove_css_class("suggested-action")
            self.apply_btn.set_sensitive(False)

        # Sidebar Footer
        clear_box(self.footer_box)
        svc = m.net.service
        if svc.daemon_installed and svc.loaded:
            s_txt, s_cls = "Persistence on", "bir-status-dot-green"
        elif svc.helper_installed:
            s_txt, s_cls = "Persistence off", "bir-status-dot-yellow"
        else:
            s_txt, s_cls = "Helper not installed", "bir-status-dot-gray"
        r1 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        dot = Gtk.Label(label="●")
        dot.add_css_class(s_cls)
        r1.append(dot)
        r1.append(Gtk.Label(label=s_txt, xalign=0))
        self.footer_box.append(r1)

        egress = (m.net.internet_route or {}).get("interface")
        if egress:
            eg_lbl = Gtk.Label(label=f"Internet via {m.display_name(egress)} ({egress})", xalign=0)
            eg_lbl.add_css_class("caption")
            eg_lbl.add_css_class("dim-label")
            eg_lbl.set_ellipsize(3)
            self.footer_box.append(eg_lbl)

        ks = (m.net.helper_status or {}).get("killSwitch") or {}
        if ks.get("active"):
            ks_lbl = Gtk.Label(label="Kill switch armed", xalign=0)
            ks_lbl.add_css_class("caption")
            self.footer_box.append(ks_lbl)

        upd_lbl = Gtk.Label(label=f"Updated {m.net.taken_at.astimezone().strftime('%H:%M:%S')}", xalign=0)
        upd_lbl.add_css_class("caption")
        upd_lbl.add_css_class("dim-label")
        self.footer_box.append(upd_lbl)

        # Refresh active page
        page = self.pages.get(sel)
        if page is not None and hasattr(page, "refresh"):
            page.refresh()


class SplitRouteApp(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.model = AppModel()
        self.win: SplitRouteWindow | None = None
        self.tray: TrayController | None = None

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        Notifier.bind_app(self)
        if ICON_PATH.is_file():
            Gtk.IconTheme.get_for_display(self._get_display()).add_search_path(str(ICON_PATH.parent))

        def add_act(name: str, cb, accels: list[str] | None = None) -> None:
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", lambda *_: cb())
            self.add_action(act)
            if accels:
                self.set_accels_for_action(f"app.{name}", accels)

        add_act("new-rule", lambda: (self.model.open_main_window("rules"), self.model.new_rule()), ["<Control>n"])
        add_act("apply", lambda: self.model.apply() if self.model.has_pending else None, ["<Control>Return"])
        add_act("refresh", lambda: self.model.refresh_all_now(), ["<Control>r"])
        add_act("path-check", lambda: self.model.open_main_window("pathCheck"), ["<Control>k"])
        add_act("flush-dns", lambda: self.model.flush_dns())
        add_act("setup", lambda: self._open_setup())
        add_act("settings", lambda: SettingsDialog(self.win, self.model).present(), ["<Control>comma"])
        add_act("quit", lambda: self.quit(), ["<Control>q"])

    @staticmethod
    def _get_display():
        from gi.repository import Gdk
        return Gdk.Display.get_default()

    def do_activate(self) -> None:
        if self.win is None:
            self.win = SplitRouteWindow(self, self.model)
            self.tray = TrayController(self, self.model)
            self.model.bind_ui(
                alert_cb=self._show_alert,
                open_window_cb=self._open_window,
                open_rule_editor_cb=lambda r: RuleEditorDialog(self.win, self.model, r).present(),
                open_setup_cb=self._open_setup,
            )
            self.model.subscribe(self._on_model_update)
            self.tray.set_enabled(bool(self.model.ui_prefs.get("menuBarEnabled", True)))
            self.model.start()
        self.win.present()
        self._on_model_update()

    def _on_model_update(self) -> None:
        if self.win is not None:
            self.win.sync()
        if self.tray is not None:
            self.tray.set_enabled(bool(self.model.ui_prefs.get("menuBarEnabled", True)))
            self.tray.update()

    def _open_window(self, page: str | None) -> None:
        if page:
            self.model.selection = page
        if self.win is not None:
            self.win.present()
            self.win.sync()

    def _open_setup(self) -> None:
        if self.win is not None:
            SetupAssistantDialog(self.win, self.model, lambda: ImportRoutesDialog(self.win, self.model).present()).present()

    def _show_alert(self, title: str, message: str) -> None:
        dlg = Adw.MessageDialog(transient_for=self.win, heading=title, body=message)
        dlg.add_response("ok", "OK")
        dlg.present()


def run_selftest() -> int:
    from .models import compile_nftables_preview
    from .path_analyzer import PathAnalyzer
    from .probes import Probes
    from .scanner import NetworkScanner

    print(f"SplitRoute Linux selftest (version {VERSION})")
    snap = NetworkScanner.scan()
    print(f"Interfaces: {len(snap.interfaces)}")
    for i in snap.interfaces:
        if not i.is_system_noise:
            print(f"  - {i.name} ({i.kind_label}) ipv4={i.primary_ipv4} router={i.router} up={i.is_up}")
    print(f"Internet route: {snap.internet_route}")
    print(f"System DNS: {snap.system_dns}")
    res = PathAnalyzer.analyze("1.1.1.1", snap, [], {})
    for h in res.hops:
        print(f"Path 1.1.1.1: {h.headline}")
        for line in h.explanation:
            print(f"  {line}")
    nft_preview = compile_nftables_preview("destinations", ["enp3s0"], ["91.108.56.0/22"], [], [], True)
    print(f"nftables preview lines: {len(nft_preview.splitlines())}")
    pub, _ = Probes.probe_path("default", "Default", None)
    print(f"Public IPv4: {pub.v4.ip if pub.v4 else pub.v4_error}")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return run_selftest()
    app = SplitRouteApp()
    return app.run(sys.argv)

