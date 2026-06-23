"""Captured-hash storage.

Converts a capture (.pcap/.pcapng) to hashcat's unified WPA format (mode 22000,
``.hc22000``) with hcxtools, and keeps a small JSON index keyed by network so the
UI can list hashes and track crack status (captured -> cracking -> cracked).
"""

import json
import re
import subprocess
import time
from pathlib import Path
from threading import Lock

from app.config import HASH_DIR


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", s or "net") or "net"


def convert(pcap_path: str) -> str | None:
    """Convert a capture to .hc22000. Returns the path if it contains ≥1 hash,
    else None (e.g. an incomplete handshake produces an empty file)."""
    HASH_DIR.mkdir(parents=True, exist_ok=True)
    out = HASH_DIR / (Path(pcap_path).stem + ".hc22000")
    try:
        subprocess.run(
            ["hcxpcapngtool", "-o", str(out), pcap_path],
            capture_output=True, text=True, timeout=60,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if out.exists() and out.stat().st_size > 0:
        return str(out)
    if out.exists():
        out.unlink()
    return None


class HashStore:
    """Thread-safe index over HASH_DIR/index.json."""

    def __init__(self):
        HASH_DIR.mkdir(parents=True, exist_ok=True)
        self._path = HASH_DIR / "index.json"
        self._lock = Lock()
        self._items = self._load()

    def _load(self):
        try:
            return json.loads(self._path.read_text())
        except (OSError, ValueError):
            return {}

    def _save(self):
        try:
            self._path.write_text(json.dumps(self._items, indent=2))
        except OSError:
            pass

    def register(self, pcap_path, bssid, ssid, kind):
        """Convert + index a fresh capture. Returns the entry, or None if the
        capture held no usable hash."""
        hc = convert(pcap_path)
        if not hc:
            return None
        name = Path(hc).name
        with self._lock:
            self._items[name] = {
                "name": name,
                "file": hc,
                "bssid": bssid,
                "ssid": ssid,
                "kind": kind,                 # "handshake" | "pmkid"
                "size": Path(hc).stat().st_size,
                "captured_at": int(time.time()),
                "status": "captured",         # captured | cracking | cracked | exhausted
                "instance_id": None,
                "password": None,
            }
            self._save()
            return dict(self._items[name])

    def list(self):
        with self._lock:
            return sorted(self._items.values(), key=lambda h: h["captured_at"], reverse=True)

    def get(self, name):
        with self._lock:
            return dict(self._items[name]) if name in self._items else None

    def content(self, name):
        e = self.get(name)
        if not e:
            return None
        try:
            return Path(e["file"]).read_text()
        except OSError:
            return None

    def update(self, name, **fields):
        with self._lock:
            if name in self._items:
                self._items[name].update(fields)
                self._save()
                return dict(self._items[name])
        return None

    def delete(self, name):
        with self._lock:
            e = self._items.pop(name, None)
            if e:
                try:
                    Path(e["file"]).unlink()
                except OSError:
                    pass
                self._save()
            return e is not None

    def captured_stems(self):
        """Source capture stems that produced a hash (a hash 'X.hc22000' came
        from capture 'X.pcap'), so the UI can flag captures with no usable hash."""
        with self._lock:
            return {n.rsplit(".", 1)[0] for n in self._items}
