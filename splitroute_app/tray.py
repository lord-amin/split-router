from __future__ import annotations

import os
from typing import TYPE_CHECKING

from gi.repository import Gdk, Gio, GLib, Gtk

from .models import format_rate, short_rate
from .paths import ICON_PATH

if TYPE_CHECKING:
    from .app_model import AppModel

SNI_XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <method name="Activate">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="SecondaryActivate">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconThemePath" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <property name="XAyatanaLabel" type="s" access="read"/>
    <property name="XAyatanaLabelGuide" type="s" access="read"/>
    <signal name="NewIcon"/>
    <signal name="NewToolTip"/>
    <signal name="NewStatus"><arg type="s"/></signal>
    <signal name="XAyatanaNewLabel"><arg type="s"/><arg type="s"/></signal>
  </interface>
</node>
"""

MENU_XML = """
<node>
  <interface name="com.canonical.dbusmenu">
    <method name="GetLayout">
      <arg name="parentId" type="i" direction="in"/>
      <arg name="recursionDepth" type="i" direction="in"/>
      <arg name="propertyNames" type="as" direction="in"/>
      <arg name="revision" type="u" direction="out"/>
      <arg name="layout" type="(ia{sv}av)" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg name="ids" type="ai" direction="in"/>
      <arg name="propertyNames" type="as" direction="in"/>
      <arg name="properties" type="a(ia{sv})" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg name="id" type="i" direction="in"/>
      <arg name="name" type="s" direction="in"/>
      <arg name="value" type="v" direction="out"/>
    </method>
    <method name="Event">
      <arg name="id" type="i" direction="in"/>
      <arg name="eventId" type="s" direction="in"/>
      <arg name="data" type="v" direction="in"/>
      <arg name="timestamp" type="u" direction="in"/>
    </method>
    <method name="AboutToShow">
      <arg name="id" type="i" direction="in"/>
      <arg name="needUpdate" type="b" direction="out"/>
    </method>
    <property name="Version" type="u" access="read"/>
    <property name="Status" type="s" access="read"/>
    <signal name="LayoutUpdated">
      <arg name="revision" type="u"/>
      <arg name="parent" type="i"/>
    </signal>
  </interface>
</node>
"""


class TrayQuickWindow(Gtk.Window):
    """Compact status panel opened from the system tray (equivalent to macOS MenuBarView)."""

    def __init__(self, app: Gtk.Application, model: AppModel) -> None:
        super().__init__(application=app, title="SplitRoute Status")
        self.model = model
        self.set_default_size(350, 440)
        self.set_resizable(False)
        self.set_hide_on_close(True)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_top(14)
        box.set_margin_bottom(14)
        box.set_margin_start(14)
        box.set_margin_end(14)
        self.set_child(box)

        # Header
        hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label="SplitRoute", xalign=0)
        title.add_css_class("title-4")
        title.set_hexpand(True)
        self.service_pill = Gtk.Label(label="Service off")
        self.service_pill.add_css_class("pill")
        hdr.append(title)
        hdr.append(self.service_pill)
        box.append(hdr)

        # Public identity card
        pub_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        pub_card.add_css_class("card")
        pub_card.set_margin_top(2)
        pub_inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        pub_inner.set_margin_top(10)
        pub_inner.set_margin_bottom(10)
        pub_inner.set_margin_start(12)
        pub_inner.set_margin_end(12)
        pub_card.append(pub_inner)

        top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.flag_lbl = Gtk.Label(label="")
        self.flag_lbl.add_css_class("title-1")
        ip_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        ip_col.set_hexpand(True)
        sub_lbl = Gtk.Label(label="Public IPv4", xalign=0)
        sub_lbl.add_css_class("caption")
        sub_lbl.add_css_class("dim-label")
        self.ip_lbl = Gtk.Label(label="—", xalign=0)
        self.ip_lbl.add_css_class("title-3")
        self.ip_lbl.add_css_class("monospace")
        self.ip_lbl.set_selectable(True)
        ip_col.append(sub_lbl)
        ip_col.append(self.ip_lbl)
        copy_btn = Gtk.Button.new_from_icon_name("edit-copy-symbolic")
        copy_btn.add_css_class("flat")
        copy_btn.set_tooltip_text("Copy IPv4")
        copy_btn.connect("clicked", self._copy_ip)
        top_row.append(self.flag_lbl)
        top_row.append(ip_col)
        top_row.append(copy_btn)
        pub_inner.append(top_row)

        self.loc_lbl = Gtk.Label(label="", xalign=0)
        self.loc_lbl.add_css_class("caption")
        self.loc_lbl.add_css_class("dim-label")
        self.org_lbl = Gtk.Label(label="", xalign=0)
        self.org_lbl.add_css_class("caption")
        self.org_lbl.add_css_class("dim-label")
        self.v6_lbl = Gtk.Label(label="", xalign=0)
        self.v6_lbl.add_css_class("caption")
        self.v6_lbl.add_css_class("dim-label")
        pub_inner.append(self.loc_lbl)
        pub_inner.append(self.org_lbl)
        pub_inner.append(self.v6_lbl)
        box.append(pub_card)

        # Speed tiles
        speed_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        speed_row.set_homogeneous(True)
        self.dl_lbl = self._make_speed_tile(speed_row, "Download", "go-down-symbolic")
        self.ul_lbl = self._make_speed_tile(speed_row, "Upload", "go-up-symbolic")
        box.append(speed_row)

        # Facts
        facts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self.egress_lbl = self._make_fact_row(facts, "Internet via")
        self.local_lbl = self._make_fact_row(facts, "Local IP")
        self.direct_lbl = self._make_fact_row(facts, "Direct public IP")
        self.profile_lbl = self._make_fact_row(facts, "Profile")
        self.routes_lbl = self._make_fact_row(facts, "Managed routes")
        self.ks_lbl = self._make_fact_row(facts, "Kill switch")
        box.append(facts)

        # Pending apply button
        self.apply_btn = Gtk.Button(label="Apply Changes")
        self.apply_btn.add_css_class("suggested-action")
        self.apply_btn.connect("clicked", lambda *_: self.model.apply())
        box.append(self.apply_btn)

        box.append(Gtk.Separator())
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        open_btn = Gtk.Button(label="Open App")
        open_btn.connect("clicked", lambda *_: self._open(None))
        path_btn = Gtk.Button(label="Check Path")
        path_btn.connect("clicked", lambda *_: self._open("pathCheck"))
        speed_btn = Gtk.Button.new_from_icon_name("utilities-system-monitor-symbolic")
        speed_btn.set_tooltip_text("Speed Test")
        speed_btn.connect("clicked", lambda *_: self._open("speedTest"))
        ref_btn = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        ref_btn.set_tooltip_text("Refresh")
        ref_btn.connect("clicked", lambda *_: self.model.refresh_all_now())
        actions.append(open_btn)
        actions.append(path_btn)
        actions.append(speed_btn)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        actions.append(spacer)
        actions.append(ref_btn)
        box.append(actions)

    def _make_speed_tile(self, parent: Gtk.Box, title: str, icon: str) -> Gtk.Label:
        card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        card.add_css_class("card")
        inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        inner.set_margin_top(8)
        inner.set_margin_bottom(8)
        inner.set_margin_start(10)
        inner.set_margin_end(10)
        img = Gtk.Image.new_from_icon_name(icon)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        t = Gtk.Label(label=title, xalign=0)
        t.add_css_class("caption")
        t.add_css_class("dim-label")
        val = Gtk.Label(label="—", xalign=0)
        val.add_css_class("heading")
        val.add_css_class("monospace")
        col.append(t)
        col.append(val)
        inner.append(img)
        inner.append(col)
        card.append(inner)
        parent.append(card)
        return val

    def _make_fact_row(self, parent: Gtk.Box, key: str) -> Gtk.Label:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        k = Gtk.Label(label=key, xalign=0)
        k.add_css_class("dim-label")
        k.set_hexpand(True)
        v = Gtk.Label(label="—", xalign=1)
        v.add_css_class("monospace")
        row.append(k)
        row.append(v)
        parent.append(row)
        return v

    def _copy_ip(self, *_args) -> None:
        d = next((p for p in self.model.public_ips if p.id == "default"), None)
        if d and d.v4:
            display = Gdk.Display.get_default()
            if display:
                display.get_clipboard().set(d.v4.ip)

    def _open(self, page: str | None) -> None:
        self.hide()
        self.model.open_main_window(page)

    def sync(self) -> None:
        m = self.model
        self.service_pill.set_label("Service on" if m.background_service_running else "Service off")
        d = next((p for p in m.public_ips if p.id == "default"), None)
        if d and d.v4:
            self.flag_lbl.set_label(d.v4.flag or "")
            self.ip_lbl.set_label(d.v4.ip)
            self.loc_lbl.set_label(d.v4.location)
            self.org_lbl.set_label(d.v4.org or "")
        else:
            self.flag_lbl.set_label("")
            self.ip_lbl.set_label("Checking…" if m.public_ip_loading else "—")
            self.loc_lbl.set_label("")
            self.org_lbl.set_label("")
        self.v6_lbl.set_label(f"IPv6 {d.v6.ip}" if (d and d.v6) else "IPv6 not available")

        rate = m.internet_rate
        self.dl_lbl.set_label(format_rate(rate.rx))
        self.ul_lbl.set_label(format_rate(rate.tx))

        egress = (m.net.internet_route or {}).get("interface")
        self.egress_lbl.set_label(f"{m.display_name(egress)} ({egress})" if egress else "—")
        lan = m.local_interface
        self.local_lbl.set_label(f"{lan.primary_ipv4 or '—'} · {lan.name}" if lan else "—")
        direct = next((p for p in m.public_ips if lan and p.id == lan.name), None)
        if direct and direct.v4:
            self.direct_lbl.set_label(f"{direct.v4.flag or ''} {direct.v4.ip}".strip())
        else:
            self.direct_lbl.set_label("—")

        hs = m.net.helper_status or {}
        prof = (hs.get("profile") or {}).get("name") or "None"
        self.profile_lbl.set_label(prof)
        self.routes_lbl.set_label(str(len(hs.get("installedRoutes") or [])))
        ks = hs.get("killSwitch") or {}
        self.ks_lbl.set_label("Armed" if ks.get("active") else "Off")

        cnt = m.pending_count
        self.apply_btn.set_visible(cnt > 0)
        self.apply_btn.set_label(f"Apply {cnt} Change" + ("" if cnt == 1 else "s"))


class TrayController:
    """Native StatusNotifierItem + DBusMenu controller via Gio.DBus."""

    def __init__(self, app: Gtk.Application, model: AppModel) -> None:
        self.app = app
        self.model = model
        self.popup = TrayQuickWindow(app, model)
        self._conn: Gio.DBusConnection | None = None
        self._reg_ids: list[int] = []
        self._bus_name_id: int = 0
        self._revision = 1
        self._enabled = False

    def set_enabled(self, enabled: bool) -> None:
        if enabled == self._enabled:
            return
        self._enabled = enabled
        if enabled:
            self._start()
        else:
            self._stop()

    def _start(self) -> None:
        try:
            self._conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except Exception:
            return
        sni_info = Gio.DBusNodeInfo.new_for_xml(SNI_XML)
        menu_info = Gio.DBusNodeInfo.new_for_xml(MENU_XML)
        self._reg_ids.append(self._conn.register_object(
            "/StatusNotifierItem",
            sni_info.interfaces[0],
            self._on_sni_call,
            self._on_sni_get_prop,
            None,
        ))
        self._reg_ids.append(self._conn.register_object(
            "/MenuBar",
            menu_info.interfaces[0],
            self._on_menu_call,
            self._on_menu_get_prop,
            None,
        ))
        bus_name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        self._bus_name_id = Gio.bus_own_name_on_connection(
            self._conn,
            bus_name,
            Gio.BusNameOwnerFlags.NONE,
            lambda conn, name: self._register_watcher(name),
            None,
        )

    def _register_watcher(self, bus_name: str) -> None:
        if not self._conn:
            return
        try:
            self._conn.call(
                "org.kde.StatusNotifierWatcher",
                "/StatusNotifierWatcher",
                "org.kde.StatusNotifierWatcher",
                "RegisterStatusNotifierItem",
                GLib.Variant("(s)", (bus_name,)),
                None,
                Gio.DBusCallFlags.NONE,
                2000,
                None,
                None,
            )
        except Exception:
            pass

    def _stop(self) -> None:
        if self._conn:
            for rid in self._reg_ids:
                try:
                    self._conn.unregister_object(rid)
                except Exception:
                    pass
        self._reg_ids.clear()
        if self._bus_name_id:
            try:
                Gio.bus_unown_name(self._bus_name_id)
            except Exception:
                pass
            self._bus_name_id = 0
        self.popup.hide()

    def update(self) -> None:
        if self.popup.get_visible():
            self.popup.sync()
        if not self._enabled or not self._conn:
            return
        self._revision += 1
        try:
            self._conn.emit_signal(None, "/StatusNotifierItem", "org.kde.StatusNotifierItem", "NewToolTip", None)
            lbl = self._current_label()
            self._conn.emit_signal(
                None,
                "/StatusNotifierItem",
                "org.kde.StatusNotifierItem",
                "XAyatanaNewLabel",
                GLib.Variant("(ss)", (lbl, "↓999.9M ↑999.9M")),
            )
            self._conn.emit_signal(
                None,
                "/MenuBar",
                "com.canonical.dbusmenu",
                "LayoutUpdated",
                GLib.Variant("(ui)", (self._revision, 0)),
            )
        except Exception:
            pass

    def _current_label(self) -> str:
        style = self.model.ui_prefs.get("menuBarLabelStyle", "icon")
        d = next((p for p in self.model.public_ips if p.id == "default"), None)
        pub = d.v4 if d else None
        if style == "flag" and pub and pub.flag:
            return pub.flag
        if style == "flagAndIP" and pub:
            return f"{pub.flag or ''} {pub.ip}".strip()
        if style == "speed":
            r = self.model.internet_rate
            return f"↓{short_rate(r.rx)} ↑{short_rate(r.tx)}"
        return ""

    def _tooltip_text(self) -> tuple[str, str]:
        d = next((p for p in self.model.public_ips if p.id == "default"), None)
        egress = (self.model.net.internet_route or {}).get("interface")
        ks = (self.model.net.helper_status or {}).get("killSwitch") or {}
        r = self.model.internet_rate
        ip_str = d.v4.ip if (d and d.v4) else "Checking…"
        flag = (d.v4.flag + " ") if (d and d.v4 and d.v4.flag) else ""
        title = f"SplitRoute — {flag}{ip_str}"
        sub = f"Via {egress or '—'} · ↓{format_rate(r.rx)} ↑{format_rate(r.tx)} · Kill Switch {'Armed' if ks.get('active') else 'Off'}"
        return title, sub

    def _on_sni_call(self, _conn, _sender, _path, _iface, method, _params, invocation) -> None:
        if method in ("Activate", "SecondaryActivate"):
            GLib.idle_add(self.toggle_popup)
        invocation.return_value(None)

    def toggle_popup(self) -> None:
        if self.popup.get_visible():
            self.popup.hide()
        else:
            self.popup.sync()
            self.popup.present()

    def _on_sni_get_prop(self, _conn, _sender, _path, _iface, prop) -> GLib.Variant | None:
        icon_dir = str(ICON_PATH.parent) if ICON_PATH.exists() else ""
        icon_name = "splitroute" if ICON_PATH.exists() else "network-vpn"
        if prop == "Category":
            return GLib.Variant("s", "SystemServices")
        if prop == "Id":
            return GLib.Variant("s", "SplitRoute")
        if prop == "Title":
            return GLib.Variant("s", "SplitRoute")
        if prop == "Status":
            return GLib.Variant("s", "Active")
        if prop == "IconName":
            return GLib.Variant("s", icon_name)
        if prop == "IconThemePath":
            return GLib.Variant("s", icon_dir)
        if prop == "Menu":
            return GLib.Variant("o", "/MenuBar")
        if prop == "XAyatanaLabel":
            return GLib.Variant("s", self._current_label())
        if prop == "XAyatanaLabelGuide":
            return GLib.Variant("s", "↓999.9M ↑999.9M")
        if prop == "ToolTip":
            title, sub = self._tooltip_text()
            return GLib.Variant("(sa(iiay)ss)", (icon_name, [], title, sub))
        return None

    def _menu_items(self) -> list[tuple[int, dict[str, GLib.Variant], list]]:
        m = self.model
        d = next((p for p in m.public_ips if p.id == "default"), None)
        pub = d.v4 if d else None
        egress = (m.net.internet_route or {}).get("interface")
        ks = (m.net.helper_status or {}).get("killSwitch") or {}
        prof = (m.net.helper_status or {}).get("profile") or {}
        r = m.internet_rate
        style = m.ui_prefs.get("menuBarLabelStyle", "icon")

        def item(mid: int, label: str, enabled: bool = True, toggle_type: str = "", checked: bool = False, sub: list | None = None) -> GLib.Variant:
            props: dict[str, GLib.Variant] = {
                "label": GLib.Variant("s", label),
                "enabled": GLib.Variant("b", enabled),
                "visible": GLib.Variant("b", True),
            }
            if toggle_type:
                props["toggle-type"] = GLib.Variant("s", toggle_type)
                props["toggle-state"] = GLib.Variant("i", 1 if checked else 0)
            if sub is not None:
                props["children-display"] = GLib.Variant("s", "submenu")
            return GLib.Variant("(ia{sv}av)", (mid, props, sub or []))

        def sep(mid: int) -> GLib.Variant:
            return GLib.Variant("(ia{sv}av)", (mid, {"type": GLib.Variant("s", "separator")}, []))

        ip_text = f"Public IP: {pub.flag or ''} {pub.ip}".strip() if pub else "Public IP: —"
        loc_text = f"Location: {pub.location}" if (pub and pub.location) else "Location: —"
        spd_text = f"Speed: ↓ {format_rate(r.rx)}  ↑ {format_rate(r.tx)}"
        egr_text = f"Internet via: {egress or '—'}"
        prof_text = f"Profile: {prof.get('name') or 'None'}"
        ks_text = f"Kill switch: {'Armed' if ks.get('active') else 'Off'}"

        style_sub = [
            item(21, "Icon", True, "radio", style == "icon"),
            item(22, "Country flag", True, "radio", style == "flag"),
            item(23, "Flag and public IP", True, "radio", style == "flagAndIP"),
            item(24, "Download / upload speed", True, "radio", style == "speed"),
        ]

        items = [
            item(1, ip_text, False),
            item(2, loc_text, False),
            item(3, spd_text, False),
            item(4, f"{egr_text} · {prof_text} · {ks_text}", False),
            sep(5),
        ]
        if m.pending_count > 0:
            items.append(item(6, f"Apply {m.pending_count} Change(s)…", True))
        items += [
            item(7, "Quick Status Panel", True),
            item(8, "Open SplitRoute", True),
            item(9, "Check a Path…", True),
            item(10, "Speed Test…", True),
            item(11, "Refresh Now", True),
            sep(12),
            item(20, "Show in Tray", True, sub=style_sub),
            sep(13),
            item(14, "Quit SplitRoute", True),
        ]
        return items

    def _on_menu_call(self, _conn, _sender, _path, _iface, method, params, invocation) -> None:
        if method == "GetLayout":
            root = (0, {"children-display": GLib.Variant("s", "submenu")}, self._menu_items())
            invocation.return_value(GLib.Variant("(u(ia{sv}av))", (self._revision, root)))
        elif method == "GetGroupProperties":
            invocation.return_value(GLib.Variant("(a(ia{sv}))", ([],)))
        elif method == "GetProperty":
            invocation.return_value(GLib.Variant("(v)", (GLib.Variant("s", ""),)))
        elif method == "AboutToShow":
            invocation.return_value(GLib.Variant("(b)", (True,)))
        elif method == "Event":
            mid, ev_id, _data, _ts = params.unpack()
            if ev_id == "clicked":
                GLib.idle_add(self._handle_menu_click, mid)
            invocation.return_value(None)
        else:
            invocation.return_value(None)

    def _handle_menu_click(self, mid: int) -> None:
        if mid == 6:
            self.model.apply()
        elif mid == 7:
            self.toggle_popup()
        elif mid == 8:
            self.model.open_main_window(None)
        elif mid == 9:
            self.model.open_main_window("pathCheck")
        elif mid == 10:
            self.model.open_main_window("speedTest")
        elif mid == 11:
            self.model.refresh_all_now()
        elif mid in (21, 22, 23, 24):
            mapping = {21: "icon", 22: "flag", 23: "flagAndIP", 24: "speed"}
            self.model.set_ui_pref("menuBarLabelStyle", mapping[mid])
        elif mid == 14:
            self.app.quit()

    def _on_menu_get_prop(self, _conn, _sender, _path, _iface, prop) -> GLib.Variant | None:
        if prop == "Version":
            return GLib.Variant("u", 3)
        if prop == "Status":
            return GLib.Variant("s", "normal")
        return None

