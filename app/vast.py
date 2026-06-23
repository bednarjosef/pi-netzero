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
notify "crack started: $LABEL"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq && apt-get install -y hashcat aria2 p7zip-full curl python3 >/dev/null 2>&1
cd /workspace
cat > target.hc22000 <<'HCEOF'
__HASH__
HCEOF
POT=/workspace/cracked.pot
check(){
  hashcat -m 22000 target.hc22000 --show --potfile-path "$POT" 2>/dev/null > RESULT.txt
  if [ -s RESULT.txt ]; then PW=$(awk -F: '{print $NF}' RESULT.txt | paste -sd" "); notify "CRACKED $LABEL : $PW"; return 0; fi
  return 1
}
notify "stage 1/3: rockyou"
curl -sL -o rockyou.txt "__ROCKYOU__"
hashcat -m 22000 -a 0 -O target.hc22000 rockyou.txt --potfile-path "$POT"
check && { notify "done"; selfdestruct; exit 0; }
notify "stage 2/3: all 8-digit numbers"
hashcat -m 22000 -a 3 -O target.hc22000 '?d?d?d?d?d?d?d?d' --potfile-path "$POT"
check && { notify "done"; selfdestruct; exit 0; }
notify "stage 3/3: downloading all-h.txt torrent (28.5GB)"
aria2c --seed-time=0 --console-log-level=warn -d /workspace "__TORRENT__"
ARCHIVE=$(ls /workspace/*.7z 2>/dev/null | head -1)
notify "stage 3/3: cracking all-h.txt (streamed)"
7z e -so "$ARCHIVE" 2>/dev/null | hashcat -m 22000 -a 0 -O target.hc22000 --potfile-path "$POT"
check || notify "not cracked: $LABEL (all lists exhausted)"
notify "job complete: $LABEL"
selfdestruct
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
