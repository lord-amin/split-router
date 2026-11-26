from __future__ import annotations

import json
import subprocess
import urllib.request
from dataclasses import dataclass

from gi.repository import Gio, GLib

from .paths import VERSION

GITHUB_REPO = "lord-amin/split-router"


@dataclass
class AvailableUpdate:
    version: str
    page_url: str
    download_url: str | None
    notes: str


class Notifier:
    _app: Gio.Application | None = None

    @classmethod
    def bind_app(cls, app: Gio.Application) -> None:
        cls._app = app

    @classmethod
    def post(cls, title: str, body: str, event_id: str | None = None) -> None:
        if cls._app is not None:
            try:
                notif = Gio.Notification.new(title)
                if body:
                    notif.set_body(body)
                cls._app.send_notification(event_id, notif)
                return
            except Exception:
                pass
        try:
            subprocess.Popen(
                ["notify-send", "-a", "SplitRoute", title, body or ""],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass


class UpdateChecker:
    @staticmethod
    def is_newer(a: str, than_b: str) -> bool:
        def parse(s: str) -> list[int]:
            base = s.split("-", 1)[0].lstrip("v")
            out = []
            for p in base.split("."):
                digits = "".join(ch for ch in p if ch.isdigit())
                out.append(int(digits) if digits else 0)
            return out

        pa = parse(a)
        pb = parse(than_b)
        n = max(len(pa), len(pb))
        pa += [0] * (n - len(pa))
        pb += [0] * (n - len(pb))
        return pa > pb

    @classmethod
    def latest(cls) -> AvailableUpdate | None:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": f"SplitRoute/{VERSION}",
            },
        )
        with urllib.request.urlopen(req, timeout=12.0) as resp:
            if resp.status == 404:
                return None
            j = json.loads(resp.read().decode("utf-8"))
        tag = j.get("tag_name", "")
        page = j.get("html_url")
        if not tag or not page:
            return None
        ver = tag[1:] if tag.startswith("v") else tag
        if not cls.is_newer(ver, VERSION):
            return None
        assets = j.get("assets", [])
        dl = next((a.get("browser_download_url") for a in assets if str(a.get("browser_download_url", "")).endswith(".zip")), None)
        return AvailableUpdate(version=ver, page_url=page, download_url=dl, notes=j.get("body", ""))


def open_url(url: str) -> None:
    try:
        Gio.AppInfo.launch_default_for_uri(url, None)
    except Exception:
        try:
            subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

