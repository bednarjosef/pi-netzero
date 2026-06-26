# pi-netzero

Phone-controlled Wi-Fi recon for the **Raspberry Pi Zero 2 W** — no external
adapter, no second device. The onboard radio runs in monitor mode full-time
(via Nexmon), and your **Android phone drives it over a single USB cable**.

It fuses the working scapy logic from `netzero` (streaming scanner) and
`network-security` (deauth / handshake / PMKID) into one FastAPI + WebSocket
service with a touch-friendly web UI.

```
 ┌──────────┐   USB cable (CDC NCM Ethernet)  ┌────────────────┐  onboard Wi-Fi  ┌────────────┐
 │ Android  │◄═══════════════════════════════►│  Pi Zero 2 W   │  monitor (Nexmon)│ your network│
 │ browser  │  http://10.55.0.1               │  wlan0 → monitor│════════════════►│            │
 └──────────┘                                 └────────────────┘                 └────────────┘
```

## Why this shape

A single radio can't be in monitor mode **and** be your phone's Wi-Fi link at the
same time — monitor mode detaches the interface from all networking. So the
control channel is moved off Wi-Fi onto **USB**: the Pi presents itself as a
USB-Ethernet (CDC NCM) device, the phone reaches the web UI at `http://10.55.0.1`,
and the Wi-Fi radio is free to capture the whole time.

> **Why NCM, not RNDIS?** Modern Android (Pixel and others) dropped the RNDIS
> USB-host driver, so an RNDIS gadget only ever shows up as "Charging connected
> device…" with no network interface. CDC NCM is supported natively by Android,
> Linux, macOS and Windows 10/11.

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

#### Reaching the UI from the phone

Connect the Pi to the phone with a **plain micro-USB↔USB-C cable** (the phone is
the USB host and powers the Pi — no power bank, see Power). The Pi presents as a
CDC-NCM USB-Ethernet device. There are two ways to reach it, and the
`pitail-uplink-monitor` service switches between them automatically:

**Local-only — offline capture, no tethering (default).** Just plug in. The Pi
serves the phone an address, so with the phone's **mobile data/Wi-Fi off** you
open **`http://192.168.42.254:8080`** and run scans, handshakes, PMKID and deauth
captures. Everything *except* Vast.ai cracking works here — no internet needed.

**Tethered — full use with mobile data ON.** Turn on **Settings → Network &
internet → Hotspot & tethering → Ethernet tethering** (keep mobile data on). The
**phone** now owns the link (DHCP + gateway + NAT): the Pi steps its own DHCP
server aside, accepts the phone's address, and **pushes the URL to open to ntfy**
— e.g. `✅ Pi ONLINE — open http://10.21.224.60:8080` (a phone-assigned IP,
normally stable per phone). The phone keeps mobile data, reaches the UI, **and**
the Pi gets the internet Vast.ai needs.

> **Why two modes?** With mobile data ON, Android refuses to route the browser to
> a USB network that has no internet of its own — so a plain plug-in won't load
> with data on. Tethering fixes that by making the phone own the link (and feeds
> the Pi internet as a bonus); with data OFF the USB link is the only network, so
> simple local-only mode works. The Pi must *not* run its own DHCP server *during*
> tethering (Android would pick a conflicting subnet and nothing connects), so the
> monitor stops it automatically while tethered and restarts it after. The static
> `192.168.42.254` is also the address for a directly-wired laptop (laptop side
> static `192.168.42.129/24`).

> **Gadget protocol & a dead end.** `pi-tail-ncm.service` swaps Pi-Tail's stock
> `g_ether` (whose RNDIS config modern Android can't drive — it shows only
> "Charging connected device…") to **CDC NCM** at boot; `g_ether` stays in
> `cmdline.txt` as a fail-safe. USB *host* mode (to use ordinary phone USB
> tethering) does **not** work over a micro-USB↔USB-C cable: it can't ground the
> Pi's OTG ID pin, so the Pi stays a device — hence Ethernet tethering is the
> path.

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

1. Connect the Pi to your phone with a plain micro-USB↔USB-C cable (Pi's inner
   **USB** port, not `PWR`). On **Pi-Tail** the phone powers the Pi — **no power
   bank** (a second 5 V source on the Zero's shared rail drops the link); see Power.
2. Open the UI:
   - **Pi-Tail, offline capture** (no internet): mobile data off → **http://192.168.42.254:8080**.
   - **Pi-Tail, with internet** (Vast): turn on **Ethernet tethering** (data ON) →
     open the `http://<ip>:8080` from the ntfy push.
   - **Plain image:** **http://10.55.0.1**.
3. **Scan Networks** → tap a network to target it → **Scan Clients**,
   **Handshake**, **PMKID**, or **Deauth**. Captures appear in the Captures
   panel as downloadable `.pcap` files.

## Power

The Zero 2 W ties both micro-USB ports to one 5 V rail, so you **can't just add a
power bank** while the phone also powers the data port — the two sources fight and
the USB link drops. With the **Ethernet-tethering** setup the **phone powers the
Pi** over the single data cable (no power bank).

Phone-only power is fine for the control link and light use, but a Zero 2 W doing
sustained capture on phone power alone can brown out (corrupting a capture). For
heavy sessions, power the Pi from a power bank on **PWR** and use a **data-only
(power-blocking)** cable to the phone so it carries data without VBUS — that
avoids the contention. (The plain-image path doesn't tether, so it can just use
power bank → `PWR`, data cable → phone.)

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
  usb_gadget.sh           CDC NCM USB-Ethernet gadget up/down (+ dnsmasq)
  dnsmasq-usb0.conf        DHCP for the cable, no default route
  pi-netzero-usb.service   systemd: bring the gadget up at boot (plain image)
  pi-netzero.service       systemd: run the server (plain image)
  install.sh               installer (plain Kali image, self-contained)
  pi-netzero-pitail.service systemd: run the server on Pi-Tail (app only)
  pi-tail-ncm.sh           swap Pi-Tail's g_ether(RNDIS) gadget -> CDC NCM (Android)
  pi-tail-ncm.service      systemd: apply the NCM swap on boot (Pi-Tail)
  pitail-uplink-monitor.sh DHCP client for the phone's Ethernet tethering + ntfy online status (Pi-Tail)
  pitail-uplink-monitor.service systemd: run the uplink monitor (Pi-Tail)
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
GET  /api/v1/hashes | /hashes/{name}      captured hc22000 hashes + status
POST /api/v1/crack/{name}                 launch a Vast.ai GPU crack for a hash
GET  /api/v1/crack/instances              running crack jobs
DELETE /api/v1/crack/instances/{id}       kill a crack job
WS   /ws/v1/stream                        live status/log/network/client/capture/hash events
```

## Cracking captured hashes (Vast.ai)

Successful handshakes/PMKIDs auto-convert to hashcat `hc22000` (via `hcxtools`)
and appear in the **Hashes** tab. Tap **Crack** to rent a GPU on
[Vast.ai](https://vast.ai) that runs hashcat **mode 22000** in stages —
`rockyou` → all 8-digit numbers (`?d×8`) → `all-h.txt` (streamed from the
weakpass torrent) — pushes the result to your phone via **ntfy**, and
**self-destructs** (hard ~3h cap + on completion) so it can't drain credits.

One-time setup on the Pi:
```bash
echo "YOUR_VAST_API_KEY" | sudo tee /opt/pi-netzero/vast.key   # from vast.ai → Account → API key
sudo systemctl restart pi-netzero
```
Then install the **ntfy** app on your phone and subscribe to the topic shown at
the top of the Hashes tab (e.g. `pinetzero-xxxx`). The Pi must have internet when
you launch a crack (it's the Pi that calls the Vast API) — that's exactly what the
**Ethernet tethering** link above provides; the `pitail-uplink-monitor` ntfy push
confirms the Pi is online before you crack.

## Config (env vars)

| var | default | meaning |
|---|---|---|
| `PI_NETZERO_IFACE` | `wlan0` | Wi-Fi interface to put in monitor mode |
| `PI_NETZERO_PORT` | `80` | HTTP port |
| `PI_NETZERO_CAPTURES` | `<repo>/captures` | where `.pcap` files are written |
| `PI_NETZERO_CHANNELS` | `1..11` | channels to hop while scanning |
| `PI_NETZERO_RELEASE_RADIO` | `1` | free the radio from NetworkManager/wpa_supplicant first (set `0` on Pi-Tail) |
| `PI_NETZERO_MONITOR_UP_CMD` | _(empty)_ | command to create the monitor vif if missing (Pi-Tail: `mon0up`) |
| `PI_NETZERO_DOWN_IFACE` | _(empty)_ | managed iface to down so the monitor vif owns the radio (Pi-Tail: `wlan0`) |
| `PI_NETZERO_VAST_KEY` | _(empty)_ | Vast.ai API key (or put it in `vast.key`) |
| `PI_NETZERO_NTFY_TOPIC` | auto | ntfy.sh topic for crack notifications |
| `PI_NETZERO_CRACK_GPU` | `RTX_4090` | GPU to rent for cracking |
| `PI_NETZERO_CRACK_MAX_HOURS` | `3` | hard auto-kill timer on crack jobs |

## ⚠️ Authorization

Monitor-mode capture is passive, but **deauth, handshake, PMKID, and cracking
are active operations**. Only run them against networks you own or are
explicitly authorized to test. Injection on the Broadcom/Nexmon radio works but
is less reliable than a dedicated Atheros/Realtek adapter — expect occasional
misfires.
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
