from __future__ import annotations

import concurrent.futures
import json
import random
import subprocess
import threading
import time
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from .models import IPIdentity, SpeedHistoryStore, new_id, now_iso
from .paths import VERSION, find_probe_binary
from .probes import Probes


@dataclass
class SpeedServer:
    id: str
    name: str
    host: str
    base_path: str
    engine: str  # cloudflare | libreSpeed

    @classmethod
    def cloudflare(cls) -> SpeedServer:
        return cls(
            id="speed.cloudflare.com/",
            name="Cloudflare (nearest location)",
            host="speed.cloudflare.com",
            base_path="/",
            engine="cloudflare",
        )

    def ping_url(self) -> str:
        if self.engine == "cloudflare":
            return f"https://{self.host}/__down?bytes=0"
        return f"https://{self.host}{self.base_path}empty.php?r={random.randint(0, 2**31)}"

    def download_url(self, bytes_count: int = 25_000_000) -> str:
        if self.engine == "cloudflare":
            return f"https://{self.host}/__down?bytes={bytes_count}"
        mb = max(1, bytes_count // 1_048_576)
        return f"https://{self.host}{self.base_path}garbage.php?ckSize={mb}&r={random.randint(0, 2**31)}"

    def upload_url(self) -> str:
        if self.engine == "cloudflare":
            return f"https://{self.host}/__up"
        return f"https://{self.host}{self.base_path}empty.php?r={random.randint(0, 2**31)}"


@dataclass
class SpeedPath:
    id: str
    title: str
    detail: str
    interface_name: str | None = None
    pinned: bool = False


class SpeedTestRunner:
    STREAMS = 4
    DOWNLOAD_SECONDS = 10.0
    UPLOAD_SECONDS = 8.0

    def __init__(self) -> None:
        self.phase: str = "idle"  # idle | server | ping | download | upload | done | failed | cancelled
        self.live_mbps: float = 0.0
        self.samples: list[float] = []
        self.download_samples: list[float] = []
        self.upload_samples: list[float] = []
        self.progress: float = 0.0
        self.ping_ms: float | None = None
        self.jitter_ms: float | None = None
        self.loaded_ping_ms: float | None = None
        self.download_mbps: float | None = None
        self.upload_mbps: float | None = None
        self.server_name: str | None = None
        self.identity: IPIdentity | None = None
        self.error: str | None = None
        self.history: list[dict[str, Any]] = SpeedHistoryStore.load()
        self.libre_servers: list[SpeedServer] = []
        self._cancel = threading.Event()
        self._proc: subprocess.Popen[str] | None = None

    @property
    def is_running(self) -> bool:
        return self.phase in ("server", "ping", "download", "upload")

    def stop(self) -> None:
        self._cancel.set()
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except Exception:
                pass
        if self.is_running:
            self.phase = "cancelled"

    def load_libre_servers(self, on_done: Callable[[], None]) -> None:
        if self.libre_servers:
            on_done()
            return

        def worker() -> None:
            try:
                req = urllib.request.Request(
                    "https://librespeed.org/backend-servers/servers.php",
                    headers={"User-Agent": f"SplitRoute/{VERSION}"},
                )
                with urllib.request.urlopen(req, timeout=12.0) as resp:
                    items = json.loads(resp.read().decode("utf-8"))
                out: list[SpeedServer] = []
                for o in items:
                    name = o.get("name")
                    srv = o.get("server")
                    if not name or not srv:
                        continue
                    if srv.startswith("//"):
                        srv = "https:" + srv
                    if "://" not in srv:
                        continue
                    rest = srv.split("://", 1)[1]
                    host, _, path = rest.partition("/")
                    base = "/" + path if path else "/"
                    if not base.endswith("/"):
                        base += "/"
                    dl = o.get("dlURL", "")
                    if "/" in dl:
                        base += dl.rsplit("/", 1)[0] + "/"
                    out.append(SpeedServer(id=host + base, name=name, host=host, base_path=base, engine="libreSpeed"))
                self.libre_servers = out
            except Exception:
                pass
            on_done()

        threading.Thread(target=worker, daemon=True).start()

    def start(
        self,
        engine: str,
        libre_server_id: str | None,
        path: SpeedPath,
        geo_provider: str,
        on_update: Callable[[], None],
    ) -> None:
        self.stop()
        self._cancel = threading.Event()
        self.phase = "server"
        self.live_mbps = 0.0
        self.progress = 0.0
        self.samples = []
        self.download_samples = []
        self.upload_samples = []
        self.ping_ms = None
        self.jitter_ms = None
        self.loaded_ping_ms = None
        self.download_mbps = None
        self.upload_mbps = None
        self.server_name = None
        self.identity = None
        self.error = None
        on_update()

        threading.Thread(
            target=self._run,
            args=(engine, libre_server_id, path, geo_provider, on_update),
            daemon=True,
        ).start()

    def _ping_once(self, server: SpeedServer, iface: str | None) -> float | None:
        probe = find_probe_binary()
        if probe is not None:
            args = [str(probe), "ping", "--url", server.ping_url()]
            if iface:
                args += ["--interface", iface]
            try:
                p = subprocess.run(args, capture_output=True, text=True, timeout=5.0)
                if p.returncode == 0 and p.stdout.strip():
                    data = json.loads(p.stdout.strip())
                    if "ms" in data:
                        return float(data["ms"])
                    return None
            except Exception:
                return None
        if iface:
            return None
        t0 = time.monotonic()
        try:
            req = urllib.request.Request(server.ping_url(), headers={"User-Agent": f"SplitRoute/{VERSION}"})
            with urllib.request.urlopen(req, timeout=4.0) as resp:
                resp.read(1024)
            return (time.monotonic() - t0) * 1000.0
        except Exception:
            return None

    def _transfer_with_probe(
        self,
        mode: str,
        server: SpeedServer,
        iface: str | None,
        seconds: float,
        on_update: Callable[[], None],
    ) -> tuple[float, int, float | None, str | None]:
        probe = find_probe_binary()
        if probe is None:
            if iface:
                return 0.0, 0, None, "splitroute-probe binary is required to pin speed tests to an interface."
            return self._transfer_python_fallback(mode, server, seconds, on_update)

        url = server.download_url() if mode == "download" else server.upload_url()
        args = [
            str(probe),
            mode,
            "--url",
            url,
            "--seconds",
            str(seconds),
            "--streams",
            str(self.STREAMS),
        ]
        if iface:
            args += ["--interface", iface]
        if mode == "download":
            args += ["--ping-url", server.ping_url()]

        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self._proc = proc
        final_mbps = 0.0
        total_bytes = 0
        loaded_ms: float | None = None
        err_msg: str | None = None
        assert proc.stdout is not None
        for line in proc.stdout:
            if self._cancel.is_set():
                proc.terminate()
                break
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            typ = ev.get("type")
            if typ == "sample":
                self.live_mbps = float(ev.get("mbps", 0.0))
                self.samples.append(self.live_mbps)
                self.progress = float(ev.get("progress", 0.0))
                on_update()
            elif typ == "done":
                final_mbps = float(ev.get("mbps", 0.0))
                total_bytes = int(ev.get("bytes", 0))
                lp = ev.get("loadedPingMs")
                loaded_ms = float(lp) if lp else None
            elif typ == "error":
                err_msg = ev.get("error") or "Transfer failed"
        proc.wait()
        self._proc = None
        return final_mbps, total_bytes, loaded_ms, err_msg

    def _transfer_python_fallback(
        self,
        mode: str,
        server: SpeedServer,
        seconds: float,
        on_update: Callable[[], None],
    ) -> tuple[float, int, float | None, str | None]:
        counter = [0]
        lock = threading.Lock()
        deadline = time.monotonic() + seconds
        first_err: list[str] = []

        def worker() -> None:
            payload = b"\x00" * (2 * 1024 * 1024)
            while time.monotonic() < deadline and not self._cancel.is_set():
                try:
                    if mode == "download":
                        req = urllib.request.Request(server.download_url(10_000_000), headers={"User-Agent": f"SplitRoute/{VERSION}"})
                        with urllib.request.urlopen(req, timeout=seconds + 2) as resp:
                            while time.monotonic() < deadline and not self._cancel.is_set():
                                chunk = resp.read(65536)
                                if not chunk:
                                    break
                                with lock:
                                    counter[0] += len(chunk)
                    else:
                        req = urllib.request.Request(
                            server.upload_url(),
                            data=payload,
                            method="POST",
                            headers={"User-Agent": f"SplitRoute/{VERSION}", "Content-Type": "application/octet-stream"},
                        )
                        with urllib.request.urlopen(req, timeout=seconds + 2) as resp:
                            resp.read()
                        with lock:
                            counter[0] += len(payload)
                except Exception as exc:
                    if not first_err:
                        first_err.append(str(exc))
                    time.sleep(0.25)

        pool = concurrent.futures.ThreadPoolExecutor(max_workers=self.STREAMS)
        futs = [pool.submit(worker) for _ in range(self.STREAMS)]
        t_start = time.monotonic()
        last_t = t_start
        last_b = 0
        warm_t: float | None = None
        warm_b = 0
        while time.monotonic() < deadline and not self._cancel.is_set():
            time.sleep(0.2)
            now = time.monotonic()
            with lock:
                b = counter[0]
            dt = max(0.05, now - last_t)
            inst = (b - last_b) * 8.0 / 1_000_000.0 / dt
            self.live_mbps = inst if self.live_mbps == 0 else self.live_mbps * 0.7 + inst * 0.3
            self.samples.append(self.live_mbps)
            self.progress = min(1.0, (now - t_start) / seconds)
            if warm_t is None and (now - t_start) >= 1.0:
                warm_t = now
                warm_b = b
            last_t = now
            last_b = b
            on_update()

        pool.shutdown(wait=True)
        _ = futs
        end = time.monotonic()
        with lock:
            total = counter[0]
        from_t = warm_t or t_start
        measured = total - (warm_b if warm_t else 0)
        mbps = max(0.0, measured * 8.0 / 1_000_000.0 / max(0.5, end - from_t))
        return mbps, total, None, (first_err[0] if total == 0 and first_err else None)

    def _run(
        self,
        engine: str,
        libre_server_id: str | None,
        path: SpeedPath,
        geo_provider: str,
        on_update: Callable[[], None],
    ) -> None:
        iface = path.interface_name if path.pinned else None

        def ident_task() -> IPIdentity | None:
            ip, _ms, _err = Probes.public_address(False, iface)
            if not ip:
                return None
            return Probes.geolocate(ip, preferred=geo_provider) or IPIdentity(ip=ip)

        ident_fut = concurrent.futures.ThreadPoolExecutor(max_workers=1).submit(ident_task)

        server = SpeedServer.cloudflare()
        if engine == "libreSpeed":
            if not self.libre_servers:
                ev = threading.Event()
                self.load_libre_servers(ev.set)
                ev.wait(timeout=12.0)
            if libre_server_id:
                match = next((s for s in self.libre_servers if s.id == libre_server_id), None)
                if match:
                    server = match
            else:
                best: tuple[SpeedServer, float] | None = None
                with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                    futs = {pool.submit(self._ping_once, s, iface): s for s in self.libre_servers[:20]}
                    for fut in concurrent.futures.as_completed(futs):
                        s = futs[fut]
                        ms = fut.result()
                        if ms is not None and (best is None or ms < best[1]):
                            best = (s, ms)
                if best:
                    server = best[0]
                else:
                    self._fail("No LibreSpeed server answered on this path.", on_update)
                    return

        self.server_name = server.name
        if self._cancel.is_set():
            return

        # Ping
        self.phase = "ping"
        on_update()
        pings: list[float] = []
        for i in range(10):
            if self._cancel.is_set():
                return
            ms = self._ping_once(server, iface)
            if ms is not None:
                pings.append(ms)
            self.progress = (i + 1) / 10.0
            on_update()
        if not pings:
            self._fail(f"The server did not answer on {path.title}. This path may have no internet access.", on_update)
            return
        pings_sorted = sorted(pings)
        self.ping_ms = pings_sorted[len(pings_sorted) // 2]
        if len(pings) > 1:
            self.jitter_ms = sum(abs(b - a) for a, b in zip(pings, pings[1:])) / (len(pings) - 1)
        else:
            self.jitter_ms = 0.0

        # Download
        self.phase = "download"
        self.samples = []
        self.live_mbps = 0.0
        on_update()
        dl_mbps, dl_bytes, loaded_ms, dl_err = self._transfer_with_probe("download", server, iface, self.DOWNLOAD_SECONDS, on_update)
        if self._cancel.is_set():
            return
        self.download_samples = list(self.samples)
        self.download_mbps = dl_mbps
        self.loaded_ping_ms = loaded_ms

        # Upload
        self.phase = "upload"
        self.samples = []
        self.live_mbps = 0.0
        on_update()
        ul_mbps, ul_bytes, _lp, ul_err = self._transfer_with_probe("upload", server, iface, self.UPLOAD_SECONDS, on_update)
        if self._cancel.is_set():
            return
        self.upload_samples = list(self.samples)
        self.upload_mbps = ul_mbps

        self.identity = ident_fut.result(timeout=5.0) if not ident_fut.done() else ident_fut.result()
        if dl_bytes == 0 and ul_bytes == 0:
            self._fail(dl_err or ul_err or "No data could be transferred on this path.", on_update)
            return

        self.phase = "done"
        self.live_mbps = 0.0
        item = {
            "id": new_id(),
            "date": now_iso(),
            "engine": engine,
            "server": server.name,
            "pathTitle": path.title,
            "interface": path.interface_name,
            "downloadMbps": dl_mbps,
            "uploadMbps": ul_mbps,
            "pingMs": self.ping_ms or 0.0,
            "jitterMs": self.jitter_ms or 0.0,
            "loadedPingMs": loaded_ms,
            "dataMB": (dl_bytes + ul_bytes) / 1_000_000.0,
            "publicIP": self.identity.ip if self.identity else None,
            "country": self.identity.country if self.identity else None,
            "org": self.identity.org if self.identity else None,
        }
        self.history.append(item)
        SpeedHistoryStore.save(self.history)
        on_update()

    def _fail(self, msg: str, on_update: Callable[[], None]) -> None:
        self.error = msg
        self.phase = "failed"
        self.live_mbps = 0.0
        on_update()

