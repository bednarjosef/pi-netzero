"""Radio control: monitor mode + channel selection for the single onboard chip.

On the Pi Zero 2 W the Broadcom radio is patched by Nexmon (ships in Kali for
the Pi). Monitor mode is enabled by flipping the existing interface in place
(`iw dev wlan0 set type monitor`) rather than adding a `wlan0mon` vif — the
interface name stays `wlan0`. Because the phone reaches us over USB, dedicating
the Wi-Fi radio to monitor full-time costs us nothing.
"""

import os
import subprocess
import sys
from contextlib import contextmanager
from threading import Event, Thread

from app.config import IFACE, CHANNELS, RELEASE_RADIO, MONITOR_UP_CMD


def ensure_root():
    if os.geteuid() != 0:
        sys.exit("pi-netzero must run as root (it manipulates the Wi-Fi radio). Use sudo.")


def _run(cmd, check=False, timeout=10):
    """Run a system command, bounded by a timeout so a slow/blocking call (e.g.
    a polkit prompt, a wedged service) can never freeze the controller thread.
    A timeout or missing binary is treated as a soft failure (rc != 0); real
    non-zero exits still raise when check=True so the caller can surface them."""
    try:
        return subprocess.run(cmd, check=check, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", "timed out")
    except FileNotFoundError:
        return subprocess.CompletedProcess(cmd, 127, "", "not found")


def list_interfaces():
    interfaces = []
    result = _run(["iw", "dev"])
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("Interface"):
            interfaces.append(line.split()[1])
    return interfaces


def interface_type(iface=IFACE):
    """Return the current type of the interface ('monitor', 'managed', ...)."""
    result = _run(["iw", "dev", iface, "info"])
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("type "):
            return line.split(None, 1)[1]
    return "unknown"


def is_monitor(iface=IFACE) -> bool:
    return interface_type(iface) == "monitor"


def enable_monitor(iface=IFACE):
    """Put the onboard radio into monitor mode (idempotent).

    Refuses to do anything — including releasing the radio from
    NetworkManager/wpa_supplicant — unless the configured interface actually
    exists. This makes running on a machine without that interface (e.g. a dev
    laptop) a safe no-op instead of disturbing the host's Wi-Fi.
    """
    if iface not in list_interfaces():
        # On Pi-Tail the monitor vif (mon0) is created on demand by `mon0up`.
        if MONITOR_UP_CMD:
            _run(MONITOR_UP_CMD.split(), timeout=20)
        if iface not in list_interfaces():
            raise RuntimeError(
                f"Interface {iface!r} not found (have: {list_interfaces()}). "
                + (f"`{MONITOR_UP_CMD}` did not create it. " if MONITOR_UP_CMD else "")
                + "Refusing to touch the radio. Check PI_NETZERO_IFACE / PI_NETZERO_MONITOR_UP_CMD."
            )

    if is_monitor(iface):
        return

    if RELEASE_RADIO:
        # Best-effort: stop whatever owns the radio. Safe — control is over USB.
        _run(["nmcli", "device", "set", iface, "managed", "no"])
        _run(["systemctl", "stop", "wpa_supplicant"])

    _run(["ip", "link", "set", iface, "down"], check=True)
    # Primary path (Nexmon / mac80211): flip type in place.
    r = _run(["iw", "dev", iface, "set", "type", "monitor"])
    if r.returncode != 0:
        # Fallback for drivers that prefer the `set monitor` form.
        _run(["iw", "dev", iface, "set", "monitor", "none"])
    _run(["ip", "link", "set", iface, "up"], check=True)

    if not is_monitor(iface):
        raise RuntimeError(
            f"Failed to enable monitor mode on {iface}. "
            "On the Pi this needs Nexmon (use Kali for the Pi Zero 2 W)."
        )


def disable_monitor(iface=IFACE):
    """Return the radio to managed mode (rarely needed; mainly for cleanup)."""
    _run(["ip", "link", "set", iface, "down"])
    _run(["iw", "dev", iface, "set", "type", "managed"])
    _run(["ip", "link", "set", iface, "up"])
    if RELEASE_RADIO:
        _run(["nmcli", "device", "set", iface, "managed", "yes"])


def set_channel(channel, iface=IFACE):
    _run(["iw", "dev", iface, "set", "channel", str(channel)])


@contextmanager
def channel_hopper(delay, iface=IFACE, channels=None):
    """Hop across 2.4 GHz channels in a background thread for the duration."""
    channels = channels or CHANNELS
    stop = Event()

    def _hop():
        while not stop.is_set():
            for ch in channels:
                set_channel(ch, iface)
                if stop.wait(timeout=delay):
                    return

    t = Thread(target=_hop, daemon=True)
    t.start()
    try:
        yield
    finally:
        stop.set()
        t.join()
