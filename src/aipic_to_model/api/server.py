"""The single permitted local HTTP listener for the Tauri sidecar."""

from __future__ import annotations

import socket
from dataclasses import dataclass

from fastapi import FastAPI


@dataclass(frozen=True)
class LoopbackServerConfig:
    host: str = "127.0.0.1"
    port: int = 0

    def __post_init__(self) -> None:
        if self.host != "127.0.0.1" or self.port != 0:
            raise ValueError("B01 sidecar must bind exactly 127.0.0.1:0")


def run_loopback(app: FastAPI, listener: socket.socket | None = None) -> None:
    """Run the sidecar on an OS-selected loopback port; never expose a LAN bind."""
    import uvicorn

    listener = listener or bind_loopback_socket()
    try:
        config = uvicorn.Config(app, log_level="warning", access_log=False)
        uvicorn.Server(config).run(sockets=[listener])
    finally:
        listener.close()


def bind_loopback_socket() -> socket.socket:
    """Ask the OS for a port, then prove the actual listener is IPv4 loopback."""
    config = LoopbackServerConfig()
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((config.host, config.port))
        address = listener.getsockname()
        if address[0] != config.host or not isinstance(address[1], int) or address[1] <= 0:
            raise RuntimeError("sidecar bound an invalid non-loopback address")
        return listener
    except Exception:
        listener.close()
        raise
