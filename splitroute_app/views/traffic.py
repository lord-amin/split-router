from __future__ import annotations

import time
from typing import TYPE_CHECKING

import cairo
from gi.repository import Gtk

from ..models import format_bytes, format_rate
from .widgets import clear_box, make_card, make_page_header

if TYPE_CHECKING:
    from ..app_model import AppModel

SERIES_COLORS = [
    (0.06, 0.73, 0.51),
    (0.02, 0.71, 0.83),
    (0.66, 0.33, 0.97),
    (0.96, 0.62, 0.04),
    (0.94, 0.27, 0.27),
    (0.22, 0.74, 0.97),
]


class TrafficView(Gtk.ScrolledWindow):
    RANGES = [("15 min", 900, False), ("1 hour", 3600, False), ("24 hours", 86400, True), ("7 days", 7 * 86400, True)]

    def __init__(self, model: AppModel) -> None:
        super().__init__()
        self.model = model
        self.range_idx = 1  # 1 hour
        self.direction = "both"  # both | down | up
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        root.set_margin_top(24)
        root.set_margin_bottom(24)
        root.set_margin_start(24)
        root.set_margin_end(24)
        self.set_child(root)

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.dir_combo = Gtk.ComboBoxText()
        self.dir_combo.append("both", "Down + up")
        self.dir_combo.append("down", "Download")
        self.dir_combo.append("up", "Upload")
        self.dir_combo.set_active_id("both")
        self.dir_combo.connect("changed", self._on_dir_changed)
        controls.append(self.dir_combo)

        self.range_btns: list[Gtk.ToggleButton] = []
        r_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        r_box.add_css_class("linked")
        first: Gtk.ToggleButton | None = None
        for idx, (label, _, _) in enumerate(self.RANGES):
            btn = Gtk.ToggleButton(label=label)
            if first is None:
                first = btn
            else:
                btn.set_group(first)
            if idx == self.range_idx:
                btn.set_active(True)
            btn.connect("toggled", self._on_range_toggled, idx)
            self.range_btns.append(btn)
            r_box.append(btn)
        controls.append(r_box)

        root.append(make_page_header("Traffic", "How much data each interface carries over time", controls))

        chart_card, chart_inner = make_card("Throughput", "utilities-system-monitor-symbolic")
        self.chart_msg = Gtk.Label(label="", xalign=0)
        self.chart_msg.add_css_class("dim-label")
        chart_inner.append(self.chart_msg)
        self.chart_area = Gtk.DrawingArea()
        self.chart_area.set_content_height(260)
        self.chart_area.set_draw_func(self._draw_chart)
        chart_inner.append(self.chart_area)
        self.legend_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        chart_inner.append(self.legend_box)
        root.append(chart_card)

        self.totals_card, self.totals_inner = make_card("Totals", "view-list-symbolic")
        root.append(self.totals_card)

    def _on_dir_changed(self, combo: Gtk.ComboBoxText) -> None:
        self.direction = combo.get_active_id() or "both"
        self.refresh()

    def _on_range_toggled(self, btn: Gtk.ToggleButton, idx: int) -> None:
        if btn.get_active():
            self.range_idx = idx
            self.refresh()

    def _active_interfaces(self) -> list[str]:
        _, _, uses_min = self.RANGES[self.range_idx]
        src = self.model.traffic.minutes if uses_min else self.model.traffic.recent
        out = []
        for name in sorted(src.keys()):
            info = self.model.interface(name)
            if (info and not info.is_system_noise) or (info and info.is_full_tunnel) or name.startswith("ppp"):
                out.append(name)
        return out

    def _samples(self, name: str) -> list[dict]:
        _, secs, uses_min = self.RANGES[self.range_idx]
        cutoff = time.time() - secs
        src = self.model.traffic.minutes.get(name, []) if uses_min else self.model.traffic.recent.get(name, [])
        return [s for s in src if s.get("ts", 0) >= cutoff]

    def _rate(self, s: dict) -> float:
        _, _, uses_min = self.RANGES[self.range_idx]
        scale = (1.0 / 60.0) if uses_min else 1.0
        if self.direction == "down":
            return s.get("rx", 0.0) * scale
        if self.direction == "up":
            return s.get("tx", 0.0) * scale
        return (s.get("rx", 0.0) + s.get("tx", 0.0)) * scale

    def _total(self, name: str) -> tuple[float, float]:
        _, _, uses_min = self.RANGES[self.range_idx]
        lst = self._samples(name)
        if uses_min:
            return sum(s.get("rx", 0.0) for s in lst), sum(s.get("tx", 0.0) for s in lst)
        step = self.model.workspace.settings.refresh_seconds
        return sum(s.get("rx", 0.0) * step for s in lst), sum(s.get("tx", 0.0) * step for s in lst)

    def refresh(self) -> None:
        label, _, uses_min = self.RANGES[self.range_idx]
        ifaces = self._active_interfaces()
        has_points = any(self._samples(n) for n in ifaces)
        if not has_points:
            self.chart_msg.set_visible(True)
            self.chart_msg.set_label(
                "Minute totals appear after the app has been running for a few minutes."
                if uses_min
                else f"Collecting… samples are taken every {int(self.model.workspace.settings.refresh_seconds)} seconds."
            )
            self.chart_area.set_visible(False)
        else:
            self.chart_msg.set_visible(False)
            self.chart_area.set_visible(True)
            self.chart_area.queue_draw()

        clear_box(self.legend_box)
        for idx, name in enumerate(ifaces):
            r, g, b = SERIES_COLORS[idx % len(SERIES_COLORS)]
            item = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            dot = Gtk.DrawingArea()
            dot.set_content_width(10)
            dot.set_content_height(10)
            dot.set_draw_func(lambda _a, ctx, w, h, col=(r, g, b): (ctx.set_source_rgb(*col), ctx.arc(w / 2, h / 2, 4, 0, 6.283), ctx.fill()))
            lbl = Gtk.Label(label=f"{self.model.display_name(name)} ({name})")
            lbl.add_css_class("caption")
            item.append(dot)
            item.append(lbl)
            self.legend_box.append(item)

        clear_box(self.totals_inner)
        hdr = Gtk.Label(label=f"Totals for the last {label}", xalign=0)
        hdr.add_css_class("heading")
        self.totals_inner.append(hdr)
        grid = Gtk.Grid(column_spacing=20, row_spacing=8)
        for c, h in enumerate(["Interface", "Downloaded", "Uploaded", "Share"]):
            hl = Gtk.Label(label=h, xalign=0)
            hl.add_css_class("caption")
            hl.add_css_class("dim-label")
            grid.attach(hl, c, 0, 1, 1)
        totals = [(n, self._total(n)) for n in ifaces]
        grand = max(1.0, sum(rx + tx for _, (rx, tx) in totals))
        for r_idx, (name, (rx, tx)) in enumerate(totals, start=1):
            grid.attach(Gtk.Label(label=f"{self.model.display_name(name)} ({name})", xalign=0), 0, r_idx, 1, 1)
            dl = Gtk.Label(label=format_bytes(rx), xalign=0)
            dl.add_css_class("numeric")
            grid.attach(dl, 1, r_idx, 1, 1)
            ul = Gtk.Label(label=format_bytes(tx), xalign=0)
            ul.add_css_class("numeric")
            grid.attach(ul, 2, r_idx, 1, 1)
            pb = Gtk.ProgressBar()
            pb.set_fraction((rx + tx) / grand)
            pb.set_size_request(140, -1)
            pb.set_valign(Gtk.Align.CENTER)
            grid.attach(pb, 3, r_idx, 1, 1)
        self.totals_inner.append(grid)
        foot = Gtk.Label(label="Measured while SplitRoute is running (including in the system tray). The last 7 days are kept.", xalign=0)
        foot.add_css_class("caption")
        foot.add_css_class("dim-label")
        self.totals_inner.append(foot)

    def _draw_chart(self, _area: Gtk.DrawingArea, ctx: cairo.Context, w: int, h: int) -> None:
        ifaces = self._active_interfaces()
        _, secs, _ = self.RANGES[self.range_idx]
        now = time.time()
        t0 = now - secs
        pad_l, pad_r, pad_t, pad_b = 76, 16, 12, 24
        cw = max(10, w - pad_l - pad_r)
        ch = max(10, h - pad_t - pad_b)

        # Collect series
        series_pts: list[list[tuple[float, float]]] = []
        max_val = 1024.0
        for name in ifaces:
            pts = [(s["ts"], self._rate(s)) for s in self._samples(name)]
            series_pts.append(pts)
            for _, v in pts:
                if v > max_val:
                    max_val = v
        max_val *= 1.15

        # Grid lines + Y labels
        ctx.set_font_size(11)
        for i in range(5):
            frac = i / 4.0
            y = pad_t + ch * (1.0 - frac)
            ctx.set_source_rgba(0.5, 0.5, 0.5, 0.20)
            ctx.set_line_width(1.0)
            ctx.move_to(pad_l, y)
            ctx.line_to(pad_l + cw, y)
            ctx.stroke()

            ctx.set_source_rgba(0.55, 0.55, 0.60, 0.90)
            lbl = format_rate(max_val * frac)
            ctx.move_to(4, y + 4)
            ctx.show_text(lbl)

        for idx, pts in enumerate(series_pts):
            if len(pts) < 2:
                continue
            r, g, b = SERIES_COLORS[idx % len(SERIES_COLORS)]
            coords = [
                (pad_l + ((ts - t0) / secs) * cw, pad_t + ch * (1.0 - min(1.0, val / max_val)))
                for ts, val in pts
            ]
            # Fill area
            ctx.new_path()
            ctx.move_to(coords[0][0], pad_t + ch)
            for x, y in coords:
                ctx.line_to(x, y)
            ctx.line_to(coords[-1][0], pad_t + ch)
            ctx.close_path()
            ctx.set_source_rgba(r, g, b, 0.22)
            ctx.fill()

            # Line
            ctx.new_path()
            ctx.move_to(coords[0][0], coords[0][1])
            for x, y in coords[1:]:
                ctx.line_to(x, y)
            ctx.set_source_rgba(r, g, b, 0.95)
            ctx.set_line_width(2.0)
            ctx.stroke()

