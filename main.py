"""Entrypoint: `sudo .venv/bin/python main.py` (or via the systemd unit)."""

import uvicorn

from app.config import HOST, PORT
from app.radio import ensure_root
from app.server import app

if __name__ == "__main__":
    ensure_root()
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
