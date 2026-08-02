"""Small dependency-free CDP transport for controlled WebView2 evidence.

The harness talks only to a test WebView that has explicitly enabled a local
remote-debugging port.  It is intentionally not a browser automation library:
test cases own their DOM actions, while this module owns diagnostics and
redaction so every failure produces the same safe evidence bundle.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import socket
import struct
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import urlopen

_SECRET = re.compile(r"(?i)(bearer\s+|api[_-]?key[=:]\s*|token[=:]\s*)[^\s,\"}]+")
# Do not treat the `p://` portion of an http(s) URL as a Windows drive.
_WINDOWS_PATH = re.compile(r'(?i)(?<![a-z])[a-z]:[\\/][^\s,"}]+')
_SECRET_KEY = re.compile(r"(?i)(?:api[_-]?key|bearer|token|authorization|secret|password)")


def redact(value: Any) -> Any:
    """Redact secrets and local paths before an artifact reaches disk."""

    if isinstance(value, dict):
        return {
            str(key): "<redacted>" if _SECRET_KEY.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if not isinstance(value, str):
        return value
    return _WINDOWS_PATH.sub("<local-path>", _SECRET.sub(r"\1<redacted>", value))


class CdpConnection:
    def __init__(self, websocket_url: str) -> None:
        parsed = urlparse(websocket_url)
        if parsed.scheme != "ws" or parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise ValueError("controlled CDP must use a loopback ws:// endpoint")
        self._socket = socket.create_connection((parsed.hostname, parsed.port or 80), timeout=10)
        self._socket.settimeout(20)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        target = (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")
        request = (
            f"GET {target} HTTP/1.1\r\nHost: {parsed.hostname}:{parsed.port}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        self._socket.sendall(request.encode("ascii"))
        response = self._read_http_header()
        if not response.startswith("HTTP/1.1 101"):
            raise RuntimeError(f"CDP websocket upgrade failed: {response.splitlines()[0]}")
        self._next_id = 1
        self._events: list[dict[str, Any]] = []

    @classmethod
    def attach(cls, debug_port: int) -> CdpConnection:
        with urlopen(f"http://127.0.0.1:{debug_port}/json/list", timeout=10) as response:
            targets = json.load(response)
        pages = [item for item in targets if item.get("type") == "page"]
        page = next(
            (
                item
                for item in pages
                if "screen-capture=" not in str(item.get("url", ""))
            ),
            pages[0] if pages else None,
        )
        if not page or not isinstance(page.get("webSocketDebuggerUrl"), str):
            raise RuntimeError("no debuggable WebView page is available")
        return cls(page["webSocketDebuggerUrl"])

    def close(self) -> None:
        self._socket.close()

    def call(self, method: str, **params: Any) -> Any:
        message_id = self._next_id
        self._next_id += 1
        self._send_json({"id": message_id, "method": method, "params": params})
        while True:
            response = self._read_json()
            if response.get("id") != message_id:
                self._events.append(response)
                continue
            if "error" in response:
                raise RuntimeError(f"CDP {method} failed: {response['error']}")
            return response.get("result", {})

    def evaluate(self, expression: str) -> Any:
        result = self.call("Runtime.evaluate", expression=expression, returnByValue=True, awaitPromise=True)
        payload = result.get("result", {})
        if "exceptionDetails" in result:
            raise RuntimeError(f"page evaluation failed: {result['exceptionDetails']}")
        return payload.get("value")

    def events(self) -> list[dict[str, Any]]:
        """Return CDP events observed while waiting for command responses."""

        return list(self._events)

    def _read_http_header(self) -> str:
        data = bytearray()
        while b"\r\n\r\n" not in data:
            data.extend(self._socket.recv(1))
        return data.decode("latin-1")

    def _send_json(self, message: dict[str, Any]) -> None:
        payload = json.dumps(message, separators=(",", ":")).encode("utf-8")
        mask = os.urandom(4)
        header = bytearray([0x81])
        size = len(payload)
        if size < 126:
            header.append(0x80 | size)
        elif size <= 0xFFFF:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", size))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", size))
        encrypted = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self._socket.sendall(bytes(header) + mask + encrypted)

    def _read_json(self) -> dict[str, Any]:
        while True:
            first, second = self._read_exact(2)
            opcode = first & 0x0F
            size = second & 0x7F
            if size == 126:
                size = struct.unpack("!H", self._read_exact(2))[0]
            elif size == 127:
                size = struct.unpack("!Q", self._read_exact(8))[0]
            masked = second & 0x80
            mask = self._read_exact(4) if masked else b""
            payload = self._read_exact(size)
            if masked:
                payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
            if opcode == 8:
                raise RuntimeError("CDP websocket closed")
            if opcode == 9:
                self._socket.sendall(b"\x8a" + bytes([len(payload)]) + payload)
                continue
            if opcode != 1:
                continue
            return json.loads(payload)

    def _read_exact(self, size: int) -> bytes:
        data = bytearray()
        while len(data) < size:
            chunk = self._socket.recv(size - len(data))
            if not chunk:
                raise RuntimeError("CDP websocket closed unexpectedly")
            data.extend(chunk)
        return bytes(data)


_DIAGNOSTICS_SCRIPT = """(() => {
          if (globalThis.__aipicE2E) return;
          const records = { errors: [], rejections: [], network: [] };
          globalThis.__aipicE2E = records;
          const safe = (value) => String(value ?? '').slice(0, 8000);
          addEventListener('error', (event) => records.errors.push({message: safe(event.message), source: safe(event.filename), line: event.lineno}));
          addEventListener('unhandledrejection', (event) => records.rejections.push(safe(event.reason?.stack || event.reason)));
          const original = globalThis.fetch.bind(globalThis);
          globalThis.fetch = async (input, init = {}) => {
            const request = new Request(input, init);
            const record = {method: request.method, url: request.url, request: safe(init.body), status: null, response: ''};
            try {
              const response = await original(input, init);
              record.status = response.status;
              record.response = safe(await response.clone().text());
              return response;
            } catch (error) {
              record.response = safe(error?.stack || error);
              throw error;
            } finally { records.network.push(record); }
          };
        })()"""


def install_diagnostics(connection: CdpConnection, *, document_start: bool = False) -> None:
    """Install redacted runtime probes now and, optionally, before the next navigation."""

    if document_start:
        connection.call("Page.addScriptToEvaluateOnNewDocument", source=_DIAGNOSTICS_SCRIPT)
    connection.evaluate(_DIAGNOSTICS_SCRIPT)


def cdp_network_records(connection: CdpConnection) -> list[dict[str, Any]]:
    """Normalize request/response metadata seen through the CDP Network domain."""

    requests: dict[str, dict[str, Any]] = {}
    for event in connection.events():
        method = event.get("method")
        params = event.get("params") or {}
        request_id = params.get("requestId")
        if not isinstance(request_id, str):
            continue
        if method == "Network.requestWillBeSent":
            request = params.get("request") or {}
            requests[request_id] = {
                "method": request.get("method"),
                "url": request.get("url"),
                "request": request.get("postData", ""),
                "status": None,
                "response": "",
            }
        elif method == "Network.responseReceived" and request_id in requests:
            response = params.get("response") or {}
            requests[request_id]["status"] = response.get("status")
        elif method == "Network.loadingFailed" and request_id in requests:
            requests[request_id]["response"] = params.get("errorText", "network request failed")
    return list(requests.values())


def collect_evidence(connection: CdpConnection, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    html = connection.evaluate("document.documentElement.outerHTML")
    state = connection.evaluate("globalThis.__aipicE2E || {errors:[],rejections:[],network:[]}")
    if isinstance(state, dict):
        # The page-level probe sees calls through the current global fetch;
        # CDP additionally sees modules that captured fetch before attachment.
        state["network"] = [*state.get("network", []), *cdp_network_records(connection)]
    workspace = connection.evaluate(
        "JSON.stringify({title: document.title, body: document.body.innerText, focus: document.activeElement?.outerHTML?.slice(0,300)})"
    )
    screenshot = connection.call("Page.captureScreenshot", format="png").get("data", "")
    (destination / "dom.html").write_text(str(redact(html)), encoding="utf-8")
    (destination / "runtime.json").write_text(json.dumps(redact(state), ensure_ascii=False, indent=2), encoding="utf-8")
    (destination / "workspace.json").write_text(str(redact(workspace)), encoding="utf-8")
    if screenshot:
        (destination / "webview.png").write_bytes(base64.b64decode(screenshot))


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture a redacted controlled-WebView2 evidence bundle.")
    parser.add_argument("--debug-port", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    connection = CdpConnection.attach(args.debug_port)
    try:
        install_diagnostics(connection)
        collect_evidence(connection, args.output)
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
