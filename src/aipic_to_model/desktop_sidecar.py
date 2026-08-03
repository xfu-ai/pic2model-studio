"""Secure desktop sidecar entrypoint used exclusively by the Tauri host.

The host supplies an in-memory session token.  The sidecar chooses an OS
allocated IPv4 loopback port and reports only that port on stdout, allowing the
host to pass the token to the WebView through Tauri IPC without ever writing it
to a command line, URL, browser storage, or log.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .api.app import create_app
from .api.server import bind_loopback_socket, run_loopback


def _startup_stage(stage: str) -> None:
    if os.environ.get("AIPIC_TO_MODEL_STARTUP_DIAGNOSTICS") == "1":
        print(json.dumps({"event": "startup", "stage": stage}), file=sys.stderr, flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pic2Model Studio desktop sidecar")
    parser.add_argument("--app-db", required=True)
    return parser.parse_args()


def main() -> None:
    _startup_stage("arguments")
    args = parse_args()
    token = os.environ.get("AIPIC_TO_MODEL_SESSION_TOKEN")
    host_control_token = os.environ.get("AIPIC_TO_MODEL_HOST_CONTROL_TOKEN")
    renderer_origin = os.environ.get(
        "AIPIC_TO_MODEL_RENDERER_ORIGIN", "http://tauri.localhost"
    )
    if not token:
        raise RuntimeError("desktop session token was not supplied")
    if not host_control_token:
        raise RuntimeError("desktop host control token was not supplied")
    _startup_stage("composition")
    application = create_app(
        token=token,
        app_db=Path(args.app_db),
        host_control_token=host_control_token,
        renderer_origin=renderer_origin,
    )
    _startup_stage("binding")
    listener = bind_loopback_socket()
    port = int(listener.getsockname()[1])
    # This is a deliberately tiny, non-secret readiness protocol for the host.
    print(json.dumps({"event": "ready", "port": port}), flush=True)
    _startup_stage("serving")
    run_loopback(application, listener)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
