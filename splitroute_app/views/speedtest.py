from __future__ import annotations

import math
from typing import TYPE_CHECKING

import cairo
from gi.repository import Adw, GLib, Gtk

from ..models import IPIdentity, SpeedHistoryStore, parse_dt
from ..speedtest import SpeedPath
from .widgets import clear_box, make_card, make_page_header

if TYPE_CHECKING:
    from ..app_model import AppModel


class SpeedDialWidget(Gtk.DrawingArea):
    """270° logarithmic speedometer gauge for SplitRoute."""

    MBPS_MARKS = [0, 1, 5, 10, 25, 50, 100, 250, 500, 1000]
    MB_MARKS = [x * 8.0 for x in (0, 0.1, 0.5, 1, 2.5, 5, 10, 25, 50, 125)]

    def __init__(self) -> None:
        super().__init__()
        self.mbps = 0.0
        self.unit = "mbps"
        self.style = "idle"  # idle | download | upload | done
        self.active = False
        self.set_content_width(310)
        self.set_content_height(280)
        self.set_draw_func(self._draw)

    @staticmethod
    def fraction(v: float) -> float:
        if v <= 0:
            return 0.0
        return min(1.0, math.log10(1.0 + v) / math.log10(1001.0))

    @staticmethod
    def fmt_value(mbps: float, unit: str) -> str:
        v = mbps if unit == "mbps" else mbps / 8.0
        if v >= 100:
            return f"{v:.0f}"
        if v >= 10:
            return f"{v:.1f}"
        return f"{v:.2f}"

    def _colors(self) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        if self.style == "upload":
            return (0.06, 0.73, 0.51), (0.20, 0.83, 0.60)
        if self.style in ("download", "done"):
            return (0.02, 0.71, 0.83), (0.06, 0.73, 0.51)
        return (0.39, 0.45, 0.55), (0.28, 0.33, 0.41)

    def _draw(self, _area: Gtk.DrawingArea, ctx: cairo.Context, w: int, h: int) -> None:
        size = min(w, h)
        cx, cy = w / 2.0, h / 2.0
        radius = size / 2.0 - 24.0
        start_deg = 135.0
        sweep_deg = 270.0
        f = self.fraction(self.mbps)
        c1, c2 = self._colors()
        mid_c = ((c1[0] + c2[0]) / 2, (c1[1] + c2[1]) / 2, (c1[2] + c2[2]) / 2)

        # Backdrop radial glow
        rad = cairo.RadialGradient(cx, cy, 0, cx, cy, size / 2.0)
        rad.add_color_stop_rgba(0, *mid_c, 0.16 if self.active else 0.06)
        rad.add_color_stop_rgba(1, *mid_c, 0.0)
        ctx.set_source(rad)
        ctx.arc(cx, cy, size / 2.0, 0, 2 * math.pi)
        ctx.fill()

        a_start = math.radians(start_deg)
        a_end = math.radians(start_deg + sweep_deg)

        # Track arc
        ctx.set_line_cap(cairo.LINE_CAP_ROUND)
        ctx.set_line_width(14)
        ctx.set_source_rgba(0.5, 0.5, 0.55, 0.14)
        ctx.arc(cx, cy, radius, a_start, a_end)
        ctx.stroke()

        # Value arc + soft glow
        if f > 0.001:
            a_val = math.radians(start_deg + sweep_deg * f)
            pat = cairo.LinearGradient(cx - radius, cy, cx + radius, cy)
            pat.add_color_stop_rgb(0, *c1)
            pat.add_color_stop_rgb(1, *c2)

            ctx.set_line_width(22)
            ctx.set_source_rgba(*mid_c, 0.25 if self.active else 0.12)
            ctx.arc(cx, cy, radius, a_start, a_val)
            ctx.stroke()

            ctx.set_line_width(14)
            ctx.set_source(pat)
            ctx.arc(cx, cy, radius, a_start, a_val)
            ctx.stroke()

        # Ticks
        for i in range(61):
            t = i / 60.0
            ang = math.radians(start_deg + sweep_deg * t)
            r1, r2 = radius - 14, radius - 19
            lit = t <= f + 0.0001 and f > 0
            ctx.set_line_width(1.0)
            ctx.set_source_rgba(*(mid_c + (0.85,)) if lit else (0.55, 0.55, 0.60, 0.22))
            ctx.move_to(cx + math.cos(ang) * r1, cy + math.sin(ang) * r1)
            ctx.line_to(cx + math.cos(ang) * r2, cy + math.sin(ang) * r2)
            ctx.stroke()

        marks = self.MBPS_MARKS if self.unit == "mbps" else self.MB_MARKS
        ctx.set_font_size(10)
        for m in marks:
            t = self.fraction(m)
            ang = math.radians(start_deg + sweep_deg * t)
            r1, r2 = radius - 14, radius - 25
            lit = t <= f + 0.0001 and f > 0
            ctx.set_line_width(2.2)
            ctx.set_source_rgba(*(mid_c + (1.0,)) if lit else (0.55, 0.55, 0.60, 0.50))
            ctx.move_to(cx + math.cos(ang) * r1, cy + math.sin(ang) * r1)
            ctx.line_to(cx + math.cos(ang) * r2, cy + math.sin(ang) * r2)
            ctx.stroke()

            if self.unit == "mbps":
                lbl = "1G" if m >= 1000 else str(int(m))
            else:
                v = m / 8.0
                lbl = f"{v:.1f}" if 0 < v < 1 else (str(int(v)) if v == round(v) else f"{v:.1f}")
            lx = cx + math.cos(ang) * (radius - 38)
            ly = cy + math.sin(ang) * (radius - 38)
            ext = ctx.text_extents(lbl)
            ctx.set_source_rgba(0.65, 0.65, 0.72, 0.95 if lit else 0.70)
            ctx.move_to(lx - ext.width / 2, ly + ext.height / 2)
            ctx.show_text(lbl)

        # Tapered needle
        n_ang = math.radians(start_deg + sweep_deg * f)
        n_len = radius - 28
        ctx.save()
        ctx.translate(cx, cy)
        ctx.rotate(n_ang)
        ctx.move_to(0, -4.5)
        ctx.line_to(n_len, -1.0)
        ctx.line_to(n_len, 1.0)
        ctx.line_to(0, 4.5)
        ctx.close_path()
        n_pat = cairo.LinearGradient(0, 0, n_len, 0)
        n_pat.add_color_stop_rgba(0, *mid_c, 0.25)
        n_pat.add_color_stop_rgba(1, *mid_c, 1.0)
        ctx.set_source(n_pat)
        ctx.fill()
        ctx.restore()

        # Center hub
        ctx.arc(cx, cy, 10, 0, 2 * math.pi)
        ctx.set_source_rgb(0.08, 0.11, 0.15)
        ctx.fill_preserve()
        ctx.set_line_width(3)
        ctx.set_source_rgb(*mid_c)
        ctx.stroke()

        # Readout
        val_txt = self.fmt_value(self.mbps, self.unit)
        unit_txt = "Mbps" if self.unit == "mbps" else "MB/s"
        other_unit = "MB/s" if self.unit == "mbps" else "Mbps"
        other_txt = f"{self.fmt_value(self.mbps, 'mBps' if self.unit == 'mbps' else 'mbps')} {other_unit}"

        ctx.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        ctx.set_font_size(32)
        ext = ctx.text_extents(val_txt)
        alpha = 0.95 if (self.active or self.mbps > 0) else 0.45
        ctx.set_source_rgba(0.85, 0.87, 0.92, alpha)
        ctx.move_to(cx - ext.width / 2, cy + radius * 0.60)
        ctx.show_text(val_txt)

        ctx.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        ctx.set_font_size(13)
        u_ext = ctx.text_extents(unit_txt)
        ctx.set_source_rgba(0.65, 0.68, 0.74, alpha)
        ctx.move_to(cx - u_ext.width / 2, cy + radius * 0.60 + 20)
        ctx.show_text(unit_txt)

        ctx.set_font_size(11)
        o_ext = ctx.text_extents(other_txt)
        ctx.set_source_rgba(0.55, 0.58, 0.64, alpha * 0.8)
        ctx.move_to(cx - o_ext.width / 2, cy + radius * 0.60 + 36)
        ctx.show_text(other_txt)


class SparklineWidget(Gtk.DrawingArea):
    def __init__(self) -> None:
        super().__init__()
        self.values: list[float] = []
        self.color = (0.25, 0.45, 1.0)
        self.set_content_height(46)
        self.set_draw_func(self._draw)

    def _draw(self, _area: Gtk.DrawingArea, ctx: cairo.Context, w: int, h: int) -> None:
        if len(self.values) < 2:
            return
        mx = max(max(self.values), 1.0) * 1.1
        n = max(10, len(self.values) - 1)
        coords = [(i / n * w, h - (v / mx) * (h - 6) - 3) for i, v in enumerate(self.values)]
        r, g, b = self.color
        ctx.move_to(coords[0][0], h)
        for x, y in coords:
            ctx.line_to(x, y)
        ctx.line_to(coords[-1][0], h)
        ctx.close_path()
        ctx.set_source_rgba(r, g, b, 0.25)
        ctx.fill()

        ctx.move_to(coords[0][0], coords[0][1])
        for x, y in coords[1:]:
            ctx.line_to(x, y)
        ctx.set_source_rgba(r, g, b, 0.95)
        ctx.set_line_width(2.0)
        ctx.stroke()


class SpeedTestView(Gtk.ScrolledWindow):
    def __init__(self, model: AppModel, get_window) -> None:
        super().__init__()
        self.model = model
        self.get_window = get_window
        self.paths: list[SpeedPath] = []
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        root.set_margin_top(24)
        root.set_margin_bottom(24)
        root.set_margin_start(24)
        root.set_margin_end(24)
        self.set_child(root)

        # Unit toggle
        unit_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        unit_box.add_css_class("linked")
        self.mbps_btn = Gtk.ToggleButton(label="Mbps")
        self.mb_btn = Gtk.ToggleButton(label="MB/s")
        self.mb_btn.set_group(self.mbps_btn)
        cur_unit = self.model.ui_prefs.get("speedTestUnit", "mbps")
        self.mbps_btn.set_active(cur_unit == "mbps")
        self.mb_btn.set_active(cur_unit == "mBps")
        self.mbps_btn.connect("toggled", lambda b: self._set_unit("mbps") if b.get_active() else None)
        self.mb_btn.connect("toggled", lambda b: self._set_unit("mBps") if b.get_active() else None)
        unit_box.append(self.mbps_btn)
        unit_box.append(self.mb_btn)
        root.append(make_page_header("Speed Test", "Download, upload, ping and jitter — on the connection you choose", unit_box))

        # Controls card
        ctrl_card, ctrl_inner = make_card()
        ctrl_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        ctrl_left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        ctrl_left.set_hexpand(True)
        self.path_combo = Gtk.ComboBoxText()
        self.path_combo.connect("changed", self._on_path_changed)
        ctrl_left.append(self.path_combo)
        self.path_detail = Gtk.Label(label="", xalign=0)
        self.path_detail.add_css_class("caption")
        self.path_detail.add_css_class("dim-label")
        self.path_detail.set_wrap(True)
        ctrl_left.append(self.path_detail)

        srv_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        eng_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        eng_box.add_css_class("linked")
        self.cf_btn = Gtk.ToggleButton(label="Cloudflare")
        self.ls_btn = Gtk.ToggleButton(label="LibreSpeed")
        self.ls_btn.set_group(self.cf_btn)
        cur_eng = self.model.ui_prefs.get("speedTestEngine", "cloudflare")
        self.cf_btn.set_active(cur_eng == "cloudflare")
        self.ls_btn.set_active(cur_eng == "libreSpeed")
        self.cf_btn.connect("toggled", lambda b: self._set_engine("cloudflare") if b.get_active() else None)
        self.ls_btn.connect("toggled", lambda b: self._set_engine("libreSpeed") if b.get_active() else None)
        eng_box.append(self.cf_btn)
        eng_box.append(self.ls_btn)
        srv_row.append(eng_box)

        self.libre_combo = Gtk.ComboBoxText()
        self.libre_combo.append("auto", "Fastest server (automatic)")
        self.libre_combo.set_active_id("auto")
        self.libre_combo.connect("changed", lambda c: self.model.set_ui_pref("speedTestLibreServer", c.get_active_id() or "auto"))
        srv_row.append(self.libre_combo)
        ctrl_left.append(srv_row)

        warn = Gtk.Label(label="Uses roughly 100–300 MB of data per test.", xalign=0)
        warn.add_css_class("caption")
        warn.add_css_class("dim-label")
        ctrl_left.append(warn)
        ctrl_row.append(ctrl_left)

        self.start_btn = Gtk.Button(label="Start Test")
        self.start_btn.add_css_class("suggested-action")
        self.start_btn.set_valign(Gtk.Align.CENTER)
        self.start_btn.connect("clicked", self._on_start_or_stop)
        ctrl_row.append(self.start_btn)
        ctrl_inner.append(ctrl_row)
        root.append(ctrl_card)

        # Dial + Results
        mid = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        dial_card, dial_inner = make_card()
        dial_card.set_size_request(370, -1)
        self.phase_lbl = Gtk.Label(label="Ping  ·  Download  ·  Upload")
        self.phase_lbl.add_css_class("caption")
        self.phase_lbl.add_css_class("dim-label")
        dial_inner.append(self.phase_lbl)
        self.dial = SpeedDialWidget()
        dial_inner.append(self.dial)
        self.status_lbl = Gtk.Label(label="Ready when you are")
        self.status_lbl.add_css_class("heading")
        dial_inner.append(self.status_lbl)
        self.server_lbl = Gtk.Label(label="")
        self.server_lbl.add_css_class("caption")
        self.server_lbl.add_css_class("dim-label")
        dial_inner.append(self.server_lbl)
        self.spark = SparklineWidget()
        dial_inner.append(self.spark)
        self.progress_bar = Gtk.ProgressBar()
        dial_inner.append(self.progress_bar)
        self.err_lbl = Gtk.Label(label="")
        self.err_lbl.add_css_class("caption")
        self.err_lbl.set_wrap(True)
        dial_inner.append(self.err_lbl)
        mid.append(dial_card)

        res_card, res_inner = make_card("Result", "utilities-system-monitor-symbolic")
        res_card.set_hexpand(True)
        dl_ul_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        dl_ul_row.set_homogeneous(True)
        self.dl_val, self.dl_unit, self.dl_other = self._make_result_tile(dl_ul_row, "Download", "bir-speed-tile-dl")
        self.ul_val, self.ul_unit, self.ul_other = self._make_result_tile(dl_ul_row, "Upload", "bir-speed-tile-ul")
        res_inner.append(dl_ul_row)

        metrics_grid = Gtk.Grid(column_spacing=12, row_spacing=10)
        metrics_grid.set_column_homogeneous(True)
        self.ping_lbl = self._make_metric(metrics_grid, 0, 0, "Ping")
        self.jitter_lbl = self._make_metric(metrics_grid, 1, 0, "Jitter")
        self.loaded_lbl = self._make_metric(metrics_grid, 0, 1, "Ping under load")
        self.bloat_lbl = self._make_metric(metrics_grid, 1, 1, "Bufferbloat")
        res_inner.append(metrics_grid)
        res_inner.append(Gtk.Separator())
        self.ident_lbl = Gtk.Label(label="The public IP of the tested connection appears here.", xalign=0)
        self.ident_lbl.add_css_class("caption")
        self.ident_lbl.add_css_class("dim-label")
        self.ident_lbl.set_wrap(True)
        res_inner.append(self.ident_lbl)
        mid.append(res_card)
        root.append(mid)

        # History
        clr_btn = Gtk.Button(label="Clear")
        clr_btn.connect("clicked", self._clear_history)
        self.hist_card, self.hist_inner = make_card("History", "document-open-recent-symbolic", clr_btn)
        root.append(self.hist_card)

        self._update_engine_ui()

    def _make_result_tile(self, parent: Gtk.Box, title: str, css_cls: str) -> tuple[Gtk.Label, Gtk.Label, Gtk.Label]:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.add_css_class(css_cls)
        t = Gtk.Label(label=title, xalign=0)
        t.add_css_class("heading")
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        val = Gtk.Label(label="—", xalign=0)
        val.add_css_class("title-1")
        val.add_css_class("numeric")
        unit = Gtk.Label(label="Mbps", xalign=0)
        unit.add_css_class("dim-label")
        unit.set_valign(Gtk.Align.BASELINE)
        row.append(val)
        row.append(unit)
        other = Gtk.Label(label="", xalign=0)
        other.add_css_class("caption")
        other.add_css_class("dim-label")
        box.append(t)
        box.append(row)
        box.append(other)
        parent.append(box)
        return val, unit, other

    def _make_metric(self, grid: Gtk.Grid, col: int, row: int, title: str) -> Gtk.Label:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.add_css_class("card")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        inner.set_margin_top(10)
        inner.set_margin_bottom(10)
        inner.set_margin_start(12)
        inner.set_margin_end(12)
        t = Gtk.Label(label=title, xalign=0)
        t.add_css_class("caption")
        t.add_css_class("dim-label")
        v = Gtk.Label(label="—", xalign=0)
        v.add_css_class("title-3")
        v.add_css_class("numeric")
        inner.append(t)
        inner.append(v)
        box.append(inner)
        grid.attach(box, col, row, 1, 1)
        return v

    def _set_unit(self, u: str) -> None:
        self.model.set_ui_pref("speedTestUnit", u)
        self.refresh()

    def _set_engine(self, eng: str) -> None:
        self.model.set_ui_pref("speedTestEngine", eng)
        self._update_engine_ui()

    def _update_engine_ui(self) -> None:
        eng = self.model.ui_prefs.get("speedTestEngine", "cloudflare")
        self.libre_combo.set_visible(eng == "libreSpeed")
        if eng == "libreSpeed" and not self.model.speed_test.libre_servers:
            self.model.speed_test.load_libre_servers(lambda: GLib.idle_add(self._populate_libre))

    def _populate_libre(self) -> None:
        cur = self.model.ui_prefs.get("speedTestLibreServer", "auto")
        self.libre_combo.remove_all()
        self.libre_combo.append("auto", "Fastest server (automatic)")
        for s in self.model.speed_test.libre_servers:
            self.libre_combo.append(s.id, s.name)
        self.libre_combo.set_active_id(cur)
        if not self.libre_combo.get_active_id():
            self.libre_combo.set_active_id("auto")

    def _on_path_changed(self, combo: Gtk.ComboBoxText) -> None:
        pid = combo.get_active_id()
        if pid:
            self.model.set_ui_pref("speedTestPath", pid)
        p = self._selected_path()
        self.path_detail.set_label(p.detail if p else "")

    def _selected_path(self) -> SpeedPath | None:
        pid = self.model.ui_prefs.get("speedTestPath", "direct")
        return next((p for p in self.paths if p.id == pid), self.paths[0] if self.paths else None)

    def _on_start_or_stop(self, *_args) -> None:
        r = self.model.speed_test
        if r.is_running:
            r.stop()
            self.refresh()
            return
        if self.model.ui_prefs.get("speedTestSkipDataWarning"):
            self._begin()
            return
        p = self._selected_path()
        dlg = Adw.MessageDialog(
            transient_for=self.get_window(),
            heading="This test uses a lot of data",
            body=(
                f"A speed test uses roughly 100–300 MB of data. "
                f"If {p.title if p else 'this connection'} is metered or goes through a VPN with a data limit, that usage counts against it."
            ),
        )
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("once", "Start Test")
        dlg.add_response("always", "Start and Don't Ask Again")
        dlg.set_response_appearance("once", Adw.ResponseAppearance.SUGGESTED)
        dlg.connect("response", self._on_data_warn_resp)
        dlg.present()

    def _on_data_warn_resp(self, _dlg, resp: str) -> None:
        if resp == "always":
            self.model.set_ui_pref("speedTestSkipDataWarning", True)
            self._begin()
        elif resp == "once":
            self._begin()

    def _begin(self) -> None:
        p = self._selected_path()
        if not p:
            return
        eng = self.model.ui_prefs.get("speedTestEngine", "cloudflare")
        ls = self.model.ui_prefs.get("speedTestLibreServer", "auto")
        self.model.speed_test.start(
            engine=eng,
            libre_server_id=None if ls == "auto" else ls,
            path=p,
            geo_provider=self.model.workspace.settings.geo_provider,
            on_update=lambda: GLib.idle_add(self.refresh),
        )
        self.model.log("Speed test started", True, f"{eng} on {p.title}")

    def _clear_history(self, *_args) -> None:
        self.model.speed_test.history.clear()
        SpeedHistoryStore.save([])
        self.refresh()

    def refresh(self) -> None:
        r = self.model.speed_test
        unit = self.model.ui_prefs.get("speedTestUnit", "mbps")

        # Update path list if not running
        if not r.is_running:
            self.paths = self.model.speed_test_paths()
            cur = self.model.ui_prefs.get("speedTestPath", "direct")
            self.path_combo.handler_block_by_func(self._on_path_changed)
            self.path_combo.remove_all()
            for p in self.paths:
                self.path_combo.append(p.id, p.title)
            self.path_combo.set_active_id(cur if any(p.id == cur for p in self.paths) else (self.paths[0].id if self.paths else None))
            self.path_combo.handler_unblock_by_func(self._on_path_changed)
            sel = self._selected_path()
            self.path_detail.set_label(sel.detail if sel else "")

        for w in (self.path_combo, self.cf_btn, self.ls_btn, self.libre_combo):
            w.set_sensitive(not r.is_running)

        if r.is_running:
            self.start_btn.set_label("Stop")
            self.start_btn.remove_css_class("suggested-action")
            self.start_btn.add_css_class("destructive-action")
        else:
            self.start_btn.set_label("Test Again" if r.phase == "done" else "Start Test")
            self.start_btn.remove_css_class("destructive-action")
            self.start_btn.add_css_class("suggested-action")

        # Dial & Sparkline
        self.dial.unit = unit
        self.dial.active = r.is_running
        if r.phase == "upload":
            self.dial.style = "upload"
            self.dial.mbps = r.live_mbps
            self.spark.color = (0.06, 0.73, 0.51)
        elif r.phase == "download":
            self.dial.style = "download"
            self.dial.mbps = r.live_mbps
            self.spark.color = (0.02, 0.71, 0.83)
        elif r.phase == "done":
            self.dial.style = "done"
            self.dial.mbps = r.download_mbps or 0.0
            self.spark.color = (0.02, 0.71, 0.83)
        else:
            self.dial.style = "idle"
            self.dial.mbps = 0.0
        self.dial.queue_draw()

        spark_vals = r.samples if r.phase in ("download", "upload") else (r.download_samples if r.phase == "done" else [])
        self.spark.values = spark_vals
        self.spark.set_visible(bool(spark_vals))
        self.spark.queue_draw()

        self.progress_bar.set_visible(r.is_running)
        self.progress_bar.set_fraction(r.progress)

        status_map = {
            "idle": "Ready when you are",
            "server": "Finding a server…",
            "ping": "Measuring ping…",
            "download": "Testing download…",
            "upload": "Testing upload…",
            "done": "Download result",
            "failed": "Test failed",
            "cancelled": "Stopped",
        }
        self.status_lbl.set_label(status_map.get(r.phase, ""))
        self.server_lbl.set_label(r.server_name or "")
        self.err_lbl.set_visible(bool(r.error))
        self.err_lbl.set_label(r.error or "")

        # Results
        u_lbl = "Mbps" if unit == "mbps" else "MB/s"
        o_unit = "mBps" if unit == "mbps" else "mbps"
        o_lbl = "MB/s" if unit == "mbps" else "Mbps"
        self.dl_unit.set_label(u_lbl)
        self.ul_unit.set_label(u_lbl)
        if r.download_mbps is not None:
            self.dl_val.set_label(SpeedDialWidget.fmt_value(r.download_mbps, unit))
            self.dl_other.set_label(f"{SpeedDialWidget.fmt_value(r.download_mbps, o_unit)} {o_lbl}")
        else:
            self.dl_val.set_label("—")
            self.dl_other.set_label("")
        if r.upload_mbps is not None:
            self.ul_val.set_label(SpeedDialWidget.fmt_value(r.upload_mbps, unit))
            self.ul_other.set_label(f"{SpeedDialWidget.fmt_value(r.upload_mbps, o_unit)} {o_lbl}")
        else:
            self.ul_val.set_label("—")
            self.ul_other.set_label("")

        self.ping_lbl.set_label(f"{r.ping_ms:.0f} ms" if r.ping_ms is not None else "—")
        self.jitter_lbl.set_label(f"{r.jitter_ms:.1f} ms" if r.jitter_ms is not None else "—")
        self.loaded_lbl.set_label(f"{r.loaded_ping_ms:.0f} ms" if r.loaded_ping_ms is not None else "—")
        if r.ping_ms is not None and r.loaded_ping_ms is not None:
            extra = max(0.0, r.loaded_ping_ms - r.ping_ms)
            grade = "A" if extra < 30 else ("B" if extra < 100 else ("C" if extra < 300 else "D"))
            self.bloat_lbl.set_label(f"{grade}  +{int(extra)} ms")
        else:
            self.bloat_lbl.set_label("—")

        if r.identity:
            flag = f"{r.identity.flag} " if r.identity.flag else ""
            det = " · ".join(x for x in (r.identity.location, r.identity.org or "") if x)
            self.ident_lbl.set_label(f"{flag}{r.identity.ip}  —  {det}" if det else f"{flag}{r.identity.ip}")
        else:
            self.ident_lbl.set_label("Looking up the public IP of this connection…" if r.is_running else "The public IP of the tested connection appears here.")

        # History table
        clear_box(self.hist_inner)
        if not r.history:
            lbl = Gtk.Label(label="Past results appear here, so you can compare the VPN and the direct connection over time.", xalign=0)
            lbl.add_css_class("dim-label")
            self.hist_inner.append(lbl)
        else:
            grid = Gtk.Grid(column_spacing=16, row_spacing=6)
            for c, h in enumerate(["When", "Connection", "Server", "Down", "Up", "Ping", "IP"]):
                hl = Gtk.Label(label=h, xalign=0)
                hl.add_css_class("caption")
                hl.add_css_class("dim-label")
                grid.attach(hl, c, 0, 1, 1)
            for row_idx, item in enumerate(reversed(r.history[-15:]), start=1):
                dt = parse_dt(item.get("date")).astimezone().strftime("%b %d %H:%M")
                grid.attach(Gtk.Label(label=dt, xalign=0), 0, row_idx, 1, 1)
                grid.attach(Gtk.Label(label=item.get("pathTitle", ""), xalign=0), 1, row_idx, 1, 1)
                grid.attach(Gtk.Label(label=item.get("engine", ""), xalign=0), 2, row_idx, 1, 1)
                grid.attach(Gtk.Label(label=f"{SpeedDialWidget.fmt_value(item.get('downloadMbps', 0), unit)} {u_lbl}", xalign=0), 3, row_idx, 1, 1)
                grid.attach(Gtk.Label(label=f"{SpeedDialWidget.fmt_value(item.get('uploadMbps', 0), unit)} {u_lbl}", xalign=0), 4, row_idx, 1, 1)
                grid.attach(Gtk.Label(label=f"{int(item.get('pingMs', 0))} ms", xalign=0), 5, row_idx, 1, 1)
                flag = IPIdentity(ip=item.get("publicIP") or "", country=item.get("country")).flag or ""
                grid.attach(Gtk.Label(label=f"{flag} {item.get('publicIP') or '—'}".strip(), xalign=0), 6, row_idx, 1, 1)
            self.hist_inner.append(grid)

