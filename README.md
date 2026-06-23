# pi-netzero

Phone-controlled Wi-Fi recon for the **Raspberry Pi Zero 2 W** — no external
adapter, no second device. The onboard radio runs in monitor mode full-time
(via Nexmon), and your **Android phone drives it over a single USB cable**.

It fuses the working scapy logic from `netzero` (streaming scanner) and
`network-security` (deauth / handshake / PMKID) into one FastAPI + WebSocket
service with a touch-friendly web UI.

```
 ┌──────────┐   USB cable (RNDIS Ethernet)    ┌────────────────┐  onboard Wi-Fi  ┌────────────┐
 │ Android  │◄═══════════════════════════════►│  Pi Zero 2 W   │  monitor (Nexmon)│ your network│
 │ browser  │  http://10.55.0.1               │  wlan0 → monitor│════════════════►│            │
 └──────────┘                                 └────────────────┘                 └────────────┘
```

## Why this shape

A single radio can't be in monitor mode **and** be your phone's Wi-Fi link at the
same time — monitor mode detaches the interface from all networking. So the
control channel is moved off Wi-Fi onto **USB**: the Pi presents itself as a
USB-Ethernet (RNDIS) device, the phone reaches the web UI at `http://10.55.0.1`,
and the Wi-Fi radio is free to capture the whole time.

The DHCP handed to the phone advertises **no default route**, so plugging in the
Pi never steals the phone's cellular internet.

## Requirements

- Raspberry Pi Zero 2 W
- **Kali Linux** for the Pi Zero 2 W — it ships the Nexmon packages that give the
  onboard Broadcom chip monitor mode + injection. (Plain Raspberry Pi OS will not
  do monitor mode on the onboard chip without building Nexmon yourself.)
- An Android phone (USB host capable — most are)
- A small power bank (recommended — see Power below)

## Install

On the Pi:

```bash
git clone <this repo> ~/pi-netzero      # or copy it across
cd ~/pi-netzero
sudo deploy/install.sh
sudo reboot
```

`install.sh` sets up the venv, enables the `dwc2` USB gadget, installs the
systemd services (auto-start on boot), and runs a private dnsmasq on the cable.

## Use

1. Power the Pi from a power bank via the **PWR** port.
2. Run a data cable from the Pi's **USB** port (the inner one, labelled `USB`,
   not `PWR`) to your phone.
3. The phone auto-detects a wired/RNDIS connection. Open a browser to
   **http://10.55.0.1**.
4. **Scan Networks** → tap a network to target it → **Scan Clients**,
   **Handshake**, **PMKID**, or **Deauth**. Captures appear in the Captures
   panel as downloadable `.pcap` files.

> If the page doesn't load on first plug-in, some Android builds need a moment to
> bring up the wired link, or a toggle in *Settings → Network → Ethernet*.

## Power

Powering the Pi *and* a Wi-Fi capture purely from a phone's USB-OTG port is
unreliable (many phones limit OTG current, and a brownout corrupts the capture).
**Recommended:** power bank → Pi `PWR` port, and data cable → phone. The Pi draws
from the power bank; the phone only carries data.

## Layout

```
app/
  config.py       env-overridable settings (interface, channels, paths)
  dot11.py        802.11 parsing + frame builders (scan/deauth/pmkid/eapol)
  radio.py        Nexmon-aware monitor mode + channel control
  attacks.py      deauth, handshake capture, PMKID capture
  controller.py   single-task state machine, streams structured events
  server.py       FastAPI: REST + WebSocket, serves the UI, capture downloads
  web/index.html  mobile-first control surface
deploy/
  usb_gadget.sh           RNDIS USB-Ethernet gadget up/down (+ dnsmasq)
  dnsmasq-usb0.conf        DHCP for the cable, no default route
  pi-netzero-usb.service   systemd: bring the gadget up at boot
  pi-netzero.service       systemd: run the server
  install.sh               installer
main.py           entrypoint (sudo .venv/bin/python main.py)
```

## API (for scripting / the python client in `netzero`)

```
GET  /api/v1/health | /state | /interfaces | /networks
POST /api/v1/scan/networks/start          {}
POST /api/v1/scan/clients/start           {bssid, channel}
POST /api/v1/attack/deauth                {bssid, channel, client?, bursts?}
POST /api/v1/attack/handshake             {bssid, channel, client?, ssid?}
POST /api/v1/attack/pmkid                 {bssid, ssid, channel}
POST /api/v1/stop                         {}
GET  /api/v1/captures | /captures/{name}
WS   /ws/v1/stream                        live status/log/network/client/capture events
```

## Config (env vars)

| var | default | meaning |
|---|---|---|
| `PI_NETZERO_IFACE` | `wlan0` | Wi-Fi interface to put in monitor mode |
| `PI_NETZERO_PORT` | `80` | HTTP port |
| `PI_NETZERO_CAPTURES` | `<repo>/captures` | where `.pcap` files are written |
| `PI_NETZERO_CHANNELS` | `1..11` | channels to hop while scanning |
| `PI_NETZERO_RELEASE_RADIO` | `1` | free the radio from NetworkManager/wpa_supplicant first |

## ⚠️ Authorization

Monitor-mode capture is passive, but **deauth, handshake, and PMKID are active
operations**. Only run them against networks you own or are explicitly
authorized to test. Injection on the Broadcom/Nexmon radio works but is less
reliable than a dedicated Atheros/Realtek adapter — expect occasional misfires.
```
