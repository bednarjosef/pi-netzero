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
- One of two Kali images (both ship **Nexmon** for onboard monitor mode +
  injection; plain Raspberry Pi OS will not do monitor mode on the onboard chip):
  - **Pi-Tail (recommended)** — Kali's image purpose-built to drive a Pi Zero 2 W
    from a phone. It already provides USB-gadget phone control, SSH/VNC, and a
    `mon0up` helper for monitor mode. You run *only* the app on top.
  - **Plain Kali ARM image** — `pi-netzero` then sets up its own USB gadget and
    becomes a self-contained appliance.
- An Android phone (USB host capable — most are)
- A small power bank (recommended — see Power below)

## Install

### A. Pi-Tail (recommended)

Pi-Tail owns the USB-gadget link and monitor mode, so install only the app:

```bash
git clone https://github.com/bednarjosef/pi-netzero ~/pi-netzero
cd ~/pi-netzero
sudo deploy/install-pitail.sh
sudo reboot
```

> **⚠️ Critical Pi-Tail step (Zero 2 W onboard Wi-Fi).** Out of the box Pi-Tail
> tries to associate `wlan0` with the `sepultura` hotspot. On the Zero 2 W's
> Broadcom chip that **station-mode association crashes the Wi-Fi firmware**
> (`brcmf_fw_crashed`, the SDIO card drops off and never comes back until
> reboot) — so monitor mode never works. **Disable the `wlan0` station stanza**
> in `/boot/firmware/interfaces` (comment out `allow-hotplug wlan0` and its
> `iface wlan0 … wpa-roam …` block) and mask wpa_supplicant:
> ```bash
> sudo sed -i '/allow-hotplug wlan0/,+3 s/^/#/' /boot/firmware/interfaces
> sudo systemctl mask wpa_supplicant
> ```
> The chip then stays alive and monitor mode works. `install-pitail.sh` does
> this for you; the snippet above is what it runs.

The service is preconfigured (`PI_NETZERO_IFACE=mon0`, `RELEASE_RADIO=0`,
`PORT=8080`, `MONITOR_UP_CMD=mon0up`, `DOWN_IFACE=wlan0`) and an `ExecStartPre`
waits for the radio, runs `mon0up`, and downs `wlan0` so the `mon0` monitor vif
owns the channel. Notes on why each is needed:

- **`mon0up` (not flipping wlan0)** — nexmon only delivers frames through a
  dedicated `mon0` vif; setting `wlan0` itself to `type monitor` reports success
  but captures nothing.
- **`DOWN_IFACE=wlan0`** — leaving `wlan0` up pins the radio's channel and
  starves the hopping monitor vif (scans come back empty).

Then from your phone (connected to Pi-Tail over USB) open
**`http://<pi-tail-ip>:8080`** — the same IP you SSH to Pi-Tail on
(`ip a show usb0`; for a PC over USB it's `192.168.42.254`).

> Onboard Nexmon monitor on the Zero 2 W is real but **marginal** — capture
> counts vary run to run and injection is less reliable than a dedicated
> adapter. For heavy use, an external Atheros/Realtek adapter is steadier.

### B. Plain Kali ARM image (self-contained appliance)

```bash
git clone https://github.com/bednarjosef/pi-netzero ~/pi-netzero
cd ~/pi-netzero
sudo deploy/install.sh
sudo reboot
```

`install.sh` sets up the venv, enables the `dwc2` USB gadget, installs the
systemd services (auto-start on boot), and runs a private dnsmasq on the cable.
The phone then reaches the UI at **http://10.55.0.1**.

## Use

1. Power the Pi from a power bank via the **PWR** port.
2. Run a data cable from the Pi's **USB** port (the inner one, labelled `USB`,
   not `PWR`) to your phone.
3. Open the UI: **`http://<pi-tail-ip>:8080`** (Pi-Tail) or **http://10.55.0.1**
   (plain image).
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
  pi-netzero-usb.service   systemd: bring the gadget up at boot (plain image)
  pi-netzero.service       systemd: run the server (plain image)
  install.sh               installer (plain Kali image, self-contained)
  pi-netzero-pitail.service systemd: run the server on Pi-Tail (app only)
  install-pitail.sh        installer for Pi-Tail (app only, no USB gadget)
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
| `PI_NETZERO_RELEASE_RADIO` | `1` | free the radio from NetworkManager/wpa_supplicant first (set `0` on Pi-Tail) |
| `PI_NETZERO_MONITOR_UP_CMD` | _(empty)_ | command to create the monitor vif if missing (Pi-Tail: `mon0up`) |

## ⚠️ Authorization

Monitor-mode capture is passive, but **deauth, handshake, and PMKID are active
operations**. Only run them against networks you own or are explicitly
authorized to test. Injection on the Broadcom/Nexmon radio works but is less
reliable than a dedicated Atheros/Realtek adapter — expect occasional misfires.
```
