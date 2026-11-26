from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from gi.repository import Adw, Gio, Gtk

from ..models import Storage, atomic_write_json
from .widgets import clear_box, make_card, make_kv_row, make_page_header

if TYPE_CHECKING:
    from ..app_model import AppModel


class RestoreDialog(Adw.Window):
    def __init__(self, parent: Gtk.Window | None, model: AppModel, bundle: dict[str, Any]) -> None:
        super().__init__(transient_for=parent, modal=True, title="Restore Backup")
        self.model = model
        self.bundle = bundle
        self.set_default_size(560, 480)

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

        body.append(make_kv_row("Created", bundle.get("createdAt", ""), mono=False))
        body.append(make_kv_row("From", f"{bundle.get('hostName', '')} · SplitRoute {bundle.get('appVersion', '')}", mono=False))
        body.append(make_kv_row("Reason", bundle.get("reason", ""), mono=False))
        body.append(Gtk.Separator())

        rules = bundle.get("rules", [])
        hl = Gtk.Label(label=f"{len(rules)} rules", xalign=0)
        hl.add_css_class("heading")
        body.append(hl)

        sc = Gtk.ScrolledWindow()
        sc.set_vexpand(True)
        rl_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        for r in rules:
            rl_box.append(Gtk.Label(label=f"{r.get('name', 'Untitled')} — {', '.join(r.get('destinations', []))}", xalign=0))
        sc.set_child(rl_box)
        body.append(sc)

        self.replace_chk = Gtk.CheckButton(label="Replace current rules (unchecked = merge)")
        self.replace_chk.set_active(True)
        self.settings_chk = Gtk.CheckButton(label="Also restore interface aliases and settings")
        body.append(self.replace_chk)
        body.append(self.settings_chk)

        hint = Gtk.Label(label="Restored rules show as unapplied changes until you press Apply.", xalign=0)
        hint.add_css_class("caption")
        hint.add_css_class("dim-label")
        body.append(hint)

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
        restore = Gtk.Button(label="Restore")
        restore.add_css_class("suggested-action")
        restore.connect("clicked", self._do_restore)
        foot.append(cancel)
        foot.append(restore)
        outer.append(foot)

    def _do_restore(self, *_args) -> None:
        self.model.restore_backup(self.bundle, self.replace_chk.get_active(), self.settings_chk.get_active())
        self.close()


class BackupView(Gtk.ScrolledWindow):
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

        self.root.append(make_page_header("Backup & Restore", "Save, move and roll back your routing setup"))

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        top.set_homogeneous(True)
        exp_c, exp_in = make_card("Export", "document-save-symbolic")
        exp_in.append(Gtk.Label(label="Saves all rules, interface aliases, settings and a snapshot of the IPv4 routing table to a JSON file.", xalign=0, wrap=True))
        exp_b = Gtk.Button(label="Export Backup…")
        exp_b.add_css_class("suggested-action")
        exp_b.set_halign(Gtk.Align.START)
        exp_b.connect("clicked", self._export_backup)
        exp_in.append(exp_b)
        top.append(exp_c)

        imp_c, imp_in = make_card("Import", "document-open-symbolic")
        imp_in.append(Gtk.Label(label="Loads rules from a backup file. You can review them and then press Apply.", xalign=0, wrap=True))
        imp_b = Gtk.Button(label="Restore from File…")
        imp_b.set_halign(Gtk.Align.START)
        imp_b.connect("clicked", self._import_backup)
        imp_in.append(imp_b)
        top.append(imp_c)
        self.root.append(top)

        self.snaps_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.root.append(self.snaps_box)

        # Reset card
        rst_c, rst_in = make_card("Reset to default", "edit-undo-symbolic")
        rst_in.append(Gtk.Label(label="Puts routing back to how it was before SplitRoute. Routes created by other apps are not touched.", xalign=0, wrap=True))
        self.r_backup = Gtk.CheckButton(label="Take a snapshot first")
        self.r_backup.set_active(True)
        self.r_routes = Gtk.CheckButton(label="Remove all routes and firewall rules installed by SplitRoute")
        self.r_routes.set_active(True)
        self.r_rules = Gtk.CheckButton(label="Delete all rules")
        self.r_rules.set_active(True)
        self.r_svc = Gtk.CheckButton(label="Uninstall the background service and helper")
        self.r_cfg = Gtk.CheckButton(label="Reset interface aliases and app settings")
        for c in (self.r_backup, self.r_routes, self.r_rules, self.r_svc, self.r_cfg):
            rst_in.append(c)
        rst_b = Gtk.Button(label="Reset to Default…")
        rst_b.add_css_class("destructive-action")
        rst_b.set_halign(Gtk.Align.START)
        rst_b.connect("clicked", self._confirm_reset)
        rst_in.append(rst_b)
        self.root.append(rst_c)

    def _export_backup(self, *_args) -> None:
        dlg = Gtk.FileDialog(title="Export Backup")
        dlg.set_initial_name(f"SplitRoute-backup-{datetime.now().strftime('%Y-%m-%d-%H%M')}.json")
        dlg.save(self.get_window(), None, self._on_export_chosen)

    def _on_export_chosen(self, dlg: Gtk.FileDialog, res: Gio.AsyncResult) -> None:
        try:
            gfile = dlg.save_finish(res)
            path = Path(gfile.get_path())
            routes_raw = [{"destination": r.destination, "gateway": r.gateway, "netif": r.netif} for r in self.model.net.routes_v4]
            bundle = Storage.make_backup_bundle(self.model.workspace, "Manual export", routes_raw)
            atomic_write_json(path, bundle)
            self.model.log("Exported backup", True, str(path))
        except Exception as exc:
            if "Dismissed" not in str(exc):
                self.model.show_error("Export failed", str(exc))

    def _import_backup(self, *_args) -> None:
        dlg = Gtk.FileDialog(title="Restore from Backup")
        dlg.open(self.get_window(), None, self._on_import_chosen)

    def _on_import_chosen(self, dlg: Gtk.FileDialog, res: Gio.AsyncResult) -> None:
        try:
            gfile = dlg.open_finish(res)
            bundle = Storage.read_backup(Path(gfile.get_path()))
            RestoreDialog(self.get_window(), self.model, bundle).present()
        except Exception as exc:
            if "Dismissed" not in str(exc):
                self.model.show_error("Cannot read backup", str(exc))

    def _confirm_reset(self, *_args) -> None:
        dlg = Adw.MessageDialog(
            transient_for=self.get_window(),
            heading="Reset routing to default?",
            body="This removes SplitRoute's managed routes, firewall rules, and selected configuration.",
        )
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("reset", "Reset")
        dlg.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", self._on_reset_resp)
        dlg.present()

    def _on_reset_resp(self, _d, resp: str) -> None:
        if resp == "reset":
            self.model.reset_to_default(
                backup_first=self.r_backup.get_active(),
                remove_routes=self.r_routes.get_active(),
                delete_rules=self.r_rules.get_active(),
                uninstall_service=self.r_svc.get_active(),
                reset_aliases_settings=self.r_cfg.get_active(),
            )

    def refresh(self) -> None:
        m = self.model
        clear_box(self.snaps_box)
        snap_btn = Gtk.Button(label="Snapshot Now")
        snap_btn.connect("clicked", lambda *_: m.auto_backup("Manual snapshot"))
        card, inner = make_card("Automatic snapshots", "document-open-recent-symbolic", snap_btn)
        desc = Gtk.Label(
            label=f"Taken after every successful Apply and before every restore or reset. Keeps the last {m.workspace.settings.auto_backup_limit}.",
            xalign=0,
        )
        desc.add_css_class("caption")
        desc.add_css_class("dim-label")
        inner.append(desc)
        if not m.backups:
            lbl = Gtk.Label(label="No snapshots yet", xalign=0)
            lbl.add_css_class("dim-label")
            inner.append(lbl)
        for b in m.backups:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            col.set_hexpand(True)
            col.append(Gtk.Label(label=b.created_at.astimezone().strftime("%Y-%m-%d %H:%M:%S"), xalign=0))
            sub = Gtk.Label(label=f"{b.reason} · {b.rule_count} rule" + ("" if b.rule_count == 1 else "s"), xalign=0)
            sub.add_css_class("caption")
            sub.add_css_class("dim-label")
            col.append(sub)
            row.append(col)
            rest = Gtk.Button(label="Restore…")
            rest.connect("clicked", lambda *_, p=b.path: RestoreDialog(self.get_window(), m, Storage.read_backup(p)).present())
            dl = Gtk.Button.new_from_icon_name("user-trash-symbolic")
            dl.add_css_class("flat")
            dl.connect("clicked", lambda *_, p=b.path: m.delete_backup(p))
            row.append(rest)
            row.append(dl)
            inner.append(row)
            inner.append(Gtk.Separator())
        self.snaps_box.append(card)

