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
