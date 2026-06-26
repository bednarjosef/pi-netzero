#!/bin/bash
# Uplink manager for the phone link (usb0). Two modes, switched automatically:
#
#   * LOCAL-ONLY (default): no tethering -> the Pi serves DHCP (dnsmasq) so a
#     plugged-in phone gets an address and can reach the UI at 192.168.42.254 for
#     offline work (scan / handshake / PMKID / capture). No internet required.
#   * TETHERED: you switch ON "Ethernet tethering" -> the PHONE becomes DHCP +
#     gateway + NAT. The monitor stops its DHCP server, grabs the phone's lease
#     (Pi gets internet for Vast.ai), and pushes the URL to open to ntfy.
#
# Local-only is kept bulletproof: dnsmasq is ALWAYS up while the link is, and we
# only probe for tethering after the link has been serving for ~90s and then
# only every ~90s — so the probe can never race the phone's initial DHCP (the bug
# that left a freshly plugged-in phone with no address). Only ever touches usb0.
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
up=0
while true; do
  ip addr add 192.168.42.254/24 dev usb0 2>/dev/null || true   # keep the stable local address
  state=down
  if online; then
    serve_off; state=up; up=0                  # tethered with internet
  elif ip link show usb0 up >/dev/null 2>&1; then
    up=$((up + 1))
    serve_on                                   # ALWAYS serve so a connecting phone finds DHCP
    if [ "$up" -ge 9 ] && [ $((up % 9)) -eq 0 ]; then   # first probe ~90s after link-up, then every ~90s
      serve_off
      ip route flush default 2>/dev/null || true
      rm -f /var/lib/dhcp/dhclient.usb0.leases /var/lib/dhcp/dhclient.leases 2>/dev/null
      timeout 8 dhclient -1 usb0 >>"$LOG" 2>&1 || true
      ip addr add 192.168.42.254/24 dev usb0 2>/dev/null || true   # restore immediately
      if online; then state=up; else serve_on; fi
    fi
  else
    up=0
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
