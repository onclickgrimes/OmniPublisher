from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.config import (
    BASE_DIR,
    CLOUDFLARED_PATH,
    CLOUDFLARE_TUNNEL_STARTUP_TIMEOUT_SECONDS,
    OMNIPUBLISHER_HOST,
    OMNIPUBLISHER_PORT,
)


@dataclass(frozen=True)
class TunnelLease:
    lease_id: str
    public_url: str


class CloudflareTunnelManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._process: subprocess.Popen | None = None
        self._public_url: str | None = None
        self._logs: list[str] = []
        self._url_event = threading.Event()
        self._connected_event = threading.Event()
        self._reader_thread: threading.Thread | None = None
        self._leases: dict[str, str] = {}
        self._timers: dict[str, threading.Timer] = {}

    def acquire(self, purpose: str, *, ttl_seconds: int) -> TunnelLease:
        with self._lock:
            self._cleanup_expired_process_locked()
            if not self._process or self._process.poll() is not None:
                self._start_locked()

            if not self._public_url:
                raise RuntimeError("Cloudflare Tunnel iniciou sem URL pública.")

            lease_id = str(uuid.uuid4())
            self._leases[lease_id] = purpose
            timer = threading.Timer(max(60, ttl_seconds), self.release, args=[lease_id])
            timer.daemon = True
            self._timers[lease_id] = timer
            timer.start()
            return TunnelLease(lease_id=lease_id, public_url=self._public_url)

    def release(self, lease_id: str | None, *, delay_seconds: float = 0) -> None:
        if not lease_id:
            return

        with self._lock:
            if delay_seconds > 0:
                if lease_id not in self._leases:
                    return
                timer = self._timers.pop(lease_id, None)
                if timer:
                    timer.cancel()
                timer = threading.Timer(delay_seconds, self.release, args=[lease_id])
                timer.daemon = True
                self._timers[lease_id] = timer
                timer.start()
                return

            self._leases.pop(lease_id, None)
            timer = self._timers.pop(lease_id, None)
            if timer:
                timer.cancel()
            if not self._leases:
                self.stop()

    def stop(self) -> None:
        with self._lock:
            for timer in self._timers.values():
                timer.cancel()
            self._timers.clear()
            self._leases.clear()

            process = self._process
            self._process = None
            self._public_url = None
            self._url_event.clear()
            self._connected_event.clear()

            if not process or process.poll() is not None:
                return

            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)

    def current_public_url(self) -> str | None:
        with self._lock:
            if self._process and self._process.poll() is None:
                return self._public_url
            return None

    def _start_locked(self) -> None:
        executable = self._resolve_cloudflared()
        local_url = self._local_origin_url()
        self._logs = []
        self._url_event.clear()
        self._connected_event.clear()

        creationflags = 0
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            creationflags = subprocess.CREATE_NO_WINDOW

        self._process = subprocess.Popen(
            [
                executable,
                "tunnel",
                "--no-autoupdate",
                "--url",
                local_url,
            ],
            cwd=str(BASE_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
        )
        self._reader_thread = threading.Thread(
            target=self._read_logs,
            name="cloudflared-log-reader",
            daemon=True,
        )
        self._reader_thread.start()

        deadline = time.monotonic() + max(5, CLOUDFLARE_TUNNEL_STARTUP_TIMEOUT_SECONDS)
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                break
            if self._url_event.wait(timeout=0.5):
                while time.monotonic() < deadline:
                    if self._process.poll() is not None:
                        break
                    if self._connected_event.wait(timeout=0.5):
                        time.sleep(5)
                        return
                if self._connected_event.is_set():
                    return
                break

        logs = "\n".join(self._logs[-20:])
        self.stop()
        raise RuntimeError(
            "Não foi possível iniciar Cloudflare Tunnel temporário. "
            f"Últimos logs:\n{logs}"
        )

    def _read_logs(self) -> None:
        process = self._process
        if not process or not process.stdout:
            return

        pattern = re.compile(r"https://[-a-z0-9]+\.trycloudflare\.com")
        for line in process.stdout:
            line = line.rstrip()
            self._logs.append(line)
            if len(self._logs) > 500:
                self._logs = self._logs[-500:]
            match = pattern.search(line)
            if match and not self._public_url:
                self._public_url = match.group(0)
                self._url_event.set()
            if "Registered tunnel connection" in line:
                self._connected_event.set()

    def _cleanup_expired_process_locked(self) -> None:
        if self._process and self._process.poll() is not None:
            self._process = None
            self._public_url = None
            self._url_event.clear()
            self._connected_event.clear()

    def _resolve_cloudflared(self) -> str:
        candidates = []
        if CLOUDFLARED_PATH:
            candidates.append(Path(CLOUDFLARED_PATH))
        candidates.append(BASE_DIR / ".tools" / "cloudflared.exe")

        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)

        found = shutil.which("cloudflared")
        if found:
            return found

        raise RuntimeError(
            "cloudflared não encontrado. Instale em .tools/cloudflared.exe "
            "ou configure CLOUDFLARED_PATH."
        )

    def _local_origin_url(self) -> str:
        host = OMNIPUBLISHER_HOST
        if host in {"0.0.0.0", "::"}:
            host = "127.0.0.1"
        return f"http://{host}:{OMNIPUBLISHER_PORT}"


cloudflare_tunnel_manager = CloudflareTunnelManager()
