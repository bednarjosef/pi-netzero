#!/bin/bash
# Uplink manager + monitor for the phone link (usb0).
#
# The Pi's own DHCP server is disabled, so when you switch ON tethering the PHONE
# becomes the DHCP server + gateway + NAT. This grabs that lease with dhclient
# (fresh each time — no stale renew), confirms real internet, pushes the result
# to ntfy, AND writes a detailed diagnostic trail to /opt/pi-netzero/uplink.log
# so a connection can be debugged after the fact. Only ever touches usb0.
LOG=/opt/pi-netzero/uplink.log
TOPIC=$(cat /opt/pi-netzero/ntfy.topic 2>/dev/null)
log(){ echo "$(date '+%F %T') | $*" >> "$LOG" 2>/dev/null; logger -t pitail-uplink "$*"; }
push(){ log "PUSH: $1"; [ -n "$TOPIC" ] && curl -s -m 6 -H "Title: pi-netzero uplink" -d "$1" "https://ntfy.sh/$TOPIC" >/dev/null 2>&1 || true; }
online(){ curl -s -m 5 -o /dev/null https://1.1.1.1 2>/dev/null; }

log "===== monitor (re)started ====="
prev=init
while true; do
  # keep the Pi's stable .254 for direct/laptop access (coexists with a lease)
  ip addr add 192.168.42.254/24 dev usb0 2>/dev/null || true
  state=down
  if online; then
    state=up
  elif ip link show usb0 up >/dev/null 2>&1; then
    log "OFFLINE — asking the phone for a fresh lease on usb0"
    ip route flush default 2>/dev/null || true                       # drop any dead default
    rm -f /var/lib/dhcp/dhclient.usb0.leases /var/lib/dhcp/dhclient.leases 2>/dev/null  # force fresh DISCOVER
    timeout 15 dhclient -1 -v usb0 >>"$LOG" 2>&1 || true
    GW=$(ip route show default 2>/dev/null | grep -oE 'via [0-9.]+' | awk '{print $2}' | head -1)
    log "  usb0   = $(ip -4 -br addr show usb0)"
    log "  routes = $(ip route show default 2>/dev/null | tr '\n' ';')"
    if [ -n "$GW" ]; then ping -c1 -W2 "$GW" >/dev/null 2>&1 && log "  ping gw $GW = OK" || log "  ping gw $GW = FAIL"; else log "  (no gateway leased)"; fi
    ping -c1 -W2 1.1.1.1 >/dev/null 2>&1 && log "  ping 1.1.1.1 = OK" || log "  ping 1.1.1.1 = FAIL"
    online && state=up
    log "  => online=$state"
  fi
  if [ "$state" != "$prev" ]; then
    if [ "$state" = up ]; then
      ip=$(ip route get 1.1.1.1 2>/dev/null | grep -oE 'src [0-9.]+' | awk '{print $2}')
      gw=$(ip route show default 2>/dev/null | grep -oE 'via [0-9.]+' | awk '{print $2}' | head -1)
      vast=$(curl -s -m 8 -o /dev/null -w '%{http_code}' https://console.vast.ai/api/v0/ 2>/dev/null)
      push "✅ Pi ONLINE — open http://${ip}:8080 on the phone · internet via gw ${gw} · Vast HTTP ${vast}"
    elif [ "$prev" != init ]; then
      push "⚠️ Pi offline"
    fi
    prev=$state
  fi
  sleep 10
done
