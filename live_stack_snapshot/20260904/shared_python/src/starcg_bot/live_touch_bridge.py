from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
from typing import Any, Callable
from urllib.parse import urlparse

from .pico_touch import PicoTouchError, PicoTouchScheduler, _scheduler_for_config
from .slot_limits import MAX_SLOT, MIN_SLOT


class LiveTouchProtocolError(RuntimeError):
    """Raised when a remote touch sequence is invalid or out of order."""


class WindowsKeyboardInputError(RuntimeError):
    """Raised when GUI_TEST_PC cannot deliver a Windows keyboard event."""


INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
SW_RESTORE = 9
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_SHOWWINDOW = 0x0040
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2


class _MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _KeyboardInput(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _HardwareInput(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _InputUnion(ctypes.Union):
    _fields_ = [("mi", _MouseInput), ("ki", _KeyboardInput), ("hi", _HardwareInput)]


class _Input(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = [("type", wintypes.DWORD), ("value", _InputUnion)]


class WindowsKeyboardInjector:
    """Activates one GUI_TEST_PC slot and sends Windows keyboard input."""

    _KEYS = {"enter": 0x0D, "backspace": 0x08}

    def __init__(self) -> None:
        if not hasattr(ctypes, "WinDLL"):
            raise WindowsKeyboardInputError("Windows SendInput is unavailable on this host")
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._lock = threading.Lock()
        self._configure_api()

    def send_text(self, hwnd: int, text: str) -> None:
        if not text:
            raise LiveTouchProtocolError("text must not be empty")
        if len(text) > 256:
            raise LiveTouchProtocolError("text must be 256 characters or fewer")
        units = [
            int.from_bytes(encoded[index : index + 2], "little")
            for encoded in [text.encode("utf-16-le")]
            for index in range(0, len(encoded), 2)
        ]
        inputs: list[_Input] = []
        for unit in units:
            inputs.append(self._unicode_input(unit, key_up=False))
            inputs.append(self._unicode_input(unit, key_up=True))
        with self._lock:
            self._activate(hwnd)
            self._send(inputs)

    def send_key(self, hwnd: int, key: str) -> None:
        normalized = str(key or "").strip().casefold()
        if normalized not in self._KEYS:
            raise LiveTouchProtocolError("key must be enter or backspace")
        vk = self._KEYS[normalized]
        inputs = [self._virtual_key_input(vk, key_up=False), self._virtual_key_input(vk, key_up=True)]
        with self._lock:
            self._activate(hwnd)
            self._send(inputs)

    def _configure_api(self) -> None:
        self._user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_Input), ctypes.c_int]
        self._user32.SendInput.restype = wintypes.UINT
        self._user32.GetForegroundWindow.argtypes = []
        self._user32.GetForegroundWindow.restype = wintypes.HWND
        self._user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self._user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        self._user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
        self._user32.AttachThreadInput.restype = wintypes.BOOL
        self._user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        self._user32.ShowWindow.restype = wintypes.BOOL
        self._user32.BringWindowToTop.argtypes = [wintypes.HWND]
        self._user32.BringWindowToTop.restype = wintypes.BOOL
        self._user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        self._user32.SetForegroundWindow.restype = wintypes.BOOL
        self._user32.SetWindowPos.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        self._user32.SetWindowPos.restype = wintypes.BOOL
        self._kernel32.GetCurrentThreadId.argtypes = []
        self._kernel32.GetCurrentThreadId.restype = wintypes.DWORD

    def _activate(self, hwnd: int) -> None:
        handle = wintypes.HWND(int(hwnd))
        self._user32.ShowWindow(handle, SW_RESTORE)
        self._user32.BringWindowToTop(handle)
        self._user32.SetForegroundWindow(handle)
        if int(self._user32.GetForegroundWindow() or 0) == int(hwnd):
            time.sleep(0.04)
            return

        current_thread = int(self._kernel32.GetCurrentThreadId())
        target_thread = int(self._user32.GetWindowThreadProcessId(handle, None) or 0)
        foreground = self._user32.GetForegroundWindow()
        foreground_thread = int(self._user32.GetWindowThreadProcessId(foreground, None) or 0)
        attached: list[int] = []
        try:
            for thread_id in {target_thread, foreground_thread}:
                if thread_id and thread_id != current_thread:
                    if self._user32.AttachThreadInput(current_thread, thread_id, True):
                        attached.append(thread_id)
            self._user32.ShowWindow(handle, SW_RESTORE)
            self._user32.BringWindowToTop(handle)
            self._user32.SetForegroundWindow(handle)
        finally:
            for thread_id in attached:
                self._user32.AttachThreadInput(current_thread, thread_id, False)

        if int(self._user32.GetForegroundWindow() or 0) != int(hwnd):
            flags = SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW
            self._user32.SetWindowPos(handle, wintypes.HWND(HWND_TOPMOST), 0, 0, 0, 0, flags)
            self._user32.SetWindowPos(handle, wintypes.HWND(HWND_NOTOPMOST), 0, 0, 0, 0, flags)
            self._user32.BringWindowToTop(handle)
            self._user32.SetForegroundWindow(handle)
        if int(self._user32.GetForegroundWindow() or 0) != int(hwnd):
            raise WindowsKeyboardInputError(
                f"GUI_TEST_PC could not activate keyboard target hwnd=0x{int(hwnd):X}"
            )
        time.sleep(0.04)

    def _send(self, inputs: list[_Input]) -> None:
        if not inputs:
            return
        array_type = _Input * len(inputs)
        array = array_type(*inputs)
        sent = int(self._user32.SendInput(len(inputs), array, ctypes.sizeof(_Input)))
        if sent != len(inputs):
            raise WindowsKeyboardInputError(
                f"Windows SendInput accepted {sent}/{len(inputs)} keyboard events; "
                f"WinError {ctypes.get_last_error()}"
            )

    @staticmethod
    def _unicode_input(unit: int, *, key_up: bool) -> _Input:
        flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if key_up else 0)
        return _Input(
            type=INPUT_KEYBOARD,
            value=_InputUnion(
                ki=_KeyboardInput(wVk=0, wScan=int(unit), dwFlags=flags, time=0, dwExtraInfo=0)
            ),
        )

    @staticmethod
    def _virtual_key_input(vk: int, *, key_up: bool) -> _Input:
        flags = KEYEVENTF_KEYUP if key_up else 0
        return _Input(
            type=INPUT_KEYBOARD,
            value=_InputUnion(
                ki=_KeyboardInput(wVk=int(vk), wScan=0, dwFlags=flags, time=0, dwExtraInfo=0)
            ),
        )


@dataclass(frozen=True)
class _ActiveContact:
    slot: int
    pointer_id: int
    hwnd: int


class GuiTestPcLiveTouchEngine:
    """Maps normalized phone input to GUI_TEST_PC's shared Pico scheduler."""

    def __init__(
        self,
        config_path: str | Path,
        target_resolver: Callable[[int], dict[str, Any] | None],
        *,
        scheduler: PicoTouchScheduler | None = None,
        keyboard_injector: WindowsKeyboardInjector | None = None,
        event_logger: Callable[[str], None] | None = None,
    ) -> None:
        self.config_path = Path(config_path).resolve()
        self.target_resolver = target_resolver
        self.scheduler = scheduler or _scheduler_for_config(self.config_path)
        self.keyboard_injector = keyboard_injector
        self.event_logger = event_logger
        self._lock = threading.RLock()
        self._active: _ActiveContact | None = None

    def health(self) -> dict[str, Any]:
        with self._lock:
            active = self._active
            activity_snapshot = getattr(self.scheduler, "activity_snapshot", None)
            pico_activity = (
                activity_snapshot(hold_seconds=1.0)
                if callable(activity_snapshot)
                else {
                    "slot": None if active is None else active.slot,
                    "contact_active": active is not None,
                    "last_slot": None if active is None else active.slot,
                    "age_ms": None,
                    "minimum_hold_ms": 1000,
                }
            )
            return {
                "ok": True,
                "enabled": True,
                "token_required": True,
                "report_mode": self.scheduler.config.report_mode,
                "port": self.scheduler.config.port,
                "min_slot_interval_ms": self.scheduler.config.min_slot_interval_ms,
                "global_click_interval_ms": self.scheduler.config.global_click_interval_ms,
                "live_resume_interval_ms": getattr(
                    self.scheduler.config,
                    "live_resume_interval_ms",
                    50,
                ),
                "foreground_settle_ms": self.scheduler.config.foreground_settle_ms,
                "measurement": "network_rtt_and_host_to_hid_ack",
                "execution_owner": "GUI_TEST_PC",
                "relayed_to": "GUI_TEST_PC",
                "keyboard_backend": "windows_sendinput_gui_test_pc",
                "active_contact": None
                if active is None
                else {"slot": active.slot, "pointer_id": active.pointer_id},
                "pico_activity": pico_activity,
            }

    def handle(self, payload: dict[str, Any]) -> dict[str, Any]:
        received_at_ms = int(time.time() * 1000)
        host_received_at_ms = int(payload.get("host_received_at_ms") or received_at_ms)
        action = str(payload.get("action") or "").strip().casefold()
        if action in {"text", "key"}:
            return self._handle_keyboard(payload, action, host_received_at_ms, received_at_ms)
        if action not in {"down", "move", "up", "cancel"}:
            raise LiveTouchProtocolError("action must be down, move, up, cancel, text, or key")
        slot = self._required_int(payload, "slot")
        pointer_id = int(payload.get("pointer_id") or 0)
        if slot < MIN_SLOT or slot > MAX_SLOT:
            raise LiveTouchProtocolError(
                f"slot must be between {MIN_SLOT} and {MAX_SLOT}"
            )

        queue_wait_ms = 0
        recovered_contact: dict[str, int] | None = None
        with self._lock:
            try:
                if action == "down":
                    if self._active is not None:
                        recovered_contact = {
                            "slot": self._active.slot,
                            "pointer_id": self._active.pointer_id,
                        }
                        self.scheduler.cancel(expected_slot=self._active.slot)
                        self._active = None
                    target = self.target_resolver(slot)
                    if not target:
                        raise LiveTouchProtocolError(f"GUI_TEST_PC slot {slot} target is not ready")
                    hwnd = int(target.get("hwnd") or 0)
                    if hwnd <= 0:
                        raise LiveTouchProtocolError(f"GUI_TEST_PC slot {slot} HWND is invalid")
                    x, y = self._client_point(payload)
                    queue_wait_ms = round(
                        self.scheduler.begin_live(slot, hwnd, x, y) * 1000
                    )
                    self._active = _ActiveContact(slot=slot, pointer_id=pointer_id, hwnd=hwnd)
                    self._log(
                        f"OPLINK touch DOWN slot={slot} pointer={pointer_id} "
                        f"queue_wait_ms={queue_wait_ms}"
                    )
                elif action == "cancel":
                    if self._active is not None:
                        self._require_active(slot, pointer_id)
                        self.scheduler.cancel(expected_slot=slot)
                        self._active = None
                        self._log(f"OPLINK touch CANCEL slot={slot} pointer={pointer_id}")
                else:
                    self._require_active(slot, pointer_id)
                    x, y = self._client_point(payload)
                    if action == "move":
                        self.scheduler.move(x, y)
                    else:
                        self.scheduler.end(x, y)
                        self._active = None
                        self._log(f"OPLINK touch UP slot={slot} pointer={pointer_id}")
            except Exception:
                active = self._active
                if (
                    action != "down"
                    and active is not None
                    and active.slot == slot
                    and active.pointer_id == pointer_id
                ):
                    try:
                        self.scheduler.cancel(expected_slot=active.slot)
                    finally:
                        self._active = None
                self._log(
                    f"OPLINK touch ERROR action={action} slot={slot} "
                    f"pointer={pointer_id}"
                )
                raise

        hid_ack_at_ms = int(time.time() * 1000)
        return {
            "ok": True,
            "slot": slot,
            "action": action,
            "host_received_at_ms": host_received_at_ms,
            "gui_received_at_ms": received_at_ms,
            "hid_ack_at_ms": hid_ack_at_ms,
            "host_to_hid_ack_ms": float(max(0, hid_ack_at_ms - host_received_at_ms)),
            "slot_cooldown_wait_ms": queue_wait_ms,
            "backend": "pico_hid_touch_gui_test_pc",
            "execution_owner": "GUI_TEST_PC",
            "relayed_to": "GUI_TEST_PC",
            "recovered_stale_contact": recovered_contact,
        }

    def _log(self, message: str) -> None:
        if self.event_logger is not None:
            self.event_logger(str(message))

    def _handle_keyboard(
        self,
        payload: dict[str, Any],
        action: str,
        host_received_at_ms: int,
        received_at_ms: int,
    ) -> dict[str, Any]:
        slot = self._required_int(payload, "slot")
        if slot < MIN_SLOT or slot > MAX_SLOT:
            raise LiveTouchProtocolError(
                f"slot must be between {MIN_SLOT} and {MAX_SLOT}"
            )
        with self._lock:
            if self._active is not None:
                raise LiveTouchProtocolError("keyboard input is blocked while a touch contact is active")
            target = self.target_resolver(slot)
            if not target:
                raise LiveTouchProtocolError(f"GUI_TEST_PC slot {slot} target is not ready")
            hwnd = int(target.get("hwnd") or 0)
            if hwnd <= 0:
                raise LiveTouchProtocolError(f"GUI_TEST_PC slot {slot} HWND is invalid")
            injector = self.keyboard_injector
            if injector is None:
                injector = WindowsKeyboardInjector()
                self.keyboard_injector = injector
            if action == "text":
                injector.send_text(hwnd, str(payload.get("text") or ""))
            else:
                injector.send_key(hwnd, str(payload.get("key") or ""))

        ack_at_ms = int(time.time() * 1000)
        return {
            "ok": True,
            "slot": slot,
            "action": action,
            "host_received_at_ms": host_received_at_ms,
            "gui_received_at_ms": received_at_ms,
            "hid_ack_at_ms": ack_at_ms,
            "host_to_hid_ack_ms": float(max(0, ack_at_ms - host_received_at_ms)),
            "slot_cooldown_wait_ms": 0,
            "backend": "windows_sendinput_keyboard_gui_test_pc",
            "execution_owner": "GUI_TEST_PC",
            "relayed_to": "GUI_TEST_PC",
        }

    def shutdown(self) -> None:
        with self._lock:
            if self._active is None:
                return
            try:
                self.scheduler.cancel(expected_slot=self._active.slot)
            finally:
                self._active = None

    def _client_point(self, payload: dict[str, Any]) -> tuple[int, int]:
        try:
            normalized_x = float(payload["x"])
            normalized_y = float(payload["y"])
        except (KeyError, TypeError, ValueError) as exc:
            raise LiveTouchProtocolError("x and y must be normalized numbers") from exc
        if not (0.0 <= normalized_x <= 1.0 and 0.0 <= normalized_y <= 1.0):
            raise LiveTouchProtocolError("x and y must be between 0 and 1")
        policy = self.scheduler.layout_policy
        return (
            round(normalized_x * (policy.client_width - 1)),
            round(normalized_y * (policy.client_height - 1)),
        )

    def _require_active(self, slot: int, pointer_id: int) -> _ActiveContact:
        active = self._active
        if active is None:
            raise LiveTouchProtocolError("touch sequence has no active DOWN")
        if active.slot != slot or active.pointer_id != pointer_id:
            raise LiveTouchProtocolError(
                "touch sequence slot or pointer_id does not match the active contact"
            )
        return active

    @staticmethod
    def _required_int(payload: dict[str, Any], name: str) -> int:
        try:
            return int(payload[name])
        except (KeyError, TypeError, ValueError) as exc:
            raise LiveTouchProtocolError(f"{name} must be an integer") from exc


class _LoopbackTouchServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class _LiveTouchHandler(BaseHTTPRequestHandler):
    server: _LoopbackTouchServer

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self) -> None:
        if urlparse(self.path).path != "/health":
            self.send_error(HTTPStatus.NOT_FOUND, "not found")
            return
        self._json(self.server.engine.health())

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/input":
            self.send_error(HTTPStatus.NOT_FOUND, "not found")
            return
        try:
            payload = self._read_json()
            self._json(self.server.engine.handle(payload))
        except LiveTouchProtocolError as exc:
            self._json(self._error_payload(exc), HTTPStatus.CONFLICT)
        except PicoTouchError as exc:
            self._json(self._error_payload(exc), HTTPStatus.SERVICE_UNAVAILABLE)
        except WindowsKeyboardInputError as exc:
            self._json(self._error_payload(exc), HTTPStatus.SERVICE_UNAVAILABLE)
        except (ValueError, json.JSONDecodeError) as exc:
            self._json(self._error_payload(exc), HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self._json(self._error_payload(exc), HTTPStatus.INTERNAL_SERVER_ERROR)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 16_384:
            raise ValueError("invalid request body length")
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        return payload

    @staticmethod
    def _error_payload(exc: Exception) -> dict[str, Any]:
        return {
            "ok": False,
            "error": str(exc),
            "execution_owner": "GUI_TEST_PC",
            "relayed_to": "GUI_TEST_PC",
        }

    def _json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class GuiTestPcLiveTouchServer:
    def __init__(
        self,
        config_path: str | Path,
        target_resolver: Callable[[int], dict[str, Any] | None],
        *,
        host: str = "127.0.0.1",
        port: int = 5111,
        event_logger: Callable[[str], None] | None = None,
    ) -> None:
        if host not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("GUI_TEST_PC live touch server must remain loopback-only")
        self.engine = GuiTestPcLiveTouchEngine(
            config_path,
            target_resolver,
            event_logger=event_logger,
        )
        self.httpd = _LoopbackTouchServer((host, int(port)), _LiveTouchHandler)
        self.httpd.engine = self.engine
        self._thread = threading.Thread(
            target=self.httpd.serve_forever,
            name="gui-test-pc-live-touch",
            daemon=True,
        )

    @property
    def address(self) -> str:
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}"

    def start(self) -> None:
        self._thread.start()

    def health(self) -> dict[str, Any]:
        return self.engine.health()

    def stop(self) -> None:
        self.engine.shutdown()
        self.httpd.shutdown()
        self.httpd.server_close()
        if self._thread.is_alive():
            self._thread.join(timeout=2)
