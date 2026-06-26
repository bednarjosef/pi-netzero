"""FastAPI control surface: REST + WebSocket, serves the phone UI.

The controller runs tasks in worker threads and calls `emit()` from those
threads; we marshal each event onto the asyncio loop and fan it out to every
connected WebSocket.
"""

import asyncio
import json
import re
import socket
import time
from asyncio import Queue
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from app import vast
from app.config import CAPTURE_DIR, HOST, PORT, NTFY_TOPIC
from app.controller import NetZero
from app.radio import enable_monitor, ensure_root, list_interfaces

WEB_DIR = Path(__file__).parent / "web"

_loop = None
_queue: Queue = Queue()
_subscribers: list[WebSocket] = []


def emit(event: dict):
    """Thread-safe: push a controller event onto the asyncio loop for broadcast."""
    if _loop and _loop.is_running():
        asyncio.run_coroutine_threadsafe(_queue.put(json.dumps(event)), _loop)


netzero = NetZero(emit)


async def _broadcast():
    while True:
        msg = await _queue.get()
        for ws in list(_subscribers):
            try:
                await ws.send_text(msg)
            except Exception:
                if ws in _subscribers:
                    _subscribers.remove(ws)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _loop
    _loop = asyncio.get_running_loop()
    task = asyncio.create_task(_broadcast())
    try:
        enable_monitor()  # radio is dedicated to monitor; get it ready at boot
    except Exception as exc:
        print(f"[startup] monitor mode not ready: {exc}")
    yield
    task.cancel()


app = FastAPI(title="pi-netzero", lifespan=lifespan)

# Allow the UI loaded from http://netzero.box to talk to the Pi at whatever IP it
# currently has (it switches the API host under the hood when tethering moves the
# Pi to a new subnet) — that's a cross-origin call, so permit it.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# --- request bodies -----------------------------------------------------------
class ClientScan(BaseModel):
    bssid: str
    channel: int


class Deauth(BaseModel):
    bssid: str
    channel: int
    client: str | None = None
    bursts: int = 3


class Handshake(BaseModel):
    bssid: str
    channel: int
    client: str | None = None
    ssid: str = ""


class Pmkid(BaseModel):
    bssid: str
    ssid: str
    channel: int


def _require_idle():
    if not netzero.is_idle():
        raise HTTPException(409, detail=f"Busy: {netzero.task}")


# --- UI + websocket -----------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def dashboard():
    return (WEB_DIR / "index.html").read_text()


@app.websocket("/ws/v1/stream")
async def stream(ws: WebSocket):
    await ws.accept()
    _subscribers.append(ws)
    # send current state immediately so a fresh page isn't blank
    await ws.send_text(json.dumps({"type": "state", "state": netzero.state()}))
    for n in netzero.network_list():
        await ws.send_text(json.dumps({"type": "network", "network": n}))
    for c in netzero.client_list():
        await ws.send_text(json.dumps({"type": "client", "client": c}))
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        if ws in _subscribers:
            _subscribers.remove(ws)


# --- REST ---------------------------------------------------------------------
@app.get("/api/v1/health")
def health():
    return {"status": "alive"}


_net_cache = {"t": 0.0, "online": False, "ip": None}


def _check_online():
    """Quick TCP probe to a public host: does the Pi have internet right now?"""
    try:
        s = socket.create_connection(("1.1.1.1", 443), timeout=1.5)
        ip = s.getsockname()[0]   # the Pi's source IP on the route out
        s.close()
        return True, ip
    except OSError:
        return False, None


def _last_tether():
    """The Pi's last-seen tethered IP (written by pitail-uplink-monitor), so the UI
    can auto-switch the window there when tethering takes the link off this IP."""
    try:
        return (Path("/opt/pi-netzero/last-tether-ip").read_text().strip() or None)
    except Exception:
        return None


@app.get("/api/v1/net")
def net():
    """Internet reachability for the top-bar indicator. Online == phone tethering
    is sharing data (Vast works); offline == local-only capture mode. Cached so
    rapid polls don't each open a socket. `tether` is the last tethered IP."""
    now = time.time()
    if now - _net_cache["t"] > 8:
        online, ip = _check_online()
        _net_cache.update(t=now, online=online, ip=ip)
    return {"online": _net_cache["online"], "ip": _net_cache["ip"], "tether": _last_tether()}


@app.get("/api/v1/state")
def state():
    return netzero.state()


@app.get("/api/v1/interfaces")
def interfaces():
    return {"interfaces": list_interfaces()}


@app.get("/api/v1/networks")
def networks():
    return {"networks": netzero.network_list()}


@app.post("/api/v1/scan/networks/start")
def scan_networks_start():
    _require_idle()
    netzero.start_network_scan()
    return {"message": "Network scan started."}


@app.post("/api/v1/scan/clients/start")
def scan_clients_start(body: ClientScan):
    _require_idle()
    netzero.start_client_scan(body.bssid, body.channel)
    return {"message": "Client scan started."}


@app.post("/api/v1/attack/deauth")
def attack_deauth(body: Deauth):
    _require_idle()
    netzero.start_deauth(body.bssid, body.client, body.channel, body.bursts)
    return {"message": "Deauth started."}


@app.post("/api/v1/attack/handshake")
def attack_handshake(body: Handshake):
    _require_idle()
    netzero.start_handshake(body.bssid, body.client, body.channel, body.ssid)
    return {"message": "Handshake capture started."}


@app.post("/api/v1/attack/pmkid")
def attack_pmkid(body: Pmkid):
    _require_idle()
    netzero.start_pmkid(body.bssid, body.ssid, body.channel)
    return {"message": "PMKID capture started."}


@app.post("/api/v1/stop")
def stop():
    netzero.stop()
    return {"message": "Stop signalled."}


@app.get("/api/v1/captures")
def captures():
    files = sorted(CAPTURE_DIR.glob("*.pcap"), key=lambda p: p.stat().st_mtime, reverse=True)
    stems = netzero.store.captured_stems()
    return {"captures": [{"name": f.name, "size": f.stat().st_size, "has_hash": f.stem in stems}
                         for f in files]}


@app.delete("/api/v1/captures/{name}")
def delete_capture(name: str):
    target = (CAPTURE_DIR / name).resolve()
    if target.parent != CAPTURE_DIR.resolve() or not target.is_file():
        raise HTTPException(404, detail="Not found")
    target.unlink()
    return {"message": "Capture deleted."}


@app.get("/api/v1/captures/{name}")
def download_capture(name: str):
    target = (CAPTURE_DIR / name).resolve()
    if target.parent != CAPTURE_DIR.resolve() or not target.is_file():
        raise HTTPException(404, detail="Not found")
    return FileResponse(target, filename=name, media_type="application/vnd.tcpdump.pcap")


# --- hashes + cracking --------------------------------------------------------
@app.get("/api/v1/hashes")
def hashes():
    return {"hashes": netzero.store.list(), "ntfy": NTFY_TOPIC, "vast": vast.configured()}


@app.get("/api/v1/hashes/{name}")
def download_hash(name: str):
    e = netzero.store.get(name)
    if not e or not Path(e["file"]).is_file():
        raise HTTPException(404, detail="Not found")
    return FileResponse(e["file"], filename=name, media_type="text/plain")


@app.delete("/api/v1/hashes/{name}")
def delete_hash(name: str):
    if not netzero.store.delete(name):
        raise HTTPException(404, detail="Not found")
    return {"message": "Hash deleted."}


@app.post("/api/v1/crack/{name}")
def crack(name: str):
    e = netzero.store.get(name)
    if not e:
        raise HTTPException(404, detail="Hash not found")
    if not vast.configured():
        raise HTTPException(400, detail="No Vast API key on the Pi (put it in vast.key).")
    content = netzero.store.content(name)
    if not content:
        raise HTTPException(400, detail="Hash file empty or missing")
    label = "pinetzero-" + re.sub(r"[^A-Za-z0-9]", "-", name.rsplit(".", 1)[0])[:40] + "-" + str(int(time.time()))
    try:
        res = vast.launch(label, content, e.get("ssid", "network"))
    except vast.VastError as ex:
        raise HTTPException(502, detail=str(ex))
    netzero.store.update(name, status="cracking", instance_id=res["instance_id"], label=label)
    return {"message": f"Launched on {res['gpu']} (${res['dph']}/hr) — watch ntfy '{NTFY_TOPIC}'.", **res}


@app.get("/api/v1/crack/instances")
def crack_instances():
    if not vast.configured():
        return {"instances": [], "vast": False}
    try:
        ins = vast.instances()
    except vast.VastError as ex:
        raise HTTPException(502, detail=str(ex))
    prog = vast.progress()
    ours = [{
        "id": i.get("id"), "label": i.get("label"),
        "status": i.get("actual_status") or i.get("cur_state"),
        "dph": round(i.get("dph_total", 0) or 0, 3), "gpu": i.get("gpu_name"),
        "location": i.get("geolocation"),
        "progress": prog.get(i.get("label")),
    } for i in ins if str(i.get("label", "")).startswith("pinetzero-")]
    # Reconcile: a hash still marked "cracking" whose instance has self-destructed
    # is finished — flip it so the UI doesn't show it cracking forever.
    live = {i["id"] for i in ours}
    for h in netzero.store.list():
        if h.get("status") == "cracking" and h.get("instance_id") not in live:
            # The job self-destructed. Its last status line tells us the outcome:
            # "cracked: <pw>" -> record the password; otherwise it's finished.
            last = (prog.get(h.get("label", "")) or "")
            if last.lower().startswith("cracked:"):
                netzero.store.update(h["name"], status="cracked", password=last.split(":", 1)[1].strip())
            else:
                netzero.store.update(h["name"], status="finished")
    return {"instances": ours, "vast": True}


@app.delete("/api/v1/crack/instances/{iid}")
def destroy_instance(iid: int):
    try:
        vast.destroy(iid)
    except vast.VastError as ex:
        raise HTTPException(502, detail=str(ex))
    return {"message": "Instance destroyed."}


if __name__ == "__main__":
    ensure_root()
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
