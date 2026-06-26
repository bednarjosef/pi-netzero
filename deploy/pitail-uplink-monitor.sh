#!/bin/bash
# Uplink manager for the phone link (usb0), two modes switched automatically:
#   LOCAL-ONLY (default): no tethering -> the Pi serves DHCP so a plugged-in phone
#     reaches the UI at http://netzero.box for offline capture. No internet needed.
#   TETHERED: turn on "Ethernet tethering" -> the phone becomes DHCP+gateway+NAT;
#     the monitor stops its own DHCP server, leases an address from the phone (so
#     the Pi gets internet for Vast), and pushes a tappable ntfy that opens the UI.
# The first probe waits ~40s after link-up so it can't race the phone's initial
# DHCP, then runs ~every 20s. Only ever touches usb0, never the Wi-Fi radio.
TOPIC=$(cat /opt/pi-netzero/ntfy.topic 2>/dev/null)
DHCP=pi-netzero-usb-dhcp.service
log(){ logger -t pitail-uplink "$*"; }
online(){ curl -s -m 4 -o /dev/null https://1.1.1.1 2>/dev/null; }
serve_on(){  systemctl is-active --quiet "$DHCP" || systemctl start "$DHCP" 2>/dev/null; }
serve_off(){ systemctl is-active --quiet "$DHCP" && systemctl stop  "$DHCP" 2>/dev/null; }

prev=init; up=0
while true; do
  ip addr add 192.168.42.254/24 dev usb0 2>/dev/null || true   # keep the stable local address
  state=down
  if online; then
    serve_off; state=up; up=0
  elif ip link show usb0 up >/dev/null 2>&1; then
    up=$((up + 1)); serve_on                                   # always serve so a connecting phone finds DHCP
    if [ "$up" -ge 4 ] && [ $((up % 2)) -eq 0 ]; then          # probe for tethering ~every 20s
      serve_off
      ip route flush default 2>/dev/null || true
      rm -f /var/lib/dhcp/dhclient.usb0.leases /var/lib/dhcp/dhclient.leases 2>/dev/null
      timeout 5 dhclient -1 usb0 >/dev/null 2>&1 || true
      ip addr add 192.168.42.254/24 dev usb0 2>/dev/null || true
      online && state=up || serve_on
    fi
  else
    up=0
  fi
  if [ "$state" != "$prev" ] && [ "$state" = up ]; then        # just gained internet -> notify, tappable
    ip=$(ip route get 1.1.1.1 2>/dev/null | grep -oE 'src [0-9.]+' | awk '{print $2}')
    log "tethered online -> http://${ip}"
    [ -n "$TOPIC" ] && [ -n "$ip" ] && curl -s -m 6 \
      -H "Title: pi-netzero is online" -H "Tags: white_check_mark" -H "Click: http://${ip}" \
      -d "Tethered — tap to open the UI. Vast is ready." "https://ntfy.sh/$TOPIC" >/dev/null 2>&1 || true
  fi
  prev=$state
  sleep 10
done
