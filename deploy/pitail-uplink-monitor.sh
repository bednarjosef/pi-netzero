#!/bin/bash
# Uplink manager for the phone link (usb0). Two modes, switched automatically:
#
#   * LOCAL-ONLY (default): no tethering -> the Pi serves DHCP (dnsmasq) so a
#     plugged-in phone gets an address and can reach the UI at 192.168.42.254 for
#     offline work (scan / handshake / PMKID / capture). No internet required.
#   * TETHERED: you switch ON "Ethernet tethering" on the phone -> the PHONE
#     becomes DHCP server + gateway + NAT. The monitor STOPS its own DHCP server
#     (so the Pi's dhclient hears the phone, not itself), grabs the phone's lease
#     (giving the Pi internet for Vast.ai), and pushes the URL to open to ntfy.
#
# It probes for tethering ~every 60s by briefly stopping its DHCP server and
# asking for a lease; an active local-only phone keeps its address across that
# blip (the lease is held client-side). Only ever touches usb0; never wlan0/mon0.
LOG=/opt/pi-netzero/uplink.log
TOPIC=$(cat /opt/pi-netzero/ntfy.topic 2>/dev/null)
DHCP=pi-netzero-usb-dhcp.service
log(){ echo "$(date '+%F %T') | $*" >> "$LOG" 2>/dev/null; logger -t pitail-uplink "$*"; }
push(){ log "PUSH: $1"; [ -n "$TOPIC" ] && curl -s -m 6 -H "Title: pi-netzero uplink" -d "$1" "https://ntfy.sh/$TOPIC" >/dev/null 2>&1 || true; }
online(){ curl -s -m 5 -o /dev/null https://1.1.1.1 2>/dev/null; }
serve_on(){  systemctl is-active --quiet "$DHCP" || { systemctl start "$DHCP" 2>/dev/null && log "local-only: DHCP server ON (phone -> http://192.168.42.254:8080)"; }; }
serve_off(){ systemctl is-active --quiet "$DHCP" && { systemctl stop  "$DHCP" 2>/dev/null && log "tethered: DHCP server OFF (phone owns the link)"; }; }

log "===== monitor (re)started ====="
prev=init
n=0
while true; do
  ip addr add 192.168.42.254/24 dev usb0 2>/dev/null || true   # keep the stable local address
  state=down
  if online; then
    serve_off                                    # tethered with internet
    state=up
  elif ip link show usb0 up >/dev/null 2>&1; then
    if [ $((n % 6)) -eq 0 ]; then                # ~every 60s: look for fresh tethering
      serve_off
      ip route flush default 2>/dev/null || true
      rm -f /var/lib/dhcp/dhclient.usb0.leases /var/lib/dhcp/dhclient.leases 2>/dev/null
      timeout 10 dhclient -1 usb0 >>"$LOG" 2>&1 || true
      if online; then state=up; else serve_on; fi # got tether internet, else back to local-only
    else
      serve_on                                   # local-only: keep serving the phone
    fi
    n=$((n + 1))
  fi
  if [ "$state" != "$prev" ]; then
    if [ "$state" = up ]; then
      ip=$(ip route get 1.1.1.1 2>/dev/null | grep -oE 'src [0-9.]+' | awk '{print $2}')
      gw=$(ip route show default 2>/dev/null | grep -oE 'via [0-9.]+' | awk '{print $2}' | head -1)
      vast=$(curl -s -m 8 -o /dev/null -w '%{http_code}' https://console.vast.ai/api/v0/ 2>/dev/null)
      push "✅ Pi ONLINE (tethered) — open http://${ip}:8080 on the phone · internet via gw ${gw} · Vast HTTP ${vast}"
    fi
    prev=$state
  fi
  sleep 10
done
