"""FastAPI control surface: REST + WebSocket, serves the phone UI.

The controller runs tasks in worker threads and calls `emit()` from those
threads; we marshal each event onto the asyncio loop and fan it out to every
connected WebSocket.
"""

import asyncio
import json
from asyncio import Queue
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from app.config import CAPTURE_DIR, HOST, PORT
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
    return {"captures": [{"name": f.name, "size": f.stat().st_size} for f in files]}


@app.get("/api/v1/captures/{name}")
def download_capture(name: str):
    target = (CAPTURE_DIR / name).resolve()
    if target.parent != CAPTURE_DIR.resolve() or not target.is_file():
        raise HTTPException(404, detail="Not found")
    return FileResponse(target, filename=name, media_type="application/vnd.tcpdump.pcap")


if __name__ == "__main__":
    ensure_root()
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
