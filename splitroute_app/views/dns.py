from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from gi.repository import Adw, GLib, Gtk

from ..models import DomainResolver, is_private_ipv4, is_valid_ipv4
from ..probes import Probes
from ..scanner import NetworkScanner
from .widgets import PendingBanner, clear_box, make_banner, make_card, make_kv_row, make_page_header, make_pill

if TYPE_CHECKING:
    from ..app_model import AppModel


class ResolverEditorDialog(Adw.Window):
    def __init__(self, parent: Gtk.Window | None, model: AppModel, resolver: DomainResolver) -> None:
        super().__init__(transient_for=parent, modal=True, title="Per-domain Resolver")
        self.model = model
        self.resolver = DomainResolver.from_dict(resolver.to_dict())
        self.set_default_size(480, 360)

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

        grp = Adw.PreferencesGroup()
        self.dom_row = Adw.EntryRow(title="Domain (e.g. example.com)")
        self.dom_row.set_text(self.resolver.domain)
        self.dom_row.connect("changed", lambda *_: self._validate())
        grp.add(self.dom_row)

        self.ns_row = Adw.EntryRow(title="Name servers (e.g. 10.0.0.53 10.0.0.54)")
        self.ns_row.set_text(" ".join(self.resolver.nameservers))
        self.ns_row.connect("changed", lambda *_: self._validate())
        grp.add(self.ns_row)

        self.en_row = Adw.SwitchRow(title="Enabled")
        self.en_row.set_active(self.resolver.enabled)
        grp.add(self.en_row)
        body.append(grp)

        tip = Gtk.Label(
            label="Tip: if the name server is only reachable through a VPN, also add a routing rule that sends its IP through that VPN.",
            xalign=0,
        )
        tip.add_css_class("caption")
        tip.add_css_class("dim-label")
        tip.set_wrap(True)
        body.append(tip)

        self.err_lbl = Gtk.Label(label="", xalign=0)
        self.err_lbl.add_css_class("caption")
        body.append(self.err_lbl)

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
        self.save_btn = Gtk.Button(label="Save")
        self.save_btn.add_css_class("suggested-action")
        self.save_btn.connect("clicked", self._save)
        foot.append(cancel)
        foot.append(self.save_btn)
        outer.append(foot)
        self._validate()

    def _draft(self) -> DomainResolver:
        dom = self.dom_row.get_text().strip().lower()
        ns = [x.strip() for x in self.ns_row.get_text().replace(",", " ").split() if x.strip()]
        return DomainResolver(id=self.resolver.id, domain=dom, nameservers=ns, enabled=self.en_row.get_active())

    def _validate(self) -> None:
        err = self._draft().validation_error
        self.err_lbl.set_visible(bool(err))
        self.err_lbl.set_label(err or "")
        self.save_btn.set_sensitive(err is None)

    def _save(self, *_args) -> None:
        d = self._draft()
        if d.validation_error:
            return
        lst = self.model.workspace.automation.resolvers
        for i, r in enumerate(lst):
            if r.id == d.id:
                lst[i] = d
                break
        else:
            lst.append(d)
        self.model.save_workspace()
        self.close()


class DNSView(Gtk.ScrolledWindow):
    def __init__(self, model: AppModel, get_window) -> None:
        super().__init__()
        self.model = model
        self.get_window = get_window
        self.leak_result: dict | None = None
        self.leak_error: str | None = None
        self.testing = False
        self.resolver_paths: dict[str, str] = {}
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self.root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.root.set_margin_top(24)
        self.root.set_margin_bottom(24)
        self.root.set_margin_start(24)
        self.root.set_margin_end(24)
        self.set_child(self.root)

        rel_btn = Gtk.Button(label="Reload")
        rel_btn.connect("clicked", lambda *_: self.reload_paths())
        self.root.append(make_page_header(
            "DNS",
            "Which servers answer your lookups, whether they bypass the VPN, and per-domain resolvers",
            rel_btn,
        ))
        self.pending_banner = PendingBanner(model, get_window)
        self.root.append(self.pending_banner)

        self.leak_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.sys_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.dom_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.root.append(self.leak_box)
        self.root.append(self.sys_box)
        self.root.append(self.dom_box)

    def reload_paths(self) -> None:
        blocks = list(self.model.net.dns_resolvers)

        def worker() -> None:
            paths: dict[str, str] = {}
            for b in blocks:
                for ns in b.get("nameservers", []):
                    if is_valid_ipv4(ns) and ns not in paths:
                        lk = NetworkScanner.lookup_route(ns)
                        if lk and lk.get("interface"):
                            paths[ns] = lk["interface"]
            GLib.idle_add(self._set_paths, paths)

        threading.Thread(target=worker, daemon=True).start()

    def _set_paths(self, paths: dict[str, str]) -> None:
        self.resolver_paths = paths
        self.refresh()

    def _run_leak_test(self, *_args) -> None:
        if self.testing:
            return
        self.testing = True
        self.leak_error = None
        self.refresh()

        def worker() -> None:
            try:
                res = Probes.dns_leak_test()
                GLib.idle_add(self._on_leak_done, res, None)
            except Exception as exc:
                GLib.idle_add(self._on_leak_done, None, str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _on_leak_done(self, res: dict | None, err: str | None) -> None:
        self.testing = False
        self.leak_result = res
        self.leak_error = err
        det = "\n".join(f"{s['ip']} {s.get('country') or ''}" for s in (res or {}).get("servers", [])) if res else (err or "")
        self.model.log("DNS leak test", err is None, det)
        self.refresh()

    def refresh(self) -> None:
        m = self.model
        self.pending_banner.refresh()

        if not self.resolver_paths and m.net.dns_resolvers:
            self.reload_paths()

        # Leak test card
        clear_box(self.leak_box)
        run_btn = Gtk.Button(label="Running…" if self.testing else "Run Test")
        run_btn.set_sensitive(not self.testing)
        run_btn.connect("clicked", self._run_leak_test)
        l_card, l_inner = make_card("DNS leak test", "network-server-symbolic", run_btn)
        desc = Gtk.Label(
            label="Looks up random names and asks bash.ws which DNS servers fetched them. If those servers belong to your local ISP while you expect the VPN's, DNS is leaking.",
            xalign=0,
        )
        desc.add_css_class("dim-label")
        desc.set_wrap(True)
        l_inner.append(desc)
        if self.leak_error:
            l_inner.append(make_banner("Error", self.leak_error, tone="orange"))
        if self.leak_result:
            ip = self.leak_result.get("yourIP")
            if ip:
                l_inner.append(make_kv_row("Your IP", f"{ip.get('flag') or ''} {ip.get('ip', '')} · {ip.get('country') or ''} {ip.get('asn') or ''}".strip(), mono=False))
            for s in self.leak_result.get("servers", []):
                l_inner.append(make_kv_row("DNS server", f"{s.get('flag') or ''} {s.get('ip', '')} · {s.get('country') or ''} {s.get('asn') or ''}".strip(), mono=False))
            conc = self.leak_result.get("conclusion")
            if conc:
                leaking = "leak" in conc.lower() and "not leak" not in conc.lower()
                l_inner.append(make_pill(conc, "red" if leaking else "green"))
        self.leak_box.append(l_card)

        # System resolvers
        clear_box(self.sys_box)
        s_card, s_inner = make_card("Resolvers Linux uses", "network-workgroup-symbolic")
        tunnel = m.full_tunnel.name if m.full_tunnel else None
        if not m.net.dns_resolvers:
            lbl = Gtk.Label(label="No resolvers reported by resolvectl.", xalign=0)
            lbl.add_css_class("dim-label")
            s_inner.append(lbl)
        for blk in m.net.dns_resolvers:
            iface = blk.get("interface")
            doms = blk.get("domains", [])
            hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            title = f"For {', '.join(doms)}" if doms else "Default (all other names)"
            tl = Gtk.Label(label=title, xalign=0)
            tl.add_css_class("heading")
            hdr.append(tl)
            if iface:
                hdr.append(make_pill(f"{m.display_name(iface)} ({iface})", "gray"))
            s_inner.append(hdr)
            for ns in blk.get("nameservers", []):
                ns_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                nsl = Gtk.Label(label=ns, xalign=0)
                nsl.add_css_class("monospace")
                ns_row.append(nsl)
                via = self.resolver_paths.get(ns)
                if via:
                    vl = Gtk.Label(label=f"reached via {m.display_name(via)} ({via})", xalign=0)
                    vl.add_css_class("caption")
                    vl.add_css_class("dim-label")
                    ns_row.append(vl)
                    if tunnel and via != tunnel and not (is_private_ipv4(ns) and doms):
                        ns_row.append(make_pill("Outside the VPN tunnel", "orange"))
                s_inner.append(ns_row)
            s_inner.append(Gtk.Separator())
        s_note = Gtk.Label(
            label="A resolver reached outside the tunnel sees which sites you look up. Fix it with a per-domain resolver below, DNS settings on the adapter, or the VPN's own DNS option.",
            xalign=0,
        )
        s_note.add_css_class("caption")
        s_note.add_css_class("dim-label")
        s_note.set_wrap(True)
        s_inner.append(s_note)
        self.sys_box.append(s_card)

        # Per-domain resolvers
        clear_box(self.dom_box)
        add_b = Gtk.Button(label="Add")
        add_b.connect("clicked", lambda *_: ResolverEditorDialog(self.get_window(), m, DomainResolver()).present())
        d_card, d_inner = make_card("Per-domain resolvers", "go-jump-symbolic", add_b)
        d_desc = Gtk.Label(
            label="Send lookups for a domain and its subdomains to specific name servers (configured via systemd-resolved routing domains).",
            xalign=0,
        )
        d_desc.add_css_class("dim-label")
        d_desc.set_wrap(True)
        d_inner.append(d_desc)

        active_doms = set(((m.net.helper_status or {}).get("resolvers") or {}).get("active") or [])
        for r in m.workspace.automation.resolvers:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            sw = Gtk.Switch()
            sw.set_active(r.enabled)
            sw.set_valign(Gtk.Align.CENTER)
            sw.connect("notify::active", self._toggle_res, r.id)
            row.append(sw)
            dl = Gtk.Label(label=f"*.{r.domain}", xalign=0)
            dl.add_css_class("monospace")
            row.append(dl)
            nsl = Gtk.Label(label=f"→ {', '.join(r.nameservers)}", xalign=0)
            nsl.add_css_class("monospace")
            nsl.add_css_class("dim-label")
            nsl.set_hexpand(True)
            row.append(nsl)
            if r.domain.lower() in active_doms:
                row.append(make_pill("Active", "green"))
            ed = Gtk.Button(label="Edit…")
            ed.connect("clicked", lambda *_, res=r: ResolverEditorDialog(self.get_window(), m, res).present())
            dl_b = Gtk.Button.new_from_icon_name("user-trash-symbolic")
            dl_b.add_css_class("flat")
            dl_b.connect("clicked", lambda *_, rid=r.id: self._delete_res(rid))
            row.append(ed)
            row.append(dl_b)
            d_inner.append(row)

        for err in ((m.net.helper_status or {}).get("resolvers") or {}).get("errors") or []:
            el = Gtk.Label(label=err, xalign=0)
            el.add_css_class("caption")
            d_inner.append(el)
        self.dom_box.append(d_card)

    def _toggle_res(self, sw: Gtk.Switch, _p, rid: str) -> None:
        for r in self.model.workspace.automation.resolvers:
            if r.id == rid:
                if r.enabled != sw.get_active():
                    r.enabled = sw.get_active()
                    self.model.save_workspace()
                break

    def _delete_res(self, rid: str) -> None:
        self.model.workspace.automation.resolvers = [
            r for r in self.model.workspace.automation.resolvers if r.id != rid
        ]
        self.model.save_workspace()

