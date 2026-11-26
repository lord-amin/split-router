from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from gi.repository import Adw, Gdk, Gtk

if TYPE_CHECKING:
    from ..app_model import AppModel

APP_CSS = """
window, .background {
    background-color: #0b0f14;
    color: #e6edf3;
}
headerbar {
    background-color: #0e141c;
    border-bottom: 1px solid #1e293b;
}
.navigation-sidebar {
    background-color: #0e141d;
}
.navigation-sidebar row {
    border-radius: 10px;
    margin: 2px 6px;
    border-left: 3px solid transparent;
}
.navigation-sidebar row:selected {
    background-color: alpha(#10b981, 0.14);
    border-left: 3px solid #10b981;
    color: #34d399;
}
.card {
    background-color: #131b26;
    border: 1px solid #223044;
    border-radius: 14px;
    box-shadow: 0 4px 16px rgba(0, 0, 0, 0.25);
}
button.suggested-action {
    background-color: #10b981;
    color: #042f2e;
    font-weight: 700;
}
button.suggested-action:hover {
    background-color: #34d399;
}
switch:checked {
    background-color: #10b981;
}
.sr-stat-tile {
    border-top: 2px solid alpha(#10b981, 0.65);
}
.sr-stat-tile-blue { border-top-color: #06b6d4; }
.sr-stat-tile-teal { border-top-color: #10b981; }
.sr-stat-tile-green { border-top-color: #22c55e; }
.sr-stat-tile-purple { border-top-color: #a855f7; }
.sr-stat-tile-orange { border-top-color: #f59e0b; }
.sr-stat-tile-indigo { border-top-color: #38bdf8; }

.bir-pill {
    border-radius: 8px;
    padding: 2px 9px;
    font-size: 0.8em;
    font-weight: 600;
    border: 1px solid transparent;
}
.bir-pill-green { background-color: alpha(#10b981, 0.16); color: #34d399; border-color: alpha(#10b981, 0.35); }
.bir-pill-blue { background-color: alpha(#06b6d4, 0.16); color: #22d3ee; border-color: alpha(#06b6d4, 0.35); }
.bir-pill-teal { background-color: alpha(#14b8a6, 0.16); color: #2dd4bf; border-color: alpha(#14b8a6, 0.35); }
.bir-pill-purple { background-color: alpha(#a855f7, 0.16); color: #c084fc; border-color: alpha(#a855f7, 0.35); }
.bir-pill-orange { background-color: alpha(#f59e0b, 0.18); color: #fbbf24; border-color: alpha(#f59e0b, 0.35); }
.bir-pill-yellow { background-color: alpha(#eab308, 0.18); color: #facc15; border-color: alpha(#eab308, 0.35); }
.bir-pill-red { background-color: alpha(#ef4444, 0.16); color: #f87171; border-color: alpha(#ef4444, 0.35); }
.bir-pill-gray { background-color: alpha(#94a3b8, 0.12); color: #94a3b8; border-color: alpha(#94a3b8, 0.22); }

.bir-banner {
    border-radius: 12px;
    padding: 12px 16px;
}
.bir-banner-orange { background-color: alpha(#f59e0b, 0.10); border: 1px solid alpha(#f59e0b, 0.35); }
.bir-banner-blue { background-color: alpha(#06b6d4, 0.10); border: 1px solid alpha(#06b6d4, 0.35); }
.bir-banner-yellow { background-color: alpha(#eab308, 0.10); border: 1px solid alpha(#eab308, 0.35); }
.bir-banner-red { background-color: alpha(#ef4444, 0.10); border: 1px solid alpha(#ef4444, 0.35); }

.bir-icon-badge {
    border-radius: 10px;
    padding: 8px;
}
.bir-badge-blue { background-color: alpha(#06b6d4, 0.15); color: #22d3ee; }
.bir-badge-teal { background-color: alpha(#10b981, 0.15); color: #34d399; }
.bir-badge-green { background-color: alpha(#22c55e, 0.15); color: #4ade80; }
.bir-badge-purple { background-color: alpha(#a855f7, 0.15); color: #c084fc; }
.bir-badge-orange { background-color: alpha(#f59e0b, 0.15); color: #fbbf24; }
.bir-badge-indigo { background-color: alpha(#38bdf8, 0.15); color: #38bdf8; }
.bir-badge-red { background-color: alpha(#ef4444, 0.15); color: #f87171; }
.bir-badge-gray { background-color: alpha(#94a3b8, 0.12); }

.bir-status-dot-green { color: #10b981; }
.bir-status-dot-yellow { color: #f59e0b; }
.bir-status-dot-gray { color: #64748b; }

.bir-speed-tile-dl {
    border-radius: 12px;
    padding: 12px;
    background-color: alpha(#06b6d4, 0.12);
    border: 1px solid alpha(#06b6d4, 0.32);
}
.bir-speed-tile-ul {
    border-radius: 12px;
    padding: 12px;
    background-color: alpha(#10b981, 0.12);
    border: 1px solid alpha(#10b981, 0.32);
}
"""

_css_installed = False


def install_css() -> None:
    global _css_installed
    if _css_installed:
        return
    display = Gdk.Display.get_default()
    if display is None:
        return
    provider = Gtk.CssProvider()
    provider.load_from_data(APP_CSS.encode("utf-8"))
    Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _css_installed = True


def clear_box(box: Gtk.Box) -> None:
    child = box.get_first_child()
    while child is not None:
        nxt = child.get_next_sibling()
        box.remove(child)
        child = nxt


def copy_to_clipboard(text: str) -> None:
    display = Gdk.Display.get_default()
    if display:
        display.get_clipboard().set(text)


def make_pill(text: str, tone: str = "gray") -> Gtk.Label:
    lbl = Gtk.Label(label=text)
    lbl.add_css_class("bir-pill")
    lbl.add_css_class(f"bir-pill-{tone}")
    lbl.set_valign(Gtk.Align.CENTER)
    return lbl


def make_card(
    title: str | None = None,
    icon_name: str | None = None,
    trailing: Gtk.Widget | None = None,
) -> tuple[Gtk.Box, Gtk.Box]:
    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    card.add_css_class("card")
    inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    inner.set_margin_top(16)
    inner.set_margin_bottom(16)
    inner.set_margin_start(16)
    inner.set_margin_end(16)
    card.append(inner)

    if title is not None or trailing is not None:
        hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        if icon_name:
            img = Gtk.Image.new_from_icon_name(icon_name)
            img.add_css_class("dim-label")
            hdr.append(img)
        if title:
            lbl = Gtk.Label(label=title, xalign=0)
            lbl.add_css_class("heading")
            lbl.set_hexpand(True)
            hdr.append(lbl)
        else:
            sp = Gtk.Box()
            sp.set_hexpand(True)
            hdr.append(sp)
        if trailing is not None:
            hdr.append(trailing)
        inner.append(hdr)
    return card, inner


def make_page_header(title: str, subtitle: str, trailing: Gtk.Widget | None = None) -> Gtk.Box:
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    col.set_hexpand(True)
    t = Gtk.Label(label=title, xalign=0)
    t.add_css_class("title-1")
    s = Gtk.Label(label=subtitle, xalign=0)
    s.add_css_class("dim-label")
    s.set_wrap(True)
    col.append(t)
    col.append(s)
    row.append(col)
    if trailing is not None:
        trailing.set_valign(Gtk.Align.CENTER)
        row.append(trailing)
    return row


def make_stat_tile(
    title: str,
    value: str,
    detail: str,
    icon_name: str,
    tone: str = "blue",
    badge: str | None = None,
) -> Gtk.Box:
    card, inner = make_card()
    card.add_css_class("sr-stat-tile")
    card.add_css_class(f"sr-stat-tile-{tone}")
    card.set_hexpand(True)
    inner.set_spacing(6)
    top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    icon_wrap = Gtk.Box()
    icon_wrap.add_css_class("bir-icon-badge")
    icon_wrap.add_css_class(f"bir-badge-{tone}")
    icon_wrap.append(Gtk.Image.new_from_icon_name(icon_name))
    top.append(icon_wrap)
    sp = Gtk.Box()
    sp.set_hexpand(True)
    top.append(sp)
    if badge:
        b_lbl = Gtk.Label(label=badge)
        b_lbl.add_css_class("title-1")
        top.append(b_lbl)
    inner.append(top)

    t_lbl = Gtk.Label(label=title.upper(), xalign=0)
    t_lbl.add_css_class("caption")
    t_lbl.add_css_class("dim-label")
    v_lbl = Gtk.Label(label=value, xalign=0)
    v_lbl.add_css_class("title-2")
    v_lbl.add_css_class("numeric")
    v_lbl.set_selectable(True)
    v_lbl.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
    d_lbl = Gtk.Label(label=detail, xalign=0)
    d_lbl.add_css_class("caption")
    d_lbl.add_css_class("dim-label")
    d_lbl.set_wrap(True)
    d_lbl.set_lines(2)
    inner.append(t_lbl)
    inner.append(v_lbl)
    inner.append(d_lbl)
    return card


def make_kv_row(key: str, value: str, mono: bool = True) -> Gtk.Box:
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    k = Gtk.Label(label=key, xalign=0)
    k.add_css_class("dim-label")
    k.set_size_request(130, -1)
    v = Gtk.Label(label=value if value else "—", xalign=0)
    v.set_selectable(True)
    v.set_wrap(True)
    v.set_hexpand(True)
    if mono:
        v.add_css_class("monospace")
    row.append(k)
    row.append(v)
    return row


def make_banner(
    title: str,
    message: str,
    tone: str = "orange",
    icon: str = "dialog-warning-symbolic",
    actions: list[Gtk.Widget] | None = None,
) -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    box.add_css_class("bir-banner")
    box.add_css_class(f"bir-banner-{tone}")
    img = Gtk.Image.new_from_icon_name(icon)
    img.set_pixel_size(24)
    box.append(img)
    col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    col.set_hexpand(True)
    t = Gtk.Label(label=title, xalign=0)
    t.add_css_class("heading")
    m = Gtk.Label(label=message, xalign=0)
    m.add_css_class("caption")
    m.add_css_class("dim-label")
    m.set_wrap(True)
    col.append(t)
    col.append(m)
    box.append(col)
    if actions:
        act_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        act_box.set_valign(Gtk.Align.CENTER)
        for w in actions:
            act_box.append(w)
        box.append(act_box)
    return box


def rule_state_pill(status: dict | None, pending: bool, live: object | None = None) -> Gtk.Label:
    if pending:
        if live is not None and getattr(live, "all_live", False):
            return make_pill("Live · not managed", "blue")
        if live is not None and getattr(live, "live", 0) > 0:
            return make_pill(f"Partly live {live.live}/{live.expected}", "orange")
        return make_pill("Not applied", "orange")
    if status is None:
        return make_pill("Unknown", "gray")
    st = status.get("state", "")
    routes = status.get("routes") or []
    expected = status.get("expected", 0)
    if st == "applied":
        return make_pill(f"Active · {len(routes)}", "green")
    if st == "partial":
        return make_pill(f"Partial {len(routes)}/{expected}", "yellow")
    if st == "failed":
        return make_pill("Failed", "red")
    if st == "waiting":
        return make_pill("Waiting", "blue")
    if st == "disabled":
        return make_pill("Disabled", "gray")
    return make_pill("Unknown", "gray")


class PendingBanner(Gtk.Box):
    def __init__(self, model: AppModel, parent_window: Callable[[], Gtk.Window | None]) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.model = model
        self.parent_window = parent_window

    def refresh(self) -> None:
        clear_box(self)
        if not self.model.has_pending:
            self.set_visible(False)
            return
        self.set_visible(True)
        changes = self.model.pending_changes
        items = [f"{c.kind}: {c.rule.name}" for c in changes[:4]]
        if self.model.kill_switch_pending:
            items.append("Kill switch settings")
        if self.model.automation_pending:
            items.append("Profiles, triggers or DNS")
        msg = " · ".join(items)
        live_unmanaged = self.model.live_but_unmanaged
        if live_unmanaged:
            msg += (
                f"\nThe routes of {len(live_unmanaged)} of these already exist on this system. "
                "Applying takes them over without interrupting traffic and makes them persistent."
            )

        discard_btn = Gtk.Button(label="Discard…")
        discard_btn.connect("clicked", self._confirm_discard)
        apply_btn = Gtk.Button(label="Apply Changes")
        apply_btn.add_css_class("suggested-action")
        apply_btn.connect("clicked", lambda *_: self.model.apply())

        cnt = self.model.pending_count
        banner = make_banner(
            f"{cnt} unapplied change" + ("" if cnt == 1 else "s"),
            msg,
            tone="orange",
            actions=[discard_btn, apply_btn],
        )
        self.append(banner)

    def _confirm_discard(self, *_args) -> None:
        cnt = self.model.pending_count
        dlg = Adw.MessageDialog(
            transient_for=self.parent_window(),
            heading=f"Discard {cnt} unapplied change" + ("" if cnt == 1 else "s") + "?",
            body="Your edits go back to what is applied on this machine. This cannot be undone.",
        )
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("discard", "Discard Changes")
        dlg.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", lambda _d, resp: self.model.discard_changes() if resp == "discard" else None)
        dlg.present()

