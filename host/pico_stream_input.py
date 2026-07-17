from __future__ import annotations

import ctypes
import json
import threading
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any


TOUCH_LOGICAL_MAX = 32767
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
SW_RESTORE = 9


class PicoStreamInputError(RuntimeError):
    pass


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class PicoStreamInputController:
    """Single-contact Pico bridge for the selected streaming HWND."""

    def __init__(self, config_path: str | Path) -> None:
        value = json.loads(Path(config_path).read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict) or not value.get("enabled"):
            raise PicoStreamInputError("Pico input config is missing or disabled")
        self.port = str(value.get("port") or "").strip()
        self.report_mode = str(value.get("report_mode") or "touchscreen").strip().casefold()
        if self.report_mode not in {"touchscreen", "absolute_mouse"}:
            raise PicoStreamInputError("Pico report_mode must be touchscreen or absolute_mouse")
        if not self.port:
            raise PicoStreamInputError("Pico input config does not define a COM port")
        self.ack_timeout_ms = int(value.get("ack_timeout_ms", 1000))
        self.foreground_settle_ms = int(value.get("foreground_settle_ms", 150))
        self.min_slot_interval_ms = int(value.get("min_slot_interval_ms", 1500))
        self.move_min_interval_ms = int(value.get("move_min_interval_ms", 8))
        self.hid_drain_timeout_ms = int(value.get("hid_drain_timeout_ms", 1500))
        self._serial: Any | None = None
        self._next_sequence = 1
        self._lock = threading.RLock()
        self._active_slot: int | None = None
        self._active_hwnd: int | None = None
        self._next_allowed: dict[int, float] = {}
        self._last_move_at = 0.0
        self._user32 = self._load_user32()

    def describe(self) -> dict[str, object]:
        return {
            "enabled": True,
            "port": self.port,
            "report_mode": self.report_mode,
            "min_slot_interval_ms": self.min_slot_interval_ms,
            "measurement": "network_rtt_and_host_to_hid_ack",
        }

    def health(self) -> dict[str, object]:
        with self._lock:
            self._ensure_ready()
            status = self._command("STATUS")
            return {**self.describe(), "status": status}

    def handle(self, payload: dict[str, object], source: dict[str, object]) -> dict[str, object]:
        received_ns = time.perf_counter_ns()
        received_at_ms = int(time.time() * 1000)
        action = str(payload.get("action") or "").strip().casefold()
        slot = int(payload.get("slot") or 0)
        if action not in {"down", "move", "up", "cancel"}:
            raise PicoStreamInputError("action must be down, move, up, or cancel")
        if slot not in {1, 15}:
            raise PicoStreamInputError("this test accepts only slot 1 or 15")
        if not source.get("ok") or int(source.get("slot") or 0) != slot:
            raise PicoStreamInputError(f"slot {slot} source identity is not ready")

        x = float(payload.get("x", 0.0))
        y = float(payload.get("y", 0.0))
        if action != "cancel" and not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            raise PicoStreamInputError("normalized x and y must be between 0 and 1")

        with self._lock:
            self._ensure_ready()
            wait_ms = 0
            if action == "down":
                if self._active_slot is not None:
                    self._cancel_active()
                remaining = self._next_allowed.get(slot, 0.0) - time.monotonic()
                if remaining > 0:
                    wait_ms = round(remaining * 1000)
                    time.sleep(remaining)
                hwnd = int(source["hwnd"])
                self._activate(hwnd)
                command_x, command_y = self._map_normalized(hwnd, x, y)
                command = self._protocol_command("DOWN")
                pico_response = self._command(command, command_x, command_y)
                self._active_slot = slot
                self._active_hwnd = hwnd
                self._last_move_at = time.monotonic()
            elif action == "move":
                self._require_active(slot)
                elapsed_ms = (time.monotonic() - self._last_move_at) * 1000
                if elapsed_ms < self.move_min_interval_ms:
                    pico_response = "MOVE throttled"
                else:
                    command_x, command_y = self._map_normalized(int(self._active_hwnd), x, y)
                    command = self._protocol_command("MOVE")
                    pico_response = self._command(command, command_x, command_y)
                    self._last_move_at = time.monotonic()
            elif action == "up":
                self._require_active(slot)
                command = self._protocol_command("UP")
                pico_response = self._command(command)
                self._wait_idle()
                self._next_allowed[slot] = time.monotonic() + self.min_slot_interval_ms / 1000
                self._active_slot = None
                self._active_hwnd = None
            else:
                pico_response = self._cancel_active()

        ack_at_ms = int(time.time() * 1000)
        return {
            "ok": True,
            "slot": slot,
            "action": action,
            "client_sent_at_ms": payload.get("client_sent_at_ms"),
            "host_received_at_ms": received_at_ms,
            "hid_ack_at_ms": ack_at_ms,
            "host_to_hid_ack_ms": round((time.perf_counter_ns() - received_ns) / 1_000_000, 2),
            "slot_cooldown_wait_ms": wait_ms,
            "backend": self.report_mode,
            "pico_response": pico_response,
        }

    def _ensure_ready(self) -> None:
        if self._serial is None:
            try:
                import serial
            except ImportError as exc:
                raise PicoStreamInputError("pyserial is required for Pico input") from exc
            try:
                self._serial = serial.Serial(
                    port=self.port,
                    baudrate=115200,
                    timeout=0.05,
                    write_timeout=self.ack_timeout_ms / 1000,
                )
                self._serial.dtr = True
                self._serial.rts = True
                self._serial.reset_input_buffer()
            except Exception as exc:
                self._serial = None
                raise PicoStreamInputError(f"could not open Pico serial port {self.port}: {exc}") from exc
            ready = self._write_wait("HELLO 1", expected=("READY",), sequence=None)
            if "proto=1" not in ready or "hid=1" not in ready:
                raise PicoStreamInputError(f"unexpected Pico HELLO response: {ready}")

    def _command(self, name: str, *args: int) -> str:
        sequence = self._next_sequence
        self._next_sequence += 1
        command = " ".join([name, str(sequence), *(str(int(value)) for value in args)])
        return self._write_wait(command, expected=("ACK", "STATE", "ERR"), sequence=sequence)

    def _write_wait(self, command: str, *, expected: tuple[str, ...], sequence: int | None) -> str:
        if self._serial is None:
            raise PicoStreamInputError("Pico serial port is not open")
        try:
            self._serial.write((command + "\n").encode("ascii"))
            self._serial.flush()
        except Exception as exc:
            self._close_serial()
            raise PicoStreamInputError(f"Pico serial write failed: {exc}") from exc

        deadline = time.monotonic() + self.ack_timeout_ms / 1000
        while time.monotonic() < deadline:
            raw = self._serial.readline()
            if not raw:
                continue
            line = raw.decode("ascii", errors="replace").strip()
            if not line or not line.startswith(expected):
                continue
            if sequence is not None and line.startswith(("ACK ", "STATE ", "ERR ")):
                fields = line.split(maxsplit=2)
                if len(fields) < 2 or fields[1] != str(sequence):
                    continue
            if line.startswith("ERR "):
                raise PicoStreamInputError(f"Pico rejected {command!r}: {line}")
            return line
        raise PicoStreamInputError(f"timed out waiting for Pico response to {command!r}")

    def _wait_idle(self) -> None:
        active_field = "mouse" if self.report_mode == "absolute_mouse" else "tip"
        deadline = time.monotonic() + self.hid_drain_timeout_ms / 1000
        while time.monotonic() < deadline:
            status = self._command("STATUS")
            if f"{active_field}=0" in status and "queued=0" in status:
                return
            time.sleep(0.005)
        raise PicoStreamInputError("Pico HID queue did not become idle")

    def _cancel_active(self) -> str:
        response = self._command("CANCEL")
        if self._active_slot is not None:
            self._next_allowed[self._active_slot] = time.monotonic() + self.min_slot_interval_ms / 1000
        self._active_slot = None
        self._active_hwnd = None
        return response

    def _require_active(self, slot: int) -> None:
        if self._active_slot != slot or self._active_hwnd is None:
            raise PicoStreamInputError(f"slot {slot} does not own an active Pico contact")

    def _protocol_command(self, command: str) -> str:
        if self.report_mode == "touchscreen":
            return command
        return {"DOWN": "MDOWN", "MOVE": "MMOVE", "UP": "MUP"}[command]

    def _activate(self, hwnd: int) -> None:
        previous = int(self._user32.GetForegroundWindow() or 0)
        self._user32.ShowWindow(wintypes.HWND(hwnd), SW_RESTORE)
        self._user32.BringWindowToTop(wintypes.HWND(hwnd))
        self._user32.SetForegroundWindow(wintypes.HWND(hwnd))
        if int(self._user32.GetForegroundWindow() or 0) != hwnd:
            raise PicoStreamInputError(f"could not make slot window foreground: hwnd={hwnd}")
        if previous != hwnd and self.foreground_settle_ms > 0:
            time.sleep(self.foreground_settle_ms / 1000)

    def _map_normalized(self, hwnd: int, x: float, y: float) -> tuple[int, int]:
        self._user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        rect = RECT()
        if not self._user32.GetClientRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
            raise PicoStreamInputError("GetClientRect failed for selected game window")
        width = int(rect.right - rect.left)
        height = int(rect.bottom - rect.top)
        point = POINT(round(x * max(0, width - 1)), round(y * max(0, height - 1)))
        if not self._user32.ClientToScreen(wintypes.HWND(hwnd), ctypes.byref(point)):
            raise PicoStreamInputError("ClientToScreen failed for selected game window")
        hit = int(self._user32.WindowFromPoint(point) or 0)
        root = int(self._user32.GetAncestor(wintypes.HWND(hit), 2) or 0)
        if hit != hwnd and root != hwnd:
            raise PicoStreamInputError("mapped game point is covered by another window")

        left = int(self._user32.GetSystemMetrics(SM_XVIRTUALSCREEN))
        top = int(self._user32.GetSystemMetrics(SM_YVIRTUALSCREEN))
        screen_width = int(self._user32.GetSystemMetrics(SM_CXVIRTUALSCREEN))
        screen_height = int(self._user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))
        if not (left <= point.x < left + screen_width and top <= point.y < top + screen_height):
            raise PicoStreamInputError("mapped game point is outside the virtual desktop")
        logical_x = round((point.x - left) * TOUCH_LOGICAL_MAX / max(1, screen_width - 1))
        logical_y = round((point.y - top) * TOUCH_LOGICAL_MAX / max(1, screen_height - 1))
        return logical_x, logical_y

    def _close_serial(self) -> None:
        if self._serial is not None:
            self._serial.close()
            self._serial = None

    @staticmethod
    def _load_user32() -> Any:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.BringWindowToTop.argtypes = [wintypes.HWND]
        user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
        user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(POINT)]
        user32.WindowFromPoint.argtypes = [POINT]
        user32.WindowFromPoint.restype = wintypes.HWND
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetAncestor.restype = wintypes.HWND
        user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        return user32

