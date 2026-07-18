from __future__ import annotations

import argparse
import ctypes
import json
import os
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
SLOT_PID_MAP_PATH = Path(
    os.environ.get("GUI_TEST_PC_SLOT_PID_MAP", r"D:\15game\gui_test_pc_slot_pids.json")
)
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _window_text(user32: ctypes.WinDLL, hwnd: int) -> str:
    length = int(user32.GetWindowTextLengthW(hwnd))
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, len(buffer))
    return buffer.value.strip()


def _process_path(pid: int) -> str | None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        capacity = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(capacity.value)
        if not kernel32.QueryFullProcessImageNameW(
            handle, 0, buffer, ctypes.byref(capacity)
        ):
            return None
        return buffer.value
    finally:
        kernel32.CloseHandle(handle)


def _slot_pid_map() -> dict[int, dict[str, object]]:
    if not SLOT_PID_MAP_PATH.exists():
        return {}
    try:
        payload = json.loads(SLOT_PID_MAP_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    result: dict[int, dict[str, object]] = {}
    if not isinstance(payload, dict):
        return result
    for slot_text, value in payload.items():
        try:
            slot = int(slot_text)
            pid = int((value or {}).get("Pid") or (value or {}).get("pid") or 0)
        except (TypeError, ValueError, AttributeError):
            continue
        if 1 <= slot <= 15 and pid > 0:
            result[pid] = {
                "slot": slot,
                "expected_exe": str((value or {}).get("Exe") or ""),
            }
    return result


def _numbered_title(slot: int, title: str) -> str:
    base = re.sub(r"^\[\d{1,2}\]\s*", "", title).strip() or "StarCG"
    return f"[{slot:02d}] {base}"


def _window_candidates() -> list[dict[str, object]]:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    pid_map = _slot_pid_map()
    candidates: list[dict[str, object]] = []

    enum_proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @enum_proc_type
    def callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        title = _window_text(user32, hwnd)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        pid_value = int(pid.value)
        mapped = pid_map.get(pid_value)
        title_match = SLOT_TITLE.match(title)
        if mapped:
            slot = int(mapped["slot"])
            identity_source = "gui_test_pc_pid_map"
        elif title_match:
            slot = int(title_match.group(1))
            identity_source = "window_title"
        else:
            return True

        process_path = _process_path(pid_value)
        expected_exe = str((mapped or {}).get("expected_exe") or "")
        if process_path and Path(process_path).name.casefold() != "starcg.exe":
            return True
        if expected_exe and process_path:
            expected_normalized = os.path.normcase(os.path.abspath(expected_exe))
            actual_normalized = os.path.normcase(os.path.abspath(process_path))
            if expected_normalized != actual_normalized:
                return True

        desired_title = _numbered_title(slot, title)
        title_rename_ok = title == desired_title
        if not title_rename_ok:
            title_rename_ok = bool(user32.SetWindowTextW(hwnd, desired_title))
            if title_rename_ok:
                title = desired_title

        rect = wintypes.RECT()
        if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
            return True
        width = int(rect.right - rect.left)
        height = int(rect.bottom - rect.top)
        if width <= 0 or height <= 0:
            return True

        try:
            dpi = int(user32.GetDpiForWindow(hwnd)) or 96
        except AttributeError:
            dpi = 96
        candidates.append(
            {
                "slot": slot,
                "hwnd": int(hwnd),
                "pid": pid_value,
                "title": title,
                "title_rename_ok": title_rename_ok,
                "identity_source": identity_source,
                "process_path": process_path,
                "slot_pid_map_path": str(SLOT_PID_MAP_PATH) if mapped else None,
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
            "error": (
                f"No visible slot {slot} game window was found by GUI_TEST_PC PID map "
                f"or [{slot:02d}] title"
            ),
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
            {"encoded": {"w": 1920, "h": 1080}, "fps": 30, "bitrate_kbps": 6000},
        ),
        "encoder": state.get("encoder", "unknown"),
        "network_underlay": state.get("network_underlay", {}),
        "input": state.get("input", {"enabled": False}),
        "sources": [source_identity(slot) for slot in slots],
    }


class Handler(BaseHTTPRequestHandler):
    slots = list(range(1, 16))
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
            payload["service"] = "oplink-pc-no-zego-slots-1-15"
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
    parser = argparse.ArgumentParser(description="OPLINK_PC slots 1-15 stream metadata service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5110)
    parser.add_argument("--slots", default=",".join(str(slot) for slot in range(1, 16)))
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
