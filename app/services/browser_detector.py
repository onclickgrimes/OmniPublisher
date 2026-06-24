from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from app.config import TIKTOK_CHROME_PATH


def _existing_file(path: str | Path | None) -> str | None:
    if not path:
        return None
    candidate = Path(path).expanduser()
    try:
        candidate = candidate.resolve()
    except OSError:
        return None
    if candidate.is_file():
        return str(candidate)
    return None


def _registry_chrome_paths() -> list[str]:
    if sys.platform != "win32":
        return []

    try:
        import winreg
    except ImportError:
        return []

    registry_locations = [
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"),
    ]

    paths: list[str] = []
    for root, key_path in registry_locations:
        try:
            with winreg.OpenKey(root, key_path) as key:
                value, _ = winreg.QueryValueEx(key, "")
                if value:
                    paths.append(str(value))
        except OSError:
            continue
    return paths


def _common_chrome_paths() -> list[str]:
    if sys.platform == "win32":
        paths = []
        for env_name in ["PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"]:
            base = os.getenv(env_name)
            if base:
                paths.append(str(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"))
        return paths

    if sys.platform == "darwin":
        return ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]

    return [
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/snap/bin/chromium",
    ]


def find_chrome_executable() -> tuple[str | None, str | None]:
    explicit_path = _existing_file(TIKTOK_CHROME_PATH)
    if explicit_path:
        return explicit_path, "env"

    for path in _registry_chrome_paths():
        existing = _existing_file(path)
        if existing:
            return existing, "registry"

    for command in ["chrome", "chrome.exe", "google-chrome", "google-chrome-stable"]:
        path = shutil.which(command)
        existing = _existing_file(path)
        if existing:
            return existing, "path"

    for path in _common_chrome_paths():
        existing = _existing_file(path)
        if existing:
            return existing, "default_path"

    return None, None


def _chrome_version(executable_path: str) -> str | None:
    if sys.platform == "win32":
        application_dir = Path(executable_path).parent
        versions = [
            child.name
            for child in application_dir.iterdir()
            if child.is_dir() and re.match(r"^\d+\.\d+\.\d+\.\d+$", child.name)
        ]
        return sorted(versions)[-1] if versions else None

    try:
        creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        completed = subprocess.run(
            [executable_path, "--version"],
            capture_output=True,
            text=True,
            timeout=3,
            creationflags=creation_flags,
            check=False,
        )
    except Exception:
        return None

    output = (completed.stdout or completed.stderr or "").strip()
    return output or None


def get_chrome_status() -> dict[str, Any]:
    executable_path, source = find_chrome_executable()
    available = bool(executable_path)

    return {
        "browser": "chrome",
        "available": available,
        "source": source,
        "executablePath": executable_path,
        "version": _chrome_version(executable_path) if executable_path else None,
        "message": (
            "Google Chrome encontrado."
            if available
            else "Google Chrome nao encontrado. Instale o Google Chrome para publicar no TikTok."
        ),
    }
