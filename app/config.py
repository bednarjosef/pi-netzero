"""Central configuration. Everything is overridable via environment variables so
the same code runs on the Pi (defaults) and on a dev laptop."""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


# --- Wi-Fi radio --------------------------------------------------------------
# Single onboard radio, flipped in place to monitor mode (Nexmon keeps the name
# as `wlan0`; no separate `wlan0mon` vif is created). Override if your setup
# uses airmon-ng and produces a different monitor interface name.
IFACE = _env("PI_NETZERO_IFACE", "wlan0")

# Pi Zero 2 W is 2.4 GHz only.
CHANNELS = [int(c) for c in _env("PI_NETZERO_CHANNELS", "1,2,3,4,5,6,7,8,9,10,11").split(",")]
CHANNEL_HOP_DELAY = float(_env("PI_NETZERO_HOP_DELAY", "0.5"))

# Best-effort: free the radio from NetworkManager / wpa_supplicant before
# entering monitor mode. Safe because the phone link is USB, not Wi-Fi.
# On Pi-Tail set this to 0 — Pi-Tail owns the radio.
RELEASE_RADIO = _env("PI_NETZERO_RELEASE_RADIO", "1") == "1"

# Optional command that creates the monitor interface if it's missing. On
# Pi-Tail this is `mon0up`, which brings up the `mon0` monitor vif. Empty (the
# default) means pi-netzero flips the interface into monitor mode itself.
MONITOR_UP_CMD = _env("PI_NETZERO_MONITOR_UP_CMD", "")

# Interface to bring DOWN before capturing. On Pi-Tail `mon0up` adds a monitor
# vif but leaves wlan0 up in managed mode on the same radio — it pins the
# channel and starves the monitor interface (captures nothing). Downing it lets
# the monitor vif own the radio. Empty = leave other interfaces alone.
DOWN_IFACE = _env("PI_NETZERO_DOWN_IFACE", "")

# --- Server -------------------------------------------------------------------
HOST = _env("PI_NETZERO_HOST", "0.0.0.0")
PORT = int(_env("PI_NETZERO_PORT", "80"))

# --- Captures -----------------------------------------------------------------
CAPTURE_DIR = Path(_env("PI_NETZERO_CAPTURES", str(PROJECT_ROOT / "captures")))

# --- 802.11 constants ---------------------------------------------------------
BROADCAST = "ff:ff:ff:ff:ff:ff"
DEAUTH_BURST = int(_env("PI_NETZERO_DEAUTH_BURST", "64"))   # frames per burst
EAPOL_MIC_BIT = 0x0100
EAPOL_SECURE_BIT = 0x0200

# Default capture timeouts (seconds)
HANDSHAKE_TIMEOUT = int(_env("PI_NETZERO_HANDSHAKE_TIMEOUT", "60"))
PMKID_TIMEOUT = int(_env("PI_NETZERO_PMKID_TIMEOUT", "10"))

# --- Hash storage + cracking -------------------------------------------------
HASH_DIR = Path(_env("PI_NETZERO_HASHES", str(PROJECT_ROOT / "hashes")))


def _read_key():
    """Vast.ai API key from env or a gitignored file (keeps it out of the repo)."""
    k = os.environ.get("PI_NETZERO_VAST_KEY", "").strip()
    if k:
        return k
    f = PROJECT_ROOT / "vast.key"
    try:
        return f.read_text().strip()
    except OSError:
        return ""


VAST_API_KEY = _read_key()

# ntfy.sh topic for crack notifications. Auto-generated + persisted if unset, so
# there's always a working push channel (subscribe to it in the ntfy app).
def _ntfy_topic():
    t = os.environ.get("PI_NETZERO_NTFY_TOPIC", "").strip()
    if t:
        return t
    f = PROJECT_ROOT / "ntfy.topic"
    try:
        return f.read_text().strip()
    except OSError:
        # derive a stable, non-guessable-ish topic from the machine id
        try:
            mid = Path("/etc/machine-id").read_text().strip()[:12]
        except OSError:
            mid = "pi"
        topic = f"pinetzero-{mid}"
        try:
            f.write_text(topic + "\n")
        except OSError:
            pass
        return topic


NTFY_TOPIC = _ntfy_topic()

# Crack-job sizing (the user's choice: fast GPU, hard auto-kill).
CRACK_GPU = _env("PI_NETZERO_CRACK_GPU", "RTX_4090")
CRACK_MAX_DPH = float(_env("PI_NETZERO_CRACK_MAX_DPH", "0.7"))   # $/hr ceiling for the offer
CRACK_MAX_HOURS = float(_env("PI_NETZERO_CRACK_MAX_HOURS", "3"))  # hard auto-kill
CRACK_DISK_GB = int(_env("PI_NETZERO_CRACK_DISK", "60"))          # all-h.txt unpacks ~30GB
CRACK_IMAGE = _env("PI_NETZERO_CRACK_IMAGE", "nvidia/cuda:12.4.1-runtime-ubuntu22.04")
ALL_H_TORRENT = _env("PI_NETZERO_ALLH_TORRENT", "https://weakpass.com/download/2085/all-h.txt.7z.torrent")
ROCKYOU_URL = _env("PI_NETZERO_ROCKYOU_URL",
                   "https://github.com/brannondorsey/naive-hashcat/releases/download/data/rockyou.txt")
