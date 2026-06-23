"""Vast.ai launcher for GPU hashcat cracking.

Picks the cheapest suitable GPU offer, creates an instance whose startup script
downloads the wordlists, runs hashcat (mode 22000) in stages, pushes results to
ntfy, and self-destructs (hard time cap + on completion) so it can't drain
credits. Uses stdlib urllib only — no extra dependencies.
"""

import json
import urllib.error
import urllib.parse
import urllib.request

from app.config import (
    VAST_API_KEY, CRACK_GPU, CRACK_MAX_DPH, CRACK_MAX_HOURS, CRACK_DISK_GB,
    CRACK_IMAGE, NTFY_TOPIC, ALL_H_TORRENT, ROCKYOU_URL,
)

API = "https://console.vast.ai/api/v0"


class VastError(Exception):
    pass


def configured() -> bool:
    return bool(VAST_API_KEY)


def _req(method, path, body=None, params=None):
    if not VAST_API_KEY:
        raise VastError("No Vast API key. Put it in /opt/pi-netzero/vast.key or set PI_NETZERO_VAST_KEY.")
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {VAST_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise VastError(f"Vast API {e.code}: {e.read().decode()[:200]}")
    except urllib.error.URLError as e:
        raise VastError(f"Vast unreachable ({e.reason}) — the Pi needs internet to launch a crack.")


def find_offer():
    gpu = CRACK_GPU.replace("_", " ")
    q = {
        "verified": {"eq": True}, "rentable": {"eq": True}, "rented": {"eq": False},
        "gpu_name": {"eq": gpu}, "num_gpus": {"eq": 1},
        "dph_total": {"lte": CRACK_MAX_DPH}, "disk_space": {"gte": CRACK_DISK_GB},
        "order": [["dph_total", "asc"]], "type": "on-demand",
    }
    offers = _req("GET", "/bundles/", params={"q": json.dumps(q)}).get("offers", [])
    if not offers:
        raise VastError(f"No {gpu} offer ≤ ${CRACK_MAX_DPH}/hr with ≥{CRACK_DISK_GB}GB disk right now.")
    return offers[0]


def launch(label, hc22000):
    """Provision a crack instance. Returns {instance_id, dph, gpu}."""
    offer = find_offer()
    body = {
        "client_id": "me", "image": CRACK_IMAGE, "disk": CRACK_DISK_GB,
        "onstart": _onstart(label, hc22000), "runtype": "ssh", "label": label,
    }
    res = _req("PUT", f"/asks/{offer['id']}/", body=body)
    if not res.get("success"):
        raise VastError(f"Vast create failed: {res}")
    return {"instance_id": res.get("new_contract"),
            "dph": round(offer.get("dph_total", 0), 3),
            "gpu": offer.get("gpu_name", CRACK_GPU)}


def instances():
    return _req("GET", "/instances/", params={"owner": "me"}).get("instances", [])


def destroy(instance_id):
    return _req("DELETE", f"/instances/{instance_id}/")


def progress():
    """Latest stage/% per crack label, pushed by running instances to the quiet
    <topic>-status ntfy topic. Returns {label: "stage · NN%"}. Best-effort."""
    out = {}
    try:
        url = f"https://ntfy.sh/{NTFY_TOPIC}-status/json?poll=1&since=20m"
        with urllib.request.urlopen(url, timeout=10) as r:
            for line in r.read().decode().splitlines():
                try:
                    m = json.loads(line)
                except ValueError:
                    continue
                if m.get("event") != "message":
                    continue
                msg = m.get("message", "")
                if "|" in msg:
                    lab, _, st = msg.partition("|")
                    out[lab.strip()] = st.strip()   # later messages overwrite -> latest wins
    except Exception:
        pass
    return out


_TEMPLATE = r"""#!/bin/bash
exec > /workspace/crack.log 2>&1; set -x
TOPIC="__TOPIC__"; LABEL="__LABEL__"; KEY="__KEY__"
notify(){ curl -s -H "Title: pi-netzero" -d "$1" "https://ntfy.sh/$TOPIC" >/dev/null 2>&1 || true; }
selfdestruct(){
  ID=$(curl -s "https://console.vast.ai/api/v0/instances/?owner=me&api_key=$KEY" \
    | python3 -c "import sys,json;print(next((str(i['id']) for i in json.load(sys.stdin).get('instances',[]) if i.get('label')=='$LABEL'),''))" 2>/dev/null)
  [ -n "$ID" ] && curl -s -X DELETE "https://console.vast.ai/api/v0/instances/$ID/?api_key=$KEY" >/dev/null 2>&1
  sleep 5; shutdown -h now 2>/dev/null || kill 1
}
( sleep __MAXSEC__; notify "time limit reached, stopping: $LABEL"; selfdestruct ) &

# Progress reporter: publishes "<label>|<stage> · NN%" to the quiet <topic>-status
# topic every 15s. The Pi polls that topic to show live stage + % in the UI.
mkdir -p /workspace; echo "starting" > /workspace/STAGE; : > /workspace/CURLOG
setstage(){ echo "$1" > /workspace/STAGE; echo "$2" > /workspace/CURLOG; }
( while true; do
    S=$(cat /workspace/STAGE 2>/dev/null); L=$(cat /workspace/CURLOG 2>/dev/null); P=""
    if [ -n "$L" ] && [ -f "$L" ]; then
      case "$S" in
        *download*) P=$(grep -oE '\([0-9]+%\)' "$L" 2>/dev/null | tail -1 | tr -dc '0-9') ;;
        *crack*)    P=$(grep Progress "$L" 2>/dev/null | grep -oE '\([0-9.]+%\)' | tail -1 | tr -dc '0-9.') ;;
      esac
    fi
    MSG="$S"; [ -n "$P" ] && MSG="$S · ${P}%"
    curl -s -H "Priority: min" -H "Title: $LABEL" -d "$LABEL|$MSG" "https://ntfy.sh/$TOPIC-status" >/dev/null 2>&1
    sleep 15
  done ) &

notify "crack started: $LABEL"
export DEBIAN_FRONTEND=noninteractive
setstage "installing tools" ""
apt-get update -qq && apt-get install -y hashcat aria2 p7zip-full curl python3 >/dev/null 2>&1
cd /workspace
cat > target.hc22000 <<'HCEOF'
__HASH__
HCEOF
POT=/workspace/cracked.pot
check(){ hashcat -m 22000 target.hc22000 --show --potfile-path "$POT" 2>/dev/null > RESULT.txt; [ -s RESULT.txt ]; }
finish(){ PW=$(awk -F: '{print $NF}' RESULT.txt | sort -u | paste -sd" "); notify "CRACKED $LABEL : $PW"; setstage "cracked: $PW" ""; sleep 20; selfdestruct; exit 0; }

setstage "downloading rockyou" ""
curl -sL -o rockyou.txt "__ROCKYOU__"
setstage "cracking rockyou" /workspace/hc1.log
hashcat -m 22000 -a 0 -O target.hc22000 rockyou.txt --status --status-timer 10 --potfile-path "$POT" > /workspace/hc1.log 2>&1
check && finish

setstage "cracking 8-digit numbers" /workspace/hc2.log
hashcat -m 22000 -a 3 -O target.hc22000 '?d?d?d?d?d?d?d?d' --status --status-timer 10 --potfile-path "$POT" > /workspace/hc2.log 2>&1
check && finish

setstage "downloading all-h.txt (28.5GB)" /workspace/aria.log
aria2c --seed-time=0 --summary-interval=10 --console-log-level=warn -d /workspace "__TORRENT__" > /workspace/aria.log 2>&1
ARCHIVE=$(ls /workspace/*.7z 2>/dev/null | head -1)
setstage "cracking all-h.txt" /workspace/hc3.log
7z e -so "$ARCHIVE" 2>/dev/null | hashcat -m 22000 -a 0 -O target.hc22000 --status --status-timer 10 --potfile-path "$POT" > /workspace/hc3.log 2>&1
check && finish
notify "not cracked: $LABEL (all lists exhausted)"; setstage "exhausted - not found" ""
sleep 22; selfdestruct
"""


def _onstart(label, hc22000):
    return (_TEMPLATE
            .replace("__TOPIC__", NTFY_TOPIC)
            .replace("__LABEL__", label)
            .replace("__KEY__", VAST_API_KEY)
            .replace("__MAXSEC__", str(int(CRACK_MAX_HOURS * 3600)))
            .replace("__ROCKYOU__", ROCKYOU_URL)
            .replace("__TORRENT__", ALL_H_TORRENT)
            .replace("__HASH__", hc22000.strip()))
