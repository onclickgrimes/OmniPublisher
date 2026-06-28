from __future__ import annotations

import mimetypes
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path


@dataclass
class TempMediaEntry:
    path: Path
    expires_at: datetime
    media_type: str


class TempMediaStore:
    def __init__(self) -> None:
        self._entries: dict[str, TempMediaEntry] = {}
        self._lock = threading.RLock()

    def register(self, path: str | Path, *, ttl_seconds: int = 3600) -> str:
        media_path = Path(path).expanduser().resolve()
        if not media_path.is_file():
            raise FileNotFoundError(f"Arquivo de mídia não encontrado: {path}")

        with self._lock:
            self.cleanup()
            token = secrets.token_urlsafe(32)
            media_type = mimetypes.guess_type(str(media_path))[0] or "application/octet-stream"
            self._entries[token] = TempMediaEntry(
                path=media_path,
                expires_at=datetime.utcnow() + timedelta(seconds=max(60, ttl_seconds)),
                media_type=media_type,
            )
            return token

    def get(self, token: str) -> TempMediaEntry | None:
        with self._lock:
            self.cleanup()
            entry = self._entries.get(token)
            if not entry:
                return None
            if entry.expires_at <= datetime.utcnow():
                self._entries.pop(token, None)
                return None
            return entry

    def revoke(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._entries.pop(token, None)

    def cleanup(self) -> None:
        with self._lock:
            now = datetime.utcnow()
            expired = [token for token, entry in self._entries.items() if entry.expires_at <= now]
            for token in expired:
                self._entries.pop(token, None)


temp_media_store = TempMediaStore()
