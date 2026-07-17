from __future__ import annotations

import argparse
import ctypes
import json
import re
import time
from ctypes import wintypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from pico_stream_input import PicoStreamInputController, PicoStreamInputError


ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / "runtime"
SLOT_TITLE = re.compile(r"^\[(\d{2})\](?:\s.*)?$")


def _window_text(user32: ctypes.WinDLL, hwnd: int) -> str:
    length = int(user32.GetWindowTextLengthW(hwnd))
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, len(buffer))
    return buffer.value.strip()


def _window_candidates() -> list[dict[str, object]]:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    candidates: list[dict[str, object]] = []

    enum_proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @enum_proc_type
    def callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        title = _window_text(user32, hwnd)
        match = SLOT_TITLE.match(title)
        if not match:
            return True

        rect = wintypes.RECT()
        if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
            return True
        width = int(rect.right - rect.left)
        height = int(rect.bottom - rect.top)
        if width <= 0 or height <= 0:
            return True

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        try:
            dpi = int(user32.GetDpiForWindow(hwnd)) or 96
        except AttributeError:
            dpi = 96
        candidates.append(
            {
                "slot": int(match.group(1)),
                "hwnd": int(hwnd),
                "pid": int(pid.value),
                "title": title,
                "client_logical": {"w": width, "h": height},
                "window_dpi": dpi,
                "capture_physical_expected": {
                    "w": round(width * dpi / 96),
                    "h": round(height * dpi / 96),
                },
                "aspect": width / height,
            }
        )
        return True

    if not user32.EnumWindows(callback, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    return candidates


def source_identity(slot: int) -> dict[str, object]:
    matches = [item for item in _window_candidates() if item["slot"] == slot]
    if not matches:
        return {
            "ok": False,
            "slot": slot,
            "error": f"No visible game window named [{slot:02d}] was found",
            "captured_at_ms": int(time.time() * 1000),
        }
    if len(matches) > 1:
        return {
            "ok": False,
            "slot": slot,
            "error": f"More than one visible window matches [{slot:02d}]",
            "matches": matches,
            "captured_at_ms": int(time.time() * 1000),
        }
    result = dict(matches[0])
    result.update(
        {
            "ok": True,
            "captured_at_ms": int(time.time() * 1000),
            "aspect_is_16_9": abs(float(result["aspect"]) - (16 / 9)) <= 0.001,
        }
    )
    return result


def load_runtime_state() -> dict[str, object]:
    state_path = RUNTIME / "state.json"
    if not state_path.exists():
        return {}
    try:
        return json.loads(state_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def sources_payload(slots: list[int]) -> dict[str, object]:
    state = load_runtime_state()
    return {
        "ok": True,
        "generated_at_ms": int(time.time() * 1000),
        "profile": state.get(
            "profile",
            {"encoded": {"w": 1280, "h": 720}, "fps": 30, "bitrate_kbps": 4000},
        ),
        "encoder": state.get("encoder", "unknown"),
        "network_underlay": state.get("network_underlay", {}),
        "input": state.get("input", {"enabled": False}),
        "sources": [source_identity(slot) for slot in slots],
    }


class Handler(BaseHTTPRequestHandler):
    slots = [1, 15]
    input_token = ""
    input_controller: PicoStreamInputController | None = None

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _json(self, payload: dict[str, object], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/oplink-test":
            path = "/"
        elif path.startswith("/oplink-test/"):
            path = path.removeprefix("/oplink-test")

        if path in ("/", "/api/v1/health"):
            payload = sources_payload(self.slots)
            payload["service"] = "oplink-pc-no-zego-slot-1-15-test"
            payload["all_sources_ready"] = all(item["ok"] for item in payload["sources"])
            self._json(payload)
            return
        if path == "/api/v1/sources":
            self._json(sources_payload(self.slots))
            return
        self.send_error(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path.startswith("/oplink-test/"):
            path = path.removeprefix("/oplink-test")
        if path != "/api/v1/input":
            self.send_error(HTTPStatus.NOT_FOUND, "not found")
            return
        if not self.input_controller or not self.input_token:
            self._json({"ok": False, "error": "Pico input is disabled"}, HTTPStatus.SERVICE_UNAVAILABLE)
            return
        authorization = self.headers.get("Authorization", "")
        if authorization != f"Bearer {self.input_token}":
            self._json({"ok": False, "error": "invalid pairing token"}, HTTPStatus.UNAUTHORIZED)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 16_384:
                raise ValueError("invalid request body length")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("request body must be a JSON object")
            slot = int(payload.get("slot") or 0)
            result = self.input_controller.handle(payload, source_identity(slot))
            self._json(result)
        except (ValueError, json.JSONDecodeError, PicoStreamInputError) as exc:
            self._json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)


def main() -> int:
    parser = argparse.ArgumentParser(description="OPLINK_PC slot 1/15 stream metadata service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5110)
    parser.add_argument("--slots", default="1,15")
    parser.add_argument("--probe", type=int)
    parser.add_argument("--pico-config")
    parser.add_argument("--input-token-file")
    parser.add_argument("--pico-health", action="store_true")
    args = parser.parse_args()
    slots = [int(value.strip()) for value in args.slots.split(",") if value.strip()]
    if args.probe is not None:
        print(json.dumps(source_identity(args.probe), separators=(",", ":")))
        return 0
    controller = PicoStreamInputController(args.pico_config) if args.pico_config else None
    if args.pico_health:
        if controller is None:
            raise SystemExit("--pico-health requires --pico-config")
        print(json.dumps(controller.health(), separators=(",", ":")))
        return 0
    Handler.slots = slots
    Handler.input_controller = controller
    if args.input_token_file:
        Handler.input_token = Path(args.input_token_file).read_text(encoding="utf-8").strip()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Metadata service listening on http://{args.host}:{args.port}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
